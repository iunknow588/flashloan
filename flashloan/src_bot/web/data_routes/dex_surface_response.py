from __future__ import annotations

import math

from flask import request

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


def paginate_candidate_payload(payload: dict, *, page: int, page_size: int, query: str, quality: str) -> dict:
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


def candidate_pagination_args() -> tuple[int, int, str, str]:
    page = shared.request_int_arg("page", 1, minimum=1)
    page_size = shared.request_int_arg("page_size", 1, minimum=1, maximum=20)
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
    return [protocol for protocol in request_protocols_arg() if protocol in DEX_QUOTE_PROTOCOLS]


def selected_chain_id_from_request() -> tuple[dict | None, int | None]:
    chain_selection = shared._optional_request_chain_selection()
    selected_chain_id = None
    if isinstance(chain_selection, dict) and chain_selection.get("chain_id") is not None:
        selected_chain_id = int(chain_selection["chain_id"])
    return chain_selection, selected_chain_id


def submission_networks_from_request(chain_selection: dict | None) -> list[str]:
    networks = shared._request_networks_arg()
    if not networks and isinstance(chain_selection, dict):
        selected_network = str(chain_selection.get("network") or "").strip().lower()
        if selected_network:
            networks = [selected_network]
    return networks


__all__ = [
    "DEX_QUOTE_PROTOCOLS",
    "candidate_pagination_args",
    "paginate_candidate_payload",
    "request_dex_protocols_arg",
    "request_protocols_arg",
    "selected_chain_id_from_request",
    "submission_networks_from_request",
]
