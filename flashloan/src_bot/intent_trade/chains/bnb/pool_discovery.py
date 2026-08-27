from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from core.chain_flow_dispatcher import ChainFlowRequest
from core.chain_data_registry import chain_data_by_chain_id, chain_data_by_network, chain_data_file_path


APP_ROOT = Path(__file__).resolve().parents[3]
REPO_ROOT = APP_ROOT.parents[1]
DEFAULT_BNB_VENUS_MARKET_STATIC_TABLE_PATH = (
    REPO_ROOT / "data_info" / "BNB" / "liquidation" / "venus_market_static_table.json"
)
DEFAULT_BNB_DEX_POOL_STATIC_TABLE_PATH = APP_ROOT / "runtime" / "cache" / "bnb_arbitrage_pool_static_table.json"
PROTOCOL_LABELS = {
    "venus_core": "Venus Core",
    "pancakeswap_v2": "PancakeSwap V2",
    "pancakeswap_v3": "PancakeSwap V3",
    "biswap_v2": "Biswap V2",
    "aave_v3": "Aave V3",
    "uniswap_v4": "Uniswap V4",
}


def _read_json(path: Path | None) -> dict[str, Any] | None:
    if path is None:
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _as_int(value: object, default: int = 0) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return default


def _as_float(value: object, default: float = 0.0) -> float:
    try:
        parsed = float(value or 0)
    except (TypeError, ValueError):
        return default
    return parsed if parsed == parsed and parsed not in (float("inf"), float("-inf")) else default


def _configured_data_path(network: str, key: str, current: Path | None, fallback: Path) -> Path | None:
    if current is not None and Path(current) != fallback:
        return current
    try:
        return chain_data_file_path(network, key) or current
    except Exception:
        return current


def _network_for_chain_id(chain_id: int, default: str = "bnb") -> str:
    try:
        row = chain_data_by_chain_id(chain_id)
    except Exception:
        row = None
    return str((row or {}).get("network") or default)


def _chain_id_for_network(network: str, default: int) -> int:
    try:
        row = chain_data_by_network(network) or {}
    except Exception:
        row = {}
    try:
        return int(row.get("chain_id") or default)
    except (TypeError, ValueError):
        return default


def _summary(
    *,
    chain_id: int | None,
    network: str,
    protocol: str,
    pool_count: int,
    borrowable_pool_count: int,
    asset_count: int,
    depth_score_usd: float,
    source: str,
    refreshed_at: object = None,
) -> dict[str, Any]:
    return {
        "chain_id": chain_id,
        "network": network,
        "chain_key": f"chain:{chain_id}" if chain_id is not None else f"network:{network}",
        "protocol": protocol,
        "display_name": PROTOCOL_LABELS.get(protocol, protocol.replace("_", " ").title()),
        "pool_count": int(pool_count),
        "borrowable_pool_count": int(borrowable_pool_count),
        "asset_count": int(asset_count),
        "depth_score_usd": float(depth_score_usd or 0),
        "source": source,
        "refreshed_at": refreshed_at,
        "raw_snapshot_key": None,
    }


def _venus_summary(path: Path | None) -> dict[str, Any] | None:
    cache = _read_json(path)
    markets = [
        item
        for item in (cache or {}).get("markets") or []
        if isinstance(item, dict) and item.get("is_listed") is not False
    ]
    if not cache or not markets:
        return None
    default_chain_id = _chain_id_for_network("bnb", 56)
    chain_id = _as_int(cache.get("chain_id") or cache.get("chainId") or default_chain_id, default_chain_id)
    borrowable = 0
    for market in markets:
        if market.get("borrow_disabled"):
            continue
        if _as_float(market.get("cash")) > 0:
            borrowable += 1
    return _summary(
        chain_id=chain_id,
        network=_network_for_chain_id(chain_id),
        protocol="venus_core",
        pool_count=len(markets),
        borrowable_pool_count=borrowable,
        asset_count=len(markets),
        depth_score_usd=0.0,
        source=str(path or ""),
        refreshed_at=cache.get("generated_at"),
    )


def _dex_cache_summaries(path: Path | None) -> list[dict[str, Any]]:
    cache = _read_json(path)
    if not cache:
        return []
    chain_id = _chain_id_for_network("bnb", 56)
    cache_chain_id = _as_int(cache.get("chainId") or cache.get("chain_id") or chain_id, chain_id)
    if cache_chain_id != chain_id:
        return []
    grouped: dict[str, list[dict[str, Any]]] = {}
    for pool in cache.get("pools") or []:
        if not isinstance(pool, dict):
            continue
        protocol = str(pool.get("dex") or "").strip().lower()
        if not protocol or protocol not in PROTOCOL_LABELS:
            continue
        grouped.setdefault(protocol, []).append(pool)
    summaries: list[dict[str, Any]] = []
    for protocol, pools in grouped.items():
        available = [pool for pool in pools if _as_float(pool.get("tvlUsd") or pool.get("token0Usd") or pool.get("token1Usd")) > 0]
        if not available:
            continue
        summaries.append(
            _summary(
                chain_id=chain_id,
                network=_network_for_chain_id(chain_id),
                protocol=protocol,
                pool_count=len(available),
                borrowable_pool_count=len(available),
                asset_count=len(available),
                depth_score_usd=sum(_as_float(pool.get("tvlUsd")) for pool in available),
                source=str(path or ""),
                refreshed_at=cache.get("generatedAt") or cache.get("fetched_at"),
            )
        )
    return summaries


def bnb_flash_pool_discovery_contribution(
    *,
    venus_market_path: Path | None = DEFAULT_BNB_VENUS_MARKET_STATIC_TABLE_PATH,
    dex_pool_static_path: Path | None = DEFAULT_BNB_DEX_POOL_STATIC_TABLE_PATH,
) -> dict[str, Any]:
    venus_market_path = _configured_data_path(
        "bnb",
        "venus_markets",
        venus_market_path,
        DEFAULT_BNB_VENUS_MARKET_STATIC_TABLE_PATH,
    )
    dex_pool_static_path = _configured_data_path(
        "bnb",
        "dex_pools",
        dex_pool_static_path,
        DEFAULT_BNB_DEX_POOL_STATIC_TABLE_PATH,
    )
    summaries = []
    venus = _venus_summary(venus_market_path)
    if venus:
        summaries.append(venus)
    summaries.extend(_dex_cache_summaries(dex_pool_static_path))
    return {"protocol_summaries": summaries, "raw_snapshots": {}, "errors": []}


def pool_discovery_flow(
    *,
    request: ChainFlowRequest | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    return bnb_flash_pool_discovery_contribution(**kwargs)


__all__ = ["bnb_flash_pool_discovery_contribution", "pool_discovery_flow"]
