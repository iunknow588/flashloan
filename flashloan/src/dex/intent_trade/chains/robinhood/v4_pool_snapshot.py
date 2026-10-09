"""Robinhood mainnet V4 static pool registry and dynamic liquidity snapshot.

PoolKey addresses are fixed on-chain facts discovered from Initialize logs.
Liquidity is a separate dynamic snapshot: refreshes update liquidity evidence
without recreating or rewriting the pool-address registry.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from core.chain_data_registry import chain_data_file_path
from dex.intent_trade.chains.robinhood.config import CHAIN_ID as ROBINHOOD_CHAIN_ID
from dex.intent_trade.chains.robinhood.config import NETWORK as ROBINHOOD_NETWORK


def default_v4_pool_snapshot_path() -> Path | None:
    return chain_data_file_path(ROBINHOOD_NETWORK, "v4_pool_snapshot", chain_id=ROBINHOOD_CHAIN_ID)


def default_v4_pool_liquidity_path() -> Path | None:
    return chain_data_file_path(ROBINHOOD_NETWORK, "v4_pool_liquidity", chain_id=ROBINHOOD_CHAIN_ID)


def _read_json(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _pool_id(row: dict[str, Any]) -> str:
    return str(row.get("poolId") or row.get("pool_id") or row.get("pool") or row.get("id") or "").strip()


def _address_pair(row: dict[str, Any]) -> str:
    token0 = str(row.get("token0") or row.get("currency0") or "").strip().lower()
    token1 = str(row.get("token1") or row.get("currency1") or "").strip().lower()
    if not token0 or not token1:
        return ""
    return ":".join(sorted((token0, token1)))


def build_v4_pool_snapshot(
    *,
    index_path: Path | None = None,
    source: str = "robinhood_mainnet_v4_pool_index",
) -> dict[str, Any]:
    """Build the static PoolKey registry from the full V4 initialize index.

    Pool addresses never change after Initialize; liquidity fields are
    deliberately kept out of this file.  Consumers merge the dynamic liquidity
    snapshot at read time.
    """
    index_path = index_path or chain_data_file_path(ROBINHOOD_NETWORK, "v4_pool_index", chain_id=ROBINHOOD_CHAIN_ID)
    payload = _read_json(index_path)
    pools: list[dict[str, Any]] = []
    by_address_pair: dict[str, list[dict[str, Any]]] = {}
    for row in payload.get("pools") or []:
        if not isinstance(row, dict):
            continue
        pool_id = _pool_id(row)
        address_pair = _address_pair(row)
        if not pool_id or not address_pair:
            continue
        static = {
            "pool": pool_id,
            "poolId": pool_id,
            "poolIdentityMode": "v4_pool_id",
            "protocol": "uniswap_v4",
            "dex": "uniswap_v4",
            "poolManager": row.get("poolManager"),
            "poolKey": row.get("poolKey") or {},
            "poolKeyDecoded": bool(row.get("poolKeyDecoded") or row.get("poolKey")),
            "fee": row.get("fee"),
            "dynamicFee": bool(row.get("dynamicFee")),
            "specialFee": bool(row.get("specialFee")),
            "tickSpacing": row.get("tickSpacing"),
            "hooks": row.get("hooks") or "",
            "token0": str(row.get("token0") or row.get("currency0") or "").strip(),
            "token1": str(row.get("token1") or row.get("currency1") or "").strip(),
            "token0Symbol": row.get("token0Symbol") or "",
            "token1Symbol": row.get("token1Symbol") or "",
            "token0Decimals": row.get("token0Decimals"),
            "token1Decimals": row.get("token1Decimals"),
            "symbolPair": row.get("symbolPair") or "",
            "addressPair": address_pair,
            "initializeBlockNumber": row.get("initializeBlockNumber"),
            "initializeTransactionHash": row.get("initializeTransactionHash"),
        }
        pools.append(static)
        by_address_pair.setdefault(address_pair, []).append(static)
    return {
        "schemaVersion": "robinhood-mainnet-v4-pool-snapshot-v1",
        "mode": "robinhood_mainnet_v4_static_pool_snapshot",
        "network": ROBINHOOD_NETWORK,
        "chainId": ROBINHOOD_CHAIN_ID,
        "source": source,
        "generatedAt": payload.get("generatedAt"),
        "generatedBlockNumber": payload.get("generatedBlockNumber"),
        "poolManager": payload.get("poolManager"),
        "poolCount": len(pools),
        "pools": pools,
        "poolsByAddressPair": by_address_pair,
        "addressPairCount": len(by_address_pair),
        "liquidity": {
            "mode": "dynamic_snapshot",
            "path": str(default_v4_pool_liquidity_path() or ""),
            "refreshedAt": None,
        },
    }


def write_v4_pool_snapshot(
    *,
    output_path: Path | None = None,
    index_path: Path | None = None,
) -> dict[str, Any]:
    resolved = output_path or default_v4_pool_snapshot_path()
    if resolved is None:
        raise ValueError("Robinhood mainnet v4_pool_snapshot data file is not configured")
    payload = build_v4_pool_snapshot(index_path=index_path)
    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {**payload, "outputPath": str(resolved)}


def load_v4_pool_snapshot(
    path: Path | None = None,
    *,
    merge_liquidity: bool = True,
) -> dict[str, Any]:
    resolved = path or default_v4_pool_snapshot_path()
    payload = _read_json(resolved)
    if not payload:
        return {
            "mode": "robinhood_mainnet_v4_static_pool_snapshot",
            "network": ROBINHOOD_NETWORK,
            "chainId": ROBINHOOD_CHAIN_ID,
            "status": "pool_snapshot_missing",
            "pools": [],
            "poolsByAddressPair": {},
        }
    if merge_liquidity:
        liquidity = _read_json(default_v4_pool_liquidity_path())
        liquidity_by_pool_id = {
            str(item.get("poolId") or item.get("pool") or "").strip().lower(): item
            for item in liquidity.get("pools") or []
            if isinstance(item, dict)
        }
        merged_pools: list[dict[str, Any]] = []
        for pool in payload.get("pools") or []:
            if not isinstance(pool, dict):
                continue
            copied = dict(pool)
            live = liquidity_by_pool_id.get(str(pool.get("poolId") or "").strip().lower()) or {}
            copied.update(live)
            copied["liquiditySnapshotAt"] = liquidity.get("generatedAt") or liquidity.get("liquidityScanAt")
            merged_pools.append(copied)
        payload = dict(payload)
        payload["pools"] = merged_pools
        payload["liquidity"] = {
            **dict(payload.get("liquidity") or {}),
            "mode": "dynamic_snapshot",
            "path": str(default_v4_pool_liquidity_path() or ""),
            "refreshedAt": liquidity.get("generatedAt") or liquidity.get("liquidityScanAt"),
            "poolCount": len(merged_pools),
        }
    return payload


def refresh_v4_pool_liquidity_from_index(
    *,
    output_path: Path | None = None,
    index_path: Path | None = None,
) -> dict[str, Any]:
    """Copy the latest liquidity markers from the V4 index into the snapshot.

    This is the dynamic half of the two-file design: it updates only
    liquidity/activity evidence and never changes the static PoolKey registry.
    """
    index_path = index_path or chain_data_file_path(ROBINHOOD_NETWORK, "v4_pool_index", chain_id=ROBINHOOD_CHAIN_ID)
    index = _read_json(index_path)
    liquidity_pools: list[dict[str, Any]] = []
    for row in index.get("pools") or []:
        if not isinstance(row, dict):
            continue
        pool_id = _pool_id(row)
        if not pool_id:
            continue
        liquidity_pools.append(
            {
                "poolId": pool_id,
                "pool": pool_id,
                "liquidity": row.get("liquidity") or "",
                "liquidityIndex": row.get("liquidityIndex"),
                "hasLiquidity": row.get("hasLiquidity"),
                "estimatedTvlUsd": row.get("estimatedTvlUsd"),
                "tvlUsd": row.get("tvlUsd"),
                "balances": row.get("balances") or {},
                "depthStatus": row.get("depthStatus") or "",
                "depthBand": row.get("depthBand") or {},
                "recentSwapActive": row.get("recentSwapActive"),
                "liquidityReadAt": row.get("liquidityReadAt"),
                "liquidityBlockNumber": row.get("liquidityBlockNumber"),
                "liquidityError": row.get("liquidityError"),
                "quoteSummary": row.get("quoteSummary") or {},
            }
        )
    resolved = output_path or default_v4_pool_liquidity_path()
    if resolved is None:
        raise ValueError("Robinhood mainnet v4_pool_liquidity data file is not configured")
    payload = {
        "schemaVersion": "robinhood-mainnet-v4-liquidity-snapshot-v1",
        "mode": "robinhood_mainnet_v4_pool_liquidity_snapshot",
        "network": ROBINHOOD_NETWORK,
        "chainId": ROBINHOOD_CHAIN_ID,
        "generatedAt": index.get("generatedAt"),
        "liquidityScanAt": index.get("liquidityScanAt"),
        "recentSwapScanAt": index.get("recentSwapScanAt"),
        "liquidityMarkerActive": index.get("liquidityMarkerActive"),
        "liquidityMarkedPoolCount": index.get("liquidityMarkedPoolCount"),
        "recentSwapActive": index.get("recentSwapActive"),
        "recentSwapPoolCount": index.get("recentSwapPoolCount"),
        "poolCount": len(liquidity_pools),
        "pools": liquidity_pools,
        "sourceIndexPath": str(index_path),
        "detail": "Dynamic liquidity snapshot; pool addresses stay in v4_pool_snapshot.",
    }
    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {**payload, "outputPath": str(resolved)}


__all__ = [
    "build_v4_pool_snapshot",
    "default_v4_pool_liquidity_path",
    "default_v4_pool_snapshot_path",
    "load_v4_pool_snapshot",
    "refresh_v4_pool_liquidity_from_index",
    "write_v4_pool_snapshot",
]
