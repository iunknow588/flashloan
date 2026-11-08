"""Tests for the object-oriented closed-loop state machine layer."""

from __future__ import annotations

import pytest

from dex.runtime import closed_loop_state_machine as sm
from dex.runtime.closed_loop_object import (
    ClosedLoopIdentity,
    ClosedLoopStateMachine,
    LoopState,
    TransitionError,
)


def _identity(**overrides: object) -> ClosedLoopIdentity:
    payload = {
        "network": "robinhood-testnet",
        "chain_id": 46630,
        "protocol": "uniswap_v4",
        "market_id": "dex-arbitrage-46630-uniswap-v4",
        "loop_key": "ta|tb|tc",
    }
    payload.update(overrides)
    return ClosedLoopIdentity(**payload)


def _machine(tmp_path) -> ClosedLoopStateMachine:
    machine = ClosedLoopStateMachine(str(tmp_path / "closed_loop_object.sqlite3"))
    machine.ensure_table()
    return machine


def test_loop_state_enum_aligns_with_sqlite_strings() -> None:
    assert str(LoopState.PREFLIGHT_DONE) == "preflight_done"
    assert str(LoopState.EXECUTION_NOT_REQUESTED) == "execution_not_requested"
    assert LoopState.from_value("execution_not_requested") == LoopState.EXECUTION_NOT_REQUESTED
    assert LoopState.from_value(LoopState.EXECUTION_SUCCESS) == LoopState.EXECUTION_SUCCESS
    assert LoopState.from_value("not-a-state") is None
    assert LoopState.EXECUTION_NOT_REQUESTED.terminal_cycle is True
    assert LoopState.EXECUTION_NOT_REQUESTED.terminal_execution is False
    assert LoopState.PREFLIGHT_DONE.terminal_cycle is False


def test_identity_params_are_stable() -> None:
    ident = _identity()
    params = ident.params()
    assert params["network"] == "robinhood-testnet"
    assert params["chain_id"] == 46630
    assert "loop_key" in params


def test_closed_loop_object_owns_state_and_transition(tmp_path) -> None:
    machine = _machine(tmp_path)
    ident = _identity()
    loop = machine.upsert(ident, state=LoopState.OFFCHAIN_ESTIMATE, generation=1)
    assert loop.state == LoopState.OFFCHAIN_ESTIMATE

    claimed = loop.claim(operation="review", owner_thread="reviewer")
    assert claimed is not None
    assert claimed.state == LoopState.PREFLIGHT_RUNNING
    assert claimed.to_dict()["owner_thread"] == "reviewer"

    completed = claimed.complete(
        operation="review",
        outcome="success",
        owner_thread="reviewer",
    )
    assert completed is not None
    assert completed.state == LoopState.PREFLIGHT_DONE

    closed = completed.mark_execution_not_requested()
    assert closed is not None
    assert closed.state == LoopState.EXECUTION_NOT_REQUESTED
    assert closed.row["last_error"] == "execution_not_requested"
    assert closed.row["finished_at"] is not None

    reloaded = machine.load(ident)
    assert reloaded is not None
    assert reloaded.state == LoopState.EXECUTION_NOT_REQUESTED
    assert completed.mark_execution_not_requested() is None


def test_illegal_transition_raises_and_keeps_row_unchanged(tmp_path) -> None:
    machine = _machine(tmp_path)
    ident = _identity()
    loop = machine.upsert(ident, state=LoopState.OFFCHAIN_ESTIMATE, generation=1)

    with pytest.raises(TransitionError):
        loop.transition(LoopState.EXECUTION_SUCCESS)

    row = machine.load(ident)
    assert row is not None
    assert row.state == LoopState.OFFCHAIN_ESTIMATE


def test_machine_broadcast_path_terminates_with_tx_hash(tmp_path) -> None:
    machine = _machine(tmp_path)
    ident = _identity()
    loop = machine.upsert(ident, state=LoopState.OFFCHAIN_ESTIMATE, generation=1)

    claimed = loop.claim(operation="review", owner_thread="reviewer", expected_generation=1)
    assert claimed is not None
    done = claimed.complete(
        operation="review",
        outcome="success",
        owner_thread="reviewer",
    )
    assert done is not None
    queued = done.enqueue_execution(expected_generation=1)
    assert queued is not None
    assert queued.state == LoopState.EXECUTION_PENDING

    broadcaster = queued.claim(
        operation="broadcast",
        owner_thread="broadcaster",
        expected_generation=1,
    )
    assert broadcaster is not None
    terminal = broadcaster.complete(
        operation="broadcast",
        outcome="success",
        owner_thread="broadcaster",
        tx_hash="0x" + "a" * 64,
    )
    assert terminal is not None
    assert terminal.state == LoopState.EXECUTION_SUCCESS
    assert terminal.row["tx_hash"] == "0x" + "a" * 64


def test_machine_close_stale_review_only_loops_is_per_loop(tmp_path) -> None:
    machine = _machine(tmp_path)
    stale = _identity()
    fresh = _identity(loop_key="td|te|tf")
    machine.upsert(stale, state=LoopState.PREFLIGHT_DONE, generation=1)
    machine.upsert(fresh, state=LoopState.PREFLIGHT_DONE, generation=1)
    sm.execute(
        machine.database_url,
        f"UPDATE {sm.V4_CLOSED_LOOP_RUNTIME_STATE_TABLE} SET updated_at = ? WHERE loop_key = ?",
        ("2020-01-01T00:00:00Z", stale.loop_key),
    )
    sm.execute(
        machine.database_url,
        f"UPDATE {sm.V4_CLOSED_LOOP_RUNTIME_STATE_TABLE} SET updated_at = ? WHERE loop_key = ?",
        ("2099-01-01T00:00:00Z", fresh.loop_key),
    )

    records = machine.close_stale_review_only_loops(max_age_seconds=0)
    assert len(records) == 1
    assert records[0]["loop_key"] == stale.loop_key
    assert records[0]["new_state"] == sm.STATE_EXECUTION_NOT_REQUESTED

    stale_loop = machine.load(stale)
    fresh_loop = machine.load(fresh)
    assert stale_loop is not None
    assert stale_loop.state == LoopState.EXECUTION_NOT_REQUESTED
    assert fresh_loop is not None
    assert fresh_loop.state == LoopState.PREFLIGHT_DONE


def test_machine_recover_stuck_execution_loops_closes_both_terminals(tmp_path) -> None:
    machine = _machine(tmp_path)
    broadcast_loop = _identity(loop_key="tx|ty|tz")
    review_only = _identity()
    machine.upsert(broadcast_loop, state=LoopState.EXECUTION_PENDING, generation=1)
    machine.upsert(review_only, state=LoopState.PREFLIGHT_DONE, generation=1)
    sm.execute(
        machine.database_url,
        f"UPDATE {sm.V4_CLOSED_LOOP_RUNTIME_STATE_TABLE} SET updated_at = ? WHERE loop_key = ?",
        ("2020-01-01T00:00:00Z", broadcast_loop.loop_key),
    )
    sm.execute(
        machine.database_url,
        f"UPDATE {sm.V4_CLOSED_LOOP_RUNTIME_STATE_TABLE} SET updated_at = ? WHERE loop_key = ?",
        ("2020-01-01T00:00:00Z", review_only.loop_key),
    )

    result = machine.recover_stuck_execution_loops(max_age_seconds=0)
    assert result["failed"] == 1
    assert result["reviewOnlyClosed"] == 1
    assert result["recovered"] == 2
    assert len(result["records"]) == 2


def test_machine_recover_reclaims_expired_review_and_broadcast_leases(tmp_path) -> None:
    machine = _machine(tmp_path)
    stale_review = _identity(loop_key="stale|review|loop")
    stale_broadcast = _identity(loop_key="stale|broadcast|loop")
    machine.upsert(
        stale_review,
        state=LoopState.PREFLIGHT_RUNNING,
        generation=1,
        owner_thread="reviewer",
    )
    machine.upsert(
        stale_broadcast,
        state=LoopState.EXECUTION_SUBMITTED,
        generation=1,
        owner_thread="broadcaster",
    )
    sm.execute(
        machine.database_url,
        f"UPDATE {sm.V4_CLOSED_LOOP_RUNTIME_STATE_TABLE} SET updated_at = ? WHERE loop_key = ?",
        ("2020-01-01T00:00:00Z", stale_review.loop_key),
    )
    sm.execute(
        machine.database_url,
        f"UPDATE {sm.V4_CLOSED_LOOP_RUNTIME_STATE_TABLE} SET updated_at = ? WHERE loop_key = ?",
        ("2020-01-01T00:00:00Z", stale_broadcast.loop_key),
    )

    result = machine.recover_stuck_execution_loops(max_age_seconds=0)
    assert result["reviewReclaimed"] == 1
    assert result["broadcastReclaimed"] == 1
    assert result["failed"] == 1
    assert result["recovered"] >= 2

    review_loop = machine.load(stale_review)
    broadcast_loop = machine.load(stale_broadcast)
    assert review_loop is not None
    assert review_loop.state == LoopState.OFFCHAIN_ESTIMATE
    assert broadcast_loop is not None
    assert broadcast_loop.state == LoopState.EXECUTION_FAILED
    assert broadcast_loop.row["last_error"] == "broadcast_result_missing"


def test_machine_recover_prunes_legacy_unresolved_rows(tmp_path) -> None:
    machine = _machine(tmp_path)
    unresolved = _identity(loop_key="unresolved:0x0aaa|spy|unresolved:0x0bbb")
    machine.upsert(
        unresolved,
        state=LoopState.OFFCHAIN_ESTIMATE,
        generation=1,
    )

    result = machine.recover_stuck_execution_loops()

    assert result["unresolvedPruned"] == 1
    assert machine.load(unresolved) is None


def test_runtime_state_summary_counts_review_only_terminal(tmp_path) -> None:
    machine = _machine(tmp_path)
    review_only = _identity()
    success = _identity(loop_key="td|te|tf")
    machine.upsert(review_only, state=LoopState.EXECUTION_NOT_REQUESTED, generation=1)
    machine.upsert(success, state=LoopState.EXECUTION_SUCCESS, generation=1)

    summary = machine.runtime_state_summary()
    assert summary["total"] == 2
    assert summary["executionNotRequested"] == 1
    assert summary["terminalExecution"] == 1
    assert summary["cycleTerminals"] == 2
    assert summary["missingBroadcast"] == 0


def test_runtime_metrics_accumulate_per_loop_counters_in_memory(tmp_path) -> None:
    machine = _machine(tmp_path)
    ident = _identity()
    machine.upsert(ident, state=LoopState.OFFCHAIN_ESTIMATE, generation=1)
    key = ident.loop_key
    metrics = machine.metrics

    metrics.record_attempt(
        key,
        {
            "category": "execution_success",
            "state": "execution_success",
            "tx_hash": "0x" + "ab" * 32,
        },
    )
    metrics.record_attempt(
        key,
        {
            "category": "execution_success",
            "state": "submitted",
            "tx_hash": "0x" + "cd" * 32,
        },
    )
    metrics.record_attempt(
        key,
        {
            "category": "execution_failed",
            "state": "execution_failed",
            "tx_hash": "0x" + "ef" * 32,
        },
    )
    metrics.record_attempt(
        key,
        {"category": "execution_failed", "state": "submission_failed"},
    )

    summary = metrics.summary()
    assert summary["submissionSuccessTotal"] == 1
    assert summary["submissionFailedTotal"] == 1
    assert summary["executionSuccessTotal"] == 1
    assert summary["executionFailedTotal"] == 1
    assert summary["broadcastLoops"] == 1
    assert summary["broadcastSuccessLoops"] == 1
    assert summary["broadcastFailedLoops"] == 1


def test_runtime_metrics_reset_clears_counters_but_keeps_state(tmp_path) -> None:
    machine = _machine(tmp_path)
    ident = _identity()
    machine.upsert(ident, state=LoopState.OFFCHAIN_ESTIMATE, generation=1)
    metrics = machine.metrics
    metrics.record_attempt(
        ident.loop_key,
        {
            "category": "execution_success",
            "state": "execution_success",
            "tx_hash": "0x" + "ab" * 32,
        },
    )

    metrics.reset_counters()
    summary = metrics.summary()
    assert summary["executionSuccessTotal"] == 0
    assert summary["executionFailedTotal"] == 0
    assert summary["submissionSuccessTotal"] == 0
    assert summary["submissionFailedTotal"] == 0
    assert summary["states"] == {"offchain_estimate": 1}

    metrics.record_attempt(
        ident.loop_key,
        {
            "category": "execution_success",
            "state": "execution_success",
            "tx_hash": "0x" + "cd" * 32,
        },
    )
    assert metrics.summary()["executionSuccessTotal"] == 1


def test_reset_starts_fresh_cycle_from_terminal(tmp_path) -> None:
    machine = _machine(tmp_path)
    ident = _identity()
    machine.upsert(ident, state=LoopState.EXECUTION_NOT_REQUESTED, generation=1)

    reset = machine.reset(ident, generation=2)
    assert reset.state == LoopState.OFFCHAIN_ESTIMATE
    assert reset.row["generation"] == 2
    assert reset.row["last_error"] is None
    assert reset.row["finished_at"] is None
