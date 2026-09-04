from __future__ import annotations

from pathlib import Path
from typing import Optional

from core.json_io import read_json


def aave_reserve_cache(cache_path: Path) -> Optional[dict]:
    data = read_json(cache_path)
    if not data:
        return None
    assets = data.get("assets") or []
    symbols = list(dict.fromkeys(item.get("binance_symbol") for item in assets if item.get("binance_symbol")))
    return {
        "refreshed_at": data.get("refreshed_at"),
        "pool_address": data.get("pool_address"),
        "asset_count": len(assets),
        "symbols": symbols,
    }


def stable_target_cache(cache_path: Path) -> Optional[dict]:
    data = read_json(cache_path)
    if not data:
        return None
    assets = data.get("assets") or []
    symbols = list(dict.fromkeys(item.get("binance_symbol") for item in assets if item.get("binance_symbol")))
    by_source: dict[str, list[str]] = {}
    for item in assets:
        symbol = item.get("binance_symbol")
        for source in [*(item.get("via_borrows") or []), *(item.get("via_stables") or [])]:
            by_source.setdefault(str(source), [])
            if symbol and symbol not in by_source[str(source)]:
                by_source[str(source)].append(str(symbol))
    return {
        "refreshed_at": data.get("refreshed_at"),
        "router_address": data.get("router_address"),
        "asset_count": len(assets),
        "symbols": symbols,
        "by_source": by_source,
        "source": data.get("source"),
    }


def borrow_target_universe(aave_cache_path: Path, stable_target_cache_path: Path) -> Optional[dict]:
    borrow_cache = aave_reserve_cache(aave_cache_path)
    target_cache = stable_target_cache(stable_target_cache_path)
    if not borrow_cache or not target_cache:
        return None
    borrow_symbols = borrow_cache.get("symbols") or []
    target_symbols = target_cache.get("symbols") or []
    target_by_borrow = target_cache.get("by_source") or {}
    borrow_rows = [
        {
            "borrow_symbol": symbol,
            "borrow_name": symbol.replace("USDT", "") if isinstance(symbol, str) else symbol,
            "target_count": len(target_by_borrow.get(symbol, [])),
            "target_symbols": target_by_borrow.get(symbol, []),
        }
        for symbol in borrow_symbols
    ]
    return {
        "borrow_count": len(borrow_symbols),
        "target_count": len(target_symbols),
        "borrow_symbols": borrow_symbols,
        "target_symbols": target_symbols,
        "borrow_rows": borrow_rows,
        "borrow_cache_refreshed_at": borrow_cache.get("refreshed_at"),
        "target_cache_refreshed_at": target_cache.get("refreshed_at"),
        "target_by_borrow": target_by_borrow,
        "target_source": target_cache.get("source"),
    }
