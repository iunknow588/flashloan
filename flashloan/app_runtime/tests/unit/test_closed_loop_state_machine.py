"""Unit tests for the closed-loop runtime state machine."""

from __future__ import annotations

import json

import pytest

from dex.runtime import closed_loop_state_machine as sm


ANCHOR_ADDRESS = "0x" + ("1" * 40)
Y_ADDRESS = "0x" + ("2" * 40)
Z_ADDRESS = "0x" + ("3" * 40)
FORWARD_LOOP_KEY = f"{ANCHOR_ADDRESS}:{Y_ADDRESS}:{Z_ADDRESS}"
REVERSE_LOOP_KEY = f"{ANCHOR_ADDRESS}:{Z_ADDRESS}:{Y_ADDRESS}"


def _row_path(tmp_path) -> str:
    return str(tmp_path / "closed_loop_state.sqlite3")


def _args() -> dict[str, object]:
    return {
        "network": "robinhood-testnet",
        "chain_id": 46630,
        "protocol": "uniswap_v4",
        "market_id": "dex-arbitrage-46630-uniswap-v4",
        "loop_key": "ta|tb|tc",
    }


def test_states_align_with_route_and_runtime_terminals() -> None:
    assert sm.STATE_OFFCHAIN_PENDING == "offchain_pending"
    assert sm.STATE_OFFCHAIN_ESTIMATE == "offchain_estimate"
    assert sm.STATE_PREFLIGHT_RUNNING == "preflight_running"
    assert sm.STATE_PREFLIGHT_DONE == "preflight_done"
    assert sm.STATE_PREFLIGHT_FAILED == "preflight_failed"
    assert sm.STATE_EXECUTION_NOT_REQUESTED == "execution_not_requested"
    assert sm.STATE_EXECUTION_PENDING == "execution_pending"
    assert sm.STATE_EXECUTION_SUBMITTED == "execution_submitted"
    assert sm.STATE_EXECUTION_FAILED == "execution_failed"
    assert sm.STATE_EXECUTION_SUCCESS == "execution_success"
    assert sm.STATE_PREFLIGHT_RUNNING in sm.REVIEW_RUNNING_STATES
    assert sm.STATE_EXECUTION_SUBMITTED in sm.BROADCAST_RUNNING_STATES
    assert {
        sm.STATE_EXECUTION_SUCCESS,
        sm.STATE_EXECUTION_FAILED,
    } == set(sm.TERMINAL_EXECUTION_STATES)


@pytest.mark.parametrize(
    ("current", "next_state", "expected"),
    [
        (sm.STATE_OFFCHAIN_PENDING, sm.STATE_OFFCHAIN_ESTIMATE, True),
        (sm.STATE_OFFCHAIN_ESTIMATE, sm.STATE_PREFLIGHT_RUNNING, True),
        (sm.STATE_PREFLIGHT_RUNNING, sm.STATE_PREFLIGHT_DONE, True),
        (sm.STATE_PREFLIGHT_RUNNING, sm.STATE_EXECUTION_PENDING, False),
        (sm.STATE_PREFLIGHT_DONE, sm.STATE_EXECUTION_PENDING, True),
        (sm.STATE_PREFLIGHT_DONE, sm.STATE_EXECUTION_NOT_REQUESTED, True),
        (sm.STATE_PREFLIGHT_FAILED, sm.STATE_PREFLIGHT_RUNNING, True),
        (sm.STATE_EXECUTION_PENDING, sm.STATE_EXECUTION_SUBMITTED, True),
        (sm.STATE_EXECUTION_SUBMITTED, sm.STATE_EXECUTION_SUCCESS, True),
        (sm.STATE_EXECUTION_SUBMITTED, sm.STATE_EXECUTION_PENDING, True),
        (sm.STATE_EXECUTION_FAILED, sm.STATE_EXECUTION_PENDING, True),
        (sm.STATE_EXECUTION_SUCCESS, sm.STATE_EXECUTION_PENDING, False),
    ],
)
def test_can_transition_matches_route_state_machine(
    current: str,
    next_state: str,
    expected: bool,
) -> None:
    assert sm.can_transition(current, next_state) is expected


def test_upsert_loop_state_creates_row_and_rejects_unknown_state(tmp_path) -> None:
    path = _row_path(tmp_path)
    row = sm.upsert_loop_state(
        path,
        state=sm.STATE_OFFCHAIN_ESTIMATE,
        generation=1,
        owner_thread="scanner",
        **_args(),
    )
    assert row["state"] == sm.STATE_OFFCHAIN_ESTIMATE
    assert row["owner_thread"] == "scanner"
    assert row["generation"] == 1
    assert row["tx_hash"] is None

    with pytest.raises(ValueError):
        sm.upsert_loop_state(path, state="free", **_args())


def test_claim_loop_review_is_atomic_and_prevents_duplicate_owner(tmp_path) -> None:
    path = _row_path(tmp_path)
    sm.upsert_loop_state(path, state=sm.STATE_OFFCHAIN_ESTIMATE, **_args())

    claimed = sm.claim_loop(
        path,
        operation="review",
        owner_thread="worker-a",
        **_args(),
    )
    assert claimed is not None
    assert claimed["state"] == sm.STATE_PREFLIGHT_RUNNING
    assert claimed["owner_thread"] == "worker-a"

    assert (
        sm.claim_loop(
            path,
            operation="review",
            owner_thread="worker-b",
            **_args(),
        )
        is None
    )
    assert (
        sm.claim_loop(
            path,
            operation="review",
            owner_thread="worker-a",
            **_args(),
        )
        is None
    )


def test_claim_loop_broadcast_only_from_execution_pending(tmp_path) -> None:
    path = _row_path(tmp_path)
    sm.upsert_loop_state(path, state=sm.STATE_PREFLIGHT_DONE, **_args())
    assert (
        sm.claim_loop(
            path,
            operation="broadcast",
            owner_thread="broadcaster",
            **_args(),
        )
        is None
    )

    sm.enqueue_execution(path, **_args())
    claimed = sm.claim_loop(
        path,
        operation="broadcast",
        owner_thread="broadcaster",
        **_args(),
    )
    assert claimed is not None
    assert claimed["state"] == sm.STATE_EXECUTION_SUBMITTED
    assert (
        sm.claim_loop(
            path,
            operation="broadcast",
            owner_thread="broadcaster-2",
            **_args(),
        )
        is None
    )


def test_complete_loop_success_records_tx_hash_idempotently(tmp_path) -> None:
    path = _row_path(tmp_path)
    sm.upsert_loop_state(path, state=sm.STATE_OFFCHAIN_ESTIMATE, **_args())
    sm.claim_loop(path, operation="review", owner_thread="reviewer", **_args())
    sm.complete_loop(
        path,
        operation="review",
        outcome="success",
        owner_thread="reviewer",
        **_args(),
    )
    sm.enqueue_execution(path, **_args())
    sm.claim_loop(path, operation="broadcast", owner_thread="broadcaster", **_args())

    completed = sm.complete_loop(
        path,
        operation="broadcast",
        outcome="success",
        owner_thread="broadcaster",
        tx_hash="0x" + "a" * 64,
        **_args(),
    )

    assert completed is not None
    assert completed["state"] == sm.STATE_EXECUTION_SUCCESS
    assert completed["tx_hash"] == "0x" + "a" * 64
    assert completed["owner_thread"] is None
    assert (
        sm.claim_loop(
            path,
            operation="broadcast",
            owner_thread="broadcaster-2",
            **_args(),
        )
        is None
    )


def test_owner_mismatch_cannot_complete_or_release_loop(tmp_path) -> None:
    path = _row_path(tmp_path)
    sm.upsert_loop_state(path, state=sm.STATE_OFFCHAIN_ESTIMATE, **_args())
    sm.claim_loop(path, operation="review", owner_thread="worker-a", **_args())

    assert (
        sm.complete_loop(
            path,
            operation="review",
            outcome="success",
            owner_thread="worker-b",
            **_args(),
        )
        is None
    )
    assert (
        sm.release_loop(
            path,
            operation="review",
            owner_thread="worker-b",
            **_args(),
        )
        is None
    )
    row = sm.load_loop_state(path, **_args())
    assert row is not None
    assert row["state"] == sm.STATE_PREFLIGHT_RUNNING
    assert row["owner_thread"] == "worker-a"

    released = sm.release_loop(
        path,
        operation="review",
        owner_thread="worker-a",
        **_args(),
    )
    assert released is not None
    assert released["state"] == sm.STATE_OFFCHAIN_ESTIMATE


def test_enqueue_execution_requires_preflight_done(tmp_path) -> None:
    path = _row_path(tmp_path)
    sm.upsert_loop_state(path, state=sm.STATE_OFFCHAIN_ESTIMATE, **_args())
    assert sm.enqueue_execution(path, **_args()) is None

    sm.claim_loop(path, operation="review", owner_thread="reviewer", **_args())
    sm.complete_loop(
        path,
        operation="review",
        outcome="success",
        owner_thread="reviewer",
        **_args(),
    )
    row = sm.enqueue_execution(path, **_args())
    assert row is not None
    assert row["state"] == sm.STATE_EXECUTION_PENDING


def test_expected_generation_guards_claim_and_reset_clears_history(tmp_path) -> None:
    path = _row_path(tmp_path)
    sm.reset_loop_for_new_cycle(path, generation=1, **_args())
    claimed = sm.claim_loop(
        path,
        operation="review",
        owner_thread="reviewer",
        expected_generation=1,
        **_args(),
    )
    assert claimed is not None
    sm.complete_loop(
        path,
        operation="review",
        outcome="success",
        owner_thread="reviewer",
        **_args(),
    )
    sm.enqueue_execution(path, **_args())
    sm.claim_loop(path, operation="broadcast", owner_thread="broadcaster", **_args())
    sm.complete_loop(
        path,
        operation="broadcast",
        outcome="success",
        owner_thread="broadcaster",
        tx_hash="0x" + "b" * 64,
        **_args(),
    )

    reset = sm.reset_loop_for_new_cycle(path, generation=2, **_args())
    assert reset["state"] == sm.STATE_OFFCHAIN_ESTIMATE
    assert reset["generation"] == 2
    assert reset["tx_hash"] is None
    assert reset["owner_thread"] is None
    assert (
        sm.claim_loop(
            path,
            operation="review",
            owner_thread="reviewer",
            expected_generation=1,
            **_args(),
        )
        is None
    )
    assert (
        sm.claim_loop(
            path,
            operation="review",
            owner_thread="reviewer",
            expected_generation=2,
            **_args(),
        )
        is not None
    )


def test_invalid_operations_and_outcomes_raise(tmp_path) -> None:
    path = _row_path(tmp_path)
    sm.upsert_loop_state(path, state=sm.STATE_OFFCHAIN_ESTIMATE, **_args())

    with pytest.raises(ValueError):
        sm.claim_loop(path, operation="unknown", owner_thread="worker", **_args())
    with pytest.raises(ValueError):
        sm.complete_loop(
            path,
            operation="broadcast",
            outcome="unknown",
            owner_thread="worker",
            **_args(),
        )
    with pytest.raises(ValueError):
        sm.release_loop(path, operation="unknown", owner_thread="worker", **_args())


def test_resolve_max_workers_bounds_and_default(monkeypatch) -> None:
    monkeypatch.delenv("DEX_CLOSED_LOOP_MAX_WORKERS", raising=False)
    assert sm.resolve_max_workers() == 16
    assert sm.resolve_max_workers(None) == 16
    assert sm.resolve_max_workers(0) == 1
    assert sm.resolve_max_workers(1) == 1
    assert sm.resolve_max_workers(16) == 16
    assert sm.resolve_max_workers(32) == 32
    assert sm.resolve_max_workers(100) == 32

    monkeypatch.setenv("DEX_CLOSED_LOOP_MAX_WORKERS", "2")
    assert sm.resolve_max_workers() == 2
    monkeypatch.setenv("DEX_CLOSED_LOOP_MAX_WORKERS", "not-a-number")
    assert sm.resolve_max_workers() == 16


def test_reclaim_expired_review_claims_only_reclaims_stale_loop(tmp_path) -> None:
    path = _row_path(tmp_path)
    sm.upsert_loop_state(path, state=sm.STATE_OFFCHAIN_ESTIMATE, **_args())
    sm.claim_loop(path, operation="review", owner_thread="reviewer-a", **_args())
    other_args = {
        **_args(),
        "loop_key": "td|te|tf",
    }
    sm.upsert_loop_state(path, state=sm.STATE_OFFCHAIN_ESTIMATE, **other_args)
    sm.claim_loop(path, operation="review", owner_thread="reviewer-b", **other_args)

    sm.execute(
        path,
        f"UPDATE {sm.V4_CLOSED_LOOP_RUNTIME_STATE_TABLE} SET updated_at = ? WHERE loop_key = ?",
        ("2020-01-01T00:00:00Z", "ta|tb|tc"),
    )
    sm.execute(
        path,
        f"UPDATE {sm.V4_CLOSED_LOOP_RUNTIME_STATE_TABLE} SET updated_at = ? WHERE loop_key = ?",
        ("2099-01-01T00:00:00Z", "td|te|tf"),
    )

    reclaimed = sm.reclaim_expired_review_claims(path, max_age_seconds=0)
    assert len(reclaimed) == 1
    assert reclaimed[0]["loop_key"] == "ta|tb|tc"
    assert reclaimed[0]["disposition"] == "review_lease_expired_reclaimed"
    row = sm.load_loop_state(path, **_args())
    assert row["state"] == sm.STATE_OFFCHAIN_ESTIMATE
    assert row["owner_thread"] is None

    other = sm.load_loop_state(path, **other_args)
    assert other["state"] == sm.STATE_PREFLIGHT_RUNNING
    assert other["owner_thread"] == "reviewer-b"


def test_fail_stuck_execution_loops_terminates_missing_broadcast(tmp_path) -> None:
    path = _row_path(tmp_path)
    sm.upsert_loop_state(path, state=sm.STATE_EXECUTION_PENDING, generation=1, **_args())
    other_args = {
        **_args(),
        "loop_key": "td|te|tf",
    }
    sm.upsert_loop_state(path, state=sm.STATE_EXECUTION_SUBMITTED, generation=1, **other_args)

    sm.execute(
        path,
        f"UPDATE {sm.V4_CLOSED_LOOP_RUNTIME_STATE_TABLE} SET updated_at = ? WHERE loop_key = ?",
        ("2020-01-01T00:00:00Z", "ta|tb|tc"),
    )
    sm.execute(
        path,
        f"UPDATE {sm.V4_CLOSED_LOOP_RUNTIME_STATE_TABLE} SET updated_at = ? WHERE loop_key = ?",
        ("2020-01-01T00:00:00Z", "td|te|tf"),
    )

    records = sm.fail_stuck_execution_loops(path, max_age_seconds=0)

    assert len(records) == 2
    assert all(record["new_state"] == sm.STATE_EXECUTION_FAILED for record in records)
    assert all(record["last_error"] == "broadcast_result_missing" for record in records)
    row = sm.load_loop_state(path, **_args())
    assert row["state"] == sm.STATE_EXECUTION_FAILED
    assert row["last_error"] == "broadcast_result_missing"
    assert row["finished_at"] is not None


def test_fail_stuck_execution_loop_single_and_fresh_pending_is_not_failed(tmp_path) -> None:
    path = _row_path(tmp_path)
    sm.upsert_loop_state(path, state=sm.STATE_EXECUTION_PENDING, generation=1, **_args())
    fresh = sm.fail_stuck_execution_loop(path, max_age_seconds=300, **_args())
    assert fresh is None
    row = sm.load_loop_state(path, **_args())
    assert row["state"] == sm.STATE_EXECUTION_PENDING

    sm.execute(
        path,
        f"UPDATE {sm.V4_CLOSED_LOOP_RUNTIME_STATE_TABLE} SET updated_at = ? WHERE loop_key = ?",
        ("2020-01-01T00:00:00Z", "ta|tb|tc"),
    )
    stuck = sm.fail_stuck_execution_loop(path, max_age_seconds=0, **_args())
    assert stuck is not None
    assert stuck["new_state"] == sm.STATE_EXECUTION_FAILED
    row = sm.load_loop_state(path, **_args())
    assert row["state"] == sm.STATE_EXECUTION_FAILED
    assert row["last_error"] == "broadcast_result_missing"


def test_reclaim_expired_broadcast_claims_only_reclaims_stale_loop(tmp_path) -> None:
    path = _row_path(tmp_path)
    sm.upsert_loop_state(path, state=sm.STATE_PREFLIGHT_DONE, **_args())
    sm.enqueue_execution(path, **_args())
    sm.claim_loop(path, operation="broadcast", owner_thread="broadcaster-a", **_args())
    other_args = {
        **_args(),
        "loop_key": "td|te|tf",
    }
    sm.upsert_loop_state(path, state=sm.STATE_PREFLIGHT_DONE, **other_args)
    sm.enqueue_execution(path, **other_args)
    sm.claim_loop(path, operation="broadcast", owner_thread="broadcaster-b", **other_args)

    sm.execute(
        path,
        f"UPDATE {sm.V4_CLOSED_LOOP_RUNTIME_STATE_TABLE} SET updated_at = ? WHERE loop_key = ?",
        ("2020-01-01T00:00:00Z", "ta|tb|tc"),
    )
    sm.execute(
        path,
        f"UPDATE {sm.V4_CLOSED_LOOP_RUNTIME_STATE_TABLE} SET updated_at = ? WHERE loop_key = ?",
        ("2099-01-01T00:00:00Z", "td|te|tf"),
    )

    reclaimed = sm.reclaim_expired_broadcast_claims(path, max_age_seconds=0)
    assert len(reclaimed) == 1
    assert reclaimed[0]["loop_key"] == "ta|tb|tc"
    assert reclaimed[0]["disposition"] == "broadcast_lease_expired_reclaimed"
    row = sm.load_loop_state(path, **_args())
    assert row["state"] == sm.STATE_EXECUTION_PENDING
    assert row["owner_thread"] is None

    other = sm.load_loop_state(path, **other_args)
    assert other["state"] == sm.STATE_EXECUTION_SUBMITTED
    assert other["owner_thread"] == "broadcaster-b"


def test_claim_complete_populates_and_clears_cycle_lease(tmp_path) -> None:
    path = _row_path(tmp_path)
    sm.upsert_loop_state(path, state=sm.STATE_OFFCHAIN_ESTIMATE, **_args())

    claimed = sm.claim_loop(
        path,
        operation="review",
        owner_thread="reviewer",
        **_args(),
    )
    assert claimed is not None
    assert claimed["review_cycle_id"].startswith("review:")
    assert claimed["claimed_at"] is not None
    assert claimed["heartbeat_at"] is not None
    assert claimed["lease_until"] > claimed["claimed_at"]
    assert claimed["finished_at"] is None

    completed = sm.complete_loop(
        path,
        operation="review",
        outcome="success",
        owner_thread="reviewer",
        **_args(),
    )
    assert completed is not None
    assert completed["state"] == sm.STATE_PREFLIGHT_DONE
    assert completed["owner_thread"] is None
    assert completed["review_cycle_id"] is None
    assert completed["claimed_at"] is None
    assert completed["heartbeat_at"] is None
    assert completed["lease_until"] is None
    assert completed["finished_at"] is not None


def test_heartbeat_loop_requires_owner_and_matching_cycle(tmp_path) -> None:
    path = _row_path(tmp_path)
    sm.upsert_loop_state(path, state=sm.STATE_OFFCHAIN_ESTIMATE, **_args())
    claimed = sm.claim_loop(
        path,
        operation="review",
        owner_thread="reviewer",
        **_args(),
    )
    assert claimed is not None
    cycle_id = claimed["review_cycle_id"]

    assert (
        sm.heartbeat_loop(
            path,
            operation="review",
            owner_thread="reviewer",
            expected_cycle_id=cycle_id,
            **_args(),
        )
        is not None
    )
    assert (
        sm.heartbeat_loop(
            path,
            operation="review",
            owner_thread="intruder",
            expected_cycle_id=cycle_id,
            **_args(),
        )
        is None
    )
    assert (
        sm.heartbeat_loop(
            path,
            operation="review",
            owner_thread="reviewer",
            expected_cycle_id="review:stale-cycle",
            **_args(),
        )
        is None
    )


def test_reclaim_clears_lease_fields_for_stale_loop_only(tmp_path) -> None:
    path = _row_path(tmp_path)
    sm.upsert_loop_state(path, state=sm.STATE_OFFCHAIN_ESTIMATE, **_args())
    sm.claim_loop(path, operation="review", owner_thread="reviewer-a", **_args())
    other_args = {
        **_args(),
        "loop_key": "td|te|tf",
    }
    sm.upsert_loop_state(path, state=sm.STATE_OFFCHAIN_ESTIMATE, **other_args)
    sm.claim_loop(path, operation="review", owner_thread="reviewer-b", **other_args)

    sm.execute(
        path,
        f"UPDATE {sm.V4_CLOSED_LOOP_RUNTIME_STATE_TABLE} SET updated_at = ? WHERE loop_key = ?",
        ("2020-01-01T00:00:00Z", "ta|tb|tc"),
    )
    sm.execute(
        path,
        f"UPDATE {sm.V4_CLOSED_LOOP_RUNTIME_STATE_TABLE} SET updated_at = ? WHERE loop_key = ?",
        ("2099-01-01T00:00:00Z", "td|te|tf"),
    )

    sm.reclaim_expired_review_claims(path, max_age_seconds=0)
    row = sm.load_loop_state(path, **_args())
    assert row["state"] == sm.STATE_OFFCHAIN_ESTIMATE
    assert row["owner_thread"] is None
    assert row["review_cycle_id"] is None
    assert row["claimed_at"] is None
    assert row["lease_until"] is None
    assert row["finished_at"] is None

    other = sm.load_loop_state(path, **other_args)
    assert other["state"] == sm.STATE_PREFLIGHT_RUNNING
    assert other["owner_thread"] == "reviewer-b"
    assert other["review_cycle_id"] is not None


def test_upsert_conflict_preserves_running_claim_unless_clear_cycle(tmp_path) -> None:
    path = _row_path(tmp_path)
    sm.upsert_loop_state(path, state=sm.STATE_OFFCHAIN_ESTIMATE, **_args())
    claimed = sm.claim_loop(
        path,
        operation="review",
        owner_thread="reviewer",
        **_args(),
    )
    assert claimed is not None

    overwritten = sm.upsert_loop_state(
        path,
        state=sm.STATE_OFFCHAIN_ESTIMATE,
        generation=1,
        owner_thread="scanner",
        **_args(),
    )
    assert overwritten["review_cycle_id"] is not None
    assert overwritten["lease_until"] is not None
    assert overwritten["owner_thread"] == "scanner"

    cleared = sm.upsert_loop_state(
        path,
        state=sm.STATE_OFFCHAIN_ESTIMATE,
        generation=2,
        owner_thread=None,
        clear_cycle=True,
        **_args(),
    )
    assert cleared["generation"] == 2
    assert cleared["review_cycle_id"] is None
    assert cleared["claimed_at"] is None
    assert cleared["lease_until"] is None


def test_runtime_state_migration_adds_cycle_columns_idempotently(tmp_path) -> None:
    path = _row_path(tmp_path)
    sm.execute(
        path,
        f"""
        CREATE TABLE {sm.V4_CLOSED_LOOP_RUNTIME_STATE_TABLE} (
            network TEXT NOT NULL,
            chain_id INTEGER NOT NULL,
            protocol TEXT NOT NULL,
            market_id TEXT NOT NULL,
            loop_key TEXT NOT NULL,
            state TEXT NOT NULL,
            generation INTEGER NOT NULL DEFAULT 1,
            owner_thread TEXT,
            tx_hash TEXT,
            last_error TEXT,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (network, chain_id, protocol, market_id, loop_key)
        )
        """,
    )
    expected_columns = {
        "review_cycle_id",
        "claimed_at",
        "heartbeat_at",
        "lease_until",
        "finished_at",
        "persisted_at",
    }

    sm.ensure_runtime_state_table(path)
    columns = {
        str(row["name"])
        for row in sm.query(
            path,
            f"PRAGMA table_info({sm.V4_CLOSED_LOOP_RUNTIME_STATE_TABLE})",
        )
    }
    assert expected_columns.issubset(columns)

    sm.ensure_runtime_state_table(path)
    columns_again = {
        str(row["name"])
        for row in sm.query(
            path,
            f"PRAGMA table_info({sm.V4_CLOSED_LOOP_RUNTIME_STATE_TABLE})",
        )
    }
    assert columns_again == columns
    row = sm.upsert_loop_state(
        path,
        state=sm.STATE_OFFCHAIN_ESTIMATE,
        **_args(),
    )
    assert row is not None
    assert row["review_cycle_id"] is None


def test_prune_legacy_unresolved_runtime_state_removes_orphans_only(tmp_path) -> None:
    path = _row_path(tmp_path)
    orphan_args = {
        **_args(),
        "loop_key": "unresolved:0x0aaa|spy|unresolved:0x0bbb",
    }
    matched_args = {
        **_args(),
        "loop_key": "unresolved:0x0ccc|spy|unresolved:0x0ddd",
    }
    sm.upsert_loop_state(
        path,
        state=sm.STATE_OFFCHAIN_ESTIMATE,
        generation=1,
        **orphan_args,
    )
    sm.upsert_loop_state(
        path,
        state=sm.STATE_OFFCHAIN_ESTIMATE,
        generation=1,
        **matched_args,
    )
    sm.upsert_loop_state(
        path,
        state=sm.STATE_OFFCHAIN_ESTIMATE,
        generation=1,
        **_args(),
    )

    sm.execute(
        path,
        f"""
        CREATE TABLE {sm.V4_CLOSED_LOOPS_TABLE} (
            network TEXT NOT NULL,
            chain_id INTEGER NOT NULL,
            protocol TEXT NOT NULL,
            market_id TEXT NOT NULL,
            loop_key TEXT NOT NULL,
            PRIMARY KEY (network, chain_id, protocol, market_id, loop_key)
        )
        """,
    )
    sm.execute(
        path,
        f"""
        INSERT INTO {sm.V4_CLOSED_LOOPS_TABLE} (
            network, chain_id, protocol, market_id, loop_key
        ) VALUES (?, ?, ?, ?, ?)
        """,
        (
            str(matched_args["network"]).lower(),
            int(matched_args["chain_id"]),
            str(matched_args["protocol"]).lower(),
            str(matched_args["market_id"]).lower(),
            str(matched_args["loop_key"]).lower(),
        ),
    )

    result = sm.prune_legacy_unresolved_runtime_state(
        path,
        network=str(_args()["network"]),
        chain_id=int(_args()["chain_id"]),
        protocol=str(_args()["protocol"]),
        market_id=str(_args()["market_id"]),
    )

    assert result["deleted"] == 1
    assert result["matchedClosedLoopCount"] == 1
    assert sm.load_loop_state(path, **orphan_args) is None
    assert sm.load_loop_state(path, **matched_args) is not None
    assert sm.load_loop_state(path, **_args()) is not None


def test_prune_legacy_unresolved_runtime_state_without_closed_loops_table(tmp_path) -> None:
    path = _row_path(tmp_path)
    unresolved_args = {
        **_args(),
        "loop_key": "unresolved:0x0aaa|spy|unresolved:0x0bbb",
    }
    sm.upsert_loop_state(
        path,
        state=sm.STATE_OFFCHAIN_ESTIMATE,
        generation=1,
        **unresolved_args,
    )

    result = sm.prune_legacy_unresolved_runtime_state(path)

    assert result["deleted"] == 1
    assert result["matchedClosedLoopCount"] == 0
    assert sm.load_loop_state(path, **unresolved_args) is None


def test_prune_orphan_runtime_state_uses_exact_physical_key_only(tmp_path) -> None:
    path = _row_path(tmp_path)
    valid_args = {
        **_args(),
        "loop_key": FORWARD_LOOP_KEY,
    }
    legacy_symbol_args = {
        **_args(),
        "loop_key": "spy|tsla|usdg",
    }
    orphan_args = {
        **_args(),
        "loop_key": "weth|spy|usdg",
    }
    preserved_args = {
        **_args(),
        "loop_key": "nvda|spy|usdg",
    }
    for args in (valid_args, legacy_symbol_args, orphan_args):
        sm.upsert_loop_state(
            path,
            state=sm.STATE_OFFCHAIN_ESTIMATE,
            generation=1,
            **args,
        )
    sm.upsert_loop_state(
        path,
        state=sm.STATE_EXECUTION_SUCCESS,
        generation=1,
        **preserved_args,
    )

    sm.execute(
        path,
        f"""
        CREATE TABLE {sm.V4_CLOSED_LOOPS_TABLE} (
            network TEXT NOT NULL,
            chain_id INTEGER NOT NULL,
            protocol TEXT NOT NULL,
            market_id TEXT NOT NULL,
            loop_key TEXT NOT NULL,
            path_addresses_json TEXT,
            path_symbols_json TEXT,
            PRIMARY KEY (network, chain_id, protocol, market_id, loop_key)
        )
        """,
    )
    sm.execute(
        path,
        f"""
        INSERT INTO {sm.V4_CLOSED_LOOPS_TABLE} (
            network, chain_id, protocol, market_id, loop_key,
            path_addresses_json, path_symbols_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "robinhood-testnet",
            46630,
            "uniswap_v4",
            "dex-arbitrage-46630-uniswap-v4",
            FORWARD_LOOP_KEY,
            json.dumps(
                [ANCHOR_ADDRESS, Y_ADDRESS, Z_ADDRESS, ANCHOR_ADDRESS]
            ),
            json.dumps(["SPY", "TSLA", "USDG", "SPY"]),
        ),
    )

    result = sm.prune_orphan_runtime_state(
        path,
        network="robinhood-testnet",
        chain_id=46630,
        protocol="uniswap_v4",
        market_id="dex-arbitrage-46630-uniswap_v4",
    )

    assert result["deleted"] == 1
    assert result["remaining"] == 3
    assert result["deletedCount"] == 1
    assert result["matchedClosedLoopCount"] == 1
    assert result["preservedExecutionStateCount"] == 1
    assert result["legacySymbolIdentityPreservedCount"] == 1
    assert result["records"][0]["loop_key"] == "weth|spy|usdg"
    dispositions = {
        record["loop_key"]: record["disposition"]
        for record in result["preservedRecords"]
    }
    assert dispositions["spy|tsla|usdg"] == (
        "legacy_symbol_identity_pending_migration"
    )
    assert dispositions["nvda|spy|usdg"] == "orphan_execution_state_preserved"
    assert sm.load_loop_state(path, **valid_args) is not None
    assert sm.load_loop_state(path, **legacy_symbol_args) is not None
    assert sm.load_loop_state(path, **orphan_args) is None
    assert sm.load_loop_state(path, **preserved_args) is not None


def test_prune_orphan_runtime_state_accepts_legacy_market_underscore(tmp_path) -> None:
    path = _row_path(tmp_path)
    args = {
        "network": "robinhood-testnet",
        "chain_id": 46630,
        "protocol": "uniswap_v4",
        "market_id": "dex-arbitrage-46630-uniswap_v4",
        "loop_key": FORWARD_LOOP_KEY,
    }
    sm.upsert_loop_state(
        path,
        state=sm.STATE_OFFCHAIN_ESTIMATE,
        generation=1,
        **args,
    )
    sm.execute(
        path,
        f"""
        CREATE TABLE {sm.V4_CLOSED_LOOPS_TABLE} (
            network TEXT NOT NULL,
            chain_id INTEGER NOT NULL,
            protocol TEXT NOT NULL,
            market_id TEXT NOT NULL,
            loop_key TEXT NOT NULL,
            path_addresses_json TEXT,
            path_symbols_json TEXT,
            PRIMARY KEY (network, chain_id, protocol, market_id, loop_key)
        )
        """,
    )
    sm.execute(
        path,
        f"""
        INSERT INTO {sm.V4_CLOSED_LOOPS_TABLE} (
            network, chain_id, protocol, market_id, loop_key,
            path_addresses_json, path_symbols_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "robinhood-testnet",
            46630,
            "uniswap_v4",
            "dex-arbitrage-46630-uniswap-v4",
            FORWARD_LOOP_KEY,
            json.dumps(
                [ANCHOR_ADDRESS, Y_ADDRESS, Z_ADDRESS, ANCHOR_ADDRESS]
            ),
            json.dumps(["SPY", "TSLA", "USDG", "SPY"]),
        ),
    )

    # The state row uses the legacy underscore market id; the current loop row
    # uses the canonical dash market id. Both must resolve to one scope.
    result = sm.prune_orphan_runtime_state(
        path,
        network="robinhood-testnet",
        chain_id=46630,
        protocol="uniswap_v4",
        market_id="dex-arbitrage-46630-uniswap-v4",
    )

    assert result["deleted"] == 0
    assert result["remaining"] == 1
    assert sm.load_loop_state(path, **args) is not None


def test_prune_orphan_runtime_state_never_crosses_scope(tmp_path) -> None:
    path = _row_path(tmp_path)
    testnet_args = {
        **_args(),
        "loop_key": FORWARD_LOOP_KEY,
    }
    mainnet_args = {
        **_args(),
        "network": "robinhood",
        "chain_id": 4663,
        "market_id": "dex-arbitrage-4663-uniswap-v4",
        "loop_key": FORWARD_LOOP_KEY,
    }
    for args in (testnet_args, mainnet_args):
        sm.upsert_loop_state(
            path,
            state=sm.STATE_OFFCHAIN_ESTIMATE,
            generation=1,
            **args,
        )
    sm.execute(
        path,
        f"""
        CREATE TABLE {sm.V4_CLOSED_LOOPS_TABLE} (
            network TEXT NOT NULL,
            chain_id INTEGER NOT NULL,
            protocol TEXT NOT NULL,
            market_id TEXT NOT NULL,
            loop_key TEXT NOT NULL,
            path_addresses_json TEXT,
            path_symbols_json TEXT,
            PRIMARY KEY (network, chain_id, protocol, market_id, loop_key)
        )
        """,
    )
    sm.execute(
        path,
        f"""
        INSERT INTO {sm.V4_CLOSED_LOOPS_TABLE} (
            network, chain_id, protocol, market_id, loop_key,
            path_addresses_json, path_symbols_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "robinhood-testnet",
            46630,
            "uniswap_v4",
            "dex-arbitrage-46630-uniswap-v4",
            FORWARD_LOOP_KEY,
            json.dumps(
                [ANCHOR_ADDRESS, Y_ADDRESS, Z_ADDRESS, ANCHOR_ADDRESS]
            ),
            json.dumps(["SPY", "TSLA", "USDG", "SPY"]),
        ),
    )

    result = sm.prune_orphan_runtime_state(
        path,
        network="robinhood-testnet",
        chain_id=46630,
        protocol="uniswap_v4",
        market_id="dex-arbitrage-46630-uniswap-v4",
    )

    assert result["deleted"] == 0
    assert result["matchedClosedLoopCount"] == 1
    assert sm.load_loop_state(path, **testnet_args) is not None
    assert sm.load_loop_state(path, **mainnet_args) is not None


def test_canonicalize_runtime_state_market_ids_migrates_legacy_key(tmp_path) -> None:
    path = _row_path(tmp_path)
    legacy_args = {
        **_args(),
        "market_id": "dex-arbitrage-46630-uniswap_v4",
    }
    sm.upsert_loop_state(
        path,
        state=sm.STATE_PREFLIGHT_DONE,
        generation=7,
        **legacy_args,
    )

    result = sm.canonicalize_runtime_state_market_ids(
        path,
        network="robinhood-testnet",
        chain_id=46630,
        protocol="uniswap_v4",
        market_id="dex-arbitrage-46630-uniswap-v4",
    )

    assert result["ok"] is True
    assert result["updated"] == 1
    assert result["conflicts"] == 0
    assert sm.load_loop_state(path, **legacy_args) is None
    canonical = sm.load_loop_state(path, **_args())
    assert canonical is not None
    assert canonical["state"] == sm.STATE_PREFLIGHT_DONE
    assert canonical["generation"] == 7

    repeated = sm.canonicalize_runtime_state_market_ids(
        path,
        network="robinhood-testnet",
        chain_id=46630,
        protocol="uniswap_v4",
        market_id="dex-arbitrage-46630-uniswap-v4",
    )
    assert repeated["updated"] == 0
    assert repeated["examined"] == 1


def test_backfill_runtime_state_creates_one_row_per_directed_loop(tmp_path) -> None:
    path = _row_path(tmp_path)
    sm.execute(
        path,
        f"""
        CREATE TABLE {sm.V4_CLOSED_LOOPS_TABLE} (
            network TEXT NOT NULL,
            chain_id INTEGER NOT NULL,
            protocol TEXT NOT NULL,
            market_id TEXT NOT NULL,
            loop_key TEXT NOT NULL,
            path_symbols_json TEXT,
            PRIMARY KEY (network, chain_id, protocol, market_id, loop_key)
        )
        """,
    )
    rows = [
        (
            FORWARD_LOOP_KEY,
            json.dumps(["SPY", "TSLA", "USDG", "SPY"]),
        ),
        (
            REVERSE_LOOP_KEY,
            json.dumps(["SPY", "USDG", "TSLA", "SPY"]),
        ),
        (
            "0x1111111111111111111111111111111111111111:"
            "0x4444444444444444444444444444444444444444:"
            "0x5555555555555555555555555555555555555555",
            json.dumps(["SPY", "AMZN", "NVDA", "SPY"]),
        ),
    ]
    for loop_key, symbols_json in rows:
        sm.execute(
            path,
            f"""
            INSERT INTO {sm.V4_CLOSED_LOOPS_TABLE} (
                network, chain_id, protocol, market_id, loop_key,
                path_symbols_json
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                "robinhood-testnet",
                46630,
                "uniswap_v4",
                "dex-arbitrage-46630-uniswap-v4",
                loop_key,
                symbols_json,
            ),
        )
    existing_args = {
        **_args(),
        "loop_key": (
            "0x1111111111111111111111111111111111111111:"
            "0x4444444444444444444444444444444444444444:"
            "0x5555555555555555555555555555555555555555"
        ),
    }
    sm.upsert_loop_state(
        path,
        state=sm.STATE_OFFCHAIN_ESTIMATE,
        generation=4,
        **existing_args,
    )

    result = sm.backfill_runtime_state_for_closed_loops(
        path,
        network="robinhood-testnet",
        chain_id=46630,
        protocol="uniswap_v4",
        market_id="dex-arbitrage-46630-uniswap-v4",
    )

    assert result["runtimeStateCount"] == 3
    assert result["runtimeGroupCount"] == 3
    assert result["created"] == 2
    assert result["existing"] == 1
    forward_created = sm.load_loop_state(
        path,
        **{
            **_args(),
            "loop_key": FORWARD_LOOP_KEY,
        },
    )
    reverse_created = sm.load_loop_state(
        path,
        **{
            **_args(),
            "loop_key": REVERSE_LOOP_KEY,
        },
    )
    assert forward_created is not None
    assert forward_created["state"] == sm.STATE_OFFCHAIN_PENDING
    assert reverse_created is not None
    assert reverse_created["state"] == sm.STATE_OFFCHAIN_PENDING
    existing = sm.load_loop_state(path, **existing_args)
    assert existing is not None
    assert existing["state"] == sm.STATE_OFFCHAIN_ESTIMATE
    assert existing["generation"] == 4


def test_backfill_runtime_state_rejects_duplicate_physical_loop_key(
    tmp_path,
) -> None:
    path = _row_path(tmp_path)
    sm.execute(
        path,
        f"""
        CREATE TABLE {sm.V4_CLOSED_LOOPS_TABLE} (
            network TEXT NOT NULL,
            chain_id INTEGER NOT NULL,
            protocol TEXT NOT NULL,
            market_id TEXT NOT NULL,
            loop_key TEXT NOT NULL,
            path_symbols_json TEXT,
            PRIMARY KEY (network, chain_id, protocol, market_id, loop_key)
        )
        """,
    )
    for loop_key in (FORWARD_LOOP_KEY, FORWARD_LOOP_KEY.replace(":", "|")):
        sm.execute(
            path,
            f"""
            INSERT INTO {sm.V4_CLOSED_LOOPS_TABLE} (
                network, chain_id, protocol, market_id, loop_key,
                path_symbols_json
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                "robinhood-testnet",
                46630,
                "uniswap_v4",
                "dex-arbitrage-46630-uniswap-v4",
                loop_key,
                "[]",
            ),
        )

    result = sm.backfill_runtime_state_for_closed_loops(path)

    assert result["ok"] is False
    assert result["duplicateDirectedKey"] == 1
    assert result["disposition"] == "duplicate_directed_closed_loop_key"
