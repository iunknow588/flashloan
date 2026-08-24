from __future__ import annotations

from flask import jsonify, request

from core.chain_selection import ChainSelectionError
from db.storage_common import (
    database_unavailable_reason,
    is_database_unavailable_error,
    mark_database_unavailable,
)
from db.storage_cow_execution import (
    clear_cow_execution_attempts,
    clear_cow_execution_attempts_jsonl,
    load_recent_cow_execution_attempts,
    load_recent_cow_execution_attempts_jsonl,
)
from market.binance_market.dex_surfaces import (
    dex_market_observation_payload,
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


DEX_QUOTE_PROTOCOLS = {
    "uniswap_v2",
    "uniswap_v3",
    "uniswap_v4",
    "pancakeswap_v2",
    "pancakeswap_v3",
    "biswap_v2",
    "quickswap_v2",
    "traderjoe_v2",
    "balancer_v2",
    "curve",
}


def _request_protocols_arg() -> list[str]:
    return [
        item.strip().lower()
        for item in request.args.get("protocols", "").replace("|", ",").split(",")
        if item.strip()
    ]


def _request_dex_protocols_arg() -> list[str]:
    return [protocol for protocol in _request_protocols_arg() if protocol in DEX_QUOTE_PROTOCOLS]


def _selected_chain_id_from_request() -> tuple[dict | None, int | None]:
    chain_selection = shared._optional_request_chain_selection()
    selected_chain_id = None
    if isinstance(chain_selection, dict) and chain_selection.get("chain_id") is not None:
        selected_chain_id = int(chain_selection["chain_id"])
    return chain_selection, selected_chain_id


def _submission_networks_from_request(chain_selection: dict | None) -> list[str]:
    networks = shared._request_networks_arg()
    if not networks and isinstance(chain_selection, dict):
        selected_network = str(chain_selection.get("network") or "").strip().lower()
        if selected_network:
            networks = [selected_network]
    return networks


def register_dex_surface_routes(app, panel) -> None:
    @app.get("/api/dex/overview")
    def dex_overview():
        try:
            chain_selection, selected_chain_id = _selected_chain_id_from_request()
        except ChainSelectionError as exc:
            return shared._chain_selection_error_response(exc)
        payload = dex_overview_payload(chain_id=selected_chain_id, protocols=[])
        payload["chain_selection"] = chain_selection
        return jsonify(payload)

    @app.get("/api/dex/market-observation")
    def dex_market_observation():
        try:
            chain_selection, selected_chain_id = _selected_chain_id_from_request()
        except ChainSelectionError as exc:
            return shared._chain_selection_error_response(exc)
        payload = dex_market_observation_payload(chain_id=selected_chain_id, protocols=[])
        payload["chain_selection"] = chain_selection
        return jsonify(payload)

    @app.get("/api/dex/quote-routes")
    def dex_quote_routes():
        try:
            chain_selection, selected_chain_id = _selected_chain_id_from_request()
        except ChainSelectionError as exc:
            return shared._chain_selection_error_response(exc)
        payload = dex_quote_routes_payload(chain_id=selected_chain_id, protocols=[])
        payload["chain_selection"] = chain_selection
        return jsonify(payload)

    @app.get("/api/dex/opportunity-scanner/start")
    def dex_opportunity_scanner_start():
        try:
            chain_selection, selected_chain_id = _selected_chain_id_from_request()
        except ChainSelectionError as exc:
            return shared._chain_selection_error_response(exc)
        ensure_dex_opportunity_scanner_running(chain_id=selected_chain_id, protocols=[])
        payload = dex_opportunity_snapshot(chain_id=selected_chain_id, protocols=[])
        payload["chain_selection"] = chain_selection
        return jsonify(payload)

    @app.get("/api/dex/opportunity-scanner/snapshot")
    def dex_opportunity_scanner_snapshot():
        try:
            chain_selection, selected_chain_id = _selected_chain_id_from_request()
        except ChainSelectionError as exc:
            return shared._chain_selection_error_response(exc)
        payload = dex_opportunity_snapshot(chain_id=selected_chain_id, protocols=[])
        payload["chain_selection"] = chain_selection
        return jsonify(payload)

    @app.get("/api/dex/opportunity-scanner/scan-now")
    def dex_opportunity_scanner_scan_now():
        try:
            chain_selection, selected_chain_id = _selected_chain_id_from_request()
        except ChainSelectionError as exc:
            return shared._chain_selection_error_response(exc)
        payload = run_dex_opportunity_scan_now(chain_id=selected_chain_id, protocols=[])
        payload["chain_selection"] = chain_selection
        return jsonify(payload)

    @app.get("/api/dex/quote-request")
    def dex_quote_request():
        try:
            chain_selection, selected_chain_id = _selected_chain_id_from_request()
        except ChainSelectionError as exc:
            return shared._chain_selection_error_response(exc)
        payload = dex_quote_request_payload(
            chain_id=selected_chain_id,
            protocols=[],
            pair=request.args.get("pair", "").strip(),
            venue=request.args.get("venue", "").strip(),
            amount=request.args.get("amount", "").strip() or None,
        )
        payload["chain_selection"] = chain_selection
        return jsonify(payload), 200 if payload.get("ok") else 404

    @app.get("/api/dex-pools/candidates")
    def dex_pool_candidates():
        try:
            chain_selection, selected_chain_id = _selected_chain_id_from_request()
        except ChainSelectionError as exc:
            return shared._chain_selection_error_response(exc)
        payload = dex_pool_candidates_payload(chain_id=selected_chain_id, protocols=[])
        payload["chain_selection"] = chain_selection
        return jsonify(payload)

    @app.get("/api/dex/submissions")
    def dex_submission_review():
        try:
            chain_selection, _selected_chain_id = _selected_chain_id_from_request()
        except ChainSelectionError as exc:
            return shared._chain_selection_error_response(exc)
        limit = shared.request_int_arg("limit", 300, minimum=1, maximum=1000)
        networks = _submission_networks_from_request(chain_selection)
        database_url = shared.panel_call("database_url_or_none")
        source = "jsonl"
        if database_url and not database_unavailable_reason(database_url):
            try:
                groups = shared._cow_execution_attempt_groups(
                    lambda **kwargs: load_recent_cow_execution_attempts(
                        database_url,
                        retention_days=shared.COW_EXECUTION_RETENTION_DAYS,
                        **kwargs,
                    ),
                    limit=limit,
                    networks=networks,
                )
                source = "database"
            except Exception as exc:
                if is_database_unavailable_error(exc):
                    mark_database_unavailable(database_url, exc)
                groups = shared._cow_execution_attempt_groups(
                    lambda **kwargs: load_recent_cow_execution_attempts_jsonl(
                        shared.COW_EXECUTION_ATTEMPT_LOG_PATH,
                        retention_days=shared.COW_EXECUTION_RETENTION_DAYS,
                        **kwargs,
                    ),
                    limit=limit,
                    networks=networks,
                )
                source = f"jsonl_fallback:{shared.data_error_message(exc)}"
        else:
            groups = shared._cow_execution_attempt_groups(
                lambda **kwargs: load_recent_cow_execution_attempts_jsonl(
                    shared.COW_EXECUTION_ATTEMPT_LOG_PATH,
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
                    source=source,
                ),
                "chain_selection": chain_selection,
            }
        )

    @app.post("/api/dex/submissions/clear")
    def dex_submission_clear():
        try:
            chain_selection, _selected_chain_id = _selected_chain_id_from_request()
        except ChainSelectionError as exc:
            return shared._chain_selection_error_response(exc)
        networks = _submission_networks_from_request(chain_selection)
        database_url = shared.panel_call("database_url_or_none")
        results = []
        if database_url and not database_unavailable_reason(database_url):
            try:
                results.append(clear_cow_execution_attempts(database_url, networks=networks))
            except Exception as exc:
                if is_database_unavailable_error(exc):
                    mark_database_unavailable(database_url, exc)
                results.append({"cleared": 0, "source": "database", "error": shared.data_error_message(exc)})
        results.append(clear_cow_execution_attempts_jsonl(shared.COW_EXECUTION_ATTEMPT_LOG_PATH, networks=networks))
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
