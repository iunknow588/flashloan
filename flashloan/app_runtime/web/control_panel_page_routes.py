import logging
import os
from pathlib import Path
import threading
import time
from typing import Any

from flask import Response, g, jsonify, redirect, request, send_file

from core.build_info import build_info_payload
from core.chain_selection import ChainSelectionError
from core.chain_data_registry import default_flash_loan_pool_scope
from core.flash_loan_pool_discovery import (
    protocol_options_for_chain,
    query_flash_loan_pool_discovery,
    refresh_flash_loan_pool_discovery_cache,
    start_flash_loan_pool_discovery_idle_worker,
)
from core.market_config import DEFAULT_ROBINHOOD_CHAIN_ID, DEFAULT_ROBINHOOD_DEX_MARKET_ID
from core.market_identity import canonical_market_id_from_legacy
from web.page_routes_helpers import (
    _current_static_pool_store,
    _default_scope_kwargs,
    _flash_loan_discovery_refresh_kwargs,
    _html_response,
    _market_page_response,
    _panel_database_url,
    _panel_html_response,
    _previous_static_pool_store,
    _refresh_flash_loan_pool_sources,
    _request_bool_arg,
    _request_chain_id,
    _request_optional_int,
    _request_protocols,
    _request_shell_feature,
    _static_pool_store_diff,
)


def flash_loan_pools_payload(*args, **kwargs):
    """Lazy compatibility proxy for the flash-loan pool payload builder."""
    from web.flash_loan_pools import flash_loan_pools_payload as implementation

    return implementation(*args, **kwargs)


CHAIN_SELECTOR_SCRIPT = Path(__file__).resolve().parent / "static" / "chain_selector.js"
DEX_STRATEGY_SELECTOR_SCRIPT = Path(__file__).resolve().parent / "static" / "dex_strategy_selector.js"
APP_SHELL_SCRIPT = Path(__file__).resolve().parent / "static" / "app_shell.js"


def _dex_manager_status_payload(chain_runtime: Any | None) -> dict[str, Any]:
    """Build the status card from Rust RuntimeCore/worker state only.

    D9.41 intentionally stops wiring the control panel to the legacy Python
    scanner lifecycle. The scanner HTTP routes remain as maintenance/diagnostic
    endpoints, but this status DTO never fabricates scanner memory-state fields.
    """

    def unavailable(reason: str, error: str | None = None) -> dict[str, Any]:
        return {
            "dataSource": "rust_runtime_core",
            "managerAvailable": False,
            "runtimeCoreAvailable": False,
            "runtimeCoreRegistered": False,
            "reason": reason,
            "error": error,
            "runtimeCore": {},
            "closedLoop": {},
            "reviewQueue": {},
            "executionQueue": {},
            "maintenance": {},
            "workers": [],
            "workerCount": 0,
            "workerStateSummary": {},
            "broadcastGate": {},
        }

    if chain_runtime is None:
        return unavailable("chain_runtime_required")

    runtime_core = {}
    try:
        runtime_core = chain_runtime.runtime_core_status()
    except Exception as exc:
        return unavailable("runtime_core_status_failed", str(exc)[:300])

    registered = bool(
        runtime_core.get("available")
        and runtime_core.get("registered")
    )
    closed_loop: dict[str, Any] = {}
    closed_loop_error: str | None = None
    if registered:
        try:
            closed_loop = chain_runtime.closed_loop_control_status()
        except Exception as exc:
            closed_loop_error = str(exc)[:300]
    else:
        closed_loop_error = "runtime_core_not_registered"

    broadcast_gate: dict[str, Any] = {}
    try:
        broadcast_gate = chain_runtime.broadcast_gate
    except Exception as exc:
        broadcast_gate = {"error": str(exc)[:300]}

    workers = (
        closed_loop.get("workers")
        if isinstance(closed_loop, dict)
        and isinstance(closed_loop.get("workers"), list)
        else []
    )
    worker_count = (
        int(closed_loop.get("residentWorkers") or 0)
        if isinstance(closed_loop, dict)
        else 0
    )
    worker_state_summary = (
        closed_loop.get("summary")
        if isinstance(closed_loop, dict)
        and isinstance(closed_loop.get("summary"), dict)
        else {}
    )
    review_queue = (
        closed_loop.get("reviewQueue")
        if isinstance(closed_loop, dict)
        and isinstance(closed_loop.get("reviewQueue"), dict)
        else {}
    )
    execution_queue = (
        closed_loop.get("executionQueue")
        if isinstance(closed_loop, dict)
        and isinstance(closed_loop.get("executionQueue"), dict)
        else {}
    )
    maintenance = (
        closed_loop.get("maintenance")
        if isinstance(closed_loop, dict)
        and isinstance(closed_loop.get("maintenance"), dict)
        else {}
    )

    return {
        "dataSource": "rust_runtime_core",
        "managerAvailable": bool(runtime_core.get("snapshot_available")),
        "runtimeCoreAvailable": bool(runtime_core.get("available")),
        "runtimeCoreRegistered": registered,
        "runtimeCore": runtime_core,
        "closedLoop": closed_loop,
        "reviewQueue": review_queue,
        "executionQueue": execution_queue,
        "maintenance": maintenance,
        "workers": workers,
        "workerCount": worker_count if worker_count else len(workers),
        "workerStateSummary": worker_state_summary,
        "broadcastGate": broadcast_gate,
        "error": closed_loop_error,
    }


def register_page_routes(app, panel) -> None:
    status_logger = logging.getLogger(__name__)
    status_panel_cache = {}
    status_panel_ttl_seconds = 5.0
    status_response_cache: dict[str, Any] = {
        "payload": None,
        "built_at": 0.0,
        "cache_key": None,
        "refreshing": False,
        "last_error": None,
    }
    status_response_cache_lock = threading.Lock()
    status_response_ttl_seconds = max(
        0.2,
        float(os.getenv("FLASHLOAN_STATUS_RESPONSE_TTL_SECONDS", "5.0")),
    )

    def _status_response_cache_meta() -> dict[str, Any]:
        with status_response_cache_lock:
            return {
                "ttlSeconds": status_response_ttl_seconds,
                "cachedAtUnix": status_response_cache["built_at"],
                "refreshing": bool(status_response_cache["refreshing"]),
                "lastError": status_response_cache["last_error"],
            }

    def _build_status_payload(
        chain_runtime: Any | None,
        page_context: Any | None,
        cache_key: tuple = (None, None),
    ) -> dict[str, Any]:
        build_started = time.perf_counter()
        build_timings: dict[str, float] = {}
        scanner_started = time.perf_counter()
        scanner_status = _dex_manager_status_payload(chain_runtime)
        build_timings["scannerStatus"] = max(
            0.0, time.perf_counter() - scanner_started
        )
        panels_started = time.perf_counter()
        running = panel.quick_observer_running()
        binance_extremes = panel.safe_latest(panel.latest_binance_extremes_file)
        reserve_cache = panel.safe_latest(panel.aave_reserve_cache)
        config = panel.strategy_config()
        symbols = panel.displayed_symbols(running or panel.observer_starting)
        binance_extremes = panel.restrict_extremes_to_symbols(binance_extremes, symbols)
        opportunity_rows = panel.opportunity_health_rows(binance_extremes, config)
        now = time.monotonic()
        status_panel_cache_key = f"non_critical:{cache_key}"
        cached_panels = status_panel_cache.get(status_panel_cache_key)
        if cached_panels is not None and now - cached_panels[0] < status_panel_ttl_seconds:
            _cached_at, control_status_current, background_activity, system_monitor = cached_panels
        else:
            control_status_current = panel.control_status_payload()
            background_activity = panel.background_activity_payload(
                running,
                panel.observer_starting,
            )
            system_monitor = panel.system_monitor_payload(
                running,
                panel.observer_starting,
                binance_extremes,
                control_status_current,
                reserve_cache,
                background_activity,
            )
            status_panel_cache[status_panel_cache_key] = (
                now,
                control_status_current,
                background_activity,
                system_monitor,
            )
        build_timings["nonCriticalPanels"] = max(
            0.0, time.perf_counter() - panels_started
        )
        runtime_status_started = time.perf_counter()
        runtime_status = chain_runtime.status() if chain_runtime is not None else None
        build_timings["chainRuntimeStatus"] = max(
            0.0, time.perf_counter() - runtime_status_started
        )
        page_context_started = time.perf_counter()
        page_context_payload = (
            page_context.to_dict() if page_context is not None else None
        )
        build_timings["pageContext"] = max(
            0.0, time.perf_counter() - page_context_started
        )
        payload = {
            "running": running,
            "starting": panel.observer_starting,
            "start_error": panel.observer_start_error,
            "background_activity": background_activity,
            "dex_opportunity_scanner": scanner_status,
            "chain_runtime": runtime_status,
            "page_context": page_context_payload,
            "system_info": system_info_payload(),
            "observer_progress": panel.observer_progress_payload(running, panel.observer_starting, binance_extremes),
            "control_status": control_status_current,
            "system_monitor": system_monitor,
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
            "status_build_timings": {
                **build_timings,
                "total": max(0.0, time.perf_counter() - build_started),
            },
            "status_cache": _status_response_cache_meta(),
        }
        return payload

    def _start_status_background_refresh(
        chain_runtime: Any | None,
        page_context: Any | None,
        cache_key: tuple[int | None, str | None],
    ) -> None:
        with status_response_cache_lock:
            if status_response_cache["refreshing"]:
                return
            status_response_cache["refreshing"] = True

        def worker() -> None:
            try:
                payload = _build_status_payload(
                    chain_runtime,
                    page_context,
                    cache_key,
                )
                payload["status_cache"] = {
                    **_status_response_cache_meta(),
                    "servedFrom": "background_refresh",
                }
                with status_response_cache_lock:
                    status_response_cache["payload"] = payload
                    status_response_cache["built_at"] = time.monotonic()
                    status_response_cache["cache_key"] = cache_key
                    status_response_cache["last_error"] = None
            except Exception as exc:
                status_logger.warning("api/status background refresh failed: %s", exc)
                with status_response_cache_lock:
                    status_response_cache["last_error"] = str(exc)[:300]
            finally:
                with status_response_cache_lock:
                    status_response_cache["refreshing"] = False

        thread = threading.Thread(
            target=worker,
            name="api-status-background-refresh",
            daemon=True,
        )
        thread.start()

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
        default_feature = os.getenv("CONTROL_PANEL_DEFAULT_FEATURE", "dex_arbitrage").strip().lower()
        if request.path == "/" and not request.args.get("feature", "").strip() and default_feature in {"dex", "dex_arbitrage"}:
            target = "/flash-loan/pools"
            kept = [(key, value) for key, value in request.args.items() if key in {"chain_id", "market_id", "protocols", "protocol_id"}]
            if kept:
                from urllib.parse import urlencode

                target = f"{target}?{urlencode(kept)}"
            return redirect(target)
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
    @app.get("/dex/submissions/review-success")
    @app.get("/dex/submissions/review-failed")
    @app.get("/dex/submissions/execution-success")
    @app.get("/dex/submissions/execution-failed")
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
        timings = {}
        started_at = time.perf_counter()
        with panel.observer_start_lock:
            panel.clear_stale_observer_start()
        timings["observer_lock_clear"] = max(
            0.0, time.perf_counter() - started_at
        )
        chain_started = time.perf_counter()
        from core.runtime.chain_runtime import chain_runtime_registry

        chain_runtime = getattr(g, "chain_runtime", None)
        if chain_runtime is None:
            try:
                chain_runtime = chain_runtime_registry().current()
            except Exception:
                chain_runtime = None
        page_context = getattr(g, "page_context", None)
        if page_context is None and chain_runtime is not None:
            page_context = chain_runtime.page_context()
        timings["chain_runtime_page_context"] = max(
            0.0, time.perf_counter() - chain_started
        )
        chain_id = chain_runtime.chain_id if chain_runtime is not None else None
        database_path = (
            getattr(chain_runtime, "database_url", None)
            if chain_runtime is not None
            else None
        )
        cache_key = (
            chain_id,
            database_path,
            request.args.get("_status_cache_bust", ""),
        )
        with status_response_cache_lock:
            cached_payload = status_response_cache["payload"]
            cached_at = status_response_cache["built_at"]
            cached_key = status_response_cache["cache_key"]
        now = time.monotonic()
        cache_fresh = (
            cached_payload is not None
            and cached_key == cache_key
            and now - cached_at < status_response_ttl_seconds
        )
        if cache_fresh:
            cached_meta = _status_response_cache_meta()
            cached_meta["servedFrom"] = "fresh_cache"
            payload = dict(cached_payload)
            payload["status_cache"] = cached_meta
            timings["scanner_status"] = 0.0
            timings["non_critical_panels"] = 0.0
            timings["payload_build"] = 0.0
        else:
            stale_available = (
                cached_payload is not None
                and cached_key == cache_key
            )
            if stale_available:
                _start_status_background_refresh(
                    chain_runtime,
                    page_context,
                    cache_key,
                )
                cached_meta = _status_response_cache_meta()
                cached_meta["servedFrom"] = "stale_async"
                payload = dict(cached_payload)
                payload["status_cache"] = cached_meta
                timings["scanner_status"] = 0.0
                timings["non_critical_panels"] = 0.0
                timings["payload_build"] = 0.0
            else:
                payload_started = time.perf_counter()
                payload = _build_status_payload(
                    chain_runtime,
                    page_context,
                    cache_key,
                )
                payload["status_cache"] = {
                    **_status_response_cache_meta(),
                    "servedFrom": "computed",
                }
                with status_response_cache_lock:
                    status_response_cache["payload"] = payload
                    status_response_cache["built_at"] = time.monotonic()
                    status_response_cache["cache_key"] = cache_key
                    status_response_cache["last_error"] = None
                timings["scanner_status"] = max(
                    0.0, time.perf_counter() - payload_started
                )
                timings["non_critical_panels"] = timings["scanner_status"]
                timings["payload_build"] = timings["scanner_status"]
        timings["total"] = max(0.0, time.perf_counter() - started_at)
        status_logger.info("api/status timings=%s", timings)
        return jsonify(payload)

    @app.get("/api/debt-pool/state")
    def debt_pool_state():
        from bot.page_state import debt_pool_state_payload

        return jsonify(debt_pool_state_payload(panel))

    @app.get("/api/account-pool/state")
    def account_pool_state():
        from bot.account_pool import account_pool_state_payload

        return jsonify(account_pool_state_payload(panel))

    @app.get("/api/account-scan/state")
    def account_scan_state():
        from bot.page_state import account_scan_state_payload

        return jsonify(account_scan_state_payload(panel))

    @app.get("/api/market-observation/state")
    def market_observation_state():
        from bot.page_state import market_observation_state_payload

        return jsonify(market_observation_state_payload(panel))

    @app.get("/api/execution/state")
    def execution_state():
        from bot.page_state import execution_state_payload

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
        previous_static = _previous_static_pool_store(
            chain_id=chain_id,
            protocols=protocols,
            panel=panel,
        )
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
        current_static = _current_static_pool_store(chain_id=chain_id, protocols=protocols)
        payload["refresh_state"] = {
            **refresh_state,
            "staticPoolStore": payload.get("static_pool_store") or [],
            "previousStaticPoolStore": previous_static,
            "staticPoolDiff": _static_pool_store_diff(previous_static, current_static),
        }
        return jsonify(payload), 200 if refresh_state.get("ok") else 500
