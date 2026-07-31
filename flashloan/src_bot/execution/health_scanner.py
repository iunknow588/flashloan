from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable, Iterable

from web3 import Web3

from execution.health_checker import classify_health_factor, estimate_liquidation_profit, health_factor_band


FetchSingleAccount = Callable[[str, str, str], dict[str, Any]]
FetchBatchAccounts = Callable[[str, Iterable[str], str, str, int], dict[str, dict]]


def _default_fetch_single() -> FetchSingleAccount:
    from execution import liquidation_scan as liquidation_scan_module

    return liquidation_scan_module.fetch_user_account_data


def _default_fetch_batch() -> FetchBatchAccounts:
    from execution import liquidation_scan as liquidation_scan_module

    return liquidation_scan_module.fetch_user_account_data_batch


def scan_account_health(
    accounts: Iterable[str],
    pool_address: str,
    rpc_url: str,
    config: Any | None = None,
    *,
    fetch_single: FetchSingleAccount | None = None,
    fetch_batch: FetchBatchAccounts | None = None,
) -> list[dict]:
    if config is None:
        from execution.liquidation_scan import LiquidationScanConfig

        config = LiquidationScanConfig()
    fetch_single = fetch_single or _default_fetch_single()
    fetch_batch = fetch_batch or _default_fetch_batch()

    unique_accounts: list[str] = []
    for account in accounts:
        try:
            checksum = Web3.to_checksum_address(str(account))
        except ValueError:
            continue
        if checksum not in unique_accounts:
            unique_accounts.append(checksum)

    scan_accounts = unique_accounts[: max(1, int(config.max_candidates))]
    batch_data: dict[str, dict] = {}
    if len(scan_accounts) > 1 and int(config.batch_size or 0) > 1 and str(config.multicall3_address or "").strip():
        try:
            batch_data = fetch_batch(
                pool_address,
                scan_accounts,
                rpc_url,
                config.multicall3_address,
                config.batch_size,
            )
        except Exception:
            batch_data = {}

    def scan_one(account: str) -> dict:
        try:
            account_data = batch_data.get(account) or fetch_single(pool_address, account, rpc_url)
        except Exception as exc:
            return {"account": account, "status": "error", "error": str(exc)}
        status = classify_health_factor(
            account_data["health_factor"],
            config.warning_health_factor,
            config.liquidation_health_factor,
        )
        liquidation = estimate_liquidation_profit(
            account_data["total_debt_base"],
            config.liquidation_bonus_percent,
            config.flashloan_fee_percent,
            config.dex_slippage_percent,
            config.gas_cost_usd,
            mev_buffer_usd=config.mev_buffer_usd,
            retry_buffer_usd=config.retry_buffer_usd,
        )
        return {
            **account_data,
            "status": status,
            "health_factor_band": health_factor_band(account_data["health_factor"]),
            "alert_score": max(0.0, config.warning_health_factor - account_data["health_factor"]),
            "liquidation_profit": liquidation,
        }

    workers = max(1, int(config.parallel_workers or 1))
    results: list[dict] = []
    if workers == 1 or len(scan_accounts) <= 1:
        results = [scan_one(account) for account in scan_accounts]
    else:
        with ThreadPoolExecutor(max_workers=min(workers, len(scan_accounts))) as executor:
            future_map = {executor.submit(scan_one, account): account for account in scan_accounts}
            for future in as_completed(future_map):
                try:
                    results.append(future.result())
                except Exception as exc:
                    results.append({"account": future_map[future], "status": "error", "error": str(exc)})
    results.sort(
        key=lambda row: (
            row.get("health_factor", 10.0),
            -float(row.get("liquidation_profit", {}).get("net_profit_base", 0.0)),
        ),
        reverse=False,
    )
    return results
