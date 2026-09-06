"""Pure candidate payload filtering and pagination helpers."""

from __future__ import annotations

import math


def _number_like(value: object) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _has_data(group: dict) -> bool:
    return _number_like(group.get("ycPoolCount")) > 0 or _number_like(group.get("zcPoolCount")) > 0


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


def compact_candidate_payload(payload: dict, *, page: int, page_size: int, query: str = "", quality: str = "all") -> dict:
    groups = [item for item in payload.get("anchorGroups") or [] if isinstance(item, dict)]
    pairs = [item for item in payload.get("pairs") or [] if isinstance(item, dict)]
    use_groups = bool(groups)
    rows = groups if use_groups else pairs
    unfiltered = len(rows)
    if use_groups:
        if quality == "with_data":
            rows = [item for item in rows if _has_data(item)]
        elif quality == "empty":
            rows = [item for item in rows if not _has_data(item)]
        rows = [item for item in rows if _matches(item, query)]
    elif query:
        rows = [item for item in rows if query in " ".join(str(value or "") for value in item.values()).lower()]
    total = len(rows)
    pages = max(1, math.ceil(total / page_size))
    page = min(max(1, page), pages)
    start = (page - 1) * page_size
    selected = rows[start:start + page_size]
    result = dict(payload)
    if use_groups:
        result["anchorGroups"] = selected
        keys = _pair_keys(selected)
        result["pairs"] = [item for item in pairs if any(str(item.get(key) or "").strip() in keys for key in ("symbolPair", "addressPair"))]
    else:
        result["pairs"] = selected
    result["pagination"] = {**(payload.get("pagination") or {}), "candidate_groups": {
        "unit": "anchor_group" if use_groups else "pair", "page": page, "page_size": page_size,
        "page_count": pages, "total_count": total, "unfiltered_count": unfiltered, "offset": start,
        "display_start": 0 if total == 0 else start + 1, "display_end": min(start + page_size, total),
        "has_previous": page > 1, "has_next": page < pages,
    }}
    result["filters"] = {**(payload.get("filters") if isinstance(payload.get("filters"), dict) else {}), "query": query, "quality": quality}
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    result["summary"] = {**summary, "visibleAnchorGroupCount": str(len(result.get("anchorGroups") or [])), "filteredAnchorGroupCount": str(total if use_groups else len(result.get("anchorGroups") or []))}
    return result
