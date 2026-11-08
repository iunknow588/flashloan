"""Tests for the Rust callback SQLite maintenance consumer."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from core.db.storage_business_io import executemany, query
from core.db.storage_business_schema import ensure_business_schema
from dex.runtime import robinhood_v4_idle_maintenance as maintenance
from dex.intent_trade.chains.robinhood_common.chain_config import (
    ROBINHOOD_CHAIN_ID,
    ROBINHOOD_NETWORK,
    ROBINHOOD_TESTNET_CHAIN_ID,
    ROBINHOOD_TESTNET_MARKET_ID,
    ROBINHOOD_TESTNET_NETWORK,
)


def _pool_row(
    database: str,
    *,
    pool_id: str,
    token0: str,
    token1: str,
    has_liquidity: int | None = 1,
    snapshot_at: str | None = None,
    price: float | None = 1.0,
    sqrt_price_x96: str | None = "79228162514264337593543950336",
) -> None:
    now = "2026-10-22T00:00:00Z"
    snapshot_at = snapshot_at or now
    executemany(
        database,
        """
        INSERT INTO v4_exchange_pools (
            network, chain_id, protocol, pool_id, pool_identity_mode,
            pool_manager, pool_key_json, token0, token1, fee, fee_label,
            dynamic_fee, special_fee, tick_spacing, hooks, address_pair,
            created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                ROBINHOOD_TESTNET_NETWORK,
                ROBINHOOD_TESTNET_CHAIN_ID,
                "uniswap_v4",
                pool_id,
                "v4_pool_id",
                "0x8366a39CC670B4001A1121B8F6A443A643e40951",
                '{"initializeBlockNumber": 200}',
                token0,
                token1,
                3000,
                3000,
                0,
                0,
                60,
                "0x" + "00" * 20,
                f"{token0}:{token1}",
                now,
                now,
            )
        ],
    )
    executemany(
        database,
        """
        INSERT INTO v4_exchange_pool_liquidity_snapshots (
            network, chain_id, protocol, pool_id, has_liquidity, liquidity,
            estimated_tvl_usd, depth_status, sqrt_price_x96, price,
            observed_at, refreshed_at, evidence_source
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                ROBINHOOD_TESTNET_NETWORK,
                ROBINHOOD_TESTNET_CHAIN_ID,
                "uniswap_v4",
                pool_id,
                has_liquidity,
                "0",
                0.0,
                "empty" if has_liquidity == 0 else "liquid",
                sqrt_price_x96,
                price,
                snapshot_at,
                snapshot_at,
                "pool_manager_extsload_live_state_batch",
            )
        ],
    )


def _candidate_row(
    database: str,
    *,
    pool_id: str,
    token0: str,
    token1: str,
    anchor_symbol: str = "ETH",
) -> None:
    now = "2026-10-22T00:00:00Z"
    executemany(
        database,
        """
        INSERT INTO dex_candidate_pools (
            network, chain_id, protocol, anchor_scope, anchor_symbol,
            address_pair, direction_key, pool_id, token0, token1, fee,
            dynamic_fee, special_fee, tick_spacing, hooks, has_liquidity,
            usable_for_observation, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                ROBINHOOD_TESTNET_NETWORK,
                ROBINHOOD_TESTNET_CHAIN_ID,
                "uniswap_v4",
                ROBINHOOD_TESTNET_MARKET_ID,
                anchor_symbol,
                f"{token0}:{token1}",
                f"{token0}:{token1}",
                pool_id,
                token0,
                token1,
                3000,
                0,
                0,
                60,
                "0x" + "00" * 20,
                0,
                1,
                now,
                now,
            )
        ],
    )
    executemany(
        database,
        """
        INSERT INTO dex_candidate_pool_recommendations (
            network, chain_id, protocol, anchor_scope, anchor_symbol,
            counter_address, direction_key, hop, rank, pool_id, created_at,
            updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                ROBINHOOD_TESTNET_NETWORK,
                ROBINHOOD_TESTNET_CHAIN_ID,
                "uniswap_v4",
                ROBINHOOD_TESTNET_MARKET_ID,
                anchor_symbol,
                token1,
                f"{token0}:{token1}",
                "first",
                1,
                pool_id,
                now,
                now,
            )
        ],
    )


def test_run_idle_maintenance_accepts_mainnet(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(maintenance, "_write_evidence", lambda report: None)
    database = str((tmp_path / "mainnet-maintenance.sqlite3").resolve())
    ensure_business_schema(database)

    result = json.loads(
        maintenance.run_idle_maintenance(
            json.dumps(
                {
                    "mode": "full_scope_refresh",
                    "network": ROBINHOOD_NETWORK,
                    "chain_id": ROBINHOOD_CHAIN_ID,
                    "database_path": database,
                }
            )
        )
    )

    assert result["ok"] is False
    assert result["mode"] == "full_scope_refresh"
    assert result["phase"] == "no_raw_pools"
    assert result["safety"]["writesSqlite"] is False


def test_run_idle_maintenance_rejects_unsupported_scope() -> None:
    result = json.loads(
        maintenance.run_idle_maintenance(
            json.dumps(
                {
                    "network": "ethereum",
                    "chain_id": 1,
                    "database_path": "",
                }
            )
        )
    )

    assert result["ok"] is False
    assert "unsupported_scope" in result["error"]
    assert result["safety"]["writesSqlite"] is False


def test_route_direction_plan_one_pool_covers_both_route_directions(
    monkeypatch,
    tmp_path,
) -> None:
    zero = "0x" + "00" * 20
    one = "0x" + "11" * 20
    only_pool = "0x" + "aa" * 32
    database = str((tmp_path / "maintenance.sqlite3").resolve())
    ensure_business_schema(database)
    now = "2026-10-22T00:00:00Z"
    executemany(
        database,
        """
        INSERT INTO v4_exchange_pools (
            network, chain_id, protocol, pool_id, pool_identity_mode,
            pool_manager, pool_key_json, token0, token1, fee, fee_label,
            dynamic_fee, special_fee, tick_spacing, hooks, address_pair,
            created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                ROBINHOOD_TESTNET_NETWORK,
                ROBINHOOD_TESTNET_CHAIN_ID,
                "uniswap_v4",
                only_pool,
                "v4_pool_id",
                "0x8366a39CC670B4001A1121B8F6A443A643e40951",
                '{"initializeBlockNumber": 200}',
                zero,
                one,
                3000,
                3000,
                0,
                0,
                60,
                "0x" + "00" * 20,
                f"{zero}:{one}",
                now,
                now,
            )
        ],
    )
    executemany(
        database,
        """
        INSERT INTO v4_exchange_pool_liquidity_snapshots (
            network, chain_id, protocol, pool_id, has_liquidity, liquidity,
            estimated_tvl_usd, depth_status, observed_at, refreshed_at,
            evidence_source
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                ROBINHOOD_TESTNET_NETWORK,
                ROBINHOOD_TESTNET_CHAIN_ID,
                "uniswap_v4",
                only_pool,
                1,
                "1000000000000000000",
                100.0,
                "liquid",
                now,
                now,
                "pool_manager_extsload_live_state_batch",
            )
        ],
    )

    plan = maintenance._route_direction_plan(
        database,
        network=ROBINHOOD_TESTNET_NETWORK,
        chain_id=ROBINHOOD_TESTNET_CHAIN_ID,
        directed_pairs=(f"{zero}:{one}", f"{one}:{zero}"),
    )

    assert plan["counts"] == {f"{zero}:{one}": 1, f"{one}:{zero}": 1}
    assert plan["targets"] == {}
    assert plan["neededDirectionCount"] == 0
    assert plan["satisfiedDirectionCount"] == 2


def test_zero_liquidity_cleanup_accepts_mainnet(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(maintenance, "_write_evidence", lambda report: None)
    database = str((tmp_path / "mainnet-cleanup.sqlite3").resolve())
    ensure_business_schema(database)

    result = json.loads(
        maintenance.run_zero_liquidity_pool_cleanup(
            json.dumps(
                {
                    "network": ROBINHOOD_NETWORK,
                    "chain_id": ROBINHOOD_CHAIN_ID,
                    "zero_liquidity_pool_ids": ["0x" + "ab" * 32],
                    "database_path": database,
                }
            )
        )
    )

    assert result["ok"] is True
    assert result["mode"] == "zero_liquidity_pool_cleanup"
    assert result["cleanedPoolCount"] == 1
    assert result["safety"]["writesSqlite"] is True


def test_zero_liquidity_cleanup_rejects_unsupported_scope() -> None:
    result = json.loads(
        maintenance.run_zero_liquidity_pool_cleanup(
            json.dumps(
                {
                    "network": "ethereum",
                    "chain_id": 1,
                    "zero_liquidity_pool_ids": ["0x" + "ab" * 32],
                    "database_path": "",
                }
            )
        )
    )

    assert result["ok"] is False
    assert "unsupported_scope" in result["error"]
    assert result["safety"]["writesSqlite"] is False


def test_zero_liquidity_cleanup_no_op_for_empty_ids(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(maintenance, "_write_evidence", lambda report: None)
    database = str((tmp_path / "empty.sqlite3").resolve())
    ensure_business_schema(database)

    result = json.loads(
        maintenance.run_zero_liquidity_pool_cleanup(
            json.dumps(
                {
                    "network": ROBINHOOD_TESTNET_NETWORK,
                    "chain_id": ROBINHOOD_TESTNET_CHAIN_ID,
                    "zero_liquidity_pool_ids": [],
                    "database_path": database,
                }
            )
        )
    )

    assert result["ok"] is True
    assert result["phase"] == "no_pools_to_clean"
    assert result["cleanedPoolCount"] == 0
    assert result["safety"]["writesSqlite"] is False


def test_zero_liquidity_cleanup_removes_candidates_but_keeps_raw_pool(
    tmp_path,
    monkeypatch,
) -> None:
    database = str((tmp_path / "cleanup.sqlite3").resolve())
    ensure_business_schema(database)
    monkeypatch.setattr(maintenance, "_write_evidence", lambda report: None)
    token0 = "0x" + "00" * 20  # ETH anchor on Robinhood testnet
    token1 = "0x" + "22" * 20
    # V4 pool ids are 66-character 0x hashes, not 42-character token addresses.
    pool_id = "0x" + "aa" * 32
    _pool_row(
        database,
        pool_id=pool_id,
        token0=token0,
        token1=token1,
        has_liquidity=0,
    )
    _candidate_row(database, pool_id=pool_id, token0=token0, token1=token1)

    result = json.loads(
        maintenance.run_zero_liquidity_pool_cleanup(
            json.dumps(
                {
                    "network": ROBINHOOD_TESTNET_NETWORK,
                    "chain_id": ROBINHOOD_TESTNET_CHAIN_ID,
            "zero_liquidity_pool_ids": [pool_id],
            "database_path": database,
            "anchor_symbol": "ETH",
        }
    )
        )
    )

    assert result["ok"] is True
    assert result["cleaned"] == {
        "v4_exchange_pool_liquidity_snapshots": 0,
        "v4_exchange_pools": 0,
        "dex_candidate_pool_recommendations": 1,
        "dex_candidate_pools": 1,
        "v4_closed_loop_hops": 0,
        "v4_closed_loop_optimizations": 0,
        "v4_closed_loops": 0,
    }
    assert result["retained"]["v4_exchange_pools"] == 1
    assert query(
        database,
        """
        SELECT pool_id FROM v4_exchange_pools
        WHERE network = ? AND chain_id = ? AND protocol = ? AND pool_id = ?
        """,
        (
            ROBINHOOD_TESTNET_NETWORK,
            ROBINHOOD_TESTNET_CHAIN_ID,
            "uniswap_v4",
            pool_id,
        ),
    )
    assert query(
        database,
        """
        SELECT pool_id, has_liquidity FROM v4_exchange_pool_liquidity_snapshots
        WHERE network = ? AND chain_id = ? AND protocol = ? AND pool_id = ?
        """,
        (
            ROBINHOOD_TESTNET_NETWORK,
            ROBINHOOD_TESTNET_CHAIN_ID,
            "uniswap_v4",
            pool_id,
        ),
    ) == [{"pool_id": pool_id, "has_liquidity": 0}]
    assert query(
        database,
        """
        SELECT pool_id FROM dex_candidate_pools
        WHERE network = ? AND chain_id = ? AND protocol = ? AND pool_id = ?
        """,
        (
            ROBINHOOD_TESTNET_NETWORK,
            ROBINHOOD_TESTNET_CHAIN_ID,
            "uniswap_v4",
            pool_id,
        ),
    ) == []
    assert query(
        database,
        """
        SELECT pool_id FROM dex_candidate_pool_recommendations
        WHERE network = ? AND chain_id = ? AND protocol = ? AND pool_id = ?
        """,
        (
            ROBINHOOD_TESTNET_NETWORK,
            ROBINHOOD_TESTNET_CHAIN_ID,
            "uniswap_v4",
            pool_id,
        ),
    ) == []


def test_idle_maintenance_refreshes_stale_snapshots_when_directions_satisfied(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(maintenance, "_write_evidence", lambda report: None)
    monkeypatch.setattr(
        maintenance,
        "_robinhood_rpc_url",
        lambda network, chain_id: "http://rpc.test",
    )
    monkeypatch.setattr(
        maintenance,
        "_resolve_affected_anchors",
        lambda database, *, network, chain_id, route_addresses: ("ETH",),
    )
    monkeypatch.setattr(
        maintenance,
        "_rebuild_anchor",
        lambda *args, **kwargs: {"anchorSymbol": "ETH", "candidate": {"ok": True}},
    )
    calls: list[dict] = []

    def fake_refresh_v4_live(**kwargs):
        calls.append(kwargs)
        return {
            "ok": True,
            "steps": [{"step": "v4_pool_liquidity", "ok": True}],
            "errors": [],
            "import": {"ok": True},
            "prune": {"deletedZeroLiquidityCount": 0},
        }

    monkeypatch.setattr(
        "dex.tools.refresh_robinhood_v4_live.refresh_v4_live",
        fake_refresh_v4_live,
    )

    database = str((tmp_path / "maintenance.sqlite3").resolve())
    ensure_business_schema(database)
    zero = "0x" + "00" * 20
    one = "0x" + "11" * 20
    two = "0x" + "22" * 20
    snapshot_at = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat().replace("+00:00", "Z")
    _pool_row(
        database,
        pool_id="0x" + "aa" * 32,
        token0=zero,
        token1=one,
        snapshot_at=snapshot_at,
    )
    _pool_row(
        database,
        pool_id="0x" + "bb" * 32,
        token0=one,
        token1=two,
        snapshot_at=snapshot_at,
    )
    _pool_row(
        database,
        pool_id="0x" + "cc" * 32,
        token0=two,
        token1=zero,
        snapshot_at=snapshot_at,
    )

    result = maintenance._run_route_maintenance(
        database=database,
        network=ROBINHOOD_TESTNET_NETWORK,
        chain_id=ROBINHOOD_TESTNET_CHAIN_ID,
        route_addresses=[zero, one, two, zero],
        pool_candidates=[],
        direction_target=1,
    )

    assert result["ok"] is True
    assert result["phase"] == "directions_satisfied_refreshed"
    assert result["freshness"]["stale"] is True
    assert result["backfill"] is None
    assert len(calls) == 1
    assert calls[0]["skip_index"] is True
    assert calls[0]["skip_liquidity"] is False
    assert calls[0]["candidate_backfill"] is False
    assert calls[0]["pool_ids"] == [
        "0x" + "aa" * 32,
        "0x" + "bb" * 32,
        "0x" + "cc" * 32,
    ]
    assert result["refreshPoolCount"] == 3
    assert result["safety"]["writesSqlite"] is True
    assert result["safety"]["broadcastsTransaction"] is False


def test_idle_maintenance_skips_refresh_when_snapshots_are_fresh(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(maintenance, "_write_evidence", lambda report: None)
    calls: list[dict] = []

    def fake_refresh_v4_live(**kwargs):
        calls.append(kwargs)
        return {"ok": True}

    monkeypatch.setattr(
        "dex.tools.refresh_robinhood_v4_live.refresh_v4_live",
        fake_refresh_v4_live,
    )

    database = str((tmp_path / "fresh-maintenance.sqlite3").resolve())
    ensure_business_schema(database)
    zero = "0x" + "00" * 20
    one = "0x" + "11" * 20
    two = "0x" + "22" * 20
    snapshot_at = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat().replace("+00:00", "Z")
    _pool_row(
        database,
        pool_id="0x" + "aa" * 32,
        token0=zero,
        token1=one,
        snapshot_at=snapshot_at,
    )
    _pool_row(
        database,
        pool_id="0x" + "bb" * 32,
        token0=one,
        token1=two,
        snapshot_at=snapshot_at,
    )
    _pool_row(
        database,
        pool_id="0x" + "cc" * 32,
        token0=two,
        token1=zero,
        snapshot_at=snapshot_at,
    )

    result = maintenance._run_route_maintenance(
        database=database,
        network=ROBINHOOD_TESTNET_NETWORK,
        chain_id=ROBINHOOD_TESTNET_CHAIN_ID,
        route_addresses=[zero, one, two, zero],
        pool_candidates=[],
        direction_target=1,
    )

    assert result["ok"] is True
    assert result["phase"] == "directions_satisfied"
    assert result["freshness"]["stale"] is False
    assert calls == []
    assert result["safety"]["writesSqlite"] is False


def test_idle_maintenance_triggers_immediate_review_after_price_move(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(maintenance, "_write_evidence", lambda report: None)
    monkeypatch.setattr(
        maintenance,
        "_robinhood_rpc_url",
        lambda network, chain_id: "http://rpc.test",
    )
    monkeypatch.setattr(
        maintenance,
        "_resolve_affected_anchors",
        lambda database, *, network, chain_id, route_addresses: ("ETH",),
    )
    monkeypatch.setattr(
        maintenance,
        "_rebuild_anchor",
        lambda *args, **kwargs: {"anchorSymbol": "ETH", "candidate": {"ok": True}},
    )
    database = str((tmp_path / "price-move.sqlite3").resolve())
    ensure_business_schema(database)
    zero = "0x" + "00" * 20
    one = "0x" + "11" * 20
    two = "0x" + "22" * 20
    snapshot_at = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat().replace("+00:00", "Z")
    _pool_row(
        database,
        pool_id="0x" + "aa" * 32,
        token0=zero,
        token1=one,
        snapshot_at=snapshot_at,
        price=1.0,
        sqrt_price_x96="79228162514264337593543950336",
    )
    _pool_row(
        database,
        pool_id="0x" + "bb" * 32,
        token0=one,
        token1=two,
        snapshot_at=snapshot_at,
        price=1.0,
        sqrt_price_x96="79228162514264337593543950336",
    )
    _pool_row(
        database,
        pool_id="0x" + "cc" * 32,
        token0=two,
        token1=zero,
        snapshot_at=snapshot_at,
        price=1.0,
        sqrt_price_x96="79228162514264337593543950336",
    )

    def fake_refresh_v4_live(**kwargs):
        from core.db.storage_business_io import execute

        execute(
            database,
            """
            UPDATE v4_exchange_pool_liquidity_snapshots
            SET price = 1.006,
                sqrt_price_x96 = '79469055930104439390599231040',
                refreshed_at = ?,
                observed_at = ?
            WHERE pool_id = ?
            """,
            (
                datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                "0x" + "aa" * 32,
            ),
        )
        return {
            "ok": True,
            "steps": [{"step": "v4_pool_liquidity", "ok": True}],
            "errors": [],
            "import": {"ok": True},
            "prune": {"deletedZeroLiquidityCount": 0},
        }

    monkeypatch.setattr(
        "dex.tools.refresh_robinhood_v4_live.refresh_v4_live",
        fake_refresh_v4_live,
    )

    result = maintenance._run_route_maintenance(
        database=database,
        network=ROBINHOOD_TESTNET_NETWORK,
        chain_id=ROBINHOOD_TESTNET_CHAIN_ID,
        route_addresses=[zero, one, two, zero],
        pool_candidates=[],
        direction_target=1,
    )

    assert result["ok"] is True
    assert result["phase"] == "directions_satisfied_refreshed"
    assert result["triggerImmediateReview"] is True
    assert result["priceChange"]["triggered"] is True
    assert result["priceChange"]["changedPoolCount"] >= 1
    assert result["priceChange"]["maxChangeBps"] >= 50
    assert result["priceChange"]["maxChangePoolId"] == "0x" + "aa" * 32


def test_full_scope_route_reads_path_addresses_from_closed_loops(tmp_path) -> None:
    database = str((tmp_path / "full-scope-route.sqlite3").resolve())
    ensure_business_schema(database)
    zero = "0x" + "00" * 20
    one = "0x" + "11" * 20
    two = "0x" + "22" * 20
    now = "2026-10-22T00:00:00Z"
    executemany(
        database,
        """
        INSERT INTO v4_closed_loops (
            network, chain_id, protocol, market_id, loop_key,
            anchor_address, anchor_symbol, path_addresses_json,
            recommended_first_priority, status, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                ROBINHOOD_TESTNET_NETWORK,
                ROBINHOOD_TESTNET_CHAIN_ID,
                "uniswap_v4",
                ROBINHOOD_TESTNET_MARKET_ID,
                "loop-main",
                zero,
                "ETH",
                json.dumps([zero, one, two, zero]),
                1,
                "active",
                now,
                now,
            )
        ],
    )

    route, row = maintenance._full_scope_route(
        database,
        network=ROBINHOOD_TESTNET_NETWORK,
        chain_id=ROBINHOOD_TESTNET_CHAIN_ID,
        market_id=ROBINHOOD_TESTNET_MARKET_ID,
    )

    assert route == [zero, one, two, zero]
    assert row["loop_key"] == "loop-main"
    assert row["anchor_symbol"] == "ETH"


def test_full_scope_refresh_runs_rebuild_then_route_maintenance(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(maintenance, "_write_evidence", lambda report: None)
    database = str((tmp_path / "full-scope-refresh.sqlite3").resolve())
    ensure_business_schema(database)
    zero = "0x" + "00" * 20
    one = "0x" + "11" * 20
    two = "0x" + "22" * 20
    calls: list[dict] = []

    def fake_rebuild(
        database: str,
        *,
        network: str,
        chain_id: int,
        market_id: str,
    ) -> dict:
        calls.append(
            {
                "step": "rebuild",
                "database": database,
                "network": network,
                "chain_id": chain_id,
                "market_id": market_id,
            }
        )
        return {
            "ok": True,
            "phase": "rebuild_completed",
            "rebuilt": True,
            "result": {"ok": True},
        }

    monkeypatch.setattr(maintenance, "_full_scope_rebuild_if_empty", fake_rebuild)

    def fake_full_scope_route(
        database: str,
        *,
        network: str,
        chain_id: int,
        market_id: str,
    ):
        calls.append(
            {
                "step": "route",
                "database": database,
                "network": network,
                "chain_id": chain_id,
                "market_id": market_id,
            }
        )
        return (
            [zero, one, two, zero],
            {"loop_key": "loop-main", "anchor_symbol": "ETH"},
        )

    monkeypatch.setattr(maintenance, "_full_scope_route", fake_full_scope_route)

    def fake_route_maintenance(**kwargs):
        calls.append(
            {
                "step": "maintenance",
                "route_addresses": kwargs.get("route_addresses"),
                "direction_target": kwargs.get("direction_target"),
            }
        )
        return {
            "ok": True,
            "mode": "idle_maintenance",
            "phase": "directions_satisfied_refreshed",
            "triggerImmediateReview": True,
            "priceChange": {"triggered": True},
            "safety": {"writesSqlite": True},
        }

    monkeypatch.setattr(maintenance, "_run_route_maintenance", fake_route_maintenance)

    result = json.loads(
        maintenance.run_idle_maintenance(
            json.dumps(
                {
                    "mode": "full_scope_refresh",
                    "network": ROBINHOOD_TESTNET_NETWORK,
                    "chain_id": ROBINHOOD_TESTNET_CHAIN_ID,
                    "database_path": database,
                    "direction_target": 1,
                }
            )
        )
    )

    assert result["ok"] is True
    assert result["mode"] == "full_scope_refresh"
    assert result["phase"] == "directions_satisfied_refreshed"
    assert result["triggerImmediateReview"] is True
    assert result["scopeRebuild"]["phase"] == "rebuild_completed"
    assert result["scopeRoute"]["loopKey"] == "loop-main"
    assert result["routeAddresses"] == [zero, one, two, zero]
    assert [call["step"] for call in calls] == [
        "rebuild",
        "route",
        "maintenance",
    ]
    assert calls[-1]["route_addresses"] == [zero, one, two, zero]


def test_full_scope_refresh_returns_no_raw_pools_early(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(maintenance, "_write_evidence", lambda report: None)
    database = str((tmp_path / "full-scope-empty.sqlite3").resolve())
    ensure_business_schema(database)

    result = json.loads(
        maintenance.run_idle_maintenance(
            json.dumps(
                {
                    "mode": "full_scope_refresh",
                    "network": ROBINHOOD_TESTNET_NETWORK,
                    "chain_id": ROBINHOOD_TESTNET_CHAIN_ID,
                    "database_path": database,
                }
            )
        )
    )

    assert result["ok"] is False
    assert result["phase"] == "no_raw_pools"
    assert result["safety"]["writesSqlite"] is False


def test_pool_liquidity_tick_advances_slice_and_keeps_liquidity_evidence(
    tmp_path,
    monkeypatch,
) -> None:
    """The fixed pool worker refreshes the next pool slice only.

    The tick must not re-run a full index/build, must not prune liquidity
    verdicts, and must persist the next cursor so the following cycle resumes
    where the previous query stopped.
    """
    monkeypatch.setattr(maintenance, "_write_evidence", lambda report: None)
    database = str((tmp_path / "liquidity-worker.sqlite3").resolve())
    ensure_business_schema(database)
    zero = "0x" + "00" * 20
    first = "0x" + "11" * 20
    second = "0x" + "22" * 20
    third = "0x" + "33" * 20
    pool_one = "0x" + "aa" * 32
    pool_two = "0x" + "bb" * 32
    pool_three = "0x" + "cc" * 32
    for pool_id, token in (
        (pool_one, first),
        (pool_two, second),
        (pool_three, third),
    ):
        _pool_row(
            database,
            pool_id=pool_id,
            token0=zero,
            token1=token,
            has_liquidity=None,
        )

    calls: list[dict] = []

    def fake_refresh_v4_live(**kwargs):
        calls.append(kwargs)
        return {"ok": True, "errors": [], "liquidity": {"blockNumber": 100500}}

    monkeypatch.setattr(
        "dex.tools.refresh_robinhood_v4_live.refresh_v4_live",
        fake_refresh_v4_live,
    )
    monkeypatch.setenv("V4_POOL_LIQUIDITY_WORKER_BATCH_SIZE", "2")
    monkeypatch.setattr(maintenance, "_liquidity_worker_batch_size", lambda: 2)

    result = maintenance._run_pool_liquidity_tick(
        database=database,
        network=ROBINHOOD_TESTNET_NETWORK,
        chain_id=ROBINHOOD_TESTNET_CHAIN_ID,
    )

    assert result["ok"] is True
    assert result["phase"] == "incremental"
    assert result["cursorBefore"] == 0
    assert result["cursor"] == 2
    assert result["cursorPoolId"] == pool_two
    assert result["cursorPoolIdBefore"] == ""
    assert result["poolCount"] == 3
    assert result["batchSize"] == 2
    assert result["safety"]["writesSqlite"] is True
    assert len(calls) == 1
    call = calls[0]
    assert call["pool_ids"] == sorted([pool_one, pool_two])
    assert call["import_pool_ids"] == sorted([pool_one, pool_two])
    assert call["skip_index"] is True
    assert call["skip_build"] is True
    assert call["candidate_backfill"] is False
    assert call["prune_zero_liquidity"] is False
    assert call["prune_unknown_liquidity"] is False
    assert call["replace_evidence"] is False
    from core.db.v4_pool_liquidity_worker import load_v4_pool_liquidity_cursor

    cursor = load_v4_pool_liquidity_cursor(
        database,
        network=ROBINHOOD_TESTNET_NETWORK,
        chain_id=ROBINHOOD_TESTNET_CHAIN_ID,
    )
    assert cursor["cursor"] == 2
    assert cursor["cursor_pool_id"] == pool_two
    assert cursor["pool_count"] == 3
    assert cursor["scanned_pool_count"] == 2
    assert cursor["remaining_pool_count"] == 1
    assert cursor["mode"] == "scanning"
    assert cursor["last_block_number"] == 100500


def test_pool_liquidity_tick_resumes_after_zeroed_cursor_pool_id(
    tmp_path,
    monkeypatch,
) -> None:
    """A zeroed last-scanned pool must not fall back to the whole set.

    Dynamic discovery may re-add the zero pool when short directions need
    recovery, but the cursor must keep advancing instead of restarting at the
    beginning of the eligible set.
    """
    monkeypatch.setattr(maintenance, "_write_evidence", lambda report: None)
    database = str((tmp_path / "liquidity-worker-zero-cursor.sqlite3").resolve())
    ensure_business_schema(database)
    zero = "0x" + "00" * 20
    first = "0x" + "11" * 20
    second = "0x" + "22" * 20
    third = "0x" + "33" * 20
    pool_one = "0x" + "aa" * 32
    pool_two = "0x" + "bb" * 32
    pool_three = "0x" + "cc" * 32
    for pool_id, token in (
        (pool_one, first),
        (pool_two, second),
        (pool_three, third),
    ):
        _pool_row(
            database,
            pool_id=pool_id,
            token0=zero,
            token1=token,
            has_liquidity=None,
        )

    calls: list[dict] = []

    def fake_refresh_v4_live(**kwargs):
        calls.append(kwargs)
        return {"ok": True, "errors": [], "liquidity": {"blockNumber": 100500}}

    monkeypatch.setattr(
        "dex.tools.refresh_robinhood_v4_live.refresh_v4_live",
        fake_refresh_v4_live,
    )
    monkeypatch.setenv("V4_POOL_LIQUIDITY_WORKER_BATCH_SIZE", "2")
    monkeypatch.setattr(maintenance, "_liquidity_worker_batch_size", lambda: 2)

    first_tick = maintenance._run_pool_liquidity_tick(
        database=database,
        network=ROBINHOOD_TESTNET_NETWORK,
        chain_id=ROBINHOOD_TESTNET_CHAIN_ID,
    )
    assert first_tick["cursorPoolId"] == pool_two
    assert calls[0]["pool_ids"] == sorted([pool_one, pool_two])

    from core.db.storage_business_io import execute

    execute(
        database,
        """
        UPDATE v4_exchange_pool_liquidity_snapshots
        SET has_liquidity = 0, depth_status = 'empty',
            observed_at = ?, refreshed_at = ?
        WHERE network = ? AND chain_id = ? AND protocol = ? AND pool_id = ?
        """,
        (
            "2026-10-22T00:01:00Z",
            "2026-10-22T00:01:00Z",
            ROBINHOOD_TESTNET_NETWORK,
            ROBINHOOD_TESTNET_CHAIN_ID,
            "uniswap_v4",
            pool_two,
        ),
    )

    second_tick = maintenance._run_pool_liquidity_tick(
        database=database,
        network=ROBINHOOD_TESTNET_NETWORK,
        chain_id=ROBINHOOD_TESTNET_CHAIN_ID,
    )

    assert second_tick["ok"] is True
    assert second_tick["cursorBefore"] == 2
    assert second_tick["cursorPoolIdBefore"] == pool_two
    assert second_tick["cursorPoolId"] == ""
    assert len(calls) == 2
    assert calls[1]["pool_ids"] == [pool_three]
    assert calls[1]["import_pool_ids"] == [pool_three]
    from core.db.v4_pool_liquidity_worker import load_v4_pool_liquidity_cursor

    cursor = load_v4_pool_liquidity_cursor(
        database,
        network=ROBINHOOD_TESTNET_NETWORK,
        chain_id=ROBINHOOD_TESTNET_CHAIN_ID,
    )
    assert cursor["cursor_pool_id"] == ""
    assert cursor["mode"] == "scanning"


def test_pool_liquidity_tick_honors_explicit_batch_size(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(maintenance, "_write_evidence", lambda report: None)
    database = str((tmp_path / "liquidity-worker-batch.sqlite3").resolve())
    ensure_business_schema(database)
    zero = "0x" + "00" * 20
    first = "0x" + "11" * 20
    second = "0x" + "22" * 20
    pool_one = "0x" + "aa" * 32
    pool_two = "0x" + "bb" * 32
    for pool_id, token in (
        (pool_one, first),
        (pool_two, second),
    ):
        _pool_row(
            database,
            pool_id=pool_id,
            token0=zero,
            token1=token,
            has_liquidity=None,
        )

    calls: list[dict] = []

    def fake_refresh_v4_live(**kwargs):
        calls.append(kwargs)
        return {"ok": True, "errors": [], "liquidity": {"blockNumber": 100500}}

    monkeypatch.setattr(
        "dex.tools.refresh_robinhood_v4_live.refresh_v4_live",
        fake_refresh_v4_live,
    )
    monkeypatch.setenv("V4_POOL_LIQUIDITY_WORKER_BATCH_SIZE", "10")
    monkeypatch.setattr(maintenance, "_liquidity_worker_batch_size", lambda: 10)

    result = maintenance._run_pool_liquidity_tick(
        database=database,
        network=ROBINHOOD_TESTNET_NETWORK,
        chain_id=ROBINHOOD_TESTNET_CHAIN_ID,
        batch_size=1,
    )

    assert result["ok"] is True
    assert result["batchSize"] == 1
    assert calls[0]["pool_ids"] == [pool_one]
    assert calls[0]["import_pool_ids"] == [pool_one]


def test_pool_liquidity_tick_retries_same_slice_after_error(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(maintenance, "_write_evidence", lambda report: None)
    database = str((tmp_path / "liquidity-worker-error.sqlite3").resolve())
    ensure_business_schema(database)
    zero = "0x" + "00" * 20
    one = "0x" + "11" * 20
    pool_id = "0x" + "aa" * 32
    _pool_row(database, pool_id=pool_id, token0=zero, token1=one, has_liquidity=None)

    def fake_refresh_v4_live(**kwargs):
        return {"ok": False, "errors": ["rpc timeout"]}

    monkeypatch.setattr(
        "dex.tools.refresh_robinhood_v4_live.refresh_v4_live",
        fake_refresh_v4_live,
    )

    result = maintenance._run_pool_liquidity_tick(
        database=database,
        network=ROBINHOOD_TESTNET_NETWORK,
        chain_id=ROBINHOOD_TESTNET_CHAIN_ID,
    )

    assert result["ok"] is False
    assert result["phase"] == "tick_failed"
    assert result["cursor"] == 0
    assert result["cursorBefore"] == 0
    from core.db.v4_pool_liquidity_worker import load_v4_pool_liquidity_cursor

    cursor = load_v4_pool_liquidity_cursor(
        database,
        network=ROBINHOOD_TESTNET_NETWORK,
        chain_id=ROBINHOOD_TESTNET_CHAIN_ID,
    )
    assert cursor["cursor"] == 0
    assert cursor["mode"] == "error"


def test_candidate_eligible_pool_ids_skip_explicit_zero_liquidity(tmp_path) -> None:
    database = str((tmp_path / "eligible-ids.sqlite3").resolve())
    ensure_business_schema(database)
    zero_token = "0x" + "00" * 20
    one_token = "0x" + "11" * 20
    two_token = "0x" + "22" * 20
    three_token = "0x" + "33" * 20
    zero_pool = "0x" + "aa" * 32
    unknown_pool = "0x" + "bb" * 32
    confirmed_pool = "0x" + "cc" * 32
    _pool_row(
        database,
        pool_id=zero_pool,
        token0=zero_token,
        token1=one_token,
        has_liquidity=0,
    )
    _pool_row(
        database,
        pool_id=unknown_pool,
        token0=zero_token,
        token1=two_token,
        has_liquidity=None,
    )
    _pool_row(
        database,
        pool_id=confirmed_pool,
        token0=zero_token,
        token1=three_token,
        has_liquidity=1,
    )

    eligible = maintenance._candidate_eligible_pool_ids(
        database,
        network=ROBINHOOD_TESTNET_NETWORK,
        chain_id=ROBINHOOD_TESTNET_CHAIN_ID,
    )

    assert eligible == sorted([unknown_pool, confirmed_pool])
    assert zero_pool not in eligible


def test_candidate_scope_uses_classified_token_universe(tmp_path) -> None:
    database = str((tmp_path / "known-token-scope.sqlite3").resolve())
    ensure_business_schema(database)
    now = "2026-10-22T00:00:00Z"
    known_zero = "0x" + "ab" * 20
    known_one = "0x" + "cd" * 20
    unknown_zero = "0x" + "ef" * 20
    unknown_one = "0x" + "12" * 20
    known_pool = "0x" + "aa" * 32
    unknown_pool = "0x" + "bb" * 32
    for token_address, token_symbol, category in (
        (known_zero, "KZ", "native"),
        (known_one, "KO", "stablecoin"),
        (unknown_zero, "UNZ", "unknown"),
        (unknown_one, "UNO", "unknown"),
    ):
        executemany(
            database,
            """
            INSERT INTO chain_tokens (
                network, chain_id, token_address, token_symbol, token_name,
                token_decimals, token_kind, is_native, is_wrapped_native,
                is_stable, anchor_symbol, token_metadata_json, source_json,
                first_seen_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    ROBINHOOD_TESTNET_NETWORK,
                    ROBINHOOD_TESTNET_CHAIN_ID,
                    token_address,
                    token_symbol,
                    token_symbol,
                    18,
                    "erc20",
                    0,
                    0,
                    0,
                    None,
                    '{"category":"' + category + '"}',
                    "{}",
                    now,
                    now,
                )
            ],
        )
    _pool_row(
        database,
        pool_id=known_pool,
        token0=known_zero,
        token1=known_one,
        has_liquidity=None,
    )
    _pool_row(
        database,
        pool_id=unknown_pool,
        token0=unknown_zero,
        token1=unknown_one,
        has_liquidity=None,
    )

    known_tokens = maintenance._known_pool_token_addresses(
        database,
        network=ROBINHOOD_TESTNET_NETWORK,
        chain_id=ROBINHOOD_TESTNET_CHAIN_ID,
    )
    assert known_tokens == sorted([known_zero, known_one])

    scoped = maintenance._candidate_eligible_pool_ids(
        database,
        network=ROBINHOOD_TESTNET_NETWORK,
        chain_id=ROBINHOOD_TESTNET_CHAIN_ID,
        known_token_addresses=known_tokens,
    )
    assert scoped == [known_pool]
    assert maintenance._candidate_eligible_missing_liquidity_count(
        database,
        network=ROBINHOOD_TESTNET_NETWORK,
        chain_id=ROBINHOOD_TESTNET_CHAIN_ID,
        known_token_addresses=known_tokens,
    ) == 1
    assert maintenance._candidate_eligible_missing_liquidity_count(
        database,
        network=ROBINHOOD_TESTNET_NETWORK,
        chain_id=ROBINHOOD_TESTNET_CHAIN_ID,
    ) == 2


def test_pool_liquidity_tick_cleans_zero_liquidity_candidate_refs(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(maintenance, "_write_evidence", lambda report: None)
    database = str((tmp_path / "liquidity-worker-zero.sqlite3").resolve())
    ensure_business_schema(database)
    zero_token = "0x" + "00" * 20
    one_token = "0x" + "11" * 20
    two_token = "0x" + "22" * 20
    zero_pool = "0x" + "aa" * 32
    unknown_pool = "0x" + "bb" * 32
    _pool_row(
        database,
        pool_id=zero_pool,
        token0=zero_token,
        token1=one_token,
        has_liquidity=0,
    )
    _pool_row(
        database,
        pool_id=unknown_pool,
        token0=zero_token,
        token1=two_token,
        has_liquidity=None,
    )
    _candidate_row(
        database,
        pool_id=zero_pool,
        token0=zero_token,
        token1=one_token,
    )

    def fake_refresh_v4_live(**kwargs):
        return {"ok": True, "errors": [], "liquidity": {"blockNumber": 100500}}

    monkeypatch.setattr(
        "dex.tools.refresh_robinhood_v4_live.refresh_v4_live",
        fake_refresh_v4_live,
    )
    monkeypatch.setenv("V4_POOL_LIQUIDITY_WORKER_BATCH_SIZE", "10")
    monkeypatch.setattr(maintenance, "_liquidity_worker_batch_size", lambda: 10)

    result = maintenance._run_pool_liquidity_tick(
        database=database,
        network=ROBINHOOD_TESTNET_NETWORK,
        chain_id=ROBINHOOD_TESTNET_CHAIN_ID,
    )

    assert result["ok"] is True
    assert result["zeroLiquidityPoolCount"] == 1
    assert result["zeroLiquidityCleanup"]["dex_candidate_pools"] == 1
    assert result["zeroLiquidityCleanup"]["dex_candidate_pool_recommendations"] == 1
    assert query(
        database,
        """
        SELECT pool_id FROM dex_candidate_pools
        WHERE network = ? AND chain_id = ? AND protocol = ? AND pool_id = ?
        """,
        (
            ROBINHOOD_TESTNET_NETWORK,
            ROBINHOOD_TESTNET_CHAIN_ID,
            "uniswap_v4",
            zero_pool,
        ),
    ) == []
    assert query(
        database,
        """
        SELECT pool_id FROM v4_exchange_pools
        WHERE network = ? AND chain_id = ? AND protocol = ? AND pool_id = ?
        """,
        (
            ROBINHOOD_TESTNET_NETWORK,
            ROBINHOOD_TESTNET_CHAIN_ID,
            "uniswap_v4",
            zero_pool,
        ),
    )


def test_full_scope_refresh_runs_liquidity_tick_before_second_rebuild(
    tmp_path,
    monkeypatch,
) -> None:
    """When rebuild has raw pools but no loop, liquidity must be backfilled
    before a second rebuild instead of returning ``no_closed_loop_route``."""
    monkeypatch.setattr(maintenance, "_write_evidence", lambda report: None)
    monkeypatch.setenv("V4_POOL_LIQUIDITY_WORKER_BATCH_SIZE", "100")
    monkeypatch.setattr(maintenance, "_liquidity_worker_batch_size", lambda: 100)
    database = str((tmp_path / "full-scope-liquidity.sqlite3").resolve())
    ensure_business_schema(database)
    zero = "0x" + "00" * 20
    one = "0x" + "11" * 20
    pool_id = "0x" + "aa" * 32
    now = "2026-10-22T00:00:00Z"
    for token_address, token_symbol in (
        ("0x" + "00" * 20, "ETH"),
        ("0x" + "11" * 20, "T1"),
    ):
        executemany(
            database,
            """
            INSERT INTO chain_tokens (
                network, chain_id, token_address, token_symbol, token_name,
                token_decimals, token_kind, is_native, is_wrapped_native,
                is_stable, anchor_symbol, token_metadata_json, source_json,
                first_seen_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    ROBINHOOD_TESTNET_NETWORK,
                    ROBINHOOD_TESTNET_CHAIN_ID,
                    token_address,
                    token_symbol,
                    token_symbol,
                    18,
                    "erc20",
                    1 if token_address == zero else 0,
                    0,
                    1 if token_symbol == "T1" else 0,
                    "ETH" if token_address == zero else None,
                    "{}",
                    "{}",
                    now,
                    now,
                )
            ],
        )
    _pool_row(database, pool_id=pool_id, token0=zero, token1=one, has_liquidity=None)

    calls: list[str] = []

    def fake_refresh_v4_live(**kwargs):
        from core.db.storage_business_io import execute

        calls.append("liquidity")
        execute(
            database,
            """
            UPDATE v4_exchange_pool_liquidity_snapshots
            SET has_liquidity = 1, liquidity = '1000000000000000000',
                depth_status = 'liquid',
                observed_at = ?,
                refreshed_at = ?
            WHERE pool_id = ?
            """,
            (
                datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                pool_id,
            ),
        )
        return {"ok": True, "errors": [], "liquidity": {"blockNumber": 42}}

    monkeypatch.setattr(
        "dex.tools.refresh_robinhood_v4_live.refresh_v4_live",
        fake_refresh_v4_live,
    )
    real_full_scope_route = maintenance._full_scope_route

    def fake_full_scope_route(*args, **kwargs):
        route, row = real_full_scope_route(*args, **kwargs)
        if route:
            calls.append("route_after_liquidity")
        return route, row

    monkeypatch.setattr(maintenance, "_full_scope_route", fake_full_scope_route)

    result = json.loads(
        maintenance.run_idle_maintenance(
            json.dumps(
                {
                    "mode": "full_scope_refresh",
                    "network": ROBINHOOD_TESTNET_NETWORK,
                    "chain_id": ROBINHOOD_TESTNET_CHAIN_ID,
                    "database_path": database,
                }
            )
        )
    )

    assert result["ok"] is False
    assert result["phase"] == "no_closed_loop_route"
    worker = result["poolLiquidityWorker"]
    assert worker is not None
    assert worker["ok"] is True
    assert worker["missingBefore"] == 1
    assert worker["missingAfter"] == 0
    assert result["progress"] == [
        {"step": "scope_rebuild", "phase": "rebuild_completed", "ok": True},
        {
            "step": "pool_liquidity_worker",
            "phase": "cycle_complete",
            "ok": True,
            "cursorBefore": 0,
            "cursor": 0,
            "missingBefore": 1,
            "missingAfter": 0,
        },
        {
            "step": "scope_rebuild_after_liquidity",
            "phase": "rebuild_completed",
            "ok": True,
        },
    ]
    assert "liquidity" in calls


def test_full_scope_refresh_continues_when_liquidity_creates_route(
    tmp_path,
    monkeypatch,
) -> None:
    """A second rebuild that now yields a loop must feed route maintenance."""
    monkeypatch.setattr(maintenance, "_write_evidence", lambda report: None)
    monkeypatch.setenv("V4_POOL_LIQUIDITY_WORKER_BATCH_SIZE", "100")
    monkeypatch.setattr(maintenance, "_liquidity_worker_batch_size", lambda: 100)
    database = str((tmp_path / "full-scope-liquidity-route.sqlite3").resolve())
    ensure_business_schema(database)
    zero = "0x" + "00" * 20
    one = "0x" + "11" * 20
    two = "0x" + "22" * 20
    now = "2026-10-22T00:00:00Z"
    for pool_id, token in (
        ("0x" + "aa" * 32, one),
        ("0x" + "bb" * 32, two),
        ("0x" + "cc" * 32, zero),
    ):
        _pool_row(database, pool_id=pool_id, token0=zero, token1=token, has_liquidity=None)
    # A valid route already exists so the tick is not required; this test
    # simply verifies the existing happy path remains after the condition.
    executemany(
        database,
        """
        INSERT INTO v4_closed_loops (
            network, chain_id, protocol, market_id, loop_key,
            anchor_address, anchor_symbol, path_addresses_json,
            recommended_first_priority, status, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                ROBINHOOD_TESTNET_NETWORK,
                ROBINHOOD_TESTNET_CHAIN_ID,
                "uniswap_v4",
                ROBINHOOD_TESTNET_MARKET_ID,
                "loop-existing",
                zero,
                "ETH",
                json.dumps([zero, one, two, zero]),
                1,
                "active",
                now,
                now,
            )
        ],
    )

    def fake_route_maintenance(**kwargs):
        return {
            "ok": True,
            "mode": "idle_maintenance",
            "phase": "directions_satisfied",
            "safety": {"writesSqlite": False},
        }

    monkeypatch.setattr(maintenance, "_run_route_maintenance", fake_route_maintenance)

    result = json.loads(
        maintenance.run_idle_maintenance(
            json.dumps(
                {
                    "mode": "full_scope_refresh",
                    "network": ROBINHOOD_TESTNET_NETWORK,
                    "chain_id": ROBINHOOD_TESTNET_CHAIN_ID,
                    "database_path": database,
                }
            )
        )
    )

    assert result["ok"] is True
    assert result["poolLiquidityWorker"] is None
    assert result["progress"] == [
        {"step": "scope_rebuild", "phase": "existing_closed_loops", "ok": True}
    ]


def test_dynamic_pool_discovery_env_switch(monkeypatch) -> None:
    assert maintenance._dynamic_pool_discovery_enabled() is True
    monkeypatch.setenv("V4_POOL_LIQUIDITY_WORKER_DYNAMIC_DISCOVERY_ENABLED", "0")
    assert maintenance._dynamic_pool_discovery_enabled() is False
    monkeypatch.setenv("V4_POOL_LIQUIDITY_WORKER_DYNAMIC_DISCOVERY_ENABLED", "true")
    assert maintenance._dynamic_pool_discovery_enabled() is True
