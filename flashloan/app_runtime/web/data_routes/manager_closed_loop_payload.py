"""Shared RuntimeCore adapter and comparison helpers."""

from __future__ import annotations

import sys
from typing import Any

from flask import has_app_context
from core.manager_api_client import ManagerApiClient, ManagerApiClientError, manager_ui_data_source

CANDIDATE_SNAPSHOT_LIMIT = 1000
CANDIDATE_ROW_COMPARISON_SAMPLE_LIMIT = 200
CLOSED_LOOP_SNAPSHOT_LIMIT = 1000
CLOSED_LOOP_ROW_COMPARISON_SAMPLE_LIMIT = 200
CANDIDATE_EXCHANGE_SNAPSHOT_LIMIT = 1000
CANDIDATE_EXCHANGE_ROW_COMPARISON_SAMPLE_LIMIT = 200
RUNTIME_CORE_OVERVIEW_VIEW = "overview"
RUNTIME_CORE_OVERVIEW_LIMIT = CANDIDATE_EXCHANGE_SNAPSHOT_LIMIT
SUBMISSION_SNAPSHOT_LIMIT = 1000
RUNTIME_CORE_MANAGER_VIEWS = {
    "/v1/candidate-edge-pools": "candidate_edge",
    "/v1/candidate-exchange-pools": "candidate_exchange",
    "/v1/closed-loops": "closed_loops",
    "/v1/reviews": "reviews",
    "/v1/broadcasts": "broadcasts",
}
SUBMISSION_FAILURE_CATEGORY_ORDER = ("all", "broadcast", "submission", "code", "rpc", "execution", "quote", "blocked", "deferred", "other")

def _compat_manager_client():
    facade = sys.modules.get("web.data_routes.manager_page_compare")
    return getattr(facade, "ManagerApiClient", ManagerApiClient) if facade is not None else ManagerApiClient

from .manager_page_compare_common import (
    CLOSED_LOOP_ROW_COMPARISON_SAMPLE_LIMIT,
    CLOSED_LOOP_SNAPSHOT_LIMIT,
    ManagerApiClientError,
    _comparison_summary,
    _int_or_none,
    _list_length,
    _manager_read_envelope,
    _manager_read_error,
    _shadow_error,
    _shadow_gate,
)


def closed_loop_manager_page_payload(
    *,
    chain_id: int,
    protocols: list[str],
    include_routes: bool,
    chain_selection: dict[str, Any] | None = None,
    request_id: str | None = None,
) -> dict[str, Any]:
    """Read the manager-owned closed-loop group projection with no legacy fallback."""

    try:
        envelope = _manager_read_envelope(
            "/v1/closed-loops",
            query={"chainId": int(chain_id), "limit": CLOSED_LOOP_SNAPSHOT_LIMIT},
            request_id=request_id,
        )
    except ManagerApiClientError as exc:
        return _manager_read_error(
            exc.code,
            exc.message,
            upstream_status=exc.status,
        )
    data = envelope.get("data")
    if not isinstance(data, dict):
        return _manager_read_error(
            "MANAGER_API_INVALID_RESPONSE",
            "closed-loop manager response must contain an object data field",
        )
    status = data.get("status") if isinstance(data.get("status"), dict) else None
    if status is None or not isinstance(status.get("enabled"), bool):
        return _manager_read_error(
            "MANAGER_API_INVALID_RESPONSE",
            "closed-loop manager response field status.enabled must be a boolean",
        )
    if _int_or_none(status.get("chainId")) != int(chain_id):
        return _manager_read_error(
            "MANAGER_CLOSED_LOOP_SCOPE_MISMATCH",
            "closed-loop manager response chainId does not match the requested chain",
        )
    loops = data.get("loops")
    hops = data.get("hops")
    if not isinstance(loops, list) or not isinstance(hops, list):
        return _manager_read_error(
            "MANAGER_API_INVALID_RESPONSE",
            "closed-loop manager response fields loops and hops must be arrays",
        )

    hops_by_loop: dict[str, list[dict[str, Any]]] = {}
    for hop in hops:
        if not isinstance(hop, dict):
            return _manager_read_error(
                "MANAGER_API_INVALID_RESPONSE",
                "closed-loop manager response hops must contain objects",
            )
        loop_key = str(hop.get("loopKey") or "").strip()
        if loop_key:
            hops_by_loop.setdefault(loop_key, []).append(hop)

    groups: dict[str, dict[str, Any]] = {}
    loop_snapshot_count = 0
    for row in loops:
        if not isinstance(row, dict):
            return _manager_read_error(
                "MANAGER_API_INVALID_RESPONSE",
                "closed-loop manager response loops must contain objects",
            )
        anchor_key = str(row.get("anchorTokenKeyId") or "").strip()
        anchor_address = str(row.get("anchorAddress") or "").strip()
        anchor_symbol = str(row.get("anchorSymbol") or "").strip().upper()
        if not anchor_key or not anchor_address or not anchor_symbol:
            return _manager_read_error(
                "MANAGER_API_INVALID_RESPONSE",
                "closed-loop rows must include anchorTokenKeyId, anchorAddress and anchorSymbol",
            )
        group = groups.get(anchor_key)
        if group is None:
            group = {
                "flashLoanToken": {
                    "symbol": anchor_symbol,
                    "address": anchor_address,
                },
                "startToken": {
                    "symbol": anchor_symbol,
                    "address": anchor_address,
                },
                "endToken": {
                    "symbol": anchor_symbol,
                    "address": anchor_address,
                },
                # M5 only admits loops for manager-approved core anchors. The
                # actual flash source remains outside this topology snapshot.
                "borrowable": True,
                "borrowableBasis": "closed_loop_manager_anchor",
                "routeCount": 0,
                "poolDependencyCount": 0,
                "routes": [],
                "poolDependencies": [],
                "_poolIds": set(),
            }
            groups[anchor_key] = group
        elif str(group["flashLoanToken"]["address"]).lower() != anchor_address.lower():
            return _manager_read_error(
                "MANAGER_API_INVALID_RESPONSE",
                f"closed-loop anchor {anchor_key} has conflicting addresses",
            )

        group["routeCount"] += 1
        loop_snapshot_count += 1
        pool_ids = [
            str(value).strip()
            for value in (row.get("poolIds") if isinstance(row.get("poolIds"), list) else [])
            if str(value).strip()
        ]
        group["_poolIds"].update(pool_ids)
        if include_routes:
            group["routes"].append(
                _closed_loop_manager_route(
                    row,
                    hops_by_loop.get(str(row.get("loopKey") or "").strip(), []),
                )
            )

    result: list[dict[str, Any]] = []
    total_pool_ids: set[str] = set()
    for group in groups.values():
        pool_ids = sorted(group.pop("_poolIds"))
        total_pool_ids.update(pool_ids)
        group["poolDependencyCount"] = len(pool_ids)
        group["poolDependencies"] = [
            {
                "pool": pool_id,
                "poolId": pool_id,
                "pairSymbol": "",
                "direction": "",
                "routeSymbols": [],
                "routeAddresses": [],
                "protocol": "",
                "fee": "",
                "tokenIn": "",
                "tokenOut": "",
                "anchorSymbol": group["flashLoanToken"]["symbol"],
                "counterSymbol": "",
                "pairAddress": "",
            }
            for pool_id in pool_ids
        ]
        if not include_routes:
            group.pop("routes", None)
        result.append(group)
    result.sort(
        key=lambda group: (
            0 if group["borrowable"] else 1,
            group["flashLoanToken"]["symbol"],
            group["flashLoanToken"]["address"].lower(),
        )
    )

    manager_loop_count = _int_or_none(status.get("loopCount"))
    manager_anchor_count = _int_or_none(status.get("anchorCount"))
    return {
        "ok": True,
        "source": "manager",
        "dataSource": "manager",
        "mode": "dex_closed_loop_groups",
        "chainId": int(chain_id),
        "chain_id": int(chain_id),
        "network": status.get("network"),
        "requested_protocols": list(protocols),
        "selected_protocols": list(protocols),
        "chain_selection": dict(chain_selection or {}),
        "includeRoutes": bool(include_routes),
        "summary": {
            "borrowableSymbolCount": len(groups),
            "groupCount": len(groups),
            "closedLoopRouteCount": loop_snapshot_count,
            "poolDependencyCount": len(total_pool_ids),
            "managerAnchorCount": manager_anchor_count,
            "managerLoopCount": manager_loop_count,
            "snapshotLimited": (
                manager_loop_count is not None and manager_loop_count > loop_snapshot_count
            ),
            "borrowableBasis": "closed_loop_manager_anchor",
            "usdDepthRequired": False,
        },
        "groups": result,
        "snapshotLimit": CLOSED_LOOP_SNAPSHOT_LIMIT,
        "managerSnapshotCounts": {
            "loopSnapshotCount": loop_snapshot_count,
            "hopSnapshotCount": len(hops),
            "groupSnapshotCount": len(groups),
        },
        "managerStatus": status,
        "versions": {
            "closedLoopVersion": status.get("closedLoopVersion"),
            "closedLoopChecksum": status.get("materializedChecksum"),
            "candidateEdgeVersion": status.get("candidateEdgeVersion"),
            "candidateEdgeChecksum": status.get("candidateEdgeChecksum"),
            "observationVersion": status.get("observationVersion"),
            "observationChecksum": status.get("observationChecksum"),
            "poolScopeChecksum": status.get("poolScopeChecksum"),
            "consumersNeedRebuild": bool(status.get("consumersNeedRebuild")),
        },
        "managerEnvelopeVersion": envelope.get("version"),
    }


def _closed_loop_manager_route(
    loop: dict[str, Any],
    hops: list[dict[str, Any]],
) -> dict[str, Any]:
    path_symbols = [
        str(value).strip().upper()
        for value in (loop.get("pathSymbols") if isinstance(loop.get("pathSymbols"), list) else [])
        if str(value).strip()
    ]
    path_addresses = [
        str(value).strip()
        for value in (
            loop.get("pathAddresses") if isinstance(loop.get("pathAddresses"), list) else []
        )
        if str(value).strip()
    ]
    route_pools: list[dict[str, Any]] = []
    for hop in hops:
        hop_index = _int_or_none(hop.get("hopIndex"))
        input_symbol = (
            path_symbols[hop_index]
            if hop_index is not None and hop_index < len(path_symbols)
            else ""
        )
        output_symbol = (
            path_symbols[hop_index + 1]
            if hop_index is not None and hop_index + 1 < len(path_symbols)
            else ""
        )
        input_address = (
            path_addresses[hop_index]
            if hop_index is not None and hop_index < len(path_addresses)
            else str(hop.get("inputAddress") or "")
        )
        output_address = (
            path_addresses[hop_index + 1]
            if hop_index is not None and hop_index + 1 < len(path_addresses)
            else str(hop.get("outputAddress") or "")
        )
        route_pools.append(
            {
                "pool": str(hop.get("poolId") or ""),
                "poolId": str(hop.get("poolId") or ""),
                "pairSymbol": " -> ".join(
                    value for value in (input_symbol, output_symbol) if value
                ),
                "direction": str(hop.get("hopName") or ""),
                "routeSymbols": [value for value in (input_symbol, output_symbol) if value],
                "routeAddresses": [
                    value for value in (input_address, output_address) if value
                ],
                "protocol": "uniswap_v4",
                "fee": "",
                "tokenIn": str(hop.get("inputTokenKeyId") or ""),
                "tokenOut": str(hop.get("outputTokenKeyId") or ""),
                "anchorSymbol": str(loop.get("anchorSymbol") or "").upper(),
                "counterSymbol": "",
                "pairAddress": "",
            }
        )
    return {
        "routeKey": str(loop.get("routeKeyId") or loop.get("loopKey") or ""),
        "kind": "closed_loop",
        "source": str(loop.get("source") or "closed_loop_manager"),
        "anchorSymbol": str(loop.get("anchorSymbol") or "").upper(),
        "counterSymbol": "",
        "bridgeSymbol": "",
        "pathText": str(loop.get("pathText") or ""),
        "pathSymbols": path_symbols,
        "pathAddresses": path_addresses,
        "routeTokens": [
            {
                "symbol": symbol,
                "address": path_addresses[index] if index < len(path_addresses) else "",
            }
            for index, symbol in enumerate(path_symbols)
        ],
        "pairSymbols": [
            pool["pairSymbol"] for pool in route_pools if pool.get("pairSymbol")
        ],
        "poolIds": [
            str(value)
            for value in (loop.get("poolIds") if isinstance(loop.get("poolIds"), list) else [])
            if str(value)
        ],
        "routePools": route_pools,
        "observationRef": "",
    }


def closed_loop_manager_shadow_payload(
    legacy_payload: dict[str, Any],
    *,
    chain_id: int,
    request_id: str | None = None,
) -> dict[str, Any]:
    """Read the M5 shadow and report bounded diagnostics without changing legacy fields."""

    if (gate := _shadow_gate("closed-loop")) is not None:
        return gate
    try:
        envelope = _manager_read_envelope(
            "/v1/closed-loops",
            query={"chainId": int(chain_id), "limit": CLOSED_LOOP_SNAPSHOT_LIMIT},
            request_id=request_id,
        )
    except ManagerApiClientError as exc:
        return _shadow_error(
            exc.code,
            exc.message,
            data_source="manager",
            upstream_status=exc.status,
        )
    data = envelope.get("data")
    if not isinstance(data, dict):
        return _shadow_error(
            "MANAGER_API_INVALID_RESPONSE",
            "closed-loop manager response must contain an object data field",
            data_source="manager",
        )
    status = data.get("status") if isinstance(data.get("status"), dict) else {}
    manager_status_counts = {
        "anchorCount": _int_or_none(status.get("anchorCount")),
        "loopCount": _int_or_none(status.get("loopCount")),
        "hopCount": _int_or_none(status.get("hopCount")),
        "optimizationCount": _int_or_none(status.get("optimizationCount")),
        "skippedTokenCount": _int_or_none(status.get("skippedTokenCount")),
    }
    manager_snapshot_counts = {
        "loopSnapshotCount": _list_length(data.get("loops")),
        "hopSnapshotCount": _list_length(data.get("hops")),
        "optimizationSnapshotCount": _list_length(data.get("optimizations")),
        "runtimeStateSnapshotCount": _list_length(data.get("runtimeStates")),
        "skippedTokenSnapshotCount": _list_length(data.get("skippedTokens")),
    }
    legacy_summary = (
        legacy_payload.get("summary")
        if isinstance(legacy_payload.get("summary"), dict)
        else {}
    )
    return {
        "ok": True,
        "source": "manager",
        "enabled": True,
        "legacyUnaffected": True,
        "managerEnvelopeVersion": envelope.get("version"),
        "closedLoopVersion": status.get("closedLoopVersion"),
        "closedLoopChecksum": status.get("materializedChecksum"),
        "candidateEdgeVersion": status.get("candidateEdgeVersion"),
        "candidateEdgeChecksum": status.get("candidateEdgeChecksum"),
        "observationVersion": status.get("observationVersion"),
        "observationChecksum": status.get("observationChecksum"),
        "poolScopeChecksum": status.get("poolScopeChecksum"),
        "consumersNeedRebuild": bool(status.get("consumersNeedRebuild")),
        "snapshotLimit": CLOSED_LOOP_SNAPSHOT_LIMIT,
        "rowComparisonSampleLimit": CLOSED_LOOP_ROW_COMPARISON_SAMPLE_LIMIT,
        "snapshotCountSemantics": {
            "statusCounts": "full_materialized_scope",
            "rowCounts": "bounded_snapshot_rows",
            "detail": (
                "managerStatusCounts are full materialized scope counts; "
                "managerSnapshotCounts values derived from row arrays are limited to "
                f"the requested snapshot limit ({CLOSED_LOOP_SNAPSHOT_LIMIT})."
            ),
        },
        "managerStatus": status,
        "legacyPageCounts": {
            "borrowableSymbolCount": _int_or_none(legacy_summary.get("borrowableSymbolCount")),
            "groupCount": _int_or_none(legacy_summary.get("groupCount")),
            "closedLoopRouteCount": _int_or_none(legacy_summary.get("closedLoopRouteCount")),
        },
        "managerStatusCounts": manager_status_counts,
        "managerSnapshotCounts": manager_snapshot_counts,
        "rowComparison": _closed_loop_row_comparison(
            chain_id=chain_id,
            request_id=request_id,
        ),
        "comparisonReadyForFieldSwitch": False,
        "comparisonReason": "The legacy group/route projection is not field-equivalent to manager loops/hops; full production-copy dual-read is required before cutover.",
    }


def _closed_loop_row_comparison(
    *,
    chain_id: int,
    request_id: str | None,
) -> dict[str, Any]:
    legacy_market_id = f"dex-arbitrage-{int(chain_id)}-uniswap-v4"
    try:
        envelope = ManagerApiClient.from_env().get_json(
            "/v1/closed-loops/compare",
            query={
                "chainId": int(chain_id),
                "sampleLimit": CLOSED_LOOP_ROW_COMPARISON_SAMPLE_LIMIT,
                "legacyMarketId": legacy_market_id,
            },
            request_id=request_id,
        )
    except ManagerApiClientError as exc:
        return {
            "ok": False,
            "source": "manager",
            "sampleLimit": CLOSED_LOOP_ROW_COMPARISON_SAMPLE_LIMIT,
            "legacyMarketId": legacy_market_id,
            "code": exc.code,
            "detail": exc.message,
            "upstreamStatus": exc.status,
            "readyForCutover": False,
        }
    data = envelope.get("data")
    if not isinstance(data, dict):
        return {
            "ok": False,
            "source": "manager",
            "sampleLimit": CLOSED_LOOP_ROW_COMPARISON_SAMPLE_LIMIT,
            "legacyMarketId": legacy_market_id,
            "code": "MANAGER_API_INVALID_RESPONSE",
            "detail": "closed-loop compare response must contain an object data field",
            "readyForCutover": False,
        }
    return {
        "ok": True,
        "source": "manager",
        "sampleLimit": CLOSED_LOOP_ROW_COMPARISON_SAMPLE_LIMIT,
        "legacyMarketId": legacy_market_id,
        "managerEnvelopeVersion": envelope.get("version"),
        "managerChecksum": data.get("managerChecksum"),
        "legacyTablesAvailable": bool(data.get("legacyTablesAvailable")),
        "readyForCutover": bool(data.get("readyForCutover")),
        "differencesTruncated": bool(data.get("differencesTruncated")),
        "causeCounts": data.get("causeCounts") if isinstance(data.get("causeCounts"), dict) else {},
        "differenceCounts": {
            key: _comparison_summary(data.get(key))
            for key in ("loops", "hops")
        },
        "sampleReadyForCutover": bool(data.get("readyForCutover"))
        and not bool(data.get("differencesTruncated")),
    }
