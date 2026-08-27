from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

from intent_trade.chains.robinhood.chain_test_policy import ROBINHOOD_CHAIN_ID, ROBINHOOD_NETWORK


DEFAULT_THRESHOLDS = {
    "minMarketPools": 50,
    "minLiquidPools": 25,
    "minObservablePairs": 20,
    "minStaticCandidates": 10,
    "minWethDegree": 10,
    "minUsdgDegree": 5,
    "minBridgeTriangles": 1,
    "minMultiPoolPairs": 3,
}


def _as_dict(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: object) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: object) -> str:
    return str(value or "").strip()


def _symbol(value: object) -> str:
    return _text(value).upper()


def _number(value: object) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return 0.0
    return parsed if parsed == parsed and parsed not in (float("inf"), float("-inf")) else 0.0


def _chain_id(value: object, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _is_mainnet_scope(payload: dict[str, Any]) -> bool:
    return (
        _text(payload.get("network") or ROBINHOOD_NETWORK).lower() == ROBINHOOD_NETWORK
        and _chain_id(payload.get("chainId") or payload.get("chain_id"), ROBINHOOD_CHAIN_ID) == ROBINHOOD_CHAIN_ID
    )


def _scope_report(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "expectedNetwork": ROBINHOOD_NETWORK,
        "expectedChainId": ROBINHOOD_CHAIN_ID,
        "actualNetwork": _text(payload.get("network") or ROBINHOOD_NETWORK).lower(),
        "actualChainId": _chain_id(payload.get("chainId") or payload.get("chain_id"), ROBINHOOD_CHAIN_ID),
    }


def _pool_symbol_pair(pool: dict[str, Any]) -> tuple[str, str] | None:
    token0 = _symbol(pool.get("token0Symbol"))
    token1 = _symbol(pool.get("token1Symbol"))
    if not token0 or not token1 or token0 == token1:
        return None
    return tuple(sorted((token0, token1)))


def _is_market_observable(pool: dict[str, Any]) -> bool:
    return pool.get("quality") == "pass" or pool.get("status") == "observable"


def _market_pool_supply(market_cache: dict[str, Any]) -> dict[str, Any]:
    pools = [pool for pool in _as_list(market_cache.get("pools")) if isinstance(pool, dict)]
    observable = [pool for pool in pools if _is_market_observable(pool)]
    pair_groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    adjacency: dict[str, set[str]] = defaultdict(set)
    for pool in observable:
        pair = _pool_symbol_pair(pool)
        if pair is None:
            continue
        pair_groups[pair].append(pool)
        left, right = pair
        adjacency[left].add(right)
        adjacency[right].add(left)

    multi_pool_pairs = {
        ":".join(pair): sorted(group, key=lambda row: -_number(row.get("tvlUsd")))
        for pair, group in pair_groups.items()
        if len(group) >= 2
    }
    bridge_triangles: list[dict[str, Any]] = []
    if "WETH" in adjacency and "USDG" in adjacency["WETH"]:
        for symbol in sorted((adjacency["WETH"] & adjacency["USDG"]) - {"WETH", "USDG"}):
            bridge_triangles.append(
                {
                    "routeSymbols": ["USDG", symbol, "WETH", "USDG"],
                    "connector": symbol,
                }
            )
    protocol_counts = Counter(_text(pool.get("protocol")) or "unknown" for pool in observable)
    top_pools = [
        {
            "symbolPair": pool.get("symbolPair"),
            "protocol": pool.get("protocol"),
            "pool": pool.get("pool"),
            "tvlUsd": pool.get("tvlUsd"),
            "volumeH24Usd": pool.get("volumeH24Usd"),
        }
        for pool in sorted(observable, key=lambda row: (-_number(row.get("tvlUsd")), -_number(row.get("volumeH24Usd"))))[:12]
    ]
    return {
        "poolCount": len(pools),
        "observablePoolCount": len(observable),
        "liquidPoolCount": sum(1 for pool in observable if _number(pool.get("tvlUsd")) >= 5_000),
        "observablePairCount": len(pair_groups),
        "protocolCounts": dict(sorted(protocol_counts.items())),
        "multiPoolPairCount": len(multi_pool_pairs),
        "multiPoolPairs": sorted(multi_pool_pairs)[:25],
        "bridgeTriangleCount": len(bridge_triangles),
        "bridgeTriangles": bridge_triangles[:25],
        "tokenDegree": {
            symbol: len(neighbours)
            for symbol, neighbours in sorted(adjacency.items(), key=lambda item: (-len(item[1]), item[0]))[:25]
        },
        "topPools": top_pools,
    }


def _static_pool_supply(static_table: dict[str, Any]) -> dict[str, Any]:
    pools = [pool for pool in _as_list(static_table.get("pools")) if isinstance(pool, dict)]
    candidates = [item for item in _as_list(static_table.get("candidates")) if isinstance(item, dict)]
    return {
        "poolCount": len(pools),
        "observablePoolCount": sum(1 for pool in pools if pool.get("status") == "observable"),
        "candidateCount": len(candidates),
        "candidateKindCounts": dict(Counter(_text(item.get("route_kind")) or "unknown" for item in candidates)),
        "sampleRoutes": [
            {
                "routeKind": item.get("route_kind"),
                "routeKey": item.get("route_key"),
                "routeSymbols": item.get("route_symbols"),
            }
            for item in candidates[:12]
        ],
    }


def summarize_robinhood_candidate_pool_supply(
    *,
    market_cache: dict[str, Any],
    static_table: dict[str, Any] | None = None,
    thresholds: dict[str, int] | None = None,
) -> dict[str, Any]:
    limits = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
    scope = _scope_report(market_cache)
    if not _is_mainnet_scope(market_cache):
        return {
            "schemaVersion": "robinhood-candidate-pool-supply-v1",
            "network": scope["actualNetwork"],
            "chainId": scope["actualChainId"],
            "scope": scope,
            "thresholds": limits,
            "market": _market_pool_supply({"pools": []}),
            "staticDiscovery": _static_pool_supply({}),
            "checks": {
                "mainnetScope": False,
                "marketPoolsEnough": False,
                "liquidPoolsEnough": False,
                "observablePairsEnough": False,
                "staticCandidatesEnough": False,
                "wethHubEnough": False,
                "usdgHubEnough": False,
                "bridgeTrianglesEnough": False,
                "multiPoolPairsEnough": False,
            },
            "sufficientForMonitoring": False,
            "sufficientForRouteSearch": False,
            "sufficientForExecution": False,
            "executionEnabled": False,
            "broadcastEnabled": False,
            "blockingReasons": ["robinhood_mainnet_scope_required"],
            "nextRequiredActions": ["use_robinhood_testnet_pool_evidence_gate_for_chain_46630"],
        }
    market = _market_pool_supply(market_cache)
    static = _static_pool_supply(static_table or {})
    blockers: list[str] = []

    checks = {
        "marketPoolsEnough": market["poolCount"] >= limits["minMarketPools"],
        "liquidPoolsEnough": market["liquidPoolCount"] >= limits["minLiquidPools"],
        "observablePairsEnough": market["observablePairCount"] >= limits["minObservablePairs"],
        "staticCandidatesEnough": static["candidateCount"] >= limits["minStaticCandidates"],
        "wethHubEnough": market["tokenDegree"].get("WETH", 0) >= limits["minWethDegree"],
        "usdgHubEnough": market["tokenDegree"].get("USDG", 0) >= limits["minUsdgDegree"],
        "bridgeTrianglesEnough": market["bridgeTriangleCount"] >= limits["minBridgeTriangles"],
        "multiPoolPairsEnough": market["multiPoolPairCount"] >= limits["minMultiPoolPairs"],
    }
    for key, ok in checks.items():
        if not ok:
            blockers.append(key)

    sufficient_for_monitoring = all(
        checks[key]
        for key in (
            "marketPoolsEnough",
            "liquidPoolsEnough",
            "observablePairsEnough",
            "wethHubEnough",
            "usdgHubEnough",
        )
    )
    sufficient_for_route_search = sufficient_for_monitoring and (
        checks["bridgeTrianglesEnough"] or checks["multiPoolPairsEnough"] or checks["staticCandidatesEnough"]
    )
    sufficient_for_execution = False

    return {
        "schemaVersion": "robinhood-candidate-pool-supply-v1",
        "network": market_cache.get("network") or "robinhood",
        "chainId": market_cache.get("chainId") or 4663,
        "scope": scope,
        "thresholds": limits,
        "market": market,
        "staticDiscovery": static,
        "checks": checks,
        "sufficientForMonitoring": sufficient_for_monitoring,
        "sufficientForRouteSearch": sufficient_for_route_search,
        "sufficientForExecution": sufficient_for_execution,
        "executionEnabled": False,
        "broadcastEnabled": False,
        "blockingReasons": blockers,
        "nextRequiredActions": [
            action
            for action, needed in [
                ("expand_market_pool_sources", not sufficient_for_monitoring),
                ("refresh_onchain_static_discovery", not checks["staticCandidatesEnough"]),
                ("run_live_quote_loop_until_min_profit_candidate", sufficient_for_route_search),
                ("keep_broadcast_disabled", True),
            ]
            if needed
        ],
    }


__all__ = ["summarize_robinhood_candidate_pool_supply"]
