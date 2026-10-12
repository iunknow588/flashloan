from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
import re
from typing import Any

from core.chain_flow_dispatcher import ChainFlowDispatchError, dispatch_chain_flow
from core.chain_data_registry import (
    chain_data_by_chain_id,
    chain_data_file_path,
    chain_data_files,
    chain_flow_spec_by_chain_id,
)
from core.contracts import source_metadata
from core.flash_loan_pool_discovery import query_flash_loan_pool_discovery
from market.binance_market.pool_candidate_chain_profiles import (
    _attach_execution_context,
    _chain_id_from_table,
    _flash_loan_chain_profile,
    _flashloan_linkage,
    _uniswapx_profile,
)

from market.binance_market.pool_candidate_filtering import (
    _anchor_token_groups_from_rows,
    _annotate_candidate_pool_cache,
    _bnb_pool_candidate_row,
    _candidate_pool_available,
    _candidate_pool_has_depth,
    _candidate_pool_is_high_quality,
    _candidate_pool_source_available,
    _count_classes,
    _dex_pool_protocol_filter,
    _filter_anchor_group_protocols,
    _filter_pair_protocols,
    _filter_payload_protocols,
    _payload_has_candidate_data,
    _pool_candidate_row,
    _pool_protocol,
    _pool_quote_summary,
    _protocol_key,
    _reject_reason_counts,
)

from market.binance_market.pool_candidate_normalization import (
    _cache_signature,
    _depth_band,
    _int_sort_value,
    _pair_classification,
    _pool_address,
    _read_json_object,
    _readable_symbol,
    _stable_symbol,
    _string_number,
    _token_address,
    _unordered_address_pair,
)

from market.binance_market.candidate_pool_cache import candidate_pool_cache_paths, load_candidate_pool_snapshot, refresh_candidate_pool_cache
from market.binance_market.dex_surface_cache import _cache_surface_payload, _cached_surface_payload, _clear_dex_surface_cache
from dex.intent_trade.chains.common.dex_candidate_tables import build_anchor_candidate_groups

SRC_ROOT = Path(__file__).resolve().parents[2]
DEX_POOL_STATIC_TABLE_PATH = SRC_ROOT / "runtime" / "cache" / "bnb_arbitrage_pool_static_table.json"
ROBINHOOD_DEX_CANDIDATE_PATH = (
    chain_data_file_path("robinhood", "dex_candidates")
    or SRC_ROOT / "runtime" / "cache" / "robinhood" / "robinhood_dex_candidates.json"
)
ROBINHOOD_DEX_MARKET_POOL_PATH = (
    chain_data_file_path("robinhood", "dex_market_pools")
    or SRC_ROOT / "runtime" / "cache" / "robinhood" / "robinhood_dex_market_pools.json"
)
ROBINHOOD_DEX_EVIDENCE_ROOT = SRC_ROOT / "runtime" / "evidence" / "robinhood" / "dex_discovery"

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
    "MIM",
    "SUSDE",
    "TUSD",
    "USDC",
    "USDC.E",
    "USDD",
    "USDE",
    "USDG",
    "USDT",
    "USDT.E",
}
FLASH_LOAN_NATIVE_PROTOCOLS = {"aave_v3", "venus_core", "morpho_blue"}
FLASH_SWAP_PROTOCOLS = {
    "uniswap_v2",
    "uniswap_v3",
    "uniswap_v4",
    "pancakeswap_v2",
    "pancakeswap_v3",
    "quickswap_v2",
    "traderjoe_v2",
    "balancer_v2",
    "curve",
}
DEX_POOL_PROTOCOLS = set(FLASH_SWAP_PROTOCOLS)
UNISWAPX_DUTCH_V3_CHAIN_IDS = {42161, 43114, 8453, 56, 4663, 4217, 130}
READABLE_SYMBOL_RE = re.compile(r"^[A-Z0-9.$_\-\u4e00-\u9fff()]+$")
MAX_READABLE_SYMBOL_LENGTH = 24






































































def _bnb_dex_pool_candidates_payload(table: dict, pool_cache_path: Path) -> dict:
    groups: dict[str, list[dict]] = {}
    for row in table.get("pools") or []:
        if not isinstance(row, dict) or not _pool_address(row):
            continue
        groups.setdefault(str(row.get("symbolPair") or "unknown"), []).append(row)
    pair_reports = []
    for pair, rows in groups.items():
        all_rows = [_bnb_pool_candidate_row(row) for row in rows]
        strict_rows = [row for row in all_rows if _candidate_pool_available(row)]
        preferred_rows = list(strict_rows)
        rejected_rows = [row for row in all_rows if row not in strict_rows]
        for row in all_rows:
            row.pop("_sortTvlUsd", None)
        pair_reports.append(
            {
                "symbolPair": pair,
                "addressPair": "",
                "tokenX": "",
                "tokenY": "",
                "tokenXSymbol": rows[0].get("token0Symbol") if rows else "",
                "tokenYSymbol": rows[0].get("token1Symbol") if rows else "",
                "classification": _pair_classification(
                    rows[0].get("token0Symbol") if rows else "",
                    rows[0].get("token1Symbol") if rows else "",
                ),
                "allPoolCount": str(len(all_rows)),
                "strictQualityPoolCount": str(len(strict_rows)),
                "preferredPoolCount": str(len(preferred_rows)),
                "executionPoolCount": str(min(5, len(preferred_rows))),
                "rejectedPoolCount": str(len(rejected_rows)),
                "hasStrictQualityPools": bool(strict_rows),
                "hasPreferredPools": bool(preferred_rows),
                "strictQualityPools": strict_rows,
                "preferredPools": preferred_rows,
                "executionPools": preferred_rows[:5],
                "rejectedPools": rejected_rows[:5],
                "rejectReasonCounts": {},
            }
        )
    pair_reports.sort(key=lambda item: (-int(item["preferredPoolCount"]), str(item["symbolPair"])))
    summary = table.get("summary") if isinstance(table.get("summary"), dict) else {}
    anchor_groups = build_anchor_candidate_groups(pair_reports)
    return {
        "mode": "dex_pool_candidates",
        "poolStaticTable": {
            "path": str(pool_cache_path),
            "exists": pool_cache_path.exists(),
            "mode": "bnb_static_pool_table",
            "network": table.get("network"),
            "chainId": table.get("chainId"),
            "generatedAt": table.get("generatedAt"),
            "generatedBlockNumber": table.get("generatedBlockNumber"),
            "sourceDiscoveryBlockNumber": None,
            "sourceDiscoveryFetchedAt": None,
            "factory": None,
            "router": None,
            "quoter": None,
        },
        "filters": {"status": "high quality pools with positive depth"},
        "summary": {
            **summary,
            "pairTableCount": str(len(pair_reports)),
            "anchorGroupCount": str(len(anchor_groups)),
            "ycPoolCount": str(sum(int(group.get("ycPoolCount") or 0) for group in anchor_groups)),
            "zcPoolCount": str(sum(int(group.get("zcPoolCount") or 0) for group in anchor_groups)),
            "ycTokenCount": str(sum(int(group.get("ycTokenCount") or 0) for group in anchor_groups)),
            "zcTokenCount": str(sum(int(group.get("zcTokenCount") or 0) for group in anchor_groups)),
            "pairsWithStrictQualityPools": str(sum(1 for item in pair_reports if item["hasStrictQualityPools"])),
            "pairsWithPreferredPools": str(sum(1 for item in pair_reports if item["hasPreferredPools"])),
        },
        "pairs": pair_reports,
        "anchorGroups": anchor_groups,
    }


def _latest_robinhood_pool_static_table(evidence_root: Path | None = None) -> tuple[dict[str, Any], Path | None]:
    root = evidence_root or ROBINHOOD_DEX_EVIDENCE_ROOT
    try:
        paths = sorted(root.glob("*/pool_static_table.json"), key=lambda item: item.stat().st_mtime, reverse=True)
    except OSError:
        return {}, None
    for path in paths:
        table = _read_json_object(path)
        if str(table.get("network") or "").lower() == "robinhood" and int(table.get("chainId") or 0) == 4663:
            return table, path
    return {}, None


def _robinhood_pool_rows_and_source(table: dict, pool_cache_path: Path) -> tuple[list[dict[str, Any]], Path, str]:
    if isinstance(table.get("pools"), list) and table.get("pools"):
        return list(table.get("pools") or []), pool_cache_path, "robinhood_pool_cache"
    market_table = _read_json_object(ROBINHOOD_DEX_MARKET_POOL_PATH)
    if isinstance(market_table.get("pools"), list) and market_table.get("pools"):
        return list(market_table.get("pools") or []), ROBINHOOD_DEX_MARKET_POOL_PATH, "robinhood_dexscreener_market_cache"
    evidence_table, evidence_path = _latest_robinhood_pool_static_table()
    if isinstance(evidence_table.get("pools"), list) and evidence_table.get("pools") and evidence_path is not None:
        return list(evidence_table.get("pools") or []), evidence_path, "robinhood_evidence_pool_static_table"
    fallback_rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for candidate in table.get("candidates") or []:
        if not isinstance(candidate, dict):
            continue
        symbols = [str(item or "").strip().upper() for item in candidate.get("route_symbols") or [] if item]
        if len(symbols) < 2:
            continue
        route_fees = str(candidate.get("route_key") or "").rsplit(":", 1)[-1].split("->")
        evidence = candidate.get("evidence") if isinstance(candidate.get("evidence"), dict) else {}
        evidence_pools = evidence.get("pools") or []
        for index, pool in enumerate(candidate.get("pool_addresses") or []):
            pool_address = str(pool or "").strip()
            if not pool_address or pool_address.lower() in seen:
                continue
            seen.add(pool_address.lower())
            evidence_pool = evidence_pools[index] if index < len(evidence_pools) and isinstance(evidence_pools[index], dict) else {}
            fallback_rows.append(
                {
                    "network": "robinhood",
                    "chainId": 4663,
                    "dex": "uniswap_v3",
                    "protocol": "uniswap_v3",
                    "pool": pool_address,
                    "token0Symbol": symbols[0],
                    "token1Symbol": symbols[1],
                    "symbolPair": f"{symbols[0]}:{symbols[1]}",
                    "fee": route_fees[index] if index < len(route_fees) else "",
                    "quality": "pass",
                    "status": "observable",
                    "liquidity": evidence_pool.get("liquidity"),
                    "evidence": evidence_pool,
                }
            )
    return fallback_rows, pool_cache_path, "robinhood_route_candidate_cache_legacy_fallback"


def _robinhood_pool_candidate_row(row: dict) -> dict:
    balances = row.get("balances") if isinstance(row.get("balances"), dict) else {}
    tvl = _string_number(balances.get("tvlUsd") if balances else row.get("tvlUsd"))
    status = str(row.get("status") or "").strip().lower()
    raw_fee_text = str(row.get("fee") or "").strip()
    try:
        raw_fee_int = int(raw_fee_text) if raw_fee_text else 0
    except (TypeError, ValueError):
        raw_fee_int = 0
    dynamic_fee = bool(raw_fee_int & 8388608)
    special_fee = dynamic_fee or raw_fee_int > 100_000
    hooks = str(row.get("hooks") or "").strip().lower()
    quality = str(row.get("quality") or "").strip().lower()
    if not quality:
        if status in {"observable", "recommended", "active"}:
            quality = "pass"
        elif not special_fee and 0 < raw_fee_int <= 3000 and hooks in ("", "0x0000000000000000000000000000000000000000"):
            quality = "pass"
        else:
            quality = status or "rejected"
    liquidity_index = _string_number(row.get("liquidityIndex") or row.get("liquidity") or 0)
    result = {
        "pool": _pool_address(row),
        "dex": row.get("dex") or row.get("protocol") or "uniswap_v3",
        "protocol": _protocol_key(row.get("protocol") or row.get("dex") or "uniswap_v3"),
        "fee": str(row.get("fee") or ""),
        "dynamicFee": dynamic_fee,
        "specialFee": special_fee,
        "feeLabel": ("dynamic" if dynamic_fee else "special") if special_fee else raw_fee_text,
        "usableForObservation": (
            not special_fee
            and 0 < raw_fee_int <= 3000
            and hooks in ("", "0x0000000000000000000000000000000000000000")
        ),
        "quality": quality,
        "rejectReasons": row.get("rejectReasons") or row.get("reject_reasons") or [],
        "token0": row.get("token0") or row.get("baseToken"),
        "token1": row.get("token1") or row.get("quoteToken"),
        "token0Symbol": row.get("token0Symbol") or row.get("baseTokenSymbol"),
        "token1Symbol": row.get("token1Symbol") or row.get("quoteTokenSymbol"),
        "liquidity": str(row.get("liquidity") or ""),
        "liquidityIndex": row.get("liquidityIndex"),
        "hasLiquidity": row.get("hasLiquidity"),
        "estimatedTvlUsd": row.get("estimatedTvlUsd") or (balances.get("tvlUsd") if balances else row.get("tvlUsd")),
        "depthStatus": row.get("depthStatus") or "",
        "tick": str(row.get("tick") or ((row.get("slot0") or {}).get("tick") if isinstance(row.get("slot0"), dict) else "")),
        "sqrtPriceX96": str(
            row.get("sqrtPriceX96") or ((row.get("slot0") or {}).get("sqrtPriceX96") if isinstance(row.get("slot0"), dict) else "")
        ),
        "balances": {
            "token0": balances.get("token0") if balances else row.get("token0Amount"),
            "token1": balances.get("token1") if balances else row.get("token1Amount"),
            "token0Usd": balances.get("token0Usd") if balances else row.get("token0Usd"),
            "token1Usd": balances.get("token1Usd") if balances else row.get("token1Usd"),
            "tvlUsd": balances.get("tvlUsd") if balances else row.get("tvlUsd"),
        },
        "quoteSummary": _pool_quote_summary(row),
        "market": row.get("market") or {},
        "evidence": row.get("evidence") or {},
        "_sortTvlUsd": tvl if tvl is not None else -1.0,
    }
    result.update(_pair_classification(result.get("token0Symbol"), result.get("token1Symbol")))
    if isinstance(row.get("depthBand"), dict):
        result["depthBand"] = row.get("depthBand")
    elif result.get("depthStatus") and result.get("depthStatus") != "initialized_unpriced":
        result["depthBand"] = {"level": result.get("depthStatus"), "label": result.get("depthStatus")}
    elif bool(row.get("hasLiquidity")) or (liquidity_index is not None and liquidity_index > 0):
        result["depthBand"] = {
            "level": "liquidity-index",
            "label": f"liquidity-index {liquidity_index:.6g}",
            "liquidityIndex": liquidity_index,
        }
    else:
        result["depthBand"] = (
            {"level": "observed", "label": "链上流动性已观察"}
            if result.get("liquidity") and _string_number(result["balances"].get("tvlUsd")) is None
            else _depth_band(result["balances"].get("tvlUsd"))
        )
    return result


def _robinhood_pool_table_payload(
    table: dict,
    pool_cache_path: Path,
    *,
    source: str,
    fallback_reason: str | None,
) -> dict:
    rows, effective_path, pool_source = _robinhood_pool_rows_and_source(table, pool_cache_path)
    grouped: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict) or not _pool_address(row):
            continue
        candidate = _robinhood_pool_candidate_row(row)
        if not _readable_symbol(candidate.get("token0Symbol")) or not _readable_symbol(candidate.get("token1Symbol")):
            continue
        symbols = [candidate.get("token0Symbol"), candidate.get("token1Symbol")]
        symbol_pair = str(row.get("symbolPair") or ":".join(str(item or "").upper() for item in symbols if item)).strip(":")
        if not symbol_pair:
            continue
        address_pair = _unordered_address_pair(candidate.get("token0"), candidate.get("token1"))
        group_key = address_pair or f"symbol:{symbol_pair}"
        bucket = grouped.setdefault(
            group_key,
            {
                "symbolPair": symbol_pair,
                "addressPair": address_pair,
                "identityMode": "address" if address_pair else "symbol_fallback",
                "rows": [],
            },
        )
        bucket["rows"].append(candidate)

    pair_reports = []
    for bucket in grouped.values():
        pool_rows = bucket["rows"]
        pool_rows.sort(key=lambda row: (-row["_sortTvlUsd"], _int_sort_value(row.get("fee"))))
        strict_rows = [row for row in pool_rows if str(row.get("quality") or "").lower() == "pass"]
        preferred_rows = list(strict_rows)
        rejected_rows = [row for row in pool_rows if row not in strict_rows]
        for row in pool_rows:
            row.pop("_sortTvlUsd", None)
        first = pool_rows[0]
        pair_reports.append(
            {
                "symbolPair": bucket["symbolPair"],
                "addressPair": bucket["addressPair"],
                "identityMode": bucket["identityMode"],
                "tokenX": first.get("token0"),
                "tokenY": first.get("token1"),
                "tokenXSymbol": first.get("token0Symbol"),
                "tokenYSymbol": first.get("token1Symbol"),
                "classification": _pair_classification(first.get("token0Symbol"), first.get("token1Symbol")),
                "allPoolCount": str(len(pool_rows)),
                "strictQualityPoolCount": str(len(strict_rows)),
                "preferredPoolCount": str(len(preferred_rows)),
                "executionPoolCount": str(min(5, len(preferred_rows))),
                "rejectedPoolCount": str(len(rejected_rows)),
                "hasStrictQualityPools": bool(strict_rows),
                "hasPreferredPools": bool(preferred_rows),
                "strictQualityPools": strict_rows,
                "preferredPools": preferred_rows,
                "executionPools": preferred_rows[:5],
                "rejectedPools": rejected_rows[:5],
                "rejectReasonCounts": _reject_reason_counts(rejected_rows),
            }
        )
    pair_reports.sort(key=lambda item: (-int(item.get("preferredPoolCount") or 0), str(item.get("symbolPair") or "")))
    summary = table.get("summary") if isinstance(table.get("summary"), dict) else {}
    anchor_groups = build_anchor_candidate_groups(pair_reports)
    return {
        "mode": "dex_pool_candidates",
        **source_metadata(source, freshness=table.get("generatedAt"), fallback_reason=fallback_reason),
        "poolStaticTable": {
            "path": str(effective_path),
            "exists": effective_path.exists(),
            "mode": table.get("schemaVersion") or "robinhood_pool_static_table",
            "network": "robinhood",
            "chainId": 4663,
            "generatedAt": table.get("generatedAt"),
            "generatedBlockNumber": table.get("generatedBlockNumber") or table.get("blockNumber"),
            "sourceDiscoveryBlockNumber": table.get("sourceDiscoveryBlockNumber"),
            "sourceDiscoveryFetchedAt": table.get("sourceDiscoveryFetchedAt"),
            "factory": table.get("factory"),
            "router": table.get("router"),
            "quoter": table.get("quoter"),
        },
        "filters": {"source": pool_source, "status": "observable DEX pools with market depth when available"},
        "summary": {
            **summary,
            "staticPoolCount": str(sum(len(item["preferredPools"]) + len(item["rejectedPools"]) for item in pair_reports)),
            "staticPairCount": str(len(pair_reports)),
            "pairTableCount": str(len(pair_reports)),
            "anchorGroupCount": str(len(anchor_groups)),
            "ycPoolCount": str(sum(int(group.get("ycPoolCount") or 0) for group in anchor_groups)),
            "zcPoolCount": str(sum(int(group.get("zcPoolCount") or 0) for group in anchor_groups)),
            "ycTokenCount": str(sum(int(group.get("ycTokenCount") or 0) for group in anchor_groups)),
            "zcTokenCount": str(sum(int(group.get("zcTokenCount") or 0) for group in anchor_groups)),
            "pairsWithStrictQualityPools": str(sum(1 for item in pair_reports if item["hasStrictQualityPools"])),
            "pairsWithPreferredPools": str(sum(1 for item in pair_reports if item["hasPreferredPools"])),
            "preferredPoolCount": str(sum(len(item["preferredPools"]) for item in pair_reports)),
            "executionPoolCount": str(sum(len(item["executionPools"]) for item in pair_reports)),
            "strictQualityPoolCount": str(sum(len(item["strictQualityPools"]) for item in pair_reports)),
        },
        "pairs": pair_reports,
        "anchorGroups": anchor_groups,
    }


@lru_cache(maxsize=8)
def _dex_pool_candidates_payload_cached(cache_key: tuple[str, int, int]) -> dict:
    path = Path(cache_key[0])
    table = _read_json_object(path)
    source = "runtime_cache" if table else "builtin_fallback"
    generated_at = table.get("generatedAt") if table else None
    fallback_reason = None if table else "dex_pool_static_table_unavailable"
    if str(table.get("network") or "").lower() == "bnb":
        payload = _bnb_dex_pool_candidates_payload(table, path)
        payload.update(source_metadata(source, freshness=generated_at, fallback_reason=fallback_reason))
        return payload
    if str(table.get("schemaVersion") or "").startswith("robinhood-"):
        return _robinhood_pool_table_payload(table, path, source=source, fallback_reason=fallback_reason)
    pair_reports = []
    for pair in table.get("pairs") or []:
        if not isinstance(pair, dict):
            continue
        all_rows = [row for row in pair.get("allPools") or [] if isinstance(row, dict) and _pool_address(row)]
        strict_rows = [
            _pool_candidate_row(row)
            for row in all_rows
            if str(row.get("quality") or "").lower() == "pass" and _candidate_pool_has_depth(row)
        ]
        preferred_rows = [
            _pool_candidate_row(row)
            for row in (pair.get("preferredPools") or [])
            if isinstance(row, dict) and _pool_address(row) and _candidate_pool_available(row)
        ]
        if not preferred_rows:
            preferred_rows = list(strict_rows)
        execution_rows = [
            _pool_candidate_row(row)
            for row in (pair.get("executionPools") or [])
            if isinstance(row, dict) and _pool_address(row) and _candidate_pool_available(row)
        ]
        if not execution_rows:
            execution_rows = preferred_rows[:5]
        rejected_rows = [_pool_candidate_row(row) for row in all_rows if str(row.get("quality") or "").lower() != "pass"]
        strict_rows.sort(key=lambda row: (-row["_sortTvlUsd"], int(row.get("fee") or 0)))
        preferred_rows.sort(key=lambda row: (-row["_sortTvlUsd"], int(row.get("fee") or 0)))
        execution_rows.sort(key=lambda row: (-row["_sortTvlUsd"], int(row.get("fee") or 0)))
        rejected_rows.sort(key=lambda row: (-row["_sortTvlUsd"], int(row.get("fee") or 0)))
        for row in strict_rows + preferred_rows + execution_rows + rejected_rows:
            row.pop("_sortTvlUsd", None)
        pair_reports.append(
            {
                "symbolPair": pair.get("symbolPair"),
                "addressPair": pair.get("addressPair"),
                "tokenX": pair.get("tokenX"),
                "tokenY": pair.get("tokenY"),
                "tokenXSymbol": pair.get("tokenXSymbol"),
                "tokenYSymbol": pair.get("tokenYSymbol"),
                "classification": _pair_classification(pair.get("tokenXSymbol"), pair.get("tokenYSymbol")),
                "allPoolCount": str(len(all_rows)),
                "strictQualityPoolCount": str(len(strict_rows)),
                "preferredPoolCount": str(len(preferred_rows)),
                "executionPoolCount": str(len(execution_rows)),
                "rejectedPoolCount": str(len(rejected_rows)),
                "hasStrictQualityPools": bool(strict_rows),
                "hasPreferredPools": bool(preferred_rows),
                "strictQualityPools": strict_rows,
                "preferredPools": preferred_rows,
                "executionPools": execution_rows,
                "rejectedPools": rejected_rows[:5],
                "rejectReasonCounts": _reject_reason_counts(all_rows),
            }
        )
    pair_reports.sort(
        key=lambda item: (
            -int(item.get("preferredPoolCount") or 0),
            str(item.get("symbolPair") or ""),
        )
    )
    anchor_groups = build_anchor_candidate_groups(pair_reports)
    return {
        "mode": "dex_pool_candidates",
        **source_metadata(source, freshness=generated_at, fallback_reason=fallback_reason),
        "poolStaticTable": {
            "path": str(path),
            "exists": path.exists(),
            "mode": table.get("mode"),
            "network": table.get("network"),
            "chainId": table.get("chainId") or table.get("chain_id"),
            "generatedAt": table.get("generatedAt"),
            "generatedBlockNumber": table.get("generatedBlockNumber"),
            "sourceDiscoveryBlockNumber": table.get("sourceDiscoveryBlockNumber"),
            "sourceDiscoveryFetchedAt": table.get("sourceDiscoveryFetchedAt"),
            "factory": table.get("factory"),
            "router": table.get("router"),
            "quoter": table.get("quoter"),
        },
        "filters": table.get("filters") or {},
        "summary": {
            **(table.get("summary") if isinstance(table.get("summary"), dict) else {}),
            "pairTableCount": str(len(pair_reports)),
            "anchorGroupCount": str(len(anchor_groups)),
            "ycPoolCount": str(sum(int(group.get("ycPoolCount") or 0) for group in anchor_groups)),
            "zcPoolCount": str(sum(int(group.get("zcPoolCount") or 0) for group in anchor_groups)),
            "ycTokenCount": str(sum(int(group.get("ycTokenCount") or 0) for group in anchor_groups)),
            "zcTokenCount": str(sum(int(group.get("zcTokenCount") or 0) for group in anchor_groups)),
            "pairsWithStrictQualityPools": str(sum(1 for item in pair_reports if item["hasStrictQualityPools"])),
            "pairsWithPreferredPools": str(sum(1 for item in pair_reports if item["hasPreferredPools"])),
        },
        "pairs": pair_reports,
        "anchorGroups": anchor_groups,
    }


def _robinhood_dex_pool_candidates_payload(
    table: dict,
    pool_cache_path: Path,
    *,
    source: str,
    fallback_reason: str | None,
) -> dict:
    grouped: dict[str, dict[str, Any]] = {}
    for candidate in table.get("candidates") or []:
        if not isinstance(candidate, dict):
            continue
        symbols = [str(item or "").strip().upper() for item in candidate.get("route_symbols") or [] if item]
        if len(symbols) < 2:
            continue
        pair_key = ":".join(symbols[:2])
        pair = grouped.setdefault(
            pair_key,
            {
                "symbolPair": pair_key,
                "addressPair": "",
                "tokenX": "",
                "tokenY": "",
                "tokenXSymbol": symbols[0],
                "tokenYSymbol": symbols[1],
                "classification": _pair_classification(symbols[0], symbols[1]),
                "strictQualityPools": [],
                "preferredPools": [],
                "executionPools": [],
                "rejectedPools": [],
                "routeCandidates": [],
                "_seenPools": set(),
            },
        )
        pair["routeCandidates"].append(candidate)
        route_fees = str(candidate.get("route_key") or "").rsplit(":", 1)[-1].split("->")
        evidence = candidate.get("evidence") if isinstance(candidate.get("evidence"), dict) else {}
        evidence_pools = evidence.get("pools") or []
        for index, pool in enumerate(candidate.get("pool_addresses") or []):
            pool_address = str(pool or "").strip()
            if not pool_address or pool_address.lower() in pair["_seenPools"]:
                continue
            pair["_seenPools"].add(pool_address.lower())
            evidence_pool = evidence_pools[index] if index < len(evidence_pools) and isinstance(evidence_pools[index], dict) else {}
            liquidity = str(evidence_pool.get("liquidity") or "")
            row = {
                "pool": pool_address,
                "dex": "uniswap_v3",
                "protocol": "uniswap_v3",
                "fee": route_fees[index] if index < len(route_fees) else "",
                "quality": "pass",
                "rejectReasons": [],
                "token0": "",
                "token1": "",
                "token0Symbol": symbols[0],
                "token1Symbol": symbols[1],
                "liquidity": liquidity,
                "tick": "",
                "sqrtPriceX96": "",
                "balances": {"token0": None, "token1": None, "token0Usd": None, "token1Usd": None, "tvlUsd": None},
                "quoteSummary": {},
                "evidence": {
                    "blockNumber": evidence_pool.get("blockNumber"),
                    "poolStateVerified": bool(evidence_pool.get("poolStateVerified")),
                    "factoryQueryVerified": bool(evidence_pool.get("factoryQueryVerified")),
                    "sourceUrl": evidence_pool.get("sourceUrl"),
                },
            }
            row.update(_pair_classification(symbols[0], symbols[1]))
            row["depthBand"] = {"level": "observed", "label": "链上流动性已观察"} if liquidity else _depth_band(None)
            pair["preferredPools"].append(row)
            pair["strictQualityPools"].append(row)
            if len(pair["executionPools"]) < 5:
                pair["executionPools"].append(row)
    pair_reports = []
    for pair in grouped.values():
        pair.pop("_seenPools", None)
        pair["allPoolCount"] = str(len(pair["preferredPools"]))
        pair["strictQualityPoolCount"] = str(len(pair["strictQualityPools"]))
        pair["preferredPoolCount"] = str(len(pair["preferredPools"]))
        pair["executionPoolCount"] = str(len(pair["executionPools"]))
        pair["rejectedPoolCount"] = "0"
        pair["hasStrictQualityPools"] = bool(pair["strictQualityPools"])
        pair["hasPreferredPools"] = bool(pair["preferredPools"])
        pair["rejectReasonCounts"] = {}
        pair_reports.append(pair)
    pair_reports.sort(key=lambda item: (-int(item.get("preferredPoolCount") or 0), str(item.get("symbolPair") or "")))
    return {
        "mode": "dex_pool_candidates",
        **source_metadata(source, freshness=table.get("generatedAt"), fallback_reason=fallback_reason),
        "poolStaticTable": {
            "path": str(pool_cache_path),
            "exists": pool_cache_path.exists(),
            "mode": table.get("schemaVersion"),
            "network": table.get("network") or "robinhood",
            "chainId": 4663,
            "generatedAt": table.get("generatedAt"),
            "generatedBlockNumber": None,
            "sourceDiscoveryBlockNumber": None,
            "sourceDiscoveryFetchedAt": None,
            "factory": None,
            "router": None,
            "quoter": None,
        },
        "filters": {"source": "robinhood route candidate cache"},
        "summary": {
            "candidateRouteCount": str(len(table.get("candidates") or [])),
            "pairTableCount": str(len(pair_reports)),
            "pairsWithStrictQualityPools": str(sum(1 for item in pair_reports if item["hasStrictQualityPools"])),
            "pairsWithPreferredPools": str(sum(1 for item in pair_reports if item["hasPreferredPools"])),
            "preferredPoolCount": str(sum(len(item["preferredPools"]) for item in pair_reports)),
            "executionPoolCount": str(sum(len(item["executionPools"]) for item in pair_reports)),
            "strictQualityPoolCount": str(sum(len(item["strictQualityPools"]) for item in pair_reports)),
        },
        "pairs": pair_reports,
    }


def dex_pool_candidates_payload(
    pool_cache_path: Path | None = None,
    *,
    chain_id: int | None = None,
    protocols: list[str] | tuple[str, ...] | set[str] | None = None,
    refresh: bool = False,
    force_full: bool = False,
) -> dict:
    if pool_cache_path is None:
        if chain_id is not None:
            protocol_filter = _dex_pool_protocol_filter(protocols)
            chain_data = chain_data_by_chain_id(int(chain_id))
            if not chain_data:
                payload = {
                    "mode": "dex_pool_candidates",
                    **source_metadata("not_configured", fallback_reason="dex_pool_static_table_not_configured_for_chain"),
                    "poolStaticTable": {"path": None, "exists": False, "chainId": int(chain_id), "network": None},
                    "filters": {},
                    "summary": {"pairTableCount": "0", "pairsWithStrictQualityPools": "0", "pairsWithPreferredPools": "0"},
                    "pairs": [],
                }
                return _attach_execution_context(payload, int(chain_id))
            network = str(chain_data.get("network") or "").strip().lower()
            use_generic_candidate_cache = network not in {"robinhood", "robinhood-testnet"}
            if not use_generic_candidate_cache:
                # Robinhood mainnet V4 candidate surface is now database-first.
                # The old JSON cache remains only as a compatibility fallback
                # when the SQLite business tables are not yet available.
                if network == "robinhood" and int(chain_id) == 4663 and (
                    not protocol_filter or "uniswap_v4" in protocol_filter
                ):
                    from core.db.page_payloads import build_candidate_payload as _build_db_candidate_payload

                    # Candidate data is a static business snapshot.  Keep the
                    # DB-shaped page payload in the process-wide surface cache
                    # so every page view can reuse the Rust/SQLite result.
                    # ``refresh`` and ``force_full`` are the explicit manual
                    # refresh paths and are the only normal invalidation point.
                    if not refresh and not force_full:
                        cached = _cached_surface_payload(
                            "dex_pool_candidates",
                            chain_id=int(chain_id),
                            protocols=protocols,
                            use_static_observation=False,
                        )
                        if cached is not None:
                            filtered = _filter_payload_protocols(cached, protocol_filter)
                            return _attach_execution_context(filtered, int(chain_id))
                    _clear_dex_surface_cache(
                        surface="dex_pool_candidates",
                        chain_id=int(chain_id),
                        protocols=protocols,
                        use_static_observation=False,
                    )

                    db_payload = _build_db_candidate_payload(
                        chain_id=int(chain_id),
                        protocols=protocols,
                    )
                    filtered = _filter_payload_protocols(db_payload, protocol_filter)
                    _cache_surface_payload(
                        "dex_pool_candidates",
                        filtered,
                        chain_id=int(chain_id),
                        protocols=protocols,
                        use_static_observation=False,
                        ttl_seconds=0,
                    )
                    return _attach_execution_context(filtered, int(chain_id))
                try:
                    payload = dispatch_chain_flow(
                        chain_id=int(chain_id),
                        flow_name="dex_pool_candidates",
                        protocol="",
                    )
                    if (
                        network not in {"robinhood", "robinhood-testnet"}
                        and _payload_has_candidate_data(payload)
                    ):
                        refresh_candidate_pool_cache(
                            payload,
                            network=network,
                            chain_id=int(chain_id),
                            force_full=force_full,
                        )
                    filtered = _filter_payload_protocols(payload, protocol_filter)
                    return _attach_execution_context(filtered, int(chain_id))
                except ChainFlowDispatchError as exc:
                    if exc.code != "chain_flow_missing":
                        raise
                    payload = {
                        "mode": "dex_pool_candidates",
                        **source_metadata("builtin_fallback", fallback_reason="dex_pool_candidates_chain_flow_missing"),
                        "poolStaticTable": {"path": None, "exists": False, "chainId": int(chain_id), "network": network},
                        "filters": {},
                        "summary": {"pairTableCount": "0", "pairsWithStrictQualityPools": "0", "pairsWithPreferredPools": "0"},
                        "pairs": [],
                }
                return _attach_execution_context(payload, int(chain_id))
            cached_payload = None
            if not refresh and not force_full:
                cached_payload = load_candidate_pool_snapshot(network=network, chain_id=int(chain_id))
            if cached_payload is not None:
                filtered = _filter_payload_protocols(cached_payload, protocol_filter)
                filtered = _annotate_candidate_pool_cache(filtered, network=network, chain_id=int(chain_id), force_full=force_full)
                return _attach_execution_context(filtered, int(chain_id))
            if not _candidate_pool_source_available(int(chain_id)):
                payload = {
                    "mode": "dex_pool_candidates",
                    **source_metadata("builtin_fallback", fallback_reason="dex_pool_candidates_local_json_missing"),
                    "poolStaticTable": {"path": None, "exists": False, "chainId": int(chain_id), "network": network},
                    "filters": {},
                    "summary": {"pairTableCount": "0", "pairsWithStrictQualityPools": "0", "pairsWithPreferredPools": "0"},
                    "pairs": [],
                }
                return _attach_execution_context(payload, int(chain_id))
            try:
                payload = dispatch_chain_flow(
                    chain_id=int(chain_id),
                    flow_name="dex_pool_candidates",
                    protocol="",
                )
                if _payload_has_candidate_data(payload):
                    refresh_candidate_pool_cache(
                        payload,
                        network=network,
                        chain_id=int(chain_id),
                        force_full=force_full,
                    )
                filtered = _filter_payload_protocols(payload, protocol_filter)
                filtered = _annotate_candidate_pool_cache(filtered, network=network, chain_id=int(chain_id), force_full=force_full)
                return _attach_execution_context(filtered, int(chain_id))
            except ChainFlowDispatchError as exc:
                if exc.code != "chain_flow_missing":
                    raise
                if cached_payload is None:
                    cached_payload = load_candidate_pool_snapshot(network=network, chain_id=int(chain_id))
                if cached_payload is not None:
                    filtered = _filter_payload_protocols(cached_payload, protocol_filter)
                    filtered = _annotate_candidate_pool_cache(filtered, network=network, chain_id=int(chain_id), force_full=force_full)
                    return _attach_execution_context(filtered, int(chain_id))
        if chain_id is None:
            pool_cache_path = DEX_POOL_STATIC_TABLE_PATH
        else:
            payload = {
                "mode": "dex_pool_candidates",
                **source_metadata("not_configured", fallback_reason="dex_pool_static_table_not_configured_for_chain"),
                "poolStaticTable": {"path": None, "exists": False, "chainId": int(chain_id), "network": None},
                "filters": {},
                "summary": {"pairTableCount": "0", "pairsWithStrictQualityPools": "0", "pairsWithPreferredPools": "0"},
                "pairs": [],
            }
            return _attach_execution_context(payload, int(chain_id))
    payload = _dex_pool_candidates_payload_cached(_cache_signature(pool_cache_path))
    table = _read_json_object(pool_cache_path)
    filtered = _filter_payload_protocols(payload, _dex_pool_protocol_filter(protocols))
    return _attach_execution_context(filtered, _chain_id_from_table(table))


__all__ = ["DEX_POOL_STATIC_TABLE_PATH", "dex_pool_candidates_payload"]
