from db.storage import (
    ensure_database_schema,
    liquidation_execution_attempt_stats as db_liquidation_execution_attempt_stats,
    load_recent_liquidation_execution_attempts as db_load_recent_liquidation_execution_attempts,
    record_liquidation_execution_attempt as db_record_liquidation_execution_attempt,
)
from web.control_panel_liquidation_base import database_url_or_none


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
    if not database_url:
        return None
    try:
        ensure_database_schema(database_url)
        return db_record_liquidation_execution_attempt(
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
            error=error,
        )
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
            "attempts": db_load_recent_liquidation_execution_attempts(database_url, limit=limit),
            "stats": db_liquidation_execution_attempt_stats(database_url),
        }
    except Exception as exc:
        return {"configured": True, "error": str(exc), "attempts": [], "stats": empty_execution_attempt_stats()}


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
