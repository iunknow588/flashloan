from pathlib import Path

from flask import Response, jsonify

from core.build_info import build_info_payload
from web.account_pool_state_service import account_pool_state_payload
from web.page_state_service import (
    account_scan_state_payload,
    debt_pool_state_payload,
    execution_state_payload,
    market_observation_state_payload,
)


def _html_response(path: Path) -> Response:
    response = Response(path.read_text(encoding="utf-8"), mimetype="text/html")
    response.charset = "utf-8"
    return response


def register_page_routes(app, panel) -> None:
    def system_info_payload() -> dict:
        refresh_profile = (
            panel.liquidation_scan_refresh_profile()
            if hasattr(panel, "liquidation_scan_refresh_profile")
            else {}
        )
        scan_version = (
            panel.liquidation_scan_version()
            if hasattr(panel, "liquidation_scan_version")
            else "unknown"
        )
        return {
            "version": scan_version,
            "build": build_info_payload(),
            "scan_policy": {
                "strategy": "core_every_base_cycle_high_frequency_after_5m_borrow_health_after_30m",
                "core_opportunity_refresh_seconds": refresh_profile.get("core_opportunity_refresh_seconds", 1.0),
                "high_frequency_refresh_seconds": refresh_profile.get("high_frequency_refresh_seconds", 300.0),
                "borrow_health_refresh_seconds": refresh_profile.get("borrow_health_refresh_seconds", 1800.0),
            },
        }

    @app.get("/")
    def index():
        return _html_response(panel.LIQUIDATION_TEMPLATE_PATH)

    @app.get("/home")
    @app.get("/legacy")
    def legacy_control_panel():
        return panel.render_control_panel()

    @app.get("/liquidation")
    @app.get("/account-scan")
    @app.get("/audit")
    def liquidation_panel():
        return _html_response(panel.LIQUIDATION_TEMPLATE_PATH)

    @app.get("/execution")
    def execution_panel():
        return _html_response(panel.LIQUIDATION_ACCOUNT_TEMPLATE_PATH)

    @app.get("/liquidation/account")
    def liquidation_account_panel():
        return _html_response(panel.LIQUIDATION_ACCOUNT_TEMPLATE_PATH)

    @app.get("/market-observation")
    def market_observation_panel():
        return panel.render_control_panel()

    @app.get("/binance-market")
    @app.get("/dex-arbitrage")
    def binance_market_panel():
        return _html_response(panel.BINANCE_MARKET_TEMPLATE_PATH)

    @app.get("/config")
    def config_panel():
        return panel.render_control_panel()

    @app.get("/exchange-matrix")
    def exchange_matrix_panel():
        return _html_response(panel.EXCHANGE_MATRIX_TEMPLATE_PATH)

    @app.get("/opportunity-health")
    def opportunity_health_panel():
        return _html_response(panel.OPPORTUNITY_HEALTH_TEMPLATE_PATH)

    @app.get("/healthz")
    def healthz():
        return jsonify({"status": "ok"})

    @app.get("/api/system-info")
    def system_info():
        return jsonify(system_info_payload())

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
                "system_info": system_info_payload(),
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
