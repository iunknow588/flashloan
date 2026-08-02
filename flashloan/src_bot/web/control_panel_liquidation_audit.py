from db.storage import (
    ensure_database_schema,
    liquidation_execution_attempt_stats as db_liquidation_execution_attempt_stats,
    load_recent_liquidation_execution_attempts as db_load_recent_liquidation_execution_attempts,
    load_recent_liquidation_failure_samples as db_load_recent_liquidation_failure_samples,
    load_liquidation_execution_attempts_for_account as db_load_liquidation_execution_attempts_for_account,
    load_liquidation_failure_samples_for_account as db_load_liquidation_failure_samples_for_account,
    record_liquidation_execution_attempt as db_record_liquidation_execution_attempt,
    record_liquidation_failure_sample as db_record_liquidation_failure_sample,
)
from core.config_schema import parse_env_int
from core.sensitive_data import redact_sensitive_text
from web.control_panel_liquidation_base import database_url_or_none
from web.control_panel_liquidation_pause import (
    clear_pause_guard,
    load_pause_guard_state,
    record_pause_guard_event,
)
from web.page_state import normalize_execution_phase, normalize_tx_hash, receipt_status
from execution.liquidation_preflight import SOFT_BLOCK_REASONS

LIQUIDATION_PAUSE_GUARD_PATH = None


def _safe_error_message(error: object | None) -> str | None:
    if error is None:
        return None
    return redact_sensitive_text(error)


def _decorate_execution_attempts(rows: list[dict]) -> list[dict]:
    decorated: list[dict] = []
    for row in rows:
        item = dict(row)
        item["execution_phase"] = normalize_execution_phase(item)
        item["tx_hash"] = normalize_tx_hash(item)
        decorated.append(item)
    return decorated


def record_liquidation_execution_attempt_safely(
    *,
    account: str | None,
    mode: str,
    state: str,
    blocked_reasons: list[str] | None = None,
    request_payload: dict | None = None,
    quote: dict | None = None,
    preflight: dict | None = None,
    tx_hash: str | None = None,
    receipt: dict | None = None,
    error: str | None = None,
) -> int | None:
    database_url = database_url_or_none()
    safe_error = _safe_error_message(error)
    _record_pause_guard_if_configured(state, blocked_reasons, safe_error)
    if not database_url:
        return None
    try:
        ensure_database_schema(database_url)
        attempt_id = db_record_liquidation_execution_attempt(
            database_url,
            account=account,
            mode=mode,
            state=state,
            blocked_reasons=blocked_reasons,
            request_payload=request_payload,
            quote=quote,
            preflight=preflight,
            tx_hash=tx_hash,
            receipt=receipt,
            error=safe_error,
        )
        if error or blocked_reasons or state in {"submission_blocked", "submission_failed", "static_call_failed", "confirmed_failed"}:
            db_record_liquidation_failure_sample(
                database_url,
                account=account,
                block_number=_payload_block_number(preflight, quote),
                collateral_asset=_request_value(request_payload, "collateralAsset"),
                debt_asset=_request_value(request_payload, "debtAsset"),
                failure_type=_failure_type(state, blocked_reasons, safe_error),
                failure_reason=safe_error or ", ".join(blocked_reasons or []) or state,
                payload=build_failure_sample_payload(
                    mode=mode,
                    state=state,
                    blocked_reasons=blocked_reasons,
                    request_payload=request_payload,
                    quote=quote,
                    preflight=preflight,
                    tx_hash=tx_hash,
                    receipt=receipt,
                    error=safe_error,
                ),
            )
        return attempt_id
    except Exception:
        return None


def recent_liquidation_execution_attempts(limit: int = 20) -> dict:
    database_url = database_url_or_none()
    if not database_url:
        return {"configured": False, "attempts": [], "stats": empty_execution_attempt_stats()}
    try:
        ensure_database_schema(database_url)
        return {
            "configured": True,
            "attempts": _decorate_execution_attempts(db_load_recent_liquidation_execution_attempts(database_url, limit=limit)),
            "stats": db_liquidation_execution_attempt_stats(database_url),
        }
    except Exception as exc:
        return {"configured": True, "error": _safe_error_message(exc), "attempts": [], "stats": empty_execution_attempt_stats()}


def recent_liquidation_failure_samples(limit: int = 20) -> dict:
    database_url = database_url_or_none()
    if not database_url:
        return {"configured": False, "samples": []}
    try:
        ensure_database_schema(database_url)
        return {
            "configured": True,
            "samples": db_load_recent_liquidation_failure_samples(database_url, limit=limit),
        }
    except Exception as exc:
        return {"configured": True, "error": _safe_error_message(exc), "samples": []}


def liquidation_execution_attempts_for_account(account: str, limit: int = 20) -> dict:
    database_url = database_url_or_none()
    if not database_url:
        return {"configured": False, "attempts": []}
    try:
        ensure_database_schema(database_url)
        return {
            "configured": True,
            "account": account,
            "attempts": _decorate_execution_attempts(db_load_liquidation_execution_attempts_for_account(database_url, account, limit=limit)),
        }
    except Exception as exc:
        return {"configured": True, "account": account, "error": _safe_error_message(exc), "attempts": []}


def liquidation_failure_samples_for_account(account: str, limit: int = 20) -> dict:
    database_url = database_url_or_none()
    if not database_url:
        return {"configured": False, "samples": []}
    try:
        ensure_database_schema(database_url)
        return {
            "configured": True,
            "account": account,
            "samples": db_load_liquidation_failure_samples_for_account(database_url, account, limit=limit),
        }
    except Exception as exc:
        return {"configured": True, "account": account, "error": _safe_error_message(exc), "samples": []}


def liquidation_pause_guard_status() -> dict:
    if LIQUIDATION_PAUSE_GUARD_PATH is None:
        return {"configured": False, "paused": False}
    state = load_pause_guard_state(LIQUIDATION_PAUSE_GUARD_PATH)
    return {"configured": True, **state}


def clear_liquidation_pause_guard_status() -> dict:
    if LIQUIDATION_PAUSE_GUARD_PATH is None:
        return {"configured": False, "paused": False}
    return {"configured": True, **clear_pause_guard(LIQUIDATION_PAUSE_GUARD_PATH)}


def empty_execution_attempt_stats() -> dict[str, int]:
    return {
        "total": 0,
        "blocked": 0,
        "submitted": 0,
        "confirmed_success": 0,
        "confirmed_failed": 0,
        "static_call_failed": 0,
        "errors": 0,
    }


def _request_value(request_payload: dict | None, key: str) -> str | None:
    value = (request_payload or {}).get(key)
    return str(value) if value else None


def _payload_block_number(preflight: dict | None, quote: dict | None) -> int | None:
    for source, key in ((quote or {}, "quote_block"), (preflight or {}, "block_number")):
        try:
            if source.get(key) is not None:
                return int(source[key])
        except (TypeError, ValueError):
            continue
    return None


def build_failure_sample_payload(
    *,
    mode: str,
    state: str,
    blocked_reasons: list[str] | None = None,
    request_payload: dict | None = None,
    quote: dict | None = None,
    preflight: dict | None = None,
    tx_hash: str | None = None,
    receipt: dict | None = None,
    error: str | None = None,
) -> dict:
    blocked = list(blocked_reasons or [])
    payload = {
        "mode": mode,
        "state": state,
        "execution_phase": normalize_execution_phase(
            {
                "state": state,
                "preflight": preflight or {},
                "receipt": receipt or {},
            }
        ),
        "blocked_reasons": blocked,
        "request": request_payload or {},
        "quote": quote or {},
        "preflight": preflight or {},
        "tx_hash": normalize_tx_hash(
            {
                "tx_hash": tx_hash,
                "preflight": preflight or {},
                "receipt": receipt or {},
            }
        ),
        "receipt_status": receipt_status(receipt or {}),
        "receipt": receipt or {},
        "error": error,
        "retryable": failure_retryable(state, blocked, error),
    }
    return {key: value for key, value in payload.items() if value is not None}


def failure_retryable(state: str, blocked_reasons: list[str] | None, error: str | None = None) -> bool:
    state = str(state or "")
    blocked = set(blocked_reasons or [])
    if state == "confirmed_failed":
        return False
    if blocked:
        return bool(blocked and blocked.issubset(SOFT_BLOCK_REASONS))
    if state in {"submission_failed", "static_call_failed"}:
        return True
    return bool(error)


def _failure_type(state: str, blocked_reasons: list[str] | None, error: str | None) -> str:
    if blocked_reasons:
        return str(blocked_reasons[0])
    if state:
        return state
    return "error" if error else "unknown"


def _record_pause_guard_if_configured(state: str, blocked_reasons: list[str] | None, error: str | None) -> None:
    if LIQUIDATION_PAUSE_GUARD_PATH is None:
        return
    try:
        import os

        enabled = os.getenv("LIQUIDATION_AUTO_PAUSE_ENABLED", "true").strip().lower() not in {"0", "false", "no", "off"}
        threshold, _ = parse_env_int("LIQUIDATION_AUTO_PAUSE_FAILURE_THRESHOLD", 3, minimum=1)
        record_pause_guard_event(
            LIQUIDATION_PAUSE_GUARD_PATH,
            state_name=state,
            blocked_reasons=blocked_reasons,
            error=error,
            enabled=enabled,
            threshold=threshold,
        )
    except Exception:
        pass
