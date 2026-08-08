from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from page_state import DebtPoolScanResult, DebtPoolStatus


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _row_account(row: dict[str, Any]) -> str:
    return str(row.get("account") or "").strip()


def is_liquidatable_row(row: dict[str, Any]) -> bool:
    status = str(row.get("status") or "").lower()
    if status == "liquidatable":
        return True
    health_factor = _float_or_none(row.get("health_factor"))
    return health_factor is not None and health_factor < 1.0


def candidate_hash(row: dict[str, Any], *, source_pool: str, block_number: int | None = None) -> str:
    stable = {
        "account": _row_account(row),
        "source_pool": source_pool,
        "health_factor": row.get("health_factor"),
        "last_scanned_at": row.get("last_scanned_at"),
        "block_number": block_number,
        "best_debt_asset": row.get("best_debt_asset"),
        "best_collateral_asset": row.get("best_collateral_asset"),
        "debt_to_cover_units": row.get("debt_to_cover_units"),
    }
    encoded = json.dumps(stable, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:24]


def build_liquidatable_context(
    row: dict[str, Any],
    *,
    source_pool: str,
    block_number: int | None = None,
    checked_at: str | None = None,
) -> dict[str, Any]:
    now = checked_at or datetime.now(timezone.utc).isoformat(timespec="seconds")
    return {
        "account": _row_account(row),
        "health_factor": row.get("health_factor"),
        "checked_at": now,
        "block_number": block_number,
        "candidate_hash": candidate_hash(row, source_pool=source_pool, block_number=block_number),
        "source_pool": source_pool,
        "last_scanned_at": row.get("last_scanned_at"),
    }


def _parse_iso(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def validate_liquidatable_context(
    context: dict[str, Any] | None,
    *,
    account: str | None = None,
    max_age_seconds: int = 30,
    latest_block_number: int | None = None,
    max_block_lag: int = 3,
    now: datetime | None = None,
) -> dict[str, Any]:
    ctx = dict(context or {})
    reasons: list[str] = []
    ctx_account = str(ctx.get("account") or "").strip().lower()
    expected_account = str(account or "").strip().lower()
    if expected_account and ctx_account and ctx_account != expected_account:
        reasons.append("context_account_mismatch")

    checked_at = _parse_iso(ctx.get("checked_at"))
    current = now or datetime.now(timezone.utc)
    if checked_at is None:
        reasons.append("context_missing_checked_at")
        age_seconds = None
    else:
        age_seconds = max(0.0, (current - checked_at).total_seconds())
        if age_seconds > max(0, int(max_age_seconds)):
            reasons.append("context_expired")

    block_number = ctx.get("block_number")
    try:
        block_number_int = int(block_number) if block_number is not None and str(block_number).strip() != "" else None
    except (TypeError, ValueError):
        block_number_int = None
    if block_number_int is None:
        reasons.append("context_missing_block_number")
    elif latest_block_number is not None and int(latest_block_number) - block_number_int > max(0, int(max_block_lag)):
        reasons.append("context_block_too_old")

    if not str(ctx.get("candidate_hash") or "").strip():
        reasons.append("context_missing_candidate_hash")

    return {
        "fresh": not reasons,
        "blocked_reasons": reasons,
        "age_seconds": age_seconds,
        "max_age_seconds": max(0, int(max_age_seconds)),
        "block_number": block_number_int,
        "latest_block_number": latest_block_number,
        "max_block_lag": max(0, int(max_block_lag)),
        "context": ctx,
    }


def _first_liquidatable(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    for row in rows:
        if _row_account(row) and is_liquidatable_row(row):
            return row
    return None


def decide_debt_pool_layers(
    *,
    core_rows: list[dict[str, Any]] | None = None,
    high_frequency_rows: list[dict[str, Any]] | None = None,
    normal_rows: list[dict[str, Any]] | None = None,
    block_number: int | None = None,
    checked_at: str | None = None,
) -> dict[str, Any]:
    core = list(core_rows or [])
    high = list(high_frequency_rows or [])
    normal = list(normal_rows or [])

    core_liquidatable = _first_liquidatable(core)
    if core_liquidatable:
        return {
            "status": DebtPoolStatus.CORE_LIQUIDATION_DECISION.value,
            "result": DebtPoolScanResult.CORE_POOL_LIQUIDATABLE.value,
            "route_intent": "execution",
            "liquidatable_context": build_liquidatable_context(
                core_liquidatable,
                source_pool="core",
                block_number=block_number,
                checked_at=checked_at,
            ),
            "counts": {"core": len(core), "high_frequency": len(high), "normal": len(normal)},
        }

    if high:
        return {
            "status": DebtPoolStatus.SYNCING_CORE_POOL.value,
            "result": DebtPoolScanResult.HIGH_FREQUENCY_RISK_FOUND.value,
            "route_intent": "sync_core_then_rejudge",
            "liquidatable_context": None,
            "risk_context": build_liquidatable_context(high[0], source_pool="high_frequency", block_number=block_number, checked_at=checked_at),
            "counts": {"core": len(core), "high_frequency": len(high), "normal": len(normal)},
        }

    if normal:
        return {
            "status": DebtPoolStatus.SYNCING_CORE_POOL.value,
            "result": DebtPoolScanResult.NORMAL_POOL_RISK_FOUND.value,
            "route_intent": "sync_core_then_rejudge",
            "liquidatable_context": None,
            "risk_context": build_liquidatable_context(normal[0], source_pool="normal", block_number=block_number, checked_at=checked_at),
            "counts": {"core": len(core), "high_frequency": len(high), "normal": len(normal)},
        }

    return {
        "status": DebtPoolStatus.IDLE_FRESH.value,
        "result": DebtPoolScanResult.NO_NORMAL_POOL_RISK.value,
        "route_intent": None,
        "liquidatable_context": None,
        "counts": {"core": len(core), "high_frequency": len(high), "normal": len(normal)},
    }


def decision_from_borrow_pool_payload(payload: dict[str, Any]) -> dict[str, Any]:
    tiers = payload.get("tiers") if isinstance(payload.get("tiers"), dict) else {}
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    block_number = summary.get("block_number")
    try:
        block_number = int(block_number) if block_number is not None else None
    except (TypeError, ValueError):
        block_number = None
    core_rows = list(tiers.get("core_opportunity_rows") or [])
    high_rows = list(tiers.get("high_frequency_rows") or [])
    core_accounts = {_row_account(row) for row in core_rows}
    high_accounts = {_row_account(row) for row in high_rows}
    normal_rows = [
        row
        for row in list(payload.get("rows") or [])
        if _row_account(row) and _row_account(row) not in core_accounts and _row_account(row) not in high_accounts
    ]
    return decide_debt_pool_layers(
        core_rows=core_rows,
        high_frequency_rows=high_rows,
        normal_rows=normal_rows,
        block_number=block_number,
    )
