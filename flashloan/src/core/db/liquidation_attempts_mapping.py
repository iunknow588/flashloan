from __future__ import annotations

import json
from typing import Any

from core.db.scoped_adapter import validate_scoped_row


def json_or_default(value: Any, default: Any) -> Any:
    if not value:
        return default
    if not isinstance(value, str):
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default


def execution_attempt_from_row(row: Any, scope: dict[str, Any]) -> dict[str, Any]:
    if len(row) == 13:
        row = (scope["market_id"], scope["chain_id"], scope["network"], scope["protocol"], *row)
    row = validate_scoped_row(
        {
            "market_id": row[0],
            "chain_id": row[1],
            "network": row[2],
            "protocol": row[3],
        },
        scope,
    ) | {"_raw": row}
    raw = row["_raw"]
    return {
        "market_id": row["market_id"],
        "chain_id": row["chain_id"],
        "network": row["network"],
        "protocol": row["protocol"],
        "id": int(raw[4]),
        "account": str(raw[5]) if raw[5] else None,
        "mode": str(raw[6]),
        "state": str(raw[7]),
        "blocked_reasons": json_or_default(raw[8], []),
        "request": json_or_default(raw[9], {}),
        "quote": json_or_default(raw[10], {}),
        "preflight": json_or_default(raw[11], {}),
        "tx_hash": str(raw[12]) if raw[12] else None,
        "receipt": json_or_default(raw[13], {}),
        "error": str(raw[14]) if raw[14] else None,
        "created_at": raw[15].isoformat() if raw[15] else None,
        "updated_at": raw[16].isoformat() if raw[16] else None,
    }


__all__ = ["execution_attempt_from_row", "json_or_default"]
