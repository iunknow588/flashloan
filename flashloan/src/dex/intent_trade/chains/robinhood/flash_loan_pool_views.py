from __future__ import annotations

import json
import os
import copy
import threading
import time
from typing import Any, Callable

from core.chain_data_registry import chain_data_by_chain_id, chain_data_by_network
from dex.intent_trade.chains.robinhood_common.chain_config import (
    MAINNET_ANCHOR_ADDRESS_BY_SYMBOL,
    MAINNET_ANCHOR_SYMBOLS,
)
from dex.intent_trade.flows.flash_loan_pools.presenter import (
    _as_float,
    _asset_row,
    _cache_identities,
    _cache_identity,
    _depth_counts,
    _empty_protocol_view,
    _is_stable_symbol,
    _pool_view_capabilities,
    _protocol_display_name,
)


_robinhood_v4_static_pool_cache: dict[tuple[Any, str, int, str], tuple[float, list[dict[str, Any]]]] = {}
_robinhood_v4_static_pool_cache_lock = threading.Lock()
_ROBINHOOD_V4_STATIC_CACHE_TTL_SECONDS = 5.0
# Display-only cache. It never feeds broadcast quotes, balances, minProfit,
# or keyset assembly; execution paths always read SQLite directly.
_robinhood_v4_protocol_view_cache: dict[tuple[Any, str, int, str], tuple[float, dict[str, Any]]] = {}
_robinhood_v4_protocol_view_cache_lock = threading.Lock()
_ROBINHOOD_V4_PROTOCOL_VIEW_CACHE_TTL_SECONDS = 15.0


def _sqlite_fingerprint(database_url: str | None) -> tuple[Any, ...]:
    try:
        from core.db.database_path import absolute_database_path

        path = absolute_database_path(database_url)
        # Cache invalidation is explicit through clear_robinhood_v4_pool_cache.
        # File mtime/size can change merely from SQLite opening the database,
        # which would make ordinary page reads miss the protocol-view cache.
        return (str(path),)
    except OSError:
        return (str(database_url or ""),)


def _network_for_chain_id(chain_id: int | None) -> str:
    if chain_id is None:
        return ""
    try:
        row = chain_data_by_chain_id(int(chain_id)) or {}
    except Exception:
        row = {}
    return str(row.get("network") or "").strip().lower()


def _chain_id_for_network(network: str, default: int) -> int:
    try:
        row = chain_data_by_network(network) or {}
    except Exception:
        row = {}
    try:
        return int(row.get("chain_id") or default)
    except (TypeError, ValueError):
        return default


def _is_robinhood_mainnet_scope(payload: dict[str, Any]) -> bool:
    """Reject evidence clearly scoped to another Robinhood network/chain."""
    expected_chain_id = _chain_id_for_network("robinhood", 4663)
    network = str(payload.get("network") or "").strip().lower()
    if network and network != "robinhood":
        return False
    raw_chain_id = payload.get("chainId", payload.get("chain_id"))
    if raw_chain_id is None:
        return True
    try:
        return int(str(raw_chain_id).strip()) == expected_chain_id
    except (TypeError, ValueError):
        return False


def _json_object(value: object) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _row_first(row: dict[str, Any], names: tuple[str, ...]) -> Any:
    for name in names:
        if name in row and row[name] not in (None, ""):
            return row[name]
    return None


def clear_robinhood_v4_pool_cache(
    *,
    database_url: str | None = None,
    network: str = "robinhood",
    chain_id: int = 4663,
) -> int:
    """Drop cached static V4 pool rows after an import/rebuild."""
    from core.rs_bridge import clear_v4_exchange_pool_cache
    from dex.intent_trade.chains.robinhood.v4_quote_adapter import (
        clear_robinhood_v4_pool_rows_cache as clear_mainnet_quote_rows,
    )
    from dex.intent_trade.chains.robinhood_testnet.v4_quote_adapter import (
        clear_robinhood_v4_pool_rows_cache as clear_testnet_quote_rows,
    )
    from market.binance_market.dex_surface_v4 import clear_v4_token_metadata_cache

    with _robinhood_v4_static_pool_cache_lock:
        _robinhood_v4_static_pool_cache.clear()
    with _robinhood_v4_protocol_view_cache_lock:
        _robinhood_v4_protocol_view_cache.clear()
    protocol = "uniswap_v4"
    clear_mainnet_quote_rows()
    clear_testnet_quote_rows()
    clear_v4_token_metadata_cache()
    return clear_v4_exchange_pool_cache(
        database_url,
        network=network,
        chain_id=chain_id,
        protocol=protocol,
    )


def _robinhood_v4_static_pool_rows(
    database_url: str | None,
    *,
    network: str = "robinhood",
    chain_id: int = 4663,
) -> list[dict[str, Any]]:
    cache_key = (database_url, network, chain_id, "uniswap_v4")
    now = time.time()
    with _robinhood_v4_static_pool_cache_lock:
        cached_at, cached_rows = _robinhood_v4_static_pool_cache.get(cache_key, (None, None))
        if cached_at is not None and now - cached_at < _ROBINHOOD_V4_STATIC_CACHE_TTL_SECONDS:
            return cached_rows or []
    from core.rs_bridge import load_v4_exchange_pool_rows_for_view

    rows = load_v4_exchange_pool_rows_for_view(
        network,
        chain_id,
        "uniswap_v4",
        database_url=database_url,
    )
    with _robinhood_v4_static_pool_cache_lock:
        _robinhood_v4_static_pool_cache[cache_key] = (now, rows)
    return rows


def _robinhood_v4_pool_rows_with_liquidity(
    pool_rows: list[dict[str, Any]],
    liquidity_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    liquidity_by_id = {
        str(_row_first(row, ("pool_id", "poolId", "pool")) or "").strip().lower(): row
        for row in liquidity_rows
        if isinstance(row, dict)
    }
    pools: list[dict[str, Any]] = []
    for row in pool_rows:
        if not isinstance(row, dict):
            continue
        pool_id = str(_row_first(row, ("pool_id", "poolId", "pool", "id")) or "").strip()
        token0 = str(_row_first(row, ("token0", "currency0")) or "").strip().lower()
        token1 = str(_row_first(row, ("token1", "currency1")) or "").strip().lower()
        if not pool_id or not token0 or not token1:
            continue
        pool_key = {
            "currency0": token0,
            "currency1": token1,
            "fee": _row_first(row, ("fee",)),
            "hooks": _row_first(row, ("hooks",)),
        }
        liquidity = liquidity_by_id.get(pool_id.lower(), {})
        liquidity_index = liquidity.get("liquidity_index")
        if liquidity_index is None:
            liquidity_index = _as_float(liquidity.get("liquidity"))
        has_liquidity = liquidity.get("has_liquidity")
        if has_liquidity is None:
            has_liquidity = liquidity.get("hasLiquidity")
        if has_liquidity is not None and isinstance(has_liquidity, str):
            has_liquidity = has_liquidity.strip().lower() in {"1", "true", "yes"}
        depth_band = {"level": "indexed", "label": "indexed V4 PoolKey"}
        pools.append(
            {
                "protocol": "uniswap_v4",
                "poolId": pool_id,
                "pool": pool_id,
                "poolManager": _row_first(row, ("pool_manager", "poolManager")),
                "poolKey": pool_key,
                "poolKeyDecoded": bool(pool_key),
                "token0": token0,
                "token1": token1,
                "token0Symbol": "",
                "token1Symbol": "",
                "symbolPair": "",
                "fee": _row_first(row, ("fee",)),
                "hooks": _row_first(row, ("hooks",)),
                "tickSpacing": _row_first(row, ("tick_spacing", "tickSpacing")),
                "status": "initialized_pool",
                "hasLiquidity": has_liquidity,
                "liquidity": liquidity.get("liquidity") or "",
                "liquidityIndex": liquidity_index or 0,
                "estimatedTvlUsd": liquidity.get("estimated_tvl_usd") or 0,
                "depthStatus": liquidity.get("depth_status") or "",
                "depthBand": depth_band,
                "balances": {},
                "sqrtPriceX96": liquidity.get("sqrt_price_x96") or "",
                "tick": liquidity.get("tick"),
                "liquidityReadAt": liquidity.get("liquidity_read_at"),
                "liquidityBlockNumber": liquidity.get("liquidity_block_number"),
                "recentSwapActive": liquidity.get("recent_swap_active"),
                "source": "robinhood_mainnet_v4_sqlite",
            }
        )
    return pools


def _stock_metadata_from_db_rows(
    token_rows: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Build official stock token metadata from SQLite ``chain_tokens`` rows."""
    result: dict[str, dict[str, Any]] = {}
    for row in token_rows:
        if not isinstance(row, dict):
            continue
        address = str(row.get("token_address") or "").strip().lower()
        symbol = str(row.get("token_symbol") or "").strip().upper()
        if not address or not symbol:
            continue
        raw_metadata = str(row.get("token_metadata_json") or "")
        if '"category"' not in raw_metadata or '"robinhood_stock"' not in raw_metadata:
            continue
        metadata = _json_object(raw_metadata)
        if str(metadata.get("category") or "").strip().lower() != "robinhood_stock":
            continue
        try:
            decimals = int(row.get("token_decimals") or 18)
        except (TypeError, ValueError):
            decimals = 18
        result[address] = {"symbol": symbol, "decimals": decimals}
    return result


def _static_token_symbol_map_from_db_rows(
    token_rows: list[dict[str, Any]],
) -> dict[str, str]:
    """Return static chain-token symbols without dynamic on-chain lookups."""
    result: dict[str, str] = {}
    for row in token_rows:
        if not isinstance(row, dict):
            continue
        address = str(row.get("token_address") or "").strip().lower()
        symbol = str(row.get("token_symbol") or "").strip().upper()
        if not address or not symbol or symbol.startswith("0X"):
            continue
        result[address] = symbol
    return result


def _robinhood_v4_sqlite_payload(
    database_url: str | None = None,
    *,
    network: str = "robinhood",
    chain_id: int = 4663,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return static pools with current liquidity plus raw liquidity rows."""
    from core.rs_bridge import load_v4_pool_liquidity_rows_for_view

    pool_rows = _robinhood_v4_static_pool_rows(
        database_url,
        network=network,
        chain_id=chain_id,
    )
    liquidity_rows = load_v4_pool_liquidity_rows_for_view(
        network,
        chain_id,
        "uniswap_v4",
        database_url=database_url,
    )
    return (
        _robinhood_v4_pool_rows_with_liquidity(pool_rows, liquidity_rows),
        liquidity_rows,
    )


def _robinhood_v4_sqlite_loader(
    database_url: str | None = None,
    *,
    network: str = "robinhood",
    chain_id: int = 4663,
) -> list[dict[str, Any]]:
    """Read dynamic liquidity once and reuse the cached static pool rows."""
    pools, _ = _robinhood_v4_sqlite_payload(
        database_url,
        network=network,
        chain_id=chain_id,
    )
    return pools


def _robinhood_v4_token_symbol(
    address: str,
    fallback: str = "",
    contracts: dict[str, Any] | None = None,
    stock_by_address: dict[str, dict[str, Any]] | None = None,
    symbols_by_address: dict[str, str] | None = None,
) -> str:
    normalized = str(address or "").strip().lower()
    if normalized == "0x0000000000000000000000000000000000000000":
        return "ETH"
    stock_meta = (stock_by_address or {}).get(normalized)
    if stock_meta:
        stock_symbol = str(stock_meta.get("symbol") or "").strip().upper()
        if stock_symbol:
            return stock_symbol
    if contracts is None:
        try:
            row = chain_data_by_network("robinhood") or {}
            contracts = row.get("contracts")
        except Exception:
            contracts = {}
        contracts = contracts if isinstance(contracts, dict) else {}
    else:
        contracts = contracts if isinstance(contracts, dict) else {}
    if normalized == str(contracts.get("wrapped_native") or "").strip().lower():
        return "WETH"
    if normalized == str(contracts.get("usdg") or "").strip().lower():
        return "USDG"
    static_symbol = (symbols_by_address or {}).get(normalized)
    if static_symbol:
        return static_symbol
    fallback_symbol = str(fallback or "").strip()
    if fallback_symbol:
        return fallback_symbol.upper()
    if normalized.startswith("0x"):
        return normalized[:10].upper()
    return str(address or "").upper()


def _mainnet_v4_anchor_assets() -> list[dict[str, Any]]:
    """Return configured Robinhood mainnet V4 borrow/anchor assets from SQLite config."""
    assets = []
    seen: set[str] = set()
    for symbol in MAINNET_ANCHOR_SYMBOLS:
        address = str(MAINNET_ANCHOR_ADDRESS_BY_SYMBOL.get(symbol) or "").strip().lower()
        if not address or address in seen:
            continue
        stable = symbol in {"USDG", "USDE"}
        decimals = 6 if symbol == "USDG" else 18
        assets.append(
            {
                "symbol": symbol,
                "address": address,
                "decimals": decimals,
                "stable": stable,
                "source": "sqlite_db_chain_config",
            }
        )
        seen.add(address)
    return assets


def _robinhood_v4_borrow_asset_rows(
    pools: list[dict[str, Any]],
    token_rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int]:
    """Collapse V4 pools into configured anchors plus official stock rows."""
    if not token_rows:
        raise ValueError("Robinhood mainnet V4 borrow assets require SQLite chain_tokens rows")
    try:
        chain_row = chain_data_by_network("robinhood") or {}
        contracts = chain_row.get("contracts")
        contracts = contracts if isinstance(contracts, dict) else {}
    except Exception:
        contracts = {}
    anchors = _mainnet_v4_anchor_assets()
    stock_metadata_by_address = _stock_metadata_from_db_rows(token_rows)
    stock_registry_by_address = stock_metadata_by_address
    symbol_map_by_address = _static_token_symbol_map_from_db_rows(token_rows)
    stock_assets = []
    for address, meta in stock_metadata_by_address.items():
        symbol = str(meta.get("symbol") or "").strip().upper()
        if not symbol:
            continue
        try:
            decimals = int(meta.get("decimals") or 18)
        except (TypeError, ValueError):
            decimals = 18
        stock_assets.append(
            {
                "symbol": symbol,
                "address": address,
                "decimals": decimals,
                "stable": False,
                "source": "robinhood_stock_token_registry",
            }
        )
    stock_assets.sort(key=lambda item: str(item["symbol"]))
    anchor_symbol_by_address = {
        anchor["address"].lower(): anchor["symbol"]
        for anchor in anchors
        if str(anchor.get("address") or "").strip()
    }
    anchor_symbol_by_address.update(
        {
            asset["address"].lower(): asset["symbol"]
            for asset in stock_assets
            if str(asset.get("address") or "").strip()
        }
    )
    configured_scope = set(anchor_symbol_by_address)
    anchor_rows: dict[str, dict[str, Any]] = {}
    for asset in [*anchors, *stock_assets]:
        if asset["symbol"] in anchor_rows:
            continue
        anchor_rows[asset["symbol"]] = _asset_row(
            {
                "token_symbol": asset["symbol"],
                "binance_symbol": asset["symbol"],
                "is_stable": bool(asset["stable"]),
                "anchor_symbol": asset["symbol"],
                "paired_symbol": asset["symbol"],
                "depth_score_usd": 0.0,
                "available_liquidity": 0,
                "pool_count": 0,
                "token_address": asset["address"],
                "pool_manager_address": "",
                "fee": "",
                "dex": "uniswap_v4",
                "flash_capable": True,
                "flash_borrowable": False,
                "borrowable": False,
                "pool_role": "configured_anchor_or_stock_v4_borrow_token",
                "asset_source": asset["source"],
                "decimals": asset["decimals"],
                "observed_block": None,
                "pool_ids": [],
                "paired_symbols": [],
            }
        )
    total_pool_count = 0
    seen_pool_ids: set[str] = set()
    for pool in pools:
        if not isinstance(pool, dict):
            continue
        pool_key = pool.get("poolKey")
        pool_key = pool_key if isinstance(pool_key, dict) else {}
        token0_address = str(pool.get("token0") or pool_key.get("token0") or pool_key.get("currency0") or "").strip().lower()
        token1_address = str(pool.get("token1") or pool_key.get("token1") or pool_key.get("currency1") or "").strip().lower()
        if token0_address not in configured_scope and token1_address not in configured_scope:
            continue
        pool_id = str(pool.get("poolId") or pool.get("pool") or pool.get("pool_id") or "").strip()
        matched_anchor_symbols = {
            symbol
            for symbol in (
                anchor_symbol_by_address.get(token0_address, ""),
                anchor_symbol_by_address.get(token1_address, ""),
            )
            if symbol
        }
        if pool_id:
            first_time = pool_id not in seen_pool_ids
            seen_pool_ids.add(pool_id)
        else:
            pool_identity = f"{token0_address}:{token1_address}:{pool_key.get('fee') or pool.get('fee') or ''}"
            first_time = pool_identity not in seen_pool_ids
            seen_pool_ids.add(pool_identity)
        if first_time:
            total_pool_count += 1
        token0_symbol = anchor_symbol_by_address.get(token0_address) or _robinhood_v4_token_symbol(
            token0_address,
            pool.get("token0Symbol"),
            contracts=contracts,
            stock_by_address=stock_registry_by_address,
            symbols_by_address=symbol_map_by_address,
        )
        token1_symbol = anchor_symbol_by_address.get(token1_address) or _robinhood_v4_token_symbol(
            token1_address,
            pool.get("token1Symbol"),
            contracts=contracts,
            stock_by_address=stock_registry_by_address,
            symbols_by_address=symbol_map_by_address,
        )
        for symbol in matched_anchor_symbols:
            row = anchor_rows.get(symbol)
            if row is None:
                continue
            row["pool_count"] = int(row.get("pool_count") or 0) + 1
            if pool_id:
                pool_ids = row.setdefault("pool_ids", [])
                if len(pool_ids) < 16 and pool_id not in pool_ids:
                    pool_ids.append(pool_id)
            for address, pool_symbol in (
                (token0_address, token0_symbol),
                (token1_address, token1_symbol),
            ):
                if not pool_symbol or pool_symbol.upper() == symbol.upper():
                    continue
                paired_symbols = row.setdefault("paired_symbols", [])
                if pool_symbol.upper() not in paired_symbols:
                    paired_symbols.append(pool_symbol.upper())
    assets = []
    seen_asset_symbols: set[str] = set()
    for asset in [*anchors, *stock_assets]:
        symbol = asset["symbol"]
        row = anchor_rows.get(symbol)
        if symbol in seen_asset_symbols or row is None:
            continue
        if str(asset.get("source") or "").startswith("robinhood_stock") and int(row.get("pool_count") or 0) <= 0:
            continue
        seen_asset_symbols.add(symbol)
        assets.append(row)
    for asset in assets:
        asset["available_liquidity"] = float(asset.get("pool_count") or 0)
        asset["reserve_data_liquidity"] = 0
        is_stock = str(asset.get("asset_source") or "").startswith("robinhood_stock")
        asset["source"] = (
            "robinhood_mainnet_v4_sqlite_configured_anchor_token"
            if not is_stock
            else "robinhood_mainnet_v4_sqlite_stock_token"
        )
        asset["depth_status"] = (
            "initialized_unpriced_anchor_pool"
            if not is_stock
            else "initialized_unpriced_stock_pool"
        )
        asset.pop("asset_source", None)
        paired_symbols = asset.pop("paired_symbols", [])
        asset["paired_symbol_count"] = len(paired_symbols) if isinstance(paired_symbols, list) else int(paired_symbols or 0)
        if not _as_float(asset.get("depth_score_usd")):
            asset["depth_level"] = "observable"
            asset["depth_label"] = "可观察"
            asset["depth_class"] = "ok"
    return assets, total_pool_count


def _robinhood_v4_borrow_asset_rows_from_pair_counts(
    pair_rows: list[dict[str, Any]],
    token_rows: list[dict[str, Any]],
    *,
    pool_manager: str = "",
) -> tuple[list[dict[str, Any]], int]:
    """Build the same page assets from SQLite pair-count rows."""
    if not token_rows:
        raise ValueError("Robinhood mainnet V4 borrow assets require SQLite chain_tokens rows")
    try:
        chain_row = chain_data_by_network("robinhood") or {}
        contracts = chain_row.get("contracts")
        contracts = contracts if isinstance(contracts, dict) else {}
    except Exception:
        contracts = {}
    anchors = _mainnet_v4_anchor_assets()
    stock_metadata_by_address = _stock_metadata_from_db_rows(token_rows)
    symbol_map_by_address = _static_token_symbol_map_from_db_rows(token_rows)
    stock_assets = []
    for address, meta in stock_metadata_by_address.items():
        symbol = str(meta.get("symbol") or "").strip().upper()
        if not symbol:
            continue
        try:
            decimals = int(meta.get("decimals") or 18)
        except (TypeError, ValueError):
            decimals = 18
        stock_assets.append(
            {
                "symbol": symbol,
                "address": address,
                "decimals": decimals,
                "stable": False,
                "source": "robinhood_stock_token_registry",
            }
        )
    stock_assets.sort(key=lambda item: str(item["symbol"]))
    anchor_symbol_by_address = {
        anchor["address"].lower(): anchor["symbol"]
        for anchor in anchors
        if str(anchor.get("address") or "").strip()
    }
    anchor_symbol_by_address.update(
        {
            asset["address"].lower(): asset["symbol"]
            for asset in stock_assets
            if str(asset.get("address") or "").strip()
        }
    )
    configured_scope = set(anchor_symbol_by_address)
    anchor_rows: dict[str, dict[str, Any]] = {}
    for asset in [*anchors, *stock_assets]:
        if asset["symbol"] in anchor_rows:
            continue
        anchor_rows[asset["symbol"]] = _asset_row(
            {
                "token_symbol": asset["symbol"],
                "binance_symbol": asset["symbol"],
                "is_stable": bool(asset["stable"]),
                "anchor_symbol": asset["symbol"],
                "paired_symbol": asset["symbol"],
                "depth_score_usd": 0.0,
                "available_liquidity": 0,
                "pool_count": 0,
                "token_address": asset["address"],
                "pool_manager_address": pool_manager,
                "fee": "",
                "dex": "uniswap_v4",
                "flash_capable": True,
                "flash_borrowable": False,
                "borrowable": False,
                "pool_role": "configured_anchor_or_stock_v4_borrow_token",
                "asset_source": asset["source"],
                "decimals": asset["decimals"],
                "observed_block": None,
                "pool_ids": [],
                "paired_symbols": [],
            }
        )
    total_pool_count = 0
    for pair in pair_rows:
        if not isinstance(pair, dict):
            continue
        token0_address = str(pair.get("token0") or "").strip().lower()
        token1_address = str(pair.get("token1") or "").strip().lower()
        if token0_address not in configured_scope and token1_address not in configured_scope:
            continue
        raw_pool_count = pair.get("pool_count")
        if raw_pool_count is None:
            raw_pool_count = pair.get("poolCount")
        try:
            pool_count = max(0, int(raw_pool_count or 0))
        except (TypeError, ValueError):
            pool_count = 0
        if pool_count <= 0:
            continue
        matched_anchor_symbols = {
            symbol
            for symbol in (
                anchor_symbol_by_address.get(token0_address, ""),
                anchor_symbol_by_address.get(token1_address, ""),
            )
            if symbol
        }
        token0_symbol = anchor_symbol_by_address.get(token0_address) or _robinhood_v4_token_symbol(
            token0_address,
            "",
            contracts=contracts,
            stock_by_address=stock_metadata_by_address,
            symbols_by_address=symbol_map_by_address,
        )
        token1_symbol = anchor_symbol_by_address.get(token1_address) or _robinhood_v4_token_symbol(
            token1_address,
            "",
            contracts=contracts,
            stock_by_address=stock_metadata_by_address,
            symbols_by_address=symbol_map_by_address,
        )
        for symbol in matched_anchor_symbols:
            row = anchor_rows.get(symbol)
            if row is None:
                continue
            row["pool_count"] = int(row.get("pool_count") or 0) + pool_count
            for address, pool_symbol in (
                (token0_address, token0_symbol),
                (token1_address, token1_symbol),
            ):
                if not pool_symbol or pool_symbol.upper() == symbol.upper():
                    continue
                paired_symbols = row.setdefault("paired_symbols", [])
                if pool_symbol.upper() not in paired_symbols:
                    paired_symbols.append(pool_symbol.upper())
        total_pool_count += pool_count
    assets = []
    seen_asset_symbols: set[str] = set()
    for asset in [*anchors, *stock_assets]:
        symbol = asset["symbol"]
        row = anchor_rows.get(symbol)
        if symbol in seen_asset_symbols or row is None:
            continue
        if str(asset.get("source") or "").startswith("robinhood_stock") and int(row.get("pool_count") or 0) <= 0:
            continue
        seen_asset_symbols.add(symbol)
        assets.append(row)
    for asset in assets:
        asset["available_liquidity"] = float(asset.get("pool_count") or 0)
        asset["reserve_data_liquidity"] = 0
        is_stock = str(asset.get("asset_source") or "").startswith("robinhood_stock")
        asset["source"] = (
            "robinhood_mainnet_v4_sqlite_configured_anchor_token"
            if not is_stock
            else "robinhood_mainnet_v4_sqlite_stock_token"
        )
        asset["depth_status"] = (
            "initialized_unpriced_anchor_pool"
            if not is_stock
            else "initialized_unpriced_stock_pool"
        )
        asset.pop("asset_source", None)
        paired_symbols = asset.pop("paired_symbols", [])
        asset["paired_symbol_count"] = len(paired_symbols) if isinstance(paired_symbols, list) else int(paired_symbols or 0)
        if not _as_float(asset.get("depth_score_usd")):
            asset["depth_level"] = "observable"
            asset["depth_label"] = "可观察"
            asset["depth_class"] = "ok"
    return assets, total_pool_count


def _robinhood_v4_sort_key(asset: dict[str, Any]) -> tuple[bool, str]:
    symbol = str(asset.get("token_symbol") or "").strip().upper()
    has_eth = symbol == "ETH" or symbol.startswith("ETH:")
    return (not has_eth, symbol)


def _robinhood_v4_protocol_view(
    protocol: str,
    *,
    database_url: str | None = None,
    **_: Any,
) -> dict[str, Any]:
    protocol_key = str(protocol or "").strip().lower()
    cache_key = (_sqlite_fingerprint(database_url), "robinhood", 4663, protocol_key)
    now = time.time()
    with _robinhood_v4_protocol_view_cache_lock:
        cached_at, cached_view = _robinhood_v4_protocol_view_cache.get(cache_key, (None, None))
        if (
            cached_at is not None
            and now - cached_at < _ROBINHOOD_V4_PROTOCOL_VIEW_CACHE_TTL_SECONDS
            and isinstance(cached_view, dict)
            and "status" not in cached_view
        ):
            return copy.deepcopy(cached_view)
    view = _robinhood_v4_protocol_view_from_sqlite(protocol, database_url=database_url)
    if isinstance(view, dict) and "status" not in view:
        with _robinhood_v4_protocol_view_cache_lock:
            _robinhood_v4_protocol_view_cache[cache_key] = (now, view)
    return view


def _robinhood_v4_protocol_view_from_sqlite(
    protocol: str,
    *,
    database_url: str | None = None,
) -> dict[str, Any]:
    try:
        from core.rs_bridge import (
            load_v4_exchange_pool_manager,
            load_v4_exchange_pool_pair_counts,
            load_v4_pool_liquidity_refreshed_at,
        )
        from core.db.database_path import absolute_database_path
        from core.db.storage_chain_core import load_chain_tokens

        pair_rows = load_v4_exchange_pool_pair_counts(
            "robinhood",
            4663,
            "uniswap_v4",
            database_url=database_url,
        )
        refreshed_at = load_v4_pool_liquidity_refreshed_at(
            "robinhood",
            4663,
            "uniswap_v4",
            database_url=database_url,
        )
        token_rows = load_chain_tokens(
            str(absolute_database_path(database_url)),
            network="robinhood",
            chain_id=4663,
        )
        manager_row = load_v4_exchange_pool_manager(
            "robinhood",
            4663,
            "uniswap_v4",
            database_url=database_url,
        )
    except Exception as exc:
        view = _empty_protocol_view(protocol, status="protocol_pool_cache_not_available")
        view["status_message"] = f"Robinhood {_protocol_display_name(protocol)} SQLite read failed: {str(exc)[:160]}"
        return view
    if not pair_rows:
        view = _empty_protocol_view(protocol, status="protocol_pool_cache_not_available")
        view["status_message"] = f"Robinhood {_protocol_display_name(protocol)} SQLite pool evidence is empty"
        return view
    block_number = None
    refreshed_at = None
    schema_version = "robinhood-mainnet-v4-sqlite-v1"
    pool_manager = ""
    if isinstance(manager_row, dict):
        pool_manager = manager_row.get("pool_manager") or manager_row.get("poolManager") or ""
    if not pool_manager:
        pool_manager = "0x8366a39CC670B4001A1121B8F6A443A643e40951"
    source = "robinhood_mainnet_v4_sqlite"
    if not token_rows:
        view = _empty_protocol_view(protocol, status="protocol_pool_cache_not_available")
        view["status_message"] = "Robinhood Uniswap V4 SQLite chain_tokens rows are empty"
        return view
    assets, total_pool_count = _robinhood_v4_borrow_asset_rows_from_pair_counts(
        pair_rows,
        token_rows=token_rows,
        pool_manager=pool_manager,
    )
    stable_assets = [asset for asset in assets if asset.get("is_stable")]
    non_stable_assets = [asset for asset in assets if not asset.get("is_stable")]
    stable_assets.sort(key=_robinhood_v4_sort_key)
    non_stable_assets.sort(key=_robinhood_v4_sort_key)
    return {
        "protocol": protocol,
        "display_name": f"Robinhood {_protocol_display_name(protocol)}",
        "active": True,
        "display_mode": "observable_dex_pool",
        "pool_role_label": "V4 PoolKey 池",
        "status_note": "Robinhood Uniswap V4 pools are read from the SQLite business store.",
        "cache": {
            "schema_version": schema_version,
            "chain_id": 4663,
            "block_number": block_number,
            "refreshed_at": refreshed_at,
            "pool_address": "",
            "identities": _cache_identities(
                _cache_identity("pool_manager", pool_manager, label="PoolManager"),
            ),
            "capabilities": _pool_view_capabilities(
                concrete_pool_count=len(assets),
                flash_capable=True,
                observable=True,
            ),
            "rpc_url": "",
            "asset_count": len(assets),
            "source": source,
        },
        "summary": {
            "stable_borrow_pool_count": len(stable_assets),
            "non_stable_borrow_pool_count": len(non_stable_assets),
            "observed_borrow_token_count": len(assets),
            "borrow_target_count": 0,
            "borrow_asset_count": len(assets),
            "observable_pool_count": len(assets),
            "stable_observable_pool_count": len(stable_assets),
            "non_stable_observable_pool_count": len(non_stable_assets),
            "flash_capable_pool_count": total_pool_count,
            "v4_pool_key_count": total_pool_count,
            "v4_decoded_pool_key_count": total_pool_count,
            "flashloan_borrowable_pool_count": 0,
            "flashloan_unverified_pool_count": len(assets),
            "depth_counts": _depth_counts(assets),
        },
        "stable_borrow_pools": stable_assets,
        "non_stable_borrow_pools": non_stable_assets,
        "borrow_target_universe": None,
    }


def _robinhood_dex_protocol_view(
    protocol: str,
    *,
    evidence_root: Any = None,
    **_: Any,
) -> dict[str, Any]:
    """Return a SQLite-only view for legacy non-V4 Robinhood DEX protocols."""
    view = _empty_protocol_view(protocol, status="protocol_pool_cache_not_available")
    view["status_message"] = (
        "Robinhood DEX pool view only reads SQLite; "
        f"{_protocol_display_name(protocol)} has no SQLite pool evidence."
    )
    cache = view.get("cache")
    if isinstance(cache, dict):
        cache["source"] = "sqlite_db"
    return view


def _flash_source_pool_row(source: dict[str, Any]) -> dict[str, Any]:
    protocol = str(source.get("protocol") or "").strip().lower()
    token_symbol = str(source.get("token_symbol") or "").strip()
    source_kind = str(source.get("source_kind") or "").strip().lower()
    row = _asset_row(
        {
            "token_symbol": token_symbol,
            "binance_symbol": token_symbol.replace(":", "/"),
            "is_stable": bool(source.get("is_stable")) or _is_stable_symbol(token_symbol),
            "depth_score_usd": source.get("depth_score_usd") or 0,
            "available_liquidity": source.get("available_liquidity") or 0,
            "reserve_data_liquidity": source.get("reserve0") or source.get("reserve_data_liquidity") or 0,
            "a_token_total_supply": source.get("reserve1") or source.get("available_liquidity") or 0,
            "oracle_price": "",
            "token_address": source.get("token_address") or "",
            "pool_address": source.get("provider_address") if source_kind == "flash_swap" else "",
            "provider_address": source.get("provider_address") or "",
            "provider": source.get("provider") or "",
            "fee": source.get("fee_bps"),
            "dex": protocol,
            "quality": "borrowable" if source.get("flash_borrowable") else "unavailable",
            "source_kind": source_kind,
            "flash_capable": bool(source.get("flash_capable")),
            "flash_borrowable": bool(source.get("flash_borrowable")),
            "borrowable": bool(source.get("borrowable")),
            "repayment_model": source.get("repayment_model") or "",
            "adapter_hint": source.get("adapter_hint") or "",
        }
    )
    if source_kind == "flash_swap":
        token0 = str(source.get("token0_symbol") or "").strip()
        token1 = str(source.get("token1_symbol") or "").strip()
        row["is_stable"] = _is_stable_symbol(token0) or _is_stable_symbol(token1)
        row["token0Symbol"] = token0
        row["token1Symbol"] = token1
        row["token0_address"] = source.get("token0_address") or ""
        row["token1_address"] = source.get("token1_address") or ""
    return row


def _robinhood_flash_borrow_rows(protocol: str, sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for source in sources:
        if source.get("source_kind") == "flash_swap":
            for symbol_key, address_key, reserve_key in (
                ("token0_symbol", "token0_address", "reserve0"),
                ("token1_symbol", "token1_address", "reserve1"),
            ):
                symbol = str(source.get(symbol_key) or "").strip()
                if not symbol:
                    continue
                rows.append(
                    {
                        "borrow_symbol": symbol,
                        "target_count": 1 if _as_float(source.get(reserve_key)) > 0 else 0,
                        "target_symbols": [str(source.get("token_symbol") or "")],
                        "token_address": source.get(address_key) or "",
                        "provider_address": source.get("provider_address") or "",
                        "source_kind": source.get("source_kind") or "",
                    }
                )
            continue
        rows.append(
            {
                "borrow_symbol": source.get("token_symbol") or "",
                "target_count": 1 if source.get("flash_borrowable") else 0,
                "target_symbols": [source.get("display_name") or _protocol_display_name(protocol)],
                "token_address": source.get("token_address") or "",
                "provider_address": source.get("provider_address") or "",
                "source_kind": source.get("source_kind") or "",
            }
        )
    return rows


def _robinhood_flash_protocol_view(
    protocol: str,
    *,
    snapshot_loader: Callable[[], dict[str, Any]] | None = None,
    database_url: str | None = None,
) -> dict[str, Any]:
    if snapshot_loader is None:
        def loader() -> dict[str, Any]:
            from dex.intent_trade.chains.robinhood.flash_liquidity import (
                robinhood_flash_liquidity_snapshot_from_sqlite,
            )

            return robinhood_flash_liquidity_snapshot_from_sqlite(database_url=database_url)
    else:
        loader = snapshot_loader
    try:
        snapshot = loader()
    except Exception as exc:
        view = _empty_protocol_view(protocol, status="protocol_pool_cache_not_available")
        view["status_message"] = f"Robinhood {_protocol_display_name(protocol)} 闪电流动性读取失败：{str(exc)[:160]}"
        return view
    if not _is_robinhood_mainnet_scope(snapshot):
        view = _empty_protocol_view(protocol, status="protocol_pool_cache_not_available")
        view["status_message"] = "Robinhood flash liquidity snapshot is not scoped to the Robinhood mainnet chain"
        return view
    sources = [
        source
        for source in snapshot.get("sources") or []
        if isinstance(source, dict)
        and str(source.get("protocol") or "").strip().lower() == protocol
    ]
    if not sources:
        view = _empty_protocol_view(protocol, status="protocol_pool_cache_not_available")
        view["status_message"] = f"Robinhood {_protocol_display_name(protocol)} 暂无可展示闪电资金来源"
        view["errors"] = snapshot.get("errors") or []
        return view
    assets = [_flash_source_pool_row(source) for source in sources]
    stable_assets = [asset for asset in assets if asset.get("is_stable")]
    non_stable_assets = [asset for asset in assets if not asset.get("is_stable")]
    stable_assets.sort(key=lambda item: float(item.get("depth_score_usd") or 0), reverse=True)
    non_stable_assets.sort(key=lambda item: float(item.get("depth_score_usd") or 0), reverse=True)
    borrow_rows = _robinhood_flash_borrow_rows(protocol, sources)
    flash_borrowable_count = sum(1 for asset in assets if asset.get("flash_borrowable"))
    display_mode = "flash_swap_source" if any(source.get("source_kind") == "flash_swap" for source in sources) else "flash_loan_source"
    morpho_core = snapshot.get("morphoBlueCore")
    uniswap_factory = snapshot.get("uniswapV2Factory")
    identities = (
        _cache_identities(_cache_identity("core", morpho_core, label="Morpho Blue Core"))
        if protocol == "morpho_blue"
        else _cache_identities(_cache_identity("factory", uniswap_factory, label="Factory"))
    )
    return {
        "protocol": protocol,
        "display_name": f"Robinhood {_protocol_display_name(protocol)}",
        "active": True,
        "display_mode": display_mode,
        "pool_role_label": "Flash Swap 池" if display_mode == "flash_swap_source" else "Flash Loan 池",
        "status_note": "数据来自 rh-flash-toolkit 建模的只读链上读取；当前仍保持观察模式，不代表本项目执行器已开放广播。",
        "cache": {
            "schema_version": "robinhood-flash-liquidity-v1",
            "chain_id": snapshot.get("chainId"),
            "block_number": snapshot.get("blockNumber"),
            "refreshed_at": snapshot.get("generatedAt"),
            "pool_address": "",
            "identities": identities,
            "capabilities": _pool_view_capabilities(
                concrete_pool_count=len(assets),
                borrowable=flash_borrowable_count > 0,
                flash_capable=True,
                priced_depth=any(float(asset.get("depth_score_usd") or 0) > 0 for asset in assets),
                observable=True,
            ),
            "rpc_url": snapshot.get("rpcUrl") or "",
            "asset_count": len(assets),
            "source": ", ".join(snapshot.get("evidence") or []),
        },
        "summary": {
            "stable_borrow_pool_count": len(stable_assets),
            "non_stable_borrow_pool_count": len(non_stable_assets),
            "borrow_target_count": sum(1 for row in borrow_rows if int(row.get("target_count") or 0) > 0),
            "borrow_asset_count": len(borrow_rows),
            "flash_capable_pool_count": len(assets),
            "flashloan_borrowable_pool_count": flash_borrowable_count,
            "flashloan_unverified_pool_count": 0,
            "depth_counts": _depth_counts(assets),
        },
        "stable_borrow_pools": stable_assets,
        "non_stable_borrow_pools": non_stable_assets,
        "borrow_target_universe": {
            "borrow_count": len(borrow_rows),
            "target_count": sum(1 for row in borrow_rows if int(row.get("target_count") or 0) > 0),
            "target_source": "rh-flash-toolkit readonly snapshot",
            "borrow_rows": borrow_rows,
        },
        "errors": snapshot.get("errors") or [],
    }


robinhood_dex_protocol_view = _robinhood_dex_protocol_view
robinhood_flash_protocol_view = _robinhood_flash_protocol_view
robinhood_v4_protocol_view = _robinhood_v4_protocol_view

__all__ = ["robinhood_dex_protocol_view", "robinhood_flash_protocol_view", "robinhood_v4_protocol_view"]
