from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from core.chain_data_registry import chain_data_by_chain_id, chain_data_by_network, chain_data_file_path
from core.chain_flow_dispatcher import ChainFlowRequest


APP_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_AAVE_CACHE_PATH = APP_ROOT / "runtime" / "cache" / "aave_reserve_assets.json"
DEFAULT_AVALANCHE_V3_POOL_STATIC_TABLE_PATH = APP_ROOT / "runtime" / "cache" / "avalanche_l1_pool_static_table.json"
DEFAULT_AVALANCHE_V2_POOL_DISCOVERY_PATH = APP_ROOT / "runtime" / "cache" / "avalanche_multi_dex_v2_pool_discovery.json"
PROTOCOL_LABELS = {
    "aave_v3": "Aave V3",
    "uniswap_v3": "Uniswap V3",
    "traderjoe_v2": "Trader Joe V2",
    "curve": "Curve",
    "uniswap_v4": "Uniswap V4",
}
AVALANCHE_V2_DEX_PROTOCOLS = {
    "trader_joe_v1": "traderjoe_v2",
    "trader_joe_v2": "traderjoe_v2",
    "traderjoe_v2": "traderjoe_v2",
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


def _network_for_chain_id(chain_id: int, default: str = "avalanche") -> str:
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


def _aave_summary(path: Path | None) -> dict[str, Any] | None:
    cache = _read_json(path)
    assets = [item for item in (cache or {}).get("assets") or [] if isinstance(item, dict)]
    if not cache or not assets:
        return None
    chain_id = _as_int(cache.get("chain_id") or cache.get("chainId"))
    return _summary(
        chain_id=chain_id or None,
        network=_network_for_chain_id(chain_id),
        protocol="aave_v3",
        pool_count=len(assets),
        borrowable_pool_count=sum(1 for item in assets if _as_float(item.get("available_liquidity")) > 0),
        asset_count=len({str(item.get("token_symbol") or "").upper() for item in assets if item.get("token_symbol")}),
        depth_score_usd=sum(_as_float(item.get("depth_score_usd")) for item in assets),
        source=str(path or ""),
        refreshed_at=cache.get("refreshed_at"),
    )


def _v3_summaries(path: Path | None) -> list[dict[str, Any]]:
    cache = _read_json(path)
    if not cache:
        return []
    pools: list[dict[str, Any]] = []
    for pair in cache.get("pools") or []:
        if not isinstance(pair, dict):
            continue
        for pool in pair.get("pools") or pair.get("executionPools") or []:
            if isinstance(pool, dict) and _as_float(pool.get("tvlUsd")) > 0:
                pools.append(pool)
    if not pools:
        return []
    chain_id = _chain_id_for_network("avalanche", 43114)
    network = _network_for_chain_id(chain_id)
    return [
        _summary(
            chain_id=chain_id,
            network=network,
            protocol="uniswap_v3",
            pool_count=len(pools),
            borrowable_pool_count=len(pools),
            asset_count=len(pools),
            depth_score_usd=sum(_as_float(pool.get("tvlUsd")) for pool in pools),
            source=str(path or ""),
            refreshed_at=cache.get("generatedAt"),
        )
    ]


def _v2_summaries(path: Path | None) -> list[dict[str, Any]]:
    cache = _read_json(path)
    if not cache:
        return []
    chain_id = _chain_id_for_network("avalanche", 43114)
    cache_chain_id = _as_int(cache.get("chainId") or cache.get("chain_id") or chain_id, chain_id)
    if cache_chain_id != chain_id:
        return []
    grouped: dict[str, list[dict[str, Any]]] = {}
    for pool in cache.get("pools") or []:
        if not isinstance(pool, dict):
            continue
        raw_protocol = str(pool.get("dex") or "").strip().lower()
        if raw_protocol not in AVALANCHE_V2_DEX_PROTOCOLS:
            continue
        protocol = AVALANCHE_V2_DEX_PROTOCOLS[raw_protocol]
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


def avalanche_flash_pool_discovery_contribution(
    *,
    aave_cache_path: Path | None = DEFAULT_AAVE_CACHE_PATH,
    v3_pool_static_path: Path | None = DEFAULT_AVALANCHE_V3_POOL_STATIC_TABLE_PATH,
    v2_pool_discovery_path: Path | None = DEFAULT_AVALANCHE_V2_POOL_DISCOVERY_PATH,
) -> dict[str, Any]:
    aave_cache_path = _configured_data_path("avalanche", "aave_reserves", aave_cache_path, DEFAULT_AAVE_CACHE_PATH)
    v3_pool_static_path = _configured_data_path(
        "avalanche",
        "uniswap_v3_pools",
        v3_pool_static_path,
        DEFAULT_AVALANCHE_V3_POOL_STATIC_TABLE_PATH,
    )
    v2_pool_discovery_path = _configured_data_path(
        "avalanche",
        "v2_pool_discovery",
        v2_pool_discovery_path,
        DEFAULT_AVALANCHE_V2_POOL_DISCOVERY_PATH,
    )
    summaries = []
    aave = _aave_summary(aave_cache_path)
    if aave:
        summaries.append(aave)
    summaries.extend(_v3_summaries(v3_pool_static_path))
    summaries.extend(_v2_summaries(v2_pool_discovery_path))
    return {"protocol_summaries": summaries, "raw_snapshots": {}, "errors": []}


def pool_discovery_flow(
    *,
    request: ChainFlowRequest | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    return avalanche_flash_pool_discovery_contribution(**kwargs)


__all__ = ["avalanche_flash_pool_discovery_contribution", "pool_discovery_flow"]
