from __future__ import annotations

from pathlib import Path
from typing import Any

from intent_trade.flows.flash_loan_pools.presenter import (
    _as_float,
    _asset_row,
    _cache_identities,
    _cache_identity,
    _configured_data_path,
    _depth_counts,
    _dex_pool_row,
    _empty_protocol_view,
    _is_stable_symbol,
    _pool_view_capabilities,
    _protocol_display_name,
    _unit_amount,
)
from web.control_panel_data_cache import read_json


APP_ROOT = Path(__file__).resolve().parents[3]
REPO_ROOT = APP_ROOT.parents[1]
DEFAULT_BNB_DEX_POOL_STATIC_TABLE_PATH = APP_ROOT / "runtime" / "cache" / "bnb_arbitrage_pool_static_table.json"
DEFAULT_BNB_VENUS_MARKET_STATIC_TABLE_PATH = REPO_ROOT / "data_info" / "BNB" / "liquidation" / "venus_market_static_table.json"


def _first_value(rows: list[dict[str, Any]], key: str) -> Any:
    for row in rows:
        value = row.get(key)
        if value:
            return value
    return ""


def _venus_market_row(market: dict[str, Any]) -> dict[str, Any]:
    symbol = str(market.get("underlying_symbol") or market.get("vtoken_symbol") or "").strip()
    decimals = market.get("underlying_decimals")
    cash_units = _unit_amount(market.get("cash"), decimals)
    borrow_units = _unit_amount(market.get("total_borrows"), decimals)
    supply_units = _unit_amount(market.get("total_supply"), market.get("vtoken_decimals"))
    price = _as_float(market.get("oracle_price_usd") or market.get("oracle_price"))
    depth_score_usd = cash_units * price
    return _asset_row(
        {
            "token_symbol": symbol,
            "binance_symbol": f"{symbol.upper()}USDT" if symbol else "",
            "is_stable": bool(market.get("stable")) or _is_stable_symbol(symbol),
            "depth_score_usd": depth_score_usd,
            "available_liquidity": cash_units,
            "reserve_data_liquidity": borrow_units,
            "a_token_total_supply": supply_units,
            "oracle_price": price,
            "token_address": market.get("underlying_address") or "",
            "pool_address": market.get("vtoken_address") or "",
            "vtoken_symbol": market.get("vtoken_symbol") or "",
            "borrow_disabled": market.get("borrow_disabled"),
            "collateral_disabled": market.get("collateral_disabled"),
        }
    )


def _venus_protocol_view(
    *,
    chain_id: int | None,
    venus_market_path: Path | None = None,
) -> dict[str, Any]:
    path = _configured_data_path(
        "bnb",
        "venus_markets",
        venus_market_path or DEFAULT_BNB_VENUS_MARKET_STATIC_TABLE_PATH,
        DEFAULT_BNB_VENUS_MARKET_STATIC_TABLE_PATH,
    )
    cache = read_json(path)
    if not cache:
        view = _empty_protocol_view("venus_core", status="protocol_pool_cache_not_available")
        view["status_message"] = "Venus Core 市场静态表尚未生成"
        return view
    cache_chain_id = cache.get("chain_id") or cache.get("chainId")
    if chain_id is not None and cache_chain_id is not None and int(cache_chain_id) != int(chain_id):
        view = _empty_protocol_view("venus_core", status="protocol_pool_cache_not_available")
        view["status_message"] = "当前链的 Venus Core 市场静态表尚未接入"
        return view
    assets = [
        _venus_market_row(market)
        for market in cache.get("markets") or []
        if isinstance(market, dict) and market.get("is_listed") is not False
    ]
    stable_assets = [asset for asset in assets if asset.get("is_stable")]
    non_stable_assets = [asset for asset in assets if not asset.get("is_stable")]
    stable_assets.sort(key=lambda item: float(item.get("depth_score_usd") or 0), reverse=True)
    non_stable_assets.sort(key=lambda item: float(item.get("depth_score_usd") or 0), reverse=True)
    comptroller_address = cache.get("comptroller_address")
    return {
        "protocol": "venus_core",
        "display_name": "Venus Core",
        "active": True,
        "cache": {
            "schema_version": None,
            "chain_id": cache_chain_id,
            "block_number": cache.get("block_number"),
            "refreshed_at": cache.get("generated_at"),
            "pool_address": "",
            "identities": _cache_identities(
                _cache_identity("comptroller", comptroller_address, label="Comptroller"),
            ),
            "capabilities": _pool_view_capabilities(
                concrete_pool_count=len(assets),
                borrowable=True,
                priced_depth=any(float(asset.get("depth_score_usd") or 0) > 0 for asset in assets),
                observable=True,
            ),
            "rpc_url": cache.get("rpc_url"),
            "asset_count": len(assets),
            "source": str(path),
        },
        "summary": {
            "stable_borrow_pool_count": len(stable_assets),
            "non_stable_borrow_pool_count": len(non_stable_assets),
            "borrow_target_count": 0,
            "borrow_asset_count": len(assets),
            "depth_counts": _depth_counts(assets),
        },
        "stable_borrow_pools": stable_assets,
        "non_stable_borrow_pools": non_stable_assets,
        "borrow_target_universe": None,
    }

def _bnb_dex_protocol_view(
    protocol: str,
    *,
    chain_id: int | None,
    dex_pool_static_path: Path | None = None,
) -> dict[str, Any]:
    path = _configured_data_path(
        "bnb",
        "dex_pools",
        dex_pool_static_path or DEFAULT_BNB_DEX_POOL_STATIC_TABLE_PATH,
        DEFAULT_BNB_DEX_POOL_STATIC_TABLE_PATH,
    )
    cache = read_json(path)
    if not cache:
        return _empty_protocol_view(protocol, status="protocol_pool_cache_not_available")
    cache_chain_id = cache.get("chainId") or cache.get("chain_id")
    if chain_id is not None and cache_chain_id is not None and int(cache_chain_id) != int(chain_id):
        view = _empty_protocol_view(protocol, status="protocol_pool_cache_not_available")
        view["status_message"] = "当前链的 DEX 静态池表尚未接入"
        return view
    pools = [
        pool
        for pool in cache.get("pools") or []
        if isinstance(pool, dict) and str(pool.get("dex") or "").strip().lower() == protocol
    ]
    if not pools:
        view = _empty_protocol_view(protocol, status="protocol_pool_cache_not_available")
        view["status_message"] = f"{_protocol_display_name(protocol)} 暂无静态池缓存"
        return view
    assets = [_dex_pool_row(pool) for pool in pools]
    stable_assets = [asset for asset in assets if asset.get("is_stable")]
    non_stable_assets = [asset for asset in assets if not asset.get("is_stable")]
    stable_assets.sort(key=lambda item: float(item.get("depth_score_usd") or 0), reverse=True)
    non_stable_assets.sort(key=lambda item: float(item.get("depth_score_usd") or 0), reverse=True)
    recommended = sum(1 for pool in pools if str(pool.get("status") or "").lower() == "recommended")
    router_address = cache.get("router") or _first_value(pools, "router")
    factory_address = cache.get("factory") or _first_value(pools, "factory")
    return {
        "protocol": protocol,
        "display_name": _protocol_display_name(protocol),
        "active": True,
        "cache": {
            "schema_version": None,
            "chain_id": cache_chain_id,
            "block_number": cache.get("generatedBlockNumber"),
            "refreshed_at": cache.get("generatedAt"),
            "pool_address": "",
            "identities": _cache_identities(
                _cache_identity("router", router_address, label="Router"),
                _cache_identity("factory", factory_address, label="Factory"),
            ),
            "capabilities": _pool_view_capabilities(
                concrete_pool_count=len(assets),
                flash_capable=True,
                priced_depth=any(float(asset.get("depth_score_usd") or 0) > 0 for asset in assets),
                observable=True,
            ),
            "rpc_url": cache.get("rpcUrl"),
            "asset_count": len(assets),
            "source": str(path),
        },
        "summary": {
            "stable_borrow_pool_count": len(stable_assets),
            "non_stable_borrow_pool_count": len(non_stable_assets),
            "borrow_target_count": recommended,
            "borrow_asset_count": len(assets),
            "depth_counts": _depth_counts(assets),
        },
        "stable_borrow_pools": stable_assets,
        "non_stable_borrow_pools": non_stable_assets,
        "borrow_target_universe": {
            "borrow_count": len(assets),
            "target_count": recommended,
            "target_source": "bnb_arbitrage_pool_static_table",
            "borrow_rows": [
                {
                    "borrow_symbol": asset.get("token_symbol"),
                    "target_count": 1 if str(asset.get("quality") or "").lower() == "recommended" else 0,
                    "target_symbols": [asset.get("binance_symbol")] if asset.get("binance_symbol") else [],
                }
                for asset in assets[:80]
            ],
        },
    }


venus_protocol_view = _venus_protocol_view
bnb_dex_protocol_view = _bnb_dex_protocol_view

__all__ = ["bnb_dex_protocol_view", "venus_protocol_view"]
