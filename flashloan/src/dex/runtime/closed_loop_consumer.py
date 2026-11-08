"""Bridge native closed-loop results into the persisted runtime state machine.

Rust owns the review and broadcast work.  This module owns the SQLite queue
index at the Python boundary: a group claims review before it is submitted,
then result polling completes review and, when appropriate, claims and
completes broadcast.  The functions are deliberately idempotent so a status
poll or a recovered worker cannot submit the same loop twice.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
import logging
from typing import Any

from core.broadcast_failure_audit import (
    append_broadcast_failure_audit,
    build_auto_disposition_audit_record,
)
from core.market_identity import canonical_market_id, canonical_market_id_from_legacy
from core.db.storage_common import database_url as storage_database_url
from dex.runtime.closed_loop_identity import (
    directed_symbol_key_from_symbols,
    normalize_physical_loop_key,
    physical_loop_key_from_route,
)
from dex.runtime.closed_loop_state_machine import (
    CYCLE_TERMINAL_STATES,
    STATE_EXECUTION_PENDING,
    STATE_EXECUTION_SUBMITTED,
    STATE_EXECUTION_SUCCESS,
    STATE_EXECUTION_FAILED,
    STATE_OFFCHAIN_ESTIMATE,
    STATE_PREFLIGHT_DONE,
    STATE_PREFLIGHT_RUNNING,
    claim_loop,
    complete_loop,
    fail_stuck_execution_loop,
    load_loop_state,
    release_loop,
    upsert_error_isolation,
    upsert_loop_state,
)
from dex.runtime.error_disposition import error_fingerprint, resolve_broadcast_error

logger = logging.getLogger(__name__)

RPC_FAILURE_STATUSES = frozenset(
    {"rpc_verification_failed", "rpc_verification_timeout", "gas_estimation_failed"}
)


def resolve_runtime_database(database_url: str | None = None) -> str | None:
    value = str(database_url or "").strip()
    if value:
        return value
    try:
        resolved = storage_database_url()
    except Exception:
        return None
    return str(resolved or "").strip() or None


def owner_thread(loop_key: str, operation: str) -> str:
    prefix = "review" if operation == "review" else "broadcast"
    return f"rust-native-{prefix}:{str(loop_key or '').strip().lower()}"


def _default_market_id(chain_id: int, protocol: str) -> str:
    return canonical_market_id(
        "dex_arbitrage",
        int(chain_id),
        str(protocol or "uniswap_v4").strip().lower(),
    )


def mark_group_submitted(
    database_url: str | None,
    *,
    network: str,
    chain_id: int,
    protocol: str,
    market_id: str,
    loop_key: str,
    generation: int | None = None,
) -> dict[str, Any] | None:
    """Create/refresh the index row before a Rust group is submitted."""
    database = resolve_runtime_database(database_url)
    if not database:
        return None
    existing = load_loop_state(
        database,
        network=network,
        chain_id=chain_id,
        protocol=protocol,
        market_id=market_id,
        loop_key=loop_key,
    )
    if existing is None:
        current_generation = int(generation or 1)
        return upsert_loop_state(
            database,
            network=network,
            chain_id=chain_id,
            protocol=protocol,
            market_id=market_id,
            loop_key=loop_key,
            state=STATE_OFFCHAIN_ESTIMATE,
            generation=current_generation,
            owner_thread=None,
            tx_hash=None,
            last_error=None,
        )
    current_state = str((existing or {}).get("state") or "").strip()
    current_generation = int((existing or {}).get("generation") or generation or 1)
    if current_state in CYCLE_TERMINAL_STATES:
        # A terminal execution is the end of one cycle, not the end of the
        # loop forever.  Keep the durable attempt/broadcast history elsewhere
        # and advance the index generation for the next scan cycle.
        return upsert_loop_state(
            database,
            network=network,
            chain_id=chain_id,
            protocol=protocol,
            market_id=market_id,
            loop_key=loop_key,
            state=STATE_OFFCHAIN_ESTIMATE,
            generation=current_generation + 1,
            owner_thread=None,
            tx_hash=None,
            last_error=None,
        )
    # A running or queued loop must not be reset by a subsequent scan.  A
    # preflight-done row is reusable for a fresh read-only review cycle.
    if current_state not in {
        STATE_OFFCHAIN_ESTIMATE,
        "",
        "offchain_pending",
        "preflight_failed",
        STATE_PREFLIGHT_DONE,
    }:
        return existing
    return upsert_loop_state(
        database,
        network=network,
        chain_id=chain_id,
        protocol=protocol,
        market_id=market_id,
        loop_key=loop_key,
        state=STATE_OFFCHAIN_ESTIMATE,
        generation=current_generation,
        owner_thread=None,
        tx_hash=None if existing is None else existing.get("tx_hash"),
        last_error=None,
    )


def claim_review_before_submit(
    database_url: str | None,
    *,
    network: str,
    chain_id: int,
    protocol: str,
    market_id: str,
    loop_key: str,
) -> dict[str, Any] | None:
    database = resolve_runtime_database(database_url)
    if not database:
        return {"state": STATE_PREFLIGHT_RUNNING, "database": None}
    mark_group_submitted(
        database,
        network=network,
        chain_id=chain_id,
        protocol=protocol,
        market_id=market_id,
        loop_key=loop_key,
    )
    claimed = claim_loop(
        database,
        network=network,
        chain_id=chain_id,
        protocol=protocol,
        market_id=market_id,
        loop_key=loop_key,
        operation="review",
        owner_thread=owner_thread(loop_key, "review"),
    )
    if claimed is None:
        existing = load_loop_state(
            database,
            network=network,
            chain_id=chain_id,
            protocol=protocol,
            market_id=market_id,
            loop_key=loop_key,
        )
        if existing and existing.get("owner_thread") == owner_thread(loop_key, "review"):
            return existing
        return None
    return claimed


def release_review_after_submit_failure(
    database_url: str | None,
    *,
    network: str,
    chain_id: int,
    protocol: str,
    market_id: str,
    loop_key: str,
) -> dict[str, Any] | None:
    database = resolve_runtime_database(database_url)
    if not database:
        return None
    return release_loop(
        database,
        network=network,
        chain_id=chain_id,
        protocol=protocol,
        market_id=market_id,
        loop_key=loop_key,
        operation="review",
        owner_thread=owner_thread(loop_key, "review"),
    )


def _attempt_identity(
    attempt: dict[str, Any],
    *,
    loop_key: str | None = None,
) -> tuple[str, int, str, str, str]:
    try:
        chain_id = int(attempt.get("chain_id") or 0)
    except (TypeError, ValueError):
        chain_id = 0
    network = str(attempt.get("network") or "").strip().lower()
    protocol = str(attempt.get("protocol") or "uniswap_v4").strip().lower()
    market_id = str(attempt.get("market_id") or "").strip().lower()
    canonical_from_attempt = canonical_market_id_from_legacy(market_id)
    if canonical_from_attempt:
        market_id = canonical_from_attempt
    if not market_id:
        market_id = _default_market_id(chain_id, protocol)
    key = normalize_physical_loop_key(loop_key)
    if not key:
        key = normalize_physical_loop_key(attempt.get("loop_key"))
    if not key:
        key = physical_loop_key_from_route(attempt)
    if not key:
        quote = attempt.get("quote") if isinstance(attempt.get("quote"), dict) else {}
        key = physical_loop_key_from_route(quote)
    if not key:
        route = attempt.get("route_path") if isinstance(attempt.get("route_path"), list) else []
        key = directed_symbol_key_from_symbols(route) or ""
    return network, chain_id, protocol, market_id, key


def _evidence_status(attempt: dict[str, Any]) -> str:
    quote = attempt.get("quote") if isinstance(attempt.get("quote"), dict) else {}
    evidence = quote.get("live_onchain_evidence") if isinstance(quote.get("live_onchain_evidence"), dict) else {}
    return str(evidence.get("status") or attempt.get("priority_reason") or "").strip().lower()


def _review_failed(attempts: list[dict[str, Any]]) -> bool:
    for attempt in attempts:
        if _evidence_status(attempt) in RPC_FAILURE_STATUSES:
            return True
        auto = attempt.get("auto_execute") if isinstance(attempt.get("auto_execute"), dict) else {}
        if str(auto.get("confirmationStatus") or "").strip().lower() == "failed":
            return True
    return False


def _execution_requested(attempt: dict[str, Any]) -> bool:
    auto = attempt.get("auto_execute") if isinstance(attempt.get("auto_execute"), dict) else {}
    explicit = bool(
        attempt.get("executionQueued")
        or auto.get("queued")
        or auto.get("executed")
        or auto.get("attempted")
    )
    if explicit:
        return True
    status = _evidence_status(attempt)
    if status not in {"live_verified", "profitable_amount_found"}:
        return False
    return _positive_profit_value(attempt) > 0


def _positive_profit_value(attempt: dict[str, Any]) -> float:
    """Return the signed profit when evidence proves a positive loop."""
    quote = attempt.get("quote") if isinstance(attempt.get("quote"), dict) else {}
    evidence = quote.get("live_onchain_evidence") if isinstance(quote.get("live_onchain_evidence"), dict) else {}
    costs = evidence.get("costModel") if isinstance(evidence.get("costModel"), dict) else {}
    candidates = [
        costs.get("netProfitAmount"),
        evidence.get("referenceNetProfitAmount"),
        quote.get("profit_amount"),
        (quote.get("quote_analysis") or {}).get("referenceNetProfitAmount")
        if isinstance(quote.get("quote_analysis"), dict)
        else None,
    ]
    for value in candidates:
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            continue
        if parsed > 0:
            return parsed
    return 0.0


def _attempt_observed_at(attempt: dict[str, Any]) -> str:
    auto = attempt.get("auto_execute") if isinstance(attempt.get("auto_execute"), dict) else {}
    return str(
        attempt.get("observed_at")
        or attempt.get("updated_at")
        or attempt.get("created_at")
        or auto.get("observed_at")
        or auto.get("observedAt")
        or ""
    ).strip()


def _is_newer_cycle_attempt(attempt: dict[str, Any], state: dict[str, Any]) -> bool:
    observed = _attempt_observed_at(attempt)
    state_updated = str(state.get("updated_at") or "").strip()
    parsed_observed = _parse_iso_timestamp(observed)
    parsed_state = _parse_iso_timestamp(state_updated)
    return (
        parsed_observed is not None
        and parsed_state is not None
        and parsed_observed > parsed_state
    )


def _parse_iso_timestamp(value: str) -> float | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        normalized = text[:-1] + "+00:00" if text.endswith("Z") else text
        return datetime.fromisoformat(normalized).timestamp()
    except (TypeError, ValueError):
        return None


def _broadcast_payload(attempt: dict[str, Any]) -> dict[str, Any] | None:
    auto = attempt.get("auto_execute") if isinstance(attempt.get("auto_execute"), dict) else {}
    if not _execution_requested(attempt):
        return None
    result = auto.get("result") if isinstance(auto.get("result"), dict) else {}
    has_broadcast_marker = bool(
        attempt.get("executionQueued")
        or auto.get("queued")
        or auto.get("executed")
        or auto.get("attempted")
        or auto.get("failed")
        or result
        or attempt.get("broadcast")
        or attempt.get("broadcastError")
    )
    if not has_broadcast_marker:
        return None
    if (
        bool(attempt.get("executionQueued") or auto.get("queued"))
        and not bool(auto.get("attempted") or auto.get("executed"))
        and not result
        and not attempt.get("broadcastError")
    ):
        return None
    tx_hash = str(
        attempt.get("tx_hash")
        or result.get("txHash")
        or result.get("transactionHash")
        or result.get("transaction_hash")
        or ""
    ).strip() or None
    confirmation = str(auto.get("confirmationStatus") or result.get("confirmationStatus") or "").strip().lower()
    if confirmation in {"failed", "reverted", "0x0", "0"} or bool(auto.get("failed")):
        error = str(result.get("error") or attempt.get("error") or "broadcast_failed")
        info = resolve_broadcast_error(
            error,
            error_key=str(attempt.get("errorKey") or attempt.get("error_key") or "") or None,
        )
        return {
            "outcome": "failed",
            "tx_hash": tx_hash,
            "error": error,
            **info,
        }
    if bool(auto.get("executed")) and tx_hash:
        if confirmation in {"pending", "submitted", ""}:
            return {
                "outcome": "submitted",
                "tx_hash": tx_hash,
                "error": None,
                "errorKey": None,
                "errorCode": None,
                "errorName": None,
                "disposition": None,
            }
        return {
            "outcome": "success",
            "tx_hash": tx_hash,
            "error": None,
            "errorKey": None,
            "errorCode": None,
            "errorName": None,
            "disposition": None,
        }
    error = str(result.get("error") or attempt.get("error") or "broadcast_failed")
    info = resolve_broadcast_error(
        error,
        error_key=str(attempt.get("errorKey") or attempt.get("error_key") or "") or None,
    )
    return {
        "outcome": "failed",
        "tx_hash": tx_hash,
        "error": error,
        **info,
    }


def _verify_chain_state(*, chain_id: int, tx_hash: str) -> str:
    """Return confirmed, failed, pending, absent, or unavailable."""
    from dex.runtime.dex_route_rpc import fetch_dex_tx_receipt

    try:
        result = fetch_dex_tx_receipt(chain_id=int(chain_id), tx_hash=tx_hash)
    except Exception:  # noqa: BLE001 - verification must fail closed
        return "unavailable"
    if not result.get("ok"):
        return "unavailable"
    receipt = result.get("receipt") if isinstance(result.get("receipt"), dict) else {}
    transaction = result.get("transaction") if isinstance(result.get("transaction"), dict) else {}
    receipt_status = str(receipt.get("status") or "").strip().lower()
    status = str(result.get("status") or "").strip().lower()
    if status == "confirmed" or receipt_status in {"0x1", "1", "confirmed", "success"}:
        return "confirmed"
    if status == "failed" or receipt_status in {"0x0", "0", "failed", "reverted"}:
        return "failed"
    if transaction.get("from") or transaction.get("blockNumber"):
        return "pending"
    return "absent"


def _append_disposition_audit(
    *,
    network: str,
    chain_id: int,
    protocol: str,
    market_id: str,
    loop_key: str,
    payload: dict[str, Any],
    state: dict[str, Any],
    owner: str,
    next_state: str,
    next_retry_at: str | None = None,
    isolation_reason: str | None = None,
) -> None:
    append_broadcast_failure_audit(
        build_auto_disposition_audit_record(
            network=network,
            chain_id=chain_id,
            protocol=protocol,
            market_id=market_id,
            scope_key=loop_key,
            error_key=str(payload.get("errorKey") or "BOT.INTERNAL"),
            error_code=payload.get("errorCode"),
            error_name=str(payload.get("errorName") or "unknown"),
            disposition=str(payload.get("disposition") or "DESIGN_BLOCKED"),
            raw_error=str(payload.get("error") or ""),
            attempt=1,
            generation=int(state.get("generation") or 1),
            owner=owner,
            next_state=next_state,
            next_retry_at=next_retry_at,
            isolation_reason=isolation_reason,
        )
    )


def _apply_broadcast_failure(
    database: str,
    *,
    network: str,
    chain_id: int,
    protocol: str,
    market_id: str,
    loop_key: str,
    attempt: dict[str, Any],
    payload: dict[str, Any],
    state: dict[str, Any],
    broadcast_owner: str,
) -> tuple[bool, str]:
    """Apply one automatic disposition to a claimed broadcast failure."""
    del attempt  # reserved for raw-tx metadata when same-raw retry is enabled
    disposition = str(payload.get("disposition") or "DESIGN_BLOCKED").strip().upper()
    tx_hash = str(payload.get("tx_hash") or "").strip() or None
    error = str(payload.get("error") or "broadcast_failed")
    isolation = None
    isolation_reason: str | None = None

    if disposition == "VERIFY_CHAIN_STATE" and tx_hash:
        verdict = _verify_chain_state(chain_id=chain_id, tx_hash=tx_hash)
        if verdict == "confirmed":
            completed = complete_loop(
                database,
                network=network,
                chain_id=chain_id,
                protocol=protocol,
                market_id=market_id,
                loop_key=loop_key,
                operation="broadcast",
                outcome="success",
                owner_thread=broadcast_owner,
                tx_hash=tx_hash,
                error=None,
            )
            if completed is not None:
                _append_disposition_audit(
                    network=network,
                    chain_id=chain_id,
                    protocol=protocol,
                    market_id=market_id,
                    loop_key=loop_key,
                    payload=payload,
                    state=state,
                    owner=broadcast_owner,
                    next_state=STATE_EXECUTION_SUCCESS,
                )
            return completed is not None, "success"
        if verdict == "pending":
            _append_disposition_audit(
                network=network,
                chain_id=chain_id,
                protocol=protocol,
                market_id=market_id,
                loop_key=loop_key,
                payload=payload,
                state=state,
                owner=broadcast_owner,
                next_state=STATE_EXECUTION_SUBMITTED,
            )
            return False, "pending"
        isolation_reason = f"chain_state_{verdict}"
    elif disposition == "VERIFY_CHAIN_STATE":
        isolation_reason = "tx_hash_unavailable"
    elif disposition in {"CLOSE_AND_QUARANTINE", "STOP_BROADCAST_SCOPE", "DESIGN_BLOCKED"}:
        isolation_reason = disposition.lower()
    else:
        isolation_reason = None

    if isolation_reason:
        isolation = upsert_error_isolation(
            database,
            network=network,
            chain_id=chain_id,
            protocol=protocol,
            scope_key=loop_key,
            error_fingerprint=error_fingerprint(payload),
            disposition=disposition,
            reason=isolation_reason,
        )
    completed = complete_loop(
        database,
        network=network,
        chain_id=chain_id,
        protocol=protocol,
        market_id=market_id,
        loop_key=loop_key,
        operation="broadcast",
        outcome="failed",
        owner_thread=broadcast_owner,
        tx_hash=tx_hash,
        error=error,
    )
    if completed is not None:
        _append_disposition_audit(
            network=network,
            chain_id=chain_id,
            protocol=protocol,
            market_id=market_id,
            loop_key=loop_key,
            payload=payload,
            state=state,
            owner=broadcast_owner,
            next_state=STATE_EXECUTION_FAILED,
            next_retry_at=(isolation or {}).get("blockedUntil"),
            isolation_reason=isolation_reason,
        )
    return completed is not None, "failed"


def record_state_from_attempts(
    database_url: str | None,
    attempts: list[dict[str, Any]],
    *,
    loop_key: str | None = None,
) -> dict[str, Any]:
    """Advance each native result through review and broadcast claims."""
    from dex.runtime.closed_loop_object import ClosedLoopIdentity, ClosedLoopStateMachine

    database = resolve_runtime_database(database_url)
    result = {
        "ok": bool(database),
        "loops": 0,
        "reviewCompleted": 0,
        "reviewFailed": 0,
        "executionEnqueued": 0,
        "executionNotRequested": 0,
        "broadcastSuccess": 0,
        "broadcastFailed": 0,
        "duplicateReviews": 0,
        "duplicateBroadcasts": 0,
        "broadcastMissing": 0,
    }
    if not database or not attempts:
        return result
    machine = ClosedLoopStateMachine(database)
    groups: dict[tuple[str, int, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for attempt in attempts:
        if not isinstance(attempt, dict):
            continue
        identity = _attempt_identity(attempt, loop_key=loop_key)
        if identity[1] <= 0 or not identity[0] or not identity[4]:
            continue
        groups[identity].append(attempt)
    for (network, chain_id, protocol, market_id, key), group_attempts in groups.items():
        result["loops"] += 1
        review_owner = owner_thread(key, "review")
        identity = ClosedLoopIdentity(
            network=network,
            chain_id=chain_id,
            protocol=protocol,
            market_id=market_id,
            loop_key=key,
        )
        loop = machine.load(identity)
        if loop is None:
            mark_group_submitted(
                database,
                network=network,
                chain_id=chain_id,
                protocol=protocol,
                market_id=market_id,
                loop_key=key,
            )
            loop = machine.load(identity)
        if loop is None:
            continue
        current_state = loop.state.value
        if current_state in CYCLE_TERMINAL_STATES and any(
            _is_newer_cycle_attempt(item, loop.to_dict()) for item in group_attempts
        ):
            mark_group_submitted(
                database,
                network=network,
                chain_id=chain_id,
                protocol=protocol,
                market_id=market_id,
                loop_key=key,
            )
            loop = machine.load(identity)
            if loop is not None:
                current_state = loop.state.value
        if current_state in {
            "offchain_pending",
            STATE_OFFCHAIN_ESTIMATE,
            STATE_PREFLIGHT_DONE,
            "preflight_failed",
        }:
            claimed = machine.claim(
                identity,
                operation="review",
                owner_thread=review_owner,
                expected_generation=int(loop.to_dict().get("generation") or 1),
            )
            if claimed is None:
                loop = machine.load(identity) or loop
                result["duplicateReviews"] += 1
            else:
                loop = claimed
        if loop.state.value == STATE_PREFLIGHT_RUNNING and loop.to_dict().get("owner_thread") == review_owner:
            failed = _review_failed(group_attempts)
            completed = machine.complete(
                identity,
                operation="review",
                outcome="failed" if failed else "success",
                owner_thread=review_owner,
                error="native review failed" if failed else None,
            )
            if completed is not None:
                result["reviewFailed" if failed else "reviewCompleted"] += 1
                machine.metrics.record_review_outcome(
                    key,
                    "failed" if failed else "success",
                )
                loop = completed
        if loop.state.value == STATE_PREFLIGHT_DONE:
            if any(_execution_requested(item) for item in group_attempts):
                queued = machine.enqueue_execution(
                    identity,
                    expected_generation=int(loop.to_dict().get("generation") or 1),
                )
                if queued is not None:
                    result["executionEnqueued"] += 1
                    loop = queued
            else:
                closed = machine.mark_execution_not_requested(
                    identity,
                    expected_generation=int(loop.to_dict().get("generation") or 1),
                )
                if closed is not None:
                    result["executionNotRequested"] += 1
                    loop = closed
        if loop.state.value in {STATE_EXECUTION_PENDING, STATE_EXECUTION_SUBMITTED}:
            broadcast = next(
                (payload for payload in (_broadcast_payload(item) for item in group_attempts) if payload),
                None,
            )
            if broadcast is None:
                failed = fail_stuck_execution_loop(
                    database,
                    network=network,
                    chain_id=chain_id,
                    protocol=protocol,
                    market_id=market_id,
                    loop_key=key,
                )
                if failed is not None:
                    upsert_error_isolation(
                        database,
                        network=network,
                        chain_id=chain_id,
                        protocol=protocol,
                        scope_key=key,
                        error_fingerprint=error_fingerprint(
                            {"errorKey": "broadcast_result_missing"}
                        ),
                        disposition="CLOSE_AND_QUARANTINE",
                        reason="broadcast_result_missing",
                    )
                    result["broadcastMissing"] += 1
                continue
            broadcast_owner = owner_thread(key, "broadcast")
            claimed = machine.claim(
                identity,
                operation="broadcast",
                owner_thread=broadcast_owner,
                expected_generation=int(loop.to_dict().get("generation") or 1),
            )
            if claimed is None:
                result["duplicateBroadcasts"] += 1
                continue
            outcome = str(broadcast.get("outcome") or "failed")
            if outcome == "failed":
                advanced, final_outcome = _apply_broadcast_failure(
                    database,
                    network=network,
                    chain_id=chain_id,
                    protocol=protocol,
                    market_id=market_id,
                    loop_key=key,
                    attempt=group_attempts[0],
                    payload=broadcast,
                    state=loop.to_dict(),
                    broadcast_owner=broadcast_owner,
                )
                if advanced:
                    machine.metrics.record_attempt(key, group_attempts[0])
                    if final_outcome == "success":
                        result["broadcastSuccess"] += 1
                    else:
                        result["broadcastFailed"] += 1
                continue
            completed = machine.complete(
                identity,
                operation="broadcast",
                outcome=outcome,
                owner_thread=broadcast_owner,
                tx_hash=broadcast.get("tx_hash"),
                error=broadcast.get("error"),
            )
            if completed is not None:
                machine.metrics.record_attempt(key, group_attempts[0])
                if outcome == "success":
                    result["broadcastSuccess"] += 1
                elif outcome == "failed":
                    result["broadcastFailed"] += 1
    return result


def recover_stuck_execution_loops(
    database_url: str | None = None,
    *,
    network: str | None = None,
    chain_id: int | None = None,
    protocol: str | None = None,
    market_id: str | None = None,
    max_age_seconds: int | None = None,
) -> dict[str, Any]:
    """Recover every execution loop that waited too long for a broadcast result."""
    from dex.runtime.closed_loop_object import ClosedLoopStateMachine

    database = resolve_runtime_database(database_url)
    if not database:
        return {
            "failed": 0,
            "reviewOnlyClosed": 0,
            "records": [],
            "error": "database_unavailable",
        }
    machine = ClosedLoopStateMachine(database)
    recovery = machine.recover_stuck_execution_loops(
        network=network,
        chain_id=chain_id,
        protocol=protocol,
        market_id=market_id,
        max_age_seconds=max_age_seconds,
    )
    records = recovery.get("records") or []
    for record in records:
        if str(record.get("last_error") or "") == "broadcast_result_missing":
            upsert_error_isolation(
                database,
                network=str(record.get("network") or ""),
                chain_id=int(record.get("chain_id") or 0),
                protocol=str(record.get("protocol") or "uniswap_v4"),
                scope_key=str(record.get("loop_key") or ""),
                error_fingerprint=error_fingerprint(
                    {"errorKey": "broadcast_result_missing"}
                ),
                disposition="CLOSE_AND_QUARANTINE",
                reason="broadcast_result_missing",
            )
    return {
        "failed": int(recovery.get("failed") or 0),
        "reviewOnlyClosed": int(recovery.get("reviewOnlyClosed") or 0),
        "reviewReclaimed": int(recovery.get("reviewReclaimed") or 0),
        "broadcastReclaimed": int(recovery.get("broadcastReclaimed") or 0),
        "unresolvedPruned": int(recovery.get("unresolvedPruned") or 0),
        "residualPruned": int(recovery.get("residualPruned") or 0),
        "orphanPruned": int(recovery.get("orphanPruned") or 0),
        "recovered": len(records),
        "records": records,
        "error": recovery.get("error"),
    }


def runtime_state_summary(database_url: str | None = None) -> dict[str, Any]:
    """Return the durable loop-state distribution for monitoring pages."""
    from dex.runtime.closed_loop_object import ClosedLoopStateMachine

    try:
        machine = ClosedLoopStateMachine(database_url)
        if not machine.database_url:
            return {"available": False}
        return machine.runtime_state_summary()
    except Exception:
        return {"available": False}


__all__ = [
    "claim_review_before_submit",
    "mark_group_submitted",
    "owner_thread",
    "record_state_from_attempts",
    "recover_stuck_execution_loops",
    "release_review_after_submit_failure",
    "resolve_runtime_database",
    "runtime_state_summary",
]
