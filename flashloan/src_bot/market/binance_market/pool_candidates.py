from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from core.contracts import source_metadata
from core.flash_loan_pool_discovery import query_flash_loan_pool_discovery

SRC_ROOT = Path(__file__).resolve().parents[2]
DEX_POOL_STATIC_TABLE_PATH = SRC_ROOT / "runtime" / "cache" / "bnb_arbitrage_pool_static_table.json"
AVALANCHE_DEX_POOL_STATIC_TABLE_PATH = SRC_ROOT / "runtime" / "cache" / "avalanche_l1_pool_static_table.json"
ROBINHOOD_DEX_CANDIDATE_PATH = SRC_ROOT / "runtime" / "cache" / "robinhood_dex_candidates.json"
ROBINHOOD_DEX_MARKET_POOL_PATH = SRC_ROOT / "runtime" / "cache" / "robinhood_dex_market_pools.json"
ROBINHOOD_DEX_EVIDENCE_ROOT = SRC_ROOT / "runtime" / "evidence" / "robinhood" / "dex_discovery"
CHAIN_DEX_POOL_STATIC_TABLE_PATHS = {
    56: DEX_POOL_STATIC_TABLE_PATH,
    43114: AVALANCHE_DEX_POOL_STATIC_TABLE_PATH,
    4663: ROBINHOOD_DEX_CANDIDATE_PATH,
}

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
    "pancakeswap_v2",
    "pancakeswap_v3",
    "quickswap_v2",
    "traderjoe_v2",
    "balancer_v2",
    "curve",
}
DEX_POOL_PROTOCOLS = set(FLASH_SWAP_PROTOCOLS)
UNISWAPX_DUTCH_V3_CHAIN_IDS = {42161, 43114, 8453, 56, 4663, 4217, 130}


def _read_json_object(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _cache_signature(path: Path) -> tuple[str, int, int]:
    try:
        stat = path.stat()
    except OSError:
        return (str(path), 0, 0)
    return (str(path), int(stat.st_mtime_ns), int(stat.st_size))


def _string_number(value) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed == parsed and parsed not in (float("inf"), float("-inf")) else None


def _stable_symbol(symbol: object) -> bool:
    normalized = str(symbol or "").strip().upper()
    bridge_base = normalized[:-2] if normalized.endswith(".E") else normalized
    return normalized in STABLE_SYMBOLS or bridge_base in STABLE_SYMBOLS or normalized.endswith("USD")


def _pair_classification(token0_symbol: object, token1_symbol: object) -> dict[str, Any]:
    token0 = str(token0_symbol or "").strip().upper()
    token1 = str(token1_symbol or "").strip().upper()
    token0_stable = _stable_symbol(token0)
    token1_stable = _stable_symbol(token1)
    stable_symbols = [symbol for symbol, stable in ((token0, token0_stable), (token1, token1_stable)) if symbol and stable]
    non_stable_symbols = [symbol for symbol, stable in ((token0, token0_stable), (token1, token1_stable)) if symbol and not stable]
    if token0_stable and token1_stable:
        pair_class = "stable_pair"
        label = "稳定币池"
        route_role = "stable_anchor_pool"
    elif token0_stable or token1_stable:
        pair_class = "stable_to_non_stable_pair"
        label = "稳定币-非稳定币池"
        route_role = "flashloan_entry_or_exit_leg"
    else:
        pair_class = "non_stable_pair"
        label = "非稳定币池"
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


def _depth_band(tvl_usd: object) -> dict[str, str]:
    tvl = _string_number(tvl_usd) or 0.0
    if tvl >= 1_000_000:
        return {"level": "deep", "label": "深度充足"}
    if tvl >= 100_000:
        return {"level": "medium", "label": "中等深度"}
    if tvl >= 10_000:
        return {"level": "shallow", "label": "浅池"}
    if tvl > 0:
        return {"level": "dust", "label": "极浅"}
    return {"level": "unavailable", "label": "不可用"}


def _pool_address(row: dict) -> str:
    return str(row.get("pool") or row.get("pool_address") or row.get("address") or "").strip()


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
    result["depthBand"] = _depth_band(result["balances"].get("tvlUsd"))
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
    result["depthBand"] = _depth_band(result["balances"].get("tvlUsd"))
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


def _candidate_pool_available(row: dict) -> bool:
    return _candidate_pool_is_high_quality(row) and _candidate_pool_has_depth(row)


def _protocol_key(value: object) -> str:
    text = str(value or "").strip().lower()
    aliases = {
        "v2": "uniswap_v2",
        "v3": "uniswap_v3",
        "pancakeswap_v2": "pancakeswap_v2",
        "pancakeswap_v3": "pancakeswap_v3",
        "biswap_v2": "biswap_v2",
        "traderjoe_v2": "traderjoe_v2",
        "uniswap_v2": "uniswap_v2",
        "uniswap_v3": "uniswap_v3",
        "curve": "curve",
        "balancer_v2": "balancer_v2",
        "quickswap_v2": "quickswap_v2",
    }
    return aliases.get(text, text)


def _pool_protocol(row: dict) -> str:
    return _protocol_key(row.get("dex") or row.get("protocol") or row.get("adapterKind"))


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


def _filter_payload_protocols(payload: dict[str, Any], protocols: set[str]) -> dict[str, Any]:
    if not protocols:
        return payload
    pairs = [
        filtered
        for pair in payload.get("pairs") or []
        for filtered in [_filter_pair_protocols(pair, protocols)]
        if filtered is not None
    ]
    payload["pairs"] = pairs
    summary = payload.setdefault("summary", {})
    summary["pairTableCount"] = str(len(pairs))
    summary["preferredPoolCount"] = str(sum(int(pair.get("preferredPoolCount") or 0) for pair in pairs))
    summary["executionPoolCount"] = str(sum(int(pair.get("executionPoolCount") or 0) for pair in pairs))
    summary["strictQualityPoolCount"] = str(sum(int(pair.get("strictQualityPoolCount") or 0) for pair in pairs))
    summary["protocolFilter"] = sorted(protocols)
    return payload


def _chain_id_from_table(table: dict) -> int | None:
    if str(table.get("schemaVersion") or "").startswith("robinhood-"):
        return 4663
    raw = table.get("chainId") or table.get("chain_id")
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _flash_loan_chain_profile(chain_id: int | None) -> dict[str, Any]:
    if chain_id is None:
        return {
            "source": "flash_loan_pool_discovery_snapshot",
            "chain_id": None,
            "available_protocols": [],
            "native_flashloan_protocols": [],
            "flash_swap_protocols": [],
            "has_flashloan_liquidity": False,
            "removed_protocols": [],
        }
    try:
        snapshot = query_flash_loan_pool_discovery(chain_id=chain_id)
    except Exception as exc:
        return {
            "source": "flash_loan_pool_discovery_snapshot",
            "chain_id": chain_id,
            "available_protocols": [],
            "native_flashloan_protocols": [],
            "flash_swap_protocols": [],
            "has_flashloan_liquidity": False,
            "removed_protocols": [],
            "error": str(exc)[:240],
        }
    protocols = [str(item.get("protocol") or "").strip().lower() for item in snapshot.get("protocols") or []]
    return {
        "source": "flash_loan_pool_discovery_snapshot",
        "chain_id": chain_id,
        "available_protocols": protocols,
        "native_flashloan_protocols": [protocol for protocol in protocols if protocol in FLASH_LOAN_NATIVE_PROTOCOLS],
        "flash_swap_protocols": [protocol for protocol in protocols if protocol in FLASH_SWAP_PROTOCOLS],
        "has_flashloan_liquidity": bool(protocols),
        "removed_protocols": [
            {
                "protocol": item.get("protocol"),
                "display_name": item.get("display_name"),
                "removed_reason": item.get("removed_reason"),
            }
            for item in snapshot.get("removed_protocols") or []
        ],
    }


def _uniswapx_profile(chain_id: int | None) -> dict[str, Any]:
    supported = chain_id in UNISWAPX_DUTCH_V3_CHAIN_IDS
    return {
        "name": "UniswapX",
        "settlement_model": "dutch_auction_reactor",
        "pool_model": "intent_order_flow_not_amm_pool",
        "chain_id": chain_id,
        "supported": supported,
        "reactor_family": "Dutch V3 Reactor" if supported else None,
        "flashloan_role": "solver_prefunds_or_uses_flash_liquidity_then_settles_and_repays" if supported else None,
        "arbitrage_note": (
            "可作为订单填充/结算层，不能当作可借出的 AMM 池；需要外部 AMM/借贷资金源完成买卖和还款。"
            if supported
            else "当前链未配置为 UniswapX Dutch V3 结算链。"
        ),
    }


def _flashloan_linkage(pair: dict, flash_profile: dict[str, Any]) -> dict[str, Any]:
    classification = pair.get("classification") or {}
    pair_class = str(classification.get("pairClass") or "")
    stable_symbols = list(classification.get("stableSymbols") or [])
    non_stable_symbols = list(classification.get("nonStableSymbols") or [])
    native_protocols = flash_profile.get("native_flashloan_protocols") or []
    flash_swap_protocols = flash_profile.get("flash_swap_protocols") or []
    direct_stable_borrow = bool(stable_symbols and native_protocols)
    flash_swap_bridge = bool(flash_swap_protocols)
    if pair_class == "stable_pair":
        strategy = "stable_rebalance_or_fee_tier_arbitrage"
    elif pair_class == "stable_to_non_stable_pair":
        strategy = "stable_flashloan_entry_exit"
    else:
        strategy = "requires_stable_bridge_route"
    return {
        "strategy": strategy,
        "borrow_symbols": stable_symbols,
        "route_symbols": stable_symbols + non_stable_symbols,
        "direct_stable_flashloan": direct_stable_borrow,
        "flash_swap_bridge": flash_swap_bridge,
        "available_flashloan_protocols": flash_profile.get("available_protocols") or [],
        "ready_for_atomic_arbitrage": bool((direct_stable_borrow or flash_swap_bridge) and pair.get("hasPreferredPools")),
        "conditions": [
            "selected pool must have executable depth after slippage",
            "quoted output must exceed borrowed principal plus flashloan fee and gas reserve",
            "route must end in the borrowed stable asset for same-transaction repayment",
            "UniswapX fills require a signed order/opportunity and solver execution path",
        ],
    }


def _attach_execution_context(payload: dict[str, Any], chain_id: int | None) -> dict[str, Any]:
    flash_profile = _flash_loan_chain_profile(chain_id)
    uniswapx_profile = _uniswapx_profile(chain_id)
    pairs = payload.get("pairs") if isinstance(payload.get("pairs"), list) else []
    for pair in pairs:
        pair["flashLoanLinkage"] = _flashloan_linkage(pair, flash_profile)
        pair["settlementRails"] = [uniswapx_profile] if uniswapx_profile.get("supported") else []
    class_counts = _count_classes(pairs)
    summary = payload.setdefault("summary", {})
    summary["pairClassCounts"] = class_counts["pairs"]
    summary["poolClassCounts"] = class_counts["pools"]
    summary["stablePairCount"] = str(class_counts["pairs"].get("stable_pair", 0))
    summary["stableToNonStablePairCount"] = str(class_counts["pairs"].get("stable_to_non_stable_pair", 0))
    summary["nonStablePairCount"] = str(class_counts["pairs"].get("non_stable_pair", 0))
    payload["flashLoanLiquidity"] = flash_profile
    payload["dexSettlement"] = {
        "rails": [uniswapx_profile],
        "uniswapx": uniswapx_profile,
        "arbitrage_integration": {
            "can_combine_with_flashloan": bool(flash_profile.get("has_flashloan_liquidity") and uniswapx_profile.get("supported")),
            "required_atomic_flow": [
                "borrow from flash loan source",
                "fill UniswapX order or execute AMM swap leg",
                "complete hedge/exit leg on available DEX pools",
                "repay principal plus fee in the same transaction",
            ],
        },
    }
    return payload


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
            "pairsWithStrictQualityPools": str(sum(1 for item in pair_reports if item["hasStrictQualityPools"])),
            "pairsWithPreferredPools": str(sum(1 for item in pair_reports if item["hasPreferredPools"])),
        },
        "pairs": pair_reports,
    }


def _latest_robinhood_pool_static_table(evidence_root: Path = ROBINHOOD_DEX_EVIDENCE_ROOT) -> tuple[dict[str, Any], Path | None]:
    try:
        paths = sorted(evidence_root.glob("*/pool_static_table.json"), key=lambda item: item.stat().st_mtime, reverse=True)
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
    quality = str(row.get("quality") or "").strip().lower()
    if not quality:
        quality = "pass" if status in {"observable", "recommended", "active"} else status or "observed"
    result = {
        "pool": _pool_address(row),
        "dex": row.get("dex") or row.get("protocol") or "uniswap_v3",
        "protocol": _protocol_key(row.get("protocol") or row.get("dex") or "uniswap_v3"),
        "fee": str(row.get("fee") or ""),
        "quality": quality,
        "rejectReasons": row.get("rejectReasons") or row.get("reject_reasons") or [],
        "token0": row.get("token0") or row.get("baseToken"),
        "token1": row.get("token1") or row.get("quoteToken"),
        "token0Symbol": row.get("token0Symbol") or row.get("baseTokenSymbol"),
        "token1Symbol": row.get("token1Symbol") or row.get("quoteTokenSymbol"),
        "liquidity": str(row.get("liquidity") or ""),
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
    result["depthBand"] = _depth_band(result["balances"].get("tvlUsd"))
    return result


def _robinhood_pool_table_payload(
    table: dict,
    pool_cache_path: Path,
    *,
    source: str,
    fallback_reason: str | None,
) -> dict:
    rows, effective_path, pool_source = _robinhood_pool_rows_and_source(table, pool_cache_path)
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        if not isinstance(row, dict) or not _pool_address(row):
            continue
        candidate = _robinhood_pool_candidate_row(row)
        symbols = [candidate.get("token0Symbol"), candidate.get("token1Symbol")]
        symbol_pair = str(row.get("symbolPair") or ":".join(str(item or "").upper() for item in symbols if item)).strip(":")
        if not symbol_pair:
            continue
        grouped.setdefault(symbol_pair, []).append(candidate)

    pair_reports = []
    for pair, pool_rows in grouped.items():
        pool_rows.sort(key=lambda row: (-row["_sortTvlUsd"], int(row.get("fee") or 0)))
        strict_rows = [row for row in pool_rows if str(row.get("quality") or "").lower() == "pass"]
        preferred_rows = list(strict_rows)
        rejected_rows = [row for row in pool_rows if row not in strict_rows]
        for row in pool_rows:
            row.pop("_sortTvlUsd", None)
        first = pool_rows[0]
        pair_reports.append(
            {
                "symbolPair": pair,
                "addressPair": "",
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
            "pairsWithStrictQualityPools": str(sum(1 for item in pair_reports if item["hasStrictQualityPools"])),
            "pairsWithPreferredPools": str(sum(1 for item in pair_reports if item["hasPreferredPools"])),
            "preferredPoolCount": str(sum(len(item["preferredPools"]) for item in pair_reports)),
            "executionPoolCount": str(sum(len(item["executionPools"]) for item in pair_reports)),
            "strictQualityPoolCount": str(sum(len(item["strictQualityPools"]) for item in pair_reports)),
        },
        "pairs": pair_reports,
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
            "pairsWithStrictQualityPools": str(sum(1 for item in pair_reports if item["hasStrictQualityPools"])),
            "pairsWithPreferredPools": str(sum(1 for item in pair_reports if item["hasPreferredPools"])),
        },
        "pairs": pair_reports,
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
) -> dict:
    if pool_cache_path is None:
        if chain_id is None:
            pool_cache_path = DEX_POOL_STATIC_TABLE_PATH
        elif int(chain_id) in CHAIN_DEX_POOL_STATIC_TABLE_PATHS:
            pool_cache_path = CHAIN_DEX_POOL_STATIC_TABLE_PATHS[int(chain_id)]
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
    payload = json.loads(json.dumps(_dex_pool_candidates_payload_cached(_cache_signature(pool_cache_path))))
    table = _read_json_object(pool_cache_path)
    filtered = _filter_payload_protocols(payload, _dex_pool_protocol_filter(protocols))
    return _attach_execution_context(filtered, _chain_id_from_table(table))


__all__ = ["DEX_POOL_STATIC_TABLE_PATH", "dex_pool_candidates_payload"]
