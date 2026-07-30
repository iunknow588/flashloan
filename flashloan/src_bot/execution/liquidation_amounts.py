from __future__ import annotations

from typing import Any


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
    profit = candidate.get("estimated_profit") or {}
    legacy_net_profit_base = _float_value(profit.get("net_profit_base"))
    contract_surplus_base = _float_value(profit.get("contract_surplus_base") or legacy_net_profit_base)
    gas_cost_usd = _float_value(profit.get("gas_cost_usd"))
    mev_buffer_usd = _float_value(profit.get("mev_buffer_usd"))
    retry_buffer_usd = _float_value(profit.get("retry_buffer_usd"))
    operator_net_profit_usd = _float_value(
        profit.get("operator_net_profit_usd")
        or profit.get("estimated_operator_net_profit_usd")
        or (contract_surplus_base - gas_cost_usd - mev_buffer_usd - retry_buffer_usd)
    )

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
            "contract_surplus_base": contract_surplus_base,
            "gas_cost_usd": gas_cost_usd,
            "mev_buffer_usd": mev_buffer_usd,
            "retry_buffer_usd": retry_buffer_usd,
            "operator_net_profit_estimate_usd": operator_net_profit_usd,
            "legacy_net_profit_base": legacy_net_profit_base,
        },
        "legacy_fields": {
            "debtToCover": str(debt_to_cover_units),
            "minCollateralSwapOut": str(min_collateral_swap_out_units),
            "minProfitAmount": str(min_profit_units),
        },
    }
