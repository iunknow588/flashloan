from __future__ import annotations

import time
from typing import Any

from core.kv_rows import count, delete_key, load_rows, upsert_row, upsert_rows
from core.local_kv import ensure_store, scan as kv_scan
from core.db.storage_liquidation_reports import preserve_existing_positions as _preserve_existing_positions


def _scope_values(*, market_id: str | None = None, chain_id: int | None = None) -> dict[str, Any]:
    return {
        "market_id": str(market_id or "default"),
        "chain_id": int(chain_id) if chain_id is not None else 0,
        "network": str(market_id or "local"),
        "protocol": "aave",
    }


def _write_liquidation_account_scan(database_url: str, report: dict[str, Any]) -> bool:
    account = str(report.get("account") or "").strip()
    if not account:
        return False
    scope = _scope_values(
        market_id=report.get("market_id") or report.get("context", {}).get("market_id"),
        chain_id=report.get("chain_id") or report.get("context", {}).get("chain_id"),
    )
    summary = report.get("summary") or {}
    stored_report = _preserve_existing_positions({}, report)
    upsert_row(
        database_url,
        "liquidation_accounts",
        f"{scope['market_id']}|{scope['chain_id']}|{account.lower()}",
        {
            **scope,
            "account": account,
            "source": "scan",
            "active": True,
            "updated_at": time.time(),
            "last_scanned_at": time.time(),
            "last_health_factor": summary.get("health_factor"),
            "last_status": summary.get("status"),
            "last_health_factor_band": summary.get("health_factor_band"),
            "last_candidate_count": summary.get("candidate_count"),
            "last_total_collateral_base": summary.get("total_collateral_base") or report.get("total_collateral_base"),
            "last_total_debt_base": summary.get("total_debt_base") or report.get("total_debt_base"),
            "last_summary_json": summary,
            "last_report_json": stored_report,
        },
    )
    upsert_row(
        database_url,
        "liquidation_account_health_scans",
        f"{scope['market_id']}|{scope['chain_id']}|{account.lower()}|{time.time()}",
        {
            **scope,
            "account": account,
            "scanned_at": time.time(),
            "health_factor": summary.get("health_factor"),
            "status": summary.get("status"),
            "health_factor_band": summary.get("health_factor_band"),
            "candidate_count": summary.get("candidate_count"),
            "summary_json": summary,
            "report_json": stored_report,
        },
    )
    return True


def write_liquidation_account_scan_reports(database_url: str, reports: list[dict[str, Any]]) -> int:
    recorded = 0
    for report in reports:
        if _write_liquidation_account_scan(database_url, report):
            recorded += 1
    return recorded


def record_liquidation_account_scans(database_url: str, reports: list[dict[str, Any]]) -> int:
    if not reports:
        return 0
    ensure_store(database_url)
    return write_liquidation_account_scan_reports(database_url, reports)


def record_liquidation_account_scan(database_url: str, report: dict[str, Any]) -> None:
    record_liquidation_account_scans(database_url, [report])


def load_latest_liquidation_account_reports(
    database_url: str,
    limit: int = 500,
    *,
    market_id: str | None = None,
    chain_id: int | None = None,
) -> list[dict[str, Any]]:
    rows = load_rows(database_url, "liquidation_accounts")
    if market_id is not None:
        rows = [row for row in rows if str(row.get("market_id")) == str(market_id)]
    if chain_id is not None:
        rows = [row for row in rows if int(row.get("chain_id") or 0) == int(chain_id)]
    rows.sort(key=lambda row: str(row.get("last_scanned_at") or ""), reverse=True)
    return rows[:max(1, int(limit))]


def prune_liquidation_accounts(database_url: str, retained_days: int = 365) -> int:
    ensure_store(database_url)
    cutoff = time.time() - max(1, int(retained_days)) * 86400
    removed = 0
    for item in kv_scan(database_url, "liquidation_account_health_scans"):
        scanned_at = _numeric_timestamp(item.get("value") or {}, "scanned_at")
        if 0 < scanned_at < cutoff and delete_key(database_url, "liquidation_account_health_scans", item["key"]):
            removed += 1
    for item in kv_scan(database_url, "liquidation_accounts"):
        row = item.get("value") or {}
        if bool(row.get("active")):
            continue
        last_seen = _numeric_timestamp(row, "last_scanned_at") or _numeric_timestamp(row, "updated_at")
        if 0 < last_seen < cutoff and delete_key(database_url, "liquidation_accounts", item["key"]):
            removed += 1
    return removed


def _numeric_timestamp(row: dict[str, Any], key: str) -> float:
    try:
        return float(row.get(key) or 0)
    except (TypeError, ValueError):
        return 0.0


__all__ = [
    "load_latest_liquidation_account_reports",
    "prune_liquidation_accounts",
    "record_liquidation_account_scan",
    "record_liquidation_account_scans",
    "write_liquidation_account_scan_reports",
]
