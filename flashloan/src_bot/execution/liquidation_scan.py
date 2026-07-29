from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional

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

BORROW_EVENT_TOPIC = Web3.keccak(text="Borrow(address,address,address,uint256,uint8,uint256,uint16)").hex()

AAVE_PROTOCOL_DATA_PROVIDER_ABI = [
    {
        "inputs": [
            {"internalType": "address", "name": "asset", "type": "address"},
            {"internalType": "address", "name": "user", "type": "address"},
        ],
        "name": "getUserReserveData",
        "outputs": [
            {"internalType": "uint256", "name": "currentATokenBalance", "type": "uint256"},
            {"internalType": "uint256", "name": "currentStableDebt", "type": "uint256"},
            {"internalType": "uint256", "name": "currentVariableDebt", "type": "uint256"},
            {"internalType": "uint256", "name": "principalStableDebt", "type": "uint256"},
            {"internalType": "uint256", "name": "scaledVariableDebt", "type": "uint256"},
            {"internalType": "uint256", "name": "stableBorrowRate", "type": "uint256"},
            {"internalType": "uint256", "name": "liquidityRate", "type": "uint256"},
            {"internalType": "uint40", "name": "stableRateLastUpdated", "type": "uint40"},
            {"internalType": "bool", "name": "usageAsCollateralEnabled", "type": "bool"},
        ],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [{"internalType": "address", "name": "asset", "type": "address"}],
        "name": "getReserveTokensAddresses",
        "outputs": [
            {"internalType": "address", "name": "aTokenAddress", "type": "address"},
            {"internalType": "address", "name": "stableDebtTokenAddress", "type": "address"},
            {"internalType": "address", "name": "variableDebtTokenAddress", "type": "address"},
        ],
        "stateMutability": "view",
        "type": "function",
    },
]

LIQUIDATION_DATA_PROVIDER_ABI = [
    {
        "inputs": [{"internalType": "address", "name": "user", "type": "address"}],
        "name": "getUserPositionFullInfo",
        "outputs": [
            {
                "components": [
                    {"internalType": "uint256", "name": "totalCollateralInBaseCurrency", "type": "uint256"},
                    {"internalType": "uint256", "name": "totalDebtInBaseCurrency", "type": "uint256"},
                    {"internalType": "uint256", "name": "availableBorrowsInBaseCurrency", "type": "uint256"},
                    {"internalType": "uint256", "name": "currentLiquidationThreshold", "type": "uint256"},
                    {"internalType": "uint256", "name": "ltv", "type": "uint256"},
                    {"internalType": "uint256", "name": "healthFactor", "type": "uint256"},
                ],
                "internalType": "struct UserPositionFullInfo",
                "name": "",
                "type": "tuple",
            }
        ],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [
            {"internalType": "address", "name": "user", "type": "address"},
            {"internalType": "address", "name": "collateralAsset", "type": "address"},
        ],
        "name": "getCollateralFullInfo",
        "outputs": [
            {
                "components": [
                    {"internalType": "uint256", "name": "assetUnit", "type": "uint256"},
                    {"internalType": "uint256", "name": "price", "type": "uint256"},
                    {"internalType": "address", "name": "aToken", "type": "address"},
                    {"internalType": "uint256", "name": "collateralBalance", "type": "uint256"},
                    {"internalType": "uint256", "name": "collateralBalanceInBaseCurrency", "type": "uint256"},
                ],
                "internalType": "struct CollateralFullInfo",
                "name": "",
                "type": "tuple",
            }
        ],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [
            {"internalType": "address", "name": "user", "type": "address"},
            {"internalType": "address", "name": "debtAsset", "type": "address"},
        ],
        "name": "getDebtFullInfo",
        "outputs": [
            {
                "components": [
                    {"internalType": "uint256", "name": "assetUnit", "type": "uint256"},
                    {"internalType": "uint256", "name": "price", "type": "uint256"},
                    {"internalType": "address", "name": "variableDebtToken", "type": "address"},
                    {"internalType": "uint256", "name": "debtBalance", "type": "uint256"},
                    {"internalType": "uint256", "name": "debtBalanceInBaseCurrency", "type": "uint256"},
                ],
                "internalType": "struct DebtFullInfo",
                "name": "",
                "type": "tuple",
            }
        ],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [
            {"internalType": "address", "name": "user", "type": "address"},
            {"internalType": "address", "name": "collateralAsset", "type": "address"},
            {"internalType": "address", "name": "debtAsset", "type": "address"},
        ],
        "name": "getLiquidationInfo",
        "outputs": [
            {
                "components": [
                    {
                        "components": [
                            {"internalType": "uint256", "name": "totalCollateralInBaseCurrency", "type": "uint256"},
                            {"internalType": "uint256", "name": "totalDebtInBaseCurrency", "type": "uint256"},
                            {"internalType": "uint256", "name": "availableBorrowsInBaseCurrency", "type": "uint256"},
                            {"internalType": "uint256", "name": "currentLiquidationThreshold", "type": "uint256"},
                            {"internalType": "uint256", "name": "ltv", "type": "uint256"},
                            {"internalType": "uint256", "name": "healthFactor", "type": "uint256"},
                        ],
                        "internalType": "struct UserPositionFullInfo",
                        "name": "userInfo",
                        "type": "tuple",
                    },
                    {
                        "components": [
                            {"internalType": "uint256", "name": "assetUnit", "type": "uint256"},
                            {"internalType": "uint256", "name": "price", "type": "uint256"},
                            {"internalType": "address", "name": "aToken", "type": "address"},
                            {"internalType": "uint256", "name": "collateralBalance", "type": "uint256"},
                            {"internalType": "uint256", "name": "collateralBalanceInBaseCurrency", "type": "uint256"},
                        ],
                        "internalType": "struct CollateralFullInfo",
                        "name": "collateralInfo",
                        "type": "tuple",
                    },
                    {
                        "components": [
                            {"internalType": "uint256", "name": "assetUnit", "type": "uint256"},
                            {"internalType": "uint256", "name": "price", "type": "uint256"},
                            {"internalType": "address", "name": "variableDebtToken", "type": "address"},
                            {"internalType": "uint256", "name": "debtBalance", "type": "uint256"},
                            {"internalType": "uint256", "name": "debtBalanceInBaseCurrency", "type": "uint256"},
                        ],
                        "internalType": "struct DebtFullInfo",
                        "name": "debtInfo",
                        "type": "tuple",
                    },
                    {"internalType": "uint256", "name": "maxCollateralToLiquidate", "type": "uint256"},
                    {"internalType": "uint256", "name": "maxDebtToLiquidate", "type": "uint256"},
                    {"internalType": "uint256", "name": "liquidationProtocolFee", "type": "uint256"},
                    {"internalType": "uint256", "name": "amountToPassToLiquidationCall", "type": "uint256"},
                ],
                "internalType": "struct LiquidationInfo",
                "name": "",
                "type": "tuple",
            }
        ],
        "stateMutability": "view",
        "type": "function",
    },
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
    watch_health_factor: float = 1.5
    close_factor: float = 0.5


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


def write_account_addresses(path: str | Path, addresses: Iterable[str]) -> list[str]:
    raw_path = Path(path)
    unique_addresses = []
    for item in addresses:
        try:
            checksum = Web3.to_checksum_address(str(item))
        except ValueError:
            continue
        if checksum not in unique_addresses:
            unique_addresses.append(checksum)
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_text("\n".join(unique_addresses) + ("\n" if unique_addresses else ""), encoding="utf-8")
    return unique_addresses


def topic_to_address(topic) -> str:
    raw = topic.hex() if hasattr(topic, "hex") else str(topic)
    raw = raw[2:] if raw.startswith("0x") else raw
    if len(raw) < 40:
        return ""
    return Web3.to_checksum_address("0x" + raw[-40:])


def discover_borrower_addresses(
    rpc_url: str,
    pool_address: str,
    from_block: int,
    to_block: Optional[int] = None,
    chunk_size: int = 50_000,
    limit: int = 5000,
) -> list[str]:
    w3 = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": 20}))
    latest_block = int(w3.eth.block_number)
    raw_start_block = int(from_block)
    start_block = max(0, latest_block + raw_start_block) if raw_start_block < 0 else max(0, raw_start_block)
    end_limit = latest_block if to_block is None else min(latest_block, int(to_block))
    chunk = max(1, min(int(chunk_size), 50_000))
    unique_addresses: list[str] = []
    current = start_block
    while current <= end_limit and len(unique_addresses) < max(1, int(limit)):
        end_block = min(end_limit, current + chunk - 1)
        logs = w3.eth.get_logs(
            {
                "address": Web3.to_checksum_address(pool_address),
                "fromBlock": current,
                "toBlock": end_block,
                "topics": [BORROW_EVENT_TOPIC],
            }
        )
        for log in logs:
            topics = log.get("topics") or []
            if len(topics) < 3:
                continue
            borrower = topic_to_address(topics[2])
            if borrower and borrower not in unique_addresses:
                unique_addresses.append(borrower)
                if len(unique_addresses) >= max(1, int(limit)):
                    break
        current = end_block + 1
    return unique_addresses


def classify_health_factor(health_factor: float, warning_threshold: float, liquidation_threshold: float) -> str:
    if health_factor < liquidation_threshold:
        return "liquidatable"
    if health_factor < warning_threshold:
        return "warning"
    return "healthy"


def health_factor_band(health_factor: float) -> str:
    value = float(health_factor)
    if value < 1.0:
        return "red"
    if value < 1.1:
        return "orange"
    if value < 1.2:
        return "yellow"
    if value < 1.3:
        return "beige"
    return "green"


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


def _safe_contract(w3: Web3, address: str, abi: list[dict]):
    return w3.eth.contract(address=Web3.to_checksum_address(address), abi=abi)


def _tuple_value(raw: Any, index: int, default: Any = None) -> Any:
    try:
        return raw[index]
    except Exception:
        return default


def _parse_position_info(raw: Any) -> dict:
    health_factor = _tuple_value(raw, 5)
    health_factor_float = float(health_factor) / 1e18 if isinstance(health_factor, int) and health_factor > 10**9 else float(health_factor or 0.0)
    return {
        "total_collateral_in_base_currency": int(_tuple_value(raw, 0) or 0),
        "total_debt_in_base_currency": int(_tuple_value(raw, 1) or 0),
        "available_borrows_in_base_currency": int(_tuple_value(raw, 2) or 0),
        "current_liquidation_threshold": int(_tuple_value(raw, 3) or 0),
        "ltv": int(_tuple_value(raw, 4) or 0),
        "health_factor": health_factor_float,
    }


def _parse_collateral_info(raw: Any) -> dict:
    return {
        "asset_unit": int(_tuple_value(raw, 0) or 0),
        "price": int(_tuple_value(raw, 1) or 0),
        "a_token": _tuple_value(raw, 2),
        "collateral_balance": int(_tuple_value(raw, 3) or 0),
        "collateral_balance_in_base_currency": int(_tuple_value(raw, 4) or 0),
    }


def _parse_debt_info(raw: Any) -> dict:
    return {
        "asset_unit": int(_tuple_value(raw, 0) or 0),
        "price": int(_tuple_value(raw, 1) or 0),
        "variable_debt_token": _tuple_value(raw, 2),
        "debt_balance": int(_tuple_value(raw, 3) or 0),
        "debt_balance_in_base_currency": int(_tuple_value(raw, 4) or 0),
    }


def _parse_liquidation_info(raw: Any) -> dict:
    user_info = _parse_position_info(_tuple_value(raw, 0, ()))
    collateral_info = _parse_collateral_info(_tuple_value(raw, 1, ()))
    debt_info = _parse_debt_info(_tuple_value(raw, 2, ()))
    return {
        "user_info": user_info,
        "collateral_info": collateral_info,
        "debt_info": debt_info,
        "max_collateral_to_liquidate": int(_tuple_value(raw, 3) or 0),
        "max_debt_to_liquidate": int(_tuple_value(raw, 4) or 0),
        "liquidation_protocol_fee": int(_tuple_value(raw, 5) or 0),
        "amount_to_pass_to_liquidation_call": int(_tuple_value(raw, 6) or 0),
    }


def load_reserve_assets_for_scan(rpc_url: str, pool_address: str, limit: int = 1000) -> list[dict]:
    from market.aave_reserve_cache import load_aave_reserve_assets

    assets = load_aave_reserve_assets(
        rpc_url,
        pool_address,
        limit=limit,
        exclude_stables=False,
    )
    return assets or []


def get_user_positions(
    rpc_url: str,
    pool_address: str,
    user: str,
    reserve_assets: list[dict],
    protocol_data_provider_address: str,
) -> list[dict]:
    if not protocol_data_provider_address:
        return []
    w3 = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": 20}))
    provider = _safe_contract(w3, protocol_data_provider_address, AAVE_PROTOCOL_DATA_PROVIDER_ABI)
    positions: list[dict] = []
    for asset in reserve_assets:
        token_address = str(asset.get("token_address") or "").strip()
        if not token_address:
            continue
        try:
            raw = provider.functions.getUserReserveData(
                Web3.to_checksum_address(token_address),
                Web3.to_checksum_address(user),
            ).call()
        except Exception:
            continue
        position = {
            "symbol": str(asset.get("binance_symbol") or asset.get("token_symbol") or "").upper(),
            "token_address": Web3.to_checksum_address(token_address),
            "token_symbol": str(asset.get("token_symbol") or "").upper(),
            "binance_symbol": str(asset.get("binance_symbol") or "").upper(),
            "current_a_token_balance": int(_tuple_value(raw, 0) or 0),
            "current_stable_debt": int(_tuple_value(raw, 1) or 0),
            "current_variable_debt": int(_tuple_value(raw, 2) or 0),
            "principal_stable_debt": int(_tuple_value(raw, 3) or 0),
            "scaled_variable_debt": int(_tuple_value(raw, 4) or 0),
            "stable_borrow_rate": int(_tuple_value(raw, 5) or 0),
            "liquidity_rate": int(_tuple_value(raw, 6) or 0),
            "stable_rate_last_updated": int(_tuple_value(raw, 7) or 0),
            "usage_as_collateral_enabled": bool(_tuple_value(raw, 8)),
        }
        if position["current_a_token_balance"] or position["current_stable_debt"] or position["current_variable_debt"]:
            position["health_factor_band"] = health_factor_band(float("inf"))
            positions.append(position)
    return positions


def build_liquidation_candidates(
    rpc_url: str,
    user: str,
    positions: list[dict],
    liquidation_data_provider_address: str,
    config: LiquidationScanConfig,
) -> list[dict]:
    if not liquidation_data_provider_address:
        return []
    active_collateral = [row for row in positions if row.get("usage_as_collateral_enabled") and int(row.get("current_a_token_balance") or 0) > 0]
    active_debts = [row for row in positions if int(row.get("current_stable_debt") or 0) > 0 or int(row.get("current_variable_debt") or 0) > 0]
    if not active_collateral or not active_debts:
        return []
    w3 = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": 20}))
    provider = _safe_contract(w3, liquidation_data_provider_address, LIQUIDATION_DATA_PROVIDER_ABI)
    candidates: list[dict] = []
    for collateral in active_collateral:
        for debt in active_debts:
            if collateral["token_address"].lower() == debt["token_address"].lower():
                continue
            try:
                raw = provider.functions.getLiquidationInfo(
                    Web3.to_checksum_address(user),
                    Web3.to_checksum_address(collateral["token_address"]),
                    Web3.to_checksum_address(debt["token_address"]),
                ).call()
            except Exception:
                continue
            info = _parse_liquidation_info(raw)
            user_info = info["user_info"]
            debt_base = float(info["debt_info"]["debt_balance_in_base_currency"] or 0)
            profit = estimate_liquidation_profit(
                debt_base or float(user_info["total_debt_in_base_currency"] or 0),
                config.liquidation_bonus_percent,
                config.flashloan_fee_percent,
                config.dex_slippage_percent,
                config.gas_cost_usd,
                repay_fraction=min(1.0, max(0.0, config.close_factor)),
            )
            candidates.append(
                {
                    "collateral_asset": collateral["token_address"],
                    "collateral_symbol": collateral["symbol"],
                    "debt_asset": debt["token_address"],
                    "debt_symbol": debt["symbol"],
                    "user_info": user_info,
                    "collateral_info": info["collateral_info"],
                    "debt_info": info["debt_info"],
                    "max_collateral_to_liquidate": info["max_collateral_to_liquidate"],
                    "max_debt_to_liquidate": info["max_debt_to_liquidate"],
                    "liquidation_protocol_fee": info["liquidation_protocol_fee"],
                    "amount_to_pass_to_liquidation_call": info["amount_to_pass_to_liquidation_call"],
                    "estimated_profit": profit,
                }
            )
    candidates.sort(
        key=lambda row: (
            float(row.get("estimated_profit", {}).get("net_profit_base", 0.0)),
            float(row.get("max_debt_to_liquidate", 0.0)),
        ),
        reverse=True,
    )
    return candidates


def build_liquidation_execution_plan(
    account: str,
    summary: dict[str, Any],
    recommended_candidate: Optional[dict],
    config: LiquidationScanConfig,
) -> dict:
    health_factor = float(summary.get("health_factor") or 0.0)
    liquidation_ready = health_factor < float(config.liquidation_health_factor)
    profitable = bool(
        recommended_candidate
        and float((recommended_candidate.get("estimated_profit") or {}).get("net_profit_base", 0.0)) > 0
    )
    execution_ready = liquidation_ready and profitable and recommended_candidate is not None
    reason: str
    if not recommended_candidate:
        reason = "no liquidation candidate available"
    elif not liquidation_ready:
        reason = f"health factor {health_factor:.3f} is above liquidation threshold"
    elif not profitable:
        reason = "recommended candidate is not profitable after fees"
    else:
        reason = "ready for execution preflight"
    return {
        "account": account,
        "execution_ready": execution_ready,
        "liquidation_ready": liquidation_ready,
        "profitable": profitable,
        "reason": reason,
        "health_factor": health_factor,
        "recommended_candidate": recommended_candidate,
        "repay_fraction": min(1.0, max(0.0, float(config.close_factor))),
        "profit_preview": (recommended_candidate or {}).get("estimated_profit") or {},
        "next_step": (
            "build liquidation tx calldata"
            if execution_ready
            else "continue watching account health"
        ),
    }


def build_user_liquidation_report(
    user: str,
    rpc_url: str,
    pool_address: str,
    reserve_assets: list[dict],
    protocol_data_provider_address: str,
    liquidation_data_provider_address: str,
    config: LiquidationScanConfig,
) -> dict:
    w3 = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": 20}))
    checksum_user = Web3.to_checksum_address(user)
    summary: dict[str, Any] = {}
    positions: list[dict] = []
    candidates: list[dict] = []
    if protocol_data_provider_address:
        try:
            provider = _safe_contract(w3, protocol_data_provider_address, LIQUIDATION_DATA_PROVIDER_ABI)
            raw_summary = provider.functions.getUserPositionFullInfo(checksum_user).call()
            summary = _parse_position_info(raw_summary)
        except Exception:
            summary = {}
        try:
            positions = get_user_positions(rpc_url, pool_address, checksum_user, reserve_assets, protocol_data_provider_address)
        except Exception:
            positions = []
    if not summary:
        summary = fetch_user_account_data(pool_address, checksum_user, rpc_url)
    if liquidation_data_provider_address and positions:
        candidates = build_liquidation_candidates(rpc_url, checksum_user, positions, liquidation_data_provider_address, config)
    liquidation_state = "healthy"
    health_factor = float(summary.get("health_factor") or 0.0)
    if health_factor < config.liquidation_health_factor:
        liquidation_state = "liquidatable"
    elif health_factor < config.warning_health_factor:
        liquidation_state = "warning"
    report = {
        "account": checksum_user,
        "summary": {
            **summary,
            "status": liquidation_state,
            "health_factor_band": health_factor_band(health_factor),
            "candidate_count": len(candidates),
            "positions_count": len(positions),
            "debt_positions_count": sum(1 for row in positions if int(row.get("current_stable_debt") or 0) or int(row.get("current_variable_debt") or 0)),
            "collateral_positions_count": sum(1 for row in positions if int(row.get("current_a_token_balance") or 0)),
        },
        "positions": [
            {
                "account": checksum_user,
                "symbol": row["symbol"],
                "health_factor": health_factor,
                "health_factor_band": health_factor_band(health_factor),
                "status": "liquidatable" if health_factor < 1.0 else ("warning" if health_factor < config.warning_health_factor else "healthy"),
                "collateral_balance": row["current_a_token_balance"],
                "stable_debt": row["current_stable_debt"],
                "variable_debt": row["current_variable_debt"],
                "usage_as_collateral_enabled": row["usage_as_collateral_enabled"],
            }
            for row in positions
        ],
        "liquidation_candidates": candidates,
        "recommended_candidate": candidates[0] if candidates else None,
    }
    report["execution_plan"] = build_liquidation_execution_plan(
        checksum_user,
        report["summary"],
        report["recommended_candidate"],
        config,
    )
    return report


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
                "health_factor_band": health_factor_band(account_data["health_factor"]),
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
