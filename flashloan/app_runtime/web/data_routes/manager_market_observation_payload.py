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
    CANDIDATE_EXCHANGE_ROW_COMPARISON_SAMPLE_LIMIT,
    CANDIDATE_EXCHANGE_SNAPSHOT_LIMIT,
    ManagerApiClientError,
    _comparison_summary,
    _float_or_none,
    _int_or_none,
    _list_length,
    _manager_read_envelope,
    _manager_read_error,
    _mapping_value,
    _shadow_error,
    _shadow_gate,
)
from .manager_candidate_payload import _candidate_key_address, _candidate_token_metadata_map


def market_observation_manager_page_payload(
    *,
    chain_id: int,
    protocols: list[str],
    include_all: bool = False,
    chain_selection: dict[str, Any] | None = None,
    request_id: str | None = None,
) -> dict[str, Any]:
    """Read the manager-owned market-observation projection with no legacy fallback."""

    try:
        envelope = _manager_read_envelope(
            "/v1/candidate-exchange-pools",
            query={"chainId": int(chain_id), "limit": CANDIDATE_EXCHANGE_SNAPSHOT_LIMIT},
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
            "candidate-exchange manager response must contain an object data field",
        )
    status = data.get("status") if isinstance(data.get("status"), dict) else None
    if status is None or not isinstance(status.get("enabled"), bool):
        return _manager_read_error(
            "MANAGER_API_INVALID_RESPONSE",
            "candidate-exchange manager response field status.enabled must be a boolean",
        )
    if _int_or_none(status.get("chainId")) != int(chain_id):
        return _manager_read_error(
            "MANAGER_OBSERVATION_SCOPE_MISMATCH",
            "candidate-exchange manager response chainId does not match the requested chain",
        )
    for field in (
        "tokens",
        "yzPairs",
        "pools",
        "abnormalYzPairs",
        "abnormalPools",
        "liquiditySnapshots",
    ):
        if not isinstance(data.get(field), list):
            return _manager_read_error(
                "MANAGER_API_INVALID_RESPONSE",
                f"candidate-exchange manager response field {field} must be an array",
            )

    token_map = _candidate_token_metadata_map(data)
    token_rows: list[dict[str, Any]] = []
    seen_token_keys: set[str] = set()
    for row in data.get("tokens") or []:
        if not isinstance(row, dict):
            continue
        token_key_id = str(row.get("tokenKeyId") or "").strip()
        if not token_key_id or token_key_id in seen_token_keys:
            continue
        seen_token_keys.add(token_key_id)
        token_meta = _exchange_token_meta(token_key_id, token_map)
        token_rows.append(
            _exchange_token_dto(
                token_key_id,
                token_meta,
                admission_status=row.get("admissionStatus"),
            )
        )

    active_pool_map: dict[str, dict[str, Any]] = {}
    for row in data.get("pools") or []:
        if not isinstance(row, dict):
            continue
        anchor_id = str(row.get("anchorTokenKeyId") or "").strip()
        anchor_meta = _exchange_token_meta(anchor_id, token_map)
        pool = _exchange_pool_dto(
            row,
            token_map=token_map,
            anchor_meta=anchor_meta,
            abnormal=False,
        )
        pool_id = str(pool.get("poolId") or "").lower()
        if pool_id:
            active_pool_map[pool_id] = pool

    abnormal_pool_map: dict[str, dict[str, Any]] = {}
    abnormal_pool_rows: list[dict[str, Any]] = []
    for row in data.get("abnormalPools") or []:
        if not isinstance(row, dict):
            continue
        anchor_id = str(row.get("anchorTokenKeyId") or "").strip()
        anchor_meta = _exchange_token_meta(anchor_id, token_map)
        pool = _exchange_pool_dto(
            row,
            token_map=token_map,
            anchor_meta=anchor_meta,
            abnormal=True,
        )
        abnormal_pool_rows.append(pool)
        pool_id = str(pool.get("poolId") or "").lower()
        if pool_id:
            abnormal_pool_map[pool_id] = pool

    pair_rows = [
        _exchange_pair_dto(
            row,
            token_map=token_map,
            pool_map=active_pool_map,
            abnormal=False,
        )
        for row in data.get("yzPairs") or []
        if isinstance(row, dict)
    ]
    abnormal_pair_rows = [
        _exchange_pair_dto(
            row,
            token_map=token_map,
            pool_map=abnormal_pool_map,
            abnormal=True,
        )
        for row in data.get("abnormalYzPairs") or []
        if isinstance(row, dict)
    ]
    groups = _exchange_observation_groups(
        pair_rows=pair_rows,
        abnormal_pair_rows=abnormal_pair_rows,
        abnormal_pool_rows=abnormal_pool_rows,
        token_map=token_map,
    )
    observation_sequences = [
        sequence
        for group in groups
        for sequence in group.get("observationSequences") or []
        if isinstance(sequence, dict)
    ]
    anchor_symbols = sorted(
        {
            str(group.get("anchorSymbol") or "").strip().upper()
            for group in groups
            if str(group.get("anchorSymbol") or "").strip()
        }
    )
    direction_count = _int_or_none(status.get("directionCount"))
    pool_count = _int_or_none(status.get("poolCount"))
    associated_token_count = len(
        {
            value
            for pair in pair_rows
            for side in ("token0", "token1")
            if isinstance(pair.get(side), dict)
            for value in (str(pair[side].get("tokenKeyId") or "").strip(),)
            if value
        }
    )
    unmatched_pair_count = sum(
        1 for pair in pair_rows if not bool(pair.get("hasDirectSwapPool"))
    )
    snapshot_limited = bool(
        (direction_count is not None and direction_count > len(pair_rows))
        or (pool_count is not None and pool_count > len(active_pool_map))
    )
    summary = {
        "anchorBorrowSymbols": anchor_symbols,
        "observationAnchorSymbols": anchor_symbols,
        "observationAnchorCount": len(anchor_symbols),
        "candidateObservationGroupCount": len(groups),
        "directPairCount": sum(
            1 for pair in pair_rows if pair.get("hasDirectSwapPool")
        ),
        "observationSequenceCount": len(observation_sequences),
        "chainSpecificObservationSequenceCount": direction_count,
        "candidateObservationSequenceCount": len(observation_sequences),
        "closedLoopSequenceCount": 0,
        "closedLoopRouteCount": 0,
        "fundAnchorPairObservationRouteCount": 0,
        "fundAnchorPairObservationAssociatedPairCount": len(pair_rows),
        "fundAnchorPairObservationAssociatedObservationPoolCount": len(active_pool_map),
        "fundAnchorPairObservationAssociatedObservationTokenCount": associated_token_count,
        "nonEthPairFirstAssociatedPairCount": len(pair_rows),
        "nonEthPairFirstAssociatedObservationPoolCount": len(active_pool_map),
        "nonEthPairFirstAssociatedObservationTokenCount": associated_token_count,
        "unmatchedPairCount": unmatched_pair_count,
        "managerDirectionCount": direction_count,
        "managerPoolCount": pool_count,
        "managerAbnormalDirectionCount": _int_or_none(
            status.get("abnormalDirectionCount")
        ),
        "managerAbnormalPoolCount": _int_or_none(status.get("abnormalPoolCount")),
        "snapshotLimited": snapshot_limited,
        "snapshotLimit": CANDIDATE_EXCHANGE_SNAPSHOT_LIMIT,
        "usdDepthRequired": False,
        "dataSource": "manager",
    }
    static_table = {
        "exists": True,
        "source": "manager",
        "network": status.get("network"),
        "chain_id": status.get("chainId"),
        "protocols": list(protocols),
        "path": "manager://candidate-exchange-pools",
        "summary": summary,
        "list_counts": {
            "chainSpecificObservationPairs": len(pair_rows),
            "chainSpecificObservationPools": len(active_pool_map),
            "chainSpecificObservationTokens": len(token_rows),
            "abnormalPools": len(abnormal_pool_rows),
        },
    }
    return {
        "ok": True,
        "source": "manager",
        "dataSource": "manager",
        "mode": "dex_market_observation",
        "chainId": int(chain_id),
        "chain_id": int(chain_id),
        "network": status.get("network"),
        "selected_protocols": list(protocols),
        "requested_protocols": list(protocols),
        "chain_selection": dict(chain_selection or {}),
        "includeAll": bool(include_all),
        "tableSource": "candidate_exchange_manager",
        "referencedTable": "manager_market_observation",
        "marketObservationSource": "manager",
        "marketObservationSourceStage": "manager",
        "processing": {
            "flow_name": "market_observation",
            "network": status.get("network"),
            "handler": "manager_api:/v1/candidate-exchange-pools",
        },
        "marketObservationStaticTable": static_table,
        "borrowableSymbols": anchor_symbols,
        "observationAnchorSymbols": anchor_symbols,
        "anchorGroups": groups,
        "candidateObservationGroups": groups,
        "pairs": pair_rows,
        "bridgePairs": pair_rows,
        "observationSequences": observation_sequences,
        "closedLoopSequences": [],
        "closedLoopRoutes": [],
        "chainSpecificObservationPairs": pair_rows,
        "chainSpecificObservationPools": list(active_pool_map.values()),
        "chainSpecificObservationTokens": token_rows,
        "abnormalObservationPairs": abnormal_pair_rows,
        "unmatchedPools": abnormal_pool_rows,
        "marketObservationPreview": {
            "chainSpecificObservationPoolsReturned": len(active_pool_map),
            "chainSpecificObservationPoolsTotal": pool_count,
            "observationSequencesReturned": len(observation_sequences),
            "observationSequencesTotal": direction_count,
            "closedLoopSequencesReturned": 0,
            "closedLoopSequencesTotal": 0,
            "snapshotLimited": snapshot_limited,
            "snapshotLimit": CANDIDATE_EXCHANGE_SNAPSHOT_LIMIT,
            "includeAllParameter": "include_all=1",
        },
        "summary": summary,
        "managerStatus": status,
        "managerSnapshotCounts": {
            "tokenSnapshotCount": len(token_rows),
            "yzPairSnapshotCount": len(pair_rows),
            "poolSnapshotCount": len(active_pool_map),
            "abnormalYzPairSnapshotCount": len(abnormal_pair_rows),
            "abnormalPoolSnapshotCount": len(abnormal_pool_rows),
            "liquiditySnapshotCount": len(data.get("liquiditySnapshots") or []),
        },
        "versions": {
            "observationVersion": status.get("observationVersion"),
            "observationChecksum": status.get("materializedChecksum"),
            "candidateEdgeVersion": status.get("candidateEdgeVersion"),
            "candidateEdgeChecksum": status.get("candidateEdgeChecksum"),
            "poolScopeChecksum": status.get("poolScopeChecksum"),
            "libraryVersions": _mapping_value(status.get("libraryVersions")),
            "libraryChecksums": _mapping_value(status.get("libraryChecksums")),
        },
        "managerEnvelopeVersion": envelope.get("version"),
    }


def _exchange_token_meta(
    token_key_id: str,
    token_map: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    key = str(token_key_id or "").strip()
    meta = token_map.get(key)
    if meta is not None:
        return meta
    address = _candidate_key_address(key)
    symbol = f"UNRESOLVED:{address[:10]}" if address else "UNRESOLVED:unknown"
    return {
        "address": address,
        "symbol": symbol,
        "name": "",
        "libraryKey": "",
        "resolved": False,
    }


def _exchange_token_dto(
    token_key_id: str,
    meta: dict[str, Any],
    *,
    admission_status: object = None,
    pool_count: object = None,
) -> dict[str, Any]:
    symbol = str(meta.get("symbol") or "").strip()
    name = str(meta.get("name") or "").strip()
    address = str(meta.get("address") or _candidate_key_address(token_key_id))
    return {
        "tokenKeyId": str(token_key_id or ""),
        "address": address,
        "symbol": symbol or "UNRESOLVED:unknown",
        "symbolRaw": symbol,
        "symbolResolved": bool(meta.get("resolved")) and not symbol.startswith("UNRESOLVED:"),
        "name": name,
        "nameResolved": bool(name),
        "nameMissing": not bool(name),
        "nameFallbackSymbol": symbol,
        "libraryKey": meta.get("libraryKey") or "",
        "admissionStatus": admission_status,
        "poolCount": pool_count,
    }


def _exchange_pool_dto(
    row: dict[str, Any],
    *,
    token_map: dict[str, dict[str, Any]],
    anchor_meta: dict[str, Any],
    abnormal: bool,
) -> dict[str, Any]:
    pool_id = str(row.get("poolId") or "").strip()
    token0_key_id = str(row.get("token0KeyId") or "").strip()
    token1_key_id = str(row.get("token1KeyId") or "").strip()
    token0_meta = _exchange_token_meta(token0_key_id, token_map)
    token1_meta = _exchange_token_meta(token1_key_id, token_map)
    token0_address = str(row.get("token0") or token0_meta.get("address") or "")
    token1_address = str(row.get("token1") or token1_meta.get("address") or "")
    token0_symbol = str(token0_meta.get("symbol") or "UNRESOLVED:token0")
    token1_symbol = str(token1_meta.get("symbol") or "UNRESOLVED:token1")
    anchor_symbol = str(anchor_meta.get("symbol") or "UNRESOLVED:anchor")
    fee = _int_or_none(row.get("fee"))
    dynamic_fee = bool(fee is not None and (fee & 0x800000))
    hooks = str(row.get("hooks") or "").strip().lower()
    has_nontrivial_hook = bool(hooks and hooks != "0x" + "0" * 40)
    liquidity_index = _float_or_none(row.get("liquidityIndex"))
    liquidity = _float_or_none(row.get("liquidity"))
    has_liquidity = bool(
        (liquidity_index is not None and liquidity_index > 0)
        or (liquidity is not None and liquidity > 0)
    )
    usable = not abnormal and not dynamic_fee and not has_nontrivial_hook
    depth_status = str(row.get("depthStatus") or "").strip() or "unknown"
    selection_rank = _int_or_none(row.get("selectionRank"))
    reasons = [
        str(value)
        for value in (row.get("reasons") if isinstance(row.get("reasons"), list) else [])
        if str(value).strip()
    ]
    return {
        "pool": pool_id,
        "poolId": pool_id,
        "anchorSymbol": anchor_symbol,
        "anchorTokenKeyId": str(row.get("anchorTokenKeyId") or ""),
        "poolManager": str(row.get("poolManager") or ""),
        "poolKeyJson": str(row.get("poolKeyJson") or ""),
        "token0": token0_address,
        "token1": token1_address,
        "token0KeyId": token0_key_id,
        "token1KeyId": token1_key_id,
        "token0Symbol": token0_symbol,
        "token1Symbol": token1_symbol,
        "token0Name": token0_meta.get("name") or "",
        "token1Name": token1_meta.get("name") or "",
        "token0SymbolResolved": bool(token0_meta.get("resolved")),
        "token1SymbolResolved": bool(token1_meta.get("resolved")),
        "token0SymbolRaw": token0_symbol,
        "token1SymbolRaw": token1_symbol,
        "token0NameResolved": bool(token0_meta.get("name")),
        "token1NameResolved": bool(token1_meta.get("name")),
        "token0NameMissing": not bool(token0_meta.get("name")),
        "token1NameMissing": not bool(token1_meta.get("name")),
        "token0NameFallbackSymbol": token0_symbol,
        "token1NameFallbackSymbol": token1_symbol,
        "symbolPair": f"{token0_symbol}:{token1_symbol}",
        "addressPair": f"{token0_address}:{token1_address}",
        "fee": fee,
        "feeLabel": "dynamic" if dynamic_fee else str(fee) if fee is not None else "-",
        "dynamicFee": dynamic_fee,
        "specialFee": dynamic_fee or has_nontrivial_hook,
        "tickSpacing": _int_or_none(row.get("tickSpacing")),
        "hooks": hooks,
        "sqrtPriceX96": row.get("sqrtPriceX96"),
        "tick": _int_or_none(row.get("tick")),
        "liquidity": row.get("liquidity"),
        "liquidityIndex": liquidity_index,
        "price": _float_or_none(row.get("price")),
        "hasLiquidity": has_liquidity,
        "depthStatus": depth_status,
        "depthBand": {"level": depth_status, "label": depth_status},
        "liquidityReadAt": row.get("liquidityReadAt"),
        "liquidityBlockNumber": _int_or_none(row.get("liquidityBlockNumber")),
        "evidenceSource": row.get("evidenceSource"),
        "observationUsable": usable,
        "usableForObservation": usable,
        "observationReason": "associated observation pair",
        "poolStatus": "abnormal" if abnormal else "active",
        "reasons": reasons,
        "selectionRank": selection_rank,
        "recommendedPoolRole": "primary" if selection_rank == 1 and not abnormal else "",
        "recommendedPoolRoleLabel": "primary" if selection_rank == 1 and not abnormal else "",
        "associatedFundSymbols": [anchor_symbol],
        "token0AssociatedFunds": [anchor_symbol],
        "token1AssociatedFunds": [anchor_symbol],
    }


def _exchange_pair_dto(
    row: dict[str, Any],
    *,
    token_map: dict[str, dict[str, Any]],
    pool_map: dict[str, dict[str, Any]],
    abnormal: bool,
) -> dict[str, Any]:
    anchor_id = str(row.get("anchorTokenKeyId") or "").strip()
    token_x_id = str(row.get("tokenXKeyId") or "").strip()
    token_y_id = str(row.get("tokenYKeyId") or "").strip()
    anchor_meta = _exchange_token_meta(anchor_id, token_map)
    token_x_meta = _exchange_token_meta(token_x_id, token_map)
    token_y_meta = _exchange_token_meta(token_y_id, token_map)
    anchor_symbol = str(anchor_meta.get("symbol") or "UNRESOLVED:anchor")
    token_x_symbol = str(token_x_meta.get("symbol") or "UNRESOLVED:x")
    token_y_symbol = str(token_y_meta.get("symbol") or "UNRESOLVED:y")
    token_x_address = str(token_x_meta.get("address") or "")
    token_y_address = str(token_y_meta.get("address") or "")
    pool_ids = [
        str(value).strip()
        for value in (row.get("poolIds") if isinstance(row.get("poolIds"), list) else [])
        if str(value).strip()
    ]
    direct_pools = [
        pool_map[pool_id.lower()]
        for pool_id in pool_ids
        if pool_id.lower() in pool_map
    ]
    reasons = [
        str(value).strip()
        for value in (row.get("reasons") if isinstance(row.get("reasons"), list) else [])
        if str(value).strip()
    ]
    empty_reason = str(row.get("observationEmptyReason") or "").strip()
    if not empty_reason and abnormal:
        empty_reason = ",".join(reasons) or "only_abnormal_pools"
    token0 = {
        **_exchange_token_dto(token_x_id, token_x_meta),
        "associatedFunds": [anchor_symbol],
    }
    token1 = {
        **_exchange_token_dto(token_y_id, token_y_meta),
        "associatedFunds": [anchor_symbol],
    }
    return {
        "anchorTokenKeyId": anchor_id,
        "anchorSymbol": anchor_symbol,
        "anchorAddress": anchor_meta.get("address") or "",
        "directionKey": str(row.get("directionKey") or ""),
        "token0": token0,
        "token1": token1,
        "token0KeyId": token_x_id,
        "token1KeyId": token_y_id,
        "token0Symbol": token_x_symbol,
        "token1Symbol": token_y_symbol,
        "token0Address": token_x_address,
        "token1Address": token_y_address,
        "symbolPair": f"{token_x_symbol}:{token_y_symbol}",
        "addressPair": f"{token_x_address}:{token_y_address}",
        "poolIds": pool_ids,
        "pools": direct_pools,
        "directSwapPools": direct_pools,
        "poolCount": len(direct_pools),
        "observedPoolCount": len(pool_ids),
        "hasDirectSwapPool": bool(
            row.get("hasDirectSwapPool") or direct_pools or (pool_ids and abnormal)
        ),
        "directSwapPoolCount": _int_or_none(row.get("directSwapPoolCount"))
        if _int_or_none(row.get("directSwapPoolCount")) is not None
        else len(direct_pools),
        "optimalPoolId": row.get("optimalPoolId"),
        "abnormalPoolCount": _int_or_none(row.get("abnormalPoolCount")),
        "observationEmptyReason": empty_reason or None,
        "observationReason": (
            "pair_tokens_are_associated_with_flash_fund_assets"
            if direct_pools
            else empty_reason or "associated observation pair"
        ),
        "reasons": reasons,
        "associatedFundSymbols": [anchor_symbol],
        "token0AssociatedFunds": [anchor_symbol],
        "token1AssociatedFunds": [anchor_symbol],
        "abnormal": bool(abnormal),
    }


def _exchange_observation_groups(
    *,
    pair_rows: list[dict[str, Any]],
    abnormal_pair_rows: list[dict[str, Any]],
    abnormal_pool_rows: list[dict[str, Any]],
    token_map: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    anchor_ids: list[str] = []
    seen_anchor_ids: set[str] = set()
    for row in [*pair_rows, *abnormal_pair_rows, *abnormal_pool_rows]:
        anchor_id = str(row.get("anchorTokenKeyId") or "").strip()
        if anchor_id and anchor_id not in seen_anchor_ids:
            seen_anchor_ids.add(anchor_id)
            anchor_ids.append(anchor_id)

    groups: list[dict[str, Any]] = []
    for anchor_id in anchor_ids:
        anchor_meta = _exchange_token_meta(anchor_id, token_map)
        anchor_symbol = str(anchor_meta.get("symbol") or "UNRESOLVED:anchor")
        anchor_pairs = [
            row for row in pair_rows if row.get("anchorTokenKeyId") == anchor_id
        ]
        anchor_abnormal_pairs = [
            row for row in abnormal_pair_rows if row.get("anchorTokenKeyId") == anchor_id
        ]
        anchor_abnormal_pools = [
            row for row in abnormal_pool_rows if row.get("anchorTokenKeyId") == anchor_id
        ]
        shared_tokens: dict[str, dict[str, Any]] = {}
        for pair in [*anchor_pairs, *anchor_abnormal_pairs]:
            for side in ("token0", "token1"):
                token = pair.get(side)
                if not isinstance(token, dict):
                    continue
                token_key_id = str(token.get("tokenKeyId") or "")
                if token_key_id and token_key_id not in shared_tokens:
                    shared_tokens[token_key_id] = dict(token)
        token_dtos = sorted(
            shared_tokens.values(),
            key=lambda row: (
                str(row.get("symbol") or ""),
                str(row.get("address") or "").lower(),
            ),
        )
        sequences: list[dict[str, Any]] = []
        bridge_groups: dict[str, dict[str, Any]] = {}
        for pair in anchor_pairs:
            direct_pools = pair.get("directSwapPools")
            direct_pools = direct_pools if isinstance(direct_pools, list) else []
            pool_ids = [
                str(pool.get("poolId") or "")
                for pool in direct_pools
                if isinstance(pool, dict) and pool.get("poolId")
            ]
            pair_text = (
                f"{pair.get('token0Symbol')} -> {pair.get('token1Symbol')}"
            )
            candidate = {
                "leg": "y-z",
                "direction": "y-z",
                "directionKey": pair.get("directionKey"),
                "symbolPair": pair_text,
                "poolCount": len(direct_pools),
                "pools": direct_pools,
                "optimalPoolId": pair.get("optimalPoolId"),
            }
            sequence = {
                "anchorTokenKeyId": anchor_id,
                "anchorSymbol": anchor_symbol,
                "pathText": f"{anchor_symbol} -> {pair_text} -> {anchor_symbol}",
                "bridgeSymbol": pair.get("token1Symbol"),
                "counterSymbol": pair.get("token0Symbol"),
                "bridgePool": direct_pools[0] if direct_pools else {},
                "pools": direct_pools,
                "poolIds": pool_ids,
                "hopCandidates": [candidate],
                "observationReachable": bool(direct_pools),
                "observationState": (
                    "reachable" if direct_pools else "no_yz_pool"
                ),
                "observationEmptyReason": pair.get("observationEmptyReason"),
                "directionKey": pair.get("directionKey"),
            }
            sequences.append(sequence)
            if not direct_pools:
                continue
            canonical_addresses = sorted(
                [
                    str(pair.get("token0Address") or "").lower(),
                    str(pair.get("token1Address") or "").lower(),
                ]
            )
            bridge_key = ":".join(value for value in canonical_addresses if value)
            bridge_key = bridge_key or str(pair.get("directionKey") or "")
            bridge = bridge_groups.setdefault(
                bridge_key,
                {
                    "bridgePool": pool_ids[0] if pool_ids else "",
                    "preview": pair_text,
                    "directionCount": 0,
                    "poolIds": set(),
                },
            )
            bridge["directionCount"] = int(bridge.get("directionCount") or 0) + 1
            bridge["poolIds"].update(pool_ids)
        bridge_group_rows = []
        for bridge in bridge_groups.values():
            bridge["poolIds"] = sorted(bridge.get("poolIds") or [])
            bridge["poolCount"] = len(bridge["poolIds"])
            bridge_group_rows.append(bridge)
        bridge_group_rows.sort(
            key=lambda row: (
                str(row.get("preview") or ""),
                str(row.get("bridgePool") or ""),
            )
        )
        active_pool_by_id: dict[str, dict[str, Any]] = {}
        for pair in anchor_pairs:
            for pool in pair.get("directSwapPools") or []:
                if not isinstance(pool, dict):
                    continue
                pool_id = str(pool.get("poolId") or "").lower()
                if pool_id:
                    active_pool_by_id[pool_id] = pool
        reachable_count = sum(
            1 for sequence in sequences if sequence.get("observationReachable")
        )
        empty_reason = next(
            (
                str(pair.get("observationEmptyReason") or "").strip()
                for pair in anchor_pairs
                if not pair.get("hasDirectSwapPool")
                and str(pair.get("observationEmptyReason") or "").strip()
            ),
            "",
        )
        groups.append(
            {
                "anchorTokenKeyId": anchor_id,
                "anchorSymbol": anchor_symbol,
                "anchorAddresses": [anchor_meta.get("address") or ""],
                "ycZcSameSet": True,
                "ycTokensRef": "sharedTokens",
                "zcTokensRef": "sharedTokens",
                "ycPoolsRef": "sharedPools",
                "zcPoolsRef": "sharedPools",
                "ycTokens": token_dtos,
                "zcTokens": token_dtos,
                "sharedTokens": token_dtos,
                "ycTokenCount": str(len(token_dtos)),
                "zcTokenCount": str(len(token_dtos)),
                "sharedTokenCount": str(len(token_dtos)),
                "ycPools": list(active_pool_by_id.values()),
                "zcPools": list(active_pool_by_id.values()),
                "sharedPools": list(active_pool_by_id.values()),
                "ycPoolCount": str(len(active_pool_by_id)),
                "zcPoolCount": str(len(active_pool_by_id)),
                "sharedPoolCount": str(len(active_pool_by_id)),
                "candidatePoolCount": str(len(active_pool_by_id)),
                "candidateCombinationCount": str(len(sequences)),
                "observationCombinationCount": str(len(sequences)),
                "observationSequenceCount": str(len(sequences)),
                "reachableCombinationCount": str(reachable_count),
                "noPoolCombinationCount": str(len(sequences) - reachable_count),
                "observationSequences": sequences,
                "closedLoopSequences": [],
                "combinationGroups": sequences,
                "bridgePoolObservationGroups": bridge_group_rows,
                "observationEmptyReason": empty_reason,
                "observationState": {
                    "status": "reachable" if reachable_count else "partial",
                    "reachable": bool(reachable_count),
                    "reason": empty_reason,
                },
                "abnormalTokens": [],
                "abnormalPools": anchor_abnormal_pools,
            }
        )
    groups.sort(
        key=lambda group: (
            str(group.get("anchorSymbol") or ""),
            str(group.get("anchorTokenKeyId") or ""),
        )
    )
    return groups


def market_observation_manager_shadow_payload(
    legacy_payload: dict[str, Any],
    *,
    chain_id: int,
    request_id: str | None = None,
) -> dict[str, Any]:
    """Read the M4 shadow and report bounded diagnostics without changing legacy fields."""

    if (gate := _shadow_gate("market-observation")) is not None:
        return gate
    try:
        envelope = _manager_read_envelope(
            "/v1/candidate-exchange-pools",
            query={
                "chainId": int(chain_id),
                "limit": CANDIDATE_EXCHANGE_SNAPSHOT_LIMIT,
            },
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
            "candidate-exchange manager response must contain an object data field",
            data_source="manager",
        )
    status = data.get("status") if isinstance(data.get("status"), dict) else {}
    manager_status_counts = {
        "anchorCount": _int_or_none(status.get("anchorCount")),
        "tokenCount": _int_or_none(status.get("tokenCount")),
        "directionCount": _int_or_none(status.get("directionCount")),
        "emptyDirectionCount": _int_or_none(status.get("emptyDirectionCount")),
        "poolCount": _int_or_none(status.get("poolCount")),
        "abnormalDirectionCount": _int_or_none(status.get("abnormalDirectionCount")),
        "abnormalPoolCount": _int_or_none(status.get("abnormalPoolCount")),
    }
    manager_snapshot_counts = {
        "yzPairSnapshotCount": _list_length(data.get("yzPairs")),
        "poolSnapshotCount": _list_length(data.get("pools")),
        "abnormalYzPairSnapshotCount": _list_length(data.get("abnormalYzPairs")),
        "abnormalPoolSnapshotCount": _list_length(data.get("abnormalPools")),
        "liquiditySnapshotCount": _list_length(data.get("liquiditySnapshots")),
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
        "observationVersion": status.get("observationVersion"),
        "observationChecksum": status.get("materializedChecksum"),
        "candidateEdgeVersion": status.get("candidateEdgeVersion"),
        "candidateEdgeChecksum": status.get("candidateEdgeChecksum"),
        "poolScopeChecksum": status.get("poolScopeChecksum"),
        "snapshotLimit": CANDIDATE_EXCHANGE_SNAPSHOT_LIMIT,
        "rowComparisonSampleLimit": CANDIDATE_EXCHANGE_ROW_COMPARISON_SAMPLE_LIMIT,
        "snapshotCountSemantics": {
            "statusCounts": "full_materialized_scope",
            "rowCounts": "bounded_snapshot_rows",
            "detail": (
                "managerStatusCounts are full materialized scope counts; "
                "managerSnapshotCounts values derived from row arrays are limited to "
                f"the requested snapshot limit ({CANDIDATE_EXCHANGE_SNAPSHOT_LIMIT})."
            ),
        },
        "managerStatus": status,
        "legacyPageCounts": {
            "routeCount": _int_or_none(
                legacy_summary.get("routeCount") or legacy_payload.get("routeCount")
            ),
            "candidateYzPairCount": _int_or_none(
                legacy_summary.get("candidateYzPairCount")
                or legacy_payload.get("candidateYzPairCount")
            ),
            "verifiedYzPairCount": _int_or_none(
                legacy_summary.get("verifiedYzPairCount")
                or legacy_payload.get("verifiedYzPairCount")
            ),
            "missingYzPairCount": _int_or_none(
                legacy_summary.get("missingYzPairCount")
                or legacy_payload.get("missingYzPairCount")
            ),
            "matchedPoolCount": _int_or_none(legacy_payload.get("matchedPoolCount")),
        },
        "managerStatusCounts": manager_status_counts,
        "managerSnapshotCounts": manager_snapshot_counts,
        "rowComparison": _candidate_exchange_row_comparison(
            chain_id=chain_id,
            request_id=request_id,
        ),
        "comparisonReadyForFieldSwitch": False,
        "comparisonReason": "Legacy observation rows are a page projection over route/YZ data; manager YZ/pool rows require full production-copy dual-read before cutover.",
    }


def _candidate_exchange_row_comparison(
    *,
    chain_id: int,
    request_id: str | None,
) -> dict[str, Any]:
    legacy_anchor_scope = f"dex-arbitrage-{int(chain_id)}-uniswap-v4"
    try:
        envelope = ManagerApiClient.from_env().get_json(
            "/v1/candidate-exchange-pools/compare",
            query={
                "chainId": int(chain_id),
                "sampleLimit": CANDIDATE_EXCHANGE_ROW_COMPARISON_SAMPLE_LIMIT,
                "legacyAnchorScope": legacy_anchor_scope,
            },
            request_id=request_id,
        )
    except ManagerApiClientError as exc:
        return {
            "ok": False,
            "source": "manager",
            "sampleLimit": CANDIDATE_EXCHANGE_ROW_COMPARISON_SAMPLE_LIMIT,
            "legacyAnchorScope": legacy_anchor_scope,
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
            "sampleLimit": CANDIDATE_EXCHANGE_ROW_COMPARISON_SAMPLE_LIMIT,
            "legacyAnchorScope": legacy_anchor_scope,
            "code": "MANAGER_API_INVALID_RESPONSE",
            "detail": "candidate-exchange compare response must contain an object data field",
            "readyForCutover": False,
        }
    return {
        "ok": True,
        "source": "manager",
        "sampleLimit": CANDIDATE_EXCHANGE_ROW_COMPARISON_SAMPLE_LIMIT,
        "legacyAnchorScope": legacy_anchor_scope,
        "managerEnvelopeVersion": envelope.get("version"),
        "managerChecksum": data.get("managerChecksum"),
        "readyForCutover": bool(data.get("readyForCutover")),
        "differencesTruncated": bool(data.get("differencesTruncated")),
        "causeCounts": data.get("causeCounts") if isinstance(data.get("causeCounts"), dict) else {},
        "differenceCounts": {
            key: _comparison_summary(data.get(key))
            for key in ("yzPairs", "pools", "abnormalYzPairs", "abnormalPools")
        },
        "sampleReadyForCutover": bool(data.get("readyForCutover"))
        and not bool(data.get("differencesTruncated")),
    }
