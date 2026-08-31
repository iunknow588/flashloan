from __future__ import annotations

import json
import os
from pathlib import Path

from flask import jsonify

from tools.liquidation_observation_report import (
    build_liquidation_observation_report,
    default_output_path as liquidation_observation_report_path,
    write_liquidation_observation_report,
)
from web import control_panel_data_routes as shared
from web.control_panel_liquidation_base import database_url_or_none
from web.control_panel_liquidation_routes import liquidation_coverage_payload
from web.control_panel_market import database_table_counts, observation_count, safe_latest
from web.control_panel_stats import testnet_trade_stats as read_testnet_trade_stats, trade_stats as read_trade_stats

ROUTE_CONTEXT = shared.ROUTE_CONTEXT
panel_call = shared.panel_call
data_error_message = shared.data_error_message
REPO_ROOT = Path(__file__).resolve().parents[4]


def register_liquidation_data_routes(app, panel) -> None:
    ROUTE_CONTEXT.bind(panel, globals())

    @app.get("/api/db-summary")
    def db_summary():
        schema = panel_call("schema_status_payload")
        pool_address = os.getenv("AAVE_POOL_ADDRESS", "").strip()
        coverage = liquidation_coverage_payload(pool_address, panel=ROUTE_CONTEXT.panel)
        attempts = panel_call("recent_liquidation_execution_attempts", limit=1)
        failure_samples = panel_call("recent_liquidation_failure_samples", limit=20)
        pause_guard = panel_call("liquidation_pause_guard_status")
        return jsonify(
            {
                "rows": observation_count(),
                "db_counts": database_table_counts(),
                "trade_stats": safe_latest(lambda: read_trade_stats(database_url_or_none())),
                "testnet_trade_stats": safe_latest(lambda: read_testnet_trade_stats(REPO_ROOT)),
                "liquidation": {
                    "schema": {
                        "configured": schema.get("configured"),
                        "up_to_date": schema.get("up_to_date"),
                        "missing_migrations": schema.get("missing_migrations", []),
                    },
                    "discovery_coverage": coverage,
                    "execution_attempts": attempts.get("stats", {}),
                    "failure_samples": {
                        "configured": failure_samples.get("configured"),
                        "recent_count": len(failure_samples.get("samples") or []),
                    },
                    "pause_guard": pause_guard,
                },
            }
        )

    @app.get("/api/liquidation/control-summary")
    def liquidation_control_summary():
        database_url = panel_call("database_url_or_none")
        pool_address = os.getenv("AAVE_POOL_ADDRESS", "").strip()
        schema = panel_call("schema_status_payload")
        discovery_progress = panel_call("liquidation_discovery_progress", pool_address) if database_url and pool_address else {}
        registry_window = panel_call("liquidation_account_registry_window") if database_url else {}
        pause_guard = panel_call("liquidation_pause_guard_status")
        background_activity = panel_call("background_activity_payload")
        attempts = panel_call("recent_liquidation_execution_attempts", limit=5)
        failure_samples = panel_call("recent_liquidation_failure_samples", limit=5)
        latest_batch = None
        core_opportunities = []
        high_frequency_rows = []
        account_tiers = {}
        if database_url:
            try:
                panel_call("ensure_database_schema", database_url)
                latest_batches = panel_call("db_load_liquidation_borrow_health_scan_batches", database_url, limit=1)
                latest_batch = latest_batches[0] if latest_batches else None
                core_opportunities = panel_call("db_load_liquidation_core_opportunity_pool", database_url, limit=5)
                high_frequency_rows = panel_call("db_load_liquidation_high_frequency_pool", database_url, limit=5)
                account_tiers = panel_call("liquidation_account_tier_summary")
            except Exception as exc:
                return jsonify({"configured": True, "error": data_error_message(exc), "schema": schema}), 400
        return jsonify(
            {
                "configured": bool(database_url),
                "schema": {
                    "configured": schema.get("configured"),
                    "up_to_date": schema.get("up_to_date"),
                    "missing_migrations": schema.get("missing_migrations", []),
                    "error": schema.get("error"),
                },
                "pause_guard": pause_guard,
                "background_activity": background_activity,
                "discovery_running": bool((background_activity.get("liquidation_discovery") or {}).get("running")),
                "discovery_stage": (background_activity.get("liquidation_discovery") or {}).get("stage"),
                "scan_running": bool((background_activity.get("liquidation_health_scan") or {}).get("running")),
                "scan_stage": (background_activity.get("liquidation_health_scan") or {}).get("stage"),
                "integrity": {
                    "schema_up_to_date": bool(schema.get("up_to_date")),
                    "discovery_has_gap": bool(discovery_progress.get("has_gap")),
                    "discovery_covered_from_block": discovery_progress.get("earliest_backfill_from_block"),
                    "discovery_covered_to_block": discovery_progress.get("latest_recent_to_block"),
                    "discovery_stage": str(
                        discovery_progress.get("stage")
                        or ("borrowers" if discovery_progress.get("latest_recent_to_block") is not None else "idle")
                    ),
                    "registry_total_count": int(registry_window.get("total_count") or 0),
                    "registry_active_count": int(registry_window.get("active_count") or 0),
                    "latest_batch_status": (latest_batch or {}).get("status") if latest_batch else None,
                    "latest_batch_account_count": int((latest_batch or {}).get("account_count") or 0),
                    "latest_batch_scanned_count": int((latest_batch or {}).get("scanned_count") or 0),
                    "latest_batch_matches_account_count": bool(
                        latest_batch
                        and str((latest_batch or {}).get("status") or "").lower() == "success"
                        and int((latest_batch or {}).get("account_count") or 0) == int((latest_batch or {}).get("scanned_count") or 0)
                    ),
                    "complete": bool(
                        schema.get("up_to_date")
                        and not discovery_progress.get("has_gap")
                        and latest_batch
                        and str((latest_batch or {}).get("status") or "").lower() == "success"
                        and int((latest_batch or {}).get("account_count") or 0) == int((latest_batch or {}).get("scanned_count") or 0)
                    ),
                },
                "execution_attempts": attempts.get("stats", {}),
                "failure_samples": {"recent_count": len(failure_samples.get("samples") or [])},
                "latest_batch": latest_batch,
                "core_opportunities": core_opportunities,
                "high_frequency_rows": high_frequency_rows,
                "account_tiers": account_tiers,
                "control_status": panel_call("control_status_payload"),
            }
        )

    @app.get("/api/liquidation/reports/daily")
    def liquidation_daily_report_api():
        database_url = panel_call("database_url_or_none")
        if not database_url:
            return jsonify({"configured": False, "error": "DATABASE_URL is required", "report": None})
        try:
            path = liquidation_observation_report_path()
            if path.exists():
                report = json.loads(path.read_text(encoding="utf-8"))
                return jsonify({"configured": True, "path": str(path), "reused": True, "report": report})
            report = build_liquidation_observation_report(database_url)
            write_liquidation_observation_report(report, path)
            return jsonify({"configured": True, "path": str(path), "reused": False, "report": report})
        except Exception as exc:
            return jsonify({"configured": True, "error": data_error_message(exc), "report": None}), 400

    @app.post("/api/liquidation/reports/daily")
    def liquidation_daily_report_refresh_api():
        database_url = panel_call("database_url_or_none")
        if not database_url:
            return jsonify({"configured": False, "error": "DATABASE_URL is required", "report": None})
        try:
            path = liquidation_observation_report_path()
            report = build_liquidation_observation_report(database_url)
            write_liquidation_observation_report(report, path)
            return jsonify({"configured": True, "path": str(path), "reused": False, "report": report})
        except Exception as exc:
            return jsonify({"configured": True, "error": data_error_message(exc), "report": None}), 400
