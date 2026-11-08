"""Tests for the dedicated V4 maintenance worker slots."""

from __future__ import annotations

import json
import os
import threading
import time
from types import SimpleNamespace

import pytest

from core.db.storage_business_io import execute
from core.db.storage_business_schema import ensure_business_schema
from core.db.v4_pool_liquidity_worker import (
    MAINTENANCE_WORKER_ORDER,
    load_worker_cursor,
    save_worker_cursor,
)
from dex.intent_trade.chains.robinhood_common.chain_config import (
    ROBINHOOD_TESTNET_CHAIN_ID,
    ROBINHOOD_TESTNET_MARKET_ID,
    ROBINHOOD_TESTNET_NETWORK,
)
from dex.runtime import v4_maintenance_workers as workers
from dex.runtime import rust_closed_loop
from dex.runtime import closed_loop_state_machine as sm
from dex.runtime.closed_loop_state_machine import upsert_loop_state


def _complete_worker(
    database: str,
    worker: str,
    *,
    mode: str = "complete",
) -> None:
    save_worker_cursor(
        database,
        network=ROBINHOOD_TESTNET_NETWORK,
        chain_id=ROBINHOOD_TESTNET_CHAIN_ID,
        worker=worker,
        cursor=0,
        pool_count=1,
        scanned_pool_count=1,
        remaining_pool_count=0,
        mode=mode,
    )


def test_worker_domains_cover_required_stages() -> None:
    assert MAINTENANCE_WORKER_ORDER == (
        "v4_pool_liquidity_worker",
        "v4_candidate_yc_zc_worker",
        "v4_market_observation_worker",
        "v4_dex_overview_worker",
        "v4_closed_loop_worker",
    )
    for slot in (
        "v4_pool_liquidity_worker",
        "v4_candidate_yc_zc_worker",
        "v4_market_observation_worker",
        "v4_dex_overview_worker",
        "v4_closed_loop_worker",
        "v4_onchain_review_worker",
        "v4_broadcast_execution_worker",
    ):
        assert slot in workers.V4_MAINTENANCE_WORKER_DOMAINS


def test_worker_dependencies_follow_pool_first_order() -> None:
    deps = workers.worker_dependencies()
    assert deps["v4_pool_liquidity_worker"] == ()
    assert deps["v4_candidate_yc_zc_worker"] == ("v4_pool_liquidity_worker",)
    assert deps["v4_market_observation_worker"] == ("v4_candidate_yc_zc_worker",)
    assert deps["v4_closed_loop_worker"] == ("v4_market_observation_worker",)
    assert deps["v4_dex_overview_worker"] == (
        "v4_market_observation_worker",
        "v4_closed_loop_worker",
    )


def test_maintenance_intervals_are_idle_heavy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("V4_POOL_LIQUIDITY_WORKER_INTERVAL_SECONDS", "1")
    monkeypatch.setenv("V4_CANDIDATE_YC_ZC_WORKER_INTERVAL_SECONDS", "2")
    intervals = workers.maintenance_intervals()
    assert intervals["v4_pool_liquidity_worker"] == 1
    assert intervals["v4_candidate_yc_zc_worker"] == 2
    assert intervals["v4_market_observation_worker"] >= 60
    assert intervals["v4_dex_overview_worker"] >= 60
    assert intervals["v4_closed_loop_worker"] >= 60


def test_upstream_ready_blocks_candidate_until_pool_complete(
    tmp_path,
) -> None:
    database = str((tmp_path / "upstream.sqlite3").resolve())
    ensure_business_schema(database)

    readiness = workers.upstream_ready(
        database_url=database,
        network=ROBINHOOD_TESTNET_NETWORK,
        chain_id=ROBINHOOD_TESTNET_CHAIN_ID,
        worker=workers.WORKER_CANDIDATE_YC_ZC,
    )
    assert readiness["ready"] is False
    assert [row.get("worker") for row in readiness["blocking"]] == [
        workers.WORKER_POOL_LIQUIDITY
    ]

    save_worker_cursor(
        database,
        network=ROBINHOOD_TESTNET_NETWORK,
        chain_id=ROBINHOOD_TESTNET_CHAIN_ID,
        worker=workers.WORKER_POOL_LIQUIDITY,
        cursor=0,
        pool_count=4,
        scanned_pool_count=4,
        remaining_pool_count=0,
        mode="complete",
    )
    readiness = workers.upstream_ready(
        database_url=database,
        network=ROBINHOOD_TESTNET_NETWORK,
        chain_id=ROBINHOOD_TESTNET_CHAIN_ID,
        worker=workers.WORKER_CANDIDATE_YC_ZC,
    )
    assert readiness["ready"] is True
    assert readiness["blocking"] == []


def test_worker_stats_reports_all_slots(tmp_path) -> None:
    database = str((tmp_path / "stats.sqlite3").resolve())
    ensure_business_schema(database)
    save_worker_cursor(
        database,
        network=ROBINHOOD_TESTNET_NETWORK,
        chain_id=ROBINHOOD_TESTNET_CHAIN_ID,
        worker=workers.WORKER_POOL_LIQUIDITY,
        mode="complete",
    )

    stats = workers.worker_stats(
        database_url=database,
        network=ROBINHOOD_TESTNET_NETWORK,
        chain_id=ROBINHOOD_TESTNET_CHAIN_ID,
    )

    assert stats["ok"] is True
    assert set(stats["workers"]) == set(workers.V4_MAINTENANCE_WORKER_DOMAINS)
    assert stats["workers"][workers.WORKER_POOL_LIQUIDITY]["mode"] == "complete"
    assert stats["scope"]["eligiblePoolCount"] == 0
    assert stats["scope"]["pendingLiquidityCount"] == 0
    assert stats["scope"]["explicitZeroLiquidityPoolCount"] == 0


def test_pool_liquidity_wrapper_forwards_batch_size(tmp_path, monkeypatch) -> None:
    database = str((tmp_path / "wrapper.sqlite3").resolve())
    ensure_business_schema(database)
    seen: dict[str, object] = {}

    def fake_tick(**kwargs: object) -> dict[str, object]:
        seen.update(kwargs)
        return {"ok": True, "phase": "unit_test"}

    monkeypatch.setattr(
        "dex.runtime.robinhood_v4_idle_maintenance._run_pool_liquidity_tick",
        fake_tick,
    )

    result = workers.run_pool_liquidity_tick(
        database_url=database,
        network=ROBINHOOD_TESTNET_NETWORK,
        chain_id=ROBINHOOD_TESTNET_CHAIN_ID,
        batch_size=7,
    )

    assert seen["batch_size"] == 7
    assert result["batchSize"] == 7


def test_candidate_worker_build_uses_configured_anchors_and_persists_cursor(
    tmp_path,
    monkeypatch,
) -> None:
    database = str((tmp_path / "candidate.sqlite3").resolve())
    ensure_business_schema(database)
    _complete_worker(database, workers.WORKER_POOL_LIQUIDITY)
    built: list[str] = []

    monkeypatch.setattr(
        workers,
        "all_anchor_symbols",
        lambda *args, **kwargs: ("ETH", "TZ"),
    )

    def fake_build_candidate_anchor(
        database,
        *,
        network,
        chain_id,
        protocol,
        anchor_scope,
        anchor_symbol,
    ):
        built.append(anchor_symbol)
        return {"anchorSymbol": anchor_symbol, "tokenCount": 1}

    monkeypatch.setattr(
        "core.db.builders.build_candidate_anchor",
        fake_build_candidate_anchor,
    )

    result = workers.run_candidate_yc_zc_build(
        database_url=database,
        network=ROBINHOOD_TESTNET_NETWORK,
        chain_id=ROBINHOOD_TESTNET_CHAIN_ID,
    )

    assert result["ok"] is True
    assert built == ["ETH", "TZ"]
    from core.db.v4_pool_liquidity_worker import load_worker_cursor

    cursor = load_worker_cursor(
        database,
        network=ROBINHOOD_TESTNET_NETWORK,
        chain_id=ROBINHOOD_TESTNET_CHAIN_ID,
        worker=workers.WORKER_CANDIDATE_YC_ZC,
    )
    assert cursor["mode"] == "complete"
    assert cursor["pool_count"] == 2
    assert cursor["remaining_pool_count"] == 0
    assert cursor["error"] is None


def test_candidate_worker_is_blocked_when_pool_cursor_not_complete(tmp_path) -> None:
    database = str((tmp_path / "candidate-blocked.sqlite3").resolve())
    ensure_business_schema(database)
    save_worker_cursor(
        database,
        network=ROBINHOOD_TESTNET_NETWORK,
        chain_id=ROBINHOOD_TESTNET_CHAIN_ID,
        worker=workers.WORKER_POOL_LIQUIDITY,
        mode="scanning",
    )

    result = workers.run_candidate_yc_zc_build(
        database_url=database,
        network=ROBINHOOD_TESTNET_NETWORK,
        chain_id=ROBINHOOD_TESTNET_CHAIN_ID,
    )

    assert result["ok"] is False
    assert result["phase"] == "blocked_upstream"
    assert result["blockedBy"] == [workers.WORKER_POOL_LIQUIDITY]


def test_candidate_worker_keeps_runtime_state_until_loop_rebuild(tmp_path, monkeypatch) -> None:
    """Candidate rebuild must not prune paths that still need loop rebuild."""
    database = str((tmp_path / "candidate-keeps-state.sqlite3").resolve())
    ensure_business_schema(database)
    _complete_worker(database, workers.WORKER_POOL_LIQUIDITY)
    monkeypatch.setattr(
        workers,
        "all_anchor_symbols",
        lambda *args, **kwargs: ("ETH",),
    )
    monkeypatch.setattr(
        "core.db.builders.build_candidate_anchor",
        lambda *args, **kwargs: {"ok": True, "anchorSymbol": kwargs["anchor_symbol"]},
    )
    upsert_loop_state(
        database,
        network=ROBINHOOD_TESTNET_NETWORK,
        chain_id=ROBINHOOD_TESTNET_CHAIN_ID,
        protocol="uniswap_v4",
        market_id="dex-arbitrage-46630-uniswap_v4",
        loop_key="weth|spy|usdg",
        state="offchain_estimate",
    )

    result = workers.run_candidate_yc_zc_build(
        database_url=database,
        network=ROBINHOOD_TESTNET_NETWORK,
        chain_id=ROBINHOOD_TESTNET_CHAIN_ID,
    )

    assert result["ok"] is True
    assert "orphanRuntimeStatePrune" not in result
    assert "orphanPruned" not in result
    from dex.runtime.closed_loop_state_machine import load_loop_state

    assert load_loop_state(
        database,
        network=ROBINHOOD_TESTNET_NETWORK,
        chain_id=ROBINHOOD_TESTNET_CHAIN_ID,
        protocol="uniswap_v4",
        market_id="dex-arbitrage-46630-uniswap_v4",
        loop_key="weth|spy|usdg",
    ) is not None


def test_closed_loop_maintenance_preserves_protected_testnet_residue(
    tmp_path,
    monkeypatch,
) -> None:
    database = str((tmp_path / "closed-loop-testnet-residue.sqlite3").resolve())
    ensure_business_schema(database)
    for worker in (
        workers.WORKER_POOL_LIQUIDITY,
        workers.WORKER_CANDIDATE_YC_ZC,
        workers.WORKER_MARKET_OBSERVATION,
    ):
        _complete_worker(database, worker)
    monkeypatch.setattr(
        workers,
        "all_anchor_symbols",
        lambda *args, **kwargs: ("ETH",),
    )
    monkeypatch.setattr(
        "core.db.builders.build_closed_loops_anchor",
        lambda *args, **kwargs: {"ok": True, "loopCount": 0},
    )
    residue = {
        "network": ROBINHOOD_TESTNET_NETWORK,
        "chain_id": ROBINHOOD_TESTNET_CHAIN_ID,
        "protocol": "uniswap_v4",
        "market_id": ROBINHOOD_TESTNET_MARKET_ID,
        "loop_key": "aaa|bbb|ccc",
    }
    upsert_loop_state(
        database,
        **residue,
        state=sm.STATE_PREFLIGHT_FAILED,
    )

    result = workers.run_closed_loop_build(
        database_url=database,
        network=ROBINHOOD_TESTNET_NETWORK,
        chain_id=ROBINHOOD_TESTNET_CHAIN_ID,
    )

    assert result["ok"] is True
    assert result["residualPruned"] == 0
    assert result["testResiduePrune"]["preservedCount"] == 1
    assert result["orphanRuntimeStatePrune"]["protectedTestResidueCount"] == 1
    assert sm.load_loop_state(database, **residue) is not None


def test_closed_loop_worker_canonicalizes_prunes_and_backfills_after_rebuild(
    tmp_path,
    monkeypatch,
) -> None:
    database = str((tmp_path / "closed-loop-maintenance.sqlite3").resolve())
    ensure_business_schema(database)
    for worker in (
        workers.WORKER_POOL_LIQUIDITY,
        workers.WORKER_CANDIDATE_YC_ZC,
        workers.WORKER_MARKET_OBSERVATION,
    ):
        _complete_worker(database, worker)
    monkeypatch.setattr(
        workers,
        "all_anchor_symbols",
        lambda *args, **kwargs: ("SPY",),
    )
    monkeypatch.setattr(
        "core.db.builders.build_closed_loops_anchor",
        lambda *args, **kwargs: {"ok": True, "loopCount": 2},
    )
    anchor = "0x" + ("1" * 40)
    y = "0x" + ("2" * 40)
    z = "0x" + ("3" * 40)
    d = "0x" + ("4" * 40)
    e = "0x" + ("5" * 40)
    for loop_key, symbols in (
        (
            f"{anchor}:{y}:{z}",
            ["SPY", "TSLA", "USDG", "SPY"],
        ),
        (
            f"{anchor}:{z}:{y}",
            ["SPY", "USDG", "TSLA", "SPY"],
        ),
        (
            f"{anchor}:{d}:{e}",
            ["SPY", "AMZN", "NVDA", "SPY"],
        ),
    ):
        execute(
            database,
            """
            INSERT INTO v4_closed_loops (
                network, chain_id, protocol, market_id, loop_key,
                anchor_address, anchor_symbol, path_symbols_json,
                status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active', 'now', 'now')
            """,
            (
                ROBINHOOD_TESTNET_NETWORK,
                ROBINHOOD_TESTNET_CHAIN_ID,
                "uniswap_v4",
                ROBINHOOD_TESTNET_MARKET_ID,
                loop_key,
                anchor,
                "SPY",
                json.dumps(symbols),
            ),
        )
    upsert_loop_state(
        database,
        network=ROBINHOOD_TESTNET_NETWORK,
        chain_id=ROBINHOOD_TESTNET_CHAIN_ID,
        protocol="uniswap_v4",
        market_id="dex-arbitrage-46630-uniswap_v4",
        loop_key="spy|tsla|usdg",
        state="preflight_done",
    )
    upsert_loop_state(
        database,
        network=ROBINHOOD_TESTNET_NETWORK,
        chain_id=ROBINHOOD_TESTNET_CHAIN_ID,
        protocol="uniswap_v4",
        market_id="dex-arbitrage-46630-uniswap_v4",
        loop_key="weth|spy|usdg",
        state="offchain_estimate",
    )

    result = workers.run_closed_loop_build(
        database_url=database,
        network=ROBINHOOD_TESTNET_NETWORK,
        chain_id=ROBINHOOD_TESTNET_CHAIN_ID,
    )

    assert result["ok"] is True
    assert result["runtimeMarketIdsUpdated"] == 2
    assert result["runtimeStatesBackfilled"] == 3
    assert result["orphanPruned"] == 1
    from dex.runtime.closed_loop_state_machine import load_loop_state

    assert load_loop_state(
        database,
        network=ROBINHOOD_TESTNET_NETWORK,
        chain_id=ROBINHOOD_TESTNET_CHAIN_ID,
        protocol="uniswap_v4",
        market_id=ROBINHOOD_TESTNET_MARKET_ID,
        loop_key=f"{anchor}:{y}:{z}",
    ) is not None
    assert load_loop_state(
        database,
        network=ROBINHOOD_TESTNET_NETWORK,
        chain_id=ROBINHOOD_TESTNET_CHAIN_ID,
        protocol="uniswap_v4",
        market_id=ROBINHOOD_TESTNET_MARKET_ID,
        loop_key=f"{anchor}:{z}:{y}",
    ) is not None
    assert load_loop_state(
        database,
        network=ROBINHOOD_TESTNET_NETWORK,
        chain_id=ROBINHOOD_TESTNET_CHAIN_ID,
        protocol="uniswap_v4",
        market_id=ROBINHOOD_TESTNET_MARKET_ID,
        loop_key=f"{anchor}:{d}:{e}",
    ) is not None
    assert load_loop_state(
        database,
        network=ROBINHOOD_TESTNET_NETWORK,
        chain_id=ROBINHOOD_TESTNET_CHAIN_ID,
        protocol="uniswap_v4",
        market_id=ROBINHOOD_TESTNET_MARKET_ID,
        loop_key="spy|tsla|usdg",
    ) is not None
    assert load_loop_state(
        database,
        network=ROBINHOOD_TESTNET_NETWORK,
        chain_id=ROBINHOOD_TESTNET_CHAIN_ID,
        protocol="uniswap_v4",
        market_id=ROBINHOOD_TESTNET_MARKET_ID,
        loop_key="weth|spy|usdg",
    ) is None


def test_pipeline_rebuilds_after_pool_worker_completes(
    tmp_path,
    monkeypatch,
) -> None:
    """Pool completion must unlock candidate, observation and loop rebuilds."""
    database = str((tmp_path / "pipeline-recovery.sqlite3").resolve())
    ensure_business_schema(database)
    save_worker_cursor(
        database,
        network=ROBINHOOD_TESTNET_NETWORK,
        chain_id=ROBINHOOD_TESTNET_CHAIN_ID,
        worker=workers.WORKER_POOL_LIQUIDITY,
        mode="scanning",
    )

    blocked = workers.run_candidate_yc_zc_build(
        database_url=database,
        network=ROBINHOOD_TESTNET_NETWORK,
        chain_id=ROBINHOOD_TESTNET_CHAIN_ID,
    )
    assert blocked["ok"] is False
    assert blocked["phase"] == "blocked_upstream"

    _complete_worker(database, workers.WORKER_POOL_LIQUIDITY)
    monkeypatch.setattr(
        workers,
        "all_anchor_symbols",
        lambda *args, **kwargs: ("ETH",),
    )
    monkeypatch.setattr(
        "core.db.builders.build_candidate_anchor",
        lambda *args, **kwargs: {
            "ok": True,
            "anchorSymbol": kwargs["anchor_symbol"],
        },
    )
    monkeypatch.setattr(
        "core.db.builders.build_market_observation_anchor",
        lambda *args, **kwargs: {
            "ok": True,
            "anchorSymbol": kwargs["anchor_symbol"],
        },
    )
    monkeypatch.setattr(
        "core.db.builders.build_closed_loops_anchor",
        lambda *args, **kwargs: {
            "ok": True,
            "anchorSymbol": kwargs["anchor_symbol"],
            "loopCount": 1,
        },
    )
    monkeypatch.setattr(
        "market.binance_market.dex_surfaces.database_overview_snapshot",
        lambda *args, **kwargs: {"exists": True, "closedLoopRouteSummaries": []},
    )

    assert workers.run_candidate_yc_zc_build(
        database_url=database,
        network=ROBINHOOD_TESTNET_NETWORK,
        chain_id=ROBINHOOD_TESTNET_CHAIN_ID,
    )["ok"] is True
    assert workers.run_market_observation_build(
        database_url=database,
        network=ROBINHOOD_TESTNET_NETWORK,
        chain_id=ROBINHOOD_TESTNET_CHAIN_ID,
    )["ok"] is True
    assert workers.run_closed_loop_build(
        database_url=database,
        network=ROBINHOOD_TESTNET_NETWORK,
        chain_id=ROBINHOOD_TESTNET_CHAIN_ID,
    )["ok"] is True
    assert workers.run_dex_overview_build(
        database_url=database,
        network=ROBINHOOD_TESTNET_NETWORK,
        chain_id=ROBINHOOD_TESTNET_CHAIN_ID,
    )["ok"] is True

    for worker in (
        workers.WORKER_CANDIDATE_YC_ZC,
        workers.WORKER_MARKET_OBSERVATION,
        workers.WORKER_CLOSED_LOOP,
        workers.WORKER_DEX_OVERVIEW,
    ):
        cursor = load_worker_cursor(
            database,
            network=ROBINHOOD_TESTNET_NETWORK,
            chain_id=ROBINHOOD_TESTNET_CHAIN_ID,
            worker=worker,
        )
        assert cursor["mode"] == "complete"
        assert cursor["error"] is None


def test_onchain_review_tick_monitors_native_groups_without_scanning(
    tmp_path,
    monkeypatch,
) -> None:
    database = str((tmp_path / "review.sqlite3").resolve())
    ensure_business_schema(database)
    _complete_worker(database, workers.WORKER_POOL_LIQUIDITY)
    _complete_worker(database, workers.WORKER_CANDIDATE_YC_ZC)
    _complete_worker(database, workers.WORKER_MARKET_OBSERVATION)
    scan_calls: list[dict[str, Any]] = []

    monkeypatch.setattr(
        rust_closed_loop,
        "native_closed_loop_enabled",
        lambda: True,
    )

    def fake_status(*, include_payloads: bool = True, database_url: str | None = None) -> dict[str, Any]:
        return {
            "workers": [
                {"group_key": "eth|a|b", "state": "running"},
                {"group_key": "eth|c|d", "state": "stopped"},
            ],
            "reviewQueue": {
                "enabled": True,
                "running": 1,
                "claimed": 3,
                "completed": 2,
                "failed": 1,
            },
        }

    monkeypatch.setattr(
        rust_closed_loop,
        "native_worker_status",
        fake_status,
    )
    monkeypatch.setattr(
        "dex.runtime.dex_opportunity_scanner.run_dex_opportunity_scan_now",
        lambda **kwargs: scan_calls.append(kwargs) or {},
    )

    result = workers.run_onchain_review_tick(
        database_url=database,
        network=ROBINHOOD_TESTNET_NETWORK,
        chain_id=ROBINHOOD_TESTNET_CHAIN_ID,
    )

    assert result["ok"] is True
    assert result["activeGroups"] == ["eth|a|b"]
    assert result["reviewerQueue"]["running"] == 1
    assert result["reviewerQueue"]["claimed"] == 3
    assert result["reviewerQueue"]["completed"] == 2
    assert scan_calls == []
    from core.db.v4_pool_liquidity_worker import load_worker_cursor

    cursor = load_worker_cursor(
        database,
        network=ROBINHOOD_TESTNET_NETWORK,
        chain_id=ROBINHOOD_TESTNET_CHAIN_ID,
        worker=workers.WORKER_ONCHAIN_REVIEW,
    )
    assert cursor["mode"] == "reviewer_watch_ok"


def test_onchain_review_tick_skips_when_native_disabled(tmp_path, monkeypatch) -> None:
    database = str((tmp_path / "review-disabled.sqlite3").resolve())
    ensure_business_schema(database)
    _complete_worker(database, workers.WORKER_POOL_LIQUIDITY)
    _complete_worker(database, workers.WORKER_CANDIDATE_YC_ZC)
    _complete_worker(database, workers.WORKER_MARKET_OBSERVATION)
    monkeypatch.setattr(rust_closed_loop, "native_closed_loop_enabled", lambda: False)

    result = workers.run_onchain_review_tick(
        database_url=database,
        network=ROBINHOOD_TESTNET_NETWORK,
        chain_id=ROBINHOOD_TESTNET_CHAIN_ID,
    )

    assert result["phase"] == "native_review_disabled"
    assert result["activeWorkers"] == 0


def test_broadcast_execution_tick_only_supervises_queue(tmp_path, monkeypatch) -> None:
    database = str((tmp_path / "broadcast.sqlite3").resolve())
    ensure_business_schema(database)
    _complete_worker(database, workers.WORKER_POOL_LIQUIDITY)
    _complete_worker(database, workers.WORKER_CANDIDATE_YC_ZC)
    _complete_worker(database, workers.WORKER_MARKET_OBSERVATION)
    _complete_worker(
        database,
        workers.WORKER_ONCHAIN_REVIEW,
        mode="reviewer_watch_ok",
    )

    def fake_status(*, include_payloads: bool = True, database_url: str | None = None) -> dict[str, Any]:
        return {
            "executionQueue": {
                "enabled": True,
                "workers": 2,
                "queued": 1,
                "completed": 3,
            }
        }

    monkeypatch.setattr(rust_closed_loop, "native_worker_status", fake_status)
    monkeypatch.setattr(
        rust_closed_loop,
        "broadcast_gate_status",
        lambda **kwargs: {"broadcastEnabled": True},
    )

    result = workers.run_broadcast_execution_tick(
        database_url=database,
        network=ROBINHOOD_TESTNET_NETWORK,
        chain_id=ROBINHOOD_TESTNET_CHAIN_ID,
    )

    assert result["ok"] is True
    assert result["executionQueue"]["queued"] == 1
    assert result["broadcastGate"]["broadcastEnabled"] is True


def test_worker_manager_starts_and_stops_slots(tmp_path, monkeypatch) -> None:
    database = str((tmp_path / "manager.sqlite3").resolve())
    ensure_business_schema(database)
    calls: list[str] = []

    class FakeManager(workers.V4MaintenanceWorkerManager):
        def __init__(self) -> None:
            super().__init__(
                database_url=database,
                network=ROBINHOOD_TESTNET_NETWORK,
                chain_id=ROBINHOOD_TESTNET_CHAIN_ID,
                intervals={
                    "v4_pool_liquidity_worker": 60,
                    "v4_candidate_yc_zc_worker": 60,
                    "v4_market_observation_worker": 60,
                    "v4_dex_overview_worker": 60,
                    "v4_closed_loop_worker": 60,
                },
            )

        def _run_one(self, worker):
            calls.append(worker)
            return {"ok": True, "worker": worker, "phase": "unit_test"}

    manager = FakeManager()
    manager.start()
    try:
        assert manager.status_json()["running"]
    finally:
        manager.stop(timeout=0.5)
    assert manager.status_json()["running"] == {}


def test_singleton_get_manager_replaces_database_path(tmp_path) -> None:
    first = str((tmp_path / "one.sqlite3").resolve())
    second = str((tmp_path / "two.sqlite3").resolve())
    ensure_business_schema(first)
    ensure_business_schema(second)
    try:
        manager_a = workers.get_v4_maintenance_manager(database_url=first)
        assert manager_a.database_url == first
        manager_b = workers.get_v4_maintenance_manager(database_url=second)
        assert manager_b.database_url == second
    finally:
        workers.stop_v4_maintenance_workers()
