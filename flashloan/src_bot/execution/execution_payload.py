from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from web3 import Web3

from execution.plan_quotes import quote_token, to_units


USE_FULL_BALANCE = str(2**256 - 1)


@dataclass(frozen=True)
class PayloadConfig:
    min_profit_usdc: float = 0.0
    deadline_seconds: int = 600
    min_step_count: int = 3


def build_execution_payload(execution_plan: dict, quote: dict, config: PayloadConfig | None = None) -> dict:
    if not execution_plan:
        raise ValueError("execution_plan is required")
    if not quote:
        raise ValueError("quote is required")
    if quote.get("errors"):
        raise ValueError("quote has errors; refusing to build execution payload")
    if not quote.get("viable"):
        raise ValueError("quote is not viable; refusing to build execution payload")

    config = config or PayloadConfig()
    router_address = Web3.to_checksum_address(quote["router_address"])
    steps = []

    for step in quote.get("buy_steps", []):
        steps.append(exact_in_swap_step(router_address, step, "input_amount", "min_output_amount"))
    for step in quote.get("sell_steps", []):
        steps.append(exact_in_swap_step(router_address, step, "input_amount", "min_output_amount"))
    for step in quote.get("repay_steps", []):
        steps.append(exact_in_swap_step(router_address, step, "max_input_amount", "output_amount"))

    usdc = quote_token("USDC")
    if len(steps) < max(2, int(config.min_step_count)):
        raise ValueError("flashloan execution payload requires a multi-step closed route")
    aave_compatible, aave_reason = aave_usdc_compatibility(steps, usdc.token_address)
    min_profit_units = str(to_units(config.min_profit_usdc, usdc.decimals)) if config.min_profit_usdc > 0 else "0"

    return {
        "version": 1,
        "source_plan_version": execution_plan.get("version"),
        "strategy": execution_plan.get("mode"),
        "contract": {
            "mockFundedExecutor": {
                "steps": steps,
                "profitToken": Web3.to_checksum_address(usdc.token_address),
                "minProfit": min_profit_units,
                "deadlineSeconds": int(config.deadline_seconds),
            },
            "aaveSequentialFlashLoanExecutor": {
                "compatible": aave_compatible,
                "reason": aave_reason,
                "borrowAsset": steps[0]["tokenIn"] if aave_compatible and steps else None,
                "borrowAmount": steps[0]["amountIn"] if aave_compatible and steps else None,
                "plan": {
                    "steps": steps,
                    "deadlineSeconds": int(config.deadline_seconds),
                    "profitToken": Web3.to_checksum_address(usdc.token_address),
                    "minProfitAmount": min_profit_units,
                } if aave_compatible else None,
            },
        },
        "quote": {
            "dex_name": quote.get("dex_name"),
            "slippage_bps": quote.get("slippage_bps"),
            "quoted_profit_usdc": quote.get("quoted_profit_usdc"),
            "total_sell_usdc": quote.get("total_sell_usdc"),
            "total_repay_usdc": quote.get("total_repay_usdc"),
        },
    }


def exact_in_swap_step(router_address: str, step: dict, amount_in_key: str, min_out_key: str) -> dict:
    from_token = quote_token(step["from_symbol"])
    to_token = quote_token(step["to_symbol"])
    path = [Web3.to_checksum_address(address) for address in step["path"]]
    if path[0] != Web3.to_checksum_address(from_token.token_address):
        raise ValueError(f"path tokenIn mismatch for {step.get('action')}")
    if path[-1] != Web3.to_checksum_address(to_token.token_address):
        raise ValueError(f"path tokenOut mismatch for {step.get('action')}")
    return {
        "router": router_address,
        "tokenIn": path[0],
        "tokenOut": path[-1],
        "amountIn": str(to_units(float(step[amount_in_key]), from_token.decimals)),
        "amountOutMin": str(to_units(float(step[min_out_key]), to_token.decimals)),
        "path": path,
        "rank": int(step.get("rank", 0)),
        "action": step.get("action"),
        "pathSymbols": step.get("path_symbols", []),
    }


def aave_usdc_compatibility(steps: list[dict], usdc_address: str) -> tuple[bool, str]:
    if not steps:
        return False, "no swap steps"
    borrowed_asset = steps[0]["tokenIn"]
    if steps[-1]["tokenOut"] != borrowed_asset:
        return False, "current plan does not end in the borrowed asset required for flashLoanSimple repayment"
    return True, "compatible with single-asset flashLoanSimple borrowed-asset repayment"


def write_payload_file(payload: dict, path: str | Path) -> Path:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")
    return output_path
