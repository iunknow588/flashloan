from flask import Response, jsonify

from web.account_pool_state_service import account_pool_state_payload
from web.page_state_service import (
    account_scan_state_payload,
    debt_pool_state_payload,
    execution_state_payload,
    market_observation_state_payload,
)


def register_page_routes(app, panel) -> None:
    @app.get("/")
    def index():
        return panel.render_control_panel()

    @app.get("/home")
    @app.get("/legacy")
    def legacy_control_panel():
        return panel.render_control_panel()

    @app.get("/liquidation")
    @app.get("/account-scan")
    @app.get("/audit")
    def liquidation_panel():
        return panel.LIQUIDATION_TEMPLATE_PATH.read_text(encoding="utf-8")

    @app.get("/execution")
    def execution_panel():
        return panel.LIQUIDATION_ACCOUNT_TEMPLATE_PATH.read_text(encoding="utf-8")

    @app.get("/liquidation/account")
    def liquidation_account_panel():
        return panel.LIQUIDATION_ACCOUNT_TEMPLATE_PATH.read_text(encoding="utf-8")

    @app.get("/market-observation")
    def market_observation_panel():
        return panel.render_control_panel()

    @app.get("/config")
    def config_panel():
        return panel.render_control_panel()

    @app.get("/exchange-matrix")
    def exchange_matrix_panel():
        return panel.EXCHANGE_MATRIX_TEMPLATE_PATH.read_text(encoding="utf-8")

    @app.get("/opportunity-health")
    def opportunity_health_panel():
        return panel.OPPORTUNITY_HEALTH_TEMPLATE_PATH.read_text(encoding="utf-8")

    @app.get("/healthz")
    def healthz():
        return jsonify({"status": "ok"})

    @app.get("/favicon.ico")
    def favicon():
        return Response(status=204)

    @app.get("/api/status")
    def status():
        with panel.observer_start_lock:
            panel.clear_stale_observer_start()
        running = panel.quick_observer_running()
        binance_extremes = panel.safe_latest(panel.latest_binance_extremes_file)
        control_status_current = panel.control_status_payload()
        reserve_cache = panel.safe_latest(panel.aave_reserve_cache)
        config = panel.strategy_config()
        symbols = panel.displayed_symbols(running or panel.observer_starting)
        binance_extremes = panel.restrict_extremes_to_symbols(binance_extremes, symbols)
        opportunity_rows = panel.opportunity_health_rows(binance_extremes, config)
        background_activity = panel.background_activity_payload(running, panel.observer_starting)
        return jsonify(
            {
                "running": running,
                "starting": panel.observer_starting,
                "start_error": panel.observer_start_error,
                "background_activity": background_activity,
                "observer_progress": panel.observer_progress_payload(running, panel.observer_starting, binance_extremes),
                "control_status": control_status_current,
                "system_monitor": panel.system_monitor_payload(
                    running,
                    panel.observer_starting,
                    binance_extremes,
                    control_status_current,
                    reserve_cache,
                    background_activity,
                ),
                "pid": panel.quick_observer_pid() if running else None,
                "symbols": symbols,
                "binance_extremes": binance_extremes,
                "opportunity_health_summary": panel.opportunity_health_summary(opportunity_rows, config),
                "arbitrage_simulation": panel.safe_latest(panel.latest_arbitrage_simulation_file),
                "executable_signal": panel.safe_latest(panel.latest_executable_signal),
                "aave_reserve_cache": reserve_cache,
                "borrow_target_universe": panel.safe_latest(panel.borrow_target_universe),
                "strategy_config": config,
                "sampling_profile": panel.unified_sampling_profile(config),
            }
        )

    @app.get("/api/debt-pool/state")
    def debt_pool_state():
        return jsonify(debt_pool_state_payload(panel))

    @app.get("/api/account-pool/state")
    def account_pool_state():
        return jsonify(account_pool_state_payload(panel))

    @app.get("/api/account-scan/state")
    def account_scan_state():
        return jsonify(account_scan_state_payload(panel))

    @app.get("/api/market-observation/state")
    def market_observation_state():
        return jsonify(market_observation_state_payload(panel))

    @app.get("/api/execution/state")
    def execution_state():
        return jsonify(execution_state_payload(panel))
