import json
from typing import Any

from core.contracts import validate_scoped_payload

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


def preserve_existing_positions(existing_report: Any, incoming_report: dict[str, Any]) -> dict[str, Any]:
    """Keep a full account snapshot when a health-only scan is written."""
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
    cursor=None,
    *,
    market_id: str,
    chain_id: int,
    account: str,
    include_summary: bool = True,
    include_positions: bool = True,
    expected_scope: dict[str, Any] | None = None,
) -> tuple[tuple[Any, ...] | None, tuple[Any, ...] | None]:
    """Return None for both sources; historical health scans are now in KV."""

    return None, None


def _validate_historical_row_scope(row: tuple[Any, ...], expected_scope: dict[str, Any]) -> None:
    """Validate embedded scope when present, preserving legacy unscoped rows."""
    for value in row[1:]:
        payload = json_or_default(value, {})
        if isinstance(payload, dict) and ("scope" in payload or "market_id" in payload):
            validate_scoped_payload(payload, expected_scope)
