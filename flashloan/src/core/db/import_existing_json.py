"""Import existing Robinhood V4 JSON evidence into the business SQLite tables.

This is the offline bootstrap path: it reads the current candidate-pool
shards, market-observation intermediate pool files, V4 pool index, V4
liquidity snapshot and configured tokens, then writes the independent
business tables through the Python storage layer. Rust only reads these rows
later.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from core.db.storage_business_io import query
from core.db.storage_chain_core import (
    seed_chain_info,
    seed_chain_markets_from_builtin,
    seed_chain_tokens,
    upsert_initialization,
)
from core.db.storage_dex_candidate import replace_dex_candidate_tables
from core.db.storage_market_observation import (
    delete_market_observation_anchor,
    replace_market_observation_tables,
)
from core.db.storage_v4_closed_loop import replace_v4_closed_loop_tables
from core.db.storage_v4_exchange import (
    upsert_v4_exchange_pools,
    upsert_v4_liquidity_snapshots,
)


ROBINHOOD_NETWORK = "robinhood"
ROBINHOOD_CHAIN_ID = 4663
ROBINHOOD_MARKET_ID = "dex-arbitrage-4663-uniswap-v4"
ANCHOR_SYMBOLS = ("ETH", "USDG", "USDE", "TZ", "WETH")


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _text(value: Any) -> str:
    return str(value or "").strip().lower()


def _symbol(value: Any) -> str:
    return str(value or "").strip().upper()


def _number(value: Any, default: float | None = None) -> float | None:
    if value in (None, ""):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _pool_id(pool: dict[str, Any]) -> str:
    return _text(pool.get("pool") or pool.get("poolId") or pool.get("pool_id") or pool.get("id"))


def _pool_token(pool: dict[str, Any], key: str) -> str:
    return _text(pool.get(key) or pool.get(f"token{key[-1]}") or "")


def _token_rows_from_v4_index(index: dict[str, Any]) -> list[dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for raw in index.get("poolKeys") or index.get("pools") or []:
        if not isinstance(raw, dict):
            continue
        pool = raw.get("poolKey") if isinstance(raw.get("poolKey"), dict) else raw
        for side in (0, 1):
            address = _text(pool.get(f"token{side}") or pool.get(f"currency{side}"))
            symbol = _symbol(pool.get(f"token{side}Symbol"))
            decimals = pool.get(f"token{side}Decimals")
            if not address or not symbol:
                continue
            key = address
            existing = rows.get(key)
            if existing:
                continue
            rows[key] = {
                "token_address": address,
                "token_symbol": symbol,
                "token_decimals": decimals,
                "is_native": 1 if address == "0x0000000000000000000000000000000000000000" else 0,
                "is_wrapped_native": 1 if symbol == "WETH" else 0,
                "is_stable": 1 if symbol in {"USDG", "USDE", "TZ"} else 0,
                "anchor_symbol": symbol if symbol in ANCHOR_SYMBOLS else None,
                "token_kind": "native" if address == "0x0000000000000000000000000000000000000000" else ("wrapped_native" if symbol == "WETH" else "erc20"),
            }
    return list(rows.values())


def _liquidity_rows(v4_liquidity: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for raw in v4_liquidity.get("pools") or []:
        if not isinstance(raw, dict):
            continue
        rows.append(
            {
                "pool_id": _pool_id(raw),
                "has_liquidity": raw.get("hasLiquidity"),
                "liquidity": raw.get("liquidity") or "",
                "liquidity_index": _number(raw.get("liquidityIndex")),
                "estimated_tvl_usd": _number(
                    raw.get("estimatedTvlUsd")
                    or (raw.get("balances") or {}).get("tvlUsd")
                    if isinstance(raw.get("balances"), dict)
                    else raw.get("estimatedTvlUsd")
                ),
                "balances": raw.get("balances") or {},
                "depth_status": raw.get("depthStatus") or "",
                "depth_band": raw.get("depthBand") or {},
                "sqrt_price_x96": raw.get("sqrtPriceX96") or "",
                "tick": raw.get("tick"),
                "liquidity_read_at": raw.get("liquidityReadAt"),
                "liquidity_block_number": raw.get("liquidityBlockNumber"),
                "recent_swap_active": raw.get("recentSwapActive") or False,
                "observed_at": v4_liquidity.get("generatedAt") or v4_liquidity.get("liquidityScanAt"),
            }
        )
    return rows


def _v4_pool_rows_from_index(index: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for raw in index.get("pools") or []:
        if not isinstance(raw, dict):
            continue
        key = raw.get("poolKey") if isinstance(raw.get("poolKey"), dict) else raw
        rows.append(
            {
                "pool_id": _pool_id(raw),
                "pool_manager": raw.get("poolManager") or "",
                "pool_key": key or {},
                "token0": _text(key.get("token0") or key.get("currency0") or raw.get("token0")),
                "token1": _text(key.get("token1") or key.get("currency1") or raw.get("token1")),
                "fee": key.get("fee") if key.get("fee") is not None else raw.get("fee"),
                "tick_spacing": key.get("tickSpacing") if key.get("tickSpacing") is not None else raw.get("tickSpacing"),
                "hooks": key.get("hooks") or raw.get("hooks") or "",
                "sqrt_price_x96": key.get("sqrtPriceX96") or raw.get("sqrtPriceX96") or "",
                "tick": key.get("tick") if key.get("tick") is not None else raw.get("tick"),
            }
        )
    return rows


def _direction_rows(directions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for direction in directions:
        if not isinstance(direction, dict):
            continue
        all_pools = direction.get("allPools")
        if not isinstance(all_pools, list):
            all_pools = direction.get("executionPools") or []
        rows.append(
            {
                "direction_key": direction.get("directionKey") or direction.get("direction_key") or "",
                "token_x_address": direction.get("tokenX") or direction.get("token_x_address") or "",
                "token_y_address": direction.get("tokenY") or direction.get("token_y_address") or "",
                "address_pair": direction.get("addressPair") or direction.get("address_pair") or "",
                "allPools": [pool for pool in all_pools if isinstance(pool, dict)],
                "observation_empty_reason": direction.get("observationEmptyReason") or direction.get("observation_empty_reason"),
            }
        )
    return rows


def _pool_rows_from_directions(directions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: dict[str, dict[str, Any]] = {}
    for direction in directions:
        if not isinstance(direction, dict):
            continue
        for pool in direction.get("allPools") or direction.get("executionPools") or []:
            if not isinstance(pool, dict):
                continue
            pool_id = _pool_id(pool)
            if not pool_id:
                continue
            seen.setdefault(
                pool_id,
                {
                    "pool_id": pool_id,
                    "pool_identity_mode": pool.get("poolIdentityMode") or "v4_pool_id",
                    "pool_manager": pool.get("poolManager") or "",
                    "pool_key": pool.get("poolKey") or {},
                    "token0": _text(pool.get("token0")),
                    "token1": _text(pool.get("token1")),
                    "fee": pool.get("fee"),
                    "tick_spacing": pool.get("tickSpacing"),
                    "hooks": pool.get("hooks") or "",
                    "address_pair": pool.get("addressPair") or "",
                },
            )
    return list(seen.values())


def _liquidity_rows_from_pools(pool_rows: list[dict[str, Any]], anchor_symbol: str) -> list[dict[str, Any]]:
    rows = []
    for pool in pool_rows:
        rows.append(
            {
                "pool_id": _pool_id(pool),
                "has_liquidity": pool.get("hasLiquidity"),
                "liquidity": pool.get("liquidity") or "",
                "liquidity_index": _number(pool.get("liquidityIndex")),
                "estimated_tvl_usd": _number(pool.get("estimatedTvlUsd")),
                "balances": pool.get("balances") or {},
                "depth_status": pool.get("depthStatus") or "",
                "depth_band": pool.get("depthBand") or {},
                "sqrt_price_x96": pool.get("sqrtPriceX96") or "",
                "tick": pool.get("tick"),
                "liquidity_read_at": pool.get("liquidityReadAt"),
                "liquidity_block_number": pool.get("liquidityBlockNumber"),
                "recent_swap_active": pool.get("recentSwapActive") or False,
            }
        )
    return rows


def _candidate_rows_from_shard(shard: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    group = shard.get("anchorGroup") if isinstance(shard.get("anchorGroup"), dict) else {}
    anchor_symbol = _symbol(shard.get("anchorSymbol") or group.get("anchorSymbol"))
    tokens = []
    for row in group.get("sharedTokens") or []:
        if not isinstance(row, dict):
            continue
        tokens.append(
            {
                "token_address": row.get("address") or row.get("token_address") or "",
                "token_symbol": row.get("symbol") or row.get("token_symbol") or "",
                "pool_count": row.get("poolCount") or row.get("pool_count") or 0,
            }
        )
    pools = []
    seen_pool_ids: set[str] = set()
    for bucket in ("ycPools", "zcPools", "sharedPools"):
        for row in group.get(bucket) or []:
            if not isinstance(row, dict):
                continue
            pool_id = _pool_id(row)
            if not pool_id or pool_id in seen_pool_ids:
                continue
            seen_pool_ids.add(pool_id)
            pools.append(
                {
                    "pool_id": pool_id,
                    "pool_key": row.get("poolKey") or {},
                    "pool_manager": row.get("poolManager") or "",
                    "token0": _text(row.get("token0") or row.get("currency0")),
                    "token1": _text(row.get("token1") or row.get("currency1")),
                    "fee": row.get("fee"),
                    "tick_spacing": row.get("tickSpacing"),
                    "hooks": row.get("hooks") or "",
                    "address_pair": row.get("addressPair") or row.get("address_pair") or "",
                    "sqrt_price_x96": row.get("sqrtPriceX96") or "",
                    "tick": row.get("tick"),
                    "has_liquidity": row.get("hasLiquidity"),
                    "liquidity": row.get("liquidity") or "",
                    "liquidity_index": _number(row.get("liquidityIndex")),
                    "estimated_tvl_usd": _number(row.get("estimatedTvlUsd")),
                    "balances": row.get("balances") or {},
                    "depth_status": row.get("depthStatus") or "",
                    "depth_band": row.get("depthBand") or {},
                    "usable_for_observation": row.get("usableForObservation") or False,
                    "is_first_hop_recommended": row.get("isFirstHopRecommended") or False,
                    "is_third_hop_recommended": row.get("isThirdHopRecommended") or False,
                    "liquidity_read_at": row.get("liquidityReadAt"),
                    "liquidity_block_number": row.get("liquidityBlockNumber"),
                }
            )
    pairs = []
    counter_seen: set[str] = set()
    for pool in pools:
        token0 = pool.get("token0")
        token1 = pool.get("token1")
        if not token0 or not token1:
            continue
        # The candidate shard stores anchor+counter pools; the pool itself is
        # enough to derive the pair in the import fallback.
        for address in (token0, token1):
            if address in {pool.get("anchor_address", ""), _anchor_address_from_symbol(anchor_symbol)}:
                continue
            counter = address
        counter = pool.get("counterAddress") or ""
        if not counter:
            # Fall back to the non-anchor token in the pool.
            anchor = _anchor_address_from_symbol(anchor_symbol)
            if token0 == anchor:
                counter = token1
            elif token1 == anchor:
                counter = token0
        if not counter or counter in counter_seen:
            continue
        counter_seen.add(counter)
        pairs.append(
            {
                "anchor_address": _anchor_address_from_symbol(anchor_symbol),
                "counter_address": counter,
                "address_pair": f"{token0}:{token1}" if token0 <= token1 else f"{token1}:{token0}",
                "pool_count": 1,
                "fixed_fee_liquid_pool_count": 1 if pool.get("usable_for_observation") else 0,
                "priced_pool_count": 1 if pool.get("liquidity_index") else 0,
            }
        )
    return tokens, pairs, pools, []


def _anchor_address_from_symbol(symbol: str) -> str:
    from dex.intent_trade.chains.robinhood.market_observation import _mainnet_configured_anchor_addresses
    from dex.intent_trade.chains.robinhood.config import WETH_ADDRESS
    from dex.intent_trade.chains.robinhood.v4_pool_index import ZERO_ADDRESS

    addresses = _mainnet_configured_anchor_addresses()
    # The configured set is returned as lowercase addresses; symbol resolution
    # is only a fallback in this importer and the config module owns the truth.
    if symbol == "ETH":
        return ZERO_ADDRESS.lower()
    if symbol == "WETH":
        return WETH_ADDRESS.lower()
    return str(next(iter(sorted(addresses)), "")).lower() if addresses else ""


def _build_loops(
    *,
    anchor_symbol: str,
    anchor_address: str,
    candidates: dict[str, Any],
    observation: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Build a small but complete loop set from candidate and observation rows."""
    token_rows = candidates.get("tokens") or []
    token_addresses = [
        _text(row.get("token_address") or row.get("address"))
        for row in token_rows
        if _text(row.get("token_address") or row.get("address"))
    ]
    token_addresses = sorted(set(address for address in token_addresses if address != anchor_address))
    pools_by_id: dict[str, dict[str, Any]] = {
        _pool_id(row): row for row in (observation.get("pools") or [])
    }
    loops: list[dict[str, Any]] = []
    hops: list[dict[str, Any]] = []
    for y_address in token_addresses:
        for z_address in token_addresses:
            if y_address == z_address:
                continue
            path = [anchor_address, y_address, z_address, anchor_address]
            loop_key = f"{anchor_address}:{y_address}:{z_address}"
            loops.append(
                {
                    "loop_key": loop_key,
                    "anchor_address": anchor_address,
                    "anchor_symbol": anchor_symbol,
                    "path_text": " -> ".join(path),
                    "path_addresses": path,
                    "path_symbols": [anchor_symbol, y_address, z_address, anchor_symbol],
                    "pool_path_key": ":".join(path),
                    "pool_ids": [],
                    "hop_candidate_counts": {},
                    "hop_candidate_combination_count": 0,
                    "recommendation_basis": "imported_existing_json",
                    "recommended_first_priority": 0,
                    "status": "active",
                    "source": "existing_json_import",
                }
            )
            for hop_index, (input_address, output_address) in enumerate(
                [(anchor_address, y_address), (y_address, z_address), (z_address, anchor_address)],
                start=1,
            ):
                matched = _find_pool_for_hop(pools_by_id, input_address, output_address)
                pool_id = matched or "0x0000000000000000000000000000000000000000000000000000000000000000"
                hops.append(
                    {
                        "loop_key": loop_key,
                        "hop_index": hop_index,
                        "hop_name": "yc" if hop_index == 1 else ("y-z" if hop_index == 2 else "zc"),
                        "input_address": input_address,
                        "output_address": output_address,
                        "pool_id": pool_id,
                        "base_pool_id": pool_id,
                        "pool_selection_basis": "imported_existing_json" if matched else "no_pool",
                        "temporary_override": 0,
                    }
                )
    return loops, hops


def _find_pool_for_hop(
    pools_by_id: dict[str, dict[str, Any]],
    input_address: str,
    output_address: str,
) -> str:
    edge = tuple(sorted((_text(input_address), _text(output_address))))
    for pool in pools_by_id.values():
        token0 = _text(pool.get("token0"))
        token1 = _text(pool.get("token1"))
        if tuple(sorted((token0, token1))) == edge:
            return _pool_id(pool)
    return ""


def import_existing_json(
    database_url: str,
    *,
    runtime_root: Path | str | None = None,
) -> dict[str, Any]:
    root = Path(runtime_root) if runtime_root is not None else Path(__file__).resolve().parents[2] / "runtime"
    v4_index_path = root / "evidence" / "robinhood" / "v4_pool_index" / "latest.json"
    v4_liquidity_path = root / "evidence" / "robinhood" / "v4_pool_liquidity" / "latest.json"
    candidate_root = root / "cache" / "candidate_pools" / "robinhood" / "chain-4663" / "shards"
    observation_root = root / "market_observation" / "robinhood" / "4663" / "uniswap_v4" / "intermediate_pool_files"

    index = _read_json(v4_index_path)
    liquidity = _read_json(v4_liquidity_path)
    token_rows = _token_rows_from_v4_index(index)
    seed_chain_tokens(
        database_url,
        network=ROBINHOOD_NETWORK,
        chain_id=ROBINHOOD_CHAIN_ID,
        token_rows=token_rows,
    )
    seed_chain_markets_from_builtin(
        database_url,
        network=ROBINHOOD_NETWORK,
        chain_id=ROBINHOOD_CHAIN_ID,
    )
    v4_pool_count = upsert_v4_exchange_pools(
        database_url,
        network=ROBINHOOD_NETWORK,
        chain_id=ROBINHOOD_CHAIN_ID,
        protocol="uniswap_v4",
        pool_rows=_v4_pool_rows_from_index(index),
    )
    upsert_v4_liquidity_snapshots(
        database_url,
        network=ROBINHOOD_NETWORK,
        chain_id=ROBINHOOD_CHAIN_ID,
        protocol="uniswap_v4",
        rows=_liquidity_rows(liquidity),
    )

    anchor_reports = []
    loop_reports = []
    total_candidate_pools = 0
    total_observation_pairs = 0
    for anchor_symbol in ANCHOR_SYMBOLS:
        shard_path = candidate_root / f"{anchor_symbol.lower()}.json"
        observation_path = observation_root / f"{anchor_symbol.upper()}.json"
        shard = _read_json(shard_path)
        observation = _read_json(observation_path)
        anchor_address = _anchor_address_from_symbol(anchor_symbol)
        tokens, pairs, pools, recommendations = _candidate_rows_from_shard(shard)
        candidate_counts = replace_dex_candidate_tables(
            database_url,
            network=ROBINHOOD_NETWORK,
            chain_id=ROBINHOOD_CHAIN_ID,
            protocol="uniswap_v4",
            anchor_scope=ROBINHOOD_MARKET_ID,
            anchor_symbol=anchor_symbol,
            tokens=tokens,
            pairs=pairs,
            pools=pools,
            recommendations=recommendations,
        )
        total_candidate_pools += candidate_counts["pools"]
        direction_rows = _direction_rows(observation.get("directions") or [])
        pool_rows = _pool_rows_from_directions(observation.get("directions") or [])
        delete_market_observation_anchor(
            database_url,
            network=ROBINHOOD_NETWORK,
            chain_id=ROBINHOOD_CHAIN_ID,
            protocol="uniswap_v4",
            anchor_symbol=anchor_symbol,
        )
        observation_counts = replace_market_observation_tables(
            database_url,
            network=ROBINHOOD_NETWORK,
            chain_id=ROBINHOOD_CHAIN_ID,
            protocol="uniswap_v4",
            anchor_scope=ROBINHOOD_MARKET_ID,
            anchor_symbol=anchor_symbol,
            directions=direction_rows,
            pools=pool_rows,
            liquidity=_liquidity_rows_from_pools(pool_rows, anchor_symbol),
        )
        total_observation_pairs += observation_counts["pairs"]
        anchor_reports.append(
            {
                "anchorSymbol": anchor_symbol,
                "candidateTokens": len(tokens),
                "candidatePairs": len(pairs),
                "candidatePools": len(pools),
                "observationDirections": len(direction_rows),
                "observationPools": len(pool_rows),
            }
        )

        loops, hops = _build_loops(
            anchor_symbol=anchor_symbol,
            anchor_address=anchor_address,
            candidates={"tokens": tokens},
            observation={"pools": pool_rows},
        )
        loop_counts = replace_v4_closed_loop_tables(
            database_url,
            network=ROBINHOOD_NETWORK,
            chain_id=ROBINHOOD_CHAIN_ID,
            protocol="uniswap_v4",
            market_id=ROBINHOOD_MARKET_ID,
            loops=loops,
            hops=hops,
        )
        loop_reports.append(
            {
                "anchorSymbol": anchor_symbol,
                "loops": loop_counts["loops"],
                "hops": loop_counts["hops"],
            }
        )

    outputs = query(
        database_url,
        "SELECT anchor_symbol, COUNT(*) AS direction_count FROM market_observation_yz_pairs GROUP BY anchor_symbol",
    )
    direction_count_total = sum(int(row.get("direction_count") or 0) for row in outputs)
    upsert_initialization(
        database_url,
        network=ROBINHOOD_NETWORK,
        chain_id=ROBINHOOD_CHAIN_ID,
        protocol="uniswap_v4",
        domain="v4_exchange_pool_index",
        status="complete",
        completeness={
            "initialize_log_count": index.get("initializeLogCount") or 0,
            "pool_count": v4_pool_count,
        },
    )
    return {
        "ok": True,
        "network": ROBINHOOD_NETWORK,
        "chainId": ROBINHOOD_CHAIN_ID,
        "v4PoolCount": v4_pool_count,
        "tokenCount": len(token_rows),
        "anchorReports": anchor_reports,
        "loopReports": loop_reports,
        "directionCountTotal": direction_count_total,
    }


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--database", default="")
    parser.add_argument("--runtime-root", default="")
    args = parser.parse_args()
    database = args.database or str(Path(__file__).resolve().parents[2] / "runtime" / "local_store.sqlite3")
    result = import_existing_json(database, runtime_root=args.runtime_root or None)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
