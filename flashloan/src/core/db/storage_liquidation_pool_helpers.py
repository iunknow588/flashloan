"""Private cache and scan config helpers for liquidation pool storage."""

from __future__ import annotations

import copy
import hashlib
import json
import threading
import time
from typing import Any

from core.config_schema import parse_env_float
from core.market_config import liquidation_market_config, liquidation_market_config_for


_POOL_READ_CACHE_LOCK = threading.Lock()
_POOL_READ_CACHE: dict[tuple[str, str, int, int], dict[str, Any]] = {}
_POOL_WRITE_CACHE_LOCK = threading.Lock()
_POOL_WRITE_CACHE: dict[tuple[str, str, str], str] = {}


def database_cache_key(database_url: str) -> str:
    return hashlib.sha256(database_url.encode("utf-8")).hexdigest()[:16]


def refresh_seconds(name: str, default: float) -> float:
    value, _ = parse_env_float(name, default, minimum=0.1)
    return max(0.1, float(value))


def borrow_health_refresh_seconds() -> float:
    return refresh_seconds("LIQUIDATION_BORROW_HEALTH_REFRESH_SECONDS", 1800.0)


def high_frequency_refresh_seconds() -> float:
    return refresh_seconds("LIQUIDATION_HIGH_FREQUENCY_REFRESH_SECONDS", 300.0)


def core_opportunity_refresh_seconds() -> float:
    return refresh_seconds("LIQUIDATION_CORE_OPPORTUNITY_REFRESH_SECONDS", 1.0)


def pool_counts_refresh_seconds() -> float:
    return refresh_seconds("LIQUIDATION_POOL_COUNTS_REFRESH_SECONDS", 1.0)


def cache_lookup(cache_key: tuple[str, str, int, int], ttl_seconds: float) -> Any | None:
    now = time.monotonic()
    with _POOL_READ_CACHE_LOCK:
        cached = _POOL_READ_CACHE.get(cache_key)
        if cached and now - float(cached.get("updated_at") or 0.0) < ttl_seconds:
            return copy.deepcopy(cached.get("value"))
    return None


def cache_store(cache_key: tuple[str, str, int, int], value: Any) -> None:
    with _POOL_READ_CACHE_LOCK:
        _POOL_READ_CACHE[cache_key] = {"updated_at": time.monotonic(), "value": copy.deepcopy(value)}


def invalidate_pool_cache(database_url: str, *tables: str) -> None:
    if not tables:
        return
    db_key = database_cache_key(database_url)
    with _POOL_READ_CACHE_LOCK:
        for cache_key in list(_POOL_READ_CACHE):
            if cache_key[0] == db_key and any(cache_key[1] == table or cache_key[1].startswith(f"{table}:") for table in tables):
                del _POOL_READ_CACHE[cache_key]


def payload_fingerprint(payload: Any) -> str:
    encoded = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def write_changed(database_url: str, namespace: str, key: str, payload: Any) -> bool:
    cache_key = (database_cache_key(database_url), namespace, str(key))
    fingerprint = payload_fingerprint(payload)
    with _POOL_WRITE_CACHE_LOCK:
        if _POOL_WRITE_CACHE.get(cache_key) == fingerprint:
            return False
        _POOL_WRITE_CACHE[cache_key] = fingerprint
    return True


def json_value(value: Any) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=True, separators=(",", ":"))


def market_scope(market_id: str | None = None, chain_id: int | None = None) -> dict[str, Any]:
    market = liquidation_market_config_for(market_id, chain_id=chain_id) if (market_id is not None or chain_id is not None) else liquidation_market_config()
    return {"market_id": market.market_id, "chain_id": market.chain_id, "network": market.network, "protocol": market.protocol}


def cache_namespace(table: str, scope: dict[str, Any]) -> str:
    return f"{table}:{scope['market_id']}:{scope['chain_id']}"


def scoped_account_key(scope: dict[str, Any], account: str) -> str:
    return f"{scope['market_id']}:{scope['chain_id']}:{account}"


def upsert_scan_config_snapshot(
    cursor,
    *,
    database_url: str | None = None,
    config_key: str,
    source_table: str,
    payload: dict[str, Any],
    source_key: str | None = None,
    category: str = "scan",
    active: bool = True,
    market_id: str | None = None,
    chain_id: int | None = None,
) -> None:
    scope = market_scope(market_id, chain_id)
    item = dict(payload or {})
    item.setdefault("config_key", config_key)
    item.setdefault("source_table", source_table)
    item.setdefault("market_id", scope["market_id"])
    item.setdefault("chain_id", scope["chain_id"])
    if database_url and not write_changed(
        database_url,
        "liquidation_scan_config_library",
        config_key,
        {
            "config_key": config_key,
            "category": category,
            "source_table": source_table,
            "source_key": source_key,
            "active": bool(active),
            "payload": item,
        },
    ):
        return
    from core.kv_rows import upsert_row
    key = f"{scope['market_id']}|{scope['chain_id']}|{config_key}"
    upsert_row(
        database_url,
        "liquidation_scan_config_library",
        key,
        {
            "market_id": scope["market_id"],
            "chain_id": scope["chain_id"],
            "network": scope["network"],
            "protocol": scope["protocol"],
            "config_key": config_key,
            "category": category,
            "source_table": source_table,
            "source_key": source_key,
            "active": bool(active),
            "payload_json": json_value(item),
            "updated_at": time.time(),
        },
    )


__all__ = [
    "borrow_health_refresh_seconds",
    "cache_lookup",
    "cache_namespace",
    "cache_store",
    "core_opportunity_refresh_seconds",
    "database_cache_key",
    "high_frequency_refresh_seconds",
    "invalidate_pool_cache",
    "json_value",
    "market_scope",
    "payload_fingerprint",
    "pool_counts_refresh_seconds",
    "refresh_seconds",
    "scoped_account_key",
    "upsert_scan_config_snapshot",
    "write_changed",
]
