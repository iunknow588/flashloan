from __future__ import annotations

from typing import Any


LIQUIDATION_POOL_TIERS = ("normal", "high_frequency", "core")


def builtin_market_scan_policies(market_id: str, *, feature: str = "liquidation") -> list[dict[str, Any]]:
    if str(feature).strip().lower() != "liquidation":
        return []
    normalized_market_id = str(market_id or "").strip().lower()
    if not normalized_market_id:
        return []
    return [
        {
            "market_id": normalized_market_id,
            "feature": "liquidation",
            "pool_tier": "normal",
            "metric_name": "health_factor",
            "metric_min": 1.0,
            "metric_max": 1.5,
            "refresh_interval_seconds": 1800,
            "page_size": 100,
            "enabled": True,
            "metadata": {"label": "普通账号池", "source": "builtin_fallback"},
        },
        {
            "market_id": normalized_market_id,
            "feature": "liquidation",
            "pool_tier": "high_frequency",
            "metric_name": "health_factor",
            "metric_min": 1.0,
            "metric_max": 1.15,
            "refresh_interval_seconds": 300,
            "page_size": 100,
            "enabled": True,
            "metadata": {"label": "高频账号池", "source": "builtin_fallback"},
        },
        {
            "market_id": normalized_market_id,
            "feature": "liquidation",
            "pool_tier": "core",
            "metric_name": "expected_net_profit_usd",
            "metric_min": 1.0,
            "metric_max": None,
            "refresh_interval_seconds": 1,
            "page_size": 100,
            "enabled": True,
            "metadata": {"label": "核心机会池", "source": "builtin_fallback"},
        },
    ]


def market_scan_policies(
    market_id: str,
    *,
    feature: str = "liquidation",
    database_url: str | None = None,
) -> list[dict[str, Any]]:
    fallback = builtin_market_scan_policies(market_id, feature=feature)
    if not database_url:
        return fallback
    from db.storage_common import (
        database_unavailable_reason,
        is_database_unavailable_error,
        mark_database_unavailable,
    )

    if database_unavailable_reason(database_url):
        return fallback
    try:
        from db.storage_chain_markets import load_market_scan_policies

        rows = load_market_scan_policies(database_url, market_id=market_id, feature=feature)
    except Exception as exc:
        if is_database_unavailable_error(exc):
            mark_database_unavailable(database_url, exc)
        return fallback
    return rows or fallback


def market_scan_refresh_profile(
    market_id: str,
    *,
    feature: str = "liquidation",
    database_url: str | None = None,
) -> dict[str, float]:
    policies = {
        str(row.get("pool_tier") or ""): row
        for row in market_scan_policies(market_id, feature=feature, database_url=database_url)
        if row.get("enabled")
    }

    def interval(tier: str, fallback: float) -> float:
        try:
            return max(1.0, float(policies.get(tier, {}).get("refresh_interval_seconds") or fallback))
        except (TypeError, ValueError):
            return fallback

    return {
        "borrow_health_refresh_seconds": interval("normal", 1800.0),
        "high_frequency_refresh_seconds": interval("high_frequency", 300.0),
        "core_opportunity_refresh_seconds": interval("core", 1.0),
    }
