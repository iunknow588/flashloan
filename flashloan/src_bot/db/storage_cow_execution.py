from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from typing import Any

from db.storage_common import db_connection

DEFAULT_COW_EXECUTION_RETENTION_DAYS = 7
COW_EXECUTION_RETENTION_LOCAL_TZ = timezone(timedelta(hours=8), "Asia/Shanghai")
COW_ATTEMPT_CATEGORY_NOT_EXECUTABLE = "not_executable"
COW_ATTEMPT_CATEGORY_EXECUTION_FAILED = "execution_failed"
COW_ATTEMPT_CATEGORY_EXECUTION_SUCCESS = "execution_success"
COW_ATTEMPT_CATEGORY_RETENTION_DAYS = {
    COW_ATTEMPT_CATEGORY_NOT_EXECUTABLE: 2,
    COW_ATTEMPT_CATEGORY_EXECUTION_FAILED: 14,
    COW_ATTEMPT_CATEGORY_EXECUTION_SUCCESS: None,
}
COW_ATTEMPT_CATEGORY_ANALYSIS_DAYS = {
    COW_ATTEMPT_CATEGORY_NOT_EXECUTABLE: 1,
    COW_ATTEMPT_CATEGORY_EXECUTION_FAILED: 7,
    COW_ATTEMPT_CATEGORY_EXECUTION_SUCCESS: None,
}
COW_ATTEMPT_CATEGORY_RETENTION_POLICY = {
    COW_ATTEMPT_CATEGORY_NOT_EXECUTABLE: {
        "unit": "day",
        "active_bucket_days": 1,
        "storage_buckets": 2,
        "storage_days": 2,
        "timezone": "Asia/Shanghai",
        "description": "current and previous local-day buckets",
    },
    COW_ATTEMPT_CATEGORY_EXECUTION_FAILED: {
        "unit": "iso_week",
        "active_bucket_days": 7,
        "storage_buckets": 2,
        "storage_days": 14,
        "timezone": "Asia/Shanghai",
        "description": "current and previous local ISO-week buckets",
    },
    COW_ATTEMPT_CATEGORY_EXECUTION_SUCCESS: {
        "unit": "forever",
        "active_bucket_days": None,
        "storage_buckets": None,
        "storage_days": None,
        "timezone": "Asia/Shanghai",
        "description": "long-term retention",
    },
}
COW_ATTEMPT_CATEGORY_LABELS = {
    COW_ATTEMPT_CATEGORY_NOT_EXECUTABLE: "not executable",
    COW_ATTEMPT_CATEGORY_EXECUTION_FAILED: "execution failed",
    COW_ATTEMPT_CATEGORY_EXECUTION_SUCCESS: "execution success",
}

_SUCCESS_STATES = {
    "confirmed_success",
    "confirmation_success",
    "settled",
    "settlement_success",
    "executed",
    "execution_success",
    "success",
    "submitted_success",
}
_SUCCESS_PHASES = {"confirmed_success", "settled", "execution_success"}
_NOT_EXECUTABLE_STATES = {
    "market_candidate",
    "quote_required",
    "unsupported",
    "unsupported_route",
    "unsupported_token",
    "missing_token",
    "quote_unavailable",
    "flashloan_payload_required",
    "price_guard_failed",
    "profit_below_threshold",
    "not_profitable",
    "checks_failed",
    "order_disabled",
}
_FAILED_STATES = {
    "checks_passed_order_disabled",
    "limit_order_ready_not_submitted",
    "limit_order_ready_to_submit",
    "ready_not_submitted",
    "ready_to_submit",
    "quote_failed",
    "submission_failed",
    "submit_failed",
    "order_failed",
    "execution_failed",
    "settlement_failed",
    "confirmation_failed",
    "confirmed_failed",
    "failed",
    "error",
}
_EXECUTION_PHASES = {
    "order_submission",
    "submission",
    "execution",
    "settlement",
    "confirmation",
}


def _json_text(value: Any) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _json_loads(value: Any, fallback: Any) -> Any:
    try:
        return json.loads(value or "")
    except (TypeError, json.JSONDecodeError):
        return fallback


def _cow_intent_from_attempt(item: dict[str, Any]) -> dict[str, Any]:
    quote = item.get("quote") if isinstance(item.get("quote"), dict) else {}
    precheck = item.get("precheck") if isinstance(item.get("precheck"), dict) else {}
    for value in (
        item.get("cow_flashloan_intent"),
        quote.get("cow_flashloan_intent"),
        precheck.get("cow_flashloan_intent"),
    ):
        if isinstance(value, dict):
            return value
    return {}


def _cow_sdk_result_from_attempt(item: dict[str, Any]) -> dict[str, Any]:
    quote = item.get("quote") if isinstance(item.get("quote"), dict) else {}
    precheck = item.get("precheck") if isinstance(item.get("precheck"), dict) else {}
    for value in (
        item.get("cow_sdk_result"),
        quote.get("cow_sdk_result"),
        precheck.get("cow_sdk_result"),
    ):
        if isinstance(value, dict):
            return value
    return {}


def _cow_sdk_status_from_attempt(item: dict[str, Any]) -> str:
    sdk = _cow_sdk_result_from_attempt(item)
    for value in (sdk.get("submission_status"), sdk.get("status")):
        text = str(value or "").strip().lower()
        if text:
            return text
    return ""


def _cow_control_mode_from_attempt(item: dict[str, Any]) -> str | None:
    precheck = item.get("precheck") if isinstance(item.get("precheck"), dict) else {}
    intent = _cow_intent_from_attempt(item)
    for value in (item.get("control_mode"), precheck.get("control_mode"), intent.get("control_mode")):
        text = str(value or "").strip()
        if text:
            return text
    return None


def _route_hop_constraints_enforced_from_attempt(item: dict[str, Any]) -> bool:
    precheck = item.get("precheck") if isinstance(item.get("precheck"), dict) else {}
    value = item.get("route_hop_constraints_enforced")
    if value is None:
        value = precheck.get("route_hop_constraints_enforced")
    return bool(value)


def _parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value).strip()
        if text.endswith("Z"):
            text = f"{text[:-1]}+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def cow_execution_attempt_category(row: dict[str, Any]) -> str:
    state = str(row.get("state") or "").strip().lower()
    phase = str(row.get("execution_phase") or "").strip().lower()
    checks_passed = bool(row.get("checks_passed"))
    can_submit_order = bool(row.get("can_submit_order"))
    sdk = _cow_sdk_result_from_attempt(row)
    sdk_status = _cow_sdk_status_from_attempt(row)
    submission_attempted = bool(
        sdk.get("submission_attempted")
        or sdk.get("submission_order_id")
        or sdk.get("submission_tx_hash")
    )

    if sdk_status in _SUCCESS_STATES or sdk_status in _SUCCESS_PHASES or state in _SUCCESS_STATES or phase in _SUCCESS_PHASES:
        return COW_ATTEMPT_CATEGORY_EXECUTION_SUCCESS
    terminal_failure_states = {
        "quote_failed",
        "submission_failed",
        "submit_failed",
        "order_failed",
        "execution_failed",
        "settlement_failed",
        "confirmation_failed",
        "confirmed_failed",
        "failed",
        "error",
    }
    if (
        sdk_status in terminal_failure_states
        or state in terminal_failure_states
        or "failed" in sdk_status
        or "error" in sdk_status
    ):
        return COW_ATTEMPT_CATEGORY_EXECUTION_FAILED
    if phase == "market_candidate" or state in _NOT_EXECUTABLE_STATES:
        return COW_ATTEMPT_CATEGORY_NOT_EXECUTABLE
    if not checks_passed or not can_submit_order:
        return COW_ATTEMPT_CATEGORY_NOT_EXECUTABLE
    if sdk_status in {"", "quote_precheck", "quote_required", "not_submitted", "ready_not_submitted", "limit_order_ready_not_submitted", "limit_order_ready_to_submit", "ready_to_submit"}:
        return COW_ATTEMPT_CATEGORY_NOT_EXECUTABLE
    if submission_attempted:
        return COW_ATTEMPT_CATEGORY_NOT_EXECUTABLE
    return COW_ATTEMPT_CATEGORY_NOT_EXECUTABLE


def cow_execution_attempt_retention_days(row_or_category: dict[str, Any] | str) -> int | None:
    if isinstance(row_or_category, str):
        category = row_or_category
    else:
        category = cow_execution_attempt_category(row_or_category)
    return COW_ATTEMPT_CATEGORY_RETENTION_DAYS.get(category, DEFAULT_COW_EXECUTION_RETENTION_DAYS)


def cow_execution_attempt_analysis_days(row_or_category: dict[str, Any] | str) -> int | None:
    if isinstance(row_or_category, str):
        category = row_or_category
    else:
        category = cow_execution_attempt_category(row_or_category)
    return COW_ATTEMPT_CATEGORY_ANALYSIS_DAYS.get(category, DEFAULT_COW_EXECUTION_RETENTION_DAYS)


def cow_execution_attempt_retention_policy(row_or_category: dict[str, Any] | str) -> dict[str, Any]:
    if isinstance(row_or_category, str):
        category = row_or_category
    else:
        category = cow_execution_attempt_category(row_or_category)
    return dict(COW_ATTEMPT_CATEGORY_RETENTION_POLICY.get(category, {}))


def _local_midnight(value: datetime) -> datetime:
    local = value.astimezone(COW_EXECUTION_RETENTION_LOCAL_TZ)
    return local.replace(hour=0, minute=0, second=0, microsecond=0)


def _local_iso_week_start(value: datetime) -> datetime:
    local_midnight = _local_midnight(value)
    return local_midnight - timedelta(days=local_midnight.weekday())


def _category_retention_cutoff(row_or_category: dict[str, Any] | str, now: datetime | None = None) -> datetime | None:
    current = now or datetime.now(timezone.utc)
    category = row_or_category if isinstance(row_or_category, str) else cow_execution_attempt_category(row_or_category)
    if category == COW_ATTEMPT_CATEGORY_EXECUTION_SUCCESS:
        return None
    if category == COW_ATTEMPT_CATEGORY_NOT_EXECUTABLE:
        return _local_midnight(current) - timedelta(days=1)
    if category == COW_ATTEMPT_CATEGORY_EXECUTION_FAILED:
        return _local_iso_week_start(current) - timedelta(days=7)
    days = cow_execution_attempt_retention_days(category)
    if days is None:
        return None
    return current - timedelta(days=max(1, int(days)))


def _category_within_retention(row: dict[str, Any], now: datetime | None = None) -> bool:
    created_at = _parse_datetime(row.get("created_at") or row.get("observed_at"))
    if created_at is None:
        return True
    cutoff = _category_retention_cutoff(row, now=now)
    if cutoff is None:
        return True
    return created_at.astimezone(COW_EXECUTION_RETENTION_LOCAL_TZ) >= cutoff


def _decorate_attempt_review(row: dict[str, Any]) -> dict[str, Any]:
    category = cow_execution_attempt_category(row)
    row["review_category"] = category
    row["review_category_label"] = COW_ATTEMPT_CATEGORY_LABELS.get(category, category)
    row["review_retention_days"] = cow_execution_attempt_retention_days(category)
    row["review_analysis_days"] = cow_execution_attempt_analysis_days(category)
    row["review_retention_policy"] = cow_execution_attempt_retention_policy(category)
    row["review_summary"] = _build_review_summary(row)
    return row


def _market_state_summary(market_state: dict[str, Any]) -> dict[str, Any]:
    return {
        "observed_at": market_state.get("observed_at"),
        "window_seconds": market_state.get("window_seconds"),
        "price_source": market_state.get("price_source"),
        "market_state_source": market_state.get("market_state_source"),
        "fallback_reason": market_state.get("fallback_reason"),
        "cow_filter": market_state.get("cow_filter"),
    }


def _first_present(*values: Any) -> Any:
    for value in values:
        if value is not None and value != "":
            return value
    return None


def _review_decimal(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _review_change_percent(item: dict[str, Any]) -> Any:
    raw = item.get("change_percent")
    start = _review_decimal(item.get("start_price"))
    current = _review_decimal(item.get("current_price"))
    if (raw is None or raw == "" or str(raw) == "0" or str(raw) == "0.0") and start and current:
        return (current - start) / start * 100
    return raw


def _review_error_summary(error: Any) -> dict[str, Any]:
    text = str(error or "").strip()
    lowered = text.lower()
    if not text:
        return {"type": None, "display": None, "raw": None}
    if "403 error" in lowered and ("cloudfront" in lowered or "request blocked" in lowered):
        return {
            "type": "quote_api_http_403_cloudfront_request_blocked",
            "display": "CoW quote API HTTP 403: CloudFront request blocked",
            "raw": text,
        }
    if "timed out" in lowered or "timeout" in lowered:
        return {"type": "quote_api_timeout", "display": "CoW quote API timeout", "raw": text}
    if "ssl" in lowered:
        return {"type": "quote_api_ssl_error", "display": "CoW quote API SSL error", "raw": text}
    if "http" in lowered or "<html" in lowered or "<!doctype" in lowered:
        return {"type": "quote_api_http_error", "display": "CoW quote API HTTP error", "raw": text}
    return {"type": "quote_api_error", "display": text[:240], "raw": text}


def _review_market_prices(quote: dict[str, Any]) -> list[dict[str, Any]]:
    plan = quote.get("binance_execution_plan") if isinstance(quote.get("binance_execution_plan"), dict) else {}
    prices = plan.get("market_prices") if isinstance(plan, dict) else None
    if isinstance(prices, list) and prices:
        return [{**item, "change_percent": _review_change_percent(item)} for item in prices if isinstance(item, dict)]
    rows = []
    for prefix in ("x", "y"):
        symbol = quote.get(f"{prefix}_symbol") or quote.get(f"{prefix}_base_symbol")
        if symbol:
            rows.append(
                {
                    "symbol": symbol,
                    "base_symbol": quote.get(f"{prefix}_base_symbol"),
                    "start_price": quote.get(f"{prefix}_start_price"),
                    "current_price": quote.get(f"{prefix}_current_price"),
                    "change_percent": _review_change_percent(
                        {
                            "start_price": quote.get(f"{prefix}_start_price"),
                            "current_price": quote.get(f"{prefix}_current_price"),
                            "change_percent": quote.get(f"{prefix}_change_percent"),
                        }
                    ),
                }
            )
    return rows


def _review_plan_steps(quote: dict[str, Any]) -> list[dict[str, Any]]:
    plan = quote.get("binance_execution_plan") if isinstance(quote.get("binance_execution_plan"), dict) else {}
    steps = plan.get("steps") if isinstance(plan, dict) else None
    if not isinstance(steps, list):
        return []
    summaries = []
    for step in steps:
        if not isinstance(step, dict):
            continue
        summaries.append(
            {
                "step": step.get("step"),
                "from_symbol": step.get("from_symbol"),
                "to_symbol": step.get("to_symbol"),
                "input_amount": _first_present(step.get("query_sell_amount_before_fee"), step.get("input_amount")),
                "query_output_amount": step.get("query_buy_amount_after_fee"),
                "target_output_amount": step.get("target_output_amount"),
                "min_output_amount": step.get("min_output_amount"),
                "price_candidates": step.get("price_candidates") or [],
                "rate_candidates": step.get("rate_candidates") or [],
                "query_price_usd_per_token": step.get("query_price_usd_per_token"),
                "query_exchange_rate": step.get("query_exchange_rate"),
                "query_price_position": step.get("query_price_position"),
                "query_rate_position": step.get("query_rate_position"),
                "query_window_timing": step.get("query_window_timing"),
                "query_guard_analysis": step.get("query_guard_analysis"),
                "selected_target_source": step.get("selected_target_source"),
                "selected_target_price_usd_per_token": step.get("selected_target_price_usd_per_token"),
                "selected_target_exchange_rate": step.get("selected_target_exchange_rate"),
                "acceptable_slippage_price_usd_per_token": step.get("acceptable_slippage_price_usd_per_token"),
                "acceptable_slippage_exchange_rate": step.get("acceptable_slippage_exchange_rate"),
                "slippage_bps": step.get("slippage_bps"),
                "selection_rule": step.get("selection_rule") or step.get("price_compare_rule"),
            }
        )
    return summaries


def _build_review_summary(row: dict[str, Any]) -> dict[str, Any]:
    quote = row.get("quote") if isinstance(row.get("quote"), dict) else {}
    precheck = row.get("precheck") if isinstance(row.get("precheck"), dict) else {}
    market_state = row.get("market_state") if isinstance(row.get("market_state"), dict) else {}
    cow_filter = market_state.get("cow_filter") if isinstance(market_state.get("cow_filter"), dict) else {}
    threshold = cow_filter.get("threshold_detail") if isinstance(cow_filter.get("threshold_detail"), dict) else {}
    costs = quote.get("costs") if isinstance(quote.get("costs"), dict) else {}
    plan = quote.get("binance_execution_plan") if isinstance(quote.get("binance_execution_plan"), dict) else {}
    error_info = _review_error_summary(quote.get("error") or row.get("error"))
    return {
        "phase": row.get("execution_phase"),
        "control_mode": row.get("control_mode") or precheck.get("control_mode") or (quote.get("cow_flashloan_intent") or {}).get("control_mode"),
        "route_hop_constraints_enforced": bool(row.get("route_hop_constraints_enforced") or precheck.get("route_hop_constraints_enforced")),
        "candidate_basis": quote.get("candidate_basis"),
        "trigger_source": quote.get("trigger_source"),
        "window_seconds": market_state.get("window_seconds"),
        "price_source": market_state.get("price_source"),
        "window_spread_percent": quote.get("window_spread_percent"),
        "edge_hint_percent": quote.get("edge_hint_percent"),
        "market_prices": _review_market_prices(quote),
        "plan": {
            "available": plan.get("available"),
            "initial_amount": plan.get("initial_amount") or quote.get("input_amount"),
            "initial_symbol": plan.get("initial_symbol") or quote.get("input_symbol"),
            "final_target_amount": plan.get("final_target_amount"),
            "final_symbol": plan.get("final_symbol") or quote.get("final_symbol"),
            "profit_amount": plan.get("profit_amount"),
            "profit_percent": plan.get("profit_percent"),
            "slippage_bps": plan.get("slippage_bps"),
            "steps": _review_plan_steps(quote),
        },
        "cow_quote": {
            "quote_verified": bool(quote.get("quote_verified")),
            "viable": quote.get("viable"),
            "input_amount": quote.get("input_amount"),
            "input_symbol": quote.get("input_symbol"),
            "final_amount": quote.get("final_amount"),
            "final_symbol": quote.get("final_symbol"),
            "final_delta_amount": quote.get("final_delta_amount") or row.get("final_delta_amount"),
            "hops": quote.get("hops") or [],
            "error": error_info.get("display") or quote.get("error") or row.get("error"),
            "error_type": error_info.get("type"),
            "error_raw": error_info.get("raw"),
        },
        "cow_sdk": {
            "result": row.get("cow_sdk_result") or quote.get("cow_sdk_result") or {},
            "intent": row.get("cow_flashloan_intent") or quote.get("cow_flashloan_intent") or {},
        },
        "timing": {
            "quote": quote.get("quote_timing") or {},
            "signal": quote.get("signal_timing") or market_state.get("signal_timing") or {},
            "binance_window": quote.get("binance_window") or {},
            "three_hop_window_analysis": [
                {
                    "step": step.get("step"),
                    "from_symbol": step.get("from_symbol"),
                    "to_symbol": step.get("to_symbol"),
                    "query_window_timing": step.get("query_window_timing") or {},
                }
                for step in _review_plan_steps(quote)
                if isinstance(step, dict)
            ],
        },
        "costs": {
            "cow_fee_amounts": costs.get("cow_fee_amounts") or [],
            "quote_api_gas_used": costs.get("quote_api_gas_used"),
            "user_order_submission_gas_used": costs.get("user_order_submission_gas_used"),
            "settlement_gas_payer": costs.get("settlement_gas_payer"),
            "approval_gas_status": costs.get("approval_gas_status"),
            "native_balance_source": costs.get("native_balance_source"),
        },
        "profit_guard": {
            "status": precheck.get("status") or row.get("state"),
            "checks_passed": row.get("checks_passed"),
            "can_submit_order": row.get("can_submit_order"),
            "price_guards_passed": precheck.get("price_guards_passed"),
            "profit_above_auto_threshold": precheck.get("profit_above_auto_threshold"),
            "local_profit_gate_enforced": precheck.get("local_profit_gate_enforced"),
            "local_profit_diagnostic_reasons": precheck.get("local_profit_diagnostic_reasons") or [],
            "pure_profit_amount": precheck.get("pure_profit_amount") or precheck.get("final_delta_amount") or row.get("final_delta_amount"),
            "final_symbol": precheck.get("final_symbol") or row.get("final_symbol"),
            "auto_execute_min_profit_usd": precheck.get("auto_execute_min_profit_usd") or threshold.get("min_profit_usd"),
            "auto_execute_min_profit_percent": precheck.get("auto_execute_min_profit_percent") or threshold.get("min_profit_percent"),
            "reasons": row.get("blocked_reasons") or precheck.get("reasons") or [],
            "hop_checks": precheck.get("hop_checks") or [],
            "blocking_cause_counts": precheck.get("blocking_cause_counts") or {},
            "quote_error_type": precheck.get("quote_error_type") or error_info.get("type"),
            "quote_error_display": precheck.get("quote_error_display") or error_info.get("display"),
            "drawdown_amount": precheck.get("drawdown_amount"),
            "drawdown_percent": precheck.get("drawdown_percent"),
        },
        "threshold": threshold,
    }


def ensure_cow_execution_attempts_table(database_url: str) -> None:
    with db_connection(database_url, connect_timeout=8) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS cow_execution_attempts (
                    id BIGSERIAL PRIMARY KEY,
                    observed_at TIMESTAMPTZ,
                    network TEXT NOT NULL,
                    chain_id INTEGER,
                    owner_address TEXT,
                    pair TEXT,
                    pair_rank INTEGER,
                    priority_reason TEXT,
                    route_path_json TEXT,
                    state TEXT NOT NULL,
                    execution_phase TEXT NOT NULL,
                    checks_passed BOOLEAN NOT NULL DEFAULT FALSE,
                    can_submit_order BOOLEAN NOT NULL DEFAULT FALSE,
                    order_submission_enabled BOOLEAN NOT NULL DEFAULT FALSE,
                    auto_execute_requested BOOLEAN NOT NULL DEFAULT FALSE,
                    control_mode TEXT,
                    route_hop_constraints_enforced BOOLEAN NOT NULL DEFAULT FALSE,
                    final_delta_amount TEXT,
                    final_symbol TEXT,
                    blocked_reasons_json TEXT,
                    cow_flashloan_intent_json TEXT,
                    cow_sdk_result_json TEXT,
                    quote_json TEXT,
                    precheck_json TEXT,
                    market_state_json TEXT,
                    error TEXT,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_cow_execution_attempts_network_time "
                "ON cow_execution_attempts(network, created_at DESC)"
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_cow_execution_attempts_state_time "
                "ON cow_execution_attempts(state, created_at DESC)"
            )
            cursor.execute("ALTER TABLE cow_execution_attempts ADD COLUMN IF NOT EXISTS control_mode TEXT")
            cursor.execute(
                "ALTER TABLE cow_execution_attempts ADD COLUMN IF NOT EXISTS "
                "route_hop_constraints_enforced BOOLEAN NOT NULL DEFAULT FALSE"
            )
            cursor.execute("ALTER TABLE cow_execution_attempts ADD COLUMN IF NOT EXISTS cow_flashloan_intent_json TEXT")
            cursor.execute("ALTER TABLE cow_execution_attempts ADD COLUMN IF NOT EXISTS cow_sdk_result_json TEXT")


def _attempt_from_quote(
    quote: dict[str, Any],
    *,
    market_state: dict[str, Any],
    cow_network: str,
    cow_chain_id: int | None,
    owner: str | None,
) -> dict[str, Any]:
    precheck = quote.get("execution_precheck") or {}
    reasons = precheck.get("reasons") if isinstance(precheck, dict) else []
    state = str(precheck.get("status") or ("quote_failed" if quote.get("error") else "quoted"))
    execution_phase = str(
        precheck.get("execution_phase")
        or ("order_submission" if state in {"submitted_success", "submission_failed"} else "quote_precheck")
    )
    cow_intent = quote.get("cow_flashloan_intent") if isinstance(quote.get("cow_flashloan_intent"), dict) else precheck.get("cow_flashloan_intent") if isinstance(precheck, dict) else {}
    cow_sdk_result = quote.get("cow_sdk_result") if isinstance(quote.get("cow_sdk_result"), dict) else {}
    return {
        "observed_at": market_state.get("observed_at"),
        "network": cow_network,
        "chain_id": cow_chain_id,
        "owner_address": owner,
        "pair": quote.get("pair"),
        "pair_rank": quote.get("pair_rank"),
        "priority_reason": quote.get("priority_reason"),
        "route_path": quote.get("path") or [],
        "state": state,
        "execution_phase": execution_phase,
        "checks_passed": bool(precheck.get("checks_passed")),
        "can_submit_order": bool(precheck.get("can_submit_order")),
        "order_submission_enabled": bool(precheck.get("order_submission_enabled")),
        "auto_execute_requested": bool(precheck.get("auto_execute_requested")),
        "control_mode": precheck.get("control_mode") or (cow_intent or {}).get("control_mode"),
        "route_hop_constraints_enforced": bool(precheck.get("route_hop_constraints_enforced")),
        "final_delta_amount": quote.get("final_delta_amount"),
        "final_symbol": quote.get("final_symbol"),
        "blocked_reasons": reasons if isinstance(reasons, list) else [str(reasons)],
        "cow_flashloan_intent": cow_intent or {},
        "cow_sdk_result": cow_sdk_result or {},
        "quote": quote,
        "precheck": precheck,
        "market_state": _market_state_summary(market_state),
        "error": quote.get("error"),
    }


def _attempt_from_market_route(
    pair: dict[str, Any],
    route: dict[str, Any],
    *,
    market_state: dict[str, Any],
    cow_network: str,
    cow_chain_id: int | None,
) -> dict[str, Any]:
    route_path = route.get("route") or pair.get("route") or []
    reasons = []
    for item in [*(pair.get("blocked_reasons") or []), *(route.get("blocked_reasons") or [])]:
        if item and item not in reasons:
            reasons.append(item)
    if not reasons:
        reasons.append("requires_cow_or_dex_quote")
    precheck = {
        "status": "quote_required",
        "checks_passed": False,
        "can_submit_order": False,
        "order_submission_enabled": False,
        "auto_execute_requested": False,
        "reasons": reasons,
        "quote_required": bool(pair.get("quote_required", True) or route.get("quote_required", True)),
        "estimation_available": bool(pair.get("estimation_available") or route.get("estimation_available")),
        "edge_hint_percent": route.get("edge_hint_percent", pair.get("edge_hint_percent")),
        "window_spread_percent": pair.get("window_spread_percent"),
    }
    quote = {
        "pair": pair.get("pair"),
        "pair_rank": pair.get("rank") or pair.get("grid_rank"),
        "priority_reason": route.get("priority_reason"),
        "path": route_path,
        "input_amount": route.get("initial_amount"),
        "input_symbol": route.get("initial_symbol"),
        "final_symbol": route.get("initial_symbol"),
        "final_delta_amount": route.get("net_after_flashloan_amount") or route.get("profit_amount"),
        "quote_verified": False,
        "quote_required": True,
        "candidate_basis": pair.get("candidate_basis") or route.get("candidate_basis"),
        "trigger_source": pair.get("trigger_source"),
        "edge_hint_percent": route.get("edge_hint_percent", pair.get("edge_hint_percent")),
        "window_spread_percent": pair.get("window_spread_percent"),
        "binance_execution_plan": route.get("binance_execution_plan") or pair.get("binance_execution_plan") or {},
        "x_symbol": pair.get("x_symbol"),
        "y_symbol": pair.get("y_symbol"),
        "x_base_symbol": pair.get("x_base_symbol"),
        "y_base_symbol": pair.get("y_base_symbol"),
        "x_change_percent": pair.get("x_change_percent"),
        "y_change_percent": pair.get("y_change_percent"),
        "x_start_price": pair.get("x_start_price"),
        "x_current_price": pair.get("x_current_price"),
        "x_end_price": pair.get("x_end_price", pair.get("x_current_price")),
        "x_start_ms": pair.get("x_start_ms"),
        "x_end_ms": pair.get("x_end_ms"),
        "x_price_source": pair.get("x_price_source"),
        "y_start_ms": pair.get("y_start_ms"),
        "y_end_ms": pair.get("y_end_ms"),
        "y_price_source": pair.get("y_price_source"),
        "y_start_price": pair.get("y_start_price"),
        "y_current_price": pair.get("y_current_price"),
        "y_end_price": pair.get("y_end_price", pair.get("y_current_price")),
        "signal_timing": pair.get("signal_timing") or market_state.get("signal_timing"),
        "route": route,
    }
    return {
        "observed_at": market_state.get("observed_at"),
        "network": cow_network,
        "chain_id": cow_chain_id,
        "owner_address": None,
        "pair": pair.get("pair"),
        "pair_rank": pair.get("rank") or pair.get("grid_rank"),
        "priority_reason": route.get("priority_reason"),
        "route_path": route_path,
        "state": "quote_required",
        "execution_phase": "market_candidate",
        "checks_passed": False,
        "can_submit_order": False,
        "order_submission_enabled": False,
        "auto_execute_requested": False,
        "final_delta_amount": quote["final_delta_amount"],
        "final_symbol": quote["final_symbol"],
        "blocked_reasons": reasons,
        "quote": quote,
        "precheck": precheck,
        "market_state": _market_state_summary(market_state),
        "error": None,
    }


def build_cow_execution_attempts(
    payload: dict[str, Any],
    *,
    market_state: dict[str, Any],
) -> list[dict[str, Any]]:
    return [
        _attempt_from_quote(
            quote,
            market_state=market_state,
            cow_network=str(payload.get("cow_network") or ""),
            cow_chain_id=payload.get("cow_chain_id"),
            owner=payload.get("owner"),
        )
        for quote in payload.get("ranking") or []
        if isinstance(quote, dict)
    ]


def build_cow_market_candidate_attempts(market_state: dict[str, Any]) -> list[dict[str, Any]]:
    cow_filter = market_state.get("cow_filter") if isinstance(market_state, dict) else {}
    cow_network = str((cow_filter or {}).get("network") or market_state.get("cow_network") or "")
    cow_chain_id = (cow_filter or {}).get("chain_id") or market_state.get("cow_chain_id")
    attempts = []
    for pair in market_state.get("pairs") or []:
        if not isinstance(pair, dict):
            continue
        route_results = pair.get("route_results") or []
        if not route_results:
            route_results = [{"route": pair.get("route") or [], "priority_reason": pair.get("priority_reason")}]
        for route in route_results:
            if isinstance(route, dict):
                attempts.append(
                    _attempt_from_market_route(
                        pair,
                        route,
                        market_state=market_state,
                        cow_network=cow_network,
                        cow_chain_id=cow_chain_id,
                    )
                )
    return attempts


def _claim_route_results(x: dict[str, Any], y: dict[str, Any], amount: Any) -> list[dict[str, Any]]:
    x_base = str(x.get("base_symbol") or x.get("symbol") or "").removesuffix("USDT")
    y_base = str(y.get("base_symbol") or y.get("symbol") or "").removesuffix("USDT")
    return [
        {
            "route_no": 1,
            "route": ["USDC", y_base, x_base, "USDC"],
            "initial_amount": str(amount) if amount is not None else None,
            "initial_symbol": "USDC",
            "priority_reason": "buy_loser_then_gainer",
            "quote_required": True,
        },
        {
            "route_no": 2,
            "route": ["USDC", x_base, y_base, "USDC"],
            "initial_amount": str(amount) if amount is not None else None,
            "initial_symbol": "USDC",
            "priority_reason": "reverse_check",
            "quote_required": True,
        },
    ]


def _claim_pair_row(x: dict[str, Any], y: dict[str, Any], rank: int, *, amount: Any) -> dict[str, Any] | None:
    x_symbol = str(x.get("symbol") or "").strip().upper()
    y_symbol = str(y.get("symbol") or "").strip().upper()
    if not x_symbol or not y_symbol or x_symbol == y_symbol:
        return None
    try:
        x_change = float(x.get("change_percent"))
        y_change = float(y.get("change_percent"))
    except (TypeError, ValueError):
        return None
    x_base = str(x.get("base_symbol") or x_symbol).strip().upper()
    y_base = str(y.get("base_symbol") or y_symbol).strip().upper()
    return {
        "rank": rank,
        "pair": f"{x_symbol} / {y_symbol}",
        "x_symbol": x_symbol,
        "y_symbol": y_symbol,
        "x_base_symbol": x_base,
        "y_base_symbol": y_base,
        "x_change_percent": x_change,
        "y_change_percent": y_change,
        "x_start_price": x.get("start_price"),
        "x_current_price": x.get("current_price"),
        "x_end_price": x.get("end_price", x.get("current_price")),
        "x_start_ms": x.get("start_ms"),
        "x_end_ms": x.get("end_ms"),
        "x_price_source": x.get("price_source"),
        "y_start_price": y.get("start_price"),
        "y_current_price": y.get("current_price"),
        "y_end_price": y.get("end_price", y.get("current_price")),
        "y_start_ms": y.get("start_ms"),
        "y_end_ms": y.get("end_ms"),
        "y_price_source": y.get("price_source"),
        "window_spread_percent": x_change - y_change,
        "candidate_basis": "cow_network_claim_top_bottom",
        "trigger_source": "cow_network_claim",
        "quote_required": True,
        "estimation_available": False,
        "blocked_reasons": ["requires_cow_or_dex_quote"],
        "route_results": _claim_route_results(x, y, amount),
    }


def build_cow_market_claim_pairs(
    claim: dict[str, Any],
    *,
    include_below_min_spread: bool = False,
    max_pairs: int | None = None,
) -> list[dict[str, Any]]:
    if not isinstance(claim, dict):
        return []
    threshold_detail = claim.get("threshold_detail") if isinstance(claim.get("threshold_detail"), dict) else {}
    try:
        min_spread = float(claim.get("min_spread_percent") or threshold_detail.get("adjusted_min_spread_percent") or 0)
    except (TypeError, ValueError):
        min_spread = 0.0
    amount = threshold_detail.get("amount")
    pairs = []
    for x in claim.get("top") or []:
        if not isinstance(x, dict):
            continue
        for y in claim.get("bottom") or []:
            if not isinstance(y, dict):
                continue
            pair = _claim_pair_row(x, y, len(pairs) + 1, amount=amount)
            if not pair:
                continue
            if float(pair.get("window_spread_percent") or 0) <= min_spread and not include_below_min_spread:
                continue
            if float(pair.get("window_spread_percent") or 0) <= min_spread:
                pair["blocked_reasons"] = ["spread_below_dynamic_min", "requires_cow_or_dex_quote"]
                pair["candidate_basis"] = "cow_network_claim_top_bottom_below_spread"
            pairs.append(pair)
            if max_pairs is not None and len(pairs) >= max(1, int(max_pairs)):
                return pairs
    return pairs


def build_cow_market_claim_candidate_attempts(
    network_claims: list[dict[str, Any]],
    *,
    observed_at: Any = None,
    window_seconds: Any = None,
    price_source: Any = None,
    market_state_source: Any = None,
    fallback_reason: Any = None,
) -> list[dict[str, Any]]:
    attempts = []
    for claim in network_claims or []:
        if not isinstance(claim, dict):
            continue
        threshold_detail = claim.get("threshold_detail") if isinstance(claim.get("threshold_detail"), dict) else {}
        pairs = build_cow_market_claim_pairs(claim)
        if not pairs:
            continue
        market_state = {
            "observed_at": observed_at,
            "window_seconds": window_seconds,
            "price_source": price_source,
            "market_state_source": market_state_source,
            "fallback_reason": fallback_reason,
            "cow_filter": {
                "network": claim.get("network"),
                "chain_id": claim.get("chain_id"),
                "source": "cow_network_claim",
                "token_cache_source": claim.get("token_cache_source"),
                "token_cache_count": claim.get("token_cache_count"),
                "threshold_detail": threshold_detail,
            },
            "pairs": pairs,
        }
        attempts.extend(build_cow_market_candidate_attempts(market_state))
    return attempts


def _is_market_candidate(item: dict[str, Any]) -> bool:
    return str(item.get("execution_phase") or "") == "market_candidate"


def _attempt_signature(item: dict[str, Any]) -> tuple[Any, ...]:
    return (
        item.get("observed_at"),
        item.get("network"),
        item.get("pair"),
        item.get("pair_rank"),
        item.get("priority_reason"),
        _json_text(item.get("route_path") or []),
        item.get("execution_phase") or "quote_precheck",
    )


def prune_cow_execution_attempts(database_url: str, retention_days: int = DEFAULT_COW_EXECUTION_RETENTION_DAYS) -> int:
    ensure_cow_execution_attempts_table(database_url)
    now = datetime.now(timezone.utc)
    prune_scan_before = _local_midnight(now).astimezone(timezone.utc)
    with db_connection(database_url, connect_timeout=8) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    id, observed_at, network, chain_id, owner_address,
                    pair, pair_rank, priority_reason, route_path_json,
                    state, execution_phase, checks_passed, can_submit_order,
                    order_submission_enabled, auto_execute_requested,
                    final_delta_amount, final_symbol, blocked_reasons_json,
                    error, created_at, quote_json, precheck_json, market_state_json
                FROM cow_execution_attempts
                WHERE created_at < %s
                LIMIT 5000
                """,
                (prune_scan_before,),
            )
            expired_ids = []
            for row in cursor.fetchall():
                item = {
                    "id": int(row[0]),
                    "observed_at": row[1],
                    "network": row[2],
                    "chain_id": row[3],
                    "owner_address": row[4],
                    "pair": row[5],
                    "pair_rank": row[6],
                    "priority_reason": row[7],
                    "route_path": _json_loads(row[8], []),
                    "state": row[9],
                    "execution_phase": row[10],
                    "checks_passed": bool(row[11]),
                    "can_submit_order": bool(row[12]),
                    "order_submission_enabled": bool(row[13]),
                    "auto_execute_requested": bool(row[14]),
                    "final_delta_amount": row[15],
                    "final_symbol": row[16],
                    "blocked_reasons": _json_loads(row[17], []),
                    "error": row[18],
                    "created_at": row[19],
                    "quote": _json_loads(row[20], {}),
                    "precheck": _json_loads(row[21], {}),
                    "market_state": _json_loads(row[22], {}),
                }
                if not _category_within_retention(item, now=now):
                    expired_ids.append(int(row[0]))
            if not expired_ids:
                return 0
            cursor.execute("DELETE FROM cow_execution_attempts WHERE id = ANY(%s)", (expired_ids,))
            return int(cursor.rowcount or 0)


def record_cow_execution_attempts(
    database_url: str,
    attempts: list[dict[str, Any]],
    *,
    retention_days: int = DEFAULT_COW_EXECUTION_RETENTION_DAYS,
    dedupe_market_candidates: bool = True,
) -> list[int]:
    if not attempts:
        return []
    ensure_cow_execution_attempts_table(database_url)
    if retention_days:
        prune_cow_execution_attempts(database_url, retention_days=retention_days)
    ids = []
    with db_connection(database_url, connect_timeout=8) as connection:
        with connection.cursor() as cursor:
            for item in attempts:
                route_path_json = _json_text(item.get("route_path") or [])
                if dedupe_market_candidates and _is_market_candidate(item):
                    cursor.execute(
                        """
                        SELECT id
                        FROM cow_execution_attempts
                        WHERE observed_at IS NOT DISTINCT FROM %s
                          AND network IS NOT DISTINCT FROM %s
                          AND pair IS NOT DISTINCT FROM %s
                          AND pair_rank IS NOT DISTINCT FROM %s
                          AND priority_reason IS NOT DISTINCT FROM %s
                          AND route_path_json IS NOT DISTINCT FROM %s
                          AND execution_phase IS NOT DISTINCT FROM %s
                        LIMIT 1
                        """,
                        (
                            item.get("observed_at"),
                            item.get("network"),
                            item.get("pair"),
                            item.get("pair_rank"),
                            item.get("priority_reason"),
                            route_path_json,
                            item.get("execution_phase") or "market_candidate",
                        ),
                    )
                    if cursor.fetchone():
                        continue
                cursor.execute(
                    """
                    INSERT INTO cow_execution_attempts (
                        observed_at, network, chain_id, owner_address,
                        pair, pair_rank, priority_reason, route_path_json,
                        state, execution_phase, checks_passed, can_submit_order,
                        order_submission_enabled, auto_execute_requested,
                        control_mode, route_hop_constraints_enforced,
                        final_delta_amount, final_symbol, blocked_reasons_json,
                        cow_flashloan_intent_json, cow_sdk_result_json,
                        quote_json, precheck_json, market_state_json, error, created_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
                    RETURNING id
                    """,
                    (
                        item.get("observed_at"),
                        item.get("network"),
                        item.get("chain_id"),
                        item.get("owner_address"),
                        item.get("pair"),
                        item.get("pair_rank"),
                        item.get("priority_reason"),
                        route_path_json,
                        item.get("state") or "unknown",
                        item.get("execution_phase") or "quote_precheck",
                        bool(item.get("checks_passed")),
                        bool(item.get("can_submit_order")),
                        bool(item.get("order_submission_enabled")),
                        bool(item.get("auto_execute_requested")),
                        _cow_control_mode_from_attempt(item),
                        _route_hop_constraints_enforced_from_attempt(item),
                        item.get("final_delta_amount"),
                        item.get("final_symbol"),
                        _json_text(item.get("blocked_reasons") or []),
                        _json_text(_cow_intent_from_attempt(item)),
                        _json_text(_cow_sdk_result_from_attempt(item)),
                        _json_text(item.get("quote") or {}),
                        _json_text(item.get("precheck") or {}),
                        _json_text(item.get("market_state") or {}),
                        item.get("error"),
                    ),
                )
                row = cursor.fetchone()
                if row:
                    ids.append(int(row[0]))
    return ids


def _jsonl_signature(row: dict[str, Any]) -> tuple[Any, ...]:
    return _attempt_signature(row)


def _within_retention(row: dict[str, Any], cutoff: datetime | None) -> bool:
    if cutoff is None:
        return True
    created_at = _parse_datetime(row.get("created_at") or row.get("observed_at"))
    return created_at is None or created_at >= cutoff


def append_cow_execution_attempts_jsonl(
    path: Path,
    attempts: list[dict[str, Any]],
    *,
    retention_days: int = DEFAULT_COW_EXECUTION_RETENTION_DAYS,
    dedupe_market_candidates: bool = True,
) -> int:
    if not attempts:
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    created_at = now.isoformat()
    existing_rows = []
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict) and _category_within_retention(row, now=now):
                existing_rows.append(row)
    market_signatures = {
        _jsonl_signature(row)
        for row in existing_rows
        if _is_market_candidate(row)
    }
    additions = []
    for item in attempts:
        row = {**item, "created_at": created_at}
        if dedupe_market_candidates and _is_market_candidate(row):
            signature = _jsonl_signature(row)
            if signature in market_signatures:
                continue
            market_signatures.add(signature)
        additions.append(row)
    rows = [*existing_rows, *additions]
    with path.open("w", encoding="utf-8") as handle:
        for item in rows:
            handle.write(_json_text(item) + "\n")
    return len(additions)


def load_recent_cow_execution_attempts_jsonl(
    path: Path,
    limit: int = 50,
    *,
    networks: list[str] | None = None,
    retention_days: int = DEFAULT_COW_EXECUTION_RETENTION_DAYS,
    category: str | None = None,
) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    wanted = {str(item).strip().lower() for item in networks or [] if str(item).strip()}
    wanted_category = str(category or "").strip().lower()
    now = datetime.now(timezone.utc)
    lines = path.read_text(encoding="utf-8").splitlines()
    rows = []
    network_counts: dict[str, int] = {}
    global_limit = max(1, int(limit))
    for line in reversed(lines):
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        if wanted and str(payload.get("network") or "").lower() not in wanted:
            continue
        payload_category = cow_execution_attempt_category(payload)
        if wanted_category and payload_category != wanted_category:
            continue
        if not _category_within_retention(payload, now=now):
            continue
        network = str(payload.get("network") or "").lower()
        if wanted:
            if network_counts.get(network, 0) >= global_limit:
                continue
            network_counts[network] = network_counts.get(network, 0) + 1
        rows.append(_decorate_attempt_review(payload))
        if wanted and wanted.issubset({key for key, count in network_counts.items() if count >= global_limit}):
            break
        if not wanted and len(rows) >= global_limit:
            break
    return rows


def load_recent_cow_execution_attempts(
    database_url: str,
    limit: int = 50,
    *,
    networks: list[str] | None = None,
    retention_days: int = DEFAULT_COW_EXECUTION_RETENTION_DAYS,
    category: str | None = None,
) -> list[dict[str, Any]]:
    ensure_cow_execution_attempts_table(database_url)
    wanted = [str(item).strip().lower() for item in networks or [] if str(item).strip()]
    wanted_category = str(category or "").strip().lower()
    where = ["created_at >= NOW() - (%s * INTERVAL '1 day')"]
    params: list[Any] = [max(1, int(retention_days or DEFAULT_COW_EXECUTION_RETENTION_DAYS))]
    if wanted_category:
        cutoff = _category_retention_cutoff(wanted_category)
        if cutoff is None:
            where = ["TRUE"]
            params = []
        else:
            where = ["created_at >= %s"]
            params = [cutoff.astimezone(timezone.utc)]
    if wanted:
        where.append("LOWER(network) = ANY(%s)")
        params.append(wanted)
    query_limit = max(1, int(limit))
    params.append(query_limit * 5 if wanted_category else query_limit)
    with db_connection(database_url, connect_timeout=8) as connection:
        with connection.cursor() as cursor:
            if wanted:
                cursor.execute(
                    f"""
                    SELECT
                        id, observed_at, network, chain_id, owner_address,
                        pair, pair_rank, priority_reason, route_path_json,
                        state, execution_phase, checks_passed, can_submit_order,
                        order_submission_enabled, auto_execute_requested,
                        control_mode, route_hop_constraints_enforced,
                        final_delta_amount, final_symbol, blocked_reasons_json,
                        cow_flashloan_intent_json, cow_sdk_result_json,
                        error, created_at, quote_json, precheck_json, market_state_json
                    FROM (
                        SELECT
                            id, observed_at, network, chain_id, owner_address,
                            pair, pair_rank, priority_reason, route_path_json,
                            state, execution_phase, checks_passed, can_submit_order,
                            order_submission_enabled, auto_execute_requested,
                            control_mode, route_hop_constraints_enforced,
                            final_delta_amount, final_symbol, blocked_reasons_json,
                            cow_flashloan_intent_json, cow_sdk_result_json,
                            error, created_at, quote_json, precheck_json, market_state_json,
                            ROW_NUMBER() OVER (
                                PARTITION BY LOWER(network)
                                ORDER BY created_at DESC, id DESC
                            ) AS network_row_number
                        FROM cow_execution_attempts
                        WHERE {" AND ".join(where)}
                    ) scoped
                    WHERE network_row_number <= %s
                    ORDER BY created_at DESC, id DESC
                    """,
                    tuple(params),
                )
            else:
                cursor.execute(
                    f"""
                    SELECT
                        id, observed_at, network, chain_id, owner_address,
                        pair, pair_rank, priority_reason, route_path_json,
                        state, execution_phase, checks_passed, can_submit_order,
                        order_submission_enabled, auto_execute_requested,
                        control_mode, route_hop_constraints_enforced,
                        final_delta_amount, final_symbol, blocked_reasons_json,
                        cow_flashloan_intent_json, cow_sdk_result_json,
                        error, created_at, quote_json, precheck_json, market_state_json
                    FROM cow_execution_attempts
                    WHERE {" AND ".join(where)}
                    ORDER BY created_at DESC, id DESC
                    LIMIT %s
                    """,
                    tuple(params),
                )
            rows = cursor.fetchall()
    result = []
    for row in rows:
        item = {
                "id": int(row[0]),
                "observed_at": row[1].isoformat() if row[1] else None,
                "network": row[2],
                "chain_id": row[3],
                "owner_address": row[4],
                "pair": row[5],
                "pair_rank": row[6],
                "priority_reason": row[7],
                "route_path": json.loads(row[8] or "[]"),
                "state": row[9],
                "execution_phase": row[10],
                "checks_passed": bool(row[11]),
                "can_submit_order": bool(row[12]),
                "order_submission_enabled": bool(row[13]),
                "auto_execute_requested": bool(row[14]),
                "control_mode": row[15],
                "route_hop_constraints_enforced": bool(row[16]),
                "final_delta_amount": row[17],
                "final_symbol": row[18],
                "blocked_reasons": _json_loads(row[19], []),
                "cow_flashloan_intent": _json_loads(row[20], {}),
                "cow_sdk_result": _json_loads(row[21], {}),
                "error": row[22],
                "created_at": row[23].isoformat() if row[23] else None,
                "quote": _json_loads(row[24], {}),
                "precheck": _json_loads(row[25], {}),
                "market_state": _json_loads(row[26], {}),
            }
        if wanted_category and cow_execution_attempt_category(item) != wanted_category:
            continue
        if not _category_within_retention(item):
            continue
        result.append(_decorate_attempt_review(item))
        if len(result) >= query_limit:
            break
    return result
