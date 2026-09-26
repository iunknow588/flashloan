from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from core.evidence_scope import evidence_scope_matches, infer_evidence_scope
from core.db.cow_execution_mapping import scope_consistency_reasons
from core.db.storage_cow_policy import (
    _category_within_retention,
    _decorate_attempt_review,
    _json_text,
    cow_execution_attempt_category,
)
from core.db.storage_cow_candidate_builders import (
    _attempt_signature,
    _is_market_candidate,
)

DEFAULT_COW_EXECUTION_RETENTION_DAYS = 7


def _jsonl_signature(row: dict[str, Any]) -> tuple[Any, ...]:
    return _attempt_signature(row)


def _within_retention(row: dict[str, Any], cutoff: datetime | None) -> bool:
    if cutoff is None:
        return True
    created_at = _parse_datetime(row.get("created_at") or row.get("observed_at"))
    return created_at is None or created_at >= cutoff


def append_cow_execution_attempts_jsonl(
    path: Path,
    attempts: list[dict[str, Any]],
    *,
    retention_days: int = DEFAULT_COW_EXECUTION_RETENTION_DAYS,
    dedupe_market_candidates: bool = True,
) -> int:
    if not attempts:
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    created_at = now.isoformat()
    existing_rows = []
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict) and _category_within_retention(row, now=now):
                existing_rows.append(row)
    market_signatures = {
        _jsonl_signature(row)
        for row in existing_rows
        if _is_market_candidate(row)
    }
    additions = []
    for item in attempts:
        row = {**item, "created_at": created_at}
        if scope_consistency_reasons(row):
            # A contradictory evidence record must never enter the durable log.
            continue
        if dedupe_market_candidates and _is_market_candidate(row):
            signature = _jsonl_signature(row)
            if signature in market_signatures:
                continue
            market_signatures.add(signature)
        additions.append(row)
    rows = [*existing_rows, *additions]
    with path.open("w", encoding="utf-8") as handle:
        for item in rows:
            handle.write(_json_text(item) + "\n")
    return len(additions)


def _jsonl_source_paths(path: Path) -> list[Path]:
    if path.is_dir():
        paths = [item for item in path.rglob("*.jsonl") if item.is_file()]
        legacy_file = path.with_suffix(".jsonl")
        if legacy_file.exists() and legacy_file.is_file():
            paths.append(legacy_file)
        return sorted({item for item in paths})
    return [path]


def clear_cow_execution_attempts_jsonl(
    path: Path,
    *,
    networks: list[str] | None = None,
    category: str | None = None,
) -> dict[str, Any]:
    source_paths = [item for item in _jsonl_source_paths(path) if item.exists()]
    if not source_paths:
        return {"cleared": 0, "remaining": 0, "source": "jsonl", "error": None}
    wanted = {str(item).strip().lower() for item in networks or [] if str(item).strip()}
    wanted_category = str(category or "").strip().lower()
    cleared = 0
    remaining = 0
    for source_path in source_paths:
        kept_rows = []
        for line in source_path.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                kept_rows.append(line)
                continue
            if not isinstance(row, dict):
                kept_rows.append(line)
                continue
            network_matches = not wanted or str(row.get("network") or "").strip().lower() in wanted
            category_matches = not wanted_category or cow_execution_attempt_category(row) == wanted_category
            if network_matches and category_matches:
                cleared += 1
                continue
            kept_rows.append(_json_text(row))
        source_path.parent.mkdir(parents=True, exist_ok=True)
        with source_path.open("w", encoding="utf-8") as handle:
            for item in kept_rows:
                handle.write(str(item) + "\n")
        remaining += len(kept_rows)
    return {"cleared": cleared, "remaining": remaining, "source": "jsonl", "error": None}



def load_recent_cow_execution_attempts_jsonl(
    path: Path,
    limit: int = 50,
    *,
    networks: list[str] | None = None,
    chain_ids: list[int] | None = None,
    market_ids: list[str] | None = None,
    retention_days: int = DEFAULT_COW_EXECUTION_RETENTION_DAYS,
    category: str | None = None,
) -> list[dict[str, Any]]:
    source_paths = [item for item in _jsonl_source_paths(path) if item.exists()]
    if not source_paths:
        return []
    wanted = {str(item).strip().lower() for item in networks or [] if str(item).strip()}
    wanted_chain_ids = {int(item) for item in chain_ids or [] if item is not None}
    wanted_market_ids = {str(item).strip().lower() for item in market_ids or [] if str(item).strip()}
    wanted_category = str(category or "").strip().lower()
    now = datetime.now(timezone.utc)
    rows: list[dict[str, Any]] = []
    global_limit = max(1, int(limit))
    for source_path in source_paths:
        try:
            lines = source_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in reversed(lines):
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(payload, dict):
                continue
            if not evidence_scope_matches(
                payload,
                networks=wanted,
                chain_ids=wanted_chain_ids,
                market_ids=wanted_market_ids,
            ):
                continue
            payload_category = cow_execution_attempt_category(payload)
            if wanted_category and payload_category != wanted_category:
                continue
            if not _category_within_retention(payload, now=now):
                continue
            rows.append(_decorate_attempt_review(payload))
    rows.sort(key=lambda item: (str(item.get("created_at") or item.get("observed_at") or ""), int(item.get("id") or 0)), reverse=True)
    if wanted:
        network_counts: dict[str, int] = {}
        limited_rows: list[dict[str, Any]] = []
        for row in rows:
            network = infer_evidence_scope(row).network
            if network_counts.get(network, 0) >= global_limit:
                continue
            network_counts[network] = network_counts.get(network, 0) + 1
            limited_rows.append(row)
            if wanted.issubset({key for key, count in network_counts.items() if count >= global_limit}):
                break
        return limited_rows
    return rows[:global_limit]


