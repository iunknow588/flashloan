from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import time
from typing import Any

from core.chain_data_registry import chain_data_by_chain_id
from core.db.storage_cow_execution import load_recent_cow_execution_attempts_jsonl
from core.error_registry import ErrorCode
from dex.runtime.cow_attempt_log import cow_execution_attempt_log_path
from dex.runtime.dex_quote_evidence_helpers import dex_error_stack_from_state
from dex.runtime.dex_onchain_quote_evidence import _complete_route_steps, _pool_map


def _structured_dex_error(message: object, *, flow: str, node_id: str) -> list[dict[str, Any]]:
    return [
        {
            "module": "DEX",
            "code": int(ErrorCode.DEX_INTERNAL),
            "flow": flow,
            "nodeId": node_id,
            "message": str(message or "")[:500],
        }
    ]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


TERMINAL_DEPTH_STATUSES = {
    "missing_effective_pool",
    "depth_insufficient",
    "pool_not_initialized",
    "not_enough_liquidity",
    "pool_insufficient_for_profitable_execution",
    "rpc_verification_failed",
    "rpc_verification_timeout",
}


def _evidence_has_cache_source(evidence: dict[str, Any]) -> bool:
    """Return True when an evidence payload originates from a cache source."""
    if not isinstance(evidence, dict):
        return False
    markers = (
        "cache",
        "token_balance_scan_cache",
        "v4_pool_liquidity_cache",
        "poolkey_liquidity_cache",
    )

    def _has_marker(value: object) -> bool:
        return any(marker in str(value or "").lower() for marker in markers)

    for key in ("source", "priceEvidenceSource", "quoteBlockSource", "liquiditySource"):
        if _has_marker(evidence.get(key)):
            return True
    for hop in evidence.get("hops") or []:
        if not isinstance(hop, dict):
            continue
        for key in ("source", "quoteSource", "liveQuoteError"):
            if _has_marker(hop.get(key)):
                return True
        pool_state = hop.get("poolState")
        if isinstance(pool_state, dict):
            for key in ("source", "liquiditySource"):
                if _has_marker(pool_state.get(key)):
                    return True
    return False


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
        "auto_executable_closed_loop_count": int(summary.get("autoExecutableClosedLoopCount") or 0),
        "best_live_net_profit_usd": summary.get("bestLiveNetProfitUsd"),
    }


def _route_evidence(route: dict[str, Any]) -> dict[str, Any]:
    evidence = route.get("live_onchain_evidence")
    if isinstance(evidence, dict) and evidence:
        return evidence
    analysis = route.get("quoteAnalysis") if isinstance(route.get("quoteAnalysis"), dict) else {}
    evidence = analysis.get("live_onchain_evidence")
    return evidence if isinstance(evidence, dict) else {}


def _evidence_ready_for_auto_execute(row: dict[str, Any]) -> bool:
    """Return whether a route evidence row satisfies the auto-execute gates."""
    if not row:
        return False
    if str(row.get("status") or "").strip().lower() != "live_verified":
        return False
    if not bool(row.get("readyForTransactionPreflight")):
        return False
    costs = row.get("costModel") if isinstance(row.get("costModel"), dict) else {}
    if not (bool(row.get("bestAmountConfirmed")) or bool(costs.get("bestAmountConfirmed"))):
        return False
    profitable = bool(row.get("profitable")) or bool((row.get("validation") or {}).get("profitableAfterCosts"))
    if not profitable:
        net_profit = costs.get("netProfitUsd")
        if net_profit is None:
            net_profit = costs.get("netProfitAmount")
        try:
            profitable = float(net_profit or 0) > 0
        except (TypeError, ValueError):
            profitable = False
    return profitable


def _route_addresses_from_route(route: dict[str, Any]) -> list[str]:
    for key in ("routeAddresses", "route_addresses", "addresses"):
        value = route.get(key)
        if isinstance(value, list):
            addresses = [str(item or "").strip().lower() for item in value if str(item or "").strip()]
            if addresses:
                return addresses
    return []


def _route_evidence_dedup_key(route: dict[str, Any]) -> str:
    addresses = _route_addresses_from_route(route)
    if addresses:
        return "addr|" + "|".join(addresses)
    raw_path = route.get("routeSymbols") or route.get("symbolPair") or route.get("path") or []
    if isinstance(raw_path, str):
        raw_path = [part for part in raw_path.split(" -> ") if str(part).strip()]
    parts = []
    for item in raw_path or []:
        s = str(item).strip().upper()
        if not s:
            continue
        if s.startswith("0X") and len(s) < 20:
            continue
        parts.append(s)
    return "symbol|" + " -> ".join(parts)


def _recent_attempt_max_age_seconds() -> float:
    raw = os.getenv("DEX_RECENT_ATTEMPT_MAX_AGE_SECONDS", "600")
    try:
        return max(0.0, float(raw))
    except (TypeError, ValueError):
        return 600.0


def _attempt_timestamp(attempt: dict[str, Any]) -> float | None:
    raw = str(attempt.get("created_at") or attempt.get("observed_at") or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def _attempt_route_evidence(attempt: dict[str, Any]) -> dict[str, Any]:
    quote = attempt.get("quote") if isinstance(attempt.get("quote"), dict) else {}
    candidates = (
        quote.get("live_onchain_evidence"),
        attempt.get("live_onchain_evidence"),
        attempt.get("evidence"),
    )
    for candidate in candidates:
        if isinstance(candidate, dict) and candidate:
            return candidate
    return {}


def _attempt_has_submitted_transaction(attempt: dict[str, Any]) -> bool:
    for key in ("tx_hash", "transaction_hash", "txHash", "transactionHash"):
        if str(attempt.get(key) or "").strip():
            return True
    quote = attempt.get("quote") if isinstance(attempt.get("quote"), dict) else {}
    for key in ("tx_hash", "transaction_hash", "txHash", "transactionHash"):
        if str(quote.get(key) or "").strip():
            return True
    return False


def merge_recent_manual_review_evidence_into_routes(
    routes: list[dict[str, Any]],
    *,
    chain_id: int,
    recent_attempt_limit: int = 500,
    max_age_seconds: float | None = None,
) -> list[dict[str, Any]]:
    """Fold recent manual quote-route test evidence into scanner routes.

    Submitted/executed attempts are intentionally excluded so a manually
    executed opportunity is not offered again by the scanner as an executable
    candidate. The route keeps fresh live evidence only for manual reviews
    that have not produced a transaction yet.
    """
    if not routes or chain_id is None:
        return routes
    try:
        log_path = cow_execution_attempt_log_path(chain_id=int(chain_id))
        attempts = load_recent_cow_execution_attempts_jsonl(
            log_path,
            limit=max(1, int(recent_attempt_limit)),
            chain_ids=[int(chain_id)],
        )
    except Exception:
        return routes
    latest_evidence_by_route = {}
    max_age = _recent_attempt_max_age_seconds() if max_age_seconds is None else float(max_age_seconds)
    now = time.time()
    for attempt in attempts:
        if not isinstance(attempt, dict):
            continue
        attempt_ts = _attempt_timestamp(attempt)
        if attempt_ts is None or now - attempt_ts > max_age:
            continue
        if _attempt_has_submitted_transaction(attempt):
            continue
        quote = attempt.get("quote") if isinstance(attempt.get("quote"), dict) else {}
        raw_path = attempt.get("route_path") or quote.get("path") or attempt.get("path") or []
        evidence = _attempt_route_evidence(attempt)
        if not isinstance(evidence, dict) or not evidence.get("status"):
            continue
        route_key = _route_evidence_dedup_key(
            {
                "routeAddresses": evidence.get("routeAddresses") or evidence.get("route_addresses") or [],
                "routeSymbols": raw_path,
            }
        )
        if not route_key:
            continue
        latest_evidence_by_route.setdefault(route_key, evidence)
    for route in routes:
        if not isinstance(route, dict):
            continue
        route_key = _route_evidence_dedup_key(route)
        evidence = latest_evidence_by_route.get(route_key)
        if not isinstance(evidence, dict) or not evidence:
            continue
        existing_evidence = route.get("live_onchain_evidence")
        existing_status = (
            str(existing_evidence.get("status") or "")
            if isinstance(existing_evidence, dict)
            else ""
        ).strip().lower()
        attempt_status = str(evidence.get("status") or "").strip().lower()
        if attempt_status != "live_verified":
            continue
        if existing_status == "live_verified":
            continue
        _merge_onchain_evidence_into_route(route, evidence)
    return routes


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
            "route_unprofitable",
            "slippage_limit_exceeded",
            "pool_not_initialized",
            "not_enough_liquidity",
            "pool_insufficient_for_profitable_execution",
            "missing_effective_pool",
            "depth_insufficient",
            "rpc_verification_failed",
            "rpc_verification_timeout",
            "live_quote_reference_only",
        }
        for status in statuses
    )
    deferred_count = sum(status == "live_evidence_deferred" for status in statuses)
    not_reviewed_count = sum(status == "not_reviewed_this_scan" for status in statuses)
    pending_reference_count = sum(
        status in {"static_reference_pending_live_review"}
        for status in statuses
    )
    failed_count = sum(status in {"live_quote_failed", "rpc_verification_failed", "rpc_verification_timeout"} for status in statuses)
    unreviewed_count = sum(not evidence for evidence in evidence_rows)
    ready_count = sum(bool(row.get("readyForTransactionPreflight")) for row in evidence_rows)
    auto_executable_closed_loop_count = sum(_evidence_ready_for_auto_execute(row) for row in evidence_rows)
    adapter_count = sum(
        status in {"", "live_evidence_deferred"}
        and bool((route.get("quoteAnalysis") if isinstance(route.get("quoteAnalysis"), dict) else {}).get("adapterRequired"))
        for route, status in zip(routes, statuses)
    )
    live_profits = []
    live_profitable_count = 0
    for row in evidence_rows:
        if str(row.get("status") or "").strip().lower() != "live_verified":
            continue
        costs = row.get("costModel") if isinstance(row.get("costModel"), dict) else {}
        profitable = bool(row.get("profitable")) or bool((row.get("validation") or {}).get("profitableAfterCosts"))
        net_profit_amount = None
        try:
            net_profit_amount = float(costs.get("netProfitAmount"))
        except (TypeError, ValueError):
            net_profit_amount = None
        if not profitable:
            try:
                profitable = float(costs.get("netProfitUsd") or net_profit_amount or 0) > 0
            except (TypeError, ValueError):
                profitable = False
        if profitable:
            live_profitable_count += 1
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
        summary["onchainEvidenceNotReviewedRouteCount"] = not_reviewed_count
        summary["onchainEvidenceFailedRouteCount"] = failed_count
        summary["onchainEvidencePendingRouteCount"] = deferred_count + not_reviewed_count + unreviewed_count + pending_reference_count
        summary["liveQuoterRequiredCount"] = deferred_count + not_reviewed_count + unreviewed_count + pending_reference_count
        summary["executableCandidateCount"] = ready_count
        summary["chainSubmissionCandidateCount"] = ready_count
        summary["autoExecutableClosedLoopCount"] = auto_executable_closed_loop_count
        summary["sdkAdapterRequiredCount"] = adapter_count
        summary["liveVerifiedRouteCount"] = sum(status == "live_verified" for status in statuses)
        summary["liveProfitableRouteCount"] = live_profitable_count
        summary["bestLiveNetProfitUsd"] = max(live_profits) if live_profits else None
        summary["profitableRouteCount"] = summary["liveProfitableRouteCount"]
        summary["bestNetProfitUsd"] = summary["bestLiveNetProfitUsd"]
    summary["autoExecutableClosedLoopCount"] = auto_executable_closed_loop_count
    return summary


def _number_or_none(value: object) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if parsed != parsed or parsed in (float("inf"), float("-inf")):
        return None
    return parsed


def _hop_amount_continuity_issues(hops: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return hop-by-hop amount mismatches that break routed value continuity."""
    issues: list[dict[str, Any]] = []
    for index in range(1, len(hops)):
        previous = hops[index - 1]
        current = hops[index]
        previous_symbol = str(previous.get("toSymbol") or "").strip().upper()
        current_symbol = str(current.get("fromSymbol") or "").strip().upper()
        previous_output = _number_or_none(previous.get("outputAmount") if _number_or_none(previous.get("outputAmount")) is not None else previous.get("amountOut"))
        current_input = _number_or_none(current.get("inputAmount") if _number_or_none(current.get("inputAmount")) is not None else current.get("amountIn"))
        if previous_symbol and current_symbol and previous_symbol != current_symbol:
            issues.append(
                {
                    "kind": "symbol",
                    "fromHop": previous.get("hop") or index,
                    "toHop": current.get("hop") or index + 1,
                    "fromSymbol": previous_symbol,
                    "toSymbol": current_symbol,
                    "message": f"hop {previous.get('hop') or index} output symbol {previous_symbol} does not match hop {current.get('hop') or index + 1} input symbol {current_symbol}",
                }
            )
        if previous_output is None or current_input is None:
            continue
        tolerance = max(abs(previous_output), abs(current_input), 1.0) * 1e-9
        if abs(previous_output - current_input) > tolerance:
            issues.append(
                {
                    "kind": "amount",
                    "fromHop": previous.get("hop") or index,
                    "toHop": current.get("hop") or index + 1,
                    "fromOutput": previous_output,
                    "toInput": current_input,
                    "message": f"hop {previous.get('hop') or index} output {previous_output} does not equal hop {current.get('hop') or index + 1} input {current_input}",
                }
            )
    return issues


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
    # The production scanner passes None and reviews every eligible route.
    # An explicit max_routes value remains available for isolated callers and
    # tests; it is not an environment-controlled production review budget.
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
    terminal_depth_status = evidence_status in TERMINAL_DEPTH_STATUSES
    live_percent = _live_profit_percent(evidence)
    quote.update(
        {
            "live_onchain_evidence": evidence,
            "quote_verified": evidence_status == "live_verified",
            "quote_required": not ready_for_preflight and not terminal_depth_status,
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
            "quote_required": not ready_for_preflight and not terminal_depth_status,
            "requires_live_quoter": not ready_for_preflight and not terminal_depth_status,
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
    if _evidence_has_cache_source(evidence):
        status = "static_reference_pending_live_review"
        evidence = {**evidence, "status": status, "executionTrust": "static_reference_pending_live_review"}
    live_hops = evidence.get("hops") if isinstance(evidence.get("hops"), list) else []
    has_live_hops = bool(live_hops)
    costs = evidence.get("costModel") if isinstance(evidence.get("costModel"), dict) else {}
    has_trusted_live_amounts = bool(
        has_live_hops
        or evidence.get("bestAmountConfirmed")
        or costs.get("bestAmountConfirmed")
    )
    if status == "live_verified" and not has_trusted_live_amounts:
        status = "static_reference_pending_live_review"
        evidence = {**evidence, "status": status, "executionTrust": "static_reference_pending_live_review"}
    live_verified = bool(evidence.get("livePriceVerified")) or status == "live_verified"
    is_deferred = status in {"live_evidence_deferred", "not_reviewed_this_scan"}
    static_reference_status = status == "static_reference_pending_live_review"
    terminal_depth_status = status in TERMINAL_DEPTH_STATUSES
    validation = evidence.get("validation") if isinstance(evidence.get("validation"), dict) else {}
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
        "v4_token_decimals_missing_requires_metadata",
    }
    has_live_evidence = bool(evidence.get("hops")) or (status and status not in {"live_evidence_deferred", "not_reviewed_this_scan"})
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

    cost_final_output_amount = costs.get("finalOutputAmount")
    cost_net_profit_amount = costs.get("netProfitAmount")
    cost_net_profit_bps = costs.get("netProfitAmountBps")
    cost_borrow_input_amount = (
        costs.get("selectedBorrowInputAmount")
        if _number_or_none(costs.get("selectedBorrowInputAmount")) is not None
        else costs.get("borrowInputAmount")
        if _number_or_none(costs.get("borrowInputAmount")) is not None
        else costs.get("requestedBorrowInputAmount")
    )
    reference_input_amount = (
        cost_borrow_input_amount
        if has_trusted_live_amounts and _number_or_none(cost_borrow_input_amount) is not None
        else (analysis.get("referenceInputAmount") if static_reference_status or is_deferred else None)
    )
    reference_final_output_amount = (
        cost_final_output_amount
        if has_trusted_live_amounts and _number_or_none(cost_final_output_amount) is not None
        else (analysis.get("referenceFinalOutputAmount") if static_reference_status or is_deferred else None)
    )
    reference_final_output_symbol = (
        costs.get("finalOutputSymbol") or validation.get("finalOutputSymbol")
        if reference_final_output_amount is not None and has_trusted_live_amounts
        else (analysis.get("referenceFinalOutputSymbol") if static_reference_status or is_deferred else None)
    )
    reference_net_profit_amount = (
        cost_net_profit_amount
        if has_trusted_live_amounts and _number_or_none(cost_net_profit_amount) is not None
        else (analysis.get("referenceNetProfitAmount") if static_reference_status or is_deferred else None)
    )
    reference_net_return_bps = (
        cost_net_profit_bps
        if has_trusted_live_amounts and _number_or_none(cost_net_profit_bps) is not None
        else (analysis.get("referenceNetReturnBps") if static_reference_status or is_deferred else None)
    )
    reference_input_symbol = (
        costs.get("borrowInputSymbol") or analysis.get("referenceInputSymbol")
        if reference_input_amount is not None
        else analysis.get("referenceInputSymbol")
    )
    reference_final_output_usd = costs.get("finalOutputUsd") if live_verified else None
    reference_gross_profit_usd = costs.get("grossProfitUsd") if live_verified else None
    reference_gross_profit_amount = costs.get("grossProfitAmount") if live_verified else None
    selected_amount_reason = str(costs.get("selectedBorrowInputAmountReason") or "").lower()
    selected_amount_confirmed_max = bool(costs.get("selectedBorrowInputAmountConfirmedMax"))
    unconfirmed_fixed_amount = bool(
        not selected_amount_confirmed_max
        and selected_amount_reason in {"requested_amount_fallback", "onchain_execution_size_probe_confirmed_profitable_lower_bound"}
    )
    if costs.get("bestAmountMissing") is True or unconfirmed_fixed_amount:
        best_amount = None
    else:
        best_amount = costs.get("selectedBorrowInputAmount")
        if _number_or_none(best_amount) is None:
            best_amount = costs.get("borrowInputAmount")
        if _number_or_none(best_amount) is None:
            best_amount = None
    best_amount_confirmed = bool(evidence.get("bestAmountConfirmed")) or bool(costs.get("bestAmountConfirmed"))
    best_amount_unconfirmed = bool(
        costs.get("bestAmountMissing") is True
        or unconfirmed_fixed_amount
        or best_amount is None
        or not best_amount_confirmed
    )
    if status == "live_verified" and best_amount_unconfirmed:
        status = "static_reference_pending_live_review"
        evidence = {
            **evidence,
            "status": status,
            "executionTrust": "static_reference_pending_live_review",
            "readyForTransactionPreflight": False,
        }
        live_verified = False
    ready_for_preflight_candidate = bool(
        evidence.get("readyForTransactionPreflight")
        and (live_verified or has_trusted_live_amounts)
        and not best_amount_unconfirmed
    )
    profitable_from_reference = reference_net_profit_amount is not None and float(reference_net_profit_amount) > 0
    analysis.update(
        {
            "live_onchain_evidence": deepcopy(evidence),
            "onchainEvidenceStatus": status,
            "livePriceVerified": live_verified,
            "priceEvidenceMode": evidence.get("priceEvidenceMode") or ("live_verified" if live_verified else ("live_required" if not is_deferred else "static_reference_pending_live_review")),
            "priceEvidenceSource": evidence.get("priceEvidenceSource") or ("chain_live_quote" if live_verified else ("chain_live_quote_required" if not is_deferred else "static_reference_pending_live_review")),
            "requiresLiveQuoter": not live_verified and not terminal_depth_status,
            "adapterRequired": False if status != "live_evidence_deferred" else bool(analysis.get("adapterRequired")),
            "quote_verified": live_verified,
            "executableCandidate": ready_for_preflight_candidate,
            "chainSubmissionCandidate": ready_for_preflight_candidate,
            "bestAmountMissing": best_amount_unconfirmed,
            "bestAmountConfirmed": bool(evidence.get("bestAmountConfirmed")) or bool(costs.get("bestAmountConfirmed")),
            "finalOutputUsd": costs.get("finalOutputUsd") if live_verified else None,
            "grossProfitUsd": costs.get("grossProfitUsd") if live_verified else None,
            "netProfitUsd": costs.get("netProfitUsd") if live_verified else None,
            "netProfitBps": costs.get("netProfitBps") if live_verified else None,
            "referenceInputAmount": reference_input_amount,
            "referenceFinalOutputAmount": reference_final_output_amount,
            "referenceFinalOutputUsd": reference_final_output_usd,
            "referenceFinalOutputSymbol": reference_final_output_symbol,
            "referenceGrossProfitUsd": reference_gross_profit_usd,
            "referenceGrossProfitAmount": reference_gross_profit_amount,
            "referenceNetProfitAmount": reference_net_profit_amount,
            "referenceNetReturnBps": reference_net_return_bps,
            "referenceInputSymbol": reference_input_symbol,
            "referenceReturnBasis": costs.get("profitBasis") or analysis.get("referenceReturnBasis"),
            "referenceReturnTrusted": live_verified,
            "profitable": profitable_from_reference if not has_trusted_live_amounts else bool(evidence.get("profitable") or validation.get("profitableAfterCosts")),
            "conclusion": evidence.get("conclusion"),
        }
    )
    evidence_recommendation = evidence.get("recommendation") if isinstance(evidence.get("recommendation"), dict) else {}
    if evidence_recommendation:
        analysis["recommendation"] = deepcopy(evidence_recommendation)
        analysis["recommendation"]["reasons"] = blockers or analysis["recommendation"].get("reasons") or []
    elif blockers:
        analysis["recommendation"] = {
            "level": status,
            "action": (
                "historic block state unavailable; deferred on-chain reverify"
                if status == "live_evidence_deferred"
                else "on-chain reverify complete; missing effective trading pool"
                if status in {"missing_effective_pool", "pool_not_initialized", "not_enough_liquidity", "pool_insufficient_for_profitable_execution", "depth_insufficient"}
                else "on-chain step-by-step reverify failed; blocking transaction preflight"
                if not live_verified
                else "on-chain reverify complete; current state does not satisfy transaction submission conditions"
            ),
            "reasons": blockers,
        }
    if best_amount_unconfirmed:
        recommendation = analysis.get("recommendation") or {}
        recommendation["readyForTransactionPreflight"] = False
        recommendation["blocked"] = True
        recommendation["action"] = "on-chain best amount unconfirmed; cannot enter transaction preflight"
        analysis["recommendation"] = recommendation

    static_steps = analysis.get("steps") if isinstance(analysis.get("steps"), list) else []
    route_symbols = [str(item) for item in route.get("routeSymbols") or [] if str(item).strip()]
    route_addresses = [str(item) for item in route.get("routeAddresses") or [] if str(item).strip()]
    if static_steps:
        static_steps = _complete_route_steps(
            route_symbols,
            route_addresses,
            static_steps,
            _pool_map(route),
        )
    live_direction_symbols: list[str] = []
    live_direction_addresses: list[str] = []
    for hop in live_hops:
        if not isinstance(hop, dict):
            continue
        from_symbol = str(hop.get("fromSymbol") or "").strip().upper()
        to_symbol = str(hop.get("toSymbol") or "").strip().upper()
        if from_symbol and (not live_direction_symbols or live_direction_symbols[-1] != from_symbol):
            live_direction_symbols.append(from_symbol)
        if to_symbol:
            live_direction_symbols.append(to_symbol)
        from_address = str(hop.get("tokenIn") or hop.get("token_in") or hop.get("fromAddress") or "").strip().lower()
        to_address = str(hop.get("tokenOut") or hop.get("token_out") or hop.get("toAddress") or "").strip().lower()
        if from_address and (not live_direction_addresses or live_direction_addresses[-1] != from_address):
            live_direction_addresses.append(from_address)
        if to_address:
            live_direction_addresses.append(to_address)
    if len(live_direction_symbols) >= 2:
        route["routeSymbols"] = live_direction_symbols
        route["symbolPair"] = " -> ".join(live_direction_symbols)
        analysis["routeSymbols"] = live_direction_symbols
        evidence["routeSymbols"] = live_direction_symbols
    if len(live_direction_addresses) == len(live_direction_symbols):
        route["routeAddresses"] = live_direction_addresses
        analysis["routeAddresses"] = live_direction_addresses
        evidence["routeAddresses"] = live_direction_addresses
    if live_direction_symbols or evidence.get("directionConfirmed"):
        analysis["directionConfirmed"] = True
        analysis["direction"] = " -> ".join(live_direction_symbols or route_symbols)
    analysis["bestAmount"] = best_amount
    analysis["bestAmountSymbol"] = costs.get("borrowInputSymbol") or analysis.get("referenceInputSymbol")
    if live_hops or static_steps:
        merged_steps = []
        live_by_hop: dict[int, dict[str, Any]] = {}
        live_tail: list[dict[str, Any]] = []
        for hop in live_hops:
            if not isinstance(hop, dict):
                continue
            hop_number = int(hop.get("hop") or len(live_by_hop) + 1)
            live_by_hop[hop_number] = hop
        previous_output_amount: float | None = None
        previous_output_symbol: str | None = None
        previous_output_text: str | None = None
        for index, base in enumerate(static_steps, start=1):
            if not isinstance(base, dict):
                continue
            merged = deepcopy(base)
            hop_number = int(merged.get("hop") or index)
            live = live_by_hop.get(hop_number)
            if isinstance(live, dict):
                live_amount_in = live.get("amountIn")
                if live_amount_in not in (None, ""):
                    try:
                        live_amount_in = float(live_amount_in)
                    except (TypeError, ValueError):
                        live_amount_in = None
                if live_amount_in in (None, ""):
                    if live.get("amountInRaw") is not None:
                        decimals = live.get("inputDecimals")
                        if decimals in (None, ""):
                            decimals = merged.get("inputDecimals")
                        if decimals in (None, ""):
                            live_amount_in = None
                        else:
                            try:
                                live_amount_in = float(int(live["amountInRaw"]) / (10 ** int(decimals)))
                            except (TypeError, ValueError):
                                live_amount_in = None
                if live_amount_in in (None, "") and previous_output_symbol and previous_output_symbol == str(merged.get("fromSymbol") or "").upper():
                    live_amount_in = previous_output_amount
                if live.get("amountIn") in (None, "") and live_amount_in is not None:
                    live["amountIn"] = (
                        previous_output_text
                        if previous_output_text is not None
                        and live_amount_in == previous_output_amount
                        else str(live_amount_in)
                    )
                if live.get("inputAmount") in (None, "") and live_amount_in is not None:
                    live["inputAmount"] = live_amount_in
                merged.update(
                    {
                        "hop": live.get("hop", merged.get("hop", index)),
                        "fromSymbol": live.get("fromSymbol", merged.get("fromSymbol")),
                        "toSymbol": live.get("toSymbol", merged.get("toSymbol")),
                        "protocol": live.get("protocol", merged.get("protocol")),
                        "pool": live.get("pool", merged.get("pool")),
                        "quoteSource": live.get("quoteSource", merged.get("quoteSource")),
                        "executionTrust": live.get("executionTrust", merged.get("executionTrust")),
                        "liveQuoteError": live.get("liveQuoteError"),
                        "inputAmount": live_amount_in if live_amount_in is not None else merged.get("inputAmount"),
                        "outputAmount": live.get("amountOut") if live.get("amountOut") is not None else merged.get("outputAmount", merged.get("referenceOutputAmount")),
                        "outputUsd": base.get("outputUsd") if live.get("amountOutRaw") is None else live.get("outputUsd", base.get("outputUsd")),
                        "effectiveRate": live.get("effectiveRate") if live.get("amountOutRaw") is not None else merged.get("effectiveRate", merged.get("referenceEffectiveRate")),
                        "priceImpactBps": live.get("priceImpactBps", merged.get("priceImpactBps")),
                    }
                )
                if live.get("amountOut") not in (None, ""):
                    previous_output_amount = float(live["amountOut"])
                    previous_output_symbol = str(live.get("toSymbol") or "").upper()
                    previous_output_text = str(live["amountOut"])
                elif live.get("amountOutRaw") is not None:
                    output_decimals = live.get("outputDecimals")
                    if output_decimals in (None, ""):
                        output_decimals = merged.get("outputDecimals")
                    if output_decimals in (None, ""):
                        previous_output_amount = None
                    else:
                        try:
                            previous_output_amount = float(int(live["amountOutRaw"]) / (10 ** int(output_decimals)))
                        except (TypeError, ValueError):
                            previous_output_amount = None
                    previous_output_symbol = str(live.get("toSymbol") or "").upper()
                    if previous_output_amount is not None:
                        try:
                            previous_output_text = _human_from_raw(
                                int(live["amountOutRaw"]),
                                int(output_decimals),
                            )
                        except (TypeError, ValueError):
                            previous_output_text = None
                    else:
                        previous_output_text = None
            merged_steps.append(merged)
            live_by_hop.pop(hop_number, None)
        for live in live_by_hop.values():
            if isinstance(live, dict):
                merged_steps.append(deepcopy(live))
        analysis["steps"] = merged_steps
        amount_issues = _hop_amount_continuity_issues(merged_steps)
        if amount_issues:
            blockers.append("on-chain quote hop amounts are inconsistent; cannot enter transaction preflight")
            for issue in amount_issues:
                blockers.append(str(issue.get("message") or "hop amount continuity mismatch"))
            analysis.setdefault("warnings", [])
            analysis["warnings"].append("hop_amount_continuity_mismatch")

    if blockers and not isinstance(analysis.get("recommendation"), dict):
        analysis["recommendation"] = {
            "level": status,
            "action": (
                "on-chain verified but net profit is not positive after flash loan fees; do not execute"
                if status == "route_unprofitable"
                else "on-chain quote failed; route cannot enter transaction preflight"
            ),
            "reasons": blockers,
            "profitable": False,
            "readyForTransactionPreflight": False,
            "blocked": True,
        }
    elif blockers and isinstance(analysis.get("recommendation"), dict):
        reasons = list(analysis["recommendation"].get("reasons") or [])
        for blocker in blockers:
            if blocker not in reasons:
                reasons.append(blocker)
        analysis["recommendation"]["reasons"] = reasons

    analysis["blockers"] = list(blockers)

    verification = dict(route.get("quoteVerification") or {})
    verification.update(
        {
            "status": status,
            "livePriceVerified": live_verified,
            "requiresLiveQuoter": not live_verified and not terminal_depth_status,
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
    route["errorStack"] = deepcopy(evidence.get("errorStack") or [])
    route["priceEvidenceSource"] = analysis["priceEvidenceSource"]
    route["priceEvidenceMode"] = analysis["priceEvidenceMode"]
    return route

