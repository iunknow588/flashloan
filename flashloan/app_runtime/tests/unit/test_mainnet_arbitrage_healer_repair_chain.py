"""Tests for the mainnet self-healing orchestrator's repair chain.

The orchestrator has to be able to drive the whole chain on its own:

* an **invalid token** is repaired by the token manager,
* an **invalid pool** is repaired by the pool manager,
* a **closed loop** is rebuilt by the composite manager,
* a **review without an explicit profit value** is replaced by a fresh
  review, until every route carries one, and
* a **failed broadcast** is diagnosed and repaired by the broadcast manager.

The tests here lock down the two pieces that were missing:

* the **broadcast failure classification** and the repair that follows from
  it. A market outcome ("the route did not pay") is **not** a defect and is
  never repaired; a rejected transaction **is** a defect and is finalized;
  an unknown failure is reported rather than folded into a known class.
* the **review convergence**: the loop is bounded, it detects a stall, and
  it reports why it stopped instead of looking like a success.

The tests never touch the network and never write to the production
database: they use a temporary SQLite file and a stubbed API.
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
import sqlite3
from datetime import datetime, timezone
import sys

import pytest


def _load_healer():
    root = pathlib.Path(__file__).resolve().parents[4]
    script = root / "scripts" / "mainnet_arbitrage_healer.py"
    spec = importlib.util.spec_from_file_location("mainnet_arbitrage_healer", script)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


HEALER = _load_healer()


# ------------------------------------------------- the broadcast classification


def test_a_market_outcome_is_not_a_defect_and_is_never_repaired():
    """A route that did not pay is a market outcome, not a broken attempt."""
    assert HEALER.classify_broadcast_failure("not_profitable_after_costs") == "no_profit"
    assert HEALER.classify_broadcast_failure("NO_PROFIT") == "no_profit"
    assert HEALER.classify_broadcast_failure("below_min_profit") == "no_profit"


def test_a_rejected_transaction_is_a_defect():
    """A rejected transaction is a defect that the broadcast manager fixes."""
    assert HEALER.classify_broadcast_failure("execution reverted") == "transaction_rejected"
    assert HEALER.classify_broadcast_failure("out of gas") == "transaction_rejected"
    assert HEALER.classify_broadcast_failure("nonce too low") == "transaction_rejected"


def test_a_missing_pool_state_is_repaired_upstream_first():
    """The pool state has to be repaired before another attempt can be made."""
    assert HEALER.classify_broadcast_failure("pool_key_unavailable") == "pool_state_unavailable"
    assert HEALER.classify_broadcast_failure("no liquidity") == "pool_state_unavailable"


def test_a_stale_state_is_told_apart_from_a_missing_one():
    """A stale state and a missing state need different repairs."""
    assert HEALER.classify_broadcast_failure("stale state") == "stale_pool_state"
    assert HEALER.classify_broadcast_failure("sqrt_price_changed") == "stale_pool_state"


def test_an_unreachable_endpoint_is_a_runtime_retry():
    """Nothing is wrong with the route, so the runtime retries the endpoint."""
    assert HEALER.classify_broadcast_failure("rpc_unavailable") == "rpc_unavailable"
    assert HEALER.classify_broadcast_failure("request timed out") == "rpc_unavailable"


def test_an_unknown_failure_is_reported_and_not_guessed():
    """An unknown message must be visible, not folded into a known class."""
    assert HEALER.classify_broadcast_failure("something entirely new") == "unknown"
    assert HEALER.classify_broadcast_failure("") == "unknown"
    assert HEALER.classify_broadcast_failure(None) == "unknown"


# ------------------------------------------------------ the broadcast repair


def _broadcast_database(tmp_path, *, status, reason):
    database = tmp_path / "broadcast.sqlite3"
    connection = sqlite3.connect(database)
    connection.execute(
        """
        CREATE TABLE manager_cow_execution_attempts (
            attempt_id TEXT, attempt_key TEXT, loop_key TEXT, network TEXT,
            chain_id INTEGER, status TEXT, status_reason TEXT, tx_hash TEXT,
            created_at TEXT
        )
        """
    )
    connection.execute(
        "INSERT INTO manager_cow_execution_attempts VALUES "
        "('attempt-1','key-1','loop-1','robinhood',4663,?,?,NULL,'2026-11-15T00:00:00Z')",
        (status, reason),
    )
    connection.commit()
    connection.close()
    return database


def test_a_failed_broadcast_is_finalized_by_the_broadcast_manager(tmp_path, monkeypatch):
    """A rejected transaction is closed through the broadcast manager API."""
    database = _broadcast_database(
        tmp_path, status="failed", reason="execution reverted"
    )
    calls: list[dict] = []

    def fake_post(base, path, body):
        calls.append({"path": path, "body": body})
        return {"ok": True, "status": 200, "body": json.dumps({"ok": True})}

    monkeypatch.setattr(HEALER, "api_post", fake_post)
    result = HEALER.repair_broadcast_failures(
        database, "http://manager", True, 24, 10
    )
    assert result["candidateCount"] == 1
    assert result["failureClasses"] == {"transaction_rejected": 1}
    entry = result["results"][0]
    assert entry["action"] == "finalize_failed"
    assert entry["finalizeResponse"]["ok"] is True
    assert calls, "the broadcast manager API must be called"
    assert calls[0]["path"] == "/v1/broadcasts/finalize"
    assert calls[0]["body"]["attempts"][0]["attemptId"] == "attempt-1"
    assert calls[0]["body"]["dryRun"] is False


def test_a_market_outcome_is_never_repaired(tmp_path, monkeypatch):
    """A route that did not pay is reported and left alone."""
    database = _broadcast_database(
        tmp_path, status="failed", reason="not_profitable_after_costs"
    )

    def fake_post(base, path, body):  # pragma: no cover - must not be called
        raise AssertionError("a market outcome must not reach the broadcast manager")

    monkeypatch.setattr(HEALER, "api_post", fake_post)
    result = HEALER.repair_broadcast_failures(
        database, "http://manager", True, 24, 10
    )
    assert result["candidateCount"] == 1
    assert result["results"][0]["action"] == "none"
    assert "not a defect" in result["results"][0]["reason"]


def test_a_dry_run_never_writes(tmp_path, monkeypatch):
    """The dry run asks the same question but writes nothing."""
    database = _broadcast_database(
        tmp_path, status="failed", reason="execution reverted"
    )
    calls: list[dict] = []

    def fake_post(base, path, body):
        calls.append({"path": path, "body": body})
        return {"ok": True, "status": 200, "body": json.dumps({"ok": True})}

    monkeypatch.setattr(HEALER, "api_post", fake_post)
    result = HEALER.repair_broadcast_failures(
        database, "http://manager", False, 24, 10
    )
    assert result["dryRun"] is True
    assert calls[0]["body"]["dryRun"] is True


def test_a_pending_attempt_is_never_repaired(tmp_path, monkeypatch):
    """A pending transaction is not a failure; repairing it risks a double spend."""
    database = _broadcast_database(
        tmp_path, status="pending", reason="execution reverted"
    )

    def fake_post(base, path, body):  # pragma: no cover - must not be called
        raise AssertionError("a pending attempt must not be touched")

    monkeypatch.setattr(HEALER, "api_post", fake_post)
    result = HEALER.repair_broadcast_failures(
        database, "http://manager", True, 24, 10
    )
    assert result["candidateCount"] == 0


# ------------------------------------------------------ the review convergence


def test_the_convergence_reports_a_stall_instead_of_a_success(tmp_path, monkeypatch):
    """A route that can never be valued must not spin the loop forever."""
    database = tmp_path / "reviews.sqlite3"
    connection = sqlite3.connect(database)
    connection.execute(
        """
        CREATE TABLE cow_execution_reviews (
            id INTEGER PRIMARY KEY, attempt_key TEXT, network TEXT, chain_id INTEGER,
            category TEXT, payload_json TEXT, summary_json TEXT,
            selected_profit_raw TEXT, selected_profit_amount REAL,
            selected_profit_amount_symbol TEXT, selected_profit_missing_reason TEXT,
            created_at TEXT, updated_at TEXT
        )
        """
    )
    connection.execute(
        "INSERT INTO cow_execution_reviews VALUES "
        "(1,'key-1','robinhood',4663,'executable',?1,'{}',NULL,NULL,'USDG',"
        "'no_profit_value','2026-11-15T00:00:00Z','2026-11-15T00:00:00Z')",
        (json.dumps({
            "routeAddresses": [
                "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
                "0xcccccccccccccccccccccccccccccccccccccccc",
                "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            ]
        }),),
    )
    connection.commit()
    connection.close()

    monkeypatch.setattr(HEALER, "review_rows", lambda db, hours, limit=None: [
        {
            "id": 1,
            "attempt_key": "key-1",
            "network": "robinhood",
            "chain_id": 4663,
            "category": "executable",
            "payload_json": json.dumps({
                "routeAddresses": [
                    "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                    "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
                    "0xcccccccccccccccccccccccccccccccccccccccc",
                    "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                ]
            }),
            "summary_json": "{}",
            "selected_profit_raw": None,
            "selected_profit_amount": None,
            "selected_profit_amount_symbol": "USDG",
            "selected_profit_missing_reason": "no_profit_value",
            "created_at": "2026-11-15T00:00:00Z",
            "updated_at": "2026-11-15T00:00:00Z",
        }
    ])
    monkeypatch.setattr(HEALER, "pnl", lambda row: None)
    monkeypatch.setattr(
        HEALER,
        "rereview_missing_pnl",
        lambda db, runtime_api, apply, hours, limit, excluded=None, manager_api=None: {
            "candidateCount": 1,
            "results": [],
        },
    )

    result = HEALER.converge_reviews(
        database, "http://runtime", True, 24, 10, max_rounds=3
    )
    # The loop stopped because it made no progress, not because it
    # succeeded.
    assert result["converged"] is False
    assert result["stalled"] is True
    assert result["remainingMissingValue"] == 1
    assert len(result["rounds"]) == 1
    assert result["rounds"][0]["progressed"] is False


def test_the_convergence_stops_when_every_review_carries_a_value(tmp_path, monkeypatch):
    """A route that already carries a value ends the loop immediately."""
    database = tmp_path / "reviews.sqlite3"
    connection = sqlite3.connect(database)
    connection.execute("CREATE TABLE t (id INTEGER)")
    connection.commit()
    connection.close()

    monkeypatch.setattr(HEALER, "reviews_missing_a_value", lambda db, hours: [])
    monkeypatch.setattr(
        HEALER,
        "rereview_missing_pnl",
        lambda db, runtime_api, apply, hours, limit, excluded=None: {
            "candidateCount": 0,
            "results": [],
        },
    )
    result = HEALER.converge_reviews(
        database, "http://runtime", True, 24, 10, max_rounds=3
    )
    assert result["converged"] is True
    assert result["stalled"] is False
    assert result["remainingMissingValue"] == 0
    assert result["rounds"] == []


def test_the_convergence_is_bounded(tmp_path, monkeypatch):
    """The convergence never runs forever, even when it keeps progressing."""
    database = tmp_path / "reviews.sqlite3"
    connection = sqlite3.connect(database)
    connection.execute("CREATE TABLE t (id INTEGER)")
    connection.commit()
    connection.close()

    counter = {"calls": 0}

    def fake_missing(db, hours):
        counter["calls"] += 1
        # Always one missing route, but the count shrinks on every round so
        # the loop keeps making "progress".
        return [{"loopKey": f"loop-{counter['calls']}"}]

    monkeypatch.setattr(HEALER, "reviews_missing_a_value", fake_missing)
    monkeypatch.setattr(
        HEALER,
        "rereview_missing_pnl",
        lambda db, runtime_api, apply, hours, limit, excluded=None, manager_api=None: {
            "candidateCount": 1,
            "results": [],
        },
    )
    result = HEALER.converge_reviews(
        database, "http://runtime", True, 24, 10, max_rounds=3
    )
    assert len(result["rounds"]) <= 3


# -------------------------------------------------- the action selection


def test_a_failed_broadcast_names_the_repair_action():
    """A failed broadcast has to show up as an explicit repair action.

    INV-5: the broadcast layer is only repaired when the review layer above
    it is sound **and** produced a positive net result. A failed attempt
    without an executable positive review is a market outcome or an
    upstream defect, and repairing it here would only mask the real cause.
    """
    report = {
        "layers": {
            "tokens": {"valid": True},
            "pools": {"valid": True},
            "closedLoops": {"valid": True},
            "reviews": {
                "valid": True,
                "legacy": {"stats": {"missingValue": 0, "positive": 2}},
            },
            "broadcast": {"outcomes": {"failed": 3}},
        }
    }
    actions = HEALER.healing_actions(report)
    assert "repair_broadcast_failures" in actions


def test_a_failed_broadcast_without_a_positive_review_is_not_repaired():
    """INV-5: no executable positive review means no broadcast repair."""
    report = {
        "layers": {
            "tokens": {"valid": True},
            "pools": {"valid": True},
            "closedLoops": {"valid": True},
            "reviews": {
                "valid": True,
                "legacy": {"stats": {"missingValue": 0, "positive": 0}},
            },
            "broadcast": {"outcomes": {"failed": 3}},
        }
    }
    actions = HEALER.healing_actions(report)
    assert "repair_broadcast_failures" not in actions


def test_a_failed_broadcast_with_an_unsound_review_layer_is_not_repaired():
    """INV-4: the broadcast layer stays blocked while the review layer fails."""
    report = {
        "layers": {
            "tokens": {"valid": True},
            "pools": {"valid": True},
            "closedLoops": {"valid": True},
            "reviews": {
                "valid": False,
                "legacy": {"stats": {"missingValue": 4, "positive": 2}},
            },
            "broadcast": {"outcomes": {"failed": 3}},
        }
    }
    actions = HEALER.healing_actions(report)
    assert "repair_broadcast_failures" not in actions


def test_a_clean_broadcast_names_no_repair_action():
    """No failed attempt means no repair."""
    report = {
        "layers": {
            "tokens": {"valid": True},
            "pools": {"valid": True},
            "closedLoops": {"valid": True},
            "reviews": {"valid": True, "legacy": {"stats": {"missingValue": 0}}},
            "broadcast": {"outcomes": {"failed": 0}},
        }
    }
    actions = HEALER.healing_actions(report)
    assert "repair_broadcast_failures" not in actions


# ------------------------------------------- the closed-loop scope drift


def test_a_closed_loop_layer_that_records_the_wrong_scope_is_detected():
    """A closed-loop layer must carry the checksum of the scope it belongs to."""
    drift = {
        "driftedLayers": ["manager_v4_closed_loop_library_state"],
        "missingPoolHopCount": 0,
        "affectedLoopCount": 0,
    }
    classification = HEALER.closed_loop_scope_drift(drift)
    assert classification["closedLoopScopeDrifted"] is True
    # Every hop still resolves, so the pool set is healthy and only the
    # closed-loop library has to be rebuilt.
    assert classification["hopsMissingFromCandidateScope"] is False


def test_a_hop_that_names_a_missing_pool_is_a_pool_defect():
    """A hop outside the candidate set has to be repaired before the rebuild."""
    drift = {
        "driftedLayers": ["manager_v4_closed_loop_library_state"],
        "missingPoolHopCount": 38,
        "affectedLoopCount": 12,
    }
    classification = HEALER.closed_loop_scope_drift(drift)
    assert classification["closedLoopScopeDrifted"] is True
    assert classification["hopsMissingFromCandidateScope"] is True


def test_a_healthy_scope_reports_no_drift():
    """No drifted layer means there is nothing to rebuild."""
    classification = HEALER.closed_loop_scope_drift({"driftedLayers": []})
    assert classification["closedLoopScopeDrifted"] is False
    assert classification["hopsMissingFromCandidateScope"] is False
    assert classification["reason"] is None


def test_a_scope_drift_routes_to_the_closed_loop_repair():
    """The drift is a closed-loop defect, so the pool repair cannot clear it."""
    report = {
        "layers": {
            "tokens": {"valid": True},
            "pools": {
                "valid": False,
                "scopeDrift": {
                    "driftedLayers": ["manager_v4_closed_loop_library_state"],
                    "missingPoolHopCount": 0,
                    "affectedLoopCount": 0,
                },
            },
            "closedLoops": {"valid": True},
            "reviews": {"valid": True, "legacy": {"stats": {"missingValue": 0}}},
            "broadcast": {"outcomes": {"failed": 0}},
        }
    }
    actions = HEALER.healing_actions(report)
    assert actions == ["repair_closed_loops"]


def test_a_missing_hop_keeps_the_pool_repair():
    """A hop outside the candidate set is repaired in the pool layer first."""
    report = {
        "layers": {
            "tokens": {"valid": True},
            "pools": {
                "valid": False,
                "scopeDrift": {
                    "driftedLayers": ["manager_v4_closed_loop_library_state"],
                    "missingPoolHopCount": 38,
                    "affectedLoopCount": 12,
                },
            },
            "closedLoops": {"valid": True},
            "reviews": {"valid": True, "legacy": {"stats": {"missingValue": 0}}},
            "broadcast": {"outcomes": {"failed": 0}},
        }
    }
    actions = HEALER.healing_actions(report)
    assert actions == ["repair_pools"]


def test_a_skipped_write_is_not_a_tool_failure():
    """A dry run must not report the write-guarded commands as broken.

    The pool manager refuses to run `ensure-indexes` and `ensure-depth-schema`
    without `--apply`, so it exits non-zero by design in a dry run. The
    healer used to read that as a tool failure and stopped the chain.
    """
    entry = {
        "layer": "depthSchema",
        "skipped": True,
        "result": {"ok": False, "returncode": 1,
                   "stderr": "pool-manager error: ensure-depth-schema requires --apply"},
    }
    # The predicate is a local closure inside `repair`, so the behaviour is
    # locked down through the helper the chain actually uses.
    assert HEALER.tool_entry_failed(entry) is False


def test_a_real_tool_failure_is_still_a_tool_failure():
    """A tool that actually broke still stops the chain."""
    entry = {
        "layer": "pools",
        "result": {"ok": False, "returncode": 1, "stderr": "boom"},
    }
    assert HEALER.tool_entry_failed(entry) is True


def test_a_successful_tool_is_not_a_tool_failure():
    """A successful tool call is obviously not a failure."""
    assert HEALER.tool_entry_failed({"result": {"ok": True, "returncode": 0}}) is False


# ------------------------------------- the Manager-authoritative review source


def _review_database(tmp_path, *, manager_rows=(), legacy_rows=()):
    """Build a store with both review libraries populated."""
    database = tmp_path / "reviews.sqlite3"
    connection = sqlite3.connect(database)
    connection.execute(
        """
        CREATE TABLE manager_v4_reviews (
            network TEXT, chain_id INTEGER, protocol TEXT, scope_key TEXT,
            loop_key TEXT, closed_loop_version INTEGER, closed_loop_checksum TEXT,
            candidate_edge_version INTEGER, candidate_edge_checksum TEXT,
            observation_version INTEGER, observation_checksum TEXT,
            pool_scope_checksum TEXT, anchor_token_key_id TEXT, anchor_address TEXT,
            anchor_symbol TEXT, path_text TEXT, path_addresses_json TEXT,
            path_symbols_json TEXT, pool_path_key TEXT, pool_ids_json TEXT,
            review_status TEXT, review_reason TEXT, static_preview_json TEXT,
            run_arbitrage_json TEXT, signed_pnl_raw TEXT, signed_pnl_amount REAL,
            signed_pnl_currency TEXT, signed_pnl_address TEXT, signed_pnl_unit TEXT,
            signed_pnl_source TEXT, reviewed_at TEXT, created_at TEXT, updated_at TEXT
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE cow_execution_reviews (
            id INTEGER PRIMARY KEY, attempt_key TEXT, network TEXT, chain_id INTEGER,
            category TEXT, pair TEXT, tx_hash TEXT, payload_json TEXT, summary_json TEXT,
            selected_profit_raw TEXT, selected_profit_amount REAL,
            selected_profit_amount_symbol TEXT, selected_profit_source TEXT,
            selected_profit_missing_reason TEXT,
            created_at TEXT, updated_at TEXT
        )
        """
    )
    for row in manager_rows:
        connection.execute(
            "INSERT INTO manager_v4_reviews VALUES (" + ",".join(["?"] * len(row)) + ")",
            row,
        )
    for row in legacy_rows:
        connection.execute(
            "INSERT INTO cow_execution_reviews VALUES (" + ",".join(["?"] * len(row)) + ")",
            row,
        )
    connection.commit()
    connection.close()
    return database


def _fresh_timestamp() -> str:
    """A timestamp the healer has to accept as recent.

    The legacy review table is read with a **time window**: a row older
    than the window is not part of the current observation. A fixture with
    a fixed date therefore stops being visible once the wall clock moves
    past it, and the test fails for a reason that has nothing to do with
    the behaviour it is about.
    """
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _manager_row(loop_key, *, amount=None, status="reviewed", reason=None):
    return (
        "robinhood", 4663, "uniswap_v4", "core4", loop_key,
        2, "sha256:loop", 1, "sha256:edge", 1, "sha256:obs", "sha256:scope",
        "anchor-1", "0x" + "11" * 20, "ETH",
        "ETH>USDC>WETH", json.dumps(["0x" + "aa" * 20, "0x" + "bb" * 20, "0x" + "cc" * 20]),
        json.dumps(["ETH", "USDC", "WETH"]), "key", "[]",
        status, reason, "{}", "{}", None, amount, "USDG", "0x" + "dd" * 20,
        "raw", "live_review", "2026-11-15T00:00:00Z", "2026-11-15T00:00:00Z",
        "2026-11-15T00:00:00Z",
    )


def test_the_manager_library_is_the_audit_source_when_it_holds_rows(tmp_path):
    """A materialized review must not be invisible to the healer."""
    database = _review_database(
        tmp_path,
        manager_rows=[_manager_row("loop-a", amount=1.5)],
        legacy_rows=[
            (1, "key-1", "robinhood", 4663, "executable", "ETH/USDC", None,
             "{}", "{}",
             None, None, "USDG", None, "no_profit_value",
             _fresh_timestamp(), _fresh_timestamp()),
        ],
    )
    rows = HEALER.review_rows(database, 24)
    assert len(rows) == 1
    assert rows[0]["review_source"] == "manager"
    # The legacy row carries no value; the manager row carries one. The
    # healer has to see the manager row, otherwise the convergence can
    # never satisfy "every review carries an explicit value".
    assert HEALER.pnl(rows[0])["amount"] == 1.5


def test_the_legacy_library_is_used_while_the_manager_library_is_empty(tmp_path):
    """A store that has not migrated yet still produces a report."""
    database = _review_database(
        tmp_path,
        legacy_rows=[
            (1, "key-1", "robinhood", 4663, "executable", "ETH/USDC", None,
             "{}", "{}",
             "7", 7.0, "USDG", "live_review", None,
             _fresh_timestamp(), _fresh_timestamp()),
        ],
    )
    rows = HEALER.review_rows(database, 24)
    assert len(rows) == 1
    assert rows[0]["review_source"] == "legacy"
    assert HEALER.pnl(rows[0])["amount"] == 7.0


def test_a_manager_review_without_a_value_is_reported_as_missing(tmp_path):
    """A missing value stays missing; it is never read as zero profit."""
    database = _review_database(
        tmp_path, manager_rows=[_manager_row("loop-a", amount=None, reason="no_profit_value")]
    )
    rows = HEALER.review_rows(database, 24)
    assert HEALER.pnl(rows[0]) is None
    assert "no_profit_value" in HEALER.review_missing_reason(rows[0])
    missing = HEALER.reviews_missing_a_value(database, 24)
    assert [item["loopKey"] for item in missing] == ["loop-a"]


def test_the_manager_loop_key_is_used_as_the_route_identity(tmp_path):
    """The Manager library carries the loop key, so it is authoritative."""
    database = _review_database(tmp_path, manager_rows=[_manager_row("loop-x", amount=2.0)])
    rows = HEALER.review_rows(database, 24)
    assert HEALER.review_loop_key(rows[0]) == "loop-x"


def test_the_review_materialization_sends_the_expected_upstream(tmp_path):
    """The review manager refuses a review whose upstream does not match."""
    database = tmp_path / "reviews.sqlite3"
    connection = sqlite3.connect(database)
    connection.execute(
        """
        CREATE TABLE manager_v4_closed_loop_library_state (
            network TEXT, chain_id INTEGER, protocol TEXT, scope_key TEXT,
            closed_loop_version INTEGER, materialized_checksum TEXT,
            candidate_edge_version INTEGER, candidate_edge_checksum TEXT,
            observation_version INTEGER, observation_checksum TEXT,
            pool_scope_checksum TEXT
        )
        """
    )
    connection.execute(
        "INSERT INTO manager_v4_closed_loop_library_state VALUES "
        "('robinhood',4663,'uniswap_v4','core4',3,'sha256:loop',"
        "2,'sha256:edge',1,'sha256:obs','sha256:scope')"
    )
    connection.commit()
    connection.close()

    calls: list[dict] = []

    def fake_post(base, path, body):
        calls.append({"path": path, "body": body})
        return {"ok": True, "status": 200, "body": json.dumps({"ok": True})}

    original_post = HEALER.api_post
    original_count = HEALER.count
    try:
        HEALER.api_post = fake_post
        HEALER.count = lambda db, table: 1
        result = HEALER.materialize_reviews(
            database, "http://manager",
            [{"loopKey": "loop-a", "runArbitrage": {"ok": True}}], True,
        )
    finally:
        HEALER.api_post = original_post
        HEALER.count = original_count

    assert result["evidenceCount"] == 1
    assert calls, "the review manager API must be called"
    assert calls[0]["path"] == "/v1/reviews/run"
    body = calls[0]["body"]
    assert body["dryRun"] is False
    assert body["expectedUpstream"]["closedLoopVersion"] == 3
    assert body["expectedUpstream"]["poolScopeChecksum"] == "sha256:scope"


def test_an_empty_evidence_list_materializes_nothing(tmp_path):
    """No evidence means no write, and the manager is not called."""
    database = tmp_path / "reviews.sqlite3"
    connection = sqlite3.connect(database)
    connection.execute("CREATE TABLE t (id INTEGER)")
    connection.commit()
    connection.close()

    def fake_post(base, path, body):  # pragma: no cover - must not be called
        raise AssertionError("the review manager must not be called without evidence")

    original_post = HEALER.api_post
    try:
        HEALER.api_post = fake_post
        result = HEALER.materialize_reviews(database, "http://manager", [], True)
    finally:
        HEALER.api_post = original_post
    assert result["evidenceCount"] == 0
    assert result["materializedCount"] == 0


def test_manual_review_transition_ack_is_not_review_evidence():
    """RuntimeCore acceptance only queues work; it carries no signed result."""
    candidate = {
        "loopKey": "loop-a",
        "routeSymbols": ["AAA", "BBB", "CCC", "AAA"],
        "routeAddresses": ["0xa", "0xb", "0xc", "0xa"],
    }
    response = {
        "finalReview": {
            "ok": True,
            "status": 200,
            "body": json.dumps({
                "ok": True,
                "accepted": True,
                "mode": "native_closed_loop_group_manual_review",
                "groupKey": "aaa|bbb|ccc",
                "transitionQueued": True,
                "processingAsync": True,
            }),
        }
    }

    assert HEALER.live_review_evidence(response, candidate) is None


def test_runtime_review_result_builds_complete_manager_input(tmp_path):
    """A real signed result keeps its evidence and uses the Manager loop key."""
    database = tmp_path / "reviews.sqlite3"
    connection = sqlite3.connect(database)
    connection.execute(
        """
        CREATE TABLE manager_v4_closed_loop_library_state (
            network TEXT, chain_id INTEGER, protocol TEXT, scope_key TEXT,
            closed_loop_version INTEGER, materialized_checksum TEXT,
            candidate_edge_version INTEGER, candidate_edge_checksum TEXT,
            observation_version INTEGER, observation_checksum TEXT,
            pool_scope_checksum TEXT
        )
        """
    )
    connection.execute(
        "INSERT INTO manager_v4_closed_loop_library_state VALUES "
        "('robinhood',4663,'uniswap_v4','core4',3,'sha256:loop',"
        "2,'sha256:edge',1,'sha256:obs','sha256:scope')"
    )
    connection.commit()
    connection.close()
    candidate = {
        "loopKey": "manager-loop-a",
        "routeSymbols": ["AAA", "BBB", "CCC", "AAA"],
        "routeAddresses": ["0xa", "0xb", "0xc", "0xa"],
    }
    response = {
        "finalReview": {
            "ok": True,
            "status": 200,
            "body": json.dumps({
                "ok": True,
                "task": {
                    "result": {
                        "evidence": {
                            "loopKey": "runtime-presentation-key",
                            "staticPreview": {"method": "previewArbitrageBestAmount"},
                            "runArbitrage": {
                                "ok": True,
                                "executable": False,
                                "blockNumber": 123,
                                "signedPnl": {
                                    "raw": "-10",
                                    "amount": -0.1,
                                    "currency": "USDG",
                                    "address": "0x1",
                                    "unit": "token",
                                    "source": "runArbitrage",
                                },
                                "diagnostics": [],
                            },
                            "diagnostics": [],
                            "reviewedAt": "2026-11-15T01:23:45Z",
                        }
                    }
                },
            }),
        }
    }
    evidence = HEALER.live_review_evidence(response, candidate)
    calls = []

    def fake_post(base, path, body):
        calls.append({"base": base, "path": path, "body": body})
        return {"ok": True, "status": 200, "body": json.dumps({"ok": True})}

    original_post = HEALER.api_post
    original_count = HEALER.count
    try:
        HEALER.api_post = fake_post
        HEALER.count = lambda db, table: 1
        result = HEALER.materialize_reviews(
            database, "http://manager", [evidence], True,
        )
    finally:
        HEALER.api_post = original_post
        HEALER.count = original_count

    assert result["evidenceCount"] == 1
    assert evidence["loopKey"] == "manager-loop-a"
    assert evidence["runArbitrage"]["signedPnl"]["amount"] == -0.1
    assert calls[0]["path"] == "/v1/reviews/run"
    assert calls[0]["body"]["evidence"] == [evidence]


# ------------------------------------------- the plan tree and the epoch


def test_the_observation_epoch_is_read_from_the_current_plan_tree():
    """The healer must find the epoch after the plan tree migration.

    The plan tree was migrated from `logs/plan/` to `plan/` on 2026-11-14.
    The healer kept the old path, so the epoch was never found and the
    review window silently widened to "everything".
    """
    epoch = HEALER.observation_epoch()
    assert epoch["epochFallback"] is False
    assert epoch["epochUnknown"] is False
    assert epoch["startedAt"].startswith("20")
    assert "logs\\plan" not in str(HEALER.EPOCH_FILE).replace("/", "\\")


def test_the_report_directory_is_inside_the_plan_tree():
    """New evidence has to land in the tree the plan documents point at."""
    assert "logs\\plan" not in str(HEALER.REPORT_DIR).replace("/", "\\")
    assert str(HEALER.REPORT_DIR).replace("/", "\\").endswith("plan\\runtime_evidence\\reports")
