"""Tests for the ARCH-2b replay harness.

The harness replays the routes the chain actually reviewed and compares the
off-chain simulation with what the chain reported.

The harness is a **thin CLI over the Rust managers**. It reads no table,
implements no simulation and performs no comparison: it asks the in-memory
``RuntimeCore`` to run the ``review_parity_replay`` command and writes the
report it gets back. The parsing, the simulation, the attribution and the
gate all live in Review Manager, and are locked down by the Rust test
suite.

The tests here lock down three things:

* the **architecture**: the harness never opens a database, never names a
  manager-owned table and never computes a Uniswap amount. The functional
  work lives in the Rust managers and Python only carries the request;
* the **request shape**: the harness passes the database, the network, the
  protocol, the limit and the tolerance to the manager that does the work,
  and it marks the command as a read-only dry run;
* the **report**: the harness writes the report the manager produced,
  unchanged, and it refuses to run without a database.
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
import sqlite3
import sys

import pytest


def _load_harness():
    # ``unit`` -> ``tests`` -> ``app_runtime`` -> ``flashloan`` -> repo root
    root = pathlib.Path(__file__).resolve().parents[4]
    script = root / "scripts" / "arch2b_offchain_parity_replay.py"
    spec = importlib.util.spec_from_file_location("arch2b_replay_harness", script)
    module = importlib.util.module_from_spec(spec)
    # The module has to be registered before it is executed: ``dataclass``
    # looks the module up in ``sys.modules`` while it is being built.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


HARNESS = _load_harness()


SCRIPT_PATH = (
    pathlib.Path(__file__).resolve().parents[4]
    / "scripts"
    / "arch2b_offchain_parity_replay.py"
)


# --------------------------------------------------------- the architecture


def test_the_harness_never_opens_a_database():
    """The managers own the database. The harness only names it."""
    source = SCRIPT_PATH.read_text(encoding="utf-8")
    # The database *driver* must not appear: the harness never opens a
    # connection, it only hands the path to the Rust managers.
    assert "import sqlite3" not in source
    assert "sqlite3.connect" not in source
    assert ".connect(" not in source


def test_the_harness_never_names_a_manager_owned_table():
    source = SCRIPT_PATH.read_text(encoding="utf-8")
    for table in (
        "v4_exchange_pools",
        "v4_exchange_pool_liquidity_snapshots",
        "cow_execution_reviews",
        "v4_closed_loop_hops",
    ):
        assert table not in source, f"the harness must not reference {table}"


def test_the_harness_does_not_compute_the_uniswap_arithmetic():
    source = SCRIPT_PATH.read_text(encoding="utf-8")
    for forbidden in (
        "sqrt_price",
        "get_next_sqrt_price",
        "amount_0_delta",
        "amount_1_delta",
        "liquidity_q96",
        "Q96",
        "Q192",
    ):
        assert forbidden not in source, f"the harness must not compute {forbidden}"


def test_the_harness_reaches_the_managers_only_through_the_runtime_core():
    source = SCRIPT_PATH.read_text(encoding="utf-8")
    assert "chain_runtime_registry" in source
    assert "execute_command" in source
    assert "review_parity_replay" in source


# ------------------------------------------------------------- the request


class _StubRuntime:
    """A stand-in for the in-memory RuntimeCore."""

    def __init__(self, payload):
        self.payload = payload
        self.calls: list[dict] = []

    def execute_command(self, command, *, request_id, idempotency_key, dry_run, options=None):
        self.calls.append(
            {
                "command": command,
                "requestId": request_id,
                "idempotencyKey": idempotency_key,
                "dryRun": dry_run,
                "options": options or {},
            }
        )
        return self.payload


def test_the_harness_registers_the_requested_read_only_scope_when_needed(monkeypatch):
    from core.runtime import chain_runtime

    runtime = _StubRuntime({"ok": True, "data": {}})

    class _Registry:
        def __init__(self):
            self.registration = None

        def get(self, chain_id):
            raise KeyError(chain_id)

        def register_chain(self, chain_id, **kwargs):
            self.registration = (chain_id, kwargs)
            return runtime

    registry = _Registry()
    monkeypatch.setattr(chain_runtime, "chain_runtime_registry", lambda: registry)
    resolved = HARNESS._runtime_core(
        4663,
        database_path="C:/tmp/replica.sqlite3",
        network="robinhood",
        protocol="uniswap_v4",
    )

    assert resolved is runtime
    assert registry.registration == (
        4663,
        {
            "network": "robinhood",
            "protocol": "uniswap_v4",
            "database_url": "C:/tmp/replica.sqlite3",
            "make_active": False,
        },
    )


def test_the_harness_asks_the_manager_to_do_the_replay(monkeypatch):
    runtime = _StubRuntime(
        {
            "ok": True,
            "data": {
                "routeCount": 54,
                "comparableCount": 54,
                "matchCount": 34,
                "deviationCount": 20,
                "gatePassed": False,
                "outcomes": [],
            },
        }
    )
    monkeypatch.setattr(HARNESS, "_runtime_core", lambda chain_id, **kwargs: runtime)
    report = HARNESS._execute_replay(
        chain_id=4663,
        database_path="C:/tmp/replica.sqlite3",
        network="robinhood",
        protocol="uniswap_v4",
        limit=None,
        tolerance=1e-9,
    )
    assert report["routeCount"] == 54
    assert report["deviationCount"] == 20
    assert report["gatePassed"] is False

    assert len(runtime.calls) == 1
    call = runtime.calls[0]
    assert call["command"] == "review_parity_replay"
    # The replay only reads, so it is always a dry run.
    assert call["dryRun"] is True
    options = call["options"]
    assert options["network"] == "robinhood"
    assert options["protocol"] == "uniswap_v4"
    assert options["databasePath"].endswith("replica.sqlite3")
    assert options["limit"] is None
    assert options["tolerance"] == 1e-9
    # The selection is part of the measurement, so it travels with the
    # command: a reader has to know which routes the report covers.
    assert options["selection"] == "newestPerEvaluableRoute"


def test_the_harness_carries_the_selection(monkeypatch):
    runtime = _StubRuntime({"ok": True, "data": {"routeCount": 1}})
    monkeypatch.setattr(HARNESS, "_runtime_core", lambda chain_id, **kwargs: runtime)
    HARNESS._execute_replay(
        chain_id=4663,
        database_path="C:/tmp/replica.sqlite3",
        network="robinhood",
        protocol="uniswap_v4",
        limit=None,
        tolerance=1e-9,
        selection="allAttempts",
    )
    assert runtime.calls[0]["options"]["selection"] == "allAttempts"


def test_the_harness_carries_the_limit_and_the_tolerance(monkeypatch):
    runtime = _StubRuntime({"ok": True, "data": {"routeCount": 1}})
    monkeypatch.setattr(HARNESS, "_runtime_core", lambda chain_id, **kwargs: runtime)
    HARNESS._execute_replay(
        chain_id=4663,
        database_path="C:/tmp/replica.sqlite3",
        network="robinhood",
        protocol="uniswap_v4",
        limit=10,
        tolerance=1e-6,
    )
    options = runtime.calls[0]["options"]
    assert options["limit"] == 10
    assert options["tolerance"] == 1e-6


def test_a_rejected_command_is_reported(monkeypatch):
    runtime = _StubRuntime({"ok": False, "detail": "the runtime refused"})
    monkeypatch.setattr(HARNESS, "_runtime_core", lambda chain_id, **kwargs: runtime)
    with pytest.raises(RuntimeError):
        HARNESS._execute_replay(
            chain_id=4663,
            database_path="C:/tmp/replica.sqlite3",
            network="robinhood",
            protocol="uniswap_v4",
            limit=None,
            tolerance=1e-9,
        )


def test_a_missing_runtime_is_reported(monkeypatch):
    monkeypatch.setattr(HARNESS, "_runtime_core", lambda chain_id, **kwargs: None)
    with pytest.raises(RuntimeError):
        HARNESS._execute_replay(
            chain_id=4663,
            database_path="C:/tmp/replica.sqlite3",
            network="robinhood",
            protocol="uniswap_v4",
            limit=None,
            tolerance=1e-9,
        )


# --------------------------------------------------------------- the report


def test_the_harness_writes_the_manager_report_unchanged(tmp_path, monkeypatch):
    payload = {
        "routeCount": 54,
        "comparableCount": 54,
        "matchCount": 34,
        "executableMismatchCount": 20,
        "amountMismatchCount": 0,
        "notComparableCount": 0,
        "deviationCount": 20,
        "gatePassed": False,
        "attributionCounts": {"stale_local_state": 20},
        "outcomes": [],
    }
    runtime = _StubRuntime({"ok": True, "data": payload})
    monkeypatch.setattr(HARNESS, "_runtime_core", lambda chain_id, **kwargs: runtime)
    database = tmp_path / "replica.sqlite3"
    database.write_bytes(b"")
    output = tmp_path / "report.json"
    exit_code = HARNESS.main(
        [
            "--database",
            str(database),
            "--output",
            str(output),
        ]
    )
    assert exit_code == 0
    written = json.loads(output.read_text(encoding="utf-8"))
    assert written == payload


def test_the_harness_refuses_to_run_without_a_database(monkeypatch, capsys):
    monkeypatch.setenv("DEX_LOCAL_DB_PATH", "")
    with pytest.raises(SystemExit):
        HARNESS.main([])


def test_the_harness_writes_utf8_without_a_bom(tmp_path, monkeypatch):
    runtime = _StubRuntime({"ok": True, "data": {"routeCount": 0}})
    monkeypatch.setattr(HARNESS, "_runtime_core", lambda chain_id, **kwargs: runtime)
    database = tmp_path / "replica.sqlite3"
    database.write_bytes(b"")
    output = tmp_path / "report.json"
    HARNESS.main(["--database", str(database), "--output", str(output)])
    raw = output.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf")
    assert b"\r\n" not in raw
