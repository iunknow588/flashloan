from __future__ import annotations

from dataclasses import dataclass
from typing import Any


def _float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class LiquidationProfitInput:
    debt_to_cover_usd: float | None
    liquidation_bonus_bps: float | None = 500.0
    protocol_fee_bps: float | None = 0.0
    slippage_bps: float | None = None
    gas_used: float | None = None
    gas_price_wei: float | None = None
    native_usd: float | None = None
    flashloan_premium_bps: float | None = 5.0
    mev_loss_usd: float | None = None
    other_costs_usd: float | None = 0.0


def profit_input_from_event(event: dict[str, Any]) -> LiquidationProfitInput:
    """Read explicit event fields without inventing market prices."""
    return LiquidationProfitInput(
        debt_to_cover_usd=_float(event.get("debt_to_cover_usd")),
        liquidation_bonus_bps=_float(event.get("liquidation_bonus_bps")),
        protocol_fee_bps=_float(event.get("protocol_fee_bps")),
        slippage_bps=_float(event.get("estimated_slippage_bps")),
        gas_used=_float(event.get("gas_used")),
        gas_price_wei=_float(event.get("effective_gas_price") or event.get("gas_price_wei")),
        native_usd=_float(event.get("native_usd") or event.get("native_usd_price")),
        flashloan_premium_bps=_float(event.get("flashloan_premium_bps")),
        mev_loss_usd=_float(event.get("mev_loss_usd")),
        other_costs_usd=_float(event.get("other_costs_usd")) or 0.0,
    )


def calculate_liquidation_profit(item: LiquidationProfitInput) -> dict[str, Any]:
    """Calculate only when the minimum economic inputs are present."""
    debt = item.debt_to_cover_usd
    required = {
        "debt_to_cover_usd": debt,
        "gas_used": item.gas_used,
        "gas_price_wei": item.gas_price_wei,
        "native_usd": item.native_usd,
    }
    missing = [name for name, value in required.items() if value is None or value <= 0]
    if debt is None or debt <= 0:
        missing.insert(0, "debt_to_cover_usd")
    if missing:
        return {
            "status": "missing_price_inputs",
            "missing_inputs": list(dict.fromkeys(missing)),
            "net_profit_usd": None,
            "net_margin_percent": None,
        }

    bonus_bps = max(0.0, item.liquidation_bonus_bps or 0.0)
    protocol_bps = max(0.0, item.protocol_fee_bps or 0.0)
    slippage_bps = max(0.0, item.slippage_bps or 0.0)
    premium_bps = max(0.0, item.flashloan_premium_bps or 0.0)
    gross_bonus = debt * bonus_bps / 10_000.0
    protocol_fee = debt * protocol_bps / 10_000.0
    slippage = debt * slippage_bps / 10_000.0
    flashloan_fee = debt * premium_bps / 10_000.0
    gas_cost = item.gas_used * item.gas_price_wei * item.native_usd / 1e18
    mev_loss = max(0.0, item.mev_loss_usd or 0.0)
    other_costs = max(0.0, item.other_costs_usd or 0.0)
    total_costs = protocol_fee + slippage + flashloan_fee + gas_cost + mev_loss + other_costs
    net = gross_bonus - total_costs
    return {
        "status": "calculated",
        "missing_inputs": [],
        "gross_bonus_usd": gross_bonus,
        "protocol_fee_usd": protocol_fee,
        "slippage_usd": slippage,
        "flashloan_fee_usd": flashloan_fee,
        "gas_cost_usd": gas_cost,
        "mev_loss_usd": mev_loss,
        "other_costs_usd": other_costs,
        "total_costs_usd": total_costs,
        "net_profit_usd": net,
        "net_margin_percent": net / debt * 100.0,
    }


def gas_stress_table(
    item: LiquidationProfitInput,
    gas_price_gwei_values: list[float],
) -> list[dict[str, Any]]:
    rows = []
    for gas_price_gwei in gas_price_gwei_values:
        stressed = LiquidationProfitInput(
            **{
                **item.__dict__,
                "gas_price_wei": max(0.0, float(gas_price_gwei)) * 1e9,
            }
        )
        result = calculate_liquidation_profit(stressed)
        rows.append({"gas_price_gwei": float(gas_price_gwei), **result})
    return rows

