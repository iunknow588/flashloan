from __future__ import annotations

from copy import deepcopy
import os
import threading
import time
from typing import Any, Callable

from core.sensitive_data import redact_sensitive_text
from db.storage_cow_execution import build_cow_execution_attempts
from intent_trade import bind_cow_intent_context, build_triangular_onchain_intent_trade, submit_cow_intent_trade
from runtime.cow_candidate_queue import CowCandidateQueue
from web.control_panel_cow_pause import cow_submission_pause_guard_status


DatabaseUrlProvider = Callable[[], str | None]
RecordAttempts = Callable[[list[dict[str, Any]], str | None], dict[str, Any]]
QuoteCandidate = Callable[[dict[str, Any], str | None], dict[str, Any]]

_QUEUE = CowCandidateQueue(
    max_size=int(os.getenv("COW_CANDIDATE_QUEUE_MAX_SIZE", "500") or "500"),
    ttl_seconds=float(os.getenv("COW_CANDIDATE_QUEUE_TTL_SECONDS", str(7 * 24 * 60 * 60)) or str(7 * 24 * 60 * 60)),
    requote_cooldown_seconds=float(os.getenv("COW_CANDIDATE_REQUOTE_COOLDOWN_SECONDS", "30") or "30"),
)
_DAEMON: CowQuoteDaemon | None = None
_DAEMON_LOCK = threading.Lock()


def cow_quote_daemon_enabled() -> bool:
    raw = os.getenv("COW_ARBITRAGE_DAEMON_ENABLED", "true").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def get_cow_candidate_queue() -> CowCandidateQueue:
    return _QUEUE


def enqueue_cow_candidate_attempts(attempts: list[dict[str, Any]], *, source: str = "") -> dict[str, Any]:
    pause_guard = cow_submission_pause_guard_status()
    if pause_guard.get("paused"):
        return {
            "accepted": 0,
            "enqueued": 0,
            "skipped": len(attempts or []),
            "source": source,
            "status": "paused",
            "pause_guard": pause_guard,
        }
    return _QUEUE.enqueue_many(attempts, source=source)


def retain_cow_candidate_networks(networks: list[str]) -> dict[str, Any]:
    return _QUEUE.retain_networks(networks)


def clear_cow_candidate_queue(*, reason: str = "submission_switch_enabled_clear_stale") -> dict[str, Any]:
    return _QUEUE.clear_with_result(reason=reason)


def _env_float(name: str, default: float, *, minimum: float | None = None, maximum: float | None = None) -> float:
    try:
        value = float(os.getenv(name, str(default)) or default)
    except (TypeError, ValueError):
        value = float(default)
    if minimum is not None:
        value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value


def _env_int(name: str, default: int, *, minimum: int | None = None, maximum: int | None = None) -> int:
    try:
        value = int(os.getenv(name, str(default)) or default)
    except (TypeError, ValueError):
        value = int(default)
    if minimum is not None:
        value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value


def _blocked_submission_state(precheck: dict[str, Any]) -> str:
    status = str(precheck.get("status") or "").strip()
    explicit_states = {
        "submission_paused",
        "order_submission_switch_off",
        "order_submission_adapter_unavailable",
        "cow_flashloan_sdk_install_required",
        "order_submission_network_unsupported",
        "order_submission_signer_not_ready",
    }
    if status in explicit_states:
        return status
    reasons = {str(item or "").strip() for item in precheck.get("reasons") or []}
    for reason in (
        "cow_submission_paused",
        "order_submission_switch_off",
        "order_submission_adapter_unavailable",
        "cow_flashloan_sdk_install_required",
        "order_submission_network_unsupported",
        "order_submission_signer_not_ready",
    ):
        if reason in reasons:
            return "submission_paused" if reason == "cow_submission_paused" else reason
    return "ready_not_submitted"


def default_record_attempts(attempts: list[dict[str, Any]], database_url: str | None) -> dict[str, Any]:
    from db.storage_cow_execution import append_cow_execution_attempts_jsonl, record_cow_execution_attempts
    from db.storage_common import database_unavailable_reason, is_database_unavailable_error, mark_database_unavailable
    from web.control_panel_data_routes import COW_EXECUTION_ATTEMPT_LOG_PATH, COW_EXECUTION_RETENTION_DAYS, data_error_message

    if not attempts:
        return {"recorded": 0, "source": "empty", "error": None}
    pause_guard = cow_submission_pause_guard_status()
    if pause_guard.get("paused"):
        return {"recorded": 0, "source": "paused", "error": None, "pause_guard": pause_guard}
    if database_url:
        unavailable = database_unavailable_reason(database_url)
        if unavailable:
            count = append_cow_execution_attempts_jsonl(
                COW_EXECUTION_ATTEMPT_LOG_PATH,
                attempts,
                retention_days=COW_EXECUTION_RETENTION_DAYS,
                dedupe_market_candidates=False,
            )
            return {"recorded": count, "source": "jsonl_fallback", "error": unavailable}
        try:
            ids = record_cow_execution_attempts(
                database_url,
                attempts,
                retention_days=COW_EXECUTION_RETENTION_DAYS,
                dedupe_market_candidates=False,
            )
            return {"recorded": len(ids), "source": "database", "ids": ids, "error": None}
        except Exception as exc:
            if is_database_unavailable_error(exc):
                mark_database_unavailable(database_url, exc)
            count = append_cow_execution_attempts_jsonl(
                COW_EXECUTION_ATTEMPT_LOG_PATH,
                attempts,
                retention_days=COW_EXECUTION_RETENTION_DAYS,
                dedupe_market_candidates=False,
            )
            return {"recorded": count, "source": "jsonl_fallback", "error": data_error_message(exc)}
    count = append_cow_execution_attempts_jsonl(
        COW_EXECUTION_ATTEMPT_LOG_PATH,
        attempts,
        retention_days=COW_EXECUTION_RETENTION_DAYS,
        dedupe_market_candidates=False,
    )
    return {"recorded": count, "source": "jsonl", "error": None}


def default_quote_candidate(candidate: dict[str, Any], database_url: str | None) -> dict[str, Any]:
    from cow_flashloan.routes import cow_account_config, cow_network_config, evaluate_cow_route, rank_cow_routes
    from market.binance_market.service import (
        _apply_cow_quote_analysis,
        _attach_cow_flashloan_sdk_plan,
        _cow_sdk_result_snapshot,
        _binance_execution_plan,
        _cow_cost_summary,
        _cow_execution_precheck,
        _cow_route_support,
        load_cow_supported_token_registry,
    )

    attempt = candidate.get("candidate") if isinstance(candidate.get("candidate"), dict) else candidate
    quote = attempt.get("quote") if isinstance(attempt.get("quote"), dict) else {}
    route_detail = quote.get("route") if isinstance(quote.get("route"), dict) else {}
    network_config = cow_network_config(network=attempt.get("network"))
    account_config = cow_account_config(network_config.network)
    token_cache = load_cow_supported_token_registry(
        cow_network=network_config.network,
        database_url=database_url,
        allow_live_fallback=True,
    )
    amount = "1000"
    spec = {
        "name": f"queued_{attempt.get('pair_rank') or 0}_{attempt.get('priority_reason') or 'route'}",
        "path": attempt.get("route_path") or quote.get("path") or [],
        "amount": amount,
        "pair": attempt.get("pair"),
        "pair_rank": attempt.get("pair_rank"),
        "priority_reason": attempt.get("priority_reason"),
        "edge_hint_percent": quote.get("edge_hint_percent"),
    }
    plan = route_detail.get("binance_execution_plan")
    if not isinstance(plan, dict):
        x_row = {
            "base_symbol": quote.get("x_base_symbol"),
            "start_price": quote.get("x_start_price"),
            "current_price": quote.get("x_current_price"),
        }
        y_row = {
            "base_symbol": quote.get("y_base_symbol"),
            "start_price": quote.get("y_start_price"),
            "current_price": quote.get("y_current_price"),
        }
        plan = _binance_execution_plan(spec["path"], x_row, y_row, amount)
    if isinstance(plan, dict):
        spec["binance_execution_plan"] = plan
    support = _cow_route_support(spec, token_cache["registry"], cow_network=network_config.network)
    if support.get("supported"):
        result = evaluate_cow_route(
            spec,
            registry=token_cache["registry"],
            default_amount=amount,
            owner=account_config.owner,
            cow_network=network_config.network,
            quote_timeout_seconds=_env_float("COW_ARBITRAGE_QUOTE_TIMEOUT_SECONDS", 8.0, minimum=1.0, maximum=30.0),
        )
    else:
        path = spec.get("path") or []
        result = {
            "name": spec.get("name"),
            "path": path,
            "input_amount": str(amount),
            "input_symbol": path[0] if path else None,
            "final_symbol": path[-1] if path else None,
            "viable": False,
            "error": support.get("error") or "CoW token support precheck failed",
            "cow_sdk_result": {
                "status": "not_called",
                "reason": support.get("error") or "CoW token support precheck failed",
                "controller": "cow_sdk",
            },
            "hops": [],
        }
    result["pair"] = spec["pair"]
    result["pair_rank"] = spec["pair_rank"]
    result["priority_reason"] = spec["priority_reason"]
    result["edge_hint_percent"] = spec["edge_hint_percent"]
    result["cow_network"] = network_config.network
    result["cow_chain_id"] = network_config.chain_id
    for timing_key in (
        "signal_timing",
        "quote_trigger",
        "binance_window",
        "x_start_ms",
        "x_end_ms",
        "y_start_ms",
        "y_end_ms",
    ):
        if quote.get(timing_key) is not None:
            result[timing_key] = quote.get(timing_key)
    result["cow_support"] = support
    result["queue_signature"] = candidate.get("signature")
    final_amount = result.get("final_amount")
    input_amount = result.get("input_amount")
    result["final_delta_amount"] = None
    result["profit_prediction_disabled"] = True
    result["profit_prediction_mode"] = "disabled_intent_only_sdk_settlement"
    market_state = attempt.get("market_state") if isinstance(attempt.get("market_state"), dict) else {}
    cow_filter = market_state.get("cow_filter") if isinstance(market_state.get("cow_filter"), dict) else {}
    route_path = result.get("path") or spec.get("path") or []
    priority_reason = result.get("priority_reason") or spec.get("priority_reason") or ""
    if str(priority_reason).strip().lower() == "reverse_check":
        rising_tokens = route_path[1:2]
        falling_tokens = route_path[2:3]
    else:
        rising_tokens = route_path[2:3]
        falling_tokens = route_path[1:2]
    threshold_detail = cow_filter.get("threshold_detail") if isinstance(cow_filter, dict) else {}
    expected_profit = threshold_detail.get("min_pure_profit_amount") if isinstance(threshold_detail, dict) else None
    if expected_profit is None and isinstance(threshold_detail, dict):
        expected_profit = threshold_detail.get("min_profit_usd")
    result["cow_flashloan_intent"] = bind_cow_intent_context(
        build_triangular_onchain_intent_trade(
            result.get("priority_reason") or spec.get("priority_reason") or result.get("name") or spec.get("name"),
            expected_profit,
            rising_tokens,
            falling_tokens,
        ),
        requested_amount=result.get("input_amount") or amount,
        input_symbol=result.get("input_symbol"),
        final_symbol=result.get("final_symbol"),
        owner=account_config.owner,
        cow_network=network_config.network,
        cow_chain_id=network_config.chain_id,
    )
    result["binance_execution_plan"] = _apply_cow_quote_analysis(spec.get("binance_execution_plan"), result)
    _attach_cow_flashloan_sdk_plan(result, result.get("binance_execution_plan"), token_cache["registry"])
    result["execution_precheck"] = _cow_execution_precheck(result)
    pause_guard = cow_submission_pause_guard_status()
    if (result.get("execution_precheck") or {}).get("can_submit_order") and pause_guard.get("paused"):
        precheck = dict(result.get("execution_precheck") or {})
        existing_reasons = list(precheck.get("reasons") or [])
        if "cow_submission_paused" not in existing_reasons:
            existing_reasons.append("cow_submission_paused")
        precheck.update(
            {
                "status": "submission_paused",
                "checks_passed": True,
                "can_submit_order": False,
                "auto_execute_requested": True,
                "auto_execute_blocked": True,
                "submission_attempted": False,
                "submission_status": "submission_paused",
                "submission_pause_guard": pause_guard,
                "reasons": existing_reasons,
            }
        )
        result["execution_precheck"] = precheck
        result["cow_submission_result"] = {
            "ok": False,
            "submitted": False,
            "status": "submission_paused",
            "blocked_reason": "cow_submission_paused",
            "error": pause_guard.get("pause_reason"),
            "pause_guard": pause_guard,
        }
    elif (result.get("execution_precheck") or {}).get("can_submit_order"):
        submission = submit_cow_intent_trade(
            quote_payload=result,
            opportunity={
                "attempt": attempt,
                "market_state": market_state,
                "queue_signature": candidate.get("signature"),
            },
        )
        result["cow_submission_result"] = submission
        sdk_plan = result.get("cow_flashloan_sdk_plan") if isinstance(result.get("cow_flashloan_sdk_plan"), dict) else {}
        if sdk_plan:
            sdk_plan["submission_status"] = submission.get("status")
            sdk_plan["order_id"] = submission.get("order_id")
            sdk_plan["tx_hash"] = submission.get("tx_hash")
        precheck = dict(result.get("execution_precheck") or {})
        existing_reasons = list(precheck.get("reasons") or [])
        precheck["execution_phase"] = "order_submission"
        precheck["submission_attempted"] = True
        precheck["submission_status"] = submission.get("status")
        precheck["submission_order_id"] = submission.get("order_id")
        precheck["submission_tx_hash"] = submission.get("tx_hash")
        if submission.get("submitted"):
            precheck["status"] = "submitted_success"
            precheck["reasons"] = [*existing_reasons, "cow_order_submitted_successfully"]
        else:
            precheck["status"] = "submission_failed"
            error = submission.get("error") or submission.get("blocked_reason") or "cow_order_submission_failed"
            precheck["reasons"] = [*existing_reasons, str(error)]
            result["error"] = str(error)
        result["execution_precheck"] = precheck
    result["cow_sdk_result"] = _cow_sdk_result_snapshot(result, result["execution_precheck"])
    result["costs"] = _cow_cost_summary(result, final_delta_amount=None)
    result["quote_verified"] = True
    payload = {
        "observed_at": attempt.get("observed_at"),
        "amount": str(amount),
        "owner": account_config.owner,
        "owner_source": account_config.owner_source,
        "cow_network": network_config.network,
        "cow_chain_id": network_config.chain_id,
        "cow_testnet": network_config.testnet,
        "price_quality": "fast",
        "valid_for": 60,
        "selected_pair_count": 1,
        "route_count": 1,
        "supported_route_count": 1 if support.get("supported") else 0,
        "unsupported_route_count": 0 if support.get("supported") else 1,
        "viable_count": 1 if result.get("viable") else 0,
        "opportunity_count": 1 if (result.get("execution_precheck") or {}).get("checks_passed") else 0,
        "ranking": rank_cow_routes([result]),
    }
    attempts = build_cow_execution_attempts(payload, market_state=market_state)
    return {"payload": payload, "attempts": attempts, "result": result}


def _failed_attempt_from_candidate(candidate: dict[str, Any], message: str) -> dict[str, Any]:
    attempt = candidate.get("candidate") if isinstance(candidate.get("candidate"), dict) else candidate
    failed = deepcopy(attempt) if isinstance(attempt, dict) else {}
    quote = failed.get("quote") if isinstance(failed.get("quote"), dict) else {}
    precheck = failed.get("precheck") if isinstance(failed.get("precheck"), dict) else {}
    route_path = failed.get("route_path") or quote.get("path") or []
    error = str(message or "cow quote failed")
    blocked_reasons = failed.get("blocked_reasons")
    if isinstance(blocked_reasons, list):
        reasons = [str(item) for item in blocked_reasons if item]
    elif blocked_reasons:
        reasons = [str(blocked_reasons)]
    else:
        reasons = []
    quote.update(
        {
            "quote_verified": False,
            "viable": False,
            "error": error,
            "cow_sdk_result": {
                "status": "quote_failed",
                "error": error,
                "controller": "cow_sdk",
            },
        }
    )
    precheck.update(
        {
            "status": "quote_failed",
            "checks_passed": False,
            "can_submit_order": False,
            "order_submission_enabled": False,
            "auto_execute_requested": False,
            "reasons": [error],
        }
    )
    failed.update(
        {
            "state": "quote_failed",
            "execution_phase": "quote_precheck",
            "route_path": route_path,
            "quote": quote,
            "precheck": precheck,
            "error": error,
            "blocked_reasons": list(dict.fromkeys([*reasons, error])),
        }
    )
    return failed


class CowQuoteDaemon:
    def __init__(
        self,
        queue: CowCandidateQueue,
        *,
        database_url_provider: DatabaseUrlProvider | None = None,
        record_attempts: RecordAttempts | None = None,
        quote_candidate: QuoteCandidate | None = None,
        poll_interval_seconds: float = 2.0,
        sort_key: str = "fifo",
        max_attempts: int = 3,
        retry_delay_seconds: float = 10.0,
    ) -> None:
        self.queue = queue
        self.database_url_provider = database_url_provider or (lambda: None)
        self.record_attempts = record_attempts or default_record_attempts
        self.quote_candidate = quote_candidate or default_quote_candidate
        self.poll_interval_seconds = max(0.2, float(poll_interval_seconds))
        self.sort_key = sort_key
        self.max_attempts = max(1, int(max_attempts))
        self.retry_delay_seconds = max(0.0, float(retry_delay_seconds))
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._last_error: str | None = None
        self._processed = 0
        self._last_activity_at: float | None = None

    def start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(target=self.run_forever, name="cow-quote-daemon", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def status(self) -> dict[str, Any]:
        thread = self._thread
        pause_guard = cow_submission_pause_guard_status()
        return {
            "enabled": cow_quote_daemon_enabled(),
            "running": bool(thread and thread.is_alive()),
            "paused": bool(pause_guard.get("paused")),
            "pause_guard": pause_guard,
            "sort_key": self.sort_key,
            "max_attempts": self.max_attempts,
            "retry_delay_seconds": self.retry_delay_seconds,
            "poll_interval_seconds": self.poll_interval_seconds,
            "processed": self._processed,
            "last_error": self._last_error,
            "last_activity_at": self._last_activity_at,
            "queue": self.queue.stats(),
        }

    def run_forever(self) -> None:
        while not self._stop.is_set():
            processed = self.process_once()
            if not processed:
                time.sleep(self.poll_interval_seconds)

    def process_once(self) -> bool:
        pause_guard = cow_submission_pause_guard_status()
        if pause_guard.get("paused"):
            self._last_error = "cow_automation_paused"
            return False
        candidate = self.queue.claim_next(sort_key=self.sort_key)
        if not candidate:
            return False
        signature = str(candidate.get("signature") or "")
        database_url = self.database_url_provider()
        try:
            result = self.quote_candidate(candidate, database_url)
            attempts = result.get("attempts") if isinstance(result, dict) else []
            recording = self.record_attempts(attempts or [], database_url)
            state = "quoted"
            quote_result = result.get("result") if isinstance(result, dict) else {}
            submission = quote_result.get("cow_submission_result") if isinstance(quote_result, dict) else {}
            ranking = ((result.get("payload") or {}).get("ranking") if isinstance(result, dict) else []) or []
            if isinstance(submission, dict) and submission:
                if submission.get("submitted") or str(submission.get("status") or "") == "submitted_success" or submission.get("order_id"):
                    state = "submitted_success"
                elif str(submission.get("status") or "") in {"submission_paused", "adapter_unavailable", "order_submission_disabled"}:
                    state = str(submission.get("status") or "ready_not_submitted")
                else:
                    state = "submission_failed"
            if ranking:
                precheck = ranking[0].get("execution_precheck") if isinstance(ranking[0], dict) else {}
                if state in {
                    "submitted_success",
                    "submission_failed",
                    "ready_not_submitted",
                    "submission_paused",
                    "adapter_unavailable",
                    "order_submission_disabled",
                }:
                    pass
                elif precheck.get("can_submit_order"):
                    state = "ready_to_submit"
                elif precheck.get("checks_passed"):
                    state = _blocked_submission_state(precheck)
                elif not precheck.get("checks_passed"):
                    state = "blocked"
            self.queue.complete(signature, status=state, result={"quote": result, "recording": recording})
            self._processed += 1
            self._last_error = None
            self._last_activity_at = time.time()
            return True
        except Exception as exc:
            message = redact_sensitive_text(exc)
            failed_attempt = _failed_attempt_from_candidate(candidate, message)
            try:
                recording = self.record_attempts([failed_attempt], database_url)
            except Exception as record_exc:
                recording = {"recorded": 0, "source": "record_failed", "error": redact_sensitive_text(record_exc)}
            attempts = int(candidate.get("attempts") or 0)
            if attempts < self.max_attempts:
                self.queue.requeue(signature, error=message, delay_seconds=self.retry_delay_seconds)
            else:
                self.queue.complete(signature, status="failed", error=message, result={"recording": recording, "quote": failed_attempt})
            self._last_error = message
            self._last_activity_at = time.time()
            return True


def ensure_cow_quote_daemon_running(
    *,
    database_url_provider: DatabaseUrlProvider | None = None,
    record_attempts: RecordAttempts | None = None,
    quote_candidate: QuoteCandidate | None = None,
) -> CowQuoteDaemon:
    global _DAEMON
    with _DAEMON_LOCK:
        if _DAEMON is None:
            _DAEMON = CowQuoteDaemon(
                _QUEUE,
                database_url_provider=database_url_provider,
                record_attempts=record_attempts,
                quote_candidate=quote_candidate,
                poll_interval_seconds=_env_float("COW_ARBITRAGE_DAEMON_POLL_SECONDS", 2.0, minimum=0.2, maximum=60.0),
                sort_key=os.getenv("COW_ARBITRAGE_QUEUE_SORT_KEY", "fifo").strip() or "fifo",
                max_attempts=_env_int("COW_ARBITRAGE_QUOTE_MAX_ATTEMPTS", 3, minimum=1, maximum=10),
                retry_delay_seconds=_env_float("COW_ARBITRAGE_QUOTE_RETRY_DELAY_SECONDS", 10.0, minimum=0.0, maximum=300.0),
            )
        _DAEMON.start()
        return _DAEMON


def cow_quote_daemon_status() -> dict[str, Any]:
    pause_guard = cow_submission_pause_guard_status()
    with _DAEMON_LOCK:
        daemon = _DAEMON
    if daemon is None:
        return {
            "enabled": cow_quote_daemon_enabled(),
            "running": False,
            "paused": bool(pause_guard.get("paused")),
            "pause_guard": pause_guard,
            "queue": _QUEUE.stats(),
            "processed": 0,
            "last_error": None,
        }
    return daemon.status()


def cow_candidate_queue_snapshot(*, limit: int = 50, networks: list[str] | None = None) -> dict[str, Any]:
    return {"daemon": cow_quote_daemon_status(), "items": _QUEUE.snapshot(limit=limit, networks=networks)}
