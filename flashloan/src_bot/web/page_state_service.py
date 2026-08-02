from __future__ import annotations

from typing import Any

from web.account_pool_state_service import account_pool_state_payload
from web.market_volatility_event_service import (
    build_market_volatility_event,
    market_volatility_event_is_fresh,
    market_volatility_route_intent,
)
from web.market_volatility_event_store import (
    consume_market_volatility_event,
    record_market_volatility_event,
)
from web.page_state import AccountScanStatus, DebtPoolStatus, ExecutionStatus, MarketObservationStatus, PageName
from web.page_state_store import PAGE_STATE_STORE


def _cache_value(panel: Any, name: str) -> dict:
    value = getattr(panel, name, {})
    return value if isinstance(value, dict) else {}


def _call_value(panel: Any, name: str, *args: Any, **kwargs: Any) -> Any:
    value = getattr(panel, name, None)
    if not callable(value):
        return None
    try:
        return value(*args, **kwargs)
    except Exception:
        return None


def _state_payload(
    page: PageName,
    status: str,
    *,
    result: str | None = None,
    message: str | None = None,
    source_event_id: str | None = None,
    last_error: str | None = None,
    context: dict | None = None,
) -> dict:
    return PAGE_STATE_STORE.set(
        page.value,
        status,
        result=result,
        message=message,
        source_event_id=source_event_id,
        last_error=last_error,
        context=context,
    ).to_dict()


def store_page_state(
    page: PageName,
    status: str,
    *,
    result: str | None = None,
    message: str | None = None,
    source_event_id: str | None = None,
    last_error: str | None = None,
    context: dict | None = None,
) -> dict:
    return _state_payload(
        page,
        status,
        result=result,
        message=message,
        source_event_id=source_event_id,
        last_error=last_error,
        context=context,
    )


def _latest_extremes(panel: Any) -> dict | None:
    latest = _call_value(panel, "latest_binance_extremes_file")
    if isinstance(latest, dict):
        return latest
    latest = _call_value(panel, "latest_binance_extremes")
    return latest if isinstance(latest, dict) else None


def _market_event_snapshot(panel: Any) -> dict | None:
    event = build_market_volatility_event(_latest_extremes(panel))
    if event and market_volatility_event_is_fresh(event):
        event = _with_market_event_record(event, record_market_volatility_event(event))
    return event


def _with_market_event_record(event: dict, record: dict | None) -> dict:
    if not isinstance(record, dict):
        return event
    result = dict(event)
    result["store_status"] = record.get("status")
    result["recorded_at"] = record.get("recorded_at")
    result["consumed_at"] = record.get("consumed_at")
    result["consumer_page"] = record.get("consumer_page")
    return result


def _market_event_pending(event: dict | None) -> bool:
    return bool(
        event
        and market_volatility_event_is_fresh(event)
        and not event.get("consumed_at")
        and event.get("store_status") != "consumed"
    )


def debt_pool_state_payload(panel: Any) -> dict:
    scan_cache = _cache_value(panel, "LIQUIDATION_SCAN_CACHE")
    account_pool = account_pool_state_payload(panel)
    market_event = _market_event_snapshot(panel)
    current_state = PAGE_STATE_STORE.get(PageName.DEBT_POOL.value, DebtPoolStatus.ENTER.value)
    running = bool(scan_cache.get("running"))
    stage = str(scan_cache.get("stage") or "idle")
    last_result = scan_cache.get("last_result") if isinstance(scan_cache.get("last_result"), dict) else {}
    decision = last_result.get("debt_pool_decision") if isinstance(last_result.get("debt_pool_decision"), dict) else {}
    error = last_result.get("error") or scan_cache.get("error")
    new_market_event = bool(_market_event_pending(market_event) and market_event.get("event_id") != current_state.source_event_id)
    if new_market_event:
        status = DebtPoolStatus.MARKET_ALERT_RECEIVED
        result = str(account_pool.get("result") or "ACCOUNT_POOL_READY")
        message = str(market_event.get("trigger_reason") or "market volatility alert received")
        source_event_id = str(market_event.get("event_id") or "")
        route_intent = market_volatility_route_intent(market_event)
        market_event = _with_market_event_record(
            market_event,
            consume_market_volatility_event(market_event, PageName.DEBT_POOL.value),
        )
    elif not account_pool.get("ready"):
        status = DebtPoolStatus.NEED_ACCOUNT_POOL
        result = str(account_pool.get("result") or "")
        message = str(account_pool.get("reason") or "")
        source_event_id = None
        route_intent = None
    elif running:
        if stage == "debt_pool":
            status = DebtPoolStatus.SCANNING_CORE_POOL
        elif stage == "health":
            status = DebtPoolStatus.CORE_LIQUIDATION_DECISION
        else:
            status = DebtPoolStatus.CHECKING_DATA
        result = None
        message = None
        source_event_id = None
        route_intent = None
    elif error:
        status = DebtPoolStatus.IDLE_STALE
        result = "SCAN_FAILED"
        message = str(error)
        source_event_id = None
        route_intent = None
    elif decision:
        status = DebtPoolStatus(str(decision.get("status") or DebtPoolStatus.IDLE_FRESH.value))
        result = str(decision.get("result") or "") or None
        message = None
        source_event_id = None
        route_intent = None
    else:
        status = DebtPoolStatus.IDLE_FRESH
        result = None
        message = None
        source_event_id = None
        route_intent = None
    return _state_payload(
        PageName.DEBT_POOL,
        status.value,
        result=result,
        message=message,
        source_event_id=source_event_id,
        last_error=str(error) if error else None,
        context={
            "account_pool": account_pool,
            "market_event": market_event,
            "route_intent": route_intent,
            "event_received": new_market_event,
            "debt_pool_decision": decision,
            "stage": stage,
            "running": running,
            "started_at": scan_cache.get("started_at"),
            "finished_at": scan_cache.get("finished_at"),
            "progress": scan_cache.get("progress") or {},
            "last_result": last_result,
        },
    )


def account_scan_state_payload(panel: Any) -> dict:
    backfill_cache = _cache_value(panel, "LIQUIDATION_ACCOUNT_BACKFILL_CACHE")
    discovery_cache = _cache_value(panel, "LIQUIDATION_DISCOVERY_CACHE")
    account_pool = account_pool_state_payload(panel)
    backfill_running = bool(backfill_cache.get("running"))
    discovery_running = bool(discovery_cache.get("running"))
    discovery_result = discovery_cache.get("last_result") if isinstance(discovery_cache.get("last_result"), dict) else {}
    error = backfill_cache.get("error") or discovery_result.get("error")
    if backfill_running:
        stage = str(backfill_cache.get("stage") or "")
        status = AccountScanStatus.SCANNING_HISTORICAL_EVENTS if stage == "borrowers" else AccountScanStatus.PREPARING_BACKFILL
    elif discovery_running:
        status = AccountScanStatus.SCANNING_EVENTS
    elif error:
        status = AccountScanStatus.ERROR
    else:
        status = AccountScanStatus.SHOWING_ACCOUNT_POOL
    return _state_payload(
        PageName.ACCOUNT_SCAN,
        status.value,
        message=str(error) if error else None,
        last_error=str(error) if error else None,
        context={
            "account_pool": account_pool,
            "backfill": {
                "running": backfill_running,
                "stage": backfill_cache.get("stage"),
                "progress": backfill_cache.get("progress") or {},
                "started_at": backfill_cache.get("started_at"),
                "finished_at": backfill_cache.get("finished_at"),
            },
            "discovery": {
                "running": discovery_running,
                "stage": discovery_cache.get("stage"),
                "progress": discovery_cache.get("progress") or {},
                "started_at": discovery_cache.get("started_at"),
                "finished_at": discovery_cache.get("finished_at"),
            },
        },
    )


def market_observation_state_payload(panel: Any) -> dict:
    if getattr(panel, "observer_starting", False):
        status = MarketObservationStatus.STARTING
        event = None
        message = None
    elif panel.quick_observer_running():
        event = _market_event_snapshot(panel)
        if _market_event_pending(event):
            status = MarketObservationStatus.ALERTING_DEBT_POOL
            message = str(event.get("trigger_reason") or "")
        elif event and market_volatility_event_is_fresh(event):
            status = MarketObservationStatus.VOLATILITY_DETECTED
            message = str(event.get("trigger_reason") or "")
        elif event:
            status = MarketObservationStatus.VOLATILITY_DETECTED
            message = str(event.get("trigger_reason") or "")
        else:
            status = MarketObservationStatus.OBSERVING
            message = None
    elif getattr(panel, "observer_start_error", None):
        status = MarketObservationStatus.ERROR
        event = None
        message = getattr(panel, "observer_start_error", None)
    else:
        status = MarketObservationStatus.IDLE
        event = _market_event_snapshot(panel)
        message = str(event.get("trigger_reason") or "") if event else None
    return _state_payload(
        PageName.MARKET_OBSERVATION,
        status.value,
        message=message,
        source_event_id=str(event.get("event_id") or "") if isinstance(event, dict) else None,
        last_error=getattr(panel, "observer_start_error", None),
        context={
            "pid": panel.quick_observer_pid() if status in {MarketObservationStatus.OBSERVING, MarketObservationStatus.ALERTING_DEBT_POOL, MarketObservationStatus.VOLATILITY_DETECTED} else None,
            "market_event": event,
            "route_intent": market_volatility_route_intent(event) if _market_event_pending(event) else None,
        },
    )


def execution_state_payload(panel: Any) -> dict:
    state = PAGE_STATE_STORE.get(PageName.EXECUTION.value, ExecutionStatus.IDLE.value)
    return _state_payload(
        PageName.EXECUTION,
        state.status,
        result=state.result,
        message=state.message,
        source_event_id=state.source_event_id,
        last_error=state.last_error,
        context=dict(state.context) or {"message": "execution page has no active submission task"},
    )
