from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any, Callable

from core.dex_provenance import build_price_evidence_payload

DEFAULT_QUOTE_PROBE_NOTIONAL_USD = 1000.0


def _number(value: object) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return 0.0
    return parsed if parsed == parsed and parsed not in (float("inf"), float("-inf")) else 0.0


def _raw_int(value: object) -> int | None:
    try:
        parsed = int(str(value))
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def _decimal(value: object) -> Decimal | None:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return parsed if parsed.is_finite() else None


def _route_input_decimals(route: dict[str, Any], analysis: dict[str, Any] | None = None) -> int | None:
    route_tokens = [token for token in route.get("routeTokenPath") or [] if isinstance(token, dict)]
    if route_tokens:
        for key in ("decimals", "tokenDecimals", "inputDecimals"):
            decimals = _raw_int(route_tokens[0].get(key))
            if decimals is not None:
                return decimals
    route_analysis = analysis if isinstance(analysis, dict) else route.get("quoteAnalysis")
    if isinstance(route_analysis, dict):
        steps = route_analysis.get("steps") if isinstance(route_analysis.get("steps"), list) else []
        if steps:
            decimals = _raw_int(steps[0].get("inputDecimals"))
            if decimals is not None:
                return decimals
    overview_route = route.get("overviewRoute") if isinstance(route.get("overviewRoute"), dict) else {}
    path_tokens = [token for token in overview_route.get("pathTokens") or [] if isinstance(token, dict)]
    if path_tokens:
        for key in ("decimals", "tokenDecimals", "inputDecimals"):
            decimals = _raw_int(path_tokens[0].get(key))
            if decimals is not None:
                return decimals
    return None


def _human_to_raw(value: object, decimals: int | None) -> int | None:
    if decimals is None:
        return None
    parsed = _decimal(value)
    if parsed is None or parsed <= 0:
        return None
    return int((parsed * (Decimal(10) ** int(decimals))).to_integral_value())


def _best_probe_row(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    scored = [row for row in rows if _number(row.get("observedNetProfitUsd")) is not None and row.get("referenceInputAmount") is not None]
    if scored:
        return max(scored, key=lambda row: _number(row.get("observedNetProfitUsd")))
    executable = [row for row in rows if row.get("executableCandidate") and row.get("referenceInputAmount") is not None]
    if executable:
        return max(executable, key=lambda row: _number(row.get("observedNetProfitUsd")))
    return next((row for row in rows if row.get("referenceInputAmount") is not None), None)


def build_overview_profit_payload(analysis: dict[str, Any]) -> dict[str, Any]:
    observed_gross = analysis.get("grossProfitUsd")
    if observed_gross is None:
        observed_gross = analysis.get("referenceGrossProfitUsd")
    observed_net = analysis.get("netProfitUsd")
    if observed_net is None:
        observed_net = analysis.get("referenceNetProfitUsd")
    observed_bps = analysis.get("netProfitBps")
    if observed_bps is None:
        observed_bps = analysis.get("referenceNetProfitBps")
    recommendation = analysis.get("recommendation") if isinstance(analysis.get("recommendation"), dict) else {}
    execution_size_probe = analysis.get("executionSizeProbe") if isinstance(analysis.get("executionSizeProbe"), dict) else {}
    max_executable_amount_raw = analysis.get("maxExecutableAmountRaw")
    if max_executable_amount_raw in (None, "") and execution_size_probe:
        max_executable_amount_raw = execution_size_probe.get("maxExecutableAmountRaw")
    first_failure_amount_raw = analysis.get("firstFailureAmountRaw")
    if first_failure_amount_raw in (None, "") and execution_size_probe:
        first_failure_amount_raw = execution_size_probe.get("firstFailureAmountRaw")
    execution_size_probe_status = analysis.get("executionSizeProbeStatus")
    if not execution_size_probe_status and execution_size_probe:
        execution_size_probe_status = execution_size_probe.get("status")
    execution_size_probe_search_status = analysis.get("executionSizeProbeSearchStatus")
    if not execution_size_probe_search_status and execution_size_probe:
        execution_size_probe_search_status = execution_size_probe.get("searchStatus")
    execution_size_probe_attempts = analysis.get("executionSizeProbeAttempts")
    if execution_size_probe_attempts in (None, "") and execution_size_probe:
        execution_size_probe_attempts = execution_size_probe.get("probeAttempts")
    recommended_borrow_input_amount_raw = analysis.get("recommendedBorrowInputAmountRaw")
    recommended_borrow_input_amount = analysis.get("recommendedBorrowInputAmount")
    payload = {
        "method": analysis.get("method"),
        "notionalUsd": analysis.get("notionalUsd"),
        "complete": bool(analysis.get("complete")),
        "requiresLiveQuoter": bool(analysis.get("requiresLiveQuoter")),
        "adapterRequired": bool(analysis.get("adapterRequired")),
        "stepCount": analysis.get("stepCount"),
        "finalOutputUsd": analysis.get("finalOutputUsd"),
        "referenceFinalOutputUsd": analysis.get("referenceFinalOutputUsd"),
        "grossProfitUsd": analysis.get("grossProfitUsd"),
        "referenceGrossProfitUsd": analysis.get("referenceGrossProfitUsd"),
        "observedGrossProfitUsd": observed_gross,
        "flashLoanFeeUsd": analysis.get("flashLoanFeeUsd"),
        "gasReserveUsd": analysis.get("gasReserveUsd"),
        "offchainMinProfitUsd": analysis.get("offchainMinProfitUsd") or analysis.get("minProfitUsd"),
        "submissionMinProfitUsd": analysis.get("submissionMinProfitUsd"),
        "submissionNetProfitUsd": analysis.get("submissionNetProfitUsd"),
        "netProfitUsd": analysis.get("netProfitUsd"),
        "referenceNetProfitUsd": analysis.get("referenceNetProfitUsd"),
        "observedNetProfitUsd": observed_net,
        "observedNetProfitBps": observed_bps,
        "referenceInputAmount": analysis.get("referenceInputAmount"),
        "referenceInputSymbol": analysis.get("referenceInputSymbol"),
        "referenceFinalOutputAmount": analysis.get("referenceFinalOutputAmount"),
        "referenceFinalOutputSymbol": analysis.get("referenceFinalOutputSymbol"),
        "referenceGrossProfitAmount": analysis.get("referenceGrossProfitAmount"),
        "referenceFlashLoanFeeAmount": analysis.get("referenceFlashLoanFeeAmount"),
        "referenceNetProfitAmount": analysis.get("referenceNetProfitAmount"),
        "referenceGrossReturnBps": analysis.get("referenceGrossReturnBps"),
        "referenceNetReturnBps": analysis.get("referenceNetReturnBps"),
        "referenceGasExcluded": analysis.get("referenceGasExcluded"),
        "referenceReturnBasis": analysis.get("referenceReturnBasis"),
        "referenceReturnTrusted": analysis.get("referenceReturnTrusted"),
        "borrowAssetClass": analysis.get("borrowAssetClass"),
        "borrowAssetIsStable": analysis.get("borrowAssetIsStable"),
        "tokenBasisReferenceProfitable": bool(analysis.get("tokenBasisReferenceProfitable")),
        "tokenBasisProfitable": bool(analysis.get("tokenBasisProfitable")),
        "chainSubmissionCandidate": bool(analysis.get("chainSubmissionCandidate")),
        "chainSubmissionPolicy": analysis.get("chainSubmissionPolicy"),
        "chainSubmissionMinProfitAmount": analysis.get("chainSubmissionMinProfitAmount"),
        "chainSubmissionMinProfitSymbol": analysis.get("chainSubmissionMinProfitSymbol"),
        "profitable": bool(analysis.get("profitable") or (observed_net is not None and _number(observed_net) > 0)),
        "executableCandidate": bool(analysis.get("executableCandidate")),
        "trustedUsdReference": analysis.get("trustedUsdReference"),
        "tokenDecimalsTrusted": analysis.get("tokenDecimalsTrusted"),
        "executionSizeProbe": execution_size_probe or None,
        "maxExecutableAmountRaw": max_executable_amount_raw,
        "firstFailureAmountRaw": first_failure_amount_raw,
        "executionSizeProbeStatus": execution_size_probe_status,
        "executionSizeProbeSearchStatus": execution_size_probe_search_status,
        "executionSizeProbeAttempts": execution_size_probe_attempts,
        "recommendedBorrowInputAmount": recommended_borrow_input_amount,
        "recommendedBorrowInputAmountRaw": recommended_borrow_input_amount_raw,
        "recommendedBorrowInputAmountBasis": analysis.get("recommendedBorrowInputAmountBasis"),
        "recommendedBorrowInputAmountReason": analysis.get("recommendedBorrowInputAmountReason"),
        "recommendedBorrowInputAmountDecimals": analysis.get("recommendedBorrowInputAmountDecimals"),
        "staticDepthStatus": analysis.get("staticDepthStatus"),
        "staticDepthAvailable": analysis.get("staticDepthAvailable"),
        "maxStaticDepthImpactBps": analysis.get("maxStaticDepthImpactBps"),
        "recommendation": recommendation,
        "recommendationLevel": recommendation.get("level"),
        "recommendationAction": recommendation.get("action"),
        "warnings": list(analysis.get("warnings") or []),
    }
    payload.update(
        build_price_evidence_payload(
            source=analysis.get("priceEvidenceSource"),
            mode=analysis.get("priceEvidenceMode"),
        )
    )
    return payload


def apply_route_overview_profit(
    route: dict[str, Any],
    *,
    price_map: dict[str, float],
    chain_id: int | None = None,
    route_quote_analysis_fn: Callable[..., dict[str, Any]],
) -> dict[str, Any]:
    analysis = route_quote_analysis_fn(route, price_map=price_map, chain_id=chain_id)
    updated = dict(route)
    updated["overviewProfit"] = build_overview_profit_payload(analysis)
    updated["routeFrameworkSource"] = route.get("routeFrameworkSource") or route.get("pathSource") or "market_observation"
    updated.update(
        build_price_evidence_payload(
            source=analysis.get("priceEvidenceSource")
            or (
                "live_quoter_required"
                if analysis.get("requiresLiveQuoter") or analysis.get("adapterRequired")
                else "static_reference_probe"
            ),
            mode=analysis.get("priceEvidenceMode")
            or (
                "live_required"
                if analysis.get("requiresLiveQuoter") or analysis.get("adapterRequired")
                else "reference_only"
            ),
        )
    )
    return updated


def build_route_size_analysis(
    route: dict[str, Any],
    *,
    price_map: dict[str, float],
    chain_id: int | None = None,
    route_quote_analysis_fn: Callable[..., dict[str, Any]],
    quote_probe_notional_usd: float = DEFAULT_QUOTE_PROBE_NOTIONAL_USD,
) -> dict[str, Any]:
    overview_route = route.get("overviewRoute") or {}
    route_tokens = [token for token in route.get("routeTokenPath") or overview_route.get("pathTokens") or [] if isinstance(token, dict)]
    route_symbols = list(route.get("routeSymbols") or overview_route.get("pathSymbols") or route.get("routeSymbols") or [])
    if route_tokens:
        route_symbols = [
            str(token.get("symbol") or "").strip().upper()
            for token in route_tokens
            if str(token.get("symbol") or "").strip()
        ] or route_symbols
    input_symbol = route_symbols[0] if route_symbols else None
    use_token_basis_ladder = bool(input_symbol and len(route_symbols) > 1 and route_symbols[-1] == input_symbol and not price_map.get(input_symbol))
    notionals = [] if use_token_basis_ladder else [100.0, 500.0, 1000.0, 2000.0, 5000.0, 10000.0]
    token_amounts = [0.01, 0.1, 1.0, 10.0, 100.0] if use_token_basis_ladder else []
    rows: list[dict[str, Any]] = []
    for notional in notionals:
        analysis = route_quote_analysis_fn(route, price_map=price_map, chain_id=chain_id, notional_usd=notional)
        summary = build_overview_profit_payload(analysis)
        rows.append(
            {
                "borrowBasis": "usd",
                "notionalUsd": notional,
                "borrowAmount": notional,
                "borrowSymbol": "USD",
                "complete": summary["complete"],
                "requiresLiveQuoter": summary["requiresLiveQuoter"],
                "adapterRequired": summary["adapterRequired"],
                "observedGrossProfitUsd": summary["observedGrossProfitUsd"],
                "flashLoanFeeUsd": summary["flashLoanFeeUsd"],
                "gasReserveUsd": summary["gasReserveUsd"],
                "observedNetProfitUsd": summary["observedNetProfitUsd"],
                "observedNetProfitBps": summary["observedNetProfitBps"],
                "referenceInputAmount": summary["referenceInputAmount"],
                "referenceInputSymbol": summary["referenceInputSymbol"],
                "referenceFinalOutputAmount": summary["referenceFinalOutputAmount"],
                "referenceFinalOutputSymbol": summary["referenceFinalOutputSymbol"],
                "referenceNetProfitAmount": summary["referenceNetProfitAmount"],
                "referenceNetReturnBps": summary["referenceNetReturnBps"],
                "referenceReturnBasis": summary["referenceReturnBasis"],
                "referenceReturnTrusted": summary["referenceReturnTrusted"],
                "tokenBasisReferenceProfitable": summary["tokenBasisReferenceProfitable"],
                "tokenBasisProfitable": summary["tokenBasisProfitable"],
                "chainSubmissionCandidate": summary["chainSubmissionCandidate"],
                "chainSubmissionPolicy": summary["chainSubmissionPolicy"],
                "chainSubmissionMinProfitAmount": summary["chainSubmissionMinProfitAmount"],
                "chainSubmissionMinProfitSymbol": summary["chainSubmissionMinProfitSymbol"],
                "executableCandidate": summary["executableCandidate"],
                "executionSizeProbe": summary["executionSizeProbe"],
                "maxExecutableAmountRaw": summary["maxExecutableAmountRaw"],
                "firstFailureAmountRaw": summary["firstFailureAmountRaw"],
                "executionSizeProbeStatus": summary["executionSizeProbeStatus"],
                "executionSizeProbeSearchStatus": summary["executionSizeProbeSearchStatus"],
                "executionSizeProbeAttempts": summary["executionSizeProbeAttempts"],
                "recommendationLevel": summary["recommendationLevel"],
                "staticDepthStatus": summary.get("staticDepthStatus"),
                "staticDepthAvailable": summary.get("staticDepthAvailable"),
                "maxStaticDepthImpactBps": summary.get("maxStaticDepthImpactBps"),
            }
        )
    for token_amount in token_amounts:
        analysis = route_quote_analysis_fn(
            route,
            price_map=price_map,
            chain_id=chain_id,
            notional_usd=quote_probe_notional_usd,
            token_basis_input_amount=token_amount,
        )
        summary = build_overview_profit_payload(analysis)
        rows.append(
            {
                "borrowBasis": "borrow_token",
                "notionalUsd": None,
                "borrowAmount": token_amount,
                "borrowSymbol": input_symbol,
                "complete": summary["complete"],
                "requiresLiveQuoter": summary["requiresLiveQuoter"],
                "adapterRequired": summary["adapterRequired"],
                "observedGrossProfitUsd": summary["observedGrossProfitUsd"],
                "flashLoanFeeUsd": summary["flashLoanFeeUsd"],
                "gasReserveUsd": summary["gasReserveUsd"],
                "observedNetProfitUsd": summary["observedNetProfitUsd"],
                "observedNetProfitBps": summary["observedNetProfitBps"],
                "referenceInputAmount": summary["referenceInputAmount"],
                "referenceInputSymbol": summary["referenceInputSymbol"],
                "referenceFinalOutputAmount": summary["referenceFinalOutputAmount"],
                "referenceFinalOutputSymbol": summary["referenceFinalOutputSymbol"],
                "referenceNetProfitAmount": summary["referenceNetProfitAmount"],
                "referenceNetReturnBps": summary["referenceNetReturnBps"],
                "referenceReturnBasis": summary["referenceReturnBasis"],
                "referenceReturnTrusted": summary["referenceReturnTrusted"],
                "tokenBasisReferenceProfitable": summary["tokenBasisReferenceProfitable"],
                "tokenBasisProfitable": summary["tokenBasisProfitable"],
                "chainSubmissionCandidate": summary["chainSubmissionCandidate"],
                "chainSubmissionPolicy": summary["chainSubmissionPolicy"],
                "chainSubmissionMinProfitAmount": summary["chainSubmissionMinProfitAmount"],
                "chainSubmissionMinProfitSymbol": summary["chainSubmissionMinProfitSymbol"],
                "executableCandidate": summary["executableCandidate"],
                "executionSizeProbe": summary["executionSizeProbe"],
                "maxExecutableAmountRaw": summary["maxExecutableAmountRaw"],
                "firstFailureAmountRaw": summary["firstFailureAmountRaw"],
                "executionSizeProbeStatus": summary["executionSizeProbeStatus"],
                "executionSizeProbeSearchStatus": summary["executionSizeProbeSearchStatus"],
                "executionSizeProbeAttempts": summary["executionSizeProbeAttempts"],
                "recommendationLevel": summary["recommendationLevel"],
                "staticDepthStatus": summary.get("staticDepthStatus"),
                "staticDepthAvailable": summary.get("staticDepthAvailable"),
                "maxStaticDepthImpactBps": summary.get("maxStaticDepthImpactBps"),
            }
        )
    observed = [row for row in rows if row.get("observedNetProfitUsd") is not None]
    executable = [row for row in rows if row.get("executableCandidate") and row.get("observedNetProfitUsd") is not None]
    positive = [row for row in observed if _number(row.get("observedNetProfitUsd")) > 0]
    token_basis_rows = [row for row in rows if row.get("referenceNetReturnBps") is not None]
    best_observed = max(observed, key=lambda row: _number(row.get("observedNetProfitUsd")), default=None)
    best_executable = max(executable, key=lambda row: _number(row.get("observedNetProfitUsd")), default=None)
    best_token_basis = max(token_basis_rows, key=lambda row: _number(row.get("referenceNetReturnBps")), default=None)
    size_probe_rows = [
        row
        for row in rows
        if row.get("executionSizeProbeStatus") or row.get("maxExecutableAmountRaw") not in (None, "")
    ]
    size_probe_row = size_probe_rows[0] if size_probe_rows else None
    execution_size_probe = size_probe_row.get("executionSizeProbe") if isinstance(size_probe_row, dict) else None
    recommended_row = _best_probe_row(rows)
    recommended_input_amount = recommended_row.get("referenceInputAmount") if isinstance(recommended_row, dict) else None
    if recommended_input_amount in (None, "") and isinstance(recommended_row, dict):
        recommended_input_amount = recommended_row.get("borrowAmount")
    recommended_input_decimals = _route_input_decimals(route, recommended_row if isinstance(recommended_row, dict) else None)
    recommended_input_amount_raw = _human_to_raw(recommended_input_amount, recommended_input_decimals)
    recommended_input_basis = str(
        recommended_row.get("borrowBasis") if isinstance(recommended_row, dict) else ""
    ).strip().lower() or None
    recommended_input_reason = None
    if isinstance(recommended_row, dict):
        if _number(recommended_row.get("observedNetProfitUsd")) > 0:
            recommended_input_reason = "best_positive_observed_net_profit"
        elif recommended_row.get("executableCandidate"):
            recommended_input_reason = "best_executable_candidate"
        else:
            recommended_input_reason = "best_available_probe_row"
    recommended_analysis = None
    if recommended_input_amount is not None and recommended_input_basis == "borrow_token":
        recommended_analysis = route_quote_analysis_fn(
            route,
            price_map=price_map,
            chain_id=chain_id,
            notional_usd=quote_probe_notional_usd,
            token_basis_input_amount=float(recommended_input_amount),
        )
    depth_statuses = [str(row.get("staticDepthStatus") or "").strip().lower() for row in rows]
    depth_exploration_required = any(status in {"unavailable", "exceeds_10_bps"} for status in depth_statuses)
    return {
        "method": "static_probe_notional_ladder",
        "borrowBasis": "borrow_token" if use_token_basis_ladder else "usd",
        "notionalUsdSteps": notionals,
        "tokenAmountSteps": token_amounts,
        "rows": rows,
        "bestObservedNotionalUsd": best_observed.get("notionalUsd") if best_observed else None,
        "bestObservedNetProfitUsd": best_observed.get("observedNetProfitUsd") if best_observed else None,
        "bestExecutableNotionalUsd": best_executable.get("notionalUsd") if best_executable else None,
        "bestExecutableNetProfitUsd": best_executable.get("observedNetProfitUsd") if best_executable else None,
        "bestTokenBasisNotionalUsd": best_token_basis.get("notionalUsd") if best_token_basis else None,
        "bestTokenBasisInputSymbol": best_token_basis.get("referenceInputSymbol") if best_token_basis else None,
        "bestTokenBasisNetProfitAmount": best_token_basis.get("referenceNetProfitAmount") if best_token_basis else None,
        "bestTokenBasisNetReturnBps": best_token_basis.get("referenceNetReturnBps") if best_token_basis else None,
        "recommendedBorrowInputAmount": float(recommended_input_amount) if recommended_input_amount is not None else None,
        "recommendedBorrowInputAmountRaw": str(recommended_input_amount_raw) if recommended_input_amount_raw is not None else None,
        "recommendedBorrowInputAmountBasis": recommended_input_basis,
        "recommendedBorrowInputAmountReason": recommended_input_reason,
        "recommendedBorrowInputAmountDecimals": recommended_input_decimals,
        "recommendedQuoteAnalysis": recommended_analysis,
        "depthExplorationRequired": depth_exploration_required,
        "depthImpactLimitBps": 10.0,
        "maxExecutableAmountRaw": size_probe_row.get("maxExecutableAmountRaw") if size_probe_row else None,
        "firstFailureAmountRaw": size_probe_row.get("firstFailureAmountRaw") if size_probe_row else None,
        "executionSizeProbeStatus": size_probe_row.get("executionSizeProbeStatus") if size_probe_row else None,
        "executionSizeProbeSearchStatus": size_probe_row.get("executionSizeProbeSearchStatus") if size_probe_row else None,
        "executionSizeProbeAttempts": size_probe_row.get("executionSizeProbeAttempts") if size_probe_row else None,
        "executionSizeProbe": execution_size_probe,
        "routeExecutionSizeProbe": {
            "status": size_probe_row.get("executionSizeProbeStatus") if size_probe_row else None,
            "searchStatus": size_probe_row.get("executionSizeProbeSearchStatus") if size_probe_row else None,
            "probeAttempts": size_probe_row.get("executionSizeProbeAttempts") if size_probe_row else None,
            "maxExecutableAmountRaw": size_probe_row.get("maxExecutableAmountRaw") if size_probe_row else None,
            "firstFailureAmountRaw": size_probe_row.get("firstFailureAmountRaw") if size_probe_row else None,
        }
        if size_probe_row
        else None,
        "positiveRangeUsd": {
            "min": min((_number(row.get("notionalUsd")) for row in positive), default=None),
            "max": max((_number(row.get("notionalUsd")) for row in positive), default=None),
        },
    }
