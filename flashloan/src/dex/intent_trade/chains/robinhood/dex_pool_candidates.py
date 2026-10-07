from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from core.chain_data_registry import chain_data_file_path
from core.chain_data_registry import chain_data_by_network
from core.chain_flow_dispatcher import ChainFlowRequest
from dex.intent_trade.chains.common.dex_candidate_tables import build_robinhood_pool_candidates, read_json_object
from dex.intent_trade.chains.common.dex_candidate_tables import build_anchor_candidate_groups, pair_classification


APP_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_ROBINHOOD_DEX_CANDIDATE_PATH = (
    chain_data_file_path("robinhood", "dex_candidates")
    or APP_ROOT / "runtime" / "cache" / "robinhood" / "robinhood_dex_candidates.json"
)
DEFAULT_ROBINHOOD_DEX_MARKET_POOL_PATH = (
    chain_data_file_path("robinhood", "dex_market_pools")
    or APP_ROOT / "runtime" / "cache" / "robinhood" / "robinhood_dex_market_pools.json"
)
DEFAULT_ROBINHOOD_DEX_EVIDENCE_ROOT = APP_ROOT / "runtime" / "evidence" / "robinhood" / "dex_discovery"
DEFAULT_ROBINHOOD_YC_ZC_TOKEN_CACHE = (
    chain_data_file_path("robinhood", "yc_zc_tokens")
    or APP_ROOT / "runtime" / "evidence" / "robinhood" / "yc_zc_tokens.json"
)

_ROBINHOOD_ROW = chain_data_by_network("robinhood") or {}
_ROBINHOOD_CONTRACTS = _ROBINHOOD_ROW.get("contracts") if isinstance(_ROBINHOOD_ROW.get("contracts"), dict) else {}
_WETH_ADDRESS = str(_ROBINHOOD_CONTRACTS.get("wrapped_native") or "").strip().lower()
_USDG_ADDRESS = str(_ROBINHOOD_CONTRACTS.get("usdg") or "").strip().lower()
_ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"
_ETH_SYMBOLS = {"ETH", "WETH"}
_ETH_ADDRESSES = {_ZERO_ADDRESS, _WETH_ADDRESS}
REPO_ROOT = Path(__file__).resolve().parents[6]
MAINNET_V4_MANIFEST_PATH = (
    REPO_ROOT / "test-token_create" / "config" / "mainnet-v4-amm" / "manifest.json"
)


def _read_json_object(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _load_mainnet_v4_manifest() -> dict[str, Any]:
    return _read_json_object(MAINNET_V4_MANIFEST_PATH)


def _mainnet_test_token_addresses() -> dict[str, str]:
    manifest = _load_mainnet_v4_manifest()
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


def _mainnet_stable_token_addresses() -> dict[str, tuple[str, int]]:
    manifest = _load_mainnet_v4_manifest()
    result: dict[str, tuple[str, int]] = {}
    for raw in manifest.get("stableTokens") or []:
        if not isinstance(raw, dict):
            continue
        symbol = str(raw.get("symbol") or "").strip().upper()
        address = str(raw.get("address") or "").strip().lower()
        try:
            decimals = int(raw.get("decimals") or 18)
        except (TypeError, ValueError):
            decimals = 18
        if symbol and address:
            result[symbol] = (address, decimals)
    if _USDG_ADDRESS and "USDG" not in result:
        result["USDG"] = (_USDG_ADDRESS, 6)
    if "USDE" not in result:
        # Confirmed from the Robinhood mainnet V4 pool index and yc-zc token cache.
        result["USDE"] = ("0x5d3a1ff2b6bab83b63cd9ad0787074081a52ef34", 18)
    return result


_MAINNET_TEST_ADDRESS_BY_SYMBOL = _mainnet_test_token_addresses()
_MAINNET_STABLE_TOKEN_DECIMALS_BY_SYMBOL = _mainnet_stable_token_addresses()
_MAINNET_STABLE_ADDRESS_BY_SYMBOL = {
    symbol: address for symbol, (address, _decimals) in _MAINNET_STABLE_TOKEN_DECIMALS_BY_SYMBOL.items()
}


def _mainnet_configured_anchor_symbols() -> list[str]:
    """Return the configured Robinhood mainnet borrow anchors.

    ETH, confirmed stable settlement assets, and the TZ test token are
    configured anchors. TA/TB intentionally stay bridge/middle tokens so a
    single closed loop is counted once instead of producing duplicate anchor
    groups for every test token permutation.
    """
    symbols = ["ETH", *sorted(_MAINNET_STABLE_ADDRESS_BY_SYMBOL.keys())]
    if "TZ" in _MAINNET_TEST_ADDRESS_BY_SYMBOL:
        symbols.append("TZ")
    return symbols


def _known_v4_token_metadata(address: str) -> tuple[str, int | None] | None:
    normalized = str(address or "").strip().lower()
    if normalized == _ZERO_ADDRESS:
        return "ETH", 18
    if normalized and normalized == _WETH_ADDRESS:
        return "WETH", 18
    if normalized and normalized == _USDG_ADDRESS:
        return "USDG", 6
    for symbol, address in _MAINNET_TEST_ADDRESS_BY_SYMBOL.items():
        if normalized and normalized == address:
            return symbol, 18
    for symbol, (address, decimals) in _MAINNET_STABLE_TOKEN_DECIMALS_BY_SYMBOL.items():
        if normalized and normalized == address:
            return symbol, decimals
    return None


def _v4_index_path(request: ChainFlowRequest) -> Path | None:
    return chain_data_file_path(request.network, "v4_pool_index", chain_id=request.chain_id)


def _v4_index_rows(path: Path | None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    payload = read_json_object(path)
    rows = [row for row in payload.get("pools") or [] if isinstance(row, dict)]
    return payload, rows


def _v4_symbol(row: dict[str, Any], index: int) -> str:
    value = str(row.get(f"token{index}Symbol") or "").strip().upper()
    address = str(row.get(f"token{index}") or row.get(f"currency{index}") or "").strip()
    known = _known_v4_token_metadata(address)
    if known is not None:
        return known[0]
    if value:
        return value
    if address.lower() == _ZERO_ADDRESS:
        return "ETH"
    return address[:10].upper() if address else ""


def _v4_is_eth_symbol_symbol(address: str, symbol: str) -> bool:
    return str(address or "").strip().lower() in _ETH_ADDRESSES or str(symbol or "").strip().upper() in _ETH_SYMBOLS


def _v4_eth_neighbor_candidate(
    candidate: dict[str, Any],
) -> dict[str, Any] | None:
    token0 = str(candidate.get("token0") or "").strip()
    token1 = str(candidate.get("token1") or "").strip()
    symbol0 = str(candidate.get("token0Symbol") or "").strip()
    symbol1 = str(candidate.get("token1Symbol") or "").strip()
    eth0 = _v4_is_eth_symbol_symbol(token0, symbol0)
    eth1 = _v4_is_eth_symbol_symbol(token1, symbol1)
    if eth0 and eth1:
        # Native/wrapped ETH pair is not a useful y-z route candidate.
        return None
    if eth0:
        return {"token": token1, "symbol": symbol1, "candidate": candidate}
    if eth1:
        return {"token": token0, "symbol": symbol0, "candidate": candidate}
    return None


def _v4_pool_candidate(row: dict[str, Any], pool_manager: str) -> dict[str, Any] | None:
    pool_id = str(row.get("poolId") or row.get("pool_id") or row.get("pool") or "").strip()
    token0 = str(row.get("token0") or row.get("currency0") or "").strip()
    token1 = str(row.get("token1") or row.get("currency1") or "").strip()
    symbol0 = _v4_symbol(row, 0)
    symbol1 = _v4_symbol(row, 1)
    decimals0 = row.get("token0Decimals")
    decimals1 = row.get("token1Decimals")
    known0 = _known_v4_token_metadata(token0)
    known1 = _known_v4_token_metadata(token1)
    if known0 is not None and decimals0 in (None, ""):
        decimals0 = known0[1]
    if known1 is not None and decimals1 in (None, ""):
        decimals1 = known1[1]
    if decimals0 in (None, ""):
        decimals0 = 18
    if decimals1 in (None, ""):
        decimals1 = 18
    if not pool_id or not token0 or not token1 or not symbol0 or not symbol1:
        return None
    raw_fee = row.get("fee")
    try:
        raw_fee_int = int(raw_fee) if raw_fee not in (None, "") else 0
    except (TypeError, ValueError):
        raw_fee_int = 0
    dynamic_fee = bool(raw_fee_int & 8388608)
    special_fee = dynamic_fee or raw_fee_int > 100_000
    liquidity_index = row.get("liquidityIndex")
    if liquidity_index is None:
        try:
            liquidity_index = float(str(row.get("liquidity") or "0"))
        except ValueError:
            liquidity_index = 0
    has_liquidity = bool(row.get("hasLiquidity")) or (liquidity_index is not None and liquidity_index > 0)
    balances = row.get("balances")
    if not isinstance(balances, dict):
        balances = {"tvlUsd": row.get("estimatedTvlUsd") or row.get("tvlUsd") or 0}
    return {
        "pool": pool_id,
        "poolId": pool_id,
        "poolIdentityMode": "v4_pool_id",
        "poolManager": row.get("poolManager") or pool_manager,
        "poolKey": row.get("poolKey") or row,
        "dex": "uniswap_v4",
        "protocol": "uniswap_v4",
        "fee": row.get("fee"),
        "dynamicFee": dynamic_fee,
        "specialFee": special_fee,
        "feeLabel": ("dynamic" if dynamic_fee else "special") if special_fee else (int(raw_fee_int) if raw_fee_int else row.get("fee")),
        "usableForObservation": (not special_fee) and has_liquidity,
        "hasLiquidity": row.get("hasLiquidity") if row.get("hasLiquidity") is not None else has_liquidity,
        "liquidityIndex": liquidity_index,
        "estimatedTvlUsd": row.get("estimatedTvlUsd") or balances.get("tvlUsd"),
        "depthStatus": row.get("depthStatus") or "",
        "tickSpacing": row.get("tickSpacing"),
        "hooks": row.get("hooks"),
        "quality": "pass",
        "status": row.get("status") or "initialized_pool",
        "rejectReasons": [],
        "token0": token0,
        "token1": token1,
        "token0Symbol": symbol0,
        "token1Symbol": symbol1,
        "token0Decimals": decimals0,
        "token1Decimals": decimals1,
        "sqrtPriceX96": row.get("sqrtPriceX96"),
        "tick": row.get("tick"),
        "liquidity": row.get("liquidity") or "",
        "balances": balances,
        "tvlUsd": row.get("estimatedTvlUsd") or row.get("tvlUsd") or 0,
        "depthBand": row.get("depthBand") or {"level": "indexed", "label": "indexed V4 PoolKey"},
        "symbolPair": row.get("symbolPair") or f"{symbol0}:{symbol1}",
        "source": "robinhood_mainnet_v4_pool_index",
        "evidence": {
            **(row.get("evidence") or {}),
            "source": "robinhood_mainnet_v4_pool_index",
            "poolKeyDecoded": bool(row.get("poolKeyDecoded") or row.get("poolKey")),
            "initializeBlockNumber": row.get("initializeBlockNumber"),
            "initializeTransactionHash": row.get("initializeTransactionHash"),
        },
    }


def _symbol(value: object) -> str:
    return str(value or "").strip().upper()


def _address(value: object) -> str:
    text = str(value or "").strip()
    return text if text.startswith("0x") and len(text) == 42 else ""


def _shared_candidate_key(row: dict[str, Any]) -> str:
    pool_id = str(row.get("pool") or "").strip().lower()
    counter_address = _address(row.get("counterAddress")).lower()
    counter_symbol = _symbol(row.get("counterSymbol"))
    fee = str(row.get("fee") or "").strip().lower()
    if pool_id:
        return ":".join(("pool", pool_id, counter_address or counter_symbol, fee))
    token0 = _address(row.get("token0")).lower()
    token1 = _address(row.get("token1")).lower()
    token_pair = ":".join(sorted(address for address in (token0, token1) if address))
    return ":".join(("pair", token_pair, counter_address or counter_symbol, fee))


def _shared_candidate_row(anchor_symbol: str, yc_row: dict[str, Any] | None, zc_row: dict[str, Any] | None) -> dict[str, Any]:
    base = dict(yc_row or zc_row or {})
    counter_symbol = _symbol(base.get("counterSymbol"))
    route_text = f"{anchor_symbol} <-> {counter_symbol}" if counter_symbol else str(base.get("routeText") or "")
    base.update(
        {
            "direction": "shared",
            "directions": ["yc", "zc"],
            "candidateSetMode": "bidirectional_same_pool_key",
            "routeText": route_text,
            "ycRouteText": (yc_row or {}).get("routeText"),
            "zcRouteText": (zc_row or {}).get("routeText"),
            "ycRouteKey": (yc_row or {}).get("routeKey"),
            "zcRouteKey": (zc_row or {}).get("routeKey"),
        }
    )
    return base


def _shared_tokens_from_pools(shared_pools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    buckets: dict[str, dict[str, Any]] = {}
    for row in shared_pools:
        symbol = _symbol(row.get("counterSymbol"))
        if not symbol:
            continue
        address = _address(row.get("counterAddress"))
        pool_id = str(row.get("pool") or "").strip()
        bucket = buckets.setdefault(symbol, {"symbol": symbol, "address": address, "pools": [], "poolIds": set()})
        if address and not bucket.get("address"):
            bucket["address"] = address
        if pool_id and pool_id not in bucket["poolIds"]:
            bucket["poolIds"].add(pool_id)
            bucket["pools"].append(row)
    tokens = []
    for token in sorted(buckets.values(), key=lambda item: (-len(item["pools"]), str(item["symbol"]))):
        tokens.append(
            {
                "symbol": token["symbol"],
                "address": token.get("address") or "",
                "poolCount": str(len(token["pools"])),
                "pools": token["pools"][:10],
            }
        )
    return tokens


def _yc_zc_token_cache_path(request: ChainFlowRequest) -> Path | None:
    try:
        return chain_data_file_path(request.network, "yc_zc_tokens", chain_id=request.chain_id)
    except (TypeError, ValueError, OSError):
        return None


def _load_yc_zc_token_cache(request: ChainFlowRequest) -> dict[str, Any] | None:
    path = _yc_zc_token_cache_path(request) or DEFAULT_ROBINHOOD_YC_ZC_TOKEN_CACHE
    if not path.exists():
        return None
    payload = read_json_object(path)
    tokens = payload.get("tokens") if isinstance(payload.get("tokens"), list) else []
    if not tokens:
        return None
    return payload


def _shared_tokens_from_cache(
    cache: dict[str, Any],
    shared_pools: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    pool_by_symbol: dict[str, list[dict[str, Any]]] = {}
    pool_by_address: dict[str, list[dict[str, Any]]] = {}
    for row in shared_pools:
        symbol = _symbol(row.get("counterSymbol"))
        address = _address(row.get("counterAddress")).lower()
        if symbol:
            pool_by_symbol.setdefault(symbol, []).append(row)
        if address:
            pool_by_address.setdefault(address, []).append(row)
    tokens: list[dict[str, Any]] = []
    seen_symbols: set[str] = set()
    for cached in cache.get("tokens") or []:
        if not isinstance(cached, dict):
            continue
        symbol = _symbol(cached.get("symbol"))
        if not symbol or symbol in seen_symbols:
            continue
        seen_symbols.add(symbol)
        address = _address(cached.get("address"))
        address_key = address.lower()
        pools = pool_by_symbol.get(symbol) or pool_by_address.get(address_key) or []
        tokens.append(
            {
                "symbol": symbol,
                "address": address,
                "decimals": cached.get("decimals"),
                "poolCount": str(cached.get("poolCount") or len(pools)),
                "source": cached.get("source") or "yc_zc_token_cache",
                "pools": pools[:10],
            }
        )
    return tokens


def _mark_v4_shared_yc_zc_sets(
    anchor_groups: list[dict[str, Any]],
    stable_token_cache: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    marked: list[dict[str, Any]] = []
    for group in anchor_groups:
        if not isinstance(group, dict):
            continue
        yc_rows = [row for row in group.get("ycPools") or [] if isinstance(row, dict)]
        zc_rows = [row for row in group.get("zcPools") or [] if isinstance(row, dict)]
        yc_by_key = {_shared_candidate_key(row): row for row in yc_rows}
        zc_by_key = {_shared_candidate_key(row): row for row in zc_rows}
        same_set = bool(yc_by_key) and set(yc_by_key) == set(zc_by_key)
        if not same_set:
            marked.append(group)
            continue
        anchor_symbol = _symbol(group.get("anchorSymbol"))
        shared_pools = [
            _shared_candidate_row(anchor_symbol, yc_by_key.get(key), zc_by_key.get(key))
            for key in sorted(yc_by_key)
        ]
        shared_tokens = (
            _shared_tokens_from_cache(stable_token_cache, shared_pools)
            if stable_token_cache
            else _shared_tokens_from_pools(shared_pools)
        )
        marked.append(
            {
                **group,
                "candidateSetMode": "bidirectional_same_pool_key_set",
                "ycZcSameSet": True,
                "ycZcSharedPoolSet": True,
                "sharedPools": shared_pools,
                "sharedTokens": shared_tokens,
                "sharedPoolCount": str(len(shared_pools)),
                "sharedTokenCount": str(len(shared_tokens)),
                "ycPools": [],
                "zcPools": [],
                "ycTokens": [],
                "zcTokens": [],
                "ycPoolsRef": "sharedPools",
                "zcPoolsRef": "sharedPools",
                "ycTokensRef": "sharedTokens",
                "zcTokensRef": "sharedTokens",
                "ycPoolCount": str(len(shared_pools)),
                "zcPoolCount": str(len(shared_pools)),
                "ycTokenCount": str(len(shared_tokens)),
                "zcTokenCount": str(len(shared_tokens)),
                "candidatePoolCount": str(len(shared_pools)),
                "candidateQueueCount": str(len(shared_pools) * 2),
            }
        )
    return marked


def _mainnet_v4_payload(request: ChainFlowRequest, path: Path, index_payload: dict[str, Any], rows: list[dict[str, Any]]) -> dict[str, Any]:
    pool_manager = str(index_payload.get("poolManager") or "").strip()
    all_candidates = [candidate for row in rows if (candidate := _v4_pool_candidate(row, pool_manager)) is not None]
    eth_neighbor_tokens: dict[str, str] = {}
    for candidate in all_candidates:
        neighbor = _v4_eth_neighbor_candidate(candidate)
        if neighbor:
            eth_neighbor_tokens.setdefault(neighbor["token"].lower(), neighbor["symbol"])
    eth_token_set = {address for address in eth_neighbor_tokens if address}
    known_anchor_addresses = {
        address
        for address in [
            *_MAINNET_TEST_ADDRESS_BY_SYMBOL.values(),
            *_MAINNET_STABLE_ADDRESS_BY_SYMBOL.values(),
            _USDG_ADDRESS,
        ]
        if address
    }
    candidates: list[dict[str, Any]] = []
    for candidate in all_candidates:
        token0 = str(candidate.get("token0") or "").strip().lower()
        token1 = str(candidate.get("token1") or "").strip().lower()
        symbol0 = str(candidate.get("token0Symbol") or "").strip()
        symbol1 = str(candidate.get("token1Symbol") or "").strip()
        eth0 = _v4_is_eth_symbol_symbol(token0, symbol0)
        eth1 = _v4_is_eth_symbol_symbol(token1, symbol1)
        if eth0 and eth1:
            continue
        if eth0 or eth1:
            candidates.append(candidate)
        elif token0 in eth_token_set and token1 in eth_token_set:
            candidates.append(candidate)
        elif token0 in known_anchor_addresses and token1 in known_anchor_addresses:
            candidates.append(candidate)
    grouped: dict[str, dict[str, Any]] = {}
    for candidate in candidates:
        token_pair = tuple(sorted((str(candidate.get("token0") or "").lower(), str(candidate.get("token1") or "").lower())))
        key = ":".join(token_pair) if all(token_pair) else str(candidate.get("symbolPair") or "")
        bucket = grouped.setdefault(
            key,
            {
                "symbolPair": candidate.get("symbolPair"),
                "addressPair": ":".join(token_pair),
                "identityMode": "v4_pool_id",
                "tokenX": candidate.get("token0"),
                "tokenY": candidate.get("token1"),
                "tokenXSymbol": candidate.get("token0Symbol"),
                "tokenYSymbol": candidate.get("token1Symbol"),
                "classification": pair_classification(candidate.get("token0Symbol"), candidate.get("token1Symbol")),
                "strictQualityPools": [],
                "preferredPools": [],
                "executionPools": [],
                "rejectedPools": [],
            },
        )
        bucket["strictQualityPools"].append(candidate)
        bucket["preferredPools"].append(candidate)
        bucket["executionPools"].append(candidate)

    pairs: list[dict[str, Any]] = []
    for pair in grouped.values():
        pair["allPoolCount"] = str(len(pair["preferredPools"]))
        pair["strictQualityPoolCount"] = str(len(pair["strictQualityPools"]))
        pair["preferredPoolCount"] = str(len(pair["preferredPools"]))
        pair["executionPoolCount"] = str(len(pair["executionPools"]))
        pair["rejectedPoolCount"] = "0"
        pair["hasStrictQualityPools"] = bool(pair["strictQualityPools"])
        pair["hasPreferredPools"] = bool(pair["preferredPools"])
        pair["rejectReasonCounts"] = {}
        pairs.append(pair)

    pairs.sort(key=lambda item: str(item.get("symbolPair") or ""))
    yc_zc_token_cache = _load_yc_zc_token_cache(request)
    # The cache is display metadata only. It must not filter the candidate pool
    # set: YC/ZC token discovery depends on pools, so filtering pools by the
    # YC/ZC cache would create a circular dependency and silently drop real
    # first/third-hop candidates.
    anchor_groups = _mark_v4_shared_yc_zc_sets(
        build_anchor_candidate_groups(pairs, anchor_symbols=_mainnet_configured_anchor_symbols()),
        stable_token_cache=yc_zc_token_cache,
    )
    shared_group_count = sum(1 for group in anchor_groups if group.get("ycZcSameSet"))
    shared_pool_count = sum(int(group.get("sharedPoolCount") or 0) for group in anchor_groups)
    return {
        "mode": "dex_pool_candidates",
        "source": "mainnet_v4_pool_index",
        "fallbackReason": None if candidates else "mainnet_v4_pool_index_has_no_decoded_pools",
        "generatedAt": index_payload.get("generatedAt"),
        "freshness": index_payload.get("generatedAt"),
        "poolStaticTable": {
            "path": str(path),
            "exists": bool(candidates),
            "mode": index_payload.get("schemaVersion") or "robinhood-v4-pool-index-v1",
            "network": request.network,
            "chainId": request.chain_id,
            "generatedAt": index_payload.get("generatedAt"),
            "generatedBlockNumber": index_payload.get("generatedBlockNumber"),
            "poolManager": pool_manager,
        },
        "filters": {"source": "mainnet v4 pool index", "protocol": "uniswap_v4", "identity": "poolId/poolKey"},
        "summary": {
            "candidateRouteCount": str(len(candidates)),
            "staticPoolCount": str(len(candidates)),
            "staticPairCount": str(len(pairs)),
            "pairTableCount": str(len(pairs)),
            "anchorGroupCount": str(len(anchor_groups)),
            "ycPoolCount": str(sum(int(group.get("ycPoolCount") or 0) for group in anchor_groups)),
            "zcPoolCount": str(sum(int(group.get("zcPoolCount") or 0) for group in anchor_groups)),
            "ycTokenCount": str(sum(int(group.get("ycTokenCount") or 0) for group in anchor_groups)),
            "zcTokenCount": str(sum(int(group.get("zcTokenCount") or 0) for group in anchor_groups)),
            "ycZcSameSetGroupCount": str(shared_group_count),
            "sharedPoolSetCount": str(shared_pool_count),
            "candidateSetStorageMode": "shared_yc_zc_pool_set" if shared_group_count else "directional_pool_sets",
            "ycZcTokenCache": "loaded" if yc_zc_token_cache else "missing",
            "ycZcTokenCacheCount": str(len(yc_zc_token_cache.get("tokens") or [])) if yc_zc_token_cache else "0",
            "ycZcTokenCacheSource": str(_yc_zc_token_cache_path(request) or "") if yc_zc_token_cache else "",
            "preferredPoolCount": str(len(candidates)),
            "executionPoolCount": str(sum(len(pair["executionPools"]) for pair in pairs)),
            "strictQualityPoolCount": str(len(candidates)),
            "pairsWithStrictQualityPools": str(sum(1 for pair in pairs if pair["hasStrictQualityPools"])),
            "pairsWithPreferredPools": str(sum(1 for pair in pairs if pair["hasPreferredPools"])),
            "v4IndexedPoolCount": str(len(rows)),
        },
        "pairs": pairs,
        "anchorGroups": anchor_groups,
        "errors": [] if candidates else ["mainnet_v4_pool_index_has_no_decoded_pools"],
    }


def dex_pool_candidates_payload(
    *,
    request: ChainFlowRequest,
    pool_cache_path: Path | None = None,
    market_pool_path: Path | None = None,
    evidence_root: Path | None = None,
    **_: Any,
) -> dict[str, Any]:
    protocol_set = {str(item or "").strip().lower() for item in request.flow.get("allowed_protocols") or []}
    if not protocol_set or "uniswap_v4" in protocol_set:
        v4_path = _v4_index_path(request)
        index_payload, v4_rows = _v4_index_rows(v4_path)
        if v4_rows:
            return _mainnet_v4_payload(request, v4_path or Path(""), index_payload, v4_rows)
    path = (
        pool_cache_path
        or chain_data_file_path(request.network, "dex_candidates", chain_id=request.chain_id)
        or DEFAULT_ROBINHOOD_DEX_CANDIDATE_PATH
    )
    market_path = (
        market_pool_path
        or chain_data_file_path(request.network, "dex_market_pools", chain_id=request.chain_id)
        or DEFAULT_ROBINHOOD_DEX_MARKET_POOL_PATH
    )
    evidence_path = (
        evidence_root
        or chain_data_file_path(request.network, "dex_discovery", chain_id=request.chain_id)
        or DEFAULT_ROBINHOOD_DEX_EVIDENCE_ROOT
    )
    table = read_json_object(path)
    return build_robinhood_pool_candidates(
        table,
        path,
        market_pool_path=market_path,
        evidence_root=evidence_path,
        source="runtime_cache" if table else "local_json_missing",
        fallback_reason=None if table else "dex_pool_static_table_unavailable",
    )


__all__ = ["dex_pool_candidates_payload"]
