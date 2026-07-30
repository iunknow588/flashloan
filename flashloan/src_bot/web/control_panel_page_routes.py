from flask import Response, jsonify


def register_page_routes(app, panel) -> None:
    @app.get("/")
    def index():
        return panel.render_control_panel()

    @app.get("/liquidation")
    def liquidation_panel():
        return panel.LIQUIDATION_TEMPLATE_PATH.read_text(encoding="utf-8")

    @app.get("/healthz")
    def healthz():
        return jsonify({"status": "ok"})

    @app.get("/favicon.ico")
    def favicon():
        return Response(status=204)

    @app.get("/api/status")
    def status():
        running = panel.quick_observer_running()
        binance_extremes = panel.safe_latest(panel.latest_binance_extremes_file)
        control_status_current = panel.control_status_payload()
        reserve_cache = panel.safe_latest(panel.aave_reserve_cache)
        config = panel.strategy_config()
        symbols = panel.displayed_symbols(running or panel.observer_starting)
        binance_extremes = panel.restrict_extremes_to_symbols(binance_extremes, symbols)
        opportunity_rows = panel.opportunity_health_rows(binance_extremes, config)
        return jsonify(
            {
                "running": running,
                "starting": panel.observer_starting,
                "start_error": panel.observer_start_error,
                "observer_progress": panel.observer_progress_payload(running, panel.observer_starting, binance_extremes),
                "control_status": control_status_current,
                "system_monitor": panel.system_monitor_payload(
                    running,
                    panel.observer_starting,
                    binance_extremes,
                    control_status_current,
                    reserve_cache,
                ),
                "pid": panel.quick_observer_pid() if running else None,
                "symbols": symbols,
                "binance_extremes": binance_extremes,
                "opportunity_health": opportunity_rows,
                "opportunity_health_summary": panel.opportunity_health_summary(opportunity_rows, config),
                "arbitrage_simulation": panel.safe_latest(panel.latest_arbitrage_simulation_file),
                "executable_signal": panel.safe_latest(panel.latest_executable_signal),
                "aave_reserve_cache": reserve_cache,
                "borrow_target_universe": panel.safe_latest(panel.borrow_target_universe),
                "strategy_config": config,
                "sampling_profile": panel.unified_sampling_profile(config),
            }
        )
