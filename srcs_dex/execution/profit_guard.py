from __future__ import annotations

from dataclasses import dataclass

from execution.plan_quotes import from_units, to_units


@dataclass(frozen=True)
class ProfitGuardConfig:
    notional_usd: float
    slippage_bps: int
    gas_cost_usdc: float
    min_net_profit_usdc: float
    safety_margin_usdc: float = 0.0


def usdc_units(value: float) -> int:
    safe_value = max(0.0, value)
    return 0 if safe_value == 0 else to_units(safe_value, 6)


def evaluate_profit_guard(best_quote: dict | None, config: ProfitGuardConfig) -> dict:
    if not best_quote:
        return {
            "net_profit_verified": False,
            "blocked_reasons": ["no_best_quote"],
        }

    quoted_profit_units = int(best_quote.get("profit_usdc_units") or 0)
    slippage_reserve_usdc = config.notional_usd * max(0, config.slippage_bps) / 10000
    slippage_reserve_units = usdc_units(slippage_reserve_usdc)
    gas_cost_units = usdc_units(config.gas_cost_usdc)
    safety_margin_units = usdc_units(config.safety_margin_usdc)
    min_net_profit_units = usdc_units(config.min_net_profit_usdc)

    # Contract-level profit is the atomic on-chain asset surplus. Gas is paid
    # outside the flash-loan repayment path, so it is reported separately and
    # not subtracted from net_profit_usdc.
    net_profit_units = quoted_profit_units - slippage_reserve_units - safety_margin_units
    blocked_reasons = []
    if net_profit_units < min_net_profit_units:
        blocked_reasons.append("net_profit_below_minimum")

    return {
        "quoted_profit_usdc": from_units(quoted_profit_units, 6),
        "slippage_reserve_usdc": from_units(slippage_reserve_units, 6),
        "gas_cost_usdc": from_units(gas_cost_units, 6),
        "safety_margin_usdc": from_units(safety_margin_units, 6),
        "min_net_profit_usdc": from_units(min_net_profit_units, 6),
        "net_profit_usdc": from_units(net_profit_units, 6),
        "quoted_profit_usdc_units": str(quoted_profit_units),
        "slippage_reserve_usdc_units": str(slippage_reserve_units),
        "gas_cost_usdc_units": str(gas_cost_units),
        "safety_margin_usdc_units": str(safety_margin_units),
        "min_net_profit_usdc_units": str(min_net_profit_units),
        "net_profit_usdc_units": str(net_profit_units),
        "net_profit_verified": not blocked_reasons,
        "blocked_reasons": blocked_reasons,
    }
