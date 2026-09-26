from __future__ import annotations

import time
from typing import Any

from core.kv_rows import count, latest_rows, load_rows, upsert_row
from core.local_kv import ensure_store
from core.db.liquidation_attempts_mapping import execution_attempt_from_row as _execution_attempt_from_row


def _market_scope(market_id: str | None = None, chain_id: int | None = None) -> dict[str, Any]:
    return {
        "market_id": str(market_id or "default"),
        "chain_id": int(chain_id) if chain_id is not None else 0,
        "network": str(market_id or "local"),
        "protocol": "aave",
    }


def record_liquidation_execution_attempt(
    database_url: str,
    *,
    account: str | None = None,
    mode: str,
    state: str,
    blocked_reasons: list[str] | None = None,
    request_payload: dict[str, Any] | None = None,
    quote: dict[str, Any] | None = None,
    preflight: dict[str, Any] | None = None,
    tx_hash: str | None = None,
    receipt: dict[str, Any] | None = None,
    error: str | None = None,
    market_id: str | None = None,
    chain_id: int | None = None,
) -> int:
    ensure_store(database_url)
    scope = _market_scope(market_id, chain_id)
    attempt_id = count(database_url, "liquidation_execution_attempts") + 1
    upsert_row(
        database_url,
        "liquidation_execution_attempts",
        str(attempt_id),
        {
            "id": attempt_id,
            **scope,
            "account": account,
            "mode": mode,
            "state": state,
            "blocked_reasons": blocked_reasons or [],
            "request": request_payload or {},
            "quote": quote or {},
            "preflight": preflight or {},
            "tx_hash": tx_hash,
            "receipt": receipt or {},
            "error": error,
            "created_at": time.time(),
            "updated_at": time.time(),
        },
    )
    return attempt_id


def _filtered_rows(database_url: str, *, market_id: str | None = None, chain_id: int | None = None) -> list[dict]:
    scope = _market_scope(market_id, chain_id)
    rows = load_rows(database_url, "liquidation_execution_attempts")
    if market_id is not None:
        rows = [row for row in rows if str(row.get("market_id")) == scope["market_id"]]
    if chain_id is not None:
        rows = [row for row in rows if int(row.get("chain_id") or 0) == scope["chain_id"]]
    rows.sort(key=lambda row: str(row.get("created_at") or ""), reverse=True)
    return rows


def load_recent_liquidation_execution_attempts(
    database_url: str,
    limit: int = 20,
    *,
    market_id: str | None = None,
    chain_id: int | None = None,
) -> list[dict[str, Any]]:
    return _filtered_rows(database_url, market_id=market_id, chain_id=chain_id)[:max(1, int(limit))]


def load_latest_liquidation_execution_attempts_for_accounts(
    database_url: str,
    accounts: list[str],
    *,
    market_id: str | None = None,
    chain_id: int | None = None,
) -> list[dict[str, Any]]:
    requested = {str(account).strip().lower() for account in accounts if str(account).strip()}
    rows = _filtered_rows(database_url, market_id=market_id, chain_id=chain_id)
    latest: dict[str, dict] = {}
    for row in rows:
        key = str(row.get("account") or "").lower()
        if key not in requested:
            continue
        latest.setdefault(key, row)
    return list(latest.values())


def load_liquidation_execution_attempts_for_account(
    database_url: str,
    account: str,
    limit: int = 20,
    *,
    market_id: str | None = None,
    chain_id: int | None = None,
) -> list[dict[str, Any]]:
    rows = _filtered_rows(database_url, market_id=market_id, chain_id=chain_id)
    return [row for row in rows if str(row.get("account") or "").lower() == str(account).lower()][:max(1, int(limit))]


def liquidation_execution_attempt_stats(
    database_url: str,
    *,
    market_id: str | None = None,
    chain_id: int | None = None,
) -> dict[str, int]:
    rows = _filtered_rows(database_url, market_id=market_id, chain_id=chain_id)
    return {
        "total": len(rows),
        "blocked": sum(1 for row in rows if row.get("state") == "submission_blocked"),
        "submitted": sum(1 for row in rows if row.get("tx_hash")),
        "confirmed_success": sum(1 for row in rows if row.get("state") == "confirmed_success"),
        "confirmed_failed": sum(1 for row in rows if row.get("state") == "confirmed_failed"),
        "static_call_failed": sum(1 for row in rows if row.get("state") == "static_call_failed"),
        "errors": sum(1 for row in rows if row.get("error")),
    }


__all__ = [
    "_execution_attempt_from_row",
    "load_latest_liquidation_execution_attempts_for_accounts",
    "load_liquidation_execution_attempts_for_account",
    "load_recent_liquidation_execution_attempts",
    "liquidation_execution_attempt_stats",
    "record_liquidation_execution_attempt",
]
