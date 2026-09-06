from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Iterable

from web3 import Web3

from bot.execution.health_checker import estimate_liquidation_profit, health_factor_band
from bot.execution.venus_abis import COMPTROLLER_ABI, VTOKEN_ABI
from bot.execution.venus_markets import MANTISSA


@dataclass(frozen=True)
class VenusScanConfig:
    warning_liquidity_raw: int = 10**20
    close_factor_mantissa: int = 500_000_000_000_000_000
    min_repay_raw: int = 1
    min_seize_raw: int = 1
    max_candidates: int = 100
    flashloan_fee_percent: float = 0.05
    dex_swap_fee_percent: float = 0.25
    dex_slippage_percent: float = 0.10
    gas_cost_usd: float = 0.0
    mev_buffer_usd: float = 0.0
    retry_buffer_usd: float = 0.0
    min_operator_net_profit_usd: float = 0.0
    flashloan_supported_debt_assets: tuple[str, ...] = ()


def _contract(w3: Web3, address: str, abi: list[dict]):
    return w3.eth.contract(address=Web3.to_checksum_address(address), abi=abi)


def _call(function: Any, default: Any = None) -> Any:
    try:
        return function.call()
    except Exception:
        return default


def _contract_call(contract: Any, name: str, *args: Any, default: Any = None) -> Any:
    try:
        fn = getattr(contract.functions, name)(*args)
    except Exception:
        return default
    return _call(fn, default=default)


def parse_account_liquidity(raw: Any) -> dict[str, int]:
    return {
        "error": int(raw[0] if isinstance(raw, (list, tuple)) and len(raw) > 0 else 0),
        "liquidity": int(raw[1] if isinstance(raw, (list, tuple)) and len(raw) > 1 else 0),
        "shortfall": int(raw[2] if isinstance(raw, (list, tuple)) and len(raw) > 2 else 0),
    }


def classify_venus_risk(liquidity: int, shortfall: int, *, warning_liquidity_raw: int = 10**20) -> str:
    if int(shortfall or 0) > 0:
        return "liquidatable"
    if int(liquidity or 0) <= int(warning_liquidity_raw):
        return "warning"
    return "healthy"


def venus_health_factor(
    liquidity_raw: Any,
    shortfall_raw: Any,
    total_debt_value_usd: Any,
) -> float | None:
    """Estimate Venus account health from Comptroller liquidity and current debt value."""
    try:
        debt_value = float(total_debt_value_usd or 0.0)
        liquidity_value = float(int(liquidity_raw or 0)) / float(MANTISSA)
        shortfall_value = float(int(shortfall_raw or 0)) / float(MANTISSA)
    except (TypeError, ValueError):
        return None
    if shortfall_value > 0:
        if debt_value > 0:
            return max(0.0, (debt_value - shortfall_value) / debt_value)
        return 0.0
    if debt_value <= 0:
        return None
    return 1.0 + max(0.0, liquidity_value) / debt_value


def close_factor_repay_amount(borrow_balance_raw: int, close_factor_mantissa: int) -> int:
    borrow = max(0, int(borrow_balance_raw or 0))
    close_factor = max(0, min(MANTISSA, int(close_factor_mantissa or 0)))
    return int(borrow * close_factor // MANTISSA)


def _market_by_vtoken(markets: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    by_address = {}
    for row in markets:
        address = str(row.get("vtoken_address") or row.get("address") or "").strip()
        if not address:
            continue
        try:
            by_address[Web3.to_checksum_address(address)] = dict(row)
        except Exception:
            continue
    return by_address


def _human_amount(raw_amount: int, decimals: int) -> float:
    try:
        return float(raw_amount or 0) / float(10 ** max(0, int(decimals)))
    except Exception:
        return 0.0


def _asset_filter_keys(values: Iterable[Any] | None) -> set[str]:
    keys: set[str] = set()
    for value in values or []:
        text = str(value or "").strip()
        if not text:
            continue
        keys.add(text.upper())
        try:
            keys.add(Web3.to_checksum_address(text).lower())
        except Exception:
            continue
    return keys


def _position_asset_keys(position: dict[str, Any]) -> set[str]:
    keys: set[str] = set()
    for name in ("underlying_symbol", "vtoken_symbol", "symbol"):
        value = str(position.get(name) or "").strip()
        if value:
            keys.add(value.upper())
    for name in ("wrapped_underlying_address", "underlying_address", "vtoken_address"):
        value = str(position.get(name) or "").strip()
        if not value:
            continue
        try:
            keys.add(Web3.to_checksum_address(value).lower())
        except Exception:
            continue
    return keys


def _underlying_raw_from_vtoken_balance(supply_raw: int, exchange_rate_stored: int) -> int:
    if int(supply_raw or 0) <= 0 or int(exchange_rate_stored or 0) <= 0:
        return 0
    return int(int(supply_raw) * int(exchange_rate_stored) // MANTISSA)


def scan_venus_account_risk(
    w3: Web3,
    comptroller_address: str,
    account: str,
    markets: Iterable[dict[str, Any]],
    *,
    warning_liquidity_raw: int = 10**20,
) -> dict[str, Any]:
    started = time.perf_counter()
    checksum_account = Web3.to_checksum_address(account)
    comptroller = _contract(w3, comptroller_address, COMPTROLLER_ABI)
    liquidity = parse_account_liquidity(_contract_call(comptroller, "getAccountLiquidity", checksum_account, default=(1, 0, 0)))
    entered_assets = _contract_call(comptroller, "getAssetsIn", checksum_account, default=[]) or []
    entered = {Web3.to_checksum_address(address) for address in entered_assets}
    positions: list[dict[str, Any]] = []
    for market in markets:
        vtoken_address = str(market.get("vtoken_address") or "").strip()
        if not vtoken_address:
            continue
        checksum_vtoken = Web3.to_checksum_address(vtoken_address)
        vtoken = _contract(w3, checksum_vtoken, VTOKEN_ABI)
        borrow_raw = int(_contract_call(vtoken, "borrowBalanceStored", checksum_account, default=0) or 0)
        supply_raw = int(_contract_call(vtoken, "balanceOf", checksum_account, default=0) or 0)
        if borrow_raw <= 0 and supply_raw <= 0:
            continue
        market_row = dict(market)
        underlying_decimals = int(market_row.get("underlying_decimals") or 18)
        underlying_raw = _underlying_raw_from_vtoken_balance(
            supply_raw,
            int(market_row.get("exchange_rate_stored") or 0),
        )
        oracle_price_usd = float(market_row.get("oracle_price_usd") or 0.0)
        positions.append(
            {
                **market_row,
                "vtoken_address": checksum_vtoken,
                "account": checksum_account,
                "borrow_balance_raw": borrow_raw,
                "supply_balance_raw": supply_raw,
                "borrow_amount": _human_amount(borrow_raw, underlying_decimals),
                "supply_underlying_raw": underlying_raw,
                "supply_underlying_amount": _human_amount(underlying_raw, underlying_decimals),
                "collateral_value_usd": _human_amount(underlying_raw, underlying_decimals) * oracle_price_usd,
                "debt_value_usd": _human_amount(borrow_raw, underlying_decimals) * oracle_price_usd,
                "entered_market": checksum_vtoken in entered,
            }
        )
    status = classify_venus_risk(
        liquidity["liquidity"],
        liquidity["shortfall"],
        warning_liquidity_raw=warning_liquidity_raw,
    )
    total_collateral_value_usd = sum(
        float(row.get("collateral_value_usd") or 0.0) for row in positions if row.get("entered_market")
    )
    total_debt_value_usd = sum(float(row.get("debt_value_usd") or 0.0) for row in positions)
    health_factor = venus_health_factor(
        liquidity["liquidity"],
        liquidity["shortfall"],
        total_debt_value_usd,
    )
    return {
        "account": checksum_account,
        "network": "bnb",
        "chain_id": 56,
        "protocol": "venus_core",
        "block_number": int(getattr(w3.eth, "block_number", 0) or 0),
        "scan_latency_ms": round((time.perf_counter() - started) * 1000, 3),
        "liquidity_error": liquidity["error"],
        "liquidity_raw": liquidity["liquidity"],
        "shortfall_raw": liquidity["shortfall"],
        "status": status,
        "entered_markets": sorted(entered),
        "positions": positions,
        "total_collateral_value_usd": total_collateral_value_usd,
        "total_debt_value_usd": total_debt_value_usd,
        "health_factor": health_factor,
        "health_factor_band": health_factor_band(health_factor) if health_factor is not None else None,
        "health_factor_source": "venus_account_liquidity",
        "debt_positions_count": sum(1 for row in positions if int(row.get("borrow_balance_raw") or 0) > 0),
        "collateral_positions_count": sum(1 for row in positions if int(row.get("supply_balance_raw") or 0) > 0 and row.get("entered_market")),
        "error": None if liquidity["error"] == 0 else f"getAccountLiquidity error={liquidity['error']}",
    }


def _seize_tokens(
    comptroller: Any | None,
    debt_vtoken: str,
    collateral_vtoken: str,
    repay_raw: int,
    fallback: int = 0,
) -> tuple[int, str, str | None]:
    if comptroller is None:
        return int(fallback or 0), "fallback", None
    raw = _contract_call(
        comptroller,
        "liquidateCalculateSeizeTokens",
        Web3.to_checksum_address(debt_vtoken),
        Web3.to_checksum_address(collateral_vtoken),
        int(repay_raw),
        default=None,
    )
    if not isinstance(raw, (list, tuple)) or len(raw) < 2:
        return int(fallback or 0), "fallback", "seize_quote_failed"
    error = int(raw[0] or 0)
    if error != 0:
        return 0, "comptroller", f"seize_quote_error_{error}"
    return int(raw[1] or 0), "comptroller", None


def build_venus_liquidation_candidates(
    account_risk: dict[str, Any],
    *,
    comptroller: Any | None = None,
    close_factor_mantissa: int | None = None,
    min_repay_raw: int = 1,
    min_seize_raw: int = 1,
    max_candidates: int = 100,
    flashloan_fee_percent: float = 0.05,
    dex_swap_fee_percent: float = 0.25,
    dex_slippage_percent: float = 0.10,
    gas_cost_usd: float = 0.0,
    mev_buffer_usd: float = 0.0,
    retry_buffer_usd: float = 0.0,
    min_operator_net_profit_usd: float = 0.0,
    flashloan_supported_debt_assets: Iterable[str] | None = None,
) -> list[dict[str, Any]]:
    if str(account_risk.get("status") or "") != "liquidatable":
        return []
    positions = list(account_risk.get("positions") or [])
    debts = [row for row in positions if int(row.get("borrow_balance_raw") or 0) > 0]
    collaterals = [
        row
        for row in positions
        if int(row.get("supply_balance_raw") or 0) > 0 and bool(row.get("entered_market")) and not bool(row.get("collateral_disabled"))
    ]
    supported_debt_keys = _asset_filter_keys(flashloan_supported_debt_assets)
    candidates: list[dict[str, Any]] = []
    for debt in debts:
        debt_flashloan_supported = not supported_debt_keys or bool(_position_asset_keys(debt) & supported_debt_keys)
        factor = int(close_factor_mantissa or debt.get("close_factor_mantissa") or VenusScanConfig.close_factor_mantissa)
        repay_raw = close_factor_repay_amount(int(debt.get("borrow_balance_raw") or 0), factor)
        if repay_raw < int(min_repay_raw):
            candidates.append(
                {
                    "account": account_risk.get("account"),
                    "debt_vtoken": debt.get("vtoken_address"),
                    "status": "rejected",
                    "rejected_reason": "repay_dust",
                    "repay_amount_raw": repay_raw,
                }
            )
            continue
        for collateral in collaterals:
            seize_raw, seize_source, seize_error = _seize_tokens(
                comptroller,
                str(debt.get("vtoken_address")),
                str(collateral.get("vtoken_address")),
                repay_raw,
                fallback=int(collateral.get("expected_seize_tokens_raw") or 0),
            )
            if seize_error:
                rejected_reason = seize_error
            elif seize_raw < int(min_seize_raw):
                rejected_reason = "collateral_dust"
            else:
                rejected_reason = ""
            theoretical_reward_raw = max(0, seize_raw - repay_raw)
            collateral_underlying_raw = int(
                collateral.get("supply_underlying_raw")
                or _underlying_raw_from_vtoken_balance(
                    int(collateral.get("supply_balance_raw") or 0),
                    int(collateral.get("exchange_rate_stored") or 0),
                )
            )
            collateral_decimals = int(collateral.get("underlying_decimals") or 18)
            collateral_underlying_amount = float(
                collateral.get("supply_underlying_amount")
                if collateral.get("supply_underlying_amount") is not None
                else _human_amount(collateral_underlying_raw, collateral_decimals)
            )
            liquidation_incentive = float(collateral.get("liquidation_incentive") or 1.1)
            protocol_share = float(collateral.get("protocol_share_mantissa") or 0) / 1e18
            debt_price_usd = float(debt.get("oracle_price_usd") or 0.0)
            debt_decimals = int(debt.get("underlying_decimals") or 18)
            repay_value_usd = _human_amount(repay_raw, debt_decimals) * debt_price_usd
            bonus_percent = max(0.0, (liquidation_incentive - 1.0) * 100.0)
            adjusted_bonus_percent = bonus_percent * max(0.0, 1.0 - protocol_share)
            estimated_profit = estimate_liquidation_profit(
                total_debt_base=repay_value_usd,
                liquidation_bonus_percent=adjusted_bonus_percent,
                flashloan_fee_percent=flashloan_fee_percent,
                dex_slippage_percent=float(dex_slippage_percent) + float(dex_swap_fee_percent),
                gas_cost_usd=gas_cost_usd,
                repay_fraction=1.0,
                mev_buffer_usd=mev_buffer_usd,
                retry_buffer_usd=retry_buffer_usd,
                flashloan_premium_source="venus_core_estimate",
            )
            estimated_profit["dex_swap_fee_percent"] = float(dex_swap_fee_percent)
            estimated_profit["dex_slippage_percent"] = float(dex_slippage_percent)
            repay_base = float(estimated_profit.get("repay_base") or 0.0)
            estimated_profit["profit_bps"] = (
                float(estimated_profit.get("net_profit_base") or 0.0) / repay_base * 10_000
                if repay_base > 0
                else 0.0
            )
            final_rejected_reason = rejected_reason
            if not final_rejected_reason and not debt_flashloan_supported:
                final_rejected_reason = "debt_asset_not_flashloan_supported"
            if not final_rejected_reason and float(estimated_profit.get("net_profit_base") or 0.0) < float(min_operator_net_profit_usd):
                final_rejected_reason = "net_profit_below_minimum"
            candidates.append(
                {
                    "account": account_risk.get("account"),
                    "network": "bnb",
                    "chain_id": 56,
                    "protocol": "venus_core",
                    "status": "rejected" if final_rejected_reason else "candidate",
                    "rejected_reason": final_rejected_reason or None,
                    "debt_vtoken": debt.get("vtoken_address"),
                    "debt_symbol": debt.get("underlying_symbol") or debt.get("vtoken_symbol"),
                    "debt_asset": debt.get("wrapped_underlying_address") or debt.get("underlying_address"),
                    "debt_flashloan_supported": bool(debt_flashloan_supported),
                    "debt_decimals": debt.get("underlying_decimals", 18),
                    "collateral_vtoken": collateral.get("vtoken_address"),
                    "collateral_symbol": collateral.get("underlying_symbol") or collateral.get("vtoken_symbol"),
                    "collateral_asset": collateral.get("wrapped_underlying_address") or collateral.get("underlying_address"),
                    "collateral_decimals": collateral_decimals,
                    "collateral_underlying_raw": collateral_underlying_raw,
                    "collateral_underlying_amount": collateral_underlying_amount,
                    "collateral_value_usd": float(collateral.get("collateral_value_usd") or 0.0),
                    "borrow_balance_raw": int(debt.get("borrow_balance_raw") or 0),
                    "supply_balance_raw": int(collateral.get("supply_balance_raw") or 0),
                    "debt_amount": float(debt.get("borrow_amount") or 0.0),
                    "debt_value_usd": float(debt.get("debt_value_usd") or 0.0),
                    "repay_value_usd": repay_value_usd,
                    "close_factor_mantissa": factor,
                    "repay_amount_raw": repay_raw,
                    "seize_tokens_raw": seize_raw,
                    "seize_source": seize_source,
                    "theoretical_reward_raw": theoretical_reward_raw,
                    "liquidation_incentive_mantissa": collateral.get("liquidation_incentive_mantissa"),
                    "protocol_share_mantissa": collateral.get("protocol_share_mantissa"),
                    "estimated_profit": estimated_profit,
                    "liquidation_profit": estimated_profit,
                }
            )
    candidates.sort(
        key=lambda row: (
            row.get("status") == "candidate",
            int(row.get("theoretical_reward_raw") or 0),
            int(row.get("repay_amount_raw") or 0),
        ),
        reverse=True,
    )
    return candidates[: max(1, int(max_candidates or 100))]


def build_venus_user_report(
    w3: Web3,
    comptroller_address: str,
    account: str,
    market_table: dict[str, Any],
    config: VenusScanConfig = VenusScanConfig(),
) -> dict[str, Any]:
    markets = market_table.get("markets") or []
    risk = scan_venus_account_risk(
        w3,
        comptroller_address,
        account,
        markets,
        warning_liquidity_raw=config.warning_liquidity_raw,
    )
    comptroller = _contract(w3, comptroller_address, COMPTROLLER_ABI)
    close_factor = int(market_table.get("close_factor_mantissa") or config.close_factor_mantissa)
    candidates = build_venus_liquidation_candidates(
        risk,
        comptroller=comptroller,
        close_factor_mantissa=close_factor,
        min_repay_raw=config.min_repay_raw,
        min_seize_raw=config.min_seize_raw,
        max_candidates=config.max_candidates,
        flashloan_fee_percent=config.flashloan_fee_percent,
        dex_swap_fee_percent=config.dex_swap_fee_percent,
        dex_slippage_percent=config.dex_slippage_percent,
        gas_cost_usd=config.gas_cost_usd,
        mev_buffer_usd=config.mev_buffer_usd,
        retry_buffer_usd=config.retry_buffer_usd,
        min_operator_net_profit_usd=config.min_operator_net_profit_usd,
        flashloan_supported_debt_assets=config.flashloan_supported_debt_assets,
    )
    return {
        "account": risk["account"],
        "network": "bnb",
        "chain_id": 56,
        "protocol": "venus_core",
        "summary": {
            "status": risk["status"],
            "health_factor": risk.get("health_factor"),
            "health_factor_band": risk.get("health_factor_band"),
            "health_factor_source": risk.get("health_factor_source"),
            "liquidity_raw": risk["liquidity_raw"],
            "shortfall_raw": risk["shortfall_raw"],
            "total_collateral_value_usd": risk.get("total_collateral_value_usd", 0.0),
            "total_debt_value_usd": risk.get("total_debt_value_usd", 0.0),
            "total_collateral_base": risk.get("total_collateral_value_usd", 0.0),
            "total_debt_base": risk.get("total_debt_value_usd", 0.0),
            "candidate_count": sum(1 for row in candidates if row.get("status") == "candidate"),
            "positions_count": len(risk.get("positions") or []),
            "debt_positions_count": risk.get("debt_positions_count"),
            "collateral_positions_count": risk.get("collateral_positions_count"),
        },
        "risk": risk,
        "positions": risk.get("positions") or [],
        "liquidation_candidates": candidates,
        "recommended_candidate": next((row for row in candidates if row.get("status") == "candidate"), None),
        "execution_enabled": False,
        "execution_plan": {
            "execution_ready": False,
            "reason": "venus_core_observation_mode",
            "next_step": "continue read-only watch",
        },
    }


def summarize_venus_reports(reports: Iterable[dict[str, Any]]) -> dict[str, Any]:
    rows = list(reports)
    candidate_count = sum(len(row.get("liquidation_candidates") or []) for row in rows)
    executable_count = sum(1 for row in rows for item in row.get("liquidation_candidates") or [] if item.get("status") == "candidate")
    profit_values = [
        float((item.get("estimated_profit") or {}).get("net_profit_base") or 0.0)
        for row in rows
        for item in row.get("liquidation_candidates") or []
        if item.get("status") == "candidate"
    ]
    return {
        "network": "bnb",
        "chain_id": 56,
        "protocol": "venus_core",
        "account_count": len(rows),
        "liquidatable_accounts": sum(1 for row in rows if (row.get("summary") or {}).get("status") == "liquidatable"),
        "warning_accounts": sum(1 for row in rows if (row.get("summary") or {}).get("status") == "warning"),
        "healthy_accounts": sum(1 for row in rows if (row.get("summary") or {}).get("status") == "healthy"),
        "candidate_count": candidate_count,
        "executable_candidate_count": executable_count,
        "positive_net_profit_count": sum(1 for value in profit_values if value > 0),
        "negative_net_profit_count": sum(1 for value in profit_values if value < 0),
        "execution_enabled": False,
    }
