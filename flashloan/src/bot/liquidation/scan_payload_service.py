from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable, Optional


def page_value(value: object, default: int = 1) -> int:
    try:
        return max(1, int(value or default))
    except (TypeError, ValueError):
        return default


def page_size_value(value: object, default: int = 20) -> int:
    try:
        return max(1, min(int(value or default), 100))
    except (TypeError, ValueError):
        return default


def pagination(page: int, page_size: int, total_count: int) -> dict[str, int]:
    return {
        "page": page,
        "page_size": page_size,
        "total_count": total_count,
        "page_count": max(1, (total_count + page_size - 1) // page_size),
    }


def empty_tier_payload(
    page_size: int = 20,
    *,
    high_page: int = 1,
    core_page: int = 1,
) -> dict:
    return {
        "borrow_health_count": 0,
        "high_frequency_rows": [],
        "core_opportunity_rows": [],
        "high_frequency_count": 0,
        "core_opportunity_count": 0,
        "pagination": {
            "high_frequency": pagination(page_value(high_page), page_size_value(page_size), 0),
            "core_opportunity": pagination(page_value(core_page), page_size_value(page_size), 0),
        },
    }


def database_block_reason(configured: bool, error: str | None) -> str:
    if not configured:
        return "database_unconfigured"
    text = str(error or "").lower()
    if "endpoint has been disabled" in text:
        return "database_endpoint_disabled"
    if "terminating connection due to administrator command" in text:
        return "database_connection_terminated"
    return "database_unavailable"


def database_next_action(reason: str) -> str:
    return {
        "database_unconfigured": "配置有效 DATABASE_URL 后重启 UI，再执行账户扫描。",
        "database_endpoint_disabled": "当前数据库端点已被服务商禁用；启用端点或替换 DATABASE_URL 后重启 UI，再继续扫描。",
        "database_connection_terminated": "数据库连接被管理端终止；确认端点状态后重启 UI，再继续扫描。",
        "database_unavailable": "数据库当前不可用或连接已失效；恢复数据库端点后重启 UI，再继续扫描。",
    }.get(reason, "恢复数据库连接后重启 UI，再继续扫描。")


def build_borrow_pool_blocked_payload(
    *,
    configured: bool,
    error: str,
    decision_from_payload: Callable[[dict], dict],
    page_size: int = 20,
    risk_page: int = 1,
    high_page: int = 1,
    core_page: int = 1,
    latest_batch: Optional[dict] = None,
) -> dict:
    normalized_size = page_size_value(page_size)
    tiers = empty_tier_payload(normalized_size, high_page=high_page, core_page=core_page)
    reason = database_block_reason(configured, error)
    summary = {
        "configured": configured,
        "db_ready": False,
        "scan_blocked": True,
        "scan_blocked_reason": reason,
        "next_action": database_next_action(reason),
        "error": error,
        "scanned": False,
        "scan_running": False,
        "stage": "database",
        "count": 0,
        "display_limit": normalized_size,
        "latest_batch": latest_batch,
    }
    payload = {
        "rows": [],
        "tiers": tiers,
        "latest_batch": latest_batch,
        "summary": summary,
        "pagination": {
            "borrow_health": pagination(page_value(risk_page), normalized_size, 0),
            **tiers["pagination"],
        },
    }
    payload["debt_pool_decision"] = decision_from_payload(payload)
    return payload


def parse_core_execution_time(value: object) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except ValueError:
        return None


def core_execution_summary(row: dict, latest_attempt: Optional[dict] = None) -> dict:
    blocked_reasons = [str(reason) for reason in (row.get("blocked_reasons") or []) if reason]
    profit = float(row.get("estimated_operator_net_profit_usd") or 0.0)
    assessment = row.get("profit_assessment") if isinstance(row.get("profit_assessment"), dict) else {}
    if "account_not_liquidatable" in blocked_reasons:
        value_state = "not_liquidatable"
    elif profit < 1.0 or "profit_below_minimum" in blocked_reasons:
        value_state = "manual_test_under_1u"
    elif not row.get("best_debt_asset") or not row.get("best_collateral_asset") or "no_liquidation_candidate" in blocked_reasons:
        value_state = "no_executable_candidate"
    else:
        value_state = "worth_executing"

    if row.get("auto_execution_blocked"):
        execution_status = "blocked"
    elif not row.get("best_debt_asset") or not row.get("best_collateral_asset"):
        execution_status = "no_candidate"
    elif not row.get("quote_viable"):
        execution_status = "waiting_quote"
    elif str(row.get("static_call_status") or "").lower() in {"error", "failed"}:
        execution_status = "preflight_failed"
    elif str(row.get("static_call_status") or "").lower() == "passed":
        execution_status = "preflight_passed"
    else:
        execution_status = "waiting_preflight"

    latest = None
    latest_is_current = False
    if isinstance(latest_attempt, dict):
        row_scanned_at = parse_core_execution_time(row.get("last_scanned_at"))
        attempt_created_at = parse_core_execution_time(latest_attempt.get("created_at"))
        latest_is_current = not (row_scanned_at and attempt_created_at and attempt_created_at < row_scanned_at)
        latest = {
            "state": latest_attempt.get("state"),
            "execution_phase": latest_attempt.get("execution_phase"),
            "tx_hash": latest_attempt.get("tx_hash"),
            "created_at": latest_attempt.get("created_at"),
            "stale": not latest_is_current,
        }
    return {
        "value_state": value_state,
        "value_usd": profit,
        "above_auto_profit_threshold": bool(
            assessment.get("above_auto_profit_threshold", row.get("above_auto_profit_threshold"))
        ),
        "auto_execution_allowed": not bool(row.get("auto_execution_blocked")),
        "execution_status": execution_status,
        "execution_result": ((latest or {}).get("state") if latest_is_current else None) or "not_submitted",
        "blocked_reasons": blocked_reasons,
        "latest_attempt": latest,
    }


def attach_core_execution_summaries(
    database_url: str,
    rows: list[dict],
    *,
    market_kwargs: dict[str, object],
    load_latest_for_accounts: Callable[..., list[dict]],
    load_recent: Callable[..., list[dict]],
) -> list[dict]:
    if not rows:
        return rows
    latest_by_account: dict[str, dict] = {}
    row_accounts = [str(row.get("account") or "") for row in rows if str(row.get("account") or "").strip()]
    try:
        attempts = load_latest_for_accounts(database_url, row_accounts, **market_kwargs)
    except TypeError:
        if market_kwargs:
            raise
        try:
            attempts = load_latest_for_accounts(database_url, row_accounts)
        except Exception:
            attempts = []
    except Exception:
        attempts = []
    if not attempts:
        try:
            attempts = load_recent(database_url, limit=max(100, len(rows) * 5), **market_kwargs)
        except TypeError:
            if market_kwargs:
                raise
            try:
                attempts = load_recent(database_url, limit=max(100, len(rows) * 5))
            except Exception:
                attempts = []
        except Exception:
            attempts = []
    try:
        for attempt in attempts:
            account = str(attempt.get("account") or "").lower()
            if account and account not in latest_by_account:
                latest_by_account[account] = attempt
    except Exception:
        latest_by_account = {}
    return [
        {
            **row,
            "execution": core_execution_summary(
                row,
                latest_by_account.get(str(row.get("account") or "").lower()),
            ),
        }
        for row in rows
    ]


def load_core_rows_with_execution(
    database_url: str,
    limit: int,
    offset: int,
    *,
    market_kwargs: dict[str, object],
    load_core: Callable[..., list[dict]],
    load_latest_for_accounts: Callable[..., list[dict]],
    load_recent: Callable[..., list[dict]],
) -> list[dict]:
    try:
        rows = load_core(database_url, limit=limit, offset=max(0, int(offset)), **market_kwargs)
    except TypeError:
        if int(offset) != 0 or market_kwargs:
            raise
        rows = load_core(database_url, limit=limit)
    return attach_core_execution_summaries(
        database_url,
        rows,
        market_kwargs=market_kwargs,
        load_latest_for_accounts=load_latest_for_accounts,
        load_recent=load_recent,
    )


def build_pool_tier_payload(
    database_url: str,
    limit: int,
    *,
    high_page: int,
    core_page: int,
    market_kwargs: dict[str, object],
    load_high: Callable[..., list[dict]],
    load_core: Callable[..., list[dict]],
    load_counts: Callable[..., dict[str, int]],
    load_latest_for_accounts: Callable[..., list[dict]],
    load_recent: Callable[..., list[dict]],
) -> dict:
    page_size = page_size_value(limit)
    high_page = page_value(high_page)
    core_page = page_value(core_page)
    high_frequency = load_high(
        database_url,
        limit=page_size,
        offset=(high_page - 1) * page_size,
        **market_kwargs,
    )
    core = load_core_rows_with_execution(
        database_url,
        limit=page_size,
        offset=(core_page - 1) * page_size,
        market_kwargs=market_kwargs,
        load_core=load_core,
        load_latest_for_accounts=load_latest_for_accounts,
        load_recent=load_recent,
    )
    counts = load_counts(database_url, **market_kwargs)
    return {
        "borrow_health_count": counts["borrow_health_count"],
        "high_frequency_rows": high_frequency,
        "core_opportunity_rows": core,
        "high_frequency_count": counts["high_frequency_count"],
        "core_opportunity_count": counts["core_opportunity_count"],
        "pagination": {
            "high_frequency": pagination(high_page, page_size, counts["high_frequency_count"]),
            "core_opportunity": pagination(core_page, page_size, counts["core_opportunity_count"]),
        },
    }


def build_borrow_pool_display_payload(
    *,
    database_url: str,
    page_size: int,
    risk_page: int,
    high_page: int,
    core_page: int,
    scope: dict[str, object],
    market_kwargs: dict[str, object],
    ensure_schema: Callable[[str], None],
    load_borrow_health: Callable[..., list[dict]],
    build_tiers: Callable[..., dict],
    load_latest_batch: Callable[..., dict | None],
    load_scan_configs: Callable[..., list[dict]],
    build_summary: Callable[..., dict],
    decision_from_payload: Callable[[dict], dict],
    database_fingerprint: Callable[[str | None], str | None],
    skip_schema: bool = False,
) -> dict:
    normalized_size = page_size_value(page_size)
    if not skip_schema:
        ensure_schema(database_url)
    rows = load_borrow_health(
        database_url,
        limit=normalized_size,
        offset=(risk_page - 1) * normalized_size,
        **market_kwargs,
    )
    tiers = build_tiers(
        database_url,
        normalized_size,
        high_page=high_page,
        core_page=core_page,
        **market_kwargs,
    )
    latest_batch = load_latest_batch(database_url, **market_kwargs)
    scan_configs = load_scan_configs(database_url, limit=20, **market_kwargs)
    summary = build_summary(
        rows,
        display_limit=normalized_size,
        scan_payload={"summary": {"latest_batch": latest_batch, "pool_counts": tiers}},
    )
    summary.update(
        {
            "configured": True,
            "db_ready": True,
            "scan_blocked": False,
            "scan_blocked_reason": None,
            "next_action": None,
            "database_url_fingerprint": database_fingerprint(database_url),
            "market_id": scope["market_id"],
            "chain_id": scope["chain_id"],
            "network": scope["network"],
            "protocol": scope["protocol"],
            "scan_response_source": "database_display",
        }
    )
    payload = {
        "rows": rows,
        "tiers": tiers,
        "latest_batch": latest_batch,
        "scan_configs": scan_configs,
        "summary": summary,
        "pagination": {
            "borrow_health": pagination(risk_page, normalized_size, tiers["borrow_health_count"]),
            **tiers["pagination"],
        },
    }
    payload["debt_pool_decision"] = decision_from_payload(payload)
    return payload
