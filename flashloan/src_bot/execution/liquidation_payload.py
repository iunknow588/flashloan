from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from web3 import Web3


@dataclass(frozen=True)
class LiquidationExecutionPayloadConfig:
    min_profit_buffer_base: int = 0
    rounding_buffer_units: int = 10
    allow_zero_min_collateral_out: bool = False


def _checksum(value: str) -> str:
    return Web3.to_checksum_address(str(value))


def build_liquidation_execution_payload(
    report: dict[str, Any],
    *,
    executor_address: str,
    router_address: str,
    deadline: int,
    config: LiquidationExecutionPayloadConfig = LiquidationExecutionPayloadConfig(),
) -> dict[str, Any]:
    summary = report.get("summary") or {}
    execution_plan = report.get("execution_plan") or {}
    if summary.get("status") != "liquidatable":
        raise ValueError("account is not liquidatable")
    if execution_plan and not execution_plan.get("execution_ready"):
        raise ValueError("liquidation execution plan is not ready")

    candidate = report.get("recommended_candidate") or {}
    if not candidate:
        raise ValueError("recommended_candidate is required")

    account = _checksum(str(report.get("account") or ""))
    collateral_asset = _checksum(str(candidate.get("collateral_asset") or ""))
    debt_asset = _checksum(str(candidate.get("debt_asset") or ""))
    debt_to_cover = int(candidate.get("amount_to_pass_to_liquidation_call") or candidate.get("max_debt_to_liquidate") or 0)
    if debt_to_cover <= 0:
        raise ValueError("debt_to_cover must be positive")
    min_collateral_swap_out = int(
        candidate.get("min_collateral_swap_out")
        or candidate.get("min_amount_out")
        or candidate.get("min_debt_asset_out")
        or 0
    )
    if collateral_asset.lower() != debt_asset.lower() and min_collateral_swap_out <= 0 and not config.allow_zero_min_collateral_out:
        raise ValueError("min_collateral_swap_out is required")

    profit = candidate.get("estimated_profit") or {}
    estimated_net_profit = int(max(0, float(profit.get("net_profit_base") or 0)))
    min_profit_amount = max(0, estimated_net_profit - int(config.min_profit_buffer_base) - int(config.rounding_buffer_units))

    request = {
        "user": account,
        "collateralAsset": collateral_asset,
        "debtAsset": debt_asset,
        "debtToCover": str(debt_to_cover),
        "minCollateralSwapOut": str(min_collateral_swap_out),
        "minProfitAmount": str(min_profit_amount),
        "deadline": str(int(deadline)),
        "swapPath": [collateral_asset, debt_asset] if collateral_asset.lower() != debt_asset.lower() else [],
    }
    return {
        "executor": _checksum(executor_address),
        "router": _checksum(router_address),
        "method": "requestLiquidation",
        "request": request,
        "preflight": {
            "static_call_required": True,
            "rounding_buffer_units": int(config.rounding_buffer_units),
            "min_profit_buffer_base": int(config.min_profit_buffer_base),
            "allow_zero_min_collateral_out": bool(config.allow_zero_min_collateral_out),
        },
    }
