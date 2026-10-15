"""SQLite-only V4 pool index/liquidity view loaders for Robinhood chains.

These loaders convert the raw V4 exchange/liquidity tables back into the
document-shaped dictionaries consumed by legacy page helpers. They never read
JSON evidence files; ``sqlite_db`` is the only business-data source.
"""

from __future__ import annotations

import json
from typing import Any

from core.db.database_path import absolute_database_path
from core.db.storage_chain_core import load_chain_tokens
from core.db.storage_v4_exchange import (
    load_v4_exchange_pool_manager,
    load_v4_exchange_pools,
    load_v4_pool_liquidity,
    load_v4_pool_liquidity_refreshed_at,
)


def _text(value: object) -> str:
    return str(value or "").strip()


def _lower(value: object) -> str:
    return _text(value).lower()


def _json_loads(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if value in (None, ""):
        return {}
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}
    return value


def _tri_bool(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    text = _text(value).lower()
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off", ""}:
        return False
    return None


def _pool_key(row: dict[str, Any]) -> dict[str, Any]:
    key = _json_loads(
        row.get("pool_key_json")
        or row.get("poolKeyJson")
        or row.get("pool_key")
        or row.get("poolKey")
        or {}
    )
    return key if isinstance(key, dict) else {}


def _pool_id(row: dict[str, Any]) -> str:
    return _text(row.get("pool_id") or row.get("poolId") or row.get("pool"))


def _liquidity_status(has_liquidity: bool | None, depth_status: str) -> str:
    if has_liquidity is True:
        return "observable_poolid_nonzero_liquidity_swap"
    if has_liquidity is False:
        return "no_liquidity"
    return _text(depth_status) or "liquidity_evidence_missing"


def _liquidity_pool_row(row: dict[str, Any]) -> dict[str, Any]:
    pool_id = _pool_id(row)
    has_liquidity = _tri_bool(row.get("has_liquidity"))
    depth_status = _text(row.get("depth_status"))
    return {
        "pool": pool_id,
        "poolId": pool_id,
        "hasLiquidity": has_liquidity,
        "liquidity": row.get("liquidity") or "",
        "liquidityIndex": row.get("liquidity_index"),
        "estimatedTvlUsd": row.get("estimated_tvl_usd"),
        "balances": _json_loads(row.get("balances_json") or {}),
        "depthStatus": depth_status,
        "status": _liquidity_status(has_liquidity, depth_status),
        "sqrtPriceX96": row.get("sqrt_price_x96") or "",
        "tick": row.get("tick"),
        "recentSwapActive": _tri_bool(row.get("recent_swap_active")),
        "usableForObservation": has_liquidity if has_liquidity is not None else None,
        "liquidityReadAt": row.get("liquidity_read_at"),
        "liquidityBlockNumber": row.get("liquidity_block_number"),
        "observedAt": row.get("observed_at"),
        "refreshedAt": row.get("refreshed_at"),
        "source": "sqlite_db",
    }


def _index_pool_row(pool: dict[str, Any], liquidity: dict[str, Any]) -> dict[str, Any]:
    pool_id = _pool_id(pool)
    pool_key = _pool_key(pool)
    token0 = _text(pool.get("token0") or pool_key.get("token0") or pool_key.get("currency0"))
    token1 = _text(pool.get("token1") or pool_key.get("token1") or pool_key.get("currency1"))
    currency0_metadata = pool_key.get("currency0Metadata")
    if not isinstance(currency0_metadata, dict):
        currency0_metadata = {}
    currency1_metadata = pool_key.get("currency1Metadata")
    if not isinstance(currency1_metadata, dict):
        currency1_metadata = {}
    return {
        "pool": pool_id,
        "poolId": pool_id,
        "poolIdentityMode": pool.get("pool_identity_mode") or "v4_pool_id",
        "poolManager": pool.get("pool_manager") or pool_key.get("poolManager") or "",
        "poolKey": pool_key,
        "poolKeyDecoded": bool(pool_key),
        "token0": token0,
        "token1": token1,
        "currency0": token0,
        "currency1": token1,
        "token0Symbol": _text(
            pool_key.get("token0Symbol")
            or pool_key.get("currency0Symbol")
            or currency0_metadata.get("symbol")
        ),
        "token1Symbol": _text(
            pool_key.get("token1Symbol")
            or pool_key.get("currency1Symbol")
            or currency1_metadata.get("symbol")
        ),
        "token0Decimals": pool_key.get("token0Decimals")
        or currency0_metadata.get("decimals"),
        "token1Decimals": pool_key.get("token1Decimals")
        or currency1_metadata.get("decimals"),
        "fee": pool.get("fee") if pool.get("fee") is not None else pool_key.get("fee"),
        "tickSpacing": pool.get("tick_spacing")
        if pool.get("tick_spacing") is not None
        else pool_key.get("tickSpacing"),
        "hooks": pool.get("hooks") or pool_key.get("hooks") or "",
        "sqrtPriceX96": (
            liquidity.get("sqrt_price_x96")
            or liquidity.get("sqrtPriceX96")
            or pool_key.get("sqrtPriceX96")
            or ""
        ),
        "tick": liquidity.get("tick")
        if liquidity.get("tick") is not None
        else pool_key.get("tick"),
        "liquidity": liquidity.get("liquidity") or "",
        "liquidityIndex": liquidity.get("liquidity_index"),
        "hasLiquidity": _tri_bool(liquidity.get("has_liquidity")),
        "estimatedTvlUsd": liquidity.get("estimated_tvl_usd"),
        "balances": _json_loads(liquidity.get("balances_json") or {}),
        "depthStatus": liquidity.get("depth_status") or "",
        "recentSwapActive": _tri_bool(liquidity.get("recent_swap_active")),
        "status": _liquidity_status(
            _tri_bool(liquidity.get("has_liquidity")),
            liquidity.get("depth_status") or "",
        )
        if liquidity
        else "initialized_pool",
        "initializeBlockNumber": pool_key.get("initializeBlockNumber"),
        "initializeTransactionHash": pool_key.get("initializeTransactionHash"),
        "source": "sqlite_db",
    }


def _index_payload(
    network: str,
    chain_id: int,
    *,
    database_url: str | None,
    protocol: str,
) -> dict[str, Any]:
    db = str(absolute_database_path(database_url))
    pool_rows = load_v4_exchange_pools(
        db,
        network=network,
        chain_id=chain_id,
        protocol=protocol,
    )
    liquidity_rows = load_v4_pool_liquidity(
        db,
        network=network,
        chain_id=chain_id,
        protocol=protocol,
    )
    liquidity_by_id = {
        _lower(row.get("pool_id")): row
        for row in liquidity_rows
        if _pool_id(row)
    }
    pool_keys: list[dict[str, Any]] = []
    pools: list[dict[str, Any]] = []
    pool_manager = ""
    refreshed_at = load_v4_pool_liquidity_refreshed_at(
        db,
        network=network,
        chain_id=chain_id,
        protocol=protocol,
    )
    for row in pool_rows:
        pool_id = _pool_id(row)
        if not pool_id:
            continue
        pool_manager = pool_manager or _text(row.get("pool_manager"))
        pool_key = _pool_key(row)
        if pool_key:
            pool_keys.append(pool_key)
        pools.append(
            _index_pool_row(
                row,
                liquidity_by_id.get(_lower(pool_id)) or {},
            )
        )
    payload = {
        "schemaVersion": "sqlite-v4-pool-index-v1",
        "network": network,
        "chainId": chain_id,
        "poolManager": pool_manager,
        "poolKeys": pool_keys,
        "pools": pools,
        "poolCount": len(pools),
        "generatedAt": refreshed_at,
        "source": "sqlite_db",
    }
    return {
        "ok": bool(pools),
        "status": "v4_pool_index_available" if pools else "v4_pool_index_empty",
        "source": "sqlite_db",
        "payload": payload,
        "pools": pools,
        "pool_keys": pool_keys,
        "pool_manager": pool_manager,
        "block_number": None,
        "refreshed_at": refreshed_at,
        "blocking_reasons": [] if pools else ["v4_exchange_pools_empty_in_sqlite"],
    }


def _liquidity_payload(
    network: str,
    chain_id: int,
    *,
    database_url: str | None,
    protocol: str,
) -> dict[str, Any]:
    db = str(absolute_database_path(database_url))
    rows = load_v4_pool_liquidity(
        db,
        network=network,
        chain_id=chain_id,
        protocol=protocol,
    )
    pools = [_liquidity_pool_row(row) for row in rows if _pool_id(row)]
    manager_rows = load_v4_exchange_pool_manager(
        db,
        network=network,
        chain_id=chain_id,
        protocol=protocol,
    )
    pool_manager = _text(manager_rows.get("pool_manager"))
    refreshed_at = load_v4_pool_liquidity_refreshed_at(
        db,
        network=network,
        chain_id=chain_id,
        protocol=protocol,
    )
    payload = {
        "schemaVersion": "sqlite-v4-pool-liquidity-v1",
        "network": network,
        "chainId": chain_id,
        "poolManager": pool_manager,
        "pools": pools,
        "poolKeys": [],
        "poolCount": len(pools),
        "generatedAt": refreshed_at,
        "flashAccountingSources": [],
        "source": "sqlite_db",
    }
    return {
        "ok": bool(pools),
        "status": "v4_pool_liquidity_cache_available" if pools else "v4_pool_liquidity_cache_not_available",
        "status_message": "Robinhood V4 pool liquidity snapshot loaded from SQLite",
        "source": "sqlite_db",
        "payload": payload,
        "pools": pools,
        "pool_keys": [],
        "pool_manager": pool_manager,
        "block_number": None,
        "refreshed_at": refreshed_at,
        "blocking_reasons": [] if pools else ["v4_pool_liquidity_snapshots_empty_in_sqlite"],
    }


def load_v4_pool_index_payload(
    network: str,
    chain_id: int,
    *,
    database_url: str | None = None,
    protocol: str = "uniswap_v4",
) -> dict[str, Any]:
    return _index_payload(
        network,
        int(chain_id),
        database_url=database_url,
        protocol=protocol,
    )


def load_v4_pool_liquidity_payload(
    network: str,
    chain_id: int,
    *,
    database_url: str | None = None,
    protocol: str = "uniswap_v4",
) -> dict[str, Any]:
    return _liquidity_payload(
        network,
        int(chain_id),
        database_url=database_url,
        protocol=protocol,
    )


def load_v4_chain_token_metadata(
    network: str,
    chain_id: int,
    *,
    database_url: str | None = None,
) -> dict[str, dict[str, Any]]:
    db = str(absolute_database_path(database_url))
    metadata: dict[str, dict[str, Any]] = {}
    for row in load_chain_tokens(db, network=network, chain_id=int(chain_id)):
        address = _lower(row.get("token_address"))
        if not address:
            continue
        symbol = _text(row.get("token_symbol"))
        decimals = row.get("token_decimals")
        try:
            decimals = int(decimals) if decimals not in (None, "") else None
        except (TypeError, ValueError):
            decimals = None
        metadata[address] = {
            "address": _text(row.get("token_address")),
            "symbol": symbol,
            "decimals": decimals,
            "metadataOk": bool(symbol and decimals is not None),
            "source": "sqlite_db",
        }
    return metadata


__all__ = [
    "load_v4_chain_token_metadata",
    "load_v4_pool_index_payload",
    "load_v4_pool_liquidity_payload",
]
