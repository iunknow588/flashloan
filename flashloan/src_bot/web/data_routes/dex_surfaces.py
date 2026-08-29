from __future__ import annotations

import math

from flask import jsonify, request

from core.chain_data_registry import chain_data_by_chain_id
from core.chain_flow_dispatcher import ChainFlowDispatchError, chain_flow_request, dispatch_chain_flow
from core.chain_selection import ChainSelectionError
from core.market_observation_store import (
    load_market_observation_static,
    observation_payload_from_static,
    market_observation_static_table,
    write_market_observation_static,
)
from db.storage_cow_execution import (
    clear_cow_execution_attempts_jsonl,
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
from intent_trade.chains.robinhood_testnet.v4_pool_index import write_v4_pool_index_from_initialize_logs
from intent_trade.chains.robinhood_testnet.v4_pool_liquidity import write_v4_pool_liquidity_from_contract_evidence
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


def _number_like(value: object) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _candidate_group_has_data(group: dict) -> bool:
    return _number_like(group.get("ycPoolCount")) > 0 or _number_like(group.get("zcPoolCount")) > 0


def _candidate_group_matches(group: dict, query: str) -> bool:
    if not query:
        return True
    parts = [
        group.get("anchorSymbol"),
        *(group.get("anchorAddresses") or []),
        *[
            " ".join(str(part or "") for part in (item.get("symbol"), item.get("address"), item.get("poolCount")))
            for item in [*(group.get("ycTokens") or []), *(group.get("sharedTokens") or [])]
            if isinstance(item, dict)
        ],
        *[
            " ".join(str(part or "") for part in (item.get("symbol"), item.get("address"), item.get("poolCount")))
            for item in group.get("zcTokens") or []
            if isinstance(item, dict)
        ],
        *[
            " ".join(str(part or "") for part in (row.get("pool"), row.get("routeText"), row.get("counterSymbol"), row.get("counterAddress"), row.get("protocol"), row.get("fee")))
            for row in [*(group.get("ycPools") or []), *(group.get("zcPools") or []), *(group.get("sharedPools") or [])]
            if isinstance(row, dict)
        ],
    ]
    return query in " ".join(str(part or "") for part in parts).lower()


def _candidate_pair_keys(groups: list[dict]) -> set[str]:
    keys: set[str] = set()
    for group in groups:
        for row in [*(group.get("ycPools") or []), *(group.get("zcPools") or []), *(group.get("sharedPools") or [])]:
            if not isinstance(row, dict):
                continue
            for key in (row.get("pairSymbol"), row.get("pairAddress")):
                value = str(key or "").strip()
                if value:
                    keys.add(value)
            token0_symbol = str(row.get("token0Symbol") or "").strip()
            token1_symbol = str(row.get("token1Symbol") or "").strip()
            if token0_symbol and token1_symbol:
                keys.add(f"{token0_symbol}:{token1_symbol}")
            token0_address = str(row.get("token0") or "").strip()
            token1_address = str(row.get("token1") or "").strip()
            if token0_address and token1_address:
                keys.add(f"{token0_address}:{token1_address}")
    return keys


def _pair_in_keys(pair: dict, keys: set[str]) -> bool:
    return any(str(pair.get(name) or "").strip() in keys for name in ("symbolPair", "addressPair"))


def _paginate_candidate_payload(payload: dict, *, page: int, page_size: int, query: str, quality: str) -> dict:
    groups = [group for group in payload.get("anchorGroups") or [] if isinstance(group, dict)]
    pairs = [pair for pair in payload.get("pairs") or [] if isinstance(pair, dict)]
    use_groups = bool(groups)
    rows = groups if use_groups else pairs
    unfiltered_count = len(rows)
    if use_groups:
        if quality == "with_data":
            groups = [group for group in groups if _candidate_group_has_data(group)]
        elif quality == "empty":
            groups = [group for group in groups if not _candidate_group_has_data(group)]
        groups = [group for group in groups if _candidate_group_matches(group, query)]
        rows = groups
    elif query:
        rows = [pair for pair in pairs if query in " ".join(str(value or "") for value in pair.values()).lower()]

    total_count = len(rows)
    page_count = max(1, math.ceil(total_count / page_size))
    page = min(max(1, page), page_count)
    start = (page - 1) * page_size
    paged_rows = rows[start : start + page_size]
    paged_payload = dict(payload)

    if use_groups:
        paged_groups = list(paged_rows)
        pair_keys = _candidate_pair_keys(paged_groups)
        paged_payload["anchorGroups"] = paged_groups
        paged_payload["pairs"] = [pair for pair in pairs if _pair_in_keys(pair, pair_keys)]
    else:
        paged_payload["pairs"] = list(paged_rows)

    pagination = {
        "unit": "anchor_group" if use_groups else "pair",
        "page": page,
        "page_size": page_size,
        "page_count": page_count,
        "total_count": total_count,
        "unfiltered_count": unfiltered_count,
        "offset": start,
        "display_start": 0 if total_count == 0 else start + 1,
        "display_end": min(start + page_size, total_count),
        "has_previous": page > 1,
        "has_next": page < page_count,
    }
    paged_payload["pagination"] = {**(payload.get("pagination") or {}), "candidate_groups": pagination}
    filters = payload.get("filters") if isinstance(payload.get("filters"), dict) else {}
    paged_payload["filters"] = {**filters, "query": query, "quality": quality}
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    paged_payload["summary"] = {
        **summary,
        "visibleAnchorGroupCount": str(len(paged_payload.get("anchorGroups") or [])),
        "filteredAnchorGroupCount": str(total_count if use_groups else len(paged_payload.get("anchorGroups") or [])),
    }
    return paged_payload


def _candidate_pagination_args() -> tuple[int, int, str, str]:
    page = shared.request_int_arg("page", 1, minimum=1)
    page_size = shared.request_int_arg("page_size", 1, minimum=1, maximum=20)
    query = request.args.get("q", "").strip().lower()
    quality = request.args.get("quality", "all").strip().lower()
    if quality not in {"all", "with_data", "empty"}:
        quality = "all"
    return page, page_size, query, quality


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


def _attach_chain_flow_processing(
    payload: dict,
    *,
    flow_name: str,
    chain_id: int | None,
    protocols: list[str],
) -> dict:
    if chain_id is None or payload.get("processing"):
        return payload
    try:
        request_payload = chain_flow_request(
            chain_id=chain_id,
            flow_name=flow_name,
            protocol=_chain_flow_protocol(protocols),
        ).as_dict()
    except ChainFlowDispatchError:
        return payload
    allowed = {
        str(protocol or "").strip().lower()
        for protocol in request_payload.get("allowed_protocols") or []
        if str(protocol or "").strip()
    }
    selected = [
        str(protocol or "").strip().lower()
        for protocol in protocols
        if str(protocol or "").strip()
    ]
    if allowed and any(protocol not in allowed for protocol in selected):
        return payload
    payload["processing"] = request_payload
    return payload


def _refresh_robinhood_testnet_v4_sources() -> dict:
    steps: list[dict] = []
    errors: list[str] = []
    try:
        index_result = write_v4_pool_index_from_initialize_logs()
        steps.append(
            {
                "source": "v4_pool_index",
                "ok": True,
                "outputPath": index_result.get("outputPath"),
                "poolCount": len(index_result.get("pools") or []),
                "poolKeyCount": len(index_result.get("pool_keys") or []),
            }
        )
    except Exception as exc:
        errors.append(f"v4_pool_index: {exc}")
        steps.append({"source": "v4_pool_index", "ok": False, "error": str(exc)})

    try:
        liquidity_result = write_v4_pool_liquidity_from_contract_evidence(
            token_balance_scan_scope="active_plus_known",
            multicall_chunk_size=200,
            max_token_balance_scan_seconds=None,
        )
        steps.append(
            {
                "source": "v4_pool_liquidity",
                "ok": True,
                "outputPath": liquidity_result.get("outputPath"),
                "poolCount": len(liquidity_result.get("pools") or []),
                "poolKeyCount": len(liquidity_result.get("pool_keys") or []),
                "tokenBalanceScanComplete": liquidity_result.get("tokenBalanceScanComplete"),
                "tokenBalanceScanCount": liquidity_result.get("tokenBalanceScanCount"),
                "tokenBalancePendingCount": liquidity_result.get("tokenBalancePendingCount"),
            }
        )
    except Exception as exc:
        errors.append(f"v4_pool_liquidity: {exc}")
        steps.append({"source": "v4_pool_liquidity", "ok": False, "error": str(exc)})

    return {
        "ok": not errors,
        "source": "robinhood_testnet_v4_rebuild",
        "steps": steps,
        "errors": errors,
        "message": "Robinhood Testnet V4 source files rebuilt" if not errors else "Robinhood Testnet V4 rebuild completed with errors",
    }


def _refresh_state_summary(payload: dict, refresh_state: dict) -> dict:
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    pool_static = payload.get("poolStaticTable") if isinstance(payload.get("poolStaticTable"), dict) else {}
    candidate_count = summary.get("anchorGroupCount") or summary.get("pairTableCount") or len(payload.get("pairs") or [])
    refresh_state = dict(refresh_state)
    if refresh_state.get("ok") is None:
        refresh_state["ok"] = True
    refresh_state["candidateCount"] = candidate_count
    refresh_state["poolStaticPath"] = pool_static.get("path")
    refresh_state["poolStaticExists"] = pool_static.get("exists")
    if refresh_state.get("ok") is True:
        prefix = "全量刷新完成" if refresh_state.get("requested") == "full" else "增量刷新完成"
        refresh_state["message"] = f"{prefix}，候选分组 {candidate_count}"
    elif refresh_state.get("ok") is False and refresh_state.get("errors"):
        refresh_state["message"] = f"{refresh_state.get('message')}: {refresh_state['errors'][0]}"
    return refresh_state


def _market_observation_static_metadata(snapshot: dict) -> dict:
    return market_observation_static_table(snapshot)


def _market_observation_payload_from_static(snapshot: dict) -> dict:
    return observation_payload_from_static(snapshot)


def _market_observation_page_payload(payload: dict, *, pool_preview_limit: int = 80) -> dict:
    if str(request.args.get("include_all") or "").strip().lower() in {"1", "true", "yes", "all"}:
        return payload
    rows = payload.get("chainSpecificObservationPools")
    if not isinstance(rows, list) or len(rows) <= pool_preview_limit:
        return payload
    trimmed = dict(payload)
    trimmed["chainSpecificObservationPools"] = rows[:pool_preview_limit]
    trimmed["marketObservationPreview"] = {
        "chainSpecificObservationPoolsReturned": pool_preview_limit,
        "chainSpecificObservationPoolsTotal": len(rows),
        "includeAllParameter": "include_all=1",
    }
    return trimmed


def register_dex_surface_routes(app, panel) -> None:
    @app.get("/api/dex/overview")
    def dex_overview():
        try:
            chain_selection, selected_chain_id = _selected_chain_id_from_request()
        except ChainSelectionError as exc:
            return shared._chain_selection_error_response(exc)
        protocols = _request_dex_protocols_arg()
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
            chain_selection, selected_chain_id = _selected_chain_id_from_request()
        except ChainSelectionError as exc:
            return shared._chain_selection_error_response(exc)
        protocols = _request_dex_protocols_arg()
        snapshot = load_market_observation_static(chain_id=selected_chain_id, protocols=protocols)
        if snapshot.get("exists"):
            payload = _market_observation_payload_from_static(snapshot)
            payload = _attach_chain_flow_processing(
                payload,
                flow_name="market_observation",
                chain_id=selected_chain_id,
                protocols=protocols,
            )
        else:
            payload = _dex_surface_payload_for_chain(
                flow_name="market_observation",
                mode="dex_market_observation",
                chain_id=selected_chain_id,
                protocols=protocols,
                fallback=lambda: dex_market_observation_payload(chain_id=selected_chain_id, protocols=protocols),
                force_compute=True,
            )
            stored = write_market_observation_static(
                payload,
                chain_id=selected_chain_id,
                protocols=protocols,
                refresh_mode="full",
            )
            payload["marketObservationStaticTable"] = _market_observation_static_metadata(stored)
            payload["marketObservationSource"] = "generated_missing_static_file"
        payload = _market_observation_page_payload(payload)
        payload["chain_selection"] = chain_selection
        return jsonify(payload)

    @app.post("/api/dex/market-observation/refresh")
    def dex_market_observation_refresh():
        try:
            chain_selection, selected_chain_id = _selected_chain_id_from_request()
        except ChainSelectionError as exc:
            return shared._chain_selection_error_response(exc)
        protocols = _request_dex_protocols_arg()
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
        stored = write_market_observation_static(
            payload,
            chain_id=selected_chain_id,
            protocols=protocols,
            refresh_mode=refresh_mode,
        )
        payload["chain_selection"] = chain_selection
        payload["marketObservationStaticTable"] = _market_observation_static_metadata(stored)
        payload["marketObservationSource"] = "refreshed_static_file"
        payload["refresh_state"] = {
            "ok": True,
            "requested": refresh_mode,
            "message": "市场观察全量刷新完成" if full_refresh else "市场观察增量刷新完成",
            "path": stored.get("path"),
            "list_counts": stored.get("list_counts") or {},
            "routeCount": (stored.get("summary") or {}).get("closedLoopRouteCount"),
            "associatedPairCount": (stored.get("summary") or {}).get("fundAnchorPairObservationAssociatedPairCount"),
        }
        payload = _market_observation_page_payload(payload)
        return jsonify(payload)

    @app.get("/api/dex/quote-routes")
    def dex_quote_routes():
        try:
            chain_selection, selected_chain_id = _selected_chain_id_from_request()
        except ChainSelectionError as exc:
            return shared._chain_selection_error_response(exc)
        protocols = _request_dex_protocols_arg()
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
            chain_selection, selected_chain_id = _selected_chain_id_from_request()
        except ChainSelectionError as exc:
            return shared._chain_selection_error_response(exc)
        protocols = _request_dex_protocols_arg()
        ensure_dex_opportunity_scanner_running(chain_id=selected_chain_id, protocols=protocols)
        payload = dex_opportunity_snapshot(chain_id=selected_chain_id, protocols=protocols)
        payload["chain_selection"] = chain_selection
        return jsonify(payload)

    @app.get("/api/dex/opportunity-scanner/snapshot")
    def dex_opportunity_scanner_snapshot():
        try:
            chain_selection, selected_chain_id = _selected_chain_id_from_request()
        except ChainSelectionError as exc:
            return shared._chain_selection_error_response(exc)
        protocols = _request_dex_protocols_arg()
        payload = dex_opportunity_snapshot(chain_id=selected_chain_id, protocols=protocols)
        payload["chain_selection"] = chain_selection
        return jsonify(payload)

    @app.get("/api/dex/opportunity-scanner/scan-now")
    def dex_opportunity_scanner_scan_now():
        try:
            chain_selection, selected_chain_id = _selected_chain_id_from_request()
        except ChainSelectionError as exc:
            return shared._chain_selection_error_response(exc)
        protocols = _request_dex_protocols_arg()
        payload = run_dex_opportunity_scan_now(chain_id=selected_chain_id, protocols=protocols)
        payload["chain_selection"] = chain_selection
        return jsonify(payload)

    @app.get("/api/dex/quote-request")
    def dex_quote_request():
        try:
            chain_selection, selected_chain_id = _selected_chain_id_from_request()
        except ChainSelectionError as exc:
            return shared._chain_selection_error_response(exc)
        protocols = _request_dex_protocols_arg()
        pair = request.args.get("pair", "").strip()
        venue = request.args.get("venue", "").strip()
        amount = request.args.get("amount", "").strip() or None
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
            chain_selection, selected_chain_id = _selected_chain_id_from_request()
        except ChainSelectionError as exc:
            return shared._chain_selection_error_response(exc)
        protocols = _request_dex_protocols_arg()
        page, page_size, query, quality = _candidate_pagination_args()
        payload = dex_pool_candidates_payload(chain_id=selected_chain_id, protocols=protocols)
        payload["chain_selection"] = chain_selection
        return jsonify(_paginate_candidate_payload(payload, page=page, page_size=page_size, query=query, quality=quality))

    @app.get("/api/dex-pools/candidates/refresh")
    @app.post("/api/dex-pools/candidates/refresh")
    def dex_pool_candidates_refresh():
        try:
            chain_selection, selected_chain_id = _selected_chain_id_from_request()
        except ChainSelectionError as exc:
            return shared._chain_selection_error_response(exc)
        protocols = _request_dex_protocols_arg()
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
        page, page_size, query, quality = _candidate_pagination_args()
        return jsonify(_paginate_candidate_payload(payload, page=page, page_size=page_size, query=query, quality=quality))

    @app.get("/api/dex/submissions")
    def dex_submission_review():
        try:
            chain_selection, _selected_chain_id = _selected_chain_id_from_request()
        except ChainSelectionError as exc:
            return shared._chain_selection_error_response(exc)
        limit = shared.request_int_arg("limit", 300, minimum=1, maximum=1000)
        networks = _submission_networks_from_request(chain_selection)
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
                    source="jsonl",
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
        results = [clear_cow_execution_attempts_jsonl(shared.COW_EXECUTION_ATTEMPT_LOG_PATH, networks=networks)]
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
