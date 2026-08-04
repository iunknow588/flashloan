import json
from typing import Any

from db.storage_common import db_connection, require_psycopg


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
) -> int:
    psycopg = require_psycopg()
    with db_connection(database_url, connect_timeout=8) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO liquidation_execution_attempts (
                    account, mode, state, blocked_reasons_json,
                    request_json, quote_json, preflight_json,
                    tx_hash, receipt_json, error, created_at, updated_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
                RETURNING id
                """,
                (
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


def record_liquidation_failure_sample(
    database_url: str,
    *,
    account: str | None = None,
    block_number: int | None = None,
    collateral_asset: str | None = None,
    debt_asset: str | None = None,
    failure_type: str,
    failure_reason: str | None = None,
    payload: dict[str, Any] | None = None,
    source: str = "execution_attempt",
) -> int:
    psycopg = require_psycopg()
    with db_connection(database_url, connect_timeout=8) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO liquidation_failure_samples (
                    account, block_number, collateral_asset, debt_asset,
                    failure_type, failure_reason, payload_json, source, created_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW())
                RETURNING id
                """,
                (
                    account,
                    block_number,
                    collateral_asset,
                    debt_asset,
                    failure_type,
                    failure_reason,
                    json.dumps(payload or {}, ensure_ascii=True, separators=(",", ":")),
                    source,
                ),
            )
            row = cursor.fetchone()
            return int(row[0]) if row else 0


def load_recent_liquidation_failure_samples(database_url: str, limit: int = 20) -> list[dict[str, Any]]:
    psycopg = require_psycopg()
    with db_connection(database_url, connect_timeout=8) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    id, account, block_number, collateral_asset, debt_asset,
                    failure_type, failure_reason, payload_json, source, created_at
                FROM liquidation_failure_samples
                ORDER BY created_at DESC, id DESC
                LIMIT %s
                """,
                (max(1, int(limit)),),
            )
            rows = cursor.fetchall()
    samples: list[dict[str, Any]] = []
    for row in rows:
        samples.append(
            {
                "id": int(row[0]),
                "account": str(row[1]) if row[1] else None,
                "block_number": int(row[2]) if row[2] is not None else None,
                "collateral_asset": str(row[3]) if row[3] else None,
                "debt_asset": str(row[4]) if row[4] else None,
                "failure_type": str(row[5]),
                "failure_reason": str(row[6]) if row[6] else None,
                "payload": _json_or_default(row[7], {}),
                "source": str(row[8]),
                "created_at": row[9].isoformat() if row[9] else None,
            }
        )
    return samples


def load_liquidation_failure_samples_for_account(
    database_url: str,
    account: str,
    limit: int = 20,
) -> list[dict[str, Any]]:
    psycopg = require_psycopg()
    with db_connection(database_url, connect_timeout=8) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    id, account, block_number, collateral_asset, debt_asset,
                    failure_type, failure_reason, payload_json, source, created_at
                FROM liquidation_failure_samples
                WHERE lower(account) = lower(%s)
                ORDER BY created_at DESC, id DESC
                LIMIT %s
                """,
                (account, max(1, int(limit))),
            )
            rows = cursor.fetchall()
    return [
        {
            "id": int(row[0]),
            "account": str(row[1]) if row[1] else None,
            "block_number": int(row[2]) if row[2] is not None else None,
            "collateral_asset": str(row[3]) if row[3] else None,
            "debt_asset": str(row[4]) if row[4] else None,
            "failure_type": str(row[5]),
            "failure_reason": str(row[6]) if row[6] else None,
            "payload": _json_or_default(row[7], {}),
            "source": str(row[8]),
            "created_at": row[9].isoformat() if row[9] else None,
        }
        for row in rows
    ]


def load_recent_liquidation_execution_attempts(database_url: str, limit: int = 20) -> list[dict[str, Any]]:
    psycopg = require_psycopg()
    with db_connection(database_url, connect_timeout=8) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    id, account, mode, state, blocked_reasons_json,
                    request_json, quote_json, preflight_json,
                    tx_hash, receipt_json, error, created_at, updated_at
                FROM liquidation_execution_attempts
                ORDER BY created_at DESC, id DESC
                LIMIT %s
                """,
                (max(1, int(limit)),),
            )
            rows = cursor.fetchall()
    attempts: list[dict[str, Any]] = []
    for row in rows:
        attempts.append(
            {
                "id": int(row[0]),
                "account": str(row[1]) if row[1] else None,
                "mode": str(row[2]),
                "state": str(row[3]),
                "blocked_reasons": _json_or_default(row[4], []),
                "request": _json_or_default(row[5], {}),
                "quote": _json_or_default(row[6], {}),
                "preflight": _json_or_default(row[7], {}),
                "tx_hash": str(row[8]) if row[8] else None,
                "receipt": _json_or_default(row[9], {}),
                "error": str(row[10]) if row[10] else None,
                "created_at": row[11].isoformat() if row[11] else None,
                "updated_at": row[12].isoformat() if row[12] else None,
            }
        )
    return attempts


def load_liquidation_execution_attempts_for_account(
    database_url: str,
    account: str,
    limit: int = 20,
) -> list[dict[str, Any]]:
    psycopg = require_psycopg()
    with db_connection(database_url, connect_timeout=8) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    id, account, mode, state, blocked_reasons_json,
                    request_json, quote_json, preflight_json,
                    tx_hash, receipt_json, error, created_at, updated_at
                FROM liquidation_execution_attempts
                WHERE lower(account) = lower(%s)
                ORDER BY created_at DESC, id DESC
                LIMIT %s
                """,
                (account, max(1, int(limit))),
            )
            rows = cursor.fetchall()
    return [
        {
            "id": int(row[0]),
            "account": str(row[1]) if row[1] else None,
            "mode": str(row[2]),
            "state": str(row[3]),
            "blocked_reasons": _json_or_default(row[4], []),
            "request": _json_or_default(row[5], {}),
            "quote": _json_or_default(row[6], {}),
            "preflight": _json_or_default(row[7], {}),
            "tx_hash": str(row[8]) if row[8] else None,
            "receipt": _json_or_default(row[9], {}),
            "error": str(row[10]) if row[10] else None,
            "created_at": row[11].isoformat() if row[11] else None,
            "updated_at": row[12].isoformat() if row[12] else None,
        }
        for row in rows
    ]


def liquidation_execution_attempt_stats(database_url: str) -> dict[str, int]:
    psycopg = require_psycopg()
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
                """
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
