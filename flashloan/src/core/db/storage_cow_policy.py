from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from typing import Any

from core.evidence_scope import evidence_scope_matches, infer_evidence_scope
from core.db.storage_common import db_connection
from core.db.cow_execution_mapping import scope_consistency_reasons
from core.db.cow_execution_review import (
    _attempt_route_address_identity,
    _build_review_summary,
    _market_state_summary,
)

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
    "quote_failed",
    "unsupported",
    "unsupported_route",
    "unsupported_token",
    "live_quote_failed",
    "live_quote_verified",
    "ready_for_transaction_development",
    "transaction_development_required",
    "missing_token",
    "quote_unavailable",
    "flashloan_payload_required",
    "price_guard_failed",
    "submission_paused",
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
    auto_execute = row.get("auto_execute") if isinstance(row.get("auto_execute"), dict) else {}
    auto_execute_result = auto_execute.get("result") if isinstance(auto_execute.get("result"), dict) else {}
    auto_tx_hash = str(
        row.get("tx_hash")
        or row.get("transaction_hash")
        or auto_execute_result.get("txHash")
        or auto_execute_result.get("transactionHash")
        or auto_execute_result.get("transaction_hash")
        or (row.get("quote") or {}).get("tx_hash")
        or (row.get("quote") or {}).get("transaction_hash")
        or ""
    ).strip()
    sdk = _cow_sdk_result_from_attempt(row)
    sdk_status = _cow_sdk_status_from_attempt(row)
    submission_attempted = bool(
        sdk.get("submission_attempted")
        or sdk.get("submission_order_id")
        or sdk.get("submission_tx_hash")
    )

    if bool(auto_execute.get("executed")) and auto_tx_hash:
        return COW_ATTEMPT_CATEGORY_EXECUTION_SUCCESS
    if auto_tx_hash and state in _SUCCESS_STATES:
        return COW_ATTEMPT_CATEGORY_EXECUTION_SUCCESS
    if sdk_status in _SUCCESS_STATES or sdk_status in _SUCCESS_PHASES or state in _SUCCESS_STATES or phase in _SUCCESS_PHASES:
        return COW_ATTEMPT_CATEGORY_EXECUTION_SUCCESS
    if not submission_attempted and (sdk_status in {"quote_failed", "quote_unavailable"} or state in {"quote_failed", "quote_unavailable"}):
        return COW_ATTEMPT_CATEGORY_NOT_EXECUTABLE
    terminal_failure_states = {
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
