from pathlib import Path

from flask import jsonify, request

from core.chain_data_registry import chain_data_by_chain_id, chain_data_by_network
from core.sensitive_data import redact_sensitive_text
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
COW_EXECUTION_ATTEMPT_LOG_ROOT = SRC_ROOT / "runtime" / "logs" / "cow_execution_attempts"
COW_EXECUTION_RETENTION_DAYS = 7
COW_EXECUTION_REVIEW_CATEGORIES = [
    COW_ATTEMPT_CATEGORY_NOT_EXECUTABLE,
    COW_ATTEMPT_CATEGORY_EXECUTION_FAILED,
    COW_ATTEMPT_CATEGORY_EXECUTION_SUCCESS,
]
# Compatibility name retained for route imports; it now means the default,
# not the only network supported by the DEX workflow.
DEX_ARBITRAGE_SUPPORTED_NETWORK = DEFAULT_COW_NETWORK


def cow_execution_attempt_log_path(
    *,
    network: str | None = None,
    chain_id: int | None = None,
    market_state: dict | None = None,
    fallback: Path | None = None,
) -> Path:
    state = market_state if isinstance(market_state, dict) else {}
    state_filter = state.get("cow_filter") if isinstance(state.get("cow_filter"), dict) else {}
    resolved_network = str(
        network
        or state_filter.get("network")
        or state.get("network")
        or state.get("cow_network")
        or ""
    ).strip().lower()
    resolved_chain_id = chain_id
    if resolved_chain_id is None:
        for value in (
            state_filter.get("chain_id"),
            state.get("chain_id"),
            state.get("cow_chain_id"),
        ):
            if value is None or value == "":
                continue
            try:
                resolved_chain_id = int(value)
                break
            except (TypeError, ValueError):
                continue
    row = None
    if resolved_chain_id is not None:
        row = chain_data_by_chain_id(int(resolved_chain_id))
    elif resolved_network:
        row = chain_data_by_network(resolved_network)
    if row:
        resolved_network = str(row.get("network") or resolved_network or "").strip().lower()
        if resolved_chain_id is None:
            try:
                resolved_chain_id = int(row.get("chain_id"))
            except (TypeError, ValueError):
                resolved_chain_id = resolved_chain_id
    if not resolved_network and resolved_chain_id is None:
        if fallback is not None:
            return fallback
        return COW_EXECUTION_ATTEMPT_LOG_PATH
    if not resolved_network:
        row = chain_data_by_network(state.get("network") or state.get("cow_network"))
        if row:
            resolved_network = str(row.get("network") or "").strip().lower()
    scope_network = resolved_network or "unknown"
    scope_chain = str(resolved_chain_id if resolved_chain_id is not None else "no-chain-id")
    return COW_EXECUTION_ATTEMPT_LOG_ROOT / scope_network / scope_chain / "attempts.jsonl"


def panel_call(name: str, *args, **kwargs):
    return ROUTE_CONTEXT.call(name, *args, **kwargs)


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
    if selected:
        return selected[0]
    try:
        chain_selection = resolve_request_chain_selection(
            database_url=panel_call("database_url_or_none"),
            feature="dex_arbitrage",
        )
        network = str(getattr(chain_selection, "network", "") or "").strip().lower()
        if network:
            return network
    except ChainSelectionError:
        pass
    except Exception:
        pass
    return DEFAULT_COW_NETWORK


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


def _attempt_evidence(row: dict) -> dict:
    review = row.get("review_summary") if isinstance(row.get("review_summary"), dict) else {}
    review_evidence = review.get("onchain_evidence") if isinstance(review.get("onchain_evidence"), dict) else {}
    quote = row.get("quote") if isinstance(row.get("quote"), dict) else {}
    quote_evidence = quote.get("live_onchain_evidence") if isinstance(quote.get("live_onchain_evidence"), dict) else {}
    return review_evidence or quote_evidence or {}


def _attempt_evidence_rank(row: dict) -> int:
    evidence = _attempt_evidence(row)
    status = str(evidence.get("status") or row.get("priority_reason") or row.get("state") or "").strip().lower()
    if evidence.get("trustedForExecution") or evidence.get("trusted_for_execution"):
        return 50
    if evidence.get("readyForTransactionPreflight") or evidence.get("ready_for_transaction_preflight"):
        return 40
    if status == "live_verified":
        return 35
    if status in {"not_live_verified", "live_quote_failed"}:
        return 30
    if status == "live_evidence_deferred":
        return 10
    return 0


def _attempt_route_key(row: dict) -> tuple[str, str]:
    network = str(row.get("network") or row.get("cow_network") or "").strip().lower()
    pair = str(row.get("pair") or row.get("symbol_pair") or row.get("route_key") or "").strip()
    if not pair:
        route = row.get("route_path") if isinstance(row.get("route_path"), list) else []
        pair = " -> ".join(str(item).strip() for item in route if str(item).strip())
    return network, pair


def _attempt_time_key(row: dict) -> str:
    return str(row.get("created_at") or row.get("observed_at") or "")


def _dedupe_attempts_by_route_evidence(rows: list[dict], *, limit: int) -> list[dict]:
    best: dict[tuple[str, str], dict] = {}
    for row in rows:
        key = _attempt_route_key(row)
        if not key[1]:
            key = (key[0], f"__row_{len(best)}_{_attempt_time_key(row)}")
        current = best.get(key)
        candidate_score = (_attempt_evidence_rank(row), _attempt_time_key(row))
        current_score = (_attempt_evidence_rank(current), _attempt_time_key(current)) if current else (-1, "")
        if current is None or candidate_score > current_score:
            best[key] = row
    deduped = list(best.values())
    deduped.sort(key=lambda item: (_attempt_evidence_rank(item), _attempt_time_key(item)), reverse=True)
    return deduped[: max(1, int(limit))]


def record_cow_attempt_list_safely(
    attempts: list[dict],
    *,
    database_url: str | None,
    dedupe_market_candidates: bool = True,
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
    grouped_attempts: dict[Path, list[dict]] = {}
    for attempt in attempts:
        if not isinstance(attempt, dict):
            continue
        path = cow_execution_attempt_log_path(
            network=attempt.get("network") or attempt.get("cow_network"),
            chain_id=attempt.get("chain_id") or attempt.get("cow_chain_id"),
            market_state=attempt.get("market_state"),
        )
        grouped_attempts.setdefault(path, []).append(attempt)
    file_count = 0
    log_paths: list[str] = []
    for path, rows in grouped_attempts.items():
        file_count += append_cow_execution_attempts_jsonl(
            path,
            rows,
            retention_days=COW_EXECUTION_RETENTION_DAYS,
            dedupe_market_candidates=dedupe_market_candidates,
        )
        log_paths.append(str(path))
    return {"recorded": file_count, "source": "jsonl", "error": None, "log_paths": log_paths}


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
        rows = _dedupe_attempts_by_route_evidence(rows, limit=limit)
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
    return _dedupe_attempts_by_route_evidence(rows, limit=limit)


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

    ROUTE_CONTEXT.bind(panel, globals())
    register_market_analysis_routes(app, panel)
