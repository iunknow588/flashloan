from __future__ import annotations

from typing import Any

from core.db.liquidation_attempts_mapping import json_or_default as _json_or_default
from core.db.scoped_adapter import validate_scoped_rows


COLUMN_NAMES = (
    "market_id",
    "chain_id",
    "network",
    "protocol",
    "id",
    "started_at",
    "finished_at",
    "status",
    "account_count",
    "scanned_count",
    "risk_count",
    "error_count",
    "entered_count",
    "exited_count",
    "rpc_url",
    "block_number",
    "watch_health_factor",
    "error",
    "metadata_json",
)


def borrow_health_scan_batch_from_row(row: Any, scope: dict[str, Any]) -> dict[str, Any]:
    named = validate_scoped_rows(
        [dict(zip(COLUMN_NAMES, row))],
        scope,
    )[0]
    row = tuple(named[name] for name in COLUMN_NAMES)
    if len(row) == 15:
        row = (scope["market_id"], scope["chain_id"], scope["network"], scope["protocol"], *row)
    return {
        "market_id": row[0],
        "chain_id": int(row[1]) if row[1] is not None else None,
        "network": row[2],
        "protocol": row[3],
        "id": int(row[4]),
        "started_at": row[5].isoformat() if row[5] else None,
        "finished_at": row[6].isoformat() if row[6] else None,
        "status": row[7],
        "account_count": int(row[8] or 0),
        "scanned_count": int(row[9] or 0),
        "risk_count": int(row[10] or 0),
        "error_count": int(row[11] or 0),
        "entered_count": int(row[12] or 0),
        "exited_count": int(row[13] or 0),
        "rpc_url": row[14],
        "block_number": int(row[15]) if row[15] is not None else None,
        "watch_health_factor": row[16],
        "error": row[17],
        "metadata": _json_or_default(row[18], {}),
    }


__all__ = ["borrow_health_scan_batch_from_row"]
