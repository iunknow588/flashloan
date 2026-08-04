from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_FLOOR
from typing import Any

from web3 import Web3

from core.sensitive_data import redact_sensitive_text
from execution.dex_costs import ROUTER_ABI, USDC
from execution.liquidation_amounts import build_liquidation_amounts

WAVAX = "0xB31f66AA3C1e785363F0875A1B74E27b85FD66c7"


@dataclass(frozen=True)
class LiquidationExecutionPayloadConfig:
    min_profit_buffer_base: int = 0
    rounding_buffer_units: int = 10
    allow_zero_min_collateral_out: bool = False
    slippage_bps: int = 50
    gas_limit: int = 0
    swap_intermediate_assets: tuple[str, ...] = (USDC, WAVAX)


def _checksum(value: str) -> str:
    return Web3.to_checksum_address(str(value))


def _decimal_value(value: Any, default: str = "0") -> Decimal:
    try:
        return Decimal(str(value if value is not None else default))
    except (InvalidOperation, ValueError):
        return Decimal(default)


def _decimals_value(value: Any) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 18


def profit_base_to_debt_units(
    profit_base: Any,
    *,
    debt_decimals: Any = 18,
    debt_price: Any = 1,
) -> int:
    """Convert a USD/base-currency profit estimate into debt-asset token units."""
    profit = max(Decimal("0"), _decimal_value(profit_base))
    price = _decimal_value(debt_price)
    if profit <= 0 or price <= 0:
        return 0
    decimals = _decimals_value(debt_decimals)
    units = (profit / price) * (Decimal(10) ** decimals)
    return int(units.to_integral_value(rounding=ROUND_FLOOR))


def expected_min_profit_amount(
    profit: dict[str, Any],
    *,
    debt_decimals: Any = 18,
    debt_price: Any = 1,
    min_profit_buffer_base: Any = 0,
    rounding_buffer_units: int = 10,
) -> int:
    net_profit_base = max(Decimal("0"), _decimal_value(profit.get("net_profit_base")))
    buffered_profit_base = max(Decimal("0"), net_profit_base - max(Decimal("0"), _decimal_value(min_profit_buffer_base)))
    min_profit_units = profit_base_to_debt_units(
        buffered_profit_base,
        debt_decimals=debt_decimals,
        debt_price=debt_price,
    )
    return max(0, min_profit_units - int(rounding_buffer_units))


def _unique_swap_paths(paths: list[list[str]]) -> list[list[str]]:
    seen: set[tuple[str, ...]] = set()
    unique: list[list[str]] = []
    for path in paths:
        cleaned = [_checksum(address) for address in path if str(address or "").strip()]
        if len(cleaned) < 2:
            continue
        if len(set(address.lower() for address in cleaned)) != len(cleaned):
            continue
        key = tuple(address.lower() for address in cleaned)
        if key in seen:
            continue
        seen.add(key)
        unique.append(cleaned)
    return unique


def liquidation_swap_quote_paths(
    collateral_asset: str,
    debt_asset: str,
    intermediate_assets: tuple[str, ...] = (USDC, WAVAX),
) -> list[list[str]]:
    collateral = _checksum(collateral_asset)
    debt = _checksum(debt_asset)
    hops = [
        _checksum(asset)
        for asset in intermediate_assets
        if str(asset or "").strip()
        and str(asset).lower() not in {collateral.lower(), debt.lower()}
    ]
    paths = [[collateral, debt]]
    for hop in hops:
        paths.append([collateral, hop, debt])
    for first in hops:
        for second in hops:
            if first.lower() != second.lower():
                paths.append([collateral, first, second, debt])
    return _unique_swap_paths(paths)


def quote_liquidation_collateral_swap(
    *,
    rpc_url: str,
    router_address: str,
    collateral_asset: str,
    debt_asset: str,
    collateral_amount: int,
    slippage_bps: int = 50,
    intermediate_assets: tuple[str, ...] = (USDC, WAVAX),
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
    paths = liquidation_swap_quote_paths(collateral, debt, intermediate_assets)

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

    candidate = report.get("recommended_candidate") or {}
    if not candidate:
        raise ValueError("no executable liquidation candidate; a debt/collateral pair is required")
    if execution_plan and not execution_plan.get("execution_ready"):
        raise ValueError("liquidation execution plan is not ready")

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
                intermediate_assets=config.swap_intermediate_assets,
            )
            min_collateral_swap_out = int(dex_quote["min_amount_out"])
            swap_path = list(dex_quote.get("path") or [])
    if (
        collateral_asset.lower() != debt_asset.lower()
        and not swap_path
        and isinstance(dex_quote, dict)
        and dex_quote.get("path")
    ):
        swap_path = [_checksum(address) for address in dex_quote.get("path") or []]
    if collateral_asset.lower() != debt_asset.lower() and min_collateral_swap_out <= 0 and not config.allow_zero_min_collateral_out:
        raise ValueError("min_collateral_swap_out is required")
    if collateral_asset.lower() != debt_asset.lower() and not swap_path:
        raise ValueError("swap_path is required for cross-asset liquidation")
    if collateral_asset.lower() != debt_asset.lower() and (
        len(swap_path) < 2
        or swap_path[0].lower() != collateral_asset.lower()
        or swap_path[-1].lower() != debt_asset.lower()
    ):
        raise ValueError("swap_path must start with collateral_asset and end with debt_asset")

    profit = candidate.get("estimated_profit") or {}
    debt_decimals = candidate.get("debt_decimals") or 18
    debt_price = candidate.get("debt_price") if candidate.get("debt_price") is not None else 1
    if _decimal_value(profit.get("net_profit_base")) > 0 and _decimal_value(debt_price) <= 0:
        raise ValueError("debt_price is required to build minProfitAmount")
    min_profit_amount = expected_min_profit_amount(
        profit,
        debt_decimals=debt_decimals,
        debt_price=debt_price,
        min_profit_buffer_base=config.min_profit_buffer_base,
        rounding_buffer_units=config.rounding_buffer_units,
    )
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
                debt_decimals=debt_decimals,
                debt_price=debt_price,
                min_profit_buffer_base=int(config.min_profit_buffer_base),
                rounding_buffer_units=int(config.rounding_buffer_units),
            ),
            "allow_zero_min_collateral_out": bool(config.allow_zero_min_collateral_out),
            "slippage_bps": int(config.slippage_bps),
        },
        "dex_quote": dex_quote,
        "amounts": amounts,
    }


def validate_min_profit_consistency(
    request: dict[str, Any],
    profit: dict[str, Any],
    *,
    debt_decimals: Any = 18,
    debt_price: Any = 1,
    min_profit_buffer_base: Any = 0,
    rounding_buffer_units: int = 10,
) -> dict[str, Any]:
    expected = expected_min_profit_amount(
        profit,
        debt_decimals=debt_decimals,
        debt_price=debt_price,
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
        "debt_decimals": _decimals_value(debt_decimals),
        "debt_price": float(_decimal_value(debt_price, default="1")),
        "min_profit_buffer_base": float(_decimal_value(min_profit_buffer_base)),
        "rounding_buffer_units": int(rounding_buffer_units),
    }
