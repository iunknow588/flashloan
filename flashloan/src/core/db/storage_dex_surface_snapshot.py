"""Persistent compact DEX overview snapshots backed by SQLite.

The compact overview snapshot is a static page contract derived from the
canonical SQLite loop tables.  It is written back to SQLite so a freshly
started process can serve the quote/overview surfaces without recomputing
the full loop construction.  A source fingerprint invalidates the snapshot
as soon as any raw pool, liquidity, candidate, observation or loop row
changes.
"""

from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime, timezone
import os
import threading
import time
from typing import Any

from core.db.database_path import absolute_database_path
from core.db.storage_business_io import execute, query, query_one


_SNAPSHOT_SURFACE = "dex_overview_compact"
# Bump when the persisted snapshot schema changes so stale payloads are
# rebuilt instead of being replayed without the new fields.
_SNAPSHOT_SCHEMA_VERSION = 2
_FINGERPRINT_CACHE: dict[tuple[str, str, int, str], tuple[float, str]] = {}
_FINGERPRINT_CACHE_LOCK = threading.Lock()
_SNAPSHOT_TABLE_ENSURED: set[str] = set()
_SNAPSHOT_TABLE_ENSURE_LOCK = threading.Lock()


def _stamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _ensure_snapshot_table(database_url: str) -> None:
    resolved_key = str(absolute_database_path(database_url))
    if resolved_key in _SNAPSHOT_TABLE_ENSURED:
        return
    with _SNAPSHOT_TABLE_ENSURE_LOCK:
        if resolved_key in _SNAPSHOT_TABLE_ENSURED:
            return
        _ensure_snapshot_table_unlocked(database_url)
        _SNAPSHOT_TABLE_ENSURED.add(resolved_key)


def _ensure_snapshot_table_unlocked(database_url: str) -> None:
    execute(
        database_url,
        """
        CREATE TABLE IF NOT EXISTS dex_surface_snapshots (
            surface TEXT NOT NULL,
            network TEXT NOT NULL,
            chain_id INTEGER NOT NULL,
            protocol TEXT NOT NULL DEFAULT 'uniswap_v4',
            payload_json TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT 'sqlite_db',
            source_fingerprint TEXT,
            generated_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (surface, network, chain_id, protocol)
        )
        """,
    )


def _source_fingerprint(
    database_url: str,
    *,
    network: str,
    chain_id: int,
    protocol: str,
) -> str:
    cache_key = (network, int(chain_id), str(protocol or "").lower())
    ttl = _fingerprint_cache_ttl_seconds()
    with _FINGERPRINT_CACHE_LOCK:
        cached = _FINGERPRINT_CACHE.get(cache_key)
        if cached is not None and time.time() - cached[0] <= ttl:
            return cached[1]
    parts: list[str] = []
    for table, has_protocol in (
        ("v4_exchange_pools", True),
        ("v4_exchange_pool_liquidity_snapshots", True),
        ("v4_closed_loops", True),
        ("v4_closed_loop_hops", True),
        ("dex_candidate_pool_pairs", True),
        ("market_observation_yz_pairs", True),
        ("chain_tokens", False),
    ):
        try:
            if has_protocol:
                row = query_one(
                    database_url,
                    f"""
                    SELECT COUNT(*) AS c, COALESCE(MAX(updated_at), '') AS u
                    FROM {table}
                    WHERE network = ? AND chain_id = ? AND protocol = ?
                    """,
                    (network, chain_id, protocol),
                )
            else:
                row = query_one(
                    database_url,
                    f"""
                    SELECT COUNT(*) AS c, COALESCE(MAX(updated_at), '') AS u
                    FROM {table}
                    WHERE network = ? AND chain_id = ?
                    """,
                    (network, chain_id),
                )
            parts.append(f"{int(row.get('c') or 0)}:{row.get('u') or ''}")
        except Exception:
            parts.append("0:")
    fingerprint = f"{_SNAPSHOT_SCHEMA_VERSION}|" + "|".join(parts)
    with _FINGERPRINT_CACHE_LOCK:
        _FINGERPRINT_CACHE[cache_key] = (time.time(), fingerprint)
    return fingerprint


def _fingerprint_cache_ttl_seconds() -> float:
    raw = os.getenv("DEX_SURFACE_FINGERPRINT_CACHE_TTL_SECONDS", "30")
    try:
        return max(0.0, float(raw))
    except (TypeError, ValueError):
        return 5.0


def clear_dex_surface_snapshot_fingerprint_cache(
    *,
    network: str | None = None,
    chain_id: int | None = None,
    protocol: str | None = None,
) -> int:
    with _FINGERPRINT_CACHE_LOCK:
        keys = list(_FINGERPRINT_CACHE)
        removed = 0
        for key in keys:
            key_network, key_chain_id, key_protocol = key
            if network is not None and key_network != network:
                continue
            if chain_id is not None and key_chain_id != int(chain_id):
                continue
            if protocol is not None and str(protocol or "").lower() != key_protocol:
                continue
            _FINGERPRINT_CACHE.pop(key, None)
            removed += 1
        return removed


def load_dex_overview_snapshot(
    *,
    network: str,
    chain_id: int,
    protocol: str = "uniswap_v4",
    database_url: str | None = None,
) -> dict[str, Any] | None:
    db = str(absolute_database_path(database_url))
    _ensure_snapshot_table(db)
    rows = query(
        db,
        """
        SELECT payload_json, source_fingerprint, generated_at
        FROM dex_surface_snapshots
        WHERE surface = ? AND network = ? AND chain_id = ? AND protocol = ?
        """,
        (_SNAPSHOT_SURFACE, network, chain_id, protocol),
    )
    if not rows:
        return None
    row = rows[0]
    fingerprint = _source_fingerprint(
        db,
        network=network,
        chain_id=chain_id,
        protocol=protocol,
    )
    if str(row.get("source_fingerprint") or "") != fingerprint:
        execute(
            db,
            """
            DELETE FROM dex_surface_snapshots
            WHERE surface = ? AND network = ? AND chain_id = ? AND protocol = ?
            """,
            (_SNAPSHOT_SURFACE, network, chain_id, protocol),
        )
        return None
    try:
        payload = json.loads(row.get("payload_json") or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    payload["exists"] = True
    payload["path"] = "sqlite_db"
    payload["source"] = "sqlite_db"
    payload["snapshotGeneratedAt"] = row.get("generated_at") or ""
    payload["snapshotFingerprint"] = fingerprint
    return payload


def save_dex_overview_snapshot(
    snapshot: dict[str, Any],
    *,
    network: str,
    chain_id: int,
    protocol: str = "uniswap_v4",
    database_url: str | None = None,
) -> dict[str, Any]:
    db = str(absolute_database_path(database_url))
    _ensure_snapshot_table(db)
    now = _stamp()
    payload = deepcopy(snapshot)
    payload.pop("snapshotFingerprint", None)
    payload["exists"] = True
    payload["path"] = "sqlite_db"
    payload["source"] = "sqlite_db"
    payload["snapshotGeneratedAt"] = now
    fingerprint = _source_fingerprint(
        db,
        network=network,
        chain_id=chain_id,
        protocol=protocol,
    )
    payload_json = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    execute(
        db,
        """
        INSERT INTO dex_surface_snapshots (
            surface, network, chain_id, protocol, payload_json, source,
            source_fingerprint, generated_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, 'sqlite_db', ?, ?, ?)
        ON CONFLICT(surface, network, chain_id, protocol) DO UPDATE SET
            payload_json = excluded.payload_json,
            source = excluded.source,
            source_fingerprint = excluded.source_fingerprint,
            updated_at = excluded.updated_at
        """,
        (_SNAPSHOT_SURFACE, network, chain_id, protocol, payload_json, fingerprint, now, now),
    )
    return {
        "ok": True,
        "source": "sqlite_db",
        "network": network,
        "chain_id": chain_id,
        "protocol": protocol,
        "payloadBytes": len(payload_json.encode("utf-8")),
        "snapshotGeneratedAt": now,
    }


def clear_dex_surface_snapshots(
    *,
    network: str | None = None,
    chain_id: int | None = None,
    protocol: str | None = None,
    surface: str | None = None,
    database_url: str | None = None,
) -> int:
    db = str(absolute_database_path(database_url))
    _ensure_snapshot_table(db)
    conditions: list[str] = []
    params: list[Any] = []
    if surface:
        conditions.append("surface = ?")
        params.append(surface)
    if network:
        conditions.append("network = ?")
        params.append(network)
    if chain_id is not None:
        conditions.append("chain_id = ?")
        params.append(chain_id)
    if protocol:
        conditions.append("protocol = ?")
        params.append(protocol)
    sql = "DELETE FROM dex_surface_snapshots"
    if conditions:
        sql += " WHERE " + " AND ".join(conditions)
    return execute(db, sql, tuple(params))


__all__ = [
    "clear_dex_surface_snapshots",
    "clear_dex_surface_snapshot_fingerprint_cache",
    "load_dex_overview_snapshot",
    "save_dex_overview_snapshot",
]
