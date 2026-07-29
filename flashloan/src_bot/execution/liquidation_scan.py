from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

from web3 import Web3


POOL_ACCOUNT_DATA_ABI = [
    {
        "inputs": [{"internalType": "address", "name": "user", "type": "address"}],
        "name": "getUserAccountData",
        "outputs": [
            {"internalType": "uint256", "name": "totalCollateralBase", "type": "uint256"},
            {"internalType": "uint256", "name": "totalDebtBase", "type": "uint256"},
            {"internalType": "uint256", "name": "availableBorrowsBase", "type": "uint256"},
            {"internalType": "uint256", "name": "currentLiquidationThreshold", "type": "uint256"},
            {"internalType": "uint256", "name": "ltv", "type": "uint256"},
            {"internalType": "uint256", "name": "healthFactor", "type": "uint256"},
        ],
        "stateMutability": "view",
        "type": "function",
    }
]


@dataclass(frozen=True)
class LiquidationScanConfig:
    wide_scan_seconds: float = 1800.0
    near_scan_seconds: float = 0.2
    warning_health_factor: float = 1.05
    liquidation_health_factor: float = 1.0
    max_candidates: int = 5000
    liquidation_bonus_percent: float = 5.0
    flashloan_fee_percent: float = 0.05
    dex_slippage_percent: float = 0.10
    gas_cost_usd: float = 0.0


def load_account_addresses(path: str | Path) -> list[str]:
    raw_path = Path(path)
    if not raw_path.exists():
        return []
    if raw_path.suffix.lower() == ".json":
        data = json.loads(raw_path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            data = data.get("accounts") or data.get("addresses") or []
        items = data if isinstance(data, list) else []
    else:
        items = [line.strip() for line in raw_path.read_text(encoding="utf-8").splitlines()]
    addresses: list[str] = []
    for item in items:
        if not item:
            continue
        try:
            checksum = Web3.to_checksum_address(str(item))
        except ValueError:
            continue
        if checksum not in addresses:
            addresses.append(checksum)
    return addresses


def classify_health_factor(health_factor: float, warning_threshold: float, liquidation_threshold: float) -> str:
    if health_factor < liquidation_threshold:
        return "liquidatable"
    if health_factor < warning_threshold:
        return "warning"
    return "healthy"


def fetch_user_account_data(pool_address: str, account: str, rpc_url: str) -> dict:
    w3 = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": 12}))
    pool = w3.eth.contract(address=Web3.to_checksum_address(pool_address), abi=POOL_ACCOUNT_DATA_ABI)
    raw = pool.functions.getUserAccountData(Web3.to_checksum_address(account)).call()
    return {
        "account": Web3.to_checksum_address(account),
        "total_collateral_base": int(raw[0]),
        "total_debt_base": int(raw[1]),
        "available_borrows_base": int(raw[2]),
        "current_liquidation_threshold": int(raw[3]),
        "ltv": int(raw[4]),
        "health_factor": float(raw[5]) / 1e18 if int(raw[5]) > 10**9 else float(raw[5]),
    }


def estimate_liquidation_profit(
    total_debt_base: float,
    liquidation_bonus_percent: float,
    flashloan_fee_percent: float,
    dex_slippage_percent: float,
    gas_cost_usd: float,
    repay_fraction: float = 0.5,
) -> dict:
    repay_fraction = max(0.0, min(1.0, float(repay_fraction)))
    bonus_rate = max(0.0, float(liquidation_bonus_percent)) / 100.0
    flashloan_rate = max(0.0, float(flashloan_fee_percent)) / 100.0
    slippage_rate = max(0.0, float(dex_slippage_percent)) / 100.0
    repay_base = max(0.0, float(total_debt_base)) * repay_fraction
    seized_base = repay_base * (1 + bonus_rate)
    gross_profit_base = seized_base - repay_base
    fee_base = repay_base * (flashloan_rate + slippage_rate)
    net_profit_base = gross_profit_base - fee_base - max(0.0, float(gas_cost_usd))
    return {
        "repay_base": repay_base,
        "seized_base": seized_base,
        "gross_profit_base": gross_profit_base,
        "fee_base": fee_base,
        "gas_cost_usd": max(0.0, float(gas_cost_usd)),
        "net_profit_base": net_profit_base,
        "profitable": net_profit_base > 0,
    }


def scan_account_health(
    accounts: Iterable[str],
    pool_address: str,
    rpc_url: str,
    config: LiquidationScanConfig = LiquidationScanConfig(),
) -> list[dict]:
    results: list[dict] = []
    unique_accounts = []
    for account in accounts:
        try:
            checksum = Web3.to_checksum_address(str(account))
        except ValueError:
            continue
        if checksum not in unique_accounts:
            unique_accounts.append(checksum)
    for account in unique_accounts[: max(1, int(config.max_candidates))]:
        try:
            account_data = fetch_user_account_data(pool_address, account, rpc_url)
        except Exception as exc:
            results.append(
                {
                    "account": account,
                    "status": "error",
                    "error": str(exc),
                }
            )
            continue
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
        )
        results.append(
            {
                **account_data,
                "status": status,
                "alert_score": max(0.0, config.warning_health_factor - account_data["health_factor"]),
                "liquidation_profit": liquidation,
            }
        )
    results.sort(key=lambda row: (row.get("health_factor", 10.0), -float(row.get("liquidation_profit", {}).get("net_profit_base", 0.0))), reverse=False)
    return results


def split_candidate_accounts(accounts: Iterable[dict], warning_threshold: float, liquidation_threshold: float) -> dict:
    warning_accounts = []
    liquidation_accounts = []
    healthy_accounts = []
    for item in accounts:
        try:
            health_factor = float(item.get("health_factor"))
        except (TypeError, ValueError):
            continue
        if health_factor < liquidation_threshold:
            liquidation_accounts.append(item)
        elif health_factor < warning_threshold:
            warning_accounts.append(item)
        else:
            healthy_accounts.append(item)
    return {
        "warning_accounts": warning_accounts,
        "liquidation_accounts": liquidation_accounts,
        "healthy_accounts": healthy_accounts,
    }
