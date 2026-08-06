from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SRC_ROOT = Path(__file__).resolve().parents[1]
COW_SUBMISSION_PAUSE_GUARD_PATH = SRC_ROOT / "runtime" / "cache" / "cow_submission_pause_guard.json"

_DEFAULT_STATE: dict[str, Any] = {
    "paused": False,
    "pause_reason": None,
    "updated_at": None,
    "last_paused_at": None,
    "last_resumed_at": None,
}


def cow_pause_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_cow_submission_pause_guard(path: Path = COW_SUBMISSION_PAUSE_GUARD_PATH) -> dict[str, Any]:
    try:
        if path.exists():
            raw = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                state = dict(_DEFAULT_STATE)
                state.update(raw)
                return state
    except Exception:
        pass
    return dict(_DEFAULT_STATE)


def save_cow_submission_pause_guard(
    state: dict[str, Any],
    path: Path = COW_SUBMISSION_PAUSE_GUARD_PATH,
) -> dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(_DEFAULT_STATE)
    payload.update(state)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def cow_submission_pause_guard_status(path: Path = COW_SUBMISSION_PAUSE_GUARD_PATH) -> dict[str, Any]:
    return {"configured": True, **load_cow_submission_pause_guard(path)}


def cow_submission_paused(path: Path = COW_SUBMISSION_PAUSE_GUARD_PATH) -> bool:
    return bool(load_cow_submission_pause_guard(path).get("paused"))


def set_cow_submission_pause_guard(
    *,
    paused: bool,
    reason: str | None = None,
    path: Path = COW_SUBMISSION_PAUSE_GUARD_PATH,
) -> dict[str, Any]:
    state = load_cow_submission_pause_guard(path)
    now = cow_pause_now()
    clean_reason = str(reason or "").strip() or ("manual_pause" if paused else None)
    state.update(
        {
            "paused": bool(paused),
            "pause_reason": clean_reason if paused else None,
            "updated_at": now,
        }
    )
    if paused:
        state["last_paused_at"] = now
    else:
        state["last_resumed_at"] = now
    return {"configured": True, **save_cow_submission_pause_guard(state, path)}


def clear_cow_submission_pause_guard(path: Path = COW_SUBMISSION_PAUSE_GUARD_PATH) -> dict[str, Any]:
    return set_cow_submission_pause_guard(paused=False, reason=None, path=path)
