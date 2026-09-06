"""Service payload construction for the Binance CoW quote/queue route."""

from __future__ import annotations

from dataclasses import dataclass

from core.db.storage_cow_execution import build_cow_market_claim_pairs
from market.binance_market.service import (
    DEFAULT_BINANCE_RAW_SIDE_LIMIT,
    build_binance_market_state,
    build_cow_network_market_claims,
    build_cow_quote_verification,
    select_binance_market_extremes,
)


@dataclass(frozen=True)
class CowQuoteDeps:
    build_cow_network_claims=build_cow_network_market_claims,
    build_cow_market_claim_pairs=build_cow_market_claim_pairs,
    build_cow_quote_verification=build_cow_quote_verification,


def build_cow_quote_market_state(
    extremes: dict | None,
    *,
    assets: list[str],
    arbitrage_config,
    slippage_bps: int,
    thresholds: dict,
    cow_network: str,
    token_cache: dict,
    side_limit: int,
    pair_side_limit: int,
    record_pool_evidence,
    deps: CowQuoteDeps = CowQuoteDeps(),
    select_extremes=None,
    build_market_state=None,
) -> dict:
    select_extremes = select_extremes or select_binance_market_extremes
    build_market_state = build_market_state or build_binance_market_state
    selected_extremes = select_extremes(extremes, side_limit=side_limit)
    market_state = build_market_state(
        selected_extremes,
        aave_symbols=assets,
        arbitrage_config=arbitrage_config,
        top_limit=side_limit,
        bottom_limit=side_limit,
        pair_side_limit=pair_side_limit,
        cow_display_limit=DEFAULT_BINANCE_RAW_SIDE_LIMIT,
        slippage_bps=slippage_bps,
        cow_network=token_cache["network"],
        min_spread_percent=float(thresholds["adjusted_min_spread_percent"]),
        min_side_change_percent=float(thresholds["min_side_change_percent"]),
        min_token_price_usd=float(thresholds["min_token_price_usd"]),
        threshold_detail=thresholds,
        registry=token_cache["registry"],
    )
    market_state["cow_filter"]["token_cache_source"] = token_cache["source"]
    market_state["cow_filter"]["token_cache_count"] = token_cache["token_count"]
    market_state["cow_filter"]["cow_display_limit"] = DEFAULT_BINANCE_RAW_SIDE_LIMIT
    if not market_state.get("pairs"):
        network_claims = deps.build_cow_network_claims(
            selected_extremes,
            {token_cache["network"]: token_cache},
            limit=5,
            min_spread_percent=float(thresholds["adjusted_min_spread_percent"]),
            min_side_change_percent=float(thresholds["min_side_change_percent"]),
            min_token_price_usd=float(thresholds["min_token_price_usd"]),
            threshold_detail=thresholds,
        )
        fallback_pairs = [
            pair
            for claim in network_claims
            if claim.get("network") == token_cache["network"]
            for pair in deps.build_cow_market_claim_pairs(
                claim,
                include_below_min_spread=True,
                max_pairs=1,
            )
        ]
        if fallback_pairs:
            market_state["pairs"] = fallback_pairs
            market_state["pair_count"] = len(fallback_pairs)
            market_state["quote_candidate_source"] = "cow_network_claim_top_bottom"
    market_state["pool_evidence_recording"] = record_pool_evidence(
        market_state,
        extremes=selected_extremes,
    )
    return market_state


def build_cow_quote_verification_payload(
    market_state: dict,
    *,
    amount: str,
    quote_limit: int,
    owner: str | None,
    cow_network: str,
    quote_timeout_seconds: float,
    token_cache: dict,
    deps: CowQuoteDeps = CowQuoteDeps(),
    build_quote_verification=None,
) -> dict:
    verifier = build_quote_verification or deps.build_cow_quote_verification
    return verifier(
        market_state,
        amount=amount,
        quote_limit=quote_limit,
        owner=owner,
        cow_network=cow_network,
        quote_timeout_seconds=quote_timeout_seconds,
        registry=token_cache["registry"],
    )


def apply_cow_quote_execution_payload(
    payload: dict,
    *,
    market_state: dict,
    pause_guard: dict,
    database_url,
    submit_orders,
    record_execution_attempts,
) -> dict:
    if pause_guard.get("paused"):
        for item in payload.get("ranking") or []:
            if not isinstance(item, dict):
                continue
            precheck = dict(item.get("execution_precheck") or {})
            if not precheck.get("checks_passed"):
                continue
            reasons = list(precheck.get("reasons") or [])
            if "cow_submission_paused" not in reasons:
                reasons.append("cow_submission_paused")
            precheck.update(
                {
                    "status": "submission_paused",
                    "checks_passed": True,
                    "can_submit_order": False,
                    "auto_execute_blocked": True,
                    "submission_attempted": False,
                    "submission_status": "submission_paused",
                    "submission_pause_guard": pause_guard,
                    "reasons": reasons,
                }
            )
            item["execution_precheck"] = precheck
            sdk_result = dict(item.get("cow_sdk_result") or {})
            sdk_result.update(
                {
                    "status": "submission_paused",
                    "submitted": False,
                    "blocked_reason": "cow_submission_paused",
                    "pause_guard": pause_guard,
                }
            )
            item["cow_sdk_result"] = sdk_result
        payload.update(
            {
                "status": "submission_paused",
                "blocked_reason": "cow_submission_paused",
                "pause_guard": pause_guard,
            }
        )
        payload["history_recording"] = {
            "recorded": 0,
            "source": "paused",
            "error": None,
            "pause_guard": pause_guard,
        }
        return payload
    payload = submit_orders(payload, market_state=market_state)
    payload["history_recording"] = record_execution_attempts(
        payload,
        market_state=market_state,
        database_url=database_url,
    )
    return payload


def build_cow_quote_response(payload: dict, market_state: dict) -> dict:
    # Keep the historical `quotes` key as the public response contract;
    # ranking remains available for callers that need execution details.
    response_payload = {"market_state": market_state, **payload}
    response_payload.setdefault("quotes", response_payload.get("ranking") or [])
    return response_payload


__all__ = [
    "build_cow_quote_market_state",
    "build_cow_quote_verification_payload",
    "apply_cow_quote_execution_payload",
    "build_cow_quote_response",
]
