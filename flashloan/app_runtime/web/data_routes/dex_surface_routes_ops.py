from __future__ import annotations

import threading
import time

from flask import jsonify, request

from core.chain_selection import ChainSelectionError
from core.db.storage_cow_execution import load_recent_cow_execution_attempts_jsonl_grouped
from dex.runtime.dex_closed_loop_review_queue import (
    SCAN_PRIORITY,
    USER_PRIORITY,
    normalize_closed_loop_group_key,
)
from web.data_routes import dex_surfaces as _root

_SUBMISSION_RESPONSE_CACHE: dict[str, tuple[float, dict]] = {}
_SUBMISSION_RESPONSE_CACHE_LOCK = threading.Lock()
_SUBMISSION_RESPONSE_CACHE_TTL_SECONDS = 2.0


def _submission_response_cache_key() -> str:
    return request.path + "?" + request.query_string.decode("utf-8", errors="replace")


def _cached_submission_response_payload(key: str) -> dict | None:
    with _SUBMISSION_RESPONSE_CACHE_LOCK:
        stored = _SUBMISSION_RESPONSE_CACHE.get(key)
    if not stored:
        return None
    cached_at, payload = stored
    if time.time() - cached_at > _SUBMISSION_RESPONSE_CACHE_TTL_SECONDS:
        with _SUBMISSION_RESPONSE_CACHE_LOCK:
            _SUBMISSION_RESPONSE_CACHE.pop(key, None)
        return None
    return payload


def _cache_submission_response_payload(key: str, payload: dict) -> None:
    with _SUBMISSION_RESPONSE_CACHE_LOCK:
        _SUBMISSION_RESPONSE_CACHE[key] = (time.time(), payload)


def _clear_submission_response_cache() -> None:
    with _SUBMISSION_RESPONSE_CACHE_LOCK:
        _SUBMISSION_RESPONSE_CACHE.clear()


def _closed_loop_groups_for_refresh(*, chain_id, protocols):
    overview = _root.load_market_observation_overview_static(
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


def _row_failure_reason_text(row: dict) -> str:
    """Collect human-readable failure/blocker text from a submission row."""
    parts: list[str] = []
    quote = row.get("quote") if isinstance(row.get("quote"), dict) else {}
    evidence = quote.get("live_onchain_evidence") if isinstance(quote.get("live_onchain_evidence"), dict) else {}
    precheck = row.get("precheck") if isinstance(row.get("precheck"), dict) else {}
    for value in (
        row.get("blocked_reasons"),
        row.get("reasons"),
        row.get("error"),
        row.get("detail"),
        row.get("errorDetail"),
        precheck.get("reasons"),
        evidence.get("blockers"),
        evidence.get("blocked_reasons"),
        evidence.get("reasons"),
        evidence.get("failureReason"),
        evidence.get("error"),
        evidence.get("detail"),
    ):
        if value in (None, ""):
            continue
        if isinstance(value, (list, tuple)):
            parts.extend(str(item) for item in value if str(item).strip())
        else:
            parts.append(str(value))
    for stack in (evidence.get("errorStack") or []):
        if isinstance(stack, dict) and stack.get("message"):
            parts.append(str(stack["message"]))
    for hop in evidence.get("hops") or []:
        if not isinstance(hop, dict):
            continue
        for key in ("liveQuoteError", "error", "blockers"):
            value = hop.get(key)
            if value in (None, ""):
                continue
            if isinstance(value, (list, tuple)):
                parts.extend(str(item) for item in value if str(item).strip())
            else:
                parts.append(str(value))
    return " | ".join(part for part in parts if part)


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
    return [row for row in failed_rows if isinstance(row, dict) and not _is_system_failure_row(row)]


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


def _success_submission_rows(attempt_rows: list[dict]) -> list[dict]:
    """Return only records that reached a successful submission/execution state."""

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
            rows.append(row)
            continue
        quote = row.get("quote") if isinstance(row.get("quote"), dict) else {}
        auto_execute = row.get("auto_execute") if isinstance(row.get("auto_execute"), dict) else {}
        auto_result = auto_execute.get("result") if isinstance(auto_execute.get("result"), dict) else {}
        tx_hash = str(
            row.get("tx_hash")
            or row.get("transaction_hash")
            or quote.get("tx_hash")
            or quote.get("transaction_hash")
            or auto_result.get("txHash")
            or auto_result.get("transactionHash")
            or auto_result.get("transaction_hash")
            or ""
        ).strip()
        if tx_hash and not _is_system_failure_row(row):
            rows.append(row)
    return rows


_FAILURE_CATEGORY_ORDER = (
    "all",
    "broadcast",
    "submission",
    "code",
    "rpc",
    "execution",
    "quote",
    "blocked",
    "other",
)


def _failed_submission_category(row: dict) -> str:
    """Classify a failed row into the same tag buckets used by the UI."""
    status = _row_failure_status(row)
    quote = row.get("quote") if isinstance(row.get("quote"), dict) else {}
    evidence_status = str(
        (quote.get("live_onchain_evidence") or {}).get("status")
        or (quote.get("live_onchain_evidence") or {}).get("executionTrust")
        or ""
    ).strip().lower()
    if _is_code_system_failure_text(_row_failure_reason_text(row)):
        return "code"
    if _is_business_failure_text(_row_failure_reason_text(row)):
        return "blocked"
    if status in {"broadcast_failed", "broadcast_error", "broadcast_disabled"} or "broadcast" in evidence_status:
        return "broadcast"
    if status in {"submission_failed", "submit_failed", "order_failed"}:
        return "submission"
    if status in {"rpc_verification_failed", "rpc_verification_timeout"} or "rpc" in evidence_status:
        return "rpc"
    if status in {"execution_failed", "confirmed_failed", "settlement_failed"}:
        return "execution"
    if status in {"live_quote_failed", "quote_failed"} or "quote_failed" in evidence_status:
        return "quote"
    if status in {"not_executable", "blocked", "unsupported_route", "quote_required"}:
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
    system_failed_rows = _system_failed_submission_rows(failed_rows)
    business_blocked_rows = _business_blocked_submission_rows(failed_rows)
    existing_failed_count = int(summary.get("failedCount") or 0)
    existing_blocked_count = int(summary.get("blockedCount") or 0)
    summary["failedCount"] = existing_failed_count + len(system_failed_rows)
    summary["systemErrorCount"] = summary["failedCount"]
    summary["businessBlockedCount"] = existing_blocked_count + len(business_blocked_rows)
    summary["blockedCount"] = summary["businessBlockedCount"]
    summary["attemptCount"] = len(attempts)
    summary["_mergedQuoteFailures"] = added
    return response_payload


def register_dex_scanner_routes(app) -> None:
    @app.get("/api/dex/opportunity-scanner/start")
    def dex_opportunity_scanner_start():
        try:
            chain_selection, selected_chain_id = _root.selected_chain_id_from_request()
        except ChainSelectionError as exc:
            return _root.shared._chain_selection_error_response(exc)
        protocols = _root.request_dex_protocols_arg()
        scanner = _root.get_dex_opportunity_scanner()
        scanner.start(chain_id=selected_chain_id, protocols=protocols)
        payload = _root.dex_opportunity_snapshot(chain_id=selected_chain_id, protocols=protocols)
        payload["chain_selection"] = chain_selection
        return jsonify(_root._attach_dex_strategy(payload))

    @app.get("/api/dex/opportunity-scanner/snapshot")
    def dex_opportunity_scanner_snapshot():
        try:
            chain_selection, selected_chain_id = _root.selected_chain_id_from_request()
        except ChainSelectionError as exc:
            return _root.shared._chain_selection_error_response(exc)
        protocols = _root.request_dex_protocols_arg()
        payload = _root.dex_opportunity_snapshot(chain_id=selected_chain_id, protocols=protocols)
        payload["chain_selection"] = chain_selection
        return jsonify(_root._attach_dex_strategy(payload))

    @app.get("/api/dex/opportunity-scanner/scan-now")
    def dex_opportunity_scanner_scan_now():
        try:
            chain_selection, selected_chain_id = _root.selected_chain_id_from_request()
        except ChainSelectionError as exc:
            return _root.shared._chain_selection_error_response(exc)
        protocols = _root.request_dex_protocols_arg()
        wait_for_scan = str(request.args.get("wait") or "0").strip().lower() in {"1", "true", "yes", "on"}
        if wait_for_scan:
            scanner = _root.get_dex_opportunity_scanner()
            payload = scanner.scan_now(chain_id=selected_chain_id, protocols=protocols)
        else:
            payload = _root.run_dex_opportunity_scan_now(chain_id=selected_chain_id, protocols=protocols)
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
        scanner = _root.get_dex_opportunity_scanner()
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
        scanner = _root.get_dex_opportunity_scanner()
        # Rule: every click on the auto-execute toggle resets the session stats,
        # whether it is being enabled or disabled.
        scanner.reset_dex_opportunity_stats()
        scanner.set_auto_execute_enabled(enabled)
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
        scanner = _root.get_dex_opportunity_scanner()
        scanner.reset_dex_opportunity_stats()
        payload = _root.dex_opportunity_snapshot(
            chain_id=selected_chain_id,
            protocols=_root.request_dex_protocols_arg(),
        )
        payload["chain_selection"] = chain_selection
        return jsonify(_root._attach_dex_strategy(payload))


def register_dex_pool_routes(app) -> None:
    @app.get("/api/dex-pools/candidates")
    def dex_pool_candidates():
        try:
            chain_selection, selected_chain_id = _root.selected_chain_id_from_request()
        except ChainSelectionError as exc:
            return _root.shared._chain_selection_error_response(exc)
        try:
            protocols = _root.request_dex_protocols_arg()
            _root._bootstrap_dex_surface_dependencies(
                chain_id=selected_chain_id,
                protocols=protocols,
                flow_name="dex_pool_candidates",
            )
            page, page_size, query, quality = _root.candidate_pagination_args()
            payload = _root.dex_pool_candidates_payload(chain_id=selected_chain_id, protocols=protocols)
            payload["chain_selection"] = chain_selection
            payload.setdefault("candidates", payload.get("pairs") or [])
            return jsonify(
                _root._attach_dex_strategy(
                    _root.paginate_candidate_payload(payload, page=page, page_size=page_size, query=query, quality=quality)
                )
            )
        except Exception as exc:
            return jsonify(
                {
                    "ok": False,
                    "mode": "dex_pool_candidates",
                    "error": "候选池数据暂不可用，请先执行全量刷新重建本地缓存",
                    "rawError": str(exc)[:500],
                    "chain_id": selected_chain_id,
                    "selected_protocols": _root.request_dex_protocols_arg() if selected_chain_id is not None else [],
                }
            ), 503

    @app.get("/api/dex-pools/candidates/refresh")
    @app.post("/api/dex-pools/candidates/refresh")
    def dex_pool_candidates_refresh():
        try:
            chain_selection, selected_chain_id = _root.selected_chain_id_from_request()
        except ChainSelectionError as exc:
            return _root.shared._chain_selection_error_response(exc)
        protocols = _root.request_dex_protocols_arg()
        payload = request.get_json(silent=True) or {}
        full_refresh = request.args.get("full", "").strip().lower() in {"1", "true", "yes"} or bool(payload.get("full"))
        refresh_state = {
            "requested": "full" if full_refresh else "incremental",
            "running": True,
            "ok": None,
            "message": "全量刷新进行中" if full_refresh else "增量刷新进行中",
        }
        try:
            if full_refresh:
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
            payload = _root.dex_pool_candidates_payload(
                chain_id=selected_chain_id,
                protocols=protocols,
                refresh=True,
                force_full=full_refresh,
            )
            payload["chain_selection"] = chain_selection
            payload["refresh_state"] = _root._refresh_state_summary(payload, {**refresh_state, "running": False})
            payload.setdefault("candidates", payload.get("pairs") or [])
            page, page_size, query, quality = _root.candidate_pagination_args()
            return jsonify(
                _root._attach_dex_strategy(
                    _root.paginate_candidate_payload(payload, page=page, page_size=page_size, query=query, quality=quality)
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
    @app.get("/api/dex/submissions")
    def dex_submission_review():
        cache_key = _submission_response_cache_key()
        cached = _cached_submission_response_payload(cache_key)
        if cached is not None:
            return jsonify(cached)
        try:
            chain_selection, _selected_chain_id = _root.selected_chain_id_from_request()
        except ChainSelectionError as exc:
            return _root.shared._chain_selection_error_response(exc)
        limit = _root.shared.request_int_arg("limit", 300, minimum=1, maximum=1000)
        protocols = _root.request_dex_protocols_arg()
        networks = _root.submission_networks_from_request(chain_selection)
        _root._bootstrap_dex_surface_dependencies(
            chain_id=_selected_chain_id,
            protocols=protocols,
            flow_name="dex_submission_review",
        )
        route_framework = _root._submission_review_framework_payload(
            chain_id=_selected_chain_id,
            protocols=protocols,
        )
        groups = _root.shared._cow_execution_attempt_groups(
            lambda **kwargs: _root.load_recent_cow_execution_attempts_jsonl(
                _root.shared.COW_EXECUTION_ATTEMPT_LOG_ROOT,
                retention_days=_root.shared.COW_EXECUTION_RETENTION_DAYS,
                **kwargs,
            ),
            limit=limit,
            networks=networks,
            dedupe_by_route_evidence=False,
            group_loader=lambda **kwargs: load_recent_cow_execution_attempts_jsonl_grouped(
                _root.shared.COW_EXECUTION_ATTEMPT_LOG_ROOT,
                retention_days=_root.shared.COW_EXECUTION_RETENTION_DAYS,
                **kwargs,
            ),
        )
        # The submission review page intentionally shows only records that
        # produced a chain tx hash.  Flattening all rows with a global limit
        # can accidentally drop an older submitted transaction behind a huge
        # pile of quote-only/blocked attempts, so pass per-category rows
        # through instead of truncating by combined most-recent count.
        attempt_rows = [
            row
            for group in groups.values()
            for row in group.get("attempts") or []
            if isinstance(row, dict)
        ]
        attempt_rows.sort(
            key=lambda row: str(row.get("created_at") or row.get("observed_at") or ""),
            reverse=True,
        )
        response_payload = {
            "mode": "dex_submission_review",
            **_root.dex_submission_review_payload(
                attempts=attempt_rows,
                groups=groups,
                source="jsonl",
            ),
            "routeFramework": route_framework,
            "chain_selection": chain_selection,
            "dex_strategy": _root.request_dex_strategy_arg(),
        }
        response_payload.setdefault("attempts", [])
        response_payload.setdefault("summary", {})
        success_rows = _success_submission_rows(attempt_rows)
        response_payload["attempts"] = success_rows[:100]
        summary = response_payload.setdefault("summary", {})
        summary["attemptCount"] = len(success_rows)
        summary["successCount"] = len(success_rows)
        summary["failedCount"] = 0
        summary["blockedCount"] = 0
        summary["businessBlockedCount"] = 0
        summary["systemErrorCount"] = 0
        _cache_submission_response_payload(cache_key, response_payload)
        return jsonify(response_payload)

    @app.get("/api/dex/submissions/failed")
    @app.get("/api/dex/submissions/failed/<category>")
    def dex_submission_failed_review(category: str = "all"):
        category = str(category or "all").strip().lower()
        if category not in _FAILURE_CATEGORY_ORDER:
            return jsonify(
                {
                    "ok": False,
                    "mode": "dex_submission_failed_review",
                    "error": "unknown_failure_category",
                    "category": category,
                    "available_categories": list(_FAILURE_CATEGORY_ORDER),
                }
            ), 404
        cache_key = _submission_response_cache_key()
        cached = _cached_submission_response_payload(cache_key)
        if cached is not None:
            return jsonify(cached)
        try:
            chain_selection, _selected_chain_id = _root.selected_chain_id_from_request()
        except ChainSelectionError as exc:
            return _root.shared._chain_selection_error_response(exc)
        limit = _root.shared.request_int_arg("limit", 300, minimum=1, maximum=1000)
        protocols = _root.request_dex_protocols_arg()
        networks = _root.submission_networks_from_request(chain_selection)
        _root._bootstrap_dex_surface_dependencies(
            chain_id=_selected_chain_id,
            protocols=protocols,
            flow_name="dex_submission_failed_review",
        )
        route_framework = _root._submission_review_framework_payload(
            chain_id=_selected_chain_id,
            protocols=protocols,
        )
        groups = _root.shared._cow_execution_attempt_groups(
            lambda **kwargs: _root.load_recent_cow_execution_attempts_jsonl(
                _root.shared.COW_EXECUTION_ATTEMPT_LOG_ROOT,
                retention_days=_root.shared.COW_EXECUTION_RETENTION_DAYS,
                **kwargs,
            ),
            limit=limit,
            networks=networks,
            dedupe_by_route_evidence=False,
            group_loader=lambda **kwargs: load_recent_cow_execution_attempts_jsonl_grouped(
                _root.shared.COW_EXECUTION_ATTEMPT_LOG_ROOT,
                retention_days=_root.shared.COW_EXECUTION_RETENTION_DAYS,
                **kwargs,
            ),
        )
        attempt_rows = [
            row
            for group in groups.values()
            for row in group.get("attempts") or []
            if isinstance(row, dict)
        ]
        failed_rows = _failed_submission_rows(attempt_rows)
        failed_rows.sort(
            key=lambda row: str(row.get("created_at") or row.get("observed_at") or ""),
            reverse=True,
        )
        category_counts = {item: 0 for item in _FAILURE_CATEGORY_ORDER if item != "all"}
        for row in failed_rows:
            if not isinstance(row, dict):
                continue
            category_key = _failed_submission_category(row)
            if category_key in category_counts:
                category_counts[category_key] += 1
        if category != "all":
            failed_rows = [
                row for row in failed_rows
                if isinstance(row, dict) and _failed_submission_category(row) == category
            ]
        system_failed_rows = _system_failed_submission_rows(failed_rows)
        business_blocked_rows = _business_blocked_submission_rows(failed_rows)
        failed_count = len(system_failed_rows)
        response_payload = {
            "mode": "dex_submission_failed_review",
            "error_category": category,
            "source": "jsonl",
            "summary": {
                "attemptCount": len(failed_rows),
                "successCount": 0,
                "failedCount": failed_count,
                "systemErrorCount": failed_count,
                "businessBlockedCount": len(business_blocked_rows),
                "blockedCount": len(business_blocked_rows),
            },
            "categoryCounts": category_counts,
            "groups": groups or {},
            "attempts": failed_rows[:100],
            "routeFramework": route_framework,
            "chain_selection": chain_selection,
            "dex_strategy": _root.request_dex_strategy_arg(),
        }
        _cache_submission_response_payload(cache_key, response_payload)
        return jsonify(response_payload)

    @app.get("/api/dex/submissions/success")
    def dex_submission_success_review():
        cache_key = _submission_response_cache_key()
        cached = _cached_submission_response_payload(cache_key)
        if cached is not None:
            return jsonify(cached)
        try:
            chain_selection, _selected_chain_id = _root.selected_chain_id_from_request()
        except ChainSelectionError as exc:
            return _root.shared._chain_selection_error_response(exc)
        limit = _root.shared.request_int_arg("limit", 300, minimum=1, maximum=1000)
        protocols = _root.request_dex_protocols_arg()
        networks = _root.submission_networks_from_request(chain_selection)
        _root._bootstrap_dex_surface_dependencies(
            chain_id=_selected_chain_id,
            protocols=protocols,
            flow_name="dex_submission_success_review",
        )
        route_framework = _root._submission_review_framework_payload(
            chain_id=_selected_chain_id,
            protocols=protocols,
        )
        groups = _root.shared._cow_execution_attempt_groups(
            lambda **kwargs: _root.load_recent_cow_execution_attempts_jsonl(
                _root.shared.COW_EXECUTION_ATTEMPT_LOG_ROOT,
                retention_days=_root.shared.COW_EXECUTION_RETENTION_DAYS,
                **kwargs,
            ),
            limit=limit,
            networks=networks,
            dedupe_by_route_evidence=False,
            group_loader=lambda **kwargs: load_recent_cow_execution_attempts_jsonl_grouped(
                _root.shared.COW_EXECUTION_ATTEMPT_LOG_ROOT,
                retention_days=_root.shared.COW_EXECUTION_RETENTION_DAYS,
                **kwargs,
            ),
        )
        attempt_rows = [
            row
            for group in groups.values()
            for row in group.get("attempts") or []
            if isinstance(row, dict)
        ]
        success_rows = _success_submission_rows(attempt_rows)
        success_rows.sort(
            key=lambda row: str(row.get("created_at") or row.get("observed_at") or ""),
            reverse=True,
        )
        response_payload = {
            "mode": "dex_submission_success_review",
            "source": "jsonl",
            "summary": {
                "attemptCount": len(success_rows),
                "successCount": len(success_rows),
                "failedCount": 0,
                "systemErrorCount": 0,
                "businessBlockedCount": 0,
                "blockedCount": 0,
            },
            "groups": groups or {},
            "attempts": success_rows[:100],
            "routeFramework": route_framework,
            "chain_selection": chain_selection,
            "dex_strategy": _root.request_dex_strategy_arg(),
        }
        _cache_submission_response_payload(cache_key, response_payload)
        return jsonify(response_payload)

    @app.post("/api/dex/submissions/clear")
    def dex_submission_clear():
        _clear_submission_response_cache()
        try:
            chain_selection, _selected_chain_id = _root.selected_chain_id_from_request()
        except ChainSelectionError as exc:
            return _root.shared._chain_selection_error_response(exc)
        networks = _root.submission_networks_from_request(chain_selection)
        results = [
            _root.clear_cow_execution_attempts_jsonl(
                _root.shared.COW_EXECUTION_ATTEMPT_LOG_ROOT,
                networks=networks,
            )
        ]
        return jsonify(
            {
                "ok": True,
                "mode": "dex_submission_clear",
                "chain_selection": chain_selection,
                "dex_strategy": _root.request_dex_strategy_arg(),
                "networks": networks,
                "cleared": sum(int(item.get("cleared") or 0) for item in results),
                "results": results,
            }
        )
