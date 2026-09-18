from __future__ import annotations

import logging

from flask import jsonify, request

from core.chain_data_registry import chain_data_by_chain_id
from core.chain_selection import ChainSelectionError
from core.db.storage_cow_execution import (
    clear_cow_execution_attempts_jsonl,
    load_recent_cow_execution_attempts_jsonl,
)
from core.market_observation_store import load_market_observation_overview_static
from market.binance_market.dex_flash_loan_route_groups import closed_loop_route_groups_payload
from market.binance_market.dex_strategy_registry import dex_strategy_definition, dex_strategy_key_for_path, dex_strategy_keys, dex_strategy_path_for_key, resolve_dex_strategy_key
from market.binance_market.dex_surfaces import (
    dex_market_observation_payload,
    dex_market_observation_surface_payload,
    dex_overview_payload,
    dex_quote_request_payload,
    dex_quote_routes_payload,
    dex_submission_review_payload,
)
from market.binance_market.pool_candidates import dex_pool_candidates_payload
from dex.runtime.dex_opportunity_scanner import (
    dex_opportunity_snapshot,
    get_dex_opportunity_scanner,
    run_dex_opportunity_scan_now,
)
from dex.runtime.dex_opportunity_helpers import (
    _merge_onchain_evidence_into_route as _merge_onchain_evidence_into_route,
)
from dex.runtime.dex_route_test_recorder import record_dex_route_test_attempt
from dex.intent_trade.chains.robinhood.shared import (
    ROBINHOOD_CHAIN_ID,
    ROBINHOOD_NETWORK,
    ROBINHOOD_TESTNET_CHAIN_ID,
    ROBINHOOD_TESTNET_NETWORK,
)
from web import control_panel_data_routes as shared
from market.binance_market.dex_strategy_config import dex_strategy_state, write_dex_strategy_config
from web.data_routes import dex_surface_orchestration as dex_surface_orchestration
from web.data_routes.dex_surface_orchestration import (
    _attach_chain_flow_processing,
    _bootstrap_dex_surface_dependencies,
    _finalize_market_observation_payload,
    _market_observation_page_payload,
    _refresh_robinhood_testnet_v4_sources,
    _refresh_v4_sources,
    _refresh_state_summary,
    _submission_review_framework_payload,
)
from web.data_routes.dex_surface_response import (
    attach_dex_strategy as _attach_dex_strategy,
    candidate_pagination_args,
    chain_flow_protocol as _chain_flow_protocol,
    dex_surface_payload_for_chain as _dex_surface_payload_for_chain,
    overview_response_payload as _overview_response_payload,
    paginate_candidate_payload,
    request_dex_strategy_arg,
    request_dex_protocols_arg,
    selected_chain_id_from_request,
    submission_networks_from_request,
)
from core.dex_provenance import framework_label
from web.data_routes.dex_surface_helpers import (
    _live_quote_deferred_route,
    _merge_live_route_estimate,
    _merge_recent_attempt_evidence_into_routes,
    _reference_only_live_quote_context,
    _route_dedup_key,
    _scanner_snapshot_is_stale,
    evict_stale_evidence_from_routes,
)


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


logger = logging.getLogger(__name__)


def _submission_review_framework_payload(*, chain_id: int | None, protocols: list[str]) -> dict:
    return dex_surface_orchestration._submission_review_framework_payload(
        chain_id=chain_id,
        protocols=protocols,
        overview_payload=dex_overview_payload,
        framework_label_fn=framework_label,
    )


def register_dex_surface_routes(app, panel) -> None:
    @app.get("/api/dex/strategy")
    def dex_strategy():
        try:
            config = dex_strategy_state()
            requested_strategy_raw = str(request.args.get("dex_strategy", "")).strip().lower()
            requested_strategy = requested_strategy_raw if requested_strategy_raw in set(dex_strategy_keys()) else ""
            page_path = request.args.get("page_path", "").strip()
            page_strategy = dex_strategy_key_for_path(page_path) if page_path else ""
            selected_strategy = resolve_dex_strategy_key(requested_strategy or page_strategy or config.get("active_strategy"))
            selected_definition = dex_strategy_definition(selected_strategy) or {}
            current_definition = dex_strategy_definition(page_strategy) or {}
            payload = {
                "ok": True,
                **config,
                "requested_strategy": requested_strategy or None,
                "page_strategy": page_strategy or None,
                "selected_strategy": selected_strategy,
                "dex_strategy": selected_strategy,
                "selected_strategy_definition": selected_definition,
                "selected_strategy_path": str(selected_definition.get("default_path") or dex_strategy_path_for_key(selected_strategy)),
                "page_strategy_definition": current_definition,
                "page_strategy_path": str(current_definition.get("default_path") or dex_strategy_path_for_key(page_strategy)),
                "page_path": page_path or None,
            }
            return jsonify(payload)
        except Exception as exc:
            return jsonify(error_response("dex_strategy_error", shared.data_error_message(exc) or str(exc))), 400

    @app.post("/api/dex/strategy")
    def post_dex_strategy():
        try:
            payload = write_dex_strategy_config(request.get_json(silent=True) or {})
            state = dex_strategy_state()
            return jsonify({"ok": True, **state, "config": payload})
        except Exception as exc:
            return jsonify(error_response("dex_strategy_error", shared.data_error_message(exc) or str(exc))), 400

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
        payload = _overview_response_payload(payload)
        payload.setdefault("mode", "dex_overview")
        return jsonify(_attach_dex_strategy(payload))

    @app.get("/api/dex/closed-loop-groups")
    def dex_closed_loop_groups():
        try:
            chain_selection, selected_chain_id = selected_chain_id_from_request()
        except ChainSelectionError as exc:
            return shared._chain_selection_error_response(exc)
        protocols = request_dex_protocols_arg()
        include_routes = str(request.args.get("include_routes") or "").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        snapshot = load_market_observation_overview_static(
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
        payload = closed_loop_route_groups_payload(
            snapshot,
            include_routes=include_routes,
        )
        payload["chain_selection"] = chain_selection
        payload["selected_protocols"] = protocols
        payload.setdefault("mode", "dex_closed_loop_groups")
        return jsonify(_attach_dex_strategy(payload))

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
        payload.setdefault("mode", "dex_market_observation")
        return jsonify(_attach_dex_strategy(payload))

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
        payload.setdefault("mode", "dex_market_observation")
        return jsonify(_attach_dex_strategy(payload))

    @app.get("/api/dex/quote-routes")
    def dex_quote_routes():
        try:
            chain_selection, selected_chain_id = selected_chain_id_from_request()
        except ChainSelectionError as exc:
            return shared._chain_selection_error_response(exc)
        protocols = request_dex_protocols_arg()
        use_live_estimate_fallback = str(request.args.get("live_estimate") or "0").strip().lower() in {"1", "true", "yes", "on"}
        with _reference_only_live_quote_context(disable_live=True):
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
        snapshot_container = payload.get("snapshot") if isinstance(payload.get("snapshot"), dict) else {}
        snapshot_data = snapshot_container.get("payload") if isinstance(snapshot_container.get("payload"), dict) else {}
        snapshot_routes = [route for route in (snapshot_data.get("routes") or []) if isinstance(route, dict)]
        top_routes = [route for route in (payload.get("routes") or []) if isinstance(route, dict)]
        base_routes = top_routes or snapshot_routes
        try:
            scanner_payload = dex_opportunity_snapshot(chain_id=selected_chain_id, protocols=protocols)
            scanner_snapshot = scanner_payload.get("snapshot") if isinstance(scanner_payload.get("snapshot"), dict) else {}
            snapshot_updated_at = str(scanner_snapshot.get("updated_at") or "")
            if _scanner_snapshot_is_stale(scanner_payload):
                wait_for_scan = str(request.args.get("wait") or "0").strip().lower() in {"1", "true", "yes", "on"}
                if wait_for_scan:
                    try:
                        scanner = get_dex_opportunity_scanner()
                        scanner.scan_now(chain_id=selected_chain_id, protocols=protocols)
                        scanner_payload = dex_opportunity_snapshot(chain_id=selected_chain_id, protocols=protocols)
                        scanner_snapshot = scanner_payload.get("snapshot") if isinstance(scanner_payload.get("snapshot"), dict) else {}
                        snapshot_updated_at = str(scanner_snapshot.get("updated_at") or "")
                    except Exception as exc:
                        logger.warning("synchronous scan-now refresh failed for dex quote routes: %s", exc)
                        scanner_payload = {}
                        scanner_snapshot = {}
                else:
                    try:
                        run_dex_opportunity_scan_now(chain_id=selected_chain_id, protocols=protocols)
                    except Exception as exc:
                        logger.warning("scan-now refresh failed for dex quote routes: %s", exc)
                if isinstance(scanner_payload, dict) and not _scanner_snapshot_is_stale(scanner_payload):
                    scanner_container = scanner_snapshot
                    scanner_data = scanner_container.get("payload") if isinstance(scanner_container.get("payload"), dict) else {}
                    payload["scannerState"] = {
                        "status": "fresh",
                        "stale": False,
                        "snapshotUpdatedAt": snapshot_updated_at,
                        "message": "scanner snapshot refreshed synchronously; on-chain evidence merged.",
                    }
                    scanner_routes = [route for route in (scanner_data.get("routes") or []) if isinstance(route, dict)]
                    scanner_by_key = {
                        _route_dedup_key(route): route
                        for route in scanner_routes
                    }
                    top_routes = scanner_routes
                    snapshot_routes = scanner_routes
                    snapshot_data["routes"] = scanner_routes
                    payload["routes"] = scanner_routes
                else:
                    payload["scannerState"] = {
                        "status": "stale_refresh_queued",
                        "stale": True,
                        "snapshotUpdatedAt": snapshot_updated_at,
                        "message": "scanner snapshot was stale; queued real-time scan; old scanner evidence was not merged",
                    }
                    scanner_by_key = {}
                    evict_stale_evidence_from_routes(base_routes)
            else:
                scanner_container = scanner_snapshot
                scanner_data = scanner_container.get("payload") if isinstance(scanner_container.get("payload"), dict) else {}
                payload["scannerState"] = {
                    "status": "fresh",
                    "stale": False,
                    "snapshotUpdatedAt": snapshot_updated_at,
                    "message": "scanner snapshot is fresh; on-chain evidence merged.",
                }
                scanner_routes = [route for route in (scanner_data.get("routes") or []) if isinstance(route, dict)]
                scanner_by_key = {
                    _route_dedup_key(route): route
                    for route in scanner_routes
                }
            for route in base_routes:
                key = _route_dedup_key(route)
                scanner_route = scanner_by_key.get(key)
                if not isinstance(scanner_route, dict):
                    continue
                evidence = scanner_route.get("live_onchain_evidence")
                if not isinstance(evidence, dict):
                    quote_analysis = scanner_route.get("quoteAnalysis")
                    if isinstance(quote_analysis, dict):
                        evidence = quote_analysis.get("live_onchain_evidence")
                if isinstance(evidence, dict) and evidence:
                    _merge_onchain_evidence_into_route(route, evidence)
        except (KeyError, TypeError, ValueError, AttributeError, OSError) as exc:
            logger.warning("quote-routes scanner merge failed for chain_id=%s protocols=%s: %s", selected_chain_id, protocols, exc)
        scanner_state = payload.get("scannerState") if isinstance(payload.get("scannerState"), dict) else {}
        if str(scanner_state.get("status") or "") != "stale_refresh_queued":
            _merge_recent_attempt_evidence_into_routes(
                base_routes,
                chain_id=selected_chain_id,
            )
        if use_live_estimate_fallback and (snapshot_routes or top_routes):
            scanner_routes = base_routes
            merged_by_key = {
                _route_dedup_key(route): route
                for route in scanner_routes
                if isinstance(route, dict)
            }
            try:
                from market.binance_market.dex_route_test import is_official_triangle_route
            except Exception:
                is_official_triangle_route = None

            def _priority(route):
                symbols = [str(s) for s in (route.get("routeSymbols") or []) if str(s).strip()]
                if is_official_triangle_route and is_official_triangle_route(symbols):
                    return 0
                return 1
            ordered_routes = sorted(base_routes, key=_priority)
            live_count = 0
            for route in ordered_routes:
                key = _route_dedup_key(route)
                if not key or live_count >= 1:
                    continue
                evidence = route.get("live_onchain_evidence") or {}
                if isinstance(evidence, dict):
                    status = str(evidence.get("status") or "").strip().lower()
                    if status not in {"live_evidence_deferred", "requires_live_quoter", ""}:
                        continue
                analysis = route.get("quoteAnalysis") or {}
                if isinstance(analysis, dict) and analysis.get("livePriceVerified"):
                    continue
                improved = _live_quote_deferred_route(
                    route,
                    chain_id=selected_chain_id,
                    protocols=protocols,
                )
                if improved is not None:
                    merged_by_key[key] = improved
                    live_count += 1
            if top_routes:
                payload["routes"] = list(merged_by_key.values())
                summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
                summary["routeCount"] = len(payload["routes"])
                payload["summary"] = summary
            else:
                snapshot_data["routes"] = list(merged_by_key.values())
                summary = snapshot_data.get("summary") if isinstance(snapshot_data.get("summary"), dict) else {}
                summary["routeCount"] = len(snapshot_data["routes"])
                snapshot_data["summary"] = summary
        payload["chain_selection"] = chain_selection
        payload.setdefault("routes", [])
        payload.setdefault("mode", "dex_quote_routes")
        try:
            overview = load_market_observation_overview_static(
                chain_id=selected_chain_id,
                protocols=protocols,
            )
            if isinstance(overview, dict) and overview.get("exists"):
                groups_payload = closed_loop_route_groups_payload(
                    overview,
                    include_routes=False,
                )
                payload["closedLoopGroups"] = groups_payload.get("groups") or []
            else:
                payload["closedLoopGroups"] = []
        except Exception as exc:
            # The quote-routes surface should remain useful even if the
            # grouped overview payload cannot be built for a chain.
            logger.warning(
                "closed-loop groups overview unavailable for chain_id=%s protocols=%s: %s",
                selected_chain_id,
                protocols,
                exc,
            )
            payload["closedLoopGroups"] = []
        return jsonify(_attach_dex_strategy(payload))

    @app.post("/api/dex/quote-route/test")
    def dex_quote_route_test():
        from market.binance_market.dex_route_test import (
            estimate_route_live_quote,
            is_official_triangle_route,
            normalize_route_symbols,
            recommended_route_test_amount,
            run_official_v4_arb_route_test,
        )

        try:
            chain_selection, selected_chain_id = selected_chain_id_from_request()
        except ChainSelectionError as exc:
            return shared._chain_selection_error_response(exc)
        protocols = request_dex_protocols_arg()
        body = request.get_json(silent=True) or {}
        route_symbols = normalize_route_symbols(
            body.get("routeSymbols")
            or body.get("path")
            or body.get("pair")
            or body.get("symbolPair")
            or ""
        )
        if len(route_symbols) < 2:
            return (
                jsonify(
                    {
                        "ok": False,
                        "mode": "dex_quote_route_test",
                        "error": "route_symbols_required",
                        "detail": "routeSymbols or pair with a closed route is required",
                    }
                ),
                400,
            )
        amount = None
        amount_source = "recommended"
        requested_amount_raw = body.get("amount") if body.get("amount") is not None else body.get("inputAmount")
        if requested_amount_raw is not None and str(requested_amount_raw).strip() != "":
            amount_source = "requested"
            try:
                amount = float(str(requested_amount_raw).strip())
            except (TypeError, ValueError):
                amount = None
        else:
            try:
                amount = recommended_route_test_amount(
                    route_symbols=route_symbols,
                    chain_id=selected_chain_id,
                    protocols=protocols,
                )
            except Exception as exc:
                amount = None
        if amount is None:
            return (
                jsonify(
                    {
                        "ok": False,
                        "mode": "dex_quote_route_test",
                        "error": "amount_invalid",
                        "detail": "amount must be a number or a recommended chain amount must be available",
                    }
                ),
                400,
            )
        if amount <= 0:
            return (
                jsonify(
                    {
                        "ok": False,
                        "mode": "dex_quote_route_test",
                        "error": "amount_invalid",
                        "detail": "amount must be greater than 0",
                    }
                ),
                400,
            )

        mode = str(body.get("mode") or "auto").strip().lower()
        broadcast = str(body.get("broadcast") or "").strip().lower() in {"1", "true", "yes", "on"}
        try:
            if mode == "estimate":
                payload = estimate_route_live_quote(
                    chain_id=selected_chain_id,
                    protocols=protocols,
                    route_symbols=route_symbols,
                    amount=amount,
                )
            else:
                if broadcast or is_official_triangle_route(route_symbols):
                    payload = run_official_v4_arb_route_test(
                        route_symbols=route_symbols,
                        amount=amount,
                        broadcast=broadcast,
                    )
                    if payload.get("ok") is False and payload.get("error") == "official_arb_broadcast_route_unsupported":
                        payload = estimate_route_live_quote(
                            chain_id=selected_chain_id,
                            protocols=protocols,
                            route_symbols=route_symbols,
                            amount=amount,
                        )
                else:
                    payload = estimate_route_live_quote(
                        chain_id=selected_chain_id,
                        protocols=protocols,
                        route_symbols=route_symbols,
                        amount=amount,
                    )
        except Exception as exc:
            payload = {
                "ok": False,
                "mode": "dex_quote_route_test",
                "error": "dex_route_test_failed",
                "detail": str(exc)[:500],
                "routeSymbols": route_symbols,
                "amount": amount,
                "broadcast": broadcast,
            }

        payload["chain_selection"] = chain_selection
        payload["broadcast"] = broadcast
        payload["routeSymbols"] = route_symbols
        payload["amount"] = amount
        payload["amountSource"] = amount_source
        payload["record"] = record_dex_route_test_attempt(
            payload,
            chain_id=selected_chain_id,
            route_symbols=route_symbols,
            amount=amount,
            amount_source=amount_source,
            mode=mode,
            broadcast=broadcast,
            fallback_networks=submission_networks_from_request(chain_selection),
        )
        payload = _attach_dex_strategy(payload)
        return jsonify(payload), 200 if payload.get("ok") else 400

    @app.get("/api/dex/opportunity-scanner/start")
    def dex_opportunity_scanner_start():
        try:
            chain_selection, selected_chain_id = selected_chain_id_from_request()
        except ChainSelectionError as exc:
            return shared._chain_selection_error_response(exc)
        protocols = request_dex_protocols_arg()
        scanner = get_dex_opportunity_scanner()
        scanner.start(chain_id=selected_chain_id, protocols=protocols)
        payload = dex_opportunity_snapshot(chain_id=selected_chain_id, protocols=protocols)
        payload["chain_selection"] = chain_selection
        return jsonify(_attach_dex_strategy(payload))

    @app.get("/api/dex/opportunity-scanner/snapshot")
    def dex_opportunity_scanner_snapshot():
        try:
            chain_selection, selected_chain_id = selected_chain_id_from_request()
        except ChainSelectionError as exc:
            return shared._chain_selection_error_response(exc)
        protocols = request_dex_protocols_arg()
        payload = dex_opportunity_snapshot(chain_id=selected_chain_id, protocols=protocols)
        payload["chain_selection"] = chain_selection
        return jsonify(_attach_dex_strategy(payload))

    @app.get("/api/dex/opportunity-scanner/scan-now")
    def dex_opportunity_scanner_scan_now():
        try:
            chain_selection, selected_chain_id = selected_chain_id_from_request()
        except ChainSelectionError as exc:
            return shared._chain_selection_error_response(exc)
        protocols = request_dex_protocols_arg()
        wait_for_scan = str(request.args.get("wait") or "0").strip().lower() in {"1", "true", "yes", "on"}
        if wait_for_scan:
            scanner = get_dex_opportunity_scanner()
            payload = scanner.scan_now(chain_id=selected_chain_id, protocols=protocols)
        else:
            payload = run_dex_opportunity_scan_now(chain_id=selected_chain_id, protocols=protocols)
        payload["chain_selection"] = chain_selection
        return jsonify(_attach_dex_strategy(payload))

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
        return jsonify(_attach_dex_strategy(payload)), 200 if payload.get("ok") else 404

    @app.get("/api/dex-pools/candidates")
    def dex_pool_candidates():
        try:
            chain_selection, selected_chain_id = selected_chain_id_from_request()
        except ChainSelectionError as exc:
            return shared._chain_selection_error_response(exc)
        try:
            protocols = request_dex_protocols_arg()
            _bootstrap_dex_surface_dependencies(
                chain_id=selected_chain_id,
                protocols=protocols,
                flow_name="dex_pool_candidates",
            )
            page, page_size, query, quality = candidate_pagination_args()
            payload = dex_pool_candidates_payload(chain_id=selected_chain_id, protocols=protocols)
            payload["chain_selection"] = chain_selection
            payload.setdefault("candidates", payload.get("pairs") or [])
            return jsonify(_attach_dex_strategy(paginate_candidate_payload(payload, page=page, page_size=page_size, query=query, quality=quality)))
        except Exception as exc:
            return jsonify(
                {
                    "ok": False,
                    "mode": "dex_pool_candidates",
                    "error": "候选池数据暂不可用，请先执行全量刷新重建本地缓存",
                    "rawError": str(exc)[:500],
                    "chain_id": selected_chain_id,
                    "selected_protocols": request_dex_protocols_arg() if selected_chain_id is not None else [],
                }
            ), 503

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
        try:
            if full_refresh:
                chain_row = chain_data_by_chain_id(selected_chain_id) if selected_chain_id is not None else None
                network = str((chain_selection or {}).get("network") or (chain_row or {}).get("network") or "").strip().lower()
                if network == ROBINHOOD_TESTNET_NETWORK or selected_chain_id == ROBINHOOD_TESTNET_CHAIN_ID:
                    refresh_state = {
                        **refresh_state,
                        **_refresh_robinhood_testnet_v4_sources(),
                        "scope": "robinhood-testnet-v4",
                    }
                elif network == ROBINHOOD_NETWORK or selected_chain_id == ROBINHOOD_CHAIN_ID:
                    refresh_state = {
                        **refresh_state,
                        **_refresh_v4_sources(chain_id=selected_chain_id, network=network),
                        "scope": "robinhood-mainnet-v4",
                    }
            payload = dex_pool_candidates_payload(
                chain_id=selected_chain_id,
                protocols=protocols,
                refresh=True,
                force_full=full_refresh,
            )
            payload["chain_selection"] = chain_selection
            payload["refresh_state"] = _refresh_state_summary(payload, {**refresh_state, "running": False})
            payload.setdefault("candidates", payload.get("pairs") or [])
            page, page_size, query, quality = candidate_pagination_args()
            return jsonify(_attach_dex_strategy(paginate_candidate_payload(payload, page=page, page_size=page_size, query=query, quality=quality)))
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
        response_payload = {
                "mode": "dex_submission_review",
                **dex_submission_review_payload(
                    attempts=shared._flatten_cow_execution_groups(groups, limit),
                    groups=groups,
                    source="jsonl",
                ),
                "routeFramework": route_framework,
                "chain_selection": chain_selection,
                "dex_strategy": request_dex_strategy_arg(),
            }
        response_payload.setdefault("attempts", [])
        response_payload.setdefault("summary", {})
        return jsonify(response_payload)

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
                "dex_strategy": request_dex_strategy_arg(),
                "networks": networks,
                "cleared": sum(int(item.get("cleared") or 0) for item in results),
                "results": results,
            }
        )


__all__ = ["register_dex_surface_routes"]
