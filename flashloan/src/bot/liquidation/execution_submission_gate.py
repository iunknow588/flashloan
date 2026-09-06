from __future__ import annotations

from typing import Any

from .target_reassessment import reassess_execution_target


def _int_or_none(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def validate_execution_submission_target(
    payload: dict[str, Any],
    *,
    chain_id: int,
    max_age_seconds: float = 30.0,
    now: Any = None,
) -> dict[str, Any]:
    report = dict(payload.get("account_report") or {})
    if not report.get("account") and payload.get("account"):
        report["account"] = payload.get("account")
    execution_target = dict(payload.get("execution_target") or {})
    context = report.get("context") or {}
    has_target_data = bool(
        execution_target
        or context.get("market_id")
        or context.get("chain_id")
        or report.get("observed_at")
        or report.get("scanned_at")
        or report.get("last_scanned_at")
        or report.get("liquidation_candidates")
    )
    if not has_target_data:
        return {
            "ready": True,
            "blocked_reasons": [],
            "reassessment": {
                "fresh": True,
                "reassessment_required": False,
                "blocked_reasons": [],
                "age_seconds": None,
                "max_age_seconds": max(0.0, float(max_age_seconds)),
                "target_hash": execution_target.get("target_hash"),
            },
            "target_market_id": None,
            "report_market_id": None,
            "chain_id": _int_or_none(chain_id),
            "report_chain_id": None,
        }
    target_market_id = str(execution_target.get("market_id") or context.get("market_id") or "").strip().lower() or None
    report_market_id = str(context.get("market_id") or "").strip().lower() or None
    report_chain_id = _int_or_none(context.get("chain_id"))
    current_chain_id = _int_or_none(chain_id)

    blocked_reasons: list[str] = []
    if report_market_id and target_market_id and report_market_id != target_market_id:
        blocked_reasons.append("market_id_mismatch")
    if report_chain_id is not None and current_chain_id is not None and report_chain_id != current_chain_id:
        blocked_reasons.append("chain_id_mismatch")

    reassessment = reassess_execution_target(report, execution_target, max_age_seconds=max_age_seconds, now=now)
    for reason in reassessment.get("blocked_reasons") or []:
        if reason not in blocked_reasons:
            blocked_reasons.append(reason)

    return {
        "ready": not blocked_reasons,
        "blocked_reasons": blocked_reasons,
        "reassessment": reassessment,
        "target_market_id": target_market_id,
        "report_market_id": report_market_id,
        "chain_id": current_chain_id,
        "report_chain_id": report_chain_id,
    }
