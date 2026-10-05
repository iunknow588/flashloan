from __future__ import annotations

import math

from flask import request

from core.chain_flow_dispatcher import ChainFlowDispatchError, dispatch_chain_flow
from core.dex_candidate_response import compact_candidate_payload
from core.dex_response import compact_dex_overview_response
from market.binance_market.dex_strategy_registry import resolve_dex_strategy_key
from core.chain_selection import ChainSelectionError
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

MAX_CANDIDATE_PAGE_SIZE = 1000


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
            " ".join(str(part or "") for part in (row.get("pool"), row.get("routeText"), row.get("counterSymbol"), row.get("counterAddress"), row.get("protocol"), row.get("fee"), row.get("feeLabel"), row.get("dynamicFee"), row.get("specialFee")))
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


def _legacy_paginate_candidate_payload(payload: dict, *, page: int, page_size: int, query: str, quality: str) -> dict:
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


def paginate_candidate_payload(payload: dict, *, page: int, page_size: int, query: str, quality: str) -> dict:
    """Compatibility wrapper around the core-owned candidate paginator."""
    return compact_candidate_payload(payload, page=page, page_size=page_size, query=query, quality=quality)


def candidate_pagination_args() -> tuple[int, int, str, str]:
    page = shared.request_int_arg("page", 1, minimum=1)
    page_size = shared.request_int_arg(
        "page_size",
        20,
        minimum=1,
        maximum=MAX_CANDIDATE_PAGE_SIZE,
    )
    query = request.args.get("q", "").strip().lower()
    quality = request.args.get("quality", "all").strip().lower()
    if quality not in {"all", "with_data", "empty"}:
        quality = "all"
    return page, page_size, query, quality


def request_protocols_arg() -> list[str]:
    return [
        item.strip().lower()
        for item in request.args.get("protocols", "").replace("|", ",").split(",")
        if item.strip()
    ]


def request_dex_protocols_arg() -> list[str]:
    from core.chain_route_selector import chain_dex_arbitrage_protocols

    chain_selection, chain_id = selected_chain_id_from_request()
    supported = set(chain_dex_arbitrage_protocols(chain_id))
    requested_dex = [protocol for protocol in request_protocols_arg() if protocol in DEX_QUOTE_PROTOCOLS]
    protocols = [
        protocol
        for protocol in requested_dex
        if not supported or protocol in supported
    ]
    if requested_dex:
        return protocols or requested_dex
    if isinstance(chain_selection, dict):
        configured = str(chain_selection.get("protocol") or chain_selection.get("protocol_id") or "").strip().lower()
        if configured and configured in DEX_QUOTE_PROTOCOLS and (not supported or configured in supported):
            return [configured]
    return []


def request_dex_strategy_arg() -> str:
    return resolve_dex_strategy_key(request.args.get("dex_strategy", "").strip())


def selected_chain_id_from_request() -> tuple[dict | None, int | None]:
    chain_selection = _request_chain_selection_or_default()
    selected_chain_id = None
    if isinstance(chain_selection, dict) and chain_selection.get("chain_id") is not None:
        selected_chain_id = int(chain_selection["chain_id"])
    return chain_selection, selected_chain_id


def _request_chain_selection_or_default() -> dict | None:
    chain_selection = shared._optional_request_chain_selection(feature="dex_arbitrage")
    if isinstance(chain_selection, dict):
        return chain_selection
    try:
        from core.chain_selection import ChainSelection as _ChainSelection
        try:
            from web.chain_selection import resolve_request_chain_selection
        except ImportError:
            return None
        try:
            database_url = shared.panel_call("database_url_or_none")
        except Exception:
            database_url = None
        try:
            selection = resolve_request_chain_selection(
                database_url=database_url,
                feature="dex_arbitrage",
            )
            if isinstance(selection, _ChainSelection):
                return selection.as_dict()
        except Exception:
            # If the DEX feature has no loadable default, keep the historical
            # behavior of treating unscoped calls as unscoped.
            return None
    except Exception:
        return None
    return None


def submission_networks_from_request(chain_selection: dict | None) -> list[str]:
    networks = shared._request_networks_arg()
    if not networks and isinstance(chain_selection, dict):
        selected_network = str(chain_selection.get("network") or "").strip().lower()
        if selected_network:
            networks = [selected_network]
    return networks


def chain_flow_protocol(protocols: list[str]) -> str:
    return protocols[0] if len(protocols) == 1 else ""


def dex_surface_payload_for_chain(
    *,
    flow_name: str,
    mode: str,
    chain_id: int | None,
    protocols: list[str],
    fallback,
    **kwargs,
) -> dict:
    if chain_id is None:
        payload = fallback()
        payload.setdefault("mode", mode)
        return payload
    try:
        return dispatch_chain_flow(
            chain_id=chain_id,
            flow_name=flow_name,
            protocol=chain_flow_protocol(protocols),
            protocols=protocols,
            **kwargs,
        )
    except ChainFlowDispatchError as exc:
        if exc.code == "chain_flow_missing":
            payload = fallback()
            payload.setdefault("mode", mode)
            return payload
        return {
            "ok": False,
            "mode": mode,
            "chain_id": chain_id,
            "selected_protocols": protocols,
            "code": exc.code,
            "error": exc.message,
        }


def attach_dex_strategy(payload: dict) -> dict:
    mode_by_path = {
        "/api/dex/overview": "dex_overview",
        "/api/dex/closed-loop-groups": "dex_closed_loop_groups",
        "/api/dex/market-observation": "dex_market_observation",
        "/api/dex/market-observation/refresh": "dex_market_observation",
        "/api/dex/quote-routes": "dex_quote_routes",
        "/api/dex/quote-request": "dex_quote_request",
        "/api/dex/submissions": "dex_submission_review",
        "/api/dex-pools/candidates": "dex_pool_candidates",
        "/api/dex-pools/candidates/refresh": "dex_pool_candidates",
    }
    payload.setdefault("mode", mode_by_path.get(request.path, ""))
    payload["dex_strategy"] = request_dex_strategy_arg()
    return payload


def overview_response_payload(payload: dict) -> dict:
    return compact_dex_overview_response(payload)


__all__ = [
    "DEX_QUOTE_PROTOCOLS",
    "candidate_pagination_args",
    "attach_dex_strategy",
    "chain_flow_protocol",
    "dex_surface_payload_for_chain",
    "overview_response_payload",
    "paginate_candidate_payload",
    "request_dex_protocols_arg",
    "request_dex_strategy_arg",
    "request_protocols_arg",
    "selected_chain_id_from_request",
    "submission_networks_from_request",
]
