from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .execution_target import normalize_execution_target, select_execution_candidate


def _parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def reassess_execution_target(
    report: dict[str, Any],
    execution_target: dict[str, Any],
    *,
    max_age_seconds: float = 30.0,
    now: datetime | None = None,
) -> dict[str, Any]:
    reasons: list[str] = []
    current = now or datetime.now(timezone.utc)
    observed_at = _parse_time(
        report.get("observed_at")
        or report.get("scanned_at")
        or report.get("last_scanned_at")
        or (report.get("context") or {}).get("observed_at")
    )
    age_seconds = None
    if observed_at is not None:
        age_seconds = max(0.0, (current - observed_at).total_seconds())
        if age_seconds > max(0.0, float(max_age_seconds)):
            reasons.append("target_report_expired")
    summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
    if summary.get("status") not in (None, "", "liquidatable"):
        reasons.append("account_not_liquidatable")
    try:
        if float(summary.get("health_factor", report.get("health_factor"))) >= 1.0:
            reasons.append("account_not_liquidatable")
    except (TypeError, ValueError):
        reasons.append("health_factor_unavailable")
    try:
        target = normalize_execution_target(
            str(execution_target.get("account") or report.get("account") or ""),
            borrow_index=execution_target.get("borrow_index"),
            candidate_index=execution_target.get("candidate_index"),
            market_id=execution_target.get("market_id"),
        )
        _, current_target = select_execution_candidate(report, target=target)
        if str(current_target.get("target_hash")) != str(execution_target.get("target_hash")):
            reasons.append("target_candidate_changed")
    except Exception:
        reasons.append("target_candidate_missing")
    reasons = list(dict.fromkeys(reasons))
    return {
        "fresh": not reasons,
        "reassessment_required": bool(reasons),
        "blocked_reasons": reasons,
        "age_seconds": age_seconds,
        "max_age_seconds": max(0.0, float(max_age_seconds)),
        "target_hash": execution_target.get("target_hash"),
    }
