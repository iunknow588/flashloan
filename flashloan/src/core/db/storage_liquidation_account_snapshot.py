"""Account pool snapshot loader backed by the local KV store."""

from __future__ import annotations

from typing import Any

from core.kv_rows import load_rows


def _float_or_zero(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def load_liquidation_account_pool_snapshot(
    database_url: str,
    account: str,
    *,
    market_id: str | None = None,
    chain_id: int | None = None,
) -> dict[str, Any]:
    normalized = str(account or "").strip()
    if not normalized:
        return {"found": False, "error": "account is required"}
    account_rows = [
        row
        for row in load_rows(database_url, "liquidation_accounts")
        if str(row.get("account") or "").lower() == normalized.lower()
        and (market_id is None or str(row.get("market_id")) == str(market_id))
        and (chain_id is None or int(row.get("chain_id") or 0) == int(chain_id))
    ]
    borrow_rows = [
        row
        for row in load_rows(database_url, "liquidation_borrow_health_pool")
        if str(row.get("account") or "").lower() == normalized.lower()
        and (market_id is None or str(row.get("market_id")) == str(market_id))
        and (chain_id is None or int(row.get("chain_id") or 0) == int(chain_id))
    ]
    core_rows = [
        row
        for row in load_rows(database_url, "liquidation_core_opportunity_pool")
        if str(row.get("account") or "").lower() == normalized.lower()
        and (market_id is None or str(row.get("market_id")) == str(market_id))
        and (chain_id is None or int(row.get("chain_id") or 0) == int(chain_id))
    ]
    high_rows = [
        row
        for row in load_rows(database_url, "liquidation_high_frequency_pool")
        if str(row.get("account") or "").lower() == normalized.lower()
        and (market_id is None or str(row.get("market_id")) == str(market_id))
        and (chain_id is None or int(row.get("chain_id") or 0) == int(chain_id))
    ]
    report_sources: list[dict[str, Any]] = []
    for row in [*borrow_rows, *core_rows, *high_rows, *account_rows]:
        report = row.get("report_json") or {}
        if isinstance(report, dict):
            report_sources.append(report)
        summary = row.get("last_summary_json") or row.get("summary_json") or {}
        if isinstance(summary, dict):
            report_sources.append({"summary": summary})
    report: dict[str, Any] = {"summary": {}}
    for source in report_sources:
        report.setdefault("summary", {}).update(source.get("summary") or {})
        if source.get("positions"):
            report["positions"] = source["positions"]
        if source.get("liquidation_candidates"):
            report["liquidation_candidates"] = source["liquidation_candidates"]
    positions = report.get("positions") or []
    summary = report.get("summary") or {}
    if positions:
        summary.setdefault("positions_count", len(positions))
    registry = account_rows[0] if account_rows else None
    found = bool(registry or borrow_rows or core_rows or high_rows)
    return {
        "found": found,
        "cached": True,
        "source": "local_kv",
        "market_id": str(registry.get("market_id") if registry else (market_id or "default")),
        "chain_id": int(registry.get("chain_id") if registry and registry.get("chain_id") is not None else (chain_id or 0)),
        "account": normalized,
        "summary": summary,
        "liquidation_profit": report.get("liquidation_profit") or {},
        "recommended_candidate": None,
        "liquidation_candidates": report.get("liquidation_candidates") or [],
        "positions": positions,
        "positions_source": "database_health_scan_history" if positions else "unavailable",
        "context": {
            "source": "local_kv",
            "registry": {
                "active": bool(registry and registry.get("active")),
                "market_id": registry.get("market_id") if registry else None,
                "chain_id": registry.get("chain_id") if registry else None,
                "network": registry.get("network") if registry else None,
                "protocol": registry.get("protocol") if registry else None,
                "source": registry.get("source") if registry else None,
                "activity_tier": registry.get("activity_tier") if registry else None,
            },
            "borrow_pool": {"active": bool(borrow_rows), "rank": None},
            "core_opportunity": core_rows[0] if core_rows else None,
            "high_frequency": {"active": bool(high_rows), "rank": None},
        },
    }


__all__ = ["load_liquidation_account_pool_snapshot"]
