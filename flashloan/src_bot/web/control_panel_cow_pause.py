from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from db.storage_common import database_unavailable_reason, is_database_unavailable_error, mark_database_unavailable
from web.parameter_config import (
    COW_SUBMISSION_PAUSE_GUARD_PATH,
    COW_SUBMISSION_PAGE,
    LEGACY_COW_SUBMISSION_PAUSE_GUARD_PATHS,
    load_page_parameter_map as load_control_panel_parameter_map,
    read_json_parameter,
    save_page_parameter_map as save_control_panel_parameter_map,
    write_json_parameter,
    sync_page_parameter_file,
)


_DEFAULT_STATE: dict[str, Any] = {
    "paused": True,
    "order_submission_enabled": False,
    "pause_reason": "startup_transaction_switch_off",
    "updated_at": None,
    "last_paused_at": None,
    "last_resumed_at": None,
}


def cow_pause_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _database_url_or_none(database_url: str | None = None) -> str | None:
    return str(database_url or os.getenv("DATABASE_URL", "")).strip() or None


def _normalized_state(raw: dict[str, Any] | None, *, source: str) -> dict[str, Any]:
    state = dict(_DEFAULT_STATE)
    if isinstance(raw, dict):
        state.update({key: raw.get(key) for key in state if key in raw})
    state["paused"] = bool(state.get("paused"))
    state["order_submission_enabled"] = not state["paused"]
    state["source"] = source
    return state


def _state_payload(state: dict[str, Any]) -> dict[str, Any]:
    normalized = _normalized_state(state, source=str(state.get("source") or "memory"))
    return {key: normalized.get(key) for key in _DEFAULT_STATE}


def _load_cow_submission_pause_guard_file(path: Path) -> dict[str, Any]:
    try:
        raw = read_json_parameter(path, legacy_paths=LEGACY_COW_SUBMISSION_PAUSE_GUARD_PATHS)
        if isinstance(raw, dict):
            return _normalized_state(raw, source="file")
    except Exception:
        pass
    return _normalized_state(
        {
            "paused": True,
            "pause_reason": "startup_transaction_switch_off",
        },
        source="default",
    )


def _load_cow_submission_pause_guard_db(database_url: str) -> dict[str, Any] | None:
    values = load_control_panel_parameter_map(database_url, COW_SUBMISSION_PAGE)
    if not values:
        return None
    normalized = _normalized_state(values, source="database")
    payload = _state_payload(normalized)
    if any(values.get(key) != payload.get(key) for key in payload):
        try:
            save_control_panel_parameter_map(database_url, COW_SUBMISSION_PAGE, payload)
        except Exception:
            pass
    try:
        sync_page_parameter_file(COW_SUBMISSION_PAGE, payload)
    except Exception:
        pass
    return normalized


def _save_cow_submission_pause_guard_db(database_url: str, state: dict[str, Any]) -> dict[str, Any]:
    values = _state_payload(state)
    save_control_panel_parameter_map(database_url, COW_SUBMISSION_PAGE, values)
    try:
        sync_page_parameter_file(COW_SUBMISSION_PAGE, values)
    except Exception:
        pass
    return _normalized_state(values, source="database")


def load_cow_submission_pause_guard(
    path: Path = COW_SUBMISSION_PAUSE_GUARD_PATH,
    *,
    database_url: str | None = None,
) -> dict[str, Any]:
    resolved_database_url = _database_url_or_none(database_url)
    if resolved_database_url and not database_unavailable_reason(resolved_database_url):
        try:
            state = _load_cow_submission_pause_guard_db(resolved_database_url)
            if state is not None:
                return state
            had_file = path.exists()
            file_state = _load_cow_submission_pause_guard_file(path)
            migrated = _save_cow_submission_pause_guard_db(resolved_database_url, file_state)
            migrated["source"] = "database_migrated_from_file" if had_file else "database_initialized"
            return migrated
        except Exception as exc:
            if is_database_unavailable_error(exc):
                mark_database_unavailable(resolved_database_url, exc)
            fallback = _load_cow_submission_pause_guard_file(path)
            fallback["source"] = "file_fallback"
            fallback["database_error"] = str(exc)
            return fallback
    return _load_cow_submission_pause_guard_file(path)


def save_cow_submission_pause_guard(
    state: dict[str, Any],
    path: Path = COW_SUBMISSION_PAUSE_GUARD_PATH,
    *,
    database_url: str | None = None,
) -> dict[str, Any]:
    payload = dict(_DEFAULT_STATE)
    payload.update(state)
    resolved_database_url = _database_url_or_none(database_url)
    if resolved_database_url and not database_unavailable_reason(resolved_database_url):
        try:
            return _save_cow_submission_pause_guard_db(resolved_database_url, payload)
        except Exception as exc:
            if is_database_unavailable_error(exc):
                mark_database_unavailable(resolved_database_url, exc)
            payload["source"] = "file_fallback"
            payload["database_error"] = str(exc)
    else:
        payload["source"] = "file"
    write_json_parameter(path, payload)
    return _normalized_state(payload, source=str(payload.get("source") or "file"))


def cow_submission_pause_guard_status(
    path: Path = COW_SUBMISSION_PAUSE_GUARD_PATH,
    *,
    database_url: str | None = None,
) -> dict[str, Any]:
    resolved_database_url = _database_url_or_none(database_url)
    return {
        "configured": bool(resolved_database_url),
        "database_configured": bool(resolved_database_url),
        **load_cow_submission_pause_guard(path, database_url=resolved_database_url),
    }


def cow_submission_paused(
    path: Path = COW_SUBMISSION_PAUSE_GUARD_PATH,
    *,
    database_url: str | None = None,
) -> bool:
    return bool(load_cow_submission_pause_guard(path, database_url=database_url).get("paused"))


def disable_cow_submission_for_startup(
    path: Path = COW_SUBMISSION_PAUSE_GUARD_PATH,
    *,
    database_url: str | None = None,
) -> dict[str, Any]:
    """Initialize the CoW switch once without overwriting an existing user choice."""
    existing = load_cow_submission_pause_guard(path, database_url=database_url)
    if existing.get("updated_at") or existing.get("source") in {"database", "database_migrated_from_file", "file"}:
        return {"configured": bool(_database_url_or_none(database_url)), **existing}
    return set_cow_submission_pause_guard(
        paused=True,
        reason="startup_transaction_switch_off",
        path=path,
        database_url=database_url,
    )


def set_cow_submission_pause_guard(
    *,
    paused: bool,
    reason: str | None = None,
    path: Path = COW_SUBMISSION_PAUSE_GUARD_PATH,
    database_url: str | None = None,
) -> dict[str, Any]:
    state = load_cow_submission_pause_guard(path, database_url=database_url)
    now = cow_pause_now()
    clean_reason = str(reason or "").strip() or ("manual_pause" if paused else None)
    state.update(
        {
            "paused": bool(paused),
            "order_submission_enabled": not bool(paused),
            "pause_reason": clean_reason if paused else None,
            "updated_at": now,
        }
    )
    if paused:
        state["last_paused_at"] = now
    else:
        state["last_resumed_at"] = now
    return {
        "configured": bool(_database_url_or_none(database_url)),
        "database_configured": bool(_database_url_or_none(database_url)),
        **save_cow_submission_pause_guard(state, path, database_url=database_url),
    }


def clear_cow_submission_pause_guard(
    path: Path = COW_SUBMISSION_PAUSE_GUARD_PATH,
    *,
    database_url: str | None = None,
) -> dict[str, Any]:
    return set_cow_submission_pause_guard(paused=False, reason=None, path=path, database_url=database_url)
