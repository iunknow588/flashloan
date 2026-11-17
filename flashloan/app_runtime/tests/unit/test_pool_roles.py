# -*- coding: utf-8 -*-
"""Tests for the pool role maintenance bridge.

The pool roles are functional work that belongs to the Rust Pool Manager:

* the candidate pools are the best priced pools of a direction;
* the recommendation pool is the single best priced pool of a direction;
* the backup pools are the remaining pools, retained while they trade;
* the cleanup prunes only the backups, and only behind a startup gate.

These tests lock down the parts that are easy to break:

* the **architecture**: the bridge is a thin forwarder. It reaches the Rust
  managers through the in-memory ``RuntimeCore`` and never reads a
  manager-owned table and never decides a pool role on its own;
* the **retention rule**: the candidates and the recommendations are never
  deleted, and the backups are pruned only when they did not trade inside
  the window;
* the **startup gate**: the cleanup is skipped while the runtime still
  carries little data, so the early phase never deletes a row;
* the **file format** of the report: UTF-8 without BOM and LF endings.
"""

from __future__ import annotations

import json
import pathlib

from dex.runtime.pool_roles import (
    DEFAULT_CLEANUP_INTERVAL_DAYS,
    DEFAULT_MIN_POOLS,
    DEFAULT_MIN_TRADE_OBSERVATIONS,
    DEFAULT_RETENTION_DAYS,
    PoolRoleRequest,
    roles_enabled,
    run_pool_role_maintenance,
    write_role_report,
)


BRIDGE = (
    pathlib.Path(__file__).resolve().parents[3] / "src" / "dex" / "runtime" / "pool_roles.py"
)


# --------------------------------------------------------------- architecture


def test_the_bridge_does_not_import_a_database_driver():
    """The bridge must never open a manager-owned table itself."""
    source = BRIDGE.read_text(encoding="utf-8")
    assert "sqlite3" not in source
    assert "aiosqlite" not in source


def test_the_bridge_never_names_a_manager_owned_table():
    """A direct table reference would be an ownership violation."""
    source = BRIDGE.read_text(encoding="utf-8")
    for table in (
        "v4_exchange_pools",
        "v4_exchange_pool_liquidity_snapshots",
        "dex_candidate_pools",
        "dex_candidate_pool_recommendations",
        "pool_role_assignment",
        "pool_trade_activity",
    ):
        assert table not in source, f"the bridge must not reference {table}"


def test_the_bridge_reaches_the_managers_through_the_runtime_core():
    """The in-memory big object is the only channel to the managers."""
    source = BRIDGE.read_text(encoding="utf-8")
    assert "chain_runtime_registry" in source
    assert "execute_command" in source
    assert "pool_roles_manage" in source


def test_the_bridge_does_not_implement_the_role_selection():
    """The price ordering belongs to the candidate edge manager."""
    source = BRIDGE.read_text(encoding="utf-8")
    assert "directional_score" not in source
    assert "sqrt_price_x96" not in source
    assert "MAX_RECOMMENDATIONS_PER_HOP" not in source


# ------------------------------------------------------------------ retention


def test_the_cleanup_is_off_by_default():
    """The startup gate is closed until an operator opens it."""
    request = PoolRoleRequest()
    assert request.cleanup_enabled is False
    assert request.apply is False
    assert request.action == "status"


def test_the_defaults_match_the_ruling():
    """Three days of retention and a three day cleanup cadence."""
    assert DEFAULT_RETENTION_DAYS == 3
    assert DEFAULT_CLEANUP_INTERVAL_DAYS == 3
    assert DEFAULT_MIN_POOLS > 0
    assert DEFAULT_MIN_TRADE_OBSERVATIONS > 0


def test_the_request_carries_the_gate_through():
    """The gate reaches the manager as named options."""
    request = PoolRoleRequest(
        action="cleanup",
        cleanup_enabled=True,
        retention_days=3,
        min_pools=10,
        min_trade_observations=5,
        apply=True,
    )
    payload = request.to_dict()
    assert payload["action"] == "cleanup"
    assert payload["cleanupEnabled"] is True
    assert payload["minPools"] == 10
    assert payload["minTradeObservations"] == 5
    assert payload["apply"] is True


def test_the_bridge_is_disabled_by_default(monkeypatch):
    """The outer switch keeps the maintenance out of the default run."""
    monkeypatch.delenv("DEX_POOL_ROLE_MAINTENANCE", raising=False)
    assert roles_enabled() is False
    monkeypatch.setenv("DEX_POOL_ROLE_MAINTENANCE", "1")
    assert roles_enabled() is True


# ------------------------------------------------------------------- command


def test_the_bridge_calls_the_runtime_core(monkeypatch):
    """The request is handed to the manager through the registry."""
    captured = {}

    class _Runtime:
        def execute_command(self, command, **kwargs):
            captured["command"] = command
            captured["payload"] = kwargs.get("options")
            captured["dryRun"] = kwargs.get("dry_run")
            return {"data": {"action": "status", "roles": []}}

    class _Registry:
        def get(self, chain_id):
            captured["chainId"] = chain_id
            return _Runtime()

    class _Module:
        def chain_runtime_registry(self):
            return _Registry()

    monkeypatch.setitem(
        __import__("sys").modules,
        "core.runtime.chain_runtime",
        _Module(),
    )
    report = run_pool_role_maintenance(4663)
    assert captured["command"] == "pool_roles_manage"
    assert captured["chainId"] == 4663
    assert captured["payload"]["action"] == "status"
    assert captured["dryRun"] is True
    assert report == {"action": "status", "roles": []}


# -------------------------------------------------------------------- report


def test_the_report_is_utf8_without_bom_and_lf(tmp_path):
    """The report file follows the project file format rule."""
    report = {"action": "cleanup", "roles": [{"role": "backup", "count": 3}]}
    path = write_role_report(report, report_dir=tmp_path, stamp="pool_roles")
    raw = path.read_bytes()
    assert raw[:3] != b"\xef\xbb\xbf"
    assert b"\r\n" not in raw
    assert raw.endswith(b"\n")
    parsed = json.loads(raw.decode("utf-8"))
    assert parsed["action"] == "cleanup"


def test_the_report_directory_is_created(tmp_path):
    """The report directory is created on demand."""
    target = tmp_path / "nested" / "reports"
    path = write_role_report({"action": "status"}, report_dir=target, stamp="x")
    assert path.exists()
    assert path.name == "x.json"