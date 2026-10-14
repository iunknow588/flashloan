"""Coverage for the official Robinhood stock-token registry integration."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from core.chain_data_registry import chain_data_file_path
from core.db import builders
from core.db.import_existing_json import _token_rows_from_v4_index
from core.db import page_payloads
from core.db.storage_schema import ensure_database_schema
from dex.tools.build_robinhood_stock_token_registry import build_registry
from dex.intent_trade.chains.robinhood_common.stock_token_registry import (
    apply_stock_token_metadata,
    load_stock_token_registry,
    merge_stock_registry_into_metadata,
    stock_token_by_address,
    stock_token_by_symbol,
)
from dex.intent_trade.chains.robinhood_common.chain_config import (
    all_anchor_addresses_by_symbol,
    all_anchor_symbols,
    configured_anchor_address_by_symbol,
)
from dex.intent_trade.chains.robinhood import market_observation as robinhood_market_observation
from dex.intent_trade.chains.robinhood_testnet import market_observation as robinhood_testnet_market_observation


STOCK_SYMBOLS = {"AAPL", "NVDA", "MSFT", "GOOGL", "AMZN", "META", "TSLA", "AMD"}
TESTNET_STOCK_BY_SYMBOL = {
    "AMD": "0x71178BAc73cBeb415514eB542a8995b82669778d",
    "AMZN": "0x5884aD2f920c162CFBbACc88C9C51AA75eC09E02",
    "NFLX": "0x3b8262A63d25f0477c4DDE23F83cfe22Cb768C93",
    "PLTR": "0x1FBE1a0e43594b3455993B5dE5Fd0A7A266298d0",
    "TSLA": "0xC9f9c86933092BbbfFF3CCb4b105A4A94bf3Bd4E",
}


def test_stock_token_registry_is_complete_and_consistent():
    payload = load_stock_token_registry()
    tokens = payload.get("tokens") or {}
    assert int(payload.get("token_count") or 0) == len(tokens) >= 194
    symbols = {str(item.get("symbol") or "").upper() for item in tokens.values()}
    addresses = {str(item.get("address") or "").lower() for item in tokens.values()}
    assert len(symbols) == len(tokens)
    assert len(addresses) == len(tokens)
    for item in tokens.values():
        assert str(item.get("address") or "").lower().startswith("0x")
        assert len(str(item.get("address") or "")) == 42
        assert int(item.get("decimals") or 0) == 18
    assert STOCK_SYMBOLS <= symbols
    assert stock_token_by_symbol()["AAPL"]["address"].lower() == "0xaf3d76f1834a1d425780943c99ea8a608f8a93f9"


def test_build_registry_rejects_duplicate_symbol_address():
    address = "0xaf3d76f1834a1d425780943c99ea8a608f8a93f9"
    assets = [
        {
            "tokenSymbol": "AAPL",
            "tokenDecimals": 18,
            "deployments": [{"chainId": 4663, "contractAddress": address}],
        },
        {
            "tokenSymbol": "FAKE",
            "tokenDecimals": 18,
            "deployments": [{"chainId": 4663, "contractAddress": address}],
        },
    ]
    payload = build_registry(assets)
    assert payload["token_count"] == 1
    assert payload["tokens"]["aapl"]["symbol"] == "AAPL"
    assert "fake" not in payload["tokens"]
    assert any("duplicate-address" in error for error in payload["errors"])


def test_generated_mainnet_metadata_covers_every_official_stock_token():
    registry = load_stock_token_registry()
    metadata_path = chain_data_file_path("robinhood", "v4_token_metadata", chain_id=4663)
    assert metadata_path is not None
    metadata = json.loads(Path(metadata_path).read_text(encoding="utf-8"))
    tokens = metadata.get("tokens") or {}
    assert int(metadata.get("stockTokenCount") or 0) >= 194
    for item in (registry.get("tokens") or {}).values():
        row = tokens.get(str(item["address"]).lower())
        assert row is not None
        assert row["symbol"] == str(item["symbol"]).upper()
        assert row["source"] == "robinhood_stock_official"
        assert row["metadataOk"] is True


def test_testnet_stock_registry_has_five_tokens():
    registry = load_stock_token_registry(
        network="robinhood-testnet",
        chain_id=46630,
    )
    tokens = registry.get("tokens") or {}
    assert len(tokens) == 5
    assert int(registry.get("token_count") or 0) == 5
    by_symbol = stock_token_by_symbol(network="robinhood-testnet", chain_id=46630)
    assert set(by_symbol) == set(TESTNET_STOCK_BY_SYMBOL)
    for symbol, address in TESTNET_STOCK_BY_SYMBOL.items():
        assert by_symbol[symbol]["address"].lower() == address.lower()
        assert int(by_symbol[symbol]["decimals"] or 0) == 18
        assert by_symbol[symbol]["category"] == "robinhood_stock"


def test_apply_stock_token_metadata_overrides_placeholder_symbol():
    rows = [
        {
            "token_address": "0xaf3d76f1834a1d425780943c99ea8a608f8a93f9",
            "token_symbol": "0XAF3D76F1",
            "token_decimals": None,
        }
    ]
    apply_stock_token_metadata(rows)
    assert rows[0]["token_symbol"] == "AAPL"
    assert rows[0]["token_decimals"] == 18
    assert rows[0]["token_metadata"]["category"] == "robinhood_stock"


def test_apply_testnet_stock_token_metadata_overrides_placeholder_symbol():
    rows = [
        {
            "token_address": "0xC9f9c86933092BbbfFF3CCb4b105A4A94bf3Bd4E",
            "token_symbol": "0XC9F9C8",
            "token_decimals": None,
        }
    ]
    apply_stock_token_metadata(
        rows,
        network="robinhood-testnet",
        chain_id=46630,
    )
    assert rows[0]["token_symbol"] == "TSLA"
    assert rows[0]["token_decimals"] == 18
    assert rows[0]["token_metadata"]["category"] == "robinhood_stock"


def test_merge_stock_registry_into_metadata():
    metadata = {
        "schemaVersion": "test",
        "tokenAddressCount": 1,
        "metadataOkCount": 1,
        "metadataMissingCount": 0,
        "tokens": {
            "0xaf3d76f1834a1d425780943c99ea8a608f8a93f9": {
                "address": "0xaf3d76f1834a1d425780943c99ea8a608f8a93f9",
                "symbol": "0XAF3D76F1",
                "decimals": 18,
                "metadataOk": True,
                "source": "chain_live_erc20",
            }
        },
    }
    merged = merge_stock_registry_into_metadata(metadata)
    tokens = merged["tokens"]
    assert tokens["0xaf3d76f1834a1d425780943c99ea8a608f8a93f9"]["symbol"] == "AAPL"
    assert tokens["0xaf3d76f1834a1d425780943c99ea8a608f8a93f9"]["source"] == "robinhood_stock_official"
    assert int(merged["tokenAddressCount"]) >= 194
    assert int(merged["metadataOkCount"]) >= 194


def test_v4_index_token_rows_use_official_stock_symbols():
    usde = "0x5d3a1ff2b6bab83b63cd9ad0787074081a52ef34"
    aapl = "0xaf3d76f1834a1d425780943c99ea8a608f8a93f9"
    index = {
        "schemaVersion": "test",
        "pools": [
            {
                "poolId": "0x" + "aa" * 32,
                "poolKey": {
                    "currency0": aapl,
                    "currency1": usde,
                    "token0Symbol": "0XAF3D76F1",
                    "token1Symbol": "USDE",
                },
                "token0": aapl,
                "token1": usde,
                "token0Symbol": "0XAF3D76F1",
                "token1Symbol": "USDE",
            }
        ],
    }
    rows = _token_rows_from_v4_index(
        index,
        anchor_symbols=("ETH", "WETH", "USDG", "USDE", "TZ"),
        stable_symbols={"USDG", "USDE", "TZ"},
    )
    by_address = {row["token_address"]: row for row in rows}
    assert by_address[aapl]["token_symbol"] == "AAPL"
    assert by_address[aapl]["token_decimals"] == 18


def test_seed_chain_token_symbols_migrates_stock_rows(tmp_path):
    database = str(tmp_path / "stock_seed.sqlite3")
    ensure_database_schema(database)
    connection = sqlite3.connect(database)
    connection.execute(
        """
        INSERT INTO chain_tokens (
            network, chain_id, token_address, token_symbol, token_name,
            token_decimals, token_kind, is_native, is_wrapped_native,
            is_stable, anchor_symbol, token_metadata_json, source_json,
            first_seen_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "robinhood",
            4663,
            "0xaf3d76f1834a1d425780943c99ea8a608f8a93f9",
            "0XAF3D76F1",
            "0XAF3D76F1",
            None,
            "erc20",
            0,
            0,
            0,
            None,
            "{}",
            "{}",
            "2026-10-12T00:00:00Z",
            "2026-10-12T00:00:00Z",
        ),
    )
    connection.commit()
    connection.close()

    builders._seed_chain_token_symbols(database, network="robinhood", chain_id=4663)

    connection = sqlite3.connect(database)
    row = connection.execute(
        "SELECT token_symbol, token_decimals, token_metadata_json "
        "FROM chain_tokens WHERE token_address = ?",
        ("0xaf3d76f1834a1d425780943c99ea8a608f8a93f9",),
    ).fetchone()
    connection.close()
    assert row[0] == "AAPL"
    assert row[1] == 18
    assert json.loads(row[2])["category"] == "robinhood_stock"


def test_seed_chain_token_symbols_seeds_full_stock_registry_on_mainnet(tmp_path):
    database = str(tmp_path / "stock_seed_full.sqlite3")
    ensure_database_schema(database)

    builders._seed_chain_token_symbols(database, network="robinhood", chain_id=4663)

    connection = sqlite3.connect(database)
    rows = connection.execute(
        """
        SELECT token_symbol, token_address, token_decimals, token_metadata_json
        FROM chain_tokens
        WHERE network = 'robinhood' AND chain_id = 4663
        """
    ).fetchall()
    connection.close()
    stock_rows = [
        row
        for row in rows
        if json.loads(row[3]).get("category") == "robinhood_stock"
    ]
    assert len(stock_rows) >= 194
    symbols = {row[0] for row in stock_rows}
    assert {"AAPL", "NVDA", "MSFT", "GOOGL", "AMZN", "META", "TSLA", "AMD"} <= symbols
    assert all(row[1].startswith("0x") and row[2] == 18 for row in stock_rows)
    assert not any(row[0].startswith("0X") for row in stock_rows)


def test_seed_chain_token_symbols_seeds_testnet_stock_registry(tmp_path):
    database = str(tmp_path / "stock_seed_testnet.sqlite3")
    ensure_database_schema(database)

    builders._seed_chain_token_symbols(
        database,
        network="robinhood-testnet",
        chain_id=46630,
    )

    connection = sqlite3.connect(database)
    rows = connection.execute(
        """
        SELECT token_symbol, token_address, token_decimals, token_metadata_json
        FROM chain_tokens
        WHERE network = 'robinhood-testnet' AND chain_id = 46630
        """
    ).fetchall()
    connection.close()
    stock_rows = [
        row
        for row in rows
        if json.loads(row[3]).get("category") == "robinhood_stock"
    ]
    assert len(stock_rows) == 5
    symbols = {row[0] for row in stock_rows}
    assert symbols == set(TESTNET_STOCK_BY_SYMBOL)
    assert all(row[1].startswith("0x") and row[2] == 18 for row in stock_rows)
    assert not any(row[0].startswith("0X") for row in stock_rows)


def test_stock_tokens_are_first_class_anchors_in_chain_config():
    mainnet_symbols = all_anchor_symbols("robinhood")
    assert "AAPL" in mainnet_symbols
    assert "AMD" in mainnet_symbols
    assert "ETH" in mainnet_symbols
    assert mainnet_symbols.index("ETH") < mainnet_symbols.index("AAPL")

    mainnet_addresses = all_anchor_addresses_by_symbol("robinhood")
    assert mainnet_addresses["AAPL"].lower() == "0xaf3d76f1834a1d425780943c99ea8a608f8a93f9"
    assert configured_anchor_address_by_symbol("robinhood")["AMD"].lower() == (
        "0x86923f96303D656E4aa86D9d42D1e57ad2023fdC"
    ).lower()

    testnet_symbols = all_anchor_symbols("robinhood-testnet")
    assert {"AMD", "AMZN", "NFLX", "PLTR", "TSLA"} <= set(testnet_symbols)
    assert configured_anchor_address_by_symbol("robinhood-testnet")["TSLA"].lower() == (
        "0xC9f9c86933092BbbfFF3CCb4b105A4A94bf3Bd4E"
    ).lower()


def test_builders_resolve_stock_anchor_addresses_without_chain_tokens():
    assert builders._resolve_anchor_address(
        "AAPL",
        [],
        [],
    ).lower() == "0xaf3d76f1834a1d425780943c99ea8a608f8a93f9"
    assert builders._resolve_anchor_address(
        "TSLA",
        [],
        [],
        network="robinhood-testnet",
    ).lower() == "0xC9f9c86933092BbbfFF3CCb4b105A4A94bf3Bd4E".lower()


def test_page_payload_default_anchor_symbols_include_stocks():
    mainnet = page_payloads._default_anchor_symbols(4663)
    assert "AAPL" in mainnet
    assert "AMD" in mainnet
    testnet = page_payloads._default_anchor_symbols(46630)
    assert "TSLA" in testnet
    assert "PLTR" in testnet


def test_robinhood_observation_anchor_symbols_include_stocks():
    assert "AAPL" in robinhood_market_observation.MAINNET_CONFIGURED_ANCHOR_SYMBOLS
    assert "AAPL" in robinhood_market_observation.observation_anchor_symbols(
        {},
        chain_id=4663,
        protocols=["uniswap_v4"],
    )
    assert "AMD" in robinhood_testnet_market_observation.TESTNET_DB_ANCHOR_SYMBOLS
    assert "AMD" in robinhood_testnet_market_observation.observation_anchor_symbols(
        {},
        chain_id=46630,
        protocols=["uniswap_v4"],
    )
