from __future__ import annotations

from typing import Any

from core.db.cow_execution_mapping import (
    SELECTED_PROFIT_CAMEL_KEYS,
    selected_profit_mapping,
)


_TOP_LEVEL_KEYS = frozenset(
    {
        "_key",
        "auto_execute",
        "auto_execute_requested",
        "best_amount",
        "best_amount_calculation",
        "blocked_reasons",
        "can_submit_order",
        "chain_id",
        "chain_submission_candidate",
        "chain_submission_policy",
        "checks_passed",
        "created_at",
        "detail",
        "error",
        "errorDetail",
        "errorStack",
        "execution_phase",
        "final_delta_amount",
        "final_symbol",
        "id",
        "network",
        "notForDefaultScan",
        "observed_at",
        "order_submission_enabled",
        "owner_address",
        "pair",
        "pair_rank",
        "poolPathKey",
        "precheck",
        "priority_reason",
        "probe_records",
        "referencePoolPathKey",
        "reasons",
        "reference_token_delta_amount",
        "review_analysis_days",
        "review_category",
        "review_category_label",
        "review_retention_days",
        "review_retention_policy",
        "review_summary",
        "route_addresses",
        "route_path",
        "route_token_path",
        *SELECTED_PROFIT_CAMEL_KEYS,
        "state",
        "temporaryRoute",
        "temporaryRoutePolicy",
        "transaction_hash",
        "tx_hash",
        "updated_at",
    }
)

_QUOTE_KEYS = frozenset(
    {
        "best_amount",
        "best_amount_calculation",
        "candidate_basis",
        "chain_submission_candidate",
        "chain_submission_policy",
        "direction",
        "final_amount",
        "final_delta_amount",
        "final_symbol",
        "input_amount",
        "input_symbol",
        "live_profit_basis",
        "live_profit_bps",
        "live_profit_percent",
        "live_token_profit_amount",
        "live_token_profit_symbol",
        "notForDefaultScan",
        "offchainInitialProbeReason",
        "pair",
        "pair_rank",
        "path",
        "poolPathKey",
        "priority_reason",
        "probe_records",
        "profit_amount",
        "quote_required",
        "quote_status",
        "quote_verified",
        "readonly_viable",
        "referenceNetReturnBps",
        "reference_final_amount",
        "reference_final_delta_amount",
        "reference_token_delta_amount",
        "reference_token_final_amount",
        "reference_token_return_bps",
        "route",
        "route_addresses",
        "temporaryRoute",
        "temporaryRoutePolicy",
        "transaction_hash",
        "trigger_source",
        "tx_hash",
        "viable",
    }
)

_ANALYSIS_KEYS = frozenset(
    {
        "adapterRequired",
        "amount_optimized_from",
        "bestAmount",
        "bestAmountCalculation",
        "bestAmountConfirmed",
        "bestAmountMissing",
        "best_amount",
        "best_amount_calculation",
        "best_amount_confirmed",
        "best_amount_missing",
        "chainSubmissionCandidate",
        "chainSubmissionPolicy",
        "complete",
        "direction",
        "executableCandidate",
        "finalOutputUsd",
        "final_amount",
        "grossProfitUsd",
        "initialProbeAmount",
        "initial_probe_amount",
        "livePriceVerified",
        "live_onchain_evidence",
        "method",
        "netProfitUsd",
        "notionalUsd",
        "offchainInitialProbeReason",
        "offchain_initial_probe_reason",
        "priceEvidenceMode",
        "priceEvidenceModeLabel",
        "priceEvidenceSource",
        "priceEvidenceSourceLabel",
        "probe_records",
        "profitBasis",
        "profit_amount",
        "profitable",
        "recommendation",
        "recommendationAction",
        "recommendationLevel",
        "referenceFinalOutputAmount",
        "referenceFinalOutputSymbol",
        "referenceFinalOutputUsd",
        "referenceGrossProfitUsd",
        "referenceInputAmount",
        "referenceInputSymbol",
        "referenceNetProfitAmount",
        "referenceNetProfitUsd",
        "referenceNetReturnBps",
        "referenceReturnBasis",
        "referenceReturnTrusted",
        "reference_final_output_amount",
        "reference_final_output_symbol",
        "reference_final_output_usd",
        "reference_input_symbol",
        "reference_net_profit_amount",
        "reference_net_profit_usd",
        "reference_net_return_bps",
        "requestedAmount",
        "requested_amount",
        "requiresLiveQuoter",
        "stepCount",
        "steps",
        "warnings",
    }
)

_LIVE_EVIDENCE_KEYS = frozenset(
    {
        "blockNumber",
        "block_number",
        "blocked_reasons",
        "blockers",
        "chainId",
        "chain_id",
        "conclusion",
        "costModel",
        "detail",
        "detailedPreviewValuesAvailable",
        "error",
        "errorStack",
        "executionTrust",
        "execution_trust",
        "failureReason",
        "from",
        "network",
        "offchainInitialProbeReason",
        "offchain_initial_probe_reason",
        "reasons",
        "referenceNetProfitAmount",
        "referenceNetProfitAmountMissingReason",
        "referenceRateProductDelta",
        "readyForTransactionPreflight",
        "ready_for_transaction_preflight",
        "reviewFailureDetail",
        "reviewFailureKind",
        "reviewFailureReasons",
        "status",
        "triggerBlockNumber",
        "trigger_block_number",
        "trustedForExecution",
        "trusted_for_execution",
    }
)

_PRECHECK_KEYS = frozenset(
    {
        "broadcast",
        "broadcastAttempted",
        "control_mode",
        "expected_profit_percent",
        "notional_usd",
        "route_hop_constraints_enforced",
    }
)

_REVIEW_SUMMARY_KEYS = frozenset(
    {
        "onchain_evidence",
        "plan",
        "routeIdentity",
        "route_identity",
    }
)

_CALC_KEYS = frozenset(
    {
        "bestAmountSymbol",
        "isBest",
        "isRecommended",
        "referenceNetReturnBps",
        "requestedSymbol",
    }
)


def _subset(mapping: dict[str, Any], keys: frozenset[str]) -> dict[str, Any]:
    if not isinstance(mapping, dict):
        return {}
    return {
        key: value
        for key, value in mapping.items()
        if key in keys and value is not None
    }


def _compact_calc(mapping: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(mapping, dict):
        return {}
    compact = _subset(mapping, _CALC_KEYS)
    if isinstance(mapping.get("probe_records"), list):
        compact["probe_records"] = mapping["probe_records"]
    elif isinstance(mapping.get("probeRecords"), list):
        compact["probe_records"] = mapping["probeRecords"]
    elif isinstance(mapping.get("rows"), list):
        compact["probe_records"] = mapping["rows"]
    return compact


def _compact_analysis(mapping: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(mapping, dict):
        return {}
    compact = _subset(mapping, _ANALYSIS_KEYS)
    calc = mapping.get("best_amount_calculation") or mapping.get("bestAmountCalculation")
    if isinstance(calc, dict):
        compact["best_amount_calculation"] = _compact_calc(calc)
    return compact


def compact_cow_execution_attempt_row(row: dict[str, Any]) -> dict[str, Any]:
    """Project a full attempt row to the fields the submission pages render."""
    if not isinstance(row, dict):
        return {}
    compact = _subset(row, _TOP_LEVEL_KEYS)
    compact.update(selected_profit_mapping(row))
    quote = row.get("quote") if isinstance(row.get("quote"), dict) else {}
    compact_quote = _subset(quote, _QUOTE_KEYS)
    analysis = quote.get("quote_analysis") or quote.get("analysis")
    compact_quote["quote_analysis"] = _compact_analysis(analysis)
    row_calc = row.get("best_amount_calculation") or row.get("bestAmountCalculation")
    if isinstance(row_calc, dict):
        compact["best_amount_calculation"] = _compact_calc(row_calc)
    quote_calc = compact_quote.get("best_amount_calculation")
    if isinstance(quote_calc, dict):
        compact_quote["best_amount_calculation"] = _compact_calc(quote_calc)
    live = quote.get("live_onchain_evidence") if isinstance(quote.get("live_onchain_evidence"), dict) else {}
    compact_live = _subset(live, _LIVE_EVIDENCE_KEYS)
    if isinstance(live.get("costModel"), dict):
        compact_live["costModel"] = live["costModel"]
    if compact_live:
        compact_quote["live_onchain_evidence"] = compact_live
    compact["quote"] = compact_quote
    if isinstance(row.get("precheck"), dict):
        compact["precheck"] = _subset(row["precheck"], _PRECHECK_KEYS)
    if isinstance(row.get("review_summary"), dict):
        compact["review_summary"] = _subset(
            row["review_summary"],
            _REVIEW_SUMMARY_KEYS,
        )
    return compact


def compact_cow_execution_groups(groups: dict[str, Any]) -> dict[str, Any]:
    """Keep only group metadata counts in list payloads; details load by id."""
    compact: dict[str, Any] = {}
    for key, group in (groups or {}).items():
        if not isinstance(group, dict):
            compact[key] = group
            continue
        compact[key] = {
            "count": int(group.get("count") or len(group.get("attempts") or [])),
            "retention_days": group.get("retention_days"),
            "analysis_days": group.get("analysis_days"),
            "retention_policy": group.get("retention_policy"),
            "page_size": group.get("page_size"),
        }
    return compact
