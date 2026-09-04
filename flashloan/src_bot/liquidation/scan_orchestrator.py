from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass(frozen=True)
class BorrowHealthScanResult:
    source_account_count: int = 0
    selected_accounts: list[str] = field(default_factory=list)
    rows: list[dict[str, Any]] = field(default_factory=list)
    rpc_url: str | None = None
    error: str | None = None
    sync_result: dict[str, int] = field(default_factory=dict)
    block_number: int | None = None
    scan_selection: dict[str, Any] = field(default_factory=dict)


def build_account_health_reports(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "account": row.get("account"),
            "summary": {
                "health_factor": row.get("health_factor"),
                "status": row.get("status"),
                "health_factor_band": row.get("health_factor_band"),
                "candidate_count": len(row.get("liquidation_candidates") or []),
                "total_collateral_base": row.get("total_collateral_base") or row.get("total_collateral_in_base_currency"),
                "total_debt_base": row.get("total_debt_base") or row.get("total_debt_in_base_currency"),
            },
            "liquidation_profit": row.get("liquidation_profit"),
        }
        for row in rows
    ]


def scan_progress(
    *,
    source_account_count: int,
    selected_account_count: int,
    scanned_count: int,
    scan_selection: dict[str, Any],
    rpc_url: str | None = None,
) -> dict[str, Any]:
    progress = {
        "account_count": source_account_count,
        "selected_account_count": selected_account_count,
        "scanned_count": scanned_count,
        "scan_strategy": scan_selection.get("strategy"),
    }
    if rpc_url:
        progress["rpc_url"] = rpc_url
    return progress


def run_borrow_health_scan(
    database_url: str,
    config: Any,
    *,
    force: bool,
    market_kwargs: dict[str, Any],
    ensure_schema: Callable[[str], None],
    load_accounts: Callable[..., list[str]],
    unique_accounts: Callable[[list[str]], list[str]],
    select_accounts: Callable[..., dict[str, Any]],
    load_price_triggered_accounts: Callable[..., list[str]] | None = None,
    rpc_urls: Callable[[], list[str]],
    pool_address: Callable[[], str],
    scan_health: Callable[[list[str], str, str, Any], list[dict[str, Any]]],
    block_number: Callable[[str], int | None],
    sync_pool: Callable[..., dict[str, int]],
    prune_accounts: Callable[..., Any],
    retention_days: Callable[[], int],
    mark_selection_finished: Callable[[dict[str, Any]], None],
    scan_error_message: Callable[[object], str | None],
    progress: Callable[[dict[str, Any]], None] | None = None,
) -> BorrowHealthScanResult:
    ensure_schema(database_url)
    source_accounts = load_accounts(database_url, **market_kwargs)
    source_account_count = len(unique_accounts(source_accounts))
    price_triggered_accounts: list[str] = []
    scan_mode = str(getattr(config, "scan_mode", "timer_driven") or "timer_driven").strip().lower()
    if scan_mode == "price_driven" and not force and load_price_triggered_accounts is not None:
        try:
            price_triggered_accounts = load_price_triggered_accounts(**market_kwargs)
        except TypeError:
            if market_kwargs:
                raise
            price_triggered_accounts = load_price_triggered_accounts()
    if scan_mode == "price_driven" and not force:
        selection = select_accounts(
            database_url,
            source_accounts,
            config,
            force=force,
            price_triggered_accounts=price_triggered_accounts,
            **market_kwargs,
        )
    else:
        selection = select_accounts(
            database_url,
            source_accounts,
            config,
            force=force,
            **market_kwargs,
        )
    accounts = list(selection.get("selected_accounts") or [])
    if progress:
        progress(scan_progress(
            source_account_count=source_account_count,
            selected_account_count=len(accounts),
            scanned_count=0,
            scan_selection=selection,
        ))
    if not accounts:
        no_price_accounts = str(selection.get("strategy") or "").startswith("price_driven")
        return BorrowHealthScanResult(
            source_account_count=source_account_count,
            selected_accounts=accounts,
            error=None if no_price_accounts else "database liquidation account table is empty",
            scan_selection=selection,
        )

    error = None
    for candidate in rpc_urls():
        try:
            if progress:
                progress(scan_progress(
                    source_account_count=source_account_count,
                    selected_account_count=len(accounts),
                    scanned_count=0,
                    scan_selection=selection,
                    rpc_url=candidate,
                ))
            rows = scan_health(accounts, pool_address(), candidate, config)
            if progress:
                progress(scan_progress(
                    source_account_count=source_account_count,
                    selected_account_count=len(accounts),
                    scanned_count=len(accounts),
                    scan_selection=selection,
                    rpc_url=candidate,
                ))
            sync_result = sync_pool(
                database_url,
                rows,
                config.watch_health_factor,
                account_reports=build_account_health_reports(rows),
                min_operator_net_profit_usd=config.min_operator_net_profit_usd,
                **market_kwargs,
            )
            prune_accounts(database_url, retained_days=retention_days())
            mark_selection_finished(selection)
            return BorrowHealthScanResult(
                source_account_count=source_account_count,
                selected_accounts=accounts,
                rows=rows,
                rpc_url=candidate,
                error=None,
                sync_result=sync_result,
                block_number=block_number(candidate),
                scan_selection=selection,
            )
        except Exception as exc:
            error = scan_error_message(exc)
    return BorrowHealthScanResult(
        source_account_count=source_account_count,
        selected_accounts=accounts,
        error=error,
        scan_selection=selection,
    )
