"""Unit tests for the read-only testnet V4 directional backfill evidence tool."""

from __future__ import annotations

from core.db.storage_business_io import executemany
from core.db.storage_business_schema import ensure_business_schema
import dex.tools.collect_robinhood_v4_backfill_evidence as evidence_tool
from dex.intent_trade.chains.robinhood_common.chain_config import (
    ROBINHOOD_TESTNET_CHAIN_ID,
    ROBINHOOD_TESTNET_NETWORK,
)


def _pool(
    pool_id: str,
    token0: str,
    token1: str,
    initialize_block: int,
) -> dict:
    return {
        "poolId": pool_id,
        "token0": token0,
        "token1": token1,
        "fee": 3000,
        "tickSpacing": 60,
        "hooks": "0x" + "00" * 20,
        "initializeBlockNumber": initialize_block,
    }


def test_merge_direction_selections_prefers_newest_and_deduplicates():
    zero = "0x" + "00" * 20
    one = "0x" + "11" * 20
    two = "0x" + "22" * 20
    forward = f"{zero}:{one}"
    reverse = f"{one}:{zero}"
    old = _pool("0x" + "aa" * 32, zero, one, 100)
    new = _pool("0x" + "bb" * 32, one, zero, 200)
    outsider = _pool("0x" + "cc" * 32, one, two, 300)

    selected, counts = evidence_tool._merge_direction_selections(
        targets={forward: 2, reverse: 2},
        indexed_selected={forward: [old]},
        scanned_pools=[old, new, outsider],
    )

    assert counts == {forward: 2, reverse: 2}
    assert selected[forward] == [new, old]
    assert selected[reverse] == [new, old]


def test_confirm_indexed_candidates_counts_only_live_liquidity(monkeypatch, tmp_path):
    zero = "0x" + "00" * 20
    one = "0x" + "11" * 20
    forward = f"{zero}:{one}"
    reverse = f"{one}:{zero}"
    liquid_a = "0x" + "aa" * 32
    liquid_b = "0x" + "bb" * 32
    zero_liquidity = "0x" + "cc" * 32
    database = str((tmp_path / "evidence.sqlite3").resolve())
    ensure_business_schema(database)
    now = "2026-10-17T00:00:00Z"
    rows = [
        (liquid_a, one, zero, 300, '{"initializeBlockNumber": 300}'),
        (liquid_b, zero, one, 200, '{"initializeBlockNumber": 200}'),
        (zero_liquidity, one, zero, 100, '{"initializeBlockNumber": 100}'),
    ]
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
                pool_key_json,
                token0,
                token1,
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
            for pool_id, token0, token1, _block, pool_key_json in rows
        ],
    )
    seen_factory: dict = {}

    def fake_factory(**kwargs):
        seen_factory["kwargs"] = kwargs

        def confirm(pools):
            return {
                str(pool.get("poolId") or "").lower()
                for pool in pools
                if str(pool.get("poolId") or "") != zero_liquidity
            }

        return confirm

    monkeypatch.setattr(evidence_tool, "_confirm_pool_liquidity", fake_factory)
    result = evidence_tool._confirm_indexed_direction_candidates(
        database=database,
        network=ROBINHOOD_TESTNET_NETWORK,
        chain_id=ROBINHOOD_TESTNET_CHAIN_ID,
        rpc_url="https://rpc.testnet.example",
        pool_manager="0x8366a39CC670B4001A1121B8F6A443A643e40951",
        direction_targets={forward: 5, reverse: 5},
        batch_size=2,
    )

    assert result["indexedPoolCount"] == 3
    assert result["confirmedPoolCount"] == 2
    assert result["counts"] == {forward: 2, reverse: 2}
    assert result["remaining"] == {forward: 5, reverse: 5}
    assert {str(pool["poolId"]) for pool in result["selectedPools"][forward]} == {
        liquid_a,
        liquid_b,
    }
    assert seen_factory["kwargs"]["batch_size"] == 2


def test_collect_report_skips_reverse_scan_when_index_satisfied(monkeypatch, tmp_path):
    from dex.intent_trade.chains.robinhood import config as robinhood_config
    from dex.intent_trade.chains.robinhood_testnet import v4_pool_rpc

    zero = "0x" + "00" * 20
    one = "0x" + "11" * 20
    forward = f"{zero}:{one}"
    reverse = f"{one}:{zero}"
    pool = _pool("0x" + "ab" * 32, one, zero, 500)
    fake_plan = lambda **kwargs: {  # noqa: E731
        "targets": {forward: 1, reverse: 1},
        "floor_block": 100,
        "neededDirectionCount": 2,
        "basis": "direction_candidate_shortfall",
    }
    monkeypatch.setattr(
        evidence_tool,
        "absolute_database_path",
        lambda _database: str((tmp_path / "evidence.sqlite3").resolve()),
    )
    monkeypatch.setattr(
        evidence_tool,
        "_direction_plan",
        fake_plan,
    )
    monkeypatch.setattr(robinhood_config, "testnet_rpc_urls_from_env", lambda: [])
    monkeypatch.setattr(v4_pool_rpc, "_rpc_url_from_env_or_default", lambda: "https://rpc.testnet.example")
    monkeypatch.setattr(
        evidence_tool,
        "_confirm_indexed_direction_candidates",
        lambda **kwargs: {
            "counts": {forward: 1, reverse: 1},
            "remaining": {},
            "pools": [pool],
            "selectedPools": {forward: [pool], reverse: [pool]},
            "confirmedPoolCount": 1,
            "indexedPoolCount": 1,
            "allPoolIds": [pool["poolId"].lower()],
        },
    )

    report = evidence_tool.collect_report(
        database="",
        network=ROBINHOOD_TESTNET_NETWORK,
        chain_id=ROBINHOOD_TESTNET_CHAIN_ID,
        rpc_url="https://rpc.testnet.example",
        pool_manager="0x8366a39CC670B4001A1121B8F6A443A643e40951",
        directions=(forward, reverse),
    )

    scan = report["scan"]
    assert scan["scanDirection"] == "existing_pool_index_liquidity_confirmation"
    assert scan["satisfiedDirectionCount"] == 2
    assert scan["selectedPoolCount"] == 1
    assert scan["stoppedReason"] == "existing_pool_index_liquidity_satisfied"


def test_collect_report_merges_index_and_incremental_scan(monkeypatch, tmp_path):
    import dex.intent_trade.chains.robinhood_common.v4_pool_index_core as index_core
    from dex.intent_trade.chains.robinhood import config as robinhood_config
    from dex.intent_trade.chains.robinhood_testnet import v4_pool_rpc

    zero = "0x" + "00" * 20
    one = "0x" + "11" * 20
    forward = f"{zero}:{one}"
    reverse = f"{one}:{zero}"
    indexed_pool = _pool("0x" + "aa" * 32, zero, one, 300)
    scanned_pool = _pool("0x" + "bb" * 32, one, zero, 400)
    fake_plan = lambda **kwargs: {  # noqa: E731
        "targets": {forward: 2, reverse: 2},
        "floor_block": 100,
        "neededDirectionCount": 2,
        "basis": "direction_candidate_shortfall",
    }
    monkeypatch.setattr(
        evidence_tool,
        "absolute_database_path",
        lambda _database: str((tmp_path / "evidence.sqlite3").resolve()),
    )
    monkeypatch.setattr(
        evidence_tool,
        "_direction_plan",
        fake_plan,
    )
    monkeypatch.setattr(robinhood_config, "testnet_rpc_urls_from_env", lambda: [])
    monkeypatch.setattr(v4_pool_rpc, "_rpc_url_from_env_or_default", lambda: "https://rpc.testnet.example")
    monkeypatch.setattr(
        evidence_tool,
        "_confirm_indexed_direction_candidates",
        lambda **kwargs: {
            "counts": {forward: 1, reverse: 1},
            "remaining": {forward: 1, reverse: 1},
            "pools": [indexed_pool],
            "selectedPools": {forward: [indexed_pool]},
            "confirmedPoolCount": 1,
            "indexedPoolCount": 1,
            "allPoolIds": [indexed_pool["poolId"].lower()],
        },
    )
    calls: dict = {}

    def fake_backfill(**kwargs):
        calls["kwargs"] = kwargs
        return {
            "pools": [scanned_pool],
            "fromBlock": 100,
            "toBlock": 500,
            "scanChunkSize": 1000,
            "providerCount": 1,
            "providerUrls": ["https://rpc.testnet.example"],
            "scannedSegmentCount": 1,
            "initializeLogCount": 1,
            "discoveryMethod": "reverse_initialize_logs",
            "stoppedReason": "direction_targets_satisfied",
        }

    monkeypatch.setattr(
        index_core,
        "build_v4_directional_pool_backfill",
        fake_backfill,
    )

    report = evidence_tool.collect_report(
        database="",
        network=ROBINHOOD_TESTNET_NETWORK,
        chain_id=ROBINHOOD_TESTNET_CHAIN_ID,
        rpc_url="https://rpc.testnet.example",
        pool_manager="0x8366a39CC670B4001A1121B8F6A443A643e40951",
        directions=(forward, reverse),
    )

    assert calls["kwargs"]["direction_targets"] == {forward: 1, reverse: 1}
    assert calls["kwargs"]["exclude_pool_ids"] == [indexed_pool["poolId"].lower()]
    assert set(calls["kwargs"]["topic_filter_addresses"]) == {zero, one}
    scan = report["scan"]
    assert scan["scanDirection"] == "newest_to_oldest"
    assert scan["satisfiedDirectionCount"] == 2
    assert scan["selectedPoolCount"] == 2
    assert scan["stoppedReason"] == "existing_pool_index_plus_incremental_scan_satisfied"


def test_collect_report_from_sqlite_reuses_verified_snapshot_without_rpc(monkeypatch, tmp_path):
    zero = "0x" + "00" * 20
    one = "0x" + "11" * 20
    forward = f"{zero}:{one}"
    reverse = f"{one}:{zero}"
    liquid_a = "0x" + "aa" * 32
    liquid_b = "0x" + "bb" * 32
    zero_liquidity = "0x" + "cc" * 32
    database = str((tmp_path / "evidence.sqlite3").resolve())
    ensure_business_schema(database)
    now = "2026-10-17T00:00:00Z"
    pool_rows = [
        (liquid_a, one, zero, 300, '{"initializeBlockNumber": 300}'),
        (liquid_b, zero, one, 200, '{"initializeBlockNumber": 200}'),
        (zero_liquidity, one, zero, 100, '{"initializeBlockNumber": 100}'),
    ]
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
                pool_key_json,
                token0,
                token1,
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
            for pool_id, token0, token1, _block, pool_key_json in pool_rows
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
                liquid_a,
                1,
                "1000000000000000000",
                100.0,
                "liquid",
                now,
                now,
                "pool_manager_extsload_live_state_batch",
            ),
            (
                ROBINHOOD_TESTNET_NETWORK,
                ROBINHOOD_TESTNET_CHAIN_ID,
                "uniswap_v4",
                liquid_b,
                1,
                "2000000000000000000",
                200.0,
                "liquid",
                now,
                now,
                "pool_manager_extsload_live_state_batch",
            ),
            (
                ROBINHOOD_TESTNET_NETWORK,
                ROBINHOOD_TESTNET_CHAIN_ID,
                "uniswap_v4",
                zero_liquidity,
                0,
                "0",
                None,
                None,
                now,
                now,
                "pool_manager_extsload_live_state_batch",
            ),
        ],
    )
    fake_plan = lambda **kwargs: {  # noqa: E731
        "targets": {forward: 2, reverse: 2},
        "floor_block": 100,
        "neededDirectionCount": 2,
        "basis": "direction_candidate_shortfall",
    }
    monkeypatch.setattr(
        evidence_tool,
        "absolute_database_path",
        lambda _database: database,
    )
    monkeypatch.setattr(
        evidence_tool,
        "_direction_plan",
        fake_plan,
    )

    report = evidence_tool.collect_report_from_sqlite(
        database="",
        directions=(forward, reverse),
    )

    scan = report["scan"]
    assert scan["onchainRpcScan"] is False
    assert scan["scanDirection"] == "existing_sqlite_liquidity_snapshot"
    assert scan["sqliteLiveConfirmedPoolCount"] == 2
    assert scan["sqliteSnapshotCount"] == 3
    assert scan["sqliteZeroLiquidityPoolCount"] == 1
    assert scan["sqliteMissingSnapshotPoolCount"] == 0
    assert report["indexedLiquidity"]["snapshotCount"] == 3
    assert report["indexedLiquidity"]["zeroLiquidityPoolCount"] == 1
    assert report["indexedLiquidity"]["missingSnapshotPoolCount"] == 0
    assert scan["directionCounts"] == {forward: 2, reverse: 2}
    assert scan["satisfiedDirectionCount"] == 2
    assert scan["stoppedReason"] == "sqlite_liquidity_snapshot_satisfied"
    assert report["safety"]["writesSqlite"] is False


def test_collect_report_from_sqlite_distinguishes_missing_snapshot(monkeypatch, tmp_path):
    zero = "0x" + "00" * 20
    one = "0x" + "11" * 20
    forward = f"{zero}:{one}"
    reverse = f"{one}:{zero}"
    liquid = "0x" + "aa" * 32
    missing = "0x" + "bb" * 32
    database = str((tmp_path / "evidence.sqlite3").resolve())
    ensure_business_schema(database)
    now = "2026-10-17T00:00:00Z"
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
                '{"initializeBlockNumber": 300}',
                token0,
                token1,
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
            for pool_id, token0, token1 in ((liquid, one, zero), (missing, one, zero))
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
                liquid,
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
    monkeypatch.setattr(
        evidence_tool,
        "_direction_plan",
        lambda **kwargs: {
            "targets": {forward: 1, reverse: 1},
            "floor_block": 100,
            "neededDirectionCount": 2,
            "basis": "direction_candidate_shortfall",
        },
    )
    monkeypatch.setattr(
        evidence_tool,
        "absolute_database_path",
        lambda _database: database,
    )

    report = evidence_tool.collect_report_from_sqlite(
        database="",
        directions=(forward, reverse),
    )

    scan = report["scan"]
    assert scan["sqlitePoolCount"] == 2
    assert scan["sqliteSnapshotCount"] == 1
    assert scan["sqliteLiveConfirmedPoolCount"] == 1
    assert scan["sqliteZeroLiquidityPoolCount"] == 0
    assert scan["sqliteMissingSnapshotPoolCount"] == 1
    assert scan["sqlitePoolCount"] == report["indexedLiquidity"]["poolCount"]
    assert scan["sqliteSnapshotCount"] == report["indexedLiquidity"]["snapshotCount"]
