"""Pure candidate payload filtering and pagination helpers."""

from __future__ import annotations

import math
from typing import Any


def _number_like(value: object) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _has_data(group: dict) -> bool:
    return _number_like(group.get("ycPoolCount")) > 0 or _number_like(group.get("zcPoolCount")) > 0


def _pool_liquid(row: dict) -> bool:
    if not isinstance(row, dict):
        return False
    if row.get("dynamicFee") or row.get("specialFee"):
        return False
    if row.get("usableForObservation") is not None:
        return bool(row.get("usableForObservation"))
    if row.get("hasLiquidity") is not None:
        return bool(row.get("hasLiquidity"))
    try:
        return float(row.get("liquidityIndex") or row.get("liquidity") or 0) > 0
    except (TypeError, ValueError):
        return False


def _group_has_liquid_pools(group: dict) -> bool:
    for bucket in ("ycPools", "zcPools", "sharedPools"):
        if any(_pool_liquid(row) for row in group.get(bucket) or []):
            return True
    return False


def _group_pool_rows(group: dict) -> list[dict]:
    """Return the full pool list for one anchor group.

    V4 stores YC/ZC as one shared set; legacy groups keep separate directional
    buckets.  A page slice is applied per group below while counts stay based
    on the full list so the page status reflects the whole group surface.
    """
    rows: list[dict] = []
    if group.get("ycZcSameSet") or group.get("ycPoolsRef") == "sharedPools" or group.get("zcPoolsRef") == "sharedPools":
        rows = [row for row in group.get("sharedPools") or [] if isinstance(row, dict)]
    else:
        rows = [
            *(row for row in group.get("ycPools") or [] if isinstance(row, dict)),
            *(row for row in group.get("zcPools") or [] if isinstance(row, dict)),
        ]
    seen: set[str] = set()
    unique: list[dict] = []
    for row in rows:
        key = str(
            row.get("pool")
            or row.get("poolId")
            or row.get("pool_id")
            or f"{row.get('counterAddress') or ''}:{row.get('routeText') or ''}"
            or ""
        ).strip().lower()
        if key and key in seen:
            continue
        if key:
            seen.add(key)
        unique.append(row)
    return unique


def _group_with_liquid_pools(group: dict) -> dict:
    """Return a group copy whose candidate pools are limited to liquid rows."""
    copied = dict(group)
    copied["ycPools"] = [row for row in group.get("ycPools") or [] if _pool_liquid(row)]
    copied["zcPools"] = [row for row in group.get("zcPools") or [] if _pool_liquid(row)]
    copied["sharedPools"] = [row for row in group.get("sharedPools") or [] if _pool_liquid(row)]
    return copied


def _page_group_pools(group: dict, *, page: int, page_size: int) -> dict:
    """Paginate a group's pool buckets and attach per-group pool pagination."""
    shared_rows = [row for row in group.get("sharedPools") or [] if isinstance(row, dict)]
    if group.get("ycZcSameSet") or group.get("ycPoolsRef") == "sharedPools" or group.get("zcPoolsRef") == "sharedPools":
        rows = _group_pool_rows(group)
        total = len(rows)
        pages = max(1, math.ceil(total / page_size))
        normalized_page = min(max(1, page), pages)
        start = (normalized_page - 1) * page_size
        selected = rows[start:start + page_size]
        group = dict(group)
        group["sharedPools"] = selected
        if group.get("ycPoolsRef") == "sharedPools":
            group["ycPools"] = []
        if group.get("zcPoolsRef") == "sharedPools":
            group["zcPools"] = []
        pagination = {
            "unit": "pool",
            "page": normalized_page,
            "page_size": page_size,
            "page_count": pages,
            "total_count": total,
            "unfiltered_count": total,
            "offset": start,
            "display_start": 0 if total == 0 else start + 1,
            "display_end": min(start + page_size, total),
            "has_previous": normalized_page > 1,
            "has_next": normalized_page < pages,
        }
        group["poolPagination"] = pagination
        return group

    yc_rows = [row for row in group.get("ycPools") or [] if isinstance(row, dict)]
    zc_rows = [row for row in group.get("zcPools") or [] if isinstance(row, dict)]
    total = len(yc_rows) + len(zc_rows)
    pages = max(1, math.ceil(total / page_size))
    normalized_page = min(max(1, page), pages)
    start = (normalized_page - 1) * page_size
    yc_selected = yc_rows[start:start + page_size]
    zc_selected = zc_rows[start:start + page_size]
    group = dict(group)
    group["ycPools"] = yc_selected
    group["zcPools"] = zc_selected
    pagination = {
        "unit": "pool",
        "page": normalized_page,
        "page_size": page_size,
        "page_count": pages,
        "total_count": total,
        "unfiltered_count": total,
        "offset": start,
        "display_start": 0 if total == 0 else start + 1,
        "display_end": min(start + page_size, total),
        "has_previous": normalized_page > 1,
        "has_next": normalized_page < pages,
    }
    group["poolPagination"] = pagination
    return group


def _matches(group: dict, query: str) -> bool:
    if not query:
        return True
    values = [group.get("anchorSymbol"), *(group.get("anchorAddresses") or [])]
    for key in ("ycTokens", "sharedTokens", "zcTokens"):
        values.extend(
            " ".join(str(item.get(part) or "") for part in ("symbol", "address", "poolCount"))
            for item in group.get(key) or [] if isinstance(item, dict)
        )
    for key in ("ycPools", "zcPools", "sharedPools"):
        values.extend(
            " ".join(str(item.get(part) or "") for part in ("pool", "routeText", "counterSymbol", "counterAddress", "protocol", "fee"))
            for item in group.get(key) or [] if isinstance(item, dict)
        )
    return query in " ".join(str(value or "") for value in values).lower()


def _pair_keys(groups: list[dict]) -> set[str]:
    keys: set[str] = set()
    for group in groups:
        for bucket in ("ycPools", "zcPools", "sharedPools"):
            for row in group.get(bucket) or []:
                if not isinstance(row, dict):
                    continue
                keys.update(str(row.get(key) or "").strip() for key in ("pairSymbol", "pairAddress") if row.get(key))
                symbols = [str(row.get(key) or "").strip() for key in ("token0Symbol", "token1Symbol")]
                addresses = [str(row.get(key) or "").strip() for key in ("token0", "token1")]
                if all(symbols):
                    keys.add(":".join(symbols))
                if all(addresses):
                    keys.add(":".join(addresses))
    return keys


def _list_rows(value: Any) -> list[dict]:
    return [item for item in (value or []) if isinstance(item, dict)]


def _slice_payload_rows(rows: list[dict], *, page: int, page_size: int) -> list[dict]:
    if not rows:
        return []
    page_count = max(1, math.ceil(len(rows) / page_size))
    normalized_page = min(max(1, page), page_count)
    start = (normalized_page - 1) * page_size
    return rows[start : start + page_size]


def _section_pagination(rows: list[dict], *, page: int, page_size: int) -> dict[str, Any]:
    total = len(rows)
    page_count = max(1, math.ceil(total / page_size))
    normalized_page = min(max(1, page), page_count)
    start = (normalized_page - 1) * page_size
    return {
        "page": normalized_page,
        "page_size": page_size,
        "page_count": page_count,
        "total_count": total,
        "offset": start,
        "display_start": 0 if total == 0 else start + 1,
        "display_end": min(start + page_size, total),
        "has_previous": normalized_page > 1,
        "has_next": normalized_page < page_count,
    }


def _page_group_abnormal_sections(
    group: dict,
    *,
    page: int,
    page_size: int,
    abnormal_pool_page: int | None = None,
    abnormal_token_page: int | None = None,
) -> dict:
    copied = dict(group)
    for rows_key, pagination_key, requested_page in (
        ("abnormalPools", "abnormalPoolPagination", abnormal_pool_page),
        ("abnormalTokens", "abnormalTokenPagination", abnormal_token_page),
    ):
        rows = _list_rows(group.get(rows_key))
        copied[rows_key] = _slice_payload_rows(rows, page=requested_page or page, page_size=page_size)
        copied[pagination_key] = _section_pagination(rows, page=requested_page or page, page_size=page_size)
    return copied


def compact_candidate_payload(
    payload: dict,
    *,
    page: int,
    page_size: int,
    query: str = "",
    quality: str = "all",
    abnormal_pool_page: int | None = None,
    abnormal_token_page: int | None = None,
) -> dict:
    groups = [item for item in payload.get("anchorGroups") or [] if isinstance(item, dict)]
    pairs = [item for item in payload.get("pairs") or [] if isinstance(item, dict)]
    use_groups = bool(groups)
    rows = groups if use_groups else pairs
    unfiltered = max((len(_group_pool_rows(item)) for item in groups), default=0) if use_groups else len(rows)
    if use_groups:
        if quality == "with_data":
            rows = [item for item in rows if _has_data(item)]
        elif quality == "empty":
            rows = [item for item in rows if not _has_data(item)]
        elif quality == "with_liquidity":
            rows = [item for item in rows if _group_has_liquid_pools(item)]
            rows = [_group_with_liquid_pools(item) for item in rows]
        rows = [item for item in rows if _matches(item, query)]
    elif query:
        rows = [item for item in rows if query in " ".join(str(value or "") for value in item.values()).lower()]
    full_groups = [dict(item) for item in rows] if use_groups else []
    if use_groups:
        total = max((len(_group_pool_rows(item)) for item in full_groups), default=0)
    else:
        total = len(rows)
    pages = max(1, math.ceil(total / page_size))
    page = min(max(1, page), pages)
    start = (page - 1) * page_size
    selected = [
        _page_group_abnormal_sections(
            _page_group_pools(item, page=page, page_size=page_size),
            page=page,
            page_size=page_size,
            abnormal_pool_page=abnormal_pool_page,
            abnormal_token_page=abnormal_token_page,
        )
        for item in rows
    ] if use_groups else rows[start:start + page_size]
    result = dict(payload)
    if use_groups:
        result["anchorGroups"] = selected
        keys = _pair_keys(full_groups)
        result["pairs"] = [item for item in pairs if any(str(item.get(key) or "").strip() in keys for key in ("symbolPair", "addressPair"))]
    else:
        result["pairs"] = selected
    result["pagination"] = {**(payload.get("pagination") or {}), "candidate_groups": {
        "unit": "pool" if use_groups else "pair", "page": page, "page_size": page_size,
        "page_count": pages, "total_count": total, "unfiltered_count": unfiltered, "offset": start,
        "display_start": 0 if total == 0 else start + 1, "display_end": min(start + page_size, total),
        "has_previous": page > 1, "has_next": page < pages,
    }}
    result["filters"] = {**(payload.get("filters") if isinstance(payload.get("filters"), dict) else {}), "query": query, "quality": quality}
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    result["summary"] = {**summary, "visibleAnchorGroupCount": str(len(result.get("anchorGroups") or [])), "filteredAnchorGroupCount": str(total if use_groups else len(result.get("anchorGroups") or []))}
    return result
