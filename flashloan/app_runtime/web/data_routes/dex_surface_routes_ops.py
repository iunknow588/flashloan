from __future__ import annotations

import sqlite3

from flask import g, jsonify, request

from core.chain_selection import ChainSelectionError
from core.db.storage_common import database_url
from core.runtime.chain_runtime import request_chain_database_url
from dex.runtime.dex_closed_loop_review_queue import (
    SCAN_PRIORITY,
    USER_PRIORITY,
    normalize_closed_loop_group_key,
)
from dex.runtime.closed_loop_review_classifier import (
    is_review_deferred_row as _is_review_deferred_row,
    is_rpc_busy_text as _is_rpc_busy_text,
    review_row_kind as _review_row_kind,
    row_failure_reason_text as _row_failure_reason_text,
)
from web.data_routes import dex_surfaces as _root
from web.data_routes.dex_surface_response import mask_dex_surface_placeholder_symbols
from web.data_routes.dex_surface_routes_quotes import filter_quote_routes_with_complete_v4_pool_identity
from web.data_routes.manager_page_compare import (
    SUBMISSION_FAILURE_CATEGORY_ORDER,
    broadcast_failure_manager_payload,
    candidate_edge_refresh_manager_payload,
    candidate_manager_page_payload,
    manager_compare_requested,
    submission_clear_manager_payload,
    submission_detail_manager_payload,
    submission_execution_manager_payload,
    submission_overview_manager_payload,
    review_manager_page_payload,
)

def _summarize_rust_workers(payload):
    workers = payload.get("workers") if isinstance(payload.get("workers"), list) else []
    compact = []
    for worker in workers:
        if not isinstance(worker, dict):
            continue
        result = worker.get("last_result") if isinstance(worker.get("last_result"), dict) else {}
        broadcast = result.get("broadcast") if isinstance(result.get("broadcast"), dict) else {}
        compact.append(
            {
                "group_key": worker.get("group_key"),
                "state": worker.get("state"),
                "phase": worker.get("phase"),
                "updated_at": worker.get("updated_at"),
                "status": result.get("status"),
                "error": result.get("error"),
                "error_kind": result.get("errorKind"),
                "profitable": bool(result.get("profitable_amount_found")),
                "broadcast": bool(broadcast),
                "tx_hash": (
                    broadcast.get("txHash")
                    or broadcast.get("transactionHash")
                    or broadcast.get("transaction_hash")
                    or ""
                ),
                "broadcast_failure_count": worker.get("broadcast_failure_count"),
                "last_broadcast_failure_at": worker.get("last_broadcast_failure_at"),
                "broadcast_failure_recent": worker.get("broadcast_failure_recent") or [],
            }
        )
    payload = dict(payload)
    payload["workers"] = compact
    return payload


def _write_request_contract_error(body: dict) -> tuple[dict, int] | None:
    request_id = str(request.headers.get("X-Request-Id") or "").strip()
    idempotency_key = str(request.headers.get("Idempotency-Key") or "").strip()
    if not request_id:
        return {
            "ok": False,
            "error": "request_id_required",
            "detail": "X-Request-Id is required for write requests",
        }, 400
    if not idempotency_key:
        return {
            "ok": False,
            "error": "idempotency_key_required",
            "detail": "Idempotency-Key is required for write requests",
            "requestId": request_id,
        }, 400
    dry_run = body.get("dryRun")
    if not isinstance(dry_run, bool):
        return {
            "ok": False,
            "error": "dry_run_boolean_required",
            "detail": "dryRun must be an explicit Boolean",
            "requestId": request_id,
            "idempotencyKey": idempotency_key,
        }, 400
    return None


def _closed_loop_transition_status(error: str) -> int:
    if error in {"closed_loop_group_not_found", "execution_task_not_found"}:
        return 404
    if error in {
        "closed_loop_group_busy",
        "execution_task_not_waiting",
        "broadcast_executor_unavailable",
    }:
        return 409
    if error in {"closed_loop_route_unsupported", "chain_id_required"}:
        return 400
    return 503


def _closed_loop_groups_for_refresh(*, chain_id, protocols):
    overview = _root.load_observation_overview_for_scope(
        chain_id=chain_id,
        protocols=protocols,
    )
    if not isinstance(overview, dict) or not overview.get("exists"):
        return []
    groups_payload = _root.closed_loop_route_groups_payload(
        overview,
        include_routes=False,
    )
    return groups_payload.get("groups") or []


def _match_closed_loop_group(groups, group_key):
    normalized = normalize_closed_loop_group_key(group_key)
    for group in groups:
        token = group.get("flashLoanToken") or group.get("startToken") or {}
        candidates = [
            token.get("address"),
            token.get("symbol"),
            group.get("startToken", {}).get("address"),
            group.get("startToken", {}).get("symbol"),
        ]
        if any(normalize_closed_loop_group_key(value) == normalized for value in candidates):
            return group
    return None


_SUBMISSION_FAILURE_STATES = {
    "quote_failed",
    "submission_failed",
    "submit_failed",
    "broadcast_failed",
    "broadcast_error",
    "broadcast_disabled",
    "execution_failed",
    "confirmed_failed",
    "settlement_failed",
    "order_failed",
    "rpc_verification_failed",
    "rpc_verification_timeout",
    "live_quote_failed",
    "failed",
    "error",
}

_SUBMISSION_RECORD_CATEGORIES = ("execution_failed", "execution_success")
_REVIEW_RECORD_CATEGORIES = ("not_executable",)
_REVIEW_CATEGORY_ORDER = (
    "all",
    "data_incomplete",
    "confirmed_no_profit",
    "review_failed",
    "ready_not_submitted",
    "other",
)

def _deferred_submission_rows(rows: list[dict]) -> list[dict]:
    return [row for row in rows if isinstance(row, dict) and _is_review_deferred_row(row)]


def _submission_failed_rows(attempt_rows: list[dict]) -> list[dict]:
    """Return only broadcast/submission failure records, not review rejects."""
    rows = []
    for row in attempt_rows:
        if not isinstance(row, dict):
            continue
        category = str(
            row.get("review_category")
            or row.get("category")
            or ""
        ).strip().lower()
        state = str(
            row.get("state")
            or row.get("status")
            or row.get("submission_status")
            or ""
        ).strip().lower()
        if category == "execution_failed" or state in {
            "submission_failed",
            "submit_failed",
            "order_failed",
            "broadcast_failed",
            "broadcast_error",
            "execution_failed",
            "confirmed_failed",
            "settlement_failed",
            "failed",
            "error",
        }:
            rows.append(row)
    return rows


def _review_submission_rows(attempt_rows: list[dict]) -> list[dict]:
    """Return review-stage records only (not_executable family)."""
    rows = []
    for row in attempt_rows:
        if not isinstance(row, dict):
            continue
        category = str(
            row.get("review_category")
            or row.get("category")
            or ""
        ).strip().lower()
        state = str(
            row.get("state")
            or row.get("status")
            or row.get("submission_status")
            or ""
        ).strip().lower()
        if category == "not_executable" or state in {
            "quote_required",
            "quote_failed",
            "live_quote_failed",
            "not_live_verified",
            "ready_for_transaction_development",
            "ready_not_submitted",
            "ready_to_submit",
            "checks_passed_order_disabled",
            "limit_order_ready_not_submitted",
            "limit_order_ready_to_submit",
            "not_profitable",
            "checks_failed",
        }:
            rows.append(row)
    return rows


def _review_success_rows(attempt_rows: list[dict]) -> list[dict]:
    """Return completed review records, excluding review failures."""
    return [
        row
        for row in _review_submission_rows(attempt_rows)
        if isinstance(row, dict) and _review_row_kind(row) not in {"review_failed", "rpc_busy_deferred"}
    ]


def _review_failed_rows(attempt_rows: list[dict]) -> list[dict]:
    """Return review-stage records where review did not complete."""
    return [
        row
        for row in _review_submission_rows(attempt_rows)
        if isinstance(row, dict) and _review_row_kind(row) == "review_failed"
    ]


def _row_failure_status(row: dict) -> str:
    quote = row.get("quote") if isinstance(row.get("quote"), dict) else {}
    evidence_status = str(
        (quote.get("live_onchain_evidence") or {}).get("status")
        or (quote.get("quote_status") or "")
        or ""
    ).strip().lower()
    row_status = str(
        row.get("review_category")
        or row.get("category")
        or row.get("state")
        or row.get("status")
        or (row.get("precheck") or {}).get("status")
        or ""
    ).strip().lower()
    if row_status in _SUBMISSION_FAILURE_STATES:
        return row_status
    if evidence_status:
        return evidence_status
    return row_status


def _is_rpc_system_failure_text(text: str) -> bool:
    lowered = text.lower()
    rpc_markers = (
        "rpc_verification_failed",
        "rpc_verification_timeout",
        "rpc error",
        "rpc timeout",
        "rpc endpoint",
        "rpc unavailable",
        "rpc not available",
        "cannot reach rpc",
        "web3 provider",
        "web3 connection",
        "provider disconnected",
        "provider error",
        "provider timeout",
        "endpoint refused",
        "endpoint unreachable",
        "connection refused",
        "connection reset",
        "connection timeout",
        "connection error",
        "socket",
        "ssl",
        "certificate",
        "unavailable",
        "timeout",
        "timed out",
        "not available",
        "cannot be reached",
        "ethereum node",
        "blockchain node",
        "node connection",
        "node unavailable",
        "node timeout",
    )
    if any(marker in lowered for marker in rpc_markers):
        return True
    return False


def _is_code_system_failure_text(text: str) -> bool:
    lowered = text.lower()
    code_markers = (
        "syntaxerror",
        "typeerror",
        "referenceerror",
        "unexpected token",
        "module_not_found",
        "cannot find module",
        "enoent",
        "eacces",
        "stack trace",
        "script error",
        "child_process",
        "stderr",
        "exit code 1",
    )
    return any(marker in lowered for marker in code_markers)


def _is_business_failure_text(text: str) -> bool:
    compact = text.replace("_", "").replace("(", "").replace(")", "").lower()
    markers = (
        "arbitragenotprofitable",
        "noprofitable",
        "notprofitable",
        "unprofitable",
        "not profitable",
        "no_profitable_route",
        "route_unprofitable",
        "not_profitable_after_costs",
        "no positive pool-token profit",
        "poolnotinitialized",
        "pool not initialized",
        "0x486aa307",
        "swapnotresolved",
        "swap not resolved",
        "norevertdata",
        "no revert data",
        "notenoughliquidity",
        "not enough liquidity",
        "insufficient liquidity",
        "no lp liquidity",
        "pool manager live liquidity is zero",
        "best amount is not confirmed",
        "cannot enter transaction preflight",
        "static_reference_pending_live_review",
        "requires_live_quoter",
        "requires live quoter",
    )
    return any(marker in compact or marker in text.lower() for marker in markers)


def _is_system_failure_row(row: dict) -> bool:
    """Distinguish genuine system errors from business-level non-execution."""
    if _is_review_deferred_row(row):
        return False
    status = _row_failure_status(row)
    text = _row_failure_reason_text(row)
    if status in {"rpc_verification_timeout", "rpc_verification_failed", "broadcast_failed", "broadcast_error", "submission_failed", "submit_failed", "execution_failed", "confirmed_failed", "settlement_failed", "order_failed"}:
        return True
    if _is_code_system_failure_text(text):
        return True
    if _is_rpc_system_failure_text(text):
        return True
    if _is_business_failure_text(text):
        return False
    # Generic quote failures without a known business blocker are still shown
    # separately, but they should not count as system errors.
    if status in {"live_quote_failed", "quote_failed", "not_live_verified", "requires_live_quoter", "none"}:
        return False
    # Generic failed/error states that are not known business reverts are safer
    # to keep in the system-error bucket while we refine on-chain classifiers.
    return status in {"failed", "error"}


def _system_failed_submission_rows(failed_rows: list[dict]) -> list[dict]:
    return [row for row in failed_rows if isinstance(row, dict) and _is_system_failure_row(row)]


def _business_blocked_submission_rows(failed_rows: list[dict]) -> list[dict]:
    return [
        row
        for row in failed_rows
        if isinstance(row, dict)
        and not _is_review_deferred_row(row)
        and not _is_system_failure_row(row)
    ]


def _failed_submission_rows(attempt_rows: list[dict]) -> list[dict]:
    """Return only rows that failed before producing a usable chain tx hash.

    The main submission review page intentionally keeps rows that produced a
    transaction hash. This helper powers a separate page for quote/broadcast
    failures so those attempts are not silently hidden.
    """
    failure_states = {
        "quote_failed",
        "submission_failed",
        "submit_failed",
        "broadcast_failed",
        "broadcast_error",
        "broadcast_disabled",
        "execution_failed",
        "confirmed_failed",
        "settlement_failed",
        "order_failed",
        "quote_failed",
        "rpc_verification_failed",
        "rpc_verification_timeout",
        "live_quote_failed",
        "quote_required",
        "ready_for_transaction_development",
        "not_executable",
        "failed",
        "error",
    }
    rows = []
    for row in attempt_rows:
        quote = row.get("quote") if isinstance(row.get("quote"), dict) else {}
        evidence_status = str(
            (quote.get("live_onchain_evidence") or {}).get("status")
            or (quote.get("quote_status") or "")
            or ""
        ).strip().lower()
        row_status = str(
            row.get("review_category")
            or row.get("category")
            or row.get("state")
            or row.get("status")
            or (row.get("precheck") or {}).get("status")
            or ""
        ).strip().lower()
        if row_status not in failure_states and evidence_status not in failure_states:
            continue
        tx_hash = str(
            row.get("tx_hash")
            or row.get("transaction_hash")
            or quote.get("tx_hash")
            or quote.get("transaction_hash")
            or ""
        ).strip()
        auto_execute = row.get("auto_execute") if isinstance(row.get("auto_execute"), dict) else {}
        auto_result = auto_execute.get("result") if isinstance(auto_execute.get("result"), dict) else {}
        if not tx_hash:
            tx_hash = str(
                auto_result.get("txHash")
                or auto_result.get("transactionHash")
                or auto_result.get("transaction_hash")
                or ""
            ).strip()
        if tx_hash:
            continue
        rows.append(row)
    return rows


def _attempt_tx_hash(row: dict) -> str:
    """Return the chain tx hash recorded on an attempt row, if any."""
    quote = row.get("quote") if isinstance(row.get("quote"), dict) else {}
    auto_execute = row.get("auto_execute") if isinstance(row.get("auto_execute"), dict) else {}
    auto_result = auto_execute.get("result") if isinstance(auto_execute.get("result"), dict) else {}
    return str(
        row.get("tx_hash")
        or row.get("transaction_hash")
        or quote.get("tx_hash")
        or quote.get("transaction_hash")
        or auto_result.get("txHash")
        or auto_result.get("transactionHash")
        or auto_result.get("transaction_hash")
        or ""
    ).strip()


def _success_submission_rows(attempt_rows: list[dict]) -> list[dict]:
    """Return only on-chain successful records; every success row needs a tx hash."""

    success_states = {
        "submitted_success",
        "broadcast_success",
        "broadcasted",
        "submission_success",
        "broadcast_complete",
        "execution_success",
        "confirmed_success",
        "settled",
        "executed",
        "success",
    }
    rows = []
    for row in attempt_rows:
        if not isinstance(row, dict):
            continue
        status = str(
            row.get("review_category")
            or row.get("category")
            or row.get("status")
            or row.get("state")
            or row.get("submission_status")
            or ""
        ).strip().lower()
        if status in success_states or status.endswith("_success"):
            if _attempt_tx_hash(row):
                rows.append(row)
            continue
        quote = row.get("quote") if isinstance(row.get("quote"), dict) else {}
        auto_execute = row.get("auto_execute") if isinstance(row.get("auto_execute"), dict) else {}
        auto_result = auto_execute.get("result") if isinstance(auto_execute.get("result"), dict) else {}
        if _attempt_tx_hash(row) and not _is_system_failure_row(row):
            rows.append(row)
    return rows


def _row_cost_model(row: dict) -> dict:
    quote = row.get("quote") if isinstance(row.get("quote"), dict) else {}
    evidence = (
        quote.get("live_onchain_evidence")
        if isinstance(quote.get("live_onchain_evidence"), dict)
        else {}
    )
    return evidence.get("costModel") if isinstance(evidence.get("costModel"), dict) else {}


def _submission_tx_number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if parsed != parsed or parsed in (float("inf"), float("-inf")):
        return None
    return parsed


def _submission_tx_outcome(row: dict) -> str:
    """Classify a row as success/failed/blocked without legacy tx fallback."""
    raw_status = str(
        row.get("review_category")
        or row.get("category")
        or row.get("status")
        or row.get("state")
        or row.get("submission_status")
        or ""
    ).strip().lower()
    if raw_status in {
        "submitted_success",
        "broadcast_success",
        "broadcasted",
        "submission_success",
        "broadcast_complete",
        "execution_success",
        "confirmed_success",
        "settled",
        "executed",
        "success",
    } or raw_status.endswith("_success"):
        return "success"
    if _is_system_failure_row(row):
        return "failed"
    if raw_status in _SUBMISSION_FAILURE_STATES or raw_status in {
        "quote_required",
        "ready_for_transaction_development",
        "not_executable",
    }:
        return "blocked"
    return "blocked"


def _submission_tx_unique_stats(rows: list[dict]) -> dict:
    """Count and aggregate by unique tx hash for success/failed/blocked rows."""
    tx_rows: dict[str, dict] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        tx_hash = _attempt_tx_hash(row)
        if not tx_hash:
            continue
        key = tx_hash.lower()
        if key in tx_rows:
            continue
        outcome = _submission_tx_outcome(row)
        tx_rows[key] = {"outcome": outcome, "row": row}

    counts = {"total": len(tx_rows), "success": 0, "failed": 0, "blocked": 0}
    net_profit_by_symbol: dict[str, dict] = {}
    total_gas_usd = 0.0
    total_net_profit_usd = 0.0
    for entry in tx_rows.values():
        outcome = entry["outcome"]
        counts[outcome] += 1
        row = entry["row"]
        costs = _row_cost_model(row)
        symbol = str(
            costs.get("netProfitAmountSymbol")
            or costs.get("borrowInputSymbol")
            or row.get("pair", "").split("->", 1)[0].strip()
            or ""
        ).strip()
        profit = _submission_tx_number(costs.get("netProfitAmount"))
        if profit is not None and symbol:
            bucket = net_profit_by_symbol.setdefault(symbol, {"count": 0, "sum": 0.0})
            bucket["count"] += 1
            bucket["sum"] += profit
        gas_usd = _submission_tx_number(
            costs.get("gasUsd")
            or costs.get("gasCostUsd")
            or costs.get("gasReserveUsd")
        )
        if gas_usd is not None:
            total_gas_usd += gas_usd
        profit_usd = _submission_tx_number(
            costs.get("netProfitUsd")
            or costs.get("submissionNetProfitUsd")
            or costs.get("offchainNetProfitUsd")
        )
        if profit_usd is not None:
            total_net_profit_usd += profit_usd

    return {
        **counts,
        "netProfitBySymbol": {
            symbol: {"count": int(bucket["count"]), "sum": bucket["sum"]}
            for symbol, bucket in sorted(net_profit_by_symbol.items())
        },
        "gasUsd": total_gas_usd,
        "netProfitUsd": total_net_profit_usd,
    }


_FAILURE_CATEGORY_ORDER = (
    "all",
    "broadcast",
    "submission",
    "code",
    "rpc",
    "execution",
    "quote",
    "blocked",
    "deferred",
    "other",
)


def _failed_submission_category(row: dict) -> str:
    """Classify a failed row into the same tag buckets used by the UI."""
    if _is_review_deferred_row(row):
        return "deferred"
    status = _row_failure_status(row)
    quote = row.get("quote") if isinstance(row.get("quote"), dict) else {}
    reason_text = _row_failure_reason_text(row)
    lower_reason = reason_text.lower()
    lowered_status = status.lower()
    evidence_status = str(
        (quote.get("live_onchain_evidence") or {}).get("status")
        or (quote.get("live_onchain_evidence") or {}).get("executionTrust")
        or ""
    ).strip().lower()
    if _is_code_system_failure_text(_row_failure_reason_text(row)):
        return "code"
    broadcast = row.get("broadcast") if isinstance(row.get("broadcast"), dict) else {}
    auto_execute = row.get("auto_execute") if isinstance(row.get("auto_execute"), dict) else {}
    if broadcast.get("attempted") is True and broadcast.get("ok") is False:
        return "broadcast"
    if isinstance(auto_execute.get("result"), dict) and auto_execute.get("result").get(
        "failureKind"
    ):
        return "broadcast"
    if auto_execute.get("failureKind") or status in {
        "broadcast_failed",
        "broadcast_error",
        "broadcast_disabled",
    }:
        return "broadcast"
    if auto_execute.get("reason") == "broadcast_failed":
        return "broadcast"
    if (
        _is_business_failure_text(reason_text)
        or lowered_status in {
            "route_unprofitable",
            "not_profitable_after_costs",
            "no_profitable_route",
            "not_enough_liquidity",
            "pool_not_initialized",
            "pool_insufficient_for_profitable_execution",
        }
    ):
        return "blocked"
    if (
        _is_rpc_system_failure_text(reason_text)
        or lowered_status in {"rpc_verification_failed", "rpc_verification_timeout"}
        or "rpc" in evidence_status
    ):
        return "rpc"
    if status in {"broadcast_failed", "broadcast_error", "broadcast_disabled"} or "broadcast" in evidence_status:
        return "broadcast"
    if status in {"submission_failed", "submit_failed", "order_failed"}:
        return "submission"
    if status in {"execution_failed", "confirmed_failed", "settlement_failed"}:
        return "execution"
    if (
        lowered_status in {
            "live_quote_failed",
            "hop_1_live_quote_failed",
            "quote_failed",
            "quoter_revert",
            "quoter_unavailable",
            "quoter_insufficient_liquidity",
            "not_live_verified",
            "static_reference_pending_live_review",
            "static_reference_only",
            "static_live_quoter_estimate",
            "live_quote_reference_only",
            "requires_live_quoter",
            "not_all_hops_quoted",
            "route_not_closed",
        }
        or "quote_failed" in evidence_status
        or "hop_1_live_quote_failed" in evidence_status
        or "requires_live_quoter" in evidence_status
        or "previewbestpooldetailed unavailable" in lower_reason
        or "previewbestpool unavailable" in lower_reason
        or "quoter_unavailable" in lower_reason
        or "quoter_revert" in lower_reason
        or "hop 1" in lower_reason
    ):
        return "quote"
    if lowered_status in {
        "not_executable",
        "blocked",
        "unsupported_route",
        "quote_required",
        "ready_for_transaction_development",
        "static_reference_pending_live_review",
    }:
        if "quote" in lower_reason or "quoter" in lower_reason or "reverted" in lower_reason:
            return "quote"
        return "blocked"
    return "other"


def _merge_failed_submission_rows_into_payload(
    response_payload: dict,
    failed_rows: list[dict],
) -> dict:
    """Include failed quote/review rows on the main submission review page.

    The generic submission payload intentionally only returns records with a
    chain tx hash.  Our failure classifier also handles quote-stage timeout
    records that never reached broadcast, so those rows are merged back into
    the page instead of being silently hidden.  The failedCount metric counts
    genuine system/network errors; business-level rejects such as
    ArbitrageNotProfitable, PoolNotInitialized and SwapNotResolved are moved
    into businessBlockedCount rather than inflating the system error rate.
    """
    attempts = response_payload.get("attempts")
    if not isinstance(attempts, list):
        attempts = []
    def _attempt_key(row: dict) -> str:
        tx_hash = str(
            row.get("tx_hash")
            or row.get("transaction_hash")
            or (row.get("quote") or {}).get("tx_hash")
            or (row.get("quote") or {}).get("transaction_hash")
            or ""
        ).strip().lower()
        if tx_hash:
            return f"tx:{tx_hash}"
        pair = str(row.get("pair") or "").strip().lower()
        if isinstance(row.get("route_path"), list):
            pair = pair or " -> ".join(str(item) for item in row["route_path"]).strip().lower()
        ts = str(row.get("created_at") or row.get("observed_at") or row.get("updated_at") or "").strip()
        return f"fail:{pair}|{ts}"
    seen = set()
    for row in attempts:
        if isinstance(row, dict):
            seen.add(_attempt_key(row))
    added = 0
    for row in failed_rows:
        if not isinstance(row, dict):
            continue
        key = _attempt_key(row)
        if key and key in seen:
            continue
        seen.add(key)
        attempts.append(row)
        added += 1
    attempts.sort(
        key=lambda row: str(row.get("created_at") or row.get("observed_at") or row.get("updated_at") or ""),
        reverse=True,
    )
    response_payload["attempts"] = attempts
    summary = response_payload.setdefault("summary", {})
    deferred_rows = _deferred_submission_rows(failed_rows)
    non_deferred_rows = [row for row in failed_rows if row not in deferred_rows]
    system_failed_rows = _system_failed_submission_rows(non_deferred_rows)
    business_blocked_rows = _business_blocked_submission_rows(non_deferred_rows)
    existing_failed_count = int(summary.get("failedCount") or 0)
    existing_blocked_count = int(summary.get("blockedCount") or 0)
    existing_deferred_count = int(summary.get("deferredReviewCount") or 0)
    summary["failedCount"] = existing_failed_count + len(system_failed_rows)
    summary["systemErrorCount"] = summary["failedCount"]
    summary["businessBlockedCount"] = existing_blocked_count + len(business_blocked_rows)
    summary["blockedCount"] = summary["businessBlockedCount"]
    summary["deferredReviewCount"] = existing_deferred_count + len(deferred_rows)
    summary["attemptCount"] = len(attempts)
    summary["_mergedQuoteFailures"] = added
    return response_payload


def _dedupe_failed_rows_against_attempts(
    failed_rows: list[dict],
    attempts: list[dict],
    *,
    limit: int,
) -> list[dict]:
    """Keep one failure row per route unless a verified tx row already exists."""
    routes_with_tx = set()
    for row in attempts:
        if not isinstance(row, dict):
            continue
        if _attempt_tx_hash(row):
            routes_with_tx.add(_submission_route_key(row))
    filtered = [
        row
        for row in failed_rows
        if isinstance(row, dict) and _submission_route_key(row) not in routes_with_tx
    ]
    return _root.shared._dedupe_attempts_by_route_evidence(
        filtered,
        limit=max(1, int(limit)),
        dedupe_by_route_evidence=True,
    )


def _submission_route_key(row: dict) -> tuple[str, str]:
    network = str(row.get("network") or row.get("cow_network") or "").strip().lower()
    pair = str(row.get("pair") or row.get("symbol_pair") or row.get("route_key") or "").strip()
    if not pair:
        route_path = row.get("route_path")
        if isinstance(route_path, list):
            pair = " -> ".join(str(item).strip() for item in route_path if str(item).strip())
    return network, pair.upper()


def _submission_database_url() -> str | None:
    resolved = request_chain_database_url()
    if resolved:
        return resolved
    try:
        return _root.shared.panel_call("database_url")
    except Exception:
        return database_url()


def _closed_loop_runtime_state_summary(database_url: str | None = None) -> dict:
    from dex.runtime.closed_loop_object import ClosedLoopRuntimeMetrics

    try:
        metrics = ClosedLoopRuntimeMetrics.cached_for(
            database_url or _submission_database_url()
        )
        if metrics is None:
            return {"available": False, "error": "metrics_unavailable"}
        return metrics.summary()
    except Exception:
        return {"available": False, "error": "summary_unavailable"}


def _closed_loop_monitor_summary(
    *,
    database_url: str | None,
    networks: list[str],
    chain_ids: list[int],
) -> dict:
    """Build closed-loop health counters from runtime state.

    This helper answers the monitoring question per closed loop: did its newest
    review pass/fail, and did it ever reach a broadcast terminal state?
    """
    loop_state = _closed_loop_runtime_state_summary(database_url)
    if not database_url:
        return {
            "closedLoopTotal": 0,
            "reviewSuccessLoops": 0,
            "reviewFailedLoops": 0,
            "reviewDeferredLoops": 0,
            "reviewMissingLoops": 0,
            "broadcastLoops": 0,
            "broadcastSuccessLoops": 0,
            "broadcastFailedLoops": 0,
            "loopStateSummary": loop_state,
        }
    return {
        "closedLoopTotal": int(loop_state.get("total") or 0),
        "reviewSuccessLoops": int(loop_state.get("reviewSuccessLoops") or 0),
        "reviewFailedLoops": int(loop_state.get("reviewFailedLoops") or 0),
        "reviewDeferredLoops": int(loop_state.get("reviewDeferredLoops") or 0),
        "reviewMissingLoops": max(
            0,
            int(loop_state.get("total") or 0)
            - int(loop_state.get("reviewSuccessLoops") or 0)
            - int(loop_state.get("reviewFailedLoops") or 0)
            - int(loop_state.get("reviewDeferredLoops") or 0),
        ),
        "broadcastLoops": int(loop_state.get("broadcastLoops") or 0),
        "broadcastSuccessLoops": int(loop_state.get("broadcastSuccessLoops") or 0),
        "broadcastFailedLoops": int(loop_state.get("broadcastFailedLoops") or 0),
        "submissionSuccessTotal": int(loop_state.get("submissionSuccessTotal") or 0),
        "submissionFailedTotal": int(loop_state.get("submissionFailedTotal") or 0),
        "executionSuccessTotal": int(loop_state.get("executionSuccessTotal") or 0),
        "executionFailedTotal": int(loop_state.get("executionFailedTotal") or 0),
        "loopStateSummary": loop_state,
    }


def _submission_page_args() -> tuple[int, int]:
    page = _root.shared.request_int_arg("page", 1, minimum=1, maximum=100000)
    page_size = _root.shared.request_int_arg("page_size", 20, minimum=1, maximum=100)
    return page, page_size


def register_dex_scanner_routes(app) -> None:
    # D9.41: these scanner lifecycle endpoints are maintenance/diagnostic only.
    # Formal pages must read RuntimeCore/Manager state and dispatch through the
    # RuntimeCore command surface; do not reconnect UI controls to them.

    def _filter_scanner_payload(payload):
        if not isinstance(payload, dict):
            return payload
        return filter_quote_routes_with_complete_v4_pool_identity(payload)

    @app.get("/api/dex/opportunity-scanner/start")
    def dex_opportunity_scanner_start():
        try:
            chain_selection, selected_chain_id = _root.selected_chain_id_from_request()
        except ChainSelectionError as exc:
            return _root.shared._chain_selection_error_response(exc)
        protocols = _root.request_dex_protocols_arg()
        scanner = _root.get_dex_opportunity_scanner(chain_id=selected_chain_id)
        scanner.start(chain_id=selected_chain_id, protocols=protocols)
        payload = _root.dex_opportunity_snapshot(chain_id=selected_chain_id, protocols=protocols)
        payload = _filter_scanner_payload(payload)
        payload["chain_selection"] = chain_selection
        return jsonify(_root._attach_dex_strategy(payload))

    @app.get("/api/dex/opportunity-scanner/snapshot")
    def dex_opportunity_scanner_snapshot():
        try:
            chain_selection, selected_chain_id = _root.selected_chain_id_from_request()
        except ChainSelectionError as exc:
            return _root.shared._chain_selection_error_response(exc)
        protocols = _root.request_dex_protocols_arg()
        summary = str(request.args.get("summary") or "").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        if summary:
            scanner = _root.get_dex_opportunity_scanner(chain_id=selected_chain_id)
            scanner.start(chain_id=selected_chain_id, protocols=protocols)
            status = scanner.status(chain_id=selected_chain_id)
            status["closed_loop_rust_workers"] = _summarize_rust_workers(
                status.get("closed_loop_rust_workers") or {}
            )
            return jsonify(
                {
                    "ok": True,
                    "chain_selection": chain_selection,
                    "dex_strategy": (
                        str(request.args.get("dex_strategy") or "closed_loop_routes")
                    ),
                    "scanner": status,
                    "snapshot": {
                        "updated_at": None,
                        "summary": None,
                    },
                }
            )
        payload = _root.dex_opportunity_snapshot(chain_id=selected_chain_id, protocols=protocols)
        payload = _filter_scanner_payload(payload)
        payload["chain_selection"] = chain_selection
        return jsonify(_root._attach_dex_strategy(payload))

    @app.get("/api/dex/closed-loop-broadcast-failures")
    def dex_closed_loop_broadcast_failures():
        try:
            chain_selection, selected_chain_id = _root.selected_chain_id_from_request()
        except ChainSelectionError as exc:
            return _root.shared._chain_selection_error_response(exc)
        limit_raw = str(request.args.get("limit") or "50").strip()
        try:
            limit = max(0, min(200, int(limit_raw)))
        except (TypeError, ValueError):
            limit = 50
        payload = broadcast_failure_manager_payload(
            chain_id=selected_chain_id,
            limit=limit,
            chain_selection=chain_selection,
            request_id=getattr(g, "request_id", None),
        )
        if not payload.get("ok"):
            upstream_status = payload.get("upstreamStatus")
            response_status = upstream_status if isinstance(upstream_status, int) else 503
            return jsonify(
                {
                    "ok": False,
                    "mode": "dex_closed_loop_broadcast_failures",
                    "dataSource": "manager",
                    "error": "manager_read_failed",
                    "managerDetail": payload,
                }
            ), response_status
        return jsonify(payload)

    @app.get("/api/dex/opportunity-scanner/scan-now")
    def dex_opportunity_scanner_scan_now():
        try:
            chain_selection, selected_chain_id = _root.selected_chain_id_from_request()
        except ChainSelectionError as exc:
            return _root.shared._chain_selection_error_response(exc)
        protocols = _root.request_dex_protocols_arg()
        wait_for_scan = str(request.args.get("wait") or "0").strip().lower() in {"1", "true", "yes", "on"}
        if wait_for_scan:
            scanner = _root.get_dex_opportunity_scanner(chain_id=selected_chain_id)
            payload = scanner.scan_now(chain_id=selected_chain_id, protocols=protocols)
        else:
            payload = _root.run_dex_opportunity_scan_now(chain_id=selected_chain_id, protocols=protocols)
        payload = _filter_scanner_payload(payload)
        payload["chain_selection"] = chain_selection
        return jsonify(_root._attach_dex_strategy(payload))

    @app.post("/api/dex/closed-loop-groups/<group_key>/refresh")
    def dex_closed_loop_group_refresh(group_key):
        try:
            chain_selection, selected_chain_id = _root.selected_chain_id_from_request()
        except ChainSelectionError as exc:
            return _root.shared._chain_selection_error_response(exc)
        protocols = _root.request_dex_protocols_arg()
        body = request.get_json(silent=True) or {}
        mode = str(body.get("mode") or request.args.get("mode") or "deep").strip().lower()
        wait = str(request.args.get("wait") or "0").strip().lower() in {"1", "true", "yes", "on"}
        groups = _closed_loop_groups_for_refresh(
            chain_id=selected_chain_id,
            protocols=protocols,
        )
        group = _match_closed_loop_group(groups, group_key)
        if group is None:
            return jsonify(
                {
                    "ok": False,
                    "mode": "dex_closed_loop_group_refresh",
                    "chain_id": selected_chain_id,
                    "selected_protocols": protocols,
                    "error": "closed_loop_group_not_found",
                    "group_key": group_key,
                    "available_groups": [g.get("flashLoanToken", {}).get("symbol") for g in groups],
                }
            ), 404
        anchor = str(
            (group.get("flashLoanToken") or group.get("startToken") or {}).get("symbol") or ""
        ).strip().upper()
        refresh_group_key = anchor or str(group_key).strip().upper()
        scanner = _root.get_dex_opportunity_scanner(chain_id=selected_chain_id)
        if getattr(scanner, "_native_rust_closed_loop", False):
            native_result = scanner._refresh_native_closed_loop_group(
                group_key=refresh_group_key,
                chain_id=selected_chain_id,
                protocols=protocols,
            )
            return jsonify(
                {
                    **native_result,
                    "chain_selection": chain_selection,
                }
            )
        priority = SCAN_PRIORITY if mode in {"scan"} else USER_PRIORITY
        task = scanner.closed_loop_review_queue.submit_group(
            group_key=refresh_group_key,
            chain_id=selected_chain_id,
            protocols=protocols,
            mode=mode,
            priority=priority,
        )
        if not wait:
            return jsonify(
                {
                    "ok": True,
                    "mode": "dex_closed_loop_group_refresh",
                    "queued": True,
                    "chain_selection": chain_selection,
                    "task": {
                        "group_key": task.group_key,
                        "state": task.state,
                        "mode": task.mode,
                        "priority": task.priority,
                    },
                }
            )

        scanner.closed_loop_review_pipeline.run_once()
        task_state = next(
            (item for item in scanner.closed_loop_review_queue.snapshot() if item["group_key"] == task.group_key),
            None,
        )
        if task_state is not None:
            task_state["result"] = task.result
            task_state["error"] = task.error
        return jsonify(
            {
                "ok": True,
                "mode": "dex_closed_loop_group_refresh",
                "queued": False,
                "chain_selection": chain_selection,
                "task": task_state or {"group_key": task.group_key, "state": task.state},
            }
        )

    @app.post("/api/dex/closed-loop-groups/manual-review")
    def dex_closed_loop_group_manual_review():
        try:
            chain_selection, selected_chain_id = _root.selected_chain_id_from_request()
        except ChainSelectionError as exc:
            return _root.shared._chain_selection_error_response(exc)
        protocols = _root.request_dex_protocols_arg()
        body = request.get_json(silent=True) or {}
        contract_error = _write_request_contract_error(body)
        if contract_error is not None:
            payload, status = contract_error
            return jsonify(payload), status
        route_symbols = [
            str(item).strip().upper()
            for item in (body.get("routeSymbols") or [])
            if str(item).strip()
        ]
        route_addresses = [
            str(item).strip().lower()
            for item in (body.get("routeAddresses") or [])
            if str(item).strip()
        ]
        if len(route_symbols) != 4 or route_symbols[0] != route_symbols[-1]:
            return jsonify(
                {
                    "ok": False,
                    "mode": "native_closed_loop_group_manual_review",
                    "error": "closed_loop_route_unsupported",
                    "routeSymbols": route_symbols,
                    "group_key": "",
                    "chain_selection": chain_selection,
                }
            ), 400
        middle = sorted([route_symbols[1], route_symbols[2]])
        group_key = f"{route_symbols[0].lower()}|{middle[0].lower()}|{middle[1].lower()}"
        chain_runtime = getattr(g, "chain_runtime", None)
        if chain_runtime is None or not hasattr(chain_runtime, "execute_command"):
            return jsonify(
                {
                    "ok": False,
                    "mode": "native_closed_loop_group_manual_review",
                    "error": "runtime_core_command_unavailable",
                    "detail": "手动复核需要请求绑定的 RuntimeCore command 接口",
                    "group_key": group_key,
                    "chain_selection": chain_selection,
                }
            ), 503
        try:
            # Ensure the chain's native pool is registered, then dispatch the
            # transition through RuntimeCore instead of the Python scanner.
            getattr(chain_runtime, "native_pool", None)
            native_result = chain_runtime.execute_command(
                "manual_review",
                request_id=str(request.headers.get("X-Request-Id") or "").strip(),
                idempotency_key=str(
                    request.headers.get("Idempotency-Key") or ""
                ).strip(),
                dry_run=body["dryRun"],
                options={
                    "groupKey": group_key,
                    "routeSymbols": route_symbols,
                    "routeAddresses": route_addresses,
                },
            )
        except (RuntimeError, TypeError, ValueError) as exc:
            native_result = {
                "ok": False,
                "mode": "native_closed_loop_group_manual_review",
                "error": "runtime_core_command_failed",
                "detail": str(exc)[:500],
            }
        error = str(
            native_result.get("error")
            or native_result.get("code")
            or "runtime_core_command_failed"
        )
        response_status = 200 if native_result.get("ok") else (
            native_result.get("upstreamStatus")
            if isinstance(native_result.get("upstreamStatus"), int)
            else _closed_loop_transition_status(error.lower())
        )
        return jsonify(
            {
                **native_result,
                "mode": native_result.get("mode")
                or "native_closed_loop_group_manual_review",
                "dryRun": body["dryRun"],
                "requestId": str(request.headers.get("X-Request-Id") or "").strip(),
                "idempotencyKey": str(
                    request.headers.get("Idempotency-Key") or ""
                ).strip(),
                "group_key": native_result.get("groupKey") or group_key,
                "routeSymbols": route_symbols,
                "routeAddresses": route_addresses,
                "chain_selection": chain_selection,
            }
        ), response_status

    @app.post("/api/dex/closed-loop-groups/manual-broadcast")
    def dex_closed_loop_group_manual_broadcast():
        try:
            chain_selection, selected_chain_id = _root.selected_chain_id_from_request()
        except ChainSelectionError as exc:
            return _root.shared._chain_selection_error_response(exc)
        body = request.get_json(silent=True) or {}
        contract_error = _write_request_contract_error(body)
        if contract_error is not None:
            payload, status = contract_error
            return jsonify(payload), status
        route_symbols = [
            str(item).strip().upper()
            for item in (body.get("routeSymbols") or [])
            if str(item).strip()
        ]
        route_addresses = [
            str(item).strip().lower()
            for item in (body.get("routeAddresses") or [])
            if str(item).strip()
        ]
        if len(route_symbols) != 4 or route_symbols[0] != route_symbols[-1]:
            return jsonify(
                {
                    "ok": False,
                    "mode": "native_closed_loop_group_manual_broadcast",
                    "error": "closed_loop_route_unsupported",
                    "routeSymbols": route_symbols,
                    "group_key": "",
                    "chain_selection": chain_selection,
                }
            ), 400
        middle = sorted([route_symbols[1], route_symbols[2]])
        group_key = f"{route_symbols[0].lower()}|{middle[0].lower()}|{middle[1].lower()}"
        chain_runtime = getattr(g, "chain_runtime", None)
        if chain_runtime is None or not hasattr(chain_runtime, "execute_command"):
            return jsonify(
                {
                    "ok": False,
                    "mode": "native_closed_loop_group_manual_broadcast",
                    "error": "runtime_core_command_unavailable",
                    "detail": "手动广播需要请求绑定的 RuntimeCore command 接口",
                    "group_key": group_key,
                    "chain_selection": chain_selection,
                }
            ), 503
        try:
            # Ensure the chain's native pool is registered, then dispatch the
            # transition through RuntimeCore instead of the Python scanner.
            getattr(chain_runtime, "native_pool", None)
            native_result = chain_runtime.execute_command(
                "manual_broadcast",
                request_id=str(request.headers.get("X-Request-Id") or "").strip(),
                idempotency_key=str(
                    request.headers.get("Idempotency-Key") or ""
                ).strip(),
                dry_run=body["dryRun"],
                options={
                    "groupKey": group_key,
                    "routeSymbols": route_symbols,
                    "routeAddresses": route_addresses,
                },
            )
        except (RuntimeError, TypeError, ValueError) as exc:
            native_result = {
                "ok": False,
                "mode": "native_closed_loop_group_manual_broadcast",
                "error": "runtime_core_command_failed",
                "detail": str(exc)[:500],
            }
        error = str(
            native_result.get("error")
            or native_result.get("code")
            or "runtime_core_command_failed"
        )
        response_status = 200 if native_result.get("ok") else (
            native_result.get("upstreamStatus")
            if isinstance(native_result.get("upstreamStatus"), int)
            else _closed_loop_transition_status(error.lower())
        )
        group_key = native_result.get("groupKey") or group_key
        return jsonify(
            {
                **native_result,
                "mode": native_result.get("mode")
                or "native_closed_loop_group_manual_broadcast",
                "dryRun": body["dryRun"],
                "requestId": str(request.headers.get("X-Request-Id") or "").strip(),
                "idempotencyKey": str(
                    request.headers.get("Idempotency-Key") or ""
                ).strip(),
                "group_key": native_result.get("groupKey") or group_key,
                "routeSymbols": route_symbols,
                "routeAddresses": route_addresses,
                "chain_selection": chain_selection,
            }
        ), response_status

    @app.post("/api/dex/closed-loop-groups/<group_key>/start")
    def dex_closed_loop_group_start(group_key):
        try:
            chain_selection, selected_chain_id = _root.selected_chain_id_from_request()
        except ChainSelectionError as exc:
            return _root.shared._chain_selection_error_response(exc)
        protocols = _root.request_dex_protocols_arg()
        scanner = _root.get_dex_opportunity_scanner(chain_id=selected_chain_id)
        if not getattr(scanner, "_native_rust_closed_loop", False):
            return jsonify(
                {
                    "ok": False,
                    "mode": "native_closed_loop_group_start",
                    "error": "native_rust_closed_loop_not_enabled",
                    "chain_selection": chain_selection,
                }
            ), 409
        result = scanner._start_native_closed_loop_group(
            group_key=group_key,
            chain_id=selected_chain_id,
            protocols=protocols,
        )
        result["chain_selection"] = chain_selection
        return jsonify(result)

    @app.post("/api/dex/closed-loop-groups/<group_key>/stop")
    def dex_closed_loop_group_stop(group_key):
        try:
            chain_selection, selected_chain_id = _root.selected_chain_id_from_request()
        except ChainSelectionError as exc:
            return _root.shared._chain_selection_error_response(exc)
        protocols = _root.request_dex_protocols_arg()
        scanner = _root.get_dex_opportunity_scanner(chain_id=selected_chain_id)
        if not getattr(scanner, "_native_rust_closed_loop", False):
            return jsonify(
                {
                    "ok": False,
                    "mode": "native_closed_loop_group_stop",
                    "error": "native_rust_closed_loop_not_enabled",
                    "chain_selection": chain_selection,
                }
            ), 409
        result = scanner._stop_native_closed_loop_group(
            group_key=group_key,
            chain_id=selected_chain_id,
            protocols=protocols,
        )
        result["chain_selection"] = chain_selection
        return jsonify(result)

    @app.post("/api/dex/opportunity-scanner/auto-execute")
    def dex_opportunity_scanner_auto_execute():
        try:
            chain_selection, selected_chain_id = _root.selected_chain_id_from_request()
        except ChainSelectionError as exc:
            return _root.shared._chain_selection_error_response(exc)
        body = request.get_json(silent=True) or {}
        enabled = str(body.get("enabled") or request.args.get("enabled") or "").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        protocols = _root.request_dex_protocols_arg()
        scanner = _root.get_dex_opportunity_scanner(chain_id=selected_chain_id)
        scanner.set_auto_execute_enabled(enabled)
        if enabled:
            scanner.start(chain_id=selected_chain_id, protocols=protocols)
            current = scanner.snapshot(chain_id=selected_chain_id, protocols=protocols)
            snapshot = current.get("snapshot") if isinstance(current.get("snapshot"), dict) else {}
            payload = snapshot.get("payload") if isinstance(snapshot.get("payload"), dict) else None
            if isinstance(payload, dict):
                scanner._enqueue_closed_loop_groups_from_payload(
                    payload,
                    chain_id=selected_chain_id,
                    protocols=protocols,
                )
            scanner.closed_loop_review_pipeline.start()
        payload = _root.dex_opportunity_snapshot(
            chain_id=selected_chain_id,
            protocols=_root.request_dex_protocols_arg(),
        )
        payload["chain_selection"] = chain_selection
        return jsonify(_root._attach_dex_strategy(payload))

    @app.post("/api/dex/opportunity-scanner/stats/reset")
    def dex_opportunity_scanner_stats_reset():
        try:
            chain_selection, selected_chain_id = _root.selected_chain_id_from_request()
        except ChainSelectionError as exc:
            return _root.shared._chain_selection_error_response(exc)
        scanner = _root.get_dex_opportunity_scanner(chain_id=selected_chain_id)
        scanner.reset_dex_opportunity_stats(
            chain_id=selected_chain_id,
            protocols=_root.request_dex_protocols_arg(),
        )
        metrics_reset = {"ok": True, "mode": "closed_loop_runtime_metrics_reset"}
        try:
            from dex.runtime.closed_loop_object import ClosedLoopRuntimeMetrics

            metrics = ClosedLoopRuntimeMetrics.cached_for(_submission_database_url())
            if metrics is not None:
                metrics.reset_counters()
        except (
            ImportError,
            KeyError,
            OSError,
            RuntimeError,
            sqlite3.Error,
            TypeError,
            ValueError,
        ) as exc:
            metrics_reset = {
                "ok": False,
                "error": type(exc).__name__,
                "detail": str(exc),
            }
        payload = _root.dex_opportunity_snapshot(
            chain_id=selected_chain_id,
            protocols=_root.request_dex_protocols_arg(),
        )
        payload["chain_selection"] = chain_selection
        payload["metricsReset"] = metrics_reset
        return jsonify(_root._attach_dex_strategy(payload))


def register_dex_pool_routes(app) -> None:
    @app.get("/api/dex-pools/candidates")
    def dex_pool_candidates():
        try:
            chain_selection, selected_chain_id = _root.selected_chain_id_from_request()
        except ChainSelectionError as exc:
            return _root.shared._chain_selection_error_response(exc)
        protocols = _root.request_dex_protocols_arg()
        page, page_size, query, quality = _root.candidate_pagination_args()
        abnormal_pool_page, abnormal_token_page = _root.section_pagination_args()
        payload = candidate_manager_page_payload(
            chain_id=selected_chain_id,
            protocols=protocols,
            page=page,
            page_size=page_size,
            query=query,
            quality=quality,
            abnormal_pool_page=abnormal_pool_page,
            abnormal_token_page=abnormal_token_page,
            chain_selection=chain_selection,
            request_id=getattr(g, "request_id", None),
        )
        if not payload.get("ok"):
            return jsonify(
                {
                    "ok": False,
                    "mode": "dex_pool_candidates",
                    "dataSource": "manager",
                    "error": "manager_read_failed",
                    "managerDetail": payload,
                }
            ), 503
        # The manager projection is contract-shaped; this route only attaches
        # presentation strategy metadata after the sole business read.
        g.response_contract_validated = True
        return jsonify(
            mask_dex_surface_placeholder_symbols(
                _root._attach_dex_strategy(payload)
            )
        )

    @app.post("/api/dex-pools/candidates/refresh")
    def dex_pool_candidates_refresh():
        try:
            chain_selection, selected_chain_id = _root.selected_chain_id_from_request()
        except ChainSelectionError as exc:
            return _root.shared._chain_selection_error_response(exc)
        protocols = _root.request_dex_protocols_arg()
        payload = request.get_json(silent=True)
        payload = payload if isinstance(payload, dict) else {}
        full_refresh = (
            request.args.get("full", "").strip().lower() in {"1", "true", "yes"}
            or str(payload.get("full") or "").strip().lower()
            in {"1", "true", "yes", "full"}
        )
        raw_dry_run = request.args.get("dry_run")
        if raw_dry_run is None:
            raw_dry_run = payload.get("dryRun", payload.get("dry_run"))
        if isinstance(raw_dry_run, bool):
            dry_run = raw_dry_run
        elif str(raw_dry_run or "").strip().lower() in {"true", "1", "yes"}:
            dry_run = True
        elif str(raw_dry_run or "").strip().lower() in {"false", "0", "no"}:
            dry_run = False
        else:
            return jsonify(
                {
                    "ok": False,
                    "mode": "dex_pool_candidates",
                    "dataSource": "manager",
                    "error": "dry_run_required",
                }
            ), 400
        request_id = str(request.headers.get("X-Request-Id") or "").strip()
        if not request_id:
            return jsonify(
                {
                    "ok": False,
                    "mode": "dex_pool_candidates",
                    "dataSource": "manager",
                    "error": "request_id_required",
                }
            ), 400
        idempotency_key = str(request.headers.get("Idempotency-Key") or "").strip()
        if not idempotency_key:
            return jsonify(
                {
                    "ok": False,
                    "mode": "dex_pool_candidates",
                    "dataSource": "manager",
                    "error": "idempotency_key_required",
                }
            ), 400
        refresh_state = {
            "requested": "full" if full_refresh else "incremental",
            "running": True,
            "ok": None,
            "message": "全量刷新进行中" if full_refresh else "增量刷新进行中",
        }
        try:
            _root._clear_dex_surface_cache(
                chain_id=selected_chain_id,
                protocols=protocols,
                use_static_observation=None,
            )
            if full_refresh and not dry_run:
                chain_row = _root.chain_data_by_chain_id(selected_chain_id) if selected_chain_id is not None else None
                network = str(
                    (chain_selection or {}).get("network")
                    or (chain_row or {}).get("network")
                    or ""
                ).strip().lower()
                if network == _root.ROBINHOOD_TESTNET_NETWORK or selected_chain_id == _root.ROBINHOOD_TESTNET_CHAIN_ID:
                    refresh_state = {
                        **refresh_state,
                        **_root._refresh_robinhood_testnet_v4_sources(),
                        "scope": "robinhood-testnet-v4",
                    }
                elif network == _root.ROBINHOOD_NETWORK or selected_chain_id == _root.ROBINHOOD_CHAIN_ID:
                    refresh_state = {
                        **refresh_state,
                        **_root._refresh_v4_sources(chain_id=selected_chain_id, network=network),
                        "scope": "robinhood-mainnet-v4",
                    }
            manager_result = candidate_edge_refresh_manager_payload(
                chain_id=selected_chain_id,
                dry_run=dry_run,
                request_id=request_id,
                idempotency_key=idempotency_key,
            )
            if not manager_result.get("ok"):
                upstream_status = manager_result.get("upstreamStatus")
                response_status = upstream_status if isinstance(upstream_status, int) else 503
                return jsonify(
                    {
                        "ok": False,
                        "mode": "dex_pool_candidates",
                        "dataSource": "manager",
                        "error": "manager_write_failed",
                        "managerDetail": manager_result,
                        "refresh_state": {
                            **refresh_state,
                            "running": False,
                            "ok": False,
                            "errors": [
                                str(
                                    manager_result.get("detail")
                                    or "candidate edge manager rebuild failed"
                                )[:200]
                            ],
                            "message": "候选池管理器重建失败",
                        },
                    }
                ), response_status
            page, page_size, query, quality = _root.candidate_pagination_args()
            abnormal_pool_page, abnormal_token_page = _root.section_pagination_args()
            manager_payload = candidate_manager_page_payload(
                chain_id=selected_chain_id,
                protocols=protocols,
                page=page,
                page_size=page_size,
                query=query,
                quality=quality,
                abnormal_pool_page=abnormal_pool_page,
                abnormal_token_page=abnormal_token_page,
                chain_selection=chain_selection,
                request_id=request_id,
            )
            if not manager_payload.get("ok"):
                upstream_status = manager_payload.get("upstreamStatus")
                response_status = upstream_status if isinstance(upstream_status, int) else 503
                return jsonify(
                    {
                        "ok": False,
                        "mode": "dex_pool_candidates",
                        "dataSource": "manager",
                        "error": "manager_read_failed",
                        "managerDetail": manager_payload,
                    }
                ), response_status
            manager_payload["chain_selection"] = chain_selection
            manager_payload["refresh_state"] = {
                **refresh_state,
                "running": False,
                "ok": True,
                "dryRun": manager_result["dryRun"],
                "idempotentReplay": manager_result["idempotentReplay"],
                "changedRows": manager_result["changedRows"],
                "consumersNeedRebuild": manager_result["consumersNeedRebuild"],
                "candidateVersion": manager_result["candidateVersion"],
                "candidateChecksum": manager_result["materializedChecksum"],
                "dataSource": "manager",
                "command": "rebuild_candidate_edges",
                "message": (
                    "候选池管理器预演完成"
                    if dry_run
                    else (
                        "全量刷新完成"
                        if full_refresh
                        else "增量刷新完成"
                    )
                ),
            }
            g.response_contract_validated = True
            return jsonify(
                mask_dex_surface_placeholder_symbols(
                    _root._attach_dex_strategy(manager_payload)
                )
            )
        except Exception as exc:
            return jsonify(
                {
                    "ok": False,
                    "mode": "dex_pool_candidates",
                    "error": "候选池刷新失败，底层数据文件缺失或路径失败，请稍后重新全量刷新",
                    "rawError": str(exc)[:500],
                    "chain_id": selected_chain_id,
                    "refresh_state": {
                        **refresh_state,
                        "running": False,
                        "ok": False,
                        "errors": [str(exc)[:200]],
                        "message": "候选池刷新失败，底层数据文件缺失或路径失败",
                    },
                }
            ), 503


def register_dex_submission_routes(app) -> None:
    def _manager_execution_read(status: str, mode: str):
        try:
            chain_selection, selected_chain_id = _root.selected_chain_id_from_request()
        except ChainSelectionError as exc:
            return _root.shared._chain_selection_error_response(exc)
        page, page_size = _submission_page_args()
        payload = submission_execution_manager_payload(
            chain_id=selected_chain_id,
            status=status,
            mode=mode,
            page=page,
            page_size=page_size,
            chain_selection=chain_selection,
            request_id=getattr(g, "request_id", None),
        )
        if not payload.get("ok"):
            upstream_status = payload.get("upstreamStatus")
            response_status = upstream_status if isinstance(upstream_status, int) else 503
            return jsonify(
                {
                    "ok": False,
                    "mode": mode,
                    "dataSource": "manager",
                    "error": "manager_read_failed",
                    "managerDetail": payload,
                }
            ), response_status
        payload["dex_strategy"] = _root.request_dex_strategy_arg()
        return jsonify(payload)

    def _manager_submission_read(
        mode: str,
        *,
        failure_category: str | None = None,
    ):
        try:
            chain_selection, selected_chain_id = _root.selected_chain_id_from_request()
        except ChainSelectionError as exc:
            return _root.shared._chain_selection_error_response(exc)
        page, page_size = _submission_page_args()
        payload = submission_overview_manager_payload(
            chain_id=selected_chain_id,
            mode=mode,
            failure_category=failure_category,
            page=page,
            page_size=page_size,
            chain_selection=chain_selection,
            request_id=getattr(g, "request_id", None),
        )
        if not payload.get("ok"):
            upstream_status = payload.get("upstreamStatus")
            response_status = upstream_status if isinstance(upstream_status, int) else 503
            return jsonify(
                {
                    "ok": False,
                    "mode": mode,
                    "dataSource": "manager",
                    "error": "manager_read_failed",
                    "managerDetail": payload,
                }
            ), response_status
        payload["dex_strategy"] = _root.request_dex_strategy_arg()
        return jsonify(payload)

    def _manager_review_read(
        mode: str,
        *,
        category: str | None = None,
        status: str | None = None,
        outcome: str | None = None,
    ):
        try:
            chain_selection, selected_chain_id = _root.selected_chain_id_from_request()
        except ChainSelectionError as exc:
            return _root.shared._chain_selection_error_response(exc)
        page, page_size = _submission_page_args()
        payload = review_manager_page_payload(
            chain_id=selected_chain_id,
            mode=mode,
            category=category,
            status=status,
            outcome=outcome,
            page=page,
            page_size=page_size,
            chain_selection=chain_selection,
            request_id=getattr(g, "request_id", None),
        )
        if not payload.get("ok"):
            upstream_status = payload.get("upstreamStatus")
            response_status = upstream_status if isinstance(upstream_status, int) else 503
            return jsonify(
                {
                    "ok": False,
                    "mode": mode,
                    "dataSource": "manager",
                    "error": "manager_read_failed",
                    "managerDetail": payload,
                }
            ), response_status
        payload["dex_strategy"] = _root.request_dex_strategy_arg()
        return jsonify(payload)

    @app.get("/api/dex/submissions")
    def dex_submission_review():
        return _manager_submission_read("dex_submission_review")

    @app.get("/api/dex/submissions/failed")
    @app.get("/api/dex/submissions/failed/<category>")
    def dex_submission_failed_review(category: str = "all"):
        category = str(category or "all").strip().lower()
        if category not in SUBMISSION_FAILURE_CATEGORY_ORDER:
            return jsonify(
                {
                    "ok": False,
                    "mode": "dex_submission_failed_review",
                    "error": "unknown_failure_category",
                    "category": category,
                    "available_categories": list(SUBMISSION_FAILURE_CATEGORY_ORDER),
                }
            ), 404
        return _manager_submission_read(
            "dex_submission_failed_review",
            failure_category=category,
        )

    @app.get("/api/dex/submissions/success")
    def dex_submission_success_review():
        return _manager_execution_read(
            "success",
            "dex_submission_success_review",
        )

    @app.get("/api/dex/submissions/review")
    @app.get("/api/dex/submissions/review/<category>")
    def dex_submission_review_records(category: str = "all"):
        category = str(category or "all").strip().lower()
        if category not in _REVIEW_CATEGORY_ORDER:
            return jsonify(
                {
                    "ok": False,
                    "mode": "dex_submission_review_records",
                    "error": "unknown_review_category",
                    "category": category,
                    "available_categories": list(_REVIEW_CATEGORY_ORDER),
                }
            ), 404
        return _manager_review_read(
            "dex_submission_review_records",
            category=category,
        )

    @app.get("/api/dex/submissions/review-success")
    def dex_submission_review_success_records():
        return _manager_review_read(
            "dex_submission_review_success_records",
            outcome="success",
        )

    @app.get("/api/dex/submissions/review-failed")
    def dex_submission_review_failed_records():
        return _manager_review_read(
            "dex_submission_review_failed_records",
            outcome="failed",
        )

    @app.get("/api/dex/submissions/execution-success")
    def dex_submission_execution_success_records():
        return _manager_execution_read(
            "success",
            "dex_submission_execution_success_records",
        )

    @app.get("/api/dex/submissions/execution-failed")
    def dex_submission_execution_failed_records():
        return _manager_execution_read(
            "failed",
            "dex_submission_execution_failed_records",
        )

    @app.get("/api/dex/submissions/detail")
    def dex_submission_detail():
        raw_id = str(request.args.get("attempt_id") or request.args.get("id") or "").strip()
        if not raw_id:
            return jsonify({"ok": False, "mode": "dex_submission_detail", "error": "attempt_id_required"}), 400
        try:
            _, selected_chain_id = _root.selected_chain_id_from_request()
        except ChainSelectionError as exc:
            return _root.shared._chain_selection_error_response(exc)
        manager_detail = submission_detail_manager_payload(
            {},
            chain_id=selected_chain_id,
            attempt_id=raw_id,
            request_id=getattr(g, "request_id", None),
        )
        if not manager_detail.get("ok"):
            upstream_status = manager_detail.get("upstreamStatus")
            status = upstream_status if isinstance(upstream_status, int) else 503
            return jsonify(
                {
                    "ok": False,
                    "mode": "dex_submission_detail",
                    "dataSource": "manager",
                    "error": "manager_read_failed",
                    "managerDetail": manager_detail,
                }
            ), status
        if not manager_detail.get("found"):
            return jsonify(
                {
                    "ok": False,
                    "mode": "dex_submission_detail",
                    "dataSource": "manager",
                    "error": "attempt_not_found",
                    "managerDetail": manager_detail,
                }
            ), 404
        return jsonify(
            {
                "ok": True,
                "mode": "dex_submission_detail",
                "dataSource": "manager",
                "attempt": manager_detail.get("attempt"),
                "managerDetail": manager_detail,
            }
        )

    @app.post("/api/dex/submissions/clear")
    def dex_submission_clear():
        try:
            chain_selection, selected_chain_id = _root.selected_chain_id_from_request()
        except ChainSelectionError as exc:
            return _root.shared._chain_selection_error_response(exc)
        body = request.get_json(silent=True)
        body = body if isinstance(body, dict) else {}
        record_kind = str(
            request.args.get("record_kind")
            or body.get("record_kind")
            or body.get("recordKind")
            or ""
        ).strip().lower()
        if record_kind != "submission":
            return jsonify(
                {
                    "ok": False,
                    "mode": "dex_submission_clear",
                    "error": (
                        "record_kind_required"
                        if not record_kind
                        else "record_kind_manager_scope_required"
                    ),
                    "dataSource": "manager",
                }
            ), 400
        raw_dry_run = request.args.get("dry_run", body.get("dryRun", body.get("dry_run")))
        if isinstance(raw_dry_run, bool):
            dry_run = raw_dry_run
        elif str(raw_dry_run or "").strip().lower() in {"true", "1", "yes"}:
            dry_run = True
        elif str(raw_dry_run or "").strip().lower() in {"false", "0", "no"}:
            dry_run = False
        else:
            return jsonify(
                {
                    "ok": False,
                    "mode": "dex_submission_clear",
                    "error": "dry_run_required",
                    "dataSource": "manager",
                }
            ), 400
        request_id = str(request.headers.get("X-Request-Id") or "").strip()
        if not request_id:
            return jsonify(
                {
                    "ok": False,
                    "mode": "dex_submission_clear",
                    "dataSource": "manager",
                    "error": "request_id_required",
                }
            ), 400
        idempotency_key = str(request.headers.get("Idempotency-Key") or "").strip()
        if not idempotency_key:
            return jsonify(
                {
                    "ok": False,
                    "mode": "dex_submission_clear",
                    "dataSource": "manager",
                    "error": "idempotency_key_required",
                }
            ), 400
        payload = submission_clear_manager_payload(
            chain_id=selected_chain_id,
            dry_run=dry_run,
            request_id=request_id,
            idempotency_key=idempotency_key,
        )
        if not payload.get("ok"):
            upstream_status = payload.get("upstreamStatus")
            status = upstream_status if isinstance(upstream_status, int) else 503
            return jsonify(
                {
                    "ok": False,
                    "mode": "dex_submission_clear",
                    "dataSource": "manager",
                    "error": "manager_write_failed",
                    "managerResult": payload,
                }
            ), status
        return jsonify(
            {
                "ok": True,
                "mode": "dex_submission_clear",
                "dataSource": "manager",
                "chain_selection": chain_selection,
                "dex_strategy": _root.request_dex_strategy_arg(),
                "record_kind": record_kind,
                "dryRun": payload["dryRun"],
                "cleared": payload["cleared"],
                "remaining": payload["remaining"],
                "clearedDiagnostics": payload["clearedDiagnostics"],
                "remainingDiagnostics": payload["remainingDiagnostics"],
                "idempotentReplay": payload["idempotentReplay"],
                "managerResult": payload["managerResult"],
                "results": [],
            }
        )
