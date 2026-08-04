import json
from typing import Any


def json_or_default(value: Any, default: Any) -> Any:
    if not value:
        return default
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default


def account_summary_is_valid(summary: Any) -> bool:
    if not isinstance(summary, dict):
        return False
    return (
        summary.get("health_factor") is not None
        and str(summary.get("status") or "").strip().lower() != "error"
        and summary.get("total_collateral_base") is not None
        and summary.get("total_debt_base") is not None
    )


def account_report_with_summary(report_value: Any, summary_value: Any) -> dict[str, Any]:
    report = json_or_default(report_value, {})
    if not isinstance(report, dict):
        return {}
    summary = json_or_default(summary_value, {})
    if not account_summary_is_valid(report.get("summary")) and account_summary_is_valid(summary):
        report = dict(report)
        report["summary"] = summary
    return report


def merge_account_report_sources(sources: list[Any]) -> dict[str, Any]:
    report: dict[str, Any] = {}
    for source_report in sources:
        if not isinstance(source_report, dict):
            continue
        if not report:
            report = dict(source_report)
            continue
        for key in ("positions", "liquidation_candidates", "realtime_params"):
            if not report.get(key) and source_report.get(key):
                report[key] = source_report[key]
        if (
            not account_summary_is_valid(report.get("summary"))
            and account_summary_is_valid(source_report.get("summary"))
        ):
            report["summary"] = source_report["summary"]
    return report


def account_report_recovery_needs(
    existing_report: dict[str, Any],
    incoming_report: dict[str, Any],
) -> tuple[bool, bool]:
    existing_summary = existing_report.get("summary") if isinstance(existing_report.get("summary"), dict) else {}
    incoming_summary = incoming_report.get("summary") if isinstance(incoming_report.get("summary"), dict) else {}
    needs_summary = not account_summary_is_valid(existing_summary) and not account_summary_is_valid(incoming_summary)
    existing_positions = existing_report.get("positions")
    incoming_positions = incoming_report.get("positions")
    needs_positions = (
        needs_summary
        and not bool(incoming_report.get("positions_complete"))
        and not (isinstance(existing_positions, list) and existing_positions)
        and not (isinstance(incoming_positions, list) and incoming_positions)
    )
    return needs_summary, needs_positions


def load_historical_account_report_sources(
    cursor,
    *,
    market_id: str,
    chain_id: int,
    account: str,
    include_summary: bool = True,
    include_positions: bool = True,
) -> tuple[tuple[Any, ...] | None, tuple[Any, ...] | None]:
    latest_valid_scan = None
    latest_positions_scan = None
    params = (market_id, chain_id, account)
    if include_summary:
        cursor.execute(
            """
            SELECT scanned_at, summary_json, report_json
            FROM liquidation_account_health_scans
            WHERE market_id = %s
              AND chain_id = %s
              AND lower(account) = lower(%s)
              AND health_factor IS NOT NULL
              AND LOWER(COALESCE(status, '')) <> 'error'
              AND report_json IS NOT NULL
            ORDER BY scanned_at DESC
            LIMIT 1
            """,
            params,
        )
        latest_valid_scan = cursor.fetchone()
    if include_positions:
        cursor.execute(
            """
            SELECT scanned_at, summary_json, report_json
            FROM liquidation_account_health_scans
            WHERE market_id = %s
              AND chain_id = %s
              AND lower(account) = lower(%s)
              AND health_factor IS NOT NULL
              AND LOWER(COALESCE(status, '')) <> 'error'
              AND report_json IS NOT NULL
              AND jsonb_typeof(report_json::jsonb -> 'positions') = 'array'
              AND jsonb_array_length(report_json::jsonb -> 'positions') > 0
            ORDER BY scanned_at DESC
            LIMIT 1
            """,
            params,
        )
        latest_positions_scan = cursor.fetchone()
    return latest_valid_scan, latest_positions_scan
