from __future__ import annotations

from typing import Any, Callable

from core.chain_flow_dispatcher import ChainFlowRequest
from intent_trade.chains.common.dex_candidate_tables import build_anchor_candidate_groups
from intent_trade.chains.robinhood_testnet.v4_pool_liquidity import load_robinhood_testnet_v4_pool_liquidity


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


def _pool_candidate_row(pool: dict[str, Any], pool_manager: str) -> dict[str, Any] | None:
    pool_id = str(pool.get("poolId") or pool.get("pool_id") or pool.get("id") or "").strip()
    token0_symbol = _symbol(pool.get("token0Symbol") or pool.get("token0_symbol"))
    token1_symbol = _symbol(pool.get("token1Symbol") or pool.get("token1_symbol"))
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
        "token0": _address(pool.get("token0") or pool.get("currency0")),
        "token1": _address(pool.get("token1") or pool.get("currency1")),
        "token0Symbol": token0_symbol,
        "token1Symbol": token1_symbol,
        "token0Decimals": pool.get("token0Decimals") or pool.get("token0_decimals"),
        "token1Decimals": pool.get("token1Decimals") or pool.get("token1_decimals"),
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


def dex_pool_candidates_payload(
    *,
    request: ChainFlowRequest,
    pool_liquidity_loader: Callable[[], dict[str, Any]] = load_robinhood_testnet_v4_pool_liquidity,
    **_: Any,
) -> dict[str, Any]:
    liquidity = pool_liquidity_loader()
    payload = liquidity.get("payload") if isinstance(liquidity.get("payload"), dict) else {}
    pool_manager = str(liquidity.get("pool_manager") or payload.get("poolManager") or "").strip()
    rows = [
        row
        for pool in liquidity.get("pools") or []
        if isinstance(pool, dict)
        for row in [_pool_candidate_row(pool, pool_manager)]
        if row is not None
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
    anchor_groups = build_anchor_candidate_groups(pairs)
    return {
        "mode": "dex_pool_candidates",
        "source": "chain_flow",
        "fallbackReason": None,
        "poolStaticTable": {
            "path": liquidity.get("source"),
            "exists": bool(liquidity.get("ok") and rows),
            "mode": "robinhood_testnet_v4_pool_liquidity",
            "network": request.network,
            "chainId": request.chain_id,
            "generatedAt": liquidity.get("refreshed_at") or payload.get("generatedAt"),
            "generatedBlockNumber": liquidity.get("block_number") or payload.get("generatedBlockNumber"),
            "poolManager": pool_manager,
            "factory": None,
            "router": None,
            "quoter": None,
        },
        "filters": {"source": "robinhood-testnet v4 pool liquidity cache", "identity": "poolKey/poolId"},
        "summary": {
            "candidateRouteCount": str(len(rows)),
            "pairTableCount": str(len(pairs)),
            "anchorGroupCount": str(len(anchor_groups)),
            "ycPoolCount": str(sum(int(group.get("ycPoolCount") or 0) for group in anchor_groups)),
            "zcPoolCount": str(sum(int(group.get("zcPoolCount") or 0) for group in anchor_groups)),
            "ycTokenCount": str(sum(int(group.get("ycTokenCount") or 0) for group in anchor_groups)),
            "zcTokenCount": str(sum(int(group.get("zcTokenCount") or 0) for group in anchor_groups)),
            "pairsWithStrictQualityPools": str(sum(1 for item in pairs if item["hasStrictQualityPools"])),
            "pairsWithPreferredPools": str(sum(1 for item in pairs if item["hasPreferredPools"])),
            "preferredPoolCount": str(sum(len(item["preferredPools"]) for item in pairs)),
            "executionPoolCount": str(sum(len(item["executionPools"]) for item in pairs)),
            "strictQualityPoolCount": str(sum(len(item["strictQualityPools"]) for item in pairs)),
            "v4PoolKeyCount": str(len(liquidity.get("pool_keys") or [])),
            "v4PoolIdCount": str(len(rows)),
        },
        "pairs": pairs,
        "anchorGroups": anchor_groups,
        "errors": [] if liquidity.get("ok") else list(liquidity.get("blocking_reasons") or []),
    }


__all__ = ["dex_pool_candidates_payload"]
