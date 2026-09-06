from __future__ import annotations

from pathlib import Path
from typing import Any

from core.chain_data_registry import chain_data_file_path

STABLE_SYMBOLS = {
    "AUSD",
    "BUSD",
    "DAI",
    "DAI.E",
    "EURC",
    "FDUSD",
    "FRAX",
    "FRAX.E",
    "GHO",
    "MIM",
    "SUSDE",
    "TUSD",
    "USDC",
    "USDC.E",
    "USDD",
    "USDE",
    "USDG",
    "USDT",
    "USDT.E",
}
ROUTE_ONLY_PROTOCOLS = {"hyperliquid_native"}
CONCRETE_POOL_IDENTITY_KINDS = {"aave_pool", "pool", "pair", "market", "vtoken"}

def _configured_data_path(
    network: str,
    key: str,
    current: Path | None,
    fallback: Path,
) -> Path | None:
    if current is not None and Path(current) != fallback:
        return current
    try:
        return chain_data_file_path(network, key) or current
    except Exception:
        return current

def _depth_level(depth_score_usd: object) -> dict[str, str]:
    try:
        depth = float(depth_score_usd or 0)
    except (TypeError, ValueError):
        depth = 0.0
    if depth >= 1_000_000:
        return {"level": "deep", "label": "深", "class": "good"}
    if depth >= 100_000:
        return {"level": "medium", "label": "中", "class": "ok"}
    if depth >= 10_000:
        return {"level": "shallow", "label": "浅", "class": "warn"}
    if depth > 0:
        return {"level": "dust", "label": "极浅", "class": "warn"}
    return {"level": "unavailable", "label": "不可用", "class": "bad"}


def _asset_row(asset: dict[str, Any]) -> dict[str, Any]:
    depth = _depth_level(asset.get("depth_score_usd"))
    return {
        **asset,
        "depth_level": depth["level"],
        "depth_label": depth["label"],
        "depth_class": depth["class"],
        "liquidity_units": max(
            int(asset.get("available_liquidity") or 0),
            int(asset.get("reserve_data_liquidity") or 0),
            int(asset.get("a_token_total_supply") or 0),
        ),
    }


def _as_float(value: object, default: float = 0.0) -> float:
    try:
        parsed = float(value or 0)
    except (TypeError, ValueError):
        return default
    return parsed if parsed == parsed and parsed not in (float("inf"), float("-inf")) else default


def _unit_amount(raw: object, decimals: object) -> float:
    value = _as_float(raw)
    try:
        scale = 10 ** int(decimals or 0)
    except (TypeError, ValueError, OverflowError):
        scale = 1
    return value / scale if scale else value


def _is_stable_symbol(symbol: object) -> bool:
    normalized = str(symbol or "").strip().upper()
    bridge_base = normalized[:-2] if normalized.endswith(".E") else normalized
    return normalized in STABLE_SYMBOLS or bridge_base in STABLE_SYMBOLS or normalized.endswith("USD")


def _depth_counts(assets: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for asset in assets:
        counts[asset["depth_level"]] = counts.get(asset["depth_level"], 0) + 1
    return counts


def _cache_identity(
    kind: str,
    value: object,
    *,
    label: str | None = None,
    role: str | None = None,
    is_concrete_pool: bool | None = None,
) -> dict[str, Any] | None:
    text = str(value or "").strip()
    if not text:
        return None
    normalized_kind = str(kind or "contract").strip().lower()
    concrete = normalized_kind in CONCRETE_POOL_IDENTITY_KINDS if is_concrete_pool is None else bool(is_concrete_pool)
    return {
        "kind": normalized_kind,
        "label": label or normalized_kind.replace("_", " ").title(),
        "value": text,
        "address": text if text.startswith("0x") and len(text) == 42 else "",
        "role": role or ("concrete_pool" if concrete else "protocol_interface"),
        "is_concrete_pool": concrete,
    }


def _cache_identities(*items: dict[str, Any] | None) -> list[dict[str, Any]]:
    return [item for item in items if item]


def _pool_view_capabilities(
    *,
    concrete_pool_count: int = 0,
    borrowable: bool = False,
    flash_capable: bool = False,
    priced_depth: bool = False,
    observable: bool = False,
    execution_enabled: bool = False,
) -> dict[str, bool]:
    return {
        "has_concrete_pools": concrete_pool_count > 0,
        "borrowable": bool(borrowable),
        "flash_capable": bool(flash_capable),
        "priced_depth": bool(priced_depth),
        "observable": bool(observable),
        "execution_enabled": bool(execution_enabled),
    }


def _dex_pool_row(pool: dict[str, Any]) -> dict[str, Any]:
    token0 = str(pool.get("token0Symbol") or "").strip()
    token1 = str(pool.get("token1Symbol") or "").strip()
    pair = str(pool.get("symbolPair") or ":".join([token0, token1])).strip(":")
    tvl = _as_float(pool.get("tvlUsd"))
    is_stable_pair = _is_stable_symbol(token0) or _is_stable_symbol(token1)
    row = _asset_row(
        {
            "token_symbol": pair,
            "binance_symbol": pair.replace(":", "/"),
            "is_stable": is_stable_pair,
            "depth_score_usd": tvl,
            "available_liquidity": tvl,
            "reserve_data_liquidity": _as_float(pool.get("token0Usd")) + _as_float(pool.get("token1Usd")),
            "a_token_total_supply": _as_float(pool.get("liquidity")),
            "oracle_price": "",
            "token_address": pool.get("pool") or "",
            "pool_address": pool.get("pool") or "",
            "fee": pool.get("fee") or "",
            "dex": pool.get("dex") or "",
            "quality": pool.get("status") or pool.get("quality") or "",
            "token0Symbol": token0,
            "token1Symbol": token1,
        }
    )
    row["depth_label"] = row.get("depth_label") or ("可用" if tvl > 0 else "不可用")
    return row

def _pool_rows_protocol_view(
    protocol: str,
    *,
    cache: dict[str, Any],
    path: Path,
    rows: list[dict[str, Any]],
    target_source: str,
) -> dict[str, Any]:
    if not rows:
        view = _empty_protocol_view(protocol, status="protocol_pool_cache_not_available")
        view["status_message"] = f"{_protocol_display_name(protocol)} 暂无静态池缓存"
        return view
    assets = [_dex_pool_row(pool) for pool in rows]
    stable_assets = [asset for asset in assets if asset.get("is_stable")]
    non_stable_assets = [asset for asset in assets if not asset.get("is_stable")]
    stable_assets.sort(key=lambda item: float(item.get("depth_score_usd") or 0), reverse=True)
    non_stable_assets.sort(key=lambda item: float(item.get("depth_score_usd") or 0), reverse=True)
    recommended = sum(1 for pool in rows if str(pool.get("status") or pool.get("quality") or "").lower() == "recommended")
    return {
        "protocol": protocol,
        "display_name": _protocol_display_name(protocol),
        "active": True,
        "cache": {
            "schema_version": cache.get("schemaVersion") or cache.get("schema_version"),
            "chain_id": cache.get("chainId") or cache.get("chain_id"),
            "block_number": cache.get("generatedBlockNumber") or cache.get("block_number"),
            "refreshed_at": cache.get("generatedAt") or cache.get("fetched_at"),
            "pool_address": "",
            "identities": _cache_identities(
                _cache_identity("router", cache.get("router"), label="Router"),
                _cache_identity("factory", cache.get("factory"), label="Factory"),
            ),
            "capabilities": _pool_view_capabilities(
                concrete_pool_count=len(assets),
                flash_capable=True,
                priced_depth=any(_as_float(asset.get("depth_score_usd")) > 0 for asset in assets),
                observable=True,
            ),
            "rpc_url": cache.get("rpcUrl") or cache.get("rpc_url"),
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
            "target_source": target_source,
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

def _empty_protocol_view(protocol: str, *, status: str = "protocol_pool_cache_not_available") -> dict[str, Any]:
    messages = {
        "chain_protocol_not_supported": "当前链不支持该闪电贷协议",
        "protocol_pool_cache_not_available": "该协议闪电贷可用资金池缓存尚未接入",
    }
    return {
        "protocol": protocol,
        "display_name": _protocol_display_name(protocol),
        "active": True,
        "cache": {
            "schema_version": None,
            "chain_id": None,
            "block_number": None,
            "refreshed_at": None,
            "pool_address": "",
            "identities": [],
            "capabilities": _pool_view_capabilities(),
            "rpc_url": "",
            "asset_count": 0,
        },
        "summary": {
            "stable_borrow_pool_count": 0,
            "non_stable_borrow_pool_count": 0,
            "borrow_target_count": 0,
            "borrow_asset_count": 0,
            "depth_counts": {},
        },
        "stable_borrow_pools": [],
        "non_stable_borrow_pools": [],
        "borrow_target_universe": None,
        "status": status,
        "status_message": messages.get(status, "该协议闪电贷可用资金池暂不可用"),
    }

def _route_only_protocol_view(protocol: str) -> dict[str, Any]:
    view = _empty_protocol_view(protocol, status="chain_protocol_not_supported")
    view["status_message"] = f"{_protocol_display_name(protocol)} 属于链路路由/原生执行轨道，不是可复用闪电贷可用资金池协议"
    return view

def _is_route_only_protocol(protocol: str) -> bool:
    return str(protocol or "").strip().lower() in ROUTE_ONLY_PROTOCOLS

def _protocol_display_name(protocol: str) -> str:
    labels = {
        "aave_v3": "Aave V3",
        "hyper_evm_aave_v3": "HyperEVM Aave V3",
        "venus_core": "Venus Core",
        "morpho_blue": "Morpho Blue",
        "uniswap_v2": "Uniswap V2",
        "uniswap_v3": "Uniswap V3",
        "uniswap_v4": "Uniswap V4",
        "pancakeswap_v2": "PancakeSwap V2",
        "pancakeswap_v3": "PancakeSwap V3",
        "biswap_v2": "Biswap V2",
        "quickswap_v2": "QuickSwap V2",
        "traderjoe_v2": "Trader Joe V2",
        "balancer_v2": "Balancer V2",
        "cow_protocol": "CoW Protocol",
        "hyperliquid_native": "Hyperliquid Native",
    }
    normalized = str(protocol or "").strip().lower()
    if normalized in labels:
        return labels[normalized]
    value = str(protocol or "").strip().replace("_", " ")
    return value.title() if value else "Unknown"


__all__ = [
    "_asset_row",
    "_as_float",
    "_cache_identities",
    "_cache_identity",
    "_configured_data_path",
    "_depth_counts",
    "_dex_pool_row",
    "_empty_protocol_view",
    "_is_route_only_protocol",
    "_is_stable_symbol",
    "_pool_rows_protocol_view",
    "_pool_view_capabilities",
    "_protocol_display_name",
    "_route_only_protocol_view",
    "_unit_amount",
]
