"""Service payload construction for the Binance CoW support route."""

from __future__ import annotations

from market.binance_market.service import (
    DEFAULT_BINANCE_RAW_SIDE_LIMIT,
    build_binance_market_state,
    build_cow_route_precheck,
    select_binance_market_extremes,
)


def build_cow_support_payload(
    market_state: dict,
    *,
    amount: str,
    quote_limit: int,
    cow_network: str,
    token_cache: dict,
) -> dict:
    return {"market_state": market_state, **build_cow_route_precheck(
        market_state,
        amount=amount,
        quote_limit=quote_limit,
        cow_network=cow_network,
        registry=token_cache["registry"],
    )}


def build_cow_support_market_state(
    extremes: dict | None,
    *,
    assets: list[str],
    arbitrage_config,
    slippage_bps: int,
    thresholds: dict,
    cow_network: str,
    token_cache: dict,
    side_limit: int,
    pair_side_limit: int,
) -> dict:
    market_state = build_binance_market_state(
        select_binance_market_extremes(extremes, side_limit=side_limit),
        aave_symbols=assets,
        arbitrage_config=arbitrage_config,
        top_limit=side_limit,
        bottom_limit=side_limit,
        pair_side_limit=pair_side_limit,
        cow_display_limit=DEFAULT_BINANCE_RAW_SIDE_LIMIT,
        slippage_bps=slippage_bps,
        cow_network=token_cache["network"],
        min_spread_percent=float(thresholds["adjusted_min_spread_percent"]),
        min_side_change_percent=float(thresholds["min_side_change_percent"]),
        min_token_price_usd=float(thresholds["min_token_price_usd"]),
        threshold_detail=thresholds,
        registry=token_cache["registry"],
    )
    market_state["cow_filter"]["token_cache_source"] = token_cache["source"]
    market_state["cow_filter"]["token_cache_count"] = token_cache["token_count"]
    market_state["cow_filter"]["cow_display_limit"] = DEFAULT_BINANCE_RAW_SIDE_LIMIT
    return market_state


__all__ = ["build_cow_support_market_state", "build_cow_support_payload"]
