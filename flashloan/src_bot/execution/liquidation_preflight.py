from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


DEFAULT_MAX_PAYLOAD_AGE_SECONDS = 30
DEFAULT_MAX_QUOTE_AGE_SECONDS = 15


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_now() -> str:
    return utc_now().isoformat(timespec="seconds")


def parse_iso(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        text = str(value).replace("Z", "+00:00")
        parsed = datetime.fromisoformat(text)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def age_seconds(value: Any, now: datetime | None = None) -> float | None:
    parsed = parse_iso(value)
    if parsed is None:
        return None
    current = now or utc_now()
    return max(0.0, (current - parsed).total_seconds())


def _append_once(items: list[str], value: str) -> None:
    if value not in items:
        items.append(value)


def _float_value(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _candidate_profit(payload: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    amounts_profit = ((payload.get("amounts") or {}).get("profit") or {})
    account_report = payload.get("account_report") or {}
    candidate = account_report.get("recommended_candidate") if isinstance(account_report, dict) else {}
    candidate = candidate or {}
    candidate_profit = candidate.get("estimated_profit") or {}
    return candidate, {**candidate_profit, **amounts_profit}


def evaluate_liquidation_submission(
    payload: dict[str, Any],
    controls: dict[str, Any] | None = None,
    *,
    mode: str = "flashloan",
    now: datetime | None = None,
) -> dict[str, Any]:
    controls = controls or payload.get("execution_controls") or {}
    request = payload.get("request") or {}
    preflight = payload.get("preflight") or {}
    account_report = payload.get("account_report") or {}
    summary = account_report.get("summary") if isinstance(account_report, dict) else {}
    summary = summary or {}
    dex_quote = payload.get("dex_quote") or {}
    candidate, profit = _candidate_profit(payload)
    blocked: list[str] = []

    if not controls.get("execution_enabled"):
        _append_once(blocked, "execution_disabled")
    if controls.get("auto_pause_active"):
        _append_once(blocked, "auto_pause_active")

    for reason in controls.get("config_blocked_reasons") or []:
        _append_once(blocked, str(reason))

    if mode == "flashloan":
        if not payload.get("executor"):
            _append_once(blocked, "missing_executor")
        if not controls.get("flashloan_executor_configured", True):
            _append_once(blocked, "missing_executor")
        if controls.get("owner_configured") is False:
            _append_once(blocked, "missing_owner")
    elif mode == "self_funded" and not controls.get("self_funded_ready", True):
        _append_once(blocked, "missing_self_funded_key")

    if summary and summary.get("status") != "liquidatable":
        _append_once(blocked, "account_not_liquidatable")
    if isinstance(account_report, dict) and "recommended_candidate" in account_report and not candidate:
        _append_once(blocked, "no_liquidation_candidate")

    repay_base_source = str(candidate.get("repay_base_source") or profit.get("repay_base_source") or "")
    if repay_base_source == "close_factor_fallback" and not controls.get("allow_fallback_close_factor"):
        _append_once(blocked, "fallback_close_factor")

    premium_source = str(profit.get("flashloan_premium_source") or candidate.get("flashloan_premium_source") or "")
    if premium_source == "fallback_config" and not controls.get("allow_fallback_flashloan_premium"):
        _append_once(blocked, "fallback_flashloan_premium")

    try:
        debt_to_cover = int(request.get("debtToCover") or 0)
    except (TypeError, ValueError):
        debt_to_cover = 0
    if debt_to_cover <= 0:
        _append_once(blocked, "invalid_debt_to_cover")

    max_debt = int(controls.get("max_debt_to_cover") or 0)
    if max_debt > 0 and debt_to_cover > max_debt:
        _append_once(blocked, "debt_exceeds_limit")

    try:
        min_profit = int(request.get("minProfitAmount") or 0)
    except (TypeError, ValueError):
        min_profit = 0
    min_required_profit = int(controls.get("min_profit_base") or 0)
    if min_profit < min_required_profit:
        _append_once(blocked, "profit_below_minimum")

    gas_cost_usd = _float_value(profit.get("gas_cost_usd"))
    max_gas_cost_usd = _float_value(controls.get("max_gas_cost_usd"))
    if max_gas_cost_usd > 0 and gas_cost_usd > max_gas_cost_usd:
        _append_once(blocked, "gas_cost_too_high")

    operator_net_profit_usd = _float_value(
        profit.get("operator_net_profit_estimate_usd")
        or profit.get("operator_net_profit_usd")
        or profit.get("estimated_operator_net_profit_usd")
    )
    mev_buffer_usd = _float_value(controls.get("mev_buffer_usd") or profit.get("mev_buffer_usd"))
    retry_buffer_usd = _float_value(controls.get("retry_buffer_usd") or profit.get("retry_buffer_usd"))
    min_operator_net_profit_usd = _float_value(controls.get("min_operator_net_profit_usd"))
    protected_operator_profit_usd = operator_net_profit_usd - mev_buffer_usd - retry_buffer_usd
    if min_operator_net_profit_usd > 0 and protected_operator_profit_usd < min_operator_net_profit_usd:
        _append_once(blocked, "profit_below_minimum")

    if controls.get("require_static_call", True) and not preflight.get("static_call_passed"):
        if preflight.get("static_call_status") == "error":
            _append_once(blocked, "static_call_failed")
        else:
            _append_once(blocked, "static_call_required")

    built_age = age_seconds(payload.get("payload_built_at"), now=now)
    max_payload_age = int(controls.get("max_payload_age_seconds") or DEFAULT_MAX_PAYLOAD_AGE_SECONDS)
    if built_age is not None and built_age > max_payload_age:
        _append_once(blocked, "payload_expired")

    try:
        deadline = int(request.get("deadline") or 0)
    except (TypeError, ValueError):
        deadline = 0
    current_timestamp = int((now or utc_now()).timestamp())
    if deadline > 0 and deadline <= current_timestamp:
        _append_once(blocked, "payload_expired")

    quote_age = age_seconds(dex_quote.get("quote_at"), now=now)
    max_quote_age = int(controls.get("max_quote_age_seconds") or DEFAULT_MAX_QUOTE_AGE_SECONDS)
    if dex_quote and quote_age is not None and quote_age > max_quote_age:
        _append_once(blocked, "quote_expired")

    if dex_quote and dex_quote.get("viable") is False:
        _append_once(blocked, "quote_failed")

    state = "submission_allowed" if not blocked else "submission_blocked"
    return {
        "state": state,
        "submission_allowed": not blocked,
        "blocked_reasons": blocked,
        "checks": {
            "execution_enabled": bool(controls.get("execution_enabled")),
            "auto_pause_active": bool(controls.get("auto_pause_active")),
            "auto_pause_failure_count": controls.get("auto_pause_failure_count"),
            "auto_pause_threshold": controls.get("auto_pause_threshold"),
            "auto_pause_reason": controls.get("auto_pause_reason"),
            "require_static_call": bool(controls.get("require_static_call", True)),
            "static_call_passed": bool(preflight.get("static_call_passed")),
            "debt_to_cover": debt_to_cover,
            "max_debt_to_cover": max_debt,
            "min_profit_amount": min_profit,
            "min_profit_required": min_required_profit,
            "repay_base_source": repay_base_source or None,
            "flashloan_premium_source": premium_source or None,
            "gas_cost_usd": gas_cost_usd,
            "max_gas_cost_usd": max_gas_cost_usd,
            "operator_net_profit_usd": operator_net_profit_usd,
            "mev_buffer_usd": mev_buffer_usd,
            "retry_buffer_usd": retry_buffer_usd,
            "protected_operator_net_profit_usd": protected_operator_profit_usd,
            "min_operator_net_profit_usd": min_operator_net_profit_usd,
            "payload_age_seconds": built_age,
            "max_payload_age_seconds": max_payload_age,
            "deadline": deadline,
            "current_timestamp": current_timestamp,
            "quote_age_seconds": quote_age,
            "max_quote_age_seconds": max_quote_age,
            "config_valid": controls.get("config_valid"),
            "config_errors": list(controls.get("config_errors") or []),
            "chain_id": controls.get("chain_id"),
            "expected_chain_id": controls.get("expected_chain_id"),
        },
    }


def attach_liquidation_preflight_state(
    payload: dict[str, Any],
    controls: dict[str, Any] | None = None,
    *,
    mode: str = "flashloan",
) -> dict[str, Any]:
    state = evaluate_liquidation_submission(payload, controls, mode=mode)
    payload["state"] = state["state"]
    payload["submission_allowed"] = state["submission_allowed"]
    payload["blocked_reasons"] = state["blocked_reasons"]
    payload["checks"] = state["checks"]
    return payload
