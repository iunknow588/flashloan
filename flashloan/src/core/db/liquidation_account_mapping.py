from __future__ import annotations

import json
from typing import Any

from core.liquidation_pool_mapping import core_profit_assessment


ACCOUNT_REPORT_COLUMNS = (
    "market_id",
    "chain_id",
    "network",
    "protocol",
    "account",
    "source",
    "active",
    "scan_start_at",
    "scan_end_at",
    "last_scanned_at",
    "last_health_factor",
    "last_status",
    "last_health_factor_band",
    "last_candidate_count",
    "last_total_collateral_base",
    "last_total_debt_base",
    "activity_tier",
    "last_summary_json",
    "last_report_json",
)


def _json_or_default(value: Any, default: Any) -> Any:
    if not value:
        return default
    if not isinstance(value, str):
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default


def _iso_or_none(value: Any) -> str | None:
    return value.isoformat() if value else None


def _float_or_none(value: Any) -> float | None:
    return float(value) if value is not None else None


def _int_or_none(value: Any) -> int | None:
    return int(value) if value is not None else None


def _str_or_none(value: Any) -> str | None:
    return str(value) if value else None


def account_report_from_row(row: dict[str, Any]) -> dict[str, Any]:
    """Convert one liquidation_accounts row to the public report payload.

    The input is expected to contain the keys in ``ACCOUNT_REPORT_COLUMNS``.
    JSON columns are parsed defensively and dates are rendered as ISO text.
    """
    summary = _json_or_default(row.get("last_summary_json"), {})
    report = _json_or_default(row.get("last_report_json"), {})
    if not isinstance(summary, dict):
        summary = {}
    if not isinstance(report, dict):
        report = {}
    return {
        "market_id": str(row.get("market_id")) if row.get("market_id") is not None else None,
        "chain_id": _int_or_none(row.get("chain_id")),
        "network": _str_or_none(row.get("network")),
        "protocol": _str_or_none(row.get("protocol")),
        "account": str(row.get("account") or ""),
        "source": _str_or_none(row.get("source")),
        "active": bool(row.get("active")),
        "scan_start_at": _iso_or_none(row.get("scan_start_at")),
        "scan_end_at": _iso_or_none(row.get("scan_end_at")),
        "last_scanned_at": _iso_or_none(row.get("last_scanned_at")),
        "last_health_factor": _float_or_none(row.get("last_health_factor")),
        "last_status": _str_or_none(row.get("last_status")),
        "last_health_factor_band": _str_or_none(row.get("last_health_factor_band")),
        "last_candidate_count": _int_or_none(row.get("last_candidate_count")),
        "last_total_collateral_base": _float_or_none(row.get("last_total_collateral_base")),
        "last_total_debt_base": _float_or_none(row.get("last_total_debt_base")),
        "activity_tier": _str_or_none(row.get("activity_tier")),
        "summary": summary,
        "report": report,
    }


def core_opportunity_payload_from_row(
    core: tuple[Any, ...],
    summary: dict[str, Any],
    *,
    min_profit_usd: float = 1.0,
) -> dict[str, Any]:
    """Convert one liquidation_core_opportunity_pool row to a public payload.

    The row layout matches the loader in ``storage_liquidation_account_snapshot``:
      rank, account, health_factor, priority_score, total_collateral_base,
      total_debt_base, best_debt_asset, best_collateral_asset,
      debt_to_cover_units, estimated_operator_net_profit_usd,
      estimated_gas_cost_usd, quote_viable, static_call_status,
      payload_state, blocked_reasons_json, last_scanned_at, metadata_json
    """
    blocked_reasons = _json_or_default(core[14] if len(core) > 14 else None, [])
    metadata = _json_or_default(core[16] if len(core) > 16 else None, {})
    if not isinstance(metadata, dict):
        metadata = {}
    recommended_candidate = metadata.get("recommended_candidate") if isinstance(metadata.get("recommended_candidate"), dict) else {}
    assessment_row = {
        "health_factor": core[2] if len(core) > 2 else None,
        "status": summary.get("status"),
        "total_debt_base": core[5] if len(core) > 5 else None,
        "recommended_candidate": recommended_candidate or {},
        "liquidation_profit": {"operator_net_profit_usd": core[9] if len(core) > 9 else None, "net_profit_base": core[9] if len(core) > 9 else None},
    }
    nested_min = None
    if isinstance(metadata.get("profit_assessment"), dict):
        nested_min = (metadata["profit_assessment"].get("min_operator_net_profit_usd"))
    try:
        effective_min = float(nested_min if nested_min is not None else min_profit_usd)
    except (TypeError, ValueError):
        effective_min = float(min_profit_usd)
    effective_assessment = core_profit_assessment(assessment_row, effective_min)
    effective_blocked_reasons = list(effective_assessment.get("blocked_reasons") or blocked_reasons or [])
    return {
        "rank": int(core[0]) if len(core) > 0 and core[0] is not None else None,
        "health_factor": core[2] if len(core) > 2 else None,
        "priority_score": core[3] if len(core) > 3 else None,
        "total_collateral_base": core[4] if len(core) > 4 else None,
        "total_debt_base": core[5] if len(core) > 5 else None,
        "best_debt_asset": core[6] if len(core) > 6 else None,
        "best_collateral_asset": core[7] if len(core) > 7 else None,
        "debt_to_cover_units": core[8] if len(core) > 8 else None,
        "estimated_operator_net_profit_usd": core[9] if len(core) > 9 else None,
        "estimated_gas_cost_usd": core[10] if len(core) > 10 else None,
        "quote_viable": bool(core[11]) if len(core) > 11 else None,
        "static_call_status": core[12] if len(core) > 12 else None,
        "payload_state": effective_assessment["label"],
        "blocked_reasons": effective_blocked_reasons,
        "last_scanned_at": core[15].isoformat() if len(core) > 15 and core[15] else None,
        "profit_assessment": effective_assessment,
    }


__all__ = [
    "ACCOUNT_REPORT_COLUMNS",
    "account_report_from_row",
    "core_opportunity_payload_from_row",
]
