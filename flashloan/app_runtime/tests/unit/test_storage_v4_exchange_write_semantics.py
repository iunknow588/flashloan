from __future__ import annotations

import json

from core.db.storage_business_io import query
from core.db.storage_chain_core import seed_chain_tokens
from core.db.storage_schema import ensure_database_schema
from core.db.storage_v4_exchange import (
    load_v4_pool_liquidity,
    upsert_v4_exchange_pools,
    upsert_v4_liquidity_snapshots,
)


NETWORK = "robinhood"
CHAIN_ID = 4663
PROTOCOL = "uniswap_v4"
ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"
USDE = "0x5d3a1ff2b6bab83b63cd9ad0787074081a52ef34"
POOL_ID = "0x" + "a1" * 32
POOL_MANAGER = "0x8366a39cc670b4001a1121b8f6a443a643e40951"


def _database(tmp_path) -> str:
    database = str(tmp_path / "pool-write-semantics.sqlite3")
    ensure_database_schema(database)
    seed_chain_tokens(
        database,
        network=NETWORK,
        chain_id=CHAIN_ID,
        token_rows=[
            {
                "token_address": ZERO_ADDRESS,
                "token_symbol": "ETH",
                "token_name": "Ether",
                "token_decimals": 18,
                "is_native": 1,
                "token_kind": "native",
            },
            {
                "token_address": USDE,
                "token_symbol": "USDE",
                "token_name": "USDe",
                "token_decimals": 18,
            },
        ],
    )
    return database


def _seed_pool(database: str) -> None:
    upsert_v4_exchange_pools(
        database,
        network=NETWORK,
        chain_id=CHAIN_ID,
        protocol=PROTOCOL,
        pool_rows=[
            {
                "pool_id": POOL_ID,
                "pool_manager": POOL_MANAGER,
                "pool_key": {
                    "currency0": ZERO_ADDRESS,
                    "currency1": USDE,
                    "fee": 3000,
                    "tickSpacing": 60,
                    "hooks": ZERO_ADDRESS,
                },
                "token0": ZERO_ADDRESS,
                "token1": USDE,
                "fee": 3000,
                "tick_spacing": 60,
                "hooks": ZERO_ADDRESS,
            }
        ],
    )


def test_sparse_pool_refresh_never_downgrades_static_fields(tmp_path):
    database = _database(tmp_path)
    _seed_pool(database)

    upsert_v4_exchange_pools(
        database,
        network=NETWORK,
        chain_id=CHAIN_ID,
        protocol=PROTOCOL,
        pool_rows=[
            {
                "pool_id": POOL_ID,
                "pool_manager": "",
                "pool_key": {},
                "token0": ZERO_ADDRESS,
                "token1": USDE,
                "fee": None,
                "tick_spacing": None,
                "hooks": "",
            }
        ],
    )

    row = query(
        database,
        """
        SELECT pool_manager, pool_key_json, fee, fee_label, tick_spacing, hooks
        FROM v4_exchange_pools
        WHERE network = ? AND chain_id = ? AND protocol = ? AND pool_id = ?
        """,
        (NETWORK, CHAIN_ID, PROTOCOL, POOL_ID),
    )[0]
    assert row["pool_manager"] == POOL_MANAGER
    assert row["fee"] == 3000
    assert row["fee_label"] == "3000"
    assert row["tick_spacing"] == 60
    assert row["hooks"] == ZERO_ADDRESS
    key = json.loads(row["pool_key_json"])
    assert key["currency0"] == ZERO_ADDRESS
    assert key["currency1"] == USDE
    assert key["fee"] == 3000
    assert key["tickSpacing"] == 60


def test_newer_liquidity_block_updates_dynamic_state_and_old_block_cannot_replace_it(tmp_path):
    database = _database(tmp_path)
    _seed_pool(database)

    upsert_v4_liquidity_snapshots(
        database,
        network=NETWORK,
        chain_id=CHAIN_ID,
        protocol=PROTOCOL,
        rows=[
            {
                "pool_id": POOL_ID,
                "has_liquidity": True,
                "liquidity": "1000",
                "liquidity_index": 1000.0,
                "estimated_tvl_usd": 1000.0,
                "sqrt_price_x96": "100",
                "price": 1.0,
                "tick": -10,
                "liquidity_block_number": 100,
                "evidence_source": "rpc_slot0",
            }
        ],
    )
    upsert_v4_liquidity_snapshots(
        database,
        network=NETWORK,
        chain_id=CHAIN_ID,
        protocol=PROTOCOL,
        rows=[
            {
                "pool_id": POOL_ID,
                "has_liquidity": True,
                "liquidity": "2000",
                "liquidity_index": 2000.0,
                "estimated_tvl_usd": 2000.0,
                "sqrt_price_x96": "200",
                "price": 2.0,
                "tick": -20,
                "liquidity_block_number": 101,
                "evidence_source": "rpc_slot0",
            }
        ],
    )
    upsert_v4_liquidity_snapshots(
        database,
        network=NETWORK,
        chain_id=CHAIN_ID,
        protocol=PROTOCOL,
        rows=[
            {
                "pool_id": POOL_ID,
                "has_liquidity": True,
                "liquidity": "999",
                "liquidity_index": 999.0,
                "estimated_tvl_usd": 999.0,
                "sqrt_price_x96": "99",
                "price": 0.99,
                "tick": -99,
                "liquidity_block_number": 99,
                "evidence_source": "stale_replay",
            }
        ],
    )

    row = load_v4_pool_liquidity(
        database,
        network=NETWORK,
        chain_id=CHAIN_ID,
        protocol=PROTOCOL,
    )[0]
    assert row["liquidity_block_number"] == 101
    assert row["liquidity"] == "2000"
    assert row["liquidity_index"] == 2000.0
    assert row["estimated_tvl_usd"] == 2000.0
    assert row["sqrt_price_x96"] == "200"
    assert row["price"] == 2.0
    assert row["tick"] == -20
    assert row["evidence_source"] == "rpc_slot0"


def test_unknown_rpc_values_do_not_become_false_or_erase_known_state(tmp_path):
    database = _database(tmp_path)
    _seed_pool(database)
    upsert_v4_liquidity_snapshots(
        database,
        network=NETWORK,
        chain_id=CHAIN_ID,
        protocol=PROTOCOL,
        rows=[
            {
                "pool_id": POOL_ID,
                "has_liquidity": True,
                "liquidity": "1000",
                "sqrt_price_x96": "100",
                "price": 1.0,
                "tick": -10,
                "liquidity_block_number": 100,
                "evidence_source": "rpc_slot0",
            }
        ],
    )
    upsert_v4_liquidity_snapshots(
        database,
        network=NETWORK,
        chain_id=CHAIN_ID,
        protocol=PROTOCOL,
        rows=[
            {
                "pool_id": POOL_ID,
                "has_liquidity": "unknown",
                "liquidity": None,
                "sqrt_price_x96": None,
                "price": None,
                "tick": None,
                "liquidity_block_number": 101,
                "evidence_source": "rpc_error",
            }
        ],
    )

    row = load_v4_pool_liquidity(
        database,
        network=NETWORK,
        chain_id=CHAIN_ID,
        protocol=PROTOCOL,
    )[0]
    assert row["has_liquidity"] == 1
    assert row["liquidity_block_number"] == 100
    assert row["liquidity"] == "1000"
    assert row["sqrt_price_x96"] == "100"
    assert row["price"] == 1.0
    assert row["tick"] == -10
    assert row["evidence_source"] == "rpc_slot0"
