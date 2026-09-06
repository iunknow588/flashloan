from __future__ import annotations

from datetime import datetime
from typing import Any


def build_discovery_window_result(
    *,
    force_full: bool,
    scan_start_at: datetime,
    scan_end_at: datetime,
    interval_seconds: float,
    registry: dict[str, Any],
    mode: str,
    from_block: int,
    to_block: int,
    lookback_blocks: int,
) -> dict[str, Any]:
    if force_full and scan_end_at <= scan_start_at:
        return {
            "saved": False,
            "count": 0,
            "skipped": True,
            "reason": "historical backfill complete",
            "mode": mode,
            "from_block": from_block,
            "to_block": to_block,
            "lookback_blocks": lookback_blocks,
            "scan_start_at": scan_start_at.isoformat(timespec="seconds"),
            "scan_end_at": scan_end_at.isoformat(timespec="seconds"),
            "registry_window": registry,
        }

    if not force_full and (scan_end_at - scan_start_at).total_seconds() < interval_seconds:
        return {
            "saved": False,
            "count": 0,
            "skipped": True,
            "reason": "discovery interval not reached",
            "mode": mode,
            "from_block": from_block,
            "to_block": to_block,
            "lookback_blocks": lookback_blocks,
            "scan_start_at": scan_start_at.isoformat(timespec="seconds"),
            "scan_end_at": scan_end_at.isoformat(timespec="seconds"),
            "registry_window": registry,
        }

    return {
        "saved": False,
        "count": 0,
        "skipped": False,
        "mode": mode,
        "from_block": from_block,
        "to_block": to_block,
        "lookback_blocks": lookback_blocks,
        "scan_start_at": scan_start_at.isoformat(timespec="seconds"),
        "scan_end_at": scan_end_at.isoformat(timespec="seconds"),
        "registry_window": registry,
    }
