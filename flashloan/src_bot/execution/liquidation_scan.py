from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
import os
from typing import Any, Iterable

from web3 import Web3

from execution.liquidation_accounts import load_account_addresses, topic_to_address, write_account_addresses
from execution.liquidation_abis import (
    AAVE_PROTOCOL_DATA_PROVIDER_ABI,
    BORROW_EVENT_TOPIC,
    LIQUIDATION_DATA_PROVIDER_ABI,
    MULTICALL3_ABI,
    POOL_ACCOUNT_DATA_ABI,
)
from execution.liquidation_realtime_params import read_aave_flashloan_premium
from execution.health_checker import (
    classify_health_factor as _classify_health_factor,
    estimate_liquidation_profit as _estimate_liquidation_profit,
    health_factor_band as _health_factor_band,
)
from execution.prioritizer import (
    incremental_scan_account_groups as _incremental_scan_account_groups,
    split_candidate_accounts as _split_candidate_accounts,
    watched_health_rows as _watched_health_rows,
)
from execution.scanner import discover_borrower_addresses as _discover_borrower_addresses


AVALANCHE_MULTICALL3_ADDRESS = "0xcA11bde05977b3631167028862bE2a173976CA11"
ACCOUNT_DATA_OUTPUT_TYPES = ["uint256", "uint256", "uint256", "uint256", "uint256", "uint256"]


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
    mev_buffer_usd: float = 0.0
    retry_buffer_usd: float = 0.0
    watch_health_factor: float = 1.5
    close_factor: float = 0.5
    parallel_workers: int = 8
    batch_size: int = 100
    multicall3_address: str = ""


def discover_borrower_addresses(*args, **kwargs) -> list[str]:
    kwargs.setdefault("event_topic", BORROW_EVENT_TOPIC)
    kwargs.setdefault("web3_class", Web3)
    return _discover_borrower_addresses(*args, **kwargs)


def classify_health_factor(health_factor: float, warning_threshold: float, liquidation_threshold: float) -> str:
    return _classify_health_factor(health_factor, warning_threshold, liquidation_threshold)


def health_factor_band(health_factor: float) -> str:
    return _health_factor_band(health_factor)


def watched_health_rows(rows: Iterable[dict], max_health_factor: float = 1.5) -> list[dict]:
    return _watched_health_rows(rows, max_health_factor)


def incremental_scan_account_groups(
    accounts: Iterable[str],
    previous_rows: Iterable[dict],
    *,
    watch_health_factor: float = 1.5,
    full_scan_due: bool = False,
    max_accounts: int = 5000,
) -> dict[str, list[str]]:
    return _incremental_scan_account_groups(
        accounts,
        previous_rows,
        watch_health_factor=watch_health_factor,
        full_scan_due=full_scan_due,
        max_accounts=max_accounts,
    )


def _safe_contract(w3: Web3, address: str, abi: list[dict]):
    return w3.eth.contract(address=Web3.to_checksum_address(address), abi=abi)


def _tuple_value(raw: Any, index: int, default: Any = None) -> Any:
    try:
        return raw[index]
    except Exception:
        return default


def aave_base_currency_unit() -> int:
    raw = os.getenv("AAVE_BASE_CURRENCY_UNIT", "100000000").strip()
    try:
        unit = int(raw)
    except ValueError:
        unit = 100000000
    return max(1, unit)


def _base_currency_amount(raw_value: Any, base_unit: int | None = None) -> float:
    return float(int(raw_value or 0)) / float(base_unit or aave_base_currency_unit())


def _parse_position_info(raw: Any) -> dict:
    health_factor = _tuple_value(raw, 5)
    health_factor_float = float(health_factor) / 1e18 if isinstance(health_factor, int) and health_factor > 10**9 else float(health_factor or 0.0)
    base_unit = aave_base_currency_unit()
    total_collateral_raw = int(_tuple_value(raw, 0) or 0)
    total_debt_raw = int(_tuple_value(raw, 1) or 0)
    available_borrows_raw = int(_tuple_value(raw, 2) or 0)
    return {
        "total_collateral_in_base_currency": _base_currency_amount(total_collateral_raw, base_unit),
        "total_debt_in_base_currency": _base_currency_amount(total_debt_raw, base_unit),
        "available_borrows_in_base_currency": _base_currency_amount(available_borrows_raw, base_unit),
        "total_collateral_in_base_currency_raw": total_collateral_raw,
        "total_debt_in_base_currency_raw": total_debt_raw,
        "available_borrows_in_base_currency_raw": available_borrows_raw,
        "base_currency_unit": base_unit,
        "current_liquidation_threshold": int(_tuple_value(raw, 3) or 0),
        "ltv": int(_tuple_value(raw, 4) or 0),
        "health_factor": health_factor_float,
    }


def _parse_collateral_info(raw: Any) -> dict:
    base_unit = aave_base_currency_unit()
    collateral_balance_base_raw = int(_tuple_value(raw, 4) or 0)
    return {
        "asset_unit": int(_tuple_value(raw, 0) or 0),
        "price": int(_tuple_value(raw, 1) or 0),
        "a_token": _tuple_value(raw, 2),
        "collateral_balance": int(_tuple_value(raw, 3) or 0),
        "collateral_balance_in_base_currency": _base_currency_amount(collateral_balance_base_raw, base_unit),
        "collateral_balance_in_base_currency_raw": collateral_balance_base_raw,
        "base_currency_unit": base_unit,
    }


def _parse_debt_info(raw: Any) -> dict:
    base_unit = aave_base_currency_unit()
    debt_balance_base_raw = int(_tuple_value(raw, 4) or 0)
    return {
        "asset_unit": int(_tuple_value(raw, 0) or 0),
        "price": int(_tuple_value(raw, 1) or 0),
        "variable_debt_token": _tuple_value(raw, 2),
        "debt_balance": int(_tuple_value(raw, 3) or 0),
        "debt_balance_in_base_currency": _base_currency_amount(debt_balance_base_raw, base_unit),
        "debt_balance_in_base_currency_raw": debt_balance_base_raw,
        "base_currency_unit": base_unit,
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


def _debt_amount_to_base(debt_amount: int, debt_info: dict[str, Any]) -> float:
    debt_balance = int(debt_info.get("debt_balance") or 0)
    debt_balance_base_raw = int(debt_info.get("debt_balance_in_base_currency_raw") or 0)
    if debt_amount <= 0:
        return 0.0
    if debt_balance > 0 and debt_balance_base_raw > 0:
        base_unit = int(debt_info.get("base_currency_unit") or aave_base_currency_unit())
        debt_balance_base = float(debt_balance_base_raw)
        if debt_balance_base_raw > debt_balance * 1000:
            debt_balance_base = debt_balance_base / float(max(1, base_unit))
        return debt_balance_base * float(debt_amount) / float(debt_balance)
    asset_unit = int(debt_info.get("asset_unit") or 0)
    price = int(debt_info.get("price") or 0)
    if asset_unit > 0 and price > 0:
        return float(debt_amount) * float(price) / float(asset_unit) / float(aave_base_currency_unit())
    return 0.0


def _token_amount(raw_amount: int, decimals: int) -> float:
    try:
        return float(raw_amount) / float(10 ** int(decimals))
    except Exception:
        return 0.0


def liquidation_repay_base_and_source(info: dict[str, Any], config: LiquidationScanConfig) -> tuple[float, str, float]:
    debt_info = info.get("debt_info") or {}
    amount_to_pass = int(info.get("amount_to_pass_to_liquidation_call") or 0)
    max_debt = int(info.get("max_debt_to_liquidate") or 0)
    debt_balance_base = float(debt_info.get("debt_balance_in_base_currency") or 0)
    user_debt_base = float((info.get("user_info") or {}).get("total_debt_in_base_currency") or 0)
    total_debt_base = debt_balance_base or user_debt_base
    fallback_fraction = min(1.0, max(0.0, float(config.close_factor)))

    if amount_to_pass > 0:
        repay_base = _debt_amount_to_base(amount_to_pass, debt_info)
        if repay_base <= 0 and max_debt > 0:
            max_debt_base = _debt_amount_to_base(max_debt, debt_info)
            repay_base = max_debt_base * min(1.0, float(amount_to_pass) / float(max_debt))
        return repay_base, "amount_to_pass_to_liquidation_call", 1.0

    if max_debt > 0:
        repay_base = _debt_amount_to_base(max_debt, debt_info)
        if repay_base > 0:
            return repay_base, "max_debt_to_liquidate", 1.0

    return total_debt_base, "close_factor_fallback", fallback_fraction


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
        decimals = int(asset.get("decimals") or 18)
        current_a_token_balance = int(_tuple_value(raw, 0) or 0)
        current_stable_debt = int(_tuple_value(raw, 1) or 0)
        current_variable_debt = int(_tuple_value(raw, 2) or 0)
        oracle_price = float(asset.get("oracle_price") or 0.0)
        collateral_amount = _token_amount(current_a_token_balance, decimals)
        stable_debt_amount = _token_amount(current_stable_debt, decimals)
        variable_debt_amount = _token_amount(current_variable_debt, decimals)
        total_debt_amount = stable_debt_amount + variable_debt_amount
        position = {
            "symbol": str(asset.get("binance_symbol") or asset.get("token_symbol") or "").upper(),
            "token_address": Web3.to_checksum_address(token_address),
            "token_symbol": str(asset.get("token_symbol") or "").upper(),
            "binance_symbol": str(asset.get("binance_symbol") or "").upper(),
            "decimals": decimals,
            "oracle_price": oracle_price,
            "current_token_price": oracle_price,
            "current_a_token_balance": current_a_token_balance,
            "current_stable_debt": current_stable_debt,
            "current_variable_debt": current_variable_debt,
            "collateral_amount": collateral_amount,
            "stable_debt_amount": stable_debt_amount,
            "variable_debt_amount": variable_debt_amount,
            "total_debt_amount": total_debt_amount,
            "collateral_value_base": collateral_amount * oracle_price,
            "debt_value_base": total_debt_amount * oracle_price,
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
    realtime_params: dict[str, Any] | None = None,
) -> list[dict]:
    if not liquidation_data_provider_address:
        return []
    active_collateral = [row for row in positions if row.get("usage_as_collateral_enabled") and int(row.get("current_a_token_balance") or 0) > 0]
    active_debts = [row for row in positions if int(row.get("current_stable_debt") or 0) > 0 or int(row.get("current_variable_debt") or 0) > 0]
    if not active_collateral or not active_debts:
        return []
    w3 = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": 20}))
    provider = _safe_contract(w3, liquidation_data_provider_address, LIQUIDATION_DATA_PROVIDER_ABI)
    flashloan_premium = (realtime_params or {}).get("flashloan_premium") or {}
    flashloan_fee_percent = float(flashloan_premium.get("premium_percent") or config.flashloan_fee_percent)
    flashloan_premium_source = str(flashloan_premium.get("source") or "fallback_config")
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
            repay_base, repay_base_source, repay_fraction = liquidation_repay_base_and_source(info, config)
            profit = estimate_liquidation_profit(
                repay_base,
                config.liquidation_bonus_percent,
                flashloan_fee_percent,
                config.dex_slippage_percent,
                config.gas_cost_usd,
                repay_fraction=repay_fraction,
                mev_buffer_usd=config.mev_buffer_usd,
                retry_buffer_usd=config.retry_buffer_usd,
                flashloan_premium_source=flashloan_premium_source,
            )
            profit["repay_base_source"] = repay_base_source
            profit["flashloan_premium_bps"] = flashloan_premium.get("premium_bps")
            profit["flashloan_premium_block_number"] = flashloan_premium.get("block_number")
            profit["flashloan_premium_read_at"] = flashloan_premium.get("read_at")
            collateral_decimals = int(collateral.get("decimals") or 18)
            debt_decimals = int(debt.get("decimals") or 18)
            max_collateral = int(info["max_collateral_to_liquidate"])
            max_debt = int(info["max_debt_to_liquidate"])
            amount_to_pass = int(info["amount_to_pass_to_liquidation_call"])
            candidates.append(
                {
                    "collateral_asset": collateral["token_address"],
                    "collateral_symbol": collateral["symbol"],
                    "collateral_token_symbol": collateral.get("token_symbol") or collateral["symbol"],
                    "collateral_decimals": collateral_decimals,
                    "collateral_price": float(collateral.get("oracle_price") or 0.0),
                    "collateral_amount": float(collateral.get("collateral_amount") or 0.0),
                    "collateral_value_base": float(collateral.get("collateral_value_base") or 0.0),
                    "debt_asset": debt["token_address"],
                    "debt_symbol": debt["symbol"],
                    "debt_token_symbol": debt.get("token_symbol") or debt["symbol"],
                    "debt_decimals": debt_decimals,
                    "debt_price": float(debt.get("oracle_price") or 0.0),
                    "debt_amount": float(debt.get("total_debt_amount") or 0.0),
                    "debt_value_base": float(debt.get("debt_value_base") or 0.0),
                    "user_info": user_info,
                    "collateral_info": info["collateral_info"],
                    "debt_info": info["debt_info"],
                    "max_collateral_to_liquidate": max_collateral,
                    "max_collateral_to_liquidate_amount": _token_amount(max_collateral, collateral_decimals),
                    "max_debt_to_liquidate": max_debt,
                    "max_debt_to_liquidate_amount": _token_amount(max_debt, debt_decimals),
                    "liquidation_protocol_fee": info["liquidation_protocol_fee"],
                    "amount_to_pass_to_liquidation_call": amount_to_pass,
                    "amount_to_pass_to_liquidation_call_amount": _token_amount(amount_to_pass, debt_decimals),
                    "repay_base_source": repay_base_source,
                    "estimated_profit": profit,
                    "parameter_sources": {
                        "amount_to_pass_source": repay_base_source,
                        "close_factor_source": "fallback_config" if repay_base_source == "close_factor_fallback" else "liquidation_data_provider",
                        "liquidation_bonus_source": "fallback_config",
                        "protocol_fee_source": "liquidation_data_provider",
                        "flashloan_premium_source": flashloan_premium_source,
                        "flashloan_premium_block_number": flashloan_premium.get("block_number"),
                    },
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
    realtime_params = {
        "flashloan_premium": read_aave_flashloan_premium(
            rpc_url,
            pool_address,
            fallback_percent=config.flashloan_fee_percent,
        )
    }
    if liquidation_data_provider_address and positions:
        candidates = build_liquidation_candidates(
            rpc_url,
            checksum_user,
            positions,
            liquidation_data_provider_address,
            config,
            realtime_params=realtime_params,
        )
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
                "token_symbol": row.get("token_symbol"),
                "token_address": row.get("token_address"),
                "decimals": row.get("decimals"),
                "oracle_price": row.get("oracle_price"),
                "current_token_price": row.get("current_token_price"),
                "health_factor": health_factor,
                "health_factor_band": health_factor_band(health_factor),
                "status": "liquidatable" if health_factor < 1.0 else ("warning" if health_factor < config.warning_health_factor else "healthy"),
                "collateral_balance": row["current_a_token_balance"],
                "collateral_amount": row.get("collateral_amount"),
                "collateral_value_base": row.get("collateral_value_base"),
                "stable_debt": row["current_stable_debt"],
                "stable_debt_amount": row.get("stable_debt_amount"),
                "variable_debt": row["current_variable_debt"],
                "variable_debt_amount": row.get("variable_debt_amount"),
                "total_debt_amount": row.get("total_debt_amount"),
                "debt_value_base": row.get("debt_value_base"),
                "usage_as_collateral_enabled": row["usage_as_collateral_enabled"],
            }
            for row in positions
        ],
        "liquidation_candidates": candidates,
        "recommended_candidate": candidates[0] if candidates else None,
        "realtime_params": realtime_params,
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
    base_unit = aave_base_currency_unit()
    total_collateral_raw = int(raw[0])
    total_debt_raw = int(raw[1])
    available_borrows_raw = int(raw[2])
    return {
        "account": Web3.to_checksum_address(account),
        "total_collateral_base": _base_currency_amount(total_collateral_raw, base_unit),
        "total_debt_base": _base_currency_amount(total_debt_raw, base_unit),
        "available_borrows_base": _base_currency_amount(available_borrows_raw, base_unit),
        "total_collateral_base_raw": total_collateral_raw,
        "total_debt_base_raw": total_debt_raw,
        "available_borrows_base_raw": available_borrows_raw,
        "base_currency_unit": base_unit,
        "current_liquidation_threshold": int(raw[3]),
        "ltv": int(raw[4]),
        "health_factor": float(raw[5]) / 1e18 if int(raw[5]) > 10**9 else float(raw[5]),
    }


def _encode_user_account_data_call(pool: Any, account: str) -> str:
    function = pool.functions.getUserAccountData(Web3.to_checksum_address(account))
    if hasattr(function, "_encode_transaction_data"):
        return function._encode_transaction_data()
    return pool.encodeABI(fn_name="getUserAccountData", args=[Web3.to_checksum_address(account)])


def fetch_user_account_data_batch(
    pool_address: str,
    accounts: Iterable[str],
    rpc_url: str,
    multicall3_address: str = AVALANCHE_MULTICALL3_ADDRESS,
    batch_size: int = 100,
) -> dict[str, dict]:
    checksum_accounts = [Web3.to_checksum_address(str(account)) for account in accounts]
    if not checksum_accounts:
        return {}
    if not str(multicall3_address or "").strip():
        raise ValueError("missing Multicall3 address")

    w3 = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": 20}))
    checksum_pool = Web3.to_checksum_address(pool_address)
    pool = w3.eth.contract(address=checksum_pool, abi=POOL_ACCOUNT_DATA_ABI)
    multicall = w3.eth.contract(address=Web3.to_checksum_address(multicall3_address), abi=MULTICALL3_ABI)
    size = max(1, int(batch_size or 100))
    results: dict[str, dict] = {}
    for offset in range(0, len(checksum_accounts), size):
        chunk = checksum_accounts[offset : offset + size]
        calls = [(checksum_pool, True, _encode_user_account_data_call(pool, account)) for account in chunk]
        raw_results = multicall.functions.aggregate3(calls).call()
        for account, raw_result in zip(chunk, raw_results):
            success = bool(_tuple_value(raw_result, 0))
            return_data = _tuple_value(raw_result, 1, b"")
            if not success or not return_data:
                continue
            decoded = w3.codec.decode(ACCOUNT_DATA_OUTPUT_TYPES, return_data)
            account_data = _parse_position_info(decoded)
            results[account] = {
                "account": account,
                "total_collateral_base": account_data["total_collateral_in_base_currency"],
                "total_debt_base": account_data["total_debt_in_base_currency"],
                "available_borrows_base": account_data["available_borrows_in_base_currency"],
                "total_collateral_base_raw": account_data["total_collateral_in_base_currency_raw"],
                "total_debt_base_raw": account_data["total_debt_in_base_currency_raw"],
                "available_borrows_base_raw": account_data["available_borrows_in_base_currency_raw"],
                "base_currency_unit": account_data["base_currency_unit"],
                "current_liquidation_threshold": account_data["current_liquidation_threshold"],
                "ltv": account_data["ltv"],
                "health_factor": account_data["health_factor"],
                "account_data_source": "multicall3",
            }
    return results


def estimate_liquidation_profit(
    total_debt_base: float,
    liquidation_bonus_percent: float,
    flashloan_fee_percent: float,
    dex_slippage_percent: float,
    gas_cost_usd: float,
    repay_fraction: float = 0.5,
    mev_buffer_usd: float = 0.0,
    retry_buffer_usd: float = 0.0,
    flashloan_premium_source: str = "fallback_config",
) -> dict:
    return _estimate_liquidation_profit(
        total_debt_base=total_debt_base,
        liquidation_bonus_percent=liquidation_bonus_percent,
        flashloan_fee_percent=flashloan_fee_percent,
        dex_slippage_percent=dex_slippage_percent,
        gas_cost_usd=gas_cost_usd,
        repay_fraction=repay_fraction,
        mev_buffer_usd=mev_buffer_usd,
        retry_buffer_usd=retry_buffer_usd,
        flashloan_premium_source=flashloan_premium_source,
    )


def scan_account_health(
    accounts: Iterable[str],
    pool_address: str,
    rpc_url: str,
    config: LiquidationScanConfig = LiquidationScanConfig(),
) -> list[dict]:
    unique_accounts = []
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
            batch_data = fetch_user_account_data_batch(
                pool_address,
                scan_accounts,
                rpc_url,
                multicall3_address=config.multicall3_address,
                batch_size=config.batch_size,
            )
        except Exception:
            batch_data = {}

    def scan_one(account: str) -> dict:
        try:
            account_data = batch_data.get(account) or fetch_user_account_data(pool_address, account, rpc_url)
        except Exception as exc:
            return {
                "account": account,
                "status": "error",
                "error": str(exc),
            }
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
    results.sort(key=lambda row: (row.get("health_factor", 10.0), -float(row.get("liquidation_profit", {}).get("net_profit_base", 0.0))), reverse=False)
    return results


def split_candidate_accounts(accounts: Iterable[dict], warning_threshold: float, liquidation_threshold: float) -> dict:
    return _split_candidate_accounts(accounts, warning_threshold, liquidation_threshold)
