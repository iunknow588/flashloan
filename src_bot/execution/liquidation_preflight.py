from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


DEFAULT_MAX_PAYLOAD_AGE_SECONDS = 30
DEFAULT_MAX_QUOTE_AGE_SECONDS = 15

SOFT_BLOCK_REASONS = {
    "static_call_required",
    "static_call_failed",
    "profit_below_minimum",
    "gas_cost_too_high",
    "quote_expired",
    "quote_failed",
}

CONFIG_BLOCK_REASONS = {
    "execution_disabled",
    "auto_pause_active",
    "config_invalid",
}

HARD_BLOCK_REASONS = {
    "account_not_liquidatable",
    "no_liquidation_candidate",
    "invalid_debt_to_cover",
    "debt_exceeds_limit",
    "chain_id_mismatch",
    "private_key_mismatch",
    "missing_executor",
    "missing_owner",
    "missing_self_funded_key",
    "payload_expired",
    "deadline_too_close",
    "fallback_close_factor",
    "fallback_flashloan_premium",
    "fork_simulation_required",
    "fork_simulation_failed",
    *CONFIG_BLOCK_REASONS,
}


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


def classify_liquidation_blockers(blocked_reasons: list[str]) -> dict[str, Any]:
    hard: list[str] = []
    soft: list[str] = []
    unknown: list[str] = []
    for reason in blocked_reasons:
        if reason in HARD_BLOCK_REASONS:
            _append_once(hard, reason)
        elif reason in SOFT_BLOCK_REASONS:
            _append_once(soft, reason)
        else:
            _append_once(unknown, reason)
            _append_once(hard, reason)
    if hard:
        block_level = "hard"
    elif soft:
        block_level = "soft"
    else:
        block_level = "none"
    return {
        "block_level": block_level,
        "soft_blocked_reasons": soft,
        "hard_blocked_reasons": hard,
        "unknown_blocked_reasons": unknown,
        "force_allowed": bool(soft and not hard),
    }


def force_remaining_blockers(blocked_reasons: list[str]) -> list[str]:
    classification = classify_liquidation_blockers(blocked_reasons)
    return list(classification["hard_blocked_reasons"])


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
    parameter_sources = candidate.get("parameter_sources") or {}
    if not premium_source:
        premium_source = str(parameter_sources.get("flashloan_premium_source") or "")
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

    if mode == "flashloan" and controls.get("require_fork_simulation") and not preflight.get("fork_simulation_passed"):
        if preflight.get("fork_simulation_status") == "error":
            _append_once(blocked, "fork_simulation_failed")
        else:
            _append_once(blocked, "fork_simulation_required")

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
    min_deadline_remaining = int(controls.get("min_deadline_remaining_seconds") or 0)
    deadline_seconds_remaining = deadline - current_timestamp if deadline > 0 else None
    if (
        deadline_seconds_remaining is not None
        and deadline > current_timestamp
        and min_deadline_remaining > 0
        and deadline_seconds_remaining < min_deadline_remaining
    ):
        _append_once(blocked, "deadline_too_close")

    quote_age = age_seconds(dex_quote.get("quote_at"), now=now)
    max_quote_age = int(controls.get("max_quote_age_seconds") or DEFAULT_MAX_QUOTE_AGE_SECONDS)
    if dex_quote and quote_age is not None and quote_age > max_quote_age:
        _append_once(blocked, "quote_expired")

    if dex_quote and dex_quote.get("viable") is False:
        _append_once(blocked, "quote_failed")

    state = "submission_allowed" if not blocked else "submission_blocked"
    blocker_classification = classify_liquidation_blockers(blocked)
    return {
        "state": state,
        "submission_allowed": not blocked,
        "blocked_reasons": blocked,
        **blocker_classification,
        "checks": {
            "execution_enabled": bool(controls.get("execution_enabled")),
            "auto_pause_active": bool(controls.get("auto_pause_active")),
            "auto_pause_failure_count": controls.get("auto_pause_failure_count"),
            "auto_pause_threshold": controls.get("auto_pause_threshold"),
            "auto_pause_reason": controls.get("auto_pause_reason"),
            "require_static_call": bool(controls.get("require_static_call", True)),
            "static_call_passed": bool(preflight.get("static_call_passed")),
            "fork_simulation_required": bool(controls.get("require_fork_simulation")),
            "fork_simulation_passed": bool(preflight.get("fork_simulation_passed")),
            "fork_simulation_status": preflight.get("fork_simulation_status"),
            "fork_simulation_error": preflight.get("fork_simulation_error"),
            "debt_to_cover": debt_to_cover,
            "max_debt_to_cover": max_debt,
            "min_profit_amount": min_profit,
            "min_profit_required": min_required_profit,
            "repay_base_source": repay_base_source or None,
            "flashloan_premium_source": premium_source or None,
            "amount_to_pass_source": parameter_sources.get("amount_to_pass_source") or repay_base_source or None,
            "close_factor_source": parameter_sources.get("close_factor_source"),
            "liquidation_bonus_source": parameter_sources.get("liquidation_bonus_source"),
            "protocol_fee_source": parameter_sources.get("protocol_fee_source"),
            "flashloan_premium_block_number": parameter_sources.get("flashloan_premium_block_number")
            or profit.get("flashloan_premium_block_number"),
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
            "deadline_seconds_remaining": deadline_seconds_remaining,
            "min_deadline_remaining_seconds": min_deadline_remaining,
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
    payload["block_level"] = state["block_level"]
    payload["soft_blocked_reasons"] = state["soft_blocked_reasons"]
    payload["hard_blocked_reasons"] = state["hard_blocked_reasons"]
    payload["unknown_blocked_reasons"] = state["unknown_blocked_reasons"]
    payload["force_allowed"] = state["force_allowed"]
    payload["checks"] = state["checks"]
    return payload
