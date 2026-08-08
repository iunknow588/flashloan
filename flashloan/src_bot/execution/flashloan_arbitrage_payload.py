from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_FLOOR
from typing import Any

from web3 import Web3

from cow_flashloan.routes import CowToken, resolve_token
from execution.execution_payload import USE_FULL_BALANCE


@dataclass(frozen=True)
class AtomicFlashLoanPayloadConfig:
    router_address: str
    min_profit_usd: Decimal | str | int | float = Decimal("0")
    deadline_seconds: int = 60
    use_full_balance_after_first_step: bool = True
    min_step_count: int = 3


def build_atomic_flashloan_payload_from_limit_plan(
    plan: dict[str, Any],
    registry: dict[str, CowToken],
    config: AtomicFlashLoanPayloadConfig,
) -> dict[str, Any]:
    if not isinstance(plan, dict) or not plan.get("available"):
        raise ValueError("available multi-step limit plan is required")
    if not config.router_address:
        raise ValueError("router_address is required")

    route = [str(item or "").upper() for item in plan.get("route") or []]
    steps = [step for step in plan.get("steps") or [] if isinstance(step, dict)]
    min_step_count = max(2, int(config.min_step_count))
    if len(route) != len(steps) + 1:
        raise ValueError("route and steps are inconsistent")
    if len(steps) < min_step_count:
        raise ValueError(f"flashloan arbitrage requires at least {min_step_count} swap steps")
    if route[0] != route[-1]:
        raise ValueError("flashloan arbitrage route must end in the borrowed asset")
    if route[0] not in {"USDC", "USDT", "DAI"}:
        raise ValueError("initial flashloan version only supports stablecoin borrowed closed routes")

    router = Web3.to_checksum_address(config.router_address)
    tokens = [resolve_token(symbol, registry) for symbol in route]
    contract_steps = []
    for index, step in enumerate(steps):
        from_symbol = str(step.get("from_symbol") or "").upper()
        to_symbol = str(step.get("to_symbol") or "").upper()
        if from_symbol != route[index] or to_symbol != route[index + 1]:
            raise ValueError(f"step {index + 1} does not match route")
        token_in = tokens[index]
        token_out = tokens[index + 1]
        input_amount = step.get("input_amount")
        if index > 0 and config.use_full_balance_after_first_step:
            amount_in_units = USE_FULL_BALANCE
        else:
            amount_in_units = str(_to_units(input_amount, token_in.decimals))
        amount_out_min = step.get("min_output_amount")
        contract_steps.append(
            {
                "router": router,
                "tokenIn": Web3.to_checksum_address(token_in.address),
                "tokenOut": Web3.to_checksum_address(token_out.address),
                "amountIn": amount_in_units,
                "amountOutMin": str(_to_units(amount_out_min, token_out.decimals)),
                "path": [
                    Web3.to_checksum_address(token_in.address),
                    Web3.to_checksum_address(token_out.address),
                ],
                "rank": int(step.get("step") or index + 1),
                "action": _step_action(index, len(steps)),
                "pathSymbols": [from_symbol, to_symbol],
                "targetRole": step.get("target_role"),
                "selectedTargetSource": step.get("selected_target_source"),
                "selectedAcceptableSource": step.get("selected_acceptable_source"),
            }
        )

    borrowed_token = tokens[0]
    profit_token = tokens[-1]
    borrow_amount = _to_units(plan.get("initial_amount"), borrowed_token.decimals)
    min_profit_units = _to_units(_configured_min_profit(plan, config), profit_token.decimals, allow_zero=True)
    return {
        "version": 1,
        "source": "binance_top1_low_buy_high_sell_limit_plan",
        "strategy": "atomic_aave_flashloan_multi_step_arbitrage",
        "atomicity": "single_transaction_aave_executeOperation_all_steps_or_revert",
        "route": route,
        "stepCount": len(contract_steps),
        "singleStepAllowed": False,
        "requiresStaticCallBeforeBroadcast": True,
        "contract": {
            "aaveSequentialFlashLoanExecutor": {
                "compatible": True,
                "reason": "multi-step closed route compatible with AaveSequentialFlashLoanExecutor",
                "borrowAsset": Web3.to_checksum_address(borrowed_token.address),
                "borrowAmount": str(borrow_amount),
                "plan": {
                    "steps": contract_steps,
                    "deadlineSeconds": int(config.deadline_seconds),
                    "profitToken": Web3.to_checksum_address(profit_token.address),
                    "minProfitAmount": str(min_profit_units),
                    "useFullBalanceAfterFirstStep": bool(config.use_full_balance_after_first_step),
                },
            }
        },
        "riskControls": {
            "minProfitSymbol": profit_token.symbol,
            "minProfitAmount": str(min_profit_units),
            "minProfitUsd": str(_configured_min_profit(plan, config)),
            "rejectsSingleStep": True,
            "rejectsOpenRoute": True,
            "revertsOnAnyFailedHop": True,
        },
        "planProfit": {
            "amount": plan.get("profit_amount"),
            "percent": plan.get("profit_percent"),
            "symbol": plan.get("final_symbol"),
        },
    }


def _step_action(index: int, step_count: int) -> str:
    if index == 0:
        return "flashloan_buy_loser_token"
    if index == step_count - 1:
        return "sell_gainer_back_to_borrowed_asset"
    return "swap_loser_to_gainer_token"


def _configured_min_profit(plan: dict[str, Any], config: AtomicFlashLoanPayloadConfig) -> Decimal:
    configured = _decimal(config.min_profit_usd)
    if configured > 0:
        return configured
    profit = _decimal(plan.get("profit_amount"))
    return profit if profit > 0 else Decimal("0")


def _to_units(value: Any, decimals: int, *, allow_zero: bool = False) -> int:
    amount = _decimal(value)
    if allow_zero and amount == 0:
        return 0
    if amount <= 0:
        raise ValueError("amount must be positive")
    scale = Decimal(10) ** int(decimals)
    return int((amount * scale).to_integral_value(rounding=ROUND_FLOOR))


def _decimal(value: Any) -> Decimal:
    if value is None or value == "":
        return Decimal("0")
    try:
        return Decimal(str(value))
    except Exception as exc:
        raise ValueError(f"invalid decimal amount: {value}") from exc
