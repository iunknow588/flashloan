from __future__ import annotations

from typing import Any, Callable


def apply_static_call_result(
    payload: dict[str, Any],
    result: dict[str, Any],
    *,
    archive_failure: Callable[[dict[str, Any], dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    status = result["status"]
    error = result["error"]
    parsed = result.get("parsed") or {}
    preflight = dict(payload.get("preflight") or {})
    preflight.update(
        {
            "static_call_required": True,
            "static_call_status": status,
            "static_call_passed": status == "passed",
            "static_call_error": error,
            "static_call_error_category": parsed.get("category"),
            "static_call_error_label": parsed.get("label"),
            "static_call_simulated_at": result["simulated_at"],
        }
    )
    current = dict(payload)
    current["preflight"] = preflight
    if status == "error" and archive_failure is not None:
        archive_failure(current, parsed)
    return current
