from __future__ import annotations

import logging

from flask import request

from core.chain_flow_dispatcher import ChainFlowDispatchError, chain_flow_request
from core.market_observation_store import market_observation_static_table, write_market_observation_static
from market.binance_market.dex_surface_cache import (
    _cached_surface_payload,
    _cached_framework_surface_payload,
    _cache_surface_payload,
    _clear_dex_surface_cache,
)
from dex.tools.refresh_robinhood_v4_live import refresh_v4_live
from market.binance_market.pool_candidates import dex_pool_candidates_payload
from market.binance_market.dex_surfaces import (
    _database_first_v4_mainnet,
    dex_market_observation_surface_payload,
    dex_overview_payload,
    dex_quote_routes_payload,
)
from core.dex_provenance import build_route_framework_payload, framework_label
from dex.intent_trade.chains.robinhood.shared import (
    ROBINHOOD_CHAIN_ID,
    ROBINHOOD_NETWORK,
    ROBINHOOD_TESTNET_CHAIN_ID,
    ROBINHOOD_TESTNET_NETWORK,
)

logger = logging.getLogger(__name__)


def _friendly_oserror(prefix: str, exc: Exception) -> str:
    text = str(exc or "").lower()
    if "filenotfounderror" in text or "no such file" in text or "winerror" in text or "errno 2" in text:
        return "Robinhood Chain 候选池源文件缺失或缓存路径失效，请先执行全量刷新重建本地证据缓存"
    return f"{prefix}"


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
    if (
        flow_name in {"dex_quote_routes", "dex_quote_request"}
        and _cached_framework_surface_payload(
            "dex_quote_routes",
            chain_id=chain_id,
            protocols=protocols,
            use_static_observation=True,
        ) is not None
    ):
        return
    warmers = {
        # Market observation is a static display surface. It reads the compact
        # observation snapshot directly and does not need the full candidate
        # pool universe warmed synchronously during navigation.
        "dex_market_observation": (),
        # Overview is a display surface. It consumes the compact static
        # snapshot and must not synchronously rebuild the V4 pool universe.
        "dex_overview": (),
        "dex_quote_routes": (
            lambda: pool_candidates_payload(chain_id=chain_id, protocols=protocols),
            lambda: (
                observation_surface_payload(
                    chain_id=chain_id,
                    protocols=protocols,
                    use_static_observation=True,
                )
                if not _database_first_v4_mainnet(chain_id=chain_id, protocols=protocols)
                else None
            ),
            lambda: overview_payload(chain_id=chain_id, protocols=protocols, use_static_observation=True),
        ),
        "dex_quote_request": (
            lambda: pool_candidates_payload(chain_id=chain_id, protocols=protocols),
            lambda: (
                observation_surface_payload(
                    chain_id=chain_id,
                    protocols=protocols,
                    use_static_observation=True,
                )
                if not _database_first_v4_mainnet(chain_id=chain_id, protocols=protocols)
                else None
            ),
            lambda: overview_payload(chain_id=chain_id, protocols=protocols, use_static_observation=True),
            lambda: quote_routes_payload(chain_id=chain_id, protocols=protocols, use_static_observation=True),
        ),
        # The submission review page does not need the full candidate pool
        # universe warmed synchronously.  Its route-framework panel uses the
        # overview payload directly, which is already cached/static.
        "dex_submission_review": (),
    }
    warm_surfaces = {
        "dex_market_observation": [],
        "dex_overview": [],
        "dex_quote_routes": [None, "dex_market_observation", "dex_overview"],
        "dex_quote_request": [None, "dex_market_observation", "dex_overview", "dex_quote_routes"],
        "dex_submission_review": [None, "dex_market_observation", "dex_overview"],
    }
    for surface, warm in zip(warm_surfaces.get(flow_name, []), warmers.get(flow_name, ())):
        if surface and _cached_framework_surface_payload(
            surface,
            chain_id=chain_id,
            protocols=protocols,
            use_static_observation=True,
        ) is not None:
            continue
        try:
            warm()
        except Exception:
            logger.warning("failed to warm DEX surface dependency", extra={"flow_name": flow_name}, exc_info=True)
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
    if _database_first_v4_mainnet(chain_id=chain_id, protocols=protocols):
        # Mainnet V4 is DB-first: refresh caches the computed payload in
        # memory and must not rewrite legacy observation JSON shards.
        _clear_dex_surface_cache(
            chain_id=chain_id,
            use_static_observation=True,
        )
        _cache_surface_payload(
            "dex_market_observation",
            payload,
            chain_id=chain_id,
            protocols=protocols,
            use_static_observation=True,
            ttl_seconds=0,
        )
        payload["marketObservationStaticTable"] = _market_observation_static_metadata(
            {
                "network": "robinhood",
                "chain_id": chain_id,
                "protocols": protocols,
                "exists": True,
                "path": "sqlite_db",
                "summary": payload.get("summary") or {},
                "list_counts": {},
            }
        )
        payload["marketObservationSource"] = generated_source
        payload["marketObservationSourceStage"] = generated_source
        return payload
    stored = write_market_observation_static(
        payload,
        chain_id=chain_id,
        protocols=protocols,
        refresh_mode=refresh_mode,
    )
    _clear_dex_surface_cache(
        chain_id=chain_id,
        use_static_observation=True,
    )
    # A POST refresh is the only path that recomputes market observation. Keep
    # the freshly generated payload in the process-global surface cache so GET
    # page views serve the same in-memory snapshot instead of re-reading and
    # hydrating the persisted shard files on every request.
    _cache_surface_payload(
        "dex_market_observation",
        payload,
        chain_id=chain_id,
        protocols=protocols,
        use_static_observation=True,
        ttl_seconds=0,
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


def _refresh_v4_sources(
    *,
    chain_id: int | None = None,
    network: str = "",
    full: bool = True,
    database_url: str = "",
) -> dict:
    network = str(network or "").strip().lower()
    chain_id = int(chain_id) if chain_id is not None else None
    is_mainnet = chain_id == ROBINHOOD_CHAIN_ID or network == ROBINHOOD_NETWORK
    is_testnet = chain_id == ROBINHOOD_TESTNET_CHAIN_ID or network == ROBINHOOD_TESTNET_NETWORK
    if not is_mainnet and not is_testnet:
        return {
            "ok": False,
            "source": "v4_rebuild_unsupported_chain",
            "steps": [{"source": "v4_pool_index", "ok": False, "error": "V4 pool index refresh is only enabled for Robinhood mainnet or testnet"}],
            "errors": ["V4 pool index refresh is only enabled for Robinhood mainnet or testnet"],
            "message": "V4 pool index refresh is only enabled for Robinhood mainnet or testnet",
        }
    try:
        report = refresh_v4_live(
            database=database_url or "",
            network=network,
            chain_id=chain_id,
            skip_index=False,
            skip_liquidity=False,
            skip_build=False,
            clear_cache=True,
            full_index_scan=full,
        )
    except Exception as exc:
        message = _friendly_oserror("robinhood_v4_sqlite_rebuild", exc)
        return {
            "ok": False,
            "requested": "full" if full else "incremental",
            "source": "robinhood_v4_rebuild",
            "steps": [{"source": "robinhood_v4_sqlite_rebuild", "ok": False, "error": message}],
            "errors": [message],
            "message": "Robinhood V4 SQLite rebuild completed with errors",
        }
    errors = list(report.get("errors") or [])
    return {
        "ok": bool(report.get("ok")) and not errors,
        "requested": "full" if full else "incremental",
        "source": "robinhood_testnet_v4_rebuild" if is_testnet else "robinhood_v4_rebuild",
        "steps": report.get("steps") or [],
        "errors": errors,
        "message": (
            "Robinhood V4 SQLite rebuilt from live evidence"
            if not errors
            else "Robinhood V4 SQLite rebuild completed with errors"
        ),
    }


def _refresh_robinhood_testnet_v4_sources(*, full: bool = True, database_url: str = "") -> dict:
    return _refresh_v4_sources(
        chain_id=ROBINHOOD_TESTNET_CHAIN_ID,
        network=ROBINHOOD_TESTNET_NETWORK,
        full=full,
        database_url=database_url,
    )


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


def _market_observation_page_payload(
    payload: dict,
    *,
    pool_preview_limit: int = 80,
    chain_id: int | None = None,
    protocols: list[str] | None = None,
) -> dict:
    include_all = str(request.args.get("include_all") or "").strip().lower() in {"1", "true", "yes", "all"}
    if not include_all and chain_id is not None:
        cached_page = _cached_surface_payload(
            "dex_market_observation_page",
            chain_id=chain_id,
            protocols=protocols,
            use_static_observation=True,
        )
        if cached_page is not None:
            return cached_page
    if include_all:
        return payload
    trimmed = dict(payload)
    rows = payload.get("chainSpecificObservationPools")
    if isinstance(rows, list):
        trimmed["chainSpecificObservationPools"] = rows[:pool_preview_limit]
    # The market-observation page renders per-anchor YC/ZC candidate sets and
    # compact previews.  Keeping every closed-loop sequence in the API response
    # makes the GET payload hundreds of megabytes for a display-only surface;
    # full sequences remain in the static snapshot consumed by DEX overview.
    for key in ("observationSequences", "closedLoopSequences", "indirectRoutes"):
        if isinstance(payload.get(key), list):
            trimmed[key] = []
            trimmed.pop(f"{key}Total", None)
            trimmed[f"{key}Total"] = len(payload.get(key))
    if isinstance(payload.get("pairs"), list):
        trimmed["pairs"] = []
    if isinstance(payload.get("anchorGroups"), list):
        trimmed["anchorGroups"] = []
    trimmed["marketObservationPreview"] = {
        "chainSpecificObservationPoolsReturned": len(trimmed.get("chainSpecificObservationPools") or []),
        "chainSpecificObservationPoolsTotal": len(payload.get("chainSpecificObservationPools") or []),
        "observationSequencesReturned": 0,
        "observationSequencesTotal": len(payload.get("observationSequences") or []),
        "closedLoopSequencesReturned": 0,
        "closedLoopSequencesTotal": len(payload.get("closedLoopSequences") or []),
        "includeAllParameter": "include_all=1",
    }
    if chain_id is not None:
        _cache_surface_payload(
            "dex_market_observation_page",
            trimmed,
            chain_id=chain_id,
            protocols=protocols,
            use_static_observation=True,
            ttl_seconds=0,
        )
    return trimmed


__all__ = [
    "_attach_chain_flow_processing",
    "_bootstrap_dex_surface_dependencies",
    "_finalize_market_observation_payload",
    "_market_observation_page_payload",
    "_market_observation_static_metadata",
    "_refresh_robinhood_testnet_v4_sources",
    "_refresh_v4_sources",
    "_refresh_state_summary",
    "_submission_review_framework_payload",
]
