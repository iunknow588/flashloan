from pathlib import Path
import re

from flask import Response, jsonify, request, send_file

from core.chain_selection import ChainSelectionError
from core.build_info import build_info_payload
from core.flash_loan_pool_discovery import (
    protocol_options_for_chain,
    query_flash_loan_pool_discovery,
    refresh_flash_loan_pool_discovery_cache,
    start_flash_loan_pool_discovery_idle_worker,
)
from core.chain_data_registry import default_chain_scope, default_flash_loan_pool_scope
from core.market_config import (
    DEFAULT_ROBINHOOD_CHAIN_ID,
    DEFAULT_ROBINHOOD_DEX_MARKET_ID,
    supported_market_summaries,
)
from core.market_identity import canonical_market_id_from_legacy
from account_pool import account_pool_state_payload
from page_state import (
    account_scan_state_payload,
    debt_pool_state_payload,
    execution_state_payload,
    market_observation_state_payload,
)
from web.chain_selection import inject_chain_selector_markup
from web.app_shell import inject_app_shell_markup
from web.dex_provenance import inject_dex_provenance_markup
from web.flash_loan_pools import flash_loan_pools_payload
from web.market_page_registry import resolve_market_page_variant
from web.workflow_context import inject_workflow_context_markup


CHAIN_SELECTOR_SCRIPT = Path(__file__).resolve().parent / "static" / "chain_selector.js"
DEX_STRATEGY_SELECTOR_SCRIPT = Path(__file__).resolve().parent / "static" / "dex_strategy_selector.js"
APP_SHELL_SCRIPT = Path(__file__).resolve().parent / "static" / "app_shell.js"


def _add_body_attribute(html: str, name: str, value: str) -> str:
    pattern = re.compile(r"<body([^>]*)>", re.IGNORECASE)

    def replace(match: re.Match) -> str:
        attrs = match.group(1) or ""
        if re.search(rf"\s{name}=", attrs, re.IGNORECASE):
            return match.group(0)
        return f'<body{attrs} {name}="{value}">'

    return pattern.sub(replace, html, count=1)


def _add_body_class(html: str, class_name: str) -> str:
    pattern = re.compile(r"<body([^>]*)>", re.IGNORECASE)

    def replace(match: re.Match) -> str:
        attrs = match.group(1) or ""
        class_match = re.search(r'\sclass=(["\'])(.*?)\1', attrs, re.IGNORECASE)
        if not class_match:
            return f'<body{attrs} class="{class_name}">'
        classes = class_match.group(2).split()
        if class_name in classes:
            return match.group(0)
        next_value = " ".join([*classes, class_name])
        start, end = class_match.span(2)
        return f"<body{attrs[:start]}{next_value}{attrs[end:]}>"

    return pattern.sub(replace, html, count=1)


def _inject_before_head_close(html: str, markup: str) -> str:
    if "</head>" in html.lower():
        return re.sub(r"</head>", markup + "</head>", html, count=1, flags=re.IGNORECASE)
    return markup + html


def _prepare_embedded_html(html: str) -> str:
    html = _add_body_class(html, "embedded")
    return _inject_before_head_close(
        html,
        """
<style id="flashloan-embedded-style">
  body.embedded { padding: 0 !important; }
  body.embedded > header { display: none !important; }
  body.embedded .chain-selector-shell { display: none !important; }
  body.embedded main { margin-top: 0 !important; }
</style>
<script>
(function(){
  if (window.parent === window) return;
  document.addEventListener("click", function(event) {
    var link = event.target && event.target.closest ? event.target.closest("a[href]") : null;
    if (!link || event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    if (link.target && link.target !== "_self") return;
    if (link.hasAttribute("download")) return;
    var url = new URL(link.getAttribute("href"), window.location.origin);
    if (url.origin !== window.location.origin || url.pathname.indexOf("/api/") === 0 || url.pathname.indexOf("/static/") === 0) return;
    event.preventDefault();
    window.parent.postMessage({type: "flashloan:navigate", href: url.pathname + url.search + url.hash}, window.location.origin);
  });
})();
</script>
""",
    )


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


def _request_protocol_id() -> str | None:
    raw = request.args.get("protocol_id", "").strip()
    if raw:
        return raw
    for item in request.args.get("protocols", "").replace("|", ",").split(","):
        protocol = item.strip()
        if protocol:
            return protocol
    return None


def _request_shell_feature() -> str:
    feature = request.args.get("feature", "").strip().lower()
    if feature in {"liquidation", "dex_arbitrage"}:
        return feature
    return "liquidation"


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
    else:
        html = _prepare_embedded_html(html)
    if page_surface:
        html = _add_body_attribute(html, "data-page-surface", page_surface)
    if feature == "dex_arbitrage" and not shell_enabled:
        html = inject_dex_provenance_markup(html)
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
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
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
    else:
        html = _prepare_embedded_html(html)
        if feature == "dex_arbitrage":
            html = inject_dex_provenance_markup(html)
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
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


def _request_chain_id() -> int | None:
    raw = request.args.get("chain_id", "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        raise ChainSelectionError("invalid_chain_id", f"invalid chain_id: {raw}") from None


def _request_optional_int(name: str) -> int | None:
    raw = request.args.get(name, "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        raise ChainSelectionError(f"invalid_{name}", f"invalid {name}: {raw}") from None


def _panel_database_url(panel) -> str | None:
    getter = getattr(panel, "database_url_or_none", None)
    if not callable(getter):
        return None
    try:
        return getter()
    except Exception:
        return None


def _flash_loan_discovery_refresh_kwargs(panel) -> dict:
    return {"market_rows": supported_market_summaries(_panel_database_url(panel))}


def _request_protocols() -> list[str]:
    return [
        item.strip().lower()
        for item in request.args.get("protocols", "").replace("|", ",").split(",")
        if item.strip()
    ]


def _request_bool_arg(name: str) -> bool:
    body = request.get_json(silent=True) or {}
    return request.args.get(name, "").strip().lower() in {"1", "true", "yes", "on"} or bool(body.get(name))


def _default_scope_kwargs(feature: str) -> dict[str, object]:
    scope = default_chain_scope(feature)
    return {
        "default_market_id": str(scope["market_id"]),
        "default_chain_id": int(scope["chain_id"]),
    }


def _refresh_flash_loan_pool_sources(*, chain_id: int | None, protocols: list[str], full: bool, panel) -> dict:
    steps: list[dict] = []
    errors: list[str] = []
    try:
        discovery = refresh_flash_loan_pool_discovery_cache(**_flash_loan_discovery_refresh_kwargs(panel))
        steps.append(
            {
                "source": "flash_loan_pool_discovery",
                "ok": True,
                "chainCount": discovery.get("chain_count"),
                "protocolCount": discovery.get("protocol_count"),
            }
        )
    except Exception as exc:
        errors.append(f"flash_loan_pool_discovery: {exc}")
        steps.append({"source": "flash_loan_pool_discovery", "ok": False, "error": str(exc)})

    protocol_set = set(protocols)
    refresh_v4 = chain_id == 46630 and (not protocol_set or "uniswap_v4" in protocol_set)
    if refresh_v4:
        if full:
            try:
                from intent_trade.chains.robinhood_testnet.v4_pool_index import write_v4_pool_index_from_initialize_logs

                result = write_v4_pool_index_from_initialize_logs()
                steps.append(
                    {
                        "source": "robinhood_testnet_v4_pool_index",
                        "ok": True,
                        "outputPath": result.get("outputPath"),
                        "poolCount": len(result.get("pools") or []),
                        "poolKeyCount": len(result.get("poolKeys") or result.get("pool_keys") or []),
                    }
                )
            except Exception as exc:
                errors.append(f"robinhood_testnet_v4_pool_index: {exc}")
                steps.append({"source": "robinhood_testnet_v4_pool_index", "ok": False, "error": str(exc)})
        try:
            from intent_trade.chains.robinhood_testnet.v4_pool_liquidity import write_v4_pool_liquidity_from_contract_evidence

            result = write_v4_pool_liquidity_from_contract_evidence(
                token_balance_scan_scope="active_plus_known",
                multicall_chunk_size=200,
                max_token_balance_scan_seconds=None,
            )
            steps.append(
                {
                    "source": "robinhood_testnet_v4_pool_liquidity",
                    "ok": True,
                    "outputPath": result.get("outputPath"),
                    "poolCount": len(result.get("pools") or []),
                    "poolKeyCount": len(result.get("poolKeys") or result.get("pool_keys") or []),
                    "tokenBalanceScanScope": result.get("tokenBalanceScanScope"),
                    "tokenBalanceScanCount": result.get("tokenBalanceScanCount"),
                    "tokenBalanceSourceCount": result.get("tokenBalanceSourceCount"),
                }
            )
        except Exception as exc:
            errors.append(f"robinhood_testnet_v4_pool_liquidity: {exc}")
            steps.append({"source": "robinhood_testnet_v4_pool_liquidity", "ok": False, "error": str(exc)})

    return {
        "ok": not errors,
        "requested": "full" if full else "incremental",
        "source": "flash_loan_pool_static_store_refresh",
        "steps": steps,
        "errors": errors,
        "message": "资金池全量刷新完成" if full and not errors else ("资金池增量补全完成" if not errors else "资金池刷新完成但存在错误"),
    }


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
            protocol_id=_request_protocol_id(),
            database_url=_panel_database_url(panel),
            default_market_id=default_market_id,
            default_chain_id=default_chain_id,
        )
    except ChainSelectionError as exc:
        return jsonify({"ok": False, "error": exc.message, "code": exc.code}), 400
    if variant is None:
        return jsonify({"ok": False, "error": "page market variant is not registered", "code": "page_variant_missing"}), 500
    selected_template = template_path
    if variant.page_key == "robinhood_dex":
        selected_template = panel.ROBINHOOD_DEX_TEMPLATE_PATH
    elif variant.feature == "dex_arbitrage" and variant.surface == "overview":
        selected_template = panel.DEX_OVERVIEW_TEMPLATE_PATH
    elif variant.feature == "dex_arbitrage" and variant.surface == "observation":
        selected_template = panel.MARKET_OBSERVATION_TEMPLATE_PATH
    elif variant.feature == "dex_arbitrage" and variant.surface == "candidates":
        selected_template = panel.DEX_POOL_CANDIDATES_TEMPLATE_PATH
    elif variant.feature == "dex_arbitrage" and variant.surface == "quotes":
        selected_template = panel.DEX_QUOTES_TEMPLATE_PATH
    elif variant.feature == "dex_arbitrage" and variant.surface == "submissions":
        selected_template = panel.DEX_SUBMISSIONS_TEMPLATE_PATH
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
        return _market_page_response(
            panel,
            template_path=panel.MARKET_OBSERVATION_TEMPLATE_PATH,
            **_default_scope_kwargs("dex_arbitrage"),
        )

    @app.get("/ops")
    def operations_panel():
        return _panel_html_response(
            panel.render_control_panel(),
            feature=_request_shell_feature(),
        )

    @app.get("/binance-market")
    @app.get("/dex-arbitrage")
    @app.get("/dex")
    @app.get("/dex/overview")
    @app.get("/dex/quotes")
    @app.get("/dex/submissions")
    def binance_market_panel():
        return _market_page_response(
            panel,
            **_default_scope_kwargs("dex_arbitrage"),
        )

    @app.get("/dex-pools")
    @app.get("/candidate-pools")
    @app.get("/dex/candidates")
    def dex_pool_candidates_panel():
        requested_market_id = request.args.get("market_id", "").strip()
        requested_chain_id = request.args.get("chain_id", "").strip()
        normalized_market_id = canonical_market_id_from_legacy(requested_market_id) or requested_market_id
        if normalized_market_id == DEFAULT_ROBINHOOD_DEX_MARKET_ID or requested_chain_id == str(DEFAULT_ROBINHOOD_CHAIN_ID):
            return _market_page_response(
                panel,
                template_path=panel.DEX_POOL_CANDIDATES_TEMPLATE_PATH,
                default_market_id=DEFAULT_ROBINHOOD_DEX_MARKET_ID,
                default_chain_id=DEFAULT_ROBINHOOD_CHAIN_ID,
            )
        return _html_response(
            panel.DEX_POOL_CANDIDATES_TEMPLATE_PATH,
            feature="dex_arbitrage",
            **_default_scope_kwargs("dex_arbitrage"),
            page_surface="candidates",
        )

    @app.get("/flash-loan")
    @app.get("/flash-loan/pools")
    def flash_loan_pools_panel():
        flash_scope = default_flash_loan_pool_scope()
        start_flash_loan_pool_discovery_idle_worker(
            initial_delay_seconds=2.0,
            refresh_kwargs=_flash_loan_discovery_refresh_kwargs(panel),
        )
        return _html_response(
            panel.FLASH_LOAN_POOLS_TEMPLATE_PATH,
            feature="dex_arbitrage",
            default_market_id=str(flash_scope["market_id"]),
            default_chain_id=int(flash_scope["chain_id"]),
        )

    @app.get("/robinhood-dex")
    @app.get("/robinhood-dex/candidates")
    @app.get("/dex/robinhood-shadow")
    def robinhood_dex_candidates_panel():
        return _market_page_response(panel, default_market_id=DEFAULT_ROBINHOOD_DEX_MARKET_ID, default_chain_id=DEFAULT_ROBINHOOD_CHAIN_ID)

    @app.get("/config")
    @app.get("/settings")
    def config_panel():
        return _panel_html_response(
            panel.render_control_panel(),
            feature=_request_shell_feature(),
        )

    @app.get("/exchange-matrix")
    @app.get("/analytics/exchange-matrix")
    def exchange_matrix_panel():
        return _html_response(
            panel.EXCHANGE_MATRIX_TEMPLATE_PATH,
            feature=_request_shell_feature(),
        )

    @app.get("/opportunity-health")
    @app.get("/analytics/opportunity-health")
    def opportunity_health_panel():
        return _html_response(
            panel.OPPORTUNITY_HEALTH_TEMPLATE_PATH,
            feature=_request_shell_feature(),
        )

    @app.get("/healthz")
    def healthz():
        return jsonify({"status": "ok"})

    @app.get("/api/system-info")
    def system_info():
        return jsonify(system_info_payload())

    @app.get("/static/chain-selector.js")
    def chain_selector_script():
        return send_file(CHAIN_SELECTOR_SCRIPT, mimetype="application/javascript")

    @app.get("/static/dex-strategy-selector.js")
    def dex_strategy_selector_script():
        return send_file(DEX_STRATEGY_SELECTOR_SCRIPT, mimetype="application/javascript")

    @app.get("/static/app-shell.js")
    def app_shell_script():
        return send_file(APP_SHELL_SCRIPT, mimetype="application/javascript")

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

    @app.get("/api/flash-loan/pool-discovery")
    def flash_loan_pool_discovery_api():
        start_flash_loan_pool_discovery_idle_worker(
            initial_delay_seconds=2.0,
            refresh_kwargs=_flash_loan_discovery_refresh_kwargs(panel),
        )
        try:
            chain_id = _request_optional_int("chain_id")
        except ChainSelectionError as exc:
            return jsonify({"ok": False, "error": exc.message, "code": exc.code}), 400
        protocol = request.args.get("protocol", "").strip().lower() or None
        return jsonify(query_flash_loan_pool_discovery(chain_id=chain_id, protocol=protocol))

    @app.post("/api/flash-loan/pool-discovery/refresh")
    def flash_loan_pool_discovery_refresh_api():
        snapshot = refresh_flash_loan_pool_discovery_cache(**_flash_loan_discovery_refresh_kwargs(panel))
        return jsonify(snapshot)

    @app.get("/api/flash-loan/pools")
    def flash_loan_pools_api():
        start_flash_loan_pool_discovery_idle_worker(
            initial_delay_seconds=2.0,
            refresh_kwargs=_flash_loan_discovery_refresh_kwargs(panel),
        )
        try:
            chain_id = _request_chain_id()
        except ChainSelectionError as exc:
            return jsonify({"ok": False, "error": exc.message, "code": exc.code}), 400
        if chain_id is None and not request.args.get("market_id", "").strip():
            chain_id = int(default_flash_loan_pool_scope()["chain_id"])
        discovered_protocols = [
            item["protocol"]
            for item in protocol_options_for_chain(chain_id=chain_id)
        ] if chain_id is not None else None
        protocols = _request_protocols()
        return jsonify(
            flash_loan_pools_payload(
                database_url=_panel_database_url(panel),
                market_id=request.args.get("market_id", "").strip() or None,
                chain_id=chain_id,
                protocols=protocols,
                discovered_protocols=discovered_protocols,
            )
        )

    @app.post("/api/flash-loan/pools/refresh")
    def flash_loan_pools_refresh_api():
        try:
            chain_id = _request_chain_id()
        except ChainSelectionError as exc:
            return jsonify({"ok": False, "error": exc.message, "code": exc.code}), 400
        if chain_id is None and not request.args.get("market_id", "").strip():
            chain_id = int(default_flash_loan_pool_scope()["chain_id"])
        protocols = _request_protocols()
        full_refresh = _request_bool_arg("full")
        refresh_state = _refresh_flash_loan_pool_sources(
            chain_id=chain_id,
            protocols=protocols,
            full=full_refresh,
            panel=panel,
        )
        discovered_protocols = [
            item["protocol"]
            for item in protocol_options_for_chain(chain_id=chain_id)
        ] if chain_id is not None else None
        payload = flash_loan_pools_payload(
            database_url=_panel_database_url(panel),
            market_id=request.args.get("market_id", "").strip() or None,
            chain_id=chain_id,
            protocols=protocols,
            discovered_protocols=discovered_protocols,
            persist_static=True,
            refresh_mode="full" if full_refresh else "incremental",
        )
        payload["refresh_state"] = {
            **refresh_state,
            "staticPoolStore": payload.get("static_pool_store") or [],
        }
        return jsonify(payload), 200 if refresh_state.get("ok") else 500
