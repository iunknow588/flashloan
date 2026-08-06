"""Trigger quote-only CoW analysis immediately when a Binance signal appears."""

from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path
import threading
import time
from typing import Any

from db.storage_cow_execution import (
    append_cow_execution_attempts_jsonl,
    build_cow_market_candidate_attempts,
    record_cow_execution_attempts,
)
from db.storage_common import database_unavailable_reason, is_database_unavailable_error, mark_database_unavailable
from runtime.cow_arbitrage_daemon import default_quote_candidate
from runtime.cow_candidate_queue import candidate_signature
from web.control_panel_cow_pause import cow_submission_pause_guard_status


SRC_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ATTEMPT_LOG_PATH = SRC_ROOT / "runtime" / "logs" / "cow_execution_attempts.jsonl"

_LOCK = threading.Lock()
_IN_FLIGHT = 0
_LAST_STARTED_BY_ROUTE: dict[str, float] = {}


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float, minimum: float = 0.0) -> float:
    try:
        return max(minimum, float(os.getenv(name, str(default)) or default))
    except (TypeError, ValueError):
        return float(default)


def _env_int(name: str, default: int, minimum: int = 1) -> int:
    try:
        return max(minimum, int(os.getenv(name, str(default)) or default))
    except (TypeError, ValueError):
        return int(default)


def realtime_cow_quote_enabled() -> bool:
    return _env_bool("COW_REALTIME_QUOTE_ENABLED", True)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _epoch_ms(value: Any) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        return number * 1000 if abs(number) < 100_000_000_000 else number
    text = str(value).strip()
    try:
        number = float(text)
        return number * 1000 if abs(number) < 100_000_000_000 else number
    except (TypeError, ValueError):
        pass
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp() * 1000
    except (TypeError, ValueError):
        return None


def _current_ms() -> float:
    return time.time() * 1000.0


def _max_age_seconds() -> float:
    return _env_float("COW_REALTIME_QUOTE_MAX_AGE_SECONDS", 0.2, minimum=0.0)


def _age_ms(value: Any) -> float | None:
    event_ms = _epoch_ms(value)
    if event_ms is None:
        return None
    return max(0.0, _current_ms() - event_ms)


def _freshness_status(*, extremes: dict[str, Any], simulation: dict[str, Any]) -> dict[str, Any]:
    max_age_ms = max(0.0, _max_age_seconds() * 1000.0)
    extremes_age_ms = _age_ms(extremes.get("observed_at"))
    signal_age_ms = _age_ms((simulation.get("signal_timing") or {}).get("signal_detected_at"))
    if extremes_age_ms is not None and extremes_age_ms > max_age_ms:
        return {
            "fresh": False,
            "reason": "extremes_stale",
            "age_ms": round(extremes_age_ms, 3),
            "max_age_ms": round(max_age_ms, 3),
        }
    if signal_age_ms is not None and signal_age_ms > max_age_ms:
        return {
            "fresh": False,
            "reason": "signal_stale",
            "age_ms": round(signal_age_ms, 3),
            "max_age_ms": round(max_age_ms, 3),
        }
    return {
        "fresh": True,
        "reason": "fresh",
        "age_ms": round(extremes_age_ms if extremes_age_ms is not None else signal_age_ms or 0.0, 3),
        "max_age_ms": round(max_age_ms, 3),
    }


def _window_end_ms(quote: dict[str, Any]) -> float | None:
    event_ends = [
        _epoch_ms(quote.get("x_end_ms")),
        _epoch_ms(quote.get("y_end_ms")),
    ]
    event_ends = [item for item in event_ends if item is not None]
    if event_ends:
        return max(event_ends)
    timing = quote.get("signal_timing") if isinstance(quote.get("signal_timing"), dict) else {}
    return _epoch_ms(timing.get("binance_observed_at"))


def _timing_snapshot(
    quote: dict[str, Any],
    *,
    started_at: str,
    finished_at: str | None = None,
    started_perf: float | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    timing = quote.get("signal_timing") if isinstance(quote.get("signal_timing"), dict) else {}
    started_ms = _epoch_ms(started_at)
    window_end = _window_end_ms(quote)
    signal_at = timing.get("signal_detected_at")
    signal_ms = _epoch_ms(signal_at)
    result = {
        "mode": "immediate_same_observer_cycle",
        "signal_detected_at": signal_at,
        "quote_started_at": started_at,
        "quote_finished_at": finished_at,
        "quote_latency_ms": (
            round((time.perf_counter() - started_perf) * 1000, 3)
            if started_perf is not None
            else None
        ),
        "binance_window_end_ms": round(window_end, 3) if window_end is not None else None,
        "binance_window_age_at_quote_start_ms": (
            round(started_ms - window_end, 3)
            if started_ms is not None and window_end is not None
            else None
        ),
        "signal_to_quote_start_ms": (
            round(started_ms - signal_ms, 3)
            if started_ms is not None and signal_ms is not None
            else None
        ),
    }
    if error:
        result["error"] = error[:240]
    return result


def _base_symbol(value: Any) -> str:
    return str(value or "").strip().upper().removesuffix("USDT")


def _find_row(extremes: dict[str, Any], symbol: str) -> dict[str, Any] | None:
    wanted = str(symbol or "").strip().upper()
    for row in [*(extremes.get("top") or []), *(extremes.get("bottom") or []), *(extremes.get("basket") or [])]:
        if str(row.get("symbol") or "").strip().upper() == wanted:
            return dict(row)
    return None


def _build_market_state(
    extremes: dict[str, Any],
    simulation: dict[str, Any],
    *,
    network: str,
    chain_id: int,
    amount: str,
) -> dict[str, Any] | None:
    x_symbol = str(simulation.get("x_symbol") or simulation.get("a_symbol") or "").upper()
    y_symbol = str(simulation.get("y_symbol") or simulation.get("b_symbol") or "").upper()
    x_row = _find_row(extremes, x_symbol)
    y_row = _find_row(extremes, y_symbol)
    if not x_row or not y_row or _base_symbol(x_symbol) == _base_symbol(y_symbol):
        return None

    x_base = _base_symbol(x_row.get("base_symbol") or x_row.get("symbol") or x_symbol)
    y_base = _base_symbol(y_row.get("base_symbol") or y_row.get("symbol") or y_symbol)
    observed_at = extremes.get("observed_at") or _now_iso()
    forward_route = ["USDC", y_base, x_base, "USDC"]
    reverse_route = ["USDC", x_base, y_base, "USDC"]
    pair = {
        "rank": 1,
        "pair": f"{x_symbol} / {y_symbol}",
        "x_symbol": x_symbol,
        "y_symbol": y_symbol,
        "x_base_symbol": x_base,
        "y_base_symbol": y_base,
        "x_change_percent": x_row.get("change_percent"),
        "y_change_percent": y_row.get("change_percent"),
        "x_start_price": x_row.get("start_price"),
        "x_current_price": x_row.get("current_price", x_row.get("end_price")),
        "x_end_price": x_row.get("end_price", x_row.get("current_price")),
        "x_start_ms": x_row.get("start_ms"),
        "x_end_ms": x_row.get("end_ms"),
        "x_price_source": x_row.get("price_source"),
        "y_start_price": y_row.get("start_price"),
        "y_current_price": y_row.get("current_price", y_row.get("end_price")),
        "y_end_price": y_row.get("end_price", y_row.get("current_price")),
        "y_start_ms": y_row.get("start_ms"),
        "y_end_ms": y_row.get("end_ms"),
        "y_price_source": y_row.get("price_source"),
        "window_spread_percent": simulation.get("window_spread_percent"),
        "candidate_basis": "binance_realtime_signal",
        "trigger_source": "observer_runtime_same_cycle",
        "quote_required": True,
        "route_results": [
            {
                "route_no": 1,
                "route": forward_route,
                "initial_amount": amount,
                "initial_symbol": "USDC",
                "route_direction": "forward_buy_loser_then_gainer",
                "priority_reason": "buy_loser_then_gainer_realtime",
                "quote_required": True,
            },
            {
                "route_no": 2,
                "route": reverse_route,
                "initial_amount": amount,
                "initial_symbol": "USDC",
                "route_direction": "reverse_buy_gainer_then_loser",
                "priority_reason": "reverse_check_realtime",
                "quote_required": True,
            },
        ],
    }
    return {
        "observed_at": observed_at,
        "window_seconds": extremes.get("window_seconds"),
        "sample_count": extremes.get("sample_count"),
        "price_source": extremes.get("price_source"),
        "market_state_source": "observer_realtime_same_cycle",
        "signal_timing": {
            "signal_detected_at": _now_iso(),
            "binance_observed_at": observed_at,
            "binance_window_seconds": extremes.get("window_seconds"),
            "binance_window_end_ms": max(
                [
                    value
                    for value in (
                        _epoch_ms(x_row.get("end_ms")),
                        _epoch_ms(y_row.get("end_ms")),
                    )
                    if value is not None
                ],
                default=_epoch_ms(observed_at),
            ),
            "x_event_start_ms": x_row.get("start_ms"),
            "x_event_end_ms": x_row.get("end_ms"),
            "y_event_start_ms": y_row.get("start_ms"),
            "y_event_end_ms": y_row.get("end_ms"),
        },
        "cow_filter": {
            "network": network,
            "chain_id": chain_id,
            "source": "observer_runtime_same_cycle",
            "threshold_detail": {
                "amount": amount,
                "min_side_change_percent": simulation.get("min_up_change_percent"),
                "min_window_spread_percent": simulation.get("min_window_spread_percent"),
            },
        },
        "pairs": [pair],
    }


def _record_attempts(attempts: list[dict[str, Any]], database_url: str | None) -> dict[str, Any]:
    if not attempts:
        return {"recorded": 0, "source": "empty", "error": None}
    pause_guard = cow_submission_pause_guard_status()
    if pause_guard.get("paused"):
        return {"recorded": 0, "source": "paused", "error": None, "pause_guard": pause_guard}
    if database_url:
        unavailable = database_unavailable_reason(database_url)
        if unavailable:
            count = append_cow_execution_attempts_jsonl(
                DEFAULT_ATTEMPT_LOG_PATH,
                attempts,
                dedupe_market_candidates=False,
            )
            return {"recorded": count, "source": "jsonl_fallback", "error": unavailable}
        try:
            ids = record_cow_execution_attempts(
                database_url,
                attempts,
                dedupe_market_candidates=False,
            )
            return {"recorded": len(ids), "source": "database", "ids": ids, "error": None}
        except Exception as exc:
            if is_database_unavailable_error(exc):
                mark_database_unavailable(database_url, exc)
            count = append_cow_execution_attempts_jsonl(
                DEFAULT_ATTEMPT_LOG_PATH,
                attempts,
                dedupe_market_candidates=False,
            )
            return {"recorded": count, "source": "jsonl_fallback", "error": str(exc)[:240]}
    count = append_cow_execution_attempts_jsonl(
        DEFAULT_ATTEMPT_LOG_PATH,
        attempts,
        dedupe_market_candidates=False,
    )
    return {"recorded": count, "source": "jsonl", "error": None}


def _quote_one_attempt(attempt: dict[str, Any], database_url: str | None, route_key: str) -> None:
    global _IN_FLIGHT
    started_at = _now_iso()
    started_perf = time.perf_counter()
    quote = attempt.setdefault("quote", {})
    quote.setdefault("quote_trigger", {})
    quote["quote_trigger"].update(
        {
            "mode": "immediate_same_observer_cycle",
            "quote_started_at": started_at,
            "route_key": route_key,
        }
    )
    try:
        result = default_quote_candidate(
            {
                "candidate": attempt,
                "signature": route_key,
            },
            database_url,
        )
        finished_at = _now_iso()
        timing = _timing_snapshot(
            quote,
            started_at=started_at,
            finished_at=finished_at,
            started_perf=started_perf,
        )
        for item in result.get("attempts") or []:
            item_quote = item.setdefault("quote", {})
            item_quote["quote_timing"] = timing
            item_quote["signal_timing"] = quote.get("signal_timing")
            item_quote["binance_window"] = {
                "x_start_price": quote.get("x_start_price"),
                "x_current_price": quote.get("x_current_price"),
                "y_start_price": quote.get("y_start_price"),
                "y_current_price": quote.get("y_current_price"),
                "x_start_ms": quote.get("x_start_ms"),
                "x_end_ms": quote.get("x_end_ms"),
                "y_start_ms": quote.get("y_start_ms"),
                "y_end_ms": quote.get("y_end_ms"),
            }
        _record_attempts(result.get("attempts") or [], database_url)
    except Exception as exc:
        failed = dict(attempt)
        failed_quote = failed.setdefault("quote", {})
        failed_quote["quote_timing"] = _timing_snapshot(
            failed_quote,
            started_at=started_at,
            finished_at=_now_iso(),
            started_perf=started_perf,
            error=str(exc),
        )
        failed["state"] = "quote_failed"
        failed["execution_phase"] = "quote_precheck"
        failed["error"] = str(exc)[:240]
        _record_attempts([failed], database_url)
    finally:
        with _LOCK:
            _IN_FLIGHT = max(0, _IN_FLIGHT - 1)


def trigger_realtime_cow_quote(
    extremes: dict[str, Any],
    simulation: dict[str, Any] | None,
    *,
    database_url: str | None = None,
) -> dict[str, Any]:
    if not realtime_cow_quote_enabled():
        return {"started": False, "reason": "disabled"}
    if not isinstance(simulation, dict) or not simulation.get("signal"):
        return {"started": False, "reason": "no_signal"}
    if not isinstance(extremes, dict):
        return {"started": False, "reason": "missing_extremes"}

    freshness = _freshness_status(extremes=extremes, simulation=simulation)
    if not freshness["fresh"]:
        return {"started": False, "reason": freshness["reason"], "freshness": freshness}

    from execution.cow_routes import cow_network_config

    network = os.getenv("COW_REALTIME_QUOTE_NETWORK", "avalanche").strip() or "avalanche"
    config = cow_network_config(network=network)
    amount = os.getenv(
        "COW_REALTIME_QUOTE_AMOUNT",
        os.getenv("COW_ARBITRAGE_DEFAULT_AMOUNT", "1"),
    ).strip() or "1"
    market_state = _build_market_state(
        extremes,
        simulation,
        network=config.network,
        chain_id=config.chain_id,
        amount=amount,
    )
    if not market_state:
        return {"started": False, "reason": "signal_rows_missing"}
    attempts = build_cow_market_candidate_attempts(market_state)
    if not attempts:
        return {"started": False, "reason": "no_candidate_attempt"}

    now = time.monotonic()
    cooldown = _env_float("COW_REALTIME_QUOTE_COOLDOWN_SECONDS", 0.25)
    max_inflight = _env_int("COW_REALTIME_QUOTE_MAX_INFLIGHT", 2)
    started_routes = []
    blocked_routes = []
    with _LOCK:
        global _IN_FLIGHT
        for attempt in attempts:
            route_key = candidate_signature(attempt)
            last_started = _LAST_STARTED_BY_ROUTE.get(route_key)
            if last_started is not None and now - last_started < cooldown:
                blocked_routes.append({"reason": "route_cooldown", "route_key": route_key})
                continue
            if _IN_FLIGHT >= max_inflight:
                blocked_routes.append({"reason": "max_inflight", "route_key": route_key})
                continue
            _LAST_STARTED_BY_ROUTE[route_key] = now
            _IN_FLIGHT += 1
            started_routes.append({"attempt": attempt, "route_key": route_key})

    for item in started_routes:
        thread = threading.Thread(
            target=_quote_one_attempt,
            args=(item["attempt"], database_url, item["route_key"]),
            name="cow-realtime-quote",
            daemon=True,
        )
        thread.start()

    if not started_routes:
        reason = blocked_routes[0]["reason"] if blocked_routes else "no_route_started"
        return {"started": False, "reason": reason, "blocked_routes": blocked_routes}
    return {
        "started": True,
        "started_count": len(started_routes),
        "blocked_count": len(blocked_routes),
        "route_keys": [item["route_key"] for item in started_routes],
        "route_directions": [item["attempt"].get("route_direction") for item in started_routes],
        "observed_at": market_state.get("observed_at"),
        "routes": [item["attempt"].get("route_path") for item in started_routes],
        "blocked_routes": blocked_routes,
        "mode": "immediate_same_observer_cycle",
        "freshness": freshness,
    }
