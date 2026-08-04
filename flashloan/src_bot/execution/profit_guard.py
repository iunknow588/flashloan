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



def calculate_liquidation_profit(
    *,
    repay_base: float,
    bonus_rate: float,
    flashloan_rate: float,
    slippage_rate: float,
    gas_cost_usd: float = 0.0,
    mev_buffer_usd: float = 0.0,
    retry_buffer_usd: float = 0.0,
    repay_fraction: float = 1.0,
) -> dict[str, float]:
    """Unified profit calculation used across scan, guard, and amounts modules.

    Formula: net_profit = (seized - repay) - fees - buffers
    where seized = repay * (1 + bonus_rate)
          fees   = repay * (flashloan_rate + slippage_rate)

    Returns dict with: repay_base, seized_base, gross_profit_base,
    fee_base, contract_surplus_base, gas_cost_usd, mev_buffer_usd,
    retry_buffer_usd, operator_net_profit_usd, net_profit_base, profitable
    """
    repay_base = max(0.0, float(repay_base)) * max(0.0, min(1.0, float(repay_fraction)))
    seized_base = repay_base * (1.0 + max(0.0, float(bonus_rate)))
    gross_profit_base = seized_base - repay_base
    fee_base = repay_base * (max(0.0, float(flashloan_rate)) + max(0.0, float(slippage_rate)))
    contract_surplus_base = gross_profit_base - fee_base
    gas_cost = max(0.0, float(gas_cost_usd))
    mev_buffer = max(0.0, float(mev_buffer_usd))
    retry_buffer = max(0.0, float(retry_buffer_usd))
    operator_net_profit_usd = contract_surplus_base - gas_cost - mev_buffer - retry_buffer
    net_profit_base = operator_net_profit_usd
    return {
        "repay_base": repay_base,
        "bonus_rate": max(0.0, float(bonus_rate)),
        "flashloan_rate": max(0.0, float(flashloan_rate)),
        "slippage_rate": max(0.0, float(slippage_rate)),
        "seized_base": seized_base,
        "gross_profit_base": gross_profit_base,
        "fee_base": fee_base,
        "contract_surplus_base": contract_surplus_base,
        "gas_cost_usd": gas_cost,
        "mev_buffer_usd": mev_buffer,
        "retry_buffer_usd": retry_buffer,
        "operator_net_profit_usd": operator_net_profit_usd,
        "net_profit_base": net_profit_base,
        "profitable": net_profit_base > 0,
    }

