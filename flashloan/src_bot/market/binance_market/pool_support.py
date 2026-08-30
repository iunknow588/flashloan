from __future__ import annotations

import json
from pathlib import Path
import threading
from typing import Any


SRC_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_AVALANCHE_V3_POOL_CACHE_PATH = SRC_ROOT / "runtime" / "cache" / "avalanche_l1_pool_static_table.json"
DEFAULT_BNB_POOL_CACHE_PATH = SRC_ROOT / "runtime" / "cache" / "bnb_arbitrage_pool_static_table.json"

WRAPPED_SYMBOL_ALIASES = {
    "ETH": ("WETH",),
    "AVAX": ("WAVAX", "WAVAX.E"),
    "BTC": ("BTC.B", "BTCB"),
    "BTC.B": ("BTC",),
    "WBTC": ("WBTC.E",),
    "WBTC.E": ("WBTC",),
    "LINK": ("LINK.E",),
    "AAVE": ("AAVE.E",),
    "MATIC": ("WPOL", "WMATIC", "POL"),
    "POL": ("WPOL", "WMATIC", "MATIC"),
    "BNB": ("WBNB",),
    "XDAI": ("WXDAI", "WDAI"),
}

_POOL_STATIC_TABLE_LOCK = threading.Lock()
_POOL_STATIC_TABLE_MEMORY_CACHE: dict[str, dict[str, Any]] = {}


def _symbol_alias_set(symbol: str) -> set[str]:
    value = str(symbol or "").strip().upper()
    if not value:
        return set()
    aliases = {value}
    if "." in value:
        aliases.add(value.split(".", 1)[0])
    aliases.update(str(item).upper() for item in WRAPPED_SYMBOL_ALIASES.get(value, ()))
    return aliases


def _pool_symbol(entry: dict[str, Any], side: str) -> str:
    return str(
        entry.get(f"token{side}_symbol")
        or entry.get(f"token_{side.lower()}_symbol")
        or entry.get(f"token{0 if side == 'X' else 1}_symbol")
        or ""
    ).strip().upper()


def _effective_pool_count(entry: dict[str, Any]) -> int:
    rows = entry.get("pools") if isinstance(entry.get("pools"), list) else [entry]
    count = 0
    for row in rows:
        if not isinstance(row, dict):
            continue
        if int(row.get("adapterKind", row.get("adapter_kind", 1)) or 0) != 1:
            continue
        if str(row.get("pool") or row.get("pool_address") or row.get("address") or "").strip():
            count += 1
    return count


def _load_avalanche_v3_pool_filter(cache_path: Path = DEFAULT_AVALANCHE_V3_POOL_CACHE_PATH) -> dict[str, Any]:
    key = str(cache_path)
    with _POOL_STATIC_TABLE_LOCK:
        cached = _POOL_STATIC_TABLE_MEMORY_CACHE.get(key)
        if cached:
            return cached
        payload: dict[str, Any] = {
            "enabled": True,
            "network": "avalanche",
            "source": str(cache_path),
            "loaded": False,
            "pair_count": 0,
            "symbols": [],
            "pairs": {},
            "error": None,
        }
        try:
            data = json.loads(cache_path.read_text(encoding="utf-8"))
            pairs: dict[tuple[str, str], dict[str, Any]] = {}
            symbols: set[str] = set()
            for entry in data.get("pools") or []:
                if not isinstance(entry, dict):
                    continue
                left = _pool_symbol(entry, "X")
                right = _pool_symbol(entry, "Y")
                if not left or not right:
                    continue
                count = _effective_pool_count(entry)
                if count <= 0:
                    continue
                pair_key = tuple(sorted((left, right)))
                pairs[pair_key] = {
                    "pair": f"{left}:{right}",
                    "left": left,
                    "right": right,
                    "effectivePoolCount": count,
                }
                symbols.update((left, right))
            payload.update(
                {
                    "loaded": True,
                    "pair_count": len(pairs),
                    "symbols": sorted(symbols),
                    "pairs": pairs,
                    "cache_block_number": str(data.get("block_number") or data.get("sourceDiscoveryBlockNumber") or ""),
                    "fetched_at": str(data.get("fetched_at") or data.get("sourceDiscoveryFetchedAt") or data.get("generatedAt") or ""),
                    "factory": str(data.get("factory") or ""),
                    "quoter": str(data.get("quoter") or ""),
                    "mode": str(data.get("mode") or ""),
                }
            )
        except Exception as exc:
            payload["error"] = str(exc)
        _POOL_STATIC_TABLE_MEMORY_CACHE[key] = payload
        return payload


def _pool_pair_info(pool_filter: dict[str, Any] | None, left: str, right: str) -> dict[str, Any] | None:
    if not pool_filter or not pool_filter.get("loaded"):
        return None
    pairs = pool_filter.get("pairs") if isinstance(pool_filter.get("pairs"), dict) else {}
    for left_alias in _symbol_alias_set(left):
        for right_alias in _symbol_alias_set(right):
            item = pairs.get(tuple(sorted((left_alias, right_alias))))
            if item:
                return item
    return None


def _pool_route_support(
    x_symbol: str,
    y_symbol: str,
    *,
    network: str | None,
    pool_filter: dict[str, Any] | None,
) -> dict[str, Any]:
    selected_network = str(network or "").strip().lower()
    if selected_network == "bnb":
        stable_candidates = ("USDT", "USDC")
        matched_routes = []
        attempted_routes = []
        for stable in stable_candidates:
            required = [(stable, x_symbol), (x_symbol, y_symbol), (y_symbol, stable)]
            missing = []
            matched = []
            effective = 0
            for left, right in required:
                item = _pool_pair_info(pool_filter, left, right)
                if not item:
                    missing.append(f"{left}:{right}")
                    continue
                count = int(item.get("effectivePoolCount") or 0)
                effective += count
                matched.append(
                    {
                        "requestedPair": f"{left}:{right}",
                        "cachePair": item.get("pair"),
                        "effectivePoolCount": count,
                    }
                )
            attempted_routes.append({"route": f"{stable}->{x_symbol}->{y_symbol}->{stable}", "missingPairs": missing})
            if not missing and pool_filter and pool_filter.get("loaded"):
                matched_routes.append(
                    {
                        "borrowSymbol": stable,
                        "route": f"{stable}->{x_symbol}->{y_symbol}->{stable}",
                        "matchedPairs": matched,
                        "effectivePoolSlots": effective,
                    }
                )
        best = max(matched_routes, key=lambda item: int(item["effectivePoolSlots"]), default=None)
        return {
            "supported": best is not None,
            "reason": None if best else "missing_required_bnb_high_quality_pool_pair",
            "requiredPairs": best.get("route").split("->") if best else [],
            "missingPairs": [] if best else [item for route in attempted_routes for item in route["missingPairs"]],
            "matchedPairs": best.get("matchedPairs") if best else [],
            "effectivePoolSlots": best.get("effectivePoolSlots") if best else 0,
            "borrowSymbol": best.get("borrowSymbol") if best else None,
            "attemptedRoutes": attempted_routes,
        }
    if selected_network != "avalanche":
        return {
            "supported": False,
            "reason": "unsupported_network_pool_filter",
            "requiredPairs": [],
            "missingPairs": [],
            "matchedPairs": [],
            "effectivePoolSlots": 0,
        }
    required = [("USDC", x_symbol), ("USDC", y_symbol), (x_symbol, y_symbol)]
    missing = []
    matched = []
    effective = 0
    for left, right in required:
        item = _pool_pair_info(pool_filter, left, right)
        if not item:
            missing.append(f"{left}:{right}")
            continue
        count = int(item.get("effectivePoolCount") or 0)
        effective += count
        matched.append(
            {
                "requestedPair": f"{left}:{right}",
                "cachePair": item.get("pair"),
                "effectivePoolCount": count,
            }
        )
    return {
        "supported": not missing and bool(pool_filter and pool_filter.get("loaded")),
        "reason": None if not missing else "missing_required_avalanche_v3_pool_pair",
        "requiredPairs": [f"{left}:{right}" for left, right in required],
        "missingPairs": missing,
        "matchedPairs": matched,
        "effectivePoolSlots": effective,
    }


def _filter_rows_by_avalanche_pool_symbol_support(rows: list[dict[str, Any]], pool_filter: dict[str, Any] | None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if not pool_filter or not pool_filter.get("loaded"):
        return [], [
            dict(row, pool_supported=False, pool_filter_reason=pool_filter.get("error") if pool_filter else "pool_filter_unavailable")
            for row in rows
        ]
    supported_symbols = set()
    for symbol in pool_filter.get("symbols") or []:
        supported_symbols.update(_symbol_alias_set(str(symbol)))
    supported = []
    unsupported = []
    for row in rows:
        base = str(row.get("base_symbol") or "").upper()
        copied = dict(row)
        if _symbol_alias_set(base) & supported_symbols:
            copied["pool_supported"] = True
            supported.append(copied)
        else:
            copied["pool_supported"] = False
            copied["pool_filter_reason"] = "symbol_not_in_avalanche_v3_pool_static_table"
            unsupported.append(copied)
    return supported, unsupported


def _filter_pairs_by_avalanche_pool_route_support(
    top_rows: list[dict[str, Any]],
    bottom_rows: list[dict[str, Any]],
    *,
    network: str | None,
    pool_filter: dict[str, Any] | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    keep_top: dict[str, dict[str, Any]] = {}
    keep_bottom: dict[str, dict[str, Any]] = {}
    rejected = []
    for x in top_rows:
        for y in bottom_rows:
            if x.get("symbol") == y.get("symbol"):
                continue
            support = _pool_route_support(
                str(x.get("base_symbol") or ""),
                str(y.get("base_symbol") or ""),
                network=network,
                pool_filter=pool_filter,
            )
            if support["supported"]:
                keep_top.setdefault(str(x.get("symbol") or ""), dict(x))
                keep_bottom.setdefault(str(y.get("symbol") or ""), dict(y))
            else:
                rejected.append(
                    {
                        "pair": f"{x.get('base_symbol')}:{y.get('base_symbol')}",
                        "missingPairs": support.get("missingPairs") or [],
                        "reason": support.get("reason"),
                    }
                )
    return list(keep_top.values()), list(keep_bottom.values()), rejected


def _load_bnb_pool_filter(cache_path: Path = DEFAULT_BNB_POOL_CACHE_PATH) -> dict[str, Any]:
    """Load only high-quality BNB pool edges from the BNB-specific static table."""
    key = str(cache_path)
    with _POOL_STATIC_TABLE_LOCK:
        cached = _POOL_STATIC_TABLE_MEMORY_CACHE.get(key)
        if cached:
            return cached
        payload: dict[str, Any] = {
            "enabled": True,
            "network": "bnb",
            "source": str(cache_path),
            "loaded": False,
            "pair_count": 0,
            "symbols": [],
            "pairs": {},
            "error": None,
        }
        try:
            data = json.loads(cache_path.read_text(encoding="utf-8"))
            if str(data.get("network") or "").lower() != "bnb" or int(data.get("chainId") or 0) != 56:
                raise ValueError("BNB static pool table has an unexpected network or chain ID")
            pairs: dict[tuple[str, str], dict[str, Any]] = {}
            symbols: set[str] = set()
            for row in data.get("pools") or []:
                if not isinstance(row, dict) or str(row.get("status") or "") != "recommended":
                    continue
                left = str(row.get("token0Symbol") or "").strip().upper()
                right = str(row.get("token1Symbol") or "").strip().upper()
                if not left or not right or not _pool_address_text(row):
                    continue
                pair_key = tuple(sorted((left, right)))
                item = pairs.setdefault(
                    pair_key,
                    {
                        "pair": f"{left}:{right}",
                        "left": left,
                        "right": right,
                        "effectivePoolCount": 0,
                        "bestTvlUsd": 0.0,
                    },
                )
                item["effectivePoolCount"] += 1
                item["bestTvlUsd"] = max(float(item["bestTvlUsd"]), float(row.get("tvlUsd") or 0))
                symbols.update((left, right))
            payload.update(
                {
                    "loaded": True,
                    "pair_count": len(pairs),
                    "symbols": sorted(symbols),
                    "pairs": pairs,
                    "cache_block_number": str(data.get("generatedBlockNumber") or ""),
                    "fetched_at": str(data.get("generatedAt") or ""),
                    "mode": "bnb_recommended_pools_only",
                }
            )
        except Exception as exc:
            payload["error"] = str(exc)
        _POOL_STATIC_TABLE_MEMORY_CACHE[key] = payload
        return payload


def _pool_address_text(row: dict[str, Any]) -> str:
    return str(row.get("pool") or row.get("pool_address") or row.get("address") or "").strip()


def _pool_filter_for_network(network: str | None) -> dict[str, Any] | None:
    selected = str(network or "").strip().lower()
    if selected == "avalanche":
        return _load_avalanche_v3_pool_filter()
    if selected == "bnb":
        return _load_bnb_pool_filter()
    return None


def _filter_rows_by_pool_symbol_support(
    rows: list[dict[str, Any]],
    pool_filter: dict[str, Any] | None,
    *,
    network: str | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if str(network or "").lower() == "avalanche":
        return _filter_rows_by_avalanche_pool_symbol_support(rows, pool_filter)
    if not pool_filter or not pool_filter.get("loaded"):
        return [], [
            dict(row, pool_supported=False, pool_filter_reason=pool_filter.get("error") if pool_filter else "pool_filter_unavailable")
            for row in rows
        ]
    supported_symbols = set(str(symbol).upper() for symbol in pool_filter.get("symbols") or [])
    supported = []
    unsupported = []
    for row in rows:
        copied = dict(row)
        base = str(copied.get("base_symbol") or "").upper()
        if base in supported_symbols:
            copied["pool_supported"] = True
            supported.append(copied)
        else:
            copied["pool_supported"] = False
            copied["pool_filter_reason"] = "symbol_not_in_bnb_high_quality_pool_static_table"
            unsupported.append(copied)
    return supported, unsupported


def _filter_pairs_by_pool_route_support(
    top_rows: list[dict[str, Any]],
    bottom_rows: list[dict[str, Any]],
    *,
    network: str | None,
    pool_filter: dict[str, Any] | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    if str(network or "").lower() == "avalanche":
        return _filter_pairs_by_avalanche_pool_route_support(
            top_rows,
            bottom_rows,
            network=network,
            pool_filter=pool_filter,
        )
    keep_top: dict[str, dict[str, Any]] = {}
    keep_bottom: dict[str, dict[str, Any]] = {}
    rejected = []
    for x in top_rows:
        for y in bottom_rows:
            if x.get("symbol") == y.get("symbol"):
                continue
            support = _pool_route_support(
                str(x.get("base_symbol") or ""),
                str(y.get("base_symbol") or ""),
                network=network,
                pool_filter=pool_filter,
            )
            if support["supported"]:
                keep_top.setdefault(str(x.get("symbol") or ""), dict(x))
                keep_bottom.setdefault(str(y.get("symbol") or ""), dict(y))
            else:
                rejected.append(
                    {
                        "pair": f"{x.get('base_symbol')}:{y.get('base_symbol')}",
                        "missingPairs": support.get("missingPairs") or [],
                        "reason": support.get("reason"),
                    }
                )
    return list(keep_top.values()), list(keep_bottom.values()), rejected


__all__ = [
    "DEFAULT_AVALANCHE_V3_POOL_CACHE_PATH",
    "DEFAULT_BNB_POOL_CACHE_PATH",
    "WRAPPED_SYMBOL_ALIASES",
    "_filter_pairs_by_avalanche_pool_route_support",
    "_filter_pairs_by_pool_route_support",
    "_filter_rows_by_avalanche_pool_symbol_support",
    "_filter_rows_by_pool_symbol_support",
    "_load_avalanche_v3_pool_filter",
    "_load_bnb_pool_filter",
    "_pool_address_text",
    "_pool_filter_for_network",
    "_pool_pair_info",
    "_pool_route_support",
    "_pool_symbol",
    "_effective_pool_count",
    "_symbol_alias_set",
]
