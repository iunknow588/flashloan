"""Runtime trade and route-group candidate assembly for unified direct execution."""

from __future__ import annotations

import json
from typing import Any


def _impl():
    from intent_trade import direct_impl

    return direct_impl


def normalize_runtime_pool(pool: Any) -> dict[str, Any]:
    impl = _impl()
    if not isinstance(pool, dict):
        raise ValueError("runtime pool must be an object")
    adapter_kind = int(pool.get("adapterKind", pool.get("adapter_kind", 0)) or 0)
    pool_address = impl._normalize_pair_address(pool.get("pool"))
    return {"adapterKind": adapter_kind, "pool": pool_address}


def normalize_runtime_trade(trade: Any, trade_index: int) -> dict[str, Any]:
    impl = _impl()
    if not isinstance(trade, dict):
        raise ValueError("runtime trade must be an object")
    token_x = impl._normalize_pair_address(trade.get("tokenX") or trade.get("token_x"))
    token_y = impl._normalize_pair_address(trade.get("tokenY") or trade.get("token_y"))
    if not token_x or not token_y:
        raise ValueError(f"runtime trade {trade_index} tokenX/tokenY must be valid addresses")
    pools = trade.get("pools") or trade.get("candidatePools") or trade.get("candidate_pools")
    if not isinstance(pools, list):
        raise ValueError(f"runtime trade {trade_index} pools must be a list")
    normalized_pools = [{"adapterKind": 0, "pool": ""} for _ in range(impl.MAX_RUNTIME_POOL_SCAN)]
    for index, pool in enumerate(pools[: impl.MAX_RUNTIME_POOL_SCAN]):
        normalized_pools[index] = normalize_runtime_pool(pool)
    normalized = {
        "tradeIndex": int(trade.get("tradeIndex", trade.get("trade_index", trade_index))),
        "tokenX": token_x,
        "tokenY": token_y,
        "pools": normalized_pools,
    }
    for key in (
        "strategyStatus",
        "strategy_status",
        "strategyStage",
        "strategy_stage",
        "routeSymbols",
        "route_symbols",
        "routeKey",
        "route_key",
        "spreadScore",
    ):
        if key in trade:
            normalized[key] = trade[key]
    return normalized


def cache_entry_symbols(entry: dict[str, Any]) -> tuple[set[str], set[str]]:
    impl = _impl()
    left = entry.get("tokenX_symbol") or entry.get("token_x_symbol") or entry.get("token0_symbol") or entry.get("base_symbol")
    right = entry.get("tokenY_symbol") or entry.get("token_y_symbol") or entry.get("token1_symbol") or entry.get("quote_symbol")
    return impl._symbol_aliases(str(left or "")), impl._symbol_aliases(str(right or ""))


def cache_entry_tokens(entry: dict[str, Any]) -> tuple[str, str]:
    impl = _impl()
    token_x = impl._normalize_pair_address(entry.get("tokenX") or entry.get("token_x") or entry.get("token0") or entry.get("base_token"))
    token_y = impl._normalize_pair_address(entry.get("tokenY") or entry.get("token_y") or entry.get("token1") or entry.get("quote_token"))
    return token_x, token_y


def cache_pool_candidates_for_pair(cache: dict[str, Any], x_symbol: str, y_symbol: str) -> tuple[str, str, list[dict[str, Any]]]:
    impl = _impl()
    wanted_x = impl._symbol_aliases(x_symbol)
    wanted_y = impl._symbol_aliases(y_symbol)
    token_x = ""
    token_y = ""
    pools: list[dict[str, Any]] = []
    entries = cache.get("pools") if isinstance(cache.get("pools"), list) else []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        left_symbols, right_symbols = cache_entry_symbols(entry)
        entry_x, entry_y = cache_entry_tokens(entry)
        if not entry_x or not entry_y:
            continue
        same_order = bool(wanted_x & left_symbols and wanted_y & right_symbols)
        reverse_order = bool(wanted_x & right_symbols and wanted_y & left_symbols)
        if not same_order and not reverse_order:
            continue
        token_x = entry_x if same_order else entry_y
        token_y = entry_y if same_order else entry_x
        nested = entry.get("preferredPools") if isinstance(entry.get("preferredPools"), list) else None
        if not nested:
            nested = entry.get("qualityPools") if isinstance(entry.get("qualityPools"), list) else None
        if not nested:
            nested = entry.get("pools") if isinstance(entry.get("pools"), list) else [entry]
        is_quality_annotated = any(
            isinstance(pool, dict) and str(pool.get("quality") or "").strip()
            for pool in (
                entry.get("preferredPools")
                if isinstance(entry.get("preferredPools"), list)
                else entry.get("qualityPools")
                if isinstance(entry.get("qualityPools"), list)
                else entry.get("pools")
                if isinstance(entry.get("pools"), list)
                else []
            )
        )
        for pool in nested:
            if not isinstance(pool, dict):
                continue
            adapter_kind = int(
                pool.get("adapterKind", pool.get("adapter_kind", entry.get("adapterKind", entry.get("adapter_kind", 0)))) or 0
            )
            pool_address = impl._normalize_pair_address(pool.get("pool") or pool.get("pool_address") or pool.get("address"))
            if adapter_kind != 1 or not pool_address:
                continue
            if (
                is_quality_annotated
                and not isinstance(entry.get("preferredPools"), list)
                and str(pool.get("quality") or "").lower() not in {"pass", ""}
            ):
                continue
            candidate = {"adapterKind": 1, "pool": pool_address}
            if candidate not in pools:
                pools.append(candidate)
            if len(pools) >= impl.MAX_RUNTIME_POOL_SCAN:
                return token_x, token_y, pools
    return token_x, token_y, pools


def runtime_strategy_from_env() -> str:
    impl = _impl()
    strategy = impl._env("TRIANGULAR_DIRECT_CANDIDATE_STRATEGY", default="expanded").strip().lower().replace("-", "_")
    aliases = {
        "abc": "expanded",
        "abc_auto": "expanded",
        "top_bottom": "expanded",
        "xy_only": "pair_only",
        "legacy": "pair_only",
    }
    strategy = aliases.get(strategy, strategy)
    return strategy if strategy in {"expanded", "pair_only"} else "expanded"


def trade_key(token_x: str, token_y: str) -> tuple[str, str]:
    return token_x.lower(), token_y.lower()


def runtime_trade_with_metadata(
    *,
    trade_index: int,
    token_x: str,
    token_y: str,
    pools: list[dict[str, Any]],
    strategy_status: int,
    strategy_stage: str,
    route_symbols: list[str],
    route_key: str = "",
    spread: float = 0.0,
) -> dict[str, Any]:
    trade = normalize_runtime_trade(
        {
            "tradeIndex": trade_index,
            "tokenX": token_x,
            "tokenY": token_y,
            "pools": pools,
        },
        trade_index,
    )
    trade["strategyStatus"] = int(strategy_status)
    trade["strategy_status"] = int(strategy_status)
    trade["strategyStage"] = strategy_stage
    trade["strategy_stage"] = strategy_stage
    trade["routeSymbols"] = route_symbols
    trade["route_symbols"] = route_symbols
    trade["routeKey"] = route_key or f"{token_x.lower()}:{token_y.lower()}"
    trade["route_key"] = trade["routeKey"]
    trade["spreadScore"] = spread
    return trade


def runtime_trade_status(trade: dict[str, Any] | None) -> int | None:
    if not isinstance(trade, dict):
        return None
    raw = trade.get("strategyStatus", trade.get("strategy_status"))
    try:
        return int(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None


def runtime_trade_route_key(trade: dict[str, Any] | None) -> str:
    if not isinstance(trade, dict):
        return ""
    value = trade.get("routeKey", trade.get("route_key"))
    if value:
        return str(value).strip().lower()
    route = trade.get("routeSymbols", trade.get("route_symbols"))
    if isinstance(route, (list, tuple)) and len(route) >= 3:
        return "->".join(str(item or "").strip().upper() for item in route)
    return ""


def runtime_trades_from_market_state(
    market_state: dict[str, Any],
    *,
    cache: dict[str, Any] | None = None,
    side_limit: int | None = None,
    trade_limit: int | None = None,
    include_stable_targets: bool = False,
) -> list[dict[str, Any]]:
    impl = _impl()
    if not isinstance(market_state, dict):
        return []
    pool_cache = cache if isinstance(cache, dict) else impl._load_runtime_pool_cache()
    side_limit = impl.RUNTIME_TOP_BOTTOM_LIMIT if side_limit is None else side_limit
    top_rows = list(market_state.get("cow_top") or market_state.get("top") or [])[: max(1, int(side_limit))]
    bottom_rows = list(market_state.get("cow_bottom") or market_state.get("bottom") or [])[: max(1, int(side_limit))]
    network = str(market_state.get("network") or "").strip().lower()
    usdc_address = impl._usdc_address_for_network(network, pool_cache)
    candidates: list[tuple[float, str, list[dict[str, Any]]]] = []
    seen_route_keys: set[str] = set()
    for top in top_rows:
        x_symbol = impl._base_symbol_from_row(top)
        x_change = impl._float_or_none(top.get("change_percent")) if isinstance(top, dict) else None
        if not x_symbol or (impl._is_stable_exercise_symbol(x_symbol) and not include_stable_targets):
            continue
        for bottom in bottom_rows:
            y_symbol = impl._base_symbol_from_row(bottom)
            y_change = impl._float_or_none(bottom.get("change_percent")) if isinstance(bottom, dict) else None
            if (
                not y_symbol
                or x_symbol == y_symbol
                or (impl._is_stable_exercise_symbol(y_symbol) and not include_stable_targets)
            ):
                continue
            ux_x, ux_y, ux_pools = cache_pool_candidates_for_pair(pool_cache, "USDC", x_symbol)
            uy_x, uy_y, uy_pools = cache_pool_candidates_for_pair(pool_cache, "USDC", y_symbol)
            xy_x, xy_y, xy_pools = cache_pool_candidates_for_pair(pool_cache, x_symbol, y_symbol)
            yx_x, yx_y, yx_pools = cache_pool_candidates_for_pair(pool_cache, y_symbol, x_symbol)
            if (
                not usdc_address
                or len(ux_pools) < 1
                or len(uy_pools) < 1
                or len(xy_pools) < 1
                or not ux_x
                or not ux_y
                or not uy_x
                or not uy_y
                or not xy_x
                or not xy_y
                or not yx_x
                or not yx_y
            ):
                continue
            spread = (x_change - y_change) if x_change is not None and y_change is not None else 0.0
            route_key = f"{x_symbol.upper()}:{y_symbol.upper()}"
            if route_key in seen_route_keys:
                continue
            seen_route_keys.add(route_key)
            candidates.append(
                (
                    abs(spread),
                    route_key,
                    [
                        runtime_trade_with_metadata(
                            trade_index=0,
                            token_x=ux_x,
                            token_y=ux_y,
                            pools=ux_pools,
                            strategy_status=1,
                            strategy_stage="status1_usdc_to_x_cross_pool",
                            route_symbols=["USDC", x_symbol, "USDC"],
                            route_key=route_key,
                            spread=spread,
                        ),
                        runtime_trade_with_metadata(
                            trade_index=1,
                            token_x=uy_x,
                            token_y=uy_y,
                            pools=uy_pools,
                            strategy_status=2,
                            strategy_stage="status2_usdc_to_y_cross_pool",
                            route_symbols=["USDC", y_symbol, "USDC"],
                            route_key=route_key,
                            spread=spread,
                        ),
                        runtime_trade_with_metadata(
                            trade_index=2,
                            token_x=xy_x,
                            token_y=xy_y,
                            pools=xy_pools,
                            strategy_status=3,
                            strategy_stage="status3_xy_cross_pool_or_triangular",
                            route_symbols=["USDC", x_symbol, y_symbol, "USDC"],
                            route_key=route_key,
                            spread=spread,
                        ),
                        runtime_trade_with_metadata(
                            trade_index=3,
                            token_x=yx_x,
                            token_y=yx_y,
                            pools=yx_pools,
                            strategy_status=5,
                            strategy_stage="status5_reverse_triangular_fallback",
                            route_symbols=["USDC", y_symbol, x_symbol, "USDC"],
                            route_key=route_key,
                            spread=-spread,
                        ),
                    ],
                )
            )
    candidates.sort(key=lambda item: (-item[0], item[1]))
    if not candidates:
        return []
    limit = impl.DEFAULT_RUNTIME_TRADE_SCAN if trade_limit is None else int(trade_limit)
    limit = max(1, min(limit, impl.MAX_RUNTIME_TRADE_SCAN))
    return candidates[0][2][:min(limit, impl.MAX_UNIFIED_ROUTE_TRADE_INPUTS)]


def cache_usdc_supported_symbols(cache: dict[str, Any]) -> list[str]:
    impl = _impl()
    symbols: set[str] = set()
    entries = cache.get("pools") if isinstance(cache.get("pools"), list) else []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        left = str(
            entry.get("tokenX_symbol")
            or entry.get("token_x_symbol")
            or entry.get("token0_symbol")
            or entry.get("base_symbol")
            or ""
        )
        right = str(
            entry.get("tokenY_symbol")
            or entry.get("token_y_symbol")
            or entry.get("token1_symbol")
            or entry.get("quote_symbol")
            or ""
        )
        left_symbols, right_symbols = cache_entry_symbols(entry)
        if "USDC" in left_symbols:
            symbol = impl._base_symbol_from_row(right)
            if symbol and "USDC" not in impl._symbol_aliases(symbol):
                symbols.add(symbol)
        if "USDC" in right_symbols:
            symbol = impl._base_symbol_from_row(left)
            if symbol and "USDC" not in impl._symbol_aliases(symbol):
                symbols.add(symbol)
    return sorted(symbols)


def market_change_by_symbol(market_state: dict[str, Any]) -> dict[str, float]:
    impl = _impl()
    changes: dict[str, float] = {}
    for key in ("cow_top", "top", "cow_bottom", "bottom"):
        rows = market_state.get(key) if isinstance(market_state, dict) else None
        if not isinstance(rows, list):
            continue
        for row in rows:
            symbol = impl._base_symbol_from_row(row)
            change = impl._float_or_none(row.get("change_percent")) if isinstance(row, dict) else None
            if symbol and change is not None:
                changes[symbol] = change
    return changes


def runtime_route_groups_from_market_state(
    market_state: dict[str, Any] | None,
    *,
    cache: dict[str, Any] | None = None,
    group_limit: int | None = None,
    include_single_pair_diagnostic: bool = False,
    include_stable_targets: bool = False,
) -> list[dict[str, Any]]:
    impl = _impl()
    context = market_state if isinstance(market_state, dict) else {}
    pool_cache = cache if isinstance(cache, dict) else impl._load_runtime_pool_cache()
    usdc_address = impl._usdc_address_for_network(str(context.get("network") or ""), pool_cache)
    changes = market_change_by_symbol(context)
    usdc_pairs: dict[str, tuple[str, str, list[dict[str, Any]]]] = {}

    for symbol in cache_usdc_supported_symbols(pool_cache):
        token_x, token_y, pools = cache_pool_candidates_for_pair(pool_cache, "USDC", symbol)
        if (
            not token_x
            or not token_y
            or len(pools) < 1
            or usdc_address.lower() not in {token_x.lower(), token_y.lower()}
        ):
            continue
        usdc_pairs[symbol] = (token_x, token_y, pools)

    groups: list[dict[str, Any]] = []
    if include_single_pair_diagnostic:
        for symbol, (token_x, token_y, pools) in usdc_pairs.items():
            if impl._is_stable_exercise_symbol(symbol) and not include_stable_targets:
                continue
            score = abs(changes.get(symbol, 0.0))
            groups.append(
                {
                    "routeKey": f"USDC:{symbol}",
                    "routeKind": "usdc_cross_pool_diagnostic",
                    "exerciseTargetPolicy": "single-pair-diagnostic",
                    "score": score,
                    "trades": [
                        runtime_trade_with_metadata(
                            trade_index=0,
                            token_x=token_x,
                            token_y=token_y,
                            pools=pools,
                            strategy_status=1,
                            strategy_stage="status1_usdc_cross_pool_diagnostic",
                            route_symbols=["USDC", symbol, "USDC"],
                            route_key=f"USDC:{symbol}",
                            spread=score,
                        )
                    ],
                }
            )

    symbols = sorted(usdc_pairs)
    for left_index, x_symbol in enumerate(symbols):
        if impl._is_stable_exercise_symbol(x_symbol) and not include_stable_targets:
            continue
        for y_symbol in symbols[left_index + 1 :]:
            if impl._is_stable_exercise_symbol(y_symbol) and not include_stable_targets:
                continue
            ux_x, ux_y, ux_pools = usdc_pairs[x_symbol]
            uy_x, uy_y, uy_pools = usdc_pairs[y_symbol]
            xy_x, xy_y, xy_pools = cache_pool_candidates_for_pair(pool_cache, x_symbol, y_symbol)
            if not xy_x or not xy_y or len(xy_pools) < 1:
                continue

            route_key = f"{x_symbol}:{y_symbol}"
            score = abs(changes.get(x_symbol, 0.0) - changes.get(y_symbol, 0.0))
            trades = [
                runtime_trade_with_metadata(
                    trade_index=0,
                    token_x=ux_x,
                    token_y=ux_y,
                    pools=ux_pools,
                    strategy_status=1,
                    strategy_stage="status1_usdc_to_x_cross_pool",
                    route_symbols=["USDC", x_symbol, "USDC"],
                    route_key=route_key,
                    spread=score,
                ),
                runtime_trade_with_metadata(
                    trade_index=1,
                    token_x=uy_x,
                    token_y=uy_y,
                    pools=uy_pools,
                    strategy_status=2,
                    strategy_stage="status2_usdc_to_y_cross_pool",
                    route_symbols=["USDC", y_symbol, "USDC"],
                    route_key=route_key,
                    spread=score,
                ),
                runtime_trade_with_metadata(
                    trade_index=2,
                    token_x=xy_x,
                    token_y=xy_y,
                    pools=xy_pools,
                    strategy_status=3,
                    strategy_stage="status3_xy_cross_pool_or_triangular",
                    route_symbols=["USDC", x_symbol, y_symbol, "USDC"],
                    route_key=route_key,
                    spread=score,
                ),
            ]
            yx_x, yx_y, yx_pools = cache_pool_candidates_for_pair(pool_cache, y_symbol, x_symbol)
            if yx_x and yx_y and len(yx_pools) >= 1:
                trades.append(
                    runtime_trade_with_metadata(
                        trade_index=3,
                        token_x=yx_x,
                        token_y=yx_y,
                        pools=yx_pools,
                        strategy_status=5,
                        strategy_stage="status5_reverse_triangular_fallback",
                        route_symbols=["USDC", y_symbol, x_symbol, "USDC"],
                        route_key=route_key,
                        spread=-score,
                    )
                )
            groups.append(
                {
                    "routeKey": route_key,
                    "routeKind": "usdc_triangular",
                    "exerciseTargetPolicy": (
                        "stable-target-diagnostic"
                        if impl._is_stable_exercise_symbol(x_symbol) or impl._is_stable_exercise_symbol(y_symbol)
                        else "non-stable-pair"
                    ),
                    "score": score,
                    "trades": trades,
                }
            )

    groups.sort(key=lambda item: (-float(item["score"]), item["routeKind"], item["routeKey"]))
    impl._attach_registry_token_risk_metadata(
        groups,
        network=str(context.get("network") or pool_cache.get("network") or "avalanche"),
    )
    if group_limit is None or int(group_limit) <= 0:
        return groups
    return groups[: max(1, int(group_limit))]


def runtime_route_group_limit(
    protocol: dict[str, Any],
    opportunity: dict[str, Any] | None,
) -> int:
    impl = _impl()
    configured = impl._first_value(
        protocol,
        opportunity,
        names=("runtime_route_group_limit", "route_group_scan_limit", "max_route_groups"),
    ) or impl._env("TRIANGULAR_RUNTIME_ROUTE_GROUP_LIMIT", default=str(impl.DEFAULT_RUNTIME_ROUTE_GROUP_SCAN))
    try:
        value = int(configured)
    except (TypeError, ValueError):
        value = impl.DEFAULT_RUNTIME_ROUTE_GROUP_SCAN
    if value <= 0:
        return 0
    return min(max(1, value), impl.MAX_RUNTIME_ROUTE_GROUP_SCAN)


def runtime_route_group_candidates(
    protocol: dict[str, Any],
    opportunity: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    impl = _impl()
    limit = runtime_route_group_limit(protocol, opportunity)
    for payload in (protocol, opportunity):
        if not isinstance(payload, dict) or not isinstance(payload.get("runtime_route_groups"), list):
            continue
        groups: list[dict[str, Any]] = []
        for index, source in enumerate(payload["runtime_route_groups"]):
            trades_source = source.get("trades") if isinstance(source, dict) else source
            if not isinstance(trades_source, list):
                continue
            trades = [normalize_runtime_trade(trade, trade_index) for trade_index, trade in enumerate(trades_source)]
            if trades:
                groups.append(
                    {
                        "routeKey": str(source.get("routeKey") or f"group:{index}") if isinstance(source, dict) else f"group:{index}",
                        "routeKind": str(source.get("routeKind") or "provided") if isinstance(source, dict) else "provided",
                        "score": float(source.get("score") or 0.0) if isinstance(source, dict) else 0.0,
                        "tokenRisks": (
                            source.get("tokenRisks", source.get("token_risks"))
                            if isinstance(source, dict)
                            else None
                        ),
                        "slippageRiskPenaltyUsdc": (
                            source.get("slippageRiskPenaltyUsdc", source.get("slippage_risk_penalty_usdc"))
                            if isinstance(source, dict)
                            else None
                        ),
                        "routeCorrelationPenaltyUsdc": (
                            source.get(
                                "routeCorrelationPenaltyUsdc",
                                source.get("route_correlation_penalty_usdc"),
                            )
                            if isinstance(source, dict)
                            else None
                        ),
                        "trades": trades,
                    }
                )
        return groups if limit == 0 else groups[:limit]

    for payload in (protocol, opportunity):
        if not isinstance(payload, dict):
            continue
        for key in ("runtime_trades", "candidate_trades", "trades"):
            if isinstance(payload.get(key), list):
                trades = [normalize_runtime_trade(trade, index) for index, trade in enumerate(payload[key])]
                return [{
                    "routeKey": "provided",
                    "routeKind": "provided",
                    "score": 0.0,
                    "tokenRisks": payload.get("tokenRisks", payload.get("token_risks")),
                    "slippageRiskPenaltyUsdc": payload.get(
                        "slippageRiskPenaltyUsdc",
                        payload.get("slippage_risk_penalty_usdc"),
                    ),
                    "routeCorrelationPenaltyUsdc": payload.get(
                        "routeCorrelationPenaltyUsdc",
                        payload.get("route_correlation_penalty_usdc"),
                    ),
                    "trades": trades,
                }]

    env_value = impl._env("TRIANGULAR_RUNTIME_TRADES_JSON")
    if env_value:
        source = json.loads(env_value)
        if not isinstance(source, list):
            raise ValueError("runtime trades must be a list")
        return [{
            "routeKey": "environment",
            "routeKind": "provided",
            "score": 0.0,
            "trades": [normalize_runtime_trade(trade, index) for index, trade in enumerate(source)],
        }]

    market_state = market_state_from_context(protocol, opportunity)
    return runtime_route_groups_from_market_state(
        market_state,
        group_limit=limit,
        include_single_pair_diagnostic=impl._allow_single_pair_diagnostic(protocol, opportunity),
        include_stable_targets=impl._allow_stable_exercise_targets(protocol, opportunity),
    )


def market_state_from_context(protocol: dict[str, Any], opportunity: dict[str, Any] | None) -> dict[str, Any] | None:
    for source in (opportunity, protocol):
        if not isinstance(source, dict):
            continue
        market_state = source.get("market_state")
        if isinstance(market_state, dict):
            return market_state
        if isinstance(source.get("top"), list) or isinstance(source.get("bottom"), list):
            return source
    return None


def runtime_trade_candidates(
    protocol: dict[str, Any],
    opportunity: dict[str, Any] | None = None,
    *,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    impl = _impl()
    limit = impl.DEFAULT_RUNTIME_TRADE_SCAN if limit is None else limit
    limit = max(1, min(int(limit), impl.MAX_OFFCHAIN_RUNTIME_CANDIDATE_SCAN))
    source = None
    for payload in (protocol, opportunity):
        if not isinstance(payload, dict):
            continue
        for key in ("runtime_trades", "candidate_trades", "trades"):
            if isinstance(payload.get(key), list):
                source = payload[key]
                break
        if source is not None:
            break
    if source is None:
        env_value = impl._env("TRIANGULAR_RUNTIME_TRADES_JSON")
        if not env_value:
            market_state = market_state_from_context(protocol, opportunity)
            return (
                runtime_trades_from_market_state(
                    market_state,
                    trade_limit=limit,
                    include_stable_targets=impl._allow_stable_exercise_targets(protocol, opportunity),
                )
                if market_state
                else []
            )
        source = json.loads(env_value)
    if not isinstance(source, list):
        raise ValueError("runtime trades must be a list")
    return [
        normalize_runtime_trade(trade, index)
        for index, trade in enumerate(source[:limit])
    ]
