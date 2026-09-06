from __future__ import annotations

import json
from typing import Any


def json_or_default(value: Any, default: Any) -> Any:
    if not value:
        return default
    if not isinstance(value, str):
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default


def failure_row(row: Any, scope: dict[str, Any]) -> dict[str, Any]:
    if len(row) == 10:
        row = (scope["market_id"], scope["chain_id"], scope["network"], scope["protocol"], *row)
    return {
        "market_id": str(row[0]),
        "chain_id": int(row[1]) if row[1] is not None else None,
        "network": str(row[2]) if row[2] else None,
        "protocol": str(row[3]) if row[3] else None,
        "id": int(row[4]),
        "account": str(row[5]) if row[5] else None,
        "block_number": int(row[6]) if row[6] is not None else None,
        "collateral_asset": str(row[7]) if row[7] else None,
        "debt_asset": str(row[8]) if row[8] else None,
        "failure_type": str(row[9]),
        "failure_reason": str(row[10]) if row[10] else None,
        "payload": json_or_default(row[11], {}),
        "source": str(row[12]),
        "created_at": row[13].isoformat() if row[13] else None,
    }


__all__ = ["failure_row", "json_or_default"]
