"""Tests for the closed-loop runtime state consumer bridge."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from dex.runtime import closed_loop_consumer as consumer
from dex.runtime import closed_loop_state_machine as sm


ANCHOR_ADDRESS = "0x" + ("1" * 40)
Y_ADDRESS = "0x" + ("2" * 40)
Z_ADDRESS = "0x" + ("3" * 40)
LOOP_KEY = f"{ANCHOR_ADDRESS}:{Y_ADDRESS}:{Z_ADDRESS}"
REVERSE_LOOP_KEY = f"{ANCHOR_ADDRESS}:{Z_ADDRESS}:{Y_ADDRESS}"


def _db(tmp_path) -> str:
    return str(tmp_path / "closed_loop_consumer.sqlite3")


def _args(**overrides: Any) -> dict[str, Any]:
    payload = {
        "network": "robinhood-testnet",
        "chain_id": 46630,
        "protocol": "uniswap_v4",
        "market_id": "dex-arbitrage-46630-uniswap-v4",
        "loop_key": LOOP_KEY,
    }
    payload.update(overrides)
    return payload


def _attempt(
    *,
    executed: bool = True,
    attempted: bool = True,
    queued: bool = False,
    confirmation: str = "confirmed",
    tx_hash: str = "",
    observed_at: str = "",
    route_symbols: list[str] | None = None,
) -> dict[str, Any]:
    auto: dict[str, Any] = {
        "broadcast": True,
        "attempted": attempted,
        "executed": executed,
        "queued": queued,
        "confirmationStatus": confirmation,
    }
    if tx_hash:
        auto["result"] = {
            "txHash": tx_hash,
            "confirmationStatus": confirmation,
        }
    addresses_by_symbol = {
        "AAA": ANCHOR_ADDRESS,
        "BBB": Y_ADDRESS,
        "CCC": Z_ADDRESS,
    }
    route_symbols = route_symbols or ["AAA", "BBB", "CCC", "AAA"]
    route_addresses = [
        addresses_by_symbol[str(symbol).upper()] for symbol in route_symbols
    ]
    attempt = {
        "network": "robinhood-testnet",
        "chain_id": 46630,
        "market_id": "dex-arbitrage-46630-uniswap-v4",
        "route_path": route_symbols,
        "route_addresses": route_addresses,
        "routeAddresses": route_addresses,
        "quote": {
            "live_onchain_evidence": {
                "status": "live_verified",
            }
        },
        "auto_execute": auto,
        "tx_hash": tx_hash,
    }
    if observed_at:
        attempt["observed_at"] = observed_at
    return attempt


def test_claim_review_gates_duplicate_submit(tmp_path) -> None:
    database = _db(tmp_path)
    claimed = consumer.claim_review_before_submit(database, **_args())

    assert claimed is not None
    assert claimed["state"] == sm.STATE_PREFLIGHT_RUNNING
    assert claimed["owner_thread"] == consumer.owner_thread(LOOP_KEY, "review")

    # A second scan must not reset the running claim.
    again = consumer.claim_review_before_submit(database, **_args())
    assert again is not None
    assert again["state"] == sm.STATE_PREFLIGHT_RUNNING

    row = sm.load_loop_state(database, **_args())
    assert row["state"] == sm.STATE_PREFLIGHT_RUNNING


def test_record_state_completes_review_enqueues_and_lands_success(tmp_path) -> None:
    database = _db(tmp_path)
    consumer.claim_review_before_submit(database, **_args())
    tx_hash = "0x" + "a" * 64
    attempt = _attempt(tx_hash=tx_hash)

    summary = consumer.record_state_from_attempts(
        database,
        [attempt],
        loop_key=LOOP_KEY,
    )

    assert summary["loops"] == 1
    assert summary["reviewCompleted"] == 1
    assert summary["executionEnqueued"] == 1
    assert summary["broadcastSuccess"] == 1
    assert summary["duplicateReviews"] == 0
    assert summary["duplicateBroadcasts"] == 0
    row = sm.load_loop_state(database, **_args())
    assert row["state"] == sm.STATE_EXECUTION_SUCCESS
    assert row["tx_hash"] == tx_hash
    assert row["owner_thread"] is None

    # A repeated poll is idempotent.
    repeated = consumer.record_state_from_attempts(
        database,
        [attempt],
        loop_key=LOOP_KEY,
    )
    assert repeated["reviewCompleted"] == 0
    assert repeated["broadcastSuccess"] == 0
    assert repeated["duplicateReviews"] == 0
    assert repeated["duplicateBroadcasts"] == 0


def test_queued_execution_waits_for_broadcast_result(tmp_path) -> None:
    database = _db(tmp_path)
    consumer.claim_review_before_submit(database, **_args())

    queued = consumer.record_state_from_attempts(
        database,
        [_attempt(executed=False, attempted=False, queued=True, confirmation="none")],
        loop_key=LOOP_KEY,
    )
    assert queued["reviewCompleted"] == 1
    assert queued["executionEnqueued"] == 1
    assert queued["broadcastSuccess"] == 0
    row = sm.load_loop_state(database, **_args())
    assert row["state"] == sm.STATE_EXECUTION_PENDING

    tx_hash = "0x" + "b" * 64
    landed = consumer.record_state_from_attempts(
        database,
        [_attempt(tx_hash=tx_hash)],
        loop_key=LOOP_KEY,
    )
    assert landed["executionEnqueued"] == 0
    assert landed["broadcastSuccess"] == 1
    row = sm.load_loop_state(database, **_args())
    assert row["state"] == sm.STATE_EXECUTION_SUCCESS
    assert row["tx_hash"] == tx_hash


def test_rpc_failure_marks_review_failed_and_never_enqueues(tmp_path) -> None:
    database = _db(tmp_path)
    consumer.claim_review_before_submit(database, **_args())
    attempt = _attempt(executed=False, attempted=False, queued=False, confirmation="none")
    attempt["quote"]["live_onchain_evidence"]["status"] = "rpc_verification_failed"

    summary = consumer.record_state_from_attempts(
        database,
        [attempt],
        loop_key=LOOP_KEY,
    )

    assert summary["reviewFailed"] == 1
    assert summary["executionEnqueued"] == 0
    row = sm.load_loop_state(database, **_args())
    assert row["state"] == sm.STATE_PREFLIGHT_FAILED


def test_broadcast_failure_resolves_disposition_and_lands_terminal(tmp_path, monkeypatch) -> None:
    """A failed broadcast must resolve a structured disposition and close."""
    # Keep this unit test from writing a real audit JSONL under runtime evidence.
    monkeypatch.setattr(
        consumer,
        "append_broadcast_failure_audit",
        lambda record: None,
    )
    database = _db(tmp_path)
    consumer.claim_review_before_submit(database, **_args())
    preflight = consumer.record_state_from_attempts(
        database,
        [_attempt(executed=False, attempted=False, queued=True, confirmation="none")],
        loop_key=LOOP_KEY,
    )
    assert preflight["reviewCompleted"] == 1
    assert preflight["executionEnqueued"] == 1
    failed_attempt = _attempt(
        executed=True,
        attempted=True,
        queued=False,
        confirmation="failed",
    )
    failed_attempt["auto_execute"]["result"] = {
        "error": "nonce too low",
        "confirmationStatus": "failed",
    }
    failed_attempt["error"] = "nonce too low"

    summary = consumer.record_state_from_attempts(
        database,
        [failed_attempt],
        loop_key=LOOP_KEY,
    )

    assert summary["broadcastFailed"] == 1
    assert summary["broadcastSuccess"] == 0
    row = sm.load_loop_state(database, **_args())
    assert row["state"] == sm.STATE_EXECUTION_FAILED
    assert row["last_error"] == "nonce too low"

    # The failed loop must not be claimed again on a repeated poll.
    repeated = consumer.record_state_from_attempts(
        database,
        [failed_attempt],
        loop_key=LOOP_KEY,
    )
    assert repeated["duplicateReviews"] == 0
    assert repeated["duplicateBroadcasts"] == 0


def test_attempt_identity_canonicalizes_quad_route_without_looping_key() -> None:
    identity = consumer._attempt_identity(_attempt())
    assert identity[4] == LOOP_KEY


def test_attempt_identity_preserves_reverse_direction_physical_key() -> None:
    attempt = _attempt()
    attempt["route_path"] = ["AAA", "CCC", "BBB", "AAA"]
    addresses_by_symbol = {
        "AAA": ANCHOR_ADDRESS,
        "BBB": Y_ADDRESS,
        "CCC": Z_ADDRESS,
    }
    attempt["route_addresses"] = [
        addresses_by_symbol[item] for item in attempt["route_path"]
    ]
    attempt["routeAddresses"] = attempt["route_addresses"]

    identity = consumer._attempt_identity(attempt)
    assert identity[4] == REVERSE_LOOP_KEY


def test_attempt_identity_ignores_legacy_group_key_when_addresses_exist() -> None:
    attempt = _attempt()
    attempt["loop_key"] = "aaa|bbb|ccc"

    identity = consumer._attempt_identity(attempt)
    assert identity[4] == LOOP_KEY


def test_record_state_maps_both_reverse_directions_to_distinct_loop_keys(
    tmp_path,
) -> None:
    database = _db(tmp_path)
    forward_tx = "0x" + ("a" * 64)
    reverse_tx = "0x" + ("b" * 64)

    summary = consumer.record_state_from_attempts(
        database,
        [
            _attempt(tx_hash=forward_tx),
            _attempt(
                tx_hash=reverse_tx,
                route_symbols=["AAA", "CCC", "BBB", "AAA"],
            ),
        ],
    )

    assert summary["loops"] == 2
    assert summary["reviewCompleted"] == 2
    assert summary["executionEnqueued"] == 2
    assert summary["broadcastSuccess"] == 2
    forward = sm.load_loop_state(
        database,
        **{
            **_args(),
            "loop_key": LOOP_KEY,
        },
    )
    reverse = sm.load_loop_state(
        database,
        **{
            **_args(),
            "loop_key": REVERSE_LOOP_KEY,
        },
    )
    assert forward is not None
    assert reverse is not None
    assert forward["state"] == sm.STATE_EXECUTION_SUCCESS
    assert reverse["state"] == sm.STATE_EXECUTION_SUCCESS
    assert forward["tx_hash"] == forward_tx
    assert reverse["tx_hash"] == reverse_tx


def test_terminal_execution_advances_to_next_generation_only_for_new_window(
    tmp_path,
) -> None:
    database = _db(tmp_path)
    first_tx = "0x" + "a" * 64
    first_observed = (
        datetime.now(UTC) - timedelta(minutes=2)
    ).isoformat(timespec="seconds")
    first = consumer.record_state_from_attempts(
        database,
        [_attempt(tx_hash=first_tx, observed_at=first_observed)],
        loop_key=LOOP_KEY,
    )
    assert first["broadcastSuccess"] == 1
    row = sm.load_loop_state(database, **_args())
    assert row["state"] == sm.STATE_EXECUTION_SUCCESS
    assert row["generation"] == 1

    repeated = consumer.record_state_from_attempts(
        database,
        [_attempt(tx_hash=first_tx, observed_at=first_observed)],
        loop_key=LOOP_KEY,
    )
    assert repeated["reviewCompleted"] == 0
    row = sm.load_loop_state(database, **_args())
    assert row["generation"] == 1

    second_observed = (
        datetime.now(UTC) + timedelta(seconds=5)
    ).isoformat(timespec="seconds")
    second = consumer.record_state_from_attempts(
        database,
        [_attempt(tx_hash="0x" + "b" * 64, observed_at=second_observed)],
        loop_key=LOOP_KEY,
    )
    assert second["loops"] == 1
    assert second["reviewCompleted"] == 1
    assert second["executionEnqueued"] == 1
    assert second["broadcastSuccess"] == 1
    row = sm.load_loop_state(database, **_args())
    assert row["state"] == sm.STATE_EXECUTION_SUCCESS
    assert row["generation"] == 2


def _positive_live_attempt(
    *,
    status: str = "live_verified",
    profit: float = 0.5,
    observed_at: str = "",
) -> dict[str, Any]:
    attempt = _attempt(
        executed=False,
        attempted=False,
        queued=False,
        confirmation="none",
    )
    attempt.pop("auto_execute", None)
    attempt.pop("tx_hash", None)
    evidence = attempt["quote"]["live_onchain_evidence"]
    evidence["status"] = status
    evidence["costModel"] = {"netProfitAmount": profit}
    if observed_at:
        attempt["observed_at"] = observed_at
    return attempt


def test_successful_review_without_execution_marker_enqueues_then_fails_missing(
    tmp_path,
) -> None:
    database = _db(tmp_path)
    attempt = _positive_live_attempt()

    first = consumer.record_state_from_attempts(
        database,
        [attempt],
        loop_key=LOOP_KEY,
    )

    assert first["reviewCompleted"] == 1
    assert first["executionEnqueued"] == 1
    assert first["broadcastMissing"] == 0
    row = sm.load_loop_state(database, **_args())
    assert row["state"] == sm.STATE_EXECUTION_PENDING

    sm.execute(
        database,
        f"UPDATE {sm.V4_CLOSED_LOOP_RUNTIME_STATE_TABLE} SET updated_at = ? WHERE loop_key = ?",
        ("2020-01-01T00:00:00Z", LOOP_KEY),
    )
    second = consumer.record_state_from_attempts(
        database,
        [attempt],
        loop_key=LOOP_KEY,
    )

    assert second["broadcastMissing"] == 1
    assert second["broadcastSuccess"] == 0
    row = sm.load_loop_state(database, **_args())
    assert row["state"] == sm.STATE_EXECUTION_FAILED
    assert row["last_error"] == "broadcast_result_missing"
    isolation = sm.load_error_isolation(
        database,
        network="robinhood-testnet",
        chain_id=46630,
        protocol="uniswap_v4",
        scope_key=LOOP_KEY,
        error_fingerprint=consumer.error_fingerprint(
            {"errorKey": "broadcast_result_missing"}
        ),
    )
    assert isolation is not None
    assert isolation["disposition"] == "CLOSE_AND_QUARANTINE"


def test_live_verified_negative_profit_closes_as_execution_not_requested(tmp_path) -> None:
    database = _db(tmp_path)
    attempt = _positive_live_attempt(profit=-0.5)

    summary = consumer.record_state_from_attempts(
        database,
        [attempt],
        loop_key=LOOP_KEY,
    )

    assert summary["reviewCompleted"] == 1
    assert summary["executionEnqueued"] == 0
    assert summary["executionNotRequested"] == 1
    row = sm.load_loop_state(database, **_args())
    assert row["state"] == sm.STATE_EXECUTION_NOT_REQUESTED
    assert row["last_error"] == "execution_not_requested"
    assert row["finished_at"] is not None


def test_static_reference_pending_live_review_closes_as_execution_not_requested(tmp_path) -> None:
    database = _db(tmp_path)
    attempt = _positive_live_attempt(status="static_reference_pending_live_review")

    summary = consumer.record_state_from_attempts(
        database,
        [attempt],
        loop_key=LOOP_KEY,
    )

    assert summary["reviewCompleted"] == 1
    assert summary["executionEnqueued"] == 0
    assert summary["executionNotRequested"] == 1
    row = sm.load_loop_state(database, **_args())
    assert row["state"] == sm.STATE_EXECUTION_NOT_REQUESTED
