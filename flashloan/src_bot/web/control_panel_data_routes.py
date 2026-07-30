from flask import jsonify, request

PANEL = None


def panel_call(name: str, *args, **kwargs):
    return getattr(PANEL, name)(*args, **kwargs)


def register_data_routes(app, panel) -> None:
    global PANEL
    PANEL = panel
    globals().update(vars(panel))
    @app.get("/api/liquidation-health")
    def liquidation_health_api():
        force = request.args.get("force", "").strip().lower() in {"1", "true", "yes"}
        return jsonify(panel_call("liquidation_health_payload", force=force))
    
    
    @app.post("/api/liquidation-discovery")
    def liquidation_discovery_api():
        payload = request.get_json(silent=True) or {}
        force_full = request.args.get("full", "").strip().lower() in {"1", "true", "yes"} or bool(payload.get("full"))
        result = panel_call("discover_and_sync_liquidation_accounts", force_full=force_full)
        LIQUIDATION_SCAN_CACHE["updated_at"] = 0.0
        return jsonify(result)
    
    
    @app.get("/api/liquidation-settings")
    def liquidation_settings_api():
        config = liquidation_runtime_config()
        return jsonify(
            {
                "retention_days": liquidation_retention_days(),
                "scan_interval_seconds": liquidation_scan_interval_seconds(),
                "discovery_interval_seconds": liquidation_discovery_interval_seconds(),
                "raw": config,
            }
        )
    
    
    @app.post("/api/liquidation-settings")
    def update_liquidation_settings_api():
        payload = request.get_json(silent=True) or {}
        config = write_liquidation_runtime_config(payload)
        LIQUIDATION_ACCOUNT_CACHE["updated_at"] = 0.0
        LIQUIDATION_SCAN_CACHE["updated_at"] = 0.0
        return jsonify(
            {
                "saved": True,
                "retention_days": liquidation_retention_days(),
                "scan_interval_seconds": liquidation_scan_interval_seconds(),
                "discovery_interval_seconds": liquidation_discovery_interval_seconds(),
                "raw": config,
            }
        )
    
    
    @app.get("/api/liquidation/account")
    def liquidation_account_api():
        account = request.args.get("account", "").strip()
        if not account:
            return jsonify({"error": "account is required"}), 400
        try:
            return jsonify(panel_call("liquidation_account_payload", account))
        except Exception as exc:
            return jsonify({"error": str(exc), "account": account}), 400
    
    
    @app.get("/api/liquidation/account/payload")
    def liquidation_account_payload_api():
        account = request.args.get("account", "").strip()
        if not account:
            return jsonify({"error": "account is required"}), 400
        try:
            deadline_seconds = int(request.args.get("deadline_seconds", "300"))
            allow_zero_min_out = request.args.get("allow_zero_min_out", "").strip().lower() in {"1", "true", "yes"}
            return jsonify(
                panel_call(
                    "liquidation_execution_payload_for_account",
                    account,
                    deadline_seconds=deadline_seconds,
                    allow_zero_min_out=allow_zero_min_out,
                )
            )
        except Exception as exc:
            return jsonify({"error": str(exc), "account": account}), 400
    
    
    @app.post("/api/liquidation/account/preflight")
    def liquidation_account_preflight_api():
        account = request.args.get("account", "").strip()
        if not account:
            return jsonify({"error": "account is required"}), 400
        try:
            payload = panel_call("liquidation_execution_payload_for_account", account)
            return jsonify(panel_call("simulate_liquidation_static_call", payload))
        except Exception as exc:
            return jsonify({"error": str(exc), "account": account}), 400
    
    
    @app.post("/api/liquidation/account/execute")
    def liquidation_account_execute_api():
        account = request.args.get("account", "").strip()
        if not account:
            return jsonify({"error": "account is required"}), 400
        payload: dict | None = None
        try:
            payload = panel_call("liquidation_execution_payload_for_account", account, require_executor=False)
            return jsonify(panel_call("execute_self_funded_liquidation_transaction", payload))
        except Exception as exc:
            response = {"error": str(exc), "account": account}
            if isinstance(payload, dict):
                account_report = payload.get("account_report") or {}
                response.update(
                    {
                        "executor": payload.get("executor"),
                        "request": payload.get("request") or {},
                        "preflight": payload.get("preflight") or {},
                        "account_report": account_report,
                        "execution_plan": account_report.get("execution_plan") if isinstance(account_report, dict) else None,
                        "execution_controls": payload.get("execution_controls") or panel_call("liquidation_execution_controls"),
                    }
                )
            return jsonify(response), 400
    
    
    @app.post("/api/liquidation/account/flashloan")
    def liquidation_account_flashloan_api():
        account = request.args.get("account", "").strip()
        if not account:
            return jsonify({"error": "account is required"}), 400
        payload: dict | None = None
        try:
            payload = panel_call("liquidation_execution_payload_for_account", account)
            return jsonify(panel_call("execute_flashloan_liquidation_transaction", payload))
        except Exception as exc:
            response = {"error": str(exc), "account": account}
            if isinstance(payload, dict):
                account_report = payload.get("account_report") or {}
                response.update(
                    {
                        "executor": payload.get("executor"),
                        "request": payload.get("request") or {},
                        "preflight": payload.get("preflight") or {},
                        "account_report": account_report,
                        "execution_plan": account_report.get("execution_plan") if isinstance(account_report, dict) else None,
                        "execution_controls": payload.get("execution_controls") or panel_call("liquidation_execution_controls"),
                    }
                )
            return jsonify(response), 400
    
    
    @app.get("/api/liquidation/samples")
    def liquidation_samples_api():
        manifest = panel_call("liquidation_sample_manifest")
        if not manifest:
            return jsonify({"error": "liquidation sample library not found", "samples": []}), 404
        return jsonify(manifest)
    
    
    @app.post("/api/liquidation/accounts")
    def liquidation_accounts_api():
        payload = request.get_json(silent=True) or {}
        raw_accounts = payload.get("accounts")
        accounts = normalize_liquidation_account_values(raw_accounts)
        if not accounts:
            return jsonify({"error": "accounts is required"}), 400
        try:
            database_url = panel_call("database_url_or_none")
            if database_url:
                panel_call("ensure_database_schema", database_url)
                panel_call("db_upsert_liquidation_accounts", database_url, accounts, source=str(payload.get("source") or "manual"), active=True)
                panel_call("db_prune_liquidation_accounts", database_url, retained_days=panel_call("liquidation_retention_days"))
            else:
                return jsonify({"error": "DATABASE_URL is required"}), 400
        except Exception as exc:
            return jsonify({"error": str(exc)}), 400
        LIQUIDATION_ACCOUNT_CACHE["updated_at"] = 0.0
        LIQUIDATION_SCAN_CACHE["updated_at"] = 0.0
        return jsonify({"saved": True, "count": len(accounts), "source": "database", "accounts": accounts})
    
    
    @app.get("/api/opportunity-health")
    def opportunity_health_api():
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
            }
        )
    
    
    @app.get("/api/db-summary")
    def db_summary():
        return jsonify(
            {
                "rows": observation_count(),
                "db_counts": database_table_counts(),
                "trade_stats": safe_latest(lambda: read_trade_stats(configured_database_url())),
                "testnet_trade_stats": safe_latest(lambda: read_testnet_trade_stats(REPO_ROOT)),
            }
        )
    
    
    @app.get("/api/velocity-timepoints")
    def velocity_timepoints():
        try:
            limit = max(1, min(int(request.args.get("limit", "200")), 500))
            rows = recent_velocity_timepoints(limit)
        except Exception as exc:
            if "does not exist" in str(exc):
                return jsonify({"timepoints": []})
            return jsonify({"error": str(exc), "timepoints": []}), 400
        return jsonify({"timepoints": rows})
    
    
    @app.get("/api/velocity-summary")
    def velocity_summary():
        try:
            raw_id = request.args.get("id", "").strip()
            snapshot_id = int(raw_id) if raw_id else None
            snapshot = velocity_timepoint_snapshot(snapshot_id)
            if not snapshot and snapshot_id is not None:
                snapshot = velocity_timepoint_snapshot(None)
            if not snapshot:
                return jsonify({"error": "no velocity timepoint found", "rows": []}), 404
            return jsonify(build_velocity_summary(snapshot))
        except Exception as exc:
            if "does not exist" in str(exc):
                return jsonify({"error": "initialize database and collect velocity windows first", "rows": []})
            return jsonify({"error": str(exc), "rows": []}), 400
    
    
    @app.get("/api/strategy-config")
    def get_strategy_config():
        config = strategy_config()
        return jsonify({"config": config, "sampling_profile": unified_sampling_profile(config), "running": is_observer_running()})
    
    
    @app.post("/api/strategy-config")
    def post_strategy_config():
        try:
            config = write_strategy_config(request.get_json(silent=True) or {})
        except Exception as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify({"config": config, "sampling_profile": unified_sampling_profile(config), "restart_required": is_observer_running()})
    
    
    @app.get("/api/trade-stats")
    def get_trade_stats():
        return jsonify({"stats": safe_latest(lambda: read_trade_stats(configured_database_url()))})
    
    
    @app.get("/api/testnet-trade-stats")
    def get_testnet_trade_stats():
        return jsonify({"stats": safe_latest(lambda: read_testnet_trade_stats(REPO_ROOT))})
    
    
    @app.get("/api/observations")
    def observations():
        symbol = request.args.get("symbol", "AVAXUSDT").strip().upper()
        try:
            limit = max(2, min(int(request.args.get("limit", "120")), 1000))
            rows = recent_observations(symbol, limit) if symbol in ASSETS else []
            mode = "aave_observations" if rows else "binance_price_history"
            if not rows:
                rows = recent_binance_price_history(symbol, limit)
        except Exception as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify(
            {
                "symbol": symbol,
                "limit": limit,
                "mode": mode,
                "supports_aave": symbol in ASSETS and mode == "aave_observations",
                "supports_dex_costs": symbol in ASSETS and mode == "aave_observations",
                "fee_slippage_percent": configured_fee_slippage_percent(),
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
            limit = max(2, min(int(request.args.get("limit", "120")), 1000))
            rows = recent_aave_pair_prices(x_symbol, y_symbol, limit)
        except Exception as exc:
            return jsonify({"error": str(exc), "rows": []}), 400
        return jsonify({"x_symbol": x_symbol, "y_symbol": y_symbol, "limit": limit, "rows": rows})
    
    
    @app.get("/api/binance-pair-prices")
    def binance_pair_prices():
        x_symbol = request.args.get("x", "").strip().upper()
        y_symbol = request.args.get("y", "").strip().upper()
        if not x_symbol or not y_symbol or x_symbol == y_symbol:
            return jsonify({"error": "select two different symbols", "rows": []}), 400
        try:
            limit = max(2, min(int(request.args.get("limit", "120")), 1000))
            rows = recent_binance_pair_prices(x_symbol, y_symbol, limit)
        except Exception as exc:
            if "does not exist" in str(exc):
                return jsonify({"x_symbol": x_symbol, "y_symbol": y_symbol, "limit": limit, "rows": []})
            return jsonify({"error": str(exc), "rows": []}), 400
        return jsonify({"x_symbol": x_symbol, "y_symbol": y_symbol, "limit": limit, "rows": rows})
    
    
    @app.get("/api/pair-route-profits")
    def pair_route_profits():
        x_symbol = request.args.get("x", "").strip().upper()
        y_symbol = request.args.get("y", "").strip().upper()
        if not x_symbol or not y_symbol or x_symbol == y_symbol:
            return jsonify({"error": "select two different symbols", "routes": []}), 400
        try:
            initial_amount = max(0.000001, float(request.args.get("initial", "100")))
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
            routes = simulate_four_route_cycles(
                rows[x_symbol],
                rows[y_symbol],
                arbitrage_config_from_strategy(),
                initial_amount,
            )
        except Exception as exc:
            if "does not exist" in str(exc):
                return jsonify({"x_symbol": x_symbol, "y_symbol": y_symbol, "initial_amount": 100, "routes": []})
            return jsonify({"error": str(exc), "routes": []}), 400
        return jsonify(
            {
                "x_symbol": x_symbol,
                "y_symbol": y_symbol,
                "initial_amount": initial_amount,
                "prices": {"x": rows[x_symbol], "y": rows[y_symbol]},
                "routes": routes,
            }
        )
    
    
    @app.get("/api/chart-symbols")
    def chart_symbols():
        try:
            limit = max(len(ASSETS), min(int(request.args.get("limit", "500")), 1000))
            symbols = available_candidate_symbols(limit)
        except Exception as exc:
            if "does not exist" not in str(exc):
                return jsonify({"error": str(exc), "symbols": list(ASSETS.keys())}), 400
            symbols = list(ASSETS.keys())
        return jsonify({"symbols": symbols, "aave_symbols": list(ASSETS.keys())})
    
    
    @app.get("/api/binance-extremes/latest")
    def binance_extremes_latest():
        return jsonify({"extremes": safe_latest(latest_binance_extremes)})
    
    
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
            simulation = latest_arbitrage_simulation()
            if not simulation or not simulation.get("execution_plan"):
                return jsonify({"error": "latest arbitrage result has no execution_plan"}), 404
            assert_fresh_execution_plan(simulation)
            router = os.getenv("DEX_ROUTER_ADDRESS", "0x60aE616a2155Ee3d9A68541Ba4544862310933d4").strip()
            last_error = None
            quote = None
            for rpc_url in aave_rpc_urls():
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
            return jsonify({"error": str(exc)}), 400
        return jsonify({"quote": quote})
    
    
    @app.get("/api/execution-plan/payload")
    def execution_plan_payload():
        try:
            simulation = latest_arbitrage_simulation()
            if not simulation or not simulation.get("execution_plan"):
                return jsonify({"error": "latest arbitrage result has no execution_plan"}), 404
            assert_fresh_execution_plan(simulation)
            router = os.getenv("DEX_ROUTER_ADDRESS", "0x60aE616a2155Ee3d9A68541Ba4544862310933d4").strip()
            last_error = None
            quote = None
            for rpc_url in aave_rpc_urls():
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
                    min_profit_usdc=float(request.args.get("min_profit_usdc", "0")),
                    deadline_seconds=int(request.args.get("deadline_seconds", "600")),
                ),
            )
        except Exception as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify({"payload": payload})
    
    
    @app.get("/api/dex-costs")
    def dex_costs():
        symbol = request.args.get("symbol", "AVAXUSDT").strip().upper()
        if symbol not in ASSETS:
            return jsonify({"error": f"unsupported symbol: {symbol}"}), 400
        try:
            amounts = parse_trade_usd_amounts(os.getenv("DEX_COST_USD_AMOUNTS"))
            reference_price = latest_reference_price(symbol)
            router = os.getenv("DEX_ROUTER_ADDRESS", "0x60aE616a2155Ee3d9A68541Ba4544862310933d4").strip()
            last_error = None
            costs = None
            for rpc_url in aave_rpc_urls():
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
            return jsonify({"error": str(exc)}), 400
        return jsonify({"symbol": symbol, "dex_name": "Trader Joe V2", "reference_price_usd": reference_price, "costs": payload})
