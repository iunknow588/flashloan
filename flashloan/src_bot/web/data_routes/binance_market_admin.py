from __future__ import annotations

from flask import jsonify, request

from core.chain_selection import ChainSelectionError
from core.market_config import DEFAULT_ROBINHOOD_DEX_MARKET_ID
from db.storage_common import (
    database_unavailable_reason,
    is_database_unavailable_error,
    mark_database_unavailable,
)
from db.storage_cow_execution import (
    COW_ATTEMPT_CATEGORY_ANALYSIS_DAYS,
    COW_ATTEMPT_CATEGORY_RETENTION_DAYS,
    COW_ATTEMPT_CATEGORY_RETENTION_POLICY,
    load_recent_cow_execution_attempts,
    load_recent_cow_execution_attempts_jsonl,
)
from web.chain_selection import resolve_request_chain_selection
from web import control_panel_data_routes as shared
from web.control_panel_robinhood_dex import (
    robinhood_dex_observation_payload,
    robinhood_dex_readiness_payload,
)
from web.data_routes import binance_market as market_routes

panel_call = market_routes.panel_call
data_error_message = market_routes.data_error_message
request_int_arg = market_routes.request_int_arg
_request_networks_arg = market_routes._request_networks_arg
_optional_request_chain_selection = market_routes._optional_request_chain_selection
_chain_selection_error_response = market_routes._chain_selection_error_response
_cow_submission_pause_guard = market_routes._cow_submission_pause_guard
_set_cow_submission_pause_guard = market_routes._set_cow_submission_pause_guard
_clear_cow_submission_pause_guard = market_routes._clear_cow_submission_pause_guard
COW_EXECUTION_ATTEMPT_LOG_PATH = market_routes.COW_EXECUTION_ATTEMPT_LOG_PATH
COW_EXECUTION_RETENTION_DAYS = market_routes.COW_EXECUTION_RETENTION_DAYS
COW_EXECUTION_REVIEW_CATEGORIES = shared.COW_EXECUTION_REVIEW_CATEGORIES
_cow_execution_attempt_groups = shared._cow_execution_attempt_groups
_flatten_cow_execution_groups = shared._flatten_cow_execution_groups
cow_candidate_queue_snapshot = market_routes.cow_candidate_queue_snapshot
cow_quote_daemon_enabled = market_routes.cow_quote_daemon_enabled
ensure_cow_quote_daemon_running = market_routes.ensure_cow_quote_daemon_running
clear_cow_candidate_queue = market_routes.clear_cow_candidate_queue
dex_pool_candidates_payload = market_routes.dex_pool_candidates_payload


def register_binance_market_admin_routes(app, panel) -> None:
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

    @app.get("/api/dex-pools/candidates")
    def dex_pool_candidates():
        try:
            chain_selection = _optional_request_chain_selection()
        except ChainSelectionError as exc:
            return _chain_selection_error_response(exc)
        payload = dex_pool_candidates_payload()
        payload["chain_selection"] = chain_selection
        return jsonify(payload)

    @app.get("/api/robinhood-dex/observation")
    def robinhood_dex_observation():
        try:
            selection = resolve_request_chain_selection(
                database_url=panel_call("database_url_or_none"),
                feature="dex_arbitrage",
                default_market_id=DEFAULT_ROBINHOOD_DEX_MARKET_ID,
            )
            payload = robinhood_dex_observation_payload(
                database_url=panel_call("database_url_or_none"),
                market_id=selection.market_id,
                chain_id=selection.chain_id,
            )
        except (ChainSelectionError, ValueError) as exc:
            code = getattr(exc, "code", "invalid_market_scope")
            return jsonify({"ok": False, "error": str(exc), "code": code}), 400
        return jsonify(payload)

    @app.get("/api/robinhood-dex/readiness")
    def robinhood_dex_readiness():
        try:
            selection = resolve_request_chain_selection(
                database_url=panel_call("database_url_or_none"),
                feature="dex_arbitrage",
                default_market_id=DEFAULT_ROBINHOOD_DEX_MARKET_ID,
            )
            payload = robinhood_dex_readiness_payload(
                market_id=selection.market_id,
                chain_id=selection.chain_id,
            )
        except (ChainSelectionError, ValueError) as exc:
            code = getattr(exc, "code", "invalid_market_scope")
            return jsonify({"ok": False, "error": str(exc), "code": code}), 400
        return jsonify(payload)

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
    
    
