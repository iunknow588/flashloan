from __future__ import annotations

from flask import jsonify, request

from core.chain_selection import ChainSelectionError
from dex.runtime.dex_closed_loop_review_queue import (
    SCAN_PRIORITY,
    USER_PRIORITY,
    normalize_closed_loop_group_key,
)
from web.data_routes import dex_surfaces as _root


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
        if tx_hash:
            continue
        rows.append(row)
    return rows


def _merge_failed_submission_rows_into_payload(
    response_payload: dict,
    failed_rows: list[dict],
) -> dict:
    """Include failed quote/review rows on the main submission review page.

    The generic submission payload intentionally only returns records with a
    chain tx hash.  Our failure classifier also handles quote-stage timeout
    records that never reached broadcast, so those rows are merged back into
    the page instead of being silently hidden behind a count-only summary.
    """
    attempts = response_payload.get("attempts")
    if not isinstance(attempts, list):
        attempts = []
    seen = set()
    for row in attempts:
        if isinstance(row, dict):
            seen.add(str(row.get("tx_hash") or row.get("transaction_hash") or row.get("pair") or "").strip().lower())
    added = 0
    for row in failed_rows:
        if not isinstance(row, dict):
            continue
        key = str(row.get("tx_hash") or row.get("transaction_hash") or row.get("pair") or "").strip().lower()
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
    summary["failedCount"] = len(failed_rows)
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
        scanner.set_auto_execute_enabled(enabled)
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
        failed_rows = _failed_submission_rows(attempt_rows)
        _merge_failed_submission_rows_into_payload(response_payload, failed_rows)
        return jsonify(response_payload)

    @app.get("/api/dex/submissions/failed")
    def dex_submission_failed_review():
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
        failed_count = len(failed_rows)
        response_payload = {
            "mode": "dex_submission_failed_review",
            "source": "jsonl",
            "summary": {
                "attemptCount": failed_count,
                "successCount": 0,
                "failedCount": failed_count,
                "blockedCount": 0,
            },
            "groups": groups or {},
            "attempts": failed_rows[:100],
            "routeFramework": route_framework,
            "chain_selection": chain_selection,
            "dex_strategy": _root.request_dex_strategy_arg(),
        }
        return jsonify(response_payload)

    @app.post("/api/dex/submissions/clear")
    def dex_submission_clear():
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
