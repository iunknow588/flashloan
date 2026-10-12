"""SQLite import count checks for the mainnet V4 evidence pipeline."""

from __future__ import annotations

import json

from core.db import builders
from core.db.import_existing_json import import_existing_json
from core.db.storage_chain_core import load_chain_tokens
from core.db.storage_schema import ensure_database_schema
from core.db.storage_v4_exchange import (
    load_v4_exchange_pools,
    load_v4_pool_liquidity,
)


def _write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _index_payload() -> dict:
    usde = "0x5d3a1ff2b6bab83b63cd9ad0787074081a52ef34"
    zero = "0x0000000000000000000000000000000000000000"
    manager = "0x8366a39CC670B4001A1121B8F6A443A643e40951"
    return {
        "schemaVersion": "robinhood-v4-pool-index-v1",
        "network": "robinhood",
        "chainId": 4663,
        "poolManager": manager,
        "generatedBlockNumber": 84_000_000,
        "initializeLogCount": 2,
        "poolKeys": [],
        "pools": [
            {
                "protocol": "uniswap_v4",
                "poolId": "0x" + "a1" * 32,
                "poolManager": manager,
                "token0": zero,
                "token1": usde,
                "token0Symbol": "ETH",
                "token1Symbol": "USDE",
                "token0Decimals": 18,
                "token1Decimals": 18,
                "fee": 3000,
                "tickSpacing": 60,
                "hooks": zero,
                "sqrtPriceX96": str(2**96),
                "tick": 10,
            },
            {
                "protocol": "uniswap_v4",
                "poolId": "0x" + "b2" * 32,
                "poolManager": manager,
                "token0": zero,
                "token1": "0x" + "11" * 20,
                "token0Symbol": "ETH",
                "token1Symbol": "X",
                "token0Decimals": 18,
                "token1Decimals": 18,
                "fee": 3000,
                "tickSpacing": 60,
                "hooks": zero,
                "sqrtPriceX96": str(2**96),
                "tick": 10,
            },
        ],
    }


def test_import_existing_json_writes_mainnet_v4_counts(tmp_path):
    root = tmp_path / "runtime"
    database = str(tmp_path / "local.sqlite3")
    ensure_database_schema(database)

    index = _index_payload()
    _write_json(root / "evidence" / "robinhood" / "v4_pool_index" / "latest.json", index)
    liquidity = {
        "schemaVersion": "robinhood-v4-pool-liquidity-v1",
        "mode": "robinhood_mainnet_v4_pool_liquidity",
        "network": "robinhood",
        "chainId": 4663,
        "generatedBlockNumber": 84_000_100,
        "generatedAt": "2026-10-10T00:00:00Z",
        "pools": [
            {
                "poolId": "0x" + "a1" * 32,
                "hasLiquidity": True,
                "liquidity": "1000000000000000000000",
                "liquidityIndex": 123.5,
                "estimatedTvlUsd": "260000.5",
                "balances": {"tvlUsd": "260000.5"},
                "depthStatus": "deep",
                "depthBand": {"level": "deep"},
                "sqrtPriceX96": str(2**96),
                "tick": 10,
                "liquidityReadAt": "2026-10-10T00:00:00Z",
                "liquidityBlockNumber": 84_000_100,
                "recentSwapActive": True,
            },
            {
                "poolId": "0x" + "b2" * 32,
                "hasLiquidity": False,
                "liquidity": "",
                "liquidityIndex": 0,
                "estimatedTvlUsd": 0,
                "balances": {"tvlUsd": 0},
                "depthStatus": "no_liquidity",
                "depthBand": {"level": "empty"},
                "sqrtPriceX96": str(2**96),
                "tick": 10,
                "liquidityReadAt": "2026-10-10T00:00:00Z",
                "liquidityBlockNumber": 84_000_100,
                "recentSwapActive": False,
            },
        ],
        "poolKeys": [],
        "flashAccountingSources": [],
    }
    _write_json(root / "evidence" / "robinhood" / "v4_pool_liquidity" / "latest.json", liquidity)

    result = import_existing_json(database, runtime_root=root)

    assert result["ok"] is True
    assert result["v4PoolCount"] == 2
    assert result["tokenCount"] >= 3
    tokens = load_chain_tokens(database, network="robinhood", chain_id=4663)
    symbols = {str(row.get("token_symbol") or "").upper() for row in tokens}
    assert {"ETH", "USDE", "X"} <= symbols
    pools = load_v4_exchange_pools(
        database,
        network="robinhood",
        chain_id=4663,
        protocol="uniswap_v4",
    )
    assert len(pools) == 2
    assert {row["pool_id"] for row in pools} == {"0x" + "a1" * 32, "0x" + "b2" * 32}
    snapshots = load_v4_pool_liquidity(
        database,
        network="robinhood",
        chain_id=4663,
        protocol="uniswap_v4",
    )
    assert len(snapshots) == 2
    liquid = next(row for row in snapshots if row["pool_id"] == "0x" + "a1" * 32)
    assert liquid["has_liquidity"] == 1
    assert liquid["estimated_tvl_usd"] == 260000.5
    assert liquid["recent_swap_active"] == 1

    # The shard/intermediate JSON import path no longer writes business rows.
    # Candidate, observation and loop tables are rebuilt from SQLite instead.
    build_result = builders.build_all_anchor_data(database)
    assert build_result["ok"] is True
    anchor_reports = {report["anchorSymbol"]: report for report in build_result["anchors"]}
    assert "ETH" in anchor_reports
    assert anchor_reports["ETH"]["candidate"]["tokenCount"] == 2
    assert anchor_reports["ETH"]["observation"]["tokenCount"] == 2
    assert anchor_reports["ETH"]["observation"]["directionCount"] == 2
