"""Validation for chain-isolated static pool tables."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_chain_static_table(path: Path, *, network: str, chain_id: int) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    table_network = str(payload.get("network") or "").strip().lower()
    table_chain_id = int(payload.get("chainId") or payload.get("chain_id") or 0)
    if table_network != network or table_chain_id != chain_id:
        raise ValueError(f"{network} intent requires a {network} chain static pool table")
    if not isinstance(payload.get("pools"), list):
        raise ValueError(f"{network} static pool table has no pools")
    return payload
