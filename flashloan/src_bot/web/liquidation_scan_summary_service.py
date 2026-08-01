from __future__ import annotations

from typing import Any


def build_liquidation_health_summary(payload: dict[str, Any]) -> dict[str, Any]:
    rows = list(payload.get("rows") or [])
    limit = int(payload.get("limit") or 0)
    if limit > 0:
        rows = rows[:limit]
    return {
        "rows": rows,
        "count": len(rows),
        "limit": limit,
    }


def build_liquidation_account_summary(payload: dict[str, Any]) -> dict[str, Any]:
    rows = list(payload.get("rows") or [])
    limit = int(payload.get("limit") or 0)
    if limit > 0:
        rows = rows[:limit]
    return {
        "rows": rows,
        "count": len(rows),
        "limit": limit,
    }
