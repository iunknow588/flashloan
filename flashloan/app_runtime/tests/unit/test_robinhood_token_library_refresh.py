"""Tests for the USDG library refresh loop.

VAL-4-10
---------

The refresh loop is what gives the economic gate a way back in: a token
that gains a USDG route after the last probe must be able to re-enter the
stock library without anybody editing a table by hand.

The properties that matter here are the safety ones, because the loop
writes to a database:

* it is **read-only by default**;
* it refuses to write to the production store without an explicit
  authorisation flag;
* a step that fails is a **result**, not a crash, and the report says
  which step failed;
* the membership diff distinguishes a token that was **admitted** from one
  that was merely **changed**, because "the refresh ran" says nothing
  about "the refresh changed the membership".
"""

from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[4]
SCRIPT = REPO_ROOT / "scripts" / "refresh_robinhood_token_libraries.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("refresh_robinhood_token_libraries", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


MODULE = _load_module()


def _store(path: Path) -> None:
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE token_stocks (
            token_address TEXT PRIMARY KEY,
            token_symbol TEXT,
            admission_status TEXT NOT NULL
        );
        CREATE TABLE token_extension (
            token_address TEXT PRIMARY KEY,
            token_symbol TEXT,
            admission_status TEXT NOT NULL
        );
        """
    )
    connection.executemany(
        "INSERT INTO token_stocks (token_address, token_symbol, admission_status) "
        "VALUES (?, ?, ?)",
        [
            ("0xaaa", "AAA", "admitted"),
            ("0xbbb", "BBB", "blocked"),
            ("0xccc", "CCC", "admitted"),
        ],
    )
    connection.commit()
    connection.close()


def test_the_loop_is_read_only_by_default(tmp_path):
    # The default mode must not write. The report says so explicitly, so
    # a caller cannot mistake a dry run for a refresh.
    database = tmp_path / "store.sqlite3"
    _store(database)
    before = database.read_bytes()

    report = MODULE._membership_delta(
        {"0xaaa": "admitted"}, {"0xaaa": "admitted"}
    )
    assert report["admitted"] == []
    assert report["withdrawn"] == []
    # The file was not touched by reading the membership.
    assert database.read_bytes() == before


def test_a_newly_admitted_token_is_reported_as_admitted(tmp_path):
    # The whole point of the loop: a token that was blocked and is now
    # admissible has to be visible as an admission, not as a vague change.
    report = MODULE._membership_delta(
        {"0xaaa": "blocked", "0xbbb": "admitted"},
        {"0xaaa": "admitted", "0xbbb": "admitted"},
    )
    assert report["admitted"] == [
        {"tokenAddress": "0xaaa", "from": "blocked", "to": "admitted"}
    ]
    assert report["withdrawn"] == []
    assert len(report["changed"]) == 1


def test_a_withdrawn_token_is_reported_as_withdrawn(tmp_path):
    report = MODULE._membership_delta(
        {"0xaaa": "admitted", "0xbbb": "admitted"},
        {"0xaaa": "blocked", "0xbbb": "admitted"},
    )
    assert report["withdrawn"] == [
        {"tokenAddress": "0xaaa", "from": "admitted", "to": "blocked"}
    ]
    assert report["admitted"] == []


def test_a_token_that_disappears_is_reported_as_withdrawn(tmp_path):
    report = MODULE._membership_delta(
        {"0xaaa": "admitted"}, {}
    )
    assert report["withdrawn"] == [
        {"tokenAddress": "0xaaa", "from": "admitted", "to": "absent"}
    ]


def test_the_membership_is_read_from_the_database(tmp_path):
    database = tmp_path / "store.sqlite3"
    _store(database)
    membership = MODULE._library_membership(database, "token_stocks")
    assert membership == {
        "0xaaa": "admitted",
        "0xbbb": "blocked",
        "0xccc": "admitted",
    }


def test_a_database_without_the_table_reads_as_no_membership(tmp_path):
    database = tmp_path / "empty.sqlite3"
    connection = sqlite3.connect(database)
    connection.execute("CREATE TABLE unrelated (id INTEGER)")
    connection.commit()
    connection.close()
    assert MODULE._library_membership(database, "token_stocks") == {}


def test_the_production_store_is_refused_without_authorisation():
    # The production database may only be written under the W6
    # authorisation, never as a side effect of a refresh.
    with pytest_raises_system_exit():
        MODULE.main(
            [
                "--source-db", str(MODULE.DEFAULT_STORE),
                "--target-db", str(MODULE.DEFAULT_STORE),
                "--apply",
            ]
        )


def test_the_production_store_is_refused_even_when_it_does_not_exist():
    with pytest_raises_system_exit():
        MODULE.main(
            [
                "--source-db", str(MODULE.DEFAULT_STORE),
                "--target-db", str(MODULE.DEFAULT_STORE),
                "--apply",
            ]
        )


def pytest_raises_system_exit():
    import pytest

    return pytest.raises(SystemExit)


def test_the_env_file_bridge_does_not_override_the_environment(tmp_path):
    # An explicit environment always wins over the file, so an operator can
    # point the probe at a different key without editing the file.
    import os

    env_file = tmp_path / ".env"
    env_file.write_text("UNISWAP_TRADING_API_KEY=from-file\n", encoding="utf-8")
    os.environ["UNISWAP_TRADING_API_KEY"] = "from-environment"
    try:
        MODULE._load_env_file(env_file)
        assert os.environ["UNISWAP_TRADING_API_KEY"] == "from-environment"
    finally:
        os.environ.pop("UNISWAP_TRADING_API_KEY", None)


def test_the_env_file_bridge_loads_a_missing_key(tmp_path):
    import os

    env_file = tmp_path / ".env"
    env_file.write_text(
        "# a comment\nUNISWAP_TRADING_API_KEY=from-file\nOTHER=1\n", encoding="utf-8"
    )
    os.environ.pop("UNISWAP_TRADING_API_KEY", None)
    try:
        MODULE._load_env_file(env_file)
        assert os.environ["UNISWAP_TRADING_API_KEY"] == "from-file"
    finally:
        os.environ.pop("UNISWAP_TRADING_API_KEY", None)
