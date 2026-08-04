import json
from typing import Any

from core.market_config import liquidation_market_config, liquidation_market_config_for
from db.storage_common import db_connection, require_psycopg
from db.storage_liquidation_failures import (
    load_liquidation_failure_samples_for_account,
    load_recent_liquidation_failure_samples,
    record_liquidation_failure_sample,
)


def _market_scope(market_id: str | None = None, chain_id: int | None = None) -> dict[str, Any]:
    market = liquidation_market_config_for(market_id, chain_id=chain_id) if (market_id is not None or chain_id is not None) else liquidation_market_config()
    return {
        "market_id": market.market_id,
        "chain_id": market.chain_id,
        "network": market.network,
        "protocol": market.protocol,
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
    psycopg = require_psycopg()
    scope = _market_scope(market_id, chain_id)
    with db_connection(database_url, connect_timeout=8) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO liquidation_execution_attempts (
                    market_id, chain_id, network, protocol,
                    account, mode, state, blocked_reasons_json,
                    request_json, quote_json, preflight_json,
                    tx_hash, receipt_json, error, created_at, updated_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
                RETURNING id
                """,
                (
                    scope["market_id"],
                    scope["chain_id"],
                    scope["network"],
                    scope["protocol"],
                    account,
                    mode,
                    state,
                    json.dumps(blocked_reasons or [], ensure_ascii=True, separators=(",", ":")),
                    json.dumps(request_payload or {}, ensure_ascii=True, separators=(",", ":")),
                    json.dumps(quote or {}, ensure_ascii=True, separators=(",", ":")),
                    json.dumps(preflight or {}, ensure_ascii=True, separators=(",", ":")),
                    tx_hash,
                    json.dumps(receipt or {}, ensure_ascii=True, separators=(",", ":")),
                    error,
                ),
            )
            row = cursor.fetchone()
            return int(row[0]) if row else 0


def load_recent_liquidation_execution_attempts(
    database_url: str,
    limit: int = 20,
    *,
    market_id: str | None = None,
    chain_id: int | None = None,
) -> list[dict[str, Any]]:
    psycopg = require_psycopg()
    scope = _market_scope(market_id, chain_id)
    with db_connection(database_url, connect_timeout=8) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    market_id, chain_id, network, protocol,
                    id, account, mode, state, blocked_reasons_json,
                    request_json, quote_json, preflight_json,
                    tx_hash, receipt_json, error, created_at, updated_at
                FROM liquidation_execution_attempts
                WHERE market_id = %s AND chain_id = %s
                ORDER BY created_at DESC, id DESC
                LIMIT %s
                """,
                (scope["market_id"], scope["chain_id"], max(1, int(limit))),
            )
            rows = cursor.fetchall()
    return [_execution_attempt_from_row(row, scope) for row in rows]


def _execution_attempt_from_row(row: Any, scope: dict[str, Any]) -> dict[str, Any]:
    if len(row) == 13:
        row = (scope["market_id"], scope["chain_id"], scope["network"], scope["protocol"], *row)
    return {
        "market_id": str(row[0]),
        "chain_id": int(row[1]) if row[1] is not None else None,
        "network": str(row[2]) if row[2] else None,
        "protocol": str(row[3]) if row[3] else None,
        "id": int(row[4]),
        "account": str(row[5]) if row[5] else None,
        "mode": str(row[6]),
        "state": str(row[7]),
        "blocked_reasons": _json_or_default(row[8], []),
        "request": _json_or_default(row[9], {}),
        "quote": _json_or_default(row[10], {}),
        "preflight": _json_or_default(row[11], {}),
        "tx_hash": str(row[12]) if row[12] else None,
        "receipt": _json_or_default(row[13], {}),
        "error": str(row[14]) if row[14] else None,
        "created_at": row[15].isoformat() if row[15] else None,
        "updated_at": row[16].isoformat() if row[16] else None,
    }


def load_latest_liquidation_execution_attempts_for_accounts(
    database_url: str,
    accounts: list[str],
    *,
    market_id: str | None = None,
    chain_id: int | None = None,
) -> list[dict[str, Any]]:
    requested_accounts = list(dict.fromkeys(str(account or "").strip() for account in accounts if str(account or "").strip()))
    if not requested_accounts:
        return []
    psycopg = require_psycopg()
    scope = _market_scope(market_id, chain_id)
    lower_accounts = [account.lower() for account in requested_accounts]
    with db_connection(database_url, connect_timeout=8) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT DISTINCT ON (lower(account))
                    market_id, chain_id, network, protocol,
                    id, account, mode, state, blocked_reasons_json,
                    request_json, quote_json, preflight_json,
                    tx_hash, receipt_json, error, created_at, updated_at
                FROM liquidation_execution_attempts
                WHERE market_id = %s
                  AND chain_id = %s
                  AND (account = ANY(%s) OR lower(account) = ANY(%s))
                ORDER BY lower(account), created_at DESC, id DESC
                """,
                (scope["market_id"], scope["chain_id"], requested_accounts, lower_accounts),
            )
            rows = cursor.fetchall()
    return [_execution_attempt_from_row(row, scope) for row in rows]


def load_liquidation_execution_attempts_for_account(
    database_url: str,
    account: str,
    limit: int = 20,
    *,
    market_id: str | None = None,
    chain_id: int | None = None,
) -> list[dict[str, Any]]:
    psycopg = require_psycopg()
    scope = _market_scope(market_id, chain_id)
    with db_connection(database_url, connect_timeout=8) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    market_id, chain_id, network, protocol,
                    id, account, mode, state, blocked_reasons_json,
                    request_json, quote_json, preflight_json,
                    tx_hash, receipt_json, error, created_at, updated_at
                FROM liquidation_execution_attempts
                WHERE lower(account) = lower(%s)
                  AND market_id = %s
                  AND chain_id = %s
                ORDER BY created_at DESC, id DESC
                LIMIT %s
                """,
                (account, scope["market_id"], scope["chain_id"], max(1, int(limit))),
            )
            rows = cursor.fetchall()
    result = []
    for row in rows:
        if len(row) == 13:
            row = (scope["market_id"], scope["chain_id"], scope["network"], scope["protocol"], *row)
        result.append(
            {
                "market_id": str(row[0]),
                "chain_id": int(row[1]) if row[1] is not None else None,
                "network": str(row[2]) if row[2] else None,
                "protocol": str(row[3]) if row[3] else None,
                "id": int(row[4]),
                "account": str(row[5]) if row[5] else None,
                "mode": str(row[6]),
                "state": str(row[7]),
                "blocked_reasons": _json_or_default(row[8], []),
                "request": _json_or_default(row[9], {}),
                "quote": _json_or_default(row[10], {}),
                "preflight": _json_or_default(row[11], {}),
                "tx_hash": str(row[12]) if row[12] else None,
                "receipt": _json_or_default(row[13], {}),
                "error": str(row[14]) if row[14] else None,
                "created_at": row[15].isoformat() if row[15] else None,
                "updated_at": row[16].isoformat() if row[16] else None,
            }
        )
    return result


def liquidation_execution_attempt_stats(
    database_url: str,
    *,
    market_id: str | None = None,
    chain_id: int | None = None,
) -> dict[str, int]:
    psycopg = require_psycopg()
    scope = _market_scope(market_id, chain_id)
    with db_connection(database_url, connect_timeout=8) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    COUNT(*) AS total,
                    COUNT(*) FILTER (WHERE state = 'submission_blocked') AS blocked,
                    COUNT(*) FILTER (WHERE tx_hash IS NOT NULL) AS submitted,
                    COUNT(*) FILTER (WHERE state = 'confirmed_success') AS confirmed_success,
                    COUNT(*) FILTER (WHERE state = 'confirmed_failed') AS confirmed_failed,
                    COUNT(*) FILTER (WHERE state = 'static_call_failed') AS static_call_failed,
                    COUNT(*) FILTER (WHERE error IS NOT NULL) AS errors
                FROM liquidation_execution_attempts
                WHERE market_id = %s AND chain_id = %s
                """
                ,
                (scope["market_id"], scope["chain_id"]),
            )
            row = cursor.fetchone() or (0, 0, 0, 0, 0, 0, 0)
    return {
        "total": int(row[0] or 0),
        "blocked": int(row[1] or 0),
        "submitted": int(row[2] or 0),
        "confirmed_success": int(row[3] or 0),
        "confirmed_failed": int(row[4] or 0),
        "static_call_failed": int(row[5] or 0),
        "errors": int(row[6] or 0),
    }


def _json_or_default(value: Any, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value) if isinstance(value, str) else default
    except json.JSONDecodeError:
        return default
