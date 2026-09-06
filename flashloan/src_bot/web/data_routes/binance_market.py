from __future__ import annotations

from flask import jsonify, request

from core.chain_selection import ChainSelectionError
from core.contracts import source_metadata
from core.db.storage_cow_execution import (
    COW_ATTEMPT_CATEGORY_NOT_EXECUTABLE,
    COW_ATTEMPT_CATEGORY_EXECUTION_FAILED,
    COW_ATTEMPT_CATEGORY_EXECUTION_SUCCESS,
    build_cow_market_claim_pairs,
)
from market.observer import ASSETS
from market.binance_market.service import (
    DEFAULT_BINANCE_RAW_SIDE_LIMIT,
    build_binance_market_state,
    build_cow_network_market_claims,
    build_cow_quote_verification,
    build_cow_supported_market_overview,
    cow_network_options,
    load_cow_supported_token_registry,
    select_binance_market_extremes,
)
from dex.runtime.cow_arbitrage_daemon import (
    clear_cow_candidate_queue,
    cow_candidate_queue_snapshot,
    cow_quote_daemon_enabled,
    cow_quote_daemon_status,
    enqueue_cow_candidate_attempts,
    ensure_cow_quote_daemon_running,
    retain_cow_candidate_networks,
)
from dex.runtime.ui_signal_pool_evidence import record_ui_signal_pool_evidence
from dex.intent_trade import submit_cow_intent_trade
from dex.runtime.cow_submission_pause import (
    clear_cow_submission_pause_guard,
    cow_submission_pause_guard_status,
    set_cow_submission_pause_guard,
)
from web.control_panel_market import latest_binance_extremes_file, safe_latest
from market.binance_market.route_services.state import (
    build_market_state_payload,
    build_market_states_payload,
)
from market.binance_market.route_services.quote import (
    apply_cow_quote_execution_payload,
    build_cow_quote_market_state,
    build_cow_quote_response,
    build_cow_quote_verification_payload,
)

from web import control_panel_data_routes as shared
from market.binance_market.pool_candidates import dex_pool_candidates_payload

ROUTE_CONTEXT = shared.ROUTE_CONTEXT
panel_call = shared.panel_call
data_error_message = shared.data_error_message
request_int_arg = shared.request_int_arg
request_float_arg = shared.request_float_arg
request_min_cow_spread_percent = shared.request_min_cow_spread_percent
request_cow_amount = shared.request_cow_amount
request_cow_trade_thresholds = shared.request_cow_trade_thresholds
_request_networks_arg = shared._request_networks_arg
_request_cow_network_arg = shared._request_cow_network_arg
_optional_request_chain_selection = shared._optional_request_chain_selection
_chain_selection_error_response = shared._chain_selection_error_response
_filter_network_claims = shared._filter_network_claims
record_cow_attempt_list_safely = shared.record_cow_attempt_list_safely
record_cow_execution_attempts_safely = shared.record_cow_execution_attempts_safely
record_cow_market_candidates_safely = shared.record_cow_market_candidates_safely
record_ui_signal_pool_evidence_safely = shared.record_ui_signal_pool_evidence_safely
_set_cow_submission_pause_guard = shared._set_cow_submission_pause_guard
_clear_cow_submission_pause_guard = shared._clear_cow_submission_pause_guard
_cow_submission_pause_guard = shared._cow_submission_pause_guard
DEX_ARBITRAGE_SUPPORTED_NETWORK = shared.DEX_ARBITRAGE_SUPPORTED_NETWORK
COW_EXECUTION_ATTEMPT_LOG_PATH = shared.COW_EXECUTION_ATTEMPT_LOG_PATH
COW_EXECUTION_RETENTION_DAYS = shared.COW_EXECUTION_RETENTION_DAYS
COW_EXECUTION_REVIEW_CATEGORIES = shared.COW_EXECUTION_REVIEW_CATEGORIES


def submit_ready_cow_quote_orders(payload: dict, *, market_state: dict) -> dict:
    return shared.submit_ready_cow_quote_orders(
        payload,
        market_state=market_state,
        submitter=submit_cow_intent_trade,
    )


def register_binance_market_routes(app, panel) -> None:
    ROUTE_CONTEXT.bind(panel, globals())
    ROUTE_CONTEXT.bind_panel_provider(panel)

    @app.get("/api/binance-market/state")
    def binance_market_state():
        try:
            chain_selection = _optional_request_chain_selection()
        except ChainSelectionError as exc:
            return _chain_selection_error_response(exc)
        extremes = safe_latest(latest_binance_extremes_file)
        side_limit = request_int_arg("side_limit", 50, minimum=1, maximum=50)
        pair_side_limit = request_int_arg("pair_side_limit", 5, minimum=1, maximum=5)
        amount = request_cow_amount()
        arbitrage_config, slippage_bps, thresholds = request_cow_trade_thresholds(amount)
        cow_network = _request_cow_network_arg()
        market_state = build_market_state_payload(
            extremes,
            assets=list(ASSETS.keys()),
            arbitrage_config=arbitrage_config,
            slippage_bps=slippage_bps,
            thresholds=thresholds,
            cow_network=cow_network,
            side_limit=side_limit,
            pair_side_limit=pair_side_limit,
            database_url=None,
            chain_selection=chain_selection,
            record_market_candidates=record_cow_market_candidates_safely,
            record_pool_evidence=record_ui_signal_pool_evidence_safely,
            filter_network_claims=_filter_network_claims,
        )
        market_state.update(
            source_metadata(
                market_state.get("market_state_source") or "runtime_cache",
                freshness=market_state.get("observed_at"),
                fallback_reason=market_state.get("fallback_reason"),
            )
        )
        return jsonify(market_state)

    @app.get("/api/binance-market/states")
    def binance_market_states():
        try:
            chain_selection = _optional_request_chain_selection()
        except ChainSelectionError as exc:
            return _chain_selection_error_response(exc)
        extremes = safe_latest(latest_binance_extremes_file)
        side_limit = request_int_arg("side_limit", 50, minimum=1, maximum=50)
        pair_side_limit = request_int_arg("pair_side_limit", 5, minimum=1, maximum=5)
        amount = request_cow_amount()
        arbitrage_config, slippage_bps, thresholds = request_cow_trade_thresholds(amount)
        requested = _request_networks_arg()
        mainnet_networks = [
            item["network"]
            for item in cow_network_options()["networks"]
            if not item.get("testnet")
        ]
        if not requested:
            requested = [DEX_ARBITRAGE_SUPPORTED_NETWORK]
        requested = [
            network
            for index, network in enumerate(requested)
            if network in mainnet_networks and network not in requested[:index]
        ][:8]
        if not requested:
            return jsonify({"error": "no supported CoW mainnet networks selected", "states": {}}), 400
        payload = build_market_states_payload(
            extremes,
            assets=list(ASSETS.keys()),
            arbitrage_config=arbitrage_config,
            slippage_bps=slippage_bps,
            thresholds=thresholds,
            side_limit=side_limit,
            pair_side_limit=pair_side_limit,
            requested=requested,
            database_url=None,
            chain_selection=chain_selection,
            record_market_candidates=record_cow_market_candidates_safely,
            record_pool_evidence=record_ui_signal_pool_evidence_safely,
            filter_network_claims=_filter_network_claims,
            retain_networks=retain_cow_candidate_networks,
        )
        return jsonify(payload)

    from web.data_routes.binance_market_cow import register_binance_market_cow_routes

    register_binance_market_cow_routes(app, panel)

    from web.data_routes.dex_surfaces import register_dex_surface_routes

    register_dex_surface_routes(app, panel)

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
        cow_network = _request_cow_network_arg()
        database_url = None
        token_cache = load_cow_supported_token_registry(cow_network=cow_network, database_url=database_url)
        market_state = build_cow_quote_market_state(
            extremes,
            assets=list(ASSETS.keys()),
            arbitrage_config=arbitrage_config,
            slippage_bps=slippage_bps,
            thresholds=thresholds,
            cow_network=cow_network,
            token_cache=token_cache,
            side_limit=side_limit,
            pair_side_limit=pair_side_limit,
            record_pool_evidence=record_ui_signal_pool_evidence_safely,
            select_extremes=select_binance_market_extremes,
            build_market_state=build_binance_market_state,
        )
        try:
            payload = build_cow_quote_verification_payload(
                market_state,
                amount=amount,
                quote_limit=quote_limit,
                owner=owner,
                cow_network=token_cache["network"],
                quote_timeout_seconds=quote_timeout_seconds,
                token_cache=token_cache,
                build_quote_verification=build_cow_quote_verification,
            )
        except Exception as exc:
            return jsonify({"error": data_error_message(exc), "market_state": market_state, "ranking": []}), 400
        pause_guard = _cow_submission_pause_guard(database_url=database_url)
        payload = apply_cow_quote_execution_payload(
            payload,
            market_state=market_state,
            pause_guard=pause_guard,
            database_url=database_url,
            submit_orders=submit_ready_cow_quote_orders,
            record_execution_attempts=record_cow_execution_attempts_safely,
        )
        response_payload = build_cow_quote_response(payload, market_state)
        return jsonify(response_payload)

    from web.data_routes.binance_market_admin import register_binance_market_admin_routes

    register_binance_market_admin_routes(app, panel)
