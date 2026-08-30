from __future__ import annotations

from flask import jsonify, request

from core.chain_data_registry import chain_data_by_chain_id
from core.chain_flow_dispatcher import ChainFlowDispatchError, chain_flow_request, dispatch_chain_flow
from core.chain_selection import ChainSelectionError
from db.storage_cow_execution import (
    clear_cow_execution_attempts_jsonl,
    load_recent_cow_execution_attempts_jsonl,
)
from market.binance_market.dex_surfaces import (
    dex_market_observation_payload,
    dex_market_observation_surface_payload,
    dex_overview_payload,
    dex_quote_request_payload,
    dex_quote_routes_payload,
    dex_submission_review_payload,
)
from market.binance_market.pool_candidates import dex_pool_candidates_payload
from runtime.dex_opportunity_scanner import (
    dex_opportunity_snapshot,
    ensure_dex_opportunity_scanner_running,
    run_dex_opportunity_scan_now,
)
from web import control_panel_data_routes as shared
from web.data_routes import dex_surface_orchestration as dex_surface_orchestration
from web.data_routes.dex_surface_orchestration import (
    _attach_chain_flow_processing,
    _bootstrap_dex_surface_dependencies,
    _finalize_market_observation_payload,
    _market_observation_page_payload,
    _refresh_robinhood_testnet_v4_sources,
    _refresh_state_summary,
    _submission_review_framework_payload,
)
from web.data_routes.dex_surface_response import (
    candidate_pagination_args,
    paginate_candidate_payload,
    request_dex_protocols_arg,
    selected_chain_id_from_request,
    submission_networks_from_request,
)
from web.dex_provenance import framework_label


def _chain_flow_protocol(protocols: list[str]) -> str:
    return protocols[0] if len(protocols) == 1 else ""


def _dex_surface_payload_for_chain(
    *,
    flow_name: str,
    mode: str,
    chain_id: int | None,
    protocols: list[str],
    fallback,
    **kwargs,
) -> dict:
    if chain_id is None:
        return fallback()
    try:
        return dispatch_chain_flow(
            chain_id=chain_id,
            flow_name=flow_name,
            protocol=_chain_flow_protocol(protocols),
            protocols=protocols,
            **kwargs,
        )
    except ChainFlowDispatchError as exc:
        if exc.code == "chain_flow_missing":
            return fallback()
        return {
            "ok": False,
            "mode": mode,
            "chain_id": chain_id,
            "selected_protocols": protocols,
            "code": exc.code,
            "error": exc.message,
        }


def _bootstrap_dex_surface_dependencies(
    *,
    chain_id: int | None,
    protocols: list[str],
    flow_name: str,
) -> None:
    return dex_surface_orchestration._bootstrap_dex_surface_dependencies(
        chain_id=chain_id,
        protocols=protocols,
        flow_name=flow_name,
        pool_candidates_payload=dex_pool_candidates_payload,
        observation_surface_payload=dex_market_observation_surface_payload,
        overview_payload=dex_overview_payload,
        quote_routes_payload=dex_quote_routes_payload,
    )


def _submission_review_framework_payload(*, chain_id: int | None, protocols: list[str]) -> dict:
    return dex_surface_orchestration._submission_review_framework_payload(
        chain_id=chain_id,
        protocols=protocols,
        overview_payload=dex_overview_payload,
        framework_label_fn=framework_label,
    )


def register_dex_surface_routes(app, panel) -> None:
    @app.get("/api/dex/overview")
    def dex_overview():
        try:
            chain_selection, selected_chain_id = selected_chain_id_from_request()
        except ChainSelectionError as exc:
            return shared._chain_selection_error_response(exc)
        protocols = request_dex_protocols_arg()
        _bootstrap_dex_surface_dependencies(
            chain_id=selected_chain_id,
            protocols=protocols,
            flow_name="dex_overview",
        )
        payload = _dex_surface_payload_for_chain(
            flow_name="dex_overview",
            mode="dex_overview",
            chain_id=selected_chain_id,
            protocols=protocols,
            fallback=lambda: dex_overview_payload(
                chain_id=selected_chain_id,
                protocols=protocols,
                use_static_observation=True,
            ),
        )
        payload["chain_selection"] = chain_selection
        return jsonify(payload)

    @app.get("/api/dex/market-observation")
    def dex_market_observation():
        try:
            chain_selection, selected_chain_id = selected_chain_id_from_request()
        except ChainSelectionError as exc:
            return shared._chain_selection_error_response(exc)
        protocols = request_dex_protocols_arg()
        _bootstrap_dex_surface_dependencies(
            chain_id=selected_chain_id,
            protocols=protocols,
            flow_name="dex_market_observation",
        )
        payload = _dex_surface_payload_for_chain(
            flow_name="market_observation",
            mode="dex_market_observation",
            chain_id=selected_chain_id,
            protocols=protocols,
            fallback=lambda: dex_market_observation_payload(chain_id=selected_chain_id, protocols=protocols),
            force_compute=False,
        )
        payload = _finalize_market_observation_payload(
            payload,
            chain_id=selected_chain_id,
            protocols=protocols,
            refresh_mode="full",
            generated_source="generated_live_snapshot",
        )
        payload = _market_observation_page_payload(payload)
        payload["chain_selection"] = chain_selection
        return jsonify(payload)

    @app.post("/api/dex/market-observation/refresh")
    def dex_market_observation_refresh():
        try:
            chain_selection, selected_chain_id = selected_chain_id_from_request()
        except ChainSelectionError as exc:
            return shared._chain_selection_error_response(exc)
        protocols = request_dex_protocols_arg()
        full_refresh = str(request.args.get("full") or "").strip().lower() in {"1", "true", "yes", "full"}
        refresh_mode = "full" if full_refresh else "incremental"
        payload = _dex_surface_payload_for_chain(
            flow_name="market_observation",
            mode="dex_market_observation",
            chain_id=selected_chain_id,
            protocols=protocols,
            fallback=lambda: dex_market_observation_payload(chain_id=selected_chain_id, protocols=protocols),
            force_compute=True,
        )
        payload = _finalize_market_observation_payload(
            payload,
            chain_id=selected_chain_id,
            protocols=protocols,
            refresh_mode=refresh_mode,
            generated_source="refreshed_live_snapshot",
        )
        payload["chain_selection"] = chain_selection
        payload["refresh_state"] = {
            "ok": True,
            "requested": refresh_mode,
            "message": "市场观察全量刷新完成" if full_refresh else "市场观察增量刷新完成",
            "path": (payload.get("marketObservationStaticTable") or {}).get("path"),
            "list_counts": (payload.get("marketObservationStaticTable") or {}).get("list_counts") or {},
            "routeCount": (payload.get("marketObservationStaticTable") or {}).get("summary", {}).get("closedLoopRouteCount"),
            "associatedPairCount": (payload.get("marketObservationStaticTable") or {}).get("summary", {}).get("fundAnchorPairObservationAssociatedPairCount"),
        }
        payload = _market_observation_page_payload(payload)
        return jsonify(payload)

    @app.get("/api/dex/quote-routes")
    def dex_quote_routes():
        try:
            chain_selection, selected_chain_id = selected_chain_id_from_request()
        except ChainSelectionError as exc:
            return shared._chain_selection_error_response(exc)
        protocols = request_dex_protocols_arg()
        _bootstrap_dex_surface_dependencies(
            chain_id=selected_chain_id,
            protocols=protocols,
            flow_name="dex_quote_routes",
        )
        payload = _dex_surface_payload_for_chain(
            flow_name="dex_quote_routes",
            mode="dex_quote_routes",
            chain_id=selected_chain_id,
            protocols=protocols,
            fallback=lambda: dex_quote_routes_payload(
                chain_id=selected_chain_id,
                protocols=protocols,
                use_static_observation=True,
            ),
        )
        payload["chain_selection"] = chain_selection
        return jsonify(payload)

    @app.get("/api/dex/opportunity-scanner/start")
    def dex_opportunity_scanner_start():
        try:
            chain_selection, selected_chain_id = selected_chain_id_from_request()
        except ChainSelectionError as exc:
            return shared._chain_selection_error_response(exc)
        protocols = request_dex_protocols_arg()
        ensure_dex_opportunity_scanner_running(chain_id=selected_chain_id, protocols=protocols)
        payload = dex_opportunity_snapshot(chain_id=selected_chain_id, protocols=protocols)
        payload["chain_selection"] = chain_selection
        return jsonify(payload)

    @app.get("/api/dex/opportunity-scanner/snapshot")
    def dex_opportunity_scanner_snapshot():
        try:
            chain_selection, selected_chain_id = selected_chain_id_from_request()
        except ChainSelectionError as exc:
            return shared._chain_selection_error_response(exc)
        protocols = request_dex_protocols_arg()
        payload = dex_opportunity_snapshot(chain_id=selected_chain_id, protocols=protocols)
        payload["chain_selection"] = chain_selection
        return jsonify(payload)

    @app.get("/api/dex/opportunity-scanner/scan-now")
    def dex_opportunity_scanner_scan_now():
        try:
            chain_selection, selected_chain_id = selected_chain_id_from_request()
        except ChainSelectionError as exc:
            return shared._chain_selection_error_response(exc)
        protocols = request_dex_protocols_arg()
        payload = run_dex_opportunity_scan_now(chain_id=selected_chain_id, protocols=protocols)
        payload["chain_selection"] = chain_selection
        return jsonify(payload)

    @app.get("/api/dex/quote-request")
    def dex_quote_request():
        try:
            chain_selection, selected_chain_id = selected_chain_id_from_request()
        except ChainSelectionError as exc:
            return shared._chain_selection_error_response(exc)
        protocols = request_dex_protocols_arg()
        pair = request.args.get("pair", "").strip()
        venue = request.args.get("venue", "").strip()
        amount = request.args.get("amount", "").strip() or None
        _bootstrap_dex_surface_dependencies(
            chain_id=selected_chain_id,
            protocols=protocols,
            flow_name="dex_quote_request",
        )
        payload = _dex_surface_payload_for_chain(
            flow_name="dex_quote_request",
            mode="dex_quote_request",
            chain_id=selected_chain_id,
            protocols=protocols,
            fallback=lambda: dex_quote_request_payload(
                chain_id=selected_chain_id,
                protocols=protocols,
                pair=pair,
                venue=venue,
                amount=amount,
                use_static_observation=True,
            ),
            pair=pair,
            venue=venue,
            amount=amount,
        )
        payload["chain_selection"] = chain_selection
        return jsonify(payload), 200 if payload.get("ok") else 404

    @app.get("/api/dex-pools/candidates")
    def dex_pool_candidates():
        try:
            chain_selection, selected_chain_id = selected_chain_id_from_request()
        except ChainSelectionError as exc:
            return shared._chain_selection_error_response(exc)
        protocols = request_dex_protocols_arg()
        _bootstrap_dex_surface_dependencies(
            chain_id=selected_chain_id,
            protocols=protocols,
            flow_name="dex_pool_candidates",
        )
        page, page_size, query, quality = candidate_pagination_args()
        payload = dex_pool_candidates_payload(chain_id=selected_chain_id, protocols=protocols)
        payload["chain_selection"] = chain_selection
        return jsonify(paginate_candidate_payload(payload, page=page, page_size=page_size, query=query, quality=quality))

    @app.get("/api/dex-pools/candidates/refresh")
    @app.post("/api/dex-pools/candidates/refresh")
    def dex_pool_candidates_refresh():
        try:
            chain_selection, selected_chain_id = selected_chain_id_from_request()
        except ChainSelectionError as exc:
            return shared._chain_selection_error_response(exc)
        protocols = request_dex_protocols_arg()
        payload = request.get_json(silent=True) or {}
        full_refresh = request.args.get("full", "").strip().lower() in {"1", "true", "yes"} or bool(payload.get("full"))
        refresh_state = {
            "requested": "full" if full_refresh else "incremental",
            "running": True,
            "ok": None,
            "message": "全量刷新进行中" if full_refresh else "增量刷新进行中",
        }
        if full_refresh:
            chain_row = chain_data_by_chain_id(selected_chain_id) if selected_chain_id is not None else None
            network = str((chain_selection or {}).get("network") or (chain_row or {}).get("network") or "").strip().lower()
            if network == "robinhood-testnet" or selected_chain_id == 46630:
                refresh_state = {
                    **refresh_state,
                    **_refresh_robinhood_testnet_v4_sources(),
                    "scope": "robinhood-testnet-v4",
                }
        payload = dex_pool_candidates_payload(
            chain_id=selected_chain_id,
            protocols=protocols,
            refresh=True,
            force_full=full_refresh,
        )
        payload["chain_selection"] = chain_selection
        payload["refresh_state"] = _refresh_state_summary(payload, {**refresh_state, "running": False})
        page, page_size, query, quality = candidate_pagination_args()
        return jsonify(paginate_candidate_payload(payload, page=page, page_size=page_size, query=query, quality=quality))

    @app.get("/api/dex/submissions")
    def dex_submission_review():
        try:
            chain_selection, _selected_chain_id = selected_chain_id_from_request()
        except ChainSelectionError as exc:
            return shared._chain_selection_error_response(exc)
        limit = shared.request_int_arg("limit", 300, minimum=1, maximum=1000)
        protocols = request_dex_protocols_arg()
        networks = submission_networks_from_request(chain_selection)
        _bootstrap_dex_surface_dependencies(
            chain_id=_selected_chain_id,
            protocols=protocols,
            flow_name="dex_submission_review",
        )
        route_framework = _submission_review_framework_payload(chain_id=_selected_chain_id, protocols=protocols)
        groups = shared._cow_execution_attempt_groups(
            lambda **kwargs: load_recent_cow_execution_attempts_jsonl(
                shared.COW_EXECUTION_ATTEMPT_LOG_ROOT,
                retention_days=shared.COW_EXECUTION_RETENTION_DAYS,
                **kwargs,
            ),
            limit=limit,
            networks=networks,
        )
        return jsonify(
            {
                **dex_submission_review_payload(
                    attempts=shared._flatten_cow_execution_groups(groups, limit),
                    groups=groups,
                    source="jsonl",
                ),
                "routeFramework": route_framework,
                "chain_selection": chain_selection,
            }
        )

    @app.post("/api/dex/submissions/clear")
    def dex_submission_clear():
        try:
            chain_selection, _selected_chain_id = selected_chain_id_from_request()
        except ChainSelectionError as exc:
            return shared._chain_selection_error_response(exc)
        networks = submission_networks_from_request(chain_selection)
        results = [clear_cow_execution_attempts_jsonl(shared.COW_EXECUTION_ATTEMPT_LOG_ROOT, networks=networks)]
        return jsonify(
            {
                "ok": True,
                "mode": "dex_submission_clear",
                "chain_selection": chain_selection,
                "networks": networks,
                "cleared": sum(int(item.get("cleared") or 0) for item in results),
                "results": results,
            }
        )


__all__ = ["register_dex_surface_routes"]
