"""Database-first page payloads for candidate pools and market observation.

The page read path is:

    page -> rs_bridge -> Rust sqlite_read (or Python storage) -> SQLite

This module converts the independent business tables back into the page-facing
shape that the legacy JSON surfaces used. It never writes to SQLite and never
reads page JSON shards.
"""

from __future__ import annotations

import json
from typing import Any

from core.db.database_path import absolute_database_path
from core.db.storage_chain_core import load_chain_tokens
from core.db.storage_dex_candidate import MAX_USABLE_CANDIDATE_FIXED_FEE
from core.rs_bridge import (
    load_dex_candidate_pools,
    load_market_observation,
)


ROBINHOOD_NETWORK = "robinhood"
ROBINHOOD_CHAIN_ID = 4663
ROBINHOOD_MARKET_ID = "dex-arbitrage-4663-uniswap-v4"
ROBINHOOD_PROTOCOL = "uniswap_v4"
ROBINHOOD_TESTNET_NETWORK = "robinhood-testnet"
ROBINHOOD_TESTNET_CHAIN_ID = 46630


def _network_for_chain_id(chain_id: int) -> str:
    return ROBINHOOD_TESTNET_NETWORK if int(chain_id or 0) == ROBINHOOD_TESTNET_CHAIN_ID else ROBINHOOD_NETWORK


def _text(value: Any) -> str:
    return str(value or "").strip()


def _lower(value: Any) -> str:
    return _text(value).lower()


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _bool(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return _text(value).lower() in {"1", "true", "yes", "y", "on"}


def _json_loads(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if value is None or value == "":
        return {}
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}
    return value


def _token_symbol_map(database_url: str, *, chain_id: int = ROBINHOOD_CHAIN_ID) -> dict[str, str]:
    rows = load_chain_tokens(
        str(absolute_database_path(database_url)),
        network=_network_for_chain_id(chain_id),
        chain_id=chain_id,
    )
    return {_lower(row.get("token_address")): _text(row.get("token_symbol")) for row in rows}


def _anchor_rows(db: str, *, chain_id: int = ROBINHOOD_CHAIN_ID) -> list[dict[str, Any]]:
    from core.db.storage_business_io import query

    return query(
        str(db),
        """
        SELECT DISTINCT anchor_symbol, anchor_address
        FROM dex_candidate_pool_pairs
        WHERE network = ? AND chain_id = ? AND protocol = ?
        ORDER BY anchor_symbol
        """,
        (_network_for_chain_id(chain_id), chain_id, ROBINHOOD_PROTOCOL),
    )


def _pool_address(pool: dict[str, Any]) -> str:
    return _text(pool.get("pool_id") or pool.get("poolId") or pool.get("pool"))


def _symbol_for_address(address: str, token_symbols: dict[str, str]) -> str:
    return _text(token_symbols.get(_lower(address)) or _text(address)[:6])


def _candidate_pool_row(
    pool: dict[str, Any],
    *,
    token_symbols: dict[str, str],
    anchor_address: str,
    anchor_symbol: str,
) -> dict[str, Any]:
    token0 = _lower(pool.get("token0"))
    token1 = _lower(pool.get("token1"))
    token0_symbol = _text(token_symbols.get(token0) or pool.get("token0Symbol") or "")
    token1_symbol = _text(token_symbols.get(token1) or pool.get("token1Symbol") or "")
    if token0 == _lower(anchor_address):
        counter_address = token1
        counter_symbol = token1_symbol
        route_text = f"{anchor_symbol} -> {counter_symbol}"
    else:
        counter_address = token0
        counter_symbol = token0_symbol
        route_text = f"{counter_symbol} -> {anchor_symbol}"
    balances = _json_loads(pool.get("balances_json") or pool.get("balances") or {})
    if not isinstance(balances, dict):
        balances = {}
    depth_band = _json_loads(pool.get("depth_band_json") or {})
    if not isinstance(depth_band, dict):
        depth_band = {}
    fee = pool.get("fee")
    fee_label = _text(pool.get("fee_label") or pool.get("feeLabel") or fee or "")
    dynamic_fee = _bool(pool.get("dynamic_fee") or pool.get("dynamicFee"))
    special_fee = _bool(pool.get("special_fee") or pool.get("specialFee"))
    first_recommended = _bool(pool.get("is_first_hop_recommended") or pool.get("isFirstHopRecommended"))
    third_recommended = _bool(pool.get("is_third_hop_recommended") or pool.get("isThirdHopRecommended"))
    optimal_roles = _json_loads(pool.get("optimal_roles_json") or {})
    if isinstance(optimal_roles, dict) and not optimal_roles:
        optimal_roles = {
            "yc": _bool(pool.get("yc_optimal_pool_id") or pool.get("ycOptimalPoolId")) or first_recommended,
            "zc": _bool(pool.get("zc_optimal_pool_id") or pool.get("zcOptimalPoolId")) or third_recommended,
        }
    pool_id = _pool_address(pool)
    fixed_fee_candidate = not dynamic_fee and not special_fee
    no_hook = _lower(pool.get("hooks")) in {"", "0x0000000000000000000000000000000000000000"}
    stored_usable = pool.get("usable_for_observation")
    if stored_usable is not None:
        usable_for_observation = _bool(stored_usable)
    else:
        try:
            fee_int = int(fee or 0)
        except (TypeError, ValueError):
            fee_int = 0
        usable_for_observation = bool(
            fixed_fee_candidate
            and no_hook
            and 0 < fee_int <= MAX_USABLE_CANDIDATE_FIXED_FEE
        )
    quality = "pass" if usable_for_observation else "rejected"
    return {
        "pool": pool_id,
        "poolId": pool_id,
        "poolIdentityMode": _text(
            pool.get("pool_identity_mode")
            or pool.get("poolIdentityMode")
            or "v4_pool_id"
        ),
        "poolKey": _json_loads(pool.get("pool_key_json") or {}),
        "poolManager": pool.get("pool_manager") or "",
        "protocol": "uniswap_v4",
        "dex": "uniswap_v4",
        "fee": fee,
        "feeLabel": fee_label,
        "dynamicFee": dynamic_fee,
        "specialFee": special_fee,
        "tickSpacing": pool.get("tick_spacing") or pool.get("tickSpacing"),
        "hooks": pool.get("hooks"),
        "token0": pool.get("token0"),
        "token1": pool.get("token1"),
        "token0Symbol": token0_symbol,
        "token1Symbol": token1_symbol,
        "symbolPair": f"{token0_symbol}:{token1_symbol}" if token0_symbol and token1_symbol else "",
        "counterSymbol": counter_symbol,
        "counterAddress": counter_address,
        "routeText": route_text,
        "liquidity": pool.get("liquidity") or "",
        "liquidityIndex": pool.get("liquidity_index") or pool.get("liquidityIndex"),
        "hasLiquidity": pool.get("has_liquidity"),
        "estimatedTvlUsd": pool.get("estimated_tvl_usd") or pool.get("estimatedTvlUsd"),
        "balances": balances,
        "depthStatus": pool.get("depth_status") or "",
        "depthBand": depth_band,
        "usableForObservation": usable_for_observation,
        "quality": quality,
        "status": "observable" if quality == "pass" else "observed",
        "hopRecommendation": {"first": first_recommended, "third": third_recommended},
        "optimalRole": optimal_roles,
        "isFirstHopRecommended": first_recommended,
        "isThirdHopRecommended": third_recommended,
        "liquidityReadAt": pool.get("liquidity_read_at") or pool.get("liquidityReadAt"),
        "liquidityBlockNumber": pool.get("liquidity_block_number") or pool.get("liquidityBlockNumber"),
    }


def _candidate_group(
    anchor: dict[str, Any],
    pairs: list[dict[str, Any]],
    pools: list[dict[str, Any]],
    *,
    token_symbols: dict[str, str],
) -> dict[str, Any]:
    anchor_symbol = _text(anchor.get("anchor_symbol") or anchor.get("anchorSymbol"))
    anchor_address = _text(anchor.get("anchor_address") or anchor.get("anchorAddress"))
    rows = [
        _candidate_pool_row(
            pool,
            token_symbols=token_symbols,
            anchor_address=anchor_address,
            anchor_symbol=anchor_symbol,
        )
        for pool in pools
    ]
    rows.sort(key=lambda row: (-_number(row.get("liquidityIndex")), _int(row.get("fee")), _text(row.get("pool"))))
    tokens_by_address: dict[str, dict[str, Any]] = {}
    for row in rows:
        counter_address = _lower(row.get("counterAddress"))
        if not counter_address:
            continue
        counter_symbol = _text(row.get("counterSymbol")) or _symbol_for_address(counter_address, token_symbols)
        token = tokens_by_address.setdefault(
            counter_address,
            {
                "symbol": counter_symbol,
                "address": counter_address,
                "poolCount": 0,
                "pools": [],
                "ycOptimalPoolId": "",
                "zcOptimalPoolId": "",
            },
        )
        token["poolCount"] += 1
        token["pools"].append(row)
        if row.get("isFirstHopRecommended") and not token["ycOptimalPoolId"]:
            token["ycOptimalPoolId"] = row.get("poolId") or ""
        if row.get("isThirdHopRecommended") and not token["zcOptimalPoolId"]:
            token["zcOptimalPoolId"] = row.get("poolId") or ""
    shared_tokens = sorted(tokens_by_address.values(), key=lambda item: _lower(item.get("address")))
    shared_pool_count = len(rows)
    shared_token_count = len(shared_tokens)
    pair_pool_count = sum(_int(pair.get("pool_count") or pair.get("poolCount")) for pair in pairs)
    return {
        "anchorSymbol": anchor_symbol,
        "anchorAddresses": [anchor_address] if anchor_address else [],
        "candidateSetMode": "bidirectional_same_pool_key_set",
        "ycZcSameSet": True,
        "ycZcSharedPoolSet": True,
        "sharedTokens": shared_tokens,
        "sharedPools": rows,
        "sharedPoolCount": str(shared_pool_count),
        "sharedTokenCount": str(shared_token_count),
        "ycPools": [],
        "zcPools": [],
        "ycTokens": [],
        "zcTokens": [],
        "ycPoolsRef": "sharedPools",
        "zcPoolsRef": "sharedPools",
        "ycTokensRef": "sharedTokens",
        "zcTokensRef": "sharedTokens",
        "ycPoolCount": str(shared_pool_count),
        "zcPoolCount": str(shared_pool_count),
        "ycTokenCount": str(shared_token_count),
        "zcTokenCount": str(shared_token_count),
        "candidatePoolCount": str(pair_pool_count or shared_pool_count),
        "candidateQueueCount": str(pair_pool_count or shared_pool_count),
        "directionCount": str(len(pairs)),
    }


def _abnormal_candidate_group(
    anchor: dict[str, Any],
    abnormal_pools: list[dict[str, Any]],
    *,
    token_symbols: dict[str, str],
) -> dict[str, Any]:
    anchor_symbol = _text(anchor.get("anchor_symbol") or anchor.get("anchorSymbol"))
    anchor_address = _text(anchor.get("anchor_address") or anchor.get("anchorAddress"))
    rows = [
        _candidate_pool_row(
            pool,
            token_symbols=token_symbols,
            anchor_address=anchor_address,
            anchor_symbol=anchor_symbol,
        )
        for pool in abnormal_pools
    ]
    rows.sort(key=lambda row: (_text(row.get("pool")), _int(row.get("fee"))))
    tokens_by_address: dict[str, dict[str, Any]] = {}
    for row in rows:
        counter_address = _lower(row.get("counterAddress"))
        if not counter_address:
            continue
        counter_symbol = _text(row.get("counterSymbol")) or _symbol_for_address(counter_address, token_symbols)
        token = tokens_by_address.setdefault(
            counter_address,
            {
                "symbol": counter_symbol,
                "address": counter_address,
                "poolCount": 0,
                "pools": [],
                "ycOptimalPoolId": "",
                "zcOptimalPoolId": "",
            },
        )
        token["poolCount"] += 1
        token["pools"].append(row)
    shared_tokens = sorted(tokens_by_address.values(), key=lambda item: _lower(item.get("address")))
    return {
        "anchorSymbol": anchor_symbol,
        "anchorAddresses": [anchor_address] if anchor_address else [],
        "abnormalTokenCount": str(len(shared_tokens)),
        "abnormalPoolCount": str(len(rows)),
        "abnormalTokens": shared_tokens,
        "abnormalPools": rows,
    }


def _candidate_pair_report(
    pair: dict[str, Any],
    pools: list[dict[str, Any]],
    *,
    token_symbols: dict[str, str],
    anchor_symbol: str,
    anchor_address: str,
) -> dict[str, Any]:
    counter_address = _text(pair.get("counter_address") or pair.get("counterAddress"))
    anchor_key = _lower(anchor_address)
    counter_key = _lower(counter_address)
    pair_pools = [
        pool
        for pool in pools
        if counter_key
        and (
            (
                _lower(pool.get("token0")) == anchor_key
                and _lower(pool.get("token1")) == counter_key
            )
            or (
                _lower(pool.get("token0")) == counter_key
                and _lower(pool.get("token1")) == anchor_key
            )
        )
    ]
    counter_symbol = _symbol_for_address(counter_address, token_symbols)
    preferred = [
        _candidate_pool_row(
            pool,
            token_symbols=token_symbols,
            anchor_address=anchor_address,
            anchor_symbol=anchor_symbol,
        )
        for pool in pair_pools
        if not _bool(pool.get("dynamic_fee") or pool.get("dynamicFee")) and not _bool(pool.get("special_fee") or pool.get("specialFee"))
    ]
    preferred.sort(key=lambda row: (-_number(row.get("liquidityIndex")), _int(row.get("fee"))))
    strict = list(preferred)
    return {
        "symbolPair": f"{anchor_symbol}:{counter_symbol}" if anchor_symbol and counter_symbol else "",
        "addressPair": _text(pair.get("address_pair") or pair.get("addressPair")),
        "tokenX": anchor_key,
        "tokenY": counter_address,
        "tokenXSymbol": anchor_symbol,
        "tokenYSymbol": counter_symbol,
        "classification": {"category": "stable" if anchor_symbol in STABLE_SYMBOLS and counter_symbol in STABLE_SYMBOLS else "token", "label": f"{anchor_symbol}:{counter_symbol}"},
        "allPoolCount": str(len(pair_pools)),
        "strictQualityPoolCount": str(len(strict)),
        "preferredPoolCount": str(len(preferred)),
        "executionPoolCount": str(min(5, len(preferred))),
        "rejectedPoolCount": str(max(0, len(pair_pools) - len(preferred))),
        "hasStrictQualityPools": bool(strict),
        "hasPreferredPools": bool(preferred),
        "strictQualityPools": strict,
        "preferredPools": preferred,
        "executionPools": preferred[:5],
        "rejectedPools": [],
        "rejectReasonCounts": {},
    }


STABLE_SYMBOLS = {"ETH", "WETH", "USDG", "USDE", "USDT", "USDC", "TZ", "DAI"}


def build_candidate_payload(
    *,
    chain_id: int = ROBINHOOD_CHAIN_ID,
    protocols: list[str] | None = None,
    database_url: str | None = None,
) -> dict[str, Any]:
    db = str(absolute_database_path(database_url))
    token_symbols = _token_symbol_map(db, chain_id=chain_id)
    anchors = _anchor_rows(db, chain_id=chain_id)
    groups: list[dict[str, Any]] = []
    pair_reports: list[dict[str, Any]] = []
    all_tokens: list[dict[str, Any]] = []
    total_pairs = 0
    total_pools = 0
    for anchor in anchors:
        symbol = _text(anchor.get("anchor_symbol") or anchor.get("anchorSymbol"))
        if not symbol:
            continue
        payload = load_dex_candidate_pools(
            _network_for_chain_id(chain_id),
            chain_id,
            ROBINHOOD_PROTOCOL,
            symbol,
            database_url=db,
        )
        tokens = payload.get("anchorTokens") or []
        pairs = payload.get("pairs") or []
        pools = payload.get("pools") or []
        abnormal_pools = payload.get("abnormalPools") or []
        abnormal_pairs = payload.get("abnormalPairs") or []
        all_tokens.extend(tokens)
        total_pairs += len(pairs)
        total_pools += len(pools)
        group = _candidate_group(anchor, pairs, pools, token_symbols=token_symbols)
        abnormal_group = _abnormal_candidate_group(anchor, abnormal_pools, token_symbols=token_symbols)
        group.update(abnormal_group)
        group["abnormalDirectionCount"] = str(len(abnormal_pairs))
        groups.append(group)
        pair_reports.extend(
            _candidate_pair_report(
                pair,
                pools,
                token_symbols=token_symbols,
                anchor_symbol=symbol,
                anchor_address=_text(anchor.get("anchor_address") or anchor.get("anchorAddress")),
            )
            for pair in pairs
        )
    groups.sort(key=lambda group: _text(group.get("anchorSymbol")))
    yc_token_count = sum(_int(group.get("ycTokenCount")) for group in groups)
    yc_pool_count = sum(_int(group.get("ycPoolCount")) for group in groups)
    shared_group_count = sum(1 for group in groups if group.get("ycZcSameSet"))
    liquid_pool_count = 0
    liquid_group_count = 0
    abnormal_pool_count = 0
    abnormal_token_count = 0
    for group in groups:
        if _group_has_liquidity(group):
            liquid_group_count += 1
        liquid_pool_count += sum(
            1
            for row in group.get("sharedPools") or []
            if not row.get("dynamicFee") and not row.get("specialFee") and _number(row.get("liquidityIndex")) > 0
        )
        abnormal_pool_count += _int(group.get("abnormalPoolCount"))
        abnormal_token_count += _int(group.get("abnormalTokenCount"))
    generated_at = _text(max((_text(row.get("updated_at")) for row in all_tokens), default=""))
    summary = {
        "pairTableCount": str(total_pairs),
        "anchorGroupCount": str(len(groups)),
        "ycPoolCount": str(yc_pool_count),
        "zcPoolCount": str(yc_pool_count),
        "ycTokenCount": str(yc_token_count),
        "zcTokenCount": str(yc_token_count),
        "pairsWithStrictQualityPools": str(sum(1 for group in groups if _int(group.get("candidatePoolCount")) > 0)),
        "pairsWithPreferredPools": str(sum(1 for group in groups if _int(group.get("candidatePoolCount")) > 0)),
        "staticPoolCount": str(total_pools),
        "staticPairCount": str(total_pairs),
        "preferredPoolCount": str(total_pools),
        "executionPoolCount": str(total_pools),
        "strictQualityPoolCount": str(total_pools),
        "ycZcSameSetGroupCount": str(shared_group_count),
        "sharedPoolSetCount": str(sum(_int(group.get("sharedPoolCount")) for group in groups)),
        "candidateSetStorageMode": "shared_yc_zc_pool_set" if shared_group_count else "directional_pool_sets",
        "liquidPoolCount": str(liquid_pool_count),
        "liquidPoolGroupCount": str(liquid_group_count),
        "abnormalStaticPoolCount": str(abnormal_pool_count),
        "abnormalStaticTokenCount": str(abnormal_token_count),
        "tokenCount": len({_lower(row.get("token_address") or row.get("tokenAddress")) for row in all_tokens}),
        "databaseSource": "sqlite_db",
    }
    return {
        "mode": "dex_pool_candidates",
        "chain_id": chain_id,
        "selected_protocols": [ROBINHOOD_PROTOCOL] if not protocols else [str(item).lower() for item in protocols],
        "tableSource": "ob_table",
        "referencedTable": "ch_table",
        "source": "rust_sqlite",
        "fallbackReason": None,
        "databasePath": db,
        "poolStaticTable": {
            "path": db,
            "exists": True,
            "mode": "sqlite_db",
            "network": _network_for_chain_id(chain_id),
            "chainId": chain_id,
            "generatedAt": generated_at,
            "generatedBlockNumber": None,
            "sourceDiscoveryBlockNumber": None,
            "sourceDiscoveryFetchedAt": None,
            "factory": None,
            "router": None,
            "quoter": None,
        },
        "filters": {"source": "sqlite_db", "status": "fixed-fee observable DEX pools with liquidity when available"},
        "summary": summary,
        "pairs": pair_reports,
        "anchorGroups": groups,
    }


def _group_has_liquidity(group: dict[str, Any]) -> bool:
    return any(
        not row.get("dynamicFee") and not row.get("specialFee") and _number(row.get("liquidityIndex")) > 0
        for row in group.get("sharedPools") or []
    )
