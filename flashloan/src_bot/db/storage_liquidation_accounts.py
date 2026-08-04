import json
from datetime import datetime, timedelta, timezone
from typing import Any

from core.market_config import liquidation_market_config, liquidation_market_config_for
from db.storage_common import db_connection, require_psycopg
from db.storage_liquidation_reports import (
    account_report_with_summary,
    account_report_recovery_needs,
    json_or_default as _json_or_default,
    load_historical_account_report_sources,
    merge_account_report_sources,
)
from execution.liquidation_priority import liquidation_account_activity_tier


def _preserve_existing_positions(existing_report: Any, incoming_report: dict[str, Any]) -> dict[str, Any]:
    """Keep a full account snapshot when a health-only scan is being written."""
    incoming = dict(incoming_report or {})
    existing = existing_report if isinstance(existing_report, dict) else {}
    existing_summary = existing.get("summary") if isinstance(existing.get("summary"), dict) else {}
    incoming_summary = incoming.get("summary") if isinstance(incoming.get("summary"), dict) else {}
    if existing_summary.get("health_factor") is not None and incoming_summary.get("health_factor") is None:
        merged_summary = dict(incoming_summary)
        for key in (
            "health_factor",
            "status",
            "health_factor_band",
            "candidate_count",
            "total_collateral_base",
            "total_debt_base",
        ):
            if existing_summary.get(key) is not None:
                merged_summary[key] = existing_summary[key]
        incoming["summary"] = merged_summary
    incoming_positions_complete = bool(incoming.get("positions_complete"))
    existing_positions = existing.get("positions")
    if incoming_positions_complete or not isinstance(existing_positions, list) or not existing_positions:
        return incoming
    if isinstance(incoming.get("positions"), list) and incoming.get("positions"):
        return incoming
    preserved = dict(incoming)
    preserved["positions"] = existing_positions
    preserved["positions_complete"] = bool(existing.get("positions_complete", True))
    return preserved


def _scope_values(
    *,
    market_id: str | None = None,
    chain_id: int | None = None,
) -> dict[str, Any]:
    market = liquidation_market_config_for(market_id, chain_id=chain_id) if (market_id is not None or chain_id is not None) else liquidation_market_config()
    return {
        "market_id": market.market_id,
        "chain_id": market.chain_id,
        "network": market.network,
        "protocol": market.protocol,
    }


def _write_liquidation_account_scan(cursor, report: dict[str, Any]) -> bool:
    account = str(report.get("account") or "").strip()
    if not account:
        return False
    summary = report.get("summary") or {}
    scope = _scope_values(
        market_id=report.get("market_id") or report.get("context", {}).get("market_id"),
        chain_id=report.get("chain_id") or report.get("context", {}).get("chain_id"),
    )
    account_tier = liquidation_account_activity_tier(
        {
            "last_status": summary.get("status"),
            "last_health_factor": summary.get("health_factor"),
            "last_total_debt_base": summary.get("total_debt_base") or report.get("total_debt_base"),
        }
    )
    cursor.execute(
        """
        SELECT last_report_json
        FROM liquidation_accounts
        WHERE market_id = %s AND chain_id = %s AND lower(account) = lower(%s)
        """,
        (scope["market_id"], scope["chain_id"], account),
    )
    existing_row = cursor.fetchone()
    existing_report = _json_or_default(existing_row[0], {}) if existing_row else {}
    needs_summary_recovery, needs_positions_recovery = account_report_recovery_needs(
        existing_report,
        report,
    )
    if needs_summary_recovery or needs_positions_recovery:
        latest_valid_scan, latest_positions_scan = load_historical_account_report_sources(
            cursor,
            market_id=scope["market_id"],
            chain_id=scope["chain_id"],
            account=account,
            include_summary=needs_summary_recovery,
            include_positions=needs_positions_recovery,
        )
        recovery_sources = [existing_report]
        for scan_row in (latest_valid_scan, latest_positions_scan):
            if scan_row:
                recovery_sources.append(account_report_with_summary(scan_row[2], scan_row[1]))
        existing_report = merge_account_report_sources(recovery_sources)
    stored_report = _preserve_existing_positions(existing_report, report)
    stored_summary = stored_report.get("summary") if isinstance(stored_report.get("summary"), dict) else summary
    cursor.execute(
        """
        INSERT INTO liquidation_account_health_scans (
            market_id, chain_id, network, protocol,
            account, scanned_at, health_factor, status,
            health_factor_band, candidate_count, summary_json, report_json
        )
        VALUES (%s, %s, %s, %s, %s, NOW(), %s, %s, %s, %s, %s, %s)
        """,
        (
            scope["market_id"],
            scope["chain_id"],
            scope["network"],
            scope["protocol"],
            account,
            summary.get("health_factor"),
            summary.get("status"),
            summary.get("health_factor_band"),
            int(summary.get("candidate_count") or 0),
            json.dumps(summary, ensure_ascii=True, separators=(",", ":")),
            json.dumps(stored_report, ensure_ascii=True, separators=(",", ":")),
        ),
    )
    cursor.execute(
        """
        INSERT INTO liquidation_accounts (
            market_id, chain_id, network, protocol,
            account, source, active, added_at, updated_at,
            scan_start_at, scan_end_at,
            last_scanned_at, last_health_factor, last_status,
            last_health_factor_band, last_candidate_count,
            last_total_collateral_base, last_total_debt_base, activity_tier,
            last_summary_json, last_report_json
        )
        VALUES (
            %s, %s, %s, %s,
            %s, 'scan', TRUE, NOW(), NOW(),
            COALESCE(%s, NOW()), NOW(),
            NOW(), %s, %s,
            %s, %s,
            %s, %s, %s,
            %s, %s
        )
        ON CONFLICT (market_id, chain_id, account) DO UPDATE SET
            network = EXCLUDED.network,
            protocol = EXCLUDED.protocol,
            source = 'scan',
            active = liquidation_accounts.active,
            updated_at = NOW(),
            scan_start_at = COALESCE(liquidation_accounts.scan_start_at, EXCLUDED.scan_start_at),
            scan_end_at = EXCLUDED.scan_end_at,
            last_scanned_at = NOW(),
            last_health_factor = EXCLUDED.last_health_factor,
            last_status = EXCLUDED.last_status,
            last_health_factor_band = EXCLUDED.last_health_factor_band,
            last_candidate_count = EXCLUDED.last_candidate_count,
            last_total_collateral_base = EXCLUDED.last_total_collateral_base,
            last_total_debt_base = EXCLUDED.last_total_debt_base,
            activity_tier = EXCLUDED.activity_tier,
            last_summary_json = EXCLUDED.last_summary_json,
            last_report_json = EXCLUDED.last_report_json
        """,
        (
            scope["market_id"],
            scope["chain_id"],
            scope["network"],
            scope["protocol"],
            account,
            None,
            stored_summary.get("health_factor"),
            stored_summary.get("status"),
            stored_summary.get("health_factor_band"),
            stored_summary.get("candidate_count"),
            stored_summary.get("total_collateral_base") or report.get("total_collateral_base") or report.get("total_collateral_in_base_currency"),
            stored_summary.get("total_debt_base") or report.get("total_debt_base") or report.get("total_debt_in_base_currency"),
            account_tier,
            json.dumps(stored_summary, ensure_ascii=True, separators=(",", ":")),
            json.dumps(stored_report, ensure_ascii=True, separators=(",", ":")),
        ),
    )
    return True


def write_liquidation_account_scan_reports(cursor, reports: list[dict[str, Any]]) -> int:
    recorded = 0
    for report in reports:
        if _write_liquidation_account_scan(cursor, report):
            recorded += 1
    return recorded


def record_liquidation_account_scans(
    database_url: str,
    reports: list[dict[str, Any]],
) -> int:
    if not reports:
        return 0
    require_psycopg()
    with db_connection(database_url, connect_timeout=8) as connection:
        with connection.cursor() as cursor:
            return write_liquidation_account_scan_reports(cursor, reports)


def record_liquidation_account_scan(database_url: str, report: dict[str, Any]) -> None:
    record_liquidation_account_scans(database_url, [report])


def load_latest_liquidation_account_reports(
    database_url: str,
    limit: int = 500,
    *,
    market_id: str | None = None,
    chain_id: int | None = None,
) -> list[dict[str, Any]]:
    psycopg = require_psycopg()
    scope = _scope_values(market_id=market_id, chain_id=chain_id)
    query = """
        SELECT
            market_id, chain_id, network, protocol,
            account, source, active, scan_start_at, scan_end_at, last_scanned_at,
            last_health_factor, last_status, last_health_factor_band, last_candidate_count,
            last_total_collateral_base, last_total_debt_base, activity_tier,
            last_summary_json, last_report_json
        FROM liquidation_accounts
        WHERE market_id = %s AND chain_id = %s
          AND (last_report_json IS NOT NULL OR last_summary_json IS NOT NULL)
        ORDER BY COALESCE(last_scanned_at, scan_end_at, updated_at) DESC, account ASC
        LIMIT %s
    """
    with db_connection(database_url, connect_timeout=8) as connection:
        with connection.cursor() as cursor:
            cursor.execute(query, (scope["market_id"], scope["chain_id"], int(limit)))
            rows = cursor.fetchall()
    reports: list[dict[str, Any]] = []
    for row in rows:
        summary = {}
        report = {}
        try:
            if row[17]:
                summary = json.loads(row[17]) if isinstance(row[17], str) else {}
        except json.JSONDecodeError:
            summary = {}
        try:
            if row[18]:
                report = json.loads(row[18]) if isinstance(row[18], str) else {}
        except json.JSONDecodeError:
            report = {}
        reports.append(
            {
                "market_id": str(row[0]),
                "chain_id": int(row[1]) if row[1] is not None else None,
                "network": str(row[2]) if row[2] else None,
                "protocol": str(row[3]) if row[3] else None,
                "account": str(row[4]),
                "source": str(row[5]),
                "active": bool(row[6]),
                "scan_start_at": row[7].isoformat() if row[7] else None,
                "scan_end_at": row[8].isoformat() if row[8] else None,
                "last_scanned_at": row[9].isoformat() if row[9] else None,
                "last_health_factor": float(row[10]) if row[10] is not None else None,
                "last_status": str(row[11]) if row[11] else None,
                "last_health_factor_band": str(row[12]) if row[12] else None,
                "last_candidate_count": int(row[13]) if row[13] is not None else None,
                "last_total_collateral_base": float(row[14]) if row[14] is not None else None,
                "last_total_debt_base": float(row[15]) if row[15] is not None else None,
                "activity_tier": str(row[16]) if row[16] else None,
                "summary": summary,
                "report": report,
            }
        )
    return reports


def prune_liquidation_accounts(database_url: str, retained_days: int = 365) -> int:
    return 0
