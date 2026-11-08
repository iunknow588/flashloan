"""Tests for the read-only Robinhood V4 scope indexer."""

from __future__ import annotations

import json

from core.db.storage_business_io import executemany
from core.db.storage_business_schema import ensure_business_schema
from core.db.storage_chain_core import upsert_initialization
from dex.intent_trade.chains.robinhood_common.chain_config import (
    ROBINHOOD_TESTNET_CHAIN_ID,
    ROBINHOOD_TESTNET_MARKET_ID,
    ROBINHOOD_TESTNET_NETWORK,
)
from dex.runtime import v4_scope_indexer as indexer


def _database(tmp_path) -> str:
    database = str((tmp_path / "scope-index.sqlite3").resolve())
    ensure_business_schema(database)
    now = "2026-10-28T00:00:00Z"
    token0 = "0x" + "01" * 20
    token1 = "0x" + "02" * 20
    token2 = "0x" + "03" * 20
    anchor = "0x" + "00" * 20
    pool = "0x" + "aa" * 32
    other_pool = "0x" + "bb" * 32
    executemany(
        database,
        """
        INSERT INTO dex_candidate_pools (
            network, chain_id, protocol, anchor_scope, anchor_symbol,
            address_pair, direction_key, pool_id, token0, token1, fee,
            dynamic_fee, special_fee, tick_spacing, hooks, has_liquidity,
            usable_for_observation, is_first_hop_recommended,
            is_third_hop_recommended, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                ROBINHOOD_TESTNET_NETWORK,
                ROBINHOOD_TESTNET_CHAIN_ID,
                "uniswap_v4",
                ROBINHOOD_TESTNET_MARKET_ID,
                "ETH",
                f"{token0}:{token1}",
                f"{token0}:{token1}",
                pool,
                token0,
                token1,
                3000,
                0,
                0,
                60,
                "0x" + "00" * 20,
                1,
                1,
                1,
                0,
                now,
                now,
            )
        ],
    )
    executemany(
        database,
        """
        INSERT INTO market_observation_yz_pairs (
            network, chain_id, protocol, anchor_scope, anchor_symbol,
            direction_key, token_x_address, token_y_address, address_pair,
            has_direct_swap_pool, direct_swap_pool_count, pool_ids_json,
            optimal_pool_id, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                ROBINHOOD_TESTNET_NETWORK,
                ROBINHOOD_TESTNET_CHAIN_ID,
                "uniswap_v4",
                ROBINHOOD_TESTNET_MARKET_ID,
                "ETH",
                f"{token0}:{token1}",
                token0,
                token1,
                f"{token0}:{token1}",
                1,
                2,
                json.dumps([pool, other_pool]),
                other_pool,
                now,
                now,
            )
        ],
    )
    executemany(
        database,
        """
        INSERT INTO v4_closed_loops (
            network, chain_id, protocol, market_id, loop_key,
            anchor_address, anchor_symbol, path_text, path_addresses_json,
            path_symbols_json, pool_ids_json, recommended_first_priority,
            status, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                ROBINHOOD_TESTNET_NETWORK,
                ROBINHOOD_TESTNET_CHAIN_ID,
                "uniswap_v4",
                ROBINHOOD_TESTNET_MARKET_ID,
                "loop-aaa",
                anchor,
                "ETH",
                "ETH -> A -> B -> ETH",
                json.dumps([anchor, token0, token1, anchor]),
                json.dumps(["ETH", "A", "B", "ETH"]),
                json.dumps([pool, other_pool]),
                1,
                "active",
                now,
                now,
            )
        ],
    )
    executemany(
        database,
        """
        INSERT INTO v4_closed_loop_hops (
            network, chain_id, protocol, market_id, loop_key, hop_index,
            hop_name, input_address, output_address, pool_id, base_pool_id,
            pool_selection_basis, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                ROBINHOOD_TESTNET_NETWORK,
                ROBINHOOD_TESTNET_CHAIN_ID,
                "uniswap_v4",
                ROBINHOOD_TESTNET_MARKET_ID,
                "loop-aaa",
                0,
                "first",
                anchor,
                token0,
                pool,
                pool,
                "optimal",
                now,
                now,
            ),
            (
                ROBINHOOD_TESTNET_NETWORK,
                ROBINHOOD_TESTNET_CHAIN_ID,
                "uniswap_v4",
                ROBINHOOD_TESTNET_MARKET_ID,
                "loop-aaa",
                1,
                "third",
                token1,
                anchor,
                "0x" + "cc" * 32,
                "0x" + "cc" * 32,
                "optimal",
                now,
                now,
            ),
        ],
    )
    return database


def test_query_pool_scope_reports_all_three_owners(tmp_path) -> None:
    database = _database(tmp_path)
    pool = "0x" + "aa" * 32

    scope = indexer.query_pool_scope(database, pool_id=pool)

    assert scope["ok"] is True
    assert scope["poolId"] == pool
    assert scope["counts"]["candidate"] == 1
    assert scope["counts"]["observation"] == 1
    assert scope["counts"]["closedLoop"] == 1
    assert scope["candidateRows"][0]["anchor_symbol"] == "ETH"
    assert pool in scope["observationRows"][0]["poolIds"]
    assert scope["closedLoops"][0]["loop_key"] == "loop-aaa"
    assert scope["closedLoops"][0]["poolIds"] == [pool, "0x" + "bb" * 32]


def test_query_pool_scope_matches_hops_even_without_loop_pool_list(tmp_path) -> None:
    database = _database(tmp_path)
    pool = "0x" + "cc" * 32

    scope = indexer.query_pool_scope(database, pool_id=pool)

    assert scope["ok"] is True
    assert scope["counts"]["candidate"] == 0
    assert scope["counts"]["closedLoop"] == 1
    assert scope["closedLoops"][0]["poolIds"] == ["0x" + "aa" * 32, "0x" + "bb" * 32]


def test_empty_pool_id_is_rejected(tmp_path) -> None:
    database = _database(tmp_path)
    scope = indexer.query_pool_scope(database, pool_id="  ")
    assert scope["ok"] is False
    assert scope["error"] == "pool_id_required"


def test_cursor_is_read_only_projection(tmp_path) -> None:
    database = _database(tmp_path)
    upsert_initialization(
        database,
        network=ROBINHOOD_TESTNET_NETWORK,
        chain_id=ROBINHOOD_TESTNET_CHAIN_ID,
        protocol="uniswap_v4",
        domain=indexer.SCOPE_INDEXER_DOMAIN,
        status="ok",
        completeness={
            "mode": "verify_complete",
            "cursor": 42,
            "pool_count": 100,
            "verified_pool_count": 100,
        },
    )

    cursor = indexer.load_scope_indexer_cursor(database)

    assert cursor["mode"] == "verify_complete"
    assert cursor["cursor"] == 42
    assert cursor["verified_pool_count"] == 100


def test_verify_only_preserves_business_row_counts(tmp_path) -> None:
    database = _database(tmp_path)
    before = indexer.query_pool_scope(database, pool_id="0x" + "aa" * 32)

    verified = indexer.verify_pool_scope(database, pool_id="0x" + "aa" * 32)
    after = indexer.query_pool_scope(database, pool_id="0x" + "aa" * 32)

    assert verified["ok"] is True
    assert after["counts"] == before["counts"]
