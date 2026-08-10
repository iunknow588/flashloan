import json
import os
from pathlib import Path

from flask import jsonify, request

from core.sensitive_data import redact_sensitive_text
from db.storage_common import (
    database_unavailable_reason,
    is_database_unavailable_error,
    mark_database_unavailable,
)
from db.storage_cow_execution import (
    COW_ATTEMPT_CATEGORY_EXECUTION_FAILED,
    COW_ATTEMPT_CATEGORY_EXECUTION_SUCCESS,
    COW_ATTEMPT_CATEGORY_ANALYSIS_DAYS,
    COW_ATTEMPT_CATEGORY_NOT_EXECUTABLE,
    COW_ATTEMPT_CATEGORY_RETENTION_POLICY,
    COW_ATTEMPT_CATEGORY_RETENTION_DAYS,
    append_cow_execution_attempts_jsonl,
    build_cow_execution_attempts,
    build_cow_market_claim_candidate_attempts,
    build_cow_market_claim_pairs,
    build_cow_market_candidate_attempts,
    load_recent_cow_execution_attempts,
    load_recent_cow_execution_attempts_jsonl,
    record_cow_execution_attempts,
)
from market.velocity_candidates import build_velocity_candidate_pairs
from runtime.cow_arbitrage_daemon import (
    clear_cow_candidate_queue,
    cow_candidate_queue_snapshot,
    cow_quote_daemon_enabled,
    cow_quote_daemon_status,
    enqueue_cow_candidate_attempts,
    ensure_cow_quote_daemon_running,
    retain_cow_candidate_networks,
)
from intent_trade import submit_cow_intent_trade
from web.control_panel_cow_pause import (
    clear_cow_submission_pause_guard,
    cow_submission_pause_guard_status,
    set_cow_submission_pause_guard,
)
from market.binance_market.service import (
    DEFAULT_BINANCE_RAW_SIDE_LIMIT,
    DEFAULT_MIN_COW_SPREAD_PERCENT,
    build_binance_market_state,
    build_cow_network_market_claims,
    build_cow_route_precheck,
    build_cow_quote_verification,
    build_cow_supported_market_overview,
    cost_adjusted_cow_thresholds,
    cow_network_options,
    load_cow_supported_token_registry,
    refresh_cow_supported_token_cache,
    select_binance_market_extremes,
)
from web.control_panel_liquidation_routes import liquidation_coverage_payload
from tools.liquidation_observation_report import (
    build_liquidation_observation_report,
    default_output_path as liquidation_observation_report_path,
    write_liquidation_observation_report,
)
from web.route_context import RouteContext

ROUTE_CONTEXT = RouteContext()
SRC_ROOT = Path(__file__).resolve().parents[1]
COW_EXECUTION_ATTEMPT_LOG_PATH = SRC_ROOT / "runtime" / "logs" / "cow_execution_attempts.jsonl"
COW_EXECUTION_RETENTION_DAYS = 7
COW_EXECUTION_REVIEW_CATEGORIES = [
    COW_ATTEMPT_CATEGORY_NOT_EXECUTABLE,
    COW_ATTEMPT_CATEGORY_EXECUTION_FAILED,
    COW_ATTEMPT_CATEGORY_EXECUTION_SUCCESS,
]


def panel_call(name: str, *args, **kwargs):
    return ROUTE_CONTEXT.call(name, *args, **kwargs)


def _cow_submission_pause_guard(*, database_url: str | None = None) -> dict:
    try:
        return cow_submission_pause_guard_status(database_url=database_url)
    except TypeError:
        return cow_submission_pause_guard_status()


def _set_cow_submission_pause_guard(*, paused: bool, reason: str | None = None, database_url: str | None = None) -> dict:
    try:
        return set_cow_submission_pause_guard(paused=paused, reason=reason, database_url=database_url)
    except TypeError:
        return set_cow_submission_pause_guard(paused=paused, reason=reason)


def _clear_cow_submission_pause_guard(*, database_url: str | None = None) -> dict:
    try:
        return clear_cow_submission_pause_guard(database_url=database_url)
    except TypeError:
        return clear_cow_submission_pause_guard()


def data_error_message(error: object | None) -> str | None:
    if error is None:
        return None
    message = str(error)
    if not message:
        message = repr(error)
    if message and message != repr(error):
        message = f"{type(error).__name__}: {message}"
    return redact_sensitive_text(message)


def _request_networks_arg() -> list[str]:
    raw = request.args.get("cow_networks", "") or request.args.get("cow_network", "")
    return [
        item.strip().lower()
        for item in raw.split(",")
        if item.strip()
    ]


def _filter_network_claims(claims: list[dict], networks: list[str]) -> list[dict]:
    selected = {
        str(network or "").strip().lower()
        for network in networks
        if str(network or "").strip()
    }
    if not selected:
        return list(claims or [])
    return [
        claim
        for claim in claims or []
        if str(claim.get("network") or "").strip().lower() in selected
    ]


def record_cow_attempt_list_safely(
    attempts: list[dict],
    *,
    database_url: str | None,
) -> dict:
    if not attempts:
        return {"recorded": 0, "source": "empty", "error": None}
    pause_guard = _cow_submission_pause_guard(database_url=database_url)
    if pause_guard.get("paused"):
        return {
            "recorded": 0,
            "source": "paused",
            "error": None,
            "pause_guard": pause_guard,
        }
    if database_url:
        unavailable = database_unavailable_reason(database_url)
        if unavailable:
            file_count = append_cow_execution_attempts_jsonl(
                COW_EXECUTION_ATTEMPT_LOG_PATH,
                attempts,
                retention_days=COW_EXECUTION_RETENTION_DAYS,
            )
            return {"recorded": file_count, "source": "jsonl_fallback", "error": unavailable}
        try:
            ids = record_cow_execution_attempts(
                database_url,
                attempts,
                retention_days=COW_EXECUTION_RETENTION_DAYS,
            )
            return {"recorded": len(ids), "source": "database", "ids": ids, "error": None}
        except Exception as exc:
            if is_database_unavailable_error(exc):
                mark_database_unavailable(database_url, exc)
            file_count = append_cow_execution_attempts_jsonl(
                COW_EXECUTION_ATTEMPT_LOG_PATH,
                attempts,
                retention_days=COW_EXECUTION_RETENTION_DAYS,
            )
            return {"recorded": file_count, "source": "jsonl_fallback", "error": data_error_message(exc)}
    file_count = append_cow_execution_attempts_jsonl(
        COW_EXECUTION_ATTEMPT_LOG_PATH,
        attempts,
        retention_days=COW_EXECUTION_RETENTION_DAYS,
    )
    return {"recorded": file_count, "source": "jsonl", "error": None}


def record_cow_execution_attempts_safely(
    payload: dict,
    *,
    market_state: dict,
    database_url: str | None,
) -> dict:
    return record_cow_attempt_list_safely(
        build_cow_execution_attempts(payload, market_state=market_state),
        database_url=database_url,
    )


def submit_ready_cow_quote_orders(payload: dict, *, market_state: dict) -> dict:
    submitted = 0
    failed = 0
    skipped = 0
    results = []
    for item in payload.get("ranking") or []:
        if not isinstance(item, dict):
            continue
        precheck = dict(item.get("execution_precheck") or {})
        if not precheck.get("can_submit_order"):
            skipped += 1
            continue
        quote_payload = {
            **item,
            "cow_network": payload.get("cow_network"),
            "cow_chain_id": payload.get("cow_chain_id"),
            "owner": payload.get("owner"),
            "owner_source": payload.get("owner_source"),
            "cow_testnet": payload.get("cow_testnet"),
        }
        submission = submit_cow_intent_trade(
            quote_payload=quote_payload,
            opportunity={
                "source": "web_cow_quotes",
                "market_state": market_state,
                "pair": item.get("pair"),
                "pair_rank": item.get("pair_rank"),
                "priority_reason": item.get("priority_reason"),
            },
        )
        item["cow_submission_result"] = submission
        sdk_plan = item.get("cow_flashloan_sdk_plan") if isinstance(item.get("cow_flashloan_sdk_plan"), dict) else {}
        if sdk_plan:
            sdk_plan["submission_status"] = submission.get("status")
            sdk_plan["order_id"] = submission.get("order_id")
            sdk_plan["tx_hash"] = submission.get("tx_hash")
        reasons = list(precheck.get("reasons") or [])
        precheck["execution_phase"] = "order_submission"
        precheck["submission_attempted"] = True
        precheck["submission_status"] = submission.get("status")
        precheck["submission_order_id"] = submission.get("order_id")
        precheck["submission_tx_hash"] = submission.get("tx_hash")
        if submission.get("submitted"):
            submitted += 1
            precheck["status"] = "submitted_success"
            precheck["can_submit_order"] = False
            if "cow_order_submitted_successfully" not in reasons:
                reasons.append("cow_order_submitted_successfully")
        else:
            failed += 1
            precheck["status"] = "submission_failed"
            precheck["can_submit_order"] = False
            error = submission.get("error") or submission.get("blocked_reason") or "cow_order_submission_failed"
            if str(error) not in reasons:
                reasons.append(str(error))
            item["error"] = str(error)
        precheck["reasons"] = reasons
        item["execution_precheck"] = precheck
        sdk_result = dict(item.get("cow_sdk_result") or {})
        sdk_result.update(
            {
                "status": precheck["status"],
                "ready": bool(precheck.get("checks_passed")),
                "submission_attempted": True,
                "submission_status": submission.get("status"),
                "submission_order_id": submission.get("order_id"),
                "submission_tx_hash": submission.get("tx_hash"),
                "submission_error": submission.get("error"),
                "submission_blocked_reason": submission.get("blocked_reason"),
                "submission_preflight": submission.get("preflight"),
                "execution_error": submission.get("execution_error"),
                "submitted": bool(submission.get("submitted")),
            }
        )
        item["cow_sdk_result"] = sdk_result
        results.append(
            {
                "pair": item.get("pair"),
                "priority_reason": item.get("priority_reason"),
                "status": submission.get("status"),
                "submitted": bool(submission.get("submitted")),
                "order_id": submission.get("order_id"),
                "tx_hash": submission.get("tx_hash"),
                "blocked_reason": submission.get("blocked_reason"),
                "error": submission.get("error"),
            }
        )
    opportunities = [
        item
        for item in payload.get("ranking") or []
        if (item.get("execution_precheck") or {}).get("checks_passed")
    ]
    payload["opportunities"] = opportunities
    payload["opportunity_count"] = len(opportunities)
    payload["best_opportunity"] = opportunities[0] if opportunities else None
    payload["best"] = opportunities[0] if opportunities else ((payload.get("ranking") or [None])[0])
    payload["submission_summary"] = {
        "attempted": submitted + failed,
        "submitted": submitted,
        "failed": failed,
        "skipped": skipped,
        "results": results,
    }
    return payload


def _cow_execution_attempt_groups(
    loader,
    *,
    limit: int,
    networks: list[str],
) -> dict[str, dict]:
    groups = {}
    for category in COW_EXECUTION_REVIEW_CATEGORIES:
        rows = loader(limit=limit, networks=networks, category=category)
        rows.sort(key=lambda item: str(item.get("created_at") or item.get("observed_at") or ""), reverse=True)
        rows = rows[: max(1, int(limit))]
        groups[category] = {
            "attempts": rows,
            "count": len(rows),
            "retention_days": COW_ATTEMPT_CATEGORY_RETENTION_DAYS.get(category),
            "analysis_days": COW_ATTEMPT_CATEGORY_ANALYSIS_DAYS.get(category),
            "retention_policy": COW_ATTEMPT_CATEGORY_RETENTION_POLICY.get(category),
            "page_size": 10,
        }
    return groups


def _flatten_cow_execution_groups(groups: dict[str, dict], limit: int) -> list[dict]:
    rows = [
        row
        for group in groups.values()
        for row in group.get("attempts", [])
    ]
    rows.sort(key=lambda item: str(item.get("created_at") or item.get("observed_at") or ""), reverse=True)
    return rows[: max(1, int(limit))]


def record_cow_market_candidates_safely(
    market_states: list[dict],
    *,
    network_claims: list[dict] | None = None,
    observed_at: object = None,
    window_seconds: object = None,
    price_source: object = None,
    market_state_source: object = None,
    fallback_reason: object = None,
    database_url: str | None,
) -> dict:
    pause_guard = _cow_submission_pause_guard(database_url=database_url)
    if pause_guard.get("paused"):
        return {
            "recorded": 0,
            "source": "paused",
            "error": None,
            "pause_guard": pause_guard,
            "queue": {"accepted": 0, "skipped": "cow_automation_paused"},
            "daemon": cow_quote_daemon_status(),
        }
    attempts = [
        attempt
        for market_state in market_states
        for attempt in build_cow_market_candidate_attempts(market_state)
    ]
    attempts.extend(
        build_cow_market_claim_candidate_attempts(
            network_claims or [],
            observed_at=observed_at,
            window_seconds=window_seconds,
            price_source=price_source,
            market_state_source=market_state_source,
            fallback_reason=fallback_reason,
        )
    )
    recording = record_cow_attempt_list_safely(attempts, database_url=database_url)
    if recording.get("source") == "paused":
        return {
            **recording,
            "queue": {"accepted": 0, "skipped": "cow_automation_paused"},
            "daemon": cow_quote_daemon_status(),
        }
    queue_result = enqueue_cow_candidate_attempts(attempts, source="binance_market_candidates")
    daemon_result = cow_quote_daemon_status()
    if cow_quote_daemon_enabled() and attempts:
        daemon = ensure_cow_quote_daemon_running(database_url_provider=lambda: panel_call("database_url_or_none"))
        daemon_result = daemon.status()
    return {**recording, "queue": queue_result, "daemon": daemon_result}


def request_int_arg(name: str, default: int, *, minimum: int | None = None, maximum: int | None = None) -> int:
    raw = request.args.get(name, str(default))
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = int(default)
    if minimum is not None:
        value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value


def request_float_arg(name: str, default: float, *, minimum: float | None = None, maximum: float | None = None) -> float:
    raw = request.args.get(name, str(default))
    try:
        value = float(raw)
    except (TypeError, ValueError):
        value = float(default)
    if minimum is not None:
        value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value


def request_min_cow_spread_percent() -> float:
    return request_float_arg(
        "min_spread_percent",
        DEFAULT_MIN_COW_SPREAD_PERCENT,
        minimum=0.0,
        maximum=100.0,
    )


def request_cow_amount(default: str = "1000") -> str:
    return request.args.get("amount", default).strip() or default


def request_cow_trade_thresholds(amount: str | None = None) -> tuple[object, int, dict]:
    arbitrage_config = arbitrage_config_from_strategy()
    slippage_bps = read_slippage_bps()
    thresholds = cost_adjusted_cow_thresholds(
        requested_min_spread_percent=request_min_cow_spread_percent(),
        amount=amount or request_cow_amount(),
        arbitrage_config=arbitrage_config,
        slippage_bps=slippage_bps,
    )
    return arbitrage_config, slippage_bps, thresholds


def register_data_routes(app, panel) -> None:
    ROUTE_CONTEXT.bind(panel, globals())

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
        schema = schema_status_payload()
        pool_address = os.getenv("AAVE_POOL_ADDRESS", "").strip()
        coverage = liquidation_coverage_payload(pool_address, panel=ROUTE_CONTEXT.panel)
        attempts = panel_call("recent_liquidation_execution_attempts", limit=1)
        failure_samples = panel_call("recent_liquidation_failure_samples", limit=20)
        pause_guard = panel_call("liquidation_pause_guard_status")
        return jsonify(
            {
                "rows": observation_count(),
                "db_counts": database_table_counts(),
                "trade_stats": safe_latest(lambda: read_trade_stats(configured_database_url())),
                "testnet_trade_stats": safe_latest(lambda: read_testnet_trade_stats(REPO_ROOT)),
                "liquidation": {
                    "schema": {
                        "configured": schema.get("configured"),
                        "up_to_date": schema.get("up_to_date"),
                        "missing_migrations": schema.get("missing_migrations", []),
                    },
                    "discovery_coverage": coverage,
                    "execution_attempts": attempts.get("stats", {}),
                    "failure_samples": {
                        "configured": failure_samples.get("configured"),
                        "recent_count": len(failure_samples.get("samples") or []),
                    },
                    "pause_guard": pause_guard,
                },
            }
        )

    @app.get("/api/liquidation/control-summary")
    def liquidation_control_summary():
        database_url = panel_call("database_url_or_none")
        pool_address = os.getenv("AAVE_POOL_ADDRESS", "").strip()
        schema = panel_call("schema_status_payload")
        discovery_progress = panel_call("liquidation_discovery_progress", pool_address) if database_url and pool_address else {}
        registry_window = panel_call("liquidation_account_registry_window") if database_url else {}
        pause_guard = panel_call("liquidation_pause_guard_status")
        background_activity = panel_call("background_activity_payload")
        attempts = panel_call("recent_liquidation_execution_attempts", limit=5)
        failure_samples = panel_call("recent_liquidation_failure_samples", limit=5)
        latest_batch = None
        core_opportunities = []
        high_frequency_rows = []
        account_tiers = {}
        if database_url:
            try:
                panel_call("ensure_database_schema", database_url)
                latest_batches = panel_call("db_load_liquidation_borrow_health_scan_batches", database_url, limit=1)
                latest_batch = latest_batches[0] if latest_batches else None
                core_opportunities = panel_call("db_load_liquidation_core_opportunity_pool", database_url, limit=5)
                high_frequency_rows = panel_call("db_load_liquidation_high_frequency_pool", database_url, limit=5)
                account_tiers = panel_call("liquidation_account_tier_summary")
            except Exception as exc:
                return jsonify({"configured": True, "error": data_error_message(exc), "schema": schema}), 400
        return jsonify(
            {
                "configured": bool(database_url),
                "schema": {
                    "configured": schema.get("configured"),
                    "up_to_date": schema.get("up_to_date"),
                    "missing_migrations": schema.get("missing_migrations", []),
                    "error": schema.get("error"),
                },
                "pause_guard": pause_guard,
                "background_activity": background_activity,
                "discovery_running": bool((background_activity.get("liquidation_discovery") or {}).get("running")),
                "discovery_stage": (background_activity.get("liquidation_discovery") or {}).get("stage"),
                "scan_running": bool((background_activity.get("liquidation_health_scan") or {}).get("running")),
                "scan_stage": (background_activity.get("liquidation_health_scan") or {}).get("stage"),
                "integrity": {
                    "schema_up_to_date": bool(schema.get("up_to_date")),
                    "discovery_has_gap": bool(discovery_progress.get("has_gap")),
                    "discovery_covered_from_block": discovery_progress.get("earliest_backfill_from_block"),
                    "discovery_covered_to_block": discovery_progress.get("latest_recent_to_block"),
                    "discovery_stage": str(
                        discovery_progress.get("stage")
                        or ("borrowers" if discovery_progress.get("latest_recent_to_block") is not None else "idle")
                    ),
                    "registry_total_count": int(registry_window.get("total_count") or 0),
                    "registry_active_count": int(registry_window.get("active_count") or 0),
                    "latest_batch_status": (latest_batch or {}).get("status") if latest_batch else None,
                    "latest_batch_account_count": int((latest_batch or {}).get("account_count") or 0),
                    "latest_batch_scanned_count": int((latest_batch or {}).get("scanned_count") or 0),
                    "latest_batch_matches_account_count": bool(
                        latest_batch
                        and str((latest_batch or {}).get("status") or "").lower() == "success"
                        and int((latest_batch or {}).get("account_count") or 0) == int((latest_batch or {}).get("scanned_count") or 0)
                    ),
                    "complete": bool(
                        schema.get("up_to_date")
                        and not discovery_progress.get("has_gap")
                        and latest_batch
                        and str((latest_batch or {}).get("status") or "").lower() == "success"
                        and int((latest_batch or {}).get("account_count") or 0) == int((latest_batch or {}).get("scanned_count") or 0)
                    ),
                },
                "execution_attempts": attempts.get("stats", {}),
                "failure_samples": {"recent_count": len(failure_samples.get("samples") or [])},
                "latest_batch": latest_batch,
                "core_opportunities": core_opportunities,
                "high_frequency_rows": high_frequency_rows,
                "account_tiers": account_tiers,
                "control_status": panel_call("control_status_payload"),
            }
        )

    @app.get("/api/liquidation/reports/daily")
    def liquidation_daily_report_api():
        database_url = panel_call("database_url_or_none")
        if not database_url:
            return jsonify({"configured": False, "error": "DATABASE_URL is required", "report": None})
        try:
            path = liquidation_observation_report_path()
            if path.exists():
                report = json.loads(path.read_text(encoding="utf-8"))
                return jsonify({"configured": True, "path": str(path), "reused": True, "report": report})
            report = build_liquidation_observation_report(database_url)
            write_liquidation_observation_report(report, path)
            return jsonify({"configured": True, "path": str(path), "reused": False, "report": report})
        except Exception as exc:
            return jsonify({"configured": True, "error": data_error_message(exc), "report": None}), 400

    @app.post("/api/liquidation/reports/daily")
    def liquidation_daily_report_refresh_api():
        database_url = panel_call("database_url_or_none")
        if not database_url:
            return jsonify({"configured": False, "error": "DATABASE_URL is required", "report": None})
        try:
            path = liquidation_observation_report_path()
            report = build_liquidation_observation_report(database_url)
            write_liquidation_observation_report(report, path)
            return jsonify({"configured": True, "path": str(path), "reused": False, "report": report})
        except Exception as exc:
            return jsonify({"configured": True, "error": data_error_message(exc), "report": None}), 400
    
    
    @app.get("/api/velocity-timepoints")
    def velocity_timepoints():
        try:
            limit = request_int_arg("limit", 200, minimum=1, maximum=500)
            rows = recent_velocity_timepoints(limit)
        except Exception as exc:
            if "does not exist" in str(exc):
                return jsonify({"timepoints": []})
            return jsonify({"error": data_error_message(exc), "timepoints": []}), 400
        return jsonify({"timepoints": rows})
    
    
    @app.get("/api/velocity-summary")
    def velocity_summary():
        try:
            raw_id = request.args.get("id", "").strip()
            snapshot_id = request_int_arg("id", 0) if raw_id else None
            snapshot = velocity_timepoint_snapshot(snapshot_id)
            if not snapshot and snapshot_id is not None:
                snapshot = velocity_timepoint_snapshot(None)
            if not snapshot:
                return jsonify({"error": "no velocity timepoint found", "rows": []}), 404
            return jsonify(build_velocity_summary(snapshot))
        except Exception as exc:
            if "does not exist" in str(exc):
                return jsonify({"error": "initialize database and collect velocity windows first", "rows": []})
            return jsonify({"error": data_error_message(exc), "rows": []}), 400
    
    
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
        return jsonify({"stats": safe_latest(lambda: read_trade_stats(configured_database_url()))})
    
    
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
                rows = recent_binance_price_history(symbol, limit)
        except Exception as exc:
            return jsonify({"error": data_error_message(exc)}), 400
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
            routes = simulate_four_route_cycles(
                rows[x_symbol],
                rows[y_symbol],
                arbitrage_config_from_strategy(),
                initial_amount,
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

    @app.get("/api/binance-market/state")
    def binance_market_state():
        extremes = safe_latest(latest_binance_extremes_file)
        side_limit = request_int_arg("side_limit", 50, minimum=1, maximum=50)
        pair_side_limit = request_int_arg("pair_side_limit", 5, minimum=1, maximum=5)
        amount = request_cow_amount()
        arbitrage_config, slippage_bps, thresholds = request_cow_trade_thresholds(amount)
        min_spread_percent = float(thresholds["adjusted_min_spread_percent"])
        min_side_change_percent = float(thresholds["min_side_change_percent"])
        min_token_price_usd = float(thresholds["min_token_price_usd"])
        cow_network = request.args.get("cow_network", "").strip() or None
        database_url = panel_call("database_url_or_none")
        token_cache = load_cow_supported_token_registry(cow_network=cow_network, database_url=database_url)
        extremes = select_binance_market_extremes(extremes, side_limit=side_limit)
        network_token_caches = {}
        for item in cow_network_options()["networks"]:
            if item.get("testnet"):
                continue
            network = item["network"]
            network_token_caches[network] = (
                token_cache
                if network == token_cache["network"]
                else load_cow_supported_token_registry(
                    cow_network=network,
                    database_url=database_url,
                    allow_live_fallback=False,
                )
            )
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
        market_state["cow_top"] = list(market_state.get("top") or [])[:DEFAULT_BINANCE_RAW_SIDE_LIMIT]
        market_state["cow_bottom"] = list(market_state.get("bottom") or [])[:DEFAULT_BINANCE_RAW_SIDE_LIMIT]
        market_state["cow_network_claims"] = build_cow_network_market_claims(
            extremes,
            network_token_caches,
            limit=5,
            min_spread_percent=min_spread_percent,
            min_side_change_percent=min_side_change_percent,
            min_token_price_usd=min_token_price_usd,
            threshold_detail=thresholds,
        )
        for claim in market_state["cow_network_claims"]:
            claim["top"] = list(claim.get("top") or [])[:5]
            claim["bottom"] = list(claim.get("bottom") or [])[:5]
        market_state["cow_supported_overview"] = build_cow_supported_market_overview(
            extremes,
            network_token_caches,
            limit=50,
            min_side_change_percent=min_side_change_percent,
            min_token_price_usd=min_token_price_usd,
            threshold_detail=thresholds,
        )
        market_state["history_recording"] = record_cow_market_candidates_safely(
            [market_state],
            network_claims=_filter_network_claims(
                market_state.get("cow_network_claims") or [],
                [token_cache["network"]],
            ),
            observed_at=market_state.get("observed_at"),
            window_seconds=market_state.get("window_seconds"),
            price_source=market_state.get("price_source"),
            market_state_source=market_state.get("market_state_source"),
            fallback_reason=market_state.get("fallback_reason"),
            database_url=database_url,
        )
        return jsonify(market_state)

    @app.get("/api/binance-market/states")
    def binance_market_states():
        extremes = safe_latest(latest_binance_extremes_file)
        side_limit = request_int_arg("side_limit", 50, minimum=1, maximum=50)
        pair_side_limit = request_int_arg("pair_side_limit", 5, minimum=1, maximum=5)
        amount = request_cow_amount()
        arbitrage_config, slippage_bps, thresholds = request_cow_trade_thresholds(amount)
        min_spread_percent = float(thresholds["adjusted_min_spread_percent"])
        min_side_change_percent = float(thresholds["min_side_change_percent"])
        min_token_price_usd = float(thresholds["min_token_price_usd"])
        requested = [
            item.strip().lower()
            for item in (request.args.get("cow_networks", "") or "").split(",")
            if item.strip()
        ]
        mainnet_networks = [
            item["network"]
            for item in cow_network_options()["networks"]
            if not item.get("testnet")
        ]
        if not requested:
            requested = ["avalanche"]
        requested = [
            network
            for index, network in enumerate(requested)
            if network in mainnet_networks and network not in requested[:index]
        ][:8]
        if not requested:
            return jsonify({"error": "no supported CoW mainnet networks selected", "states": {}}), 400
        database_url = panel_call("database_url_or_none")
        extremes = select_binance_market_extremes(extremes, side_limit=side_limit)
        network_token_caches = {
            network: load_cow_supported_token_registry(
                cow_network=network,
                database_url=database_url,
                allow_live_fallback=network in requested,
            )
            for network in mainnet_networks
        }
        claims = build_cow_network_market_claims(
            extremes,
            network_token_caches,
            limit=5,
            min_spread_percent=min_spread_percent,
            min_side_change_percent=min_side_change_percent,
            min_token_price_usd=min_token_price_usd,
            threshold_detail=thresholds,
        )
        for claim in claims:
            claim["top"] = list(claim.get("top") or [])[:5]
            claim["bottom"] = list(claim.get("bottom") or [])[:5]
        cow_supported_overview = build_cow_supported_market_overview(
            extremes,
            network_token_caches,
            limit=50,
            min_side_change_percent=min_side_change_percent,
            min_token_price_usd=min_token_price_usd,
            threshold_detail=thresholds,
        )
        states = {}
        market_states_for_history = []
        for network in requested:
            token_cache = network_token_caches[network]
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
            market_state["cow_top"] = list(market_state.get("top") or [])[:DEFAULT_BINANCE_RAW_SIDE_LIMIT]
            market_state["cow_bottom"] = list(market_state.get("bottom") or [])[:DEFAULT_BINANCE_RAW_SIDE_LIMIT]
            states[network] = market_state
            market_states_for_history.append(market_state)
        history_recording = record_cow_market_candidates_safely(
            market_states_for_history,
            network_claims=_filter_network_claims(claims, requested),
            observed_at=extremes.get("observed_at") if isinstance(extremes, dict) else None,
            window_seconds=extremes.get("window_seconds") if isinstance(extremes, dict) else None,
            price_source=extremes.get("price_source") if isinstance(extremes, dict) else None,
            market_state_source=extremes.get("market_state_source") if isinstance(extremes, dict) else None,
            fallback_reason=extremes.get("fallback_reason") if isinstance(extremes, dict) else None,
            database_url=database_url,
        )
        queue_retention = retain_cow_candidate_networks(requested)
        return jsonify(
            {
                "networks": requested,
                "states": states,
                "history_recording": history_recording,
                "queue_retention": queue_retention,
                "cow_network_claims": claims,
                "cow_supported_overview": cow_supported_overview,
                "observed_at": extremes.get("observed_at") if isinstance(extremes, dict) else None,
                "window_seconds": extremes.get("window_seconds") if isinstance(extremes, dict) else None,
                "price_source": extremes.get("price_source") if isinstance(extremes, dict) else None,
                "market_state_source": extremes.get("market_state_source") if isinstance(extremes, dict) else None,
                "fallback_reason": extremes.get("fallback_reason") if isinstance(extremes, dict) else None,
                "threshold_detail": thresholds,
            }
        )

    @app.get("/api/binance-market/cow-config")
    def binance_market_cow_config():
        return jsonify(cow_network_options())

    @app.get("/api/binance-market/cow-tokens")
    def binance_market_cow_tokens():
        cow_network = request.args.get("cow_network", "").strip() or None
        database_url = panel_call("database_url_or_none")
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
        cow_network = request.args.get("cow_network", "").strip() or None
        database_url = panel_call("database_url_or_none")
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
        quote_timeout_seconds = request_float_arg("quote_timeout_seconds", 8.0, minimum=1.0, maximum=30.0)
        arbitrage_config, slippage_bps, thresholds = request_cow_trade_thresholds(amount)
        min_spread_percent = float(thresholds["adjusted_min_spread_percent"])
        min_side_change_percent = float(thresholds["min_side_change_percent"])
        min_token_price_usd = float(thresholds["min_token_price_usd"])
        cow_network = request.args.get("cow_network", "").strip() or None
        database_url = panel_call("database_url_or_none")
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

    @app.get("/api/binance-market/cow-quotes")
    def binance_market_cow_quotes():
        extremes = safe_latest(latest_binance_extremes_file)
        side_limit = request_int_arg("side_limit", 50, minimum=1, maximum=50)
        pair_side_limit = request_int_arg("pair_side_limit", 5, minimum=1, maximum=5)
        quote_limit = request_int_arg("quote_limit", 5, minimum=1, maximum=5)
        amount = request_cow_amount()
        quote_timeout_seconds = request_float_arg("quote_timeout_seconds", 8.0, minimum=1.0, maximum=30.0)
        arbitrage_config, slippage_bps, thresholds = request_cow_trade_thresholds(amount)
        min_spread_percent = float(thresholds["adjusted_min_spread_percent"])
        min_side_change_percent = float(thresholds["min_side_change_percent"])
        min_token_price_usd = float(thresholds["min_token_price_usd"])
        owner = request.args.get("owner", "").strip() or None
        cow_network = request.args.get("cow_network", "").strip() or None
        database_url = panel_call("database_url_or_none")
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
        fallback_pairs = []
        if not market_state.get("pairs"):
            network_claims = build_cow_network_market_claims(
                extremes,
                {token_cache["network"]: token_cache},
                limit=5,
                min_spread_percent=min_spread_percent,
                min_side_change_percent=min_side_change_percent,
                min_token_price_usd=min_token_price_usd,
                threshold_detail=thresholds,
            )
            fallback_pairs = [
                pair
                for claim in network_claims
                if claim.get("network") == token_cache["network"]
                for pair in build_cow_market_claim_pairs(
                    claim,
                    include_below_min_spread=True,
                    max_pairs=1,
                )
            ]
        if fallback_pairs:
            market_state["pairs"] = fallback_pairs
            market_state["pair_count"] = len(fallback_pairs)
            market_state["quote_candidate_source"] = "cow_network_claim_top_bottom"
        try:
            payload = build_cow_quote_verification(
                market_state,
                amount=amount,
                quote_limit=quote_limit,
                owner=owner,
                cow_network=token_cache["network"],
                quote_timeout_seconds=quote_timeout_seconds,
                registry=token_cache["registry"],
            )
        except Exception as exc:
            return jsonify({"error": data_error_message(exc), "market_state": market_state, "ranking": []}), 400
        pause_guard = cow_submission_pause_guard_status()
        if pause_guard.get("paused"):
            for item in payload.get("ranking") or []:
                if not isinstance(item, dict):
                    continue
                precheck = dict(item.get("execution_precheck") or {})
                if not precheck.get("checks_passed"):
                    continue
                reasons = list(precheck.get("reasons") or [])
                if "cow_submission_paused" not in reasons:
                    reasons.append("cow_submission_paused")
                precheck.update(
                    {
                        "status": "submission_paused",
                        "checks_passed": True,
                        "can_submit_order": False,
                        "auto_execute_blocked": True,
                        "submission_attempted": False,
                        "submission_status": "submission_paused",
                        "submission_pause_guard": pause_guard,
                        "reasons": reasons,
                    }
                )
                item["execution_precheck"] = precheck
                sdk_result = dict(item.get("cow_sdk_result") or {})
                sdk_result.update(
                    {
                        "status": "submission_paused",
                        "submitted": False,
                        "blocked_reason": "cow_submission_paused",
                        "pause_guard": pause_guard,
                    }
                )
                item["cow_sdk_result"] = sdk_result
            payload.update(
                {
                    "status": "submission_paused",
                    "blocked_reason": "cow_submission_paused",
                    "pause_guard": pause_guard,
                }
            )
            payload["history_recording"] = {
                "recorded": 0,
                "source": "paused",
                "error": None,
                "pause_guard": pause_guard,
            }
        else:
            payload = submit_ready_cow_quote_orders(payload, market_state=market_state)
            payload["history_recording"] = record_cow_execution_attempts_safely(
                payload,
                market_state=market_state,
                database_url=database_url,
            )
        return jsonify({"market_state": market_state, **payload})

    @app.get("/api/binance-market/cow-execution-attempts")
    def binance_market_cow_execution_attempts():
        limit = request_int_arg("limit", 300, minimum=1, maximum=1000)
        networks = _request_networks_arg()
        database_url = panel_call("database_url_or_none")
        if database_url:
            unavailable = database_unavailable_reason(database_url)
            if unavailable:
                groups = _cow_execution_attempt_groups(
                    lambda **kwargs: load_recent_cow_execution_attempts_jsonl(
                        COW_EXECUTION_ATTEMPT_LOG_PATH,
                        retention_days=COW_EXECUTION_RETENTION_DAYS,
                        **kwargs,
                    ),
                    limit=limit,
                    networks=networks,
                )
                return jsonify(
                    {
                        "source": "jsonl_fallback",
                        "error": unavailable,
                        "retention_days": COW_EXECUTION_RETENTION_DAYS,
                        "category_retention_days": COW_ATTEMPT_CATEGORY_RETENTION_DAYS,
                        "category_analysis_days": COW_ATTEMPT_CATEGORY_ANALYSIS_DAYS,
                        "category_retention_policy": COW_ATTEMPT_CATEGORY_RETENTION_POLICY,
                        "networks": networks,
                        "groups": groups,
                        "attempts": _flatten_cow_execution_groups(groups, limit),
                    }
                )
            try:
                groups = _cow_execution_attempt_groups(
                    lambda **kwargs: load_recent_cow_execution_attempts(
                        database_url,
                        retention_days=COW_EXECUTION_RETENTION_DAYS,
                        **kwargs,
                    ),
                    limit=limit,
                    networks=networks,
                )
                return jsonify(
                    {
                        "source": "database",
                        "retention_days": COW_EXECUTION_RETENTION_DAYS,
                        "category_retention_days": COW_ATTEMPT_CATEGORY_RETENTION_DAYS,
                        "category_analysis_days": COW_ATTEMPT_CATEGORY_ANALYSIS_DAYS,
                        "category_retention_policy": COW_ATTEMPT_CATEGORY_RETENTION_POLICY,
                        "networks": networks,
                        "groups": groups,
                        "attempts": _flatten_cow_execution_groups(groups, limit),
                    }
                )
            except Exception as exc:
                if is_database_unavailable_error(exc):
                    mark_database_unavailable(database_url, exc)
                groups = _cow_execution_attempt_groups(
                    lambda **kwargs: load_recent_cow_execution_attempts_jsonl(
                        COW_EXECUTION_ATTEMPT_LOG_PATH,
                        retention_days=COW_EXECUTION_RETENTION_DAYS,
                        **kwargs,
                    ),
                    limit=limit,
                    networks=networks,
                )
                return jsonify(
                    {
                        "source": "jsonl_fallback",
                        "error": data_error_message(exc),
                        "retention_days": COW_EXECUTION_RETENTION_DAYS,
                        "category_retention_days": COW_ATTEMPT_CATEGORY_RETENTION_DAYS,
                        "category_analysis_days": COW_ATTEMPT_CATEGORY_ANALYSIS_DAYS,
                        "category_retention_policy": COW_ATTEMPT_CATEGORY_RETENTION_POLICY,
                        "networks": networks,
                        "groups": groups,
                        "attempts": _flatten_cow_execution_groups(groups, limit),
                    }
                )
        groups = _cow_execution_attempt_groups(
            lambda **kwargs: load_recent_cow_execution_attempts_jsonl(
                COW_EXECUTION_ATTEMPT_LOG_PATH,
                retention_days=COW_EXECUTION_RETENTION_DAYS,
                **kwargs,
            ),
            limit=limit,
            networks=networks,
        )
        return jsonify(
            {
                "source": "jsonl",
                "retention_days": COW_EXECUTION_RETENTION_DAYS,
                "category_retention_days": COW_ATTEMPT_CATEGORY_RETENTION_DAYS,
                "category_analysis_days": COW_ATTEMPT_CATEGORY_ANALYSIS_DAYS,
                "category_retention_policy": COW_ATTEMPT_CATEGORY_RETENTION_POLICY,
                "networks": networks,
                "groups": groups,
                "attempts": _flatten_cow_execution_groups(groups, limit),
            }
        )

    @app.get("/api/binance-market/cow-candidate-queue")
    def binance_market_cow_candidate_queue():
        limit = request_int_arg("limit", 100, minimum=1, maximum=500)
        networks = _request_networks_arg()
        pause_guard = _cow_submission_pause_guard(database_url=panel_call("database_url_or_none"))
        if not pause_guard.get("paused") and cow_quote_daemon_enabled():
            ensure_cow_quote_daemon_running(database_url_provider=lambda: panel_call("database_url_or_none"))
        return jsonify(cow_candidate_queue_snapshot(limit=limit, networks=networks))

    @app.get("/api/binance-market/cow-submission-pause")
    def binance_market_cow_submission_pause():
        return jsonify(_cow_submission_pause_guard(database_url=panel_call("database_url_or_none")))

    @app.post("/api/binance-market/cow-submission-pause")
    def binance_market_cow_submission_pause_update():
        payload = request.get_json(silent=True) or {}
        paused = bool(payload.get("paused"))
        reason = payload.get("reason")
        result = _set_cow_submission_pause_guard(paused=paused, reason=reason, database_url=panel_call("database_url_or_none"))
        if not paused:
            result["queue_cleanup"] = clear_cow_candidate_queue(reason="submission_switch_enabled_clear_stale")
            if cow_quote_daemon_enabled():
                ensure_cow_quote_daemon_running(database_url_provider=lambda: panel_call("database_url_or_none"))
        return jsonify(result)

    @app.post("/api/binance-market/cow-submission-pause/clear")
    def binance_market_cow_submission_pause_clear():
        result = _clear_cow_submission_pause_guard(database_url=panel_call("database_url_or_none"))
        result["queue_cleanup"] = clear_cow_candidate_queue(reason="submission_switch_enabled_clear_stale")
        if cow_quote_daemon_enabled():
            ensure_cow_quote_daemon_running(database_url_provider=lambda: panel_call("database_url_or_none"))
        return jsonify(result)
    
    
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
            return jsonify({"error": data_error_message(exc)}), 400
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
                    min_profit_usdc=request_float_arg("min_profit_usdc", 0, minimum=0),
                    deadline_seconds=request_int_arg("deadline_seconds", 600, minimum=1),
                ),
            )
        except Exception as exc:
            return jsonify({"error": data_error_message(exc)}), 400
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
            return jsonify({"error": data_error_message(exc)}), 400
        return jsonify({"symbol": symbol, "dex_name": "Trader Joe V2", "reference_price_usd": reference_price, "costs": payload})
