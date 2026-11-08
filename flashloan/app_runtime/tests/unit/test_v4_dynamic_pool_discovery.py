"""Tests for the dynamic NULL/zero-liquidity pool recovery planner."""

from __future__ import annotations

import sqlite3

from core.db.storage_business_schema import ensure_business_schema
from dex.intent_trade.chains.robinhood_common.chain_config import (
    ROBINHOOD_TESTNET_CHAIN_ID,
    ROBINHOOD_TESTNET_NETWORK,
)
from dex.runtime.v4_dynamic_pool_discovery import (
    dynamic_discovery_eligible_pool_ids,
    dynamic_pool_discovery_plan,
    load_dynamic_pool_discovery_cursor,
    run_dynamic_pool_index_slice,
    save_dynamic_pool_discovery_cursor,
)


NATIVE = "0x0000000000000000000000000000000000000000"
ETH = "0x5884ad2f920c162cfbbacc88c9c51aa75ec09e02"
STOCK = "0xc9f9c86933092bbbfff3ccb4b105a4a94bf3bd4e"
UNKNOWN = "0x1111111111111111111111111111111111111111"


def _seed_pool(
    con: sqlite3.Connection,
    *,
    pool_id: str,
    token0: str,
    token1: str,
    fee: int = 500,
    tick_spacing: int = 10,
    hooks: str = "0x0000000000000000000000000000000000000000",
    has_liquidity: int | None = None,
) -> None:
    con.execute(
        """
        INSERT INTO v4_exchange_pools (
            network, chain_id, protocol, pool_id, pool_identity_mode, pool_manager,
            pool_key_json, token0, token1, fee, fee_label, dynamic_fee, special_fee,
            tick_spacing, hooks, address_pair, created_at, updated_at
        ) VALUES (?, ?, ?, ?, 'v4_pool_id', '0xmanager', '{}', ?, ?, ?, 'fee', 0, 0,
                  ?, ?, ?, ?, ?)
        """,
        (
            ROBINHOOD_TESTNET_NETWORK,
            ROBINHOOD_TESTNET_CHAIN_ID,
            "uniswap_v4",
            pool_id,
            token0,
            token1,
            fee,
            tick_spacing,
            hooks,
            ":".join(sorted((token0.lower(), token1.lower()))),
            "2026-10-27T00:00:00Z",
            "2026-10-27T00:00:00Z",
        ),
    )
    if has_liquidity is not None or True:
        con.execute(
            """
            INSERT INTO v4_exchange_pool_liquidity_snapshots (
                network, chain_id, protocol, pool_id, has_liquidity, liquidity,
                liquidity_index, estimated_tvl_usd, balances_json, depth_status,
                depth_band_json, depth_score_usd, sqrt_price_x96, price, tick,
                liquidity_read_at, liquidity_block_number, recent_swap_active,
                observed_at, refreshed_at, evidence_source
            ) VALUES (?, ?, ?, ?, ?, '', 0, 0, '{}', '', '{}', 0, '', 0, 0,
                      '2026-10-27T00:00:00Z', 0, 0,
                      '2026-10-27T00:00:00Z', '2026-10-27T00:00:00Z', 'test')
            """,
            (
                ROBINHOOD_TESTNET_NETWORK,
                ROBINHOOD_TESTNET_CHAIN_ID,
                "uniswap_v4",
                pool_id,
                has_liquidity,
            ),
        )


def _seed_token(
    con: sqlite3.Connection,
    *,
    address: str,
    symbol: str,
    category: str = "stock",
) -> None:
    con.execute(
        """
        INSERT INTO chain_tokens (
            network, chain_id, token_address, token_symbol, token_name,
            token_decimals, token_kind, is_native, is_wrapped_native, is_stable,
            anchor_symbol, token_metadata_json, source_json, first_seen_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, 18, 'erc20', 0, 0, 0, NULL, ?, '{}',
                  '2026-10-27T00:00:00Z', '2026-10-27T00:00:00Z')
        """,
        (
            ROBINHOOD_TESTNET_NETWORK,
            ROBINHOOD_TESTNET_CHAIN_ID,
            address,
            symbol,
            symbol,
            f'{{"category": "{category}"}}',
        ),
    )


def _database(tmp_path) -> str:
    database = str((tmp_path / "dynamic.sqlite3").resolve())
    ensure_business_schema(database)
    con = sqlite3.connect(database)
    try:
        _seed_token(con, address=NATIVE, symbol="NATIVE", category="native")
        _seed_token(con, address=ETH, symbol="ETH", category="wrapped")
        _seed_token(con, address=STOCK, symbol="TST", category="stock")
        _seed_token(con, address=UNKNOWN, symbol="UNK", category="unknown")
        _seed_pool(
            con,
            pool_id="0x" + "a" * 64,
            token0=ETH,
            token1=STOCK,
            has_liquidity=1,
        )
        _seed_pool(
            con,
            pool_id="0x" + "b" * 64,
            token0=ETH,
            token1=STOCK,
            has_liquidity=None,
        )
        _seed_pool(
            con,
            pool_id="0x" + "c" * 64,
            token0=ETH,
            token1=STOCK,
            has_liquidity=0,
        )
        _seed_pool(
            con,
            pool_id="0x" + "d" * 64,
            token0=ETH,
            token1=UNKNOWN,
            has_liquidity=None,
        )
        con.commit()
    finally:
        con.close()
    return database


def _index_slice_database(tmp_path) -> str:
    """Seed a short direction with no NULL/zero pool ids to recover.

    The usable pool keeps the direction short (below the target of five), but
    there is no SQLite pool the liquidity worker can verify first, so the
    dynamic indexer must scan Initialize logs to discover new pools.
    """
    database = str((tmp_path / "index_slice.sqlite3").resolve())
    ensure_business_schema(database)
    con = sqlite3.connect(database)
    try:
        _seed_token(con, address=NATIVE, symbol="NATIVE", category="native")
        _seed_token(con, address=ETH, symbol="ETH", category="wrapped")
        _seed_token(con, address=STOCK, symbol="TST", category="stock")
        _seed_pool(
            con,
            pool_id="0x" + "a" * 64,
            token0=ETH,
            token1=STOCK,
            has_liquidity=1,
        )
        con.commit()
    finally:
        con.close()
    return database


def test_dynamic_plan_marks_short_direction_and_recoverable_null_zero_pools(
    tmp_path,
) -> None:
    database = _database(tmp_path)
    plan = dynamic_pool_discovery_plan(
        database=database,
        network=ROBINHOOD_TESTNET_NETWORK,
        chain_id=ROBINHOOD_TESTNET_CHAIN_ID,
        route_addresses=[NATIVE, ETH, STOCK, NATIVE],
    )
    assert plan["ok"] is True
    assert plan["maximumDirectionTarget"] == 5
    assert plan["counts"][f"{ETH}:{STOCK}"] == 1
    assert f"{ETH}:{STOCK}" in plan["shortDirections"]
    assert "0x" + "b" * 64 in (plan["recoverablePoolIds"] or [])
    assert "0x" + "c" * 64 in (plan["recoverablePoolIds"] or [])
    assert "0x" + "d" * 64 not in (plan["recoverablePoolIds"] or [])
    assert plan["noLiquidityPoolCount"] == 2


def test_dynamic_discovery_eligible_pool_ids_skip_unknown_token_pool(tmp_path) -> None:
    database = _database(tmp_path)
    pool_ids = dynamic_discovery_eligible_pool_ids(
        database=database,
        network=ROBINHOOD_TESTNET_NETWORK,
        chain_id=ROBINHOOD_TESTNET_CHAIN_ID,
        route_addresses=[NATIVE, ETH, STOCK, NATIVE],
    )
    normalized = [str(pool_id).lower() for pool_id in pool_ids]
    assert "0x" + "b" * 64 in normalized
    assert "0x" + "c" * 64 in normalized
    assert "0x" + "d" * 64 not in normalized


def test_dynamic_discovery_cursor_round_trip(tmp_path) -> None:
    database = str((tmp_path / "cursor.sqlite3").resolve())
    ensure_business_schema(database)

    save_dynamic_pool_discovery_cursor(
        database,
        network=ROBINHOOD_TESTNET_NETWORK,
        chain_id=ROBINHOOD_TESTNET_CHAIN_ID,
        next_from_block=1001,
        last_scanned_block=1000,
        direction_target=5,
        short_directions=[f"{ETH}:{STOCK}", f"{STOCK}:{ETH}"],
        pool_manager="0xmanager",
        mode="complete",
    )

    cursor = load_dynamic_pool_discovery_cursor(
        database,
        network=ROBINHOOD_TESTNET_NETWORK,
        chain_id=ROBINHOOD_TESTNET_CHAIN_ID,
    )
    assert cursor["next_from_block"] == 1001
    assert cursor["last_scanned_block"] == 1000
    assert cursor["direction_target"] == 5
    assert set(cursor["short_directions"]) == {f"{ETH}:{STOCK}", f"{STOCK}:{ETH}"}
    assert cursor["pool_manager"] == "0xmanager"
    assert cursor["mode"] == "complete"
    assert cursor["error"] is None


def test_dynamic_pool_index_slice_dry_run_bounds_window(tmp_path, monkeypatch) -> None:
    database = _index_slice_database(tmp_path)

    def fake_latest(rpc_url: str, **kwargs: object) -> int:
        return 1000

    def fake_build(**kwargs):
        return {
            "ok": True,
            "generatedBlockNumber": 1000,
            "fromBlock": kwargs.get("from_block", 0),
            "toBlock": kwargs.get("to_block", 1000),
            "initializeLogCount": 1,
            "pools": [
                {
                    "poolId": "0x" + "e" * 64,
                    "poolKey": {
                        "poolId": "0x" + "e" * 64,
                        "token0": ETH,
                        "token1": STOCK,
                    },
                    "token0": ETH,
                    "token1": STOCK,
                    "fee": 500,
                    "tickSpacing": 10,
                    "hooks": "0x0000000000000000000000000000000000000000",
                }
            ],
            "poolKeys": [
                {
                    "poolId": "0x" + "e" * 64,
                    "token0": ETH,
                    "token1": STOCK,
                }
            ],
        }

    monkeypatch.setattr(
        "dex.runtime.v4_dynamic_pool_discovery._latest_block",
        fake_latest,
    )
    monkeypatch.setattr(
        "dex.intent_trade.chains.robinhood_testnet.v4_pool_index."
        "build_v4_pool_index_from_initialize_logs",
        fake_build,
    )

    result = run_dynamic_pool_index_slice(
        database=database,
        network=ROBINHOOD_TESTNET_NETWORK,
        chain_id=ROBINHOOD_TESTNET_CHAIN_ID,
        rpc_url="http://rpc.test",
        window_blocks=100,
        dry_run=True,
    )

    assert result["ok"] is True
    assert result["phase"] == "incremental_window_scanned"
    assert result["dryRun"] is True
    assert result["scannedBlockCount"] == 100
    assert result["nextFromBlock"] == 101
    assert result["discoveredPoolCount"] == 1
    assert "0x" + "e" * 64 in result["discoveredPoolIds"]


def test_dynamic_pool_index_slice_cursor_persists_next_block(tmp_path, monkeypatch) -> None:
    database = _index_slice_database(tmp_path)

    def fake_latest(rpc_url: str, **kwargs: object) -> int:
        return 1000

    seen_keywords: dict = {}

    def fake_build(**kwargs):
        seen_keywords.update(kwargs)
        return {
            "ok": True,
            "generatedBlockNumber": 1000,
            "fromBlock": kwargs.get("from_block", 0),
            "toBlock": kwargs.get("to_block", 1000),
            "initializeLogCount": 0,
            "pools": [],
            "poolKeys": [],
        }

    def fake_import(**kwargs):
        return {"ok": True, "v4PoolCount": 0}

    monkeypatch.setattr(
        "dex.runtime.v4_dynamic_pool_discovery._latest_block",
        fake_latest,
    )
    monkeypatch.setattr(
        "dex.intent_trade.chains.robinhood_testnet.v4_pool_index."
        "build_v4_pool_index_from_initialize_logs",
        fake_build,
    )
    monkeypatch.setattr(
        "dex.tools.v4_rust_refresh.import_v4_evidence_rows",
        fake_import,
    )

    result = run_dynamic_pool_index_slice(
        database=database,
        network=ROBINHOOD_TESTNET_NETWORK,
        chain_id=ROBINHOOD_TESTNET_CHAIN_ID,
        rpc_url="http://rpc.test",
        window_blocks=100,
        dry_run=False,
    )

    assert result["ok"] is True
    assert result["scannedBlockCount"] == 100
    cursor = load_dynamic_pool_discovery_cursor(
        database,
        network=ROBINHOOD_TESTNET_NETWORK,
        chain_id=ROBINHOOD_TESTNET_CHAIN_ID,
    )
    assert cursor["last_scanned_block"] == 100
    assert cursor["next_from_block"] == 101
    assert cursor["mode"] == "complete"


def test_dynamic_pool_index_slice_defers_to_targeted_verification_when_recoverable_pools_exist(
    tmp_path, monkeypatch
) -> None:
    database = _database(tmp_path)

    def unexpected_build(**kwargs):
        raise AssertionError("Initialize log scan must not run while SQLite can be verified")

    monkeypatch.setattr(
        "dex.intent_trade.chains.robinhood_testnet.v4_pool_index."
        "build_v4_pool_index_from_initialize_logs",
        unexpected_build,
    )

    result = run_dynamic_pool_index_slice(
        database=database,
        network=ROBINHOOD_TESTNET_NETWORK,
        chain_id=ROBINHOOD_TESTNET_CHAIN_ID,
        rpc_url="http://rpc.test",
        window_blocks=100,
        dry_run=True,
    )

    assert result["ok"] is True
    assert result["phase"] == "targeted_verification_pending"
    assert result["writesSqlite"] is False
    normalized = [str(pool_id).lower() for pool_id in result["poolIds"]]
    assert "0x" + "b" * 64 in normalized
    assert "0x" + "c" * 64 in normalized
