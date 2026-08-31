from __future__ import annotations

from flask import jsonify, request

from core.chain_selection import ChainSelectionError
from core.dex_routing import dex_quote_rpc_urls_for, dex_router_context_for, dex_router_name_for
from execution.dex_costs import estimate_symbol_cost, parse_trade_usd_amounts
from execution.execution_payload import PayloadConfig, build_execution_payload
from execution.plan_quotes import quote_execution_plan
from market.velocity_candidates import build_velocity_candidate_pairs
from market.observer import ASSETS
from market.binance_market.service import DEFAULT_BINANCE_RAW_SIDE_LIMIT
from strategy.arbitrage import build_pair_strategy_analysis, simulate_four_route_cycles
from web import control_panel_data_routes as shared
from web.control_panel_liquidation_base import aave_rpc_urls, database_url_or_none
from web.control_panel_market import (
    assert_fresh_execution_plan,
    available_candidate_symbols,
    arbitrage_config_from_strategy,
    build_velocity_summary,
    configured_fee_slippage_percent,
    latest_arbitrage_simulation,
    latest_arbitrage_simulation_file,
    latest_binance_extremes,
    latest_binance_extremes_file,
    latest_candidate_price_rows,
    latest_executable_signal,
    latest_reference_price,
    observation_count,
    opportunity_health_rows,
    opportunity_health_summary,
    read_slippage_bps,
    recent_aave_pair_prices,
    recent_binance_pair_prices,
    recent_binance_price_history,
    recent_observations,
    recent_velocity_timepoints,
    safe_latest,
    strategy_config,
    write_strategy_config,
    velocity_timepoint_snapshot,
)
from web.control_panel_config import unified_sampling_profile

ROUTE_CONTEXT = shared.ROUTE_CONTEXT
panel_call = shared.panel_call
data_error_message = shared.data_error_message
request_int_arg = shared.request_int_arg
request_float_arg = shared.request_float_arg
request_min_cow_spread_percent = shared.request_min_cow_spread_percent
request_cow_amount = shared.request_cow_amount
request_cow_trade_thresholds = shared.request_cow_trade_thresholds
_optional_request_chain_selection = shared._optional_request_chain_selection
_chain_selection_error_response = shared._chain_selection_error_response


def register_market_analysis_routes(app, panel) -> None:
    ROUTE_CONTEXT.bind(panel, globals())

    @app.get("/api/opportunity-health")
    def opportunity_health_api():
        try:
            chain_selection = _optional_request_chain_selection()
        except ChainSelectionError as exc:
            return _chain_selection_error_response(exc)
        running = quick_observer_running()
        symbols = displayed_symbols(running or observer_starting)
        binance_extremes = restrict_extremes_to_symbols(safe_latest(latest_binance_extremes_file), symbols)
        config = strategy_config()
        rows = opportunity_health_rows(binance_extremes, config)
        return jsonify(
            {
                "rows": rows,
                "summary": opportunity_health_summary(rows, config),
                "sampling_profile": unified_sampling_profile(config),
                "binance_extremes": binance_extremes,
                "chain_selection": chain_selection,
            }
        )

    from web.data_routes.liquidation_data import register_liquidation_data_routes

    register_liquidation_data_routes(app, panel)

    
    @app.get("/api/velocity-timepoints")
    def velocity_timepoints():
        try:
            chain_selection = _optional_request_chain_selection()
        except ChainSelectionError as exc:
            return _chain_selection_error_response(exc)
        try:
            limit = request_int_arg("limit", 200, minimum=1, maximum=500)
            rows = recent_velocity_timepoints(limit)
        except Exception as exc:
            if "does not exist" in str(exc):
                return jsonify({"timepoints": [], "chain_selection": chain_selection})
            return jsonify({"error": data_error_message(exc), "timepoints": [], "chain_selection": chain_selection}), 400
        return jsonify({"timepoints": rows, "chain_selection": chain_selection})
    
    
    @app.get("/api/velocity-summary")
    def velocity_summary():
        try:
            chain_selection = _optional_request_chain_selection()
        except ChainSelectionError as exc:
            return _chain_selection_error_response(exc)
        try:
            raw_id = request.args.get("id", "").strip()
            snapshot_id = request_int_arg("id", 0) if raw_id else None
            snapshot = velocity_timepoint_snapshot(snapshot_id)
            if not snapshot and snapshot_id is not None:
                snapshot = velocity_timepoint_snapshot(None)
            if not snapshot:
                return jsonify({"error": "no velocity timepoint found", "rows": [], "chain_selection": chain_selection}), 404
            payload = build_velocity_summary(snapshot)
            payload["chain_selection"] = chain_selection
            return jsonify(payload)
        except Exception as exc:
            if "does not exist" in str(exc):
                return jsonify({"error": "initialize database and collect velocity windows first", "rows": [], "chain_selection": chain_selection})
            return jsonify({"error": data_error_message(exc), "rows": [], "chain_selection": chain_selection}), 400
    
    
    @app.get("/api/strategy-config")
    def get_strategy_config():
        config = strategy_config()
        return jsonify({"config": config, "sampling_profile": unified_sampling_profile(config), "running": is_observer_running()})
    
    
    @app.post("/api/strategy-config")
    def post_strategy_config():
        try:
            config = write_strategy_config(request.get_json(silent=True) or {})
        except Exception as exc:
            return jsonify({"error": data_error_message(exc)}), 400
        return jsonify({"config": config, "sampling_profile": unified_sampling_profile(config), "restart_required": is_observer_running()})
    
    
    @app.get("/api/trade-stats")
    def get_trade_stats():
        return jsonify({"stats": safe_latest(lambda: read_trade_stats(database_url_or_none()))})
    
    
    @app.get("/api/testnet-trade-stats")
    def get_testnet_trade_stats():
        return jsonify({"stats": safe_latest(lambda: read_testnet_trade_stats(REPO_ROOT))})
    
    
    @app.get("/api/observations")
    def observations():
        symbol = request.args.get("symbol", "AVAXUSDT").strip().upper()
        try:
            limit = request_int_arg("limit", 120, minimum=2, maximum=1000)
            rows = recent_observations(symbol, limit) if symbol in ASSETS else []
            mode = "aave_observations" if rows else "binance_price_history"
            if not rows:
                rows = shared.recent_binance_price_history(symbol, limit)
        except Exception as exc:
            return jsonify({"error": data_error_message(exc)}), 400
        return jsonify(
            {
                "symbol": symbol,
                "limit": limit,
                "mode": mode,
                "supports_aave": symbol in ASSETS and mode == "aave_observations",
                "supports_dex_costs": symbol in ASSETS and mode == "aave_observations",
                "fee_slippage_percent": shared.configured_fee_slippage_percent(),
                "rows": rows,
            }
        )
    
    
    @app.get("/api/aave-pair-prices")
    def aave_pair_prices():
        x_symbol = request.args.get("x", "").strip().upper()
        y_symbol = request.args.get("y", "").strip().upper()
        if not x_symbol or not y_symbol:
            simulation = safe_latest(latest_arbitrage_simulation_file) or {}
            x_symbol = x_symbol or str(simulation.get("x_symbol") or simulation.get("a_symbol") or "").upper()
            y_symbol = y_symbol or str(simulation.get("y_symbol") or simulation.get("b_symbol") or "").upper()
        if x_symbol not in ASSETS or y_symbol not in ASSETS:
            return jsonify({"error": "x and y must be mapped Aave symbols", "rows": []}), 400
        try:
            limit = request_int_arg("limit", 120, minimum=2, maximum=1000)
            rows = recent_aave_pair_prices(x_symbol, y_symbol, limit)
        except Exception as exc:
            return jsonify({"error": data_error_message(exc), "rows": []}), 400
        return jsonify({"x_symbol": x_symbol, "y_symbol": y_symbol, "limit": limit, "rows": rows})
    
    
    @app.get("/api/binance-pair-prices")
    def binance_pair_prices():
        x_symbol = request.args.get("x", "").strip().upper()
        y_symbol = request.args.get("y", "").strip().upper()
        if not x_symbol or not y_symbol or x_symbol == y_symbol:
            return jsonify({"error": "select two different symbols", "rows": []}), 400
        try:
            limit = request_int_arg("limit", 120, minimum=2, maximum=1000)
            rows = recent_binance_pair_prices(x_symbol, y_symbol, limit)
        except Exception as exc:
            if "does not exist" in str(exc):
                return jsonify({"x_symbol": x_symbol, "y_symbol": y_symbol, "limit": limit, "rows": []})
            return jsonify({"error": data_error_message(exc), "rows": []}), 400
        return jsonify({"x_symbol": x_symbol, "y_symbol": y_symbol, "limit": limit, "rows": rows})
    
    
    @app.get("/api/pair-route-profits")
    def pair_route_profits():
        x_symbol = request.args.get("x", "").strip().upper()
        y_symbol = request.args.get("y", "").strip().upper()
        if not x_symbol or not y_symbol or x_symbol == y_symbol:
            return jsonify({"error": "select two different symbols", "routes": []}), 400
        try:
            initial_amount = request_float_arg("initial", 100, minimum=0.000001)
            rows = latest_candidate_price_rows([x_symbol, y_symbol])
            if x_symbol not in rows or y_symbol not in rows:
                return jsonify(
                    {
                        "x_symbol": x_symbol,
                        "y_symbol": y_symbol,
                        "initial_amount": initial_amount,
                        "routes": [],
                        "error": "route profit needs both symbols in binance_candidate_price_history",
                    }
                )
            chain_selection = None
            try:
                chain_selection = _optional_request_chain_selection(feature="dex_arbitrage")
            except ChainSelectionError as exc:
                return _chain_selection_error_response(exc)
            router_context = dex_router_context_for(chain_selection)
            config = arbitrage_config_from_strategy()
            routes = simulate_four_route_cycles(
                rows[x_symbol],
                rows[y_symbol],
                config,
                initial_amount,
            )
            analysis = build_pair_strategy_analysis(
                rows[x_symbol],
                rows[y_symbol],
                config,
                initial_amount=initial_amount,
                routes=routes,
            )
        except Exception as exc:
            if "does not exist" in str(exc):
                return jsonify({"x_symbol": x_symbol, "y_symbol": y_symbol, "initial_amount": 100, "routes": []})
            return jsonify({"error": data_error_message(exc), "routes": []}), 400
        return jsonify(
            {
                "x_symbol": x_symbol,
                "y_symbol": y_symbol,
                "initial_amount": initial_amount,
                "prices": {"x": rows[x_symbol], "y": rows[y_symbol]},
                "routes": routes,
                "analysis": analysis,
                "chain_selection": chain_selection,
                "router_context": router_context,
            }
        )
    
    
    @app.get("/api/chart-symbols")
    def chart_symbols():
        try:
            limit = request_int_arg("limit", 500, minimum=len(ASSETS), maximum=1000)
            symbols = available_candidate_symbols(limit)
        except Exception as exc:
            if "does not exist" not in str(exc):
                return jsonify({"error": data_error_message(exc), "symbols": list(ASSETS.keys())}), 400
            symbols = list(ASSETS.keys())
        return jsonify({"symbols": symbols, "aave_symbols": list(ASSETS.keys())})
    
    
    @app.get("/api/binance-extremes/latest")
    def binance_extremes_latest():
        return jsonify({"extremes": safe_latest(latest_binance_extremes)})

    @app.get("/api/binance-velocity/candidates")
    def binance_velocity_candidates():
        side_limit = request_int_arg(
            "side_limit",
            DEFAULT_BINANCE_RAW_SIDE_LIMIT,
            minimum=1,
            maximum=DEFAULT_BINANCE_RAW_SIDE_LIMIT,
        )
        extremes = safe_latest(latest_binance_extremes_file)
        payload = build_velocity_candidate_pairs(extremes, side_limit=side_limit)
        return jsonify(payload)

    from web.data_routes.binance_market import register_binance_market_routes
    register_binance_market_routes(app, panel)
    @app.get("/api/arbitrage/latest")
    def arbitrage_latest():
        return jsonify({"simulation": safe_latest(latest_arbitrage_simulation)})
    
    
    @app.get("/api/trigger/latest")
    def trigger_latest():
        return jsonify({"trigger": safe_latest(latest_arbitrage_simulation)})
    
    
    @app.get("/api/executable-signal/latest")
    def executable_signal_latest():
        return jsonify({"executable_signal": safe_latest(latest_executable_signal)})
    
    
    @app.get("/api/execution-plan/quote")
    def execution_plan_quote():
        try:
            try:
                chain_selection = _optional_request_chain_selection(feature="dex_arbitrage")
            except ChainSelectionError as exc:
                return _chain_selection_error_response(exc)
            simulation = latest_arbitrage_simulation()
            if not simulation or not simulation.get("execution_plan"):
                return jsonify({"error": "latest arbitrage result has no execution_plan"}), 404
            assert_fresh_execution_plan(simulation)
            router_context = dex_router_context_for(chain_selection)
            router = router_context.get("router_address")
            last_error = None
            quote = None
            for rpc_url in dex_quote_rpc_urls_for(router_context, aave_rpc_urls()):
                try:
                    quote = quote_execution_plan(
                        simulation["execution_plan"],
                        rpc_url=rpc_url,
                        router_address=router,
                        slippage_bps=read_slippage_bps(),
                    )
                    break
                except Exception as exc:
                    last_error = exc
            if quote is None:
                raise last_error or RuntimeError("all AAVE RPC candidates failed")
        except Exception as exc:
            return jsonify({"error": data_error_message(exc)}), 400
        return jsonify({"quote": quote, "chain_selection": chain_selection, "router_context": router_context})
    
    
    @app.get("/api/execution-plan/payload")
    def execution_plan_payload():
        try:
            try:
                chain_selection = _optional_request_chain_selection(feature="dex_arbitrage")
            except ChainSelectionError as exc:
                return _chain_selection_error_response(exc)
            simulation = latest_arbitrage_simulation()
            if not simulation or not simulation.get("execution_plan"):
                return jsonify({"error": "latest arbitrage result has no execution_plan"}), 404
            assert_fresh_execution_plan(simulation)
            router_context = dex_router_context_for(chain_selection)
            router = router_context.get("router_address")
            last_error = None
            quote = None
            for rpc_url in dex_quote_rpc_urls_for(router_context, aave_rpc_urls()):
                try:
                    quote = quote_execution_plan(
                        simulation["execution_plan"],
                        rpc_url=rpc_url,
                        router_address=router,
                        slippage_bps=read_slippage_bps(),
                    )
                    break
                except Exception as exc:
                    last_error = exc
            if quote is None:
                raise last_error or RuntimeError("all AAVE RPC candidates failed")
            payload = build_execution_payload(
                simulation["execution_plan"],
                quote,
                PayloadConfig(
                    min_profit_usdc=request_float_arg("min_profit_usdc", 0, minimum=0),
                    deadline_seconds=request_int_arg("deadline_seconds", 600, minimum=1),
                ),
            )
        except Exception as exc:
            return jsonify({"error": data_error_message(exc)}), 400
        return jsonify({"payload": payload, "chain_selection": chain_selection, "router_context": router_context})
    
    
    @app.get("/api/dex-costs")
    def dex_costs():
        symbol = request.args.get("symbol", "AVAXUSDT").strip().upper()
        if symbol not in ASSETS:
            return jsonify({"error": f"unsupported symbol: {symbol}"}), 400
        try:
            amounts = parse_trade_usd_amounts(os.getenv("DEX_COST_USD_AMOUNTS"))
            reference_price = latest_reference_price(symbol)
            try:
                chain_selection = _optional_request_chain_selection(feature="dex_arbitrage")
            except ChainSelectionError as exc:
                return _chain_selection_error_response(exc)
            router_context = dex_router_context_for(chain_selection)
            router = router_context.get("router_address")
            last_error = None
            costs = None
            for rpc_url in dex_quote_rpc_urls_for(router_context, aave_rpc_urls()):
                try:
                    costs = [estimate_symbol_cost(rpc_url, symbol, amount, reference_price, router) for amount in amounts]
                    break
                except Exception as exc:
                    last_error = exc
            if costs is None:
                raise last_error or RuntimeError("all AAVE RPC candidates failed")
            payload = [
                {
                    "amount_usd": quote.amount_usd,
                    "buy_cost_percent": quote.buy_cost_percent,
                    "sell_cost_percent": quote.sell_cost_percent,
                    "roundtrip_cost_percent": quote.roundtrip_cost_percent,
                    "buy_price_usd": quote.buy_price_usd,
                    "sell_price_usd": quote.sell_price_usd,
                    "token_amount": quote.token_amount,
                }
                for quote in costs
                if quote is not None
            ]
        except Exception as exc:
            return jsonify({"error": data_error_message(exc)}), 400
        return jsonify({"symbol": symbol, "dex_name": dex_router_name_for(router_context), "reference_price_usd": reference_price, "costs": payload, "chain_selection": chain_selection, "router_context": router_context})
