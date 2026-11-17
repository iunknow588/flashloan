"""Regression coverage for AC-017.

``pool-manager baseline-audit`` has to report the **exact** set of migrations
a production apply would run (``missingMigrations``) together with the set the
ledger already carries (``appliedMigrations``).

The report used to say only "the latest expected migration is missing", which
left an operator unable to scope the change window: one missing migration and
seven missing migrations look identical in that report.  The Rust side
already carries the exact sets; these tests lock down that the *healer*
report -- the artefact an operator actually reads -- exposes them, and that
the serialisation keeps the camelCase key names the Python side reads.

The tests never touch the network and never write to the production
database: the tool is stubbed.
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
import sys


def _load_healer():
    root = pathlib.Path(__file__).resolve().parents[4]
    script = root / "scripts" / "mainnet_arbitrage_healer.py"
    spec = importlib.util.spec_from_file_location("mainnet_arbitrage_healer", script)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _tool_stdout(migrations: dict) -> str:
    payload = {
        "network": "robinhood",
        "chainId": 4663,
        "valid": True,
        "migrations": migrations,
        "issues": [],
    }
    return json.dumps(payload)


def test_the_healer_reports_the_exact_missing_and_applied_migrations(monkeypatch):
    healer = _load_healer()

    migrations = {
        "ledger": "schema_migrations",
        "appliedCount": 7,
        "latestExpectedMigration": "20261114_009_hop_price_limit_schema",
        "latestExpectedApplied": False,
        "unknownMigrationCount": 0,
        "missingMigrations": [
            "20261112_007_usdg_valuation_schema",
            "20261114_008_single_tick_capacity_schema",
            "20261114_009_hop_price_limit_schema",
        ],
        "appliedMigrations": [
            "20261103_001_manager_schema",
            "20261104_001_candidate_edge_schema",
            "20261104_002_candidate_exchange_schema",
            "20261104_003_closed_loop_schema",
            "20261104_004_review_schema",
            "20261104_005_broadcast_schema",
            "20261104_006_composite_schema",
        ],
    }

    monkeypatch.setattr(
        healer,
        "run_tool",
        lambda arguments, timeout=900: {
            "ok": True,
            "returncode": 0,
            "stdout": _tool_stdout(migrations),
            "stderr": "",
        },
    )

    payload = healer.audit_database_baseline(
        pathlib.Path("local_store_4663.sqlite3"), pathlib.Path("tools")
    )

    assert payload["valid"] is True
    assert payload["missingMigrations"] == migrations["missingMigrations"]
    assert payload["missingMigrationCount"] == 3
    # The applied set has to survive into the report too, otherwise the
    # operator cannot tell which migrations the ledger already carries.
    assert payload["migrations"]["appliedMigrations"] == migrations["appliedMigrations"]


def test_the_healer_reports_the_command_it_ran(monkeypatch):
    healer = _load_healer()

    seen: list[list[str]] = []

    def _capture(arguments, timeout=900):
        seen.append(list(arguments))
        return {"ok": True, "returncode": 0, "stdout": _tool_stdout({}), "stderr": ""}

    monkeypatch.setattr(healer, "run_tool", _capture)

    healer.audit_database_baseline(
        pathlib.Path("local_store_4663.sqlite3"), pathlib.Path("tools")
    )

    assert seen, "the healer must invoke the pool manager baseline audit"
    command = seen[0]
    assert "baseline-audit" in command
    assert "--chain-id" in command


def test_an_unreadable_tool_output_is_reported_as_invalid(monkeypatch):
    healer = _load_healer()

    monkeypatch.setattr(
        healer,
        "run_tool",
        lambda arguments, timeout=900: {
            "ok": True,
            "returncode": 0,
            "stdout": "not json at all",
            "stderr": "",
        },
    )

    payload = healer.audit_database_baseline(
        pathlib.Path("local_store_4663.sqlite3"), pathlib.Path("tools")
    )

    assert payload["valid"] is False
    assert "baseline_audit_output_invalid" in payload["issues"]
