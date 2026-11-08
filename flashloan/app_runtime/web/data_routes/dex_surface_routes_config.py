from __future__ import annotations

from flask import g, jsonify, request

from core.chain_data_registry import chain_supported_protocols
from core.chain_selection import ChainSelectionError
from web.data_routes import dex_surfaces as _root
from web.data_routes.dex_surface_response import mask_dex_surface_placeholder_symbols
from web.data_routes.manager_page_compare import (
    candidate_exchange_refresh_manager_payload,
    closed_loop_manager_page_payload,
    market_observation_manager_page_payload,
    overview_manager_page_payload,
)


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
        payload = overview_manager_page_payload(
            chain_id=selected_chain_id,
            protocols=protocols,
            chain_selection=chain_selection,
            request_id=getattr(g, "request_id", None),
        )
        if not payload.get("ok"):
            upstream_status = payload.get("upstreamStatus")
            response_status = upstream_status if isinstance(upstream_status, int) else 503
            return (
                jsonify(
                    {
                        "ok": False,
                        "mode": "dex_overview",
                        "dataSource": "manager",
                        "error": "manager_read_failed",
                        "managerDetail": payload,
                    }
                ),
                response_status,
            )
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
        payload = closed_loop_manager_page_payload(
            chain_id=selected_chain_id,
            protocols=protocols,
            include_routes=include_routes,
            chain_selection=chain_selection,
            request_id=getattr(g, "request_id", None),
        )
        if not payload.get("ok"):
            upstream_status = payload.get("upstreamStatus")
            response_status = upstream_status if isinstance(upstream_status, int) else 503
            return (
                jsonify(
                    {
                        "ok": False,
                        "mode": "dex_closed_loop_groups",
                        "dataSource": "manager",
                        "error": "manager_read_failed",
                        "managerDetail": payload,
                    }
                ),
                response_status,
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
        include_all = str(request.args.get("include_all") or "").strip().lower() in {
            "1",
            "true",
            "yes",
            "all",
        }
        payload = market_observation_manager_page_payload(
            chain_id=selected_chain_id,
            protocols=protocols,
            include_all=include_all,
            chain_selection=chain_selection,
            request_id=getattr(g, "request_id", None),
        )
        if not payload.get("ok"):
            upstream_status = payload.get("upstreamStatus")
            response_status = upstream_status if isinstance(upstream_status, int) else 503
            return (
                jsonify(
                    {
                        "ok": False,
                        "mode": "dex_market_observation",
                        "dataSource": "manager",
                        "error": "manager_read_failed",
                        "managerDetail": payload,
                    }
                ),
                response_status,
            )
        payload["chain_selection"] = chain_selection
        payload["selected_protocols"] = protocols
        payload.setdefault("mode", "dex_market_observation")
        return jsonify(
            mask_dex_surface_placeholder_symbols(
                _root._attach_dex_strategy(payload)
            )
        )

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
        full_refresh = (
            str(request.args.get("full") or "").strip().lower()
            in {"1", "true", "yes", "full"}
        )
        payload = request.get_json(silent=True)
        payload = payload if isinstance(payload, dict) else {}
        full_refresh = full_refresh or str(payload.get("full") or "").strip().lower() in {
            "1",
            "true",
            "yes",
            "full",
        }
        refresh_mode = "full" if full_refresh else "incremental"
        raw_dry_run = request.args.get("dry_run")
        if raw_dry_run is None:
            raw_dry_run = payload.get("dryRun", payload.get("dry_run"))
        if isinstance(raw_dry_run, bool):
            dry_run = raw_dry_run
        elif str(raw_dry_run or "").strip().lower() in {"true", "1", "yes"}:
            dry_run = True
        elif str(raw_dry_run or "").strip().lower() in {"false", "0", "no"}:
            dry_run = False
        else:
            return jsonify(
                {
                    "ok": False,
                    "mode": "dex_market_observation",
                    "dataSource": "manager",
                    "error": "dry_run_required",
                }
            ), 400
        request_id = str(request.headers.get("X-Request-Id") or "").strip()
        if not request_id:
            return jsonify(
                {
                    "ok": False,
                    "mode": "dex_market_observation",
                    "dataSource": "manager",
                    "error": "request_id_required",
                }
            ), 400
        idempotency_key = str(request.headers.get("Idempotency-Key") or "").strip()
        if not idempotency_key:
            return jsonify(
                {
                    "ok": False,
                    "mode": "dex_market_observation",
                    "dataSource": "manager",
                    "error": "idempotency_key_required",
                }
            ), 400

        manager_result = candidate_exchange_refresh_manager_payload(
            chain_id=selected_chain_id,
            dry_run=dry_run,
            request_id=request_id,
            idempotency_key=idempotency_key,
        )
        if not manager_result.get("ok"):
            upstream_status = manager_result.get("upstreamStatus")
            response_status = upstream_status if isinstance(upstream_status, int) else 503
            return jsonify(
                {
                    "ok": False,
                    "mode": "dex_market_observation",
                    "dataSource": "manager",
                    "error": "manager_write_failed",
                    "managerDetail": manager_result,
                    "refresh_state": {
                        "ok": False,
                        "requested": refresh_mode,
                        "running": False,
                        "dryRun": dry_run,
                        "command": "rebuild_candidate_exchange_pools",
                        "errors": [
                            str(
                                manager_result.get("detail")
                                or "candidate exchange manager rebuild failed"
                            )[:200]
                        ],
                    },
                }
            ), response_status

        include_all = str(request.args.get("include_all") or "").strip().lower() in {
            "1",
            "true",
            "yes",
            "all",
        }
        manager_payload = market_observation_manager_page_payload(
            chain_id=selected_chain_id,
            protocols=protocols,
            include_all=include_all,
            chain_selection=chain_selection,
            request_id=request_id,
        )
        if not manager_payload.get("ok"):
            upstream_status = manager_payload.get("upstreamStatus")
            response_status = upstream_status if isinstance(upstream_status, int) else 503
            return jsonify(
                {
                    "ok": False,
                    "mode": "dex_market_observation",
                    "dataSource": "manager",
                    "error": "manager_read_failed",
                    "managerDetail": manager_payload,
                }
            ), response_status
        manager_payload["chain_selection"] = chain_selection
        manager_payload["refresh_state"] = {
            "ok": True,
            "requested": refresh_mode,
            "message": (
                "市场观察刷新预演完成"
                if dry_run
                else (
                    "市场观察全量刷新完成"
                    if full_refresh
                    else "市场观察增量刷新完成"
                )
            ),
            "running": False,
            "dryRun": manager_result["dryRun"],
            "idempotentReplay": manager_result["idempotentReplay"],
            "changedRows": manager_result["changedRows"],
            "consumersNeedRebuild": manager_result["consumersNeedRebuild"],
            "candidateEdgeVersion": manager_result["candidateEdgeVersion"],
            "observationVersion": manager_result["observationVersion"],
            "observationChecksum": manager_result["materializedChecksum"],
            "dataSource": "manager",
            "command": "rebuild_candidate_exchange_pools",
            "path": (manager_payload.get("marketObservationStaticTable") or {}).get("path"),
            "list_counts": (manager_payload.get("marketObservationStaticTable") or {}).get("list_counts") or {},
            "routeCount": (manager_payload.get("marketObservationStaticTable") or {}).get("summary", {}).get("closedLoopRouteCount"),
            "associatedPairCount": (manager_payload.get("marketObservationStaticTable") or {}).get("summary", {}).get("fundAnchorPairObservationAssociatedPairCount"),
        }
        manager_payload.setdefault("mode", "dex_market_observation")
        g.response_contract_validated = True
        return jsonify(
            mask_dex_surface_placeholder_symbols(
                _root._attach_dex_strategy(manager_payload)
            )
        )
