from __future__ import annotations

from flask import jsonify, request

from core.chain_selection import ChainSelectionError
from web.data_routes import dex_surfaces as _root


def register_dex_scanner_routes(app) -> None:
    @app.get("/api/dex/opportunity-scanner/start")
    def dex_opportunity_scanner_start():
        try:
            chain_selection, selected_chain_id = _root.selected_chain_id_from_request()
        except ChainSelectionError as exc:
            return _root.shared._chain_selection_error_response(exc)
        protocols = _root.request_dex_protocols_arg()
        scanner = _root.get_dex_opportunity_scanner()
        scanner.start(chain_id=selected_chain_id, protocols=protocols)
        payload = _root.dex_opportunity_snapshot(chain_id=selected_chain_id, protocols=protocols)
        payload["chain_selection"] = chain_selection
        return jsonify(_root._attach_dex_strategy(payload))

    @app.get("/api/dex/opportunity-scanner/snapshot")
    def dex_opportunity_scanner_snapshot():
        try:
            chain_selection, selected_chain_id = _root.selected_chain_id_from_request()
        except ChainSelectionError as exc:
            return _root.shared._chain_selection_error_response(exc)
        protocols = _root.request_dex_protocols_arg()
        payload = _root.dex_opportunity_snapshot(chain_id=selected_chain_id, protocols=protocols)
        payload["chain_selection"] = chain_selection
        return jsonify(_root._attach_dex_strategy(payload))

    @app.get("/api/dex/opportunity-scanner/scan-now")
    def dex_opportunity_scanner_scan_now():
        try:
            chain_selection, selected_chain_id = _root.selected_chain_id_from_request()
        except ChainSelectionError as exc:
            return _root.shared._chain_selection_error_response(exc)
        protocols = _root.request_dex_protocols_arg()
        wait_for_scan = str(request.args.get("wait") or "0").strip().lower() in {"1", "true", "yes", "on"}
        if wait_for_scan:
            scanner = _root.get_dex_opportunity_scanner()
            payload = scanner.scan_now(chain_id=selected_chain_id, protocols=protocols)
        else:
            payload = _root.run_dex_opportunity_scan_now(chain_id=selected_chain_id, protocols=protocols)
        payload["chain_selection"] = chain_selection
        return jsonify(_root._attach_dex_strategy(payload))


def register_dex_pool_routes(app) -> None:
    @app.get("/api/dex-pools/candidates")
    def dex_pool_candidates():
        try:
            chain_selection, selected_chain_id = _root.selected_chain_id_from_request()
        except ChainSelectionError as exc:
            return _root.shared._chain_selection_error_response(exc)
        try:
            protocols = _root.request_dex_protocols_arg()
            _root._bootstrap_dex_surface_dependencies(
                chain_id=selected_chain_id,
                protocols=protocols,
                flow_name="dex_pool_candidates",
            )
            page, page_size, query, quality = _root.candidate_pagination_args()
            payload = _root.dex_pool_candidates_payload(chain_id=selected_chain_id, protocols=protocols)
            payload["chain_selection"] = chain_selection
            payload.setdefault("candidates", payload.get("pairs") or [])
            return jsonify(
                _root._attach_dex_strategy(
                    _root.paginate_candidate_payload(payload, page=page, page_size=page_size, query=query, quality=quality)
                )
            )
        except Exception as exc:
            return jsonify(
                {
                    "ok": False,
                    "mode": "dex_pool_candidates",
                    "error": "候选池数据暂不可用，请先执行全量刷新重建本地缓存",
                    "rawError": str(exc)[:500],
                    "chain_id": selected_chain_id,
                    "selected_protocols": _root.request_dex_protocols_arg() if selected_chain_id is not None else [],
                }
            ), 503

    @app.get("/api/dex-pools/candidates/refresh")
    @app.post("/api/dex-pools/candidates/refresh")
    def dex_pool_candidates_refresh():
        try:
            chain_selection, selected_chain_id = _root.selected_chain_id_from_request()
        except ChainSelectionError as exc:
            return _root.shared._chain_selection_error_response(exc)
        protocols = _root.request_dex_protocols_arg()
        payload = request.get_json(silent=True) or {}
        full_refresh = request.args.get("full", "").strip().lower() in {"1", "true", "yes"} or bool(payload.get("full"))
        refresh_state = {
            "requested": "full" if full_refresh else "incremental",
            "running": True,
            "ok": None,
            "message": "全量刷新进行中" if full_refresh else "增量刷新进行中",
        }
        try:
            if full_refresh:
                chain_row = _root.chain_data_by_chain_id(selected_chain_id) if selected_chain_id is not None else None
                network = str(
                    (chain_selection or {}).get("network")
                    or (chain_row or {}).get("network")
                    or ""
                ).strip().lower()
                if network == _root.ROBINHOOD_TESTNET_NETWORK or selected_chain_id == _root.ROBINHOOD_TESTNET_CHAIN_ID:
                    refresh_state = {
                        **refresh_state,
                        **_root._refresh_robinhood_testnet_v4_sources(),
                        "scope": "robinhood-testnet-v4",
                    }
                elif network == _root.ROBINHOOD_NETWORK or selected_chain_id == _root.ROBINHOOD_CHAIN_ID:
                    refresh_state = {
                        **refresh_state,
                        **_root._refresh_v4_sources(chain_id=selected_chain_id, network=network),
                        "scope": "robinhood-mainnet-v4",
                    }
            payload = _root.dex_pool_candidates_payload(
                chain_id=selected_chain_id,
                protocols=protocols,
                refresh=True,
                force_full=full_refresh,
            )
            payload["chain_selection"] = chain_selection
            payload["refresh_state"] = _root._refresh_state_summary(payload, {**refresh_state, "running": False})
            payload.setdefault("candidates", payload.get("pairs") or [])
            page, page_size, query, quality = _root.candidate_pagination_args()
            return jsonify(
                _root._attach_dex_strategy(
                    _root.paginate_candidate_payload(payload, page=page, page_size=page_size, query=query, quality=quality)
                )
            )
        except Exception as exc:
            return jsonify(
                {
                    "ok": False,
                    "mode": "dex_pool_candidates",
                    "error": "候选池刷新失败，底层数据文件缺失或路径失败，请稍后重新全量刷新",
                    "rawError": str(exc)[:500],
                    "chain_id": selected_chain_id,
                    "refresh_state": {
                        **refresh_state,
                        "running": False,
                        "ok": False,
                        "errors": [str(exc)[:200]],
                        "message": "候选池刷新失败，底层数据文件缺失或路径失败",
                    },
                }
            ), 503


def register_dex_submission_routes(app) -> None:
    @app.get("/api/dex/submissions")
    def dex_submission_review():
        try:
            chain_selection, _selected_chain_id = _root.selected_chain_id_from_request()
        except ChainSelectionError as exc:
            return _root.shared._chain_selection_error_response(exc)
        limit = _root.shared.request_int_arg("limit", 300, minimum=1, maximum=1000)
        protocols = _root.request_dex_protocols_arg()
        networks = _root.submission_networks_from_request(chain_selection)
        _root._bootstrap_dex_surface_dependencies(
            chain_id=_selected_chain_id,
            protocols=protocols,
            flow_name="dex_submission_review",
        )
        route_framework = _root._submission_review_framework_payload(
            chain_id=_selected_chain_id,
            protocols=protocols,
        )
        groups = _root.shared._cow_execution_attempt_groups(
            lambda **kwargs: _root.load_recent_cow_execution_attempts_jsonl(
                _root.shared.COW_EXECUTION_ATTEMPT_LOG_ROOT,
                retention_days=_root.shared.COW_EXECUTION_RETENTION_DAYS,
                **kwargs,
            ),
            limit=limit,
            networks=networks,
            dedupe_by_route_evidence=False,
        )
        response_payload = {
            "mode": "dex_submission_review",
            **_root.dex_submission_review_payload(
                attempts=_root.shared._flatten_cow_execution_groups(
                    groups,
                    limit,
                    dedupe_by_route_evidence=False,
                ),
                groups=groups,
                source="jsonl",
            ),
            "routeFramework": route_framework,
            "chain_selection": chain_selection,
            "dex_strategy": _root.request_dex_strategy_arg(),
        }
        response_payload.setdefault("attempts", [])
        response_payload.setdefault("summary", {})
        return jsonify(response_payload)

    @app.post("/api/dex/submissions/clear")
    def dex_submission_clear():
        try:
            chain_selection, _selected_chain_id = _root.selected_chain_id_from_request()
        except ChainSelectionError as exc:
            return _root.shared._chain_selection_error_response(exc)
        networks = _root.submission_networks_from_request(chain_selection)
        results = [
            _root.clear_cow_execution_attempts_jsonl(
                _root.shared.COW_EXECUTION_ATTEMPT_LOG_ROOT,
                networks=networks,
            )
        ]
        return jsonify(
            {
                "ok": True,
                "mode": "dex_submission_clear",
                "chain_selection": chain_selection,
                "dex_strategy": _root.request_dex_strategy_arg(),
                "networks": networks,
                "cleared": sum(int(item.get("cleared") or 0) for item in results),
                "results": results,
            }
        )
