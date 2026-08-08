import os

from flask import jsonify, request

from core.sensitive_data import redact_sensitive_text
from debt_pool import validate_liquidatable_context
from page_state import normalize_execution_phase, normalize_tx_hash, receipt_status
from web.route_context import RouteContext

ROUTE_CONTEXT = RouteContext()

CANONICAL_ROUTE_FAILURE_STATES = {
    "submission_blocked",
    "submission_failed",
    "static_call_failed",
    "confirmed_failed",
}


def panel_call(name: str, *args, **kwargs):
    return ROUTE_CONTEXT.call(name, *args, **kwargs)


def route_error_message(error: object | None) -> str | None:
    if error is None:
        return None
    return redact_sensitive_text(error)


def request_int_arg(name: str, default: int, *, minimum: int | None = None, maximum: int | None = None) -> int:
    raw = request.args.get(name, str(default))
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = int(default)
    if minimum is not None:
        value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value


def _optional_request_int_arg(name: str) -> int | None:
    raw = request.args.get(name, "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _request_market_kwargs() -> dict:
    try:
        args = request.args
    except RuntimeError:
        return {}
    market_id = args.get("market_id", "").strip() or None
    raw_chain_id = args.get("chain_id", "").strip()
    try:
        chain_id = int(raw_chain_id) if raw_chain_id else None
    except (TypeError, ValueError):
        chain_id = None
    kwargs = {}
    if market_id is not None:
        kwargs["market_id"] = market_id
    if chain_id is not None:
        kwargs["chain_id"] = chain_id
    return kwargs


def request_liquidatable_context(account: str) -> dict | None:
    context_keys = {"checked_at", "block_number", "candidate_hash", "source_pool", "health_factor"}
    if not any(request.args.get(key) for key in context_keys) and request.args.get("from", "").strip() != "debt_pool":
        return None
    return {
        "account": account,
        "checked_at": request.args.get("checked_at"),
        "block_number": request.args.get("block_number"),
        "candidate_hash": request.args.get("candidate_hash"),
        "source_pool": request.args.get("source_pool"),
        "health_factor": request.args.get("health_factor"),
    }


def validate_request_liquidatable_context(account: str) -> dict:
    context = request_liquidatable_context(account)
    if context is None:
        return {"present": False, "fresh": True, "blocked_reasons": [], "context": None}
    controls = panel_call("liquidation_execution_controls")
    return {
        "present": True,
        **validate_liquidatable_context(
            context,
            account=account,
            max_age_seconds=int(controls.get("max_payload_age_seconds") or 30),
            latest_block_number=_optional_request_int_arg("latest_block_number"),
            max_block_lag=request_int_arg("max_block_lag", 3, minimum=0),
        ),
    }


def stale_liquidatable_context_response(account: str, validation: dict) -> tuple[dict, int]:
    return (
        {
            "error": "liquidatable context is stale; refresh prediction/quote/preflight before submit",
            "account": account,
            "state": "submission_blocked",
            "execution_phase": "context_expired",
            "blocked_reasons": validation.get("blocked_reasons") or [],
            "liquidatable_context": validation,
        },
        400,
    )


def ensure_request_liquidatable_context(account: str) -> dict | None:
    validation = validate_request_liquidatable_context(account)
    if validation.get("present") and not validation.get("fresh"):
        response, status = stale_liquidatable_context_response(account, validation)
        return jsonify(response), status
    return None


def _float_or_none(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def classify_liquidation_payload_block(account: str, snapshot: dict | None, error: object | None = None) -> dict | None:
    snapshot = snapshot if isinstance(snapshot, dict) else {}
    summary = snapshot.get("summary") if isinstance(snapshot.get("summary"), dict) else {}
    context = snapshot.get("context") if isinstance(snapshot.get("context"), dict) else {}
    core = context.get("core_opportunity") if isinstance(context.get("core_opportunity"), dict) else {}
    candidates = snapshot.get("liquidation_candidates") if isinstance(snapshot.get("liquidation_candidates"), list) else []
    recommended = snapshot.get("recommended_candidate") if isinstance(snapshot.get("recommended_candidate"), dict) else {}
    error_text = route_error_message(error) if error is not None else ""
    health_factor = _float_or_none(summary.get("health_factor") or core.get("health_factor"))

    reason_code = None
    blocked_reasons: list[str] = []
    detail = None
    if health_factor is not None and health_factor >= 1:
        reason_code = "ACCOUNT_NOT_LIQUIDATABLE"
        blocked_reasons.append("account_not_liquidatable")
        detail = f"health_factor={health_factor:.18g} is not below 1.0"
        if not candidates and not recommended and snapshot.get("found"):
            blocked_reasons.append("no_liquidation_candidate")
            detail += "; pool cache has no recommended debt/collateral asset pair"
    elif not candidates and not recommended and snapshot.get("found"):
        reason_code = "NO_EXECUTABLE_CANDIDATE"
        blocked_reasons.append("no_liquidation_candidate")
        core_profit = _float_or_none(core.get("estimated_operator_net_profit_usd"))
        if core_profit is None:
            liquidation_profit = snapshot.get("liquidation_profit") if isinstance(snapshot.get("liquidation_profit"), dict) else {}
            core_profit = _float_or_none(liquidation_profit.get("operator_net_profit_usd"))
        if core_profit is not None and core_profit < 1.0:
            blocked_reasons.append("profit_below_minimum")
            detail = "no executable debt/collateral pair; estimated operator profit is below 1U; manual test only"
        else:
            detail = "pool cache has no recommended debt/collateral asset pair"
    elif error_text:
        lowered = error_text.lower()
        if "recommended_candidate" in lowered or "candidate" in lowered:
            reason_code = "NO_EXECUTABLE_CANDIDATE"
            blocked_reasons.append("no_liquidation_candidate")
        elif "not liquidatable" in lowered:
            reason_code = "ACCOUNT_NOT_LIQUIDATABLE"
            blocked_reasons.append("account_not_liquidatable")
        elif "quote" in lowered:
            reason_code = "QUOTE_FAILED"
            blocked_reasons.append("quote_failed")
        else:
            reason_code = "PAYLOAD_BUILD_FAILED"
            blocked_reasons.append("payload_build_failed")
        detail = error_text

    if not reason_code:
        return None
    return {
        "error": detail or reason_code,
        "reason_code": reason_code,
        "account": account,
        "state": "submission_blocked",
        "execution_phase": "payload_blocked",
        "blocked_reasons": blocked_reasons,
        "preflight": {
            "static_call_required": True,
            "static_call_status": "blocked",
            "static_call_passed": False,
            "static_call_error": detail or reason_code,
        },
        "checks": {
            "health_factor": health_factor,
            "candidate_count": len(candidates),
            "recommended_candidate_present": bool(recommended),
            "payload_state": core.get("payload_state"),
            "quote_viable": core.get("quote_viable"),
            "static_call_status": core.get("static_call_status"),
        },
        "cached_snapshot": snapshot or None,
    }


def liquidation_coverage_payload(pool_address: str, panel=None, *, market_id: str | None = None, chain_id: int | None = None) -> dict:
    source_panel = panel or ROUTE_CONTEXT.panel
    try:
        progress = getattr(source_panel, "liquidation_discovery_progress")(
            pool_address,
            market_id=market_id,
            chain_id=chain_id,
        )
    except TypeError:
        if market_id is not None or chain_id is not None:
            raise
        progress = getattr(source_panel, "liquidation_discovery_progress")(pool_address)
    latest_to = progress.get("latest_recent_to_block")
    earliest_from = progress.get("earliest_backfill_from_block")
    latest_gap_from = None
    latest_gap_to = None
    if latest_to is not None and earliest_from is not None and int(earliest_from) > int(latest_to) + 1:
        latest_gap_from = int(latest_to) + 1
        latest_gap_to = int(earliest_from) - 1
    return {
        "pool_address": pool_address or None,
        "covered_from_block": earliest_from,
        "covered_to_block": latest_to,
        "latest_gap_from_block": latest_gap_from,
        "latest_gap_to_block": latest_gap_to,
        "has_gap": latest_gap_from is not None,
        "progress": progress,
    }


def liquidation_failure_response(account: str, payload: dict | None, error: Exception) -> dict:
    response = {"error": route_error_message(error), "account": account}
    if not isinstance(payload, dict):
        try:
            snapshot = panel_call("liquidation_account_cached_payload", account)
        except Exception:
            snapshot = {}
        blocked = classify_liquidation_payload_block(account, snapshot, error)
        if blocked is not None:
            return blocked
        return response
    account_report = payload.get("account_report") or {}
    response.update(
        {
            "executor": payload.get("executor"),
            "request": payload.get("request") or {},
            "preflight": payload.get("preflight") or {},
            "execution_phase": payload.get("execution_phase") or (payload.get("context") or {}).get("phase"),
            "state": payload.get("state"),
            "submission_allowed": payload.get("submission_allowed"),
            "blocked_reasons": payload.get("blocked_reasons") or [],
            "checks": payload.get("checks") or {},
            "dex_quote": payload.get("dex_quote") or {},
            "tx_hash": normalize_tx_hash(payload),
            "receipt": payload.get("receipt") or {},
            "account_report": account_report,
            "execution_plan": account_report.get("execution_plan") if isinstance(account_report, dict) else None,
            "execution_controls": payload.get("execution_controls") or panel_call("liquidation_execution_controls"),
        }
    )
    return response


def route_failure_state(mode: str, response: dict) -> str:
    state = str(response.get("state") or "").strip()
    if state in CANONICAL_ROUTE_FAILURE_STATES:
        return state
    if receipt_status(response.get("receipt") or {}) == 0:
        return "confirmed_failed"
    blocked = response.get("blocked_reasons") or []
    if blocked:
        return "submission_blocked"
    preflight = response.get("preflight") if isinstance(response.get("preflight"), dict) else {}
    if mode == "static_call" and (
        preflight.get("static_call_passed") is False
        or preflight.get("static_call_error")
        or str(preflight.get("static_call_status") or "").lower() in {"error", "failed"}
    ):
        return "static_call_failed"
    return "submission_failed"


def route_failure_phase(response: dict, failure_state: str) -> str:
    return normalize_execution_phase(response, fallback_state=failure_state) or failure_state


def record_liquidation_route_failure(account: str, mode: str, response: dict, error: Exception) -> None:
    safe_error = route_error_message(error)
    failure_state = route_failure_state(mode, response)
    failure_phase = route_failure_phase(response, failure_state)
    market_kwargs = _request_market_kwargs()
    preflight = {
        **(response.get("preflight") or {}),
        "execution_phase": failure_phase,
        "receipt_status": receipt_status(response.get("receipt") or {}),
        "route_failure_state": failure_state,
        "route_error": safe_error,
    }
    panel_call(
        "record_liquidation_execution_attempt_safely",
        account=account,
        mode=mode,
        state=failure_state,
        blocked_reasons=response.get("blocked_reasons") or [],
        request_payload=response.get("request") or {},
        quote=response.get("dex_quote") or {},
        preflight=preflight,
        tx_hash=normalize_tx_hash(response),
        receipt=response.get("receipt") or {},
        error=safe_error,
        **market_kwargs,
    )


def record_liquidation_route_success(account: str, mode: str, result: dict) -> None:
    receipt = result.get("receipt") or {}
    state = "confirmed_success" if int(receipt.get("status") or 0) == 1 else "confirmed_failed"
    execution_phase = result.get("execution_phase") or state
    tx_hash = normalize_tx_hash(result)
    market_kwargs = _request_market_kwargs()
    panel_call(
        "record_liquidation_execution_attempt_safely",
        account=account,
        mode=mode,
        state=state,
        request_payload=result.get("request") or {},
        quote=result.get("dex_quote") or {},
        preflight={
            **(result.get("preflight") or {}),
            "execution_phase": execution_phase,
            "receipt_status": receipt_status(receipt),
        },
        tx_hash=tx_hash,
        receipt=receipt,
        **market_kwargs,
    )


def register_liquidation_routes(app, panel) -> None:
    ROUTE_CONTEXT.bind(panel, globals())

    @app.get("/api/liquidation-health")
    def liquidation_health_api():
        force = request.args.get("force", "").strip().lower() in {"1", "true", "yes"}
        return jsonify(panel_call("liquidation_health_payload", force=force))

    @app.get("/api/liquidation/borrow-pool")
    def liquidation_borrow_pool_api():
        market_kwargs = _request_market_kwargs()
        pagination_requested = any(
            name in request.args
            for name in ("page_size", "risk_page", "high_page", "core_page", "market_id", "chain_id")
        )
        if not pagination_requested:
            return jsonify(panel_call("liquidation_borrow_pool_payload", **market_kwargs))
        page_size = request_int_arg("page_size", 20, minimum=1, maximum=100)
        return jsonify(
            panel_call(
                "liquidation_borrow_pool_payload",
                page_size=page_size,
                risk_page=request_int_arg("risk_page", 1, minimum=1),
                high_page=request_int_arg("high_page", 1, minimum=1),
                core_page=request_int_arg("core_page", 1, minimum=1),
                skip_schema=True,
                **market_kwargs,
            )
        )

    @app.get("/api/debt-pool/decision")
    def debt_pool_decision_api():
        payload = panel_call("liquidation_borrow_pool_payload")
        return jsonify(payload.get("debt_pool_decision") or {})

    @app.post("/api/liquidation/borrow-pool/scan")
    def liquidation_borrow_pool_scan_api():
        payload = request.get_json(silent=True) or {}
        market_kwargs = _request_market_kwargs()
        force = (
            request.args.get("force", "").strip().lower() in {"1", "true", "yes"}
            or str(payload.get("force", "")).strip().lower() in {"1", "true", "yes"}
        )
        pagination_requested = any(
            name in request.args
            for name in ("page_size", "risk_page", "high_page", "core_page", "market_id", "chain_id")
        )
        if not pagination_requested:
            return jsonify(panel_call("liquidation_borrow_pool_scan_payload", force=force, **market_kwargs))
        page_size = request_int_arg("page_size", 20, minimum=1, maximum=100)
        return jsonify(
            panel_call(
                "liquidation_borrow_pool_scan_payload",
                force=force,
                page_size=page_size,
                risk_page=request_int_arg("risk_page", 1, minimum=1),
                high_page=request_int_arg("high_page", 1, minimum=1),
                core_page=request_int_arg("core_page", 1, minimum=1),
                **market_kwargs,
            )
        )

    @app.get("/api/liquidation/borrow-pool/batches")
    def liquidation_borrow_pool_batches_api():
        limit = request_int_arg("limit", 20, minimum=1, maximum=100)
        market_kwargs = _request_market_kwargs()
        database_url = panel_call("database_url_or_none")
        if not database_url:
            return jsonify({"configured": False, "batches": [], "error": "DATABASE_URL is required"})
        try:
            return jsonify({"configured": True, "batches": panel_call("db_load_liquidation_borrow_health_scan_batches", database_url, limit=limit, **market_kwargs)})
        except Exception as exc:
            return jsonify({"configured": True, "batches": [], "error": route_error_message(exc)}), 400

    @app.get("/api/liquidation/borrow-pool/latest-batch")
    def liquidation_borrow_pool_latest_batch_api():
        market_kwargs = _request_market_kwargs()
        database_url = panel_call("database_url_or_none")
        if not database_url:
            return jsonify({"configured": False, "batch": None, "error": "DATABASE_URL is required"})
        try:
            batches = panel_call("db_load_liquidation_borrow_health_scan_batches", database_url, limit=1, **market_kwargs)
            return jsonify({"configured": True, "batch": batches[0] if batches else None})
        except Exception as exc:
            return jsonify({"configured": True, "batch": None, "error": route_error_message(exc)}), 400

    @app.get("/api/liquidation/high-frequency-pool")
    def liquidation_high_frequency_pool_api():
        limit = request_int_arg("limit", 100, minimum=1, maximum=500)
        market_kwargs = _request_market_kwargs()
        database_url = panel_call("database_url_or_none")
        if not database_url:
            return jsonify({"configured": False, "rows": [], "error": "DATABASE_URL is required"})
        try:
            rows = panel_call("db_load_liquidation_high_frequency_pool", database_url, limit=limit, **market_kwargs)
            return jsonify({"configured": True, "count": len(rows), "rows": rows})
        except Exception as exc:
            return jsonify({"configured": True, "rows": [], "error": route_error_message(exc)}), 400

    @app.get("/api/liquidation/core-opportunities")
    def liquidation_core_opportunities_api():
        limit = request_int_arg("limit", 100, minimum=1, maximum=500)
        market_kwargs = _request_market_kwargs()
        database_url = panel_call("database_url_or_none")
        if not database_url:
            return jsonify({"configured": False, "rows": [], "error": "DATABASE_URL is required"})
        try:
            rows = panel_call("liquidation_core_rows_with_execution", database_url, limit=limit, **market_kwargs)
            return jsonify({"configured": True, "count": len(rows), "rows": rows})
        except Exception as exc:
            return jsonify({"configured": True, "rows": [], "error": route_error_message(exc)}), 400

    @app.post("/api/liquidation-discovery")
    def liquidation_discovery_api():
        payload = request.get_json(silent=True) or {}
        force_full = request.args.get("full", "").strip().lower() in {"1", "true", "yes"} or bool(payload.get("full"))
        result = panel_call("discover_and_sync_liquidation_accounts", force_full=force_full)
        LIQUIDATION_SCAN_CACHE["updated_at"] = 0.0
        return jsonify(result)

    @app.get("/api/liquidation/account-backfill")
    def liquidation_account_backfill_status_api():
        return jsonify(panel_call("account_backfill_status_payload"))

    @app.post("/api/liquidation/account-backfill/start")
    def liquidation_account_backfill_start_api():
        return jsonify(panel_call("start_account_backfill_background")), 202

    @app.post("/api/liquidation/account-backfill/stop")
    def liquidation_account_backfill_stop_api():
        return jsonify(panel_call("request_stop_account_backfill"))

    @app.get("/api/liquidation-settings")
    def liquidation_settings_api():
        config = liquidation_runtime_config()
        return jsonify(
            {
                "refresh_profile": liquidation_scan_refresh_profile(),
                "retention_days": liquidation_retention_days(),
                "scan_interval_seconds": liquidation_scan_interval_seconds(),
                "discovery_interval_seconds": liquidation_discovery_interval_seconds(),
                "raw": config,
            }
        )

    @app.post("/api/liquidation-settings")
    def update_liquidation_settings_api():
        payload = request.get_json(silent=True) or {}
        config = write_liquidation_runtime_config(payload)
        LIQUIDATION_ACCOUNT_CACHE["updated_at"] = 0.0
        LIQUIDATION_SCAN_CACHE["updated_at"] = 0.0
        return jsonify(
            {
                "saved": True,
                "refresh_profile": liquidation_scan_refresh_profile(),
                "retention_days": liquidation_retention_days(),
                "scan_interval_seconds": liquidation_scan_interval_seconds(),
                "discovery_interval_seconds": liquidation_discovery_interval_seconds(),
                "raw": config,
            }
        )

    @app.get("/api/liquidation/config-health")
    def liquidation_config_health_api():
        raw_chain_id = request.args.get("chain_id", "").strip()
        chain_id = request_int_arg("chain_id", 0) if raw_chain_id else None
        return jsonify(panel_call("liquidation_config_health", chain_id=chain_id))

    @app.get("/api/liquidation/market")
    def liquidation_market_api():
        return jsonify(panel_call("liquidation_market_payload"))

    @app.get("/api/liquidation/execution-attempts")
    def liquidation_execution_attempts_api():
        limit = request_int_arg("limit", 20, minimum=1, maximum=100)
        return jsonify(panel_call("recent_liquidation_execution_attempts", limit=limit, **_request_market_kwargs()))

    @app.get("/api/liquidation/failure-samples")
    def liquidation_failure_samples_api():
        limit = request_int_arg("limit", 20, minimum=1, maximum=100)
        return jsonify(panel_call("recent_liquidation_failure_samples", limit=limit, **_request_market_kwargs()))

    @app.get("/api/liquidation/account/<account>/attempts")
    def liquidation_account_attempts_api(account: str):
        limit = request_int_arg("limit", 20, minimum=1, maximum=100)
        return jsonify(panel_call("liquidation_execution_attempts_for_account", account, limit=limit, **_request_market_kwargs()))

    @app.get("/api/liquidation/account/<account>/samples")
    def liquidation_account_samples_api(account: str):
        limit = request_int_arg("limit", 20, minimum=1, maximum=100)
        return jsonify(panel_call("liquidation_failure_samples_for_account", account, limit=limit, **_request_market_kwargs()))

    @app.get("/api/liquidation/pause-guard")
    def liquidation_pause_guard_api():
        return jsonify(panel_call("liquidation_pause_guard_status"))

    @app.post("/api/liquidation/pause-guard/clear")
    def liquidation_pause_guard_clear_api():
        return jsonify(panel_call("clear_liquidation_pause_guard_status"))

    @app.get("/api/liquidation/discovery-coverage")
    def liquidation_discovery_coverage_api():
        pool_address = request.args.get("pool", panel_call("aave_pool_address")).strip()
        return jsonify(liquidation_coverage_payload(pool_address, **_request_market_kwargs()))

    @app.get("/api/liquidation/account")
    def liquidation_account_api():
        account = request.args.get("account", "").strip()
        if not account:
            return jsonify({"error": "account is required"}), 400
        try:
            return jsonify(panel_call("liquidation_account_payload", account))
        except Exception as exc:
            return jsonify({"error": route_error_message(exc), "account": account}), 400

    @app.get("/api/liquidation/daemon/status")
    def liquidation_daemon_status_api():
        return jsonify(panel_call("liquidation_daemon_status_payload"))

    @app.get("/api/liquidation/account/cached")
    def liquidation_account_cached_api():
        account = request.args.get("account", "").strip()
        if not account:
            return jsonify({"error": "account is required"}), 400
        market_id = request.args.get("market_id", "").strip() or None
        chain_id = _optional_request_int_arg("chain_id")
        try:
            try:
                payload = panel_call("liquidation_account_cached_payload", account, market_id=market_id, chain_id=chain_id)
            except TypeError:
                if market_id is not None or chain_id is not None:
                    raise
                payload = panel_call("liquidation_account_cached_payload", account)
            return jsonify(payload)
        except Exception as exc:
            return jsonify({"error": route_error_message(exc), "account": account}), 400

    @app.get("/api/liquidation/account/payload")
    def liquidation_account_payload_api():
        account = request.args.get("account", "").strip()
        if not account:
            return jsonify({"error": "account is required"}), 400
        stale_context = ensure_request_liquidatable_context(account)
        if stale_context is not None:
            return stale_context
        try:
            deadline_seconds = request_int_arg("deadline_seconds", 300, minimum=1)
            allow_zero_min_out = request.args.get("allow_zero_min_out", "").strip().lower() in {"1", "true", "yes"}
            fast = request.args.get("fast", "").strip().lower() in {"1", "true", "yes"}
            if fast:
                try:
                    market_id = request.args.get("market_id", "").strip() or None
                    chain_id = _optional_request_int_arg("chain_id")
                    try:
                        snapshot = panel_call(
                            "liquidation_account_cached_payload",
                            account,
                            market_id=market_id,
                            chain_id=chain_id,
                        )
                    except TypeError:
                        if market_id is not None or chain_id is not None:
                            raise
                        snapshot = panel_call("liquidation_account_cached_payload", account)
                    blocked = classify_liquidation_payload_block(account, snapshot)
                    if blocked is not None:
                        return jsonify(blocked), 400
                except Exception:
                    pass
            return jsonify(
                panel_call(
                    "liquidation_execution_payload_for_account",
                    account,
                    deadline_seconds=deadline_seconds,
                    allow_zero_min_out=allow_zero_min_out,
                )
            )
        except Exception as exc:
            try:
                snapshot = panel_call("liquidation_account_cached_payload", account)
            except Exception:
                snapshot = {}
            blocked = classify_liquidation_payload_block(account, snapshot, exc)
            if blocked is not None:
                return jsonify(blocked), 400
            return jsonify({"error": route_error_message(exc), "account": account}), 400

    @app.post("/api/liquidation/account/preflight")
    def liquidation_account_preflight_api():
        account = request.args.get("account", "").strip()
        if not account:
            return jsonify({"error": "account is required"}), 400
        stale_context = ensure_request_liquidatable_context(account)
        if stale_context is not None:
            return stale_context
        try:
            payload = panel_call("liquidation_execution_payload_for_account", account)
            return jsonify(panel_call("simulate_liquidation_static_call", payload))
        except Exception as exc:
            return jsonify({"error": route_error_message(exc), "account": account}), 400

    @app.post("/api/liquidation/account/<account>/static-call-and-save")
    def liquidation_account_static_call_and_save_api(account: str):
        account = str(account or "").strip()
        if not account:
            return jsonify({"error": "account is required"}), 400
        stale_context = ensure_request_liquidatable_context(account)
        if stale_context is not None:
            return stale_context
        payload: dict | None = None
        try:
            payload = panel_call("liquidation_execution_payload_for_account", account)
            result = panel_call("simulate_liquidation_static_call", payload)
            preflight = result.get("preflight") or {}
            state = "static_call_passed" if preflight.get("static_call_passed") else "static_call_failed"
            panel_call(
                "record_liquidation_execution_attempt_safely",
                account=account,
                mode="static_call",
                state=state,
                blocked_reasons=result.get("blocked_reasons") or [],
                request_payload=result.get("request") or {},
                quote=result.get("dex_quote") or {},
                preflight=preflight,
                error=preflight.get("static_call_error"),
            )
            return jsonify(result)
        except Exception as exc:
            response = liquidation_failure_response(account, payload, exc)
            record_liquidation_route_failure(account, "static_call", response, exc)
            return jsonify(response), 400

    @app.get("/api/liquidation/preflight/<account>")
    def liquidation_account_preflight_by_path_api(account: str):
        account = str(account or "").strip()
        if not account:
            return jsonify({"error": "account is required"}), 400
        stale_context = ensure_request_liquidatable_context(account)
        if stale_context is not None:
            return stale_context
        try:
            payload = panel_call("liquidation_execution_payload_for_account", account)
            return jsonify(panel_call("simulate_liquidation_static_call", payload))
        except Exception as exc:
            return jsonify({"error": route_error_message(exc), "account": account}), 400

    @app.post("/api/liquidation/account/execute")
    def liquidation_account_execute_api():
        account = request.args.get("account", "").strip()
        force = request.args.get("force", "").strip().lower() in {"1", "true", "yes"}
        if not account:
            return jsonify({"error": "account is required"}), 400
        stale_context = ensure_request_liquidatable_context(account)
        if stale_context is not None:
            return stale_context
        payload: dict | None = None
        try:
            payload = panel_call("liquidation_execution_payload_for_account", account, require_executor=False, force=force)
            result = panel_call("execute_self_funded_liquidation_transaction", payload, force=force)
            record_liquidation_route_success(account, "self_funded_force" if force else "self_funded", result)
            return jsonify(result)
        except Exception as exc:
            response = liquidation_failure_response(account, payload, exc)
            record_liquidation_route_failure(account, "self_funded_force" if force else "self_funded", response, exc)
            return jsonify(response), 400

    @app.post("/api/liquidation/account/flashloan")
    def liquidation_account_flashloan_api():
        account = request.args.get("account", "").strip()
        force = request.args.get("force", "").strip().lower() in {"1", "true", "yes"}
        if not account:
            return jsonify({"error": "account is required"}), 400
        stale_context = ensure_request_liquidatable_context(account)
        if stale_context is not None:
            return stale_context
        payload: dict | None = None
        try:
            payload = panel_call("liquidation_execution_payload_for_account", account, force=force)
            result = panel_call("execute_flashloan_liquidation_transaction", payload, force=force)
            record_liquidation_route_success(account, "flashloan_force" if force else "flashloan", result)
            return jsonify(result)
        except Exception as exc:
            response = liquidation_failure_response(account, payload, exc)
            record_liquidation_route_failure(account, "flashloan_force" if force else "flashloan", response, exc)
            return jsonify(response), 400

    @app.get("/api/liquidation/samples")
    def liquidation_samples_api():
        manifest = panel_call("liquidation_sample_manifest")
        if not manifest:
            return jsonify({"error": "liquidation sample library not found", "samples": []}), 404
        return jsonify(manifest)

    @app.get("/api/liquidation/accounts")
    def liquidation_accounts_list_api():
        force = request.args.get("force", "").strip().lower() in {"1", "true", "yes"}
        page = request_int_arg("page", 1, minimum=1)
        page_size = request_int_arg("page_size", 20, minimum=1, maximum=100)
        market_id = request.args.get("market_id", "").strip() or None
        chain_id = _optional_request_int_arg("chain_id")
        database_url = panel_call("database_url_or_none")
        if database_url:
            try:
                paged = panel_call(
                    "db_load_liquidation_accounts_page",
                    database_url,
                    page=page,
                    page_size=page_size,
                    market_id=market_id,
                    chain_id=chain_id,
                )
                account_rows = [
                    row if isinstance(row, dict) else {"account": row}
                    for row in paged["accounts"]
                ]
                return jsonify(
                    {
                        "accounts": account_rows,
                        "account_rows": account_rows,
                        "count": paged["total_count"],
                        "source": "database",
                        "registry_window": panel_call("liquidation_account_registry_window", market_id=market_id, chain_id=chain_id),
                        "pagination": {
                            "page": paged["page"],
                            "page_size": paged["page_size"],
                            "total_count": paged["total_count"],
                            "page_count": paged["page_count"],
                        },
                    }
                )
            except Exception as exc:
                if not force:
                    return jsonify(
                        {
                            "accounts": [],
                            "count": 0,
                            "source": "database-error",
                            "error": route_error_message(exc),
                        }
                    )
        accounts, source = panel_call("load_liquidation_account_registry", force=force)
        offset = (page - 1) * page_size
        total_count = len(accounts)
        return jsonify(
            {
                "accounts": [{"account": account} for account in accounts[offset : offset + page_size]],
                "count": total_count,
                "source": source,
                "registry_window": panel_call("liquidation_account_registry_window", market_id=market_id, chain_id=chain_id),
                "pagination": {
                    "page": page,
                    "page_size": page_size,
                    "total_count": total_count,
                    "page_count": max(1, (total_count + page_size - 1) // page_size),
                },
            }
        )

    @app.post("/api/liquidation/accounts")
    def liquidation_accounts_api():
        payload = request.get_json(silent=True) or {}
        raw_accounts = payload.get("accounts")
        accounts = normalize_liquidation_account_values(raw_accounts)
        if not accounts:
            return jsonify({"error": "accounts is required"}), 400
        try:
            database_url = panel_call("database_url_or_none")
            if not database_url:
                return jsonify({"error": "DATABASE_URL is required"}), 400
            panel_call("ensure_database_schema_cached", database_url)
            panel_call(
                "db_upsert_liquidation_accounts",
                database_url,
                accounts,
                source=str(payload.get("source") or "manual"),
                active=True,
            )
            panel_call("db_prune_liquidation_accounts", database_url, retained_days=panel_call("liquidation_retention_days"))
        except Exception as exc:
            return jsonify({"error": route_error_message(exc)}), 400
        LIQUIDATION_ACCOUNT_CACHE["updated_at"] = 0.0
        LIQUIDATION_SCAN_CACHE["updated_at"] = 0.0
        return jsonify({"saved": True, "count": len(accounts), "source": "database", "accounts": accounts})
