import logging
from pathlib import Path

from flask import jsonify, request

from core.chain_data_registry import chain_data_by_chain_id, chain_data_by_network
from core.db.storage_cow_execution import (
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
)
from dex.runtime.cow_arbitrage_daemon import (
    cow_quote_daemon_enabled,
    cow_quote_daemon_status,
    enqueue_cow_candidate_attempts,
    ensure_cow_quote_daemon_running,
)
from dex.runtime.ui_signal_pool_evidence import record_ui_signal_pool_evidence
from dex.runtime.cow_attempt_log import (
    COW_EXECUTION_ATTEMPT_LOG_PATH,
    COW_EXECUTION_ATTEMPT_LOG_ROOT,
    COW_EXECUTION_RETENTION_DAYS,
    cow_execution_attempt_log_path,
    record_cow_attempt_list_safely,
    record_cow_execution_attempts_safely,
)
from dex.intent_trade import submit_cow_intent_trade
from dex.runtime.cow_submission_pause import (
    clear_cow_submission_pause_guard,
    cow_submission_pause_guard_status,
    set_cow_submission_pause_guard,
)

logger = logging.getLogger(__name__)
from core.chain_selection import ChainSelectionError
from core.api_response_contract import error_response
from dex.cow_flashloan.network_scope import (
    DEFAULT_COW_NETWORK,
    parse_cow_networks,
    supported_cow_networks,
)
from market.binance_market.service import (
    DEFAULT_MIN_COW_SPREAD_PERCENT,
    cost_adjusted_cow_thresholds,
)
from market.binance_market.submission_service import submit_ready_quote_orders
from web.chain_selection import resolve_request_chain_selection
from core.route_context import RouteContext
from core.cow_evidence_helpers import (
    _attempt_evidence,
    _attempt_evidence_rank,
    _attempt_route_key,
    _attempt_time_key,
    _dedupe_attempts_by_route_evidence,
    _filter_network_claims,
    data_error_message,
)

ROUTE_CONTEXT = RouteContext()
COW_EXECUTION_REVIEW_CATEGORIES = [
    COW_ATTEMPT_CATEGORY_NOT_EXECUTABLE,
    COW_ATTEMPT_CATEGORY_EXECUTION_FAILED,
    COW_ATTEMPT_CATEGORY_EXECUTION_SUCCESS,
]
# Compatibility name retained for route imports; it now means the default,
# not the only network supported by the DEX workflow.
DEX_ARBITRAGE_SUPPORTED_NETWORK = DEFAULT_COW_NETWORK


def panel_call(name: str, *args, **kwargs):
    return ROUTE_CONTEXT.panel_call(name, *args, **kwargs)


def _cow_submission_pause_guard(*, database_url: str | None = None) -> dict:
    try:
        return cow_submission_pause_guard_status(database_url=database_url)
    except TypeError:
        return cow_submission_pause_guard_status()


from market.binance_market.pool_candidates import dex_pool_candidates_payload


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
    if selected:
        return selected[0]
    try:
        chain_selection = resolve_request_chain_selection(
            database_url=panel_call("database_url_or_none"),
            feature="dex_arbitrage",
        )
        network = str(getattr(chain_selection, "network", "") or "").strip().lower()
        if network and network in set(supported_cow_networks()):
            return network
    except ChainSelectionError:
        logger.warning("request chain selection failed; using default CoW network", exc_info=True)
        pass
    except Exception:
        logger.warning("unexpected request chain selection error; using default CoW network", exc_info=True)
        pass
    return DEFAULT_COW_NETWORK


def _optional_request_chain_selection(*, feature: str | None = None) -> dict | None:
    if not any(request.args.get(name, "").strip() for name in ("market_id", "chain_id", "feature")):
        if not feature:
            return None
        try:
            return resolve_request_chain_selection(
                database_url=panel_call("database_url_or_none"),
                feature=feature,
            ).as_dict()
        except ChainSelectionError:
            logger.warning("feature default chain selection failed for %s", feature, exc_info=True)
            return None
        except Exception:
            logger.warning("unexpected feature default chain selection error for %s", feature, exc_info=True)
            return None
    try:
        return resolve_request_chain_selection(
            database_url=panel_call("database_url_or_none"),
            feature=feature,
        ).as_dict()
    except ChainSelectionError:
        logger.warning("explicit request chain selection failed for %s", feature, exc_info=True)
        return None
    except Exception:
        logger.warning("unexpected explicit request chain selection error for %s", feature, exc_info=True)
        return None


def _chain_selection_error_response(exc: ChainSelectionError):
    return jsonify(error_response(exc.code, exc.message, status=400)), 400


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
    dedupe_by_route_evidence: bool = True,
    group_loader=None,
) -> dict[str, dict]:
    groups = {}
    if group_loader is not None:
        grouped_rows = group_loader(limit=limit, networks=networks)
        for category in COW_EXECUTION_REVIEW_CATEGORIES:
            rows = list(grouped_rows.get(category) or [])
            rows.sort(key=lambda item: str(item.get("created_at") or item.get("observed_at") or ""), reverse=True)
            rows = _dedupe_attempts_by_route_evidence(
                rows,
                limit=limit,
                dedupe_by_route_evidence=dedupe_by_route_evidence,
            )
            groups[category] = {
                "attempts": rows,
                "count": len(rows),
                "retention_days": COW_ATTEMPT_CATEGORY_RETENTION_DAYS.get(category),
                "analysis_days": COW_ATTEMPT_CATEGORY_ANALYSIS_DAYS.get(category),
                "retention_policy": COW_ATTEMPT_CATEGORY_RETENTION_POLICY.get(category),
                "page_size": 10,
            }
        return groups
    for category in COW_EXECUTION_REVIEW_CATEGORIES:
        rows = loader(limit=limit, networks=networks, category=category)
        rows.sort(key=lambda item: str(item.get("created_at") or item.get("observed_at") or ""), reverse=True)
        rows = _dedupe_attempts_by_route_evidence(
            rows,
            limit=limit,
            dedupe_by_route_evidence=dedupe_by_route_evidence,
        )
        groups[category] = {
            "attempts": rows,
            "count": len(rows),
            "retention_days": COW_ATTEMPT_CATEGORY_RETENTION_DAYS.get(category),
            "analysis_days": COW_ATTEMPT_CATEGORY_ANALYSIS_DAYS.get(category),
            "retention_policy": COW_ATTEMPT_CATEGORY_RETENTION_POLICY.get(category),
            "page_size": 10,
        }
    return groups


def _flatten_cow_execution_groups(
    groups: dict[str, dict],
    limit: int,
    *,
    dedupe_by_route_evidence: bool = True,
) -> list[dict]:
    rows = [
        row
        for group in groups.values()
        for row in group.get("attempts", [])
    ]
    return _dedupe_attempts_by_route_evidence(
        rows,
        limit=limit,
        dedupe_by_route_evidence=dedupe_by_route_evidence,
    )


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
        daemon = ensure_cow_quote_daemon_running(database_url_provider=lambda: None)
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
    from web.data_routes.metrics import register_metrics_routes

    ROUTE_CONTEXT.bind(panel, globals())
    ROUTE_CONTEXT.bind_panel_provider(panel)
    register_market_analysis_routes(app, panel)
    register_metrics_routes(app, panel)
