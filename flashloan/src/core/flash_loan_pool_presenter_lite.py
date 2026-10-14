"""Lightweight presenter helpers shared by the flash-loan web layer.

These helpers intentionally avoid importing ``dex.intent_trade`` so cold
imports of the control-panel routes stay fast.  The heavier DEX-specific
presenters remain in ``dex.intent_trade.flows.flash_loan_pools.presenter``.
"""

from __future__ import annotations

from typing import Any


STABLE_SYMBOLS = {
    "AUSD",
    "BUSD",
    "DAI",
    "DAI.E",
    "EURC",
    "FDUSD",
    "FRAX",
    "FRAX.E",
    "GHO",
    "MIM",
    "SUSDE",
    "TUSD",
    "USDC",
    "USDC.E",
    "USDD",
    "USDE",
    "USDG",
    "USDT",
    "USDT.E",
}
ROUTE_ONLY_PROTOCOLS = {"hyperliquid_native"}


def _as_float(value: object, default: float = 0.0) -> float:
    try:
        parsed = float(value or 0)
    except (TypeError, ValueError):
        return default
    return parsed if parsed == parsed and parsed not in (float("inf"), float("-inf")) else default


def _is_stable_symbol(symbol: object) -> bool:
    normalized = str(symbol or "").strip().upper()
    bridge_base = normalized[:-2] if normalized.endswith(".E") else normalized
    return normalized in STABLE_SYMBOLS or bridge_base in STABLE_SYMBOLS or normalized.endswith("USD")


def _protocol_display_name(protocol: str) -> str:
    labels = {
        "aave_v3": "Aave V3",
        "hyper_evm_aave_v3": "HyperEVM Aave V3",
        "venus_core": "Venus Core",
        "morpho_blue": "Morpho Blue",
        "uniswap_v2": "Uniswap V2",
        "uniswap_v3": "Uniswap V3",
        "uniswap_v4": "Uniswap V4",
        "pancakeswap_v2": "PancakeSwap V2",
        "pancakeswap_v3": "PancakeSwap V3",
        "biswap_v2": "Biswap V2",
        "quickswap_v2": "QuickSwap V2",
        "traderjoe_v2": "Trader Joe V2",
        "balancer_v2": "Balancer V2",
        "cow_protocol": "CoW Protocol",
        "hyperliquid_native": "Hyperliquid Native",
    }
    normalized = str(protocol or "").strip().lower()
    if normalized in labels:
        return labels[normalized]
    value = str(protocol or "").strip().replace("_", " ")
    return value.title() if value else "Unknown"


def _empty_protocol_view(protocol: str, *, status: str = "protocol_pool_cache_not_available") -> dict[str, Any]:
    messages = {
        "chain_protocol_not_supported": "当前链不支持该闪电贷协议",
        "protocol_pool_cache_not_available": "该协议闪电贷可用资金池缓存尚未接入",
    }
    return {
        "protocol": protocol,
        "display_name": _protocol_display_name(protocol),
        "active": True,
        "cache": {
            "schema_version": None,
            "chain_id": None,
            "block_number": None,
            "refreshed_at": None,
            "pool_address": "",
            "identities": [],
            "capabilities": {},
            "rpc_url": "",
            "asset_count": 0,
        },
        "summary": {
            "stable_borrow_pool_count": 0,
            "non_stable_borrow_pool_count": 0,
            "borrow_target_count": 0,
            "borrow_asset_count": 0,
            "depth_counts": {},
        },
        "stable_borrow_pools": [],
        "non_stable_borrow_pools": [],
        "borrow_target_universe": None,
        "status": status,
        "status_message": messages.get(status, "该协议闪电贷可用资金池暂不可用"),
    }


def _route_only_protocol_view(protocol: str) -> dict[str, Any]:
    view = _empty_protocol_view(protocol, status="chain_protocol_not_supported")
    view["status_message"] = f"{_protocol_display_name(protocol)} 属于链路路由/原生执行轨道，不是可复用闪电贷可用资金池协议"
    return view


def _is_route_only_protocol(protocol: str) -> bool:
    return str(protocol or "").strip().lower() in ROUTE_ONLY_PROTOCOLS


__all__ = [
    "_as_float",
    "_empty_protocol_view",
    "_is_route_only_protocol",
    "_is_stable_symbol",
    "_protocol_display_name",
    "_route_only_protocol_view",
]
