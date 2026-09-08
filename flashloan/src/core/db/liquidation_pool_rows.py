from __future__ import annotations

from typing import Any

from core.db.liquidation_attempts_mapping import json_or_default as _json_or_default
from core.db.scoped_adapter import validate_scoped_rows


COLUMN_NAMES = (
    "market_id",
    "chain_id",
    "network",
    "protocol",
    "id",
    "started_at",
    "finished_at",
    "status",
    "account_count",
    "scanned_count",
    "risk_count",
    "error_count",
    "entered_count",
    "exited_count",
    "rpc_url",
    "block_number",
    "watch_health_factor",
    "error",
    "metadata_json",
)

BORROW_HEALTH_CANDIDATE_COLUMN_NAMES = (
    "market_id",
    "chain_id",
    "network",
    "protocol",
    "account",
    "health_factor",
    "status",
    "health_factor_band",
    "total_collateral_base",
    "total_debt_base",
    "candidate_count",
    "last_scanned_at",
    "report_json",
)

CORE_OPPORTUNITY_COLUMN_NAMES = (
    "market_id",
    "chain_id",
    "network",
    "protocol",
    "account",
    "health_factor",
    "priority_score",
    "total_debt_base",
    "total_collateral_base",
    "best_debt_asset",
    "best_collateral_asset",
    "debt_to_cover_units",
    "estimated_operator_net_profit_usd",
    "estimated_gas_cost_usd",
    "quote_viable",
    "static_call_status",
    "payload_state",
    "blocked_reasons_json",
    "last_scanned_at",
    "metadata_json",
)


def borrow_health_scan_batch_from_row(row: Any, scope: dict[str, Any]) -> dict[str, Any]:
    named = validate_scoped_rows(
        [dict(zip(COLUMN_NAMES, row))],
        scope,
    )[0]
    row = tuple(named[name] for name in COLUMN_NAMES)
    if len(row) == 15:
        row = (scope["market_id"], scope["chain_id"], scope["network"], scope["protocol"], *row)
    return {
        "market_id": row[0],
        "chain_id": int(row[1]) if row[1] is not None else None,
        "network": row[2],
        "protocol": row[3],
        "id": int(row[4]),
        "started_at": row[5].isoformat() if row[5] else None,
        "finished_at": row[6].isoformat() if row[6] else None,
        "status": row[7],
        "account_count": int(row[8] or 0),
        "scanned_count": int(row[9] or 0),
        "risk_count": int(row[10] or 0),
        "error_count": int(row[11] or 0),
        "entered_count": int(row[12] or 0),
        "exited_count": int(row[13] or 0),
        "rpc_url": row[14],
        "block_number": int(row[15]) if row[15] is not None else None,
        "watch_health_factor": row[16],
        "error": row[17],
        "metadata": _json_or_default(row[18], {}),
    }


def _float_or_zero(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def borrow_health_candidate_from_row(row: Any, scope: dict[str, Any]) -> dict[str, Any]:
    if len(row) == 9:
        row = (scope["market_id"], scope["chain_id"], scope["network"], scope["protocol"], *row)
    named = validate_scoped_rows(
        [dict(zip(BORROW_HEALTH_CANDIDATE_COLUMN_NAMES, row))],
        scope,
    )[0]
    row = tuple(named[name] for name in BORROW_HEALTH_CANDIDATE_COLUMN_NAMES)
    return {
        "market_id": row[0],
        "chain_id": row[1],
        "network": row[2],
        "protocol": row[3],
        "account": row[4],
        "health_factor": row[5],
        "status": row[6],
        "health_factor_band": row[7],
        "total_collateral_base": row[8],
        "total_debt_base": row[9],
        "candidate_count": row[10],
        "last_scanned_at": row[11].isoformat() if row[11] else None,
        "report": _json_or_default(row[12], {}),
    }


def core_opportunity_pool_row_from_row(row: Any, scope: dict[str, Any]) -> dict[str, Any]:
    if len(row) == 16:
        row = (scope["market_id"], scope["chain_id"], scope["network"], scope["protocol"], *row)
    named = validate_scoped_rows(
        [dict(zip(CORE_OPPORTUNITY_COLUMN_NAMES, row))],
        scope,
    )[0]
    row = tuple(named[name] for name in CORE_OPPORTUNITY_COLUMN_NAMES)
    metadata = _json_or_default(row[19], {})
    profit_assessment = metadata.get("profit_assessment") if isinstance(metadata, dict) else None
    if not isinstance(profit_assessment, dict):
        profit = _float_or_zero(row[12])
        above_threshold = profit > 1.0
        profit_assessment = {
            "estimated_operator_net_profit_usd": profit,
            "min_operator_net_profit_usd": 1.0,
            "above_auto_profit_threshold": above_threshold,
            "manual_review_required": True,
            "auto_execution_blocked": not above_threshold
            or "profit_below_minimum" in _json_or_default(row[17], []),
            "label": "over_1u_candidate" if above_threshold else "low_profit_manual_test",
        }
    return {
        "market_id": row[0],
        "chain_id": row[1],
        "network": row[2],
        "protocol": row[3],
        "account": row[4],
        "health_factor": row[5],
        "priority_score": row[6],
        "total_debt_base": row[7],
        "total_collateral_base": row[8],
        "best_debt_asset": row[9],
        "best_collateral_asset": row[10],
        "debt_to_cover_units": row[11],
        "estimated_operator_net_profit_usd": row[12],
        "estimated_gas_cost_usd": row[13],
        "quote_viable": row[14],
        "static_call_status": row[15],
        "payload_state": row[16],
        "blocked_reasons": _json_or_default(row[17], []),
        "last_scanned_at": row[18].isoformat() if row[18] else None,
        "metadata": metadata,
        "profit_assessment": profit_assessment,
        "profit_assessment_label": profit_assessment.get("label"),
        "above_auto_profit_threshold": bool(profit_assessment.get("above_auto_profit_threshold")),
        "auto_execution_blocked": bool(profit_assessment.get("auto_execution_blocked")),
        "manual_review_required": bool(profit_assessment.get("manual_review_required", True)),
    }


__all__ = [
    "BORROW_HEALTH_CANDIDATE_COLUMN_NAMES",
    "CORE_OPPORTUNITY_COLUMN_NAMES",
    "borrow_health_candidate_from_row",
    "borrow_health_scan_batch_from_row",
    "core_opportunity_pool_row_from_row",
]
