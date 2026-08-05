from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from db.storage_common import db_connection


def _json_text(value: Any) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


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
                    final_delta_amount TEXT,
                    final_symbol TEXT,
                    blocked_reasons_json TEXT,
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
        "execution_phase": "quote_precheck",
        "checks_passed": bool(precheck.get("checks_passed")),
        "can_submit_order": bool(precheck.get("can_submit_order")),
        "order_submission_enabled": bool(precheck.get("order_submission_enabled")),
        "auto_execute_requested": bool(precheck.get("auto_execute_requested")),
        "final_delta_amount": quote.get("final_delta_amount"),
        "final_symbol": quote.get("final_symbol"),
        "blocked_reasons": reasons if isinstance(reasons, list) else [str(reasons)],
        "quote": quote,
        "precheck": precheck,
        "market_state": {
            "observed_at": market_state.get("observed_at"),
            "window_seconds": market_state.get("window_seconds"),
            "price_source": market_state.get("price_source"),
            "market_state_source": market_state.get("market_state_source"),
            "fallback_reason": market_state.get("fallback_reason"),
            "cow_filter": market_state.get("cow_filter"),
        },
        "error": quote.get("error"),
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


def record_cow_execution_attempts(database_url: str, attempts: list[dict[str, Any]]) -> list[int]:
    if not attempts:
        return []
    ensure_cow_execution_attempts_table(database_url)
    ids = []
    with db_connection(database_url, connect_timeout=8) as connection:
        with connection.cursor() as cursor:
            for item in attempts:
                cursor.execute(
                    """
                    INSERT INTO cow_execution_attempts (
                        observed_at, network, chain_id, owner_address,
                        pair, pair_rank, priority_reason, route_path_json,
                        state, execution_phase, checks_passed, can_submit_order,
                        order_submission_enabled, auto_execute_requested,
                        final_delta_amount, final_symbol, blocked_reasons_json,
                        quote_json, precheck_json, market_state_json, error, created_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
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
                        _json_text(item.get("route_path") or []),
                        item.get("state") or "unknown",
                        item.get("execution_phase") or "quote_precheck",
                        bool(item.get("checks_passed")),
                        bool(item.get("can_submit_order")),
                        bool(item.get("order_submission_enabled")),
                        bool(item.get("auto_execute_requested")),
                        item.get("final_delta_amount"),
                        item.get("final_symbol"),
                        _json_text(item.get("blocked_reasons") or []),
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


def append_cow_execution_attempts_jsonl(path: Path, attempts: list[dict[str, Any]]) -> int:
    if not attempts:
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    created_at = datetime.now(timezone.utc).isoformat()
    with path.open("a", encoding="utf-8") as handle:
        for item in attempts:
            handle.write(_json_text({**item, "created_at": created_at}) + "\n")
    return len(attempts)


def load_recent_cow_execution_attempts_jsonl(path: Path, limit: int = 50) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()[-max(1, int(limit)) :]
    rows = []
    for line in reversed(lines):
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            rows.append(payload)
    return rows


def load_recent_cow_execution_attempts(database_url: str, limit: int = 50) -> list[dict[str, Any]]:
    ensure_cow_execution_attempts_table(database_url)
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
                    error, created_at
                FROM cow_execution_attempts
                ORDER BY created_at DESC, id DESC
                LIMIT %s
                """,
                (max(1, int(limit)),),
            )
            rows = cursor.fetchall()
    result = []
    for row in rows:
        result.append(
            {
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
                "final_delta_amount": row[15],
                "final_symbol": row[16],
                "blocked_reasons": json.loads(row[17] or "[]"),
                "error": row[18],
                "created_at": row[19].isoformat() if row[19] else None,
            }
        )
    return result
