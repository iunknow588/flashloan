from __future__ import annotations

import os
import threading
import time
from typing import Any, Callable

from core.sensitive_data import redact_sensitive_text
from db.storage_cow_execution import build_cow_execution_attempts
from runtime.cow_candidate_queue import CowCandidateQueue


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
    return _QUEUE.enqueue_many(attempts, source=source)


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


def default_record_attempts(attempts: list[dict[str, Any]], database_url: str | None) -> dict[str, Any]:
    from db.storage_cow_execution import append_cow_execution_attempts_jsonl, record_cow_execution_attempts
    from web.control_panel_data_routes import COW_EXECUTION_ATTEMPT_LOG_PATH, COW_EXECUTION_RETENTION_DAYS, data_error_message

    if not attempts:
        return {"recorded": 0, "source": "empty", "error": None}
    if database_url:
        try:
            ids = record_cow_execution_attempts(
                database_url,
                attempts,
                retention_days=COW_EXECUTION_RETENTION_DAYS,
                dedupe_market_candidates=False,
            )
            return {"recorded": len(ids), "source": "database", "ids": ids, "error": None}
        except Exception as exc:
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
    from execution.cow_routes import cow_account_config, cow_network_config, evaluate_cow_route, rank_cow_routes
    from web.binance_market_service import (
        _apply_cow_quote_analysis,
        _attach_cow_flashloan_sdk_plan,
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
    amount = quote.get("input_amount") or route_detail.get("initial_amount") or os.getenv("COW_ARBITRAGE_DEFAULT_AMOUNT", "1000")
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
            "hops": [],
        }
    result["pair"] = spec["pair"]
    result["pair_rank"] = spec["pair_rank"]
    result["priority_reason"] = spec["priority_reason"]
    result["edge_hint_percent"] = spec["edge_hint_percent"]
    result["cow_support"] = support
    result["queue_signature"] = candidate.get("signature")
    final_amount = result.get("final_amount")
    input_amount = result.get("input_amount")
    try:
        if final_amount is not None and input_amount is not None:
            from decimal import Decimal

            result["final_delta_amount"] = str(Decimal(str(final_amount)) - Decimal(str(input_amount)))
    except Exception:
            result["final_delta_amount"] = result.get("final_delta_amount")
    result["binance_execution_plan"] = _apply_cow_quote_analysis(spec.get("binance_execution_plan"), result)
    _attach_cow_flashloan_sdk_plan(result, result.get("binance_execution_plan"), token_cache["registry"])
    result["execution_precheck"] = _cow_execution_precheck(result)
    result["costs"] = _cow_cost_summary(result, final_delta_amount=result.get("final_delta_amount"))
    result["quote_verified"] = True
    market_state = attempt.get("market_state") if isinstance(attempt.get("market_state"), dict) else {}
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
        return {
            "enabled": cow_quote_daemon_enabled(),
            "running": bool(thread and thread.is_alive()),
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
            ranking = ((result.get("payload") or {}).get("ranking") if isinstance(result, dict) else []) or []
            if ranking:
                precheck = ranking[0].get("execution_precheck") if isinstance(ranking[0], dict) else {}
                if precheck.get("can_submit_order"):
                    state = "ready_to_submit"
                elif precheck.get("checks_passed"):
                    state = "ready_not_submitted"
                elif not precheck.get("checks_passed"):
                    state = "blocked"
            self.queue.complete(signature, status=state, result={"quote": result, "recording": recording})
            self._processed += 1
            self._last_error = None
            self._last_activity_at = time.time()
            return True
        except Exception as exc:
            message = redact_sensitive_text(exc)
            attempts = int(candidate.get("attempts") or 0)
            if attempts < self.max_attempts:
                self.queue.requeue(signature, error=message, delay_seconds=self.retry_delay_seconds)
            else:
                self.queue.complete(signature, status="failed", error=message)
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
    with _DAEMON_LOCK:
        daemon = _DAEMON
    if daemon is None:
        return {
            "enabled": cow_quote_daemon_enabled(),
            "running": False,
            "queue": _QUEUE.stats(),
            "processed": 0,
            "last_error": None,
        }
    return daemon.status()


def cow_candidate_queue_snapshot(*, limit: int = 50) -> dict[str, Any]:
    return {"daemon": cow_quote_daemon_status(), "items": _QUEUE.snapshot(limit=limit)}
