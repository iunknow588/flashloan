import logging

from flask import jsonify, request

from bot.page_state import normalize_tx_hash, receipt_status
from bot.liquidation.scan_request import parse_scan_request
from web.control_panel_bnb_venus import bnb_venus_accounts_payload
from bot.liquidation.route_response import (
    classify_liquidation_payload_block,
    route_error_message,
    route_failure_phase,
    route_failure_state,
)
from web.data_routes.liquidation_route_request import (
    is_bnb_venus_request as _is_bnb_venus_request,
    optional_request_int_arg as _optional_request_int_arg,
    request_execution_target_kwargs as _request_execution_target_kwargs,
    request_int_arg,
    request_liquidatable_context,
    request_market_kwargs as _request_market_kwargs,
)
from web.control_panel_liquidation_route_helpers import (
    ROUTE_CONTEXT,
    ensure_request_liquidatable_context,
    liquidation_coverage_payload,
    liquidation_failure_response,
    panel_call,
    validate_request_liquidatable_context,
)

logger = logging.getLogger(__name__)


def record_liquidation_route_failure(
    account: str,
    mode: str,
    response: dict,
    error: Exception,
) -> None:
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
    ROUTE_CONTEXT.bind_panel_provider(panel)

    @app.get("/api/liquidation-health")
    def liquidation_health_api():
        force = request.args.get("force", "").strip().lower() in {"1", "true", "yes"}
        return jsonify(panel_call("liquidation_health_payload", force=force))

    @app.get("/api/liquidation/borrow-pool")
    def liquidation_borrow_pool_api():
        parsed = parse_scan_request(request.args)
        if not parsed.pagination_requested:
            return jsonify(panel_call("liquidation_borrow_pool_payload", **parsed.market.as_kwargs()))
        return jsonify(
            panel_call(
                "liquidation_borrow_pool_payload",
                skip_schema=True,
                **parsed.pagination.as_kwargs(),
                **parsed.market.as_kwargs(),
            )
        )

    @app.get("/api/debt-pool/decision")
    def debt_pool_decision_api():
        payload = panel_call("liquidation_borrow_pool_payload")
        return jsonify(payload.get("debt_pool_decision") or {})

    @app.post("/api/liquidation/borrow-pool/scan")
    def liquidation_borrow_pool_scan_api():
        payload = request.get_json(silent=True) or {}
        parsed = parse_scan_request(request.args, payload)
        if not parsed.pagination_requested:
            return jsonify(
                panel_call(
                    "liquidation_borrow_pool_scan_payload",
                    force=parsed.force,
                    **parsed.market.as_kwargs(),
                )
            )
        return jsonify(
            panel_call(
                "liquidation_borrow_pool_scan_payload",
                **parsed.as_kwargs(),
            )
        )

    @app.get("/api/liquidation/borrow-pool/batches")
    def liquidation_borrow_pool_batches_api():
        limit = request_int_arg("limit", 20, minimum=1, maximum=100)
        market_kwargs = _request_market_kwargs()
        database_url = panel_call("database_url_or_none")
        if not database_url:
            return jsonify({"configured": False, "batches": [], "error": "local KV store unavailable"})
        try:
            return jsonify({"configured": True, "batches": panel_call("db_load_liquidation_borrow_health_scan_batches", database_url, limit=limit, **market_kwargs)})
        except Exception as exc:
            return jsonify({"configured": True, "batches": [], "error": route_error_message(exc)}), 400

    @app.get("/api/liquidation/borrow-pool/latest-batch")
    def liquidation_borrow_pool_latest_batch_api():
        market_kwargs = _request_market_kwargs()
        database_url = panel_call("database_url_or_none")
        if not database_url:
            return jsonify({"configured": False, "batch": None, "error": "local KV store unavailable"})
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
            return jsonify({"configured": False, "rows": [], "error": "local KV store unavailable"})
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
            return jsonify({"configured": False, "rows": [], "error": "local KV store unavailable"})
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
                "scan_mode": config.get("LIQUIDATION_SCAN_MODE"),
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
                "scan_mode": config.get("LIQUIDATION_SCAN_MODE"),
                "raw": config,
            }
        )

    @app.get("/api/liquidation/config-health")
    def liquidation_config_health_api():
        return jsonify(panel_call("liquidation_config_health", **_request_market_kwargs()))

    @app.get("/api/liquidation/market")
    def liquidation_market_api():
        return jsonify(panel_call("liquidation_market_payload", **_request_market_kwargs()))

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
            return jsonify(panel_call("liquidation_account_payload", account, **_request_market_kwargs()))
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
            market_id = request.args.get("market_id", "").strip() or None
            chain_id = _optional_request_int_arg("chain_id")
            if _is_bnb_venus_request(market_id, chain_id):
                snapshot = panel_call(
                    "liquidation_account_cached_payload",
                    account,
                    market_id=market_id,
                    chain_id=chain_id,
                )
                return jsonify(
                    {
                        "error": "BNB Venus Core is observation-only; transaction payload generation is disabled",
                        "account": account,
                        "state": "submission_blocked",
                        "execution_phase": "observation_only",
                        "blocked_reasons": ["venus_core_observation_only"],
                        "cached_snapshot": snapshot,
                    }
                ), 400
            if fast:
                try:
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
                    logger.warning("fast liquidation payload cached snapshot failed", exc_info=True)
            return jsonify(
                panel_call(
                    "liquidation_execution_payload_for_account",
                    account,
                    deadline_seconds=deadline_seconds,
                    allow_zero_min_out=allow_zero_min_out,
                    **_request_execution_target_kwargs(),
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
            payload = panel_call("liquidation_execution_payload_for_account", account, **_request_execution_target_kwargs())
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
            payload = panel_call("liquidation_execution_payload_for_account", account, **_request_execution_target_kwargs())
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
            payload = panel_call("liquidation_execution_payload_for_account", account, **_request_execution_target_kwargs())
            return jsonify(panel_call("simulate_liquidation_static_call", payload))
        except Exception as exc:
            return jsonify({"error": route_error_message(exc), "account": account}), 400

    @app.post("/api/liquidation/account/execute")
    def liquidation_account_execute_api():
        account = request.args.get("account", "").strip()
        if not account:
            return jsonify({"error": "account is required"}), 400
        response = {
            "account": account,
            "error": "self-funded liquidation is disabled; this system executes liquidations through the flashloan executor only",
            "state": "submission_blocked",
            "execution_phase": "self_funded_disabled",
            "blocked_reasons": ["self_funded_execution_disabled", "flashloan_executor_required"],
            "mode": "flashloan",
        }
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
            payload = panel_call(
                "liquidation_execution_payload_for_account",
                account,
                force=force,
                **_request_execution_target_kwargs(),
            )
            result = panel_call("execute_flashloan_liquidation_transaction", payload, force=force)
            record_liquidation_route_success(account, "flashloan_force" if force else "flashloan", result)
            return jsonify(result)
        except Exception as exc:
            response = liquidation_failure_response(account, payload, exc)
            record_liquidation_route_failure(account, "flashloan_force" if force else "flashloan", response, exc)
            return jsonify(response), 400

    @app.get("/api/liquidation/samples")
    def liquidation_samples_api():
        library_path = getattr(ROUTE_CONTEXT.panel, "LIQUIDATION_SAMPLE_LIBRARY_PATH", None)
        manifest = panel_call("liquidation_sample_manifest", library_path=library_path)
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
        if _is_bnb_venus_request(market_id, chain_id):
            return jsonify(bnb_venus_accounts_payload(page=page, page_size=page_size))
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
                return jsonify({"error": "local KV store unavailable"}), 400
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
