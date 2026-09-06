"""Market-state refresh and fallback policy for the Binance/CoW workflow."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable


def select_market_state(
    realtime_state: dict[str, Any] | None,
    *,
    side_limit: int,
    snapshot_path: Path,
    max_previous_age_seconds: float | None,
    needs_refresh: Callable[[dict[str, Any] | None, int], bool],
    read_snapshot: Callable[[Path], dict[str, Any] | None],
    previous_snapshot: Callable[[dict[str, Any] | None, float | None], dict[str, Any] | None],
    build_snapshot: Callable[[dict[str, Any] | None, int], dict[str, Any]],
    persist_snapshot: Callable[[dict[str, Any], Path], None],
) -> dict[str, Any] | None:
    """Choose realtime state, a fresh REST interval, or an explicit fallback.

    The callbacks keep this policy independent from Binance transport and file
    formats. Callers can therefore test the fallback contract without network
    access and keep the legacy service as a compatibility facade.
    """

    limit = max(1, int(side_limit))
    if not needs_refresh(realtime_state, limit):
        selected = dict(realtime_state or {})
        selected.setdefault("market_state_source", "realtime_extremes")
        return selected

    snapshot = read_snapshot(snapshot_path)
    previous = previous_snapshot(snapshot, max_previous_age_seconds)
    try:
        selected = build_snapshot(previous, limit)
        selected["market_state_source"] = "rest_interval_live"
        selected["fallback_reason"] = (
            "latest_extremes_insufficient"
            if previous is not None
            else "previous_window_missing_or_stale"
        )
        persist_snapshot(selected, snapshot_path)
        return selected
    except Exception:
        if snapshot and not needs_refresh(snapshot, limit):
            selected = dict(snapshot)
            selected["market_state_source"] = "snapshot_file_stale_fallback"
            selected["fallback_reason"] = "rest_interval_refresh_failed"
            return selected
        raise
