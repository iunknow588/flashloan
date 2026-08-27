from __future__ import annotations

from pathlib import Path
from typing import Any

from intent_trade.flows.flash_loan_pools.presenter import (
    _configured_data_path,
    _empty_protocol_view,
    _pool_rows_protocol_view,
)
from web.control_panel_data_cache import read_json


APP_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_AVALANCHE_V3_POOL_STATIC_TABLE_PATH = APP_ROOT / "runtime" / "cache" / "avalanche_l1_pool_static_table.json"
DEFAULT_AVALANCHE_V2_POOL_DISCOVERY_PATH = APP_ROOT / "runtime" / "cache" / "avalanche_multi_dex_v2_pool_discovery.json"


AVALANCHE_V2_DEX_PROTOCOLS = {
    "trader_joe_v1": "traderjoe_v2",
    "trader_joe_v2": "traderjoe_v2",
    "traderjoe_v2": "traderjoe_v2",
}

def _avalanche_v3_pool_rows(cache: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for pair in cache.get("pools") or []:
        if not isinstance(pair, dict):
            continue
        for pool in pair.get("pools") or pair.get("executionPools") or []:
            if not isinstance(pool, dict):
                continue
            pool_address = str(pool.get("pool") or "").strip()
            if not pool_address:
                continue
            fee = pool.get("fee")
            key = f"{pool_address.lower()}:{fee}"
            if key in seen:
                continue
            seen.add(key)
            token0 = str(pool.get("token0Symbol") or pool.get("token0_symbol") or "").strip()
            token1 = str(pool.get("token1Symbol") or pool.get("token1_symbol") or "").strip()
            rows.append(
                {
                    **pool,
                    "dex": "uniswap_v3",
                    "protocol": "uniswap_v3",
                    "pool": pool_address,
                    "symbolPair": pool.get("symbolPair") or ":".join(part for part in (token0, token1) if part),
                    "token0Symbol": token0,
                    "token1Symbol": token1,
                    "token0Usd": pool.get("token0Usd"),
                    "token1Usd": pool.get("token1Usd"),
                    "tvlUsd": pool.get("tvlUsd"),
                    "status": pool.get("status") or pool.get("quality") or "observable",
                }
            )
    return rows


def _avalanche_v2_pool_rows(cache: dict[str, Any], protocol: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for pool in cache.get("pools") or []:
        if not isinstance(pool, dict):
            continue
        mapped_protocol = AVALANCHE_V2_DEX_PROTOCOLS.get(str(pool.get("dex") or "").strip().lower())
        if mapped_protocol != protocol:
            continue
        rows.append(
            {
                **pool,
                "dex": protocol,
                "protocol": protocol,
                "fee": pool.get("fee") or pool.get("feeBps"),
                "status": pool.get("status") or "observable",
            }
        )
    return rows

def _avalanche_dex_protocol_view(
    protocol: str,
    *,
    avalanche_v3_pool_static_path: Path | None = None,
    avalanche_v2_pool_discovery_path: Path | None = None,
) -> dict[str, Any]:
    if protocol == "uniswap_v3":
        path = _configured_data_path(
            "avalanche",
            "uniswap_v3_pools",
            avalanche_v3_pool_static_path or DEFAULT_AVALANCHE_V3_POOL_STATIC_TABLE_PATH,
            DEFAULT_AVALANCHE_V3_POOL_STATIC_TABLE_PATH,
        )
        cache = read_json(path)
        if not cache:
            return _empty_protocol_view(protocol, status="protocol_pool_cache_not_available")
        return _pool_rows_protocol_view(
            protocol,
            cache=cache,
            path=path,
            rows=_avalanche_v3_pool_rows(cache),
            target_source="avalanche_l1_pool_static_table",
        )
    if protocol == "traderjoe_v2":
        path = _configured_data_path(
            "avalanche",
            "v2_pool_discovery",
            avalanche_v2_pool_discovery_path or DEFAULT_AVALANCHE_V2_POOL_DISCOVERY_PATH,
            DEFAULT_AVALANCHE_V2_POOL_DISCOVERY_PATH,
        )
        cache = read_json(path)
        if not cache:
            return _empty_protocol_view(protocol, status="protocol_pool_cache_not_available")
        return _pool_rows_protocol_view(
            protocol,
            cache=cache,
            path=path,
            rows=_avalanche_v2_pool_rows(cache, protocol),
            target_source="avalanche_multi_dex_v2_pool_discovery",
        )
    return _empty_protocol_view(protocol, status="protocol_pool_cache_not_available")


avalanche_dex_protocol_view = _avalanche_dex_protocol_view

__all__ = ["avalanche_dex_protocol_view"]
