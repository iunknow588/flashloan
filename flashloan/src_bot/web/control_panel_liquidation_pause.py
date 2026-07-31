import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

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


def load_pause_guard_state(path: Path) -> dict[str, Any]:
    try:
        if path.exists():
            raw = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                # Ensure new fields have defaults
                for k, v in _DEFAULT_STATE.items():
                    raw.setdefault(k, v)
                return raw
    except Exception:
        pass
    return dict(_DEFAULT_STATE)


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
) -> dict[str, Any]:
    state = load_pause_guard_state(path)
    now = pause_guard_now()
    threshold = max(1, int(threshold))

    # ── Success path: progressive recovery ──────────────────────────────
    if state_name in SUCCESS_STATES:
        level = int(state.get("circuit_breaker_level") or 0)
        original_level = level
        # Step down one level if at level 2+ (paused/halted)
        if level >= 2:
            level -= 1
            state["circuit_breaker_level"] = level
            state["last_level_change_at"] = now
            if level < 2:
                state["paused"] = False
        # If was already at level 1 and no consecutive failures remain, fully recover
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
        # If still paused at level 2+, keep paused flag
        if int(state.get("circuit_breaker_level") or 0) >= 2:
            state["paused"] = True
        else:
            state["paused"] = False
        return save_pause_guard_state(path, state)

    # ── Neutral event: no state change ──────────────────────────────────
    if state_name not in FAILURE_STATES and not error and not blocked_reasons:
        return state

    # ── Failure path: 3-level circuit breaker ───────────────────────────
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

    # Level 1 (slowdown): failures >= 3 and currently normal
    if failures >= 3 and level < 1:
        level = 1
        state["circuit_breaker_level"] = level
        state["cooldown_multiplier"] = 2.0
        state["last_level_change_at"] = now

    # Level 2 (paused): failures >= 5
    if failures >= 5 and level < 2:
        level = 2
        state["circuit_breaker_level"] = level
        state["paused"] = True
        state["last_level_change_at"] = now

    # Level 3 (halted): failures >= 10
    if failures >= 10 and level < 3:
        level = 3
        state["circuit_breaker_level"] = level
        state["last_level_change_at"] = now

    # Legacy threshold-based pause (kept for backward compatibility)
    if enabled and failures >= threshold:
        state["paused"] = True

    return save_pause_guard_state(path, state)


def clear_pause_guard(path: Path) -> dict[str, Any]:
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
    return save_pause_guard_state(path, state)


def get_cooldown_seconds(base_seconds: float, path: Path) -> float:
    """Return cooldown duration adjusted by the current circuit-breaker multiplier."""
    state = load_pause_guard_state(path)
    multiplier = float(state.get("cooldown_multiplier") or 1.0)
    return base_seconds * multiplier

