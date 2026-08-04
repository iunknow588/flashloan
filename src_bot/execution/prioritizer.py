from __future__ import annotations

from typing import Iterable

from execution.health_checker import health_factor_band
from execution.scanner import normalize_accounts


def watched_health_rows(rows: Iterable[dict], max_health_factor: float = 1.5) -> list[dict]:
    watched = []
    for row in rows:
        try:
            health_factor = float(row.get("health_factor"))
        except (TypeError, ValueError):
            continue
        if health_factor >= float(max_health_factor):
            continue
        item = dict(row)
        item["health_factor_band"] = health_factor_band(health_factor)
        watched.append(item)
    watched.sort(key=lambda row: float(row.get("health_factor", 10.0)))
    return watched


def incremental_scan_account_groups(
    accounts: Iterable[str],
    previous_rows: Iterable[dict],
    *,
    watch_health_factor: float = 1.5,
    full_scan_due: bool = False,
    max_accounts: int = 5000,
) -> dict[str, list[str]]:
    unique_accounts = normalize_accounts(accounts, max_accounts)
    watch_accounts: list[str] = []
    for row in watched_health_rows(previous_rows, watch_health_factor):
        normalized = normalize_accounts([str(row.get("account") or "")], max_accounts=1)
        if not normalized:
            continue
        account = normalized[0]
        if account in unique_accounts and account not in watch_accounts:
            watch_accounts.append(account)
    return {
        "high_frequency_accounts": watch_accounts[: max(1, int(max_accounts))],
        "full_scan_accounts": unique_accounts,
        "scan_accounts": (unique_accounts if full_scan_due else watch_accounts)[: max(1, int(max_accounts))],
        "strategy": ["watch_high_frequency"] + (["full_low_frequency"] if full_scan_due else []),
    }


def split_candidate_accounts(accounts: Iterable[dict], warning_threshold: float, liquidation_threshold: float) -> dict[str, list[dict]]:
    result = {"warning_accounts": [], "liquidation_accounts": [], "healthy_accounts": []}
    for item in accounts:
        try:
            health_factor = float(item.get("health_factor"))
        except (TypeError, ValueError):
            continue
        if health_factor < liquidation_threshold:
            result["liquidation_accounts"].append(item)
        elif health_factor < warning_threshold:
            result["warning_accounts"].append(item)
        else:
            result["healthy_accounts"].append(item)
    return result
