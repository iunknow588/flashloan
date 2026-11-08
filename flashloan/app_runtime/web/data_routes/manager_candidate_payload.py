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

from core.dex_candidate_response import compact_candidate_payload
from .manager_candidate_refresh_payload import candidate_exchange_refresh_manager_payload
from .manager_page_compare_common import (
    CANDIDATE_EXCHANGE_ROW_COMPARISON_SAMPLE_LIMIT,
    CANDIDATE_EXCHANGE_SNAPSHOT_LIMIT,
    CANDIDATE_ROW_COMPARISON_SAMPLE_LIMIT,
    CANDIDATE_SNAPSHOT_LIMIT,
    ManagerApiClientError,
    _comparison_summary,
    _delta,
    _float_or_none,
    _int_or_none,
    _manager_read_envelope,
    _manager_read_error,
    _mapping_value,
    _shadow_error,
    _shadow_gate,
)


def candidate_manager_shadow_payload(
    legacy_payload: dict[str, Any],
    *,
    chain_id: int,
    request_id: str | None = None,
) -> dict[str, Any]:
    """Read the M3 shadow and report bounded diagnostics without changing legacy fields."""

    if (gate := _shadow_gate("candidate")) is not None:
        return gate
    try:
        envelope = _manager_read_envelope(
            "/v1/candidate-edge-pools",
            query={"chainId": int(chain_id), "limit": CANDIDATE_SNAPSHOT_LIMIT},
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
            "candidate manager response must contain an object data field",
            data_source="manager",
        )
    status = data.get("status") if isinstance(data.get("status"), dict) else {}
    pool_pairs = data.get("poolPairs")
    pools = data.get("pools")
    row_comparison = _candidate_row_comparison(
        chain_id=chain_id,
        request_id=request_id,
    )
    legacy_summary = (
        legacy_payload.get("summary")
        if isinstance(legacy_payload.get("summary"), dict)
        else {}
    )
    manager_status_counts = {
        "eligiblePoolCount": _int_or_none(status.get("eligiblePoolCount")),
        "abnormalPoolCount": _int_or_none(status.get("abnormalPoolCount")),
        "recommendationCount": _int_or_none(status.get("recommendationCount")),
        "candidateTokenCount": _int_or_none(status.get("candidateTokenCount")),
        "anchorTokenCount": _int_or_none(status.get("anchorTokenCount")),
    }
    manager_snapshot_counts = {
        "poolPairSnapshotCount": len(pool_pairs) if isinstance(pool_pairs, list) else None,
        "poolSnapshotCount": len(pools) if isinstance(pools, list) else None,
        "recommendationSnapshotCount": len(
            data.get("recommendations") if isinstance(data.get("recommendations"), list) else []
        ),
        "abnormalPoolSnapshotCount": len(
            data.get("abnormalPools") if isinstance(data.get("abnormalPools"), list) else []
        ),
    }
    legacy_counts = {
        "poolPairCount": _int_or_none(
            legacy_summary.get("staticPairCount")
            or legacy_summary.get("pairTableCount")
        ),
        "eligiblePoolCount": _int_or_none(
            legacy_summary.get("staticPoolCount")
            or legacy_summary.get("preferredPoolCount")
        ),
        "abnormalPoolCount": _int_or_none(
            legacy_summary.get("abnormalStaticPoolCount")
        ),
        "recommendationCount": None,
        "candidateTokenCount": _int_or_none(legacy_summary.get("tokenCount")),
        "anchorTokenCount": _int_or_none(legacy_summary.get("anchorGroupCount")),
    }
    comparisons = {
        key: {
            "legacy": legacy_counts[key],
            "manager": manager_status_counts.get(key),
            "delta": _delta(legacy_counts[key], manager_status_counts.get(key)),
        }
        for key in ("poolPairCount", "eligiblePoolCount", "abnormalPoolCount")
    }
    comparisons["candidateTokenCount"] = {
        "legacy": legacy_counts["candidateTokenCount"],
        "manager": manager_status_counts["candidateTokenCount"],
        "delta": _delta(
            legacy_counts["candidateTokenCount"],
            manager_status_counts["candidateTokenCount"],
        ),
        "semanticWarning": "legacy tokenCount includes the selected page scope; manager candidateTokenCount includes admitted candidate members only.",
    }
    comparisons["anchorTokenCount"] = {
        "legacy": legacy_counts["anchorTokenCount"],
        "manager": manager_status_counts["anchorTokenCount"],
        "delta": _delta(
            legacy_counts["anchorTokenCount"],
            manager_status_counts["anchorTokenCount"],
        ),
        "semanticWarning": "legacy anchorGroupCount may describe grouped anchors, not individual anchor token rows.",
    }
    comparisons["poolPairCount"]["semanticWarning"] = (
        "manager lacks a persisted full candidate pair count in this snapshot; "
        "the row-array count is bounded by snapshotLimit and must not be used "
        "as a full materialization count."
    )
    comparisons["recommendationCount"] = {
        "legacy": legacy_counts["recommendationCount"],
        "manager": manager_status_counts["recommendationCount"],
        "delta": _delta(
            legacy_counts["recommendationCount"],
            manager_status_counts["recommendationCount"],
        ),
        "semanticWarning": "legacy counts do not expose a recommendationCount field yet.",
    }
    return {
        "ok": True,
        "source": "manager",
        "enabled": True,
        "legacyUnaffected": True,
        "managerEnvelopeVersion": envelope.get("version"),
        "candidateVersion": status.get("candidateVersion"),
        "candidateChecksum": status.get("materializedChecksum"),
        "poolScopeChecksum": status.get("poolScopeChecksum"),
        "snapshotLimit": CANDIDATE_SNAPSHOT_LIMIT,
        "rowComparisonSampleLimit": CANDIDATE_ROW_COMPARISON_SAMPLE_LIMIT,
        "snapshotCountSemantics": {
            "statusCounts": "full_materialized_scope",
            "rowCounts": "bounded_snapshot_rows",
            "detail": (
                "managerStatusCounts are full materialized scope counts; "
                "managerSnapshotCounts values derived from row arrays are limited to "
                f"the requested snapshot limit ({CANDIDATE_SNAPSHOT_LIMIT}); "
                "status counts describe the full materialized scope."
            ),
        },
        "managerStatus": status,
        "legacyCounts": legacy_counts,
        "managerStatusCounts": manager_status_counts,
        "managerSnapshotCounts": manager_snapshot_counts,
        "rowComparison": row_comparison,
        "countComparisons": comparisons,
        "comparisonReadyForFieldSwitch": False,
        "comparisonReason": "Count and sampled row comparison are diagnostic only; full unbounded dual-read on a production copy is still required before cutover.",
    }


def candidate_manager_page_payload(
    *,
    chain_id: int,
    protocols: list[str],
    page: int,
    page_size: int,
    query: str,
    quality: str,
    abnormal_pool_page: int | None = None,
    abnormal_token_page: int | None = None,
    chain_selection: dict[str, Any] | None = None,
    request_id: str | None = None,
) -> dict[str, Any]:
    """Read the manager-owned candidate pool projection with no legacy fallback."""

    try:
        envelope = _manager_read_envelope(
            "/v1/candidate-edge-pools",
            query={"chainId": int(chain_id), "limit": CANDIDATE_SNAPSHOT_LIMIT},
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
            "candidate manager response must contain an object data field",
        )
    status = data.get("status") if isinstance(data.get("status"), dict) else None
    if status is None or not isinstance(status.get("enabled"), bool):
        return _manager_read_error(
            "MANAGER_API_INVALID_RESPONSE",
            "candidate manager response field status.enabled must be a boolean",
        )
    if _int_or_none(status.get("chainId")) != int(chain_id):
        return _manager_read_error(
            "MANAGER_CANDIDATE_SCOPE_MISMATCH",
            "candidate manager response chainId does not match the requested chain",
        )
    for field in (
        "anchorTokens",
        "poolPairs",
        "pools",
        "recommendations",
        "abnormalAnchorTokens",
        "abnormalPoolPairs",
        "abnormalPools",
    ):
        if not isinstance(data.get(field), list):
            return _manager_read_error(
                "MANAGER_API_INVALID_RESPONSE",
                f"candidate manager response field {field} must be an array",
            )
    if "tokens" in data and not isinstance(data.get("tokens"), list):
        return _manager_read_error(
            "MANAGER_API_INVALID_RESPONSE",
            "candidate manager response field tokens must be an array",
        )

    token_map = _candidate_token_metadata_map(data)
    anchor_ids = _ordered_candidate_anchor_ids(data)
    groups = [
        _candidate_manager_group(
            anchor_id,
            data=data,
            status=status,
            token_map=token_map,
        )
        for anchor_id in anchor_ids
    ]
    pairs = _candidate_manager_pairs(data.get("poolPairs") or [], token_map)
    payload: dict[str, Any] = {
        "ok": True,
        "source": "manager",
        "dataSource": "manager",
        "mode": "dex_pool_candidates",
        "chain_id": int(chain_id),
        "chain_selection": dict(chain_selection or {}),
        "selected_protocols": list(protocols),
        "summary": _candidate_manager_summary(
            data=data,
            status=status,
            anchor_ids=anchor_ids,
            groups=groups,
        ),
        "poolStaticTable": {
            "mode": "manager",
            "exists": True,
            "network": status.get("network"),
            "chainId": status.get("chainId"),
            "protocol": status.get("protocol"),
            "scopeKey": status.get("scopeKey"),
            "candidateVersion": status.get("candidateVersion"),
            "materializedChecksum": status.get("materializedChecksum"),
            "generatedAt": status.get("generatedAt"),
            "path": "manager://candidate-edge-pools",
        },
        "candidatePoolCache": {
            "refreshedAt": status.get("generatedAt"),
            "refreshMode": "manager",
            "version": status.get("candidateVersion"),
            "checksum": status.get("materializedChecksum"),
            "snapshotLimit": CANDIDATE_SNAPSHOT_LIMIT,
        },
        "anchorGroups": groups,
        "pairs": pairs,
        "filters": {
            "query": query,
            "quality": quality,
        },
        "pagination": {},
        "managerEnvelopeVersion": envelope.get("version"),
        "managerStatus": status,
        "versions": {
            "candidateVersion": status.get("candidateVersion"),
            "candidateChecksum": status.get("materializedChecksum"),
            "poolScopeChecksum": status.get("poolScopeChecksum"),
            "libraryVersions": _mapping_value(status.get("libraryVersions")),
            "libraryChecksums": _mapping_value(status.get("libraryChecksums")),
        },
        "managerSnapshot": {
            "snapshotLimit": CANDIDATE_SNAPSHOT_LIMIT,
            "rowCounts": {
                "anchorTokens": len(data.get("anchorTokens") or []),
                "poolPairs": len(data.get("poolPairs") or []),
                "pools": len(data.get("pools") or []),
                "recommendations": len(data.get("recommendations") or []),
            },
            "fullStatusCounts": {
                "anchorTokenCount": status.get("anchorTokenCount"),
                "candidateTokenCount": status.get("candidateTokenCount"),
                "eligiblePoolCount": status.get("eligiblePoolCount"),
                "recommendationCount": status.get("recommendationCount"),
                "abnormalPoolCount": status.get("abnormalPoolCount"),
            },
        },
    }
    return compact_candidate_payload(
        payload,
        page=page,
        page_size=page_size,
        query=query,
        quality=quality,
        abnormal_pool_page=abnormal_pool_page,
        abnormal_token_page=abnormal_token_page,
    )


def candidate_edge_refresh_manager_payload(
    *,
    chain_id: int | None,
    dry_run: bool,
    request_id: str | None,
    idempotency_key: str | None,
) -> dict[str, Any]:
    """Rebuild Candidate Edge Manager state through the request-bound RuntimeCore."""

    if chain_id is None:
        return _shadow_error(
            "MANAGER_CHAIN_ID_REQUIRED",
            "candidate edge refresh requires a selected chain id",
            data_source="manager",
            upstream_status=400,
        )
    if not str(request_id or "").strip():
        return _shadow_error(
            "MANAGER_REQUEST_ID_REQUIRED",
            "candidate edge refresh requires a request id",
            data_source="manager",
            upstream_status=400,
        )
    if not str(idempotency_key or "").strip():
        return _shadow_error(
            "MANAGER_IDEMPOTENCY_KEY_REQUIRED",
            "candidate edge refresh requires an idempotency key",
            data_source="manager",
            upstream_status=400,
        )
    if not has_app_context():
        return _shadow_error(
            "RUNTIME_CORE_UNAVAILABLE",
            "chain runtime is not bound to this request",
            data_source="manager",
            upstream_status=503,
        )
    from flask import g

    try:
        chain_runtime = getattr(g, "chain_runtime", None)
    except RuntimeError:
        chain_runtime = None
    if chain_runtime is None:
        return _shadow_error(
            "RUNTIME_CORE_UNAVAILABLE",
            "chain runtime is not bound to this request",
            data_source="manager",
            upstream_status=503,
        )
    if not hasattr(chain_runtime, "execute_command"):
        return _shadow_error(
            "RUNTIME_CORE_COMMAND_UNAVAILABLE",
            "chain runtime does not expose RuntimeCore commands",
            data_source="manager",
            upstream_status=503,
        )
    try:
        envelope = chain_runtime.execute_command(
            "rebuild_candidate_edges",
            request_id=str(request_id).strip(),
            idempotency_key=str(idempotency_key).strip(),
            dry_run=bool(dry_run),
        )
    except (TypeError, ValueError, RuntimeError) as exc:
        return _shadow_error(
            "RUNTIME_CORE_COMMAND_FAILED",
            f"RuntimeCore rebuild_candidate_edges command failed: {str(exc)[:500]}",
            data_source="manager",
            upstream_status=503,
        )
    if not isinstance(envelope, dict):
        return _shadow_error(
            "RUNTIME_CORE_INVALID_RESPONSE",
            "RuntimeCore rebuild_candidate_edges command must return an object",
            data_source="manager",
            upstream_status=503,
        )
    if envelope.get("ok") is False:
        return _shadow_error(
            str(envelope.get("code") or "RUNTIME_CORE_COMMAND_FAILED"),
            str(
                envelope.get("detail")
                or "RuntimeCore rebuild_candidate_edges command failed"
            ),
            data_source="manager",
            upstream_status=(
                envelope.get("upstreamStatus")
                if isinstance(envelope.get("upstreamStatus"), int)
                else 503
            ),
        )
    if envelope.get("command") != "rebuild_candidate_edges":
        return _shadow_error(
            "RUNTIME_CORE_INVALID_RESPONSE",
            "RuntimeCore returned an unexpected candidate edge command",
            data_source="manager",
            upstream_status=503,
        )
    if envelope.get("dataSource") != "rust_runtime_core":
        return _shadow_error(
            "RUNTIME_CORE_INVALID_RESPONSE",
            "RuntimeCore candidate edge command has an unexpected dataSource",
            data_source="manager",
            upstream_status=503,
        )
    if _int_or_none(envelope.get("chainId")) != int(chain_id):
        return _shadow_error(
            "RUNTIME_CORE_SCOPE_MISMATCH",
            "RuntimeCore candidate edge command chainId does not match the requested chain",
            data_source="manager",
            upstream_status=503,
        )
    if not isinstance(envelope.get("dryRun"), bool) or envelope.get("dryRun") != bool(
        dry_run
    ):
        return _shadow_error(
            "RUNTIME_CORE_INVALID_RESPONSE",
            "RuntimeCore candidate edge command must preserve the requested dryRun flag",
            data_source="manager",
            upstream_status=503,
        )
    if not isinstance(envelope.get("idempotentReplay"), bool):
        return _shadow_error(
            "RUNTIME_CORE_INVALID_RESPONSE",
            "RuntimeCore candidate edge command must include Boolean idempotentReplay",
            data_source="manager",
            upstream_status=503,
        )
    return {
        "ok": True,
        "source": "manager",
        "dataSource": "manager",
        "runtimeDataSource": envelope.get("dataSource"),
        "managerEnvelopeVersion": envelope.get("version"),
        "dryRun": bool(envelope.get("dryRun")),
        "idempotentReplay": bool(envelope.get("idempotentReplay")),
        "candidateVersion": _int_or_none(envelope.get("candidateVersion")),
        "materializedChecksum": envelope.get("materializedChecksum"),
        "changedRows": _int_or_none(envelope.get("changedRows")) or 0,
        "consumersNeedRebuild": bool(envelope.get("consumersNeedRebuild")),
        "managerResult": envelope,
    }


def _candidate_token_metadata_map(data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    by_key: dict[str, dict[str, Any]] = {}

    def add_meta(meta: dict[str, Any]) -> None:
        key = str(meta.get("tokenKeyId") or "").strip()
        if not key or key in by_key:
            return
        address = str(meta.get("tokenAddress") or "").strip() or _candidate_key_address(key)
        symbol = str(meta.get("tokenSymbol") or meta.get("symbol") or "").strip()
        resolved = bool(symbol) and not symbol.startswith("UNRESOLVED:")
        if not symbol:
            symbol = f"UNRESOLVED:{address[:10]}" if address else "UNRESOLVED:unknown"
        by_key[key] = {
            "address": address,
            "symbol": symbol,
            "name": meta.get("tokenName") or meta.get("name") or "",
            "libraryKey": meta.get("tokenLibraryKey") or meta.get("libraryKey") or "",
            "resolved": resolved,
        }

    for row in data.get("tokens") or []:
        if isinstance(row, dict):
            add_meta(row)
    for row in data.get("anchorTokens") or []:
        if not isinstance(row, dict):
            continue
        add_meta(
            {
                "tokenKeyId": row.get("anchorTokenKeyId"),
                "tokenSymbol": row.get("tokenSymbol"),
            }
        )
    for row in data.get("poolPairs") or []:
        if not isinstance(row, dict):
            continue
        add_meta({"tokenKeyId": row.get("anchorTokenKeyId")})
        add_meta({"tokenKeyId": row.get("candidateTokenKeyId")})
    return by_key


def _ordered_candidate_anchor_ids(data: dict[str, Any]) -> list[str]:
    anchor_ids: list[str] = []
    seen: set[str] = set()
    for row in data.get("anchorTokens") or []:
        if not isinstance(row, dict):
            continue
        key = str(row.get("anchorTokenKeyId") or "").strip()
        if key and key not in seen:
            seen.add(key)
            anchor_ids.append(key)
    return anchor_ids


def _candidate_key_address(token_key_id: str) -> str:
    parts = str(token_key_id or "").split(":")
    return parts[2] if len(parts) == 3 else ""


def _candidate_manager_pairs(
    pool_pairs: list[Any],
    token_map: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    pairs: list[dict[str, Any]] = []
    for pair in pool_pairs:
        if not isinstance(pair, dict):
            continue
        anchor_id = str(pair.get("anchorTokenKeyId") or "")
        candidate_id = str(pair.get("candidateTokenKeyId") or "")
        anchor_meta = token_map.get(anchor_id, {})
        candidate_meta = token_map.get(candidate_id, {})
        anchor_address = anchor_meta.get("address") or _candidate_key_address(anchor_id)
        candidate_address = candidate_meta.get("address") or _candidate_key_address(candidate_id)
        pairs.append(
            {
                "symbolPair": f"{anchor_meta.get('symbol') or 'X'}:{candidate_meta.get('symbol') or 'Y'}",
                "addressPair": f"{anchor_address}:{candidate_address}",
                "anchorTokenKeyId": pair.get("anchorTokenKeyId"),
                "candidateTokenKeyId": pair.get("candidateTokenKeyId"),
                "pairKeyId": pair.get("pairKeyId"),
                "poolCount": pair.get("poolCount"),
                "ycOptimalPoolId": pair.get("firstPrimaryPoolId"),
                "zcOptimalPoolId": pair.get("thirdPrimaryPoolId"),
            }
        )
    return pairs


def _candidate_manager_group(
    anchor_id: str,
    *,
    data: dict[str, Any],
    status: dict[str, Any],
    token_map: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    anchor_meta = token_map.get(anchor_id, {})
    anchor_symbol = anchor_meta.get("symbol") or "X"
    anchor_addresses = [anchor_meta.get("address")] if anchor_meta.get("address") else []
    anchor_pairs = [
        pair
        for pair in data.get("poolPairs") or []
        if isinstance(pair, dict) and pair.get("anchorTokenKeyId") == anchor_id
    ]
    anchor_pools = [
        pool
        for pool in data.get("pools") or []
        if isinstance(pool, dict) and pool.get("anchorTokenKeyId") == anchor_id
    ]
    anchor_abnormal_pools = [
        pool
        for pool in data.get("abnormalPools") or []
        if isinstance(pool, dict) and pool.get("anchorTokenKeyId") == anchor_id
    ]
    anchor_abnormal_tokens = [
        row
        for row in data.get("abnormalAnchorTokens") or []
        if isinstance(row, dict)
        and (
            row.get("anchorTokenKeyId")
            or row.get("anchor_token_key_id")
        )
        == anchor_id
    ]
    anchor_abnormal_pairs = [
        row
        for row in data.get("abnormalPoolPairs") or []
        if isinstance(row, dict) and row.get("anchorTokenKeyId") == anchor_id
    ]

    shared_tokens: list[dict[str, Any]] = []
    yc_tokens: list[dict[str, Any]] = []
    zc_tokens: list[dict[str, Any]] = []
    shared_pools: list[dict[str, Any]] = []
    yc_pools: list[dict[str, Any]] = []
    zc_pools: list[dict[str, Any]] = []
    seen_candidates: set[str] = set()
    same_set_count = 0

    for pair in anchor_pairs:
        candidate_id = str(pair.get("candidateTokenKeyId") or "")
        if not candidate_id:
            continue
        candidate_meta = token_map.get(candidate_id, {})
        first_ids = _candidate_recommended_pool_ids(pair, first=True)
        third_ids = _candidate_recommended_pool_ids(pair, first=False)
        candidate_same_set = bool(first_ids) and set(first_ids) == set(third_ids)
        if candidate_same_set:
            same_set_count += 1
        token_dto = {
            "symbol": candidate_meta.get("symbol") or "Y",
            "name": candidate_meta.get("name") or "",
            "address": candidate_meta.get("address") or _candidate_key_address(candidate_id),
            "poolCount": pair.get("poolCount"),
            "ycOptimalPoolId": pair.get("firstPrimaryPoolId"),
            "zcOptimalPoolId": pair.get("thirdPrimaryPoolId"),
            "tokenKeyId": candidate_id,
            "libraryKey": candidate_meta.get("libraryKey") or "",
        }
        if candidate_id not in seen_candidates:
            seen_candidates.add(candidate_id)
            if candidate_same_set:
                shared_tokens.append(token_dto)
            else:
                yc_tokens.append(token_dto)
                zc_tokens.append(dict(token_dto))

        candidate_pools = [
            _candidate_manager_pool_dto(
                row,
                anchor_id=anchor_id,
                candidate_id=candidate_id,
                anchor_meta=anchor_meta,
                candidate_meta=candidate_meta,
                token_map=token_map,
                first_ids=first_ids,
                third_ids=third_ids,
                status=status,
                abnormal=False,
            )
            for row in anchor_pools
            if isinstance(row, dict) and row.get("candidateTokenKeyId") == candidate_id
        ]
        if candidate_same_set:
            shared_pools.extend(candidate_pools)
        else:
            for pool in candidate_pools:
                if pool.get("isFirstHopRecommended"):
                    yc_pools.append(pool)
                if pool.get("isThirdHopRecommended"):
                    zc_pools.append(pool)

    abnormal_tokens: list[dict[str, Any]] = []
    seen_abnormal: set[str] = set()
    for row in anchor_abnormal_tokens:
        candidate_id = str(row.get("tokenKeyId") or row.get("token_key_id") or "")
        candidate_meta = token_map.get(candidate_id, {})
        if candidate_id in seen_abnormal:
            continue
        seen_abnormal.add(candidate_id)
        abnormal_tokens.append(
            {
                "symbol": row.get("tokenSymbol") or candidate_meta.get("symbol") or "Y",
                "name": candidate_meta.get("name") or "",
                "address": candidate_meta.get("address") or _candidate_key_address(candidate_id),
                "poolCount": row.get("poolCount"),
                "reasons": row.get("reasons") or [],
                "tokenKeyId": candidate_id,
            }
        )
    for row in anchor_abnormal_pairs:
        candidate_id = str(row.get("candidateTokenKeyId") or "")
        if not candidate_id or candidate_id in seen_abnormal:
            continue
        seen_abnormal.add(candidate_id)
        candidate_meta = token_map.get(candidate_id, {})
        abnormal_tokens.append(
            {
                "symbol": candidate_meta.get("symbol") or "Y",
                "name": candidate_meta.get("name") or "",
                "address": candidate_meta.get("address") or _candidate_key_address(candidate_id),
                "poolCount": row.get("poolCount"),
                "reasons": row.get("reasons") or [],
                "tokenKeyId": candidate_id,
            }
        )

    abnormal_pools = [
        _candidate_manager_pool_dto(
            row,
            anchor_id=anchor_id,
            candidate_id=str(row.get("candidateTokenKeyId") or ""),
            anchor_meta=anchor_meta,
            candidate_meta=token_map.get(str(row.get("candidateTokenKeyId") or ""), {}),
            token_map=token_map,
            first_ids=[],
            third_ids=[],
            status=status,
            abnormal=True,
        )
        for row in anchor_abnormal_pools
    ]
    group_same_set = bool(anchor_pairs) and same_set_count == len(anchor_pairs)
    return {
        "anchorSymbol": anchor_symbol,
        "anchorAddresses": anchor_addresses or ([_candidate_key_address(anchor_id)] if anchor_id else []),
        "anchorTokenKeyId": anchor_id,
        "ycZcSameSet": group_same_set,
        "ycPoolsRef": "sharedPools" if group_same_set else "ycPools",
        "zcPoolsRef": "sharedPools" if group_same_set else "zcPools",
        "ycTokensRef": "sharedTokens" if group_same_set else "ycTokens",
        "zcTokensRef": "sharedTokens" if group_same_set else "zcTokens",
        "ycTokens": yc_tokens,
        "zcTokens": zc_tokens,
        "sharedTokens": shared_tokens,
        "ycPoolCount": str(len(yc_pools) or len(shared_pools)),
        "zcPoolCount": str(len(zc_pools) or len(shared_pools)),
        "ycTokenCount": str(len(yc_tokens) or len(shared_tokens)),
        "zcTokenCount": str(len(zc_tokens) or len(shared_tokens)),
        "candidatePoolCount": str(len(anchor_pools)),
        "ycPools": yc_pools,
        "zcPools": zc_pools,
        "sharedPools": shared_pools,
        "abnormalTokens": abnormal_tokens,
        "abnormalPools": abnormal_pools,
    }


def _candidate_recommended_pool_ids(pair: dict[str, Any], *, first: bool) -> list[str]:
    primary_key = "firstPrimaryPoolId" if first else "thirdPrimaryPoolId"
    backup_key = "firstBackupPoolIds" if first else "thirdBackupPoolIds"
    ids = [str(pair.get(primary_key) or "")]
    backups = pair.get(backup_key)
    if isinstance(backups, list):
        ids.extend(str(item) for item in backups if item)
    return [item for item in ids if item]


def _candidate_manager_pool_dto(
    row: dict[str, Any],
    *,
    anchor_id: str,
    candidate_id: str,
    anchor_meta: dict[str, Any],
    candidate_meta: dict[str, Any],
    token_map: dict[str, dict[str, Any]],
    first_ids: list[str],
    third_ids: list[str],
    status: dict[str, Any],
    abnormal: bool,
) -> dict[str, Any]:
    pool_id = str(row.get("poolId") or row.get("pool_id") or "")
    anchor_symbol = anchor_meta.get("symbol") or "X"
    candidate_symbol = candidate_meta.get("symbol") or "Y"
    candidate_address = candidate_meta.get("address") or _candidate_key_address(candidate_id)
    anchor_address = anchor_meta.get("address") or _candidate_key_address(anchor_id)
    fee = _int_or_none(row.get("fee"))
    dynamic_fee = bool(fee is not None and (fee & 0x800000))
    hooks = str(row.get("hooks") or "").strip().lower()
    special_fee = dynamic_fee or str(row.get("poolStatus") or "").lower() in {
        "dynamic_fee",
        "special_fee",
        "nonzero_hook",
    }
    usable = (
        not abnormal
        and row.get("poolStatus") in (None, "active")
        and not special_fee
        and not (hooks and hooks != "0x" + "0" * 40)
    )
    liquidity_index = _float_or_none(row.get("liquidityIndex"))
    liquidity = _float_or_none(row.get("liquidity"))
    has_liquidity = bool(
        (liquidity_index is not None and liquidity_index > 0)
        or (liquidity is not None and liquidity > 0)
    )
    depth_status = str(row.get("depthStatus") or "").strip() or "liquidity-index"
    hop_recommendations: list[str] = []
    if row.get("isFirstHopRecommended"):
        hop_recommendations.append("yc_anchor_to_counter_highest_price")
    if row.get("isThirdHopRecommended"):
        hop_recommendations.append("zc_counter_to_anchor_lowest_price")
    recommended_ids = set(first_ids + third_ids)
    pool_role = "primary" if hop_recommendations else "backup" if pool_id in recommended_ids else ""
    token0_key_id = str(row.get("token0KeyId") or anchor_id)
    token1_key_id = str(row.get("token1KeyId") or candidate_id)
    token0_meta = token_map.get(token0_key_id, {})
    token1_meta = token_map.get(token1_key_id, {})
    return {
        "pool": pool_id,
        "poolId": pool_id,
        "routeText": f"{anchor_symbol} -> {candidate_symbol}",
        "poolStatus": row.get("poolStatus") or ("active" if usable else "abnormal"),
        "protocol": status.get("protocol") or row.get("poolManager") or "uniswap_v4",
        "poolManager": row.get("poolManager") or "",
        "poolKeyJson": row.get("poolKeyJson") or "",
        "counterSymbol": candidate_symbol,
        "counterSymbolResolved": bool(candidate_meta.get("resolved")),
        "counterSymbolRaw": candidate_symbol,
        "counterName": candidate_meta.get("name") or "",
        "counterAddress": candidate_address,
        "token0": row.get("token0") or anchor_address,
        "token1": row.get("token1") or candidate_address,
        "token0KeyId": token0_key_id,
        "token1KeyId": token1_key_id,
        "token0Symbol": token0_meta.get("symbol") or anchor_symbol,
        "token1Symbol": token1_meta.get("symbol") or candidate_symbol,
        "fee": fee,
        "feeLabel": "dynamic" if dynamic_fee else "special" if special_fee else str(fee) if fee is not None else "-",
        "dynamicFee": dynamic_fee,
        "specialFee": special_fee,
        "tickSpacing": _int_or_none(row.get("tickSpacing")),
        "hooks": hooks,
        "liquidityIndex": liquidity_index,
        "liquidity": liquidity,
        "hasLiquidity": has_liquidity,
        "usableForObservation": usable,
        "quality": "pass" if usable else "rejected",
        "rejectReasons": [] if usable else [row.get("reason") or row.get("poolStatus") or "not_usable"],
        "depthStatus": depth_status,
        "depthBand": {"level": depth_status, "label": depth_status},
        "isFirstHopRecommended": bool(hop_recommendations and "yc" in hop_recommendations[0]),
        "isThirdHopRecommended": bool(len(hop_recommendations) > 1 and "zc" in hop_recommendations[-1]),
        "hopRecommendation": hop_recommendations,
        "recommendedPoolRole": pool_role,
        "anchorTokenKeyId": anchor_id,
        "candidateTokenKeyId": candidate_id,
        "pairKeyId": row.get("pairKeyId") or "",
        "reason": row.get("reason") or "",
    }


def _candidate_manager_summary(
    *,
    data: dict[str, Any],
    status: dict[str, Any],
    anchor_ids: list[str],
    groups: list[dict[str, Any]],
) -> dict[str, Any]:
    full_pool_count = _int_or_none(status.get("eligiblePoolCount"))
    row_pool_count = len(data.get("pools") or [])
    row_pair_count = len(data.get("poolPairs") or [])
    candidate_token_count = len(
        {
            str(pair.get("candidateTokenKeyId") or "")
            for pair in data.get("poolPairs") or []
            if isinstance(pair, dict) and pair.get("candidateTokenKeyId")
        }
    )
    shared_group_count = sum(1 for group in groups if group.get("ycZcSameSet"))
    all_pool_rows = [
        row
        for group in groups
        for bucket in ("sharedPools", "ycPools", "zcPools")
        for row in group.get(bucket) or []
        if isinstance(row, dict)
    ]
    return {
        "staticPoolCount": str(full_pool_count if full_pool_count is not None else row_pool_count),
        "confirmedPoolCount": str(row_pool_count),
        "staticPairCount": str(row_pair_count),
        "pairTableCount": str(row_pair_count),
        "anchorGroupCount": str(len(anchor_ids)),
        "ycPoolCount": str(sum(_int_or_none(group.get("ycPoolCount")) or 0 for group in groups)),
        "zcPoolCount": str(sum(_int_or_none(group.get("zcPoolCount")) or 0 for group in groups)),
        "ycTokenCount": str(sum(_int_or_none(group.get("ycTokenCount")) or 0 for group in groups)),
        "zcTokenCount": str(sum(_int_or_none(group.get("zcTokenCount")) or 0 for group in groups)),
        "candidateTokenCount": str(candidate_token_count),
        "ycZcSameSetGroupCount": str(shared_group_count),
        "liquidPoolCount": str(sum(1 for row in all_pool_rows if row.get("hasLiquidity"))),
        "liquidPoolGroupCount": str(
            sum(
                1
                for group in groups
                if any(
                    row.get("hasLiquidity")
                    for bucket in ("sharedPools", "ycPools", "zcPools")
                    for row in group.get(bucket) or []
                    if isinstance(row, dict)
                )
            )
        ),
        "snapshotTruncated": bool(
            full_pool_count is not None and full_pool_count > row_pool_count
        ),
        "snapshotLimit": CANDIDATE_SNAPSHOT_LIMIT,
        "usdDepthRequired": False,
        "dataSource": "manager",
    }


def _candidate_row_comparison(
    *,
    chain_id: int,
    request_id: str | None,
) -> dict[str, Any]:
    legacy_anchor_scope = f"dex-arbitrage-{int(chain_id)}-uniswap-v4"
    try:
        envelope = ManagerApiClient.from_env().get_json(
            "/v1/candidate-edge-pools/compare",
            query={
                "chainId": int(chain_id),
                "sampleLimit": CANDIDATE_ROW_COMPARISON_SAMPLE_LIMIT,
                "legacyAnchorScope": legacy_anchor_scope,
            },
            request_id=request_id,
        )
    except ManagerApiClientError as exc:
        return {
            "ok": False,
            "source": "manager",
            "sampleLimit": CANDIDATE_ROW_COMPARISON_SAMPLE_LIMIT,
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
            "sampleLimit": CANDIDATE_ROW_COMPARISON_SAMPLE_LIMIT,
            "legacyAnchorScope": legacy_anchor_scope,
            "code": "MANAGER_API_INVALID_RESPONSE",
            "detail": "candidate compare response must contain an object data field",
            "readyForCutover": False,
        }
    return {
        "ok": True,
        "source": "manager",
        "sampleLimit": CANDIDATE_ROW_COMPARISON_SAMPLE_LIMIT,
        "legacyAnchorScope": legacy_anchor_scope,
        "managerEnvelopeVersion": envelope.get("version"),
        "managerChecksum": data.get("managerChecksum"),
        "readyForCutover": bool(data.get("readyForCutover")),
        "differencesTruncated": bool(data.get("differencesTruncated")),
        "unresolvedLegacyAnchors": (
            data.get("unresolvedLegacyAnchors")
            if isinstance(data.get("unresolvedLegacyAnchors"), list)
            else []
        ),
        "causeCounts": data.get("causeCounts") if isinstance(data.get("causeCounts"), dict) else {},
        "differenceCounts": {
            key: _comparison_summary(data.get(key))
            for key in ("anchorTokens", "poolPairs", "pools", "recommendations")
        },
        "sampleReadyForCutover": bool(data.get("readyForCutover"))
        and not bool(data.get("differencesTruncated")),
    }
