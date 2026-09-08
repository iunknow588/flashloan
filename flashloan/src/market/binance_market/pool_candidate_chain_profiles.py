"""Pool candidate helpers split from the public candidate service."""

from __future__ import annotations

from typing import Any

from core.flash_loan_pool_discovery import query_flash_loan_pool_discovery as _default_query_flash_loan_pool_discovery
from market.binance_market.pool_candidate_constants import (
    FLASH_LOAN_NATIVE_PROTOCOLS,
    FLASH_SWAP_PROTOCOLS,
    UNISWAPX_DUTCH_V3_CHAIN_IDS,
)
from market.binance_market.pool_candidate_filtering import _count_classes


def query_flash_loan_pool_discovery(*args: Any, **kwargs: Any):
    compatibility = None
    try:
        from market.binance_market import pool_candidates

        compatibility = getattr(pool_candidates, "query_flash_loan_pool_discovery", None)
    except Exception:
        compatibility = None
    if callable(compatibility) and compatibility is not query_flash_loan_pool_discovery:
        return compatibility(*args, **kwargs)
    return _default_query_flash_loan_pool_discovery(*args, **kwargs)


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
