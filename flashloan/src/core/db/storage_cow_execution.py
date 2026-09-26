from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from core.kv_rows import collection_key, count, delete_key, load_rows, upsert_row
from core.local_kv import ensure_store
from core.db.storage_cow_execution_jsonl import (
    append_cow_execution_attempts_jsonl,
    clear_cow_execution_attempts_jsonl,
    load_recent_cow_execution_attempts_jsonl,
)
from core.db.storage_cow_candidate_builders import (
    build_cow_execution_attempts,
    build_cow_market_claim_candidate_attempts,
    build_cow_market_candidate_attempts,
    build_cow_market_claim_pairs,
)
from core.db.storage_cow_policy import (
    COW_ATTEMPT_CATEGORY_ANALYSIS_DAYS,
    COW_ATTEMPT_CATEGORY_RETENTION_DAYS,
    COW_ATTEMPT_CATEGORY_RETENTION_POLICY,
    _category_within_retention,
    _parse_datetime,
)

DEFAULT_COW_EXECUTION_RETENTION_DAYS = 7
COW_EXECUTION_RETENTION_LOCAL_TZ = timezone(timedelta(hours=8), "Asia/Shanghai")
COW_ATTEMPT_CATEGORY_NOT_EXECUTABLE = "not_executable"
COW_ATTEMPT_CATEGORY_EXECUTION_FAILED = "execution_failed"
COW_ATTEMPT_CATEGORY_EXECUTION_SUCCESS = "execution_success"


def ensure_cow_execution_attempts_table(database_url: str) -> None:
    ensure_store(database_url)


def prune_cow_execution_attempts(database_url: str, retention_days: int = DEFAULT_COW_EXECUTION_RETENTION_DAYS) -> int:
    ensure_cow_execution_attempts_table(database_url)
    cutoff = datetime.now(timezone.utc) - timedelta(days=max(1, int(retention_days)))
    removed = 0
    for row in load_rows(database_url, "cow_execution_attempts"):
        created_at = _parse_datetime(row.get("created_at") or row.get("observed_at"))
        if created_at is not None and created_at < cutoff:
            key = str(row.get("_key") or "")
            if not key:
                for candidate in ("network", "pair", "id"):
                    if candidate in row and row[candidate] not in (None, ""):
                        key = collection_key(
                            row.get("network") or "local",
                            row.get("pair") or row.get("id"),
                            row.get("id") or row.get(candidate),
                        )
                        break
            if key and delete_key(database_url, "cow_execution_attempts", key):
                removed += 1
    return removed


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
    ids = []
    for item in attempts:
        attempt_id = count(database_url, "cow_execution_attempts") + 1
        key = collection_key(item.get("network") or "local", item.get("pair") or attempt_id, attempt_id)
        payload = dict(item)
        payload["id"] = attempt_id
        payload["_key"] = key
        payload.setdefault("created_at", datetime.now(timezone.utc).isoformat())
        upsert_row(database_url, "cow_execution_attempts", key, payload)
        ids.append(attempt_id)
    return ids


def load_recent_cow_execution_attempts(
    database_url: str,
    limit: int = 50,
    *,
    networks: list[str] | None = None,
    chain_ids: list[int] | None = None,
    market_ids: list[str] | None = None,
    retention_days: int = DEFAULT_COW_EXECUTION_RETENTION_DAYS,
    category: str | None = None,
) -> list[dict[str, Any]]:
    ensure_cow_execution_attempts_table(database_url)
    rows = load_rows(database_url, "cow_execution_attempts")
    wanted_networks = {str(item).strip().lower() for item in networks or []}
    wanted_chain_ids = {int(item) for item in chain_ids or [] if item is not None}
    if wanted_networks:
        rows = [row for row in rows if str(row.get("network") or "").lower() in wanted_networks]
    if wanted_chain_ids:
        rows = [row for row in rows if int(row.get("chain_id") or 0) in wanted_chain_ids]
    rows.sort(key=lambda row: str(row.get("created_at") or ""), reverse=True)
    return rows[:max(1, int(limit))]


__all__ = [
    "append_cow_execution_attempts_jsonl",
    "build_cow_execution_attempts",
    "build_cow_market_claim_candidate_attempts",
    "build_cow_market_candidate_attempts",
    "build_cow_market_claim_pairs",
    "COW_ATTEMPT_CATEGORY_ANALYSIS_DAYS",
    "COW_ATTEMPT_CATEGORY_RETENTION_DAYS",
    "COW_ATTEMPT_CATEGORY_RETENTION_POLICY",
    "_category_within_retention",
    "clear_cow_execution_attempts_jsonl",
    "ensure_cow_execution_attempts_table",
    "load_recent_cow_execution_attempts",
    "load_recent_cow_execution_attempts_jsonl",
    "prune_cow_execution_attempts",
    "record_cow_execution_attempts",
]
