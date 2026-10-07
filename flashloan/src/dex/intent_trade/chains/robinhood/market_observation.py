"""Robinhood mainnet market observation extensions.

This module is chain-owned. It exists so the generic DEX observation flow can
find mainnet-specific bridge/fund observation hooks without writing chain
branches in shared processing code. It never imports or reads testnet evidence.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from core.chain_data_registry import chain_data_by_network
from dex.intent_trade.chains.robinhood.config import (
    CHAIN_ID as ROBINHOOD_CHAIN_ID,
    WETH_ADDRESS,
)
from dex.intent_trade.chains.robinhood.v4_pool_index import (
    ZERO_ADDRESS,
    default_v4_pool_index_path,
)
_ROBINHOOD_ROW = chain_data_by_network("robinhood") or {}
_ROBINHOOD_CONTRACTS = _ROBINHOOD_ROW.get("contracts") if isinstance(_ROBINHOOD_ROW.get("contracts"), dict) else {}

_ETH_PAIR_TOKENS_META_CACHE: dict[str, Any] | None = None
_MAINNET_V4_INDEX_CACHE: dict[str, Any] | None = None

V4_DYNAMIC_FEE_FLAG = 8388608
V4_SPECIAL_FEE_THRESHOLD = 100_000
MAINNET_STABLE_ANCHOR_SYMBOLS = ("USDG", "USDE")

_MAINNET_TEST_MANIFEST_PATH = Path(__file__).resolve().parents[6] / "test-token_create" / "config" / "mainnet-v4-amm" / "manifest.json"


def _mainnet_test_token_addresses() -> dict[str, str]:
    manifest: dict[str, Any] = {}
    try:
        payload = json.loads(_MAINNET_TEST_MANIFEST_PATH.read_text(encoding="utf-8"))
        manifest = payload if isinstance(payload, dict) else {}
    except (OSError, ValueError, json.JSONDecodeError):
        manifest = {}
    result: dict[str, str] = {}
    for symbol, env_name in (
        ("TZ", "ROBINHOOD_MAINNET_TZ_ADDRESS"),
        ("TA", "ROBINHOOD_MAINNET_TA_ADDRESS"),
        ("TB", "ROBINHOOD_MAINNET_TB_ADDRESS"),
    ):
        address = str(os.getenv(env_name) or "").strip().lower()
        if not address:
            address = str(manifest.get(symbol.lower()) or "").strip().lower()
        if address:
            result[symbol] = address
    return result


_MAINNET_TEST_ADDRESS_BY_SYMBOL = _mainnet_test_token_addresses()


def _manifest_test_anchor_symbols() -> tuple[str, ...]:
    tz = _MAINNET_TEST_ADDRESS_BY_SYMBOL.get("TZ")
    if not tz:
        return ()
    return ("TZ",)


MAINNET_TEST_ANCHOR_SYMBOLS = _manifest_test_anchor_symbols()


def _recent_swap_required() -> bool:
    raw = os.getenv("ROBINHOOD_REQUIRE_RECENT_SWAP_ACTIVE", "").strip().lower()
    if raw:
        return raw in {"1", "true", "yes", "on"}
    return True


def _pool_fee(pool: dict[str, Any]) -> int | None:
    raw = pool.get("fee")
    if raw in (None, ""):
        return None
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        return None


def _pool_dynamic_fee(pool: dict[str, Any]) -> bool:
    fee = _pool_fee(pool)
    return bool(fee) and bool(fee & V4_DYNAMIC_FEE_FLAG)


def _pool_special_fee(pool: dict[str, Any]) -> bool:
    fee = _pool_fee(pool)
    if not fee:
        return False
    return bool(fee & V4_DYNAMIC_FEE_FLAG) or fee > V4_SPECIAL_FEE_THRESHOLD


def _pool_fee_label(pool: dict[str, Any]) -> str | int | None:
    if _pool_dynamic_fee(pool):
        return "dynamic"
    if _pool_special_fee(pool):
        return "special"
    return _pool_fee(pool)


def _pool_usable_for_observation(pool: dict[str, Any]) -> bool:
    if _pool_special_fee(pool):
        return False
    has_liquidity = pool.get("hasLiquidity")
    if has_liquidity is not None:
        return bool(has_liquidity) or _number_liquidity(pool) > 0
    return _number_liquidity(pool) > 0


def _pool_depth_label(pool: dict[str, Any]) -> str:
    depth = pool.get("depthBand")
    if isinstance(depth, dict):
        return str(depth.get("label") or pool.get("depthStatus") or "-")
    return str(pool.get("depthStatus") or "-")


def _active_pool_ids(pools: list[dict[str, Any]]) -> set[str]:
    return {
        _pool_id(pool).lower()
        for pool in pools
        if _pool_id(pool)
        and _pool_usable_for_observation(pool)
    }


def _number_liquidity(pool: dict[str, Any]) -> float:
    try:
        return float(str(pool.get("liquidity") or pool.get("liquidityIndex") or "0"))
    except (TypeError, ValueError):
        return 0.0


def default_robinhood_eth_pair_tokens_path() -> Path | None:
    """Return the Robinhood mainnet ETH-paired token evidence path."""
    try:
        from core.chain_data_registry import chain_data_file_path

        path = chain_data_file_path("robinhood", "eth_pair_tokens", chain_id=ROBINHOOD_CHAIN_ID)
        if path is not None:
            return Path(path)
    except Exception:
        pass
    return Path(__file__).resolve().parents[3] / "runtime" / "evidence" / "robinhood" / "eth_pair_tokens.json"


def _load_eth_pair_tokens_meta() -> dict[str, Any]:
    global _ETH_PAIR_TOKENS_META_CACHE
    if _ETH_PAIR_TOKENS_META_CACHE is not None:
        return _ETH_PAIR_TOKENS_META_CACHE
    path = default_robinhood_eth_pair_tokens_path()
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8-sig")) if path else {}
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    token_count = int(payload.get("ethPairTokenCount") or 0)
    if token_count <= 0:
        token_count = len(payload.get("ycZc") or [])
    _ETH_PAIR_TOKENS_META_CACHE = {
        "network": payload.get("network") or "robinhood",
        "chainId": int(payload.get("chainId") or 0) or ROBINHOOD_CHAIN_ID,
        "ethPairTokenCount": int(token_count or 0),
        "initializeLogCount": int(payload.get("initializeLogCount") or 0),
        "generatedBlockNumber": payload.get("generatedBlockNumber") or 0,
        "generatedAt": payload.get("generatedAt") or "",
        "sourcePath": str(path or ""),
    }
    return _ETH_PAIR_TOKENS_META_CACHE


def _mainnet_eth_pair_token_count() -> int:
    try:
        return int(_load_eth_pair_tokens_meta().get("ethPairTokenCount") or 0)
    except Exception:
        return 0


def _symbol(value: object) -> str:
    return str(value or "").strip().upper()


def _route_symbol_allowed(symbol: object) -> bool:
    text = _symbol(symbol)
    return bool(text) and not text.startswith("ROBINHOOD")


def _protocol_in_scope(protocols: list[str] | None) -> bool:
    protocol_set = {
        str(protocol or "").strip().lower()
        for protocol in (protocols or [])
        if str(protocol or "").strip()
    }
    return not protocol_set or "uniswap_v4" in protocol_set


def _chain_in_scope(chain_id: int | None) -> bool:
    return int(chain_id or 0) == ROBINHOOD_CHAIN_ID


def _address(value: object) -> str:
    text = str(value or "").strip()
    return text.lower() if text.startswith("0x") and len(text) == 42 else ""


def _pool_id(pool: dict[str, Any]) -> str:
    return str(pool.get("pool") or pool.get("poolId") or pool.get("pool_id") or pool.get("id") or "").strip()


def _pool_edge(pool: dict[str, Any]) -> tuple[str, str] | None:
    token0 = _address(pool.get("token0") or pool.get("currency0"))
    token1 = _address(pool.get("token1") or pool.get("currency1"))
    if not token0 or not token1 or token0 == token1:
        return None
    return tuple(sorted((token0, token1)))


def _known_symbol(address: str, fallback_symbol: str = "") -> str:
    normalized = _address(address)
    if normalized == _address(ZERO_ADDRESS):
        return "ETH"
    if normalized == _address(WETH_ADDRESS):
        return "WETH"
    if normalized == _address(_ROBINHOOD_CONTRACTS.get("usdg") or ""):
        return "USDG"
    for symbol, configured_address in _MAINNET_TEST_ADDRESS_BY_SYMBOL.items():
        if normalized == _address(configured_address):
            return symbol
    symbol = _symbol(fallback_symbol)
    return symbol or (normalized[:10].upper() if normalized and normalized.startswith("0x") else "")


def _token_from_pool(pool: dict[str, Any], side: int) -> dict[str, Any]:
    address = _address(pool.get(f"token{side}") or pool.get(f"currency{side}"))
    return {
        "symbol": _known_symbol(address, pool.get(f"token{side}Symbol")),
        "address": address,
        "decimals": pool.get(f"token{side}Decimals") or pool.get(f"token{side}_decimals"),
    }


def _pool_row(pool: dict[str, Any], *, source: str) -> dict[str, Any] | None:
    pool_id = _pool_id(pool)
    token0 = _token_from_pool(pool, 0)
    token1 = _token_from_pool(pool, 1)
    if not pool_id or not token0["address"] or not token1["address"] or not token0["symbol"] or not token1["symbol"]:
        return None
    return {
        "pool": pool_id,
        "poolId": pool_id,
        "poolIdentityMode": "v4_pool_id",
        "protocol": "uniswap_v4",
        "dex": "uniswap_v4",
        "fee": pool.get("fee"),
        "dynamicFee": _pool_dynamic_fee(pool),
        "specialFee": _pool_special_fee(pool),
        "feeLabel": _pool_fee_label(pool),
        "usableForObservation": _pool_usable_for_observation(pool),
        "tickSpacing": pool.get("tickSpacing"),
        "hooks": pool.get("hooks"),
        "poolManager": pool.get("poolManager") or pool.get("pool_manager"),
        "poolKey": pool.get("poolKey") or pool.get("pool_key"),
        "token0": token0["address"],
        "token1": token1["address"],
        "token0Symbol": token0["symbol"],
        "token1Symbol": token1["symbol"],
        "token0Decimals": token0.get("decimals"),
        "token1Decimals": token1.get("decimals"),
        "sqrtPriceX96": pool.get("sqrtPriceX96"),
        "tick": pool.get("tick"),
        "liquidity": pool.get("liquidity") or "",
        "liquidityIndex": pool.get("liquidityIndex"),
        "hasLiquidity": pool.get("hasLiquidity"),
        "estimatedTvlUsd": pool.get("estimatedTvlUsd"),
        "token0Reserve": pool.get("token0Reserve"),
        "token1Reserve": pool.get("token1Reserve"),
        "liquiditySource": pool.get("liquiditySource"),
        "liquidityReadAt": pool.get("liquidityReadAt"),
        "liquidityBlockNumber": pool.get("liquidityBlockNumber"),
        "liquidityError": pool.get("liquidityError"),
        "balances": pool.get("balances") or {"tvlUsd": pool.get("tvlUsd") or pool.get("estimatedTvlUsd") or 0},
        "depthBand": pool.get("depthBand") or {"level": "indexed", "label": "indexed V4 PoolKey"},
        "depthStatus": pool.get("depthStatus") or "",
        "symbolPair": f"{token0['symbol']}:{token1['symbol']}",
        "addressPair": f"{token0['address']}:{token1['address']}",
        "source": source,
        "status": pool.get("status") or "initialized_pool",
        "recentSwapActive": pool.get("recentSwapActive"),
        "evidence": {
            **(pool.get("evidence") or {}),
            "source": source,
            "bridgeOnly": True,
            "poolKeyDecoded": bool(pool.get("poolKeyDecoded") or pool.get("poolKey")),
            "initializeBlockNumber": pool.get("initializeBlockNumber"),
            "initializeTransactionHash": pool.get("initializeTransactionHash"),
        },
    }


def _load_mainnet_v4_index() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    global _MAINNET_V4_INDEX_CACHE
    if _MAINNET_V4_INDEX_CACHE is not None:
        cached = dict(_MAINNET_V4_INDEX_CACHE)
        pools = list(cached.get("pools") or [])
        return pools, cached
    path = default_v4_pool_index_path()
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8-sig")) if path else {}
    except Exception:
        payload = {}
    if (
        not isinstance(payload, dict)
        or str(payload.get("network") or "").strip().lower() != "robinhood"
        or int(payload.get("chainId") or payload.get("chain_id") or 0) != ROBINHOOD_CHAIN_ID
    ):
        _MAINNET_V4_INDEX_CACHE = {"pools": [], "payload": payload or {}}
        return [], payload or {}
    pools = [_pool_row(pool, source="robinhood_mainnet_v4_pool_index") for pool in payload.get("pools") or [] if isinstance(pool, dict)]
    pools = [pool for pool in pools if pool is not None]
    _MAINNET_V4_INDEX_CACHE = {"pools": pools, "payload": payload}
    return pools, payload


def _eth_anchor_neighbors(pools: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    eth_address = _address(ZERO_ADDRESS)
    neighbors: dict[str, dict[str, Any]] = {}
    for pool in pools:
        edge = _pool_edge(pool)
        if edge is None or eth_address not in edge:
            continue
        other = edge[0] if edge[1] == eth_address else edge[1]
        other_side = 0 if _address(pool.get("token0") or pool.get("currency0")) == other else 1
        symbol = _known_symbol(other, pool.get(f"token{other_side}Symbol"))
        if symbol:
            neighbors.setdefault(other, {"symbol": symbol, "address": other, "poolCount": 0})
            neighbors[other]["poolCount"] = neighbors[other].get("poolCount") or 0
            neighbors[other]["poolCount"] += 1
    return neighbors


def _pools_by_edge(pools: list[dict[str, Any]]) -> dict[tuple[str, str], list[dict[str, Any]]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for pool in pools:
        edge = _pool_edge(pool)
        if edge is None:
            continue
        grouped.setdefault(edge, []).append(pool)
    return grouped


def _eth_to_token_pool(anchor_address: str, token_address: str, pools_by_edge: dict[tuple[str, str], list[dict[str, Any]]]) -> dict[str, Any] | None:
    edge = tuple(sorted((anchor_address, token_address)))
    rows = pools_by_edge.get(edge) or []
    rows.sort(
        key=lambda row: (
            _number_liquidity(row),
            float((row.get("balances") or {}).get("tvlUsd") or row.get("estimatedTvlUsd") or 0),
            str(_pool_id(row)),
        ),
        reverse=True,
    )
    return rows[0] if rows else None


def _mainnet_eth_observation_sequences(
    pools: list[dict[str, Any]],
    *,
    route_limit: int | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    pools_by_edge = _pools_by_edge(pools)
    neighbors = _eth_anchor_neighbors(pools)
    eth_address = _address(ZERO_ADDRESS)
    eth_token = {"symbol": "ETH", "address": eth_address}
    token_by_address = {
        _address(pool.get("token0")): _token_from_pool(pool, 0)
        for pool in pools
    }
    token_by_address.update(
        {
            _address(pool.get("token1")): _token_from_pool(pool, 1)
            for pool in pools
        }
    )

    sequences: list[dict[str, Any]] = []
    bridge_pairs_by_edge: dict[tuple[str, str], dict[str, Any]] = {}
    seen: set[tuple[str, str, str]] = set()

    def _ordered_pools(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        candidates = [row for row in rows if isinstance(row, dict) and _pool_id(row)]
        candidates.sort(
            key=lambda row: (
                not _pool_usable_for_observation(row),
                not bool(row.get("recentSwapActive")),
                -_number_liquidity(row),
                -(float((row.get("balances") or {}).get("tvlUsd") or row.get("estimatedTvlUsd") or 0)),
                str(_pool_id(row)),
            ),
        )
        return candidates

    def _hop_candidates(
        *,
        leg: str,
        direction: str,
        from_token: dict[str, Any],
        to_token: dict[str, Any],
        pools: list[dict[str, Any]],
    ) -> dict[str, Any]:
        rows = [
            _step_row(pool, from_token=from_token, to_token=to_token, direction=direction)
            for pool in _ordered_pools(pools)
        ]
        usable_rows = [row for row in rows if _pool_usable_for_observation(row)]
        preferred = usable_rows[0] if usable_rows else (rows[0] if rows else None)
        return {
            "leg": leg,
            "direction": direction,
            "fromSymbol": _symbol(from_token.get("symbol")),
            "toSymbol": _symbol(to_token.get("symbol")),
            "fromAddress": _address(from_token.get("address")),
            "toAddress": _address(to_token.get("address")),
            "symbolPair": f"{_symbol(from_token.get('symbol'))} -> {_symbol(to_token.get('symbol'))}",
            "poolCount": len(rows),
            "usablePoolCount": len(usable_rows),
            "selectionBasis": "tvl_depth_desc",
            "preferredPoolId": _pool_id(preferred) if preferred else "",
            "preferredPoolUsable": bool(usable_rows),
            "pools": rows,
        }

    def _step_row(pool: dict[str, Any], *, from_token: dict[str, Any], to_token: dict[str, Any], direction: str) -> dict[str, Any]:
        from_symbol = _symbol(from_token.get("symbol"))
        to_symbol = _symbol(to_token.get("symbol"))
        from_address = _address(from_token.get("address"))
        to_address = _address(to_token.get("address"))
        return {
            "pool": _pool_id(pool),
            "poolId": _pool_id(pool),
            "direction": direction,
            "anchorSymbol": from_symbol,
            "anchorAddress": from_address,
            "counterSymbol": to_symbol,
            "counterAddress": to_address,
            "routeSymbols": [from_symbol, to_symbol],
            "routeAddresses": [from_address, to_address],
            "routeText": f"{from_symbol} -> {to_symbol}",
            "poolIdentityMode": pool.get("poolIdentityMode") or "v4_pool_id",
            "protocol": pool.get("protocol") or pool.get("dex") or "uniswap_v4",
            "dex": pool.get("dex") or pool.get("protocol") or "uniswap_v4",
            "fee": pool.get("fee"),
            "dynamicFee": _pool_dynamic_fee(pool),
            "specialFee": _pool_special_fee(pool),
            "feeLabel": _pool_fee_label(pool),
            "usableForObservation": _pool_usable_for_observation(pool),
            "tickSpacing": pool.get("tickSpacing"),
            "hooks": pool.get("hooks") or "",
            "liquidity": pool.get("liquidity") or "",
            "liquidityIndex": pool.get("liquidityIndex"),
            "hasLiquidity": pool.get("hasLiquidity"),
            "estimatedTvlUsd": pool.get("estimatedTvlUsd"),
            "token0Reserve": pool.get("token0Reserve"),
            "token1Reserve": pool.get("token1Reserve"),
            "liquiditySource": pool.get("liquiditySource"),
            "liquidityReadAt": pool.get("liquidityReadAt"),
            "liquidityBlockNumber": pool.get("liquidityBlockNumber"),
            "liquidityError": pool.get("liquidityError"),
            "depthStatus": pool.get("depthStatus") or "",
            "depthBand": pool.get("depthBand") or {},
            "poolManager": pool.get("poolManager") or pool.get("pool_manager"),
            "poolKey": pool.get("poolKey") or pool.get("pool_key"),
            "quality": pool.get("quality"),
            "classification": pool.get("classification") or {},
            "balances": pool.get("balances") or {},
            "depthBand": pool.get("depthBand") or {},
            "depthStatus": pool.get("depthStatus") or "",
            "quoteSummary": pool.get("quoteSummary") or {},
            "tick": pool.get("tick"),
            "sqrtPriceX96": pool.get("sqrtPriceX96"),
            "liquidity": pool.get("liquidity"),
            "token0": pool.get("token0"),
            "token1": pool.get("token1"),
            "token0Symbol": pool.get("token0Symbol"),
            "token1Symbol": pool.get("token1Symbol"),
            "token0Decimals": pool.get("token0Decimals") or pool.get("token0_decimals"),
            "token1Decimals": pool.get("token1Decimals") or pool.get("token1_decimals"),
            "pairSymbol": pool.get("symbolPair") or "",
            "pairAddress": pool.get("addressPair") or "",
            "source": pool.get("source") or "",
            "evidence": pool.get("evidence") or {},
            "recentSwapActive": pool.get("recentSwapActive"),
        }

    for y_address, y_token in sorted(neighbors.items(), key=lambda item: (str(item[1].get("symbol") or ""), str(item[0] or ""))):
        for z_address, z_token in sorted(neighbors.items(), key=lambda item: (str(item[1].get("symbol") or ""), str(item[0] or ""))):
            if z_address == y_address:
                continue
            yz_pool = _eth_to_token_pool(y_address, z_address, pools_by_edge)
            if yz_pool is None:
                continue
            xy_pool = _eth_to_token_pool(eth_address, y_address, pools_by_edge)
            zx_pool = _eth_to_token_pool(eth_address, z_address, pools_by_edge)
            if xy_pool is None or zx_pool is None:
                continue
            key = (y_address, z_address, _pool_id(yz_pool))
            if key in seen:
                continue
            seen.add(key)
            route_rows = [
                _step_row(xy_pool, from_token=eth_token, to_token=y_token, direction="yc"),
                _step_row(yz_pool, from_token=y_token, to_token=z_token, direction="bridge"),
                _step_row(zx_pool, from_token=z_token, to_token=eth_token, direction="zc"),
            ]
            path_symbols = ["ETH", _symbol(y_token.get("symbol")), _symbol(z_token.get("symbol")), "ETH"]
            xy_rows = _ordered_pools(pools_by_edge.get(tuple(sorted((eth_address, y_address)))) or [])
            yz_rows = _ordered_pools(pools_by_edge.get(tuple(sorted((y_address, z_address)))) or [])
            zx_rows = _ordered_pools(pools_by_edge.get(tuple(sorted((z_address, eth_address)))) or [])
            hop_candidates = [
                _hop_candidates(
                    leg="x-y",
                    direction="yc",
                    from_token=eth_token,
                    to_token=y_token,
                    pools=xy_rows,
                ),
                _hop_candidates(
                    leg="y-z",
                    direction="bridge",
                    from_token=y_token,
                    to_token=z_token,
                    pools=yz_rows,
                ),
                _hop_candidates(
                    leg="z-x",
                    direction="zc",
                    from_token=z_token,
                    to_token=eth_token,
                    pools=zx_rows,
                ),
            ]
            hop_candidate_counts = {
                "x-y": len(xy_rows),
                "y-z": len(yz_rows),
                "z-x": len(zx_rows),
            }
            sequences.append(
                {
                    "kind": "robinhood_mainnet_eth_anchor_closed_loop",
                    "source": "robinhood_mainnet_v4_pool_index",
                    "anchorSymbol": "ETH",
                    "anchorAddress": eth_address,
                    "counterSymbol": _symbol(z_token.get("symbol")),
                    "bridgeSymbol": _symbol(y_token.get("symbol")),
                    "pathSymbols": path_symbols,
                    "pathText": " -> ".join(path_symbols),
                    "pool": _pool_id(yz_pool),
                    "ycPool": route_rows[0],
                    "bridgePool": route_rows[1],
                    "zcPool": route_rows[2],
                    "routePools": route_rows,
                    "hopCandidates": hop_candidates,
                    "hopCandidateCounts": hop_candidate_counts,
                    "hopCandidateCombinationCount": max(1, len(xy_rows) * len(yz_rows) * len(zx_rows)),
                    "candidatePoolCount": "3",
                    "matchedPoolCount": "3",
                    "anchorAddresses": [eth_address],
                    "counterTokenCount": "2",
                    "executorSupported": False,
                    "poolManagerBalance": None,
                    "poolManagerBalanceRaw": None,
                }
            )
            bridge_edge = tuple(sorted((y_address, z_address)))
            if bridge_edge not in bridge_pairs_by_edge:
                yz_bridge_pools = pools_by_edge.get(tuple(sorted((y_address, z_address)))) or []
                bridge_pairs_by_edge[bridge_edge] = {
                    "symbolPair": f"{_symbol(y_token.get('symbol'))}:{_symbol(z_token.get('symbol'))}",
                    "addressPair": f"{y_address}:{z_address}",
                    "tokenX": y_address,
                    "tokenY": z_address,
                    "tokenXSymbol": _symbol(y_token.get("symbol")),
                    "tokenYSymbol": _symbol(z_token.get("symbol")),
                    "preferredPools": yz_bridge_pools,
                    "executionPools": yz_bridge_pools[:5],
                    "strictQualityPools": yz_bridge_pools,
                    "classification": {
                        "source": "robinhood_mainnet_v4_pool_index",
                        "bridgeOnly": True,
                        "protocol": "uniswap_v4",
                    },
                }
            if route_limit is not None and len(sequences) >= route_limit:
                return sequences, list(bridge_pairs_by_edge.values())
    return sequences, list(bridge_pairs_by_edge.values())


def _mainnet_eth_closed_loop_sequences() -> list[dict[str, Any]]:
    """Return the complete ETH-anchored closed-loop sequence set.

    The returned rows are the authoritative closed-loop handoff consumed by the
    DEX overview. No fixed route limit is applied here; the upper bound is the
    graph itself (every ETH-paired Y/Z pair with an existing Y-Z pool).
    """
    pools, _index = _load_mainnet_v4_index()
    sequences, _bridge_pairs = _mainnet_eth_observation_sequences(pools)
    return sequences


def _mainnet_eth_bridge_pairs(
    pools: list[dict[str, Any]],
    *,
    route_limit: int | None = None,
) -> list[dict[str, Any]]:
    return _mainnet_eth_observation_sequences(pools, route_limit=route_limit)[1]


def _mainnet_eth_fund_anchor_observation(
    pools: list[dict[str, Any]],
    *,
    route_limit: int | None = None,
) -> dict[str, Any]:
    sequences, bridge_pairs = _mainnet_eth_observation_sequences(pools, route_limit=route_limit)
    pools_by_edge = _pools_by_edge(pools)
    neighbors = _eth_anchor_neighbors(pools)
    eth_pair_token_count = _mainnet_eth_pair_token_count()
    candidate_yz_pair_count = eth_pair_token_count * max(0, eth_pair_token_count - 1)
    verified_yz_pair_count = 0
    for edge in pools_by_edge:
        if _address(edge[0]) == _address(ZERO_ADDRESS) or _address(edge[1]) == _address(ZERO_ADDRESS):
            continue
        if _address(edge[0]) in neighbors and _address(edge[1]) in neighbors:
            verified_yz_pair_count += 1
    bridge_pool_ids = sorted(
        {
            _pool_id(pool)
            for pair in bridge_pairs
            for pool in pair.get("strictQualityPools") or []
            if _pool_id(pool)
        }
    )
    active_pool_ids = sorted(
        _active_pool_ids(pools)
    )
    index, _index_payload = _load_mainnet_v4_index()
    indexed_pool_count = len(index)
    return {
        "enabled": True,
        "status": "robinhood_mainnet_v4_eth_anchor_observation",
        "source": "robinhood_mainnet_v4_pool_index",
        "recentSwapFilterApplied": False,
        "liquidityFilterApplied": True,
        "recentSwapActivePoolCount": len(active_pool_ids),
        "liquidPoolIdCount": len(active_pool_ids),
        "recentSwapIndexedPoolCount": indexed_pool_count,
        "activePoolIds": active_pool_ids,
        "liquidPoolIds": active_pool_ids,
        "ethAnchor": "ETH",
        "routeCount": len(sequences),
        "associatedObservationPairCount": len(bridge_pairs),
        "associatedObservationPoolCount": len(bridge_pool_ids),
        "associatedObservationTokenCount": len(_eth_anchor_neighbors(pools)),
        "candidateEthPairTokenCount": eth_pair_token_count,
        "candidateYzPairCount": candidate_yz_pair_count,
        "verifiedYzPairCount": verified_yz_pair_count,
        "missingYzPairCount": max(0, candidate_yz_pair_count - verified_yz_pair_count),
        "verifiedEthPairPoolTokenCount": len(neighbors),
        "sourceTokenEvidence": "robinhood_eth_pair_tokens",
        "observationSequences": sequences,
        "closedLoopSequences": sequences,
        "associatedObservationPairs": [],
        "associatedObservationPools": [],
        "associatedObservationTokens": [],
        "ethEthPairCount": 0,
        "bridgePairCount": len(bridge_pairs),
        "matchedPoolCount": sum(len(pair.get("preferredPools") or []) for pair in bridge_pairs),
        "matchedPoolCountByEdge": {f"{edge[0][:10]}:{edge[1][:10]}": len(rows) for edge, rows in pools_by_edge.items()},
        "summary": {
            "routeCount": len(sequences),
            "candidateEthPairTokenCount": eth_pair_token_count,
            "candidateYzPairCount": candidate_yz_pair_count,
            "verifiedYzPairCount": verified_yz_pair_count,
            "missingYzPairCount": max(0, candidate_yz_pair_count - verified_yz_pair_count),
            "verifiedEthPairPoolTokenCount": len(neighbors),
        },
    }


def observation_anchor_symbols(
    pool_payload: dict[str, Any],
    *,
    chain_id: int | None,
    protocols: list[str] | None = None,
) -> list[str]:
    if not _chain_in_scope(chain_id) or not _protocol_in_scope(protocols):
        return []
    # Mainnet observation is anchored to native ETH, verified stable
    # settlement assets, and any test-token anchors configured through env
    # variables. The test-token entries are deliberately data-driven rather
    # than route-specific so the same closed-loop logic handles them without
    # hard-coding TZ/TA/TB in the graph builder.
    return ["ETH", *MAINNET_STABLE_ANCHOR_SYMBOLS, *MAINNET_TEST_ANCHOR_SYMBOLS]


def borrowable_symbols_from_pool_payload(
    pool_payload: dict[str, Any],
    *,
    chain_id: int | None,
    protocols: list[str] | None = None,
) -> list[str]:
    if not _chain_in_scope(chain_id) or not _protocol_in_scope(protocols):
        return []
    return ["ETH", *MAINNET_STABLE_ANCHOR_SYMBOLS, *MAINNET_TEST_ANCHOR_SYMBOLS]


def bridge_pairs_for_anchor_groups(
    anchor_groups: list[dict[str, Any]],
    *,
    chain_id: int | None,
    protocols: list[str] | None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if not _chain_in_scope(chain_id) or not _protocol_in_scope(protocols):
        return [], {"enabled": False, "status": "chain_scope_invalid"}
    pools, _index = _load_mainnet_v4_index()
    bridge_pairs = _mainnet_eth_bridge_pairs(pools)
    eth_pair_token_count = _mainnet_eth_pair_token_count()
    candidate_yz_pair_count = eth_pair_token_count * max(0, eth_pair_token_count - 1)
    pools_by_edge = _pools_by_edge(pools)
    neighbors = _eth_anchor_neighbors(pools)
    verified_yz_pair_count = 0
    for edge in pools_by_edge:
        if _address(edge[0]) == _address(ZERO_ADDRESS) or _address(edge[1]) == _address(ZERO_ADDRESS):
            continue
        if _address(edge[0]) in neighbors and _address(edge[1]) in neighbors:
            verified_yz_pair_count += 1
    active_pool_ids = sorted(_active_pool_ids(pools))
    return bridge_pairs, {
        "enabled": True,
        "status": "robinhood_mainnet_v4_eth_anchor_bridge_observation",
        "ethAnchor": "ETH",
        "recentSwapFilterApplied": False,
        "liquidityFilterApplied": True,
        "recentSwapActivePoolCount": len(active_pool_ids),
        "liquidPoolIdCount": len(active_pool_ids),
        "activePoolIds": active_pool_ids,
        "liquidPoolIds": active_pool_ids,
        "bridgeCount": len(bridge_pairs),
        "matchedPoolCount": sum(len(pair.get("preferredPools") or []) for pair in bridge_pairs),
        "candidateEthPairTokenCount": eth_pair_token_count,
        "candidateYzPairCount": candidate_yz_pair_count,
        "verifiedYzPairCount": verified_yz_pair_count,
        "missingYzPairCount": max(0, candidate_yz_pair_count - verified_yz_pair_count),
        "verifiedEthPairPoolTokenCount": len(neighbors),
        "sourceTokenEvidence": "robinhood_eth_pair_tokens",
    }


def fund_anchor_pair_observation(
    *,
    chain_id: int | None,
    protocols: list[str] | None,
) -> dict[str, Any]:
    if not _chain_in_scope(chain_id) or not _protocol_in_scope(protocols):
        return {
            "enabled": False,
            "status": "chain_scope_invalid",
            "routeCount": 0,
            "associatedObservationPairCount": 0,
            "associatedObservationPoolCount": 0,
            "associatedObservationTokenCount": 0,
            "observationSequences": [],
            "associatedObservationPairs": [],
            "associatedObservationPools": [],
            "associatedObservationTokens": [],
        }
    pools, index = _load_mainnet_v4_index()
    return _mainnet_eth_fund_anchor_observation(pools)


__all__ = [
    "borrowable_symbols_from_pool_payload",
    "bridge_pairs_for_anchor_groups",
    "fund_anchor_pair_observation",
    "observation_anchor_symbols",
]
