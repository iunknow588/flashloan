"""Pool candidate helpers split from the public candidate service."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from market.binance_market.pool_candidate_constants import (
    MAX_READABLE_SYMBOL_LENGTH,
    READABLE_SYMBOL_RE,
    STABLE_SYMBOLS,
)

def _read_json_object(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}

def _cache_signature(path: Path) -> tuple[str, int, int]:
    try:
        stat = path.stat()
    except OSError:
        return (str(path), 0, 0)
    return (str(path), int(stat.st_mtime_ns), int(stat.st_size))

def _string_number(value) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed == parsed and parsed not in (float("inf"), float("-inf")) else None

def _int_sort_value(value: object) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0

def _stable_symbol(symbol: object) -> bool:
    normalized = str(symbol or "").strip().upper()
    bridge_base = normalized[:-2] if normalized.endswith(".E") else normalized
    return normalized in STABLE_SYMBOLS or bridge_base in STABLE_SYMBOLS or normalized.endswith("USD")

def _readable_symbol(symbol: object) -> bool:
    text = str(symbol or "").strip().upper()
    return bool(text) and len(text) <= MAX_READABLE_SYMBOL_LENGTH and bool(READABLE_SYMBOL_RE.fullmatch(text))

def _pair_classification(token0_symbol: object, token1_symbol: object) -> dict[str, Any]:
    token0 = str(token0_symbol or "").strip().upper()
    token1 = str(token1_symbol or "").strip().upper()
    token0_stable = _stable_symbol(token0)
    token1_stable = _stable_symbol(token1)
    stable_symbols = [symbol for symbol, stable in ((token0, token0_stable), (token1, token1_stable)) if symbol and stable]
    non_stable_symbols = [symbol for symbol, stable in ((token0, token0_stable), (token1, token1_stable)) if symbol and not stable]
    if token0_stable and token1_stable:
        pair_class = "stable_pair"
        label = "稳定币池"
        route_role = "stable_anchor_pool"
    elif token0_stable or token1_stable:
        pair_class = "stable_to_non_stable_pair"
        label = "稳定币-非稳定币池"
        route_role = "flashloan_entry_or_exit_leg"
    else:
        pair_class = "non_stable_pair"
        label = "非稳定币池"
        route_role = "middle_volatility_leg"
    return {
        "pairClass": pair_class,
        "pairClassLabel": label,
        "routeRole": route_role,
        "token0IsStable": token0_stable,
        "token1IsStable": token1_stable,
        "stableSymbols": stable_symbols,
        "nonStableSymbols": non_stable_symbols,
    }

def _depth_band(tvl_usd: object) -> dict[str, str]:
    tvl = _string_number(tvl_usd) or 0.0
    if tvl >= 1_000_000:
        return {"level": "deep", "label": "深度充足"}
    if tvl >= 100_000:
        return {"level": "medium", "label": "中等深度"}
    if tvl >= 10_000:
        return {"level": "shallow", "label": "浅池"}
    if tvl > 0:
        return {"level": "dust", "label": "极浅"}
    return {"level": "unavailable", "label": "不可用"}

def _pool_address(row: dict) -> str:
    pool_key = row.get("poolKey") if isinstance(row.get("poolKey"), dict) else {}
    return str(
        row.get("pool")
        or row.get("pool_address")
        or row.get("poolId")
        or row.get("address")
        or pool_key.get("poolId")
        or ""
    ).strip()

def _token_address(value: object) -> str:
    text = str(value or "").strip()
    return text.lower() if text.startswith("0x") and len(text) == 42 else ""

def _unordered_address_pair(token0: object, token1: object) -> str:
    left = _token_address(token0)
    right = _token_address(token1)
    if not left or not right:
        return ""
    return ":".join(sorted((left, right)))
