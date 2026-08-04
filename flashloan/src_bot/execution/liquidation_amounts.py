from __future__ import annotations

from typing import Any

from execution.profit_guard import calculate_liquidation_profit


def token_amount(units: int, decimals: int) -> float:
    if units <= 0:
        return 0.0
    try:
        return float(units) / float(10 ** int(decimals))
    except Exception:
        return 0.0


def usd_value(amount: float, price: float) -> float | None:
    try:
        if price <= 0:
            return None
        return float(amount) * float(price)
    except Exception:
        return None


def _int_value(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _float_value(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _rate_value(profit: dict[str, Any], key: str, percent_key: str) -> float:
    if key in profit:
        return max(0.0, _float_value(profit.get(key)))
    if percent_key in profit:
        return max(0.0, _float_value(profit.get(percent_key)) / 100.0)
    return 0.0


def _profit_snapshot(
    candidate: dict[str, Any],
    *,
    debt_to_cover_units: int,
    debt_decimals: int,
    debt_price: float,
) -> dict[str, float]:
    profit = candidate.get("estimated_profit") or {}
    debt_amount = token_amount(debt_to_cover_units, debt_decimals)
    debt_value = usd_value(debt_amount, debt_price) or 0.0
    legacy_net_profit_base = _float_value(profit.get("net_profit_base"))
    contract_surplus_base = _float_value(profit.get("contract_surplus_base") or legacy_net_profit_base)
    repay_base = _float_value(profit.get("repay_base") or debt_value)
    bonus_rate = _rate_value(profit, "bonus_rate", "liquidation_bonus_percent")
    flashloan_rate = _rate_value(profit, "flashloan_rate", "flashloan_fee_percent")
    slippage_rate = _rate_value(profit, "slippage_rate", "dex_slippage_percent")

    if repay_base > 0 and bonus_rate == 0.0 and flashloan_rate == 0.0 and slippage_rate == 0.0:
        bonus_rate = max(0.0, contract_surplus_base / repay_base)

    return calculate_liquidation_profit(
        repay_base=repay_base,
        bonus_rate=bonus_rate,
        flashloan_rate=flashloan_rate,
        slippage_rate=slippage_rate,
        gas_cost_usd=_float_value(profit.get("gas_cost_usd")),
        mev_buffer_usd=_float_value(profit.get("mev_buffer_usd")),
        retry_buffer_usd=_float_value(profit.get("retry_buffer_usd")),
    )


def build_liquidation_amounts(
    candidate: dict[str, Any],
    *,
    debt_to_cover_units: int,
    min_collateral_swap_out_units: int,
    min_profit_units: int,
) -> dict[str, Any]:
    debt_decimals = _int_value(candidate.get("debt_decimals") or 18)
    collateral_decimals = _int_value(candidate.get("collateral_decimals") or 18)
    debt_price = _float_value(candidate.get("debt_price"))
    collateral_price = _float_value(candidate.get("collateral_price"))
    max_collateral_units = _int_value(candidate.get("max_collateral_to_liquidate"))
    debt_amount = token_amount(debt_to_cover_units, debt_decimals)
    collateral_amount = token_amount(max_collateral_units, collateral_decimals)
    min_out_amount = token_amount(min_collateral_swap_out_units, debt_decimals)
    min_profit_amount = token_amount(min_profit_units, debt_decimals)
    raw_profit = candidate.get("estimated_profit") or {}
    profit = _profit_snapshot(
        candidate,
        debt_to_cover_units=debt_to_cover_units,
        debt_decimals=debt_decimals,
        debt_price=debt_price,
    )
    legacy_net_profit_base = _float_value(raw_profit.get("net_profit_base"))

    return {
        "schema_version": 1,
        "debt": {
            "asset": candidate.get("debt_asset"),
            "symbol": candidate.get("debt_symbol") or candidate.get("debt_token_symbol"),
            "decimals": debt_decimals,
            "price_usd": debt_price,
            "debt_to_cover_units": str(debt_to_cover_units),
            "debt_to_cover_amount": debt_amount,
            "debt_to_cover_usd": usd_value(debt_amount, debt_price),
        },
        "collateral": {
            "asset": candidate.get("collateral_asset"),
            "symbol": candidate.get("collateral_symbol") or candidate.get("collateral_token_symbol"),
            "decimals": collateral_decimals,
            "price_usd": collateral_price,
            "max_collateral_to_liquidate_units": str(max_collateral_units),
            "max_collateral_to_liquidate_amount": collateral_amount,
            "max_collateral_to_liquidate_usd": usd_value(collateral_amount, collateral_price),
        },
        "swap": {
            "min_collateral_swap_out_units": str(min_collateral_swap_out_units),
            "min_collateral_swap_out_amount": min_out_amount,
            "output_asset": candidate.get("debt_asset"),
            "output_symbol": candidate.get("debt_symbol") or candidate.get("debt_token_symbol"),
        },
        "profit": {
            "profit_asset": candidate.get("debt_asset"),
            "profit_symbol": candidate.get("debt_symbol") or candidate.get("debt_token_symbol"),
            "min_profit_units": str(min_profit_units),
            "min_profit_amount": min_profit_amount,
            "repay_base": profit["repay_base"],
            "seized_base": profit["seized_base"],
            "gross_profit_base": profit["gross_profit_base"],
            "fee_base": profit["fee_base"],
            "contract_surplus_base": profit["contract_surplus_base"],
            "gas_cost_usd": profit["gas_cost_usd"],
            "mev_buffer_usd": profit["mev_buffer_usd"],
            "retry_buffer_usd": profit["retry_buffer_usd"],
            "operator_net_profit_estimate_usd": profit["operator_net_profit_usd"],
            "net_profit_base": profit["net_profit_base"],
            "legacy_net_profit_base": legacy_net_profit_base,
        },
        "legacy_fields": {
            "debtToCover": str(debt_to_cover_units),
            "minCollateralSwapOut": str(min_collateral_swap_out_units),
            "minProfitAmount": str(min_profit_units),
        },
    }
