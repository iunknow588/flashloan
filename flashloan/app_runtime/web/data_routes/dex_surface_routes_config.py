from __future__ import annotations

from flask import jsonify, request

from core.chain_data_registry import chain_supported_protocols
from core.chain_selection import ChainSelectionError
from web.data_routes import dex_surfaces as _root
from web.data_routes.dex_surface_orchestration import _market_observation_static_metadata


def _protocol_not_supported_response(chain_id: int | None, protocols: list[str]):
    if chain_id is None:
        return None
    from core.chain_route_selector import chain_dex_arbitrage_protocols

    supported = {str(item).strip().lower() for item in chain_dex_arbitrage_protocols(chain_id)}
    if not supported:
        supported = {str(item).strip().lower() for item in chain_supported_protocols(chain_id=chain_id)}
    unsupported = [
        str(protocol).strip().lower()
        for protocol in protocols
        if str(protocol).strip().lower() and str(protocol).strip().lower() not in supported
    ]
    if not unsupported:
        return None
    return jsonify({
        "ok": False,
        "code": "protocol_not_supported",
        "error": "protocol_not_supported",
        "detail": "requested protocol is not enabled for this chain",
        "chain_id": chain_id,
        "protocols": protocols,
        "supported_protocols": sorted(supported),
    }), 400


def register_dex_strategy_routes(app) -> None:
    @app.get("/api/dex/strategy")
    def dex_strategy():
        try:
            config = _root.dex_strategy_state()
            requested_strategy_raw = str(request.args.get("dex_strategy", "")).strip().lower()
            requested_strategy = requested_strategy_raw if requested_strategy_raw in set(_root.dex_strategy_keys()) else ""
            page_path = request.args.get("page_path", "").strip()
            page_strategy = _root.dex_strategy_key_for_path(page_path) if page_path else ""
            selected_strategy = _root.resolve_dex_strategy_key(
                requested_strategy or page_strategy or config.get("active_strategy")
            )
            selected_definition = _root.dex_strategy_definition(selected_strategy) or {}
            current_definition = _root.dex_strategy_definition(page_strategy) or {}
            payload = {
                "ok": True,
                **config,
                "requested_strategy": requested_strategy or None,
                "page_strategy": page_strategy or None,
                "selected_strategy": selected_strategy,
                "dex_strategy": selected_strategy,
                "selected_strategy_definition": selected_definition,
                "selected_strategy_path": str(
                    selected_definition.get("default_path") or _root.dex_strategy_path_for_key(selected_strategy)
                ),
                "page_strategy_definition": current_definition,
                "page_strategy_path": str(
                    current_definition.get("default_path") or _root.dex_strategy_path_for_key(page_strategy)
                ),
                "page_path": page_path or None,
            }
            return jsonify(payload)
        except Exception as exc:
            return jsonify(
                _root.error_response("dex_strategy_error", _root.shared.data_error_message(exc) or str(exc))
            ), 400

    @app.post("/api/dex/strategy")
    def post_dex_strategy():
        try:
            payload = _root.write_dex_strategy_config(request.get_json(silent=True) or {})
            state = _root.dex_strategy_state()
            return jsonify({"ok": True, **state, "config": payload})
        except Exception as exc:
            return jsonify(
                _root.error_response("dex_strategy_error", _root.shared.data_error_message(exc) or str(exc))
            ), 400


def register_dex_overview_routes(app) -> None:
    @app.get("/api/dex/overview")
    def dex_overview():
        try:
            chain_selection, selected_chain_id = _root.selected_chain_id_from_request()
        except ChainSelectionError as exc:
            return _root.shared._chain_selection_error_response(exc)
        protocols = _root.request_dex_protocols_arg()
        force_refresh = str(request.args.get("refresh") or "").strip().lower() in {
            "1", "true", "yes", "on", "refresh",
        }
        if force_refresh:
            _root._clear_dex_surface_cache(
                surface="dex_overview",
                chain_id=selected_chain_id,
                protocols=protocols,
                use_static_observation=True,
            )
        _root._bootstrap_dex_surface_dependencies(
            chain_id=selected_chain_id,
            protocols=protocols,
            flow_name="dex_overview",
        )
        payload = _root._dex_surface_payload_for_chain(
            flow_name="dex_overview",
            mode="dex_overview",
            chain_id=selected_chain_id,
            protocols=protocols,
            fallback=lambda: _root.dex_overview_payload(
                chain_id=selected_chain_id,
                protocols=protocols,
                use_static_observation=True,
            ),
        )
        payload["chain_selection"] = chain_selection
        payload = _root._overview_response_payload(payload)
        payload.setdefault("mode", "dex_overview")
        return jsonify(_root._attach_dex_strategy(payload))

    @app.get("/api/dex/closed-loop-groups")
    def dex_closed_loop_groups():
        try:
            chain_selection, selected_chain_id = _root.selected_chain_id_from_request()
        except ChainSelectionError as exc:
            return _root.shared._chain_selection_error_response(exc)
        protocols = _root.request_dex_protocols_arg()
        include_routes = str(request.args.get("include_routes") or "").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        snapshot = _root.load_market_observation_overview_static(
            chain_id=selected_chain_id,
            protocols=protocols,
        )
        if not isinstance(snapshot, dict) or not snapshot.get("exists"):
            return (
                jsonify(
                    {
                        "ok": False,
                        "mode": "dex_closed_loop_groups",
                        "chain_id": selected_chain_id,
                        "selected_protocols": protocols,
                        "error": "market_observation_overview_missing",
                        "detail": "closed-loop overview is not cached yet; refresh market observation first",
                    }
                ),
                404,
            )
        payload = _root.closed_loop_route_groups_payload(
            snapshot,
            include_routes=include_routes,
        )
        payload["chain_selection"] = chain_selection
        payload["selected_protocols"] = protocols
        payload.setdefault("mode", "dex_closed_loop_groups")
        return jsonify(_root._attach_dex_strategy(payload))

    @app.get("/api/dex/market-observation")
    def dex_market_observation():
        try:
            chain_selection, selected_chain_id = _root.selected_chain_id_from_request()
        except ChainSelectionError as exc:
            return _root.shared._chain_selection_error_response(exc)
        protocols = _root.request_dex_protocols_arg()
        rejected = _protocol_not_supported_response(selected_chain_id, protocols)
        if rejected is not None:
            return rejected
        _root._bootstrap_dex_surface_dependencies(
            chain_id=selected_chain_id,
            protocols=protocols,
            flow_name="dex_market_observation",
        )
        payload = _root._dex_surface_payload_for_chain(
            flow_name="market_observation",
            mode="dex_market_observation",
            chain_id=selected_chain_id,
            protocols=protocols,
            fallback=lambda: _root.dex_market_observation_payload(
                chain_id=selected_chain_id,
                protocols=protocols,
            ),
            force_compute=False,
        )
        # A GET page view is read-only: it must never rewrite the static
        # shard files it is currently reading.  Static snapshots are created
        # or refreshed explicitly through POST /api/dex/market-observation/refresh.
        static_table = payload.get("marketObservationStaticTable") if isinstance(payload.get("marketObservationStaticTable"), dict) else {}
        if not static_table:
            payload["marketObservationStaticTable"] = _market_observation_static_metadata(
                {
                    "network": static_table.get("network"),
                    "chain_id": selected_chain_id,
                    "protocols": protocols,
                    "exists": False,
                    "path": static_table.get("path"),
                    "summary": payload.get("summary") or {},
                    "list_counts": static_table.get("list_counts") or {},
                }
            )
        payload = _root._market_observation_page_payload(payload)
        payload["chain_selection"] = chain_selection
        payload.setdefault("mode", "dex_market_observation")
        return jsonify(_root._attach_dex_strategy(payload))

    @app.post("/api/dex/market-observation/refresh")
    def dex_market_observation_refresh():
        try:
            chain_selection, selected_chain_id = _root.selected_chain_id_from_request()
        except ChainSelectionError as exc:
            return _root.shared._chain_selection_error_response(exc)
        protocols = _root.request_dex_protocols_arg()
        rejected = _protocol_not_supported_response(selected_chain_id, protocols)
        if rejected is not None:
            return rejected
        full_refresh = str(request.args.get("full") or "").strip().lower() in {"1", "true", "yes", "full"}
        refresh_mode = "full" if full_refresh else "incremental"
        payload = _root._dex_surface_payload_for_chain(
            flow_name="market_observation",
            mode="dex_market_observation",
            chain_id=selected_chain_id,
            protocols=protocols,
            fallback=lambda: _root.dex_market_observation_payload(
                chain_id=selected_chain_id,
                protocols=protocols,
            ),
            force_compute=True,
        )
        payload = _root._finalize_market_observation_payload(
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
        payload = _root._market_observation_page_payload(payload)
        payload.setdefault("mode", "dex_market_observation")
        return jsonify(_root._attach_dex_strategy(payload))
