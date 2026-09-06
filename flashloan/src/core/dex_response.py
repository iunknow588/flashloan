"""Pure response shaping helpers shared by DEX API adapters."""

from __future__ import annotations


_OVERVIEW_PROFIT_KEYS = frozenset(
    {
        "method", "notionalUsd", "complete", "requiresLiveQuoter", "adapterRequired",
        "observedGrossProfitUsd", "flashLoanFeeUsd", "gasReserveUsd", "offchainMinProfitUsd",
        "submissionMinProfitUsd", "submissionNetProfitUsd", "observedNetProfitUsd",
        "referenceNetProfitUsd", "observedNetProfitBps", "referenceInputAmount",
        "referenceInputSymbol", "referenceFinalOutputAmount", "referenceFinalOutputSymbol",
        "referenceNetProfitAmount", "referenceNetReturnBps", "referenceReturnBasis",
        "referenceReturnTrusted", "borrowAssetClass", "borrowAssetIsStable",
        "tokenBasisProfitable", "tokenBasisReferenceProfitable", "chainSubmissionCandidate",
        "chainSubmissionPolicy", "chainSubmissionMinProfitAmount", "chainSubmissionMinProfitSymbol",
        "profitable", "executableCandidate", "trustedUsdReference", "tokenDecimalsTrusted",
        "recommendation", "recommendationLevel", "recommendationAction", "warnings",
        "priceEvidenceSource", "priceEvidenceMode", "priceEvidenceSourceLabel",
    }
)


def compact_dex_overview_response(payload: object) -> object:
    """Keep overview responses at route-summary granularity."""
    if not isinstance(payload, dict):
        return payload
    response = dict(payload)
    # Preserve the route-level discriminator even when a test or adapter
    # supplies a minimal payload and no paths to compact.
    response.setdefault("mode", "dex_overview")
    compact_paths = []
    for path in payload.get("arbitragePaths") or []:
        if not isinstance(path, dict):
            continue
        compact = {
            key: path[key]
            for key in (
                "symbolPair", "routeSymbols", "routeAddresses", "overviewRoute", "overviewProfit",
                "routeFrameworkSource", "pathSource", "marketObservationSourceStage",
                "priceEvidenceSource", "priceEvidenceSourceLabel",
            )
            if key in path
        }
        profit = path.get("overviewProfit")
        if isinstance(profit, dict):
            compact["overviewProfit"] = {
                key: profit[key] for key in _OVERVIEW_PROFIT_KEYS if key in profit
            }
        compact["executionPools"] = [
            {
                key: pool[key]
                for key in ("pool", "protocol", "dex", "fee", "routeText", "balances", "depthBand")
                if key in pool
            }
            for pool in (path.get("executionPools") or path.get("preferredPools") or [])[:5]
            if isinstance(pool, dict)
        ]
        compact_paths.append(compact)
    response["arbitragePaths"] = compact_paths
    observation = response.get("marketObservation")
    if isinstance(observation, dict):
        response["marketObservation"] = {
            key: observation[key]
            for key in ("summary", "chainSpecificObservationPairs")
            if key in observation
        }
    return response
