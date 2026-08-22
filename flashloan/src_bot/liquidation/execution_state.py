from __future__ import annotations

from typing import Any

from page_state import ExecutionStatus, PageName, store_page_state


def _current_request_id() -> str | None:
    try:
        from flask import g, has_request_context

        if has_request_context():
            request_id = getattr(g, "request_id", None)
            if request_id:
                return str(request_id)
    except Exception:
        return None
    return None


def force_remaining_blockers(blockers: list[str]) -> list[str]:
    return [reason for reason in blockers if reason != "static_call_required"]


def execution_state_context(payload: dict[str, Any], *, mode: str, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    preflight = payload.get("preflight") or {}
    account_report = payload.get("account_report") or {}
    summary = account_report.get("summary") if isinstance(account_report, dict) else {}
    context = {
        "mode": mode,
        "account": payload.get("account"),
        "request_id": payload.get("request_id") or _current_request_id(),
        "request": dict(payload.get("request") or {}),
        "execution_target": dict(payload.get("execution_target") or {}),
        "execution_phase": payload.get("execution_phase"),
        "phase": payload.get("execution_phase") or payload.get("phase"),
        "submission_allowed": payload.get("submission_allowed"),
        "block_level": payload.get("block_level"),
        "blocked_reasons": list(payload.get("blocked_reasons") or []),
        "force_allowed": payload.get("force_allowed"),
        "static_call_status": preflight.get("static_call_status"),
        "static_call_passed": preflight.get("static_call_passed"),
        "static_call_error": preflight.get("static_call_error"),
        "candidate_status": summary.get("status") if isinstance(summary, dict) else None,
        "candidate_health_factor": summary.get("health_factor") if isinstance(summary, dict) else None,
        "tx_hash": payload.get("tx_hash"),
        "receipt_status": (payload.get("receipt") or {}).get("status"),
    }
    if extra:
        context.update(extra)
    return {
        key: value
        for key, value in context.items()
        if value is not None or key in {"request", "blocked_reasons", "execution_target"}
    }


def record_execution_state(
    payload: dict[str, Any],
    status: ExecutionStatus,
    *,
    mode: str,
    result: str | None = None,
    message: str | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    store_page_state(
        PageName.EXECUTION,
        status.value,
        result=result,
        message=message,
        last_error=message if status in {ExecutionStatus.SOFT_BLOCKED, ExecutionStatus.HARD_BLOCKED, ExecutionStatus.CONFIRMED_FAILED, ExecutionStatus.ERROR, ExecutionStatus.SCAN_ERROR} else None,
        context=execution_state_context(payload, mode=mode, extra=extra),
    )


def record_execution_error(
    payload: dict[str, Any],
    *,
    mode: str,
    message: str,
    phase: str,
    result: str | None = None,
    tx_hash: str | None = None,
) -> None:
    payload["execution_phase"] = phase
    if tx_hash:
        payload["tx_hash"] = tx_hash
    preflight = dict(payload.get("preflight") or {})
    preflight["execution_phase"] = phase
    payload["preflight"] = preflight
    extra = {"phase": phase}
    if tx_hash:
        extra["tx_hash"] = tx_hash
    record_execution_state(
        payload,
        ExecutionStatus.ERROR,
        mode=mode,
        result=result if result is not None else payload.get("state"),
        message=message,
        extra=extra,
    )


def raise_execution_error(
    payload: dict[str, Any],
    *,
    mode: str,
    message: str,
    phase: str,
    result: str | None = None,
    tx_hash: str | None = None,
) -> None:
    record_execution_error(payload, mode=mode, message=message, phase=phase, result=result, tx_hash=tx_hash)
    raise RuntimeError(message)
