from __future__ import annotations

import time
from typing import Any, Callable


def unique_accounts(accounts: list[str]) -> list[str]:
    ordered: list[str] = []
    for account in accounts:
        item = str(account or "").strip()
        if item and item not in ordered:
            ordered.append(item)
    return ordered


def tier_accounts(
    database_url: str,
    accounts: list[str],
    loader: Callable[..., list[dict[str, Any]]],
    *,
    market_id: str | None = None,
    chain_id: int | None = None,
    market_kwargs_builder: Callable[[str | None, int | None], dict[str, object]],
) -> list[str]:
    account_set = set(accounts)
    ordered: list[str] = []

    def add(value: object) -> None:
        account = str(value or "").strip()
        if account and account in account_set and account not in ordered:
            ordered.append(account)

    market_kwargs = market_kwargs_builder(market_id, chain_id)
    try:
        rows = loader(database_url, limit=len(accounts), **market_kwargs)
    except TypeError:
        if market_id is not None or chain_id is not None:
            raise
        rows = loader(database_url, limit=len(accounts))
    for row in rows:
        add(row.get("account"))
    return ordered


def select_accounts(
    database_url: str,
    accounts: list[str],
    config: Any,
    *,
    scan_cache: dict[str, Any],
    core_loader: Callable[..., list[dict[str, Any]]],
    high_frequency_loader: Callable[..., list[dict[str, Any]]],
    market_kwargs_builder: Callable[[str | None, int | None], dict[str, object]],
    force: bool = False,
    market_id: str | None = None,
    chain_id: int | None = None,
    price_triggered_accounts: list[str] | None = None,
) -> dict[str, Any]:
    source_accounts = unique_accounts(accounts)
    scan_mode = str(getattr(config, "scan_mode", "timer_driven") or "timer_driven").strip().lower()
    if scan_mode == "price_driven" and not force:
        triggered_accounts = unique_accounts(price_triggered_accounts or [])
        selected = [account for account in triggered_accounts if account in source_accounts]
        if selected:
            strategy = "price_driven_scan"
            included_tiers = ["price_driven"]
        elif triggered_accounts:
            strategy = "price_driven_no_match"
            included_tiers = []
        else:
            strategy = "price_driven_idle"
            included_tiers = []
        return {
            "selected_accounts": selected,
            "strategy": strategy,
            "scan_mode": scan_mode,
            "included_tiers": included_tiers,
            "account_count": len(source_accounts),
            "selected_account_count": len(selected),
            "core_account_count": 0,
            "high_frequency_account_count": 0,
            "core_due": False,
            "high_frequency_due": False,
            "borrow_health_due": False,
            "price_triggered_account_count": len(triggered_accounts),
        }

    if not source_accounts:
        return {
            "selected_accounts": [],
            "strategy": "empty_account_pool",
            "scan_mode": scan_mode,
            "included_tiers": [],
            "account_count": 0,
            "selected_account_count": 0,
            "core_account_count": 0,
            "high_frequency_account_count": 0,
            "core_due": False,
            "high_frequency_due": False,
            "borrow_health_due": False,
        }

    try:
        core_accounts = tier_accounts(
            database_url,
            source_accounts,
            core_loader,
            market_id=market_id,
            chain_id=chain_id,
            market_kwargs_builder=market_kwargs_builder,
        )
        high_accounts = tier_accounts(
            database_url,
            source_accounts,
            high_frequency_loader,
            market_id=market_id,
            chain_id=chain_id,
            market_kwargs_builder=market_kwargs_builder,
        )
    except Exception:
        core_accounts = []
        high_accounts = []

    now = time.monotonic()
    last_core = float(scan_cache.get("last_core_scan_monotonic") or 0.0)
    last_high = float(scan_cache.get("last_high_frequency_scan_monotonic") or 0.0)
    last_borrow = float(scan_cache.get("last_borrow_health_scan_monotonic") or 0.0)
    core_due = force or now - last_core >= max(0.1, float(config.core_opportunity_refresh_seconds))
    high_due = force or now - last_high >= max(0.1, float(config.high_frequency_refresh_seconds))
    borrow_due = force or now - last_borrow >= max(0.1, float(config.borrow_health_refresh_seconds))

    if borrow_due:
        included_tiers = ["core_opportunity", "high_frequency", "borrow_health"]
        strategy = "borrow_health_full"
        selected = source_accounts
    elif high_due:
        included_tiers = ["core_opportunity", "high_frequency"]
        strategy = "high_frequency_refresh"
        selected = unique_accounts(core_accounts + high_accounts)
    elif core_due:
        included_tiers = ["core_opportunity"]
        strategy = "core_opportunity_refresh"
        selected = list(core_accounts)
    else:
        included_tiers = []
        strategy = "cooldown"
        selected = []

    if not selected:
        if high_accounts:
            included_tiers = ["high_frequency"]
            strategy = "fallback_high_frequency"
            selected = list(high_accounts)
        else:
            included_tiers = ["borrow_health"]
            strategy = "fallback_borrow_health"
            selected = list(source_accounts)

        return {
            "selected_accounts": selected,
            "strategy": strategy,
            "scan_mode": scan_mode,
            "included_tiers": included_tiers,
            "account_count": len(source_accounts),
            "selected_account_count": len(selected),
            "core_account_count": len(core_accounts),
            "high_frequency_account_count": len(high_accounts),
            "core_due": core_due,
            "high_frequency_due": high_due,
            "borrow_health_due": borrow_due,
        }

    return {
        "selected_accounts": selected,
        "strategy": strategy,
        "scan_mode": scan_mode,
        "included_tiers": included_tiers,
        "account_count": len(source_accounts),
        "selected_account_count": len(selected),
        "core_account_count": len(core_accounts),
        "high_frequency_account_count": len(high_accounts),
        "core_due": core_due,
        "high_frequency_due": high_due,
        "borrow_health_due": borrow_due,
    }


def mark_selection_finished(selection: dict[str, Any], *, scan_cache: dict[str, Any]) -> None:
    now = time.monotonic()
    tiers = set(selection.get("included_tiers") or [])
    if "core_opportunity" in tiers:
        scan_cache["last_core_scan_monotonic"] = now
    if "high_frequency" in tiers:
        scan_cache["last_high_frequency_scan_monotonic"] = now
    if "borrow_health" in tiers:
        scan_cache["last_borrow_health_scan_monotonic"] = now
    if "price_driven" in tiers:
        scan_cache["last_price_scan_monotonic"] = now
