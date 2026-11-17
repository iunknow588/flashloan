"""Unit tests for the Robinhood Testnet token metadata backfill tool."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from core.db.storage_business_io import execute, query
from dex.tools.resolve_robinhood_testnet_token_metadata import (
    DEFAULT_REPAIR_ADDRESSES,
    _funding_library_tokens,
    _mark_remaining_placeholders,
    _repair_columns_from_metadata,
    _repair_source_json,
    _select_target_addresses,
    _scoped_addresses,
    _write_rows,
)


NETWORK = "robinhood-testnet"
CHAIN_ID = 46630
ADDR1 = "0x450d9bc42be9a45269678669a786d2c3c5221bdf"
ADDR2 = "0x4e5c427499c43b222fec1ef60f5e2164b904d84e"
ADDR3 = "0x" + "3" * 40
OLD_UPDATED_AT = "2026-01-01T00:00:00Z"


def _create_chain_tokens(path: Path) -> None:
    connection = sqlite3.connect(str(path))
    connection.execute(
        """
        CREATE TABLE chain_tokens (
            network TEXT NOT NULL,
            chain_id INTEGER NOT NULL,
            token_address TEXT NOT NULL,
            token_symbol TEXT NOT NULL,
            token_name TEXT,
            token_decimals INTEGER,
            anchor_symbol TEXT,
            token_metadata_json TEXT,
            source_json TEXT,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (network, chain_id, token_address)
        )
        """
    )
    connection.commit()
    connection.close()


def _insert_row(
    path: Path,
    address: str,
    *,
    symbol: str,
    metadata: dict,
    source: dict,
    name: str | None = None,
    decimals: int = 18,
    anchor_symbol: str | None = None,
) -> None:
    execute(
        str(path),
        """
        INSERT INTO chain_tokens (
            network, chain_id, token_address, token_symbol, token_name,
            token_decimals, anchor_symbol, token_metadata_json, source_json,
            updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            NETWORK,
            CHAIN_ID,
            address,
            symbol,
            name if name is not None else symbol,
            decimals,
            anchor_symbol,
            json.dumps(metadata, ensure_ascii=False, separators=(",", ":")),
            json.dumps(source, ensure_ascii=False, separators=(",", ":")),
            OLD_UPDATED_AT,
        ),
    )


def _create_scope_tables(path: Path) -> None:
    connection = sqlite3.connect(str(path))
    table_columns = {
        "dex_candidate_pools": ("token0", "token1"),
        "dex_candidate_pool_pairs": ("anchor_address", "counter_address"),
        "dex_candidate_anchor_tokens": ("token_address",),
        "market_observation_intermediate_pools": ("token0", "token1"),
        "market_observation_yz_pairs": ("token_x_address", "token_y_address"),
        "market_observation_abnormal_yz_pairs": (
            "token_x_address",
            "token_y_address",
        ),
        "v4_closed_loops": ("anchor_address",),
        "v4_closed_loop_hops": ("input_address", "output_address"),
    }
    for table, columns in table_columns.items():
        definition = ", ".join(
            f"{column} TEXT" for column in ("network", "chain_id", *columns)
        )
        connection.execute(f"CREATE TABLE {table} ({definition})")
    connection.execute(
        """
        CREATE TABLE v4_exchange_pools (
            network TEXT NOT NULL,
            chain_id INTEGER NOT NULL,
            token0 TEXT NOT NULL,
            token1 TEXT NOT NULL,
            fee INTEGER,
            hooks TEXT
        )
        """
    )
    connection.commit()
    connection.close()


def _insert_exchange_pool(
    path: Path,
    *,
    token0: str,
    token1: str,
    fee: int,
    hooks: str,
) -> None:
    execute(
        str(path),
        """
        INSERT INTO v4_exchange_pools (
            network, chain_id, token0, token1, fee, hooks
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (NETWORK, CHAIN_ID, token0, token1, fee, hooks),
    )


def _row(path: Path, address: str) -> dict:
    rows = query(
        str(path),
        """
        SELECT token_symbol, token_name, token_decimals,
               token_metadata_json, source_json, updated_at
        FROM chain_tokens
        WHERE network = ? AND chain_id = ? AND token_address = ?
        """,
        (NETWORK, CHAIN_ID, address),
    )
    return rows[0]


def test_default_repair_addresses_match_residual_rows():
    assert DEFAULT_REPAIR_ADDRESSES == [ADDR1, ADDR2]


def test_funding_library_tokens_include_core_stock_test_but_not_orphan(tmp_path):
    path = tmp_path / "funding-tokens.sqlite3"
    _create_chain_tokens(path)
    _insert_row(
        path,
        ADDR1,
        symbol="CLSK",
        metadata={"category": "yc_zc"},
        source={"seed": "core_anchor"},
        anchor_symbol="CLSK",
    )
    _insert_row(
        path,
        ADDR2,
        symbol="AAPL",
        metadata={"category": "robinhood_stock"},
        source={"seed": "stock_registry"},
        anchor_symbol="AAPL",
    )
    _insert_row(
        path,
        ADDR3,
        symbol="TZ",
        metadata={"category": "test_tokens"},
        source={"seed": "test_allowlist"},
        anchor_symbol="TZ",
    )
    orphan = "0x" + "5" * 40
    _insert_row(
        path,
        orphan,
        symbol="ORPHAN",
        metadata={},
        source={"seed": "v4_pool_index"},
    )

    funding = _funding_library_tokens(
        str(path),
        network=NETWORK,
        chain_id=CHAIN_ID,
    )

    assert {ADDR1, ADDR2, ADDR3} <= funding
    assert "0x0000000000000000000000000000000000000000" in funding
    assert orphan not in funding


def test_exchange_scope_uses_funding_endpoint_and_executable_pool_gates(tmp_path):
    path = tmp_path / "exchange-scope.sqlite3"
    _create_chain_tokens(path)
    _create_scope_tables(path)
    funding_token = ADDR1
    fixed_counterparty = "0x" + "6" * 40
    zero_hook_counterparty = "0x" + "7" * 40
    high_fee_counterparty = "0x" + "8" * 40
    dynamic_fee_counterparty = "0x" + "9" * 40
    hooked_counterparty = "0x" + "a" * 40
    orphan_left = "0x" + "b" * 40
    orphan_right = "0x" + "c" * 40
    _insert_row(
        path,
        funding_token,
        symbol="USDG",
        metadata={"category": "yc_zc"},
        source={"seed": "core_anchor"},
        anchor_symbol="USDG",
    )
    _insert_exchange_pool(
        path,
        token0=funding_token,
        token1=fixed_counterparty,
        fee=30000,
        hooks="",
    )
    _insert_exchange_pool(
        path,
        token0=zero_hook_counterparty,
        token1=funding_token,
        fee=500,
        hooks="0x0000000000000000000000000000000000000000",
    )
    _insert_exchange_pool(
        path,
        token0=funding_token,
        token1=high_fee_counterparty,
        fee=30001,
        hooks="",
    )
    _insert_exchange_pool(
        path,
        token0=funding_token,
        token1=dynamic_fee_counterparty,
        fee=8388608 + 30000,
        hooks="",
    )
    _insert_exchange_pool(
        path,
        token0=funding_token,
        token1=hooked_counterparty,
        fee=1000,
        hooks="0x1111111111111111111111111111111111111111",
    )
    _insert_exchange_pool(
        path,
        token0=orphan_left,
        token1=orphan_right,
        fee=1000,
        hooks="",
    )

    scope = _scoped_addresses(
        str(path),
        network=NETWORK,
        chain_id=CHAIN_ID,
        include_exchange=True,
    )

    assert funding_token in scope
    assert fixed_counterparty in scope
    assert zero_hook_counterparty in scope
    assert high_fee_counterparty not in scope
    assert dynamic_fee_counterparty not in scope
    assert hooked_counterparty not in scope
    assert orphan_left not in scope
    assert orphan_right not in scope


def test_repair_columns_from_metadata_only_upgrades_invalid_fields(tmp_path):
    path = tmp_path / "repair-columns.sqlite3"
    _create_chain_tokens(path)
    _insert_row(
        path,
        ADDR1,
        symbol="0X12345678",
        name=None,
        decimals=None,
        metadata={
            "symbol": "AMZN",
            "name": "Amazon",
            "decimals": 18,
            "source": "chain_live_erc20",
        },
        source={"source": "chain_live_erc20"},
    )
    _insert_row(
        path,
        ADDR2,
        symbol="TSLA",
        name="Tesla",
        decimals=18,
        metadata={"symbol": "0XTESLA", "name": None, "decimals": None},
        source={"source": "chain_live_erc20"},
    )

    dry_changed, dry_unchanged = _repair_columns_from_metadata(
        str(path),
        network=NETWORK,
        chain_id=CHAIN_ID,
        dry_run=True,
    )
    assert dry_changed == [ADDR1]
    assert dry_unchanged == 1
    assert _row(path, ADDR1)["token_symbol"] == "0X12345678"

    changed, unchanged = _repair_columns_from_metadata(
        str(path),
        network=NETWORK,
        chain_id=CHAIN_ID,
        dry_run=False,
    )
    assert changed == [ADDR1]
    assert unchanged == 1
    repaired = _row(path, ADDR1)
    assert repaired["token_symbol"] == "AMZN"
    assert repaired["token_name"] == "Amazon"
    assert repaired["token_decimals"] == 18
    preserved = _row(path, ADDR2)
    assert preserved["token_symbol"] == "TSLA"
    assert preserved["token_name"] == "Tesla"
    assert preserved["token_decimals"] == 18


def test_mark_remaining_placeholders_writes_only_missing_markers(tmp_path):
    path = tmp_path / "store.sqlite3"
    _create_chain_tokens(path)
    _insert_row(
        path,
        ADDR1,
        symbol="0X12345678",
        metadata={},
        source={"source": "chain_live_erc20", "nameMissing": False},
    )
    _insert_row(
        path,
        ADDR2,
        symbol="0X87654321",
        metadata={"nameMissing": True, "nameFallbackSymbol": "0X87654321"},
        source={"source": "chain_live_erc20_unresolved", "nameMissing": True},
    )
    _insert_row(
        path,
        ADDR3,
        symbol="TZ",
        metadata={"name": "TZ Token", "nameMissing": False},
        source={"source": "chain_live_erc20", "nameMissing": False},
    )

    existing = {
        ADDR1: {
            "token_symbol": "0X12345678",
            "token_decimals": 18,
            "token_metadata_json": "{}",
        },
        ADDR2: {
            "token_symbol": "0X87654321",
            "token_decimals": 18,
            "token_metadata_json": json.dumps(
                {"nameMissing": True, "nameFallbackSymbol": "0X87654321"}
            ),
        },
        ADDR3: {
            "token_symbol": "TZ",
            "token_decimals": 18,
            "token_metadata_json": json.dumps(
                {"name": "TZ Token", "nameMissing": False}
            ),
        },
    }
    changed = _mark_remaining_placeholders(
        str(path),
        existing,
        [ADDR1, ADDR2],
        network=NETWORK,
        chain_id=CHAIN_ID,
    )

    assert changed == [ADDR1]
    row1 = _row(path, ADDR1)
    metadata1 = json.loads(row1["token_metadata_json"])
    source1 = json.loads(row1["source_json"])
    assert metadata1["nameMissing"] is True
    assert metadata1["nameFallbackSymbol"] == "0X12345678"
    assert source1["nameMissing"] is True
    assert source1["nameFallbackSymbol"] == "0X12345678"
    assert row1["token_symbol"] == "0X12345678"
    assert row1["updated_at"] >= OLD_UPDATED_AT

    row2 = _row(path, ADDR2)
    source2 = json.loads(row2["source_json"])
    assert source2["nameMissing"] is True
    assert row2["updated_at"] == OLD_UPDATED_AT

    row3 = _row(path, ADDR3)
    source3 = json.loads(row3["source_json"])
    assert source3["nameMissing"] is False
    assert row3["updated_at"] == OLD_UPDATED_AT


def test_repair_source_json_dry_run_does_not_write(tmp_path):
    path = tmp_path / "store.sqlite3"
    _create_chain_tokens(path)
    _insert_row(
        path,
        ADDR1,
        symbol="0X12345678",
        metadata={"nameMissing": True, "nameFallbackSymbol": "0X12345678"},
        source={"source": "chain_live_erc20_unresolved", "nameMissing": False},
    )
    changed, missing, inconsistent = _repair_source_json(
        str(path),
        [ADDR1],
        network=NETWORK,
        chain_id=CHAIN_ID,
        dry_run=True,
    )

    assert changed == [ADDR1]
    assert missing == []
    assert inconsistent == []
    row = _row(path, ADDR1)
    assert json.loads(row["source_json"])["nameMissing"] is False
    assert row["updated_at"] == OLD_UPDATED_AT


def test_repair_source_json_writes_and_reports_consistency(tmp_path):
    path = tmp_path / "store.sqlite3"
    _create_chain_tokens(path)
    _insert_row(
        path,
        ADDR1,
        symbol="0X12345678",
        metadata={"nameMissing": True, "nameFallbackSymbol": "0X12345678"},
        source={"source": "chain_live_erc20_unresolved", "nameMissing": False},
    )
    _insert_row(
        path,
        ADDR2,
        symbol="0X87654321",
        metadata={"nameMissing": True, "nameFallbackSymbol": "0X87654321"},
        source={
            "source": "chain_live_erc20_unresolved",
            "nameMissing": True,
            "nameFallbackSymbol": "0X87654321",
            "resolvedAt": "2026-01-01T00:00:00Z",
        },
    )

    changed, missing, inconsistent = _repair_source_json(
        str(path),
        [ADDR1, ADDR2],
        network=NETWORK,
        chain_id=CHAIN_ID,
        dry_run=False,
    )

    assert changed == [ADDR1]
    assert missing == []
    assert inconsistent == []
    row1 = _row(path, ADDR1)
    source1 = json.loads(row1["source_json"])
    assert source1["nameMissing"] is True
    assert source1["nameFallbackSymbol"] == "0X12345678"
    assert source1["resolvedAt"]
    assert row1["token_symbol"] == "0X12345678"
    assert row1["token_name"] == "0X12345678"
    assert row1["token_decimals"] == 18
    assert row1["updated_at"] > OLD_UPDATED_AT
    row2 = _row(path, ADDR2)
    assert row2["updated_at"] == OLD_UPDATED_AT


def test_repair_source_json_reports_missing_address(tmp_path):
    path = tmp_path / "store.sqlite3"
    _create_chain_tokens(path)
    changed, missing, inconsistent = _repair_source_json(
        str(path),
        ["0x" + "f" * 40],
        network=NETWORK,
        chain_id=CHAIN_ID,
        dry_run=False,
    )
    assert changed == []
    assert missing == ["0x" + "f" * 40]
    assert inconsistent == []


def test_select_target_addresses_includes_unresolved_symbols_and_missing_names():
    existing = {
        ADDR1: {
            "token_symbol": "AMZN",
            "token_name": "Amazon • Robinhood Token",
            "token_decimals": 18,
        },
        ADDR2: {
            "token_symbol": "0X9ABCDEF0",
            "token_name": "",
            "token_decimals": 18,
        },
        ADDR3: {
            "token_symbol": "TINMAN",
            "token_name": "",
            "token_decimals": 18,
        },
        "0x" + "4" * 40: {
            "token_symbol": "BOKEM",
            "token_name": "0X12345678",
            "token_decimals": 18,
        },
    }
    targets = _select_target_addresses(
        [ADDR1, ADDR2, ADDR3, "0x" + "4" * 40],
        existing,
    )
    assert ADDR1 not in targets
    assert ADDR2 in targets
    assert ADDR3 in targets
    assert "0x" + "4" * 40 in targets


def test_select_target_addresses_includes_missing_or_invalid_decimals():
    existing = {
        ADDR1: {
            "token_symbol": "AMZN",
            "token_name": "Amazon",
            "token_decimals": None,
        },
        ADDR2: {
            "token_symbol": "TSLA",
            "token_name": "Tesla",
            "token_decimals": "",
        },
        ADDR3: {
            "token_symbol": "NVDA",
            "token_name": "NVIDIA",
            "token_decimals": 18,
        },
    }

    targets = _select_target_addresses([ADDR1, ADDR2, ADDR3], existing)

    assert targets == [ADDR1, ADDR2]


def test_write_rows_persists_name_missing_for_resolved_symbol_without_name(tmp_path):
    path = tmp_path / "store.sqlite3"
    _create_chain_tokens(path)
    _insert_row(
        path,
        ADDR1,
        symbol="0X12345678",
        metadata={},
        source={},
        name="",
    )
    rows = [
        {
            "address": ADDR1,
            "symbol": "AMZN",
            "name": "",
            "nameMissing": True,
            "decimals": 18,
            "source": "chain_live_erc20",
            "rpcUrl": "https://rpc.example",
        }
    ]
    updated = _write_rows(
        str(path),
        rows,
        network=NETWORK,
        chain_id=CHAIN_ID,
    )
    assert updated == 1
    row = _row(path, ADDR1)
    metadata = json.loads(row["token_metadata_json"])
    source = json.loads(row["source_json"])
    assert row["token_symbol"] == "AMZN"
    assert row["token_name"] == "AMZN"
    assert metadata["nameMissing"] is True
    assert metadata["nameFallbackSymbol"] == "AMZN"
    assert source["nameMissing"] is True
    assert source["nameFallbackSymbol"] == "AMZN"
