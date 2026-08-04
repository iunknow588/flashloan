from __future__ import annotations

from typing import Callable, Optional


def build_health_summary(
    rows: list[dict],
    *,
    account_count: int,
    account_source: str,
    config,
    rpc_url: Optional[str],
    error: Optional[str],
    registry_window: dict,
    scan_cache: dict,
    discovery_cache: dict,
    retention_days: int,
    discovery_interval_seconds: float,
    backfill_interval_seconds: float,
    recent_discovery_days: int,
    backfill_window_days: int,
) -> dict:
    liquidatable_count = sum(1 for row in rows if row.get("status") == "liquidatable")
    warning_count = sum(1 for row in rows if row.get("status") == "warning")
    healthy_count = sum(1 for row in rows if row.get("status") == "healthy")
    worst_row = rows[0] if rows else None
    return {
        "account_source": account_source,
        "source_ready": account_count > 0,
        "account_count": account_count,
        "scanned_count": len(rows),
        "liquidatable_count": liquidatable_count,
        "warning_count": warning_count,
        "healthy_count": healthy_count,
        "warning_health_factor": config.warning_health_factor,
        "liquidation_health_factor": config.liquidation_health_factor,
        "wide_scan_seconds": config.wide_scan_seconds,
        "near_scan_seconds": config.near_scan_seconds,
        "rpc_url": rpc_url,
        "error": error,
        "worst_account": worst_row.get("account") if worst_row else None,
        "worst_health_factor": worst_row.get("health_factor") if worst_row else None,
        "retention_days": retention_days,
        "registry_window": registry_window,
        "scan_running": bool(scan_cache.get("running")),
        "scan_started_at": scan_cache.get("started_at"),
        "scan_finished_at": scan_cache.get("finished_at"),
        "stage": scan_cache.get("stage") or "idle",
        "discovery_running": bool(discovery_cache.get("running")),
        "discovery_started_at": discovery_cache.get("started_at"),
        "discovery_finished_at": discovery_cache.get("finished_at"),
        "discovery_stage": discovery_cache.get("stage") or "idle",
        "discovery_last_result": discovery_cache.get("last_result"),
        "discovery_interval_seconds": discovery_interval_seconds,
        "backfill_interval_seconds": backfill_interval_seconds,
        "last_backfill_at": discovery_cache.get("last_backfill_at"),
        "historical_cursor_at": discovery_cache.get("historical_cursor_at"),
        "recent_discovery_days": recent_discovery_days,
        "backfill_window_days": backfill_window_days,
    }


def attach_scan_state(
    payload: dict,
    ttl_seconds: float,
    *,
    scan_cache: dict,
    running: bool,
    cache_age_seconds: Optional[float] = None,
    cooldown_remaining_seconds: Optional[float] = None,
) -> dict:
    current = dict(payload)
    summary = dict(current.get("summary") or {})
    summary["scan_running"] = running
    summary["scan_started_at"] = scan_cache.get("started_at")
    summary["scan_finished_at"] = scan_cache.get("finished_at")
    summary["stage"] = scan_cache.get("stage") or summary.get("stage") or "idle"
    summary["scan_interval_seconds"] = ttl_seconds
    if cache_age_seconds is not None:
        summary["scan_cache_age_seconds"] = max(0.0, cache_age_seconds)
    if cooldown_remaining_seconds is not None:
        summary["scan_cooldown_remaining_seconds"] = max(0.0, cooldown_remaining_seconds)
    current["summary"] = summary
    return current


def display_health_rows(rows: list[dict], *, limit: int, band: Callable[[float], str]) -> list[dict]:
    ranked = []
    for row in rows:
        item = dict(row)
        try:
            item["health_factor_band"] = item.get("health_factor_band") or band(float(item.get("health_factor")))
        except (TypeError, ValueError):
            pass
        ranked.append(item)
    ranked.sort(
        key=lambda row: (
            1 if row.get("status") == "error" else 0,
            float(row.get("health_factor", 10.0)) if isinstance(row.get("health_factor"), (int, float)) else 10.0,
            str(row.get("account") or ""),
        )
    )
    return ranked[:limit]


def account_tier_summary(window: dict) -> dict:
    active = int(window.get("active_count") or 0)
    hot = int(window.get("hot_count") or 0)
    warm = int(window.get("warm_count") or 0)
    cold = int(window.get("cold_count") or 0)
    return {
        "hot_count": hot,
        "warm_count": warm,
        "cold_count": cold,
        "classified_count": hot + warm + cold,
        "active_count": active,
    }


def build_borrow_pool_summary(
    rows: list[dict],
    *,
    config,
    display_limit: int,
    scan_cache: dict,
    scan_interval_seconds: float,
    account_tiers: dict,
    scanned: bool = False,
    scan_payload: Optional[dict] = None,
) -> dict:
    scan_summary = dict((scan_payload or {}).get("summary") or {})
    worst_row = rows[0] if rows else None
    latest_batch = scan_summary.get("latest_batch")
    pool_counts = scan_summary.get("pool_counts") or {}
    risk_count = int(pool_counts.get("borrow_health_count") or scan_summary.get("risk_count") or len(rows))
    active_account_count = int(account_tiers.get("active_count") or scan_summary.get("account_count") or 1)
    return {
        "count": risk_count,
        "display_limit": display_limit,
        "watch_health_factor": config.watch_health_factor,
        "worst_account": worst_row.get("account") if worst_row else None,
        "worst_health_factor": worst_row.get("health_factor") if worst_row else None,
        "scanned": scanned,
        "scan_response_source": scan_summary.get("scan_response_source") or ("chain_scan" if scanned else "database_display"),
        "manual_force_scan": bool(scan_summary.get("manual_force_scan")),
        "scan_running": bool(scan_cache.get("running")),
        "scan_started_at": scan_cache.get("started_at"),
        "scan_finished_at": scan_cache.get("finished_at"),
        "stage": scan_summary.get("stage") or scan_cache.get("stage") or "idle",
        "scan_interval_seconds": scan_interval_seconds,
        "source_account_count": scan_summary.get("account_count"),
        "scanned_account_count": scan_summary.get("scanned_count"),
        "selected_account_count": scan_summary.get("selected_account_count"),
        "scan_strategy": scan_summary.get("scan_strategy"),
        "scan_included_tiers": scan_summary.get("scan_included_tiers") or [],
        "core_due": scan_summary.get("core_due"),
        "high_frequency_due": scan_summary.get("high_frequency_due"),
        "borrow_health_due": scan_summary.get("borrow_health_due"),
        "core_account_count": scan_summary.get("core_account_count"),
        "high_frequency_account_count": scan_summary.get("high_frequency_account_count"),
        "risk_count": risk_count,
        "entered_count": scan_summary.get("entered_count"),
        "exited_count": scan_summary.get("exited_count"),
        "block_number": scan_summary.get("block_number"),
        "latest_batch": latest_batch,
        "account_tiers": account_tiers,
        "risk_pool_conversion_rate": (
            float(risk_count) / float(active_account_count)
        ),
        "error": scan_summary.get("error"),
    }
