from __future__ import annotations

from typing import Any, Callable

from core.chain_flow_dispatcher import ChainFlowRequest
from dex.intent_trade.chains.common.dex_candidate_tables import build_anchor_candidate_groups
from dex.intent_trade.chains.robinhood_testnet.v4_flash_accounting_sources import v4_flash_accounting_source_rows
from dex.intent_trade.chains.robinhood_testnet.v4_pool_index import load_robinhood_testnet_v4_pool_index
from dex.intent_trade.chains.robinhood_testnet.v4_pool_liquidity import (
    _known_token_pool_ids,
    load_robinhood_testnet_v4_pool_liquidity,
)


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
    "PYUSD",
    "RLUSD",
    "TUSD",
    "USDC",
    "USDC.E",
    "USDD",
    "USDE",
    "USDG",
    "USDF",
    "USD1",
    "USDT",
    "USDT.E",
}


def _symbol(value: object) -> str:
    return str(value or "").strip().upper()


def _address(value: object) -> str:
    text = str(value or "").strip()
    return text if text.startswith("0x") and len(text) == 42 else ""


def _number(value: object) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed == parsed and parsed not in (float("inf"), float("-inf")) else None


def _stable(symbol: object) -> bool:
    normalized = _symbol(symbol)
    bridge_base = normalized[:-2] if normalized.endswith(".E") else normalized
    return normalized in STABLE_SYMBOLS or bridge_base in STABLE_SYMBOLS or normalized.endswith("USD")


def _classification(token0_symbol: object, token1_symbol: object) -> dict[str, Any]:
    token0 = _symbol(token0_symbol)
    token1 = _symbol(token1_symbol)
    token0_stable = _stable(token0)
    token1_stable = _stable(token1)
    stable_symbols = [symbol for symbol, stable in ((token0, token0_stable), (token1, token1_stable)) if symbol and stable]
    non_stable_symbols = [symbol for symbol, stable in ((token0, token0_stable), (token1, token1_stable)) if symbol and not stable]
    if token0_stable and token1_stable:
        pair_class = "stable_pair"
        label = "stable pair"
        route_role = "stable_anchor_pool"
    elif token0_stable or token1_stable:
        pair_class = "stable_to_non_stable_pair"
        label = "stable to non-stable pool"
        route_role = "flashloan_entry_or_exit_leg"
    else:
        pair_class = "non_stable_pair"
        label = "non-stable pool"
        route_role = "middle_volatility_leg"
    return {
        "pairClass": pair_class,
        "pairClassLabel": label,
        "routeRole": route_role,
        "token0IsStable": token0_stable,
        "token1IsStable": token1_stable,
        "stableSymbols": stable_symbols,
        "nonStableSymbols": non_stable_symbols,
    }


def _depth_band(pool: dict[str, Any]) -> dict[str, str]:
    tvl = _number(pool.get("tvlUsd") or pool.get("tvl_usd"))
    if tvl is not None and tvl >= 1_000_000:
        return {"level": "deep", "label": "deep"}
    if tvl is not None and tvl >= 100_000:
        return {"level": "medium", "label": "medium"}
    if tvl is not None and tvl >= 10_000:
        return {"level": "shallow", "label": "shallow"}
    if tvl is not None and tvl > 0:
        return {"level": "dust", "label": "dust"}
    return {"level": "observed", "label": "poolId and PoolKey observed"}


def _pool_manager_balance(pool: dict[str, Any], side: int) -> Any:
    metadata = pool.get(f"currency{side}Metadata")
    if isinstance(metadata, dict):
        return metadata.get("poolManagerBalance")
    pool_key = pool.get("poolKey") if isinstance(pool.get("poolKey"), dict) else {}
    metadata = pool_key.get(f"currency{side}Metadata")
    if isinstance(metadata, dict):
        return metadata.get("poolManagerBalance")
    return pool.get(f"token{side}Amount")


def _metadata_by_address(liquidity_payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows = liquidity_payload.get("flashAccountingSources") or liquidity_payload.get("flash_accounting_sources") or []
    metadata: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        address = _address(row.get("address") or row.get("currency_address") or row.get("token_address"))
        if not address:
            continue
        metadata[address.lower()] = row
    return metadata


def _metadata_symbol(symbol: str, address: str, metadata: dict[str, Any]) -> str:
    replacement = _symbol(metadata.get("symbol") or metadata.get("token_symbol"))
    if not replacement:
        return symbol
    address_prefix = _symbol(address[:8]) if address else ""
    if not symbol or symbol == address_prefix:
        return replacement
    return symbol


def _pool_candidate_row(
    pool: dict[str, Any],
    pool_manager: str,
    *,
    token_metadata: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    pool_id = str(pool.get("poolId") or pool.get("pool_id") or pool.get("id") or "").strip()
    token0 = _address(pool.get("token0") or pool.get("currency0"))
    token1 = _address(pool.get("token1") or pool.get("currency1"))
    metadata = token_metadata or {}
    metadata0 = metadata.get(token0.lower(), {}) if token0 else {}
    metadata1 = metadata.get(token1.lower(), {}) if token1 else {}
    token0_symbol = _metadata_symbol(_symbol(pool.get("token0Symbol") or pool.get("token0_symbol")), token0, metadata0)
    token1_symbol = _metadata_symbol(_symbol(pool.get("token1Symbol") or pool.get("token1_symbol")), token1, metadata1)
    if not pool_id or not token0_symbol or not token1_symbol:
        return None
    row = {
        "pool": pool_id,
        "poolIdentityMode": "pool_id",
        "poolManager": pool.get("poolManager") or pool.get("pool_manager") or pool_manager,
        "poolKey": pool.get("poolKey") or pool.get("pool_key"),
        "dex": "uniswap_v4",
        "protocol": "uniswap_v4",
        "fee": str(pool.get("fee") or "0"),
        "quality": "pass",
        "rejectReasons": [],
        "token0": token0,
        "token1": token1,
        "token0Symbol": token0_symbol,
        "token1Symbol": token1_symbol,
        "token0Decimals": pool.get("token0Decimals") or pool.get("token0_decimals") or metadata0.get("decimals"),
        "token1Decimals": pool.get("token1Decimals") or pool.get("token1_decimals") or metadata1.get("decimals"),
        "liquidity": str(pool.get("liquidity") or ""),
        "tick": str(pool.get("tick") or ""),
        "sqrtPriceX96": str(pool.get("sqrtPriceX96") or ""),
        "tickSpacing": pool.get("tickSpacing") or pool.get("tick_spacing"),
        "hooks": pool.get("hooks") or "",
        "balances": {
            "token0": _pool_manager_balance(pool, 0),
            "token1": _pool_manager_balance(pool, 1),
            "token0Usd": pool.get("token0Usd") or pool.get("token0_usd"),
            "token1Usd": pool.get("token1Usd") or pool.get("token1_usd"),
            "tvlUsd": pool.get("tvlUsd") or pool.get("tvl_usd"),
        },
        "quoteSummary": {},
        "evidence": {
            "initializeBlockNumber": pool.get("initializeBlockNumber"),
            "initializeTransactionHash": pool.get("initializeTransactionHash"),
            "poolKeyDecoded": bool(pool.get("poolKeyDecoded")),
            "discoveryMethod": "robinhood_testnet_v4_pool_liquidity_cache",
        },
    }
    row.update(_classification(token0_symbol, token1_symbol))
    row["depthBand"] = _depth_band(pool)
    return row


def _pool_id(pool: dict[str, Any]) -> str:
    return str(pool.get("poolId") or pool.get("pool_id") or pool.get("id") or "").strip().lower()


def _active_pool_ids(liquidity: dict[str, Any]) -> set[str]:
    return {
        pool_id
        for pool in liquidity.get("pools") or []
        if isinstance(pool, dict)
        for pool_id in [_pool_id(pool)]
        if pool_id
    }


def _known_v4_pool_ids() -> set[str]:
    """Known V4 pool ids from allowlist/manifest/test-chain evidence.

    These are stable trading-pool identities used for local test fixtures and
    explicit observability.  They are not a closed-loop declaration: the
    candidate stage still only returns pools that can be reached from X.
    """
    known_pool_ids: set[str] = set()
    for keys in _known_token_pool_ids().values():
        for key in keys:
            text = str(key or "").strip().lower()
            if text.startswith("0x") and len(text) == 66:
                known_pool_ids.add(text)
    return known_pool_ids


def _flash_accounting_assets(liquidity: dict[str, Any], pool_manager: str) -> tuple[list[str], set[str]]:
    rows = v4_flash_accounting_source_rows(liquidity, pool_manager=pool_manager)
    symbols = sorted(
        {
            _symbol(row.get("token_symbol"))
            for row in rows
            if isinstance(row, dict) and _symbol(row.get("token_symbol"))
        }
    )
    addresses = {
        _address(row.get("currency_address") or row.get("token_address"))
        for row in rows
        if isinstance(row, dict) and _address(row.get("currency_address") or row.get("token_address"))
    }
    return symbols, addresses


def _row_uses_flash_asset(row: dict[str, Any], symbols: set[str], addresses: set[str]) -> bool:
    row_symbols = {_symbol(row.get("token0Symbol")), _symbol(row.get("token1Symbol"))}
    row_addresses = {_address(row.get("token0")), _address(row.get("token1"))}
    return bool((symbols and row_symbols.intersection(symbols)) or (addresses and row_addresses.intersection(addresses)))


def _candidate_source_pools(pool_index: dict[str, Any], liquidity: dict[str, Any]) -> tuple[list[dict[str, Any]], str, str | None]:
    active_ids = _active_pool_ids(liquidity)
    indexed_pools = [pool for pool in pool_index.get("pools") or [] if isinstance(pool, dict)]
    liquidity_pools = [pool for pool in liquidity.get("pools") or [] if isinstance(pool, dict)]
    known_pool_ids = _known_v4_pool_ids()
    known_pools = [
        pool
        for pool in indexed_pools
        if _pool_id(pool) in known_pool_ids and _pool_id(pool) not in active_ids
    ]
    if pool_index.get("ok") and active_ids:
        matched = [pool for pool in indexed_pools if _pool_id(pool) in active_ids]
        if matched:
            return matched + known_pools + [
                pool for pool in liquidity_pools
                if _pool_id(pool) not in {_pool_id(item) for item in matched}
            ], "robinhood_testnet_v4_pool_sources_merged", pool_index.get("source")
        # The index and liquidity cache are generated by independent scans.  A
        # newer liquidity cache may contain pool ids that the smaller decoded
        # index does not yet know; filtering it against the index would make a
        # valid observed market appear empty.  Keep both sources and let the
        # asset filter below select the relevant pools.
        if liquidity_pools:
            seen = {_pool_id(pool) for pool in indexed_pools if _pool_id(pool)}
            return indexed_pools + [pool for pool in liquidity_pools if _pool_id(pool) not in seen], "robinhood_testnet_v4_pool_sources_merged", pool_index.get("source")
    if pool_index.get("ok"):
        return indexed_pools, "robinhood_testnet_v4_pool_index", pool_index.get("source")
    return liquidity_pools, "robinhood_testnet_v4_pool_liquidity", liquidity.get("source")


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


def _mark_v4_shared_yc_zc_sets(anchor_groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
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
        shared_tokens = _shared_tokens_from_pools(shared_pools)
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


def dex_pool_candidates_payload(
    *,
    request: ChainFlowRequest,
    pool_index_loader: Callable[[], dict[str, Any]] = load_robinhood_testnet_v4_pool_index,
    pool_liquidity_loader: Callable[[], dict[str, Any]] = load_robinhood_testnet_v4_pool_liquidity,
    **_: Any,
) -> dict[str, Any]:
    pool_index = pool_index_loader()
    liquidity = pool_liquidity_loader()
    index_payload = pool_index.get("payload") if isinstance(pool_index.get("payload"), dict) else {}
    liquidity_payload = liquidity.get("payload") if isinstance(liquidity.get("payload"), dict) else {}
    pool_manager = str(
        liquidity.get("pool_manager")
        or liquidity_payload.get("poolManager")
        or pool_index.get("pool_manager")
        or index_payload.get("poolManager")
        or ""
    ).strip()
    token_metadata = _metadata_by_address(liquidity_payload)
    anchor_symbols, anchor_addresses = _flash_accounting_assets(liquidity, pool_manager)
    source_pools, source_mode, source_path = _candidate_source_pools(pool_index, liquidity)
    anchor_symbol_set = set(anchor_symbols)
    rows = [
        row
        for pool in source_pools
        if isinstance(pool, dict)
        for row in [_pool_candidate_row(pool, pool_manager, token_metadata=token_metadata)]
        if row is not None and _row_uses_flash_asset(row, anchor_symbol_set, anchor_addresses)
    ]
    grouped: dict[str, dict[str, Any]] = {}
    for row in rows:
        symbol_pair = f"{row['token0Symbol']}:{row['token1Symbol']}"
        address_pair = ":".join(address for address in (row.get("token0"), row.get("token1")) if address)
        pair = grouped.setdefault(
            symbol_pair,
            {
                "symbolPair": symbol_pair,
                "addressPair": address_pair,
                "identityMode": "pool_key",
                "tokenX": row.get("token0"),
                "tokenY": row.get("token1"),
                "tokenXSymbol": row.get("token0Symbol"),
                "tokenYSymbol": row.get("token1Symbol"),
                "classification": _classification(row.get("token0Symbol"), row.get("token1Symbol")),
                "strictQualityPools": [],
                "preferredPools": [],
                "executionPools": [],
                "rejectedPools": [],
            },
        )
        pair["strictQualityPools"].append(row)
        pair["preferredPools"].append(row)
        if len(pair["executionPools"]) < 5:
            pair["executionPools"].append(row)

    pairs = []
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
    pairs.sort(key=lambda item: (-int(item.get("preferredPoolCount") or 0), str(item.get("symbolPair") or "")))
    anchor_groups = _mark_v4_shared_yc_zc_sets(
        build_anchor_candidate_groups(pairs, anchor_symbols=anchor_symbols or None)
    )
    shared_group_count = sum(1 for group in anchor_groups if group.get("ycZcSameSet"))
    shared_pool_count = sum(int(group.get("sharedPoolCount") or 0) for group in anchor_groups)
    return {
        "mode": "dex_pool_candidates",
        "source": "chain_flow",
        "fallbackReason": None,
        "poolStaticTable": {
            "path": source_path,
            "exists": bool(rows),
            "mode": source_mode,
            "network": request.network,
            "chainId": request.chain_id,
            "generatedAt": liquidity.get("refreshed_at") or liquidity_payload.get("generatedAt") or pool_index.get("refreshed_at") or index_payload.get("generatedAt"),
            "generatedBlockNumber": liquidity.get("block_number") or liquidity_payload.get("generatedBlockNumber") or pool_index.get("block_number") or index_payload.get("generatedBlockNumber"),
            "poolManager": pool_manager,
            "factory": None,
            "router": None,
            "quoter": None,
        },
        "filters": {"source": source_mode, "identity": "poolKey/poolId"},
        "summary": {
            "candidateRouteCount": str(len(rows)),
            "pairTableCount": str(len(pairs)),
            "anchorGroupCount": str(len(anchor_groups)),
            "ycPoolCount": str(sum(int(group.get("ycPoolCount") or 0) for group in anchor_groups)),
            "zcPoolCount": str(sum(int(group.get("zcPoolCount") or 0) for group in anchor_groups)),
            "ycTokenCount": str(sum(int(group.get("ycTokenCount") or 0) for group in anchor_groups)),
            "zcTokenCount": str(sum(int(group.get("zcTokenCount") or 0) for group in anchor_groups)),
            "ycZcSameSetGroupCount": str(shared_group_count),
            "sharedPoolSetCount": str(shared_pool_count),
            "candidateSetStorageMode": "shared_yc_zc_pool_set" if shared_group_count else "directional_pool_sets",
            "pairsWithStrictQualityPools": str(sum(1 for item in pairs if item["hasStrictQualityPools"])),
            "pairsWithPreferredPools": str(sum(1 for item in pairs if item["hasPreferredPools"])),
            "preferredPoolCount": str(sum(len(item["preferredPools"]) for item in pairs)),
            "executionPoolCount": str(sum(len(item["executionPools"]) for item in pairs)),
            "strictQualityPoolCount": str(sum(len(item["strictQualityPools"]) for item in pairs)),
            "v4PoolKeyCount": str(len(liquidity.get("pool_keys") or [])),
            "v4IndexedPoolCount": str(len(pool_index.get("pools") or [])),
            "v4ActivePoolCount": str(len(_active_pool_ids(liquidity))),
            "flashAccountingAssetCount": str(len(anchor_symbols)),
            "v4PoolIdCount": str(len(rows)),
            "initializedPoolCount": str(index_payload.get("initializedPoolCount") or liquidity_payload.get("initializedPoolCount") or len(rows)),
            "nonEthPoolCount": str(index_payload.get("nonEthPoolCount") or liquidity_payload.get("nonEthPoolCount") or 0),
            "ethRelatedPoolCount": str(index_payload.get("ethRelatedPoolCount") or liquidity_payload.get("ethRelatedPoolCount") or 0),
            "recentSwapPoolCount": str(index_payload.get("recentSwapPoolCount") or liquidity_payload.get("recentSwapPoolCount") or 0),
        },
        "pairs": pairs,
        "anchorGroups": anchor_groups,
        "errors": [] if rows else list(liquidity.get("blocking_reasons") or pool_index.get("blocking_reasons") or []),
    }


__all__ = ["dex_pool_candidates_payload"]
