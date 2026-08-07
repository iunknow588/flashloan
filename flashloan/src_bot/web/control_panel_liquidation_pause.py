from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from db.storage_common import database_unavailable_reason, is_database_unavailable_error, mark_database_unavailable
from web.parameter_config import (
    LIQUIDATION_PAUSE_GUARD_PATH,
    LIQUIDATION_PAUSE_GUARD_PAGE,
    LEGACY_LIQUIDATION_PAUSE_GUARD_PATHS,
    read_json_parameter,
    save_page_parameter_map as save_control_panel_parameter_map,
    load_page_parameter_map as load_control_panel_parameter_map,
    write_json_parameter,
    sync_page_parameter_file,
)

FAILURE_STATES = {"submission_blocked", "submission_failed", "static_call_failed", "confirmed_failed"}
SUCCESS_STATES = {"confirmed_success"}

_DEFAULT_STATE: dict[str, Any] = {
    "paused": False,
    "consecutive_failure_count": 0,
    "pause_reason": None,
    "updated_at": None,
    "last_failure_at": None,
    "last_success_at": None,
    "circuit_breaker_level": 0,
    "cooldown_multiplier": 1.0,
    "last_level_change_at": None,
}


def pause_guard_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _database_url_or_none(database_url: str | None = None) -> str | None:
    return str(database_url or os.getenv("DATABASE_URL", "")).strip() or None


def _normalized_state(raw: dict[str, Any] | None, *, source: str) -> dict[str, Any]:
    state = dict(_DEFAULT_STATE)
    if isinstance(raw, dict):
        state.update({key: raw.get(key) for key in state if key in raw})
    state["paused"] = bool(state.get("paused"))
    state["source"] = source
    return state


def _load_pause_guard_file(path: Path) -> dict[str, Any]:
    try:
        raw = read_json_parameter(path, legacy_paths=LEGACY_LIQUIDATION_PAUSE_GUARD_PATHS)
        if isinstance(raw, dict):
            return _normalized_state(raw, source="file")
    except Exception:
        pass
    return _normalized_state(None, source="default")


def _load_pause_guard_db(database_url: str) -> dict[str, Any] | None:
    values = load_control_panel_parameter_map(database_url, LIQUIDATION_PAUSE_GUARD_PAGE)
    if not values:
        return None
    try:
        sync_page_parameter_file(LIQUIDATION_PAUSE_GUARD_PAGE, values)
    except Exception:
        pass
    return _normalized_state(values, source="database")


def _save_pause_guard_db(database_url: str, state: dict[str, Any]) -> dict[str, Any]:
    values = {key: state.get(key) for key in _DEFAULT_STATE}
    save_control_panel_parameter_map(database_url, LIQUIDATION_PAUSE_GUARD_PAGE, values)
    try:
        sync_page_parameter_file(LIQUIDATION_PAUSE_GUARD_PAGE, values)
    except Exception:
        pass
    return _normalized_state(values, source="database")


def load_pause_guard_state(
    path: Path = LIQUIDATION_PAUSE_GUARD_PATH,
    *,
    database_url: str | None = None,
) -> dict[str, Any]:
    resolved_database_url = _database_url_or_none(database_url)
    if resolved_database_url and not database_unavailable_reason(resolved_database_url):
        try:
            state = _load_pause_guard_db(resolved_database_url)
            if state is not None:
                return state
            had_file = path.exists()
            file_state = _load_pause_guard_file(path)
            migrated = _save_pause_guard_db(resolved_database_url, file_state)
            migrated["source"] = "database_migrated_from_file" if had_file else "database_initialized"
            return migrated
        except Exception as exc:
            if is_database_unavailable_error(exc):
                mark_database_unavailable(resolved_database_url, exc)
            fallback = _load_pause_guard_file(path)
            fallback["source"] = "file_fallback"
            fallback["database_error"] = str(exc)
            return fallback
    return _load_pause_guard_file(path)


def save_pause_guard_state(
    path: Path,
    state: dict[str, Any],
    *,
    database_url: str | None = None,
) -> dict[str, Any]:
    payload = dict(_DEFAULT_STATE)
    payload.update(state)
    resolved_database_url = _database_url_or_none(database_url)
    if resolved_database_url and not database_unavailable_reason(resolved_database_url):
        try:
            return _save_pause_guard_db(resolved_database_url, payload)
        except Exception as exc:
            if is_database_unavailable_error(exc):
                mark_database_unavailable(resolved_database_url, exc)
            payload["source"] = "file_fallback"
            payload["database_error"] = str(exc)
    else:
        payload["source"] = "file"
    write_json_parameter(path, payload)
    return _normalized_state(payload, source=str(payload.get("source") or "file"))


def pause_guard_controls(
    path: Path = LIQUIDATION_PAUSE_GUARD_PATH,
    *,
    enabled: bool,
    threshold: int,
    database_url: str | None = None,
) -> dict[str, Any]:
    state = load_pause_guard_state(path, database_url=database_url)
    threshold = max(1, int(threshold))
    return {
        "auto_pause_enabled": bool(enabled),
        "auto_pause_active": bool(enabled and state.get("paused")),
        "auto_pause_threshold": threshold,
        "auto_pause_failure_count": int(state.get("consecutive_failure_count") or 0),
        "auto_pause_reason": state.get("pause_reason"),
        "auto_pause_updated_at": state.get("updated_at"),
        "circuit_breaker_level": int(state.get("circuit_breaker_level") or 0),
        "cooldown_multiplier": float(state.get("cooldown_multiplier") or 1.0),
    }


def record_pause_guard_event(
    path: Path,
    *,
    state_name: str,
    blocked_reasons: list[str] | None = None,
    error: str | None = None,
    enabled: bool,
    threshold: int,
    database_url: str | None = None,
) -> dict[str, Any]:
    state = load_pause_guard_state(path, database_url=database_url)
    now = pause_guard_now()
    threshold = max(1, int(threshold))

    if state_name in SUCCESS_STATES:
        level = int(state.get("circuit_breaker_level") or 0)
        original_level = level
        if level >= 2:
            level -= 1
            state["circuit_breaker_level"] = level
            state["last_level_change_at"] = now
            if level < 2:
                state["paused"] = False
        if original_level == 1 and int(state.get("consecutive_failure_count") or 0) == 0:
            state["circuit_breaker_level"] = 0
            state["cooldown_multiplier"] = 1.0
            state["last_level_change_at"] = now

        state.update(
            {
                "consecutive_failure_count": 0,
                "pause_reason": None,
                "updated_at": now,
                "last_success_at": now,
            }
        )
        if int(state.get("circuit_breaker_level") or 0) >= 2:
            state["paused"] = True
        else:
            state["paused"] = False
        return save_pause_guard_state(path, state, database_url=database_url)

    if state_name not in FAILURE_STATES and not error and not blocked_reasons:
        return state

    failures = int(state.get("consecutive_failure_count") or 0) + 1
    reason = ", ".join(blocked_reasons or []) or error or state_name
    level = int(state.get("circuit_breaker_level") or 0)

    state.update(
        {
            "consecutive_failure_count": failures,
            "last_failure_at": now,
            "updated_at": now,
            "pause_reason": reason,
        }
    )

    if failures >= 3 and level < 1:
        level = 1
        state["circuit_breaker_level"] = level
        state["cooldown_multiplier"] = 2.0
        state["last_level_change_at"] = now

    if failures >= 5 and level < 2:
        level = 2
        state["circuit_breaker_level"] = level
        state["paused"] = True
        state["last_level_change_at"] = now

    if failures >= 10 and level < 3:
        level = 3
        state["circuit_breaker_level"] = level
        state["last_level_change_at"] = now

    if enabled and failures >= threshold:
        state["paused"] = True

    return save_pause_guard_state(path, state, database_url=database_url)


def clear_pause_guard(
    path: Path = LIQUIDATION_PAUSE_GUARD_PATH,
    *,
    database_url: str | None = None,
) -> dict[str, Any]:
    state: dict[str, Any] = {
        "paused": False,
        "consecutive_failure_count": 0,
        "pause_reason": None,
        "updated_at": pause_guard_now(),
        "last_failure_at": None,
        "last_success_at": None,
        "circuit_breaker_level": 0,
        "cooldown_multiplier": 1.0,
        "last_level_change_at": None,
    }
    return save_pause_guard_state(path, state, database_url=database_url)


def get_cooldown_seconds(base_seconds: float, path: Path, *, database_url: str | None = None) -> float:
    state = load_pause_guard_state(path, database_url=database_url)
    multiplier = float(state.get("cooldown_multiplier") or 1.0)
    return base_seconds * multiplier
