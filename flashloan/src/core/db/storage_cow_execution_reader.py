from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from core.db.storage_common import db_connection
from core.db.storage_cow_execution_compact import compact_cow_execution_attempt_row
from core.db.storage_cow_policy import (
    _category_within_retention,
    _decorate_attempt_review,
)
from core.db.storage_cow_execution_schema import (
    COW_ATTEMPT_CATEGORY_EXECUTION_FAILED,
    COW_ATTEMPT_CATEGORY_EXECUTION_SUCCESS,
    COW_ATTEMPT_CATEGORY_NOT_EXECUTABLE,
    DEFAULT_COW_EXECUTION_RETENTION_DAYS,
    _COW_ATTEMPT_TABLE,
    _COW_TABLES,
    _cow_table_for_category,
    ensure_cow_execution_attempts_table,
)
from core.db.storage_cow_execution_query import (
    _payload_rows_from_sql,
    _scope_sql,
    _summary_rows_from_sql,
)


def count_cow_execution_attempts_sqlite_grouped(
    database_url: str,
    *,
    networks: list[str] | None = None,
    chain_ids: list[int] | None = None,
    market_ids: list[str] | None = None,
    category: str | None = None,
    categories: list[str] | None = None,
) -> dict[str, int]:
    ensure_cow_execution_attempts_table(database_url)
    if categories:
        total = 0
        counts = {item: 0 for item in categories}
        for wanted_category in categories:
            table = _cow_table_for_category(wanted_category)
            where, params = _scope_sql(
                networks=networks,
                chain_ids=chain_ids,
                market_ids=market_ids,
                category=wanted_category,
            )
            with db_connection(database_url) as connection:
                total_row = connection.execute(
                    f"SELECT COUNT(*) AS c FROM {table}{where}",
                    params,
                ).fetchone()
                category_count = int(total_row["c"] or 0)
            total += category_count
            if wanted_category in counts:
                counts[wanted_category] = category_count
        counts["total"] = total
        return counts
    wanted_categories = list(categories) if categories else [
        COW_ATTEMPT_CATEGORY_NOT_EXECUTABLE,
        COW_ATTEMPT_CATEGORY_EXECUTION_FAILED,
        COW_ATTEMPT_CATEGORY_EXECUTION_SUCCESS,
    ]
    counts = {category: 0 for category in wanted_categories}
    total = 0
    tables = [_cow_table_for_category(category)] if category else list(_COW_TABLES)
    for table in tables:
        where, params = _scope_sql(
            networks=networks,
            chain_ids=chain_ids,
            market_ids=market_ids,
            category=category,
        )
        with db_connection(database_url) as connection:
            total_row = connection.execute(
                f"SELECT COUNT(*) AS c FROM {table}{where}",
                params,
            ).fetchone()
            grouped_rows = connection.execute(
                f"SELECT category, COUNT(*) AS c FROM {table}{where} GROUP BY category",
                params,
            ).fetchall()
        total += int(total_row["c"] or 0)
        for row in grouped_rows:
            key = str(row["category"] or "").strip().lower()
            if key in counts:
                counts[key] = int(row["c"] or 0)
    counts["total"] = total
    return counts


def load_latest_cow_execution_rows_by_pair(
    database_url: str,
    *,
    networks: list[str] | None = None,
    chain_ids: list[int] | None = None,
    market_ids: list[str] | None = None,
    categories: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Load only the newest summary row for each route pair.

    Closed-loop health pages historically load thousands of retry rows and then
    count them as if they were distinct loops.  This function keeps one row per
    direction pair so the page can merge the two directions back into a single
    ``loop_key`` without scanning the full review history.
    """
    ensure_cow_execution_attempts_table(database_url)
    wanted_categories = list(categories) if categories else [
        COW_ATTEMPT_CATEGORY_NOT_EXECUTABLE,
        COW_ATTEMPT_CATEGORY_EXECUTION_FAILED,
        COW_ATTEMPT_CATEGORY_EXECUTION_SUCCESS,
    ]
    latest_by_pair: dict[tuple[str, int, str, str, str], dict[str, Any]] = {}
    for category in wanted_categories:
        table = _cow_table_for_category(category)
        where, params = _scope_sql(
            networks=networks,
            chain_ids=chain_ids,
            market_ids=market_ids,
            category=category,
        )
        id_sql = f"""
            WITH ranked AS (
                SELECT id, created_at, network, chain_id, market_id, pair,
                       ROW_NUMBER() OVER (
                           PARTITION BY network, chain_id, market_id, pair
                           ORDER BY created_at DESC, id DESC
                       ) AS rn
                FROM {table}
                {where}
            )
            SELECT id, created_at, network, chain_id, market_id, pair
            FROM ranked
            WHERE rn = 1
        """
        with db_connection(database_url) as connection:
            latest_rows = connection.execute(id_sql, params).fetchall()
            pending_ids = [int(row["id"]) for row in latest_rows]
            row_by_id = {int(row["id"]): row for row in latest_rows}
            for offset in range(0, len(pending_ids), 400):
                batch = pending_ids[offset : offset + 400]
                placeholders = ",".join("?" for _ in batch)
                summary_rows = connection.execute(
                    f"""
                    SELECT id, summary_json, payload_json, created_at
                    FROM {table}
                    WHERE id IN ({placeholders})
                    """,
                    batch,
                ).fetchall()
                payload_by_id = {
                    int(row["id"]): row
                    for row in summary_rows
                }
                for row_id in batch:
                    sql_row = payload_by_id.get(row_id)
                    if sql_row is None:
                        continue
                    summary_text = str(sql_row["summary_json"] or "").strip()
                    if summary_text:
                        try:
                            summary = json.loads(summary_text)
                        except (TypeError, json.JSONDecodeError):
                            summary = None
                    else:
                        summary = None
                    if not isinstance(summary, dict):
                        payload = None
                        try:
                            payload = json.loads(str(sql_row["payload_json"] or "{}"))
                        except (TypeError, json.JSONDecodeError):
                            payload = None
                        if isinstance(payload, dict):
                            summary = compact_cow_execution_attempt_row(
                                _decorate_attempt_review(payload)
                            )
                    if not isinstance(summary, dict):
                        continue
                    summary["id"] = row_id
                    base_row = row_by_id.get(row_id) or {}
                    if not summary.get("created_at"):
                        summary["created_at"] = str(sql_row["created_at"] or base_row.get("created_at") or "")
                    for key in ("network", "chain_id", "market_id", "pair"):
                        if not summary.get(key):
                            summary[key] = dict(base_row).get(key) or ""
                    pair_key = str(
                        summary.get("pair") or summary.get("symbol_pair") or ""
                    ).strip().lower()
                    route_key = (
                        str(summary.get("network") or "").strip().lower(),
                        int(summary.get("chain_id") or 0),
                        str(summary.get("market_id") or "").strip().lower(),
                        pair_key,
                    )
                    sort_key = (
                        str(summary.get("created_at") or summary.get("observed_at") or ""),
                        row_id,
                    )
                    existing = latest_by_pair.get(route_key)
                    if existing is None or sort_key > (
                        str(existing.get("created_at") or existing.get("observed_at") or ""),
                        int(existing.get("id") or 0),
                    ):
                        latest_by_pair[route_key] = summary
    latest = list(latest_by_pair.values())
    latest.sort(
        key=lambda item: (
            str(item.get("created_at") or item.get("observed_at") or ""),
            int(item.get("id") or 0),
        ),
        reverse=True,
    )
    return latest


def load_recent_cow_execution_attempts(
    database_url: str,
    limit: int = 50,
    *,
    networks: list[str] | None = None,
    chain_ids: list[int] | None = None,
    market_ids: list[str] | None = None,
    retention_days: int = DEFAULT_COW_EXECUTION_RETENTION_DAYS,
    category: str | None = None,
    start_at: str | None = None,
    end_at: str | None = None,
) -> list[dict[str, Any]]:
    ensure_cow_execution_attempts_table(database_url)
    tables = [_cow_table_for_category(category)] if category else list(_COW_TABLES)
    rows: list[dict[str, Any]] = []
    for table in tables:
        rows.extend(
            _payload_rows_from_sql(
                database_url,
                limit=max(1, int(limit)),
                networks=networks,
                chain_ids=chain_ids,
                market_ids=market_ids,
                category=category,
                start_at=start_at,
                end_at=end_at,
                table=table,
            )
        )
    if start_at or end_at:
        # An explicit window is the read contract for verification/reporting
        # scripts; category retention is enforced by pruning, not by silently
        # dropping rows that already match the requested window.
        rows = list(rows)
    else:
        now = datetime.now(timezone.utc)
        rows = [row for row in rows if _category_within_retention(row, now=now)]
    rows.sort(
        key=lambda row: (
            str(row.get("created_at") or row.get("observed_at") or ""),
            int(row.get("id") or 0),
        ),
        reverse=True,
    )
    rows = [_decorate_attempt_review(row) for row in rows]
    return rows[:max(1, int(limit))]


def load_recent_cow_execution_attempts_source(
    source: str | Path,
    limit: int = 50,
    *,
    networks: list[str] | None = None,
    chain_ids: list[int] | None = None,
    market_ids: list[str] | None = None,
    retention_days: int = DEFAULT_COW_EXECUTION_RETENTION_DAYS,
    category: str | None = None,
    start_at: str | None = None,
    end_at: str | None = None,
) -> list[dict[str, Any]]:
    """Load attempts strictly from the online SQLite store."""
    path = Path(source)
    if path.suffix.lower() in {".sqlite3", ".sqlite", ".db"}:
        return load_recent_cow_execution_attempts(
            str(path),
            limit=limit,
            networks=networks,
            chain_ids=chain_ids,
            market_ids=market_ids,
            retention_days=retention_days,
            category=category,
            start_at=start_at,
            end_at=end_at,
        )
    raise ValueError(
        "only SQLite stores are supported for cow execution attempts; "
        f"legacy JSONL is no longer an online source: {path}"
    )


def load_recent_cow_execution_attempt_summaries(
    database_url: str,
    limit: int = 50,
    *,
    networks: list[str] | None = None,
    chain_ids: list[int] | None = None,
    market_ids: list[str] | None = None,
    retention_days: int = DEFAULT_COW_EXECUTION_RETENTION_DAYS,
) -> list[dict[str, Any]]:
    """Load compact attempt rows from SQLite without decoding full payloads."""
    ensure_cow_execution_attempts_table(database_url)
    rows: list[dict[str, Any]] = []
    for table in _COW_TABLES:
        rows.extend(
            _summary_rows_from_sql(
                database_url,
                limit=max(1, int(limit)),
                networks=networks,
                chain_ids=chain_ids,
                market_ids=market_ids,
                category=None,
                table=table,
            )
        )
    now = datetime.now(timezone.utc)
    rows = [row for row in rows if _category_within_retention(row, now=now)]
    rows.sort(
        key=lambda item: (
            str(item.get("created_at") or item.get("observed_at") or ""),
            int(item.get("id") or 0),
        ),
        reverse=True,
    )
    return rows[:max(1, int(limit))]


def load_auto_execute_log_stats(
    database_url: str,
    *,
    limit: int = 2000,
    networks: list[str] | None = None,
    chain_ids: list[int] | None = None,
    market_ids: list[str] | None = None,
    since: str | None = None,
) -> dict[str, Any] | None:
    """Return persisted auto-execute stats without scanning large summaries.

    Snapshot status needs only the auto-execute flag, tx hash and timestamp.
    The cheap scope index picks the recent attempt rows, and only the small
    ``execution_success`` subset reads ``summary_json`` for exact stats.
    Returns ``None`` when legacy rows still require the full decoder fallback.
    """
    ensure_cow_execution_attempts_table(database_url)
    where, params = _scope_sql(
        networks=networks,
        chain_ids=chain_ids,
        market_ids=market_ids,
        category=None,
    )
    selected_where = where or ""
    with db_connection(database_url) as connection:
        rows = connection.execute(
            f"""
            SELECT id, created_at, category, tx_hash
            FROM {_COW_ATTEMPT_TABLE}
            {selected_where}
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            [*params, max(1, int(limit))],
        ).fetchall()
        success_rows = [
            row
            for row in rows
            if str(row["category"] or "").strip().lower() == "execution_success"
        ]
        success_details: list[tuple[int, str, dict[str, Any]]] = []
        if success_rows:
            placeholders = ",".join("?" for _ in success_rows)
            detail_rows = connection.execute(
                f"""
                SELECT id, created_at, summary_json
                FROM {_COW_ATTEMPT_TABLE}
                WHERE id IN ({placeholders})
                """,
                [int(row["id"]) for row in success_rows],
            ).fetchall()
            for row in detail_rows:
                summary_text = str(row["summary_json"] or "").strip()
                try:
                    summary = json.loads(summary_text)
                except (TypeError, json.JSONDecodeError):
                    return None
                if not isinstance(summary, dict):
                    return None
                success_details.append((int(row["id"]), str(row["created_at"] or ""), summary))
    auto_execute_count = 0
    last_auto_execute_at = None
    for _attempt_id, created_at, summary in success_details:
        auto = summary.get("auto_execute") if isinstance(summary.get("auto_execute"), dict) else {}
        if not bool(auto.get("executed")):
            continue
        result = auto.get("result") if isinstance(auto.get("result"), dict) else {}
        tx_hash = str(result.get("txHash") or result.get("tx_hash") or "").strip()
        if not tx_hash:
            continue
        ts = str(auto.get("observed_at") or summary.get("created_at") or created_at or "").strip()
        if since and ts and ts < str(since):
            continue
        auto_execute_count += 1
        if ts and (last_auto_execute_at is None or ts > last_auto_execute_at):
            last_auto_execute_at = ts
    return {
        "auto_execute_count": auto_execute_count,
        "last_auto_execute_at": last_auto_execute_at,
    }


def load_recent_cow_execution_attempt_validation_rows(
    database_url: str,
    limit: int = 5000,
    *,
    networks: list[str] | None = None,
    chain_ids: list[int] | None = None,
    market_ids: list[str] | None = None,
    retention_days: int = DEFAULT_COW_EXECUTION_RETENTION_DAYS,
) -> list[dict[str, Any]]:
    """Return minimal validation rows via json_extract on summary_json.

    Avoids decoding the full 10-50KB summary payloads in Python; only submits,
    settlements and legacy rows without enough summary fields fall back to the
    full payload loader in the caller.
    """
    ensure_cow_execution_attempts_table(database_url)
    raw_rows = []
    for table in _COW_TABLES:
        where, params = _scope_sql(
            networks=networks,
            chain_ids=chain_ids,
            market_ids=market_ids,
            category=None,
        )
        sql = f"""
            SELECT
                id,
                created_at,
                json_extract(summary_json, '$.state') AS state,
                json_extract(summary_json, '$.status') AS status,
                json_extract(summary_json, '$.observed_at') AS observed_at,
                json_extract(summary_json, '$.tx_hash') AS tx_hash,
                json_extract(summary_json, '$.review_category') AS review_category,
                json_extract(summary_json, '$.category') AS category
            FROM {table}
            {where}
            ORDER BY created_at DESC, id DESC
            LIMIT ?
        """
        query_params = [*params, max(1, int(limit))]
        with db_connection(database_url) as connection:
            raw_rows.extend(connection.execute(sql, query_params).fetchall())
    now = datetime.now(timezone.utc)
    result: list[dict[str, Any]] = []
    for row in raw_rows:
        item = {
            "id": int(row["id"]),
            "created_at": str(row["created_at"] or ""),
        }
        for key in (
            "state",
            "status",
            "observed_at",
            "tx_hash",
            "review_category",
            "category",
        ):
            if row[key] is not None:
                item[key] = str(row[key])
        if _category_within_retention(item, now=now):
            result.append(item)
    return result[: max(1, int(limit))]


def load_recent_cow_execution_attempts_sqlite_grouped(
    database_url: str,
    limit: int = 50,
    *,
    networks: list[str] | None = None,
    chain_ids: list[int] | None = None,
    market_ids: list[str] | None = None,
    retention_days: int = DEFAULT_COW_EXECUTION_RETENTION_DAYS,
    use_summary: bool = True,
    categories: list[str] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Load all review categories from the SQLite mirror in one scan."""
    ensure_cow_execution_attempts_table(database_url)
    wanted_categories = list(categories) if categories else [
        COW_ATTEMPT_CATEGORY_NOT_EXECUTABLE,
        COW_ATTEMPT_CATEGORY_EXECUTION_FAILED,
        COW_ATTEMPT_CATEGORY_EXECUTION_SUCCESS,
    ]
    raw_by_category: dict[str, list[dict[str, Any]]] = {category: [] for category in wanted_categories}
    now = datetime.now(timezone.utc)
    for category in wanted_categories:
        table = _cow_table_for_category(category)
        if use_summary:
            rows = _summary_rows_from_sql(
                database_url,
                limit=max(1, int(limit)),
                networks=networks,
                chain_ids=chain_ids,
                market_ids=market_ids,
                category=category,
                table=table,
            )
        else:
            rows = _payload_rows_from_sql(
                database_url,
                limit=max(1, int(limit)),
                networks=networks,
                chain_ids=chain_ids,
                market_ids=market_ids,
                category=category,
                table=table,
            )
        raw_by_category[category] = [
            row for row in rows
            if _category_within_retention(row, now=now)
        ]
    grouped: dict[str, list[dict[str, Any]]] = {}
    for category in wanted_categories:
        rows = [
            row if use_summary else _decorate_attempt_review(row)
            for row in raw_by_category[category]
        ]
        rows.sort(
            key=lambda item: (
                str(item.get("created_at") or item.get("observed_at") or ""),
                int(item.get("id") or 0),
            ),
            reverse=True,
        )
        grouped[category] = rows[:max(1, int(limit))]
    return grouped


def load_cow_execution_attempt_detail(
    database_url: str,
    attempt_id: int,
) -> dict[str, Any] | None:
    """Return the full attempt payload for a detail view."""
    ensure_cow_execution_attempts_table(database_url)
    row = None
    with db_connection(database_url) as connection:
        for table in _COW_TABLES:
            candidate = connection.execute(
                f"SELECT payload_json, id FROM {table} WHERE id = ?",
                (int(attempt_id),),
            ).fetchone()
            if candidate is not None:
                row = candidate
                break
    if not row:
        return None
    try:
        payload = json.loads(row["payload_json"])
    except (TypeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    payload["id"] = int(row["id"])
    return _decorate_attempt_review(payload)


__all__ = [
    "count_cow_execution_attempts_sqlite_grouped",
    "load_auto_execute_log_stats",
    "load_cow_execution_attempt_detail",
    "load_latest_cow_execution_rows_by_pair",
    "load_recent_cow_execution_attempt_summaries",
    "load_recent_cow_execution_attempt_validation_rows",
    "load_recent_cow_execution_attempts",
    "load_recent_cow_execution_attempts_source",
    "load_recent_cow_execution_attempts_sqlite_grouped",
]
