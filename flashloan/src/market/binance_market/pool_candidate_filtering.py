"""Pool candidate helpers split from the public candidate service."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from core.chain_data_registry import chain_data_file_path, chain_data_files, chain_flow_spec_by_chain_id
from market.binance_market.candidate_pool_cache import candidate_pool_cache_paths
from market.binance_market.pool_candidate_constants import DEX_POOL_PROTOCOLS
from market.binance_market.pool_candidate_normalization import (
    _depth_band,
    _pair_classification,
    _pool_address,
    _string_number,
    _token_address,
)

ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"

def _is_zero_address(value: object) -> bool:
    text = str(value or "").strip().lower()
    if not text.startswith("0x"):
        return False
    try:
        return int(text, 16) == 0
    except ValueError:
        return False

def _v4_zero_address_allowed(row: dict[str, Any]) -> bool:
    """Uniswap V4 uses the zero address as the native-ETH sentinel in PoolKey.

    This is not a synthetic fallback: decoded PoolKeys that come from on-chain
    logs legitimately carry ``0x0`` for plain ETH, so we keep those rows instead
    of dropping them as invalid token addresses.
    """
    protocol = str(row.get("protocol") or row.get("dex") or "").strip().lower()
    return protocol in {"uniswap_v4", "v4"}


def _reject_reason_counts(rows: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        for reason in row.get("rejectReasons") or row.get("reject_reasons") or []:
            text = str(reason or "").strip()
            if text:
                counts[text] = counts.get(text, 0) + 1
    return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0])))

def _pool_quote_summary(row: dict) -> dict:
    quotes = row.get("quotes") if isinstance(row.get("quotes"), dict) else {}
    quote01 = quotes.get("token0ToToken1") if isinstance(quotes.get("token0ToToken1"), dict) else {}
    quote10 = quotes.get("token1ToToken0") if isinstance(quotes.get("token1ToToken0"), dict) else {}
    return {
        "probeUsd": quotes.get("probeUsd"),
        "token0ToToken1": {
            "ok": bool(quote01.get("ok")),
            "amountIn": quote01.get("amountIn"),
            "amountOut": quote01.get("amountOut"),
            "amountInUsd": quote01.get("amountInUsd"),
            "amountOutUsd": quote01.get("amountOutUsd"),
            "dexRateOutPerIn": quote01.get("dexRateOutPerIn"),
            "referenceRateOutPerIn": quote01.get("referenceRateOutPerIn"),
            "deviationPct": quote01.get("deviationPct"),
            "outputLossPct": quote01.get("outputLossPct"),
            "gas": quote01.get("gas"),
            "reason": quote01.get("reason"),
        },
        "token1ToToken0": {
            "ok": bool(quote10.get("ok")),
            "amountIn": quote10.get("amountIn"),
            "amountOut": quote10.get("amountOut"),
            "amountInUsd": quote10.get("amountInUsd"),
            "amountOutUsd": quote10.get("amountOutUsd"),
            "dexRateOutPerIn": quote10.get("dexRateOutPerIn"),
            "referenceRateOutPerIn": quote10.get("referenceRateOutPerIn"),
            "deviationPct": quote10.get("deviationPct"),
            "outputLossPct": quote10.get("outputLossPct"),
            "gas": quote10.get("gas"),
            "reason": quote10.get("reason"),
        },
    }

def _pool_candidate_row(row: dict) -> dict:
    balances = row.get("balances") if isinstance(row.get("balances"), dict) else {}
    tvl = _string_number(balances.get("tvlUsd") if balances else row.get("tvlUsd"))
    result = {
        "pool": _pool_address(row),
        "dex": row.get("dex") or row.get("adapterKind") or row.get("protocol") or "uniswap_v3",
        "protocol": row.get("dex") or row.get("adapterKind") or row.get("protocol") or "uniswap_v3",
        "fee": str(row.get("fee") or ""),
        "quality": str(row.get("quality") or ""),
        "rejectReasons": row.get("rejectReasons") or [],
        "token0": row.get("token0"),
        "token1": row.get("token1"),
        "token0Symbol": row.get("token0Symbol") or row.get("token0_symbol"),
        "token1Symbol": row.get("token1Symbol") or row.get("token1_symbol"),
        "liquidity": str(row.get("liquidity") or ""),
        "tick": str(row.get("tick") or ""),
        "sqrtPriceX96": str(row.get("sqrtPriceX96") or ""),
        "balances": {
            "token0": balances.get("token0") if balances else row.get("token0Amount"),
            "token1": balances.get("token1") if balances else row.get("token1Amount"),
            "token0Usd": balances.get("token0Usd") if balances else row.get("token0Usd"),
            "token1Usd": balances.get("token1Usd") if balances else row.get("token1Usd"),
            "tvlUsd": balances.get("tvlUsd") if balances else row.get("tvlUsd"),
        },
        "quoteSummary": _pool_quote_summary(row),
        "_sortTvlUsd": tvl if tvl is not None else -1.0,
    }
    result.update(_pair_classification(result.get("token0Symbol"), result.get("token1Symbol")))
    result["depthBand"] = (
        {"level": "observed", "label": "链上流动性已观察"}
        if result.get("liquidity") and _string_number(result["balances"].get("tvlUsd")) is None
        else _depth_band(result["balances"].get("tvlUsd"))
    )
    return result

def _bnb_pool_candidate_row(row: dict) -> dict:
    status = str(row.get("status") or "rejected").lower()
    quality = "pass" if status == "recommended" else status
    tvl = _string_number(row.get("tvlUsd"))
    result = {
        "pool": _pool_address(row),
        "dex": row.get("dex") or row.get("protocol") or "",
        "protocol": row.get("dex") or row.get("protocol") or "",
        "fee": str(row.get("fee") or ""),
        "quality": quality,
        "rejectReasons": [],
        "token0": row.get("token0"),
        "token1": row.get("token1"),
        "token0Symbol": row.get("token0Symbol"),
        "token1Symbol": row.get("token1Symbol"),
        "liquidity": str(row.get("liquidity") or ""),
        "tick": str(row.get("tick") or ""),
        "sqrtPriceX96": str(row.get("sqrtPriceX96") or ""),
        "balances": {
            "token0": row.get("token0Amount"),
            "token1": row.get("token1Amount"),
            "token0Usd": row.get("token0Usd"),
            "token1Usd": row.get("token1Usd"),
            "tvlUsd": row.get("tvlUsd"),
        },
        "quoteSummary": {},
        "_sortTvlUsd": tvl if tvl is not None else -1.0,
    }
    result.update(_pair_classification(result.get("token0Symbol"), result.get("token1Symbol")))
    result["depthBand"] = (
        {"level": "observed", "label": "链上流动性已观察"}
        if result.get("liquidity") and _string_number(result["balances"].get("tvlUsd")) is None
        else _depth_band(result["balances"].get("tvlUsd"))
    )
    return result

def _count_classes(pairs: list[dict]) -> dict[str, dict[str, int]]:
    pair_counts: dict[str, int] = {}
    pool_counts: dict[str, int] = {}
    for pair in pairs:
        pair_class = str((pair.get("classification") or {}).get("pairClass") or "unknown")
        pair_counts[pair_class] = pair_counts.get(pair_class, 0) + 1
        seen_pools: set[str] = set()
        for key in ("preferredPools", "executionPools", "strictQualityPools", "rejectedPools"):
            for pool in pair.get(key) or []:
                pool_id = str(pool.get("pool") or "")
                if not pool_id or pool_id in seen_pools:
                    continue
                seen_pools.add(pool_id)
                pool_class = str(pool.get("pairClass") or pair_class or "unknown")
                pool_counts[pool_class] = pool_counts.get(pool_class, 0) + 1
    return {"pairs": dict(sorted(pair_counts.items())), "pools": dict(sorted(pool_counts.items()))}

def _candidate_pool_has_depth(row: dict) -> bool:
    balances = row.get("balances") if isinstance(row.get("balances"), dict) else {}
    tvl = _string_number(balances.get("tvlUsd") if balances else row.get("tvlUsd"))
    if tvl is not None and tvl > 0:
        return True
    liquidity = str(row.get("liquidity") or "").strip()
    if liquidity:
        try:
            return int(liquidity) > 0
        except ValueError:
            return True
    return False

def _candidate_pool_is_high_quality(row: dict) -> bool:
    quality = str(row.get("quality") or row.get("status") or "").strip().lower()
    return quality in {"pass", "recommended", "observable"}


def _pool_candidate_has_liquidity(row: dict) -> bool:
    if row.get("dynamicFee") or row.get("specialFee"):
        return False
    if row.get("usableForObservation") is not None:
        return bool(row.get("usableForObservation"))
    if row.get("hasLiquidity") is not None:
        return bool(row.get("hasLiquidity"))
    try:
        return float(row.get("liquidityIndex") or row.get("liquidity") or 0) > 0
    except (TypeError, ValueError):
        return False


def _candidate_pool_available(row: dict) -> bool:
    return _candidate_pool_is_high_quality(row) and _candidate_pool_has_depth(row)

def _protocol_key(value: object) -> str:
    text = str(value or "").strip().lower()
    aliases = {
        "v2": "uniswap_v2",
        "v3": "uniswap_v3",
        "v4": "uniswap_v4",
        "pancakeswap_v2": "pancakeswap_v2",
        "pancakeswap_v3": "pancakeswap_v3",
        "biswap_v2": "biswap_v2",
        "traderjoe_v2": "traderjoe_v2",
        "uniswap_v2": "uniswap_v2",
        "uniswap_v3": "uniswap_v3",
        "uniswap_v4": "uniswap_v4",
        "curve": "curve",
        "balancer_v2": "balancer_v2",
        "quickswap_v2": "quickswap_v2",
    }
    return aliases.get(text, text)

def _pool_protocol(row: dict) -> str:
    return _protocol_key(row.get("protocol") or row.get("adapterKind") or row.get("dex"))

def _dex_pool_protocol_filter(protocols: list[str] | tuple[str, ...] | set[str] | None) -> set[str]:
    return {
        protocol
        for protocol in (_protocol_key(item) for item in (protocols or []) if str(item or "").strip())
        if protocol in DEX_POOL_PROTOCOLS
    }

def _filter_pair_protocols(pair: dict, protocols: set[str]) -> dict | None:
    if not protocols:
        return pair
    copied = dict(pair)
    for key in ("strictQualityPools", "preferredPools", "executionPools", "rejectedPools"):
        copied[key] = [row for row in pair.get(key) or [] if _pool_protocol(row) in protocols]
    copied["strictQualityPoolCount"] = str(len(copied["strictQualityPools"]))
    copied["preferredPoolCount"] = str(len(copied["preferredPools"]))
    copied["executionPoolCount"] = str(len(copied["executionPools"]))
    copied["rejectedPoolCount"] = str(len(copied["rejectedPools"]))
    copied["hasStrictQualityPools"] = bool(copied["strictQualityPools"])
    copied["hasPreferredPools"] = bool(copied["preferredPools"])
    if not any(copied[key] for key in ("strictQualityPools", "preferredPools", "executionPools", "rejectedPools")):
        return None
    return copied

def _anchor_token_groups_from_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    token_groups: dict[str, dict[str, Any]] = {}
    for row in rows:
        token_address = _token_address(row.get("counterAddress"))
        if not token_address or (_is_zero_address(token_address) and not _v4_zero_address_allowed(row)):
            continue
        token_symbol = str(row.get("counterSymbol") or "").strip().upper()
        if not token_symbol:
            token_symbol = _token_address(token_address)[:10].upper()
        # Uniswap V4 token identity is the currency address.  Same-symbol
        # tokens at distinct addresses are separate YC/ZC instances, so bucket
        # by address rather than by symbol.  This also keeps the observation
        # surface bounded to real token instances instead of exploding into
        # every symbol-alias permutation.
        bucket_key = token_address
        bucket = token_groups.setdefault(
            bucket_key,
            {"symbol": token_symbol, "address": token_address, "pools": [], "poolIds": set()},
        )
        pool_id = str(row.get("pool") or "").strip()
        if pool_id and pool_id not in bucket["poolIds"]:
            bucket["poolIds"].add(pool_id)
            bucket["pools"].append(row)
    tokens: list[dict[str, Any]] = []
    for token in sorted(
        token_groups.values(),
        key=lambda item: (-len(item["pools"]), str(item["symbol"]), str(item["address"])),
    ):
        if not token.get("address"):
            continue
        if _is_zero_address(token.get("address")) and not any(
            _v4_zero_address_allowed(row) for row in token.get("pools") or []
        ):
            continue
        tokens.append(
            {
                "symbol": token["symbol"],
                "address": token.get("address") or "",
                "poolCount": str(len(token["pools"])),
                "pools": token["pools"][:10],
            }
        )
    return tokens

def _filter_anchor_group_protocols(group: dict, protocols: set[str]) -> dict | None:
    if not protocols:
        return group
    copied = dict(group)
    abnormal_pools = [
        row
        for row in group.get("abnormalPools") or []
        if _pool_protocol(row) in protocols
    ]
    abnormal_tokens = _anchor_token_groups_from_rows(abnormal_pools)
    copied["abnormalPools"] = abnormal_pools
    copied["abnormalTokens"] = abnormal_tokens
    copied["abnormalPoolCount"] = str(len(abnormal_pools))
    copied["abnormalTokenCount"] = str(len(abnormal_tokens))
    copied["abnormalDirectionCount"] = str(len(abnormal_pools) * 2)
    if group.get("ycZcSameSet"):
        shared_pools = [row for row in group.get("sharedPools") or [] if _pool_protocol(row) in protocols]
        original_tokens = [row for row in group.get("sharedTokens") or [] if isinstance(row, dict)]
        shared_tokens = []
        for token in original_tokens:
            token_address = _token_address(token.get("address"))
            if not token_address:
                continue
            token_pools = [
                row
                for row in shared_pools
                if _token_address(row.get("counterAddress")) == token_address
            ]
            shared_tokens.append(
                {
                    **token,
                    "poolCount": str(len(token_pools)),
                    "pools": token_pools,
                }
            )
        if not shared_tokens:
            shared_tokens = _anchor_token_groups_from_rows(shared_pools)
        copied["sharedPools"] = shared_pools
        copied["sharedTokens"] = shared_tokens
        copied["ycPools"] = []
        copied["zcPools"] = []
        copied["ycTokens"] = []
        copied["zcTokens"] = []
        copied["ycPoolsRef"] = "sharedPools"
        copied["zcPoolsRef"] = "sharedPools"
        copied["ycTokensRef"] = "sharedTokens"
        copied["zcTokensRef"] = "sharedTokens"
        copied["sharedPoolCount"] = str(len(shared_pools))
        copied["sharedTokenCount"] = str(len(shared_tokens))
        copied["ycPoolCount"] = str(len(shared_pools))
        copied["zcPoolCount"] = str(len(shared_pools))
        copied["ycTokenCount"] = str(len(shared_tokens))
        copied["zcTokenCount"] = str(len(shared_tokens))
        copied["candidatePoolCount"] = str(len({str(row.get("pool") or "").strip() for row in shared_pools if str(row.get("pool") or "").strip()}))
        copied["candidateQueueCount"] = str(len(shared_pools) * 2)
        if not shared_pools and not abnormal_pools:
            return None
        return copied
    yc_pools = [row for row in group.get("ycPools") or [] if _pool_protocol(row) in protocols]
    zc_pools = [row for row in group.get("zcPools") or [] if _pool_protocol(row) in protocols]
    copied["ycPools"] = yc_pools
    copied["zcPools"] = zc_pools
    copied["ycTokens"] = _anchor_token_groups_from_rows(yc_pools)
    copied["zcTokens"] = _anchor_token_groups_from_rows(zc_pools)
    copied["ycPoolCount"] = str(len(yc_pools))
    copied["zcPoolCount"] = str(len(zc_pools))
    copied["ycTokenCount"] = str(len(copied["ycTokens"]))
    copied["zcTokenCount"] = str(len(copied["zcTokens"]))
    copied["candidatePoolCount"] = str(len({str(row.get("pool") or "").strip() for row in yc_pools + zc_pools if str(row.get("pool") or "").strip()}))
    if not yc_pools and not zc_pools and not abnormal_pools:
        return None
    return copied

def _filter_payload_protocols(payload: dict[str, Any], protocols: set[str]) -> dict[str, Any]:
    result = dict(payload)
    result["summary"] = dict(payload.get("summary") if isinstance(payload.get("summary"), dict) else {})
    result["selected_protocols"] = sorted(protocols)
    if not protocols:
        return result
    pairs = [
        filtered
        for pair in payload.get("pairs") or []
        for filtered in [_filter_pair_protocols(pair, protocols)]
        if filtered is not None
    ]
    result["pairs"] = pairs
    anchor_groups = [
        filtered
        for group in payload.get("anchorGroups") or []
        for filtered in [_filter_anchor_group_protocols(group, protocols)]
        if filtered is not None
    ]
    result["anchorGroups"] = anchor_groups
    summary = result["summary"]
    summary["pairTableCount"] = str(len(pairs))
    summary["anchorGroupCount"] = str(len(anchor_groups))
    summary["ycPoolCount"] = str(sum(int(group.get("ycPoolCount") or 0) for group in anchor_groups))
    summary["zcPoolCount"] = str(sum(int(group.get("zcPoolCount") or 0) for group in anchor_groups))
    summary["ycTokenCount"] = str(sum(int(group.get("ycTokenCount") or 0) for group in anchor_groups))
    summary["zcTokenCount"] = str(sum(int(group.get("zcTokenCount") or 0) for group in anchor_groups))
    summary["abnormalStaticPoolCount"] = str(sum(int(group.get("abnormalPoolCount") or 0) for group in anchor_groups))
    summary["abnormalStaticTokenCount"] = str(sum(int(group.get("abnormalTokenCount") or 0) for group in anchor_groups))
    summary["preferredPoolCount"] = str(sum(int(pair.get("preferredPoolCount") or 0) for pair in pairs))
    summary["executionPoolCount"] = str(sum(int(pair.get("executionPoolCount") or 0) for pair in pairs))
    summary["strictQualityPoolCount"] = str(sum(int(pair.get("strictQualityPoolCount") or 0) for pair in pairs))
    summary["pairsWithPreferredPools"] = str(sum(1 for pair in pairs if pair.get("hasPreferredPools")))
    summary["pairsWithStrictQualityPools"] = str(sum(1 for pair in pairs if pair.get("hasStrictQualityPools")))
    summary["preferredPairCount"] = summary["pairsWithPreferredPools"]
    summary["strictQualityPairCount"] = summary["pairsWithStrictQualityPools"]
    summary["candidateRouteCount"] = summary["preferredPoolCount"]
    summary["protocolFilter"] = sorted(protocols)
    all_anchor_groups = anchor_groups or result.get("anchorGroups") or []
    liquid_pool_ids: set[str] = set()
    liquid_pool_group_count = 0
    for group in all_anchor_groups:
        group_pools = [
            row
            for row in (
                list(group.get("ycPools") or [])
                + list(group.get("zcPools") or [])
                + list(group.get("sharedPools") or [])
            )
            if isinstance(row, dict)
        ]
        if not group_pools:
            continue
        liquid_rows = [row for row in group_pools if _pool_candidate_has_liquidity(row)]
        if liquid_rows:
            liquid_pool_group_count += 1
            liquid_pool_ids.update(
                {
                    str(row.get("pool") or row.get("poolId") or "").strip().lower()
                    for row in liquid_rows
                    if str(row.get("pool") or row.get("poolId") or "").strip()
                }
            )
    summary["liquidPoolCount"] = str(len(liquid_pool_ids))
    summary["liquidPoolGroupCount"] = str(liquid_pool_group_count)
    return result

def _annotate_candidate_pool_cache(
    payload: dict[str, Any],
    *,
    network: str,
    chain_id: int,
    force_full: bool = False,
) -> dict[str, Any]:
    cache_paths = candidate_pool_cache_paths(network=network, chain_id=chain_id)
    pool_static = payload.get("poolStaticTable") if isinstance(payload.get("poolStaticTable"), dict) else {}
    if pool_static:
        payload["poolStaticTable"] = {
            **pool_static,
            "path": str(cache_paths["snapshot"]),
            "cachePath": str(cache_paths["snapshot"]),
            "sourcePath": pool_static.get("path"),
            "exists": cache_paths["snapshot"].exists(),
        }
    anchor_groups = payload.get("anchorGroups") if isinstance(payload.get("anchorGroups"), list) else []
    anchor_symbols = [
        str(group.get("anchorSymbol") or "").strip().upper()
        for group in anchor_groups
        if isinstance(group, dict) and str(group.get("anchorSymbol") or "").strip()
    ]
    cache_meta = payload.get("candidatePoolCache") if isinstance(payload.get("candidatePoolCache"), dict) else {}
    payload["candidatePoolCache"] = {
        **cache_meta,
        "schemaVersion": cache_meta.get("schemaVersion") or "candidate-pool-cache-snapshot-v1",
        "network": str(cache_meta.get("network") or network).strip().lower(),
        "chainId": int(cache_meta.get("chainId") or chain_id),
        "globalChainId": cache_meta.get("globalChainId") or f"chain:{int(chain_id)}",
        "snapshotPath": str(cache_paths["snapshot"]),
        "manifestPath": str(cache_paths["manifest"]),
        "shardDir": str(cache_paths["shards"]),
        "tokenCount": cache_meta.get("tokenCount") if cache_meta.get("tokenCount") is not None else len(anchor_symbols),
        "tokenSymbols": cache_meta.get("tokenSymbols") or anchor_symbols,
        "refreshMode": cache_meta.get("refreshMode") or ("full" if cache_meta.get("forceFull") else "incremental"),
        "forceFull": bool(cache_meta.get("forceFull")) or bool(force_full),
        "refreshedAt": cache_meta.get("refreshedAt") or payload.get("generatedAt") or payload.get("freshness"),
    }
    return payload

def _payload_has_candidate_data(payload: dict[str, Any]) -> bool:
    if not isinstance(payload, dict):
        return False
    if isinstance(payload.get("pairs"), list) and payload.get("pairs"):
        return True
    if isinstance(payload.get("anchorGroups"), list) and payload.get("anchorGroups"):
        return True
    return False

def _candidate_pool_source_available(chain_id: int) -> bool:
    flow_spec = chain_flow_spec_by_chain_id(chain_id)
    flow = (flow_spec.get("flows") or {}).get("dex_pool_candidates") if isinstance(flow_spec, dict) else {}
    data_keys = [
        str(key or "").strip()
        for key in (flow.get("data_file_keys") or [])
        if str(key or "").strip()
    ]
    if not data_keys:
        return True
    files = chain_data_files(chain_id=chain_id)
    for key in data_keys:
        path = chain_data_file_path(None, key, chain_id=chain_id) or files.get(key)
        if path and Path(path).exists():
            return True
    return False
