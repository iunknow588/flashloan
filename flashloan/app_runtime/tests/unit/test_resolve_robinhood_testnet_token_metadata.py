"""Unit tests for the Robinhood Testnet token metadata backfill tool."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from core.db.storage_business_io import execute, query
from dex.tools.resolve_robinhood_testnet_token_metadata import (
    DEFAULT_REPAIR_ADDRESSES,
    _mark_remaining_placeholders,
    _repair_source_json,
    _select_target_addresses,
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
) -> None:
    execute(
        str(path),
        """
        INSERT INTO chain_tokens (
            network, chain_id, token_address, token_symbol, token_name,
            token_decimals, token_metadata_json, source_json, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            NETWORK,
            CHAIN_ID,
            address,
            symbol,
            name if name is not None else symbol,
            decimals,
            json.dumps(metadata, ensure_ascii=False, separators=(",", ":")),
            json.dumps(source, ensure_ascii=False, separators=(",", ":")),
            OLD_UPDATED_AT,
        ),
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
        },
        ADDR2: {
            "token_symbol": "0X9ABCDEF0",
            "token_name": "",
        },
        ADDR3: {
            "token_symbol": "TINMAN",
            "token_name": "",
        },
        "0x" + "4" * 40: {
            "token_symbol": "BOKEM",
            "token_name": "0X12345678",
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
