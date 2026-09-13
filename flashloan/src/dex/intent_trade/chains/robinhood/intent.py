"""Robinhood Chain quote-only DEX intent assembly.

Robinhood support starts in shadow mode. The module validates that the pool
table belongs to Robinhood Chain and builds a route draft from verified table
entries, without enabling broadcast or assuming any DEX deployment address.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from dex.intent_trade.chains.common.static_tables import load_chain_static_table
from dex.intent_trade.chains.robinhood import config as robinhood


STATIC_TABLE_PATH = (
    Path(__file__).resolve().parents[3] / "runtime" / "cache" / "robinhood_arbitrage_pool_static_table.json"
)


def normalize_network(value: Any) -> str:
    key = str(value or "").strip().lower().replace("_", "-")
    aliases = {
        "robinhood": "robinhood",
        "robinhood-chain": "robinhood",
        "robinhood-mainnet": "robinhood",
        str(robinhood.CHAIN_ID): "robinhood",
        "robinhood-testnet": "robinhood-testnet",
        "robinhood-sepolia": "robinhood-testnet",
        str(robinhood.TESTNET_CHAIN_ID): "robinhood-testnet",
    }
    return aliases.get(key, key)


def load_static_table(path: Path | None = None) -> dict[str, Any]:
    table_path = path or STATIC_TABLE_PATH
    return load_chain_static_table(table_path, network=robinhood.NETWORK, chain_id=robinhood.CHAIN_ID)


def _token_address_from_pool(pool: dict[str, Any], symbol: str) -> str:
    wanted = str(symbol or "").upper().strip()
    sides = (
        ("token0Symbol", "token0", "token0Address"),
        ("token1Symbol", "token1", "token1Address"),
    )
    for symbol_key, *address_keys in sides:
        if str(pool.get(symbol_key) or "").upper().strip() != wanted:
            continue
        for address_key in address_keys:
            value = str(pool.get(address_key) or "").strip()
            if value:
                return value
    configured = robinhood.TOKENS.get(wanted)
    return configured.address if configured else ""


def _edge_for_hop(
    best_edges: dict[tuple[str, str], dict[str, Any]],
    token_in: str,
    token_out: str,
) -> dict[str, Any]:
    edge = best_edges.get(tuple(sorted((token_in, token_out))))
    if edge is None:
        raise ValueError(f"no verified Robinhood pool for {token_in}->{token_out}")
    token_in_address = _token_address_from_pool(edge, token_in)
    token_out_address = _token_address_from_pool(edge, token_out)
    if not token_in_address or not token_out_address:
        raise ValueError(f"Robinhood pool for {token_in}->{token_out} is missing token addresses")
    return {
        "tokenIn": token_in,
        "tokenOut": token_out,
        "pool": edge["pool"],
        "dex": edge["dex"],
        "protocol": edge["protocol"],
        "factory": edge.get("factory"),
        "router": edge.get("router"),
        "quoter": edge.get("quoter"),
        "fee": edge.get("fee"),
        "quality": edge.get("status"),
        "tvlUsd": edge.get("tvlUsd"),
        "tokenInAddress": token_in_address,
        "tokenOutAddress": token_out_address,
    }


def build_robinhood_intent_draft(
    route_symbols: list[str] | tuple[str, ...],
    *,
    amount: str | float | int = "1000",
    static_table_path: Path | None = None,
    allowed_protocols: set[str] | None = None,
    allowed_statuses: set[str] | None = None,
) -> dict[str, Any]:
    route = [str(symbol or "").upper().strip() for symbol in route_symbols]
    if len(route) != 4 or route[0] != route[-1]:
        raise ValueError("Robinhood route must be a three-hop closed path such as WETH->USDC->TOKEN->WETH")
    if any(not symbol for symbol in route):
        raise ValueError("Robinhood route contains an empty token symbol")

    table = load_static_table(static_table_path)
    pools = list(table["pools"])
    if allowed_protocols is not None:
        pools = [pool for pool in pools if str(pool.get("protocol") or "") in allowed_protocols]
    edge_statuses = allowed_statuses or {"recommended"}
    best_edges = robinhood.best_edges_by_pair(pools, edge_statuses)
    hops = [_edge_for_hop(best_edges, route[index], route[index + 1]) for index in range(3)]
    return {
        "version": 1,
        "network": robinhood.NETWORK,
        "chainId": robinhood.CHAIN_ID,
        "mode": "shadow_quote_only",
        "executionEnabled": False,
        "broadcastEnabled": False,
        "routeSymbols": route,
        "borrowToken": {
            "symbol": route[0],
            "address": hops[0]["tokenInAddress"],
            "requestedAmount": str(amount),
        },
        "hops": hops,
        "staticTable": {
            "path": str(static_table_path or STATIC_TABLE_PATH),
            "generatedAt": table.get("generatedAt"),
            "scannedDexes": table.get("scannedDexes") or [],
            "allowedProtocols": sorted(allowed_protocols) if allowed_protocols is not None else None,
            "allowedStatuses": sorted(edge_statuses),
        },
        "gasPolicy": {
            "nativeToken": robinhood.NATIVE_SYMBOL,
            "chainWideFreeGas": False,
            "accountAbstractionOptional": True,
            "reason": "AA/paymaster sponsorship is optional and must not bypass net-profit checks",
        },
        "flashloan": {
            "status": "unverified",
            "reason": "Robinhood flash-loan provider and executor adapter are not yet verified",
        },
        "nextStage": "live_router_quote_then_fork_simulation",
    }

