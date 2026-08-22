from __future__ import annotations

from datetime import datetime, timezone
from statistics import mean, median
from typing import Any


def _time(row: dict[str, Any]) -> datetime | None:
    value = row.get("observed_at") or row.get("scanned_at") or row.get("timestamp")
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def summarize_opportunity_windows(
    rows: list[dict[str, Any]],
    *,
    intervals: dict[str, float] | None = None,
) -> dict[str, Any]:
    intervals = intervals or {"core": 1.0, "high_frequency": 300.0, "borrow_health": 1800.0}
    episodes: dict[str, list[datetime]] = {}
    for row in rows:
        account = str(row.get("account") or "").strip().lower()
        health = row.get("health_factor")
        observed = _time(row)
        if not account or observed is None:
            continue
        try:
            is_liquidatable = float(health) < 1.0
        except (TypeError, ValueError):
            is_liquidatable = str(row.get("status") or "").lower() == "liquidatable"
        if is_liquidatable:
            episodes.setdefault(account, []).append(observed)
    durations = []
    for values in episodes.values():
        ordered = sorted(values)
        if len(ordered) >= 2:
            durations.append((ordered[-1] - ordered[0]).total_seconds())
    result = {
        "status": "calculated" if durations else "insufficient_window_data",
        "liquidatable_account_count": len(episodes),
        "episode_count": len(durations),
        "survival_seconds": _summary(durations),
        "coverage": {},
    }
    if durations:
        for tier, interval in intervals.items():
            result["coverage"][tier] = {
                "refresh_interval_seconds": float(interval),
                "episodes_shorter_than_interval": sum(1 for value in durations if value < interval),
                "short_episode_fraction": sum(1 for value in durations if value < interval) / len(durations),
            }
    return result


def _summary(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"p50": None, "p90": None, "average": None}
    ordered = sorted(values)
    p90 = ordered[min(len(ordered) - 1, int(round((len(ordered) - 1) * 0.9)))]
    return {"p50": median(values), "p90": p90, "average": mean(values)}

