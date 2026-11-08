from __future__ import annotations

import json
import sys

import pytest

from core.db.storage_chain_core import seed_chain_tokens
from core.db.direction_pool_score import MAX_DIRECTION_CANDIDATE_POOLS
from core.db.import_existing_json import import_existing_json
from core.db.import_v4_evidence import import_v4_evidence_payloads
from core.db.storage_business_io import execute
from core.db.storage_schema import ensure_database_schema
from core.db.storage_v4_exchange import (
    clear_v4_exchange_evidence,
    prune_v4_non_executable_evidence,
    upsert_v4_exchange_pools,
    upsert_v4_liquidity_snapshots,
)
from core.db.v4_scan_state import load_v4_scan_state, update_v4_scan_state
from dex.intent_trade.chains.robinhood_common.stock_token_registry import (
    apply_stock_pool_metadata,
)
from dex.intent_trade.chains.robinhood_common.v4_sqlite_view import (
    load_v4_pool_index_payload,
    load_v4_pool_liquidity_payload,
)
from dex.intent_trade.chains.robinhood_common.chain_config import (
    ROBINHOOD_CHAIN_ID,
    ROBINHOOD_NETWORK,
    ROBINHOOD_TESTNET_CHAIN_ID,
    ROBINHOOD_TESTNET_MARKET_ID,
    ROBINHOOD_TESTNET_NETWORK,
    TESTNET_ANCHOR_ADDRESS_BY_SYMBOL,
)
from dex.tools.refresh_robinhood_v4_sqlite_data import (
    _network_scope,
    main,
)


def test_network_scope_resolves_mainnet_and_testnet():
    assert _network_scope("robinhood", None)[:2] == ("robinhood", 4663)
    assert _network_scope("robinhood-testnet", None)[:2] == ("robinhood-testnet", 46630)
    assert _network_scope("", 46630)[:2] == ("robinhood-testnet", 46630)


def _seed_testnet_anchor_pair(database: str) -> None:
    ensure_database_schema(database)
    zero = "0x0000000000000000000000000000000000000000"
    tz = TESTNET_ANCHOR_ADDRESS_BY_SYMBOL["TZ"]
    usde = TESTNET_ANCHOR_ADDRESS_BY_SYMBOL["USDE"]
    seed_chain_tokens(
        database,
        network=ROBINHOOD_TESTNET_NETWORK,
        chain_id=ROBINHOOD_TESTNET_CHAIN_ID,
        token_rows=[
            {
                "token_address": tz,
                "token_symbol": "TZ",
                "token_decimals": 18,
                "token_kind": "erc20",
                "anchor_symbol": "TZ",
            },
            {
                "token_address": usde,
                "token_symbol": "USDE",
                "token_decimals": 18,
                "token_kind": "erc20",
                "anchor_symbol": "USDE",
            },
        ],
    )
    upsert_v4_exchange_pools(
        database,
        network=ROBINHOOD_TESTNET_NETWORK,
        chain_id=ROBINHOOD_TESTNET_CHAIN_ID,
        protocol="uniswap_v4",
        pool_rows=[
            {
                "pool_id": "pool-tz-usde",
                "pool_manager": "0x8366a39CC670B4001A1121B8F6A443A643e40951",
                "pool_key": {"currency0": tz, "currency1": usde, "fee": 3000, "tickSpacing": 60, "hooks": zero},
                "token0": tz,
                "token1": usde,
                "fee": 3000,
                "tick_spacing": 60,
                "hooks": zero,
            }
        ],
    )
    upsert_v4_liquidity_snapshots(
        database,
        network=ROBINHOOD_TESTNET_NETWORK,
        chain_id=ROBINHOOD_TESTNET_CHAIN_ID,
        protocol="uniswap_v4",
        rows=[
            {
                "pool_id": "pool-tz-usde",
                "has_liquidity": True,
                "liquidity_index": 1000.0,
                "estimated_tvl_usd": 1_000_000.0,
                "sqrt_price_x96": str(2**96),
                "recent_swap_active": True,
            }
        ],
    )


def test_main_builds_testnet_from_sqlite_only(tmp_path, monkeypatch, capsys):
    database = str(tmp_path / "rebuild.sqlite3")
    _seed_testnet_anchor_pair(database)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "refresh_robinhood_v4_sqlite_data.py",
            "--database",
            database,
            "--network",
            "robinhood-testnet",
            "--anchors",
            "TZ,USDE",
            "--no-clear-cache",
        ],
    )

    assert main() == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["network"] == "robinhood-testnet"
    assert payload["chainId"] == 46630
    assert payload["anchorSymbols"] == ["TZ", "USDE"]
    assert payload["ok"] is True

    from core.db.storage_business_io import query

    pairs = query(
        database,
        """
        SELECT direction_key, yc_backup_pool_ids_json, zc_backup_pool_ids_json
        FROM dex_candidate_pool_pairs
        WHERE network = ? AND chain_id = ? AND protocol = ?
        ORDER BY direction_key
        """,
        ("robinhood-testnet", 46630, "uniswap_v4"),
    )
    assert pairs
    assert all(row["yc_backup_pool_ids_json"] == "[]" for row in pairs)
    assert all(row["zc_backup_pool_ids_json"] == "[]" for row in pairs)


def test_main_verify_only_resolves_anchors_from_sqlite(tmp_path, monkeypatch, capsys):
    database = str(tmp_path / "verify.sqlite3")
    _seed_testnet_anchor_pair(database)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "refresh_robinhood_v4_sqlite_data.py",
            "--database",
            database,
            "--network",
            "robinhood-testnet",
            "--verify-only",
            "--no-clear-cache",
        ],
    )

    assert main() == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["verifyOnly"] is True
    assert {"TZ", "USDE"}.issubset(payload["anchorSymbols"])
    assert payload["ok"] is True


def test_import_existing_json_refuses_without_explicit_runtime_root(tmp_path):
    database = str(tmp_path / "refuse.sqlite3")
    ensure_database_schema(database)
    result = import_existing_json(
        database,
        network=ROBINHOOD_TESTNET_NETWORK,
    )
    assert result["ok"] is False
    assert "refusing to import JSON evidence without an explicit runtime_root" in result["error"]


def _memory_evidence_payload() -> dict:
    zero = "0x0000000000000000000000000000000000000000"
    tz = TESTNET_ANCHOR_ADDRESS_BY_SYMBOL["TZ"]
    usde = TESTNET_ANCHOR_ADDRESS_BY_SYMBOL["USDE"]
    pool_id = "0x" + "c3" * 32
    return {
        "index": {
            "network": ROBINHOOD_TESTNET_NETWORK,
            "chainId": ROBINHOOD_TESTNET_CHAIN_ID,
            "poolManager": "0x8366a39CC670B4001A1121B8F6A443A643e40951",
            "initializeLogCount": 1,
            "poolKeys": [],
            "pools": [
                {
                    "protocol": "uniswap_v4",
                    "poolId": pool_id,
                    "poolManager": "0x8366a39CC670B4001A1121B8F6A443A643e40951",
                    "token0": tz,
                    "token1": usde,
                    "token0Symbol": "TZ",
                    "token1Symbol": "USDE",
                    "token0Decimals": 18,
                    "token1Decimals": 18,
                    "fee": 3000,
                    "tickSpacing": 60,
                    "hooks": zero,
                    "sqrtPriceX96": str(2**96),
                    "tick": 0,
                }
            ],
        },
        "liquidity": {
            "network": ROBINHOOD_TESTNET_NETWORK,
            "chainId": ROBINHOOD_TESTNET_CHAIN_ID,
            "generatedAt": "2026-10-15T00:00:00Z",
            "pools": [
                {
                    "poolId": pool_id,
                    "hasLiquidity": True,
                    "liquidity": str(10**18),
                    "liquidityIndex": 1000.0,
                    "estimatedTvlUsd": 1_000_000.0,
                    "balances": {"tvlUsd": 1_000_000.0},
                    "depthStatus": "deep",
                    "depthBand": {"level": "deep"},
                    "sqrtPriceX96": str(2**96),
                    "tick": 0,
                    "recentSwapActive": True,
                }
            ],
        },
    }


def test_import_v4_evidence_payloads_imports_from_memory_without_json(tmp_path, monkeypatch):
    database = str(tmp_path / "memory.sqlite3")
    ensure_database_schema(database)
    payloads = _memory_evidence_payload()

    def fail_on_json_read(path):
        raise AssertionError(f"JSON evidence must not be read during memory import: {path}")

    monkeypatch.setattr(
        "core.db.import_existing_json._read_json",
        fail_on_json_read,
    )
    result = import_v4_evidence_payloads(
        database,
        network=ROBINHOOD_TESTNET_NETWORK,
        chain_id=ROBINHOOD_TESTNET_CHAIN_ID,
        index_payload=payloads["index"],
        liquidity_payload=payloads["liquidity"],
    )
    assert result["ok"] is True
    assert result["source"] == "onchain_evidence_payload"
    assert result["v4PoolCount"] == 1
    assert result["tokenCount"] >= 2

    from core.db.storage_business_io import query

    pools = query(
        database,
        "SELECT pool_id FROM v4_exchange_pools WHERE network = ? AND chain_id = ?",
        (ROBINHOOD_TESTNET_NETWORK, ROBINHOOD_TESTNET_CHAIN_ID),
    )
    assert len(pools) == 1
    liquidity = query(
        database,
        "SELECT pool_id, has_liquidity, liquidity_index FROM v4_exchange_pool_liquidity_snapshots WHERE network = ? AND chain_id = ?",
        (ROBINHOOD_TESTNET_NETWORK, ROBINHOOD_TESTNET_CHAIN_ID),
    )
    assert len(liquidity) == 1
    assert liquidity[0]["has_liquidity"] == 1
    assert liquidity[0]["liquidity_index"] == 1000.0


def test_incremental_import_preserves_existing_token_category(tmp_path):
    database = str(tmp_path / "preserve-category.sqlite3")
    ensure_database_schema(database)
    from core.db.storage_business_io import query

    custom = "0xAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    seed_chain_tokens(
        database,
        network=ROBINHOOD_TESTNET_NETWORK,
        chain_id=ROBINHOOD_TESTNET_CHAIN_ID,
        token_rows=[
            {
                "token_address": custom,
                "token_symbol": "CUSTOM",
                "token_decimals": 18,
                "token_metadata": {
                    "category": "stablecoin",
                    "source": "operator_seed",
                },
            }
        ],
    )
    payloads = _memory_evidence_payload()
    tz = TESTNET_ANCHOR_ADDRESS_BY_SYMBOL["TZ"]
    payloads["index"]["pools"] = [
        {
            **payloads["index"]["pools"][0],
            "token0": custom,
            "token0Symbol": "CUSTOM",
            "token1": tz,
            "token1Symbol": "TZ",
        }
    ]
    import_v4_evidence_payloads(
        database,
        network=ROBINHOOD_TESTNET_NETWORK,
        chain_id=ROBINHOOD_TESTNET_CHAIN_ID,
        index_payload=payloads["index"],
        liquidity_payload=payloads["liquidity"],
    )

    rows = query(
        database,
        """
        SELECT token_symbol, token_metadata_json
        FROM chain_tokens
        WHERE network = ? AND chain_id = ? AND lower(token_address) = ?
        """,
        (ROBINHOOD_TESTNET_NETWORK, ROBINHOOD_TESTNET_CHAIN_ID, custom.lower()),
    )
    assert len(rows) == 1
    metadata = json.loads(rows[0]["token_metadata_json"])
    assert metadata.get("category") == "stablecoin"
    assert metadata.get("source") == "operator_seed"


def test_import_v4_evidence_payloads_skips_index_rows_without_explicit_liquidity(tmp_path):
    database = str(tmp_path / "no-weak-liquidity.sqlite3")
    ensure_database_schema(database)
    payloads = _memory_evidence_payload()
    payloads["liquidity"]["source"] = "pool_manager_extsload_live_state_batch"
    zero = "0x0000000000000000000000000000000000000000"
    payloads["index"]["pools"].append(
        {
            "protocol": "uniswap_v4",
            "poolId": "0x" + "dd" * 32,
            "poolManager": "0x8366a39CC670B4001A1121B8F6A443A643e40951",
            "token0": "0x1111111111111111111111111111111111111111",
            "token1": "0x2222222222222222222222222222222222222222",
            "token0Symbol": "AAA",
            "token1Symbol": "BBB",
            "token0Decimals": 18,
            "token1Decimals": 18,
            "fee": 3000,
            "tickSpacing": 60,
            "hooks": zero,
        }
    )
    import_v4_evidence_payloads(
        database,
        network=ROBINHOOD_TESTNET_NETWORK,
        chain_id=ROBINHOOD_TESTNET_CHAIN_ID,
        index_payload=payloads["index"],
        liquidity_payload=payloads["liquidity"],
    )

    from core.db.storage_business_io import query

    liquidity = query(
        database,
        "SELECT pool_id, has_liquidity, evidence_source FROM v4_exchange_pool_liquidity_snapshots WHERE network = ? AND chain_id = ?",
        (ROBINHOOD_TESTNET_NETWORK, ROBINHOOD_TESTNET_CHAIN_ID),
    )
    assert len(liquidity) == 1
    assert liquidity[0]["has_liquidity"] == 1
    assert liquidity[0]["evidence_source"] == "pool_manager_extsload_live_state_batch"


def test_import_v4_evidence_payloads_replace_clears_stale_zero_rows(tmp_path):
    database = str(tmp_path / "replace.sqlite3")
    ensure_database_schema(database)
    payloads = _memory_evidence_payload()
    import_v4_evidence_payloads(
        database,
        network=ROBINHOOD_TESTNET_NETWORK,
        chain_id=ROBINHOOD_TESTNET_CHAIN_ID,
        index_payload=payloads["index"],
        liquidity_payload=payloads["liquidity"],
    )
    pool_id = payloads["index"]["pools"][0]["poolId"]
    upsert_v4_liquidity_snapshots(
        database,
        network=ROBINHOOD_TESTNET_NETWORK,
        chain_id=ROBINHOOD_TESTNET_CHAIN_ID,
        protocol="uniswap_v4",
        rows=[
            {
                "pool_id": pool_id,
                "has_liquidity": False,
                "liquidity": "",
                "liquidity_block_number": None,
            }
        ],
    )
    result = import_v4_evidence_payloads(
        database,
        network=ROBINHOOD_TESTNET_NETWORK,
        chain_id=ROBINHOOD_TESTNET_CHAIN_ID,
        index_payload=payloads["index"],
        liquidity_payload=payloads["liquidity"],
        replace=True,
    )
    assert result["ok"] is True

    from core.db.storage_business_io import query

    liquidity = query(
        database,
        "SELECT pool_id, has_liquidity, liquidity_index FROM v4_exchange_pool_liquidity_snapshots WHERE network = ? AND chain_id = ?",
        (ROBINHOOD_TESTNET_NETWORK, ROBINHOOD_TESTNET_CHAIN_ID),
    )
    assert len(liquidity) == 1
    assert liquidity[0]["has_liquidity"] == 1
    assert liquidity[0]["liquidity_index"] == 1000.0


def test_clear_v4_exchange_evidence_is_scoped(tmp_path):
    database = str(tmp_path / "scope.sqlite3")
    ensure_database_schema(database)
    payloads = _memory_evidence_payload()
    import_v4_evidence_payloads(
        database,
        network=ROBINHOOD_TESTNET_NETWORK,
        chain_id=ROBINHOOD_TESTNET_CHAIN_ID,
        index_payload=payloads["index"],
        liquidity_payload=payloads["liquidity"],
    )
    mainnet_pool = {
        "pool_id": "0x" + "aa" * 32,
        "pool_manager": "0x8366a39CC670B4001A1121B8F6A443A643e40951",
        "pool_key": {"currency0": "0x0000000000000000000000000000000000000000", "currency1": "0x5d3a1ff2b6bab83b63cd9ad0787074081a52ef34", "fee": 3000},
        "token0": "0x0000000000000000000000000000000000000000",
        "token1": "0x5d3a1ff2b6bab83b63cd9ad0787074081a52ef34",
        "fee": 3000,
    }
    upsert_v4_exchange_pools(
        database,
        network=ROBINHOOD_NETWORK,
        chain_id=ROBINHOOD_CHAIN_ID,
        protocol="uniswap_v4",
        pool_rows=[mainnet_pool],
    )
    counts = clear_v4_exchange_evidence(
        database,
        network=ROBINHOOD_TESTNET_NETWORK,
        chain_id=ROBINHOOD_TESTNET_CHAIN_ID,
        protocol="uniswap_v4",
    )
    assert counts["pools"] >= 1
    assert counts["liquidity_snapshots"] >= 1

    from core.db.storage_business_io import query

    testnet_pools = query(
        database,
        "SELECT COUNT(*) AS c FROM v4_exchange_pools WHERE network = ? AND chain_id = ?",
        (ROBINHOOD_TESTNET_NETWORK, ROBINHOOD_TESTNET_CHAIN_ID),
    )
    mainnet_pools = query(
        database,
        "SELECT COUNT(*) AS c FROM v4_exchange_pools WHERE network = ? AND chain_id = ?",
        (ROBINHOOD_NETWORK, ROBINHOOD_CHAIN_ID),
    )
    assert testnet_pools[0]["c"] == 0
    assert mainnet_pools[0]["c"] == 1


def test_prune_v4_non_executable_evidence_removes_only_explicit_zero(tmp_path):
    database = str(tmp_path / "prune.sqlite3")
    ensure_database_schema(database)
    zero = "0x0000000000000000000000000000000000000000"
    tz = TESTNET_ANCHOR_ADDRESS_BY_SYMBOL["TZ"]
    usde = TESTNET_ANCHOR_ADDRESS_BY_SYMBOL["USDE"]
    liquid_id = "0x" + "aa" * 32
    zero_id = "0x" + "bb" * 32
    unknown_id = "0x" + "cc" * 32
    pool_rows = [
        {
            "pool_id": liquid_id,
            "pool_manager": "0x8366a39CC670B4001A1121B8F6A443A643e40951",
            "pool_key": {"currency0": tz, "currency1": usde, "fee": 3000, "tickSpacing": 60, "hooks": zero},
            "token0": tz,
            "token1": usde,
            "fee": 3000,
            "tick_spacing": 60,
            "hooks": zero,
        },
        {
            "pool_id": zero_id,
            "pool_manager": "0x8366a39CC670B4001A1121B8F6A443A643e40951",
            "pool_key": {"currency0": tz, "currency1": usde, "fee": 3000, "tickSpacing": 60, "hooks": zero},
            "token0": tz,
            "token1": usde,
            "fee": 3000,
            "tick_spacing": 60,
            "hooks": zero,
        },
        {
            "pool_id": unknown_id,
            "pool_manager": "0x8366a39CC670B4001A1121B8F6A443A643e40951",
            "pool_key": {"currency0": tz, "currency1": usde, "fee": 3000, "tickSpacing": 60, "hooks": zero},
            "token0": tz,
            "token1": usde,
            "fee": 3000,
            "tick_spacing": 60,
            "hooks": zero,
        },
    ]
    upsert_v4_exchange_pools(
        database,
        network=ROBINHOOD_TESTNET_NETWORK,
        chain_id=ROBINHOOD_TESTNET_CHAIN_ID,
        protocol="uniswap_v4",
        pool_rows=pool_rows,
    )
    upsert_v4_liquidity_snapshots(
        database,
        network=ROBINHOOD_TESTNET_NETWORK,
        chain_id=ROBINHOOD_TESTNET_CHAIN_ID,
        protocol="uniswap_v4",
        rows=[
            {
                "pool_id": liquid_id,
                "has_liquidity": True,
                "liquidity_index": 1000.0,
                "estimated_tvl_usd": 1_000_000.0,
            },
            {
                "pool_id": zero_id,
                "has_liquidity": False,
                "depth_status": "live_liquidity_zero",
            },
        ],
    )

    counts = prune_v4_non_executable_evidence(
        database,
        network=ROBINHOOD_TESTNET_NETWORK,
        chain_id=ROBINHOOD_TESTNET_CHAIN_ID,
        protocol="uniswap_v4",
    )
    assert counts["beforePoolCount"] == 3
    assert counts["deletedZeroLiquidityCount"] == 1
    assert counts["deletedUnknownLiquidityCount"] == 0
    assert counts["deletedPoolCount"] == 1
    assert counts["afterPoolCount"] == 2

    from core.db.storage_business_io import query

    pools = query(
        database,
        "SELECT pool_id FROM v4_exchange_pools WHERE network = ? AND chain_id = ? AND protocol = ?",
        (ROBINHOOD_TESTNET_NETWORK, ROBINHOOD_TESTNET_CHAIN_ID, "uniswap_v4"),
    )
    assert {row["pool_id"] for row in pools} == {liquid_id, unknown_id}
    liquidity = query(
        database,
        "SELECT pool_id, has_liquidity FROM v4_exchange_pool_liquidity_snapshots WHERE network = ? AND chain_id = ? AND protocol = ?",
        (ROBINHOOD_TESTNET_NETWORK, ROBINHOOD_TESTNET_CHAIN_ID, "uniswap_v4"),
    )
    assert len(liquidity) == 1
    assert liquidity[0]["pool_id"] == liquid_id
    assert liquidity[0]["has_liquidity"] == 1

    forced = prune_v4_non_executable_evidence(
        database,
        network=ROBINHOOD_TESTNET_NETWORK,
        chain_id=ROBINHOOD_TESTNET_CHAIN_ID,
        protocol="uniswap_v4",
        delete_unknown=True,
    )
    assert forced["deletedUnknownLiquidityCount"] == 0
    assert forced["deletedPoolCount"] == 1
    assert forced["afterPoolCount"] == 1


def test_apply_stock_pool_metadata_normalizes_amd_and_tsla_pool_keys():
    zero = "0x0000000000000000000000000000000000000000"
    amd = "0x71178bac73cbeb415514eb542a8995b82669778d"
    tsla = "0xc9f9c86933092bbbfff3ccb4b105a4a94bf3bd4e"
    rows = [
        {
            "pool_key": {
                "currency0": zero,
                "currency1": amd,
                "currency0Metadata": {"address": zero, "symbol": "ETH", "decimals": 18, "metadataOk": True},
                "currency1Metadata": {"address": amd, "symbol": "0x71178B", "decimals": None, "metadataOk": False},
                "token1Symbol": "0x71178B",
                "token1Decimals": None,
            }
        },
        {
            "pool_key": {
                "currency0": amd,
                "currency1": tsla,
                "currency0Metadata": {"address": amd, "symbol": "0x71178B", "decimals": None, "metadataOk": False},
                "currency1Metadata": {"address": tsla, "symbol": "0xC9f9c8", "decimals": None, "metadataOk": False},
                "token0Symbol": "0x71178B",
                "token1Symbol": "0xC9f9c8",
                "token0Decimals": None,
                "token1Decimals": None,
            }
        },
    ]
    apply_stock_pool_metadata(
        rows,
        network=ROBINHOOD_TESTNET_NETWORK,
        chain_id=ROBINHOOD_TESTNET_CHAIN_ID,
    )
    first = rows[0]["pool_key"]
    assert first["currency1Metadata"]["symbol"] == "AMD"
    assert first["currency1Metadata"]["metadataOk"] is True
    assert first["token1Symbol"] == "AMD"
    second = rows[1]["pool_key"]
    assert second["currency0Metadata"]["symbol"] == "AMD"
    assert second["currency1Metadata"]["symbol"] == "TSLA"
    assert second["token0Symbol"] == "AMD"
    assert second["token1Symbol"] == "TSLA"


def test_v4_sqlite_view_loaders_mark_source_as_sqlite(tmp_path):
    database = str(tmp_path / "view.sqlite3")
    ensure_database_schema(database)
    payloads = _memory_evidence_payload()
    import_v4_evidence_payloads(
        database,
        network=ROBINHOOD_TESTNET_NETWORK,
        chain_id=ROBINHOOD_TESTNET_CHAIN_ID,
        index_payload=payloads["index"],
        liquidity_payload=payloads["liquidity"],
    )

    index = load_v4_pool_index_payload(
        ROBINHOOD_TESTNET_NETWORK,
        ROBINHOOD_TESTNET_CHAIN_ID,
        database_url=database,
    )
    liquidity = load_v4_pool_liquidity_payload(
        ROBINHOOD_TESTNET_NETWORK,
        ROBINHOOD_TESTNET_CHAIN_ID,
        database_url=database,
    )
    assert index["ok"] is True
    assert index["source"] == "sqlite_db"
    assert len(index["pools"]) == 1
    assert index["pools"][0]["source"] == "sqlite_db"
    assert liquidity["ok"] is True
    assert liquidity["source"] == "sqlite_db"
    assert liquidity["pools"][0]["hasLiquidity"] is True
    assert liquidity["pools"][0]["source"] == "sqlite_db"


def test_sqlite_pool_manager_accounting_sources_round_trip(tmp_path):
    from core.db.storage_v4_pool_manager_accounting import (
        load_v4_pool_manager_accounting,
        save_v4_pool_manager_accounting,
    )

    database = str(tmp_path / "accounting.sqlite3")
    ensure_database_schema(database)
    _seed_testnet_anchor_pair(database)
    source = {
        "address": TESTNET_ANCHOR_ADDRESS_BY_SYMBOL["TZ"],
        "symbol": "TZ",
        "decimals": 18,
        "poolManagerBalanceRaw": "4112807689333827121636",
        "poolManagerBalance": 4112.807689333827,
        "poolIds": [],
        "source": "refresh_v4_live_poolmanager_balance_of",
    }
    save_v4_pool_manager_accounting(
        database,
        network=ROBINHOOD_TESTNET_NETWORK,
        chain_id=ROBINHOOD_TESTNET_CHAIN_ID,
        pool_manager="0x8366a39CC670B4001A1121B8F6A443A643e40951",
        block_number=137_676_557,
        scan_scope="active_plus_known",
        native_balance_wei="1016132958653746712869",
        sources=[source],
    )

    loaded = load_v4_pool_manager_accounting(
        database,
        network=ROBINHOOD_TESTNET_NETWORK,
        chain_id=ROBINHOOD_TESTNET_CHAIN_ID,
    )
    assert loaded["ok"] is True
    assert loaded["sourceCount"] == 1
    assert loaded["balanceOwner"] == "poolManager"
    assert "flash_loan_repayment_source_balances" in loaded["balanceSemantics"]
    assert loaded["balanceReadMode"] == "rpc_snapshot_at_block"
    assert loaded["sources"][0]["poolManagerBalanceRaw"] == source["poolManagerBalanceRaw"]

    liquidity = load_v4_pool_liquidity_payload(
        ROBINHOOD_TESTNET_NETWORK,
        ROBINHOOD_TESTNET_CHAIN_ID,
        database_url=database,
    )
    assert liquidity["ok"] is True
    payload = liquidity["payload"]
    assert payload["source"] == "sqlite_db"
    assert payload["flashAccountingSourceCount"] == 1
    assert payload["flashAccountingSources"][0]["symbol"] == "TZ"
    assert payload["nativePoolManagerBalanceWei"] == "1016132958653746712869"


def test_robinhood_v4_default_loaders_do_not_read_json(monkeypatch):
    from pathlib import Path

    from dex.intent_trade.chains.robinhood import v4_pool_liquidity as mainnet_liquidity
    from dex.intent_trade.chains.robinhood import v4_pool_index as mainnet_index
    from dex.intent_trade.chains.robinhood_testnet import v4_pool_liquidity as testnet_liquidity
    from dex.intent_trade.chains.robinhood_testnet import v4_pool_index as testnet_index

    def fail_on_json_read(path):
        raise AssertionError(f"default loader must not read JSON: {path}")

    for module in (mainnet_liquidity, testnet_liquidity):
        monkeypatch.setattr(module, "_read_json", fail_on_json_read)
        monkeypatch.setattr(
            module,
            "chain_data_file_path",
            lambda *args, **kwargs: Path("should-not-be-read.json"),
        )
    for module in (mainnet_index, testnet_index):
        monkeypatch.setattr(
            module,
            "chain_data_file_path",
            lambda *args, **kwargs: Path("should-not-be-read.json"),
        )

    monkeypatch.setattr(
        mainnet_liquidity,
        "_load_db_liquidity_payload",
        lambda network, chain_id: {
        "ok": True,
        "source": "sqlite_db",
        "pools": [],
        },
    )
    monkeypatch.setattr(
        testnet_liquidity,
        "_load_db_liquidity_payload",
        lambda network, chain_id: {
        "ok": True,
        "source": "sqlite_db",
        "pools": [],
        },
    )
    monkeypatch.setattr(
        mainnet_index,
        "_load_db_index_payload",
        lambda network, chain_id: {
        "ok": True,
        "source": "sqlite_db",
        "pools": [],
        },
    )
    monkeypatch.setattr(
        testnet_index,
        "_load_db_index_payload",
        lambda network, chain_id: {
        "ok": True,
        "source": "sqlite_db",
        "pools": [],
        },
    )

    assert mainnet_liquidity.load_robinhood_v4_pool_liquidity()["source"] == "sqlite_db"
    assert testnet_liquidity.load_robinhood_testnet_v4_pool_liquidity()["source"] == "sqlite_db"
    assert mainnet_index.load_robinhood_mainnet_v4_pool_index()["source"] == "sqlite_db"
    assert testnet_index.load_robinhood_testnet_v4_pool_index()["source"] == "sqlite_db"


def test_dex_surface_page_refresh_uses_sqlite_live_rebuild(monkeypatch):
    from web.data_routes import dex_surface_orchestration as orchestration

    calls = {}

    def fake_refresh(**kwargs):
        calls.update(kwargs)
        return {
            "ok": True,
            "network": kwargs.get("network"),
            "chainId": kwargs.get("chain_id"),
            "steps": [{"step": "build_all_anchor_data", "ok": True}],
            "errors": [],
            "build": {"anchors": ["ETH", "TZ"]},
            "cacheEntriesCleared": 2,
        }

    monkeypatch.setattr(orchestration, "refresh_v4_live", fake_refresh)
    assert not hasattr(orchestration, "write_robinhood_mainnet_v4_pool_index")
    assert not hasattr(orchestration, "write_v4_pool_liquidity_from_contract_evidence")

    result = orchestration._refresh_v4_sources(
        chain_id=ROBINHOOD_CHAIN_ID,
        network=ROBINHOOD_NETWORK,
    )
    assert result["ok"] is True
    assert calls["skip_index"] is False
    assert calls["skip_liquidity"] is False
    assert calls["full_index_scan"] is True

    incremental = orchestration._refresh_v4_sources(
        chain_id=ROBINHOOD_TESTNET_CHAIN_ID,
        network=ROBINHOOD_TESTNET_NETWORK,
        full=False,
    )
    assert incremental["ok"] is True
    assert calls["skip_index"] is False
    assert calls["skip_liquidity"] is False
    assert calls["full_index_scan"] is False


def test_flash_loan_pool_page_refresh_does_not_call_json_writers(monkeypatch):
    from dex.tools import refresh_robinhood_v4_live as live_tool
    from web import page_routes_helpers

    calls = {}

    def fake_refresh(**kwargs):
        calls.update(kwargs)
        return {
            "ok": True,
            "import": {"v4PoolCount": 7},
            "build": {"anchors": ["ETH", "TZ"]},
            "errors": [],
            "cacheEntriesCleared": 3,
        }

    monkeypatch.setattr(
        page_routes_helpers,
        "refresh_flash_loan_pool_discovery_cache",
        lambda **kwargs: {"chain_count": 0, "protocol_count": 0},
    )
    monkeypatch.setattr(live_tool, "refresh_v4_live", fake_refresh)

    result = page_routes_helpers._refresh_flash_loan_pool_sources(
        chain_id=ROBINHOOD_TESTNET_CHAIN_ID,
        protocols=["uniswap_v4"],
        full=True,
        panel=None,
    )
    assert result["ok"] is True
    assert calls["network"] == "robinhood-testnet"
    assert calls["skip_index"] is False
    assert calls["skip_liquidity"] is False
    assert calls["full_index_scan"] is True
    assert any(
        step.get("source") == "robinhood_v4_sqlite_rebuild"
        for step in result["steps"]
    )


def test_refresh_v4_live_incremental_preserves_existing_liquidity(tmp_path):
    from dex.tools.refresh_robinhood_v4_live import refresh_v4_live

    database = str(tmp_path / "incremental.sqlite3")
    _seed_testnet_anchor_pair(database)

    report = refresh_v4_live(
        database=database,
        network=ROBINHOOD_TESTNET_NETWORK,
        chain_id=ROBINHOOD_TESTNET_CHAIN_ID,
        skip_index=True,
        skip_liquidity=True,
        skip_build=True,
        clear_cache=False,
    )

    assert report["ok"] is True
    assert report["import"]["skipped"] is True

    from core.db.storage_business_io import query

    liquidity = query(
        database,
        "SELECT pool_id, has_liquidity FROM v4_exchange_pool_liquidity_snapshots WHERE network = ? AND chain_id = ?",
        (ROBINHOOD_TESTNET_NETWORK, ROBINHOOD_TESTNET_CHAIN_ID),
    )
    assert len(liquidity) == 1
    assert liquidity[0]["has_liquidity"] == 1


def test_refresh_v4_live_skip_index_forces_incremental_evidence_preserve(
    tmp_path,
    monkeypatch,
):
    from dex.tools import refresh_robinhood_v4_live as live_tool
    from dex.tools.refresh_robinhood_v4_live import refresh_v4_live

    database = str(tmp_path / "incremental-guard.sqlite3")
    _seed_testnet_anchor_pair(database)
    monkeypatch.setattr(
        live_tool,
        "_apply_live_liquidity",
        lambda **kwargs: {
            "blockNumber": 1,
            "liquidityMarkedPoolCount": 0,
            "liquidityZeroPoolCount": 0,
            "scannedPoolCount": 0,
            "liquidityErrors": [],
        },
    )
    monkeypatch.setattr(
        live_tool,
        "_connect_rpc",
        lambda network, chain_id, rpc_url=None: (
            type("FakeW3", (), {"eth": type("FakeEth", (), {"block_number": 1})()}),
            "http://rpc.test",
        ),
    )

    report = refresh_v4_live(
        database=database,
        network=ROBINHOOD_TESTNET_NETWORK,
        chain_id=ROBINHOOD_TESTNET_CHAIN_ID,
        skip_index=True,
        skip_liquidity=False,
        skip_build=True,
        replace_evidence=True,
        clear_cache=False,
    )

    assert report["ok"] is True
    assert any(
        step.get("step") == "replace_evidence_guard"
        for step in report["steps"]
    )
    from core.db.storage_business_io import query

    liquidity = query(
        database,
        """
        SELECT pool_id, has_liquidity
        FROM v4_exchange_pool_liquidity_snapshots
        WHERE network = ? AND chain_id = ?
        """,
        (ROBINHOOD_TESTNET_NETWORK, ROBINHOOD_TESTNET_CHAIN_ID),
    )
    assert len(liquidity) == 1
    assert liquidity[0]["has_liquidity"] == 1


def test_refresh_v4_live_cold_import_disabled_is_rpc_free(tmp_path, monkeypatch):
    from dex.tools import refresh_robinhood_v4_live as live_tool

    database = str(tmp_path / "cold-rpc.sqlite3")
    _seed_testnet_anchor_pair(database)
    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def _fail_connect(*args: object, **kwargs: object) -> tuple[object, str]:
        calls.append((args, kwargs))
        raise AssertionError("cold import must not connect RPC when the chain link is offline")

    monkeypatch.setattr(live_tool, "_connect_rpc", _fail_connect)
    from dex.intent_trade.chains.robinhood_common import rpc_gate
    monkeypatch.setattr(
        rpc_gate,
        "_OFFLINE_REASONS",
        {ROBINHOOD_TESTNET_CHAIN_ID: "test_offline"},
    )
    report = live_tool.refresh_v4_live(
        database=database,
        network=ROBINHOOD_TESTNET_NETWORK,
        chain_id=ROBINHOOD_TESTNET_CHAIN_ID,
        skip_index=True,
        skip_liquidity=True,
        anchors="TZ,USDE",
        clear_cache=False,
        remove_json_evidence=False,
        prune_zero_liquidity=False,
    )

    assert report["ok"] is True
    assert calls == []


def test_refresh_v4_live_dry_run_is_read_only_and_plans_small_batch(
    tmp_path,
    monkeypatch,
) -> None:
    from dex.tools import refresh_robinhood_v4_live as live_tool

    database = str((tmp_path / "dry-run.sqlite3").resolve())
    _seed_testnet_anchor_pair(database)
    tz = TESTNET_ANCHOR_ADDRESS_BY_SYMBOL["TZ"].lower()
    usde = TESTNET_ANCHOR_ADDRESS_BY_SYMBOL["USDE"].lower()
    now = "2026-10-22T00:00:00Z"
    for direction_key in (f"{tz}:{usde}", f"{usde}:{tz}"):
        execute(
            database,
            """
            INSERT INTO dex_candidate_pool_pairs (
                network, chain_id, protocol, anchor_scope, anchor_symbol,
                anchor_address, counter_address, address_pair, direction_key,
                pool_count, fixed_fee_liquid_pool_count, priced_pool_count,
                yc_optimal_pool_id, zc_optimal_pool_id,
                yc_backup_pool_ids_json, zc_backup_pool_ids_json,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, '[]', '[]', ?, ?)
            """,
            (
                ROBINHOOD_TESTNET_NETWORK,
                ROBINHOOD_TESTNET_CHAIN_ID,
                "uniswap_v4",
                ROBINHOOD_TESTNET_MARKET_ID,
                "TZ",
                tz,
                usde,
                f"{tz}:{usde}",
                direction_key,
                0,
                0,
                0,
                now,
                now,
            ),
        )

    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def _fail_connect(*args: object, **kwargs: object) -> tuple[object, str]:
        calls.append((args, kwargs))
        raise AssertionError("dry-run must not connect RPC")

    monkeypatch.setattr(live_tool, "_connect_rpc", _fail_connect)
    before_files = {item.name: item.stat().st_size for item in tmp_path.iterdir()}

    report = live_tool.refresh_v4_live(
        database=database,
        network=ROBINHOOD_TESTNET_NETWORK,
        chain_id=ROBINHOOD_TESTNET_CHAIN_ID,
        candidate_backfill=True,
        skip_index=True,
        skip_liquidity=True,
        pool_ids=["0x" + "aa" * 32],
        import_pool_ids=["0x" + "bb" * 32],
        batch_size=5,
        dry_run=True,
        clear_cache=False,
    )

    after_files = {item.name: item.stat().st_size for item in tmp_path.iterdir()}
    assert report["ok"] is True
    assert report["dryRun"] is True
    assert report["readOnly"] is True
    assert report["steps"][0]["connection"] == "sqlite_mode_ro"
    assert report["plan"]["neededDirectionCount"] == 2
    assert set(report["plan"]["targets"]) == {f"{tz}:{usde}", f"{usde}:{tz}"}
    assert report["requested"]["poolIds"] == ["0x" + "aa" * 32]
    assert report["requested"]["importPoolIds"] == ["0x" + "bb" * 32]
    assert report["requested"]["batchSize"] == 5
    assert calls == []
    assert after_files == before_files


def test_direction_backfill_plan_enumerates_missing_anchor_directions(
    tmp_path,
) -> None:
    from dex.tools import refresh_robinhood_v4_live as live_tool

    database = str((tmp_path / "direction-plan.sqlite3").resolve())
    ensure_database_schema(database)
    tz = TESTNET_ANCHOR_ADDRESS_BY_SYMBOL["TZ"].lower()
    usde = TESTNET_ANCHOR_ADDRESS_BY_SYMBOL["USDE"].lower()

    plan = live_tool._direction_backfill_plan(
        database=database,
        network=ROBINHOOD_TESTNET_NETWORK,
        chain_id=ROBINHOOD_TESTNET_CHAIN_ID,
        anchor_addresses={"TZ": tz, "USDE": usde},
    )

    assert plan["neededDirectionCount"] == 2
    assert set(plan["targets"]) == {f"{tz}:{usde}", f"{usde}:{tz}"}
    assert set(plan["targets"].values()) == {MAX_DIRECTION_CANDIDATE_POOLS}
    assert plan["basis"] == "direction_candidate_shortfall"


def test_refresh_v4_live_main_dry_run_prints_read_only_report(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    from dex.tools import refresh_robinhood_v4_live as live_tool

    database = str((tmp_path / "cli-dry-run.sqlite3").resolve())
    _seed_testnet_anchor_pair(database)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "refresh_robinhood_v4_live.py",
            "--database",
            database,
            "--network",
            ROBINHOOD_TESTNET_NETWORK,
            "--chain-id",
            str(ROBINHOOD_TESTNET_CHAIN_ID),
            "--dry-run",
            "--candidate-backfill",
            "--skip-index",
            "--skip-liquidity",
            "--pool-ids",
            "0x" + "aa" * 32,
            "--import-pool-ids",
            "0x" + "bb" * 32,
            "--batch-size",
            "3",
            "--no-clear-cache",
        ],
    )

    assert live_tool.main() == 0
    report = json.loads(capsys.readouterr().out)
    assert report["ok"] is True
    assert report["dryRun"] is True
    assert report["readOnly"] is True
    assert report["requested"]["batchSize"] == 3
    assert report["requested"]["poolIds"] == ["0x" + "aa" * 32]


def test_v4_incremental_guard_requires_every_pool_has_confirmed_liquidity(tmp_path):
    from dex.tools.refresh_robinhood_v4_live import (
        _v4_incremental_database_state,
        _resolve_v4_scan_decision,
    )

    database = str(tmp_path / "guard.sqlite3")
    _seed_testnet_anchor_pair(database)

    state = _v4_incremental_database_state(
        database=database,
        network=ROBINHOOD_TESTNET_NETWORK,
        chain_id=ROBINHOOD_TESTNET_CHAIN_ID,
    )
    assert state["poolCount"] == 1
    assert state["confirmedLiquidityPoolCount"] == 1
    assert state["eligiblePoolCount"] == 1
    assert state["eligibleConfirmedCount"] == 1
    assert state["eligibleUnknownCount"] == 0
    assert state["genericUnknownCount"] == 0
    assert state["complete"] is True

    decision = _resolve_v4_scan_decision(
        database_state=state,
        last_scanned_block=1000,
        full_index_scan=False,
        from_block_override=None,
    )
    assert decision["scanMode"] == "incremental"
    assert decision["fromBlock"] == 1001
    assert decision["forcedFullScan"] is False


def test_v4_incremental_guard_scopes_missing_and_zero_liquidity_to_eligible_pools(tmp_path):
    from core.db.storage_business_io import execute
    from dex.tools.refresh_robinhood_v4_live import (
        _v4_incremental_database_state,
        _resolve_v4_scan_decision,
    )

    database = str(tmp_path / "guard-incomplete.sqlite3")
    _seed_testnet_anchor_pair(database)

    execute(
        database,
        """
        INSERT INTO v4_exchange_pools (
            network, chain_id, protocol, pool_id, pool_identity_mode,
            pool_manager, token0, token1, fee, dynamic_fee, special_fee,
            tick_spacing, hooks, address_pair, created_at, updated_at
        ) VALUES (?, ?, ?, ?, 'v4_pool_id', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            ROBINHOOD_TESTNET_NETWORK,
            ROBINHOOD_TESTNET_CHAIN_ID,
            "uniswap_v4",
            "pool-missing-liquidity",
            "0x8366a39CC670B4001A1121B8F6A443A643e40951",
            TESTNET_ANCHOR_ADDRESS_BY_SYMBOL["TZ"],
            TESTNET_ANCHOR_ADDRESS_BY_SYMBOL["USDE"],
            3000,
            0,
            0,
            60,
            "0x0000000000000000000000000000000000000000",
            "tz:usde",
            "2026-10-17T00:00:00Z",
            "2026-10-17T00:00:00Z",
        ),
    )

    missing_state = _v4_incremental_database_state(
        database=database,
        network=ROBINHOOD_TESTNET_NETWORK,
        chain_id=ROBINHOOD_TESTNET_CHAIN_ID,
    )
    assert missing_state["poolCount"] == 2
    assert missing_state["missingLiquiditySnapshotCount"] == 1
    assert missing_state["complete"] is False

    missing_decision = _resolve_v4_scan_decision(
        database_state=missing_state,
        last_scanned_block=1000,
        full_index_scan=False,
        from_block_override=None,
    )
    assert missing_decision["scanMode"] == "full_forced_db_incomplete"
    assert missing_decision["fromBlock"] == 0
    assert missing_decision["forcedFullScan"] is True

    upsert_v4_liquidity_snapshots(
        database,
        network=ROBINHOOD_TESTNET_NETWORK,
        chain_id=ROBINHOOD_TESTNET_CHAIN_ID,
        protocol="uniswap_v4",
        rows=[
            {
                "pool_id": "pool-missing-liquidity",
                "has_liquidity": False,
                "liquidity_index": 0.0,
                "estimated_tvl_usd": 0.0,
            }
        ],
    )

    zero_state = _v4_incremental_database_state(
        database=database,
        network=ROBINHOOD_TESTNET_NETWORK,
        chain_id=ROBINHOOD_TESTNET_CHAIN_ID,
    )
    assert zero_state["zeroLiquidityPoolCount"] == 1
    assert zero_state["eligibleZeroCount"] == 1
    assert zero_state["eligibleUnknownCount"] == 0
    assert zero_state["complete"] is True

    zero_decision = _resolve_v4_scan_decision(
        database_state=zero_state,
        last_scanned_block=1000,
        full_index_scan=False,
        from_block_override=None,
    )
    assert zero_decision["scanMode"] == "incremental"
    assert zero_decision["fromBlock"] == 1001


def test_v4_incremental_guard_ignores_generic_unknown_pools(tmp_path):
    from core.db.storage_business_io import execute
    from dex.tools.refresh_robinhood_v4_live import (
        _v4_incremental_database_state,
        _resolve_v4_scan_decision,
    )

    database = str(tmp_path / "guard-generic-unknown.sqlite3")
    _seed_testnet_anchor_pair(database)

    # Give the known fixture tokens an explicit category so the maintenance
    # helper does not fall back to treating every raw pool token as eligible.
    execute(
        database,
        """
        UPDATE chain_tokens
        SET token_metadata_json = '{\"category\":\"test_fixture\"}'
        WHERE network = ? AND chain_id = ?
        """,
        (ROBINHOOD_TESTNET_NETWORK, ROBINHOOD_TESTNET_CHAIN_ID),
    )
    execute(
        database,
        """
        INSERT INTO v4_exchange_pools (
            network, chain_id, protocol, pool_id, pool_identity_mode,
            pool_manager, token0, token1, fee, dynamic_fee, special_fee,
            tick_spacing, hooks, address_pair, created_at, updated_at
        ) VALUES (?, ?, ?, ?, 'v4_pool_id', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            ROBINHOOD_TESTNET_NETWORK,
            ROBINHOOD_TESTNET_CHAIN_ID,
            "uniswap_v4",
            "pool-generic-unknown",
            "0x8366a39CC670B4001A1121B8F6A443A643e40951",
            "0x" + "11" * 20,
            "0x" + "22" * 20,
            3000,
            0,
            0,
            60,
            "0x0000000000000000000000000000000000000000",
            "generic:unknown",
            "2026-10-17T00:00:00Z",
            "2026-10-17T00:00:00Z",
        ),
    )

    state = _v4_incremental_database_state(
        database=database,
        network=ROBINHOOD_TESTNET_NETWORK,
        chain_id=ROBINHOOD_TESTNET_CHAIN_ID,
    )
    assert state["totalPoolCount"] == 2
    assert state["eligiblePoolCount"] == 1
    assert state["eligibleUnknownCount"] == 0
    assert state["genericUnknownCount"] == 1
    assert state["complete"] is True

    decision = _resolve_v4_scan_decision(
        database_state=state,
        last_scanned_block=1000,
        full_index_scan=False,
        from_block_override=None,
    )
    assert decision["scanMode"] == "incremental"
    assert decision["forcedFullScan"] is False


def test_v4_incremental_guard_skips_force_for_explicit_overrides(tmp_path):
    from dex.tools.refresh_robinhood_v4_live import (
        _v4_incremental_database_state,
        _resolve_v4_scan_decision,
    )

    database = str(tmp_path / "guard-override.sqlite3")
    ensure_database_schema(database)

    empty_state = _v4_incremental_database_state(
        database=database,
        network=ROBINHOOD_TESTNET_NETWORK,
        chain_id=ROBINHOOD_TESTNET_CHAIN_ID,
    )
    assert empty_state["complete"] is False

    override_decision = _resolve_v4_scan_decision(
        database_state=empty_state,
        last_scanned_block=1000,
        full_index_scan=False,
        from_block_override=777,
    )
    assert override_decision["scanMode"] == "explicit_override"
    assert override_decision["fromBlock"] == 777
    assert override_decision["forcedFullScan"] is False


def test_v4_scan_state_persists_last_block_for_incremental_refresh(tmp_path, monkeypatch):
    from dex.tools.refresh_robinhood_v4_live import refresh_v4_live

    database = str(tmp_path / "scan-state.sqlite3")
    _seed_testnet_anchor_pair(database)

    first = refresh_v4_live(
        database=database,
        network=ROBINHOOD_TESTNET_NETWORK,
        chain_id=ROBINHOOD_TESTNET_CHAIN_ID,
        skip_index=True,
        skip_liquidity=True,
        skip_build=True,
        clear_cache=False,
    )
    assert first["ok"] is True

    update_v4_scan_state(
        database,
        network=ROBINHOOD_TESTNET_NETWORK,
        chain_id=ROBINHOOD_TESTNET_CHAIN_ID,
        last_scanned_block=123456,
        index_from_block=100000,
        index_to_block=123456,
        liquidity_scan_block=123456,
        scanned_pool_count=10,
        pool_count=25,
        confirmed_liquidity_pool_count=8,
        explicit_zero_liquidity_pool_count=2,
        unscanned_pool_count=15,
        scan_mode="incremental",
    )

    state = load_v4_scan_state(
        database,
        network=ROBINHOOD_TESTNET_NETWORK,
        chain_id=ROBINHOOD_TESTNET_CHAIN_ID,
    )
    assert state["last_scanned_block"] == 123456
    assert state["index_from_block"] == 100000
    assert state["index_to_block"] == 123456
    assert state["liquidity_scan_block"] == 123456
    assert state["scanned_pool_count"] == 10
    assert state["direction_candidate_limit"] == 5

    from dex.tools import refresh_robinhood_v4_live as live_tool

    called_from_blocks: list[int] = []

    def fake_load_index_payload(**kwargs: object) -> dict:
        called_from_blocks.append(int(kwargs.get("from_block") or 0))
        return {
            "payload": {
                "poolManager": "0x8366a39CC670B4001A1121B8F6A443A643e40951",
                "poolCount": 1,
                "generatedBlockNumber": 123457,
                "pools": [
                    {
                        "poolId": "pool-tz-usde",
                        "poolManager": "0x8366a39CC670B4001A1121B8F6A443A643e40951",
                        "token0": TESTNET_ANCHOR_ADDRESS_BY_SYMBOL["TZ"],
                        "token1": TESTNET_ANCHOR_ADDRESS_BY_SYMBOL["USDE"],
                        "fee": 3000,
                        "hooks": "0x0000000000000000000000000000000000000000",
                        "status": "initialized_pool",
                    }
                ],
            },
            "source": "onchain_initialize_logs",
        }

    monkeypatch.setattr(live_tool, "_load_index_payload", fake_load_index_payload)
    live_tool.refresh_v4_live(
        database=database,
        network=ROBINHOOD_TESTNET_NETWORK,
        chain_id=ROBINHOOD_TESTNET_CHAIN_ID,
        skip_liquidity=True,
        skip_build=True,
        clear_cache=False,
    )
    assert called_from_blocks == [123457]


def test_v4_incremental_index_merges_existing_sqlite_pools_with_new_pools(tmp_path):
    from dex.tools.refresh_robinhood_v4_live import _merge_index_with_sqlite_pools

    database = str(tmp_path / "merge.sqlite3")
    _seed_testnet_anchor_pair(database)
    new_pool_id = "0x" + "11" * 32
    onchain = {
        "schemaVersion": "robinhood-testnet-v4-pool-index-v1",
        "network": ROBINHOOD_TESTNET_NETWORK,
        "chainId": ROBINHOOD_TESTNET_CHAIN_ID,
        "poolManager": "0x8366a39CC670B4001A1121B8F6A443A643e40951",
        "generatedBlockNumber": 123457,
        "pools": [
            {
                "poolId": new_pool_id,
                "token0": TESTNET_ANCHOR_ADDRESS_BY_SYMBOL["USDE"],
                "token1": TESTNET_ANCHOR_ADDRESS_BY_SYMBOL["TZ"],
                "fee": 3000,
                "hooks": "0x0000000000000000000000000000000000000000",
                "status": "initialized_pool",
            }
        ],
    }
    merged = _merge_index_with_sqlite_pools(
        onchain,
        database=database,
        network=ROBINHOOD_TESTNET_NETWORK,
        chain_id=ROBINHOOD_TESTNET_CHAIN_ID,
    )
    pool_ids = {str(pool.get("poolId") or "").lower() for pool in merged["pools"]}
    assert "pool-tz-usde" in pool_ids
    assert new_pool_id in pool_ids
    assert merged["generatedBlockNumber"] == 123457
    assert merged["poolManager"] == onchain["poolManager"]


def test_incremental_import_filters_pool_keys_with_pool_rows():
    from dex.tools.refresh_robinhood_v4_live import _filter_index_payload_to_pool_ids

    first = "0x" + "11" * 32
    second = "0x" + "22" * 32
    payload = {
        "poolCount": 2,
        "pools": [{"poolId": first}, {"poolId": second}],
        "poolKeys": [
            {"poolId": first, "token0": "0x01", "token1": "0x02"},
            {"poolId": second, "token0": "0x03", "token1": "0x04"},
        ],
    }

    filtered = _filter_index_payload_to_pool_ids(payload, [first])

    assert filtered["poolCount"] == 1
    assert [row["poolId"] for row in filtered["pools"]] == [first]
    assert [row["poolId"] for row in filtered["poolKeys"]] == [first]


def test_mainnet_incremental_import_filters_pool_keys_with_pool_rows():
    """Mainnet uses the same incremental import entry; poolKeys must stay scoped too."""
    from dex.tools.refresh_robinhood_v4_live import _filter_index_payload_to_pool_ids

    first = "0x" + "77" * 32
    second = "0x" + "88" * 32
    payload = {
        "poolCount": 2,
        "poolManager": "0x1234",
        "pools": [
            {"poolId": first, "token0": "0x01", "token1": "0x02", "fee": 3000},
            {"poolId": second, "token0": "0x03", "token1": "0x04", "fee": 500},
        ],
        "poolKeys": [
            {"poolId": first, "token0": "0x01", "token1": "0x02"},
            {"poolId": second, "token0": "0x03", "token1": "0x04"},
        ],
    }

    filtered = _filter_index_payload_to_pool_ids(payload, [first])

    assert filtered["poolCount"] == 1
    assert [row["poolId"] for row in filtered["pools"]] == [first]
    assert [row["poolId"] for row in filtered["poolKeys"]] == [first]


def test_testnet_backfill_liquidity_confirm_calls_batch_without_w3(monkeypatch):
    from dex.intent_trade.chains.robinhood_testnet import v4_quote_adapter as quote_adapter
    from dex.tools.refresh_robinhood_v4_live import _confirm_backfill_pool_liquidity

    pool_id = "0x" + "ab" * 32
    seen: dict = {}

    def fake_batch(**kwargs):
        seen["kwargs"] = kwargs
        assert "w3" not in kwargs
        return {
            pool_id: {
                "liquidity": str(10**18),
                "source": "pool_manager_extsload_live_state_batch",
            }
        }

    monkeypatch.setattr(quote_adapter, "_batch_read_live_pool_states", fake_batch)
    confirm = _confirm_backfill_pool_liquidity(
        network=ROBINHOOD_TESTNET_NETWORK,
        rpc_url="https://fake-testnet.example",
        pool_manager="0x8366a39CC670B4001A1121B8F6A443A643e40951",
        batch_size=100,
    )
    confirmed = confirm([{"poolId": pool_id}])
    assert confirmed == {pool_id}
    assert seen["kwargs"]["rpc_url"] == "https://fake-testnet.example"
    assert seen["kwargs"]["pool_ids"] == [pool_id]


def test_mainnet_batch_refresh_accepts_explicit_zero_liquidity_chunk(monkeypatch):
    from dex.tools import refresh_robinhood_v4_live as live_tool

    pool_id = "0x" + "ee" * 32
    index_payload = {
        "network": "robinhood",
        "chainId": 4663,
        "poolManager": "0x" + "44" * 20,
        "pools": [{"poolId": pool_id}],
    }

    def fake_batch(**kwargs):
        assert kwargs["pool_ids"] == [pool_id]
        return {
            pool_id: {
                "error": "robinhood mainnet pool manager live liquidity is zero",
                "source": "pool_manager_extsload_live_state_batch_rust",
            }
        }

    monkeypatch.setattr(
        "dex.intent_trade.chains.robinhood.dex_pool_candidates._batch_read_v4_pool_states",
        fake_batch,
    )
    result = live_tool._apply_batch_live_liquidity(
        index_payload=index_payload,
        w3=None,
        rpc_url="https://fake-mainnet.example",
        pool_manager="0x" + "44" * 20,
        pool_ids=[pool_id],
        batch_size=10,
        block_number=123,
    )

    pool = index_payload["pools"][0]
    assert result["liquidityZeroPoolCount"] == 1
    assert result["liquidityErrorCount"] == 0
    assert result["liquidityErrors"] == []
    assert pool["hasLiquidity"] is False
    assert pool["liquidity"] == ""
    assert pool["depthStatus"] == "live_liquidity_zero"
    assert pool["liquidityError"] is None
