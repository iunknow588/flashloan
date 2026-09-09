"""DTO and failure-state helpers shared by liquidation API routes."""

from __future__ import annotations

from core.error_registry import ErrorCode
from core.error_stack import ErrorStack, ModuleError
from core.sensitive_data import redact_sensitive_text
from bot.page_state import normalize_execution_phase, normalize_tx_hash, receipt_status


CANONICAL_ROUTE_FAILURE_STATES = {
    "submission_blocked",
    "submission_failed",
    "static_call_failed",
    "confirmed_failed",
}

_BOT_ERROR_KEY_MAP = {
    "account_not_liquidatable": "BOT.LIQUIDATION.ACCOUNT_NOT_LIQUIDATABLE",
    "no_liquidation_candidate": "BOT.LIQUIDATION.NO_EXECUTABLE_CANDIDATE",
    "quote_failed": "BOT.LIQUIDATION.QUOTE_FAILED",
    "static_call_failed": "BOT.LIQUIDATION.STATIC_CALL_FAILED",
    "approval_failed": "BOT.EXECUTION.APPROVAL_FAILED",
    "submission_reverted": "BOT.EXECUTION.SUBMISSION_REVERTED",
}

_BOT_ERROR_CODES = {
    "BOT.LIQUIDATION.ACCOUNT_NOT_LIQUIDATABLE": ErrorCode.BOT_ACCOUNT_NOT_LIQUIDATABLE,
    "BOT.LIQUIDATION.NO_EXECUTABLE_CANDIDATE": ErrorCode.BOT_NO_EXECUTABLE_CANDIDATE,
    "BOT.LIQUIDATION.QUOTE_FAILED": ErrorCode.BOT_QUOTE_FAILED,
    "BOT.LIQUIDATION.STATIC_CALL_FAILED": ErrorCode.BOT_STATIC_CALL_FAILED,
    "BOT.EXECUTION.APPROVAL_FAILED": ErrorCode.BOT_APPROVAL_FAILED,
    "BOT.EXECUTION.SUBMISSION_REVERTED": ErrorCode.BOT_SUBMISSION_REVERTED,
    "BOT.EXECUTION.RECEIPT_FAILED": ErrorCode.BOT_RECEIPT_FAILED,
    "BOT.EXECUTION.BLOCKED": ErrorCode.BOT_EXECUTION_BLOCKED,
    "BOT.INTERNAL": ErrorCode.BOT_INTERNAL,
}


def route_error_message(error: object | None) -> str | None:
    if error is None:
        return None
    return redact_sensitive_text(error)


def bot_error_stack_from_state(
    *,
    account: str = "",
    state: str | None = None,
    blocked_reasons: list[str] | None = None,
    error: object | None = None,
    flow: str = "liquidation",
) -> list[dict]:
    return _error_stack_from_response(
        account=account,
        state=state,
        blocked_reasons=blocked_reasons,
        error=error,
        flow=flow,
    )


def _error_stack_from_response(
    *,
    account: str,
    state: str | None,
    blocked_reasons: list[str] | None,
    error: object | None,
    flow: str = "liquidation",
) -> list[dict]:
    reasons = list(blocked_reasons or [])
    reason = reasons[0] if reasons else None
    raw = route_error_message(error) if error is not None else None
    stack = ErrorStack()
    if raw or reason or str(state or "").strip():
        if reason and reason.lower() in _BOT_ERROR_KEY_MAP:
            error_key = _BOT_ERROR_KEY_MAP[reason.lower()]
        elif reason == "account_not_liquidatable":
            error_key = "BOT.LIQUIDATION.ACCOUNT_NOT_LIQUIDATABLE"
        elif reason == "no_liquidation_candidate":
            error_key = "BOT.LIQUIDATION.NO_EXECUTABLE_CANDIDATE"
        elif reason == "quote_failed":
            error_key = "BOT.LIQUIDATION.QUOTE_FAILED"
        elif str(state or "").strip() == "static_call_failed":
            error_key = "BOT.LIQUIDATION.STATIC_CALL_FAILED"
        elif str(state or "").strip() in {"submission_blocked", "submission_failed"}:
            error_key = "BOT.EXECUTION.BLOCKED"
        else:
            error_key = "BOT.EXECUTION.BLOCKED" if reason else "BOT.INTERNAL"
        message = raw or reason or error_key
        if account:
            message = f"[{account}] {message}"
        if flow and flow != "liquidation":
            message = f"[{flow}] {message}"
        stack.push(
            ModuleError(
                module="BOT",
                code=int(_BOT_ERROR_CODES[error_key]),
                message=message,
            )
        )
    else:
        stack.mark_ok(ModuleError(module="BOT", code=0, message="OK"))
    return stack.as_list()


def stale_liquidatable_context_response(account: str, validation: dict) -> tuple[dict, int]:
    stack = ErrorStack()
    stack.push(
        ModuleError(
            module="BOT",
            code=int(ErrorCode.BOT_EXECUTION_BLOCKED),
            message=f"BOT.liquidation.context_expired[{account}]: liquidatable context is stale; refresh prediction/quote/preflight before submit",
        )
    )
    return (
        {
            "error": "liquidatable context is stale; refresh prediction/quote/preflight before submit",
            "account": account,
            "state": "submission_blocked",
            "execution_phase": "context_expired",
            "blocked_reasons": validation.get("blocked_reasons") or [],
            "errorStack": stack.as_list(),
            "liquidatable_context": validation,
        },
        400,
    )


def _float_or_none(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def classify_liquidation_payload_block(account: str, snapshot: dict | None, error: object | None = None) -> dict | None:
    snapshot = snapshot if isinstance(snapshot, dict) else {}
    summary = snapshot.get("summary") if isinstance(snapshot.get("summary"), dict) else {}
    context = snapshot.get("context") if isinstance(snapshot.get("context"), dict) else {}
    core = context.get("core_opportunity") if isinstance(context.get("core_opportunity"), dict) else {}
    candidates = snapshot.get("liquidation_candidates") if isinstance(snapshot.get("liquidation_candidates"), list) else []
    recommended = snapshot.get("recommended_candidate") if isinstance(snapshot.get("recommended_candidate"), dict) else {}
    error_text = route_error_message(error) if error is not None else ""
    health_factor = _float_or_none(summary.get("health_factor") or core.get("health_factor"))

    reason_code = None
    blocked_reasons: list[str] = []
    detail = None
    if health_factor is not None and health_factor >= 1:
        reason_code = "ACCOUNT_NOT_LIQUIDATABLE"
        blocked_reasons.append("account_not_liquidatable")
        detail = f"health_factor={health_factor:.18g} is not below 1.0"
        if not candidates and not recommended and snapshot.get("found"):
            blocked_reasons.append("no_liquidation_candidate")
            detail += "; pool cache has no recommended debt/collateral asset pair"
    elif not candidates and not recommended and snapshot.get("found"):
        reason_code = "NO_EXECUTABLE_CANDIDATE"
        blocked_reasons.append("no_liquidation_candidate")
        core_profit = _float_or_none(core.get("estimated_operator_net_profit_usd"))
        if core_profit is None:
            liquidation_profit = snapshot.get("liquidation_profit") if isinstance(snapshot.get("liquidation_profit"), dict) else {}
            core_profit = _float_or_none(liquidation_profit.get("operator_net_profit_usd"))
        if core_profit is not None and core_profit < 1.0:
            blocked_reasons.append("profit_below_minimum")
            detail = "no executable debt/collateral pair; estimated operator profit is below 1U; manual test only"
        else:
            detail = "pool cache has no recommended debt/collateral asset pair"
    elif error_text:
        lowered = error_text.lower()
        if "recommended_candidate" in lowered or "candidate" in lowered:
            reason_code = "NO_EXECUTABLE_CANDIDATE"
            blocked_reasons.append("no_liquidation_candidate")
        elif "not liquidatable" in lowered:
            reason_code = "ACCOUNT_NOT_LIQUIDATABLE"
            blocked_reasons.append("account_not_liquidatable")
        elif "quote" in lowered:
            reason_code = "QUOTE_FAILED"
            blocked_reasons.append("quote_failed")
        else:
            reason_code = "PAYLOAD_BUILD_FAILED"
            blocked_reasons.append("payload_build_failed")
        detail = error_text

    if not reason_code:
        return None
    return {
        "error": detail or reason_code,
        "reason_code": reason_code,
        "account": account,
        "state": "submission_blocked",
        "execution_phase": "payload_blocked",
        "blocked_reasons": blocked_reasons,
        "errorStack": _error_stack_from_response(
            account=account,
            state="submission_blocked",
            blocked_reasons=blocked_reasons,
            error=error_text,
        ),
        "preflight": {
            "static_call_required": True,
            "static_call_status": "blocked",
            "static_call_passed": False,
            "static_call_error": detail or reason_code,
        },
        "checks": {
            "health_factor": health_factor,
            "candidate_count": len(candidates),
            "recommended_candidate_present": bool(recommended),
            "payload_state": core.get("payload_state"),
            "quote_viable": core.get("quote_viable"),
            "static_call_status": core.get("static_call_status"),
        },
        "cached_snapshot": snapshot or None,
    }


def liquidation_failure_response(account: str, payload: dict | None, error: Exception, *, cached_snapshot=None, execution_controls=None) -> dict:
    response = {"error": route_error_message(error), "account": account}
    if not isinstance(payload, dict):
        blocked = classify_liquidation_payload_block(account, cached_snapshot, error)
        if blocked is not None:
            return blocked
        response["errorStack"] = _error_stack_from_response(
            account=account,
            state=None,
            blocked_reasons=[],
            error=error,
        )
        return response
    account_report = payload.get("account_report") or {}
    response.update(
        {
            "executor": payload.get("executor"),
            "request": payload.get("request") or {},
            "preflight": payload.get("preflight") or {},
            "execution_phase": payload.get("execution_phase") or (payload.get("context") or {}).get("phase"),
            "state": payload.get("state"),
            "submission_allowed": payload.get("submission_allowed"),
            "blocked_reasons": payload.get("blocked_reasons") or [],
            "errorStack": _error_stack_from_response(
                account=account,
                state=payload.get("state") or payload.get("execution_phase"),
                blocked_reasons=payload.get("blocked_reasons") or [],
                error=error,
                flow="execution" if payload.get("execution_phase") not in (None, "payload_blocked") else "liquidation",
            ),
            "checks": payload.get("checks") or {},
            "dex_quote": payload.get("dex_quote") or {},
            "tx_hash": normalize_tx_hash(payload),
            "receipt": payload.get("receipt") or {},
            "account_report": account_report,
            "execution_plan": account_report.get("execution_plan") if isinstance(account_report, dict) else None,
            "execution_controls": payload.get("execution_controls") or execution_controls or {},
        }
    )
    return response


def route_failure_state(mode: str, response: dict) -> str:
    state = str(response.get("state") or "").strip()
    if state in CANONICAL_ROUTE_FAILURE_STATES:
        return state
    if receipt_status(response.get("receipt") or {}) == 0:
        return "confirmed_failed"
    if response.get("blocked_reasons") or []:
        return "submission_blocked"
    preflight = response.get("preflight") if isinstance(response.get("preflight"), dict) else {}
    if mode == "static_call" and (
        preflight.get("static_call_passed") is False
        or preflight.get("static_call_error")
        or str(preflight.get("static_call_status") or "").lower() in {"error", "failed"}
    ):
        return "static_call_failed"
    return "submission_failed"


def route_failure_phase(response: dict, failure_state: str) -> str:
    return normalize_execution_phase(response, fallback_state=failure_state) or failure_state
