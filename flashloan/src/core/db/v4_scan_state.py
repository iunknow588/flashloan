"""Persistent V4 live-refresh scan progress stored in SQLite.

The raw pool tables are the authoritative index and the ``v4_live_refresh``
initialization row records how far the last on-chain Initialize scan reached.
The next refresh starts from that block and only fetches newly created pools;
existing SQLite pools are merged into the scan before liquidity is read at the
latest block.
"""

from __future__ import annotations

from typing import Any

from core.db.storage_chain_core import load_initialization, upsert_initialization


V4_LIVE_REFRESH_DOMAIN = "v4_live_refresh"
V4_LIVE_REFRESH_PROTOCOL = "uniswap_v4"


def load_v4_scan_state(
    database_url: str,
    *,
    network: str,
    chain_id: int,
    protocol: str = V4_LIVE_REFRESH_PROTOCOL,
) -> dict[str, Any]:
    """Return persisted V4 scan progress for one network/chain."""
    from core.rs_bridge import load_v4_scan_state as _rust_load_v4_scan_state
    from core.rs_bridge import rust_available

    if rust_available():
        return _rust_load_v4_scan_state(
            network,
            chain_id,
            database_url=database_url,
            protocol=protocol,
        )
    row = load_initialization(
        database_url,
        network=network,
        chain_id=chain_id,
        protocol=protocol,
        domain=V4_LIVE_REFRESH_DOMAIN,
    )
    completeness = {}
    if isinstance(row, dict):
        raw = row.get("completeness_json")
        if isinstance(raw, dict):
            completeness = raw
        elif isinstance(raw, str):
            try:
                import json

                parsed = json.loads(raw)
            except (TypeError, ValueError, json.JSONDecodeError):
                parsed = {}
            if isinstance(parsed, dict):
                completeness = parsed
    return {
        "last_scanned_block": _int(completeness.get("last_scanned_block")),
        "index_from_block": _int(completeness.get("index_from_block")),
        "index_to_block": _int(completeness.get("index_to_block")),
        "liquidity_scan_block": _int(completeness.get("liquidity_scan_block")),
        "scanned_pool_count": _int(completeness.get("scanned_pool_count")),
        "pool_count": _int(completeness.get("pool_count")),
        "confirmed_liquidity_pool_count": _int(
            completeness.get("confirmed_liquidity_pool_count")
        ),
        "explicit_zero_liquidity_pool_count": _int(
            completeness.get("explicit_zero_liquidity_pool_count")
        ),
        "unscanned_pool_count": _int(completeness.get("unscanned_pool_count")),
        "direction_candidate_limit": _int(
            completeness.get("direction_candidate_limit"),
            5,
        ),
        "scan_mode": str(completeness.get("scan_mode") or "full"),
        "refreshed_at": row.get("refreshed_at") if isinstance(row, dict) else None,
    }


def update_v4_scan_state(
    database_url: str,
    *,
    network: str,
    chain_id: int,
    protocol: str = V4_LIVE_REFRESH_PROTOCOL,
    last_scanned_block: int | None = None,
    index_from_block: int | None = None,
    index_to_block: int | None = None,
    liquidity_scan_block: int | None = None,
    scanned_pool_count: int | None = None,
    pool_count: int | None = None,
    confirmed_liquidity_pool_count: int | None = None,
    explicit_zero_liquidity_pool_count: int | None = None,
    unscanned_pool_count: int | None = None,
    direction_candidate_limit: int = 5,
    scan_mode: str = "incremental",
) -> dict[str, Any]:
    """Upsert one ``v4_live_refresh`` progress row."""
    from core.rs_bridge import update_v4_scan_state as _rust_update_v4_scan_state
    from core.rs_bridge import rust_available

    if rust_available():
        return _rust_update_v4_scan_state(
            network,
            chain_id,
            database_url=database_url,
            protocol=protocol,
            last_scanned_block=last_scanned_block,
            index_from_block=index_from_block,
            index_to_block=index_to_block,
            liquidity_scan_block=liquidity_scan_block,
            scanned_pool_count=scanned_pool_count,
            pool_count=pool_count,
            confirmed_liquidity_pool_count=confirmed_liquidity_pool_count,
            explicit_zero_liquidity_pool_count=explicit_zero_liquidity_pool_count,
            unscanned_pool_count=unscanned_pool_count,
            direction_candidate_limit=direction_candidate_limit,
            scan_mode=scan_mode,
        )
    previous = load_v4_scan_state(
        database_url,
        network=network,
        chain_id=chain_id,
        protocol=protocol,
    )
    completeness = {
        "last_scanned_block": (
            int(last_scanned_block)
            if last_scanned_block is not None
            else previous["last_scanned_block"]
        ),
        "index_from_block": (
            int(index_from_block)
            if index_from_block is not None
            else previous["index_from_block"]
        ),
        "index_to_block": (
            int(index_to_block)
            if index_to_block is not None
            else previous["index_to_block"]
        ),
        "liquidity_scan_block": (
            int(liquidity_scan_block)
            if liquidity_scan_block is not None
            else previous["liquidity_scan_block"]
        ),
        "scanned_pool_count": (
            int(scanned_pool_count)
            if scanned_pool_count is not None
            else previous["scanned_pool_count"]
        ),
        "pool_count": (
            int(pool_count)
            if pool_count is not None
            else previous["pool_count"]
        ),
        "confirmed_liquidity_pool_count": (
            int(confirmed_liquidity_pool_count)
            if confirmed_liquidity_pool_count is not None
            else previous["confirmed_liquidity_pool_count"]
        ),
        "explicit_zero_liquidity_pool_count": (
            int(explicit_zero_liquidity_pool_count)
            if explicit_zero_liquidity_pool_count is not None
            else previous["explicit_zero_liquidity_pool_count"]
        ),
        "unscanned_pool_count": (
            int(unscanned_pool_count)
            if unscanned_pool_count is not None
            else previous["unscanned_pool_count"]
        ),
        "direction_candidate_limit": max(1, int(direction_candidate_limit)),
        "scan_mode": scan_mode,
    }
    upsert_initialization(
        database_url,
        network=network,
        chain_id=chain_id,
        protocol=protocol,
        domain=V4_LIVE_REFRESH_DOMAIN,
        status="ok",
        completeness=completeness,
    )
    return completeness


def _int(value: object, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


__all__ = [
    "V4_LIVE_REFRESH_DOMAIN",
    "V4_LIVE_REFRESH_PROTOCOL",
    "load_v4_scan_state",
    "update_v4_scan_state",
]
