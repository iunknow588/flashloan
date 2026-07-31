import os

from flask import jsonify, request

PANEL = None


def panel_call(name: str, *args, **kwargs):
    return getattr(PANEL, name)(*args, **kwargs)


def liquidation_coverage_payload(pool_address: str, panel=None) -> dict:
    source_panel = panel or PANEL
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
    response = {"error": str(error), "account": account}
    if not isinstance(payload, dict):
        return response
    account_report = payload.get("account_report") or {}
    response.update(
        {
            "executor": payload.get("executor"),
            "request": payload.get("request") or {},
            "preflight": payload.get("preflight") or {},
            "state": payload.get("state"),
            "submission_allowed": payload.get("submission_allowed"),
            "blocked_reasons": payload.get("blocked_reasons") or [],
            "checks": payload.get("checks") or {},
            "account_report": account_report,
            "execution_plan": account_report.get("execution_plan") if isinstance(account_report, dict) else None,
            "execution_controls": payload.get("execution_controls") or panel_call("liquidation_execution_controls"),
        }
    )
    return response


def record_liquidation_route_failure(account: str, mode: str, response: dict, error: Exception) -> None:
    panel_call(
        "record_liquidation_execution_attempt_safely",
        account=account,
        mode=mode,
        state=response.get("state") or "submission_failed",
        blocked_reasons=response.get("blocked_reasons") or [],
        request_payload=response.get("request") or {},
        quote=response.get("dex_quote") or {},
        preflight=response.get("preflight") or {},
        error=str(error),
    )


def record_liquidation_route_success(account: str, mode: str, result: dict) -> None:
    receipt = result.get("receipt") or {}
    state = "confirmed_success" if int(receipt.get("status") or 0) == 1 else "confirmed_failed"
    panel_call(
        "record_liquidation_execution_attempt_safely",
        account=account,
        mode=mode,
        state=state,
        request_payload=result.get("request") or {},
        quote=result.get("dex_quote") or {},
        preflight=result.get("preflight") or {},
        tx_hash=result.get("tx_hash"),
        receipt=receipt,
    )


def register_liquidation_routes(app, panel) -> None:
    global PANEL
    PANEL = panel
    globals().update(vars(panel))

    @app.get("/api/liquidation-health")
    def liquidation_health_api():
        force = request.args.get("force", "").strip().lower() in {"1", "true", "yes"}
        return jsonify(panel_call("liquidation_health_payload", force=force))

    @app.get("/api/liquidation/borrow-pool")
    def liquidation_borrow_pool_api():
        return jsonify(panel_call("liquidation_borrow_pool_payload"))

    @app.post("/api/liquidation/borrow-pool/scan")
    def liquidation_borrow_pool_scan_api():
        return jsonify(panel_call("liquidation_borrow_pool_scan_payload", force=True))

    @app.get("/api/liquidation/borrow-pool/batches")
    def liquidation_borrow_pool_batches_api():
        limit = max(1, min(int(request.args.get("limit", "20")), 100))
        database_url = panel_call("database_url_or_none")
        if not database_url:
            return jsonify({"configured": False, "batches": [], "error": "DATABASE_URL is required"})
        try:
            panel_call("ensure_database_schema", database_url)
            return jsonify({"configured": True, "batches": panel_call("db_load_liquidation_borrow_health_scan_batches", database_url, limit=limit)})
        except Exception as exc:
            return jsonify({"configured": True, "batches": [], "error": str(exc)}), 400

    @app.get("/api/liquidation/borrow-pool/latest-batch")
    def liquidation_borrow_pool_latest_batch_api():
        database_url = panel_call("database_url_or_none")
        if not database_url:
            return jsonify({"configured": False, "batch": None, "error": "DATABASE_URL is required"})
        try:
            panel_call("ensure_database_schema", database_url)
            batches = panel_call("db_load_liquidation_borrow_health_scan_batches", database_url, limit=1)
            return jsonify({"configured": True, "batch": batches[0] if batches else None})
        except Exception as exc:
            return jsonify({"configured": True, "batch": None, "error": str(exc)}), 400

    @app.get("/api/liquidation/high-frequency-pool")
    def liquidation_high_frequency_pool_api():
        limit = max(1, min(int(request.args.get("limit", "100")), 500))
        database_url = panel_call("database_url_or_none")
        if not database_url:
            return jsonify({"configured": False, "rows": [], "error": "DATABASE_URL is required"})
        try:
            panel_call("ensure_database_schema", database_url)
            rows = panel_call("db_load_liquidation_high_frequency_pool", database_url, limit=limit)
            return jsonify({"configured": True, "count": len(rows), "rows": rows})
        except Exception as exc:
            return jsonify({"configured": True, "rows": [], "error": str(exc)}), 400

    @app.get("/api/liquidation/core-opportunities")
    def liquidation_core_opportunities_api():
        limit = max(1, min(int(request.args.get("limit", "100")), 500))
        database_url = panel_call("database_url_or_none")
        if not database_url:
            return jsonify({"configured": False, "rows": [], "error": "DATABASE_URL is required"})
        try:
            panel_call("ensure_database_schema", database_url)
            rows = panel_call("db_load_liquidation_core_opportunity_pool", database_url, limit=limit)
            return jsonify({"configured": True, "count": len(rows), "rows": rows})
        except Exception as exc:
            return jsonify({"configured": True, "rows": [], "error": str(exc)}), 400

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
                "retention_days": liquidation_retention_days(),
                "scan_interval_seconds": liquidation_scan_interval_seconds(),
                "discovery_interval_seconds": liquidation_discovery_interval_seconds(),
                "raw": config,
            }
        )

    @app.get("/api/liquidation/config-health")
    def liquidation_config_health_api():
        raw_chain_id = request.args.get("chain_id", "").strip()
        chain_id = int(raw_chain_id) if raw_chain_id else None
        return jsonify(panel_call("liquidation_config_health", chain_id=chain_id))

    @app.get("/api/liquidation/execution-attempts")
    def liquidation_execution_attempts_api():
        limit = max(1, min(int(request.args.get("limit", "20")), 100))
        return jsonify(panel_call("recent_liquidation_execution_attempts", limit=limit))

    @app.get("/api/liquidation/failure-samples")
    def liquidation_failure_samples_api():
        limit = max(1, min(int(request.args.get("limit", "20")), 100))
        return jsonify(panel_call("recent_liquidation_failure_samples", limit=limit))

    @app.get("/api/liquidation/account/<account>/attempts")
    def liquidation_account_attempts_api(account: str):
        limit = max(1, min(int(request.args.get("limit", "20")), 100))
        return jsonify(panel_call("liquidation_execution_attempts_for_account", account, limit=limit))

    @app.get("/api/liquidation/account/<account>/samples")
    def liquidation_account_samples_api(account: str):
        limit = max(1, min(int(request.args.get("limit", "20")), 100))
        return jsonify(panel_call("liquidation_failure_samples_for_account", account, limit=limit))

    @app.get("/api/liquidation/pause-guard")
    def liquidation_pause_guard_api():
        return jsonify(panel_call("liquidation_pause_guard_status"))

    @app.post("/api/liquidation/pause-guard/clear")
    def liquidation_pause_guard_clear_api():
        return jsonify(panel_call("clear_liquidation_pause_guard_status"))

    @app.get("/api/liquidation/discovery-coverage")
    def liquidation_discovery_coverage_api():
        pool_address = request.args.get("pool", os.getenv("AAVE_POOL_ADDRESS", "")).strip()
        return jsonify(liquidation_coverage_payload(pool_address))

    @app.get("/api/liquidation/account")
    def liquidation_account_api():
        account = request.args.get("account", "").strip()
        if not account:
            return jsonify({"error": "account is required"}), 400
        try:
            return jsonify(panel_call("liquidation_account_payload", account))
        except Exception as exc:
            return jsonify({"error": str(exc), "account": account}), 400

    @app.get("/api/liquidation/account/payload")
    def liquidation_account_payload_api():
        account = request.args.get("account", "").strip()
        if not account:
            return jsonify({"error": "account is required"}), 400
        try:
            deadline_seconds = int(request.args.get("deadline_seconds", "300"))
            allow_zero_min_out = request.args.get("allow_zero_min_out", "").strip().lower() in {"1", "true", "yes"}
            return jsonify(
                panel_call(
                    "liquidation_execution_payload_for_account",
                    account,
                    deadline_seconds=deadline_seconds,
                    allow_zero_min_out=allow_zero_min_out,
                )
            )
        except Exception as exc:
            return jsonify({"error": str(exc), "account": account}), 400

    @app.post("/api/liquidation/account/preflight")
    def liquidation_account_preflight_api():
        account = request.args.get("account", "").strip()
        if not account:
            return jsonify({"error": "account is required"}), 400
        try:
            payload = panel_call("liquidation_execution_payload_for_account", account)
            return jsonify(panel_call("simulate_liquidation_static_call", payload))
        except Exception as exc:
            return jsonify({"error": str(exc), "account": account}), 400

    @app.post("/api/liquidation/account/<account>/static-call-and-save")
    def liquidation_account_static_call_and_save_api(account: str):
        account = str(account or "").strip()
        if not account:
            return jsonify({"error": "account is required"}), 400
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
        try:
            payload = panel_call("liquidation_execution_payload_for_account", account)
            return jsonify(panel_call("simulate_liquidation_static_call", payload))
        except Exception as exc:
            return jsonify({"error": str(exc), "account": account}), 400

    @app.post("/api/liquidation/account/execute")
    def liquidation_account_execute_api():
        account = request.args.get("account", "").strip()
        force = request.args.get("force", "").strip().lower() in {"1", "true", "yes"}
        if not account:
            return jsonify({"error": "account is required"}), 400
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
        accounts, source = panel_call("load_liquidation_account_registry", force=force)
        return jsonify(
            {
                "accounts": [{"account": account} for account in accounts],
                "count": len(accounts),
                "source": source,
                "registry_window": panel_call("liquidation_account_registry_window"),
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
            panel_call("ensure_database_schema", database_url)
            panel_call(
                "db_upsert_liquidation_accounts",
                database_url,
                accounts,
                source=str(payload.get("source") or "manual"),
                active=True,
            )
            panel_call("db_prune_liquidation_accounts", database_url, retained_days=panel_call("liquidation_retention_days"))
        except Exception as exc:
            return jsonify({"error": str(exc)}), 400
        LIQUIDATION_ACCOUNT_CACHE["updated_at"] = 0.0
        LIQUIDATION_SCAN_CACHE["updated_at"] = 0.0
        return jsonify({"saved": True, "count": len(accounts), "source": "database", "accounts": accounts})
