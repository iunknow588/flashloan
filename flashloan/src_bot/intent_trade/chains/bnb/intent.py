"""BNB Chain quote-only intent assembly.

This module is deliberately isolated from the Avalanche unified executor. It
only validates a BNB static pool table and produces a shadow intent draft for a
separate BNB flash-loan/execution adapter that has not been deployed yet.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from intent_trade.chains.bnb import config as bnb
from intent_trade.chains.common.static_tables import load_chain_static_table


STATIC_TABLE_PATH = Path(__file__).resolve().parents[3] / "runtime" / "cache" / "bnb_arbitrage_pool_static_table.json"


def normalize_network(value: Any) -> str:
    key = str(value or "").strip().lower().replace("_", "-")
    aliases = {
        "bnb": "bnb",
        "bsc": "bnb",
        "bnb-chain": "bnb",
        "binance-smart-chain": "bnb",
        "56": "bnb",
    }
    return aliases.get(key, key)


def load_static_table(path: Path | None = None) -> dict[str, Any]:
    table_path = path or STATIC_TABLE_PATH
    return load_chain_static_table(table_path, network=bnb.NETWORK, chain_id=bnb.CHAIN_ID)


def _edge_for_hop(
    best_edges: dict[tuple[str, str], dict[str, Any]],
    token_in: str,
    token_out: str,
) -> dict[str, Any]:
    edge = best_edges.get(tuple(sorted((token_in, token_out))))
    if edge is None:
        raise ValueError(f"no high-quality BNB pool for {token_in}->{token_out}")
    return {
        "tokenIn": token_in,
        "tokenOut": token_out,
        "pool": edge["pool"],
        "dex": edge["dex"],
        "protocol": edge["protocol"],
        "router": edge.get("router"),
        "quoter": edge.get("quoter"),
        "fee": edge.get("fee"),
        "quality": edge.get("status"),
        "tvlUsd": edge.get("tvlUsd"),
        "tokenInAddress": bnb.TOKENS[token_in].address,
        "tokenOutAddress": bnb.TOKENS[token_out].address,
    }


def build_bnb_intent_draft(
    route_symbols: list[str] | tuple[str, ...],
    *,
    amount: str | float | int = "1000",
    static_table_path: Path | None = None,
    allowed_protocols: set[str] | None = None,
    allowed_statuses: set[str] | None = None,
) -> dict[str, Any]:
    route = [str(symbol or "").upper().strip() for symbol in route_symbols]
    if len(route) != 4 or route[0] != route[-1]:
        raise ValueError("BNB route must be a three-hop closed path such as USDT->BTCB->WBNB->USDT")
    if any(symbol not in bnb.TOKENS for symbol in route):
        unknown = [symbol for symbol in route if symbol not in bnb.TOKENS]
        raise ValueError(f"unknown BNB token symbol: {', '.join(unknown)}")

    table = load_static_table(static_table_path)
    pools = list(table["pools"])
    if allowed_protocols is not None:
        pools = [pool for pool in pools if str(pool.get("protocol") or "") in allowed_protocols]
    edge_statuses = allowed_statuses or {"recommended"}
    best_edges = bnb.best_edges_by_pair(pools, edge_statuses)
    hops = [_edge_for_hop(best_edges, route[index], route[index + 1]) for index in range(3)]
    return {
        "version": 1,
        "network": bnb.NETWORK,
        "chainId": bnb.CHAIN_ID,
        "mode": "shadow_quote_only",
        "executionEnabled": False,
        "broadcastEnabled": False,
        "routeSymbols": route,
        "borrowToken": {
            "symbol": route[0],
            "address": bnb.TOKENS[route[0]].address,
            "decimals": bnb.TOKENS[route[0]].decimals,
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
        "flashloan": {
            "status": "unverified",
            "reason": "BNB-specific flash-loan provider and executor adapter are not yet verified",
        },
        "nextStage": "live_router_quote_then_fork_simulation",
    }
