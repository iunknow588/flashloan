from __future__ import annotations

import json
import math
import os
from pathlib import Path
from typing import Any

from execution.liquidation_priority import liquidation_pool_tier
from execution.health_checker import health_factor_band
from execution.venus_liquidation_scan import venus_health_factor


BNB_MARKET_ID = "bnb-venus-core"
BNB_CHAIN_ID = 56
DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parents[3] / "data_info" / "BNB" / "liquidation"


def bnb_venus_output_dir() -> Path:
    configured = os.getenv("BNB_VENUS_LIQUIDATION_DIR", "").strip()
    return Path(configured) if configured else DEFAULT_OUTPUT_DIR


def _page(value: object, default: int = 1) -> int:
    try:
        return max(1, int(value or default))
    except (TypeError, ValueError):
        return default


def _page_size(value: object, default: int = 20) -> int:
    try:
        return max(1, min(100, int(value or default)))
    except (TypeError, ValueError):
        return default


def _pagination(page: int, page_size: int, total_count: int) -> dict[str, int]:
    return {
        "page": page,
        "page_size": page_size,
        "total_count": total_count,
        "page_count": max(1, math.ceil(total_count / page_size)),
    }


def _slice(rows: list[dict], page: int, page_size: int) -> list[dict]:
    start = (page - 1) * page_size
    return rows[start : start + page_size]


def _read_json_file(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _registry_metadata() -> dict[str, Any]:
    path = bnb_venus_output_dir() / "venus_borrower_registry.json"
    payload = _read_json_file(path)
    raw_accounts = payload.get("accounts") if isinstance(payload.get("accounts"), list) else []
    accounts = [
        row if isinstance(row, dict) else {"account": str(row)}
        for row in raw_accounts
        if (isinstance(row, dict) and row.get("account")) or (not isinstance(row, dict) and str(row).strip())
    ]
    account_count = payload.get("account_count")
    try:
        account_count = int(account_count)
    except (TypeError, ValueError):
        account_count = len(accounts)
    return {
        "path": str(path),
        "account_count": account_count,
        "accounts": accounts,
        "scan_complete": bool(payload.get("scan_complete")),
        "storage_mode": payload.get("storage_mode"),
        "discovery_mode": payload.get("discovery_mode"),
        "event_count": payload.get("event_count"),
        "latest_block": payload.get("latest_block"),
        "lookback_days": payload.get("lookback_days"),
    }


def _as_float(value: object) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _refresh_pool_tier(health_factor: float | int | None) -> str:
    tier = liquidation_pool_tier(health_factor)
    return tier if tier in {"core", "high_frequency"} else "borrow_health"


def _health_fields(summary: dict[str, Any], risk: dict[str, Any]) -> tuple[float | None, str | None, str]:
    health_factor = _as_float(summary.get("health_factor"))
    if health_factor is None:
        health_factor = _as_float(risk.get("health_factor"))
    source = str(summary.get("health_factor_source") or risk.get("health_factor_source") or "")
    if health_factor is None:
        health_factor = venus_health_factor(
            risk.get("liquidity_raw", summary.get("liquidity_raw")),
            risk.get("shortfall_raw", summary.get("shortfall_raw")),
            summary.get("total_debt_value_usd", summary.get("total_debt_base")),
        )
        source = source or "venus_account_liquidity"
    if health_factor is None:
        return None, None, source or "venus_liquidity_only"
    return health_factor, health_factor_band(health_factor), source or "venus_account_liquidity"


def _normalized_positions(
    positions: list[dict[str, Any]],
    health_factor: float | None,
    health_factor_band_value: str | None,
) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for position in positions:
        if not isinstance(position, dict):
            continue
        normalized.append(
            {
                "symbol": position.get("underlying_symbol") or position.get("vtoken_symbol") or "-",
                "token_symbol": position.get("underlying_symbol") or position.get("vtoken_symbol") or "-",
                "oracle_price": position.get("oracle_price_usd"),
                "current_token_price": position.get("oracle_price_usd"),
                "collateral_amount": position.get("supply_underlying_amount"),
                "collateral_value_base": position.get("collateral_value_usd"),
                "stable_debt_amount": 0,
                "variable_debt_amount": position.get("borrow_amount"),
                "debt_value_base": position.get("debt_value_usd"),
                "usage_as_collateral_enabled": bool(position.get("entered_market")),
                "health_factor": health_factor,
                "health_factor_band": health_factor_band_value,
            }
        )
    return normalized


def _normalized_candidates(candidates: object) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for candidate in candidates if isinstance(candidates, list) else []:
        if not isinstance(candidate, dict):
            continue
        rows.append(
            {
                **candidate,
                "collateral_symbol": candidate.get("collateral_symbol") or candidate.get("collateral_asset"),
                "debt_symbol": candidate.get("debt_symbol") or candidate.get("debt_asset"),
                "collateral_price": candidate.get("collateral_price") or candidate.get("collateral_value_usd"),
                "debt_price": candidate.get("debt_price") or candidate.get("oracle_price_usd"),
                "debt_amount": candidate.get("debt_amount"),
                "amount_to_pass_to_liquidation_call_amount": candidate.get("repay_value_usd"),
                "estimated_profit": candidate.get("estimated_profit") or candidate.get("liquidation_profit") or {},
            }
        )
    return rows


def _health_display(
    health_factor: float | None,
    summary: dict[str, Any],
    risk: dict[str, Any],
) -> str:
    if health_factor is not None:
        return f"{health_factor:.3f}"
    liquidity = risk.get("liquidity_raw", summary.get("liquidity_raw"))
    shortfall = risk.get("shortfall_raw", summary.get("shortfall_raw"))
    if liquidity is not None or shortfall is not None:
        return f"L {liquidity or 0} / S {shortfall or 0}"
    return "-"


def _pending_report(account: str, registry_row: dict[str, Any]) -> dict[str, Any]:
    return {
        "account": account,
        "account_tier": registry_row.get("tier") or "ordinary",
        "pool_tier": "borrow_health",
        "summary": {
            "status": "pending",
            "health_factor": None,
            "health_factor_band": None,
            "health_factor_source": "pending_scan",
        },
        "risk": {"positions": []},
        "liquidation_candidates": [],
        "recommended_candidate": None,
    }


def _normalize_report(report: dict[str, Any], observed_at: str | None) -> dict[str, Any]:
    raw_summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
    risk = report.get("risk") if isinstance(report.get("risk"), dict) else {}
    health_factor, band, source = _health_fields(raw_summary, risk)
    pool_tier = _refresh_pool_tier(health_factor)
    candidates = _normalized_candidates(report.get("liquidation_candidates"))
    recommended = next((item for item in candidates if item.get("status") == "candidate"), None)
    status = str(raw_summary.get("status") or risk.get("status") or report.get("status") or "unknown").lower()
    total_collateral = raw_summary.get("total_collateral_value_usd", raw_summary.get("total_collateral_base"))
    total_debt = raw_summary.get("total_debt_value_usd", raw_summary.get("total_debt_base"))
    summary = {
        **raw_summary,
        "status": status,
        "health_factor": health_factor,
        "health_factor_band": band,
        "health_factor_source": source,
        "pool_tier": pool_tier,
        "health_display": "待扫描" if status == "pending" else _health_display(health_factor, raw_summary, risk),
        "total_collateral_base": total_collateral,
        "total_debt_base": total_debt,
        "candidate_count": len(candidates),
    }
    return {
        "market_id": BNB_MARKET_ID,
        "chain_id": BNB_CHAIN_ID,
        "network": "bnb",
        "protocol": "venus_core",
        "account": report.get("account"),
        "health_factor": health_factor,
        "health_factor_band": band,
        "health_factor_source": source,
        "pool_tier": pool_tier,
        "health_display": "待扫描" if status == "pending" else _health_display(health_factor, raw_summary, risk),
        "status": status,
        "total_collateral_base": total_collateral,
        "total_debt_base": total_debt,
        "candidate_count": len(candidates),
        "priority_score": float(len(candidates)) * 100.0 + (100.0 - min(100.0, max(0.0, health_factor or 100.0))),
        "best_debt_asset": (recommended or {}).get("debt_symbol") or (recommended or {}).get("debt_asset"),
        "best_collateral_asset": (recommended or {}).get("collateral_symbol") or (recommended or {}).get("collateral_asset"),
        "debt_to_cover_units": (recommended or {}).get("repay_amount_raw") or "",
        "estimated_operator_net_profit_usd": (
            ((recommended or {}).get("estimated_profit") or {}).get("operator_net_profit_usd")
            or ((recommended or {}).get("estimated_profit") or {}).get("net_profit_base")
            or 0.0
        ),
        "quote_viable": bool(recommended),
        "static_call_status": "observation_only",
        "blocked_reasons": ["venus_core_observation_only"],
        "execution": {
            "value_state": "observation_only",
            "execution_status": "observation_only",
            "latest_result": "observation_only",
            "reason": "BNB Venus Core observation-only",
        },
        "last_scanned_at": observed_at,
        "report": {
            **report,
            "market_id": BNB_MARKET_ID,
            "chain_id": BNB_CHAIN_ID,
            "summary": summary,
            "positions": _normalized_positions(
                list(report.get("positions") or risk.get("positions") or []),
                health_factor,
                band,
            ),
            "liquidation_candidates": candidates,
            "recommended_candidate": recommended,
            "execution_plan": {
                "execution_ready": False,
                "liquidation_ready": status == "liquidatable",
                "profitable": bool(recommended),
                "reason": "BNB Venus Core remains observation-only.",
                "health_factor": health_factor,
                "recommended_candidate": recommended,
                "profit_preview": (recommended or {}).get("estimated_profit") or {},
                "next_step": "continue watching BNB Venus account health",
            },
            "execution_enabled": False,
            "context": {
                "market": {
                    "market_id": BNB_MARKET_ID,
                    "chain_id": BNB_CHAIN_ID,
                    "network": "bnb",
                    "protocol": "venus_core",
                },
                "source": "bnb_venus_current_health_pool",
                "execution_mode": "observation_only",
                "observed_at": observed_at,
            },
        },
    }


def bnb_venus_rows() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    path = bnb_venus_output_dir() / "venus_liquidation_current.json"
    registry = _registry_metadata()
    payload = _read_json_file(path)
    current_reports = payload.get("reports") if isinstance(payload.get("reports"), list) else []
    reports_by_account = {
        str(report.get("account") or "").lower(): report
        for report in current_reports
        if isinstance(report, dict) and report.get("account")
    }
    registry_rows = [row for row in registry.get("accounts") or [] if isinstance(row, dict) and row.get("account")]
    reports = [
        reports_by_account.get(str(row.get("account") or "").lower()) or _pending_report(str(row["account"]), row)
        for row in registry_rows
    ]
    if not registry_rows:
        reports = current_reports
    observed_at = str(payload.get("generated_at") or "") or None
    rows = [
        _normalize_report(report, str(report.get("observed_at") or observed_at or "") or None)
        for report in reports
        if isinstance(report, dict) and report.get("account")
    ]
    pool_tier_counts: dict[str, int] = {}
    for row in rows:
        tier = str(row.get("pool_tier") or "unknown")
        pool_tier_counts[tier] = pool_tier_counts.get(tier, 0) + 1
    rows.sort(
        key=lambda row: (
            row.get("health_factor") is None,
            float(row.get("health_factor") or 999.0),
            str(row.get("account") or ""),
        )
    )
    return rows, {
        "source": "bnb_venus_current_health_pool",
        "path": str(path),
        "observed_at": observed_at,
        "block_number": payload.get("last_block"),
        "mode": "observation_only",
        "execution_enabled": False,
        "watcher_summary": payload.get("summary") or {},
        "registry": registry,
        "pool_tier_counts": pool_tier_counts,
        "error": None if path.exists() else "BNB Venus current health pool is not initialized",
    }


def bnb_venus_borrow_pool_payload(
    *,
    page_size: int = 20,
    risk_page: int = 1,
    high_page: int = 1,
    core_page: int = 1,
) -> dict[str, Any]:
    page_size = _page_size(page_size)
    risk_page = _page(risk_page)
    high_page = _page(high_page)
    core_page = _page(core_page)
    rows, metadata = bnb_venus_rows()
    registry = metadata.get("registry") if isinstance(metadata.get("registry"), dict) else {}
    registry_account_count = int(registry.get("account_count") or 0)
    health_report_account_count = sum(1 for row in rows if row.get("status") != "pending")
    observed_account_count = len(rows)
    scanned_account_count = sum(1 for row in rows if row.get("status") != "pending")
    current_pool_account_count = registry_account_count or observed_account_count
    pending_account_count = max(0, current_pool_account_count - scanned_account_count)
    scan_coverage_rate = (
        float(scanned_account_count) / float(current_pool_account_count)
        if current_pool_account_count
        else 0.0
    )
    core_rows = [row for row in rows if liquidation_pool_tier(row.get("health_factor")) == "core"]
    high_rows = [
        row
        for row in rows
        if row not in core_rows
        and liquidation_pool_tier(row.get("health_factor")) == "high_frequency"
    ]
    ordinary_rows = [row for row in rows if row not in core_rows and row not in high_rows]
    worst = next((row for row in rows if row.get("health_factor") is not None), None)
    summary = {
        "configured": True,
        "db_ready": True,
        "scan_blocked": False,
        "scan_response_source": "file_observation",
        "market_id": BNB_MARKET_ID,
        "chain_id": BNB_CHAIN_ID,
        "network": "bnb",
        "protocol": "venus_core",
        "observation_only": True,
        "execution_enabled": False,
        "count": len(ordinary_rows),
        "ordinary_account_count": len(ordinary_rows),
        "high_frequency_account_count": len(high_rows),
        "core_account_count": len(core_rows),
        "watch_health_factor": 1.5,
        "worst_account": worst.get("account") if worst else None,
        "worst_health_factor": worst.get("health_factor") if worst else None,
        "scan_running": False,
        "scan_finished_at": metadata.get("observed_at"),
        "stage": "observation_only",
        "block_number": metadata.get("block_number"),
        "source_account_count": registry_account_count or observed_account_count,
        "selected_account_count": int((metadata.get("watcher_summary") or {}).get("last_round_scanned_count") or 0),
        "scanned_account_count": scanned_account_count,
        "pending_account_count": pending_account_count,
        "current_pool_account_count": current_pool_account_count,
        "scan_coverage_rate": scan_coverage_rate,
        "registry_account_count": registry_account_count,
        "registry_scan_complete": registry.get("scan_complete"),
        "registry_discovery_mode": registry.get("discovery_mode"),
        "registry_event_count": registry.get("event_count"),
        "health_report_account_count": scanned_account_count,
        "scan_strategy": "bnb_venus_current_health_pool",
        "account_tiers": {
            "hot_count": sum(1 for row in registry.get("accounts") or [] if isinstance(row, dict) and row.get("tier") == "hot"),
            "warm_count": sum(1 for row in registry.get("accounts") or [] if isinstance(row, dict) and row.get("tier") == "warm"),
            "cold_count": sum(1 for row in registry.get("accounts") or [] if isinstance(row, dict) and row.get("tier") == "cold"),
            "active_count": len(rows),
        },
        "pool_tier_counts": metadata.get("pool_tier_counts") if isinstance(metadata.get("pool_tier_counts"), dict) else {},
        "risk_pool_conversion_rate": float(len(ordinary_rows)) / float(len(rows)) if rows else 0.0,
        "error": metadata.get("error"),
        "data_source": metadata.get("source"),
        "data_path": metadata.get("path"),
        "watcher_mode": metadata.get("mode"),
    }
    tiers = {
        "borrow_health_count": len(ordinary_rows),
        "high_frequency_count": len(high_rows),
        "core_opportunity_count": len(core_rows),
        "high_frequency_rows": _slice(high_rows, high_page, page_size),
        "core_opportunity_rows": _slice(core_rows, core_page, page_size),
        "pagination": {
            "high_frequency": _pagination(high_page, page_size, len(high_rows)),
            "core_opportunity": _pagination(core_page, page_size, len(core_rows)),
        },
    }
    return {
        "rows": _slice(ordinary_rows, risk_page, page_size),
        "tiers": tiers,
        "latest_batch": {
            "status": "observation_only",
            "account_count": registry_account_count or observed_account_count,
            "scanned_count": int((metadata.get("watcher_summary") or {}).get("last_round_scanned_count") or 0),
            "block_number": metadata.get("block_number"),
            "finished_at": metadata.get("observed_at"),
        },
        "summary": summary,
        "pagination": {
            "borrow_health": _pagination(risk_page, page_size, len(ordinary_rows)),
            **tiers["pagination"],
        },
        "debt_pool_decision": {
            "status": "OBSERVATION_ONLY",
            "result": "BNB_VENUS_OBSERVATION_ONLY",
            "route_intent": "observe",
        },
    }


def bnb_venus_accounts_payload(*, page: int = 1, page_size: int = 20) -> dict[str, Any]:
    page = _page(page)
    page_size = _page_size(page_size)
    rows, metadata = bnb_venus_rows()
    registry = metadata.get("registry") if isinstance(metadata.get("registry"), dict) else {}
    registry_account_count = int(registry.get("account_count") or 0)
    health_report_account_count = sum(1 for row in rows if row.get("status") != "pending")
    current_pool_account_count = registry_account_count or len(rows)
    pending_health_count = max(0, current_pool_account_count - health_report_account_count)
    accounts = [
        {
            "market_id": BNB_MARKET_ID,
            "chain_id": BNB_CHAIN_ID,
            "network": "bnb",
            "protocol": "venus_core",
            "account": row.get("account"),
            "source": "bnb_venus_current_health_pool",
            "last_health_factor": row.get("health_factor"),
            "last_health_factor_band": row.get("health_factor_band"),
            "last_health_display": row.get("health_display"),
            "pool_tier": row.get("pool_tier"),
            "last_status": row.get("status"),
            "last_scanned_at": row.get("last_scanned_at"),
        }
        for row in rows
    ]
    return {
        "accounts": _slice(accounts, page, page_size),
        "account_rows": _slice(accounts, page, page_size),
        "count": len(accounts),
        "source": "bnb_venus_current_health_pool",
        "registry_account_count": registry_account_count,
        "health_report_account_count": health_report_account_count,
        "registry_window": {
            "market_id": BNB_MARKET_ID,
            "chain_id": BNB_CHAIN_ID,
            "network": "bnb",
            "protocol": "venus_core",
            "active_count": len(accounts),
            "total_count": registry_account_count or len(accounts),
            "health_report_count": health_report_account_count,
            "pending_health_count": pending_health_count,
            "scan_coverage_rate": (
                float(health_report_account_count) / float(current_pool_account_count)
                if current_pool_account_count
                else 0.0
            ),
            "registry_scan_complete": registry.get("scan_complete"),
            "registry_discovery_mode": registry.get("discovery_mode"),
            "registry_event_count": registry.get("event_count"),
            "latest_scan_end_at": metadata.get("observed_at"),
            "earliest_scan_start_at": metadata.get("observed_at"),
            "pool_tier_counts": metadata.get("pool_tier_counts") if isinstance(metadata.get("pool_tier_counts"), dict) else {},
        },
        "pagination": _pagination(page, page_size, len(accounts)),
        "error": metadata.get("error"),
    }


def bnb_venus_account_payload(account: str) -> dict[str, Any]:
    normalized = str(account or "").strip().lower()
    rows, metadata = bnb_venus_rows()
    row = next((item for item in rows if str(item.get("account") or "").lower() == normalized), None)
    if not row:
        return {
            "found": False,
            "cached": True,
            "account": account,
            "market_id": BNB_MARKET_ID,
            "chain_id": BNB_CHAIN_ID,
            "error": metadata.get("error") or "account is not present in the BNB Venus observation file",
        }
    report = dict(row.get("report") or {})
    return {
        "found": True,
        "cached": True,
        "source": "bnb_venus_current_health_pool",
        "market_id": BNB_MARKET_ID,
        "chain_id": BNB_CHAIN_ID,
        "network": "bnb",
        "protocol": "venus_core",
        "account": row.get("account"),
        "pool_tier": row.get("pool_tier"),
        "summary": report.get("summary") or {},
        "positions": report.get("positions") or [],
        "liquidation_candidates": report.get("liquidation_candidates") or [],
        "recommended_candidate": report.get("recommended_candidate"),
        "execution_plan": report.get("execution_plan") or {},
        "execution_enabled": False,
        "context": report.get("context") or {},
    }
