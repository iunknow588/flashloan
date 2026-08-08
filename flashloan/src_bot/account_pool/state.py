from __future__ import annotations

from typing import Any

from core.sensitive_data import redact_sensitive_text
from page_state import AccountPoolResult


def _int_value(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def evaluate_account_pool_state(
    *,
    registry_window: dict[str, Any] | None,
    account_count: int | None = None,
    account_source: str | None = None,
    database_configured: bool = True,
) -> dict[str, Any]:
    window = dict(registry_window or {})
    source = str(account_source or "none")
    active_count = _int_value(window.get("active_count"))
    total_count = _int_value(window.get("total_count"))
    effective_count = max(active_count, _int_value(account_count))

    if not database_configured and effective_count <= 0:
        result = AccountPoolResult.MISSING
        reason = "DATABASE_URL is required or no fallback account file is available"
    elif source.endswith("-error"):
        result = AccountPoolResult.INCOMPLETE
        reason = f"account source is not healthy: {source}"
    elif total_count <= 0 and effective_count <= 0:
        result = AccountPoolResult.EMPTY
        reason = "account pool is empty"
    elif effective_count <= 0:
        result = AccountPoolResult.EMPTY
        reason = "no active liquidation accounts"
    elif not window.get("latest_scan_end_at"):
        result = AccountPoolResult.INCOMPLETE
        reason = "account pool has accounts but no completed scan window"
    else:
        result = AccountPoolResult.READY
        reason = "account pool is ready"

    return {
        "result": result.value,
        "ready": result == AccountPoolResult.READY,
        "reason": reason,
        "account_count": effective_count,
        "account_source": source,
        "registry_window": window,
    }


def account_pool_state_payload(panel: Any, *, force: bool = False) -> dict[str, Any]:
    database_configured = bool(panel.database_url_or_none())
    try:
        accounts, source = panel.load_liquidation_account_registry(force=force)
    except Exception as exc:
        accounts = []
        source = "database-error"
        registry = {
            "total_count": 0,
            "active_count": 0,
            "earliest_scan_start_at": None,
            "latest_scan_end_at": None,
            "error": redact_sensitive_text(exc),
        }
    else:
        registry = panel.liquidation_account_registry_window()
    return evaluate_account_pool_state(
        registry_window=registry,
        account_count=len(accounts),
        account_source=source,
        database_configured=database_configured,
    )
