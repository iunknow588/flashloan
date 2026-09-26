from __future__ import annotations

import time
from typing import Any

from core.kv_rows import collection_key, count, delete_key, load_rows, upsert_row
from core.local_kv import ensure_store
from core.market_profiles import BUILTIN_MARKET_SUMMARIES


def _builtin_chain_market_rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for market_id, summary in BUILTIN_MARKET_SUMMARIES.items():
        row = dict(summary)
        row.setdefault("market_id", market_id)
        row.setdefault("canonical_market_id", market_id)
        row.setdefault("legacy_market_id", market_id)
        row.setdefault("network", str(row.get("network") or "").lower())
        row.setdefault("protocol_id", row.get("protocol") or row.get("protocol_id"))
        if row.get("chain_id") is not None:
            row["chain_id"] = int(row["chain_id"])
        rows.append(row)
    return rows


def ensure_chain_market_registry_schema(database_url: str) -> None:
    ensure_store(database_url)


def seed_builtin_chain_markets(database_url: str) -> None:
    ensure_store(database_url)
    builtin_rows = _builtin_chain_market_rows()
    builtin_ids = {str(row["market_id"]).strip().lower() for row in builtin_rows}
    for row in load_rows(database_url, "chain_markets"):
        if str(row.get("market_id") or "").strip().lower() not in builtin_ids:
            delete_key(database_url, "chain_markets", row.get("_key") or row.get("market_id") or "")
    for row in builtin_rows:
        upsert_row(database_url, "chain_markets", row["market_id"], row)


def load_chain_market_summaries(database_url: str) -> list[dict[str, Any]]:
    ensure_store(database_url)
    seed_builtin_chain_markets(database_url)
    rows = load_rows(database_url, "chain_markets")
    return rows


def load_market_scan_policies(
    database_url: str,
    market_id: str | None = None,
    feature: str | None = None,
    pool_tier: str | None = None,
) -> list[dict[str, Any]]:
    rows = load_rows(database_url, "market_scan_policies")
    if market_id is not None:
        rows = [row for row in rows if str(row.get("market_id")) == str(market_id)]
    if feature is not None:
        rows = [row for row in rows if str(row.get("feature")) == str(feature)]
    if pool_tier is not None:
        rows = [row for row in rows if str(row.get("pool_tier")) == str(pool_tier)]
    return rows


def load_current_dex_arbitrage_candidates(
    database_url: str,
    market_id: str | None = None,
) -> list[dict[str, Any]]:
    rows = load_rows(database_url, "dex_arbitrage_candidates")
    if market_id is not None:
        rows = [row for row in rows if str(row.get("market_id")) == str(market_id)]
    rows.sort(key=lambda row: str(row.get("route_key") or ""))
    return rows


def replace_current_dex_arbitrage_candidates(
    database_url: str,
    candidates: list[dict[str, Any]],
    *,
    market_id: str | None = None,
) -> int:
    ensure_store(database_url)
    if market_id is not None:
        for row in load_rows(database_url, "dex_arbitrage_candidates"):
            if str(row.get("market_id")) == str(market_id):
                from core.local_kv import delete
                delete(database_url, "dex_arbitrage_candidates", row.get("_key") or row.get("route_key") or "")
    count_written = 0
    for row in candidates:
        key = collection_key(row.get("market_id") or market_id or "default", row.get("route_key") or time.time())
        payload = dict(row)
        payload["_key"] = key
        upsert_row(database_url, "dex_arbitrage_candidates", key, payload)
        count_written += 1
    return count_written


def record_dex_arbitrage_attempt(
    database_url: str,
    attempt: dict[str, Any],
    *,
    market_id: str | None = None,
    chain_id: int | None = None,
) -> int:
    ensure_store(database_url)
    attempt_id = count(database_url, "dex_arbitrage_attempts") + 1
    payload = {
        "id": attempt_id,
        "market_id": market_id or attempt.get("market_id") or "default",
        "chain_id": chain_id or attempt.get("chain_id") or 0,
        "attempt": attempt,
        "created_at": time.time(),
    }
    upsert_row(database_url, "dex_arbitrage_attempts", str(attempt_id), payload)
    return attempt_id


__all__ = [
    "ensure_chain_market_registry_schema",
    "load_chain_market_summaries",
    "load_current_dex_arbitrage_candidates",
    "load_market_scan_policies",
    "record_dex_arbitrage_attempt",
    "replace_current_dex_arbitrage_candidates",
    "seed_builtin_chain_markets",
]
