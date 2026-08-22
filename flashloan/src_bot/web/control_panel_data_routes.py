from pathlib import Path

from flask import jsonify, request

from core.sensitive_data import redact_sensitive_text
from db.storage_common import (
    database_unavailable_reason,
    is_database_unavailable_error,
    mark_database_unavailable,
)
from db.storage_cow_execution import (
    COW_ATTEMPT_CATEGORY_EXECUTION_FAILED,
    COW_ATTEMPT_CATEGORY_EXECUTION_SUCCESS,
    COW_ATTEMPT_CATEGORY_ANALYSIS_DAYS,
    COW_ATTEMPT_CATEGORY_NOT_EXECUTABLE,
    COW_ATTEMPT_CATEGORY_RETENTION_POLICY,
    COW_ATTEMPT_CATEGORY_RETENTION_DAYS,
    append_cow_execution_attempts_jsonl,
    build_cow_execution_attempts,
    build_cow_market_claim_candidate_attempts,
    build_cow_market_candidate_attempts,
    record_cow_execution_attempts,
)
from runtime.cow_arbitrage_daemon import (
    cow_quote_daemon_enabled,
    cow_quote_daemon_status,
    enqueue_cow_candidate_attempts,
    ensure_cow_quote_daemon_running,
)
from runtime.ui_signal_pool_evidence import record_ui_signal_pool_evidence
from intent_trade import submit_cow_intent_trade
from web.control_panel_cow_pause import (
    clear_cow_submission_pause_guard,
    cow_submission_pause_guard_status,
    set_cow_submission_pause_guard,
)
from core.chain_selection import ChainSelectionError
from cow_flashloan.network_scope import (
    DEFAULT_COW_NETWORK,
    parse_cow_networks,
)
from market.binance_market.service import (
    DEFAULT_MIN_COW_SPREAD_PERCENT,
    cost_adjusted_cow_thresholds,
)
from market.binance_market.submission_service import submit_ready_quote_orders
from web.chain_selection import resolve_request_chain_selection
from web.route_context import RouteContext

ROUTE_CONTEXT = RouteContext()
SRC_ROOT = Path(__file__).resolve().parents[1]
COW_EXECUTION_ATTEMPT_LOG_PATH = SRC_ROOT / "runtime" / "logs" / "cow_execution_attempts.jsonl"
COW_EXECUTION_RETENTION_DAYS = 7
COW_EXECUTION_REVIEW_CATEGORIES = [
    COW_ATTEMPT_CATEGORY_NOT_EXECUTABLE,
    COW_ATTEMPT_CATEGORY_EXECUTION_FAILED,
    COW_ATTEMPT_CATEGORY_EXECUTION_SUCCESS,
]
# Compatibility name retained for route imports; it now means the default,
# not the only network supported by the DEX workflow.
DEX_ARBITRAGE_SUPPORTED_NETWORK = DEFAULT_COW_NETWORK


def panel_call(name: str, *args, **kwargs):
    return ROUTE_CONTEXT.call(name, *args, **kwargs)


def _cow_submission_pause_guard(*, database_url: str | None = None) -> dict:
    try:
        return cow_submission_pause_guard_status(database_url=database_url)
    except TypeError:
        return cow_submission_pause_guard_status()


from web.data_routes.dex_pools import (
    DEX_POOL_STATIC_TABLE_PATH,
    _bnb_dex_pool_candidates_payload,
    _bnb_pool_candidate_row,
    _pool_address,
    _pool_candidate_row,
    _pool_quote_summary,
    _read_json_object,
    _reject_reason_counts,
    _string_number,
    dex_pool_candidates_payload,
)


def _set_cow_submission_pause_guard(*, paused: bool, reason: str | None = None, database_url: str | None = None) -> dict:
    try:
        return set_cow_submission_pause_guard(paused=paused, reason=reason, database_url=database_url)
    except TypeError:
        return set_cow_submission_pause_guard(paused=paused, reason=reason)


def _clear_cow_submission_pause_guard(*, database_url: str | None = None) -> dict:
    try:
        return clear_cow_submission_pause_guard(database_url=database_url)
    except TypeError:
        return clear_cow_submission_pause_guard()


def data_error_message(error: object | None) -> str | None:
    if error is None:
        return None
    message = str(error)
    if not message:
        message = repr(error)
    if message and message != repr(error):
        message = f"{type(error).__name__}: {message}"
    return redact_sensitive_text(message)


def _request_networks_arg() -> list[str]:
    raw = request.args.get("cow_networks", "") or request.args.get("cow_network", "")
    if not raw:
        return []
    try:
        return list(parse_cow_networks(raw))
    except ValueError:
        return []


def _request_cow_network_arg() -> str:
    selected = _request_networks_arg()
    return selected[0] if selected else DEFAULT_COW_NETWORK


def _optional_request_chain_selection(*, feature: str | None = None) -> dict | None:
    if not any(request.args.get(name, "").strip() for name in ("market_id", "chain_id", "feature")):
        return None
    return resolve_request_chain_selection(
        database_url=panel_call("database_url_or_none"),
        feature=feature,
    ).as_dict()


def _chain_selection_error_response(exc: ChainSelectionError):
    return jsonify({"ok": False, "error": exc.message, "code": exc.code}), 400


def _filter_network_claims(claims: list[dict], networks: list[str]) -> list[dict]:
    selected = {
        str(network or "").strip().lower()
        for network in networks
        if str(network or "").strip()
    }
    if not selected:
        return list(claims or [])
    return [
        claim
        for claim in claims or []
        if str(claim.get("network") or "").strip().lower() in selected
    ]


def record_cow_attempt_list_safely(
    attempts: list[dict],
    *,
    database_url: str | None,
) -> dict:
    if not attempts:
        return {"recorded": 0, "source": "empty", "error": None}
    pause_guard = _cow_submission_pause_guard(database_url=database_url)
    if pause_guard.get("paused"):
        return {
            "recorded": 0,
            "source": "paused",
            "error": None,
            "pause_guard": pause_guard,
        }
    if database_url:
        unavailable = database_unavailable_reason(database_url)
        if unavailable:
            file_count = append_cow_execution_attempts_jsonl(
                COW_EXECUTION_ATTEMPT_LOG_PATH,
                attempts,
                retention_days=COW_EXECUTION_RETENTION_DAYS,
            )
            return {"recorded": file_count, "source": "jsonl_fallback", "error": unavailable}
        try:
            ids = record_cow_execution_attempts(
                database_url,
                attempts,
                retention_days=COW_EXECUTION_RETENTION_DAYS,
            )
            return {"recorded": len(ids), "source": "database", "ids": ids, "error": None}
        except Exception as exc:
            if is_database_unavailable_error(exc):
                mark_database_unavailable(database_url, exc)
            file_count = append_cow_execution_attempts_jsonl(
                COW_EXECUTION_ATTEMPT_LOG_PATH,
                attempts,
                retention_days=COW_EXECUTION_RETENTION_DAYS,
            )
            return {"recorded": file_count, "source": "jsonl_fallback", "error": data_error_message(exc)}
    file_count = append_cow_execution_attempts_jsonl(
        COW_EXECUTION_ATTEMPT_LOG_PATH,
        attempts,
        retention_days=COW_EXECUTION_RETENTION_DAYS,
    )
    return {"recorded": file_count, "source": "jsonl", "error": None}


def record_cow_execution_attempts_safely(
    payload: dict,
    *,
    market_state: dict,
    database_url: str | None,
) -> dict:
    return record_cow_attempt_list_safely(
        build_cow_execution_attempts(payload, market_state=market_state),
        database_url=database_url,
    )


def submit_ready_cow_quote_orders(
    payload: dict,
    *,
    market_state: dict,
    submitter=None,
) -> dict:
    return submit_ready_quote_orders(
        payload,
        market_state=market_state,
        submitter=submitter or submit_cow_intent_trade,
    )


def _cow_execution_attempt_groups(
    loader,
    *,
    limit: int,
    networks: list[str],
) -> dict[str, dict]:
    groups = {}
    for category in COW_EXECUTION_REVIEW_CATEGORIES:
        rows = loader(limit=limit, networks=networks, category=category)
        rows.sort(key=lambda item: str(item.get("created_at") or item.get("observed_at") or ""), reverse=True)
        rows = rows[: max(1, int(limit))]
        groups[category] = {
            "attempts": rows,
            "count": len(rows),
            "retention_days": COW_ATTEMPT_CATEGORY_RETENTION_DAYS.get(category),
            "analysis_days": COW_ATTEMPT_CATEGORY_ANALYSIS_DAYS.get(category),
            "retention_policy": COW_ATTEMPT_CATEGORY_RETENTION_POLICY.get(category),
            "page_size": 10,
        }
    return groups


def _flatten_cow_execution_groups(groups: dict[str, dict], limit: int) -> list[dict]:
    rows = [
        row
        for group in groups.values()
        for row in group.get("attempts", [])
    ]
    rows.sort(key=lambda item: str(item.get("created_at") or item.get("observed_at") or ""), reverse=True)
    return rows[: max(1, int(limit))]


def record_cow_market_candidates_safely(
    market_states: list[dict],
    *,
    network_claims: list[dict] | None = None,
    observed_at: object = None,
    window_seconds: object = None,
    price_source: object = None,
    market_state_source: object = None,
    fallback_reason: object = None,
    database_url: str | None,
) -> dict:
    pause_guard = _cow_submission_pause_guard(database_url=database_url)
    if pause_guard.get("paused"):
        return {
            "recorded": 0,
            "source": "paused",
            "error": None,
            "pause_guard": pause_guard,
            "queue": {"accepted": 0, "skipped": "cow_automation_paused"},
            "daemon": cow_quote_daemon_status(),
        }
    attempts = [
        attempt
        for market_state in market_states
        for attempt in build_cow_market_candidate_attempts(market_state)
    ]
    attempts.extend(
        build_cow_market_claim_candidate_attempts(
            network_claims or [],
            observed_at=observed_at,
            window_seconds=window_seconds,
            price_source=price_source,
            market_state_source=market_state_source,
            fallback_reason=fallback_reason,
        )
    )
    recording = record_cow_attempt_list_safely(attempts, database_url=database_url)
    if recording.get("source") == "paused":
        return {
            **recording,
            "queue": {"accepted": 0, "skipped": "cow_automation_paused"},
            "daemon": cow_quote_daemon_status(),
        }
    queue_result = enqueue_cow_candidate_attempts(attempts, source="binance_market_candidates")
    daemon_result = cow_quote_daemon_status()
    if cow_quote_daemon_enabled() and attempts:
        daemon = ensure_cow_quote_daemon_running(database_url_provider=lambda: panel_call("database_url_or_none"))
        daemon_result = daemon.status()
    return {**recording, "queue": queue_result, "daemon": daemon_result}


def record_ui_signal_pool_evidence_safely(market_state: dict, *, extremes: dict | None = None) -> dict:
    try:
        return record_ui_signal_pool_evidence(market_state=market_state, extremes=extremes)
    except Exception as exc:
        return {"recorded": False, "reason": "error", "error": data_error_message(exc)}


def request_int_arg(name: str, default: int, *, minimum: int | None = None, maximum: int | None = None) -> int:
    raw = request.args.get(name, str(default))
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = int(default)
    if minimum is not None:
        value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value


def request_float_arg(name: str, default: float, *, minimum: float | None = None, maximum: float | None = None) -> float:
    raw = request.args.get(name, str(default))
    try:
        value = float(raw)
    except (TypeError, ValueError):
        value = float(default)
    if minimum is not None:
        value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value


def request_min_cow_spread_percent() -> float:
    return request_float_arg(
        "min_spread_percent",
        DEFAULT_MIN_COW_SPREAD_PERCENT,
        minimum=0.0,
        maximum=100.0,
    )


def request_cow_amount(default: str = "1000") -> str:
    return request.args.get("amount", default).strip() or default


def request_cow_trade_thresholds(amount: str | None = None) -> tuple[object, int, dict]:
    arbitrage_config = arbitrage_config_from_strategy()
    slippage_bps = read_slippage_bps()
    thresholds = cost_adjusted_cow_thresholds(
        requested_min_spread_percent=request_min_cow_spread_percent(),
        amount=amount or request_cow_amount(),
        arbitrage_config=arbitrage_config,
        slippage_bps=slippage_bps,
    )
    return arbitrage_config, slippage_bps, thresholds


def register_data_routes(app, panel) -> None:
    from web.data_routes.market_analysis import register_market_analysis_routes

    ROUTE_CONTEXT.bind(panel, globals())
    register_market_analysis_routes(app, panel)
