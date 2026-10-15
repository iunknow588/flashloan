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
from typing import Any

from core.db.database_path import absolute_database_path
from core.db.storage_business_io import execute, query, query_one


_SNAPSHOT_SURFACE = "dex_overview_compact"
# Bump when the persisted snapshot schema changes so stale payloads are
# rebuilt instead of being replayed without the new fields.
_SNAPSHOT_SCHEMA_VERSION = 2


def _stamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _ensure_snapshot_table(database_url: str) -> None:
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
    return f"{_SNAPSHOT_SCHEMA_VERSION}|" + "|".join(parts)


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
    "load_dex_overview_snapshot",
    "save_dex_overview_snapshot",
]
