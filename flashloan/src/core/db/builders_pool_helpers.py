"""Shared pool-row helpers for the candidate and observation builders.

The candidate builder, the observation builder and the abnormal-candidate
builder all read the same observed pool rows and all have to answer the same
questions about them: what is the pool identifier, which token pair does it
belong to, does it carry depth, and how are its JSON columns decoded.

These helpers are shared so that the answer to each question is **one**
answer. A pool that one builder treats as having depth and another builder
treats as lacking depth would produce a candidate surface and an observation
surface that disagree about the same chain state, which is exactly the kind of
inconsistency the architecture guards are there to prevent.

The module owns no database access and no policy. The fee ceiling, the hook
rule and the depth rule are applied by the callers.
"""

from __future__ import annotations

import json
from typing import Any

ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"
NO_HOOK_ADDRESSES = {"", ZERO_ADDRESS}


def _text(value: Any) -> str:
    return str(value or "").strip().lower()


def _int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _json_loads(value: str | None) -> Any:
    if value is None:
        return {}
    try:
        return json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}


def _pool_id(pool: dict[str, Any]) -> str:
    return _text(pool.get("pool_id") or pool.get("poolId") or pool.get("pool"))


def _has_depth(pool: dict[str, Any]) -> bool:
    has_liquidity = pool.get("has_liquidity")
    if has_liquidity is not None:
        return bool(has_liquidity) or _float(pool.get("liquidity_index")) > 0
    return _float(pool.get("liquidity_index")) > 0 or _float(pool.get("estimated_tvl_usd")) > 0


def _pools_for_token_pair(
    pools_by_edge: dict[str, list[dict[str, Any]]],
    token_x: str,
    token_y: str,
) -> list[dict[str, Any]]:
    """Return physical pools regardless of which token the JSON stored first."""
    x = _text(token_x)
    y = _text(token_y)
    if not x or not y:
        return []
    return pools_by_edge.get(f"{x}:{y}") or pools_by_edge.get(f"{y}:{x}") or []
