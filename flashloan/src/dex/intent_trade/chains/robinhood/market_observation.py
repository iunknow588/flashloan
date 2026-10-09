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

from core.chain_data_registry import chain_data_by_network, chain_data_file_path
from core.market_observation_io import market_observation_static_path as _market_observation_static_path
from core.market_observation_io import _stamp
from dex.intent_trade.chains.robinhood.config import (
    CHAIN_ID as ROBINHOOD_CHAIN_ID,
    WETH_ADDRESS,
)
from dex.intent_trade.chains.robinhood.v4_pool_index import (
    ZERO_ADDRESS,
    default_v4_pool_index_path,
)
from dex.intent_trade.chains.robinhood.v4_pool_snapshot import load_v4_pool_snapshot
_ROBINHOOD_ROW = chain_data_by_network("robinhood") or {}
_ROBINHOOD_CONTRACTS = _ROBINHOOD_ROW.get("contracts") if isinstance(_ROBINHOOD_ROW.get("contracts"), dict) else {}

_ETH_PAIR_TOKENS_META_CACHE: dict[str, Any] | None = None
_MAINNET_V4_INDEX_CACHE: dict[str, Any] | None = None

V4_DYNAMIC_FEE_FLAG = 8388608
V4_SPECIAL_FEE_THRESHOLD = 100_000
MAINNET_STABLE_ANCHOR_SYMBOLS = ("USDG", "USDE")
MAINNET_CONFIGURED_ANCHOR_SYMBOLS = ("ETH", "WETH", *MAINNET_STABLE_ANCHOR_SYMBOLS, "TZ")

_MAINNET_TEST_MANIFEST_PATH = Path(__file__).resolve().parents[6] / "test-token_create" / "config" / "mainnet-v4-amm" / "manifest.json"

_MAINNET_STABLE_MANIFEST_PATH = Path(__file__).resolve().parents[6] / "test-token_create" / "config" / "mainnet-v4-amm" / "manifest.json"

# Per-hop candidate lists are the quote/routing replacement universe. They are
# small because the candidate cache already keeps a representative set: max
# depth, high/low price extremes, and low-fee liquidity. Quote/routing still
# receives every kept pool for price-based temporary replacement.
_MAINNET_HOP_CANDIDATE_LIMIT = 6
INTERMEDIATE_POOL_FILES_DIR = "intermediate_pool_files"
INTERMEDIATE_POOL_FILE_SCHEMA = "robinhood-mainnet-intermediate-swap-pools-v1"


def _mainnet_static_candidate_path() -> Path | None:
    try:
        path = chain_data_file_path("robinhood", "dex_candidates", chain_id=ROBINHOOD_CHAIN_ID)
        if path is not None:
            return Path(path)
    except (TypeError, ValueError, OSError):
        pass
    fallback = Path(__file__).resolve().parents[4] / "runtime" / "cache" / "robinhood" / "robinhood_dex_candidates.json"
    return fallback if fallback.exists() else None


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


def _mainnet_stable_token_addresses() -> dict[str, str]:
    """Resolve USDG/USDE from the same manifest used by candidate pools.

    The chain row can contain checksummed addresses, but the mainnet V4 index
    rows are lowercased. Keeping the same manifest source makes the anchor
    universe consistent across candidate pools, market observation and flash
    loan pool views.
    """
    manifest: dict[str, Any] = {}
    try:
        payload = json.loads(_MAINNET_STABLE_MANIFEST_PATH.read_text(encoding="utf-8"))
        manifest = payload if isinstance(payload, dict) else {}
    except (OSError, ValueError, json.JSONDecodeError):
        manifest = {}
    result: dict[str, str] = {}
    for raw in manifest.get("stableTokens") or []:
        if not isinstance(raw, dict):
            continue
        symbol = str(raw.get("symbol") or "").strip().upper()
        raw_address = str(raw.get("address") or "").strip()
        address = raw_address.lower() if raw_address.startswith("0x") and len(raw_address) == 42 else ""
        if symbol and address:
            result[symbol] = address
    raw_usdg = str(_ROBINHOOD_CONTRACTS.get("usdg") or "").strip()
    usdg = raw_usdg.lower() if raw_usdg.startswith("0x") and len(raw_usdg) == 42 else ""
    if usdg and "USDG" not in result:
        result["USDG"] = usdg
    if "USDE" not in result:
        result["USDE"] = "0x5d3a1ff2b6bab83b63cd9ad0787074081a52ef34"
    return {symbol: address for symbol, address in result.items() if address}


_MAINNET_STABLE_ADDRESS_BY_SYMBOL = _mainnet_stable_token_addresses()


def _mainnet_configured_anchor_addresses() -> set[str]:
    """Return the exact five-anchor mainnet address set."""
    addresses = {
        _address(ZERO_ADDRESS),
        _address(WETH_ADDRESS),
    }
    addresses.update(_MAINNET_STABLE_ADDRESS_BY_SYMBOL.values())
    tz_address = _MAINNET_TEST_ADDRESS_BY_SYMBOL.get("TZ")
    if tz_address:
        addresses.add(_address(tz_address))
    return {address for address in addresses if address}


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
        return True
    return bool(fee & V4_DYNAMIC_FEE_FLAG) or fee > V4_SPECIAL_FEE_THRESHOLD


def _pool_fee_label(pool: dict[str, Any]) -> str | int | None:
    if _pool_dynamic_fee(pool):
        return "dynamic"
    if _pool_special_fee(pool):
        return "special"
    return _pool_fee(pool)


def _pool_usable_for_observation(pool: dict[str, Any]) -> bool:
    fee = _pool_fee(pool)
    if fee is None or fee <= 0 or _pool_special_fee(pool):
        return False
    hooks = str(pool.get("hooks") or "").strip()
    if hooks and hooks.lower() != ZERO_ADDRESS.lower():
        # A non-zero hook can intercept beforeSwap/beforeSwapReturnDelta and
        # charge hidden fees or revert the official runArbitrage path. Market
        # observation may still display these pools as candidates, but they are
        # not executable fixed-fee closed-loop hops until hook semantics are
        # modelled. This keeps the recommended first-priority route on
        # static-fee pools with no arbitrary callback.
        return False
    has_liquidity = pool.get("hasLiquidity")
    if has_liquidity is not None:
        return bool(has_liquidity) or _number_liquidity(pool) > 0
    return _number_liquidity(pool) > 0


def _mainnet_adjacent_token_universe(
    pools: list[dict[str, Any]],
    *,
    anchor_addresses: set[str],
) -> dict[str, dict[str, Any]]:
    """Return the YC/ZC token universe used by market observation.

    Candidate pools provide the first/third-hop display surface.  Market
    observation owns the middle-hop surface, so it builds the Y-Z universe
    from every token that shares an edge with a configured anchor and keeps at
    least one pool with observed liquidity.  Tokens are keyed by their address
    (V4 PoolKey currency addresses are the stable identity) rather than by
    symbol, because the Robinhood mainnet index contains same-symbol tokens at
    distinct addresses.
    """
    pools_by_edge = _pools_by_edge(pools)
    adjacent = _anchor_adjacent_tokens(pools, anchor_addresses=anchor_addresses)
    universe: dict[str, dict[str, Any]] = {}
    for address, token in adjacent.items():
        if not address or address in anchor_addresses:
            continue
        edge_pools = pools_by_edge.get(tuple(sorted((next(iter(anchor_addresses)), address)))) or []
        has_depth = any(_pool_has_depth(pool) for pool in edge_pools)
        # Also allow a middle token that is only liquid through a non-anchor
        # edge, for example a token that reaches ETH through a stable bridge.
        if not has_depth:
            has_depth = any(
                _pool_has_depth(pool)
                for edge, rows in pools_by_edge.items()
                for pool in rows
                if address in edge and not (edge[0] in anchor_addresses and edge[1] in anchor_addresses)
            )
        if has_depth:
            universe[address] = token
    return universe


def _pool_has_depth(pool: dict[str, Any]) -> bool:
    """Return True when a pool has any observed on-chain liquidity.

    Market observation keeps every liquid pool, including dynamic/special fee
    pools, so the page can display the full YC-ZC depth surface and mark
    ``dynamicFee``/``specialFee`` explicitly.  Fixed-fee filtering remains the
    closed-loop/overview responsibility.
    """
    has_liquidity = pool.get("hasLiquidity")
    if has_liquidity is not None:
        return bool(has_liquidity) or _number_liquidity(pool) > 0
    liquidity = _number_liquidity(pool)
    balances = pool.get("balances") if isinstance(pool.get("balances"), dict) else {}
    tvl = balances.get("tvlUsd") or pool.get("estimatedTvlUsd")
    try:
        tvl_number = float(tvl) if tvl not in (None, "") else 0.0
    except (TypeError, ValueError):
        tvl_number = 0.0
    return liquidity > 0 or tvl_number > 0


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
    if normalized == _MAINNET_STABLE_ADDRESS_BY_SYMBOL.get("USDE"):
        return "USDE"
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


def _load_mainnet_static_candidate_pools() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Return the static candidate cache as observation pool rows."""
    path = _mainnet_static_candidate_path()
    if path is None or not path.exists():
        return [], {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError, json.JSONDecodeError):
        return [], {}
    if not isinstance(payload, dict):
        return [], {}
    pools = [
        _pool_row(pool, source="robinhood_mainnet_static_dex_candidates")
        for pool in payload.get("pools") or []
        if isinstance(pool, dict)
    ]
    pools = [pool for pool in pools if pool is not None]
    return pools, payload


def _load_mainnet_observation_pools() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Return observation pools without collapsing the closed-loop graph.

    The static candidate handoff is the first/third-hop display surface and
    must not shrink the middle-hop graph. Market observation therefore merges
    the static V4 PoolKey registry with its dynamic liquidity snapshot so every
    YC-ZC edge remains discoverable. The candidate cache still controls
    first/third-hop ordering on the candidate page.

    Market observation intentionally keeps every pool with liquidity, including
    dynamic/special fee pools, and marks ``dynamicFee``/``specialFee`` on each
    row. Only the closed-loop construction filters those out so the DEX
    overview/quote handoff receives fixed-fee liquid pools.
    """
    pools, payload = _load_mainnet_static_candidate_pools()
    snapshot_payload = load_v4_pool_snapshot(merge_liquidity=True)
    snapshot_pools = [
        _pool_row(pool, source="robinhood_mainnet_v4_pool_snapshot")
        for pool in snapshot_payload.get("pools") or []
        if isinstance(pool, dict)
    ]
    snapshot_pools = [pool for pool in snapshot_pools if pool is not None]
    index_payload = {
        **{
            key: value
            for key, value in snapshot_payload.items()
            if key not in {"pools", "poolsByAddressPair", "liquidity"}
        },
        "pools": snapshot_pools,
        "source": "v4_pool_snapshot_plus_liquidity_snapshot",
    }
    if not pools:
        pools = snapshot_pools
    else:
        by_pool_id = {str(_pool_id(pool)).lower(): pool for pool in pools if _pool_id(pool)}
        for pool in snapshot_pools:
            pool_id = str(_pool_id(pool)).lower()
            if pool_id and pool_id not in by_pool_id:
                by_pool_id[pool_id] = pool
        pools = list(by_pool_id.values())
    return pools, {
        **index_payload,
        "observationSource": "static_candidates_plus_v4_pool_snapshot_all_liquid_with_fee_flags",
    }


def _anchor_neighbors(
    pools: list[dict[str, Any]],
    *,
    anchor_addresses: set[str],
) -> dict[str, dict[str, Any]]:
    neighbors: dict[str, dict[str, Any]] = {}
    for pool in pools:
        edge = _pool_edge(pool)
        if edge is None:
            continue
        for anchor_address in tuple(anchor_addresses):
            if anchor_address not in edge:
                continue
            other = edge[0] if edge[1] == anchor_address else edge[1]
            if other in anchor_addresses:
                continue
            other_side = 0 if _address(pool.get("token0") or pool.get("currency0")) == other else 1
            symbol = _known_symbol(other, pool.get(f"token{other_side}Symbol"))
            if symbol:
                neighbors.setdefault(other, {"symbol": symbol, "address": other, "poolCount": 0})
                neighbors[other]["poolCount"] = neighbors[other].get("poolCount") or 0
                neighbors[other]["poolCount"] += 1
    return neighbors


def _anchor_adjacent_tokens(
    pools: list[dict[str, Any]],
    *,
    anchor_addresses: set[str],
) -> dict[str, dict[str, Any]]:
    """Return every token sharing an edge with a configured anchor.

    Peer anchors are intentionally kept in this set: they are valid middle
    tokens (for example USDE/USDG in ETH -> USDE -> USDG -> ETH), so only the
    alias handling in the loop builder may exclude an address.
    """
    neighbors: dict[str, dict[str, Any]] = {}
    for pool in pools:
        edge = _pool_edge(pool)
        if edge is None:
            continue
        for anchor_address in tuple(anchor_addresses):
            if anchor_address not in edge:
                continue
            other = edge[0] if edge[1] == anchor_address else edge[1]
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


def _anchor_to_token_pool(anchor_address: str, token_address: str, pools_by_edge: dict[tuple[str, str], list[dict[str, Any]]]) -> dict[str, Any] | None:
    edge = tuple(sorted((anchor_address, token_address)))
    rows = pools_by_edge.get(edge) or []
    rows.sort(
        key=lambda row: (
            not _pool_usable_for_observation(row),
            not bool(row.get("recentSwapActive")),
            _number_liquidity(row),
            float((row.get("balances") or {}).get("tvlUsd") or row.get("estimatedTvlUsd") or 0),
            str(_pool_id(row)),
        ),
    )
    return rows[0] if rows else None


def _mainnet_observation_sequences(
    pools: list[dict[str, Any]],
    *,
    route_limit: int | None = None,
    hop_candidate_limit: int | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    pools_by_edge = _pools_by_edge(pools)
    anchor_symbols = list(MAINNET_CONFIGURED_ANCHOR_SYMBOLS)
    anchor_addresses = _mainnet_configured_anchor_addresses()

    anchor_token_by_symbol: dict[str, dict[str, Any]] = {}
    for symbol in anchor_symbols:
        address = ""
        if symbol == "ETH":
            address = _address(ZERO_ADDRESS)
        elif symbol == "WETH":
            address = _address(WETH_ADDRESS)
        elif symbol == "USDG":
            address = _MAINNET_STABLE_ADDRESS_BY_SYMBOL.get("USDG") or ""
        elif symbol == "USDE":
            address = _MAINNET_STABLE_ADDRESS_BY_SYMBOL.get("USDE") or ""
        elif symbol in _MAINNET_TEST_ADDRESS_BY_SYMBOL:
            address = _address(str(_MAINNET_TEST_ADDRESS_BY_SYMBOL.get(symbol) or ""))
        if address:
            anchor_token_by_symbol[symbol] = {"symbol": symbol, "address": address}

    # The middle-hop universe is every token that shares at least one edge with
    # any configured anchor and has at least one observed-liquid pool. Peer
    # anchors stay valid middle tokens, so loops such as
    # ETH -> USDE -> USDG -> ETH are not lost.  Tokens are keyed by address
    # because the V4 index contains same-symbol tokens at distinct addresses.
    neighbors = _mainnet_adjacent_token_universe(
        pools,
        anchor_addresses=anchor_addresses,
    )
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

    def _ordered_pools(rows: list[dict[str, Any]], *, limit: int | None = None) -> list[dict[str, Any]]:
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
        return candidates[:limit] if limit is not None and limit > 0 else candidates

    def _hop_candidates(
        *,
        leg: str,
        direction: str,
        from_token: dict[str, Any],
        to_token: dict[str, Any],
        pools: list[dict[str, Any]],
        pool_limit: int,
    ) -> dict[str, Any]:
        rows = [
            _step_row(pool, from_token=from_token, to_token=to_token, direction=direction)
            for pool in _ordered_pools(pools, limit=pool_limit)
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

    sequences: list[dict[str, Any]] = []
    bridge_pairs_by_edge: dict[tuple[str, str], dict[str, Any]] = {}
    seen_edges: set[tuple[str, tuple[str, ...], tuple[str, ...]]] = set()
    seen_closed_loops: set[tuple[str, tuple[str, ...]]] = set()
    pool_limit = hop_candidate_limit if hop_candidate_limit is not None and hop_candidate_limit > 0 else _MAINNET_HOP_CANDIDATE_LIMIT
    ordered_neighbors = sorted(neighbors.items(), key=lambda item: (str(item[1].get("symbol") or ""), str(item[0] or "")))
    for anchor_symbol, anchor_token in sorted(anchor_token_by_symbol.items()):
        anchor_address = _address(anchor_token.get("address"))
        if not anchor_address:
            continue
        for y_address, y_token in ordered_neighbors:
            if y_address == anchor_address:
                continue
            xy_pool = _anchor_to_token_pool(anchor_address, y_address, pools_by_edge)
            if xy_pool is None or not _pool_usable_for_observation(xy_pool):
                continue
            for z_address, z_token in ordered_neighbors:
                if z_address == y_address or z_address == anchor_address:
                    continue
                zx_pool = _anchor_to_token_pool(anchor_address, z_address, pools_by_edge)
                if zx_pool is None or not _pool_usable_for_observation(zx_pool):
                    continue
                yz_pool = _anchor_to_token_pool(y_address, z_address, pools_by_edge)
                if yz_pool is None or not _pool_usable_for_observation(yz_pool):
                    continue
                canonical_mids = tuple(sorted((y_address, z_address)))
                loop_key = (anchor_address, canonical_mids)
                if loop_key in seen_closed_loops:
                    continue
                seen_closed_loops.add(loop_key)
                # A-B-C-A and A-C-B-A are the same token combination; only pool
                # selection order differs. Keep one canonical mid order per
                # anchor so the handoff is a combination set, not every raw
                # permutation.
                mid_a, mid_b = y_address, z_address
                if mid_a > mid_b:
                    mid_a, mid_b = mid_b, mid_a
                    y_address, y_token, z_address, z_token = z_address, z_token, y_address, y_token
                    xy_pool, zx_pool = zx_pool, xy_pool
                key = (anchor_address, tuple(sorted((_pool_id(xy_pool), _pool_id(yz_pool), _pool_id(zx_pool)))))
                if key in seen_edges:
                    continue
                seen_edges.add(key)
                path_symbols = [
                    _symbol(anchor_token.get("symbol")),
                    _symbol(y_token.get("symbol")),
                    _symbol(z_token.get("symbol")),
                    _symbol(anchor_token.get("symbol")),
                ]
                route_rows = [
                    _step_row(xy_pool, from_token=anchor_token, to_token=y_token, direction="yc"),
                    _step_row(yz_pool, from_token=y_token, to_token=z_token, direction="bridge"),
                    _step_row(zx_pool, from_token=z_token, to_token=anchor_token, direction="zc"),
                ]
                xy_rows = _ordered_pools(pools_by_edge.get(tuple(sorted((anchor_address, y_address)))) or [], limit=pool_limit)
                yz_rows = _ordered_pools(pools_by_edge.get(tuple(sorted((y_address, z_address)))) or [], limit=pool_limit)
                zx_rows = _ordered_pools(pools_by_edge.get(tuple(sorted((z_address, anchor_address)))) or [], limit=pool_limit)
                hop_candidates = [
                    _hop_candidates(
                        leg="x-y",
                        direction="yc",
                        from_token=anchor_token,
                        to_token=y_token,
                        pools=xy_rows,
                        pool_limit=pool_limit,
                    ),
                    _hop_candidates(
                        leg="y-z",
                        direction="bridge",
                        from_token=y_token,
                        to_token=z_token,
                        pools=yz_rows,
                        pool_limit=pool_limit,
                    ),
                    _hop_candidates(
                        leg="z-x",
                        direction="zc",
                        from_token=z_token,
                        to_token=anchor_token,
                        pools=zx_rows,
                        pool_limit=pool_limit,
                    ),
                ]
                sequences.append(
                    {
                        "kind": "robinhood_mainnet_closed_loop",
                        "source": "robinhood_mainnet_v4_pool_index",
                        "anchorSymbol": _symbol(anchor_token.get("symbol")),
                        "anchorAddress": anchor_address,
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
                        "hopCandidateCounts": {
                            "x-y": len(xy_rows),
                            "y-z": len(yz_rows),
                            "z-x": len(zx_rows),
                        },
                        "hopCandidateCombinationCount": max(1, len(xy_rows) * len(yz_rows) * len(zx_rows)),
                        "candidatePoolCount": "3",
                        "matchedPoolCount": "3",
                        "anchorAddresses": [anchor_address],
                        "counterTokenCount": "2",
                        "recommendedFirstPriority": True,
                        "routeRecommendationBasis": "tvl_depth_desc",
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


def _mainnet_closed_loop_sequences() -> list[dict[str, Any]]:
    """Return the complete configured-anchor closed-loop sequence set.

    The returned rows are the authoritative closed-loop handoff consumed by the
    DEX overview. No fixed route limit is applied here; the upper bound is the
    graph itself.
    """
    pools, _index = _load_mainnet_observation_pools()
    sequences, _bridge_pairs = _mainnet_observation_sequences(pools)
    return sequences


def _mainnet_bridge_pairs(
    pools: list[dict[str, Any]],
    *,
    route_limit: int | None = None,
) -> list[dict[str, Any]]:
    return _mainnet_observation_sequences(pools, route_limit=route_limit)[1]


def _mainnet_yz_pairs_from_tokens(
    pools_by_edge: dict[tuple[str, str], list[dict[str, Any]]],
    tokens: list[dict[str, Any]],
    *,
    include_directions: bool = True,
) -> list[dict[str, Any]]:
    """Return complete Y-Z direction or undirected pair rows for a token set.

    Every ordered pair is represented, including pairs that have no pool.  A
    pair's ``addressPair`` is always the address-sorted pair (smaller address
    first) so one Uniswap V4 pool is not duplicated by reverse ordering.
    Direction order is retained through ``tokenX/tokenY`` and symbol fields.
    """
    valid_tokens = [
        {"symbol": _known_symbol(token.get("address"), token.get("symbol")), "address": _address(token.get("address"))}
        for token in tokens
        if isinstance(token, dict) and _address(token.get("address"))
    ]
    if not valid_tokens:
        return []
    seen_symbols: set[str] = set()
    unique_tokens: list[dict[str, Any]] = []
    for token in valid_tokens:
        if token["address"] in {existing["address"] for existing in unique_tokens}:
            continue
        symbol = _symbol(token.get("symbol"))
        if not symbol:
            symbol = f"TOKEN{len(unique_tokens) + 1}"
        if symbol in seen_symbols:
            symbol = f"{symbol}-{token['address'][:10].upper()}"
        seen_symbols.add(symbol)
        unique_tokens.append({"symbol": symbol, "address": token["address"]})
    rows: list[dict[str, Any]] = []
    seen_edges: set[tuple[str, str]] = set()
    for y_token in unique_tokens:
        y_address = _address(y_token.get("address"))
        for z_token in unique_tokens:
            z_address = _address(z_token.get("address"))
            if y_address == z_address:
                continue
            edge = tuple(sorted((y_address, z_address)))
            edge_pools = list(pools_by_edge.get(edge) or [])
            depth_pool_count = sum(1 for pool in edge_pools if _pool_has_depth(pool))
            row = {
                "symbolPair": f"{y_token['symbol']}:{z_token['symbol']}",
                "addressPair": ":".join(edge),
                "tokenX": y_address,
                "tokenY": z_address,
                "tokenXSymbol": y_token["symbol"],
                "tokenYSymbol": z_token["symbol"],
                "token0": edge[0],
                "token1": edge[1],
                "token0Symbol": y_token["symbol"] if edge[0] == y_address else z_token["symbol"],
                "token1Symbol": z_token["symbol"] if edge[1] == z_address else y_token["symbol"],
                "preferredPools": edge_pools,
                "executionPools": edge_pools[:5],
                "strictQualityPools": edge_pools,
                "allPools": edge_pools,
                "poolCount": len(edge_pools),
                "depthPoolCount": depth_pool_count,
                "classification": {
                    "source": "robinhood_mainnet_v4_pool_index",
                    "bridgeOnly": True,
                    "protocol": "uniswap_v4",
                },
            }
            if include_directions:
                row["directionKey"] = f"{y_address}:{z_address}"
                rows.append(row)
            elif edge not in seen_edges:
                seen_edges.add(edge)
                rows.append(row)
    return rows


def _mainnet_observation_yz_pairs(pools: list[dict[str, Any]], tokens: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Return every YC-ZC pair reachable from the configured anchor set.

    The market observation page owns the complete depth surface, so this set
    intentionally includes every pool address, including dynamic/special fee
    pools and pools without a current liquidity snapshot.  Pool addresses are
    fixed on-chain facts; liquidity is dynamic and is refreshed separately.
    Each row keeps ``fee``/``dynamicFee``/``specialFee`` and depth markers;
    the DEX overview and quote routing later choose only fixed-fee liquid
    pools when they build closed loops.
    """
    pools_by_edge = _pools_by_edge(pools)
    if tokens:
        return _mainnet_yz_pairs_from_tokens(pools_by_edge, tokens, include_directions=False)
    anchor_addresses = _mainnet_configured_anchor_addresses()
    neighbors = _mainnet_adjacent_token_universe(
        pools,
        anchor_addresses=anchor_addresses,
    )
    return _mainnet_yz_pairs_from_tokens(
        pools_by_edge,
        [{"symbol": token.get("symbol"), "address": address} for address, token in sorted(neighbors.items())],
        include_directions=False,
    )


def _mainnet_candidate_yc_zc_tokens(group: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Return the candidate page's YC/ZC token set plus order errors.

    The candidate page is the authority for the YC/ZC set.  For Uniswap V4
    YC=ZC, the page stores one shared token list.  Market observation must
    validate it before use; it never silently re-sorts a wrong order.
    """
    if not isinstance(group, dict):
        return [], [{"error": "anchor_group_missing"}]
    anchor_symbol = _symbol(group.get("anchorSymbol"))
    errors: list[dict[str, str]] = []
    shared_tokens = group.get("sharedTokens")
    if isinstance(shared_tokens, list) and (
        group.get("ycTokensRef") == "sharedTokens" or group.get("zcTokensRef") == "sharedTokens" or group.get("ycZcSameSet")
    ):
        tokens = [row for row in shared_tokens if isinstance(row, dict)]
    else:
        tokens = [
            row
            for row in (group.get("ycTokens") or [])
            if isinstance(row, dict)
        ] + [
            row
            for row in (group.get("zcTokens") or [])
            if isinstance(row, dict)
        ]
    normalized: list[dict[str, Any]] = []
    seen_addresses: set[str] = set()
    for row in tokens:
        address = _address(row.get("address"))
        symbol = _symbol(row.get("symbol"))
        if not address:
            errors.append({"anchorSymbol": anchor_symbol, "error": "missing_address", "symbol": symbol})
            continue
        if address in seen_addresses:
            errors.append({"anchorSymbol": anchor_symbol, "error": "duplicate_address", "address": address})
            continue
        seen_addresses.add(address)
        normalized.append({"symbol": symbol or _known_symbol(address, ""), "address": address})
    expected_order = sorted(normalized, key=lambda item: item["address"])
    for index, (actual, expected) in enumerate(zip(normalized, expected_order)):
        if actual["address"] != expected["address"]:
            errors.append(
                {
                    "anchorSymbol": anchor_symbol,
                    "error": "yc_zc_address_order",
                    "index": str(index),
                    "actualAddress": actual["address"],
                    "expectedAddress": expected["address"],
                }
            )
    if len(normalized) != len(expected_order):
        errors.append({"anchorSymbol": anchor_symbol, "error": "yc_zc_address_order", "detail": "token count mismatch"})
    return normalized, errors


def _mainnet_validate_anchor_groups(anchor_groups: list[dict[str, Any]]) -> dict[str, list[dict[str, str]]]:
    """Validate every candidate anchor group before the page consumes it."""
    errors: dict[str, list[dict[str, str]]] = {}
    for group in anchor_groups:
        if not isinstance(group, dict):
            continue
        anchor_symbol = _symbol(group.get("anchorSymbol"))
        if not anchor_symbol:
            continue
        _tokens, group_errors = _mainnet_candidate_yc_zc_tokens(group)
        if group_errors:
            errors[anchor_symbol] = group_errors
    return errors


def _mainnet_intermediate_pool_file_path(anchor_symbol: str) -> Path:
    base = _market_observation_static_path(
        network="robinhood",
        chain_id=ROBINHOOD_CHAIN_ID,
        protocols=["uniswap_v4"],
    ).parent
    return base / INTERMEDIATE_POOL_FILES_DIR / f"{_symbol(anchor_symbol)}.json"


def _mainnet_intermediate_pool_files_complete(
    anchor_groups: list[dict[str, Any]],
) -> tuple[bool, list[dict[str, Any]]]:
    """Check whether every anchor's external swap-pool file is complete."""
    reports: list[dict[str, Any]] = []
    complete = True
    for group in anchor_groups:
        if not isinstance(group, dict):
            continue
        anchor_symbol = _symbol(group.get("anchorSymbol"))
        tokens, errors = _mainnet_candidate_yc_zc_tokens(group)
        if not anchor_symbol or errors:
            complete = False
            reports.append({"anchorSymbol": anchor_symbol, "complete": False, "errors": errors})
            continue
        path = _mainnet_intermediate_pool_file_path(anchor_symbol)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            payload = {}
        expected_direction_count = len(tokens) * max(0, len(tokens) - 1)
        file_tokens = payload.get("ycZcTokens") if isinstance(payload, dict) else []
        if not isinstance(file_tokens, list):
            file_tokens = []
        file_token_addresses = {_address(row.get("address")) for row in file_tokens if isinstance(row, dict)}
        actual_direction_count = len(payload.get("directions") or []) if isinstance(payload, dict) else 0
        file_complete = bool(
            isinstance(payload, dict)
            and payload.get("schemaVersion") == INTERMEDIATE_POOL_FILE_SCHEMA
            and file_token_addresses == {token["address"] for token in tokens}
            and actual_direction_count == expected_direction_count
        )
        complete = complete and file_complete
        reports.append(
            {
                "anchorSymbol": anchor_symbol,
                "path": str(path),
                "complete": file_complete,
                "expectedDirectionCount": expected_direction_count,
                "actualDirectionCount": actual_direction_count,
                "expectedTokenCount": len(tokens),
                "actualTokenCount": len(file_token_addresses),
            }
        )
    return complete, reports


def _mainnet_build_anchor_intermediate_pool_file(
    group: dict[str, Any],
    pools: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build one external swap-pool file for one candidate anchor group."""
    anchor_symbol = _symbol(group.get("anchorSymbol"))
    tokens, errors = _mainnet_candidate_yc_zc_tokens(group)
    if errors:
        raise RuntimeError(f"YC/ZC candidate order validation failed for {anchor_symbol}: {errors}")
    pools_by_edge = _pools_by_edge(pools)
    directions = _mainnet_yz_pairs_from_tokens(
        pools_by_edge,
        tokens,
        include_directions=True,
    )
    undirected = _mainnet_yz_pairs_from_tokens(
        pools_by_edge,
        tokens,
        include_directions=False,
    )
    pool_rows: list[dict[str, Any]] = []
    seen_pools: set[str] = set()
    for pair in directions:
        for pool in pair.get("allPools") or []:
            pool_id = str(_pool_id(pool) or "").lower()
            if not pool_id or pool_id in seen_pools:
                continue
            seen_pools.add(pool_id)
            pool_rows.append(
                {
                    "pool": pool_id,
                    "poolId": pool_id,
                    "poolIdentityMode": pool.get("poolIdentityMode") or "v4_pool_id",
                    "protocol": pool.get("protocol") or pool.get("dex") or "uniswap_v4",
                    "dex": pool.get("dex") or pool.get("protocol") or "uniswap_v4",
                    "fee": pool.get("fee"),
                    "dynamicFee": _pool_dynamic_fee(pool),
                    "specialFee": _pool_special_fee(pool),
                    "feeLabel": _pool_fee_label(pool),
                    "tickSpacing": pool.get("tickSpacing"),
                    "hooks": pool.get("hooks"),
                    "token0": pool.get("token0"),
                    "token1": pool.get("token1"),
                    "token0Symbol": pool.get("token0Symbol"),
                    "token1Symbol": pool.get("token1Symbol"),
                    "addressPair": pool.get("addressPair") or "",
                    "liquidity": pool.get("liquidity") or "",
                    "liquidityIndex": pool.get("liquidityIndex"),
                    "hasLiquidity": pool.get("hasLiquidity"),
                    "estimatedTvlUsd": pool.get("estimatedTvlUsd"),
                    "balances": pool.get("balances") or {},
                    "depthBand": pool.get("depthBand") or {},
                    "depthStatus": pool.get("depthStatus") or "",
                    "recentSwapActive": pool.get("recentSwapActive"),
                    "liquidityReadAt": pool.get("liquidityReadAt"),
                    "liquidityBlockNumber": pool.get("liquidityBlockNumber"),
                    "tick": pool.get("tick"),
                    "sqrtPriceX96": pool.get("sqrtPriceX96"),
                }
            )
    payload = {
        "schemaVersion": INTERMEDIATE_POOL_FILE_SCHEMA,
        "network": "robinhood",
        "chainId": ROBINHOOD_CHAIN_ID,
        "protocols": ["uniswap_v4"],
        "anchorSymbol": anchor_symbol,
        "generatedAt": _stamp(),
        "ycZcTokens": tokens,
        "ycTokenCount": len(tokens),
        "zcTokenCount": len(tokens),
        "directionCount": len(directions),
        "undirectedPairCount": len(undirected),
        "directions": directions,
        "undirectedPairs": undirected,
        "poolCount": len(pool_rows),
        "pools": pool_rows,
    }
    path = _mainnet_intermediate_pool_file_path(anchor_symbol)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)
    return {**payload, "path": str(path)}


def initialize_market_observation_pool_files(
    *,
    chain_id: int | None = None,
    protocols: list[str] | None = None,
) -> dict[str, Any]:
    """Initialize the five per-anchor external swap-pool files.

    Uses the candidate page's YC/ZC sets.  Order errors are raised instead of
    being silently corrected.  Existing complete files are kept; incomplete or
    outdated files are rebuilt.
    """
    if int(chain_id or 0) != ROBINHOOD_CHAIN_ID:
        return {"ok": False, "error": "chain_scope_invalid", "chain_id": chain_id}
    from market.binance_market.pool_candidates import dex_pool_candidates_payload as _page_candidate_payload

    pool_payload = _page_candidate_payload(chain_id=chain_id, protocols=protocols)
    anchor_groups = list(pool_payload.get("anchorGroups") or [])
    complete, reports = _mainnet_intermediate_pool_files_complete(anchor_groups)
    if complete:
        return {
            "ok": True,
            "initialized": False,
            "complete": True,
            "anchorFiles": reports,
            "detail": "external intermediate pool files already complete",
        }
    validation_errors = _mainnet_validate_anchor_groups(anchor_groups)
    if validation_errors:
        return {
            "ok": False,
            "initialized": False,
            "complete": False,
            "validationErrors": validation_errors,
            "detail": "candidate YC/ZC order validation failed; refusing to build files",
        }
    pools, _index = _load_mainnet_observation_pools()
    built: list[dict[str, Any]] = []
    for group in anchor_groups:
        if not isinstance(group, dict):
            continue
        anchor_symbol = _symbol(group.get("anchorSymbol"))
        if not anchor_symbol:
            continue
        built.append(_mainnet_build_anchor_intermediate_pool_file(group, pools))
    return {
        "ok": True,
        "initialized": True,
        "complete": True,
        "anchorFiles": built,
        "detail": "external intermediate pool files initialized",
    }


def _mainnet_load_intermediate_pool_files(
    anchor_groups: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Load every complete per-anchor external swap-pool file.

    A file is only usable when its YC/ZC token set and direction count match
    the candidate page's current shared token set.  Stale or incomplete files
    are reported as missing instead of being silently consumed.
    """
    files: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []
    for group in anchor_groups:
        if not isinstance(group, dict):
            continue
        anchor_symbol = _symbol(group.get("anchorSymbol"))
        if not anchor_symbol:
            continue
        tokens, _errors = _mainnet_candidate_yc_zc_tokens(group)
        path = _mainnet_intermediate_pool_file_path(anchor_symbol)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            payload = {}
        file_tokens = payload.get("ycZcTokens") if isinstance(payload, dict) else []
        if not isinstance(file_tokens, list):
            file_tokens = []
        file_token_addresses = {_address(row.get("address")) for row in file_tokens if isinstance(row, dict)}
        expected_token_addresses = {token["address"] for token in tokens}
        expected_direction_count = len(tokens) * max(0, len(tokens) - 1)
        actual_direction_count = len(payload.get("directions") or []) if isinstance(payload, dict) else 0
        usable = bool(
            isinstance(payload, dict)
            and payload.get("schemaVersion") == INTERMEDIATE_POOL_FILE_SCHEMA
            and file_token_addresses == expected_token_addresses
            and actual_direction_count == expected_direction_count
        )
        if usable:
            files.append({**payload, "path": str(path)})
        else:
            missing.append(
                {
                    "anchorSymbol": anchor_symbol,
                    "path": str(path),
                    "reason": "stale_or_incomplete"
                    if isinstance(payload, dict)
                    else "unreadable",
                    "expectedDirectionCount": expected_direction_count,
                    "actualDirectionCount": actual_direction_count,
                }
            )
    return files, missing


def _mainnet_associated_observation_rows(
    bridge_pairs: list[dict[str, Any]],
    pools: list[dict[str, Any]],
    *,
    token_rows: dict[str, dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Expand bridge-pair rows into direction-level page rows.

    Market observation owns the complete Y-Z surface: every direction from the
    candidate YC/ZC set is represented, including directions that currently
    have no middle-hop pool.  ``addressPair`` stays address-sorted so one V4
    pool is never duplicated by reverse ordering, while ``tokenX/tokenY``
    retain the requested direction for display.
    """
    tokens = dict(token_rows or {})
    grouped: dict[str, dict[str, Any]] = {}
    pool_by_id: dict[str, dict[str, Any]] = {}
    token_by_address: dict[str, dict[str, Any]] = {
        address: dict(row)
        for address, row in tokens.items()
    }

    def token_for(address: str, fallback_symbol: str) -> dict[str, Any]:
        normalized = _address(address)
        row = tokens.get(normalized)
        if row is not None:
            return dict(row)
        return {
            "symbol": _known_symbol(normalized, fallback_symbol),
            "address": normalized,
            "associatedFunds": [],
        }

    for pair in bridge_pairs:
        if not isinstance(pair, dict):
            continue
        address_pair = [address for address in str(pair.get("addressPair") or "").split(":") if _address(address)]
        if len(address_pair) < 2:
            continue
        edge = tuple(sorted((address_pair[0], address_pair[1])))
        token_x_address = _address(pair.get("tokenX"))
        token_y_address = _address(pair.get("tokenY"))
        if not token_x_address or not token_y_address:
            token_x_address = edge[0]
            token_y_address = edge[1]
        token0 = token_for(token_x_address, pair.get("tokenXSymbol"))
        token1 = token_for(token_y_address, pair.get("tokenYSymbol"))
        direction_key = f"{token_x_address}:{token_y_address}"
        row = grouped.get(direction_key)
        if row is None:
            row = {
                "kind": "robinhood_mainnet_v4_associated_fund_anchor_observation_pair",
                "source": "robinhood_mainnet_v4_pool_index",
                "symbolPair": f"{token0.get('symbol')}:{token1.get('symbol')}",
                "addressPair": ":".join(edge),
                "directionKey": direction_key,
                "token0": token0,
                "token1": token1,
                "associatedFundSymbols": [],
                "poolCount": 0,
                "directSwapPoolCount": 0,
                "hasDirectSwapPool": False,
                "pools": [],
                "directSwapPools": [],
                "closedLoopReady": False,
                "observationReason": "pair_tokens_are_associated_with_flash_fund_assets",
                "routeSemantics": "x_y_entry_y_z_bridge_z_x_exit",
                "directSwapRequired": True,
            }
            grouped[direction_key] = row
        token_by_address.setdefault(edge[0], token0)
        token_by_address.setdefault(edge[1], token1)
        bridge_pools = (
            pair.get("preferredPools")
            or pair.get("strictQualityPools")
            or pair.get("executionPools")
            or pair.get("allPools")
            or []
        )
        for pool in bridge_pools:
            if not isinstance(pool, dict):
                continue
            pool_id = _pool_id(pool)
            if pool_id and pool_id not in pool_by_id:
                pool_by_id[pool_id] = {
                    **pool,
                    "observationReason": "pair_tokens_are_associated_with_flash_fund_assets",
                    "associatedFundSymbols": [],
                    "token0AssociatedFunds": [],
                    "token1AssociatedFunds": [],
                }
            direct_pool = {
                "pool": pool_id,
                "poolId": pool_id,
                "poolIdentityMode": pool.get("poolIdentityMode") or "v4_pool_id",
                "protocol": pool.get("protocol") or pool.get("dex") or "uniswap_v4",
                "dex": pool.get("dex") or pool.get("protocol") or "uniswap_v4",
                "fee": pool.get("fee"),
                "tickSpacing": pool.get("tickSpacing"),
                "hooks": pool.get("hooks"),
                "token0": pool.get("token0"),
                "token1": pool.get("token1"),
                "token0Decimals": pool.get("token0Decimals"),
                "token1Decimals": pool.get("token1Decimals"),
                "token0Symbol": _known_symbol(pool.get("token0"), pool.get("token0Symbol")),
                "token1Symbol": _known_symbol(pool.get("token1"), pool.get("token1Symbol")),
                "symbolPair": pool.get("symbolPair")
                or f"{_known_symbol(pool.get('token0'), pool.get('token0Symbol'))}:{_known_symbol(pool.get('token1'), pool.get('token1Symbol'))}",
                "addressPair": pool.get("addressPair") or ":".join(edge),
                "tick": pool.get("tick"),
                "sqrtPriceX96": pool.get("sqrtPriceX96"),
                "liquidity": pool.get("liquidity"),
                "balances": pool.get("balances") or {},
                "closedLoopReady": bool(pool.get("closedLoopReady")),
                "role": "direct_y_z_swap_pool",
                "observationReason": "pair_tokens_are_associated_with_flash_fund_assets",
            }
            row["pools"].append(direct_pool)
            row["directSwapPools"].append(direct_pool)
            row["poolCount"] += 1
            row["directSwapPoolCount"] += 1
            row["hasDirectSwapPool"] = True
            row["closedLoopReady"] = bool(row.get("closedLoopReady") or pool.get("closedLoopReady"))
        if not row.get("hasDirectSwapPool"):
            row["observationEmptyReason"] = "YC 与 ZC 之间暂无 Y-Z 交易池"

    pair_rows = sorted(
        grouped.values(),
        key=lambda row: (
            not bool(row.get("hasDirectSwapPool")),
            str(row.get("symbolPair") or ""),
            str(row.get("directionKey") or row.get("addressPair") or ""),
        ),
    )
    token_rows_out = sorted(
        token_by_address.values(),
        key=lambda row: (str(row.get("symbol") or ""), str(row.get("address") or "")),
    )
    return pair_rows, list(pool_by_id.values()), token_rows_out


def _mainnet_fund_anchor_observation(
    pools: list[dict[str, Any]],
    *,
    route_limit: int | None = None,
    anchor_groups: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    sequences, _legacy_bridge_pairs = _mainnet_observation_sequences(pools, route_limit=route_limit)
    bridge_pairs = []
    pools_by_edge = _pools_by_edge(pools)
    anchor_symbols = list(MAINNET_CONFIGURED_ANCHOR_SYMBOLS)
    anchor_addresses = _mainnet_configured_anchor_addresses()
    neighbors = _anchor_neighbors(pools, anchor_addresses=anchor_addresses)
    if anchor_groups:
        candidate_token_count = 0
        validation_errors = _mainnet_validate_anchor_groups(anchor_groups)
        pool_files, missing_files = _mainnet_load_intermediate_pool_files(anchor_groups)
        pool_file_index = {
            _symbol(file.get("anchorSymbol")): file
            for file in pool_files
        }
        for group in anchor_groups:
            if not isinstance(group, dict):
                continue
            tokens, errors = _mainnet_candidate_yc_zc_tokens(group)
            if errors:
                continue
            candidate_token_count += len(tokens)
            anchor_symbol = _symbol(group.get("anchorSymbol"))
            file = pool_file_index.get(anchor_symbol)
            if file is not None:
                bridge_pairs.extend(
                    dict(pair)
                    for pair in file.get("directions") or []
                    if isinstance(pair, dict)
                )
                continue
            bridge_pairs.extend(
                _mainnet_yz_pairs_from_tokens(
                    pools_by_edge,
                    tokens,
                    include_directions=True,
                )
            )
        neighbors = {
            token["address"]: {"symbol": token["symbol"], "address": token["address"], "poolCount": 0}
            for group in anchor_groups
            if isinstance(group, dict)
            for tokens, group_errors in [(_mainnet_candidate_yc_zc_tokens(group))]
            for token in tokens
            if not group_errors
        }
    eth_pair_token_count = _mainnet_eth_pair_token_count()
    candidate_yz_pair_count = len(neighbors) * max(0, len(neighbors) - 1)
    if anchor_groups:
        candidate_yz_pair_count = sum(
            len(tokens) * max(0, len(tokens) - 1)
            for group in anchor_groups
            if isinstance(group, dict)
            for tokens, errors in [(_mainnet_candidate_yc_zc_tokens(group))]
            if not errors
        )
    verified_yz_pair_count = sum(
        1
        for pair in bridge_pairs
        if isinstance(pair, dict)
        and (
            pair.get("preferredPools")
            or pair.get("strictQualityPools")
            or pair.get("allPools")
        )
    )
    bridge_pool_ids = sorted(
        {
            _pool_id(pool)
            for pair in bridge_pairs
            for pool in pair.get("strictQualityPools") or []
            if _pool_id(pool)
        }
    )
    associated_observation_pairs, associated_observation_pools, associated_observation_tokens = (
        _mainnet_associated_observation_rows(
            bridge_pairs,
            pools,
            token_rows=neighbors,
        )
    )
    active_pool_ids = sorted(
        _active_pool_ids(pools)
    )
    index, _index_payload = _load_mainnet_v4_index()
    indexed_pool_count = len(index)
    return {
        "enabled": True,
        "status": "robinhood_mainnet_v4_eth_anchor_observation",
        "source": "candidate_pool_page_yc_zc_sets_plus_v4_pool_snapshot",
        "candidateYcZcSource": "candidate_pool_page_shared_tokens",
        "validationErrors": _mainnet_validate_anchor_groups(anchor_groups or []),
        "intermediatePoolFiles": [
            {
                "anchorSymbol": _symbol(file.get("anchorSymbol")),
                "path": file.get("path"),
                "directionCount": len(file.get("directions") or []),
                "poolCount": file.get("poolCount") or 0,
            }
            for file in pool_files
        ],
        "missingIntermediatePoolFiles": missing_files,
        "recentSwapFilterApplied": False,
        "liquidityFilterApplied": True,
        "recentSwapActivePoolCount": len(active_pool_ids),
        "liquidPoolIdCount": len(active_pool_ids),
        "recentSwapIndexedPoolCount": indexed_pool_count,
        "activePoolIds": active_pool_ids,
        "liquidPoolIds": active_pool_ids,
        "ethAnchor": "ETH",
        "anchorSymbols": anchor_symbols,
        "anchorAddresses": sorted(anchor_addresses),
        "routeCount": len(sequences),
        "associatedObservationPairCount": len(associated_observation_pairs),
        "associatedObservationPoolCount": len(associated_observation_pools),
        "associatedObservationTokenCount": len(associated_observation_tokens),
        "candidateEthPairTokenCount": eth_pair_token_count,
        "candidateYzPairCount": candidate_yz_pair_count,
        "verifiedYzPairCount": verified_yz_pair_count,
        "missingYzPairCount": max(0, candidate_yz_pair_count - verified_yz_pair_count),
        "verifiedEthPairPoolTokenCount": len(neighbors),
        "sourceTokenEvidence": "robinhood_eth_pair_tokens",
        "observationSequences": sequences,
        "closedLoopSequences": sequences,
        "associatedObservationPairs": associated_observation_pairs,
        "associatedObservationPools": associated_observation_pools,
        "associatedObservationTokens": associated_observation_tokens,
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
    # Mainnet observation is anchored to ETH, WETH, verified stable
    # settlement assets and any configured anchor/observation token. No TA/TB/TZ-specific exclusion is applied; symbols are ordinary chain assets discovered dynamically from pools.
    return list(MAINNET_CONFIGURED_ANCHOR_SYMBOLS)


def borrowable_symbols_from_pool_payload(
    pool_payload: dict[str, Any],
    *,
    chain_id: int | None,
    protocols: list[str] | None = None,
) -> list[str]:
    if not _chain_in_scope(chain_id) or not _protocol_in_scope(protocols):
        return []
    return list(MAINNET_CONFIGURED_ANCHOR_SYMBOLS)


def bridge_pairs_for_anchor_groups(
    anchor_groups: list[dict[str, Any]],
    *,
    chain_id: int | None,
    protocols: list[str] | None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if not _chain_in_scope(chain_id) or not _protocol_in_scope(protocols):
        return [], {"enabled": False, "status": "chain_scope_invalid"}
    validation_errors = _mainnet_validate_anchor_groups(anchor_groups)
    if validation_errors:
        return (
            [],
            {
                "enabled": False,
                "status": "yc_zc_address_order_validation_failed",
                "validationErrors": validation_errors,
                "detail": "candidate YC/ZC address order is invalid; market observation will not use it",
            },
        )
    pool_files, missing_files = _mainnet_load_intermediate_pool_files(anchor_groups)
    bridge_pairs: list[dict[str, Any]] = []
    pool_file_index: dict[str, dict[str, Any]] = {
        _symbol(file.get("anchorSymbol")): file
        for file in pool_files
    }
    pools, _index = _load_mainnet_observation_pools()
    pools_by_edge = _pools_by_edge(pools)
    for group in anchor_groups:
        if not isinstance(group, dict):
            continue
        anchor_symbol = _symbol(group.get("anchorSymbol"))
        tokens, _errors = _mainnet_candidate_yc_zc_tokens(group)
        file = pool_file_index.get(anchor_symbol)
        if file is not None:
            file_directions = [
                dict(pair)
                for pair in file.get("directions") or []
                if isinstance(pair, dict)
            ]
            if file_directions:
                bridge_pairs.extend(file_directions)
                continue
        bridge_pairs.extend(
            _mainnet_yz_pairs_from_tokens(
                pools_by_edge,
                tokens,
                include_directions=True,
            )
        )
    if missing_files:
        # The page still builds from candidate sets when the initialization
        # hook has not yet produced the external files.
        pass
    eth_pair_token_count = _mainnet_eth_pair_token_count()
    anchor_symbols = list(MAINNET_CONFIGURED_ANCHOR_SYMBOLS)
    anchor_addresses = _mainnet_configured_anchor_addresses()
    neighbors = _anchor_neighbors(pools, anchor_addresses=anchor_addresses)
    candidate_yz_pair_count = sum(
        len(tokens) * max(0, len(tokens) - 1)
        for group in anchor_groups
        if isinstance(group, dict)
        for tokens, _errors in [_mainnet_candidate_yc_zc_tokens(group)]
    )
    pools_by_edge = _pools_by_edge(pools)
    verified_yz_pair_count = sum(
        1
        for pair in bridge_pairs
        if isinstance(pair, dict) and (pair.get("preferredPools") or pair.get("strictQualityPools") or pair.get("allPools"))
    )
    active_pool_ids = sorted(_active_pool_ids(pools))
    return bridge_pairs, {
        "enabled": True,
        "status": "robinhood_mainnet_v4_eth_anchor_bridge_observation",
        "ethAnchor": "ETH",
        "anchorSymbols": anchor_symbols,
        "anchorAddresses": sorted(anchor_addresses),
        "candidateYcZcSource": "candidate_pool_page_shared_tokens",
        "intermediatePoolFiles": [
            {
                "anchorSymbol": _symbol(file.get("anchorSymbol")),
                "path": file.get("path"),
                "directionCount": len(file.get("directions") or []),
                "poolCount": file.get("poolCount") or 0,
            }
            for file in pool_files
        ],
        "missingIntermediatePoolFiles": missing_files,
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
    from market.binance_market.pool_candidates import dex_pool_candidates_payload as _page_candidate_payload

    pool_payload = _page_candidate_payload(chain_id=chain_id, protocols=protocols)
    anchor_groups = list(pool_payload.get("anchorGroups") or [])
    pools, index = _load_mainnet_observation_pools()
    return _mainnet_fund_anchor_observation(pools, anchor_groups=anchor_groups)


__all__ = [
    "borrowable_symbols_from_pool_payload",
    "bridge_pairs_for_anchor_groups",
    "fund_anchor_pair_observation",
    "observation_anchor_symbols",
]
