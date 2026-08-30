from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any


def _service():
    from market.binance_market import service as market_service

    return market_service


def _cow_route_specs(pair: dict[str, Any], amount: str | int | float | Decimal) -> list[dict[str, Any]]:
    return _service().__legacy_cow_route_specs(pair, amount)


def _cow_cost_summary(result: dict[str, Any], *, final_delta_amount: str | None) -> dict[str, Any]:
    return _service().__legacy_cow_cost_summary(result, final_delta_amount=final_delta_amount)


def _cow_min_profit_usd_for_amount(amount: Any) -> Decimal:
    return _service().__legacy_cow_min_profit_usd_for_amount(amount)


def _cow_pure_profit_intent(
    *,
    amount: Any,
    input_symbol: Any,
    final_symbol: Any,
    path: Any,
    owner: str | None,
    cow_network: str,
    cow_chain_id: int,
    threshold_detail: dict[str, Any] | None,
    market_state: dict[str, Any] | None,
    link_name: Any | None = None,
) -> dict[str, Any]:
    return _service().__legacy_cow_pure_profit_intent(
        amount=amount,
        input_symbol=input_symbol,
        final_symbol=final_symbol,
        path=path,
        owner=owner,
        cow_network=cow_network,
        cow_chain_id=cow_chain_id,
        threshold_detail=threshold_detail,
        market_state=market_state,
        link_name=link_name,
    )


def _cow_own_limit_order_intent(plan: dict[str, Any] | None, *, min_profit_usd: Decimal) -> dict[str, Any]:
    return _service().__legacy_cow_own_limit_order_intent(plan, min_profit_usd=min_profit_usd)


def _cow_sdk_result_snapshot(result: dict[str, Any], precheck: dict[str, Any]) -> dict[str, Any]:
    return _service().__legacy_cow_sdk_result_snapshot(result, precheck)


def _cow_order_submission_requested() -> bool:
    return _service().__legacy_cow_order_submission_requested()


def _cow_order_submission_adapter_available() -> bool:
    return _service().__legacy_cow_order_submission_adapter_available()


def _cow_order_submission_sdk_ready() -> bool:
    return _service().__legacy_cow_order_submission_sdk_ready()


def _cow_order_submission_sdk_status() -> dict[str, Any]:
    return _service().__legacy_cow_order_submission_sdk_status()


def _cow_order_submission_enabled() -> bool:
    return _service().__legacy_cow_order_submission_enabled()


def _attach_cow_flashloan_sdk_plan(
    result: dict[str, Any],
    plan: dict[str, Any] | None,
    registry: dict[str, Any],
) -> None:
    _service().__legacy_attach_cow_flashloan_sdk_plan(result, plan, registry)


def _execution_plan_rows(plan: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return _service().__legacy_execution_plan_rows(plan)


def _cow_query_price_for_step(step: dict[str, Any], hop: dict[str, Any] | None) -> Decimal | None:
    return _service().__legacy_cow_query_price_for_step(step, hop)


def _cow_query_rate_for_step(step: dict[str, Any], hop: dict[str, Any] | None) -> Decimal | None:
    return _service().__legacy_cow_query_rate_for_step(step, hop)


def _apply_cow_quote_targets(plan: dict[str, Any] | None, result: dict[str, Any]) -> dict[str, Any] | None:
    return _service().__legacy_apply_cow_quote_targets(plan, result)


def _apply_cow_quote_analysis(plan: dict[str, Any] | None, result: dict[str, Any]) -> dict[str, Any] | None:
    return _service().__legacy_apply_cow_quote_analysis(plan, result)


def _cow_execution_precheck(result: dict[str, Any]) -> dict[str, Any]:
    return _service().__legacy_cow_execution_precheck(result)


def build_cow_route_precheck(
    market_state: dict[str, Any],
    *,
    amount: str | int | float | Decimal = "1000",
    quote_limit: int = 25,
    cow_network: str | None = None,
    aave_cache_path: Path | None = None,
    registry: dict[str, Any] | None = None,
) -> dict[str, Any]:
    market_service = _service()
    if cow_network is None:
        cow_network = market_service.DEFAULT_COW_TEST_NETWORK
    if aave_cache_path is None:
        aave_cache_path = market_service.DEFAULT_AAVE_CACHE_PATH
    network_config = market_service.cow_network_config(network=cow_network)
    selected_pairs = list(market_state.get("pairs") or [])[: max(1, int(quote_limit))]
    route_specs = [spec for pair in selected_pairs for spec in _cow_route_specs(pair, amount)]
    token_registry = registry if registry is not None else market_service.build_token_registry(
        aave_cache_path=aave_cache_path,
        include_cow_token_list=True,
        cow_network=network_config.network,
    )
    routes = [
        market_service._cow_route_support(spec, token_registry, cow_network=network_config.network)
        for spec in route_specs
    ]
    return {
        "cow_network": network_config.network,
        "cow_chain_id": network_config.chain_id,
        "cow_testnet": network_config.testnet,
        "selected_pair_count": len(selected_pairs),
        "route_count": len(routes),
        "supported_route_count": sum(1 for item in routes if item["supported"]),
        "unsupported_route_count": sum(1 for item in routes if not item["supported"]),
        "routes": routes,
    }


def build_cow_network_market_claims(
    extremes: dict[str, Any] | None,
    network_token_caches: dict[str, dict[str, Any]],
    *,
    limit: int = 50,
    min_spread_percent: float = 1.0,
    min_side_change_percent: float | None = None,
    min_token_price_usd: float | None = None,
    threshold_detail: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    market_service = _service()
    if min_side_change_percent is None:
        min_side_change_percent = market_service.DEFAULT_MIN_SIDE_CHANGE_PERCENT
    if min_token_price_usd is None:
        min_token_price_usd = market_service.DEFAULT_MIN_TOKEN_PRICE_USD
    min_side_change_percent = market_service.DEFAULT_MIN_COW_SIDE_CHANGE_PERCENT
    market_rows = market_service._basket_rows_from_extremes(extremes)
    display_market_rows = market_service._price_filtered_market_rows(
        market_rows,
        min_token_price_usd=min_token_price_usd,
    )
    eligible_market_rows, excluded_market_rows = market_service._eligible_market_rows(
        market_rows,
        min_side_change_percent=min_side_change_percent,
        min_token_price_usd=min_token_price_usd,
    )
    summaries = []
    for network, token_cache in network_token_caches.items():
        config = market_service.cow_network_config(network=network)
        if config.testnet or config.network not in {"avalanche", "bnb"}:
            continue
        registry = market_service._market_registry_for_network(
            config.network,
            token_cache.get("registry") if isinstance(token_cache, dict) else {},
        )
        supported_rows, unsupported_rows = market_service._cow_supported_market_rows(display_market_rows, registry or {})
        candidate_supported_rows, _ = market_service._cow_supported_market_rows(eligible_market_rows, registry or {})
        pool_filter = market_service._pool_filter_for_network(config.network)
        pool_supported_rows, pool_unsupported_rows = market_service._filter_rows_by_pool_symbol_support(
            supported_rows,
            pool_filter,
            network=config.network,
        )
        candidate_pool_supported_rows, candidate_pool_unsupported_rows = market_service._filter_rows_by_pool_symbol_support(
            candidate_supported_rows,
            pool_filter,
            network=config.network,
        )
        top_rows, bottom_rows = market_service._rank_supported_extremes(
            pool_supported_rows,
            limit,
            min_side_change_percent=min_side_change_percent,
            min_token_price_usd=min_token_price_usd,
            enforce_side_move=False,
        )
        candidate_top_rows, candidate_bottom_rows = market_service._rank_supported_extremes(
            candidate_pool_supported_rows,
            limit,
            min_side_change_percent=min_side_change_percent,
            min_token_price_usd=min_token_price_usd,
        )
        candidate_top_rows, candidate_bottom_rows, rejected_pairs = market_service._filter_pairs_by_pool_route_support(
            candidate_top_rows,
            candidate_bottom_rows,
            network=config.network,
            pool_filter=pool_filter,
        )
        supported_route_pairs = {
            f"{x.get('base_symbol')}:{y.get('base_symbol')}"
            for x in candidate_top_rows
            for y in candidate_bottom_rows
            if x.get("symbol") != y.get("symbol")
            and market_service._pool_route_support(
                str(x.get("base_symbol") or ""),
                str(y.get("base_symbol") or ""),
                network=config.network,
                pool_filter=pool_filter,
            ).get("supported")
        }
        for row in top_rows:
            row["pool_route_supported"] = any(
                f"{row.get('base_symbol')}:{bottom.get('base_symbol')}" in supported_route_pairs
                for bottom in bottom_rows
            )
        for row in bottom_rows:
            row["pool_route_supported"] = any(
                f"{top.get('base_symbol')}:{row.get('base_symbol')}" in supported_route_pairs
                for top in top_rows
            )
        if not pool_supported_rows:
            continue
        summaries.append(
            {
                "network": config.network,
                "label": market_service.COW_NETWORK_LABELS.get(config.network, config.network),
                "chain_id": config.chain_id,
                "token_cache_source": token_cache.get("source") if isinstance(token_cache, dict) else None,
                "token_cache_count": token_cache.get("token_count") if isinstance(token_cache, dict) else 0,
                "supported_symbol_count": len(pool_supported_rows),
                "unsupported_symbol_count": len(unsupported_rows) + len(pool_unsupported_rows),
                "candidate_supported_symbol_count": len(candidate_top_rows) + len(candidate_bottom_rows),
                "market_eligible_symbol_count": len(eligible_market_rows),
                "market_excluded_symbol_count": len(excluded_market_rows),
                "market_display_symbol_count": len(display_market_rows),
                "pool_filter": {
                    "enabled": True,
                    "loaded": bool(pool_filter.get("loaded")),
                    "source": pool_filter.get("source"),
                    "pair_count": pool_filter.get("pair_count"),
                    "cache_block_number": pool_filter.get("cache_block_number"),
                    "fetched_at": pool_filter.get("fetched_at"),
                    "symbol_supported_count": len(pool_supported_rows),
                    "symbol_unsupported_count": len(pool_unsupported_rows),
                    "candidate_symbol_unsupported_count": len(candidate_pool_unsupported_rows),
                    "rejected_pair_count": len(rejected_pairs),
                    "rejected_pairs": rejected_pairs[:20],
                    "error": pool_filter.get("error"),
                },
                "min_side_change_percent": float(min_side_change_percent),
                "min_token_price_usd": float(min_token_price_usd),
                "min_spread_percent": max(0.0, float(min_spread_percent)),
                "threshold_detail": threshold_detail or {},
                "pair_count": market_service._candidate_pair_count(candidate_top_rows, candidate_bottom_rows, min_spread_percent),
                "top": top_rows,
                "bottom": bottom_rows,
            }
        )
    return sorted(summaries, key=lambda item: (-int(item.get("supported_symbol_count") or 0), item.get("network") or ""))


def build_cow_supported_market_overview(
    extremes: dict[str, Any] | None,
    network_token_caches: dict[str, dict[str, Any]],
    *,
    limit: int = 50,
    min_side_change_percent: float | None = None,
    min_token_price_usd: float | None = None,
    threshold_detail: dict[str, Any] | None = None,
) -> dict[str, Any]:
    market_service = _service()
    if min_side_change_percent is None:
        min_side_change_percent = market_service.DEFAULT_MIN_COW_SIDE_CHANGE_PERCENT
    if min_token_price_usd is None:
        min_token_price_usd = market_service.DEFAULT_MIN_TOKEN_PRICE_USD
    min_side_change_percent = market_service.DEFAULT_MIN_COW_SIDE_CHANGE_PERCENT
    market_rows = market_service._basket_rows_from_extremes(extremes)
    display_market_rows = market_service._price_filtered_market_rows(
        market_rows,
        min_token_price_usd=min_token_price_usd,
    )
    top_eligible_market_rows, top_excluded_market_rows = market_service._eligible_market_rows(
        market_rows,
        min_side_change_percent=min_side_change_percent,
        min_token_price_usd=min_token_price_usd,
        side="top",
    )
    bottom_eligible_market_rows, bottom_excluded_market_rows = market_service._eligible_market_rows(
        market_rows,
        min_side_change_percent=min_side_change_percent,
        min_token_price_usd=min_token_price_usd,
        side="bottom",
    )
    all_eligible_market_rows, all_excluded_market_rows = market_service._eligible_market_rows(
        market_rows,
        min_side_change_percent=min_side_change_percent,
        min_token_price_usd=min_token_price_usd,
    )
    top_supported_rows, top_unsupported_rows, top_eligible_symbols = market_service._cow_supported_union_market_rows(
        top_eligible_market_rows,
        network_token_caches,
    )
    bottom_supported_rows, bottom_unsupported_rows, bottom_eligible_symbols = market_service._cow_supported_union_market_rows(
        bottom_eligible_market_rows,
        network_token_caches,
    )
    all_supported_rows, all_unsupported_rows, all_eligible_symbols = market_service._cow_supported_union_market_rows(
        all_eligible_market_rows,
        network_token_caches,
    )
    display_supported_rows, display_unsupported_rows, display_eligible_symbols = market_service._cow_supported_union_market_rows(
        display_market_rows,
        network_token_caches,
    )
    top_rows, _ = market_service._rank_supported_extremes(
        display_supported_rows,
        max(1, int(limit)),
        min_side_change_percent=min_side_change_percent,
        min_token_price_usd=min_token_price_usd,
        enforce_side_move=False,
    )
    _, bottom_rows = market_service._rank_supported_extremes(
        display_supported_rows,
        max(1, int(limit)),
        min_side_change_percent=min_side_change_percent,
        min_token_price_usd=min_token_price_usd,
        enforce_side_move=False,
    )
    return {
        "limit": max(1, int(limit)),
        "source_market_row_count": len(market_rows),
        "eligible_symbol_count": len(display_eligible_symbols),
        "supported_symbol_count": len(display_supported_rows),
        "unsupported_symbol_count": len(display_unsupported_rows),
        "candidate_supported_symbol_count": len(all_supported_rows),
        "market_eligible_symbol_count": len(all_eligible_market_rows),
        "market_excluded_symbol_count": len(all_excluded_market_rows),
        "market_display_symbol_count": len(display_market_rows),
        "market_excluded_reason_counts": market_service._market_filter_reason_counts(all_excluded_market_rows),
        "top_filter": {
            "source_market_row_count": len(market_rows),
            "eligible_symbol_count": len(top_eligible_symbols),
            "supported_symbol_count": len(top_supported_rows),
            "unsupported_symbol_count": len(top_unsupported_rows),
            "market_eligible_symbol_count": len(top_eligible_market_rows),
            "market_excluded_symbol_count": len(top_excluded_market_rows),
            "market_excluded_reason_counts": market_service._market_filter_reason_counts(top_excluded_market_rows),
        },
        "bottom_filter": {
            "source_market_row_count": len(market_rows),
            "eligible_symbol_count": len(bottom_eligible_symbols),
            "supported_symbol_count": len(bottom_supported_rows),
            "unsupported_symbol_count": len(bottom_unsupported_rows),
            "market_eligible_symbol_count": len(bottom_eligible_market_rows),
            "market_excluded_symbol_count": len(bottom_excluded_market_rows),
            "market_excluded_reason_counts": market_service._market_filter_reason_counts(bottom_excluded_market_rows),
        },
        "min_side_change_percent": float(min_side_change_percent),
        "min_token_price_usd": float(min_token_price_usd),
        "threshold_detail": threshold_detail or {},
        "top": top_rows,
        "bottom": bottom_rows,
    }


__all__ = [
    "_apply_cow_quote_analysis",
    "_apply_cow_quote_targets",
    "_attach_cow_flashloan_sdk_plan",
    "_cow_cost_summary",
    "_cow_execution_precheck",
    "_cow_min_profit_usd_for_amount",
    "_cow_order_submission_adapter_available",
    "_cow_order_submission_enabled",
    "_cow_order_submission_requested",
    "_cow_order_submission_sdk_ready",
    "_cow_order_submission_sdk_status",
    "_cow_own_limit_order_intent",
    "_cow_pure_profit_intent",
    "_cow_query_price_for_step",
    "_cow_query_rate_for_step",
    "_cow_route_specs",
    "_cow_supported_market_overview",
    "_execution_plan_rows",
    "build_cow_network_market_claims",
    "build_cow_route_precheck",
    "build_cow_supported_market_overview",
]
