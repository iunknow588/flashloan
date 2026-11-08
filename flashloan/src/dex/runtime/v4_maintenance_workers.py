"""Dedicated maintenance worker slots for the Robinhood V4 pipeline.

The Rust closed-loop pool owns strategy scanning, onchain review and broadcast
execution threads.  SQLite business maintenance is delegated to this module so
the fixed pipeline can run as separate, mostly-idle threads:

1. ``v4_pool_liquidity_worker`` - incremental fixed-fee/no-hook pool refreshes
2. ``v4_candidate_yc_zc_worker`` - candidate YC/ZC anchor rebuilds
3. ``v4_market_observation_worker`` - market-observation anchor rebuilds
4. ``v4_dex_overview_worker`` - compact Dex overview snapshot refresh
5. ``v4_closed_loop_worker`` - closed-loop SQLite rebuild
6. ``v4_onchain_review_worker`` - light reviewer watchdog slot
7. ``v4_broadcast_execution_worker`` - light execution-queue watchdog slot

Every worker keeps a persisted cursor in
``chain_business_initialization``.  Later stages only run when their upstream
cursor is complete, so pool completeness always precedes candidate YC/ZC,
market observation and the closed-loop route.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from core.db.database_path import absolute_database_path
from core.market_identity import canonical_market_id
from core.db.v4_pool_liquidity_worker import (
    MAINTENANCE_WORKER_ORDER,
    V4_MAINTENANCE_WORKER_DOMAINS,
    load_worker_cursor,
    save_worker_cursor,
)
from dex.intent_trade.chains.robinhood_common.chain_config import (
    ROBINHOOD_TESTNET_CHAIN_ID,
    ROBINHOOD_TESTNET_NETWORK,
    all_anchor_symbols,
)


WORKER_POOL_LIQUIDITY = "v4_pool_liquidity_worker"
WORKER_CANDIDATE_YC_ZC = "v4_candidate_yc_zc_worker"
WORKER_MARKET_OBSERVATION = "v4_market_observation_worker"
WORKER_DEX_OVERVIEW = "v4_dex_overview_worker"
WORKER_CLOSED_LOOP = "v4_closed_loop_worker"
WORKER_ONCHAIN_REVIEW = "v4_onchain_review_worker"
WORKER_BROADCAST_EXECUTION = "v4_broadcast_execution_worker"

logger = logging.getLogger(__name__)


def _stamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _env_int(name: str, default: int) -> int:
    raw = str(os.getenv(name, "") or "").strip()
    if not raw:
        return int(default)
    try:
        return max(1, int(raw))
    except (TypeError, ValueError):
        return int(default)


def _env_float(name: str, default: float) -> float:
    raw = str(os.getenv(name, "") or "").strip()
    if not raw:
        return float(default)
    try:
        return max(0.0, float(raw))
    except (TypeError, ValueError):
        return float(default)


def maintenance_intervals() -> dict[str, int]:
    """Return per-worker intervals; public defaults stay mostly idle."""
    return {
        "v4_pool_liquidity_worker": _env_int(
            "V4_POOL_LIQUIDITY_WORKER_INTERVAL_SECONDS", 900
        ),
        "v4_candidate_yc_zc_worker": _env_int(
            "V4_CANDIDATE_YC_ZC_WORKER_INTERVAL_SECONDS", 300
        ),
        "v4_market_observation_worker": _env_int(
            "V4_MARKET_OBSERVATION_WORKER_INTERVAL_SECONDS", 300
        ),
        "v4_dex_overview_worker": _env_int(
            "V4_DEX_OVERVIEW_WORKER_INTERVAL_SECONDS", 300
        ),
        "v4_closed_loop_worker": _env_int(
            "V4_CLOSED_LOOP_WORKER_INTERVAL_SECONDS", 300
        ),
        "v4_onchain_review_worker": _env_int(
            "V4_ONCHAIN_REVIEW_WORKER_INTERVAL_SECONDS", 30
        ),
        "v4_broadcast_execution_worker": _env_int(
            "V4_BROADCAST_EXECUTION_WORKER_INTERVAL_SECONDS", 15
        ),
    }


def worker_dependencies() -> dict[str, tuple[str, ...]]:
    return {
        "v4_pool_liquidity_worker": (),
        "v4_candidate_yc_zc_worker": ("v4_pool_liquidity_worker",),
        "v4_market_observation_worker": ("v4_candidate_yc_zc_worker",),
        "v4_dex_overview_worker": (
            "v4_market_observation_worker",
            "v4_closed_loop_worker",
        ),
        "v4_closed_loop_worker": ("v4_market_observation_worker",),
        "v4_onchain_review_worker": ("v4_market_observation_worker",),
        "v4_broadcast_execution_worker": ("v4_onchain_review_worker",),
    }


def worker_stats(
    *,
    database_url: str,
    network: str = ROBINHOOD_TESTNET_NETWORK,
    chain_id: int = ROBINHOOD_TESTNET_CHAIN_ID,
    protocol: str = "uniswap_v4",
) -> dict[str, Any]:
    """Read-only per-worker cursor snapshot for the UI and maintenance logs."""
    database = str(absolute_database_path(database_url))
    from dex.runtime.robinhood_v4_idle_maintenance import (
        _candidate_eligible_missing_liquidity_count,
        _candidate_eligible_pool_ids,
        _explicit_zero_liquidity_pool_ids,
        _known_pool_token_addresses,
    )

    rows: dict[str, dict[str, Any]] = {}
    for worker in V4_MAINTENANCE_WORKER_DOMAINS:
        row = load_worker_cursor(
            database,
            network=network,
            chain_id=chain_id,
            protocol=protocol,
            worker=worker,
        )
        rows[worker] = row
    known_token_addresses = _known_pool_token_addresses(
        database,
        network=network,
        chain_id=chain_id,
    )
    eligible_pool_ids = _candidate_eligible_pool_ids(
        database,
        network=network,
        chain_id=chain_id,
        known_token_addresses=known_token_addresses,
    )
    zero_pool_ids = _explicit_zero_liquidity_pool_ids(
        database,
        network=network,
        chain_id=chain_id,
    )
    return {
        "ok": True,
        "network": network,
        "chainId": chain_id,
        "protocol": protocol,
        "workers": rows,
        "scope": {
            "eligiblePoolCount": len(eligible_pool_ids),
            "pendingLiquidityCount": _candidate_eligible_missing_liquidity_count(
                database,
                network=network,
                chain_id=chain_id,
                known_token_addresses=known_token_addresses,
            ),
            "explicitZeroLiquidityPoolCount": len(zero_pool_ids),
        },
        "order": list(V4_MAINTENANCE_WORKER_DOMAINS),
        "dependencies": worker_dependencies(),
        "generatedAt": _stamp(),
    }


def worker_complete(row: dict[str, Any] | None) -> bool:
    if not isinstance(row, dict):
        return False
    mode = str(row.get("mode") or "").strip().lower()
    # A missing cursor renders as ``mode=idle`` with no ``refreshed_at``; only
    # a worker that actually persisted a completed sweep is a valid upstream.
    return bool(
        mode
        in {
            "complete",
            "reviewer_watch_ok",
            "execution_watch_ok",
            "native_review_disabled",
        }
        and row.get("refreshed_at")
        and not row.get("error")
    )


def upstream_ready(
    *,
    database_url: str,
    network: str,
    chain_id: int,
    protocol: str = "uniswap_v4",
    worker: str,
) -> dict[str, Any]:
    """Return whether every upstream worker cursor has completed."""
    deps = worker_dependencies().get(worker, ())
    if not deps:
        return {"ok": True, "blocking": [], "ready": True}
    blocking = []
    for dep in deps:
        row = load_worker_cursor(
            database_url,
            network=network,
            chain_id=chain_id,
            protocol=protocol,
            worker=dep,
        )
        if not worker_complete(row):
            blocking.append({**row, "worker": dep})
    ready = not blocking
    return {"ok": ready, "blocking": blocking, "ready": ready}


def _count_rows(database: str, table: str, *, network: str, chain_id: int, protocol: str) -> int:
    from core.db.storage_business_io import query

    try:
        rows = query(
            database,
            f"""
            SELECT COUNT(*) AS count FROM {table}
            WHERE network = ? AND chain_id = ? AND protocol = ?
            """,
            (network, chain_id, protocol),
        )
        return int((rows[0] or {}).get("count") or 0)
    except Exception:
        return 0


def _save_worker_result(
    *,
    database: str,
    network: str,
    chain_id: int,
    worker: str,
    ok: bool,
    mode: str,
    pool_count: int = 0,
    scanned_pool_count: int = 0,
    remaining_pool_count: int = 0,
    cursor: int = 0,
    error: str | None = None,
    preserve_mode: bool = False,
) -> dict[str, Any]:
    return save_worker_cursor(
        database,
        network=network,
        chain_id=chain_id,
        worker=worker,
        cursor=cursor,
        pool_count=pool_count,
        scanned_pool_count=scanned_pool_count,
        remaining_pool_count=remaining_pool_count,
        last_scanned_at=_stamp(),
        mode=mode if (preserve_mode or not ok) else "complete",
        error=error,
    )


def run_pool_liquidity_tick(
    *,
    database_url: str,
    network: str,
    chain_id: int,
    batch_size: int | None = None,
) -> dict[str, Any]:
    """Run one incremental fixed-pool liquidity sweep slice."""
    from dex.runtime.robinhood_v4_idle_maintenance import (
        _run_pool_liquidity_tick,
    )

    result = _run_pool_liquidity_tick(
        database=database_url,
        network=network,
        chain_id=chain_id,
        batch_size=batch_size,
    )
    if batch_size is not None:
        result["batchSize"] = max(1, int(batch_size))
    return result


def run_candidate_yc_zc_build(
    *,
    database_url: str,
    network: str,
    chain_id: int,
    anchor_symbols: tuple[str, ...] | None = None,
    check_upstream: bool = True,
) -> dict[str, Any]:
    """Rebuild candidate YC/ZC anchor rows only when the pool sweep completed."""
    database = str(absolute_database_path(database_url))
    if check_upstream:
        readiness = upstream_ready(
            database_url=database,
            network=network,
            chain_id=chain_id,
            worker=WORKER_CANDIDATE_YC_ZC,
        )
        if not readiness["ready"]:
            return {
                "ok": False,
                "worker": WORKER_CANDIDATE_YC_ZC,
                "phase": "blocked_upstream",
                "blockedBy": [row.get("worker") for row in readiness["blocking"]],
                "anchors": [],
                "reports": [],
                "error": "upstream_pool_liquidity_not_complete",
                "generatedAt": _stamp(),
            }
    anchors = tuple(anchor_symbols) if anchor_symbols else all_anchor_symbols(
        network,
        database_url=database,
    )
    from core.db import builders
    market_id = canonical_market_id("dex_arbitrage", chain_id, "uniswap_v4", network=network)

    reports: list[dict[str, Any]] = []
    try:
        for anchor in anchors:
            reports.append(
                {
                    "anchorSymbol": anchor,
                    "result": builders.build_candidate_anchor(
                        database,
                        network=network,
                        chain_id=chain_id,
                        protocol="uniswap_v4",
                        anchor_scope=market_id,
                        anchor_symbol=anchor,
                    ),
                }
            )
        ok = True
        error = None
    except Exception as exc:
        ok = False
        error = str(exc)
    _save_worker_result(
        database=database,
        network=network,
        chain_id=chain_id,
        worker=WORKER_CANDIDATE_YC_ZC,
        ok=ok,
        mode=error or "candidate_rebuilt",
        pool_count=len(anchors),
        scanned_pool_count=len(anchors),
        remaining_pool_count=0 if ok else len(anchors),
        error=error,
    )
    return {
        "ok": ok,
        "worker": WORKER_CANDIDATE_YC_ZC,
        "anchors": list(anchors),
        "reports": reports,
        "error": error,
        "generatedAt": _stamp(),
    }


def run_market_observation_build(
    *,
    database_url: str,
    network: str,
    chain_id: int,
    anchor_symbols: tuple[str, ...] | None = None,
    check_upstream: bool = True,
) -> dict[str, Any]:
    """Rebuild market observation rows from the candidate YC/ZC set."""
    database = str(absolute_database_path(database_url))
    if check_upstream:
        readiness = upstream_ready(
            database_url=database,
            network=network,
            chain_id=chain_id,
            worker=WORKER_MARKET_OBSERVATION,
        )
        if not readiness["ready"]:
            return {
                "ok": False,
                "worker": WORKER_MARKET_OBSERVATION,
                "phase": "blocked_upstream",
                "blockedBy": [row.get("worker") for row in readiness["blocking"]],
                "anchors": [],
                "reports": [],
                "error": "upstream_candidate_yc_zc_not_complete",
                "generatedAt": _stamp(),
            }
    anchors = tuple(anchor_symbols) if anchor_symbols else all_anchor_symbols(
        network,
        database_url=database,
    )
    from core.db import builders
    from core.db.storage_business_io import query

    reports = []
    try:
        for anchor in anchors:
            token_rows = query(
                database,
                """
                SELECT token_address FROM dex_candidate_anchor_tokens
                WHERE network = ? AND chain_id = ? AND protocol = ?
                  AND anchor_symbol = ?
                ORDER BY token_address
                """,
                (network, chain_id, "uniswap_v4", anchor),
            )
            token_addresses = [str(row.get("token_address") or "") for row in token_rows]
            reports.append(
                {
                    "anchorSymbol": anchor,
                    "result": builders.build_market_observation_anchor(
                        database,
                        network=network,
                        chain_id=chain_id,
                        protocol="uniswap_v4",
                        anchor_symbol=anchor,
                        token_addresses=token_addresses,
                    ),
                }
            )
        ok = True
        error = None
    except Exception as exc:
        ok = False
        error = str(exc)
    _save_worker_result(
        database=database,
        network=network,
        chain_id=chain_id,
        worker=WORKER_MARKET_OBSERVATION,
        ok=ok,
        mode=error or "observation_rebuilt",
        pool_count=len(anchors),
        scanned_pool_count=len(anchors),
        remaining_pool_count=0 if ok else len(anchors),
        error=error,
    )
    return {
        "ok": ok,
        "worker": WORKER_MARKET_OBSERVATION,
        "anchors": list(anchors),
        "reports": reports,
        "error": error,
        "generatedAt": _stamp(),
    }


def run_closed_loop_build(
    *,
    database_url: str,
    network: str,
    chain_id: int,
    anchor_symbols: tuple[str, ...] | None = None,
    check_upstream: bool = True,
) -> dict[str, Any]:
    """Rebuild the SQLite closed-loop rows from observation rows."""
    database = str(absolute_database_path(database_url))
    if check_upstream:
        readiness = upstream_ready(
            database_url=database,
            network=network,
            chain_id=chain_id,
            worker=WORKER_CLOSED_LOOP,
        )
        if not readiness["ready"]:
            return {
                "ok": False,
                "worker": WORKER_CLOSED_LOOP,
                "phase": "blocked_upstream",
                "blockedBy": [row.get("worker") for row in readiness["blocking"]],
                "anchors": [],
                "reports": [],
                "error": "upstream_market_observation_not_complete",
                "generatedAt": _stamp(),
            }
    anchors = tuple(anchor_symbols) if anchor_symbols else all_anchor_symbols(
        network,
        database_url=database,
    )
    from core.db import builders
    market_id = canonical_market_id("dex_arbitrage", chain_id, "uniswap_v4", network=network)

    reports = []
    canonicalize_result: dict[str, Any] = {"ok": True, "updated": 0, "conflicts": 0}
    prune_result: dict[str, Any] = {"ok": True, "deleted": 0}
    residue_prune_result: dict[str, Any] = {"ok": True, "deleted": 0}
    orphan_prune_result: dict[str, Any] = {"ok": True, "deleted": 0, "skipped": False}
    backfill_result: dict[str, Any] = {"ok": True, "created": 0}
    try:
        for anchor in anchors:
            reports.append(
                {
                    "anchorSymbol": anchor,
                    "result": builders.build_closed_loops_anchor(
                        database,
                        network=network,
                        chain_id=chain_id,
                        protocol="uniswap_v4",
                        anchor_symbol=anchor,
                    ),
                }
            )
        from dex.runtime.closed_loop_state_machine import (
            backfill_runtime_state_for_closed_loops,
            canonicalize_runtime_state_market_ids,
            prune_legacy_unresolved_runtime_state,
            prune_orphan_runtime_state,
            prune_test_residue_runtime_state,
        )

        canonicalize_result = canonicalize_runtime_state_market_ids(
            database,
            network=network,
            chain_id=chain_id,
            protocol="uniswap_v4",
            market_id=market_id,
        )
        prune_result = prune_legacy_unresolved_runtime_state(
            database,
            network=network,
            chain_id=chain_id,
            protocol="uniswap_v4",
            market_id=market_id,
        )
        residue_prune_result = prune_test_residue_runtime_state(
            database,
            network=network,
            chain_id=chain_id,
            protocol="uniswap_v4",
            market_id=market_id,
        )
        orphan_prune_result = prune_orphan_runtime_state(
            database,
            network=network,
            chain_id=chain_id,
            protocol="uniswap_v4",
            market_id=market_id,
        )
        backfill_result = backfill_runtime_state_for_closed_loops(
            database,
            network=network,
            chain_id=chain_id,
            protocol="uniswap_v4",
            market_id=market_id,
        )
        conflicts = int(canonicalize_result.get("conflicts") or 0)
        ok = conflicts == 0
        error = "runtime_market_id_conflicts" if conflicts else None
    except Exception as exc:
        ok = False
        error = str(exc)
    _save_worker_result(
        database=database,
        network=network,
        chain_id=chain_id,
        worker=WORKER_CLOSED_LOOP,
        ok=ok,
        mode=error or "closed_loops_rebuilt",
        pool_count=len(anchors),
        scanned_pool_count=len(anchors),
        remaining_pool_count=0 if ok else len(anchors),
        error=error,
    )
    return {
        "ok": ok,
        "worker": WORKER_CLOSED_LOOP,
        "anchors": list(anchors),
        "reports": reports,
        "runtimeMarketIdCanonicalize": canonicalize_result,
        "legacyUnresolvedPrune": prune_result,
        "testResiduePrune": residue_prune_result,
        "orphanRuntimeStatePrune": orphan_prune_result,
        "runtimeStateBackfill": backfill_result,
        "runtimeMarketIdsUpdated": int(canonicalize_result.get("updated") or 0),
        "runtimeStatesBackfilled": int(backfill_result.get("created") or 0),
        "orphanPruned": int(orphan_prune_result.get("deleted") or 0),
        "residualPruned": int(residue_prune_result.get("residualPruned") or 0),
        "error": error,
        "generatedAt": _stamp(),
    }


def run_dex_overview_build(
    *,
    database_url: str,
    network: str,
    chain_id: int,
    check_upstream: bool = True,
) -> dict[str, Any]:
    """Refresh the compact Dex overview snapshot from SQLite route data."""
    database = str(absolute_database_path(database_url))
    if check_upstream:
        readiness = upstream_ready(
            database_url=database,
            network=network,
            chain_id=chain_id,
            worker=WORKER_DEX_OVERVIEW,
        )
        if not readiness["ready"]:
            return {
                "ok": False,
                "worker": WORKER_DEX_OVERVIEW,
                "phase": "blocked_upstream",
                "blockedBy": [row.get("worker") for row in readiness["blocking"]],
                "error": "upstream_market_observation_or_closed_loop_not_complete",
                "snapshot": {},
                "generatedAt": _stamp(),
            }
    try:
        from market.binance_market.dex_surfaces import (
            database_overview_snapshot,
        )

        snapshot = database_overview_snapshot(
            chain_id=chain_id,
            protocols=["uniswap_v4"],
        )
        ok = isinstance(snapshot, dict) and bool(snapshot.get("exists"))
        error = None
    except Exception as exc:
        ok = False
        error = str(exc)
        snapshot = {}
    _save_worker_result(
        database=database,
        network=network,
        chain_id=chain_id,
        worker=WORKER_DEX_OVERVIEW,
        ok=ok,
        mode=error or "overview_snapshot_refreshed",
        pool_count=1,
        scanned_pool_count=1 if ok else 0,
        remaining_pool_count=0 if ok else 1,
        error=error,
    )
    return {
        "ok": ok,
        "worker": WORKER_DEX_OVERVIEW,
        "snapshot": snapshot,
        "error": error,
        "generatedAt": _stamp(),
    }


def run_onchain_review_tick(
    *,
    database_url: str,
    network: str,
    chain_id: int,
    check_upstream: bool = True,
) -> dict[str, Any]:
    """Supervise native closed-loop reviewers without duplicating scans.

    The actual eth_call review runs on Rust ``rust-closed-loop-*`` reviewer
    threads; this slot is the dedicated Python watchdog that checks their
    residency and records the result.
    """
    database = str(absolute_database_path(database_url))
    if check_upstream:
        readiness = upstream_ready(
            database_url=database,
            network=network,
            chain_id=chain_id,
            worker=WORKER_ONCHAIN_REVIEW,
        )
        if not readiness["ready"]:
            return {
                "ok": False,
                "worker": WORKER_ONCHAIN_REVIEW,
                "phase": "blocked_upstream",
                "blockedBy": [row.get("worker") for row in readiness["blocking"]],
                "activeWorkers": 0,
                "activeGroups": [],
                "error": "upstream_market_observation_not_complete",
                "generatedAt": _stamp(),
            }
    from dex.runtime import rust_closed_loop

    if not rust_closed_loop.native_closed_loop_enabled():
        _save_worker_result(
            database=database,
            network=network,
            chain_id=chain_id,
            worker=WORKER_ONCHAIN_REVIEW,
            ok=True,
            mode="native_review_disabled",
            preserve_mode=True,
        )
        return {
            "ok": True,
            "worker": WORKER_ONCHAIN_REVIEW,
            "phase": "native_review_disabled",
            "activeWorkers": 0,
            "generatedAt": _stamp(),
        }
    try:
        status = rust_closed_loop.native_worker_status(
            include_payloads=False,
            database_url=database,
        )
        error = None
    except Exception as exc:
        status = {}
        error = str(exc)
    active = [
        str(worker.get("group_key") or "")
        for worker in (status.get("workers") or [])
        if isinstance(worker, dict)
        and str(worker.get("state") or "").strip().lower() != "stopped"
    ]
    reviewer_queue = (
        status.get("reviewQueue")
        if isinstance(status.get("reviewQueue"), dict)
        else {
            "available": False,
            "reason": "native_status_does_not_expose_review_queue_metrics",
        }
    )
    _save_worker_result(
        database=database,
        network=network,
        chain_id=chain_id,
        worker=WORKER_ONCHAIN_REVIEW,
        ok=not error,
        mode=error or "reviewer_watch_ok",
        pool_count=len(active),
        scanned_pool_count=len(active),
        remaining_pool_count=0,
        error=error,
        preserve_mode=True,
    )
    return {
        "ok": not error,
        "worker": WORKER_ONCHAIN_REVIEW,
        "phase": "reviewer_watch_ok" if not error else "reviewer_watch_error",
        "activeWorkers": len(active),
        "activeGroups": active,
        "reviewerQueue": reviewer_queue,
        "status": status,
        "error": error,
        "generatedAt": _stamp(),
    }


def run_broadcast_execution_tick(
    *,
    database_url: str,
    network: str,
    chain_id: int,
    check_upstream: bool = True,
) -> dict[str, Any]:
    """Supervise the Rust broadcast execution queue.

    The actual transaction broadcast is executed by dedicated
    ``rust-broadcast-manager-exec-*`` threads owned by the Rust Broadcast
    Manager.  This slot is the light watchdog that records queue/gate progress
    and never sends a second broadcast itself.
    """
    database = str(absolute_database_path(database_url))
    if check_upstream:
        readiness = upstream_ready(
            database_url=database,
            network=network,
            chain_id=chain_id,
            worker=WORKER_BROADCAST_EXECUTION,
        )
        if not readiness["ready"]:
            return {
                "ok": False,
                "worker": WORKER_BROADCAST_EXECUTION,
                "phase": "blocked_upstream",
                "blockedBy": [row.get("worker") for row in readiness["blocking"]],
                "executionQueue": {},
                "broadcastGate": {},
                "error": "upstream_onchain_review_not_complete",
                "generatedAt": _stamp(),
            }
    try:
        from dex.runtime import rust_closed_loop

        status = rust_closed_loop.native_worker_status(
            include_payloads=False,
            database_url=database,
        )
        gate = rust_closed_loop.broadcast_gate_status(chain_id=chain_id)
        error = None
    except Exception as exc:
        status = {}
        gate = {}
        error = str(exc)
    execution_queue = (
        status.get("executionQueue")
        if isinstance(status.get("executionQueue"), dict)
        else {}
    )
    _save_worker_result(
        database=database,
        network=network,
        chain_id=chain_id,
        worker=WORKER_BROADCAST_EXECUTION,
        ok=not error,
        mode=error or "execution_watch_ok",
        pool_count=1,
        scanned_pool_count=1 if not error else 0,
        remaining_pool_count=0,
        error=error,
        preserve_mode=True,
    )
    return {
        "ok": not error,
        "worker": WORKER_BROADCAST_EXECUTION,
        "phase": "execution_watch_ok" if not error else "execution_watch_error",
        "executionQueue": execution_queue,
        "broadcastGate": gate,
        "error": error,
        "generatedAt": _stamp(),
    }


def run_sequential_pipeline(
    *,
    database_url: str,
    network: str = ROBINHOOD_TESTNET_NETWORK,
    chain_id: int = ROBINHOOD_TESTNET_CHAIN_ID,
    worker_names: tuple[str, ...] | None = None,
) -> dict[str, Any]:
    """Run one tick for each requested worker in dependency order."""
    database = str(absolute_database_path(database_url))
    selected = (
        tuple(worker_names)
        if worker_names
        else V4_MAINTENANCE_WORKER_DOMAINS
    )
    results: dict[str, dict[str, Any]] = {}
    for worker in selected:
        with _sqlite_consumer_lock(database):
            readiness = upstream_ready(
                database_url=database,
                network=network,
                chain_id=chain_id,
                worker=worker,
            )
            blocking = readiness.get("blocking") or []
            if blocking:
                results[worker] = {
                    "ok": True,
                    "worker": worker,
                    "phase": "blocked_upstream",
                    "blockedBy": [row.get("worker") for row in blocking],
                }
                continue
            if worker == WORKER_POOL_LIQUIDITY:
                results[worker] = run_pool_liquidity_tick(
                    database_url=database,
                    network=network,
                    chain_id=chain_id,
                )
            elif worker == WORKER_CANDIDATE_YC_ZC:
                results[worker] = run_candidate_yc_zc_build(
                    database_url=database,
                    network=network,
                    chain_id=chain_id,
                    check_upstream=False,
                )
            elif worker == WORKER_MARKET_OBSERVATION:
                results[worker] = run_market_observation_build(
                    database_url=database,
                    network=network,
                    chain_id=chain_id,
                    check_upstream=False,
                )
            elif worker == WORKER_CLOSED_LOOP:
                results[worker] = run_closed_loop_build(
                    database_url=database,
                    network=network,
                    chain_id=chain_id,
                    check_upstream=False,
                )
            elif worker == WORKER_DEX_OVERVIEW:
                results[worker] = run_dex_overview_build(
                    database_url=database,
                    network=network,
                    chain_id=chain_id,
                    check_upstream=False,
                )
            elif worker == WORKER_ONCHAIN_REVIEW:
                results[worker] = run_onchain_review_tick(
                    database_url=database,
                    network=network,
                    chain_id=chain_id,
                )
            elif worker == WORKER_BROADCAST_EXECUTION:
                results[worker] = run_broadcast_execution_tick(
                    database_url=database,
                    network=network,
                    chain_id=chain_id,
                )
            else:
                results[worker] = {
                    "ok": False,
                    "worker": worker,
                    "error": "unknown_worker",
                }
    return {
        "ok": bool(results),
        "network": network,
        "chainId": chain_id,
        "database": database,
        "workers": results,
        "generatedAt": _stamp(),
    }


def _sqlite_consumer_lock(database_url: str):
    """Return the single SQLite write lock shared with idle maintenance."""
    from dex.runtime.robinhood_v4_idle_maintenance import _write_lock

    return _write_lock(database_url)


class V4MaintenanceWorkerManager:
    """Lifecycle owner for one thread per V4 maintenance responsibility.

    Each slot uses a daemon thread and a persisted cursor.  A worker that is
    blocked by an upstream stage stays idle and records ``blocked_by``, so the
    fixed order (pool -> candidate -> observation -> overview/closed-loop ->
    onchain review -> broadcast execution) is not enforced by one long
    serialized maintenance callback.
    """

    def __init__(
        self,
        *,
        database_url: str | None = None,
        network: str = ROBINHOOD_TESTNET_NETWORK,
        chain_id: int = ROBINHOOD_TESTNET_CHAIN_ID,
        protocol: str = "uniswap_v4",
        workers: tuple[str, ...] | None = None,
        intervals: dict[str, int] | None = None,
    ) -> None:
        self.database_url = str(absolute_database_path(database_url))
        self.network = network
        self.chain_id = int(chain_id)
        self.protocol = protocol
        self.workers = (
            tuple(workers)
            if workers
            else tuple(
                dict.fromkeys(
                    [
                        *MAINTENANCE_WORKER_ORDER,
                        WORKER_ONCHAIN_REVIEW,
                        WORKER_BROADCAST_EXECUTION,
                    ]
                )
            )
        )
        self.intervals = intervals or maintenance_intervals()
        self._stop = threading.Event()
        self._threads: dict[str, threading.Thread] = {}
        self._lock = threading.Lock()
        self._state: dict[str, dict[str, Any]] = {}

    def start(self) -> None:
        with self._lock:
            if self._threads:
                return
            self._stop.clear()
            for worker in self.workers:
                thread = threading.Thread(
                    target=self._loop,
                    name=f"v4-maintenance-{worker}",
                    daemon=True,
                    args=(worker,),
                )
                self._threads[worker] = thread
                thread.start()

    def stop(self, timeout: float = 3.0) -> None:
        self._stop.set()
        with self._lock:
            threads = list(self._threads.values())
        for thread in threads:
            if thread.is_alive():
                thread.join(timeout=timeout)
        with self._lock:
            self._threads = {}

    def _loop(self, worker: str) -> None:
        interval = max(1, int(self.intervals.get(worker, 300)))
        while not self._stop.is_set():
            started = time.monotonic()
            try:
                state = self._run_one(worker)
            except Exception as exc:
                state = {
                    "ok": False,
                    "worker": worker,
                    "error": str(exc),
                    "blockedBy": [],
                }
            with self._lock:
                self._state[worker] = {
                    **state,
                    "lastStartedAt": _stamp(),
                    "elapsedSeconds": time.monotonic() - started,
                }
            self._stop.wait(interval)

    def _run_one(self, worker: str) -> dict[str, Any]:
        with _sqlite_consumer_lock(self.database_url):
            readiness = upstream_ready(
                database_url=self.database_url,
                network=self.network,
                chain_id=self.chain_id,
                protocol=self.protocol,
                worker=worker,
            )
            blocking = readiness.get("blocking") or []
            if blocking:
                return {
                    "ok": True,
                    "worker": worker,
                    "phase": "blocked_upstream",
                    "blockedBy": [row.get("worker") for row in blocking],
                }
            return self._dispatch_worker(worker)

    def _dispatch_worker(self, worker: str) -> dict[str, Any]:
        if worker == WORKER_POOL_LIQUIDITY:
            return run_pool_liquidity_tick(
                database_url=self.database_url,
                network=self.network,
                chain_id=self.chain_id,
            )
        if worker == WORKER_CANDIDATE_YC_ZC:
            return run_candidate_yc_zc_build(
                database_url=self.database_url,
                network=self.network,
                chain_id=self.chain_id,
                check_upstream=False,
            )
        if worker == WORKER_MARKET_OBSERVATION:
            return run_market_observation_build(
                database_url=self.database_url,
                network=self.network,
                chain_id=self.chain_id,
                check_upstream=False,
            )
        if worker == WORKER_CLOSED_LOOP:
            return run_closed_loop_build(
                database_url=self.database_url,
                network=self.network,
                chain_id=self.chain_id,
                check_upstream=False,
            )
        if worker == WORKER_DEX_OVERVIEW:
            return run_dex_overview_build(
                database_url=self.database_url,
                network=self.network,
                chain_id=self.chain_id,
                check_upstream=False,
            )
        if worker == WORKER_ONCHAIN_REVIEW:
            return run_onchain_review_tick(
                database_url=self.database_url,
                network=self.network,
                chain_id=self.chain_id,
                check_upstream=False,
            )
        if worker == WORKER_BROADCAST_EXECUTION:
            return run_broadcast_execution_tick(
                database_url=self.database_url,
                network=self.network,
                chain_id=self.chain_id,
                check_upstream=False,
            )
        return {
            "ok": False,
            "worker": worker,
            "error": "unknown_worker",
            "blockedBy": [],
        }

    def status_json(self) -> dict[str, Any]:
        with self._lock:
            running = {
                worker: bool(thread.is_alive())
                for worker, thread in self._threads.items()
            }
            state = {
                worker: dict(value)
                for worker, value in self._state.items()
            }
        return {
            "ok": True,
            "manager": "v4_maintenance_workers",
            "database": self.database_url,
            "network": self.network,
            "chainId": self.chain_id,
            "running": running,
            "state": state,
        }

    def compact_status_json(self) -> dict[str, Any]:
        """Return a small status view without full maintenance last_results.

        Full worker results can be tens of MB after a candidate/observation
        sweep and must not be embedded in every periodic status poll.
        """
        with self._lock:
            running = {
                worker: bool(thread.is_alive())
                for worker, thread in self._threads.items()
            }
            state: dict[str, Any] = {}
            for worker, value in self._state.items():
                if not isinstance(value, dict):
                    continue
                summary: dict[str, Any] = {
                    key: value.get(key)
                    for key in (
                        "ok",
                        "worker",
                        "phase",
                        "error",
                        "lastStartedAt",
                        "elapsedSeconds",
                    )
                    if key in value
                }
                if worker == "v4_onchain_review_worker":
                    for key in ("activeGroups", "activeWorkers"):
                        if key in value:
                            summary[key] = value[key]
                elif worker == "v4_pool_liquidity_worker":
                    for key in (
                        "mode",
                        "poolCount",
                        "scannedPoolCount",
                        "remainingPoolCount",
                        "scopedPoolCount",
                        "missingAfter",
                        "missingBefore",
                        "refreshOk",
                        "refreshErrors",
                    ):
                        if key in value:
                            summary[key] = value[key]
                elif worker == "v4_dex_overview_worker":
                    summary["snapshotSummary"] = {
                        key: value.get(key)
                        for key in ("ok", "poolCount", "closedLoopCount")
                        if key in (value.get("snapshot") or {})
                    }
                state[worker] = summary
        return {
            "ok": True,
            "manager": "v4_maintenance_workers",
            "database": self.database_url,
            "network": self.network,
            "chainId": self.chain_id,
            "running": running,
            "state": state,
        }


_MANAGERS_BY_DATABASE: dict[str, V4MaintenanceWorkerManager] = {}
_MANAGER: V4MaintenanceWorkerManager | None = None
_MANAGER_LOCK = threading.Lock()


def get_v4_maintenance_manager(
    *,
    database_url: str | None = None,
    network: str = ROBINHOOD_TESTNET_NETWORK,
    chain_id: int = ROBINHOOD_TESTNET_CHAIN_ID,
) -> V4MaintenanceWorkerManager:
    global _MANAGER
    requested = str(absolute_database_path(database_url))
    with _MANAGER_LOCK:
        existing = _MANAGERS_BY_DATABASE.get(requested)
        if existing is not None:
            _MANAGER = existing
            return existing
        manager = V4MaintenanceWorkerManager(
            database_url=requested,
            network=network,
            chain_id=chain_id,
        )
        _MANAGERS_BY_DATABASE[requested] = manager
        _MANAGER = manager
        return manager


def ensure_v4_maintenance_workers_running(
    *,
    database_url: str | None = None,
    network: str = ROBINHOOD_TESTNET_NETWORK,
    chain_id: int = ROBINHOOD_TESTNET_CHAIN_ID,
) -> dict[str, Any]:
    manager = get_v4_maintenance_manager(
        database_url=database_url,
        network=network,
        chain_id=chain_id,
    )
    manager.start()
    return manager.status_json()


def stop_v4_maintenance_workers(
    timeout: float = 3.0, *, database_url: str | None = None
) -> dict[str, Any]:
    global _MANAGER
    with _MANAGER_LOCK:
        if database_url is None:
            managers = list(_MANAGERS_BY_DATABASE.values())
            _MANAGERS_BY_DATABASE.clear()
            _MANAGER = None
        else:
            requested = str(absolute_database_path(database_url))
            manager = _MANAGERS_BY_DATABASE.pop(requested, None)
            managers = [manager] if manager is not None else []
            if _MANAGER is not None and _MANAGER.database_url == requested:
                _MANAGER = None
    for manager in managers:
        manager.stop(timeout=timeout)
    return {"ok": True, "manager": "v4_maintenance_workers", "stopped": True}


def v4_maintenance_worker_status(
    *,
    database_url: str | None = None,
    include_details: bool = True,
) -> dict[str, Any]:
    """Return a small read-only manager status for the worker-pool UI."""
    with _MANAGER_LOCK:
        if database_url is not None:
            manager = _MANAGERS_BY_DATABASE.get(str(absolute_database_path(database_url)))
            managers = [manager] if manager is not None else []
        else:
            managers = list(_MANAGERS_BY_DATABASE.values())
    if not managers:
        return {
            "started": False,
            "manager": "v4_maintenance_workers",
            "workers": [],
            "state": {},
        }
    statuses = [
        manager.status_json() if include_details else manager.compact_status_json()
        for manager in managers
    ]
    started = any(bool(status.get("running")) for status in statuses)
    merged: dict[str, Any] = {
        "started": started,
        "manager": "v4_maintenance_workers",
        "databases": [],
        "workers": [],
        "state": {},
    }
    for status in statuses:
        database = status.get("database")
        if database:
            merged["databases"].append(database)
        merged["workers"].extend(status.get("running") or {})
        state = status.get("state") or {}
        for worker, row in state.items():
            merged["state"].setdefault(worker, {})
            merged["state"][worker][database] = row
    if len(managers) == 1:
        status = statuses[0]
    else:
        status = merged
    return {
        "started": bool(status.get("started", started)),
        "manager": "v4_maintenance_workers",
        "database": status.get("database") if len(managers) == 1 else None,
        "databases": merged.get("databases") or [status.get("database")],
        "workers": sorted(set(status.get("running") or merged.get("workers") or [])),
        "state": status.get("state") or merged.get("state") or {},
    }


def reset_v4_maintenance_workers_for_tests() -> None:
    stop_v4_maintenance_workers()


__all__ = [
    "MAINTENANCE_WORKER_ORDER",
    "V4_MAINTENANCE_WORKER_DOMAINS",
    "V4MaintenanceWorkerManager",
    "WORKER_CANDIDATE_YC_ZC",
    "WORKER_BROADCAST_EXECUTION",
    "WORKER_CLOSED_LOOP",
    "WORKER_DEX_OVERVIEW",
    "WORKER_MARKET_OBSERVATION",
    "WORKER_ONCHAIN_REVIEW",
    "WORKER_POOL_LIQUIDITY",
    "ensure_v4_maintenance_workers_running",
    "get_v4_maintenance_manager",
    "maintenance_intervals",
    "reset_v4_maintenance_workers_for_tests",
    "run_candidate_yc_zc_build",
    "run_broadcast_execution_tick",
    "run_closed_loop_build",
    "run_dex_overview_build",
    "run_market_observation_build",
    "run_onchain_review_tick",
    "run_pool_liquidity_tick",
    "run_sequential_pipeline",
    "stop_v4_maintenance_workers",
    "v4_maintenance_worker_status",
    "worker_dependencies",
    "worker_stats",
]
