import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

FAILURE_STATES = {"submission_blocked", "submission_failed", "static_call_failed", "confirmed_failed"}
SUCCESS_STATES = {"confirmed_success"}


def pause_guard_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_pause_guard_state(path: Path) -> dict[str, Any]:
    try:
        if path.exists():
            raw = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                return raw
    except Exception:
        pass
    return {
        "paused": False,
        "consecutive_failure_count": 0,
        "pause_reason": None,
        "updated_at": None,
        "last_failure_at": None,
        "last_success_at": None,
    }


def save_pause_guard_state(path: Path, state: dict[str, Any]) -> dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    return state


def pause_guard_controls(path: Path, *, enabled: bool, threshold: int) -> dict[str, Any]:
    state = load_pause_guard_state(path)
    threshold = max(1, int(threshold))
    return {
        "auto_pause_enabled": bool(enabled),
        "auto_pause_active": bool(enabled and state.get("paused")),
        "auto_pause_threshold": threshold,
        "auto_pause_failure_count": int(state.get("consecutive_failure_count") or 0),
        "auto_pause_reason": state.get("pause_reason"),
        "auto_pause_updated_at": state.get("updated_at"),
    }


def record_pause_guard_event(
    path: Path,
    *,
    state_name: str,
    blocked_reasons: list[str] | None = None,
    error: str | None = None,
    enabled: bool,
    threshold: int,
) -> dict[str, Any]:
    state = load_pause_guard_state(path)
    now = pause_guard_now()
    threshold = max(1, int(threshold))
    if state_name in SUCCESS_STATES:
        state.update(
            {
                "paused": False,
                "consecutive_failure_count": 0,
                "pause_reason": None,
                "updated_at": now,
                "last_success_at": now,
            }
        )
        return save_pause_guard_state(path, state)
    if state_name not in FAILURE_STATES and not error and not blocked_reasons:
        return state
    failures = int(state.get("consecutive_failure_count") or 0) + 1
    reason = ", ".join(blocked_reasons or []) or error or state_name
    state.update(
        {
            "consecutive_failure_count": failures,
            "last_failure_at": now,
            "updated_at": now,
            "pause_reason": reason,
        }
    )
    if enabled and failures >= threshold:
        state["paused"] = True
    return save_pause_guard_state(path, state)


def clear_pause_guard(path: Path) -> dict[str, Any]:
    state = {
        "paused": False,
        "consecutive_failure_count": 0,
        "pause_reason": None,
        "updated_at": pause_guard_now(),
        "last_failure_at": None,
        "last_success_at": None,
    }
    return save_pause_guard_state(path, state)
