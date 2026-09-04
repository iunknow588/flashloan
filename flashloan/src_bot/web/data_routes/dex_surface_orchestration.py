from __future__ import annotations

from flask import request

from core.chain_flow_dispatcher import ChainFlowDispatchError, chain_flow_request
from core.market_observation_store import market_observation_static_table, write_market_observation_static
from intent_trade.chains.robinhood_testnet.v4_pool_index import write_v4_pool_index_from_initialize_logs
from intent_trade.chains.robinhood_testnet.v4_pool_liquidity import write_v4_pool_liquidity_from_contract_evidence
from market.binance_market.pool_candidates import dex_pool_candidates_payload
from market.binance_market.dex_surfaces import (
    dex_market_observation_surface_payload,
    dex_overview_payload,
    dex_quote_routes_payload,
)
from web.dex_provenance import build_route_framework_payload, framework_label


def _bootstrap_dex_surface_dependencies(
    *,
    chain_id: int | None,
    protocols: list[str],
    flow_name: str,
    pool_candidates_payload=dex_pool_candidates_payload,
    observation_surface_payload=dex_market_observation_surface_payload,
    overview_payload=dex_overview_payload,
    quote_routes_payload=dex_quote_routes_payload,
) -> None:
    if chain_id is None:
        return
    warmers = {
        "dex_market_observation": (
            lambda: pool_candidates_payload(chain_id=chain_id, protocols=protocols),
        ),
        # Overview is a display surface. It consumes the compact static
        # snapshot and must not synchronously rebuild the V4 pool universe.
        "dex_overview": (),
        "dex_quote_routes": (
            lambda: pool_candidates_payload(chain_id=chain_id, protocols=protocols),
            lambda: observation_surface_payload(chain_id=chain_id, protocols=protocols, use_static_observation=True),
            lambda: overview_payload(chain_id=chain_id, protocols=protocols, use_static_observation=True),
        ),
        "dex_quote_request": (
            lambda: pool_candidates_payload(chain_id=chain_id, protocols=protocols),
            lambda: observation_surface_payload(chain_id=chain_id, protocols=protocols, use_static_observation=True),
            lambda: overview_payload(chain_id=chain_id, protocols=protocols, use_static_observation=True),
            lambda: quote_routes_payload(chain_id=chain_id, protocols=protocols, use_static_observation=True),
        ),
        "dex_submission_review": (
            lambda: pool_candidates_payload(chain_id=chain_id, protocols=protocols),
            lambda: observation_surface_payload(chain_id=chain_id, protocols=protocols, use_static_observation=True),
            lambda: overview_payload(chain_id=chain_id, protocols=protocols, use_static_observation=True),
        ),
    }
    for warm in warmers.get(flow_name, ()):
        try:
            warm()
        except Exception:
            continue


def _submission_review_framework_payload(
    *,
    chain_id: int | None,
    protocols: list[str],
    overview_payload=dex_overview_payload,
    framework_label_fn=framework_label,
) -> dict:
    if chain_id is None:
        return {}
    overview = overview_payload(chain_id=chain_id, protocols=protocols, use_static_observation=True)
    overview_summary = overview.get("summary") if isinstance(overview.get("summary"), dict) else {}
    closed_loop_routes = [route for route in overview.get("arbitragePaths") or [] if isinstance(route, dict)]
    top_route = closed_loop_routes[0] if closed_loop_routes else {}
    route_framework = build_route_framework_payload(
        route_framework_source=overview.get("routeFrameworkSource") or "market_observation",
        market_observation_source=overview.get("marketObservationSource") or "computed",
        market_observation_source_stage=overview.get("marketObservationSourceStage")
        or overview.get("marketObservationSource")
        or "computed",
        decision_basis=overview.get("decisionBasis") or "-",
        route_count=overview_summary.get("arbitragePathCount") or len(closed_loop_routes) or 0,
        observation_sequence_count=overview_summary.get("marketObservationSequenceCount") or 0,
        observation_anchor_count=overview_summary.get("observationAnchorCount") or 0,
        candidate_route_count=overview_summary.get("candidatePathCount") or 0,
        chain_submission_candidate_count=overview_summary.get("overviewChainSubmissionCandidateCount")
        or overview_summary.get("candidatePathCount")
        or 0,
        top_route_text=top_route.get("pathText") or top_route.get("symbolPair") or "-",
        top_route_status=top_route.get("quoteStatus") or "-",
        framework_label_fn=framework_label_fn,
    )
    route_framework["summary"] = {
        "overview": overview_summary,
        "marketObservation": overview.get("marketObservation") if isinstance(overview.get("marketObservation"), dict) else {},
    }
    return route_framework


def _market_observation_static_metadata(snapshot: dict) -> dict:
    return market_observation_static_table(snapshot)


def _finalize_market_observation_payload(
    payload: dict,
    *,
    chain_id: int | None,
    protocols: list[str],
    refresh_mode: str,
    generated_source: str,
) -> dict:
    if payload.get("marketObservationSource") == "cached_snapshot":
        static_table = payload.get("marketObservationStaticTable") if isinstance(payload.get("marketObservationStaticTable"), dict) else {}
        payload["marketObservationStaticTable"] = static_table or _market_observation_static_metadata(
            {
                "network": static_table.get("network"),
                "chain_id": chain_id,
                "protocols": protocols,
                "exists": True,
                "path": static_table.get("path"),
                "summary": payload.get("summary") or {},
                "list_counts": static_table.get("list_counts") or {},
            }
        )
        payload["marketObservationSourceStage"] = payload.get("marketObservationSourceStage") or payload.get("marketObservationSource") or "cached_snapshot"
        return payload
    stored = write_market_observation_static(
        payload,
        chain_id=chain_id,
        protocols=protocols,
        refresh_mode=refresh_mode,
    )
    payload["marketObservationStaticTable"] = _market_observation_static_metadata(stored)
    payload["marketObservationSource"] = generated_source
    payload["marketObservationSourceStage"] = generated_source
    return payload


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
            protocol=protocols[0] if len(protocols) == 1 else "",
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


__all__ = [
    "_attach_chain_flow_processing",
    "_bootstrap_dex_surface_dependencies",
    "_finalize_market_observation_payload",
    "_market_observation_page_payload",
    "_market_observation_static_metadata",
    "_refresh_robinhood_testnet_v4_sources",
    "_refresh_state_summary",
    "_submission_review_framework_payload",
]
