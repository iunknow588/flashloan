from __future__ import annotations

import json
import os
from pathlib import Path

from flask import jsonify

from bot.tools.liquidation_observation_report import (
    build_liquidation_observation_report,
    default_output_path as liquidation_observation_report_path,
    write_liquidation_observation_report,
)
from web import control_panel_data_routes as shared
from web.control_panel_liquidation_base import database_url_or_none
from web.control_panel_liquidation_routes import liquidation_coverage_payload
from web.control_panel_market import database_table_counts, observation_count, safe_latest
from core.control_panel_stats import testnet_trade_stats as read_testnet_trade_stats, trade_stats as read_trade_stats
from web.data_routes.liquidation_control_payloads import control_summary_payload, db_summary_payload

ROUTE_CONTEXT = shared.ROUTE_CONTEXT
panel_call = shared.panel_call
data_error_message = shared.data_error_message
REPO_ROOT = Path(__file__).resolve().parents[4]


def register_liquidation_data_routes(app, panel) -> None:
    ROUTE_CONTEXT.bind(panel, globals())
    ROUTE_CONTEXT.bind_panel_provider(panel)

    @app.get("/api/db-summary")
    def db_summary():
        pool_address = os.getenv("AAVE_POOL_ADDRESS", "").strip()
        return jsonify(
            db_summary_payload(
                panel_call,
                pool_address=pool_address,
                repo_root=REPO_ROOT,
                database_url_or_none=database_url_or_none,
                observation_count=observation_count,
                database_table_counts=database_table_counts,
                safe_latest=safe_latest,
                read_trade_stats=read_trade_stats,
                read_testnet_trade_stats=read_testnet_trade_stats,
            )
        )

    @app.get("/api/liquidation/control-summary")
    def liquidation_control_summary():
        pool_address = os.getenv("AAVE_POOL_ADDRESS", "").strip()
        payload, status = control_summary_payload(panel_call, pool_address=pool_address)
        return jsonify(payload), status

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
