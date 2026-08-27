from __future__ import annotations

from flask import jsonify

from market.observer import ASSETS
from market.binance_market.service import (
    DEFAULT_BINANCE_RAW_SIDE_LIMIT,
    build_binance_market_state,
    build_cow_route_precheck,
    cow_network_options,
    load_cow_supported_token_registry,
    refresh_cow_supported_token_cache,
    select_binance_market_extremes,
)
from web.data_routes import binance_market as market_routes

panel_call = market_routes.panel_call
data_error_message = market_routes.data_error_message
request_int_arg = market_routes.request_int_arg
request_float_arg = market_routes.request_float_arg
request_cow_amount = market_routes.request_cow_amount
request_cow_trade_thresholds = market_routes.request_cow_trade_thresholds
_request_cow_network_arg = market_routes._request_cow_network_arg
latest_binance_extremes_file = market_routes.latest_binance_extremes_file
safe_latest = market_routes.safe_latest


def register_binance_market_cow_routes(app, panel) -> None:
    @app.get("/api/binance-market/cow-config")
    def binance_market_cow_config():
        return jsonify(cow_network_options())

    @app.get("/api/binance-market/cow-tokens")
    def binance_market_cow_tokens():
        cow_network = _request_cow_network_arg()
        database_url = None
        token_cache = load_cow_supported_token_registry(
            cow_network=cow_network,
            database_url=database_url,
            allow_live_fallback=False,
        )
        return jsonify(
            {
                "network": token_cache["network"],
                "chain_id": token_cache["chain_id"],
                "source": token_cache["source"],
                "token_count": token_cache["token_count"],
                "tokens": token_cache["tokens"],
            }
        )

    @app.post("/api/binance-market/cow-tokens/refresh")
    def binance_market_cow_tokens_refresh():
        cow_network = _request_cow_network_arg()
        database_url = None
        try:
            payload = refresh_cow_supported_token_cache(
                cow_network=cow_network,
                database_url=database_url,
            )
        except Exception as exc:
            return jsonify({"error": data_error_message(exc)}), 400
        return jsonify(payload)

    @app.get("/api/binance-market/cow-support")
    def binance_market_cow_support():
        extremes = safe_latest(latest_binance_extremes_file)
        side_limit = request_int_arg("side_limit", 50, minimum=1, maximum=50)
        pair_side_limit = request_int_arg("pair_side_limit", 5, minimum=1, maximum=5)
        quote_limit = request_int_arg("quote_limit", 5, minimum=1, maximum=5)
        amount = request_cow_amount()
        request_float_arg("quote_timeout_seconds", 8.0, minimum=1.0, maximum=30.0)
        arbitrage_config, slippage_bps, thresholds = request_cow_trade_thresholds(amount)
        min_spread_percent = float(thresholds["adjusted_min_spread_percent"])
        min_side_change_percent = float(thresholds["min_side_change_percent"])
        min_token_price_usd = float(thresholds["min_token_price_usd"])
        cow_network = _request_cow_network_arg()
        database_url = None
        token_cache = load_cow_supported_token_registry(cow_network=cow_network, database_url=database_url)
        extremes = select_binance_market_extremes(extremes, side_limit=side_limit)
        market_state = build_binance_market_state(
            extremes,
            aave_symbols=list(ASSETS.keys()),
            arbitrage_config=arbitrage_config,
            top_limit=side_limit,
            bottom_limit=side_limit,
            pair_side_limit=pair_side_limit,
            cow_display_limit=DEFAULT_BINANCE_RAW_SIDE_LIMIT,
            slippage_bps=slippage_bps,
            cow_network=token_cache["network"],
            min_spread_percent=min_spread_percent,
            min_side_change_percent=min_side_change_percent,
            min_token_price_usd=min_token_price_usd,
            threshold_detail=thresholds,
            registry=token_cache["registry"],
        )
        market_state["cow_filter"]["token_cache_source"] = token_cache["source"]
        market_state["cow_filter"]["token_cache_count"] = token_cache["token_count"]
        market_state["cow_filter"]["cow_display_limit"] = DEFAULT_BINANCE_RAW_SIDE_LIMIT
        try:
            payload = build_cow_route_precheck(
                market_state,
                amount=amount,
                quote_limit=quote_limit,
                cow_network=token_cache["network"],
                registry=token_cache["registry"],
            )
        except Exception as exc:
            return jsonify({"error": data_error_message(exc), "market_state": market_state, "routes": []}), 400
        return jsonify({"market_state": market_state, **payload})
