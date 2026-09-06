from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import threading
import time
from typing import Any, Callable

from core.chain_data_registry import chain_data_by_chain_id
from core.env_loader import local_json_only_enabled
from market.binance_market.dex_surfaces import dex_overview_payload, dex_quote_routes_payload
from dex.runtime.dex_onchain_quote_evidence import build_route_onchain_evidence, capture_onchain_trigger_context


QuotePayloadProvider = Callable[..., dict[str, Any]]
RecordAttempts = Callable[[list[dict[str, Any]]], dict[str, Any]]
OnchainEvidenceProvider = Callable[..., dict[str, Any]]
TriggerContextProvider = Callable[..., dict[str, Any]]

CACHE_PATH = Path(__file__).resolve().parent / "cache" / "dex_opportunity_snapshot.json"
_SCANNER: DexOpportunityScanner | None = None
_SCANNER_LOCK = threading.Lock()
logger = logging.getLogger(__name__)


def default_quote_payload_provider(*, chain_id: int | None = None, protocols: list[str] | None = None) -> dict[str, Any]:
    return dex_quote_routes_payload(
        chain_id=chain_id,
        protocols=protocols,
        use_static_observation=False,
    )


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
        blockers = ["链上只读 Quoter 已复核为正收益；仍需完成交易 calldata、滑点保护、闪电贷回款约束和 fork/static-call 仿真后才能提交。"]
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
                "历史区块状态不可用，已延后链上复核"
                if status == "live_evidence_deferred"
                else "链上逐跳复核失败，禁止进入交易预检"
                if not live_verified
                else "链上复核完成，当前未满足交易提交条件"
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


def build_dex_opportunity_attempts(
    payload: dict[str, Any],
    *,
    chain_id: int | None,
    observed_at: str,
    onchain_evidence_provider: OnchainEvidenceProvider | None = None,
    max_onchain_evidence_routes: int | None = None,
    max_recordable_routes: int | None = 5,
    onchain_evidence_offset: int = 0,
    trigger_context: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    raw_attempts = []
    raw_routes = []
    recordable_routes: list[tuple[int, dict[str, Any], dict[str, Any]]] = []
    for index, route in enumerate(payload.get("routes") or [], start=1):
        if isinstance(route, dict) and _recordable_route(route, chain_id=chain_id):
            route_with_trigger = deepcopy(route)
            quote_inputs = route_with_trigger.get("quoteInputs") if isinstance(route_with_trigger.get("quoteInputs"), dict) else {}
            route_with_trigger["quoteInputs"] = {**quote_inputs, "triggerContext": trigger_context or {}}
            recordable_routes.append(
                (
                    index,
                    route_with_trigger,
                    _route_attempt(
                        route_with_trigger,
                        payload=payload,
                        chain_id=chain_id,
                        pair_rank=index,
                        observed_at=observed_at,
                        trigger_context=trigger_context,
                    ),
                )
            )
    if not recordable_routes:
        return []
    testnet_full_scan = _chain_network(payload, chain_id) == "robinhood-testnet"
    selected_routes = _select_recordable_routes(
        recordable_routes,
        max_routes=max_recordable_routes,
        testnet_full_scan=testnet_full_scan,
    )
    raw_routes = [route for _, route, _ in selected_routes]
    raw_attempts = [attempt for _, _, attempt in selected_routes]
    payload_routes = payload.get("routes") if isinstance(payload.get("routes"), list) else []
    evidence_provider = onchain_evidence_provider or build_route_onchain_evidence
    # Keep the testnet candidate universe broad, but bound live RPC work per scan.
    # A full static candidate scan must not turn into an unbounded Quoter scan.
    budget = (
        len(raw_attempts)
        if max_onchain_evidence_routes is None
        else max(0, min(len(raw_attempts), int(max_onchain_evidence_routes)))
    )
    selected_indexes: set[int] = set()
    if budget:
        start = int(onchain_evidence_offset or 0) % len(raw_attempts)
        selected_indexes = {(start + offset) % len(raw_attempts) for offset in range(budget)}
    attempts = []
    deferred_reason = "本轮扫描达到链上复核上限，已延后到下一轮空闲扫描。"
    for attempt_index, (original_index, route, attempt) in enumerate(selected_routes):
        if attempt_index in selected_indexes:
            evidence = evidence_provider(route, payload=payload, chain_id=chain_id, observed_at=observed_at, trigger_context=trigger_context or {})
            _merge_onchain_evidence_into_route(route, evidence)
            attempts.append(_apply_onchain_evidence_to_attempt(attempt, evidence))
        else:
            evidence = {
                "evidenceVersion": 1,
                "observedAt": observed_at,
                "network": _chain_network(payload, chain_id),
                "chainId": chain_id,
                "status": "live_evidence_deferred",
                "executionTrust": "static_reference_only",
                "trustedForExecution": False,
                "blockNumber": None,
                "triggerContext": trigger_context or {},
                "triggerBlockNumber": (trigger_context or {}).get("triggerBlockNumber") or (trigger_context or {}).get("blockNumber"),
                "quoteBlockSource": "deferred",
                "routeSymbols": route.get("routeSymbols") or [],
                "routeAddresses": route.get("routeAddresses") or [],
                "hops": [],
                "costModel": {},
                "blockers": [deferred_reason],
                "transactionDevelopment": {
                    "status": "blocked_before_transaction_development",
                    "executionEnabled": False,
                    "blockers": [deferred_reason],
                },
            }
            _merge_onchain_evidence_into_route(route, evidence)
            attempts.append(_apply_onchain_evidence_to_attempt(attempt, evidence))
        if 0 < original_index <= len(payload_routes) and isinstance(payload_routes[original_index - 1], dict):
            payload_routes[original_index - 1].clear()
            payload_routes[original_index - 1].update(deepcopy(route))
    return attempts


def default_record_attempts(attempts: list[dict[str, Any]]) -> dict[str, Any]:
    from dex.runtime.cow_attempt_log import record_cow_attempt_list_safely

    return record_cow_attempt_list_safely(
        attempts,
        database_url=None,
        dedupe_market_candidates=False,
    )


class DexOpportunityScanner:
    def __init__(
        self,
        *,
        interval_seconds: float = 180.0,
        record_cooldown_seconds: float = 60.0,
        quote_payload_provider: QuotePayloadProvider | None = None,
        record_attempts: RecordAttempts | None = None,
        onchain_evidence_provider: OnchainEvidenceProvider | None = None,
        trigger_context_provider: TriggerContextProvider | None = None,
        cache_path: Path | None = CACHE_PATH,
        record_opportunities: bool = True,
        max_onchain_evidence_routes: int = 5,
        max_recordable_routes: int = 5,
        testnet_max_onchain_evidence_routes: int | None = None,
    ) -> None:
        self.interval_seconds = max(0.5, float(interval_seconds))
        self.record_cooldown_seconds = max(0.0, float(record_cooldown_seconds))
        self.quote_payload_provider = quote_payload_provider or default_quote_payload_provider
        self.record_attempts = record_attempts or default_record_attempts
        self.onchain_evidence_provider = onchain_evidence_provider or build_route_onchain_evidence
        self.trigger_context_provider = trigger_context_provider or capture_onchain_trigger_context
        self.cache_path = cache_path
        self.record_opportunities = bool(record_opportunities)
        self.max_onchain_evidence_routes = max(0, int(max_onchain_evidence_routes))
        self.max_recordable_routes = max(0, int(max_recordable_routes))
        self.testnet_max_onchain_evidence_routes = (
            None
            if testnet_max_onchain_evidence_routes is None
            else max(0, int(testnet_max_onchain_evidence_routes))
        )
        self._evidence_cursor: dict[str, int] = {}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._scan_lock = threading.Lock()
        self._watched: dict[str, tuple[int | None, tuple[str, ...]]] = {}
        self._snapshots: dict[str, dict[str, Any]] = {}
        self._recorded_signatures: dict[tuple[Any, ...], float] = {}
        self._last_recording: dict[str, Any] = {"recorded": 0, "source": "empty", "error": None}
        self._recorded_attempt_count = 0
        self._last_error: str | None = None
        self._scan_count = 0
        self._last_scan_started_at: str | None = None
        self._last_scan_finished_at: str | None = None
        self._last_duration_ms: int | None = None

    def start(self, *, chain_id: int | None = None, protocols: list[str] | None = None) -> None:
        self.watch(chain_id=chain_id, protocols=protocols)
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(target=self.run_forever, name="dex-opportunity-scanner", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def watch(self, *, chain_id: int | None = None, protocols: list[str] | None = None) -> None:
        normalized_protocols = tuple(str(item).strip().lower() for item in (protocols or []) if str(item).strip())
        with self._lock:
            self._watched[_chain_key(chain_id)] = (chain_id, normalized_protocols)

    def status(self, *, chain_id: int | None = None) -> dict[str, Any]:
        thread = self._thread
        with self._lock:
            watched_keys = sorted(self._watched)
            snapshot = deepcopy(self._snapshots.get(_chain_key(chain_id))) if chain_id is not None else None
            scan_count = self._scan_count
            last_error = self._last_error
            last_started = self._last_scan_started_at
            last_finished = self._last_scan_finished_at
            last_duration = self._last_duration_ms
            last_recording = deepcopy(self._last_recording)
            recorded_attempt_count = self._recorded_attempt_count
        return {
            "running": bool(thread and thread.is_alive()),
            "interval_seconds": self.interval_seconds,
            "record_cooldown_seconds": self.record_cooldown_seconds,
            "max_onchain_evidence_routes": self.max_onchain_evidence_routes,
            "testnet_max_onchain_evidence_routes": self.testnet_max_onchain_evidence_routes,
            "watched_chain_keys": watched_keys,
            "chain_id": chain_id,
            "scan_count": scan_count,
            "recorded_attempt_count": recorded_attempt_count,
            "last_recording": last_recording,
            "last_error": last_error,
            "last_scan_started_at": last_started,
            "last_scan_finished_at": last_finished,
            "last_duration_ms": last_duration,
            "snapshot_updated_at": snapshot.get("updated_at") if snapshot else None,
        }

    def snapshot(self, *, chain_id: int | None = None) -> dict[str, Any]:
        key = _chain_key(chain_id)
        with self._lock:
            snapshot = deepcopy(self._snapshots.get(key))
        if snapshot is None:
            snapshot = {
                "chain_id": chain_id,
                "payload": None,
                "summary": _snapshot_summary(None),
                "updated_at": None,
                "error": None,
            }
        return {"ok": True, "scanner": self.status(chain_id=chain_id), "snapshot": snapshot}

    def run_forever(self) -> None:
        while not self._stop.is_set():
            watched = self._watched_items()
            for chain_id, protocols in watched:
                if self._stop.is_set():
                    break
                self._scan_one(chain_id=chain_id, protocols=list(protocols))
            self._stop.wait(self.interval_seconds)

    def scan_now(self, *, chain_id: int | None = None, protocols: list[str] | None = None) -> dict[str, Any]:
        self.watch(chain_id=chain_id, protocols=protocols)
        self._scan_one(chain_id=chain_id, protocols=protocols or [])
        return self.snapshot(chain_id=chain_id)

    def _watched_items(self) -> list[tuple[int | None, tuple[str, ...]]]:
        with self._lock:
            return list(self._watched.values())

    def _scan_one(self, *, chain_id: int | None, protocols: list[str]) -> None:
        started_at = _now_iso()
        started = time.perf_counter()
        key = _chain_key(chain_id)
        trigger_context = self.trigger_context_provider(
            chain_id=chain_id,
            network=_network_for_chain_id(chain_id),
            observed_at=started_at,
        )
        if _trigger_block_number(trigger_context) is None and _network_for_chain_id(chain_id) == "robinhood-testnet":
            try:
                from dex.intent_trade.chains.robinhood_testnet.trigger_context import capture_robinhood_testnet_trigger_context

                fallback_trigger_context = capture_robinhood_testnet_trigger_context(
                    chain_id=chain_id,
                    network="robinhood-testnet",
                    observed_at=started_at,
                )
                if _trigger_block_number(fallback_trigger_context) is not None:
                    trigger_context = fallback_trigger_context
            except Exception:
                logger.warning(
                    "robinhood trigger-context fallback failed for chain %s",
                    chain_id,
                    exc_info=True,
                )
        with self._lock:
            self._last_scan_started_at = started_at
        with self._scan_lock:
            try:
                payload = self.quote_payload_provider(chain_id=chain_id, protocols=protocols)
                recording = self._record_opportunities(payload, chain_id=chain_id, observed_at=started_at, trigger_context=trigger_context)
                _refresh_payload_summary(payload)
                snapshot = {
                    "chain_id": chain_id,
                    "payload": payload,
                    "summary": _snapshot_summary(payload),
                    "trigger_context": trigger_context,
                    "updated_at": _now_iso(),
                    "error": None,
                    "recording": recording,
                }
                error = None
            except Exception as exc:
                payload = None
                snapshot = None
                error = str(exc)
            finished_at = _now_iso()
            duration_ms = int((time.perf_counter() - started) * 1000)
            with self._lock:
                self._scan_count += 1
                self._last_error = error
                self._last_scan_finished_at = finished_at
                self._last_duration_ms = duration_ms
                if snapshot is not None:
                    self._snapshots[key] = snapshot
                elif key not in self._snapshots:
                    self._snapshots[key] = {
                        "chain_id": chain_id,
                        "payload": payload,
                        "summary": _snapshot_summary(None),
                        "updated_at": None,
                        "error": error,
                    }
                else:
                    self._snapshots[key]["error"] = error
            self._write_cache()

    def _write_cache(self) -> None:
        if self.cache_path is None:
            return
        with self._lock:
            thread = self._thread
            scanner = {
                "running": bool(thread and thread.is_alive()),
                "interval_seconds": self.interval_seconds,
                "record_cooldown_seconds": self.record_cooldown_seconds,
                "watched_chain_keys": sorted(self._watched),
                "chain_id": None,
                "scan_count": self._scan_count,
                "recorded_attempt_count": self._recorded_attempt_count,
                "last_recording": deepcopy(self._last_recording),
                "last_error": self._last_error,
                "last_scan_started_at": self._last_scan_started_at,
                "last_scan_finished_at": self._last_scan_finished_at,
                "last_duration_ms": self._last_duration_ms,
                "snapshot_updated_at": None,
            }
            payload = {
                "updated_at": _now_iso(),
                "scanner": scanner,
                "snapshots": deepcopy(self._snapshots),
            }
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            temp_path = self.cache_path.with_suffix(self.cache_path.suffix + ".tmp")
            temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            temp_path.replace(self.cache_path)
        except Exception:
            return

    def _record_opportunities(
        self,
        payload: dict[str, Any],
        *,
        chain_id: int | None,
        observed_at: str,
        trigger_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        max_onchain_evidence_routes = self.max_onchain_evidence_routes
        if (
            self.testnet_max_onchain_evidence_routes is not None
            and _chain_network(payload, chain_id) == "robinhood-testnet"
        ):
            max_onchain_evidence_routes = self.testnet_max_onchain_evidence_routes
        attempts = build_dex_opportunity_attempts(
            payload,
            chain_id=chain_id,
            observed_at=observed_at,
            onchain_evidence_provider=self.onchain_evidence_provider,
            max_onchain_evidence_routes=max_onchain_evidence_routes,
            max_recordable_routes=self.max_recordable_routes,
            onchain_evidence_offset=self._evidence_cursor.get(_chain_key(chain_id), 0),
            trigger_context=trigger_context,
        )
        if attempts and max_onchain_evidence_routes > 0:
            self._evidence_cursor[_chain_key(chain_id)] = (
                self._evidence_cursor.get(_chain_key(chain_id), 0) + max_onchain_evidence_routes
            ) % len(attempts)
        if not attempts:
            recording = {"candidate_count": 0, "recorded": 0, "source": "empty", "error": None}
            with self._lock:
                self._last_recording = recording
            return recording
        if not self.record_opportunities:
            recording = {
                "candidate_count": len(attempts),
                "recorded": 0,
                "source": "disabled",
                "error": None,
            }
            with self._lock:
                self._last_recording = recording
            return recording
        now = time.monotonic()
        cooled_attempts = []
        with self._lock:
            for attempt in attempts:
                signature = (
                    attempt.get("network"),
                    attempt.get("chain_id"),
                    attempt.get("pair"),
                    tuple(attempt.get("route_path") or []),
                    attempt.get("priority_reason"),
                )
                last_recorded_at = self._recorded_signatures.get(signature)
                if last_recorded_at is not None and now - last_recorded_at < self.record_cooldown_seconds:
                    continue
                self._recorded_signatures[signature] = now
                cooled_attempts.append(attempt)
        recording = self.record_attempts(cooled_attempts)
        recording = {
            "candidate_count": len(attempts),
            "attempted_record_count": len(cooled_attempts),
            **(recording if isinstance(recording, dict) else {"recorded": 0, "source": "unknown", "error": None}),
        }
        with self._lock:
            self._last_recording = recording
            self._recorded_attempt_count += int(recording.get("recorded") or 0)
        return recording


def get_dex_opportunity_scanner() -> DexOpportunityScanner:
    global _SCANNER
    with _SCANNER_LOCK:
        if _SCANNER is None:
            local_only = local_json_only_enabled()
            _SCANNER = DexOpportunityScanner(
                interval_seconds=_env_float("DEX_OPPORTUNITY_SCAN_INTERVAL_SECONDS", 180.0, minimum=1.0, maximum=3600.0),
                record_cooldown_seconds=_env_float(
                    "DEX_OPPORTUNITY_RECORD_COOLDOWN_SECONDS",
                    60.0,
                    minimum=0.0,
                    maximum=3600.0,
                ),
                record_opportunities=not local_only,
                max_onchain_evidence_routes=int(
                    _env_float("DEX_OPPORTUNITY_ONCHAIN_EVIDENCE_ROUTES", 1.0, minimum=0.0, maximum=10.0)
                ),
                max_recordable_routes=int(_env_float("DEX_OPPORTUNITY_SCAN_ROUTE_LIMIT", 5.0, minimum=1.0, maximum=20.0)),
                testnet_max_onchain_evidence_routes=int(
                    _env_float("DEX_OPPORTUNITY_TESTNET_ONCHAIN_EVIDENCE_ROUTES", 4.0, minimum=0.0, maximum=10.0)
                ),
            )
        return _SCANNER


def ensure_dex_opportunity_scanner_running(
    *, chain_id: int | None = None, protocols: list[str] | None = None
) -> DexOpportunityScanner:
    scanner = get_dex_opportunity_scanner()
    scanner.start(chain_id=chain_id, protocols=protocols)
    snapshot = scanner.snapshot(chain_id=chain_id)
    if not (snapshot.get("snapshot") or {}).get("payload"):
        try:
            dex_overview_payload(
                chain_id=chain_id,
                protocols=protocols or [],
                use_static_observation=False,
            )
            payload = scanner.quote_payload_provider(chain_id=chain_id, protocols=protocols or [])
            if isinstance(payload, dict):
                warmed = {
                    "chain_id": chain_id,
                    "payload": payload,
                    "summary": _snapshot_summary(payload),
                    "trigger_context": scanner.trigger_context_provider(
                        chain_id=chain_id,
                        network=_network_for_chain_id(chain_id),
                        observed_at=_now_iso(),
                    ),
                    "updated_at": _now_iso(),
                    "error": None,
                    "recording": {"candidate_count": 0, "recorded": 0, "source": "warmup", "error": None},
                }
                with scanner._lock:
                    scanner._snapshots[_chain_key(chain_id)] = warmed
        except Exception:
            logger.warning("failed to warm DEX opportunity scanner snapshot", exc_info=True)
    return scanner


def run_dex_opportunity_scan_now(*, chain_id: int | None = None, protocols: list[str] | None = None) -> dict[str, Any]:
    scanner = get_dex_opportunity_scanner()
    scanner.watch(chain_id=chain_id, protocols=protocols)
    return scanner.scan_now(chain_id=chain_id, protocols=protocols)


def dex_opportunity_snapshot(*, chain_id: int | None = None, protocols: list[str] | None = None) -> dict[str, Any]:
    scanner = get_dex_opportunity_scanner()
    scanner.watch(chain_id=chain_id, protocols=protocols)
    return scanner.snapshot(chain_id=chain_id)


def dex_opportunity_scanner_status(*, chain_id: int | None = None) -> dict[str, Any]:
    return get_dex_opportunity_scanner().status(chain_id=chain_id)


def _reset_dex_opportunity_scanner_for_tests() -> None:
    global _SCANNER
    with _SCANNER_LOCK:
        if _SCANNER is not None:
            _SCANNER.stop()
        _SCANNER = None


__all__ = [
    "DexOpportunityScanner",
    "build_dex_opportunity_attempts",
    "dex_opportunity_scanner_status",
    "dex_opportunity_snapshot",
    "ensure_dex_opportunity_scanner_running",
    "run_dex_opportunity_scan_now",
]
