from __future__ import annotations

from typing import Optional

from core.parameter_config import STRATEGY_CONFIG_PATH
from market.binance_market.control_panel_config import STRATEGY_DEFAULTS, strategy_config as read_strategy_config


def strategy_config() -> dict:
    return read_strategy_config(STRATEGY_CONFIG_PATH)


def _strategy_float(
    key: str,
    config: dict | None = None,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float:
    default = float(STRATEGY_DEFAULTS[key])
    raw = (config or strategy_config()).get(key, default)
    try:
        value = float(raw)
    except (TypeError, ValueError):
        value = default
    if minimum is not None:
        value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value


def opportunity_health_rows(extremes: Optional[dict], config: Optional[dict] = None) -> list[dict]:
    if not extremes:
        return []
    basket = extremes.get("basket")
    if not isinstance(basket, list) or not basket:
        return []
    config = config or strategy_config()
    up_threshold = _strategy_float("TRIGGER_MIN_UP_CHANGE_PERCENT", config, minimum=0.0001)
    down_threshold = _strategy_float("TRIGGER_MIN_DOWN_CHANGE_PERCENT", config, minimum=0.0001)
    rows: list[dict] = []
    for index, item in enumerate(basket, start=1):
        if not isinstance(item, dict):
            continue
        symbol = str(item.get("symbol", "")).upper().strip()
        if not symbol:
            continue
        raw_change = item.get("change_percent")
        try:
            change_percent = float(raw_change) if raw_change is not None else None
        except (TypeError, ValueError):
            change_percent = None
        threshold = up_threshold if (change_percent or 0.0) >= 0 else down_threshold
        health_score = None
        health_gap_percent = None
        if change_percent is not None:
            health_score = abs(change_percent) / threshold * 100 if threshold > 0 else 0.0
            health_gap_percent = abs(change_percent) - threshold
        if not bool(item.get("window_ready")):
            status = "watching"
        elif health_score is None:
            status = "watching"
        elif health_score >= 130:
            status = "selected"
        elif health_score >= 100:
            status = "candidate"
        elif health_score >= 70:
            status = "watching"
        else:
            status = "healthy"
        rows.append(
            {
                "rank": index,
                "symbol": symbol,
                "change_percent": change_percent,
                "health_score": round(health_score, 2) if health_score is not None else None,
                "health_gap_percent": round(health_gap_percent, 4) if health_gap_percent is not None else None,
                "trigger_threshold_percent": threshold,
                "status": status,
                "window_ready": bool(item.get("window_ready")),
                "current_price": item.get("current_price"),
                "start_price": item.get("start_price"),
                "price_source": item.get("price_source"),
                "window_seconds": extremes.get("window_seconds"),
                "observed_at": extremes.get("observed_at"),
            }
        )
    rows.sort(
        key=lambda row: (
            row["health_score"] is None,
            -(float(row["health_score"]) if row["health_score"] is not None else -1.0),
            -abs(float(row["change_percent"]) if row["change_percent"] is not None else 0.0),
            row["symbol"],
        )
    )
    for position, row in enumerate(rows, start=1):
        row["rank"] = position
    return rows


def opportunity_health_summary(rows: list[dict], config: Optional[dict] = None) -> dict:
    config = config or strategy_config()
    monitor_window_seconds = _strategy_float("BINANCE_CHANGE_WINDOW_SECONDS", config, minimum=0.0)
    trigger_up = _strategy_float("TRIGGER_MIN_UP_CHANGE_PERCENT", config, minimum=0.0)
    trigger_down = _strategy_float("TRIGGER_MIN_DOWN_CHANGE_PERCENT", config, minimum=0.0)
    candidate_count = sum(1 for row in rows if row.get("status") in {"candidate", "selected"})
    selected_count = sum(1 for row in rows if row.get("status") == "selected")
    watched_count = sum(1 for row in rows if row.get("status") == "watching")
    healthy_count = sum(1 for row in rows if row.get("status") == "healthy")
    best_row = rows[0] if rows else None
    return {
        "total": len(rows),
        "candidate_count": candidate_count,
        "selected_count": selected_count,
        "watched_count": watched_count,
        "healthy_count": healthy_count,
        "monitor_window_seconds": monitor_window_seconds,
        "trigger_up_percent": trigger_up,
        "trigger_down_percent": trigger_down,
        "best_symbol": best_row.get("symbol") if best_row else None,
        "best_health_score": best_row.get("health_score") if best_row else None,
        "best_status": best_row.get("status") if best_row else None,
    }
