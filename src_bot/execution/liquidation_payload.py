from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from web3 import Web3

from core.sensitive_data import redact_sensitive_text
from execution.dex_costs import ROUTER_ABI, USDC
from execution.liquidation_amounts import build_liquidation_amounts


@dataclass(frozen=True)
class LiquidationExecutionPayloadConfig:
    min_profit_buffer_base: int = 0
    rounding_buffer_units: int = 10
    allow_zero_min_collateral_out: bool = False
    slippage_bps: int = 50
    gas_limit: int = 0


def _checksum(value: str) -> str:
    return Web3.to_checksum_address(str(value))


def quote_liquidation_collateral_swap(
    *,
    rpc_url: str,
    router_address: str,
    collateral_asset: str,
    debt_asset: str,
    collateral_amount: int,
    slippage_bps: int = 50,
) -> dict[str, Any]:
    if collateral_amount <= 0:
        raise ValueError("collateral_amount must be positive")
    collateral = _checksum(collateral_asset)
    debt = _checksum(debt_asset)
    quote_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    if collateral.lower() == debt.lower():
        return {
            "dex_name": "same-token",
            "router_address": _checksum(router_address),
            "amount_in": str(collateral_amount),
            "quoted_amount_out": str(collateral_amount),
            "min_amount_out": str(collateral_amount),
            "path": [],
            "slippage_bps": 0,
            "quote_block": None,
            "quote_at": quote_at,
            "viable": True,
        }

    w3 = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": 10}))
    router = w3.eth.contract(address=_checksum(router_address), abi=ROUTER_ABI)
    slippage = max(0, min(int(slippage_bps), 5000))
    paths = [[collateral, debt]]
    usdc = _checksum(USDC)
    if collateral.lower() != usdc.lower() and debt.lower() != usdc.lower():
        paths.append([collateral, usdc, debt])

    errors = []
    for path in paths:
        try:
            amounts = [int(value) for value in router.functions.getAmountsOut(int(collateral_amount), path).call()]
            quoted_out = int(amounts[-1])
            if quoted_out <= 0:
                errors.append({"path": path, "error": "quoted output is zero"})
                continue
            min_out = quoted_out * (10000 - slippage) // 10000
            quote_block = None
            try:
                quote_block = int(w3.eth.block_number)
            except Exception:
                quote_block = None
            return {
                "dex_name": "Trader Joe V2",
                "router_address": _checksum(router_address),
                "amount_in": str(collateral_amount),
                "quoted_amount_out": str(quoted_out),
                "min_amount_out": str(min_out),
                "path": path,
                "slippage_bps": slippage,
                "quote_block": quote_block,
                "quote_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "viable": min_out > 0,
            }
        except Exception as exc:
            errors.append({"path": path, "error": redact_sensitive_text(exc)})
    raise ValueError(f"unable to quote collateral swap: {errors}")


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
    swap_path = [
        _checksum(address)
        for address in (candidate.get("swap_path") or [])
        if str(address or "").strip()
    ]
    dex_quote = candidate.get("dex_quote") or None
    min_collateral_swap_out = int(
        candidate.get("min_collateral_swap_out")
        or candidate.get("min_amount_out")
        or candidate.get("min_debt_asset_out")
        or 0
    )
    if collateral_asset.lower() != debt_asset.lower() and min_collateral_swap_out <= 0:
        collateral_amount = int(candidate.get("max_collateral_to_liquidate") or 0)
        rpc_url = str((report.get("context") or {}).get("rpc_url") or "").strip()
        if rpc_url:
            dex_quote = quote_liquidation_collateral_swap(
                rpc_url=rpc_url,
                router_address=router_address,
                collateral_asset=collateral_asset,
                debt_asset=debt_asset,
                collateral_amount=collateral_amount,
                slippage_bps=config.slippage_bps,
            )
            min_collateral_swap_out = int(dex_quote["min_amount_out"])
            swap_path = list(dex_quote.get("path") or [])
    if collateral_asset.lower() != debt_asset.lower() and min_collateral_swap_out <= 0 and not config.allow_zero_min_collateral_out:
        raise ValueError("min_collateral_swap_out is required")
    if collateral_asset.lower() != debt_asset.lower() and not swap_path:
        swap_path = [collateral_asset, debt_asset]

    profit = candidate.get("estimated_profit") or {}
    estimated_net_profit = int(max(0, float(profit.get("net_profit_base") or 0)))
    min_profit_amount = max(0, estimated_net_profit - int(config.min_profit_buffer_base) - int(config.rounding_buffer_units))
    gas_limit = max(0, int(config.gas_limit))

    request = {
        "user": account,
        "collateralAsset": collateral_asset,
        "debtAsset": debt_asset,
        "debtToCover": str(debt_to_cover),
        "minCollateralSwapOut": str(min_collateral_swap_out),
        "minProfitAmount": str(min_profit_amount),
        "deadline": str(int(deadline)),
        "gasLimit": str(gas_limit),
        "swapPath": swap_path if collateral_asset.lower() != debt_asset.lower() else [],
    }
    amounts = build_liquidation_amounts(
        candidate,
        debt_to_cover_units=debt_to_cover,
        min_collateral_swap_out_units=min_collateral_swap_out,
        min_profit_units=min_profit_amount,
    )
    return {
        "executor": _checksum(executor_address),
        "router": _checksum(router_address),
        "method": "requestLiquidation",
        "payload_built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "request": request,
        "preflight": {
            "static_call_required": True,
            "static_call_status": "pending",
            "static_call_passed": False,
            "static_call_error": None,
            "static_call_simulated_at": None,
            "rounding_buffer_units": int(config.rounding_buffer_units),
            "min_profit_buffer_base": int(config.min_profit_buffer_base),
            "gas_limit": gas_limit,
            "min_profit_consistency": validate_min_profit_consistency(
                request,
                profit,
                min_profit_buffer_base=int(config.min_profit_buffer_base),
                rounding_buffer_units=int(config.rounding_buffer_units),
            ),
            "allow_zero_min_collateral_out": bool(config.allow_zero_min_collateral_out),
            "slippage_bps": int(config.slippage_bps),
        },
        "dex_quote": dex_quote,
        "amounts": amounts,
    }


def expected_min_profit_amount(
    profit: dict[str, Any],
    *,
    min_profit_buffer_base: int = 0,
    rounding_buffer_units: int = 10,
) -> int:
    net_profit = int(max(0, float(profit.get("net_profit_base") or 0)))
    return max(0, net_profit - int(min_profit_buffer_base) - int(rounding_buffer_units))


def validate_min_profit_consistency(
    request: dict[str, Any],
    profit: dict[str, Any],
    *,
    min_profit_buffer_base: int = 0,
    rounding_buffer_units: int = 10,
) -> dict[str, Any]:
    expected = expected_min_profit_amount(
        profit,
        min_profit_buffer_base=min_profit_buffer_base,
        rounding_buffer_units=rounding_buffer_units,
    )
    try:
        actual = int(request.get("minProfitAmount") or 0)
    except (TypeError, ValueError):
        actual = -1
    return {
        "consistent": actual == expected,
        "actual_min_profit_amount": actual,
        "expected_min_profit_amount": expected,
        "min_profit_buffer_base": int(min_profit_buffer_base),
        "rounding_buffer_units": int(rounding_buffer_units),
    }
