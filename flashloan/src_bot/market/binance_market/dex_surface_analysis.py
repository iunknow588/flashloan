from __future__ import annotations

from typing import Any, Callable

from web.dex_provenance import build_price_evidence_payload

DEFAULT_QUOTE_PROBE_NOTIONAL_USD = 1000.0


def _number(value: object) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return 0.0
    return parsed if parsed == parsed and parsed not in (float("inf"), float("-inf")) else 0.0


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
                "recommendationLevel": summary["recommendationLevel"],
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
                "recommendationLevel": summary["recommendationLevel"],
            }
        )
    observed = [row for row in rows if row.get("observedNetProfitUsd") is not None]
    executable = [row for row in rows if row.get("executableCandidate") and row.get("observedNetProfitUsd") is not None]
    positive = [row for row in observed if _number(row.get("observedNetProfitUsd")) > 0]
    token_basis_rows = [row for row in rows if row.get("referenceNetReturnBps") is not None]
    best_observed = max(observed, key=lambda row: _number(row.get("observedNetProfitUsd")), default=None)
    best_executable = max(executable, key=lambda row: _number(row.get("observedNetProfitUsd")), default=None)
    best_token_basis = max(token_basis_rows, key=lambda row: _number(row.get("referenceNetReturnBps")), default=None)
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
        "positiveRangeUsd": {
            "min": min((_number(row.get("notionalUsd")) for row in positive), default=None),
            "max": max((_number(row.get("notionalUsd")) for row in positive), default=None),
        },
    }
