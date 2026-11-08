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
    SUBMISSION_FAILURE_CATEGORY_ORDER,
    SUBMISSION_SNAPSHOT_LIMIT,
    ManagerApiClientError,
    _int_or_none,
    _list_length,
    _manager_snapshot,
    _shadow_error,
    _shadow_gate,
)
from .manager_submission_execution_payload import (
    submission_clear_manager_payload,
    submission_detail_manager_payload,
    submission_execution_manager_payload,
)


def submission_manager_shadow_payload(
    legacy_payload: dict[str, Any],
    *,
    chain_id: int,
    request_id: str | None = None,
) -> dict[str, Any]:
    """Read review/broadcast shadows without changing submission page fields."""

    if (gate := _shadow_gate("submission")) is not None:
        return gate
    review = _review_manager_shadow(chain_id=chain_id, request_id=request_id)
    broadcast = _broadcast_manager_shadow(chain_id=chain_id, request_id=request_id)
    legacy_summary = (
        legacy_payload.get("summary")
        if isinstance(legacy_payload.get("summary"), dict)
        else {}
    )
    return {
        "ok": bool(review.get("ok")) and bool(broadcast.get("ok")),
        "partial": bool(review.get("ok")) != bool(broadcast.get("ok")),
        "source": "manager",
        "enabled": True,
        "legacyUnaffected": True,
        "snapshotLimit": SUBMISSION_SNAPSHOT_LIMIT,
        "review": review,
        "broadcast": broadcast,
        "legacySummaryCounts": {
            "attemptCount": _int_or_none(legacy_summary.get("attemptCount")),
            "successCount": _int_or_none(legacy_summary.get("successCount")),
            "failedCount": _int_or_none(legacy_summary.get("failedCount")),
            "blockedCount": _int_or_none(legacy_summary.get("blockedCount")),
            "deferredReviewCount": _int_or_none(legacy_summary.get("deferredReviewCount")),
        },
        "comparisonReadyForFieldSwitch": False,
        "comparisonReason": "Review and broadcast snapshots have no row-comparison endpoint yet; legacy submission groups remain the page authority.",
    }


def _manager_review_row_dto(row: dict[str, Any]) -> dict[str, Any]:
    signed_pnl = row.get("signedPnl") if isinstance(row.get("signedPnl"), dict) else {}
    run_arbitrage = (
        row.get("runArbitrage")
        if isinstance(row.get("runArbitrage"), dict)
        else {}
    )
    review_status = str(row.get("reviewStatus") or "")
    page_category = str(row.get("pageCategory") or review_status or "other")
    amount = signed_pnl.get("amount")
    symbol = str(signed_pnl.get("currency") or "")
    source = str(signed_pnl.get("source") or "manager_review")
    addresses = (
        row.get("pathAddresses")
        if isinstance(row.get("pathAddresses"), list)
        else []
    )
    symbols = (
        row.get("pathSymbols") if isinstance(row.get("pathSymbols"), list) else []
    )
    trusted = review_status == "completed" and amount is not None and amount > 0
    onchain_evidence = {
        "status": review_status,
        "trustedForExecution": trusted,
        "readyForTransactionPreflight": trusted,
        "executionTrust": "manager_signed_profit" if trusted else review_status,
        "blockNumber": run_arbitrage.get("blockNumber"),
        "routeIdentity": {
            "routeAddresses": addresses,
            "route_addresses": addresses,
        },
    }
    return {
        "id": row.get("loopKey"),
        "managerLoopKey": row.get("loopKey"),
        "loopKey": row.get("loopKey"),
        "pair": row.get("pathText"),
        "route_key": row.get("poolPathKey"),
        "pathText": row.get("pathText"),
        "pathSymbols": symbols,
        "pathAddresses": addresses,
        "direction": row.get("pathText"),
        "category": page_category,
        "review_category": page_category,
        "state": page_category,
        "status": review_status,
        "review_status": review_status,
        "page_category": page_category,
        "created_at": row.get("reviewedAt"),
        "observed_at": row.get("reviewedAt"),
        "updated_at": row.get("reviewedAt"),
        "signedPnl": signed_pnl,
        "selectedProfitAmount": amount,
        "selectedProfitAmountSymbol": symbol,
        "selectedProfitSource": source,
        "selectedProfitMissingReason": (
            review_status
            if amount is None
            else "no_native_signed_profit"
            if not signed_pnl
            else None
        ),
        "anchorTokenKeyId": row.get("anchorTokenKeyId"),
        "anchorAddress": row.get("anchorAddress"),
        "anchorSymbol": row.get("anchorSymbol"),
        "poolPathKey": row.get("poolPathKey"),
        "poolIds": row.get("poolIds") if isinstance(row.get("poolIds"), list) else [],
        "closedLoopVersion": _int_or_none(row.get("closedLoopVersion")),
        "closedLoopChecksum": row.get("closedLoopChecksum"),
        "candidateEdgeVersion": _int_or_none(row.get("candidateEdgeVersion")),
        "candidateEdgeChecksum": row.get("candidateEdgeChecksum"),
        "observationVersion": _int_or_none(row.get("observationVersion")),
        "observationChecksum": row.get("observationChecksum"),
        "poolScopeChecksum": row.get("poolScopeChecksum"),
        "reviewReason": row.get("reviewReason"),
        "reasons": [reason for reason in [row.get("reviewReason")] if reason],
        "error": row.get("reviewReason"),
        "review_summary": {
            "onchain_evidence": onchain_evidence,
            "route_identity": onchain_evidence["routeIdentity"],
            "plan": {
                "estimatedProfitAmount": amount,
                "profit_amount": amount,
                "finalDeltaAmount": amount,
                "inputSymbol": symbol,
                "finalOutputSymbol": symbol,
            },
            "costModel": {
                "netProfitAmount": amount,
                "netProfitAmountSymbol": symbol,
                "netProfitReferenceSource": source,
                "netProfitAmountMissingReason": (
                    None if amount is not None else row.get("reviewReason")
                ),
            },
        },
        "staticPreview": row.get("staticPreview"),
        "runArbitrage": run_arbitrage,
        "managerReview": row,
    }


def review_manager_page_payload(
    *,
    chain_id: int | None,
    mode: str,
    category: str | None,
    status: str | None,
    outcome: str | None,
    page: int,
    page_size: int,
    chain_selection: dict[str, Any] | None,
    request_id: str | None = None,
) -> dict[str, Any]:
    """Read one manager-owned review page as the submission UI DTO."""

    if chain_id is None:
        return _shadow_error(
            "MANAGER_CHAIN_ID_REQUIRED",
            "review page read requires a selected chain id",
            data_source="manager",
        )
    safe_page = max(1, int(page))
    safe_page_size = max(1, min(100, int(page_size)))
    offset = (safe_page - 1) * safe_page_size
    query: dict[str, object] = {
        "chainId": int(chain_id),
        "offset": offset,
        "limit": safe_page_size,
    }
    normalized_category = str(category or "").strip().lower() or None
    if normalized_category and normalized_category != "all":
        query["category"] = normalized_category
    normalized_status = str(status or "").strip().lower() or None
    if normalized_status:
        query["status"] = normalized_status
    normalized_outcome = str(outcome or "").strip().lower() or None
    if normalized_outcome:
        query["outcome"] = normalized_outcome
    snapshot = _manager_snapshot(
        "/v1/reviews",
        query=query,
        request_id=request_id,
        invalid_detail="review page response must contain an object data field",
    )
    if not snapshot.get("ok"):
        return {
            **snapshot,
            "enabled": True,
            "dataSource": "manager",
        }
    data = snapshot["data"]
    status_payload = data.get("status") if isinstance(data.get("status"), dict) else {}
    category_counts = (
        data.get("categoryCounts")
        if isinstance(data.get("categoryCounts"), dict)
        else {}
    )
    total = _int_or_none(data.get("filteredCount")) or 0
    total_pages = max(1, -(-total // safe_page_size))
    diagnostics: list[dict[str, Any]] = [
        row for row in (data.get("diagnostics") if isinstance(data.get("diagnostics"), list) else [])
        if isinstance(row, dict)
    ]
    diagnostics_by_loop: dict[str, list[dict[str, Any]]] = {}
    for diagnostic in diagnostics:
        loop_key = str(diagnostic.get("loopKey") or "")
        diagnostics_by_loop.setdefault(loop_key, []).append(diagnostic)
    rows = []
    for row in data.get("reviews") if isinstance(data.get("reviews"), list) else []:
        if not isinstance(row, dict):
            continue
        dto = _manager_review_row_dto(row)
        loop_key = str(row.get("loopKey") or "")
        loop_diagnostics = diagnostics_by_loop.get(loop_key, [])
        dto["diagnostics"] = loop_diagnostics
        dto["reasons"] = list(
            dict.fromkeys(
                [
                    *(reason for reason in dto["reasons"] if reason),
                    *(
                        diag.get("message")
                        for diag in loop_diagnostics
                        if diag.get("message")
                    ),
                    *(
                        f"{diag.get('diagnosticType')}:{diag.get('diagnosticCode')}"
                        for diag in loop_diagnostics
                        if diag.get("diagnosticCode")
                    ),
                ]
            )
        )
        dto["network"] = status_payload.get("network") or "robinhood"
        rows.append(dto)
    ready = _int_or_none(category_counts.get("readyNotSubmitted")) or 0
    confirmed_no_profit = _int_or_none(category_counts.get("confirmedNoProfit")) or 0
    review_failed = _int_or_none(category_counts.get("reviewFailed")) or 0
    data_incomplete = _int_or_none(category_counts.get("dataIncomplete")) or 0
    other = _int_or_none(category_counts.get("other")) or 0
    return {
        "ok": True,
        "source": "manager",
        "dataSource": "manager",
        "mode": mode,
        "recordKind": "review",
        "review_category": normalized_category or "all",
        "managerEnvelopeVersion": snapshot.get("managerEnvelopeVersion"),
        "summary": {
            "attemptCount": total,
            "pageAttemptCount": len(rows),
            "successCount": _int_or_none(category_counts.get("completed")) or 0,
            "failedCount": review_failed,
            "systemErrorCount": review_failed,
            "businessBlockedCount": confirmed_no_profit,
            "blockedCount": confirmed_no_profit + ready,
            "confirmedNoProfitCount": confirmed_no_profit,
            "reviewFailedCount": review_failed,
            "readyNotSubmittedCount": ready,
            "dataIncompleteCount": data_incomplete,
            "otherCount": other,
        },
        "categoryCounts": {
            "total": _int_or_none(category_counts.get("total")) or 0,
            "completed": _int_or_none(category_counts.get("completed")) or 0,
            "data_incomplete": data_incomplete,
            "not_executable": _int_or_none(category_counts.get("notExecutable")) or 0,
            "failed": _int_or_none(category_counts.get("failed")) or 0,
            "ready_not_submitted": ready,
            "confirmed_no_profit": confirmed_no_profit,
            "review_failed": review_failed,
            "other": other,
        },
        "groups": {},
        "attempts": rows,
        "pagination": {
            "page": safe_page,
            "page_size": safe_page_size,
            "total": total,
            "total_pages": total_pages,
        },
        "chain_selection": chain_selection or {},
        "managerStatus": {
            "enabled": bool(status_payload.get("enabled")),
            "network": status_payload.get("network"),
            "chainId": _int_or_none(status_payload.get("chainId")),
            "protocol": status_payload.get("protocol"),
            "scopeKey": status_payload.get("scopeKey"),
            "reviewVersion": status_payload.get("reviewVersion"),
            "reviewChecksum": status_payload.get("materializedChecksum"),
            "closedLoopVersion": status_payload.get("closedLoopVersion"),
            "closedLoopChecksum": status_payload.get("closedLoopChecksum"),
            "candidateEdgeVersion": status_payload.get("candidateEdgeVersion"),
            "candidateEdgeChecksum": status_payload.get("candidateEdgeChecksum"),
            "observationVersion": status_payload.get("observationVersion"),
            "observationChecksum": status_payload.get("observationChecksum"),
            "poolScopeChecksum": status_payload.get("poolScopeChecksum"),
            "loopCount": _int_or_none(status_payload.get("loopCount")),
            "reviewedCount": _int_or_none(status_payload.get("reviewedCount")),
            "completedCount": _int_or_none(status_payload.get("completedCount")),
            "dataIncompleteCount": _int_or_none(
                status_payload.get("dataIncompleteCount")
            ),
            "notExecutableCount": _int_or_none(
                status_payload.get("notExecutableCount")
            ),
            "failedCount": _int_or_none(status_payload.get("failedCount")),
            "broadcastRequestCount": _int_or_none(
                status_payload.get("broadcastRequestCount")
            ),
        },
        "managerSnapshotCount": len(rows),
        "comparisonReadyForFieldSwitch": True,
        "comparisonReason": "Review lists, categories, pagination, diagnostics and signed PnL are served by Review Manager through /v1/reviews.",
    }


def _manager_attempt_detail_dto(
    attempt: dict[str, Any] | None,
    *,
    requested_attempt_id: str,
) -> dict[str, Any] | None:
    if not isinstance(attempt, dict):
        return None
    signed_pnl = (
        attempt.get("signedPnl") if isinstance(attempt.get("signedPnl"), dict) else {}
    )
    return {
        "id": requested_attempt_id,
        "managerAttemptId": attempt.get("attemptId"),
        "network": attempt.get("network"),
        "pair": attempt.get("pathText"),
        "route_key": attempt.get("poolPathKey"),
        "pathText": attempt.get("pathText"),
        "pathSymbols": attempt.get("pathSymbols"),
        "pathAddresses": attempt.get("pathAddresses"),
        "direction": attempt.get("pathText"),
        "status": attempt.get("status"),
        "review_category": attempt.get("status"),
        "signedPnl": signed_pnl,
        "anchorTokenKeyId": attempt.get("anchorTokenKeyId"),
        "loopKey": attempt.get("loopKey"),
        "closedLoopVersion": attempt.get("closedLoopVersion"),
        "closedLoopChecksum": attempt.get("closedLoopChecksum"),
        "reviewVersion": attempt.get("reviewVersion"),
        "reviewChecksum": attempt.get("reviewChecksum"),
        "poolPathKey": attempt.get("poolPathKey"),
        "poolIds": attempt.get("poolIds"),
        "signerAddress": attempt.get("signerAddress"),
        "nonce": attempt.get("nonce"),
        "gasLimit": attempt.get("gasLimit"),
        "gasPriceWei": attempt.get("gasPriceWei"),
        "maxFeePerGasWei": attempt.get("maxFeePerGasWei"),
        "maxPriorityFeePerGasWei": attempt.get("maxPriorityFeePerGasWei"),
        "rpcUrl": attempt.get("rpcUrl"),
        "rpcName": attempt.get("rpcName"),
        "tx_hash": attempt.get("txHash"),
        "transaction_hash": attempt.get("txHash"),
        "status_reason": attempt.get("statusReason"),
        "receipt": attempt.get("receipt"),
        "evidence": attempt.get("evidence"),
        "created_at": attempt.get("createdAt"),
        "updated_at": attempt.get("updatedAt"),
        "terminal_at": attempt.get("terminalAt"),
    }


def _manager_failure_category(
    attempt: dict[str, Any],
    diagnostics: list[dict[str, Any]],
) -> str:
    """Project manager failure evidence into the existing UI tag buckets."""

    parts: list[str] = [
        str(attempt.get("statusReason") or ""),
        str(attempt.get("evidence") or ""),
        str(attempt.get("receipt") or ""),
    ]
    for diagnostic in diagnostics:
        parts.extend(
            [
                str(diagnostic.get("diagnosticType") or ""),
                str(diagnostic.get("diagnosticCode") or ""),
                str(diagnostic.get("message") or ""),
                str(diagnostic.get("payload") or ""),
            ]
        )
    text = " ".join(parts).lower()

    if any(
        marker in text
        for marker in (
            "rpc_busy",
            "rpc busy",
            "deferred",
            "cooldown",
            "rate_limit",
            "rate limit",
        )
    ):
        return "deferred"
    if any(
        marker in text
        for marker in (
            "panic",
            "typeerror",
            "valueerror",
            "assertionerror",
            "nullpointer",
            "code_error",
            "encoding_error",
        )
    ):
        return "code"
    if any(
        marker in text
        for marker in (
            "swapnotresolved",
            "swap_not_resolved",
            "quote_failed",
            "quoter_",
            "requires_live_quoter",
            "not_live_verified",
        )
    ):
        return "quote"
    if any(
        marker in text
        for marker in (
            "blocked",
            "not_executable",
            "no_profit",
            "non_positive",
            "not_enough_liquidity",
            "pool_not_initialized",
            "unsupported_route",
        )
    ):
        return "blocked"
    if any(
        marker in text
        for marker in (
            "rpc_unavailable",
            "rpc_error",
            "rpc unavailable",
            "rpc request",
            "rpc timeout",
            "rpc_timeout",
            "nonce",
        )
    ):
        return "rpc"
    if any(
        marker in text
        for marker in (
            "broadcast",
            "sendrawtransaction",
            "replacement_underpriced",
        )
    ):
        return "broadcast"
    if any(
        marker in text
        for marker in (
            "submission",
            "submit_",
            "order_failed",
        )
    ):
        return "submission"
    if any(
        marker in text
        for marker in (
            "execution",
            "reverted",
            "revert",
            "receipt",
            "settlement",
            "confirmed_failed",
        )
    ):
        return "execution"
    return "other"


def _manager_attempt_overview_dto(
    attempt: dict[str, Any],
    *,
    diagnostics: list[dict[str, Any]],
    network: object,
) -> dict[str, Any]:
    attempt_id = str(attempt.get("attemptId") or "")
    dto = _manager_attempt_detail_dto(
        attempt,
        requested_attempt_id=attempt_id,
    )
    if dto is None:
        return {}
    failure_category = (
        _manager_failure_category(attempt, diagnostics)
        if attempt.get("status") == "failed"
        else None
    )
    reasons = list(
        dict.fromkeys(
            [
                *(reason for reason in [attempt.get("statusReason")] if reason),
                *(
                    diagnostic.get("message")
                    for diagnostic in diagnostics
                    if diagnostic.get("message")
                ),
                *(
                    f"{diagnostic.get('diagnosticType')}:{diagnostic.get('diagnosticCode')}"
                    for diagnostic in diagnostics
                    if diagnostic.get("diagnosticCode")
                ),
            ]
        )
    )
    dto.update(
        {
            "network": network,
            "state": attempt.get("status"),
            "review_category": failure_category or attempt.get("status"),
            "failureCategory": failure_category,
            "reason": attempt.get("statusReason"),
            "reasons": reasons,
            "diagnostics": diagnostics,
            "evidenceSource": "manager_broadcast",
            "managerAttempt": attempt,
        }
    )
    return dto


def _manager_unique_tx_stats(attempts: list[dict[str, Any]]) -> dict[str, int]:
    hashes = {
        str(attempt.get("tx_hash") or "").lower()
        for attempt in attempts
        if attempt.get("tx_hash")
    }
    return {
        "total": len(hashes),
        "success": len(
            {
                str(attempt.get("tx_hash") or "").lower()
                for attempt in attempts
                if attempt.get("status") == "success" and attempt.get("tx_hash")
            }
        ),
        "failed": len(
            {
                str(attempt.get("tx_hash") or "").lower()
                for attempt in attempts
                if attempt.get("status") == "failed" and attempt.get("tx_hash")
            }
        ),
        "blocked": 0,
    }


def _manager_route_framework(
    attempts: list[dict[str, Any]],
    *,
    manager_status: dict[str, Any],
) -> dict[str, Any]:
    top_attempt = attempts[0] if attempts else {}
    return {
        "routeFrameworkSource": "manager_broadcast",
        "routeFrameworkLabel": "管理器广播记录",
        "marketObservationSource": "manager",
        "marketObservationSourceStage": "broadcast",
        "decisionBasis": "Broadcast Manager 按复核版本消费的提交记录",
        "routeCount": manager_status.get("attemptCount") or 0,
        "candidateRouteCount": manager_status.get("attemptCount") or 0,
        "topRouteText": top_attempt.get("pathText") or "-",
        "topRouteStatus": top_attempt.get("status") or "-",
        "managerVersion": manager_status.get("broadcastVersion"),
        "managerChecksum": manager_status.get("broadcastChecksum"),
    }


def submission_overview_manager_payload(
    *,
    chain_id: int | None,
    mode: str,
    failure_category: str | None,
    page: int,
    page_size: int,
    chain_selection: dict[str, Any] | None,
    request_id: str | None = None,
) -> dict[str, Any]:
    """Read the submission overview or failure page from manager-owned state."""

    if chain_id is None:
        return _shadow_error(
            "MANAGER_CHAIN_ID_REQUIRED",
            "submission overview read requires a selected chain id",
            data_source="manager",
        )
    safe_page = max(1, int(page))
    safe_page_size = max(1, min(100, int(page_size)))
    normalized_category = str(failure_category or "").strip().lower()
    if normalized_category and normalized_category not in SUBMISSION_FAILURE_CATEGORY_ORDER:
        return {
            "ok": False,
            "source": "manager",
            "enabled": True,
            "dataSource": "manager",
            "code": "SUBMISSION_FAILURE_CATEGORY_UNKNOWN",
            "detail": f"unknown submission failure category: {normalized_category}",
            "availableCategories": list(SUBMISSION_FAILURE_CATEGORY_ORDER),
        }

    review_snapshot = _manager_snapshot(
        "/v1/reviews",
        query={"chainId": int(chain_id), "offset": 0, "limit": 1},
        request_id=request_id,
        invalid_detail="review status response must contain an object data field",
    )
    if not review_snapshot.get("ok"):
        return {
            **review_snapshot,
            "enabled": True,
            "dataSource": "manager",
        }

    failed_view = bool(normalized_category)
    offset = 0 if failed_view else (safe_page - 1) * safe_page_size
    broadcast_query: dict[str, object] = {
        "chainId": int(chain_id),
        "offset": offset,
        "limit": SUBMISSION_SNAPSHOT_LIMIT if failed_view else safe_page_size,
    }
    if failed_view:
        broadcast_query["status"] = "failed"
    broadcast_snapshot = _manager_snapshot(
        "/v1/broadcasts",
        query=broadcast_query,
        request_id=request_id,
        invalid_detail="broadcast overview response must contain an object data field",
    )
    if not broadcast_snapshot.get("ok"):
        return {
            **broadcast_snapshot,
            "enabled": True,
            "dataSource": "manager",
        }

    review_data = review_snapshot["data"]
    broadcast_data = broadcast_snapshot["data"]
    review_status = (
        review_data.get("status") if isinstance(review_data.get("status"), dict) else {}
    )
    broadcast_status = (
        broadcast_data.get("status")
        if isinstance(broadcast_data.get("status"), dict)
        else {}
    )
    version_pairs = (
        (
            "review",
            review_status.get("materializedChecksum"),
            broadcast_status.get("reviewChecksum"),
        ),
        (
            "closedLoop",
            review_status.get("closedLoopChecksum"),
            broadcast_status.get("closedLoopChecksum"),
        ),
    )
    for version_name, review_checksum, broadcast_checksum in version_pairs:
        if review_checksum and broadcast_checksum and review_checksum != broadcast_checksum:
            return {
                "ok": False,
                "source": "manager",
                "enabled": True,
                "dataSource": "manager",
                "code": "MANAGER_UPSTREAM_VERSION_MISMATCH",
                "detail": f"{version_name} checksum differs between review and broadcast manager state",
                "reviewChecksum": review_checksum,
                "broadcastChecksum": broadcast_checksum,
            }

    network = broadcast_status.get("network") or review_status.get("network")
    diagnostics = [
        row
        for row in (
            broadcast_data.get("diagnostics")
            if isinstance(broadcast_data.get("diagnostics"), list)
            else []
        )
        if isinstance(row, dict)
    ]
    diagnostics_by_attempt: dict[str, list[dict[str, Any]]] = {}
    for diagnostic in diagnostics:
        attempt_key = str(diagnostic.get("attemptId") or "")
        diagnostics_by_attempt.setdefault(attempt_key, []).append(diagnostic)

    all_attempts = [
        _manager_attempt_overview_dto(
            row,
            diagnostics=diagnostics_by_attempt.get(str(row.get("attemptId") or ""), []),
            network=network,
        )
        for row in (
            broadcast_data.get("attempts")
            if isinstance(broadcast_data.get("attempts"), list)
            else []
        )
        if isinstance(row, dict)
    ]
    all_attempts = [attempt for attempt in all_attempts if attempt]

    category_counts = {
        category: 0 for category in SUBMISSION_FAILURE_CATEGORY_ORDER if category != "all"
    }
    for attempt in all_attempts:
        category = str(attempt.get("failureCategory") or "")
        if category in category_counts:
            category_counts[category] += 1

    selected_attempts = all_attempts
    if failed_view:
        if normalized_category != "all":
            selected_attempts = [
                attempt
                for attempt in all_attempts
                if attempt.get("failureCategory") == normalized_category
            ]
        total = len(selected_attempts)
        start = (safe_page - 1) * safe_page_size
        page_attempts = selected_attempts[start : start + safe_page_size]
    else:
        page_attempts = all_attempts
        total = _int_or_none(broadcast_status.get("attemptCount")) or 0

    total_pages = max(1, -(-total // safe_page_size))
    review_categories = (
        review_data.get("categoryCounts")
        if isinstance(review_data.get("categoryCounts"), dict)
        else {}
    )
    not_executable = _int_or_none(review_status.get("notExecutableCount")) or 0
    confirmed_no_profit = (
        _int_or_none(review_categories.get("confirmedNoProfit")) or 0
    )
    broadcast_request_count = (
        _int_or_none(review_status.get("broadcastRequestCount")) or 0
    )
    attempt_count = _int_or_none(broadcast_status.get("attemptCount")) or 0
    pending_count = _int_or_none(broadcast_status.get("pendingCount")) or 0
    success_count = _int_or_none(broadcast_status.get("successCount")) or 0
    failed_count = _int_or_none(broadcast_status.get("failedCount")) or 0
    loop_count = _int_or_none(review_status.get("loopCount")) or 0
    reviewed_count = _int_or_none(review_status.get("reviewedCount")) or 0
    manager_status = {
        "enabled": bool(review_status.get("enabled"))
        and bool(broadcast_status.get("enabled")),
        "network": network,
        "chainId": _int_or_none(broadcast_status.get("chainId"))
        or _int_or_none(review_status.get("chainId")),
        "reviewVersion": review_status.get("reviewVersion"),
        "reviewChecksum": review_status.get("materializedChecksum"),
        "broadcastVersion": broadcast_status.get("broadcastVersion"),
        "broadcastChecksum": broadcast_status.get("materializedChecksum"),
        "closedLoopVersion": review_status.get("closedLoopVersion"),
        "closedLoopChecksum": review_status.get("closedLoopChecksum"),
        "loopCount": loop_count,
        "reviewedCount": reviewed_count,
        "attemptCount": attempt_count,
        "pendingCount": pending_count,
        "successCount": success_count,
        "failedCount": failed_count,
        "terminalCount": _int_or_none(broadcast_status.get("terminalCount")),
        "broadcastRequestCount": broadcast_request_count,
        "versionsConsistent": True,
    }
    snapshot_limited = failed_view and failed_count > len(all_attempts)
    loop_state_summary = {
        "available": True,
        "source": "manager",
        "closedLoopTotal": loop_count,
        "reviewSuccessLoops": _int_or_none(review_status.get("completedCount")) or 0,
        "reviewFailedLoops": _int_or_none(review_status.get("failedCount")) or 0,
        "reviewDeferredLoops": 0,
        "reviewMissingLoops": max(0, loop_count - reviewed_count),
        "broadcastLoops": attempt_count,
        "broadcastSuccessLoops": success_count,
        "broadcastFailedLoops": failed_count,
        "missingBroadcast": max(0, broadcast_request_count - attempt_count),
        "submissionSuccessTotal": success_count,
        "submissionFailedTotal": failed_count,
        "executionSuccessTotal": success_count,
        "executionFailedTotal": failed_count,
    }
    return {
        "ok": True,
        "source": "manager",
        "dataSource": "manager",
        "mode": mode,
        "recordKind": "submission",
        "error_category": normalized_category or None,
        "managerEnvelopeVersion": broadcast_snapshot.get("managerEnvelopeVersion"),
        "summary": {
            "attemptCount": total,
            "successCount": success_count,
            "failedCount": (
                total
                if failed_view and normalized_category not in {None, "all"}
                else failed_count
            ),
            "systemErrorCount": failed_count,
            "businessBlockedCount": confirmed_no_profit,
            "blockedCount": not_executable + confirmed_no_profit,
            "pendingCount": pending_count,
            "deferredCount": category_counts["deferred"],
            "uniqueTxHash": _manager_unique_tx_stats(page_attempts),
        },
        "categoryCounts": category_counts,
        "failureCategorySnapshotCount": len(all_attempts),
        "failureCategorySnapshotLimit": (
            SUBMISSION_SNAPSHOT_LIMIT if failed_view else None
        ),
        "failureCategorySnapshotLimited": snapshot_limited,
        "groups": {},
        "attempts": page_attempts,
        "pagination": {
            "page": safe_page,
            "page_size": safe_page_size,
            "total": total,
            "total_pages": total_pages,
        },
        "routeFramework": _manager_route_framework(
            page_attempts,
            manager_status=manager_status,
        ),
        "chain_selection": chain_selection or {},
        "loopStateSummary": loop_state_summary,
        "managerStatus": manager_status,
        "managerSnapshotCount": len(page_attempts),
        "comparisonReadyForFieldSwitch": True,
        "comparisonReason": (
            "Submission overview and failure views are served from Broadcast Manager "
            "attempts plus Review Manager status counts; no legacy submission loader is used."
        ),
    }


def _review_manager_shadow(
    *,
    chain_id: int,
    request_id: str | None,
) -> dict[str, Any]:
    snapshot = _manager_snapshot(
        "/v1/reviews",
        query={"chainId": int(chain_id), "limit": SUBMISSION_SNAPSHOT_LIMIT},
        request_id=request_id,
        invalid_detail="review manager response must contain an object data field",
    )
    if not snapshot.get("ok"):
        return snapshot
    data = snapshot["data"]
    status = data.get("status") if isinstance(data.get("status"), dict) else {}
    return {
        "ok": True,
        "managerEnvelopeVersion": snapshot.get("managerEnvelopeVersion"),
        "reviewVersion": status.get("reviewVersion"),
        "reviewChecksum": status.get("materializedChecksum"),
        "closedLoopVersion": status.get("closedLoopVersion"),
        "closedLoopChecksum": status.get("closedLoopChecksum"),
        "candidateEdgeVersion": status.get("candidateEdgeVersion"),
        "candidateEdgeChecksum": status.get("candidateEdgeChecksum"),
        "observationVersion": status.get("observationVersion"),
        "observationChecksum": status.get("observationChecksum"),
        "poolScopeChecksum": status.get("poolScopeChecksum"),
        "statusCounts": {
            "loopCount": _int_or_none(status.get("loopCount")),
            "reviewedCount": _int_or_none(status.get("reviewedCount")),
            "completedCount": _int_or_none(status.get("completedCount")),
            "dataIncompleteCount": _int_or_none(status.get("dataIncompleteCount")),
            "notExecutableCount": _int_or_none(status.get("notExecutableCount")),
            "failedCount": _int_or_none(status.get("failedCount")),
            "broadcastRequestCount": _int_or_none(status.get("broadcastRequestCount")),
        },
        "snapshotCounts": {
            "reviewSnapshotCount": _list_length(data.get("reviews")),
            "diagnosticSnapshotCount": _list_length(data.get("diagnostics")),
            "broadcastRequestSnapshotCount": _list_length(data.get("broadcastRequests")),
        },
        "snapshotLimit": SUBMISSION_SNAPSHOT_LIMIT,
    }


def _broadcast_manager_shadow(
    *,
    chain_id: int,
    request_id: str | None,
) -> dict[str, Any]:
    snapshot = _manager_snapshot(
        "/v1/broadcasts",
        query={"chainId": int(chain_id), "limit": SUBMISSION_SNAPSHOT_LIMIT},
        request_id=request_id,
        invalid_detail="broadcast manager response must contain an object data field",
    )
    if not snapshot.get("ok"):
        return snapshot
    data = snapshot["data"]
    status = data.get("status") if isinstance(data.get("status"), dict) else {}
    return {
        "ok": True,
        "managerEnvelopeVersion": snapshot.get("managerEnvelopeVersion"),
        "broadcastVersion": status.get("broadcastVersion"),
        "broadcastChecksum": status.get("materializedChecksum"),
        "reviewVersion": status.get("reviewVersion"),
        "reviewChecksum": status.get("reviewChecksum"),
        "closedLoopVersion": status.get("closedLoopVersion"),
        "closedLoopChecksum": status.get("closedLoopChecksum"),
        "statusCounts": {
            "requestCount": _int_or_none(status.get("requestCount")),
            "attemptCount": _int_or_none(status.get("attemptCount")),
            "pendingCount": _int_or_none(status.get("pendingCount")),
            "successCount": _int_or_none(status.get("successCount")),
            "failedCount": _int_or_none(status.get("failedCount")),
            "terminalCount": _int_or_none(status.get("terminalCount")),
        },
        "snapshotCounts": {
            "attemptSnapshotCount": _list_length(data.get("attempts")),
            "diagnosticSnapshotCount": _list_length(data.get("diagnostics")),
        },
        "snapshotLimit": SUBMISSION_SNAPSHOT_LIMIT,
    }
