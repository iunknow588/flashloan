from __future__ import annotations

from flask import jsonify, request

from core.chain_selection import ChainSelectionError
from core.contracts import source_metadata
from db.storage_cow_execution import (
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
from runtime.cow_arbitrage_daemon import (
    clear_cow_candidate_queue,
    cow_candidate_queue_snapshot,
    cow_quote_daemon_enabled,
    cow_quote_daemon_status,
    enqueue_cow_candidate_attempts,
    ensure_cow_quote_daemon_running,
    retain_cow_candidate_networks,
)
from runtime.ui_signal_pool_evidence import record_ui_signal_pool_evidence
from intent_trade import submit_cow_intent_trade
from web.control_panel_cow_pause import (
    clear_cow_submission_pause_guard,
    cow_submission_pause_guard_status,
    set_cow_submission_pause_guard,
)
from web.control_panel_market import latest_binance_extremes_file, safe_latest

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
        min_spread_percent = float(thresholds["adjusted_min_spread_percent"])
        min_side_change_percent = float(thresholds["min_side_change_percent"])
        min_token_price_usd = float(thresholds["min_token_price_usd"])
        cow_network = _request_cow_network_arg()
        database_url = None
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
        market_state["pool_evidence_recording"] = record_ui_signal_pool_evidence_safely(
            market_state,
            extremes=extremes,
        )
        market_state["chain_selection"] = chain_selection
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
        min_spread_percent = float(thresholds["adjusted_min_spread_percent"])
        min_side_change_percent = float(thresholds["min_side_change_percent"])
        min_token_price_usd = float(thresholds["min_token_price_usd"])
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
        database_url = None
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
            market_state["pool_evidence_recording"] = record_ui_signal_pool_evidence_safely(
                market_state,
                extremes=extremes,
            )
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
                "chain_selection": chain_selection,
            }
        )

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
        market_state["pool_evidence_recording"] = record_ui_signal_pool_evidence_safely(
            market_state,
            extremes=extremes,
        )
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
        pause_guard = _cow_submission_pause_guard(database_url=database_url)
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

    from web.data_routes.binance_market_admin import register_binance_market_admin_routes

    register_binance_market_admin_routes(app, panel)
