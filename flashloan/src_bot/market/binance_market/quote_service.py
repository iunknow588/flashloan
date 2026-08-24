"""Binance market state and CoW quote verification services."""

from __future__ import annotations

import os
from decimal import Decimal
from pathlib import Path
from typing import Any

from strategy.arbitrage import ArbitrageConfig

from market.binance_market.candidate_service import build_pair_candidates
from market.binance_market.cow_tokens import DEFAULT_COW_TEST_NETWORK
from market.binance_market.pool_candidates import dex_pool_candidates_payload
from market.velocity_candidates import top_bottom_from_extremes


def build_binance_market_state(
    extremes: dict[str, Any] | None,
    *,
    aave_symbols: list[str],
    arbitrage_config: ArbitrageConfig,
    top_limit: int | None = None,
    bottom_limit: int | None = None,
    pair_side_limit: int = 5,
    cow_display_limit: int | None = None,
    slippage_bps: int | None = None,
    cow_network: str | None = None,
    min_spread_percent: float = 0.0,
    min_side_change_percent: float | None = None,
    min_token_price_usd: float | None = None,
    threshold_detail: dict[str, Any] | None = None,
    registry: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from . import service as market_service

    if top_limit is None:
        top_limit = market_service.DEFAULT_BINANCE_RAW_SIDE_LIMIT
    if bottom_limit is None:
        bottom_limit = market_service.DEFAULT_BINANCE_RAW_SIDE_LIMIT
    if slippage_bps is None:
        slippage_bps = market_service.DEFAULT_EXECUTION_SLIPPAGE_BPS
    if min_side_change_percent is None:
        min_side_change_percent = market_service.DEFAULT_MIN_COW_SIDE_CHANGE_PERCENT
    if min_token_price_usd is None:
        min_token_price_usd = market_service.DEFAULT_MIN_TOKEN_PRICE_USD
    pair_side_limit = max(1, min(int(pair_side_limit), 5))
    top_limit = max(1, min(int(top_limit), market_service.DEFAULT_BINANCE_RAW_SIDE_LIMIT))
    bottom_limit = max(1, min(int(bottom_limit), market_service.DEFAULT_BINANCE_RAW_SIDE_LIMIT))
    if cow_display_limit is not None:
        cow_display_limit = max(1, min(int(cow_display_limit), market_service.DEFAULT_BINANCE_RAW_SIDE_LIMIT))
    market_rows = market_service._basket_rows_from_extremes(extremes)
    eligible_market_rows, excluded_market_rows = market_service._eligible_market_rows(
        market_rows,
        min_side_change_percent=min_side_change_percent,
        min_token_price_usd=min_token_price_usd,
    )
    raw_top_all, raw_bottom_all = market_service._rank_supported_extremes(
        market_service._price_filtered_market_rows(
            market_rows,
            min_token_price_usd=min_token_price_usd,
        ),
        max(top_limit, bottom_limit, pair_side_limit),
        min_side_change_percent=0.0,
        min_token_price_usd=min_token_price_usd,
    )
    raw_top = raw_top_all[: max(1, int(top_limit))]
    raw_bottom = raw_bottom_all[: max(1, int(bottom_limit))]
    network_display_limit = max(1, int(cow_display_limit if cow_display_limit is not None else top_limit))
    cow_filter = None
    if cow_network:
        network_config = market_service.cow_network_config(network=cow_network)
        if network_config.network == "bnb":
            token_registry = market_service._bnb_static_market_registry()
        else:
            token_registry = registry if registry is not None else market_service.build_token_registry(
                aave_cache_path=market_service.DEFAULT_AAVE_CACHE_PATH,
                include_cow_token_list=True,
                cow_network=network_config.network,
            )
            token_registry = market_service._market_registry_for_network(network_config.network, token_registry)
        supported_rows, unsupported_rows = market_service._cow_supported_market_rows(
            eligible_market_rows,
            token_registry,
        )
        pool_filter = market_service._pool_filter_for_network(network_config.network)
        pool_supported_rows, pool_unsupported_rows = market_service._filter_rows_by_pool_symbol_support(
            supported_rows,
            pool_filter,
            network=network_config.network,
        )
        top_all, bottom_all = market_service._rank_supported_extremes(
            pool_supported_rows,
            max(top_limit, bottom_limit, pair_side_limit),
            min_side_change_percent=min_side_change_percent,
            min_token_price_usd=min_token_price_usd,
        )
        cow_filter = {
            "network": network_config.network,
            "chain_id": network_config.chain_id,
            "enabled": True,
            "supported_symbol_count": len(pool_supported_rows),
            "unsupported_symbol_count": len(unsupported_rows) + len(pool_unsupported_rows),
            "market_eligible_symbol_count": len(eligible_market_rows),
            "market_excluded_symbol_count": len(excluded_market_rows),
            "min_side_change_percent": float(min_side_change_percent),
            "min_token_price_usd": float(min_token_price_usd),
            "cow_display_limit": network_display_limit,
            "min_spread_percent": max(0.0, float(min_spread_percent)),
            "threshold_detail": threshold_detail or {},
            "pool_filter": {
                "enabled": network_config.network in {"avalanche", "bnb"},
                "loaded": bool(pool_filter and pool_filter.get("loaded")),
                "source": (pool_filter or {}).get("source"),
                "pair_count": (pool_filter or {}).get("pair_count"),
                "cache_block_number": (pool_filter or {}).get("cache_block_number"),
                "fetched_at": (pool_filter or {}).get("fetched_at"),
                "symbol_supported_count": len(pool_supported_rows),
                "symbol_unsupported_count": len(pool_unsupported_rows),
                "error": (pool_filter or {}).get("error"),
            },
        }
    else:
        top_all, bottom_all = raw_top_all, raw_bottom_all
        cow_filter = {
            "enabled": False,
            "market_eligible_symbol_count": len(eligible_market_rows),
            "market_excluded_symbol_count": len(excluded_market_rows),
            "min_side_change_percent": float(min_side_change_percent),
            "min_token_price_usd": float(min_token_price_usd),
            "cow_display_limit": network_display_limit,
            "min_spread_percent": max(0.0, float(min_spread_percent)),
            "threshold_detail": threshold_detail or {},
        }
    top = top_all[: network_display_limit]
    bottom = bottom_all[: network_display_limit]
    pair_top = top_all[: max(1, int(pair_side_limit))]
    pair_bottom = bottom_all[: max(1, int(pair_side_limit))]
    basket_rows = top_all + bottom_all
    if isinstance(extremes, dict) and isinstance(extremes.get("basket"), list):
        basket_top, basket_bottom = top_bottom_from_extremes(extremes, side_limit=1000)
        basket_rows = basket_top + basket_bottom
    by_symbol = market_service._rows_by_symbol(basket_rows)
    aave_rows = []
    for symbol in dict.fromkeys(str(item).upper() for item in aave_symbols if item):
        row = by_symbol.get(symbol)
        aave_rows.append(
            {
                "symbol": symbol,
                "change_percent": row.get("change_percent") if row else None,
                "start_price": row.get("start_price") if row else None,
                "current_price": row.get("current_price") if row else None,
                "price_source": row.get("price_source") if row else None,
                "window_ready": bool(row.get("window_ready")) if row else False,
                "tracked": row is not None,
            }
        )
    min_spread = max(0.0, float(min_spread_percent))
    pairs, pool_pair_rejections = build_pair_candidates(
        pair_top,
        pair_bottom,
        min_spread_percent=min_spread,
        network=(cow_filter or {}).get("network") if cow_filter else None,
        pool_filter=pool_filter if cow_network else None,
        route_support=lambda left, right, network, selected_filter: market_service._pool_route_support(
            left,
            right,
            network=network,
            pool_filter=selected_filter,
        ),
        pair_builder=lambda x, y: market_service._pair_quote_candidates(
            x,
            y,
            arbitrage_config,
            slippage_bps=slippage_bps,
        ),
    )
    ranked_pairs = pairs
    if cow_filter and isinstance(cow_filter.get("pool_filter"), dict):
        cow_filter["pool_filter"]["rejected_pair_count"] = len(pool_pair_rejections)
        cow_filter["pool_filter"]["rejected_pairs"] = pool_pair_rejections[:20]
    try:
        pool_candidates = dex_pool_candidates_payload()
    except Exception as exc:
        pool_candidates = {"error": str(exc), "pairs": [], "summary": {}, "poolStaticTable": {}}
    return {
        "observed_at": extremes.get("observed_at") if isinstance(extremes, dict) else None,
        "window_seconds": extremes.get("window_seconds") if isinstance(extremes, dict) else None,
        "sample_count": extremes.get("sample_count") if isinstance(extremes, dict) else 0,
        "observation_universe_size": extremes.get("observation_universe_size") if isinstance(extremes, dict) else 0,
        "price_source": extremes.get("price_source") if isinstance(extremes, dict) else None,
        "market_state_source": extremes.get("market_state_source") if isinstance(extremes, dict) else None,
        "fallback_reason": extremes.get("fallback_reason") if isinstance(extremes, dict) else None,
        "top_limit": top_limit,
        "bottom_limit": bottom_limit,
        "raw_top": raw_top,
        "raw_bottom": raw_bottom,
        "pair_count": len(pairs),
        "top": top,
        "bottom": bottom,
        "aave_rows": aave_rows,
        "pairs": ranked_pairs,
        "pool_candidates": pool_candidates,
        "best": None,
        "notional_usd": arbitrage_config.notional_usd,
        "slippage_bps": max(0, min(int(slippage_bps), 5000)),
        "cow_filter": cow_filter,
        "quote_verified": False,
        "estimation_available": False,
        "candidate_basis": "binance_token_names_only",
    }


def build_cow_quote_verification(
    market_state: dict[str, Any],
    *,
    amount: str | int | float | Decimal = "1000",
    quote_limit: int = 5,
    owner: str | None = None,
    cow_network: str | None = None,
    price_quality: str = "fast",
    valid_for: int = 60,
    quote_timeout_seconds: int | float = 8,
    aave_cache_path: Path | None = None,
    registry: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from . import service as market_service

    if aave_cache_path is None:
        aave_cache_path = market_service.DEFAULT_AAVE_CACHE_PATH
    selected_network = cow_network or os.getenv("COW_REALTIME_QUOTE_NETWORK") or os.getenv("COW_NETWORK") or DEFAULT_COW_TEST_NETWORK
    network_config = market_service.cow_network_config(network=selected_network)
    account_config = market_service.cow_account_config(network_config.network)
    requested_owner = str(owner or "").strip()
    owner = requested_owner or account_config.owner
    owner_source = "request.owner" if requested_owner else account_config.owner_source
    ranked_pairs = list(market_state.get("pairs") or [])
    selected_pairs = ranked_pairs[: max(1, min(int(quote_limit), 5))]
    route_specs = [spec for pair in selected_pairs for spec in market_service._cow_route_specs(pair, amount)]
    token_registry = registry if registry is not None else market_service.build_token_registry(
        aave_cache_path=aave_cache_path,
        include_cow_token_list=True,
        cow_network=network_config.network,
    )
    precheck_routes = [
        market_service._cow_route_support(spec, token_registry, cow_network=network_config.network)
        for spec in route_specs
    ]
    precheck_by_route = {
        (item.get("pair"), item.get("pair_rank"), item.get("priority_reason")): item
        for item in precheck_routes
    }
    results = []
    for spec in route_specs:
        support = precheck_by_route.get((spec.get("pair"), spec.get("pair_rank"), spec.get("priority_reason")), {})
        if support.get("supported"):
            result = market_service.evaluate_cow_route(
                spec,
                registry=token_registry,
                default_amount=amount,
                owner=owner,
                cow_network=network_config.network,
                price_quality=price_quality,
                valid_for=valid_for,
                quote_timeout_seconds=quote_timeout_seconds,
            )
        else:
            path = spec.get("path") or []
            result = {
                "name": spec.get("name"),
                "path": path,
                "input_amount": str(amount),
                "input_symbol": path[0] if path else None,
                "final_symbol": path[-1] if path else None,
                "viable": False,
                "error": support.get("error") or "CoW token support precheck failed",
                "cow_sdk_result": {
                    "status": "not_called",
                    "reason": support.get("error") or "CoW token support precheck failed",
                    "controller": "cow_sdk",
                },
                "hops": [],
            }
        result["pair"] = spec.get("pair")
        result["pair_rank"] = spec.get("pair_rank")
        result["priority_reason"] = spec.get("priority_reason")
        result["edge_hint_percent"] = spec.get("edge_hint_percent")
        result["cow_support"] = support
        result["final_delta_amount"] = None
        result["profit_prediction_disabled"] = True
        result["profit_prediction_mode"] = "disabled_intent_only_sdk_settlement"
        cow_filter = market_state.get("cow_filter") if isinstance(market_state.get("cow_filter"), dict) else {}
        result["cow_flashloan_intent"] = market_service._cow_pure_profit_intent(
            amount=result.get("input_amount") or amount,
            input_symbol=result.get("input_symbol"),
            final_symbol=result.get("final_symbol"),
            path=result.get("path") or spec.get("path") or [],
            owner=owner,
            cow_network=network_config.network,
            cow_chain_id=network_config.chain_id,
            threshold_detail=cow_filter.get("threshold_detail") if isinstance(cow_filter, dict) else {},
            market_state=market_state,
            link_name=result.get("priority_reason") or spec.get("priority_reason") or result.get("name") or spec.get("name"),
        )
        result["binance_execution_plan"] = market_service._apply_cow_quote_analysis(spec.get("binance_execution_plan"), result)
        market_service._attach_cow_flashloan_sdk_plan(result, result.get("binance_execution_plan"), token_registry)
        result["cow_sdk_result"] = market_service._cow_sdk_result_snapshot(result, {})
        result["execution_precheck"] = market_service._cow_execution_precheck(result)
        result["cow_sdk_result"] = market_service._cow_sdk_result_snapshot(result, result["execution_precheck"])
        result["costs"] = market_service._cow_cost_summary(result, final_delta_amount=None)
        result["quote_verified"] = True
        results.append(result)
    ranking = market_service.rank_cow_routes(results)
    opportunities = [
        item
        for item in ranking
        if (item.get("execution_precheck") or {}).get("checks_passed")
    ]
    return {
        "observed_at": market_state.get("observed_at"),
        "amount": str(amount),
        "owner": owner,
        "owner_source": owner_source,
        "cow_network": network_config.network,
        "cow_chain_id": network_config.chain_id,
        "cow_testnet": network_config.testnet,
        "price_quality": price_quality,
        "valid_for": valid_for,
        "selected_pair_count": len(selected_pairs),
        "route_count": len(route_specs),
        "supported_route_count": sum(1 for item in precheck_routes if item["supported"]),
        "unsupported_route_count": sum(1 for item in precheck_routes if not item["supported"]),
        "viable_count": sum(1 for item in ranking if item.get("viable")),
        "opportunity_count": len(opportunities),
        "precheck": {
            "routes": precheck_routes,
        },
        "best": opportunities[0] if opportunities else (ranking[0] if ranking else None),
        "best_opportunity": opportunities[0] if opportunities else None,
        "opportunities": opportunities,
        "ranking": ranking,
    }
