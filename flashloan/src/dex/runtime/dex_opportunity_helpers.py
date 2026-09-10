from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from typing import Any

from core.chain_data_registry import chain_data_by_chain_id
from dex.runtime.dex_quote_evidence_helpers import dex_error_stack_from_state

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _chain_key(chain_id: int | None) -> str:
    return str(int(chain_id)) if chain_id is not None else "default"


def _network_for_chain_id(chain_id: int | None) -> str:
    if chain_id is None:
        return "dex"
    row = chain_data_by_chain_id(int(chain_id))
    return str(row.get("network") or f"chain_{chain_id}") if row else f"chain_{chain_id}"


def _env_float(name: str, default: float, *, minimum: float | None = None, maximum: float | None = None) -> float:
    try:
        value = float(os.getenv(name, str(default)) or default)
    except (TypeError, ValueError):
        value = float(default)
    if minimum is not None:
        value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value


def _snapshot_summary(payload: dict[str, Any] | None) -> dict[str, Any]:
    summary = payload.get("summary") if isinstance(payload, dict) and isinstance(payload.get("summary"), dict) else {}
    routes = payload.get("routes") if isinstance(payload, dict) and isinstance(payload.get("routes"), list) else []
    return {
        "route_count": int(summary.get("routeCount") or len(routes) or 0),
        "complete_static_quote_count": int(summary.get("completeStaticQuoteCount") or 0),
        "executable_candidate_count": int(summary.get("executableCandidateCount") or 0),
        "profitable_route_count": int(summary.get("profitableRouteCount") or 0),
        "chain_submission_candidate_count": int(summary.get("chainSubmissionCandidateCount") or 0),
        "non_stable_chain_submission_candidate_count": int(summary.get("nonStableChainSubmissionCandidateCount") or 0),
        "live_quoter_required_count": int(summary.get("liveQuoterRequiredCount") or 0),
        "sdk_adapter_required_count": int(summary.get("sdkAdapterRequiredCount") or 0),
        "uniswapx_quoteable_route_count": int(summary.get("uniswapxQuoteableRouteCount") or 0),
        "best_net_profit_usd": summary.get("bestNetProfitUsd"),
        "best_observed_net_profit_usd": summary.get("bestObservedNetProfitUsd"),
        "static_live_quoter_required_count": int(summary.get("staticLiveQuoterRequiredCount") or 0),
        "static_sdk_adapter_required_count": int(summary.get("staticSdkAdapterRequiredCount") or 0),
        "onchain_evidence_reviewed_route_count": int(summary.get("onchainEvidenceReviewedRouteCount") or 0),
        "onchain_evidence_deferred_route_count": int(summary.get("onchainEvidenceDeferredRouteCount") or 0),
        "onchain_evidence_failed_route_count": int(summary.get("onchainEvidenceFailedRouteCount") or 0),
        "live_verified_route_count": int(summary.get("liveVerifiedRouteCount") or 0),
        "live_profitable_route_count": int(summary.get("liveProfitableRouteCount") or 0),
        "best_live_net_profit_usd": summary.get("bestLiveNetProfitUsd"),
    }


def _route_evidence(route: dict[str, Any]) -> dict[str, Any]:
    evidence = route.get("live_onchain_evidence")
    if isinstance(evidence, dict) and evidence:
        return evidence
    analysis = route.get("quoteAnalysis") if isinstance(route.get("quoteAnalysis"), dict) else {}
    evidence = analysis.get("live_onchain_evidence")
    return evidence if isinstance(evidence, dict) else {}


def _refresh_payload_summary(payload: dict[str, Any]) -> dict[str, Any]:
    """Reconcile the payload summary after live evidence mutates its routes."""
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    payload["summary"] = summary
    routes = [route for route in payload.get("routes") or [] if isinstance(route, dict)]
    evidence_rows = [_route_evidence(route) for route in routes]
    statuses = [str(row.get("status") or "").strip().lower() for row in evidence_rows]
    has_evidence = any(bool(row) for row in evidence_rows)
    reviewed_count = sum(
        status
        in {
            "live_verified",
            "live_quote_failed",
            "live_quote_reference_only",
            "route_unprofitable",
            "slippage_limit_exceeded",
            "pool_insufficient_for_profitable_execution",
        }
        for status in statuses
    )
    deferred_count = sum(status == "live_evidence_deferred" for status in statuses)
    failed_count = sum(status == "live_quote_failed" for status in statuses)
    unreviewed_count = sum(not evidence for evidence in evidence_rows)
    ready_count = sum(bool(row.get("readyForTransactionPreflight")) for row in evidence_rows)
    adapter_count = sum(
        status in {"", "live_evidence_deferred"}
        and bool((route.get("quoteAnalysis") if isinstance(route.get("quoteAnalysis"), dict) else {}).get("adapterRequired"))
        for route, status in zip(routes, statuses)
    )
    live_profits = []
    for row in evidence_rows:
        if str(row.get("status") or "").strip().lower() != "live_verified":
            continue
        costs = row.get("costModel") if isinstance(row.get("costModel"), dict) else {}
        try:
            value = float(costs.get("netProfitUsd"))
        except (TypeError, ValueError):
            continue
        if value == value and value not in (float("inf"), float("-inf")):
            live_profits.append(value)

    if has_evidence:
        summary.setdefault("staticLiveQuoterRequiredCount", summary.get("liveQuoterRequiredCount"))
        summary.setdefault("staticProfitableRouteCount", summary.get("profitableRouteCount"))
        summary.setdefault("staticBestNetProfitUsd", summary.get("bestNetProfitUsd"))
        summary.setdefault("staticBestObservedNetProfitUsd", summary.get("bestObservedNetProfitUsd"))
        summary.setdefault("staticSdkAdapterRequiredCount", summary.get("sdkAdapterRequiredCount"))
        summary["onchainEvidenceReviewedRouteCount"] = reviewed_count
        summary["onchainEvidenceDeferredRouteCount"] = deferred_count
        summary["onchainEvidenceFailedRouteCount"] = failed_count
        summary["onchainEvidencePendingRouteCount"] = deferred_count + unreviewed_count
        summary["liveQuoterRequiredCount"] = deferred_count + unreviewed_count
        summary["executableCandidateCount"] = ready_count
        summary["chainSubmissionCandidateCount"] = ready_count
        summary["sdkAdapterRequiredCount"] = adapter_count
        summary["liveVerifiedRouteCount"] = sum(status == "live_verified" for status in statuses)
        summary["liveProfitableRouteCount"] = sum(value > 0 for value in live_profits)
        summary["bestLiveNetProfitUsd"] = max(live_profits) if live_profits else None
        summary["profitableRouteCount"] = summary["liveProfitableRouteCount"]
        summary["bestNetProfitUsd"] = summary["bestLiveNetProfitUsd"]
    return summary


def _number_or_none(value: object) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if parsed != parsed or parsed in (float("inf"), float("-inf")):
        return None
    return parsed


def _chain_network(payload: dict[str, Any] | None, chain_id: int | None) -> str:
    table = payload.get("poolStaticTable") if isinstance(payload, dict) and isinstance(payload.get("poolStaticTable"), dict) else {}
    network = str(table.get("network") or "").strip().lower()
    if network:
        return network
    return _network_for_chain_id(chain_id)


def _trigger_block_number(trigger_context: dict[str, Any] | None) -> int | None:
    if not isinstance(trigger_context, dict):
        return None
    for key in ("triggerBlockNumber", "blockNumber", "trigger_block_number", "block_number"):
        try:
            parsed = int(trigger_context.get(key) or 0)
        except (TypeError, ValueError):
            continue
        if parsed > 0:
            return parsed
    return None


def _recordable_route(route: dict[str, Any], *, chain_id: int | None = None) -> bool:
    analysis = route.get("quoteAnalysis") if isinstance(route.get("quoteAnalysis"), dict) else {}
    quote_requests = (route.get("quoteInputs") or {}).get("quoteRequests") or []
    route_symbols = [str(item).strip() for item in route.get("routeSymbols") or [] if str(item).strip()]
    robinhood_network = _network_for_chain_id(chain_id).lower()
    if (
        robinhood_network in {"robinhood", "robinhood-mainnet", "robinhood-testnet", "robinhood_testnet"}
        and len(route_symbols) >= 2
        and route_symbols[0].upper() == route_symbols[-1].upper()
        and quote_requests
    ):
        # The overview owns the closed-loop universe. Every route must be
        # sent to the live reviewer even when its offline probe is negative.
        return True
    reference_profit = _number_or_none(analysis.get("referenceNetProfitUsd"))
    reference_token_profit = _number_or_none(analysis.get("referenceNetProfitAmount"))
    live_verified = bool(analysis.get("livePriceVerified")) or str(analysis.get("priceEvidenceMode") or "").strip().lower() == "live_verified"
    quote_verified = bool(analysis.get("quote_verified"))
    requires_live_quoter = bool(analysis.get("requiresLiveQuoter")) or bool(analysis.get("adapterRequired"))
    adapter_required = any(
        str((request or {}).get("venue") or "").strip().lower() == "uniswap_v4"
        and str((request or {}).get("status") or "").strip().lower() == "sdk_adapter_required"
        for request in quote_requests
        if isinstance(request, dict)
    )
    return bool(
        live_verified
        or quote_verified
        or requires_live_quoter
        or adapter_required
        or (reference_profit is not None and reference_profit > 0)
        or (reference_token_profit is not None and reference_token_profit > 0)
        or (analysis.get("executableCandidate") and not requires_live_quoter and not adapter_required)
        or (analysis.get("chainSubmissionCandidate") and not requires_live_quoter and not adapter_required)
    )


def _route_priority_key(route: dict[str, Any], *, original_index: int) -> tuple[float, float, float, int, str]:
    analysis = route.get("quoteAnalysis") if isinstance(route.get("quoteAnalysis"), dict) else {}
    chain_submission_candidate = bool(analysis.get("chainSubmissionCandidate"))
    executable_candidate = bool(analysis.get("executableCandidate"))
    profitable = bool(analysis.get("profitable"))
    if chain_submission_candidate:
        tier = 0.0
    elif executable_candidate:
        tier = 1.0
    elif profitable:
        tier = 2.0
    else:
        tier = 3.0
    reference_net_profit = _number_or_none(analysis.get("referenceNetProfitUsd"))
    if reference_net_profit is None:
        reference_net_profit = _number_or_none(analysis.get("referenceNetProfitAmount"))
    reference_net_return_bps = _number_or_none(analysis.get("referenceNetReturnBps"))
    if reference_net_return_bps is None:
        reference_net_return_bps = _number_or_none(analysis.get("netProfitBps"))
    return (
        tier,
        -(reference_net_profit or 0.0),
        -(reference_net_return_bps or 0.0),
        original_index,
        str(route.get("symbolPair") or ""),
    )


def _select_recordable_routes(
    routes: list[tuple[int, dict[str, Any], dict[str, Any]]],
    *,
    max_routes: int | None,
    testnet_full_scan: bool = False,
) -> list[tuple[int, dict[str, Any], dict[str, Any]]]:
    selected = list(routes)
    if not testnet_full_scan and max_routes is not None and max_routes >= 0:
        selected.sort(key=lambda item: _route_priority_key(item[1], original_index=item[0]))
        selected = selected[:max_routes]
        selected.sort(key=lambda item: item[0])
    return selected


def _route_blocked_reasons(route: dict[str, Any]) -> list[str]:
    analysis = route.get("quoteAnalysis") if isinstance(route.get("quoteAnalysis"), dict) else {}
    recommendation = analysis.get("recommendation") if isinstance(analysis.get("recommendation"), dict) else {}
    reasons = []
    for item in [
        route.get("quoteStatus"),
        recommendation.get("level"),
        *(recommendation.get("reasons") or []),
        *((route.get("quoteInputs") or {}).get("conditions") or []),
    ]:
        text = str(item or "").strip()
        if text and text not in reasons:
            reasons.append(text)
    return reasons or ["dex_opportunity_requires_quote_review"]


def _route_attempt(
    route: dict[str, Any],
    *,
    payload: dict[str, Any],
    chain_id: int | None,
    pair_rank: int,
    observed_at: str,
    trigger_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    analysis = route.get("quoteAnalysis") if isinstance(route.get("quoteAnalysis"), dict) else {}
    recommendation = analysis.get("recommendation") if isinstance(analysis.get("recommendation"), dict) else {}
    route_symbols = [str(item) for item in route.get("routeSymbols") or [] if str(item).strip()]
    route_addresses = [str(item) for item in route.get("routeAddresses") or [] if str(item).strip()]
    route_token_path = route.get("routeTokenPath") if isinstance(route.get("routeTokenPath"), list) else []
    quote_status = str(route.get("quoteStatus") or recommendation.get("level") or "quote_required")
    final_amount = analysis.get("finalOutputUsd")
    final_delta = analysis.get("netProfitUsd")
    reference_final_amount = analysis.get("referenceFinalOutputUsd")
    reference_final_delta = analysis.get("referenceNetProfitUsd")
    reference_token_final_amount = analysis.get("referenceFinalOutputAmount")
    reference_token_delta = analysis.get("referenceNetProfitAmount")
    reasons = _route_blocked_reasons(route)
    quote_inputs = route.get("quoteInputs") if isinstance(route.get("quoteInputs"), dict) else {}
    quote = {
        "pair": route.get("symbolPair"),
        "pair_rank": pair_rank,
        "priority_reason": recommendation.get("level") or quote_status,
        "path": route_symbols,
        "routeAddresses": route_addresses,
        "route_addresses": route_addresses,
        "routeTokenPath": route_token_path,
        "route_token_path": route_token_path,
        "input_amount": analysis.get("notionalUsd"),
        "input_symbol": analysis.get("inputSymbol") or (route_symbols[0] if route_symbols else None),
        "final_amount": final_amount,
        "final_symbol": analysis.get("outputSymbol") or (route_symbols[-1] if route_symbols else None),
        "final_delta_amount": final_delta,
        "reference_final_amount": reference_final_amount,
        "reference_final_delta_amount": reference_final_delta,
        "reference_token_final_amount": reference_token_final_amount,
        "reference_token_delta_amount": reference_token_delta,
        "reference_token_return_bps": analysis.get("referenceNetReturnBps"),
        "chain_submission_candidate": bool(analysis.get("chainSubmissionCandidate")),
        "chain_submission_policy": analysis.get("chainSubmissionPolicy"),
        "quote_verified": False,
        "quote_required": True,
        "viable": bool(analysis.get("executableCandidate")),
        "candidate_basis": "dex_opportunity_scanner",
        "trigger_source": "dex_quote_routes_scan",
        "trigger_context": trigger_context or {},
        "route": route.get("overviewRoute") or {},
        "quote_analysis": analysis,
        "candidate_pools": quote_inputs.get("candidatePools") or [],
        "quote_requests": quote_inputs.get("quoteRequests") or [],
        "hops": analysis.get("steps") or [],
    }
    precheck = {
        "status": quote_status,
        "checks_passed": bool(analysis.get("executableCandidate")),
        "can_submit_order": False,
        "order_submission_enabled": False,
        "auto_execute_requested": False,
        "quote_required": True,
        "estimation_available": bool(analysis.get("complete")),
        "requires_live_quoter": bool(analysis.get("requiresLiveQuoter")),
        "profit_amount": final_delta,
        "pure_profit_amount": final_delta,
        "final_delta_amount": final_delta,
        "reference_profit_amount": reference_final_delta,
        "reference_pure_profit_amount": reference_final_delta,
        "reference_token_profit_amount": reference_token_delta,
        "reference_token_return_bps": analysis.get("referenceNetReturnBps"),
        "chain_submission_candidate": bool(analysis.get("chainSubmissionCandidate")),
        "chain_submission_policy": analysis.get("chainSubmissionPolicy"),
        "final_symbol": quote.get("final_symbol"),
        "reasons": reasons,
    }
    return {
        "observed_at": observed_at,
        "network": _chain_network(payload, chain_id),
        "chain_id": chain_id,
        "owner_address": None,
        "pair": route.get("symbolPair"),
        "pair_rank": pair_rank,
        "priority_reason": recommendation.get("level") or quote_status,
        "route_path": route_symbols,
        "route_addresses": route_addresses,
        "route_token_path": route_token_path,
        "state": "quote_required",
        "execution_phase": "market_candidate",
        "checks_passed": False,
        "can_submit_order": False,
        "order_submission_enabled": False,
        "auto_execute_requested": False,
        "final_delta_amount": final_delta,
        "reference_token_delta_amount": reference_token_delta,
        "chain_submission_candidate": bool(analysis.get("chainSubmissionCandidate")),
        "chain_submission_policy": analysis.get("chainSubmissionPolicy"),
        "final_symbol": quote.get("final_symbol"),
        "blocked_reasons": reasons,
        "errorStack": dex_error_stack_from_state(
            quote_status,
            message=reasons[0] if reasons else "dex_opportunity_requires_quote_review",
            details={"routeKey": " -> ".join(route_symbols), "pair": route.get("symbolPair")},
            flow="dex_opportunity",
        ),
        "quote": quote,
        "precheck": precheck,
        "market_state": {
            "observed_at": observed_at,
            "window_seconds": None,
            "price_source": "dex_static_quote_probe",
            "market_state_source": "dex_opportunity_scanner",
            "trigger_context": trigger_context or {},
            "trigger_block_number": (trigger_context or {}).get("triggerBlockNumber") or (trigger_context or {}).get("blockNumber"),
            "trigger_observed_at": (trigger_context or {}).get("observedAt") or observed_at,
            "fallback_reason": None,
            "cow_filter": {
                "network": _chain_network(payload, chain_id),
                "chain_id": chain_id,
                "source": "dex_opportunity_scanner",
                "quote_status": quote_status,
                "probe_notional_usd": analysis.get("notionalUsd"),
            },
        },
        "error": None,
    }


def _live_profit_percent(evidence: dict[str, Any]) -> float | None:
    costs = evidence.get("costModel") if isinstance(evidence.get("costModel"), dict) else {}
    try:
        bps = float(costs.get("netProfitBps"))
    except (TypeError, ValueError):
        return None
    if bps != bps or bps in (float("inf"), float("-inf")):
        return None
    return bps / 100.0


def _apply_onchain_evidence_to_attempt(attempt: dict[str, Any], evidence: dict[str, Any]) -> dict[str, Any]:
    quote = attempt.get("quote") if isinstance(attempt.get("quote"), dict) else {}
    precheck = attempt.get("precheck") if isinstance(attempt.get("precheck"), dict) else {}
    market_state = attempt.get("market_state") if isinstance(attempt.get("market_state"), dict) else {}
    evidence_status = str(evidence.get("status") or "not_live_verified")
    trusted = bool(evidence.get("trustedForExecution"))
    ready_for_preflight = bool(evidence.get("readyForTransactionPreflight"))
    blockers = [str(item) for item in evidence.get("blockers") or [] if str(item).strip()]
    costs = evidence.get("costModel") if isinstance(evidence.get("costModel"), dict) else {}
    if ready_for_preflight and not blockers:
        blockers = ["on-chain Quoter already reverified as profitable; still need transaction calldata, slippage protection, flash loan return constraints, and fork/static-call simulation before submitting"]
    live_percent = _live_profit_percent(evidence)
    quote.update(
        {
            "live_onchain_evidence": evidence,
            "quote_verified": evidence_status == "live_verified",
            "quote_required": not ready_for_preflight,
            "viable": False,
            "readonly_viable": ready_for_preflight,
            "live_profit_percent": live_percent,
            "live_profit_bps": (live_percent * 100.0 if live_percent is not None else None),
            "live_profit_basis": costs.get("profitBasis"),
            "live_token_profit_amount": costs.get("netProfitAmount"),
            "live_token_profit_symbol": costs.get("netProfitAmountSymbol"),
            "transaction_development": evidence.get("transactionDevelopment") or {},
        }
    )
    precheck.update(
        {
            "status": "transaction_development_required" if ready_for_preflight else evidence_status,
            "checks_passed": False,
            "can_submit_order": False,
            "quote_required": not ready_for_preflight,
            "requires_live_quoter": not ready_for_preflight,
            "live_onchain_evidence_status": evidence_status,
            "ready_for_transaction_preflight": ready_for_preflight,
            "live_profit_percent": live_percent,
            "live_profit_basis": costs.get("profitBasis"),
            "live_token_profit_amount": costs.get("netProfitAmount"),
            "live_token_profit_symbol": costs.get("netProfitAmountSymbol"),
            "transaction_development": evidence.get("transactionDevelopment") or {},
            "reasons": blockers or precheck.get("reasons") or [],
        }
    )
    market_state.update(
        {
            "price_source": "dex_live_onchain_quote" if evidence_status == "live_verified" else "dex_static_quote_probe",
            "onchain_evidence_status": evidence_status,
            "onchain_block_number": evidence.get("blockNumber"),
            "onchain_observed_at": evidence.get("observedAt"),
        }
    )
    attempt["quote"] = quote
    attempt["precheck"] = precheck
    attempt["market_state"] = market_state
    attempt["blocked_reasons"] = blockers or attempt.get("blocked_reasons") or []
    if ready_for_preflight:
        live_delta = costs.get("netProfitUsd")
        if live_delta is None:
            live_delta = costs.get("netProfitAmount")
        attempt["state"] = "ready_for_transaction_development"
        attempt["priority_reason"] = "live_quote_verified_preflight_required"
        attempt["checks_passed"] = False
        attempt["final_delta_amount"] = live_delta
        attempt["profit_basis"] = costs.get("profitBasis")
        attempt["final_symbol"] = (evidence.get("validation") or {}).get("finalOutputSymbol") if isinstance(evidence.get("validation"), dict) else attempt.get("final_symbol")
    else:
        attempt["state"] = "quote_required"
        attempt["priority_reason"] = evidence_status
        attempt["checks_passed"] = False
    return attempt


def _merge_onchain_evidence_into_route(route: dict[str, Any], evidence: dict[str, Any]) -> dict[str, Any]:
    """Keep the scan snapshot route aligned with the evidence used for attempts."""
    analysis = route.get("quoteAnalysis") if isinstance(route.get("quoteAnalysis"), dict) else {}
    analysis = deepcopy(analysis)
    status = str(evidence.get("status") or "not_live_verified")
    live_verified = bool(evidence.get("livePriceVerified")) or status == "live_verified"
    validation = evidence.get("validation") if isinstance(evidence.get("validation"), dict) else {}
    costs = evidence.get("costModel") if isinstance(evidence.get("costModel"), dict) else {}
    blockers = [str(item) for item in evidence.get("blockers") or [] if str(item).strip()]

    # Once real on-chain Quoter/eth_call evidence has been produced, static
    # warnings about “requires live quoter” are stale noise for the user.
    stale_static_warnings = {
        "hop_1_reference_quote_requires_live_quoter",
        "hop_2_reference_quote_requires_live_quoter",
        "hop_3_reference_quote_requires_live_quoter",
        "hop_4_reference_quote_requires_live_quoter",
        "hop_1_requires_live_quoter",
        "hop_2_requires_live_quoter",
        "hop_3_requires_live_quoter",
        "hop_4_requires_live_quoter",
        "reference_price_step_requires_live_quoter",
        "reference_usd_price_missing_or_untrusted",
        "static_depth_evidence_unavailable",
        "robinhood_live_price_only_enforced",
        "v4_token_decimals_missing_or_assumed",
    }
    has_live_evidence = bool(evidence.get("hops")) or (status and status != "live_evidence_deferred")
    if has_live_evidence:
        analysis["warnings"] = [
            warning
            for warning in (analysis.get("warnings") or [])
            if not (
                isinstance(warning, str)
                and any(
                    warning == code or warning.startswith(f"{code}:")
                    for code in stale_static_warnings
                )
            )
        ]

    analysis.update(
        {
            "live_onchain_evidence": deepcopy(evidence),
            "onchainEvidenceStatus": status,
            "livePriceVerified": live_verified,
            "priceEvidenceMode": evidence.get("priceEvidenceMode") or ("live_verified" if live_verified else "live_required"),
            "priceEvidenceSource": evidence.get("priceEvidenceSource") or ("chain_live_quote" if live_verified else "chain_live_quote_required"),
            "requiresLiveQuoter": not live_verified,
            "adapterRequired": False if status != "live_evidence_deferred" else bool(analysis.get("adapterRequired")),
            "quote_verified": live_verified,
            "executableCandidate": bool(evidence.get("readyForTransactionPreflight")),
            "chainSubmissionCandidate": bool(evidence.get("readyForTransactionPreflight")),
            "finalOutputUsd": costs.get("finalOutputUsd") if live_verified else None,
            "grossProfitUsd": costs.get("grossProfitUsd") if live_verified else None,
            "netProfitUsd": costs.get("netProfitUsd") if live_verified else None,
            "netProfitBps": costs.get("netProfitBps") if live_verified else None,
            "referenceFinalOutputAmount": costs.get("finalOutputAmount") if live_verified else None,
            "referenceFinalOutputSymbol": costs.get("finalOutputSymbol") or validation.get("finalOutputSymbol"),
            "referenceNetProfitAmount": costs.get("netProfitAmount") if live_verified else None,
            "referenceNetReturnBps": costs.get("netProfitAmountBps") if live_verified else None,
            "referenceInputSymbol": costs.get("borrowInputSymbol") or analysis.get("referenceInputSymbol"),
            "referenceReturnBasis": costs.get("profitBasis") or analysis.get("referenceReturnBasis"),
            "referenceReturnTrusted": live_verified,
        }
    )
    if blockers:
        analysis["recommendation"] = {
            "level": status,
            "action": (
                "historic block state unavailable; deferred on-chain reverify"
                if status == "live_evidence_deferred"
                else "on-chain step-by-step reverify failed; blocking transaction preflight"
                if not live_verified
                else "on-chain reverify complete; current state does not satisfy transaction submission conditions"
            ),
            "reasons": blockers,
        }

    static_steps = analysis.get("steps") if isinstance(analysis.get("steps"), list) else []
    live_hops = evidence.get("hops") if isinstance(evidence.get("hops"), list) else []
    if live_hops:
        merged_steps = []
        for index, hop in enumerate(live_hops):
            base = deepcopy(static_steps[index]) if index < len(static_steps) and isinstance(static_steps[index], dict) else {}
            if isinstance(hop, dict):
                base.update(
                    {
                        "hop": hop.get("hop", base.get("hop", index + 1)),
                        "fromSymbol": hop.get("fromSymbol", base.get("fromSymbol")),
                        "toSymbol": hop.get("toSymbol", base.get("toSymbol")),
                        "protocol": hop.get("protocol", base.get("protocol")),
                        "pool": hop.get("pool", base.get("pool")),
                        "quoteSource": hop.get("quoteSource", base.get("quoteSource")),
                        "executionTrust": hop.get("executionTrust", base.get("executionTrust")),
                        "liveQuoteError": hop.get("liveQuoteError"),
                        "inputAmount": hop.get("amountIn", base.get("inputAmount")),
                        "outputAmount": hop.get("amountOut") if hop.get("amountOut") is not None else None,
                        "outputUsd": None if hop.get("amountOutRaw") is None else base.get("outputUsd"),
                        "effectiveRate": None if hop.get("amountOutRaw") is None else base.get("effectiveRate"),
                    }
                )
            merged_steps.append(base)
        analysis["steps"] = merged_steps

    verification = dict(route.get("quoteVerification") or {})
    verification.update(
        {
            "status": status,
            "livePriceVerified": live_verified,
            "requiresLiveQuoter": not live_verified,
            "adapterRequired": False if status != "live_evidence_deferred" else bool(verification.get("adapterRequired")),
            "executableCandidate": bool(evidence.get("readyForTransactionPreflight")),
            "chainSubmissionCandidate": bool(evidence.get("readyForTransactionPreflight")),
            "trustedForExecution": bool(evidence.get("trustedForExecution")),
            "executionTrust": evidence.get("executionTrust"),
            "readyForTransactionPreflight": bool(evidence.get("readyForTransactionPreflight")),
            "blockNumber": evidence.get("blockNumber"),
            "triggerBlockNumber": evidence.get("triggerBlockNumber"),
            "blockers": blockers,
        }
    )
    route["quoteAnalysis"] = analysis
    route["live_onchain_evidence"] = deepcopy(evidence)
    route["onchainEvidenceStatus"] = status
    route["quoteVerification"] = verification
    route["quoteStatus"] = status
    route["priceEvidenceSource"] = analysis["priceEvidenceSource"]
    route["priceEvidenceMode"] = analysis["priceEvidenceMode"]
    return route

