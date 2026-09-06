"""Freshness checks for pool evidence consumed by local execution gates."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping


_TIMESTAMP_FIELDS = ("observed_at", "generated_at", "refreshed_at", "updated_at")


def _parse_timestamp(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return (parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)


def evidence_timestamp(evidence: Mapping[str, Any]) -> datetime | None:
    """Find the newest top-level timestamp carried by an evidence payload."""
    timestamps = [_parse_timestamp(evidence.get(field)) for field in _TIMESTAMP_FIELDS]
    valid = [timestamp for timestamp in timestamps if timestamp is not None]
    return max(valid) if valid else None


def validate_pool_evidence_freshness(
    evidence: Mapping[str, Any],
    *,
    max_age_seconds: float | None,
    now: datetime | None = None,
) -> tuple[bool, tuple[str, ...]]:
    """Fail closed when freshness is requested but evidence is absent or stale."""
    if max_age_seconds is None:
        return True, ()
    try:
        max_age = float(max_age_seconds)
    except (TypeError, ValueError):
        return False, ("freshness_limit_invalid",)
    if max_age < 0:
        return False, ("freshness_limit_invalid",)
    observed = evidence_timestamp(evidence)
    if observed is None:
        return False, ("evidence_timestamp_missing_or_invalid",)
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    age = (current - observed).total_seconds()
    if age < 0:
        return False, ("evidence_timestamp_in_future",)
    if age > max_age:
        return False, ("pool_evidence_stale",)
    return True, ()


__all__ = ["evidence_timestamp", "validate_pool_evidence_freshness"]
