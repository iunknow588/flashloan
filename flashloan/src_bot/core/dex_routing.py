from __future__ import annotations

import os
from typing import Any

from core.market_config import DEFAULT_AVALANCHE_ROUTER_ADDRESS, MARKET_TYPE_EXECUTION_RAIL, liquidation_market_config_for


def dex_router_context_for(chain_selection: dict | None = None) -> dict[str, Any]:
    fallback_router = os.getenv("DEX_ROUTER_ADDRESS", DEFAULT_AVALANCHE_ROUTER_ADDRESS).strip()
    context = {
        "source": "env",
        "router_address": fallback_router,
        "rpc_urls": [],
        "market_id": None,
        "chain_id": None,
        "network": None,
        "protocol": None,
        "market_type": None,
        "route_family": None,
        "observation_only": True,
    }
    if not chain_selection:
        return context
    try:
        config = liquidation_market_config_for(
            market_id=str(chain_selection.get("market_id") or "").strip() or None,
            chain_id=chain_selection.get("chain_id"),
            feature="dex_arbitrage",
        )
    except Exception as exc:
        context["source"] = "fallback"
        context["error"] = str(exc)
        return context
    router_address = str(config.dex_router_address or "").strip() or fallback_router
    return {
        "source": "market_config",
        "router_address": router_address,
        "rpc_urls": list(config.rpc_urls or []),
        "rpc_url_count": len(config.rpc_urls or ()),
        "market_id": config.market_id,
        "chain_id": config.chain_id,
        "network": config.network,
        "protocol": config.protocol,
        "market_type": config.market_type,
        "route_family": "execution_rail" if config.market_type == MARKET_TYPE_EXECUTION_RAIL else config.protocol,
        "observation_only": config.observation_only,
        "display_name": config.display_name,
    }


def dex_router_name_for(router_context: dict | None) -> str:
    network = str((router_context or {}).get("network") or "").strip().lower()
    market_type = str((router_context or {}).get("market_type") or "").strip().lower()
    if market_type == MARKET_TYPE_EXECUTION_RAIL or network == "cow":
        return "CoW Execution Rail"
    if network == "bnb":
        return "PancakeSwap V2"
    if network == "ethereum":
        return "Uniswap V2"
    if network == "polygon":
        return "QuickSwap V2"
    if network == "avalanche":
        return "Trader Joe V2"
    if network == "robinhood":
        return "Robinhood Shadow DEX"
    return "Trader Joe V2"


def dex_quote_rpc_urls_for(router_context: dict | None, fallback_urls: list[str] | tuple[str, ...] = ()) -> list[str]:
    urls = [str(url).strip() for url in (router_context or {}).get("rpc_urls") or [] if str(url).strip()]
    fallback = [str(url).strip() for url in fallback_urls if str(url).strip()]
    return urls or fallback

