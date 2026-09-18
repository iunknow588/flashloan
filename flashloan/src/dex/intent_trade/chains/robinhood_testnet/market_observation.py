from __future__ import annotations

from typing import Any, Callable

from dex.intent_trade.chains.robinhood_testnet.v4_flash_accounting_sources import (
    ZERO_ADDRESS,
    v4_flash_accounting_source_rows,
)
from dex.intent_trade.chains.common.dex_candidate_tables import pair_symbol_universe
from dex.intent_trade.chains.robinhood_testnet.v4_pool_index import load_robinhood_testnet_v4_pool_index
from dex.intent_trade.chains.robinhood_testnet.v4_pool_liquidity import (
    _known_token_pool_ids,
    _load_token_balance_scan_cache,
    load_robinhood_testnet_v4_pool_liquidity,
)
from dex.intent_trade.chains.robinhood.shared import ROBINHOOD_TESTNET_CHAIN_ID


def _symbol(value: object) -> str:
    return str(value or "").strip().upper()


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


def _route_symbol_allowed(symbol: object) -> bool:
    text = _symbol(symbol)
    return bool(text) and not text.startswith("ROBINHOOD")


def _protocol_in_scope(protocols: list[str] | None) -> bool:
    protocol_set = {str(protocol or "").strip().lower() for protocol in (protocols or []) if str(protocol or "").strip()}
    return not protocol_set or "uniswap_v4" in protocol_set


def observation_anchor_symbols(
    pool_payload: dict[str, Any],
    *,
    chain_id: int | None,
    protocols: list[str] | None = None,
) -> list[str]:
    if chain_id != ROBINHOOD_TESTNET_CHAIN_ID or not _protocol_in_scope(protocols):
        return []
    anchors = {
        _symbol(group.get("anchorSymbol"))
        for group in pool_payload.get("anchorGroups") or []
        if isinstance(group, dict) and _route_symbol_allowed(group.get("anchorSymbol"))
    }
    if anchors:
        return sorted(anchors)
    return sorted({symbol for symbol in pair_symbol_universe(pool_payload.get("pairs") or []) if _route_symbol_allowed(symbol)})


def borrowable_symbols_from_pool_payload(
    pool_payload: dict[str, Any],
    *,
    chain_id: int | None,
    protocols: list[str] | None = None,
) -> list[str]:
    if chain_id != ROBINHOOD_TESTNET_CHAIN_ID or not _protocol_in_scope(protocols):
        return []
    return sorted({symbol for symbol in pair_symbol_universe(pool_payload.get("pairs") or []) if _route_symbol_allowed(symbol)})


def _token_metadata_from_flash_rows(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    metadata: dict[str, dict[str, Any]] = {}
    for row in rows:
        address = _address(row.get("currency_address") or row.get("token_address"))
        if address:
            metadata[address] = row
    return metadata


def _pool_token(pool: dict[str, Any], side: int, metadata_by_address: dict[str, dict[str, Any]]) -> dict[str, Any]:
    address = _address(pool.get(f"token{side}") or pool.get(f"currency{side}"))
    metadata = metadata_by_address.get(address) or {}
    symbol = _symbol(metadata.get("token_symbol") or metadata.get("symbol") or pool.get(f"token{side}Symbol"))
    if not symbol and address:
        symbol = "ETH" if address == ZERO_ADDRESS.lower() else address[:8].upper()
    return {
        "symbol": symbol,
        "address": address,
        "decimals": metadata.get("decimals") or pool.get(f"token{side}Decimals"),
    }


def _pool_row(pool: dict[str, Any], metadata_by_address: dict[str, dict[str, Any]], *, source: str) -> dict[str, Any] | None:
    pool_id = _pool_id(pool)
    token0 = _pool_token(pool, 0, metadata_by_address)
    token1 = _pool_token(pool, 1, metadata_by_address)
    if not pool_id or not token0["address"] or not token1["address"] or not token0["symbol"] or not token1["symbol"]:
        return None
    return {
        "pool": pool_id,
        "poolId": pool_id,
        "poolIdentityMode": "v4_pool_id",
        "protocol": "uniswap_v4",
        "dex": "uniswap_v4",
        "fee": pool.get("fee"),
        "tickSpacing": pool.get("tickSpacing"),
        "hooks": pool.get("hooks"),
        "poolManager": pool.get("poolManager") or pool.get("pool_manager"),
        "poolKey": pool.get("poolKey") or pool.get("pool_key"),
        "quoter": pool.get("quoter") or pool.get("v4Quoter") or pool.get("v4_quoter"),
        "token0": pool.get("token0") or pool.get("currency0"),
        "token1": pool.get("token1") or pool.get("currency1"),
        "token0Symbol": token0["symbol"],
        "token1Symbol": token1["symbol"],
        "token0Decimals": token0.get("decimals"),
        "token1Decimals": token1.get("decimals"),
        "sqrtPriceX96": pool.get("sqrtPriceX96"),
        "tick": pool.get("tick"),
        "liquidity": pool.get("liquidity") or "",
        "balances": pool.get("balances") or {"tvlUsd": pool.get("tvlUsd") or 0},
        "depthBand": pool.get("depthBand") or {"level": "indexed", "label": "indexed V4 PoolKey"},
        "depthStatus": pool.get("depthStatus") or "",
        "symbolPair": f"{token0['symbol']}:{token1['symbol']}",
        "addressPair": f"{token0['address']}:{token1['address']}",
        "source": source,
        "status": pool.get("status") or "initialized_pool",
        "recentSwapActive": pool.get("recentSwapActive"),
        "evidence": {
            **(pool.get("evidence") or {}),
            "source": "v4_pool_index",
            "bridgeOnly": True,
            "poolKeyDecoded": bool(pool.get("poolKeyDecoded") or pool.get("poolKey")),
            "initializeBlockNumber": pool.get("initializeBlockNumber"),
            "initializeTransactionHash": pool.get("initializeTransactionHash"),
        },
    }


def _pool_contains(pool: dict[str, Any], address: str) -> bool:
    normalized = _address(address)
    return normalized in {_address(pool.get("token0")), _address(pool.get("token1"))}


def _best_pool(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    candidates = [row for row in rows if isinstance(row, dict) and _pool_id(row)]
    candidates.sort(
        key=lambda row: (
            bool(row.get("recentSwapActive")),
            float((row.get("balances") or {}).get("tvlUsd") or 0),
            str(_pool_id(row)),
        ),
        reverse=True,
    )
    return candidates[0] if candidates else None


def _fund_associations(
    token_address: str,
    *,
    fund_addresses: set[str],
    funds_by_address: dict[str, dict[str, Any]],
    neighbors_by_address: dict[str, set[str]],
) -> list[dict[str, Any]]:
    address = _address(token_address)
    associated_addresses: set[str] = set()
    if address in fund_addresses:
        associated_addresses.add(address)
    associated_addresses.update(neighbors_by_address.get(address, set()).intersection(fund_addresses))
    return [
        funds_by_address[item]
        for item in sorted(associated_addresses, key=lambda value: (_symbol(funds_by_address[value].get("symbol")), value))
        if item in funds_by_address
    ]


def _association_summary(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "symbol": _symbol(row.get("symbol")),
            "address": _address(row.get("address")),
            "executorSupported": bool(row.get("executorSupported")),
            "poolManagerBalance": row.get("poolManagerBalance"),
            "poolManagerBalanceRaw": row.get("poolManagerBalanceRaw"),
        }
        for row in rows
    ]


def _associated_observation_pair_rows(pools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for pool in pools:
        edge = _pool_edge(pool)
        if edge is None:
            continue
        row = grouped.get(edge)
        token0 = {
            "symbol": _symbol(pool.get("token0Symbol")),
            "address": _address(pool.get("token0")),
            "associatedFunds": pool.get("token0AssociatedFunds") or [],
        }
        token1 = {
            "symbol": _symbol(pool.get("token1Symbol")),
            "address": _address(pool.get("token1")),
            "associatedFunds": pool.get("token1AssociatedFunds") or [],
        }
        if row is None:
            row = {
                "kind": "robinhood_testnet_v4_associated_fund_anchor_observation_pair",
                "source": "robinhood_testnet_v4_fund_anchor_pair_observation",
                "symbolPair": pool.get("symbolPair") or f"{token0['symbol']}:{token1['symbol']}",
                "addressPair": ":".join(edge),
                "token0": token0,
                "token1": token1,
                "associatedFundSymbols": [],
                "poolCount": 0,
                "directSwapPoolCount": 0,
                "pools": [],
                "directSwapPools": [],
                "closedLoopReady": False,
                "observationReason": pool.get("observationReason")
                or "pair_tokens_are_associated_with_flash_fund_assets",
                "routeSemantics": "x_y_entry_y_z_direct_z_x_exit",
                "directSwapRequired": True,
            }
            grouped[edge] = row
        row["poolCount"] += 1
        row["directSwapPoolCount"] += 1
        row["closedLoopReady"] = bool(row.get("closedLoopReady") or pool.get("closedLoopReady"))
        row["associatedFundSymbols"] = sorted(
            {
                *[str(symbol) for symbol in row.get("associatedFundSymbols") or [] if str(symbol or "").strip()],
                *[str(symbol) for symbol in pool.get("associatedFundSymbols") or [] if str(symbol or "").strip()],
            }
        )
        direct_pool = {
            "pool": _pool_id(pool),
            "poolId": _pool_id(pool),
            "poolIdentityMode": pool.get("poolIdentityMode") or "v4_pool_id",
            "protocol": pool.get("protocol") or pool.get("dex") or "uniswap_v4",
            "dex": pool.get("dex") or pool.get("protocol") or "uniswap_v4",
            "fee": pool.get("fee"),
            "tickSpacing": pool.get("tickSpacing"),
            "hooks": pool.get("hooks"),
            "tick": pool.get("tick"),
            "sqrtPriceX96": pool.get("sqrtPriceX96"),
            "liquidity": pool.get("liquidity"),
            "balances": pool.get("balances") or {},
            "closedLoopReady": bool(pool.get("closedLoopReady")),
            "role": "direct_y_z_swap_pool",
        }
        row["pools"].append(direct_pool)
        row["directSwapPools"].append(direct_pool)
    return sorted(
        grouped.values(),
        key=lambda row: (not bool(row.get("closedLoopReady")), str(row.get("symbolPair") or ""), str(row.get("addressPair") or "")),
    )


def _attach_route_evidence_to_associated_pairs(
    associated_pairs: list[dict[str, Any]],
    sequences: list[dict[str, Any]],
) -> None:
    for pair in associated_pairs:
        address_pair = str(pair.get("addressPair") or "").lower()
        pool_ids = {
            str(pool.get("pool") or pool.get("poolId") or "").lower()
            for pool in pair.get("directSwapPools") or pair.get("pools") or []
            if str(pool.get("pool") or pool.get("poolId") or "").strip()
        }
        evidence: list[dict[str, Any]] = []
        previews: list[str] = []
        for sequence in sequences:
            route_pools = [pool for pool in sequence.get("routePools") or [] if isinstance(pool, dict)]
            bridge = next(
                (pool for pool in route_pools if str(pool.get("direction") or "").lower() == "bridge"),
                sequence.get("bridgePool") if isinstance(sequence.get("bridgePool"), dict) else {},
            )
            bridge_pair = str(bridge.get("pairAddress") or "").lower()
            bridge_pool_id = str(bridge.get("pool") or bridge.get("poolId") or "").lower()
            if not ((address_pair and bridge_pair == address_pair) or (bridge_pool_id and bridge_pool_id in pool_ids)):
                continue
            path_text = str(sequence.get("pathText") or "").strip()
            if path_text and path_text not in previews:
                previews.append(path_text)
            evidence.append(
                {
                    "pathText": path_text,
                    "anchorSymbol": sequence.get("anchorSymbol"),
                    "anchorAddress": sequence.get("anchorAddress"),
                    "routePools": route_pools,
                    "ycPool": sequence.get("ycPool"),
                    "bridgePool": bridge,
                    "zcPool": sequence.get("zcPool"),
                    "routeSemantics": "x_y_entry_y_z_direct_z_x_exit",
                }
            )
        pair["closedLoopRoutePreviews"] = previews
        pair["routeEvidence"] = evidence[:12]


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
        "poolIdentityMode": pool.get("poolIdentityMode") or "",
        "protocol": pool.get("protocol") or pool.get("dex") or "",
        "dex": pool.get("dex") or pool.get("protocol") or "",
        "fee": pool.get("fee"),
        "tickSpacing": pool.get("tickSpacing") or pool.get("tick_spacing"),
        "hooks": pool.get("hooks") or "",
        "poolManager": pool.get("poolManager") or pool.get("pool_manager"),
        "poolKey": pool.get("poolKey") or pool.get("pool_key"),
        "quoter": pool.get("quoter") or pool.get("v4Quoter") or pool.get("v4_quoter"),
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
    }


def _group_direction_tokens(group: dict[str, Any], direction: str) -> list[dict[str, Any]]:
    tokens = [
        row
        for row in group.get(f"{direction}Tokens") or []
        if isinstance(row, dict) and _symbol(row.get("symbol"))
    ]
    if tokens or group.get(f"{direction}TokensRef") != "sharedTokens":
        return tokens
    return [
        row
        for row in group.get("sharedTokens") or []
        if isinstance(row, dict) and _symbol(row.get("symbol"))
    ]


def robinhood_testnet_v4_bridge_pairs_for_anchor_groups(
    anchor_groups: list[dict[str, Any]],
    *,
    chain_id: int | None,
    protocols: list[str] | None,
    pool_index_loader: Callable[[], dict[str, Any]] = load_robinhood_testnet_v4_pool_index,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    protocol_set = {str(protocol or "").strip().lower() for protocol in (protocols or []) if str(protocol or "").strip()}
    if chain_id != ROBINHOOD_TESTNET_CHAIN_ID or (protocol_set and "uniswap_v4" not in protocol_set):
        return [], {"enabled": False, "status": "out_of_scope"}

    needed_edges: dict[tuple[str, str], tuple[dict[str, Any], dict[str, Any]]] = {}
    token_metadata: dict[str, dict[str, Any]] = {}
    candidate_combination_count = 0
    for group in anchor_groups:
        if not isinstance(group, dict):
            continue
        for yc_token in _group_direction_tokens(group, "yc"):
            y_address = _address(yc_token.get("address"))
            y_symbol = _symbol(yc_token.get("symbol"))
            if not y_address or not y_symbol or not _route_symbol_allowed(y_symbol):
                continue
            token_metadata.setdefault(y_address, yc_token)
            for zc_token in _group_direction_tokens(group, "zc"):
                z_address = _address(zc_token.get("address"))
                z_symbol = _symbol(zc_token.get("symbol"))
                if not z_address or not z_symbol or not _route_symbol_allowed(z_symbol) or z_address == y_address:
                    continue
                candidate_combination_count += 1
                token_metadata.setdefault(z_address, zc_token)
                needed_edges.setdefault(tuple(sorted((y_address, z_address))), (yc_token, zc_token))

    if not needed_edges:
        return [], {
            "enabled": True,
            "status": "no_addressed_yz_edges",
            "candidateCombinationCount": candidate_combination_count,
            "neededEdgeCount": 0,
            "matchedEdgeCount": 0,
            "matchedPoolCount": 0,
        }

    index = pool_index_loader()
    if not index.get("ok"):
        return [], {
            "enabled": True,
            "status": index.get("status") or "v4_pool_index_unavailable",
            "source": index.get("source"),
            "blockingReasons": index.get("blocking_reasons") or [],
            "candidateCombinationCount": candidate_combination_count,
            "neededEdgeCount": len(needed_edges),
            "matchedEdgeCount": 0,
            "matchedPoolCount": 0,
        }

    pools_by_edge: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for pool in index.get("pools") or []:
        if not isinstance(pool, dict):
            continue
        edge = _pool_edge(pool)
        if edge not in needed_edges:
            continue
        row = _pool_row(pool, token_metadata, source="v4_pool_index_bridge_pool")
        if row is not None:
            pools_by_edge.setdefault(edge, []).append(row)

    bridge_pairs: list[dict[str, Any]] = []
    for edge, pools in sorted(pools_by_edge.items()):
        left_token, right_token = needed_edges[edge]
        left_symbol = _symbol(left_token.get("symbol"))
        right_symbol = _symbol(right_token.get("symbol"))
        left_address = _address(left_token.get("address"))
        right_address = _address(right_token.get("address"))
        best_pools = sorted(pools, key=lambda item: str(_pool_id(item)))
        bridge_pairs.append(
            {
                "symbolPair": f"{left_symbol}:{right_symbol}",
                "addressPair": f"{left_address}:{right_address}",
                "tokenX": left_address,
                "tokenY": right_address,
                "tokenXSymbol": left_symbol,
                "tokenYSymbol": right_symbol,
                "preferredPools": best_pools,
                "executionPools": best_pools[:5],
                "strictQualityPools": best_pools,
                "classification": {
                    "source": "v4_pool_index_bridge_lookup",
                    "bridgeOnly": True,
                    "protocol": "uniswap_v4",
                },
            }
        )

    matched_pool_count = sum(len(pair.get("preferredPools") or []) for pair in bridge_pairs)
    return bridge_pairs, {
        "enabled": True,
        "status": "v4_pool_index_bridge_lookup_complete",
        "source": index.get("source"),
        "blockNumber": index.get("block_number"),
        "refreshedAt": index.get("refreshed_at"),
        "candidateCombinationCount": candidate_combination_count,
        "neededEdgeCount": len(needed_edges),
        "matchedEdgeCount": len(bridge_pairs),
        "matchedPoolCount": matched_pool_count,
    }


def robinhood_testnet_v4_fund_anchor_pair_observation(
    *,
    chain_id: int | None,
    protocols: list[str] | None,
    pool_index_loader: Callable[[], dict[str, Any]] = load_robinhood_testnet_v4_pool_index,
    pool_liquidity_loader: Callable[[], dict[str, Any]] = load_robinhood_testnet_v4_pool_liquidity,
    route_limit: int = 200,
) -> dict[str, Any]:
    protocol_set = {str(protocol or "").strip().lower() for protocol in (protocols or []) if str(protocol or "").strip()}
    if chain_id != ROBINHOOD_TESTNET_CHAIN_ID or (protocol_set and "uniswap_v4" not in protocol_set):
        return {"enabled": False, "status": "out_of_scope", "observationSequences": []}

    index = pool_index_loader()
    liquidity = pool_liquidity_loader()
    # The liquidity document is intentionally scoped to active flash sources.
    # Reuse the balance scan cache only for allowlisted/trade-demand tokens so
    # cold pools remain visible without dragging the whole historical cache in.
    cached_liquidity = dict(liquidity)
    cached_payload = dict(liquidity.get("payload") or {})
    cache = _load_token_balance_scan_cache(
        pool_manager=str(
            liquidity.get("pool_manager")
            or (liquidity.get("payload") or {}).get("poolManager")
            or ""
        )
    )
    cached_entries = cache.get("entries") if isinstance(cache.get("entries"), dict) else {}
    configured_sources = list(
        cached_payload.get("flashAccountingSources")
        or cached_payload.get("flash_accounting_sources")
        or liquidity.get("flashAccountingSources")
        or []
    )
    configured_addresses = {
        str(row.get("address") or "").lower()
        for row in configured_sources
        if isinstance(row, dict)
    }
    known_token_addresses = set(_known_token_pool_ids().keys())
    for address, entry in cached_entries.items():
        if not isinstance(entry, dict) or str(address).lower() in configured_addresses:
            continue
        if str(address).lower() not in known_token_addresses:
            continue
        try:
            balance_raw = int(str(entry.get("balanceRaw") or "0"))
        except (TypeError, ValueError):
            balance_raw = 0
        if balance_raw <= 0:
            continue
        entry_decimals = entry.get("decimals")
        pool_manager_balance = (
            balance_raw / 10 ** int(entry_decimals)
            if entry_decimals is not None
            else None
        )
        configured_sources.append(
            {
                "address": entry.get("address") or address,
                "symbol": entry.get("symbol"),
                "decimals": entry_decimals,
                "poolManagerBalanceRaw": str(balance_raw),
                "poolManagerBalance": pool_manager_balance,
                "poolIds": entry.get("poolIds") or [],
                "source": "token_balance_scan_cache",
            }
        )
    cached_payload["flashAccountingSources"] = configured_sources
    cached_liquidity["payload"] = cached_payload
    cached_liquidity["flashAccountingSources"] = configured_sources
    liquidity = cached_liquidity
    liquidity_payload = liquidity.get("payload") if isinstance(liquidity.get("payload"), dict) else {}
    index_payload = index.get("payload") if isinstance(index.get("payload"), dict) else {}
    pool_manager = str(
        liquidity.get("pool_manager")
        or liquidity_payload.get("poolManager")
        or index.get("pool_manager")
        or index_payload.get("poolManager")
        or ""
    ).strip()
    if not index.get("ok"):
        return {
            "enabled": True,
            "status": index.get("status") or "v4_pool_index_unavailable",
            "source": index.get("source"),
            "blockingReasons": index.get("blocking_reasons") or [],
            "observationSequences": [],
        }
    if not liquidity.get("ok") or not pool_manager:
        return {
            "enabled": True,
            "status": liquidity.get("status") or "v4_pool_liquidity_unavailable",
            "source": liquidity.get("source"),
            "blockingReasons": liquidity.get("blocking_reasons") or [],
            "observationSequences": [],
        }

    flash_rows = v4_flash_accounting_source_rows(liquidity, pool_manager=pool_manager)
    metadata_by_address = _token_metadata_from_flash_rows(flash_rows)
    funds: list[dict[str, Any]] = []
    for row in flash_rows:
        address = _address(row.get("currency_address") or row.get("token_address"))
        symbol = _symbol(row.get("token_symbol") or row.get("symbol"))
        if address and symbol:
            funds.append(
                {
                    "symbol": symbol,
                    "address": address,
                    "executorSupported": bool(row.get("executor_supported")),
                    "poolManagerBalance": row.get("poolmanager_balance"),
                    "poolManagerBalanceRaw": row.get("poolmanager_balance_raw"),
                }
            )
    funds_by_address = {fund["address"]: fund for fund in funds}
    fund_addresses = set(funds_by_address)

    pools_by_edge: dict[tuple[str, str], list[dict[str, Any]]] = {}
    neighbors_by_address: dict[str, set[str]] = {}
    token_by_address: dict[str, dict[str, Any]] = {}
    indexed_pair_count = 0
    non_eth_pair_count = 0
    for pool in index.get("pools") or []:
        if not isinstance(pool, dict):
            continue
        edge = _pool_edge(pool)
        if edge is None:
            continue
        row = _pool_row(pool, metadata_by_address, source="v4_pool_index_fund_anchor_pair_observation")
        if row is None:
            continue
        pools_by_edge.setdefault(edge, []).append(row)
        left, right = edge
        neighbors_by_address.setdefault(left, set()).add(right)
        neighbors_by_address.setdefault(right, set()).add(left)
        for side in (0, 1):
            token = {
                "symbol": _symbol(row.get(f"token{side}Symbol")),
                "address": _address(row.get(f"token{side}")),
            }
            if token["address"] and token["symbol"]:
                token_by_address.setdefault(token["address"], token)
        indexed_pair_count += 1
        if ZERO_ADDRESS.lower() not in edge:
            non_eth_pair_count += 1

    # The testnet pool index may contain more initialized AMM pairs than the
    # flash-source allowlist. Promote known/system-owned tokens that actually
    # appear in the pool graph so route discovery is not limited to tz/ta/tb
    # only, without treating every unrelated initialized currency as a flash
    # anchor.
    for address in sorted(set(_known_token_pool_ids().keys())):
        token = token_by_address.get(address)
        if token is None or address in funds_by_address:
            continue
        if not _route_symbol_allowed(token.get("symbol")):
            continue
        funds_by_address[address] = {
            "symbol": token["symbol"],
            "address": address,
            "executorSupported": False,
            "poolManagerBalance": None,
            "poolManagerBalanceRaw": None,
        }
    fund_addresses = set(funds_by_address)
    funds = sorted(funds_by_address.values(), key=lambda row: (str(row.get("symbol") or ""), str(row.get("address") or "")))

    associated_observation_edges: set[tuple[str, str]] = set()
    for edge, bridge_pools in sorted(pools_by_edge.items()):
        left_address, right_address = edge
        left_associations = _fund_associations(
            left_address,
            fund_addresses=fund_addresses,
            funds_by_address=funds_by_address,
            neighbors_by_address=neighbors_by_address,
        )
        right_associations = _fund_associations(
            right_address,
            fund_addresses=fund_addresses,
            funds_by_address=funds_by_address,
            neighbors_by_address=neighbors_by_address,
        )
        if not left_associations or not right_associations:
            continue
        associated_observation_edges.add(edge)

    sequences: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str, str, str]] = set()
    closed_loop_fund_anchor_edges: set[tuple[str, str]] = set()

    def _append_route(
        *,
        anchor: dict[str, Any],
        y_address: str,
        z_address: str,
        yz_pool: dict[str, Any],
    ) -> bool:
        xy_pool = _best_pool(pools_by_edge.get(tuple(sorted((anchor["address"], y_address)))) or [])
        zx_pool = _best_pool(pools_by_edge.get(tuple(sorted((z_address, anchor["address"])))) or [])
        if xy_pool is None or zx_pool is None:
            return False
        first_token = token_by_address.get(y_address)
        second_token = token_by_address.get(z_address)
        if first_token is None or second_token is None:
            return False
        key = (
            anchor["address"],
            y_address,
            z_address,
            _pool_id(xy_pool).lower(),
            _pool_id(yz_pool).lower(),
            _pool_id(zx_pool).lower(),
        )
        if key in seen:
            return False
        seen.add(key)
        closed_loop_fund_anchor_edges.add(tuple(sorted((y_address, z_address))))
        route_rows = [
            _step_row(xy_pool, from_token=anchor, to_token=first_token, direction="yc"),
            _step_row(yz_pool, from_token=first_token, to_token=second_token, direction="bridge"),
            _step_row(zx_pool, from_token=second_token, to_token=anchor, direction="zc"),
        ]
        path_symbols = [anchor["symbol"], first_token["symbol"], second_token["symbol"], anchor["symbol"]]
        sequences.append(
            {
                "kind": "robinhood_testnet_v4_fund_anchor_pair_observation",
                "source": "robinhood_testnet_v4_fund_anchor_pair_observation",
                "anchorSymbol": anchor["symbol"],
                "anchorAddress": anchor["address"],
                "counterSymbol": second_token["symbol"],
                "bridgeSymbol": first_token["symbol"],
                "pathSymbols": path_symbols,
                "pathText": " -> ".join(path_symbols),
                "pool": _pool_id(yz_pool),
                "ycPool": route_rows[0],
                "bridgePool": route_rows[1],
                "zcPool": route_rows[2],
                "routePools": route_rows,
                "candidatePoolCount": "3",
                "matchedPoolCount": "3",
                "anchorAddresses": [anchor["address"]],
                "counterTokenCount": "2",
                "executorSupported": anchor["executorSupported"],
                "poolManagerBalance": anchor.get("poolManagerBalance"),
                "poolManagerBalanceRaw": anchor.get("poolManagerBalanceRaw"),
            }
        )
        return len(sequences) >= route_limit

    for anchor in funds:
        for y_address in sorted(neighbors_by_address.get(anchor["address"]) or []):
            for z_address in sorted(neighbors_by_address.get(y_address) or []):
                if z_address == anchor["address"] or z_address == y_address:
                    continue
                yz_pool = _best_pool(pools_by_edge.get(tuple(sorted((y_address, z_address)))) or [])
                if yz_pool is None:
                    continue
                if _append_route(anchor=anchor, y_address=y_address, z_address=z_address, yz_pool=yz_pool):
                    break
            if len(sequences) >= route_limit:
                break
        if len(sequences) >= route_limit:
            break

    # The observation page only needs bridge pools that participate in a
    # complete fund-anchor loop. Entry and exit pools remain in routePools.
    associated_observation_pools: list[dict[str, Any]] = []
    associated_observation_tokens: dict[str, dict[str, Any]] = {}
    for edge in sorted(closed_loop_fund_anchor_edges):
        for pool in sorted(pools_by_edge.get(edge) or [], key=lambda row: str(_pool_id(row))):
            token0_address = _address(pool.get("token0"))
            token1_address = _address(pool.get("token1"))
            token0_associations = _fund_associations(
                token0_address,
                fund_addresses=fund_addresses,
                funds_by_address=funds_by_address,
                neighbors_by_address=neighbors_by_address,
            )
            token1_associations = _fund_associations(
                token1_address,
                fund_addresses=fund_addresses,
                funds_by_address=funds_by_address,
                neighbors_by_address=neighbors_by_address,
            )
            for address, associations in (
                (token0_address, token0_associations),
                (token1_address, token1_associations),
            ):
                token = token_by_address.get(address) or {"symbol": address[:8].upper(), "address": address}
                associated_observation_tokens[address] = {
                    **token,
                    "associatedFunds": _association_summary(associations),
                    "associatedFundSymbols": [_symbol(row.get("symbol")) for row in associations],
                }
            associated_observation_pools.append(
                {
                    **pool,
                    "kind": "robinhood_testnet_v4_associated_fund_anchor_observation_pool",
                    "source": "robinhood_testnet_v4_fund_anchor_pair_observation",
                    "observationPool": True,
                    "closedLoopReady": True,
                    "token0AssociatedFunds": _association_summary(token0_associations),
                    "token1AssociatedFunds": _association_summary(token1_associations),
                    "associatedFundSymbols": sorted(
                        {
                            _symbol(row.get("symbol"))
                            for row in token0_associations + token1_associations
                            if _symbol(row.get("symbol"))
                        }
                    ),
                    "observationReason": "bridge_pool_participates_in_closed_fund_anchor_loop",
                }
            )

    associated_observation_pairs = _associated_observation_pair_rows(associated_observation_pools)
    _attach_route_evidence_to_associated_pairs(associated_observation_pairs, sequences)

    return {
        "enabled": True,
        "status": "v4_fund_anchor_pair_observation_complete",
        "source": index.get("source"),
        "liquiditySource": liquidity.get("source"),
        "blockNumber": index.get("block_number"),
        "refreshedAt": index.get("refreshed_at"),
        "fundAssetCount": len(funds),
        "executorSupportedFundAssetCount": sum(1 for fund in funds if fund.get("executorSupported")),
        "fundAnchorCandidatePairCount": indexed_pair_count,
        "associatedFundAnchorPairCount": len(closed_loop_fund_anchor_edges),
        "associatedObservationCandidatePairCount": len(associated_observation_edges),
        "associatedObservationPairCount": len(associated_observation_pairs),
        "associatedObservationPoolCount": len(associated_observation_pools),
        "associatedObservationTokenCount": len(associated_observation_tokens),
        "closedLoopAssociatedFundAnchorPairCount": len(closed_loop_fund_anchor_edges),
        "nonFundPairCount": indexed_pair_count,
        "associatedNonFundPairCount": len(closed_loop_fund_anchor_edges),
        "closedLoopAssociatedNonFundPairCount": len(closed_loop_fund_anchor_edges),
        "nonEthPairCount": non_eth_pair_count,
        "associatedNonEthPairCount": len(closed_loop_fund_anchor_edges),
        "closedLoopAssociatedNonEthPairCount": len(closed_loop_fund_anchor_edges),
        "routeCount": len(sequences),
        "associatedObservationPairs": associated_observation_pairs,
        "associatedObservationPools": associated_observation_pools,
        "associatedObservationTokens": sorted(
            associated_observation_tokens.values(),
            key=lambda item: (str(item.get("symbol") or ""), str(item.get("address") or "")),
        ),
        "observationSequences": sequences,
    }


def robinhood_testnet_v4_non_fund_pair_first_observation(**kwargs: Any) -> dict[str, Any]:
    return robinhood_testnet_v4_fund_anchor_pair_observation(**kwargs)


def robinhood_testnet_v4_non_eth_pair_first_observation(**kwargs: Any) -> dict[str, Any]:
    return robinhood_testnet_v4_fund_anchor_pair_observation(**kwargs)


def bridge_pairs_for_anchor_groups(
    anchor_groups: list[dict[str, Any]],
    *,
    chain_id: int | None,
    protocols: list[str] | None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    return robinhood_testnet_v4_bridge_pairs_for_anchor_groups(
        anchor_groups,
        chain_id=chain_id,
        protocols=protocols,
    )


def fund_anchor_pair_observation(
    *,
    chain_id: int | None,
    protocols: list[str] | None,
) -> dict[str, Any]:
    return robinhood_testnet_v4_fund_anchor_pair_observation(
        chain_id=chain_id,
        protocols=protocols,
    )


def market_observation_flow(*, request: Any, **_: Any) -> dict[str, Any]:
    return robinhood_testnet_v4_fund_anchor_pair_observation(
        chain_id=request.chain_id,
        protocols=[request.protocol] if request.protocol else [],
    )


__all__ = [
    "borrowable_symbols_from_pool_payload",
    "bridge_pairs_for_anchor_groups",
    "fund_anchor_pair_observation",
    "market_observation_flow",
    "observation_anchor_symbols",
    "robinhood_testnet_v4_bridge_pairs_for_anchor_groups",
    "robinhood_testnet_v4_fund_anchor_pair_observation",
    "robinhood_testnet_v4_non_fund_pair_first_observation",
    "robinhood_testnet_v4_non_eth_pair_first_observation",
]
