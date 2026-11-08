from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
from typing import Any

from core.kv_rows import collection_key
from core.db.storage_common import db_connection
from core.db.cow_execution_mapping import SELECTED_PROFIT_CAMEL_KEYS, selected_profit_mapping
from core.db.storage_cow_execution_compact import compact_cow_execution_attempt_row
from core.db.storage_cow_policy import (
    _decorate_attempt_review,
)
from core.db.storage_cow_execution_schema import (
    DEFAULT_COW_EXECUTION_RETENTION_DAYS,
    _COW_ATTEMPT_TABLE,
    _COW_TABLES,
    _cow_table_for_category,
    ensure_cow_execution_attempts_table,
)
from core.db.storage_cow_execution_query import _row_scope_values, _scope_sql


def _sqlite_attempt_key(item: dict[str, Any]) -> str:
    """Build a stable mirror key so repeated writes replace the same attempt."""
    network = str(item.get("network") or item.get("cow_network") or "local").strip().lower()
    try:
        chain_id = int(item.get("chain_id") or item.get("cow_chain_id") or 0)
    except (TypeError, ValueError):
        chain_id = 0
    tx_hash = str(
        item.get("tx_hash")
        or item.get("transaction_hash")
        or (item.get("quote") or {}).get("tx_hash")
        or (item.get("quote") or {}).get("transaction_hash")
        or ""
    ).strip().lower()
    observed_at = str(item.get("observed_at") or item.get("created_at") or item.get("updated_at") or "")
    route_path = item.get("route_path") or []
    route_addresses = item.get("route_addresses") or (item.get("quote") or {}).get("routeAddresses") or []
    identity_json = json.dumps(
        [
            route_path,
            route_addresses,
            item.get("pair") or item.get("symbol_pair") or "",
            item.get("priority_reason") or "",
            item.get("execution_phase") or "",
        ],
        ensure_ascii=True,
        sort_keys=True,
    )
    identity_hash = hashlib.sha256(identity_json.encode("utf-8")).hexdigest()[:32]
    return collection_key(
        "attempt",
        network,
        chain_id,
        tx_hash or identity_hash,
        observed_at,
    )


def prune_cow_execution_attempts(database_url: str, retention_days: int = DEFAULT_COW_EXECUTION_RETENTION_DAYS) -> int:
    ensure_cow_execution_attempts_table(database_url)
    cutoff = datetime.now(timezone.utc) - timedelta(days=max(1, int(retention_days)))
    cleared = 0
    with db_connection(database_url) as connection:
        for table in _COW_TABLES:
            cursor = connection.execute(
                f"DELETE FROM {table} WHERE created_at != '' AND created_at < ?",
                (cutoff.isoformat(),),
            )
            cleared += int(cursor.rowcount or 0)
    return cleared


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
    created_at = datetime.now(timezone.utc).isoformat()
    ids: list[int] = []
    with db_connection(database_url) as connection:
        base_id = 0
        for table in _COW_TABLES:
            base_row = connection.execute(
                f"SELECT COALESCE(MAX(id), 0) AS max_id FROM {table}"
            ).fetchone()
            base_id = max(base_id, int(base_row["max_id"] or 0))
        next_id = base_id
        for item in attempts:
            if not isinstance(item, dict):
                continue
            next_id += 1
            attempt_id = next_id
            key = _sqlite_attempt_key(item)
            payload = dict(item)
            payload["id"] = attempt_id
            payload["_key"] = key
            payload["created_at"] = str(payload.get("created_at") or payload.get("observed_at") or created_at)
            network, chain_id, market_id, pair, tx_hash, row_created_at, category = _row_scope_values(payload)
            review_row = _decorate_attempt_review(dict(payload))
            summary = compact_cow_execution_attempt_row(review_row)
            selected = selected_profit_mapping(payload)
            selected_values = tuple(
                selected.get(key) for key in SELECTED_PROFIT_CAMEL_KEYS
            )
            table = _cow_table_for_category(category)
            for other_table in _COW_TABLES:
                if other_table != table:
                    connection.execute(
                        f"DELETE FROM {other_table} WHERE attempt_key = ?",
                        (key,),
                    )
            connection.execute(
                f"""
                INSERT INTO {table} (
                    attempt_key, network, chain_id, market_id, pair, tx_hash,
                    selected_profit_raw, selected_profit_amount,
                    selected_profit_amount_symbol, selected_profit_source,
                    selected_profit_missing_reason, created_at, category,
                    payload_json, summary_json, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(attempt_key) DO UPDATE SET
                    network = excluded.network,
                    chain_id = excluded.chain_id,
                    market_id = excluded.market_id,
                    pair = excluded.pair,
                    tx_hash = excluded.tx_hash,
                    selected_profit_raw = excluded.selected_profit_raw,
                    selected_profit_amount = excluded.selected_profit_amount,
                    selected_profit_amount_symbol = excluded.selected_profit_amount_symbol,
                    selected_profit_source = excluded.selected_profit_source,
                    selected_profit_missing_reason = excluded.selected_profit_missing_reason,
                    created_at = excluded.created_at,
                    category = excluded.category,
                    payload_json = excluded.payload_json,
                    summary_json = excluded.summary_json,
                    updated_at = excluded.updated_at
                """,
                (
                    key,
                    network,
                    chain_id,
                    market_id,
                    pair,
                    tx_hash,
                    *selected_values,
                    row_created_at or created_at,
                    category,
                    json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")),
                    json.dumps(summary, ensure_ascii=True, sort_keys=True, separators=(",", ":")),
                    created_at,
                ),
            )
            ids.append(attempt_id)
    return ids


def backfill_cow_execution_attempt_summaries(
    database_url: str,
    *,
    batch_size: int = 500,
) -> dict[str, Any]:
    """Populate summary_json for rows written before the column existed."""
    ensure_cow_execution_attempts_table(database_url)
    updated = 0
    skipped = 0
    for table in _COW_TABLES:
        offset = 0
        while True:
            with db_connection(database_url) as connection:
                rows = connection.execute(
                    f"""
                    SELECT id, payload_json FROM {table}
                    WHERE summary_json = '' ORDER BY id LIMIT ? OFFSET ?
                    """,
                    (max(1, int(batch_size)), max(0, int(offset))),
                ).fetchall()
            if not rows:
                break
            writes = []
            for row in rows:
                try:
                    payload = json.loads(row["payload_json"])
                except (TypeError, json.JSONDecodeError):
                    skipped += 1
                    continue
                if not isinstance(payload, dict):
                    skipped += 1
                    continue
                summary = compact_cow_execution_attempt_row(
                    _decorate_attempt_review(payload)
                )
                writes.append(
                    (
                        json.dumps(summary, ensure_ascii=True, sort_keys=True, separators=(",", ":")),
                        int(row["id"]),
                    )
                )
            if writes:
                with db_connection(database_url) as connection:
                    connection.executemany(
                        f"UPDATE {table} SET summary_json = ? WHERE id = ?",
                        writes,
                    )
                updated += len(writes)
            offset += len(rows)
    return {
        "source": "sqlite_summary_backfill",
        "updated": updated,
        "skipped": skipped,
        "tables": list(_COW_TABLES),
    }


def clear_cow_execution_attempts_sqlite(
    database_url: str,
    *,
    networks: list[str] | None = None,
    chain_ids: list[int] | None = None,
    market_ids: list[str] | None = None,
    category: str | None = None,
    categories: list[str] | None = None,
) -> int:
    """Delete mirrored rows matching the requested scope."""
    ensure_cow_execution_attempts_table(database_url)
    if categories:
        cleared = 0
        for wanted_category in categories:
            table = _cow_table_for_category(wanted_category)
            where, params = _scope_sql(
                networks=networks,
                chain_ids=chain_ids,
                market_ids=market_ids,
                category=wanted_category,
            )
            with db_connection(database_url) as connection:
                cursor = connection.execute(
                    f"DELETE FROM {table}{where}",
                    params,
                )
            cleared += int(cursor.rowcount or 0)
        return cleared
    tables = [_cow_table_for_category(category)] if category else list(_COW_TABLES)
    cleared = 0
    for table in tables:
        where, params = _scope_sql(
            networks=networks,
            chain_ids=chain_ids,
            market_ids=market_ids,
            category=category,
        )
        with db_connection(database_url) as connection:
            cursor = connection.execute(
                f"DELETE FROM {table}{where}",
                params,
            )
        cleared += int(cursor.rowcount or 0)
    return cleared


__all__ = [
    "backfill_cow_execution_attempt_summaries",
    "clear_cow_execution_attempts_sqlite",
    "prune_cow_execution_attempts",
    "record_cow_execution_attempts",
]
