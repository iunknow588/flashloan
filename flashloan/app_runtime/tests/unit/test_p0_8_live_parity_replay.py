"""Tests for the P0-8 live parity harness.

The harness replays **one live review payload** against the chain, in
memory. The review rows are transient garbage data that the manager cleans
on purpose, so the parity measurement has to happen while the review is
being produced, from the payload the review already carries.

The harness is a **thin CLI over the Rust managers**. It reads no table,
implements no simulation and performs no comparison: it asks the in-memory
``RuntimeCore`` to run the ``review_parity_replay_payload`` command and
writes the report it gets back. The route extraction, the review-time state
extraction, the simulation, the attribution and the verdict all live in
Review Manager.

The tests here lock down three things:

* the **architecture**: the harness never opens a database, never names a
  manager-owned table, never computes a Uniswap amount and never persists
  the payload;
* the **request shape**: the harness hands the payload to the manager as a
  read-only dry run and carries the fee and the tolerance with it;
* the **report**: the harness writes the manager report unchanged, refuses to
  run without a database and refuses a malformed payload.
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
import sys

import pytest


def _load_harness():
    # ``unit`` -> ``tests`` -> ``app_runtime`` -> ``flashloan`` -> repo root
    root = pathlib.Path(__file__).resolve().parents[4]
    script = root / "scripts" / "p0_8_live_parity_replay.py"
    spec = importlib.util.spec_from_file_location("p0_8_live_parity_harness", script)
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
    / "p0_8_live_parity_replay.py"
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
    assert "review_parity_replay_payload" in source


def test_the_harness_never_persists_the_payload():
    """The payload is transient: it must not be written anywhere."""
    source = SCRIPT_PATH.read_text(encoding="utf-8")
    # The payload only travels in the request; the harness writes the
    # *report*, never the payload.
    assert "INSERT INTO" not in source
    assert "UPDATE " not in source
    assert "DELETE FROM" not in source
    assert "cow_execution_reviews" not in source


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
    assert registry.registration is not None
    chain_id, kwargs = registry.registration
    assert chain_id == 4663
    assert kwargs["database_url"] == "C:/tmp/replica.sqlite3"
    assert kwargs["network"] == "robinhood"
    assert kwargs["protocol"] == "uniswap_v4"
    assert kwargs["make_active"] is False


def test_the_harness_carries_the_payload_and_the_read_only_scope(monkeypatch):
    payload = {"routeAddresses": ["0xaa"], "selectedAmountRaw": "1000"}
    runtime = _StubRuntime(
        {
            "ok": True,
            "data": {
                "state": "match",
                "reviewTimeStateUsed": True,
                "reviewTimePoolCount": 2,
                "persisted": False,
            },
        }
    )
    monkeypatch.setattr(HARNESS, "_runtime_core", lambda chain_id, **kwargs: runtime)
    report = HARNESS._execute_payload_replay(
        chain_id=4663,
        database_path="C:/tmp/replica.sqlite3",
        network="robinhood",
        protocol="uniswap_v4",
        payload=payload,
    )
    assert report["state"] == "match"
    assert report["reviewTimeStateUsed"] is True
    assert report["persisted"] is False

    assert len(runtime.calls) == 1
    call = runtime.calls[0]
    assert call["command"] == "review_parity_replay_payload"
    # The measurement only reads, so it is always a dry run.
    assert call["dryRun"] is True
    options = call["options"]
    assert options["network"] == "robinhood"
    assert options["protocol"] == "uniswap_v4"
    assert options["payload"] == payload
    assert options["flashFeeBps"] == 0
    assert options["tolerance"] == 1e-9


def test_the_harness_carries_the_fee_and_the_tolerance(monkeypatch):
    runtime = _StubRuntime({"ok": True, "data": {"state": "match"}})
    monkeypatch.setattr(HARNESS, "_runtime_core", lambda chain_id, **kwargs: runtime)
    HARNESS._execute_payload_replay(
        chain_id=4663,
        database_path="C:/tmp/replica.sqlite3",
        network="robinhood",
        protocol="uniswap_v4",
        payload={"selectedAmountRaw": "1000"},
        flash_fee_bps=5,
        tolerance=1e-6,
    )
    options = runtime.calls[0]["options"]
    assert options["flashFeeBps"] == 5
    assert options["tolerance"] == 1e-6


def test_a_rejected_command_is_reported(monkeypatch):
    runtime = _StubRuntime({"ok": False, "detail": "the runtime refused"})
    monkeypatch.setattr(HARNESS, "_runtime_core", lambda chain_id, **kwargs: runtime)
    with pytest.raises(RuntimeError):
        HARNESS._execute_payload_replay(
            chain_id=4663,
            database_path="C:/tmp/replica.sqlite3",
            network="robinhood",
            protocol="uniswap_v4",
            payload={"selectedAmountRaw": "1000"},
        )


def test_a_missing_runtime_is_reported(monkeypatch):
    monkeypatch.setattr(HARNESS, "_runtime_core", lambda chain_id, **kwargs: None)
    with pytest.raises(RuntimeError):
        HARNESS._execute_payload_replay(
            chain_id=4663,
            database_path="C:/tmp/replica.sqlite3",
            network="robinhood",
            protocol="uniswap_v4",
            payload={"selectedAmountRaw": "1000"},
        )


# --------------------------------------------------------------- the report


def test_the_harness_writes_the_manager_report_unchanged(tmp_path, monkeypatch):
    report = {
        "state": "match",
        "profitOffchainRaw": 25,
        "profitOnchainRaw": 25,
        "profitRelativeDelta": 0,
        "reviewTimeStateUsed": True,
        "reviewTimePoolCount": 2,
        "persisted": False,
    }
    runtime = _StubRuntime({"ok": True, "data": report})
    monkeypatch.setattr(HARNESS, "_runtime_core", lambda chain_id, **kwargs: runtime)
    database = tmp_path / "replica.sqlite3"
    database.write_bytes(b"")
    payload_file = tmp_path / "review.json"
    payload_file.write_text(
        json.dumps({"routeAddresses": ["0xaa"], "selectedAmountRaw": "1000"}),
        encoding="utf-8",
    )
    output = tmp_path / "report.json"
    exit_code = HARNESS.main(
        [
            "--database",
            str(database),
            "--payload",
            str(payload_file),
            "--output",
            str(output),
        ]
    )
    assert exit_code == 0
    written = json.loads(output.read_text(encoding="utf-8"))
    assert written == report


def test_the_harness_refuses_to_run_without_a_database(monkeypatch):
    monkeypatch.setenv("DEX_LOCAL_DB_PATH", "")
    with pytest.raises(SystemExit):
        HARNESS.main([])


def test_the_harness_refuses_a_malformed_payload(tmp_path, monkeypatch):
    monkeypatch.setattr(
        HARNESS, "_runtime_core", lambda chain_id, **kwargs: _StubRuntime({"ok": True})
    )
    database = tmp_path / "replica.sqlite3"
    database.write_bytes(b"")
    payload_file = tmp_path / "review.json"
    payload_file.write_text("not json at all", encoding="utf-8")
    with pytest.raises(RuntimeError):
        HARNESS.main(["--database", str(database), "--payload", str(payload_file)])


def test_the_harness_refuses_a_payload_that_is_not_an_object(tmp_path, monkeypatch):
    monkeypatch.setattr(
        HARNESS, "_runtime_core", lambda chain_id, **kwargs: _StubRuntime({"ok": True})
    )
    database = tmp_path / "replica.sqlite3"
    database.write_bytes(b"")
    payload_file = tmp_path / "review.json"
    payload_file.write_text("[1, 2, 3]", encoding="utf-8")
    with pytest.raises(RuntimeError):
        HARNESS.main(["--database", str(database), "--payload", str(payload_file)])


def test_the_harness_writes_utf8_without_a_bom(tmp_path, monkeypatch):
    runtime = _StubRuntime({"ok": True, "data": {"state": "match"}})
    monkeypatch.setattr(HARNESS, "_runtime_core", lambda chain_id, **kwargs: runtime)
    database = tmp_path / "replica.sqlite3"
    database.write_bytes(b"")
    payload_file = tmp_path / "review.json"
    payload_file.write_text(json.dumps({"selectedAmountRaw": "1000"}), encoding="utf-8")
    output = tmp_path / "report.json"
    HARNESS.main(
        ["--database", str(database), "--payload", str(payload_file), "--output", str(output)]
    )
    raw = output.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf")
    assert b"\r\n" not in raw
