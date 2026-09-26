from __future__ import annotations

import os
from typing import Any, Callable
from urllib.parse import quote

from dex.intent_trade.chains.robinhood.shared import ROBINHOOD_CHAIN_ID
from dex.intent_trade.chains.robinhood.chain_test_policy import ROBINHOOD_TESTNET_CHAIN_ID
from market.binance_market.dex_surface_analysis import DEFAULT_QUOTE_PROBE_NOTIONAL_USD
from market.binance_market.dex_quote_route_builder import build_quote_route_payload
from market.binance_market.dex_quote_surface_helpers import (
    _enrich_route_pools_from_index,
    _framework_route_pools,
    _number,
    _static_hop_steps,
    _v4_pool_index_by_id,
)
from market.binance_market.dex_quote_surface_routes_builder import _build_quote_routes_and_best
from dex.error_stack import route_error_stack
from market.binance_market.dex_quote_route_common import (
    build_quote_analysis_defaults,
    build_quote_inputs_bundle,
    build_quote_request_specs,
    build_quote_verification_payload,
    quote_route_status,
)

def build_quote_routes_payload_from_overview(
    overview: dict[str, Any],
    *,
    chain_id: int | None,
    protocols: list[str] | None,
    route_quote_analysis_fn: Callable[..., dict[str, Any]],
    route_size_analysis_fn: Callable[..., dict[str, Any]],
    quote_probe_notional_usd: float = DEFAULT_QUOTE_PROBE_NOTIONAL_USD,
    price_map: dict[str, float] | None = None,
) -> dict[str, Any]:
    paths = [path for path in overview.get("arbitragePaths") or [] if isinstance(path, dict)]
    summary = dict(overview.get("summary") or {})
    market_observation = overview.get("marketObservation") if isinstance(overview.get("marketObservation"), dict) else {}
    settlement = overview.get("dexSettlement") if isinstance(overview.get("dexSettlement"), dict) else {}
    pool_static_table = overview.get("poolStaticTable") if isinstance(overview.get("poolStaticTable"), dict) else {}
    routes: list[dict[str, Any]] = []
    v4_index_by_pool = _v4_pool_index_by_id(chain_id)
    observed_net_profits: list[float] = []
    executable_net_profits: list[float] = []
    best_observed_notional = None
    best_observed_profit = None
    best_executable_notional = None
    best_executable_profit = None
    best_token_basis_notional = None
    best_token_basis_symbol = None
    best_token_basis_amount = None
    best_token_basis_bps = None
    best_token_basis_profit = None
    best_token_basis_profit_basis = None
    force_live_refresh = str(os.getenv("DEX_QUOTE_ROUTE_FORCE_REFRESH", "")).strip().lower() in {"1", "true", "yes", "on"}
    # Robinhood Chain/Testnet route pages are quote/review-first: run live
    # Quoter/eth_call by default so routes do not sit in "requires live quoter"
    # before the background scanner snapshot arrives. Operators can set
    # DEX_QUOTE_ROUTE_LIVE_ROUTE_LIMIT=0 to keep a static fast page.
    live_route_env = os.getenv("DEX_QUOTE_ROUTE_LIVE_ROUTE_LIMIT")
    live_route_limit = (
        max(0, int(live_route_env))
        if live_route_env not in (None, "")
    # Robinhood route pages stay responsive by default while the background
    # scanner performs the live Quoter/eth_call pass. Operators can raise this
    # limit (DEX_QUOTE_ROUTE_LIVE_ROUTE_LIMIT=999) when they want every
    # discovered route reviewed synchronously, or leave it at 0 for a static
    # fast page that consumes the scanner evidence when it is available.
    else 0
  )
    mainnet_fast_page = bool(chain_id in {ROBINHOOD_CHAIN_ID, ROBINHOOD_TESTNET_CHAIN_ID})
    size_probe_env = os.getenv("DEX_QUOTE_ROUTE_SIZE_PROBE_LIMIT")
    size_probe_limit = (
        max(0, int(size_probe_env))
        if size_probe_env not in (None, "")
        else (0 if mainnet_fast_page else 3)
    )
    probe_all_testnet_routes = (
        chain_id == ROBINHOOD_TESTNET_CHAIN_ID
        and str(os.getenv("DEX_QUOTE_ROUTE_PROBE_ALL_TESTNET_ROUTES", "")).strip().lower()
        in {"1", "true", "yes", "on"}
    )

    routes, observed_net_profits, executable_net_profits, best_routes_state = _build_quote_routes_and_best(
        paths=paths,
        overview=overview,
        chain_id=chain_id,
        route_quote_analysis_fn=route_quote_analysis_fn,
        route_size_analysis_fn=route_size_analysis_fn,
        quote_probe_notional_usd=quote_probe_notional_usd,
        price_map=price_map,
        v4_index_by_pool=v4_index_by_pool,
        settlement=settlement,
        force_live_refresh=force_live_refresh,
        live_route_limit=live_route_limit,
        size_probe_limit=size_probe_limit,
        probe_all_testnet_routes=probe_all_testnet_routes,
    )
    best_observed_notional = best_routes_state["best_observed_notional"]
    best_observed_profit = best_routes_state["best_observed_profit"]
    best_executable_notional = best_routes_state["best_executable_notional"]
    best_executable_profit = best_routes_state["best_executable_profit"]
    best_token_basis_notional = best_routes_state["best_token_basis_notional"]
    best_token_basis_symbol = best_routes_state["best_token_basis_symbol"]
    best_token_basis_amount = best_routes_state["best_token_basis_amount"]
    best_token_basis_bps = best_routes_state["best_token_basis_bps"]
    best_token_basis_profit = best_routes_state["best_token_basis_profit"]
    best_token_basis_profit_basis = best_routes_state["best_token_basis_profit_basis"]
    summary.update(
        {
            "probeNotionalUsd": quote_probe_notional_usd,
            "routeCount": len(routes),
            "candidateRouteCount": len(routes),
            "completeStaticQuoteCount": sum(1 for route in routes if (route.get("quoteAnalysis") or {}).get("complete")),
            "liveQuoterRequiredCount": sum(1 for route in routes if (route.get("quoteAnalysis") or {}).get("requiresLiveQuoter")),
            "sdkAdapterRequiredCount": sum(1 for route in routes if (route.get("quoteAnalysis") or {}).get("adapterRequired")),
            "profitableRouteCount": sum(1 for route in routes if (route.get("quoteAnalysis") or {}).get("profitable")),
            "chainSubmissionCandidateCount": sum(1 for route in routes if (route.get("quoteAnalysis") or {}).get("chainSubmissionCandidate")),
            "nonStableChainSubmissionCandidateCount": sum(
                1
                for route in routes
                if (route.get("quoteAnalysis") or {}).get("chainSubmissionCandidate")
                and not (route.get("quoteAnalysis") or {}).get("borrowAssetIsStable")
            ),
            "bestNetProfitUsd": max(executable_net_profits) if executable_net_profits else None,
            "bestObservedNetProfitUsd": max(observed_net_profits) if observed_net_profits else None,
            "bestObservedBorrowNotionalUsd": best_observed_notional,
            "bestObservedBorrowNetProfitUsd": best_observed_profit,
            "bestExecutableBorrowNotionalUsd": best_executable_notional,
            "bestExecutableBorrowNetProfitUsd": best_executable_profit,
            "bestTokenBasisBorrowNotionalUsd": best_token_basis_notional,
            "bestTokenBasisInputSymbol": best_token_basis_symbol,
            "bestTokenBasisNetProfitAmount": best_token_basis_amount,
            "bestTokenBasisNetReturnBps": best_token_basis_bps,
            "bestTokenBasisProfitAmount": best_token_basis_profit,
            "bestTokenBasisProfitBasis": best_token_basis_profit_basis,
            "directAnchorRouteCount": sum(1 for route in routes if (route.get("overviewRoute") or {}).get("kind") == "direct_anchor_pair"),
            "bridgeRouteCount": sum(1 for route in routes if (route.get("overviewRoute") or {}).get("kind") == "bridge_pair"),
            "marketObservationRouteCount": int(
                summary.get("marketObservationRouteCount")
                or len(overview.get("marketObservation", {}).get("chainSpecificObservationPairs") or [])
            ),
            "marketObservationSequenceCount": int((market_observation.get("summary") or {}).get("observationSequenceCount") or 0),
            "chainSpecificObservationPairCount": len(overview.get("marketObservation", {}).get("chainSpecificObservationPairs") or []),
            "pathSource": overview.get("routeFrameworkSource") or "market_observation",
            "uniswapxQuoteableRouteCount": sum(
                1
                for route in routes
                for request in (route.get("quoteInputs") or {}).get("quoteRequests") or []
                if request.get("venue") == "uniswapx"
            ),
        }
    )
    return {
        "mode": "dex_quote_routes",
        "chain_id": chain_id,
        "selected_protocols": protocols or [],
        "marketObservationSource": overview.get("marketObservationSource") or "computed",
        "marketObservationSourceStage": overview.get("marketObservationSourceStage")
        or overview.get("marketObservationSource")
        or "computed",
        "summary": summary,
        "routes": routes,
        "marketObservation": {
            "summary": market_observation.get("summary") or {},
            "chainSpecificObservationPairs": overview.get("marketObservation", {}).get("chainSpecificObservationPairs") or [],
        },
        "routeFrameworkSource": overview.get("routeFrameworkSource") or "market_observation",
        "dexSettlement": settlement,
        "poolStaticTable": pool_static_table,
    }

def build_quote_request_payload(
    *,
    quote_routes_payload: dict[str, Any],
    pair: str | None = None,
    venue: str | None = None,
    amount: str | None = None,
) -> dict[str, Any]:
    normalized_pair = str(pair or "").strip().upper()
    normalized_venue = str(venue or "amm_pool").strip().lower()
    matched = None
    for route in quote_routes_payload.get("routes") or []:
        if str(route.get("symbolPair") or "").strip().upper() == normalized_pair:
            matched = route
            break
    if matched is None:
        return {
            "ok": False,
            "mode": "dex_quote_request",
            "error": "candidate_pair_not_found",
            "pair": normalized_pair,
            "venue": normalized_venue,
            "availablePairs": [route.get("symbolPair") for route in quote_routes_payload.get("routes") or []],
            "quoteRequests": [],
            "errorStack": route_error_stack(
                status="candidate_pair_not_found",
                message="candidate_pair_not_found",
            ),
        }
    requests = [
        request
        for request in (matched.get("quoteInputs") or {}).get("quoteRequests") or []
        if str(request.get("venue") or "").strip().lower() == normalized_venue
    ]
    quote_analysis = matched.get("quoteAnalysis") or {}
    quote_verification = matched.get("quoteVerification") or build_quote_verification_payload(quote_analysis=quote_analysis)
    route_stack = matched.get("errorStack") or route_error_stack(
        status=matched.get("quoteStatus") or quote_route_status(quote_analysis),
        message=quote_analysis.get("failureReason") or quote_analysis.get("error"),
        evidence=quote_analysis.get("live_onchain_evidence") if isinstance(quote_analysis.get("live_onchain_evidence"), dict) else None,
        blockers=list(quote_analysis.get("blockers") or quote_analysis.get("blocked_reasons") or []),
    )
    return {
        "ok": bool(requests),
        "mode": "dex_quote_request",
        "pair": matched.get("symbolPair"),
        "venue": normalized_venue,
        "amount": amount,
        "routeSymbols": matched.get("routeSymbols") or [],
        "quoteStatus": quote_route_status(quote_analysis),
        "quoteVerification": quote_verification,
        "quoteAnalysis": quote_analysis,
        "quoteRequests": requests,
        "errorStack": route_stack,
        "candidatePools": (matched.get("quoteInputs") or {}).get("candidatePools") or [],
        "status": "quote_request_ready" if requests else "venue_not_available_for_pair",
        "note": "This endpoint returns the exact quote request specification; live quote execution is handled by the configured venue adapter.",
    }

def build_submission_review_payload(
    *,
    attempts: list[dict[str, Any]] | None = None,
    groups: dict[str, Any] | None = None,
    source: str | None = None,
) -> dict[str, Any]:
    rows = list(attempts or [])
    def _route_review_key(row: dict[str, Any]) -> tuple[str, str]:
        network = str(row.get("network") or row.get("cow_network") or "").strip().lower()
        pair = str(row.get("pair") or row.get("symbol_pair") or row.get("route_key") or "").strip()
        if not pair:
            quote = row.get("quote") if isinstance(row.get("quote"), dict) else {}
            path = row.get("route_path") or quote.get("path") or []
            if isinstance(path, list):
                pair = " -> ".join(str(item).strip() for item in path if str(item).strip())
        return network, pair.upper()
    def _attempt_ts(row: dict[str, Any]) -> str:
        return str(row.get("created_at") or row.get("observed_at") or row.get("updated_at") or "")
    def row_status(row: dict[str, Any]) -> str:
        return str(row.get("review_category") or row.get("category") or row.get("status") or row.get("state") or "").lower()
    def row_tx_hash(row: dict[str, Any]) -> str:
        quote = row.get("quote") if isinstance(row.get("quote"), dict) else {}
        return str(
            row.get("tx_hash")
            or row.get("transaction_hash")
            or quote.get("tx_hash")
            or quote.get("transaction_hash")
            or ""
        ).strip()
    # The submission review page is for chain-submitted transactions only.
    # Quote-only prechecks and broadcast-disabled attempts have no tx hash and
    # should not be shown as "复盘记录".
    def row_broadcast_attempted(row: dict[str, Any]) -> bool:
        error_blob = " ".join(
            str(row.get(key) or "")
            for key in ("error", "detail", "errorDetail")
        ).strip().lower()
        if (
            row.get("broadcastDisabled")
            or "broadcast_disabled" in error_blob
            or "broadcast disabled" in error_blob
        ):
            return False
        precheck = row.get("precheck") if isinstance(row.get("precheck"), dict) else {}
        phase = str(row.get("execution_phase") or "").strip().lower()
        state = row_status(row)
        return bool(
            row.get("broadcast")
            or row.get("broadcastAttempted")
            or precheck.get("broadcast")
            or precheck.get("broadcastAttempted")
            or phase in {"submission", "order_submission", "execution"}
            or state in {
                "submitted_success",
                "submission_failed",
                "submit_failed",
                "order_failed",
                "execution_failed",
                "settlement_failed",
            }
        )
    # The review page shows only records where a chain transaction was actually
    # submitted. A failed broadcast without a tx hash has no chain submission
    # record to review, so it is not shown here.
    rows = [row for row in rows if row_tx_hash(row)]
    # Keep off-chain estimate data available for review. A submission attempt
    # must not lose the latest off-chain probe records / best-amount calculation
    # just because the on-chain broadcast row has no probe rows itself.
    estimates_by_route: dict[tuple[str, str], dict[str, Any]] = {}
    for candidate in (attempts or []):
        if not isinstance(candidate, dict):
            continue
        if row_tx_hash(candidate) or row_broadcast_attempted(candidate):
            continue
        key = _route_review_key(candidate)
        if not key[1]:
            continue
        current = estimates_by_route.get(key)
        if current is None or _attempt_ts(candidate) > _attempt_ts(current):
            estimates_by_route[key] = candidate
    merged_rows = []
    for row in rows:
        key = _route_review_key(row)
        estimate = estimates_by_route.get(key) if key[1] else None
        if not isinstance(estimate, dict) or estimate is row:
            merged_rows.append(row)
            continue
        merged = {**row}
        est_quote = estimate.get("quote") if isinstance(estimate.get("quote"), dict) else {}
        est_calc = (
            estimate.get("best_amount_calculation")
            or estimate.get("bestAmountCalculation")
            or est_quote.get("best_amount_calculation")
            or est_quote.get("bestAmountCalculation")
        )
        if isinstance(est_calc, dict) and not (row.get("best_amount_calculation") or row.get("bestAmountCalculation")):
            merged["best_amount_calculation"] = est_calc
            merged["bestAmountCalculation"] = est_calc
        if not row.get("probe_records") and not row.get("probeRecords"):
            est_probes = (
                estimate.get("probe_records")
                or estimate.get("probeRecords")
                or est_quote.get("probe_records")
                or est_quote.get("probeRecords")
                or (est_calc if isinstance(est_calc, dict) else {}).get("probe_records")
                or (est_calc if isinstance(est_calc, dict) else {}).get("probeRecords")
                or []
            )
            if isinstance(est_probes, list):
                merged["probe_records"] = est_probes
                merged["probeRecords"] = est_probes
        if isinstance(row.get("quote"), dict) and est_quote:
            merged_quote = {**row["quote"]}
            if not merged_quote.get("probe_records") and not merged_quote.get("probeRecords"):
                est_probes = (
                    est_quote.get("probe_records")
                    or est_quote.get("probeRecords")
                    or (est_calc if isinstance(est_calc, dict) else {}).get("probe_records")
                    or (est_calc if isinstance(est_calc, dict) else {}).get("probeRecords")
                    or []
                )
                if isinstance(est_probes, list):
                    merged_quote["probe_records"] = est_probes
                    merged_quote["probeRecords"] = est_probes
            if not merged_quote.get("best_amount_calculation") and not merged_quote.get("bestAmountCalculation"):
                if isinstance(est_calc, dict):
                    merged_quote["best_amount_calculation"] = est_calc
                    merged_quote["bestAmountCalculation"] = est_calc
            merged["quote"] = merged_quote
        if isinstance(row.get("analysis"), dict):
            est_analysis = estimate.get("analysis") if isinstance(estimate.get("analysis"), dict) else {}
            if est_analysis:
                merged_analysis = {**row["analysis"]}
                if not merged_analysis.get("probe_records") and not merged_analysis.get("probeRecords"):
                    est_probes = (
                        est_analysis.get("probe_records")
                        or est_analysis.get("probeRecords")
                        or (est_calc if isinstance(est_calc, dict) else {}).get("probe_records")
                        or (est_calc if isinstance(est_calc, dict) else {}).get("probeRecords")
                        or []
                    )
                    if isinstance(est_probes, list):
                        merged_analysis["probe_records"] = est_probes
                        merged_analysis["probeRecords"] = est_probes
                if not merged_analysis.get("best_amount_calculation") and not merged_analysis.get("bestAmountCalculation"):
                    if isinstance(est_calc, dict):
                        merged_analysis["best_amount_calculation"] = est_calc
                        merged_analysis["bestAmountCalculation"] = est_calc
                merged["analysis"] = merged_analysis
        merged_rows.append(merged)
    rows = merged_rows
    filtered_groups = {}
    for key, group in (groups or {}).items():
        if not isinstance(group, dict):
            filtered_groups[key] = group
            continue
        attempts = group.get("attempts") or []
        filtered_groups[key] = {
            **group,
            "attempts": [row for row in attempts if row_tx_hash(row)],
        }
    groups = filtered_groups or groups
    success = [row for row in rows if row_status(row).endswith("success")]
    failed = [row for row in rows if "fail" in row_status(row)]
    blocked = [row for row in rows if "not_executable" in row_status(row) or row_status(row) == "quote_required"]
    return {
        "mode": "dex_submission_review",
        "source": source or "execution_attempts",
        "summary": {
            "attemptCount": len(rows),
            "successCount": len(success),
            "failedCount": len(failed),
            "blockedCount": len(blocked),
        },
        "groups": groups or {},
        "attempts": rows[:100],
    }
