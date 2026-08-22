from pathlib import Path

from flask import Response, jsonify, request, send_file

from core.chain_selection import ChainSelectionError
from core.build_info import build_info_payload
from core.market_config import (
    DEFAULT_BNB_CHAIN_ID,
    DEFAULT_BNB_DEX_MARKET_ID,
    DEFAULT_COW_DEX_MARKET_ID,
    DEFAULT_COW_VIRTUAL_CHAIN_ID,
    DEFAULT_ROBINHOOD_CHAIN_ID,
    DEFAULT_ROBINHOOD_DEX_MARKET_ID,
)
from account_pool import account_pool_state_payload
from page_state import (
    account_scan_state_payload,
    debt_pool_state_payload,
    execution_state_payload,
    market_observation_state_payload,
)
from web.chain_selection import inject_chain_selector_markup
from web.app_shell import inject_app_shell_markup
from web.market_page_registry import resolve_market_page_variant
from web.workflow_context import inject_workflow_context_markup


CHAIN_SELECTOR_SCRIPT = Path(__file__).resolve().parent / "static" / "chain_selector.js"


def _request_scope_defaults(
    default_market_id: str | None,
    default_chain_id: int | None,
) -> tuple[str | None, int | None]:
    market_id = request.args.get("market_id", "").strip() or default_market_id
    raw_chain_id = request.args.get("chain_id", "").strip()
    if not raw_chain_id:
        return market_id, default_chain_id
    try:
        return market_id, int(raw_chain_id)
    except ValueError:
        return market_id, default_chain_id


def _html_response(
    path: Path,
    *,
    feature: str | None = None,
    default_market_id: str | None = None,
    default_chain_id: int | None = None,
    page_surface: str | None = None,
    include_chain_selector: bool = True,
) -> Response:
    html = path.read_text(encoding="utf-8")
    selected_market_id, selected_chain_id = _request_scope_defaults(default_market_id, default_chain_id)
    shell_enabled = request.args.get("embed", "").strip().lower() not in {"1", "true", "yes"}
    if shell_enabled:
        html = inject_app_shell_markup(
            html,
            current_path=request.path,
            feature=feature,
            default_market_id=default_market_id,
            default_chain_id=default_chain_id,
            selected_market_id=selected_market_id,
            selected_chain_id=selected_chain_id,
            include_chain_selector=include_chain_selector,
        )
    if page_surface:
        html = html.replace(
            "<body>",
            f'<body data-page-surface="{page_surface}">',
            1,
        )
    if include_chain_selector:
        html = inject_chain_selector_markup(
            html,
            feature=feature,
            default_market_id=default_market_id,
            default_chain_id=default_chain_id,
        )
    html = inject_workflow_context_markup(html)
    response = Response(html, mimetype="text/html")
    response.charset = "utf-8"
    return response


def _panel_html_response(
    html: str,
    *,
    feature: str | None = None,
    default_market_id: str | None = None,
    default_chain_id: int | None = None,
    include_chain_selector: bool = True,
) -> Response:
    selected_market_id, selected_chain_id = _request_scope_defaults(default_market_id, default_chain_id)
    shell_enabled = request.args.get("embed", "").strip().lower() not in {"1", "true", "yes"}
    if shell_enabled:
        html = inject_app_shell_markup(
            html,
            current_path=request.path,
            feature=feature,
            default_market_id=default_market_id,
            default_chain_id=default_chain_id,
            selected_market_id=selected_market_id,
            selected_chain_id=selected_chain_id,
            include_chain_selector=include_chain_selector,
        )
    if include_chain_selector:
        html = inject_chain_selector_markup(
            html,
            feature=feature,
            default_market_id=default_market_id,
            default_chain_id=default_chain_id,
        )
    html = inject_workflow_context_markup(html)
    response = Response(
        html,
        mimetype="text/html",
    )
    response.charset = "utf-8"
    return response


def _request_chain_id() -> int | None:
    raw = request.args.get("chain_id", "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        raise ChainSelectionError("invalid_chain_id", f"invalid chain_id: {raw}") from None


def _panel_database_url(panel) -> str | None:
    getter = getattr(panel, "database_url_or_none", None)
    if not callable(getter):
        return None
    try:
        return getter()
    except Exception:
        return None


def _market_page_response(
    panel,
    *,
    template_path: Path | None = None,
    default_market_id: str | None = None,
    default_chain_id: int | None = None,
) -> Response:
    try:
        variant = resolve_market_page_variant(
            request.path,
            market_id=request.args.get("market_id", "").strip() or None,
            chain_id=_request_chain_id(),
            database_url=_panel_database_url(panel),
            default_market_id=default_market_id,
            default_chain_id=default_chain_id,
        )
    except ChainSelectionError as exc:
        return jsonify({"ok": False, "error": exc.message, "code": exc.code}), 400
    if variant is None:
        return jsonify({"ok": False, "error": "page market variant is not registered", "code": "page_variant_missing"}), 500
    selected_template = template_path
    if variant.template_kind == "robinhood_shadow":
        selected_template = panel.ROBINHOOD_DEX_TEMPLATE_PATH
    elif variant.template_kind == "single_chain":
        selected_template = panel.DEX_SINGLE_CHAIN_TEMPLATE_PATH
    elif selected_template is None and variant.feature == "dex_arbitrage":
        selected_template = panel.BINANCE_MARKET_TEMPLATE_PATH
    elif selected_template is None:
        selected_template = panel.LIQUIDATION_TEMPLATE_PATH
    return _html_response(
        selected_template,
        feature=variant.feature,
        default_market_id=variant.market_id,
        default_chain_id=variant.chain_id,
        page_surface=variant.surface,
    )


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
    @app.get("/bot")
    def index():
        return _market_page_response(panel)

    @app.get("/home")
    @app.get("/legacy")
    def legacy_control_panel():
        return _panel_html_response(panel.render_control_panel())

    @app.get("/liquidation")
    @app.get("/bot/borrow-pool")
    @app.get("/account-scan")
    @app.get("/bot/account-scan")
    @app.get("/audit")
    @app.get("/bot/audit")
    def liquidation_panel():
        return _market_page_response(panel)

    @app.get("/execution")
    @app.get("/bot/execution")
    def execution_panel():
        return _market_page_response(panel, template_path=panel.LIQUIDATION_ACCOUNT_TEMPLATE_PATH)

    @app.get("/liquidation/account")
    def liquidation_account_panel():
        return _market_page_response(panel, template_path=panel.LIQUIDATION_ACCOUNT_TEMPLATE_PATH)

    @app.get("/market-observation")
    @app.get("/dex/market-observation")
    def dex_market_observation_panel():
        return _market_page_response(panel, default_market_id=DEFAULT_COW_DEX_MARKET_ID, default_chain_id=DEFAULT_COW_VIRTUAL_CHAIN_ID)

    @app.get("/ops")
    def operations_panel():
        return _panel_html_response(panel.render_control_panel(), include_chain_selector=False)

    @app.get("/binance-market")
    @app.get("/dex-arbitrage")
    @app.get("/dex")
    @app.get("/dex/quotes")
    @app.get("/dex/submissions")
    def binance_market_panel():
        return _market_page_response(panel, default_market_id=DEFAULT_COW_DEX_MARKET_ID, default_chain_id=DEFAULT_COW_VIRTUAL_CHAIN_ID)

    @app.get("/dex-pools")
    @app.get("/candidate-pools")
    @app.get("/dex/candidates")
    def dex_pool_candidates_panel():
        return _market_page_response(panel, template_path=panel.DEX_POOL_CANDIDATES_TEMPLATE_PATH, default_market_id=DEFAULT_BNB_DEX_MARKET_ID, default_chain_id=DEFAULT_BNB_CHAIN_ID)

    @app.get("/robinhood-dex")
    @app.get("/robinhood-dex/candidates")
    @app.get("/dex/robinhood-shadow")
    def robinhood_dex_candidates_panel():
        return _market_page_response(panel, default_market_id=DEFAULT_ROBINHOOD_DEX_MARKET_ID, default_chain_id=DEFAULT_ROBINHOOD_CHAIN_ID)

    @app.get("/config")
    @app.get("/settings")
    def config_panel():
        return _panel_html_response(panel.render_control_panel(), include_chain_selector=False)

    @app.get("/exchange-matrix")
    @app.get("/analytics/exchange-matrix")
    def exchange_matrix_panel():
        return _html_response(panel.EXCHANGE_MATRIX_TEMPLATE_PATH, include_chain_selector=False)

    @app.get("/opportunity-health")
    @app.get("/analytics/opportunity-health")
    def opportunity_health_panel():
        return _html_response(panel.OPPORTUNITY_HEALTH_TEMPLATE_PATH, include_chain_selector=False)

    @app.get("/healthz")
    def healthz():
        return jsonify({"status": "ok"})

    @app.get("/api/system-info")
    def system_info():
        return jsonify(system_info_payload())

    @app.get("/static/chain-selector.js")
    def chain_selector_script():
        return send_file(CHAIN_SELECTOR_SCRIPT, mimetype="application/javascript")

    @app.get("/static/workflow-context.js")
    def workflow_context_script():
        return send_file(
            Path(__file__).resolve().parent / "static" / "workflow_context.js",
            mimetype="application/javascript",
        )

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
