"""Database-first page payloads for candidate pools and market observation.

The page read path is:

    page -> rs_bridge -> Rust sqlite_read (or Python storage) -> SQLite

This module converts the independent business tables back into the page-facing
shape that the legacy JSON surfaces used. It never writes to SQLite and never
reads page JSON shards.
"""

from __future__ import annotations

import json
from typing import Any, Iterable

from core.db.database_path import absolute_database_path
from core.db.direction_pool_score import select_directional_pool_ids
from core.db.storage_chain_core import load_chain_tokens
from core.db.storage_dex_candidate import MAX_USABLE_CANDIDATE_FIXED_FEE
from dex.intent_trade.chains.robinhood_common.chain_config import (
    all_anchor_addresses_by_symbol,
    all_anchor_symbols,
)
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


def _default_anchor_symbols(
    chain_id: int,
    *,
    database_url: str | None = None,
) -> list[str]:
    network = _network_for_chain_id(chain_id)
    return list(
        all_anchor_symbols(
            network,
            database_url=database_url,
        )
    )


def _anchor_rows(
    db: str,
    *,
    chain_id: int = ROBINHOOD_CHAIN_ID,
    anchors: Iterable[str] | None = None,
) -> list[dict[str, Any]]:
    from core.db.storage_business_io import query

    rows = query(
        str(db),
        """
        SELECT DISTINCT anchor_symbol, anchor_address
        FROM dex_candidate_pool_pairs
        WHERE network = ? AND chain_id = ? AND protocol = ?
        ORDER BY anchor_symbol
        """,
        (_network_for_chain_id(chain_id), chain_id, ROBINHOOD_PROTOCOL),
    )
    allowed = None if anchors is None else {_text(symbol).upper() for symbol in anchors}
    if allowed is None:
        return rows
    addresses_by_symbol = all_anchor_addresses_by_symbol(
        _network_for_chain_id(chain_id),
        database_url=db,
    )
    return [
        row
        for row in rows
        if (
            _text(row.get("anchor_symbol") or row.get("anchorSymbol")).upper() in allowed
            and _lower(row.get("anchor_address") or row.get("anchorAddress"))
            == _lower(addresses_by_symbol.get(_text(row.get("anchor_symbol")).upper()) or "")
        )
    ]


def _pool_address(pool: dict[str, Any]) -> str:
    return _text(pool.get("pool_id") or pool.get("poolId") or pool.get("pool"))


def _active_direction_pool_ids(
    payload: dict[str, Any],
) -> dict[tuple[str, str], list[str]]:
    """Re-select primary and backup pools for every stored recommendation.

    The stored recommendation table is treated as the candidate scope for a
    direction.  Each read applies the current liquidity snapshots again, so a
    primary pool that lost depth is replaced by the next usable backup and a
    backup that also lost depth is refreshed from the stored pool surface.
    """
    pools = payload.get("pools") or []
    selections: dict[tuple[str, str], list[str]] = {}
    pools_by_pair: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for pool in pools:
        token0 = _lower(pool.get("token0"))
        token1 = _lower(pool.get("token1"))
        if not token0 or not token1:
            continue
        pair_key = tuple(sorted((token0, token1)))
        pools_by_pair.setdefault(pair_key, []).append(pool)
    for rec in payload.get("recommendations") or []:
        direction_key = _text(rec.get("direction_key") or rec.get("directionKey"))
        hop = _text(rec.get("hop"))
        if not direction_key or not hop or ":" not in direction_key:
            continue
        token_in, token_out = direction_key.split(":", 1)
        if not token_in or not token_out:
            continue
        key = (direction_key, hop)
        if key in selections:
            continue
        scope_pools = pools_by_pair.get(tuple(sorted((token_in, token_out))), [])
        selections[key] = select_directional_pool_ids(
            scope_pools,
            token_in,
            token_out,
            limit=3,
        )
    return selections


def _pool_role(
    pool_id: str,
    active_ids: list[str],
) -> str:
    if not active_ids:
        return ""
    if active_ids[0] == pool_id:
        return "primary"
    if pool_id in active_ids[1:3]:
        return "backup"
    return ""


def _symbol_for_address(address: str, token_symbols: dict[str, str]) -> str:
    return _text(token_symbols.get(_lower(address)) or _text(address)[:6])


def _candidate_pool_row(
    pool: dict[str, Any],
    *,
    token_symbols: dict[str, str],
    anchor_address: str,
    anchor_symbol: str,
    pool_role: str = "",
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
    if pool_role in {"primary", "backup"}:
        first_recommended = True
        third_recommended = True
        optimal_roles = {
            "yc": pool_role == "primary",
            "zc": pool_role == "primary",
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
        "recommendedPoolRole": pool_role,
        "recommendedPoolRoleLabel": {
            "primary": "推荐池",
            "backup": "备用池",
        }.get(pool_role, ""),
        "liquidityReadAt": pool.get("liquidity_read_at") or pool.get("liquidityReadAt"),
        "liquidityBlockNumber": pool.get("liquidity_block_number") or pool.get("liquidityBlockNumber"),
    }


def _candidate_group(
    anchor: dict[str, Any],
    pairs: list[dict[str, Any]],
    pools: list[dict[str, Any]],
    *,
    token_symbols: dict[str, str],
    active_selection: dict[tuple[str, str], list[str]] | None = None,
) -> dict[str, Any]:
    anchor_symbol = _text(anchor.get("anchor_symbol") or anchor.get("anchorSymbol"))
    anchor_address = _text(anchor.get("anchor_address") or anchor.get("anchorAddress"))
    active_selection = active_selection or {}
    active_pool_ids = {
        _lower(pool_id)
        for pool_ids in active_selection.values()
        for pool_id in pool_ids
    }
    pools = [pool for pool in pools if _lower(_pool_address(pool)) in active_pool_ids]
    rows_by_pool: dict[str, dict[str, Any]] = {}
    for pool in pools:
        token0 = _lower(pool.get("token0"))
        token1 = _lower(pool.get("token1"))
        counter_for_pool = token1 if token0 == _lower(anchor_address) else token0
        pool_id = _lower(_pool_address(pool))
        yc_key = (f"{_lower(anchor_address)}:{counter_for_pool}", "first")
        zc_key = (f"{counter_for_pool}:{_lower(anchor_address)}", "third")
        yc_role = _pool_role(pool_id, list(active_selection.get(yc_key) or []))
        zc_role = _pool_role(pool_id, list(active_selection.get(zc_key) or []))
        pool_role = "primary" if yc_role == "primary" or zc_role == "primary" else "backup" if yc_role == "backup" or zc_role == "backup" else ""
        row = _candidate_pool_row(
            pool,
            token_symbols=token_symbols,
            anchor_address=anchor_address,
            anchor_symbol=anchor_symbol,
            pool_role=pool_role,
        )
        row["ycPoolRole"] = yc_role
        row["zcPoolRole"] = zc_role
        existing = rows_by_pool.get(pool_id)
        if existing is None:
            rows_by_pool[pool_id] = row
            continue
        existing["ycPoolRole"] = yc_role or existing.get("ycPoolRole")
        existing["zcPoolRole"] = zc_role or existing.get("zcPoolRole")
        existing["hopRecommendation"]["first"] = bool(
            existing.get("hopRecommendation", {}).get("first")
            or row.get("hopRecommendation", {}).get("first")
        )
        existing["hopRecommendation"]["third"] = bool(
            existing.get("hopRecommendation", {}).get("third")
            or row.get("hopRecommendation", {}).get("third")
        )
        existing["optimalRole"]["yc"] = bool(
            existing.get("optimalRole", {}).get("yc")
            or row.get("optimalRole", {}).get("yc")
        )
        existing["optimalRole"]["zc"] = bool(
            existing.get("optimalRole", {}).get("zc")
            or row.get("optimalRole", {}).get("zc")
        )
        existing["isFirstHopRecommended"] = bool(
            existing.get("isFirstHopRecommended") or row.get("isFirstHopRecommended")
        )
        existing["isThirdHopRecommended"] = bool(
            existing.get("isThirdHopRecommended") or row.get("isThirdHopRecommended")
        )
    rows = list(rows_by_pool.values())
    rows.sort(key=lambda row: (-_number(row.get("liquidityIndex")), _int(row.get("fee")), _text(row.get("pool"))))
    backup_by_counter: dict[str, dict[str, list[str]]] = {}
    for pair in pairs:
        counter_address = _lower(pair.get("counter_address") or pair.get("counterAddress") or pair.get("counter"))
        if not counter_address:
            continue
        forward_key = f"{_lower(anchor_address)}:{counter_address}"
        reverse_key = f"{counter_address}:{_lower(anchor_address)}"
        forward_active = list(active_selection.get((forward_key, "first")) or [])
        reverse_active = list(active_selection.get((reverse_key, "third")) or [])
        backup_by_counter.setdefault(
            counter_address,
            {
                "yc": forward_active[1:3],
                "zc": reverse_active[1:3],
            },
        )
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
                "ycBackupPoolIds": list(backup_by_counter.get(counter_address, {}).get("yc") or []),
                "zcBackupPoolIds": list(backup_by_counter.get(counter_address, {}).get("zc") or []),
            },
        )
        token["poolCount"] += 1
        token["pools"].append(row)
        if row.get("ycPoolRole") == "primary" and not token["ycOptimalPoolId"]:
            token["ycOptimalPoolId"] = row.get("poolId") or ""
        if row.get("zcPoolRole") == "primary" and not token["zcOptimalPoolId"]:
            token["zcOptimalPoolId"] = row.get("poolId") or ""
    for counter_address, token in tokens_by_address.items():
        forward_key = f"{_lower(anchor_address)}:{counter_address}"
        reverse_key = f"{counter_address}:{_lower(anchor_address)}"
        forward_active = list(active_selection.get((forward_key, "first")) or [])
        reverse_active = list(active_selection.get((reverse_key, "third")) or [])
        if forward_active:
            token["ycOptimalPoolId"] = forward_active[0]
        if reverse_active:
            token["zcOptimalPoolId"] = reverse_active[0]
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
    active_selection: dict[tuple[str, str], list[str]] | None = None,
    pools_by_counter: dict[str, list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    counter_address = _text(pair.get("counter_address") or pair.get("counterAddress"))
    anchor_key = _lower(anchor_address)
    counter_key = _lower(counter_address)
    direction_key = _text(pair.get("direction_key") or pair.get("directionKey"))
    if not direction_key:
        direction_key = f"{anchor_key}:{counter_key}"
    hop = "first" if direction_key == f"{anchor_key}:{counter_key}" else "third"
    active_ids = list((active_selection or {}).get((direction_key, hop)) or [])
    active_ids_lower = {_lower(pool_id) for pool_id in active_ids}
    if pools_by_counter is None:
        pools_by_counter = {}
        for pool in pools:
            token0 = _lower(pool.get("token0"))
            token1 = _lower(pool.get("token1"))
            if token0 == anchor_key and token1:
                pools_by_counter.setdefault(token1, []).append(pool)
            elif token1 == anchor_key and token0:
                pools_by_counter.setdefault(token0, []).append(pool)
    pair_pools = pools_by_counter.get(counter_key, [])
    counter_symbol = _symbol_for_address(counter_address, token_symbols)
    preferred = [
        _candidate_pool_row(
            pool,
            token_symbols=token_symbols,
            anchor_address=anchor_address,
            anchor_symbol=anchor_symbol,
        )
        for pool in pair_pools
        if not _bool(pool.get("dynamic_fee") or pool.get("dynamicFee"))
        and not _bool(pool.get("special_fee") or pool.get("specialFee"))
        and _lower(_pool_address(pool)) in active_ids_lower
    ]
    preferred.sort(
        key=lambda row: (
            _pool_role(_lower(row.get("poolId") or row.get("pool")), active_ids) == "",
            _pool_role(_lower(row.get("poolId") or row.get("pool")), active_ids) != "primary",
            _int(row.get("fee")),
            _text(row.get("pool")),
        )
    )
    strict = list(preferred)
    backup_rows = [
        row
        for row in preferred
        if _pool_role(_lower(row.get("poolId") or row.get("pool")), active_ids) == "backup"
    ]
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
        "recommendedPoolCount": str(sum(1 for row in preferred if _pool_role(_lower(row.get("poolId") or row.get("pool")), active_ids) == "primary")),
        "backupPoolCount": str(len(backup_rows)),
        "hasStrictQualityPools": bool(strict),
        "hasPreferredPools": bool(preferred),
        "hasBackupPools": bool(backup_rows),
        "strictQualityPools": strict,
        "preferredPools": preferred,
        "recommendedPools": [
            row
            for row in preferred
            if _pool_role(_lower(row.get("poolId") or row.get("pool")), active_ids) == "primary"
        ],
        "backupPools": backup_rows,
        "executionPools": preferred[:5],
        "rejectedPools": [],
        "rejectReasonCounts": {},
    }


def _pools_by_counter(
    pools: list[dict[str, Any]],
    anchor_key: str,
) -> dict[str, list[dict[str, Any]]]:
    indexed: dict[str, list[dict[str, Any]]] = {}
    for pool in pools:
        token0 = _lower(pool.get("token0"))
        token1 = _lower(pool.get("token1"))
        if token0 == anchor_key and token1:
            indexed.setdefault(token1, []).append(pool)
        elif token1 == anchor_key and token0:
            indexed.setdefault(token0, []).append(pool)
    return indexed


STABLE_SYMBOLS = {"ETH", "WETH", "USDG", "USDE", "USDT", "USDC", "TZ", "DAI"}


def build_candidate_payload(
    *,
    chain_id: int = ROBINHOOD_CHAIN_ID,
    protocols: list[str] | None = None,
    database_url: str | None = None,
    anchors: Iterable[str] | None = None,
) -> dict[str, Any]:
    db = str(absolute_database_path(database_url))
    token_symbols = _token_symbol_map(db, chain_id=chain_id)
    anchor_rows = _anchor_rows(
        db,
        chain_id=chain_id,
        anchors=anchors if anchors is not None else _default_anchor_symbols(
            chain_id,
            database_url=db,
        ),
    )
    groups: list[dict[str, Any]] = []
    pair_reports: list[dict[str, Any]] = []
    all_tokens: list[dict[str, Any]] = []
    total_pairs = 0
    total_pools = 0
    for anchor in anchor_rows:
        symbol = _text(anchor.get("anchor_symbol") or anchor.get("anchorSymbol"))
        if not symbol:
            continue
        anchor_key = _lower(anchor.get("anchor_address") or anchor.get("anchorAddress"))
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
        pools_by_counter = _pools_by_counter(pools, anchor_key)
        abnormal_pools = payload.get("abnormalPools") or []
        abnormal_pairs = payload.get("abnormalPairs") or []
        active_selection = _active_direction_pool_ids(payload)
        active_pool_ids = {
            _lower(pool_id)
            for pool_ids in active_selection.values()
            for pool_id in pool_ids
        }
        visible_pools = [
            pool
            for pool in pools
            if _lower(_pool_address(pool)) in active_pool_ids
        ]
        all_tokens.extend(tokens)
        total_pairs += len(pairs)
        total_pools += len(visible_pools)
        group = _candidate_group(
            anchor,
            pairs,
            pools,
            token_symbols=token_symbols,
            active_selection=active_selection,
        )
        # No-depth/abnormal pools are no longer a separate page observation
        # item.  They stay in storage for diagnostics, but the page surfaces
        # only primary and backup pools selected per direction.
        group.update(
            {
                "abnormalTokenCount": "0",
                "abnormalPoolCount": "0",
                "abnormalTokens": [],
                "abnormalPools": [],
                "abnormalDirectionCount": "0",
            }
        )
        groups.append(group)
        pair_reports.extend(
            _candidate_pair_report(
                pair,
                pools,
                token_symbols=token_symbols,
                anchor_symbol=symbol,
                anchor_address=_text(anchor.get("anchor_address") or anchor.get("anchorAddress")),
                active_selection=active_selection,
                pools_by_counter=pools_by_counter,
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
