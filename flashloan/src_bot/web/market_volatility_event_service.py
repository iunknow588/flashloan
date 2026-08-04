from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any

from web.page_state import PageName, RouteIntent


def _parse_iso(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _unique_assets(extremes: dict[str, Any]) -> list[str]:
    assets: list[str] = []
    for row in list(extremes.get("top") or []) + list(extremes.get("bottom") or []):
        symbol = str(row.get("symbol") or "").strip().upper()
        if symbol and symbol not in assets:
            assets.append(symbol)
    return assets


def _max_abs_change(extremes: dict[str, Any]) -> float:
    changes = [abs(float(row.get("change_percent") or 0.0)) for row in list(extremes.get("top") or []) + list(extremes.get("bottom") or [])]
    return max(changes) if changes else 0.0


def _event_severity(extremes: dict[str, Any]) -> str:
    active_sample_count = int(extremes.get("active_sample_count") or 0)
    divergence_index = float(extremes.get("market_divergence_index") or 0.0)
    max_abs_change = _max_abs_change(extremes)
    if max_abs_change >= 5.0 or active_sample_count >= 8 or divergence_index >= 3.0:
        return "high"
    if max_abs_change >= 2.0 or active_sample_count >= 4 or divergence_index >= 1.5:
        return "medium"
    return "low"


def build_market_volatility_event(extremes: dict[str, Any] | None, *, max_age_seconds: int = 120) -> dict[str, Any] | None:
    if not isinstance(extremes, dict):
        return None
    top = list(extremes.get("top") or [])
    bottom = list(extremes.get("bottom") or [])
    if not top and not bottom:
        return None

    observed_at = _parse_iso(extremes.get("observed_at")) or datetime.now(timezone.utc)
    window_seconds = float(extremes.get("window_seconds") or 0.0)
    active_sample_count = int(extremes.get("active_sample_count") or 0)
    if active_sample_count <= 0 and _max_abs_change(extremes) <= 0.0:
        return None

    ttl_seconds = max(float(max_age_seconds), max(30.0, window_seconds * 2.0))
    expires_at = observed_at + timedelta(seconds=ttl_seconds)
    affected_assets = _unique_assets(extremes)
    severity = _event_severity(extremes)
    trigger_reason = (
        f"{severity}_volatility: {extremes.get('gainer_count') or 0} gainers / "
        f"{extremes.get('loser_count') or 0} losers in {window_seconds:.1f}s"
    )
    event_seed = {
        "observed_at": observed_at.isoformat(timespec="seconds"),
        "window_seconds": round(window_seconds, 3),
        "severity": severity,
        "trigger_reason": trigger_reason,
        "affected_assets": affected_assets,
        "sample_count": int(extremes.get("sample_count") or 0),
        "active_sample_count": active_sample_count,
        "max_abs_change_percent": round(_max_abs_change(extremes), 6),
        "min_change_percent": round(float(extremes.get("min_change_percent") or 0.0), 6),
    }
    event_id = hashlib.sha256(json.dumps(event_seed, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()[:24]
    event = {
        "event_type": "MARKET_VOLATILITY_ALERT",
        "event_id": event_id,
        "severity": severity,
        "trigger_reason": trigger_reason,
        "affected_assets": affected_assets,
        "observed_at": observed_at.isoformat(timespec="seconds"),
        "expires_at": expires_at.isoformat(timespec="seconds"),
        "window_seconds": window_seconds,
        "sample_count": int(extremes.get("sample_count") or 0),
        "active_sample_count": active_sample_count,
        "gainer_count": int(extremes.get("gainer_count") or 0),
        "loser_count": int(extremes.get("loser_count") or 0),
        "market_divergence_index": float(extremes.get("market_divergence_index") or 0.0),
        "min_change_percent": float(extremes.get("min_change_percent") or 0.0),
        "max_abs_change_percent": _max_abs_change(extremes),
        "payload": dict(extremes),
    }
    return event


def market_volatility_event_is_fresh(event: dict[str, Any] | None, *, now: datetime | None = None) -> bool:
    if not isinstance(event, dict):
        return False
    expires_at = _parse_iso(event.get("expires_at"))
    if expires_at is None:
        return False
    current = now or datetime.now(timezone.utc)
    return current <= expires_at


def market_volatility_route_intent(event: dict[str, Any]) -> dict[str, Any]:
    return RouteIntent(
        source_page=PageName.MARKET_OBSERVATION.value,
        target_page=PageName.DEBT_POOL.value,
        reason=str(event.get("trigger_reason") or "market_volatility_alert"),
        event_id=str(event.get("event_id") or ""),
        context_version="market_volatility_event:v1",
        created_at=str(event.get("observed_at") or _iso_now()),
        context={
            "severity": event.get("severity"),
            "affected_assets": list(event.get("affected_assets") or []),
            "expires_at": event.get("expires_at"),
        },
    ).to_dict()
