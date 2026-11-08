"""Legacy cow execution attempts JSONL archive utility.

SQLite is the only online cow-record store.  This module is intentionally not
re-exported by ``storage_cow_execution`` and keeps the old JSONL semantics only
for reading/verifying the out-of-repo legacy archive and for archive-tool tests.
Online callers must use the SQLite-only APIs in ``storage_cow_execution``.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import shutil
from pathlib import Path
import threading
import time
from typing import Any
import uuid

from core.evidence_scope import evidence_scope_matches, infer_evidence_scope
from core.db.cow_execution_mapping import scope_consistency_reasons
from core.db.storage_cow_policy import (
    COW_ATTEMPT_CATEGORY_EXECUTION_FAILED,
    COW_ATTEMPT_CATEGORY_EXECUTION_SUCCESS,
    COW_ATTEMPT_CATEGORY_NOT_EXECUTABLE,
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
_JSONL_WRITE_LOCKS: dict[Path, threading.Lock] = {}
_JSONL_LOCKS_GUARD = threading.Lock()
_GROUPED_JSONL_CACHE: dict[tuple, tuple[float, dict[str, list[dict[str, Any]]]]] = {}
_GROUPED_JSONL_CACHE_GUARD = threading.Lock()
_GROUPED_JSONL_CACHE_TTL_SECONDS = 30.0


def _jsonl_write_lock(path: Path) -> threading.Lock:
    """Return the process-wide serialization lock for a JSONL path."""
    normalized = path.resolve()
    with _JSONL_LOCKS_GUARD:
        lock = _JSONL_WRITE_LOCKS.get(normalized)
        if lock is None:
            lock = threading.Lock()
            _JSONL_WRITE_LOCKS[normalized] = lock
        return lock


def _jsonl_temp_path(path: Path) -> Path:
    """Return a unique sibling temp path for an atomic JSONL replace."""
    return path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")


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
    lock = _jsonl_write_lock(path)
    with lock:
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
        # Write to a unique sibling temp file first, then atomically replace
        # the target. The path-scoped lock serializes writers so one thread
        # never replaces another thread's just-written rows on Windows, and a
        # unique temp name avoids sharing a rename target between writers.
        temp_path = _jsonl_temp_path(path)
        with temp_path.open("w", encoding="utf-8") as handle:
            for item in rows:
                handle.write(_json_text(item) + "\n")
        temp_path.replace(path)
        return len(additions)


def _jsonl_source_paths(path: Path) -> list[Path]:
    if path.is_dir():
        paths = [item for item in path.rglob("*.jsonl") if item.is_file()]
        legacy_file = path.with_suffix(".jsonl")
        if legacy_file.exists() and legacy_file.is_file():
            paths.append(legacy_file)
        return sorted({item for item in paths})
    return [path]


def _scope_network_paths(root: Path, source_paths: list[Path], wanted: set[str]) -> list[Path]:
    """Keep only source files that belong to a wanted network scope.

    Files at the root (for example a tmp_path legacy attempts file) are kept
    because their rows may be mixed-scope or belong to the requested network.
    Files under a known but different network directory are dropped so a
    network-scoped request does not repeatedly read unrelated log files.
    """
    if not wanted or not source_paths:
        return source_paths
    known_scope_dirs = set()
    try:
        known_scope_dirs = {
            str(item.name).strip().lower()
            for item in root.iterdir()
            if item.is_dir()
            and not str(item.name).strip().isdigit()
        }
    except OSError:
        known_scope_dirs = set()
    lower_parts_by_path = {}
    for source_path in source_paths:
        lower_parts = {part.strip().lower() for part in source_path.parts}
        lower_parts_by_path[source_path] = lower_parts
    return [
        source_path
        for source_path in source_paths
        if not bool(lower_parts_by_path[source_path].intersection(known_scope_dirs - wanted))
    ]


def clear_cow_execution_attempts_jsonl(
    path: Path,
    *,
    networks: list[str] | None = None,
    chain_ids: list[int] | None = None,
    category: str | None = None,
    archive_dir: Path | None = None,
    clear_all: bool = False,
) -> dict[str, Any]:
    source_paths = [item for item in _jsonl_source_paths(path) if item.exists()]
    if clear_all:
        # Clear every attempt row regardless of network/chain/category.  This
        # is used only when the operator wants a fresh observation baseline and
        # has explicitly chosen to preserve the old log as an archive.
        wanted = None
        wanted_chain_ids = None
        wanted_category = None
    else:
        wanted = {str(item).strip().lower() for item in networks or [] if str(item).strip()}
        wanted_chain_ids = {int(item) for item in chain_ids or [] if item is not None}
        wanted_category = str(category or "").strip().lower()
    if archive_dir is not None:
        archive_dir = archive_dir.resolve()
        archive_dir.mkdir(parents=True, exist_ok=True)
    cleared = 0
    remaining = 0
    archived_files = []
    if not source_paths:
        return {
            "cleared": 0,
            "remaining": 0,
            "source": "jsonl",
            "archivedFiles": archived_files,
            "error": None,
        }
    for source_path in source_paths:
        lock = _jsonl_write_lock(source_path)
        with lock:
            if clear_all and archive_dir is not None:
                archive_target = archive_dir / f"{source_path.name}.{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.bak"
                shutil.copy2(source_path, archive_target)
                archived_files.append(str(archive_target))
                source_path.unlink()
                continue
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
                chain_id = row.get("chain_id")
                try:
                    row_chain_id = int(chain_id)
                except (TypeError, ValueError):
                    row_chain_id = None
                chain_matches = not wanted_chain_ids or (row_chain_id is not None and row_chain_id in wanted_chain_ids)
                category_matches = not wanted_category or cow_execution_attempt_category(row) == wanted_category
                if network_matches and chain_matches and category_matches:
                    cleared += 1
                    continue
                kept_rows.append(_json_text(row))
            source_path.parent.mkdir(parents=True, exist_ok=True)
            with source_path.open("w", encoding="utf-8") as handle:
                for item in kept_rows:
                    handle.write(str(item) + "\n")
            remaining += len(kept_rows)
    return {
        "cleared": cleared,
        "remaining": remaining,
        "source": "jsonl",
        "archivedFiles": archived_files,
        "error": None,
    }



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
    if path.is_dir() and wanted:
        source_paths = _scope_network_paths(path, source_paths, wanted)
        source_paths = sorted(
            source_paths,
            key=lambda item: item.stat().st_mtime,
            reverse=True,
        )
    now = datetime.now(timezone.utc)
    rows: list[dict[str, Any]] = []
    global_limit = max(1, int(limit))
    network_counts: dict[str, int] = {}
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
            if wanted:
                network = infer_evidence_scope(payload).network
                if network_counts.get(network, 0) >= global_limit:
                    continue
                network_counts[network] = network_counts.get(network, 0) + 1
            rows.append(payload)
            if not wanted and len(rows) >= global_limit:
                break
            if wanted and all(network_counts.get(network, 0) >= global_limit for network in wanted):
                break
    rows.sort(key=lambda item: (str(item.get("created_at") or item.get("observed_at") or ""), int(item.get("id") or 0)), reverse=True)
    rows = [_decorate_attempt_review(row) for row in rows]
    if wanted:
        network_counts = {}
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


def load_recent_cow_execution_attempts_jsonl_grouped(
    path: Path,
    limit: int = 50,
    *,
    networks: list[str] | None = None,
    chain_ids: list[int] | None = None,
    market_ids: list[str] | None = None,
    retention_days: int = DEFAULT_COW_EXECUTION_RETENTION_DAYS,
) -> dict[str, list[dict[str, Any]]]:
    """Load recent attempts for all review categories in one file pass.

    The per-category loader used by the review pages was calling the JSONL
    reader once for every category, which repeatedly re-parsed the same log
    files.  This grouped variant reads the file once and buckets matching rows
    by category while still honoring the per-network/limit semantics used by
    the existing loaders.
    """
    source_paths = [item for item in _jsonl_source_paths(path) if item.exists()]
    if not source_paths:
        return {category: [] for category in (
            COW_ATTEMPT_CATEGORY_NOT_EXECUTABLE,
            COW_ATTEMPT_CATEGORY_EXECUTION_FAILED,
            COW_ATTEMPT_CATEGORY_EXECUTION_SUCCESS,
        )}
    source_stat_key = tuple(
        (
            str(item.resolve()),
            item.stat().st_mtime_ns,
            item.stat().st_size,
        )
        for item in source_paths
    )
    cache_key = (
        source_stat_key,
        int(limit),
        tuple(sorted(str(item).strip().lower() for item in networks or [])),
        tuple(sorted(int(item) for item in chain_ids or [])),
        tuple(sorted(str(item).strip().lower() for item in market_ids or [])),
        int(retention_days),
    )
    with _GROUPED_JSONL_CACHE_GUARD:
        cached = _GROUPED_JSONL_CACHE.get(cache_key)
    if cached is not None:
        cached_at, cached_payload = cached
        if time.monotonic() - cached_at <= _GROUPED_JSONL_CACHE_TTL_SECONDS:
            return cached_payload
    wanted = {str(item).strip().lower() for item in networks or [] if str(item).strip()}
    wanted_chain_ids = {int(item) for item in chain_ids or [] if item is not None}
    wanted_market_ids = {str(item).strip().lower() for item in market_ids or [] if str(item).strip()}
    if path.is_dir() and wanted:
        source_paths = _scope_network_paths(path, source_paths, wanted)
        source_paths = sorted(
            source_paths,
            key=lambda item: item.stat().st_mtime,
            reverse=True,
        )
    now = datetime.now(timezone.utc)
    categories = [
        COW_ATTEMPT_CATEGORY_NOT_EXECUTABLE,
        COW_ATTEMPT_CATEGORY_EXECUTION_FAILED,
        COW_ATTEMPT_CATEGORY_EXECUTION_SUCCESS,
    ]
    global_limit = max(1, int(limit))
    raw_by_category: dict[str, list[dict[str, Any]]] = {category: [] for category in categories}
    counts_by_category: dict[str, dict[str, int]] = {category: {} for category in categories}

    def _category_satisfied(category: str) -> bool:
        if wanted:
            return all(
                counts_by_category[category].get(network, 0) >= global_limit
                for network in wanted
            )
        return len(raw_by_category[category]) >= global_limit

    def _limit_met(category: str) -> bool:
        if wanted:
            return all(
                counts_by_category[category].get(network, 0) >= global_limit
                for network in wanted
            )
        return len(raw_by_category[category]) >= global_limit

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
            if payload_category not in raw_by_category:
                continue
            if not _category_within_retention(payload, now=now):
                continue
            if wanted:
                network = infer_evidence_scope(payload).network
                if counts_by_category[payload_category].get(network, 0) >= global_limit:
                    continue
                counts_by_category[payload_category][network] = (
                    counts_by_category[payload_category].get(network, 0) + 1
                )
            raw_by_category[payload_category].append(payload)
            if all(_limit_met(category) for category in categories):
                break
            if all(_category_satisfied(category) for category in categories):
                break
        if all(_category_satisfied(category) for category in categories):
            break

    grouped: dict[str, list[dict[str, Any]]] = {}
    for category in categories:
        rows = [_decorate_attempt_review(row) for row in raw_by_category[category]]
        rows.sort(
            key=lambda item: (
                str(item.get("created_at") or item.get("observed_at") or ""),
                int(item.get("id") or 0),
            ),
            reverse=True,
        )
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
            grouped[category] = limited_rows
        else:
            grouped[category] = rows[:global_limit]
    with _GROUPED_JSONL_CACHE_GUARD:
        _GROUPED_JSONL_CACHE[cache_key] = (time.monotonic(), grouped)
    return grouped


