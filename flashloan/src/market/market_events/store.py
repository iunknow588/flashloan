from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.env_loader import load_env_files, resolve_env_path

SRC_ROOT = Path(__file__).resolve().parents[1]
load_env_files(__file__)
MARKET_VOLATILITY_EVENT_STORE_PATH = resolve_env_path(
    "FLASHLOAN_MARKET_EVENT_STORE",
    "runtime/state/market_volatility_events.jsonl",
    SRC_ROOT,
)


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _parse_iso(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _event_id(event: dict[str, Any] | None) -> str:
    return str((event or {}).get("event_id") or "").strip()


def _event_timestamp(event: dict[str, Any] | None) -> datetime:
    parsed = _parse_iso((event or {}).get("recorded_at") or (event or {}).get("consumed_at") or (event or {}).get("observed_at"))
    return parsed or datetime.now(timezone.utc)


def _ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def _read_lines(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    try:
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(record, dict):
                records.append(record)
    except OSError:
        return []
    return records


def _append_record(path: Path, record: dict[str, Any]) -> dict[str, Any]:
    _ensure_parent(path)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
    return record


def _latest_records_by_event_id(records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for record in records:
        event_id = _event_id(record)
        if not event_id:
            continue
        previous = latest.get(event_id)
        if previous is None or _event_timestamp(record) >= _event_timestamp(previous):
            latest[event_id] = record
    return latest


def _normalize_record(event: dict[str, Any], *, status: str, consumer_page: str | None = None, consumed_at: str | None = None) -> dict[str, Any]:
    payload = event.get("payload")
    return {
        "event_type": event.get("event_type"),
        "event_id": _event_id(event),
        "status": status,
        "recorded_at": event.get("recorded_at") or _iso_now(),
        "observed_at": event.get("observed_at"),
        "expires_at": event.get("expires_at"),
        "severity": event.get("severity"),
        "trigger_reason": event.get("trigger_reason"),
        "affected_assets": list(event.get("affected_assets") or []),
        "window_seconds": event.get("window_seconds"),
        "sample_count": event.get("sample_count"),
        "active_sample_count": event.get("active_sample_count"),
        "gainer_count": event.get("gainer_count"),
        "loser_count": event.get("loser_count"),
        "market_divergence_index": event.get("market_divergence_index"),
        "min_change_percent": event.get("min_change_percent"),
        "max_abs_change_percent": event.get("max_abs_change_percent"),
        "consumer_page": consumer_page,
        "consumed_at": consumed_at,
        "payload": dict(payload) if isinstance(payload, dict) else {},
    }


def market_volatility_event_is_fresh(event: dict[str, Any] | None, *, now: datetime | None = None) -> bool:
    if not isinstance(event, dict):
        return False
    expires_at = _parse_iso(event.get("expires_at"))
    if expires_at is None:
        return False
    current = now or datetime.now(timezone.utc)
    return current <= expires_at


def market_volatility_event_is_consumed(event: dict[str, Any] | None) -> bool:
    if not isinstance(event, dict):
        return False
    return bool(event.get("consumed_at") or str(event.get("status") or "") == "consumed")


def market_volatility_event_record(event_id: str | None, *, path: Path | None = None) -> dict[str, Any] | None:
    if not event_id:
        return None
    store_path = path or MARKET_VOLATILITY_EVENT_STORE_PATH
    records = _read_lines(store_path)
    for record in reversed(records):
        if _event_id(record) == event_id:
            return record
    return None


def record_market_volatility_event(event: dict[str, Any], *, path: Path | None = None) -> dict[str, Any] | None:
    event_id = _event_id(event)
    if not event_id:
        return None
    store_path = path or MARKET_VOLATILITY_EVENT_STORE_PATH
    current = market_volatility_event_record(event_id, path=store_path)
    if current is not None:
        return current
    record = _normalize_record(event, status="recorded")
    return _append_record(store_path, record)


def consume_market_volatility_event(
    event: dict[str, Any],
    consumer_page: str,
    *,
    path: Path | None = None,
) -> dict[str, Any] | None:
    event_id = _event_id(event)
    if not event_id:
        return None
    store_path = path or MARKET_VOLATILITY_EVENT_STORE_PATH
    current = market_volatility_event_record(event_id, path=store_path)
    if current and market_volatility_event_is_consumed(current) and current.get("consumer_page") == consumer_page:
        return current
    if current and market_volatility_event_is_consumed(current):
        return current
    record = _normalize_record(event, status="consumed", consumer_page=consumer_page, consumed_at=_iso_now())
    return _append_record(store_path, record)


def latest_market_volatility_event(*, path: Path | None = None) -> dict[str, Any] | None:
    store_path = path or MARKET_VOLATILITY_EVENT_STORE_PATH
    records = list(_latest_records_by_event_id(_read_lines(store_path)).values())
    latest: dict[str, Any] | None = None
    for record in records:
        if latest is None:
            latest = record
            continue
        if _event_timestamp(record) >= _event_timestamp(latest):
            latest = record
    return latest


def latest_pending_market_volatility_event(*, path: Path | None = None) -> dict[str, Any] | None:
    store_path = path or MARKET_VOLATILITY_EVENT_STORE_PATH
    records = list(_latest_records_by_event_id(_read_lines(store_path)).values())
    latest: dict[str, Any] | None = None
    for record in records:
        if market_volatility_event_is_consumed(record):
            continue
        if not market_volatility_event_is_fresh(record):
            continue
        if latest is None or _event_timestamp(record) >= _event_timestamp(latest):
            latest = record
    return latest
