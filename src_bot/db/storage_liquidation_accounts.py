import json
from datetime import datetime, timedelta, timezone
from typing import Any

from db.storage_common import db_connection, require_psycopg
from execution.liquidation_priority import liquidation_account_activity_tier


def _json_or_default(value: Any, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value) if isinstance(value, str) else default
    except json.JSONDecodeError:
        return default


def record_liquidation_account_scan(database_url: str, report: dict[str, Any]) -> None:
    account = str(report.get("account") or "").strip()
    if not account:
        return
    summary = report.get("summary") or {}
    account_tier = liquidation_account_activity_tier(
        {
            "last_status": summary.get("status"),
            "last_health_factor": summary.get("health_factor"),
            "last_total_debt_base": summary.get("total_debt_base") or report.get("total_debt_base"),
        }
    )
    psycopg = require_psycopg()
    with db_connection(database_url, connect_timeout=8) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO liquidation_account_health_scans (
                    account, scanned_at, health_factor, status,
                    health_factor_band, candidate_count, summary_json, report_json
                )
                VALUES (%s, NOW(), %s, %s, %s, %s, %s, %s)
                """,
                (
                    account,
                    summary.get("health_factor"),
                    summary.get("status"),
                    summary.get("health_factor_band"),
                    int(summary.get("candidate_count") or 0),
                    json.dumps(summary, ensure_ascii=True, separators=(",", ":")),
                    json.dumps(report, ensure_ascii=True, separators=(",", ":")),
                ),
            )
            cursor.execute(
                """
                INSERT INTO liquidation_accounts (
                    account, source, active, added_at, updated_at,
                    scan_start_at, scan_end_at,
                    last_scanned_at, last_health_factor, last_status,
                    last_health_factor_band, last_candidate_count,
                    last_total_collateral_base, last_total_debt_base, activity_tier,
                    last_summary_json, last_report_json
                )
                VALUES (
                    %s, 'scan', TRUE, NOW(), NOW(),
                    COALESCE(%s, NOW()), NOW(),
                    NOW(), %s, %s,
                    %s, %s,
                    %s, %s, %s,
                    %s, %s
                )
                ON CONFLICT (account) DO UPDATE SET
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
                    account,
                    None,
                    summary.get("health_factor"),
                    summary.get("status"),
                    summary.get("health_factor_band"),
                    summary.get("candidate_count"),
                    summary.get("total_collateral_base") or report.get("total_collateral_base") or report.get("total_collateral_in_base_currency"),
                    summary.get("total_debt_base") or report.get("total_debt_base") or report.get("total_debt_in_base_currency"),
                    account_tier,
                    json.dumps(summary, ensure_ascii=True, separators=(",", ":")),
                    json.dumps(report, ensure_ascii=True, separators=(",", ":")),
                ),
            )


def load_latest_liquidation_account_reports(database_url: str, limit: int = 500) -> list[dict[str, Any]]:
    psycopg = require_psycopg()
    query = """
        SELECT
            account, source, active, scan_start_at, scan_end_at, last_scanned_at,
            last_health_factor, last_status, last_health_factor_band, last_candidate_count,
            last_total_collateral_base, last_total_debt_base, activity_tier,
            last_summary_json, last_report_json
        FROM liquidation_accounts
        WHERE last_report_json IS NOT NULL OR last_summary_json IS NOT NULL
        ORDER BY COALESCE(last_scanned_at, scan_end_at, updated_at) DESC, account ASC
        LIMIT %s
    """
    with db_connection(database_url, connect_timeout=8) as connection:
        with connection.cursor() as cursor:
            cursor.execute(query, (int(limit),))
            rows = cursor.fetchall()
    reports: list[dict[str, Any]] = []
    for row in rows:
        summary = {}
        report = {}
        try:
            if row[13]:
                summary = json.loads(row[13]) if isinstance(row[13], str) else {}
        except json.JSONDecodeError:
            summary = {}
        try:
            if row[14]:
                report = json.loads(row[14]) if isinstance(row[14], str) else {}
        except json.JSONDecodeError:
            report = {}
        reports.append(
            {
                "account": str(row[0]),
                "source": str(row[1]),
                "active": bool(row[2]),
                "scan_start_at": row[3].isoformat() if row[3] else None,
                "scan_end_at": row[4].isoformat() if row[4] else None,
                "last_scanned_at": row[5].isoformat() if row[5] else None,
                "last_health_factor": float(row[6]) if row[6] is not None else None,
                "last_status": str(row[7]) if row[7] else None,
                "last_health_factor_band": str(row[8]) if row[8] else None,
                "last_candidate_count": int(row[9]) if row[9] is not None else None,
                "last_total_collateral_base": float(row[10]) if row[10] is not None else None,
                "last_total_debt_base": float(row[11]) if row[11] is not None else None,
                "activity_tier": str(row[12]) if row[12] else None,
                "summary": summary,
                "report": report,
            }
        )
    return reports


def prune_liquidation_accounts(database_url: str, retained_days: int = 365) -> int:
    return 0
