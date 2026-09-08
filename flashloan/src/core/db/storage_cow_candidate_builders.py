"""Pure cow execution attempt builders extracted from storage_cow_execution."""

from __future__ import annotations

from typing import Any

from core.db.cow_execution_review import (
    _attempt_route_address_identity,
    _market_state_summary,
)
from core.db.storage_cow_policy import _json_text


def _attempt_from_quote(
    quote: dict[str, Any],
    *,
    market_state: dict[str, Any],
    cow_network: str,
    cow_chain_id: int | None,
    owner: str | None,
) -> dict[str, Any]:
    precheck = quote.get("execution_precheck") or {}
    reasons = precheck.get("reasons") if isinstance(precheck, dict) else []
    state = str(precheck.get("status") or ("quote_failed" if quote.get("error") else "quoted"))
    execution_phase = str(
        precheck.get("execution_phase")
        or ("order_submission" if state in {"submitted_success", "submission_failed"} else "quote_precheck")
    )
    cow_intent = quote.get("cow_flashloan_intent") if isinstance(quote.get("cow_flashloan_intent"), dict) else precheck.get("cow_flashloan_intent") if isinstance(precheck, dict) else {}
    cow_sdk_result = quote.get("cow_sdk_result") if isinstance(quote.get("cow_sdk_result"), dict) else {}
    return {
        "observed_at": market_state.get("observed_at"),
        "network": cow_network,
        "chain_id": cow_chain_id,
        "owner_address": owner,
        "pair": quote.get("pair"),
        "pair_rank": quote.get("pair_rank"),
        "priority_reason": quote.get("priority_reason"),
        "route_path": quote.get("path") or [],
        "state": state,
        "execution_phase": execution_phase,
        "checks_passed": bool(precheck.get("checks_passed")),
        "can_submit_order": bool(precheck.get("can_submit_order")),
        "order_submission_enabled": bool(precheck.get("order_submission_enabled")),
        "auto_execute_requested": bool(precheck.get("auto_execute_requested")),
        "control_mode": precheck.get("control_mode") or (cow_intent or {}).get("control_mode"),
        "route_hop_constraints_enforced": bool(precheck.get("route_hop_constraints_enforced")),
        "final_delta_amount": quote.get("final_delta_amount"),
        "final_symbol": quote.get("final_symbol"),
        "blocked_reasons": reasons if isinstance(reasons, list) else [str(reasons)],
        "cow_flashloan_intent": cow_intent or {},
        "cow_sdk_result": cow_sdk_result or {},
        "quote": quote,
        "precheck": precheck,
        "market_state": _market_state_summary(market_state),
        "error": quote.get("error"),
    }


def _attempt_from_market_route(
    pair: dict[str, Any],
    route: dict[str, Any],
    *,
    market_state: dict[str, Any],
    cow_network: str,
    cow_chain_id: int | None,
) -> dict[str, Any]:
    route_path = route.get("route") or pair.get("route") or []
    reasons = []
    for item in [*(pair.get("blocked_reasons") or []), *(route.get("blocked_reasons") or [])]:
        if item and item not in reasons:
            reasons.append(item)
    if not reasons:
        reasons.append("requires_cow_or_dex_quote")
    precheck = {
        "status": "quote_required",
        "checks_passed": False,
        "can_submit_order": False,
        "order_submission_enabled": False,
        "auto_execute_requested": False,
        "reasons": reasons,
        "quote_required": bool(pair.get("quote_required", True) or route.get("quote_required", True)),
        "estimation_available": bool(pair.get("estimation_available") or route.get("estimation_available")),
        "edge_hint_percent": route.get("edge_hint_percent", pair.get("edge_hint_percent")),
        "window_spread_percent": pair.get("window_spread_percent"),
    }
    quote = {
        "pair": pair.get("pair"),
        "pair_rank": pair.get("rank") or pair.get("grid_rank"),
        "priority_reason": route.get("priority_reason"),
        "path": route_path,
        "input_amount": route.get("initial_amount"),
        "input_symbol": route.get("initial_symbol"),
        "final_symbol": route.get("initial_symbol"),
        "final_delta_amount": route.get("net_after_flashloan_amount") or route.get("profit_amount"),
        "quote_verified": False,
        "quote_required": True,
        "candidate_basis": pair.get("candidate_basis") or route.get("candidate_basis"),
        "trigger_source": pair.get("trigger_source"),
        "edge_hint_percent": route.get("edge_hint_percent", pair.get("edge_hint_percent")),
        "window_spread_percent": pair.get("window_spread_percent"),
        "binance_execution_plan": route.get("binance_execution_plan") or pair.get("binance_execution_plan") or {},
        "x_symbol": pair.get("x_symbol"),
        "y_symbol": pair.get("y_symbol"),
        "x_base_symbol": pair.get("x_base_symbol"),
        "y_base_symbol": pair.get("y_base_symbol"),
        "x_change_percent": pair.get("x_change_percent"),
        "y_change_percent": pair.get("y_change_percent"),
        "x_start_price": pair.get("x_start_price"),
        "x_current_price": pair.get("x_current_price"),
        "x_end_price": pair.get("x_end_price", pair.get("x_current_price")),
        "x_start_ms": pair.get("x_start_ms"),
        "x_end_ms": pair.get("x_end_ms"),
        "x_price_source": pair.get("x_price_source"),
        "y_start_ms": pair.get("y_start_ms"),
        "y_end_ms": pair.get("y_end_ms"),
        "y_price_source": pair.get("y_price_source"),
        "y_start_price": pair.get("y_start_price"),
        "y_current_price": pair.get("y_current_price"),
        "y_end_price": pair.get("y_end_price", pair.get("y_current_price")),
        "signal_timing": pair.get("signal_timing") or market_state.get("signal_timing"),
        "route": route,
    }
    return {
        "observed_at": market_state.get("observed_at"),
        "network": cow_network,
        "chain_id": cow_chain_id,
        "owner_address": None,
        "pair": pair.get("pair"),
        "pair_rank": pair.get("rank") or pair.get("grid_rank"),
        "priority_reason": route.get("priority_reason"),
        "route_path": route_path,
        "state": "quote_required",
        "execution_phase": "market_candidate",
        "checks_passed": False,
        "can_submit_order": False,
        "order_submission_enabled": False,
        "auto_execute_requested": False,
        "final_delta_amount": quote["final_delta_amount"],
        "final_symbol": quote["final_symbol"],
        "blocked_reasons": reasons,
        "quote": quote,
        "precheck": precheck,
        "market_state": _market_state_summary(market_state),
        "error": None,
    }


def build_cow_execution_attempts(
    payload: dict[str, Any],
    *,
    market_state: dict[str, Any],
) -> list[dict[str, Any]]:
    return [
        _attempt_from_quote(
            quote,
            market_state=market_state,
            cow_network=str(payload.get("cow_network") or ""),
            cow_chain_id=payload.get("cow_chain_id"),
            owner=payload.get("owner"),
        )
        for quote in payload.get("ranking") or []
        if isinstance(quote, dict)
    ]


def build_cow_market_candidate_attempts(market_state: dict[str, Any]) -> list[dict[str, Any]]:
    cow_filter = market_state.get("cow_filter") if isinstance(market_state, dict) else {}
    cow_network = str((cow_filter or {}).get("network") or market_state.get("cow_network") or "")
    cow_chain_id = (cow_filter or {}).get("chain_id") or market_state.get("cow_chain_id")
    attempts = []
    for pair in market_state.get("pairs") or []:
        if not isinstance(pair, dict):
            continue
        route_results = pair.get("route_results") or []
        if not route_results:
            route_results = [{"route": pair.get("route") or [], "priority_reason": pair.get("priority_reason")}]
        for route in route_results:
            if isinstance(route, dict):
                attempts.append(
                    _attempt_from_market_route(
                        pair,
                        route,
                        market_state=market_state,
                        cow_network=cow_network,
                        cow_chain_id=cow_chain_id,
                    )
                )
    return attempts


def _claim_route_results(x: dict[str, Any], y: dict[str, Any], amount: Any) -> list[dict[str, Any]]:
    x_base = str(x.get("base_symbol") or x.get("symbol") or "").removesuffix("USDT")
    y_base = str(y.get("base_symbol") or y.get("symbol") or "").removesuffix("USDT")
    return [
        {
            "route_no": 1,
            "route": ["USDC", y_base, x_base, "USDC"],
            "initial_amount": str(amount) if amount is not None else None,
            "initial_symbol": "USDC",
            "priority_reason": "buy_loser_then_gainer",
            "quote_required": True,
        },
        {
            "route_no": 2,
            "route": ["USDC", x_base, y_base, "USDC"],
            "initial_amount": str(amount) if amount is not None else None,
            "initial_symbol": "USDC",
            "priority_reason": "reverse_check",
            "quote_required": True,
        },
    ]


def _claim_pair_row(x: dict[str, Any], y: dict[str, Any], rank: int, *, amount: Any) -> dict[str, Any] | None:
    x_symbol = str(x.get("symbol") or "").strip().upper()
    y_symbol = str(y.get("symbol") or "").strip().upper()
    if not x_symbol or not y_symbol or x_symbol == y_symbol:
        return None
    try:
        x_change = float(x.get("change_percent"))
        y_change = float(y.get("change_percent"))
    except (TypeError, ValueError):
        return None
    x_base = str(x.get("base_symbol") or x_symbol).strip().upper()
    y_base = str(y.get("base_symbol") or y_symbol).strip().upper()
    return {
        "rank": rank,
        "pair": f"{x_symbol} / {y_symbol}",
        "x_symbol": x_symbol,
        "y_symbol": y_symbol,
        "x_base_symbol": x_base,
        "y_base_symbol": y_base,
        "x_change_percent": x_change,
        "y_change_percent": y_change,
        "x_start_price": x.get("start_price"),
        "x_current_price": x.get("current_price"),
        "x_end_price": x.get("end_price", x.get("current_price")),
        "x_start_ms": x.get("start_ms"),
        "x_end_ms": x.get("end_ms"),
        "x_price_source": x.get("price_source"),
        "y_start_price": y.get("start_price"),
        "y_current_price": y.get("current_price"),
        "y_end_price": y.get("end_price", y.get("current_price")),
        "y_start_ms": y.get("start_ms"),
        "y_end_ms": y.get("end_ms"),
        "y_price_source": y.get("price_source"),
        "window_spread_percent": x_change - y_change,
        "candidate_basis": "cow_network_claim_top_bottom",
        "trigger_source": "cow_network_claim",
        "quote_required": True,
        "estimation_available": False,
        "blocked_reasons": ["requires_cow_or_dex_quote"],
        "route_results": _claim_route_results(x, y, amount),
    }


def build_cow_market_claim_pairs(
    claim: dict[str, Any],
    *,
    include_below_min_spread: bool = False,
    max_pairs: int | None = None,
) -> list[dict[str, Any]]:
    if not isinstance(claim, dict):
        return []
    threshold_detail = claim.get("threshold_detail") if isinstance(claim.get("threshold_detail"), dict) else {}
    try:
        min_spread = float(claim.get("min_spread_percent") or threshold_detail.get("adjusted_min_spread_percent") or 0)
    except (TypeError, ValueError):
        min_spread = 0.0
    amount = threshold_detail.get("amount")
    pairs = []
    for x in claim.get("top") or []:
        if not isinstance(x, dict):
            continue
        if x.get("pool_route_supported") is False:
            continue
        for y in claim.get("bottom") or []:
            if not isinstance(y, dict):
                continue
            if y.get("pool_route_supported") is False:
                continue
            pair = _claim_pair_row(x, y, len(pairs) + 1, amount=amount)
            if not pair:
                continue
            if float(pair.get("window_spread_percent") or 0) <= min_spread and not include_below_min_spread:
                continue
            if float(pair.get("window_spread_percent") or 0) <= min_spread:
                pair["blocked_reasons"] = ["spread_below_dynamic_min", "requires_cow_or_dex_quote"]
                pair["candidate_basis"] = "cow_network_claim_top_bottom_below_spread"
            pairs.append(pair)
            if max_pairs is not None and len(pairs) >= max(1, int(max_pairs)):
                return pairs
    return pairs


def build_cow_market_claim_candidate_attempts(
    network_claims: list[dict[str, Any]],
    *,
    observed_at: Any = None,
    window_seconds: Any = None,
    price_source: Any = None,
    market_state_source: Any = None,
    fallback_reason: Any = None,
) -> list[dict[str, Any]]:
    attempts = []
    for claim in network_claims or []:
        if not isinstance(claim, dict):
            continue
        threshold_detail = claim.get("threshold_detail") if isinstance(claim.get("threshold_detail"), dict) else {}
        pairs = build_cow_market_claim_pairs(claim)
        if not pairs:
            continue
        market_state = {
            "observed_at": observed_at,
            "window_seconds": window_seconds,
            "price_source": price_source,
            "market_state_source": market_state_source,
            "fallback_reason": fallback_reason,
            "cow_filter": {
                "network": claim.get("network"),
                "chain_id": claim.get("chain_id"),
                "source": "cow_network_claim",
                "token_cache_source": claim.get("token_cache_source"),
                "token_cache_count": claim.get("token_cache_count"),
                "threshold_detail": threshold_detail,
            },
            "pairs": pairs,
        }
        attempts.extend(build_cow_market_candidate_attempts(market_state))
    return attempts


def _is_market_candidate(item: dict[str, Any]) -> bool:
    return str(item.get("execution_phase") or "") == "market_candidate"


def _attempt_signature(item: dict[str, Any]) -> tuple[Any, ...]:
    return (
        item.get("observed_at"),
        item.get("network"),
        item.get("pair"),
        item.get("pair_rank"),
        item.get("priority_reason"),
        _json_text(item.get("route_path") or []),
        _json_text(_attempt_route_address_identity(item)),
        item.get("execution_phase") or "quote_precheck",
    )


__all__ = [
    "build_cow_execution_attempts",
    "build_cow_market_candidate_attempts",
    "build_cow_market_claim_candidate_attempts",
    "build_cow_market_claim_pairs",
]
