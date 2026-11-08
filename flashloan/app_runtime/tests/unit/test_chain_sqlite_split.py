from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[4]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from split_chain_sqlite import _CHAIN_UNIVERSE, split_database  # noqa: E402


def _make_source(path: Path) -> None:
    from core.db.storage_schema import ensure_database_schema

    ensure_database_schema(str(path))
    connection = sqlite3.connect(path)
    try:
        connection.execute(
            """
            CREATE TABLE chain_rows (
                network TEXT NOT NULL,
                chain_id INTEGER NOT NULL,
                label TEXT NOT NULL
            )
            """
        )
        connection.executemany(
            "INSERT INTO chain_rows (network, chain_id, label) VALUES (?, ?, ?)",
            [
                ("robinhood", 4663, "mainnet"),
                ("robinhood-testnet", 46630, "testnet"),
            ],
        )
        connection.execute(
            "INSERT INTO kv_entries (collection, key, value_json, updated_at) VALUES (?, ?, ?, ?)",
            ("parameters", "global", '"ok"', "2026-10-27T00:00:00Z"),
        )
        connection.execute(
            """
            INSERT INTO bnb_market_observations
            (id, source, network, chain_id, run_id, round_index, recorded_at, payload_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (1, "bnb-index", "bnb", 56, "r1", 0, "2026-10-27T00:00:00Z", "{}"),
        )
        connection.execute(
            """
            INSERT INTO bnb_market_observations
            (id, source, network, chain_id, run_id, round_index, recorded_at, payload_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (2, "bnb-index", "bnb", 56, "r2", 1, "2026-10-27T00:01:00Z", "{}"),
        )
        connection.execute(
            """
            INSERT INTO bnb_market_snapshots
            (id, source, symbol, price_usd, observed_at, payload_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (1, "bnb-index", "BNB", "600.0", "2026-10-27T00:00:00Z", "{}"),
        )
        connection.commit()
    finally:
        connection.close()


def test_split_database_copies_only_target_chain_rows(tmp_path, monkeypatch):
    _CHAIN_UNIVERSE.add("chain_rows")
    source = tmp_path / "local_store.sqlite3"
    output = tmp_path / "split"
    _make_source(source)
    chains = [
        {
            "chain_id": 46630,
            "network": "robinhood-testnet",
            "sqlite_file": "local_store_46630.sqlite3",
        }
    ]
    report = split_database(source, output, chains=chains)
    assert report["chains"][0]["created"] is True
    assert set(report["chains"][0]["bnb_shared_tables"]) == {
        "bnb_market_observations",
        "bnb_market_snapshots",
    }
    target = output / "local_store_46630.sqlite3"
    connection = sqlite3.connect(target)
    try:
        rows = connection.execute("SELECT network, chain_id, label FROM chain_rows").fetchall()
        assert rows == [("robinhood-testnet", 46630, "testnet")]
        assert connection.execute(
            "SELECT COUNT(*) FROM kv_entries WHERE key='global'"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM kv_meta WHERE meta_key='chain_database_split'"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM bnb_market_observations"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM bnb_market_snapshots"
        ).fetchone()[0] == 0
    finally:
        connection.close()
        _CHAIN_UNIVERSE.discard("chain_rows")


def test_split_database_marks_unknown_chain_scoped_table(tmp_path):
    source = tmp_path / "local_store.sqlite3"
    output = tmp_path / "split"
    _make_source(source)
    connection = sqlite3.connect(source)
    try:
        connection.execute(
            """
            CREATE TABLE unknown_chain_rows (
                network TEXT NOT NULL,
                chain_id INTEGER NOT NULL,
                label TEXT NOT NULL
            )
            """
        )
        connection.execute(
            "INSERT INTO unknown_chain_rows VALUES (?, ?, ?)",
            ("robinhood-testnet", 46630, "testnet"),
        )
        connection.commit()
    finally:
        connection.close()
    chains = [
        {
            "chain_id": 46630,
            "network": "robinhood-testnet",
            "sqlite_file": "local_store_46630.sqlite3",
        }
    ]
    dry = split_database(source, output, chains=chains, dry_run=True)
    dry_chain = dry["chains"][0]
    assert "unknown_chain_rows" in dry_chain["unsupported_chain_scoped_tables"]
    assert dry_chain["tables"]["unknown_chain_rows"] == 0

    report = split_database(source, output, chains=chains)
    chain = report["chains"][0]
    assert "unknown_chain_rows" in chain["unsupported_chain_scoped_tables"]
    assert chain["tables"]["unknown_chain_rows"] == 0
    target = output / "local_store_46630.sqlite3"
    target_connection = sqlite3.connect(target)
    try:
        assert target_connection.execute(
            "SELECT COUNT(*) FROM unknown_chain_rows"
        ).fetchone()[0] == 0
    finally:
        target_connection.close()


def test_split_database_preserves_every_source_business_table(tmp_path):
    source = tmp_path / "local_store.sqlite3"
    output = tmp_path / "split"
    _make_source(source)
    chains = [
        {
            "chain_id": 46630,
            "network": "robinhood-testnet",
            "sqlite_file": "local_store_46630.sqlite3",
        }
    ]
    split_database(source, output, chains=chains)
    source_connection = sqlite3.connect(source)
    target_connection = sqlite3.connect(output / "local_store_46630.sqlite3")
    try:
        source_tables = {
            str(row[0])
            for row in source_connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
            if not str(row[0]).lower().startswith("sqlite_")
        }
        target_tables = {
            str(row[0])
            for row in target_connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert source_tables - target_tables == set()
        assert "dex_surface_snapshots" in target_tables
    finally:
        source_connection.close()
        target_connection.close()
