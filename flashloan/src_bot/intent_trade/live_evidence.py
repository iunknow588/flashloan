"""Machine-checkable evidence for unified-executor shadow and live signals."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from intent_trade.unified_live_signal_schema import (
    UNIFIED_LIVE_SIGNAL_SCHEMA_CONSTANT_VERSION,
    UNIFIED_LIVE_SIGNAL_SCHEMA_VERSION,
)

ADDRESS_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonical_json_hash(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _parse_utc(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc) if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _runtime_trades_from_result(result: dict[str, Any]) -> list[dict[str, Any]]:
    request = result.get("request") if isinstance(result.get("request"), dict) else {}
    trades = request.get("runtimeTrades")
    return list(trades) if isinstance(trades, list) else []


def _validate_runtime_trade_shape(runtime_trades: list[Any]) -> list[str]:
    errors: list[str] = []
    for index, trade in enumerate(runtime_trades):
        if not isinstance(trade, dict):
            errors.append(f"runtime_trade_{index}_not_object")
            continue
        for field in ("tradeIndex", "tokenX", "tokenY", "pools"):
            if field not in trade:
                errors.append(f"runtime_trade_{index}_{field}_missing")
        for field in ("tokenX", "tokenY"):
            value = trade.get(field)
            if not isinstance(value, str) or not ADDRESS_RE.match(value):
                errors.append(f"runtime_trade_{index}_{field}_invalid")
        pools = trade.get("pools")
        if not isinstance(pools, list) or not pools:
            errors.append(f"runtime_trade_{index}_pools_empty")
            continue
        for pool_index, pool in enumerate(pools):
            if not isinstance(pool, dict):
                errors.append(f"runtime_trade_{index}_pool_{pool_index}_not_object")
                continue
            if "adapterKind" not in pool:
                errors.append(f"runtime_trade_{index}_pool_{pool_index}_adapterKind_missing")
            pool_address = pool.get("pool")
            if not isinstance(pool_address, str) or not ADDRESS_RE.match(pool_address):
                errors.append(f"runtime_trade_{index}_pool_{pool_index}_pool_invalid")
    return errors


def _delivery_policy_from_result(payload: dict[str, Any], request: dict[str, Any]) -> dict[str, Any]:
    request_policy = request.get("deliveryPolicy") if isinstance(request.get("deliveryPolicy"), dict) else {}
    return {
        **request_policy,
        "broadcastChannel": payload.get("broadcast_channel"),
        "relay": payload.get("relay"),
        "relayErrors": payload.get("relay_errors"),
        "privateRelayMetrics": payload.get("privateRelayMetrics"),
        "publicFallback": None,
        "finalAction": "submitted" if payload.get("submitted") else "dropped_or_shadowed",
    }


def _validate_delivery_and_net_profit(payload: dict[str, Any], request: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    policy = payload.get("deliveryPolicy")
    if not isinstance(policy, dict):
        errors.append("delivery_policy_missing")
        policy = {}
    mode = str(policy.get("mode") or "")
    private_research = policy.get("privateRelayResearchEnabled") is True
    if not mode:
        errors.append("delivery_policy_mode_missing")
    if private_research and mode != "private_relay_research":
        errors.append("private_relay_research_mode_mismatch")
    if not private_research and mode and mode != "public_rpc_direct_after_fresh_gates":
        errors.append("unexpected_delivery_policy_mode")
    if not private_research and policy.get("privateFirst") is True:
        errors.append("private_first_without_research_opt_in")
    if not private_research and policy.get("publicFallbackRequested") is True:
        errors.append("public_fallback_requested_without_research_opt_in")
    if not private_research and policy.get("privacyBoundary") not in (
        None,
        "",
        "deferred_to_cow_or_intent_layer",
    ):
        errors.append("unexpected_privacy_boundary")

    request_policy = request.get("deliveryPolicy") if isinstance(request.get("deliveryPolicy"), dict) else {}
    if request_policy and request_policy.get("mode") != policy.get("mode"):
        errors.append("delivery_policy_request_mismatch")

    model = payload.get("netProfitModel")
    if not isinstance(model, dict):
        return errors
    relay_wei = model.get("relayCostWei")
    delivery_wei = model.get("deliveryCostWei")
    if delivery_wei is None:
        errors.append("delivery_cost_wei_missing")
    elif relay_wei is not None and str(relay_wei) != str(delivery_wei):
        errors.append("delivery_cost_wei_relay_alias_mismatch")
    if "publicMempoolRiskPenaltyUsdc" not in model:
        errors.append("public_mempool_risk_penalty_usdc_missing")
    if "publicMempoolRiskPenaltyBps" not in model:
        errors.append("public_mempool_risk_penalty_bps_missing")
    route_evaluations = payload.get("routeEvaluations")
    if isinstance(route_evaluations, list):
        for index, evaluation in enumerate(route_evaluations):
            if not isinstance(evaluation, dict):
                continue
            net_profit = evaluation.get("netProfit")
            if not isinstance(net_profit, dict):
                continue
            if "deliveryCostWei" not in net_profit:
                errors.append(f"route_evaluation_{index}_delivery_cost_wei_missing")
            elif "relayCostWei" in net_profit and str(net_profit.get("deliveryCostWei")) != str(net_profit.get("relayCostWei")):
                errors.append(f"route_evaluation_{index}_delivery_cost_wei_relay_alias_mismatch")
            if "deliveryCostUsdc" not in net_profit:
                errors.append(f"route_evaluation_{index}_delivery_cost_usdc_missing")
            elif "relayCostUsdc" in net_profit and str(net_profit.get("deliveryCostUsdc")) != str(net_profit.get("relayCostUsdc")):
                errors.append(f"route_evaluation_{index}_delivery_cost_usdc_relay_alias_mismatch")
            if "publicMempoolRiskPenaltyUsdc" not in net_profit:
                errors.append(f"route_evaluation_{index}_public_mempool_risk_penalty_usdc_missing")
            else:
                fixed_penalty = _non_negative_int(net_profit.get("publicMempoolRiskPenaltyFixedUsdc"))
                bps_penalty = _non_negative_int(net_profit.get("publicMempoolRiskPenaltyBpsUsdc"))
                total_penalty = _non_negative_int(net_profit.get("publicMempoolRiskPenaltyUsdc"))
                if fixed_penalty is not None and bps_penalty is not None and total_penalty is not None:
                    if fixed_penalty + bps_penalty != total_penalty:
                        errors.append(f"route_evaluation_{index}_public_mempool_risk_penalty_mismatch")
    return errors


def _net_profit_review_flags(payload: dict[str, Any]) -> list[str]:
    flags: list[str] = []
    threshold_bps = _non_negative_int(payload.get("manualReviewThresholdBps"))
    if threshold_bps is None:
        threshold_bps = MANUAL_REVIEW_THRESHOLD_BPS
    route_evaluations = payload.get("routeEvaluations")
    if not isinstance(route_evaluations, list):
        return flags
    for index, evaluation in enumerate(route_evaluations):
        if not isinstance(evaluation, dict):
            continue
        net_profit = evaluation.get("netProfit")
        if not isinstance(net_profit, dict):
            continue
        expected_profit = _non_negative_int(net_profit.get("expectedProfit"))
        penalty = _non_negative_int(net_profit.get("publicMempoolRiskPenaltyUsdc"))
        if expected_profit and penalty is not None and penalty * 10000 > expected_profit * threshold_bps:
            flags.append(f"route_evaluation_{index}_public_mempool_penalty_gt_50pct_expected_profit")
    return flags


MANUAL_REVIEW_THRESHOLD_BPS = 5000
MANUAL_REVIEW_THRESHOLD_RATIONALE = (
    "conservative_manual_review_guardrail_when_public_mempool_penalty_reaches_"
    "50_percent_of_expected_profit"
)
MIN_RAW_EVIDENCE_RETENTION_DAYS = 90


def _default_regime_definition() -> dict[str, int]:
    return {
        "priceChangeWindowHours": 24,
        "lowMaxBpsExclusive": 100,
        "mediumMinBpsInclusive": 100,
        "mediumMaxBpsInclusive": 500,
        "highMinBpsExclusive": 500,
    }


def _default_metric_source() -> dict[str, Any]:
    return {
        "sourceKind": "unavailable",
        "sourceProvider": None,
        "endpointHost": None,
        "blockRange": None,
        "transactionFilter": None,
        "collectedAt": None,
        "rawEvidencePath": None,
        "rawEvidenceHash": None,
        "retentionUntil": None,
        "unavailableReason": "not_collected",
    }


def default_market_feasibility(*, conclusion: str = "insufficient_data") -> dict[str, Any]:
    return {
        "targetPairLiquidity": {},
        "volatilitySummary": {
            "regimeDefinition": _default_regime_definition(),
            "regimeDefinitionDeclaredAt": None,
            "priceSource": None,
            "currentRegime": "unknown",
        },
        "competitorPressure": {
            "observations": [],
            "inferenceOnly": True,
            "confidence": "insufficient_data",
            "metrics": {
                "opportunityDisappearanceBlocks": None,
                "priceRecoveryMs": None,
                "sameBlockHigherGasIndicator": None,
                "refillFrequency": None,
            },
            "metricSources": {
                "sameBlockHigherGasIndicator": _default_metric_source(),
            },
            "dataLimitations": ["market_feasibility_source_missing"],
            "emptyObservationMeaning": "no_sufficient_data_to_infer_competitor_pressure",
        },
        "expectedProfitDistribution": {
            "expectedProfitUsdc": {"p50": None, "p90": None, "p99": None, "max": None},
            "netProfitAfterGasUsdc": {"p50": None, "p90": None, "p99": None, "max": None},
            "netProfitAfterPublicPenaltyUsdc": {"p50": None, "p90": None, "p99": None, "max": None},
            "positiveCount": 0,
            "sampleCount": 0,
            "sampleSufficiency": "insufficient",
            "sampleSufficiencyReason": "no_healthy_24h_window",
            "candidateGenerationRatePerHour": None,
            "lowFrequencyConfirmation": None,
        },
        "candidateGeneration": {
            "candidateCount": 0,
            "listenerHealth": "unknown",
            "rpcHealth": "unknown",
            "cacheHealth": "unknown",
        },
        "gasPriceRegime": {},
        "strategyDifferentiation": [
            "baseline=public_mempool_triangular_bot_without_multi_state_redundancy",
            "uses_multi_state_redundancy",
            "uses_offchain_net_profit_risk_penalties",
            "uses_fixed_onchain_order",
            "uses_forced_shadow_no_broadcast_gate",
        ],
        "conclusion": conclusion,
        "conclusionReason": "insufficient_online_market_data",
    }


def _normalize_market_feasibility(candidate: dict[str, Any]) -> dict[str, Any]:
    """Map legacy fields only when a caller supplied them explicitly."""
    merged = default_market_feasibility()
    merged.update(candidate)
    competitor = merged.get("competitorPressure")
    if isinstance(competitor, dict):
        supplied_competitor = candidate.get("competitorPressure")
        default_competitor = default_market_feasibility()["competitorPressure"]
        default_competitor.update(competitor)
        if isinstance(supplied_competitor, dict) and "metricSources" not in supplied_competitor:
            legacy_source = supplied_competitor.get("dataSource")
            if isinstance(legacy_source, dict):
                mapped_source = _default_metric_source()
                mapped_source.update(
                    {
                        "endpointHost": legacy_source.get("endpointHost"),
                        "blockRange": legacy_source.get("blockRange"),
                        "transactionFilter": legacy_source.get("scanScope"),
                        "unavailableReason": "legacy_data_source_mapped_without_raw_evidence",
                    }
                )
                default_competitor["metricSources"] = {
                    "sameBlockHigherGasIndicator": mapped_source
                }
        metric_sources = default_competitor.get("metricSources")
        if isinstance(metric_sources, dict):
            same_block = metric_sources.get("sameBlockHigherGasIndicator")
            default_source = _default_metric_source()
            if isinstance(same_block, dict):
                default_source.update(same_block)
            metric_sources["sameBlockHigherGasIndicator"] = default_source
        merged["competitorPressure"] = default_competitor
    expected = merged.get("expectedProfitDistribution")
    if isinstance(expected, dict):
        default_expected = default_market_feasibility()["expectedProfitDistribution"]
        default_expected.update(expected)
        if (
            "candidateGenerationRatePerHour" not in expected
            and "candidateRatePerHour" in expected
        ):
            default_expected["candidateGenerationRatePerHour"] = expected["candidateRatePerHour"]
        if "sampleSufficiencyReason" not in expected and "sampleAssessment" in expected:
            default_expected["sampleSufficiencyReason"] = f"legacy_{expected['sampleAssessment']}"
        merged["expectedProfitDistribution"] = default_expected
    candidate_generation = merged.get("candidateGeneration")
    if isinstance(candidate_generation, dict):
        default_candidate_generation = default_market_feasibility()["candidateGeneration"]
        default_candidate_generation.update(candidate_generation)
        merged["candidateGeneration"] = default_candidate_generation
    return merged


def _market_feasibility_from_inputs(
    *,
    request: dict[str, Any],
    source: dict[str, Any] | None,
    payload: dict[str, Any],
) -> dict[str, Any]:
    for candidate in (
        request.get("marketFeasibility") if isinstance(request, dict) else None,
        (source or {}).get("marketFeasibility") if isinstance(source, dict) else None,
        payload.get("marketFeasibility") if isinstance(payload, dict) else None,
    ):
        if isinstance(candidate, dict):
            return _normalize_market_feasibility(candidate)
    return default_market_feasibility()


def _validate_metric_source(source: Any) -> tuple[list[str], set[str]]:
    errors: list[str] = []
    providers: set[str] = set()
    if not isinstance(source, dict):
        return ["market_feasibility_competitor_metric_source_missing"], providers
    source_kind = source.get("sourceKind")
    if source_kind not in {"confirmed_block_receipts", "pending_websocket", "unavailable"}:
        errors.append("market_feasibility_competitor_metric_source_kind_invalid")
        return errors, providers
    required = (
        "sourceProvider",
        "endpointHost",
        "blockRange",
        "transactionFilter",
        "collectedAt",
        "rawEvidencePath",
        "rawEvidenceHash",
        "retentionUntil",
        "unavailableReason",
    )
    for field in required:
        if field not in source:
            errors.append(f"market_feasibility_competitor_metric_source_{field}_missing")
    if source_kind == "unavailable":
        if not str(source.get("unavailableReason") or ""):
            errors.append("market_feasibility_competitor_metric_source_unavailable_reason_missing")
        return errors, providers
    provider = str(source.get("sourceProvider") or "").strip()
    if not provider:
        errors.append("market_feasibility_competitor_metric_source_provider_missing")
    else:
        providers.add(provider)
    if not str(source.get("endpointHost") or "").strip():
        errors.append("market_feasibility_competitor_metric_source_endpoint_missing")
    if not source.get("blockRange"):
        errors.append("market_feasibility_competitor_metric_source_block_range_missing")
    if not source.get("transactionFilter"):
        errors.append("market_feasibility_competitor_metric_source_transaction_filter_missing")
    collected_at = _parse_utc(source.get("collectedAt"))
    retention_until = _parse_utc(source.get("retentionUntil"))
    if collected_at is None:
        errors.append("market_feasibility_competitor_metric_source_collected_at_invalid")
    if retention_until is None:
        errors.append("market_feasibility_competitor_metric_source_retention_until_invalid")
    elif collected_at and retention_until < collected_at + timedelta(days=MIN_RAW_EVIDENCE_RETENTION_DAYS):
        errors.append("market_feasibility_competitor_metric_source_retention_too_short")
    raw_path = source.get("rawEvidencePath")
    raw_hash = source.get("rawEvidenceHash")
    if not str(raw_path or "").strip():
        errors.append("market_feasibility_competitor_metric_source_raw_path_missing")
    if not isinstance(raw_hash, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", raw_hash):
        errors.append("market_feasibility_competitor_metric_source_raw_hash_invalid")
    return errors, providers


def _validate_low_frequency_confirmation(
    distribution: dict[str, Any],
    candidate_generation: dict[str, Any],
    *,
    listener_started_at: datetime | None,
    listener_stopped_at: datetime | None,
) -> list[str]:
    if distribution.get("sampleSufficiency") != "low_frequency_market":
        return []
    errors: list[str] = []
    duration_hours = (
        (listener_stopped_at - listener_started_at).total_seconds() / 3600
        if listener_started_at and listener_stopped_at and listener_stopped_at >= listener_started_at
        else 0
    )
    if duration_hours < 24:
        errors.append("market_feasibility_low_frequency_requires_healthy_24h_window")
    if _non_negative_number(distribution.get("candidateGenerationRatePerHour")) is None:
        errors.append("market_feasibility_low_frequency_candidate_rate_missing")
    for field in ("listenerHealth", "rpcHealth", "cacheHealth"):
        if candidate_generation.get(field) != "healthy":
            errors.append(f"market_feasibility_low_frequency_{field}_not_healthy")
    confirmation = distribution.get("lowFrequencyConfirmation")
    if not isinstance(confirmation, dict):
        return errors + ["market_feasibility_low_frequency_confirmation_missing"]
    if confirmation.get("sourceKind") not in {"chain_block_scan", "block_explorer_export"}:
        errors.append("market_feasibility_low_frequency_confirmation_source_kind_invalid")
    for field in (
        "provider",
        "blockRange",
        "transactionFilter",
        "statistics",
        "conclusion",
        "confirmedAt",
        "reviewer",
    ):
        if not confirmation.get(field):
            errors.append(f"market_feasibility_low_frequency_confirmation_{field}_missing")
    if _parse_utc(confirmation.get("confirmedAt")) is None:
        errors.append("market_feasibility_low_frequency_confirmation_confirmed_at_invalid")
    return errors


def _validate_volatility_summary(
    summary: Any, *, listener_started_at: datetime | None
) -> list[str]:
    errors: list[str] = []
    if not isinstance(summary, dict):
        return ["market_feasibility_volatility_summary_invalid"]
    definition = summary.get("regimeDefinition")
    if not isinstance(definition, dict):
        errors.append("market_feasibility_regime_definition_missing")
    elif definition != _default_regime_definition():
        errors.append("market_feasibility_regime_definition_invalid")
    declared_at = _parse_utc(summary.get("regimeDefinitionDeclaredAt"))
    if declared_at is None:
        errors.append("market_feasibility_regime_definition_declared_at_invalid")
    elif listener_started_at and declared_at > listener_started_at:
        errors.append("market_feasibility_regime_definition_declared_after_listener_start")
    if "priceSource" not in summary:
        errors.append("market_feasibility_regime_price_source_missing")
    if summary.get("currentRegime") not in {"low", "medium", "high", "unknown"}:
        errors.append("market_feasibility_current_regime_invalid")
    return errors


def _validate_manual_review_threshold(payload: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    threshold = _non_negative_int(payload.get("manualReviewThresholdBps"))
    if threshold is None:
        errors.append("manual_review_threshold_invalid")
        return errors
    if not str(payload.get("manualReviewThresholdRationale") or "").strip():
        errors.append("manual_review_threshold_rationale_missing")
    history = payload.get("manualReviewThresholdAdjustmentHistory")
    if not isinstance(history, list):
        return errors + ["manual_review_threshold_adjustment_history_invalid"]
    if threshold != MANUAL_REVIEW_THRESHOLD_BPS and not history:
        errors.append("manual_review_threshold_non_default_without_adjustment_history")
    for index, adjustment in enumerate(history):
        if not isinstance(adjustment, dict):
            errors.append(f"manual_review_threshold_adjustment_{index}_invalid")
            continue
        for field in (
            "previousThresholdBps",
            "newThresholdBps",
            "proposedAt",
            "effectiveScope",
            "independentWindowStatistics",
            "competitorPressureSummary",
            "reviewConclusion",
            "approvedBy",
            "rollbackCondition",
        ):
            if adjustment.get(field) in (None, "", []):
                errors.append(f"manual_review_threshold_adjustment_{index}_{field}_missing")
        if _non_negative_int(adjustment.get("previousThresholdBps")) is None:
            errors.append(f"manual_review_threshold_adjustment_{index}_previous_threshold_invalid")
        if _non_negative_int(adjustment.get("newThresholdBps")) is None:
            errors.append(f"manual_review_threshold_adjustment_{index}_new_threshold_invalid")
        if _parse_utc(adjustment.get("proposedAt")) is None:
            errors.append(f"manual_review_threshold_adjustment_{index}_proposed_at_invalid")
        statistics = adjustment.get("independentWindowStatistics")
        if not isinstance(statistics, list) or len(statistics) < 3:
            errors.append(f"manual_review_threshold_adjustment_{index}_independent_windows_insufficient")
        if adjustment.get("automatic") is True:
            errors.append(f"manual_review_threshold_adjustment_{index}_automatic_change_forbidden")
    return errors


def _validate_market_feasibility(payload: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    market = payload.get("marketFeasibility")
    if not isinstance(market, dict):
        return ["market_feasibility_missing"]
    required_fields = (
        "targetPairLiquidity",
        "volatilitySummary",
        "competitorPressure",
        "expectedProfitDistribution",
        "gasPriceRegime",
        "strategyDifferentiation",
        "conclusion",
    )
    for field in required_fields:
        if field not in market:
            errors.append(f"market_feasibility_{field}_missing")
    competitor = market.get("competitorPressure")
    if not isinstance(competitor, dict):
        errors.append("market_feasibility_competitor_pressure_invalid")
    else:
        observations = competitor.get("observations")
        if not isinstance(observations, list):
            errors.append("market_feasibility_competitor_observations_invalid")
        if observations == [] and "emptyObservationMeaning" not in competitor:
            errors.append("market_feasibility_empty_observation_meaning_missing")
        if not isinstance(competitor.get("dataLimitations"), list):
            errors.append("market_feasibility_competitor_data_limitations_invalid")
        metrics = competitor.get("metrics")
        if not isinstance(metrics, dict):
            errors.append("market_feasibility_competitor_metrics_missing")
        else:
            for field in (
                "opportunityDisappearanceBlocks",
                "priceRecoveryMs",
                "sameBlockHigherGasIndicator",
                "refillFrequency",
            ):
                if field not in metrics:
                    errors.append(f"market_feasibility_competitor_metric_{field}_missing")
        confidence = competitor.get("confidence")
        if confidence not in {
            "insufficient_data",
            "low_confidence",
            "medium_confidence",
            "high_confidence",
        }:
            errors.append("market_feasibility_competitor_confidence_invalid")
        if competitor.get("inferenceOnly") is not True:
            errors.append("market_feasibility_competitor_inference_flag_missing")
        metric_sources = competitor.get("metricSources")
        if not isinstance(metric_sources, dict):
            errors.append("market_feasibility_competitor_metric_sources_missing")
        else:
            source_errors, providers = _validate_metric_source(
                metric_sources.get("sameBlockHigherGasIndicator")
            )
            errors.extend(source_errors)
            additional_sources = metric_sources.get("independentSources", [])
            if not isinstance(additional_sources, list):
                errors.append("market_feasibility_competitor_independent_sources_invalid")
                additional_sources = []
            for source in additional_sources:
                source_errors, source_providers = _validate_metric_source(source)
                errors.extend(source_errors)
                providers.update(source_providers)
            if confidence == "high_confidence" and len(providers) < 2:
                errors.append("market_feasibility_competitor_sources_not_independent")
    if not isinstance(market.get("strategyDifferentiation"), list) or not market.get("strategyDifferentiation"):
        errors.append("market_feasibility_strategy_differentiation_missing")
    elif not any(
        str(item).startswith("baseline=public_mempool_triangular_bot")
        for item in market["strategyDifferentiation"]
    ):
        errors.append("market_feasibility_strategy_baseline_missing")
    if market.get("conclusion") not in {
        "insufficient_data",
        "not_economically_supported",
        "observe_longer",
        "positive_signal_requires_review",
        "ready_for_G07c_review",
    }:
        errors.append("market_feasibility_conclusion_invalid")
    distribution = market.get("expectedProfitDistribution")
    if not isinstance(distribution, dict):
        errors.append("market_feasibility_profit_distribution_invalid")
    else:
        sample_count = _non_negative_int(distribution.get("sampleCount"))
        if sample_count is None:
            errors.append("market_feasibility_sample_count_invalid")
        sufficiency = distribution.get("sampleSufficiency")
        if sufficiency not in {"sufficient", "low_frequency_market", "insufficient"}:
            errors.append("market_feasibility_sample_sufficiency_invalid")
        expected_sufficiency = "sufficient" if sample_count is not None and sample_count >= 100 else None
        if expected_sufficiency and sufficiency != expected_sufficiency:
            errors.append("market_feasibility_sample_sufficiency_mismatch")
        if not str(distribution.get("sampleSufficiencyReason") or ""):
            errors.append("market_feasibility_sample_sufficiency_reason_missing")
    candidate_generation = market.get("candidateGeneration")
    if not isinstance(candidate_generation, dict):
        errors.append("market_feasibility_candidate_generation_missing")
    else:
        listener_started_at = _parse_utc(payload.get("listenerStartedAt"))
        listener_stopped_at = _parse_utc(payload.get("listenerStoppedAt"))
        errors.extend(
            _validate_low_frequency_confirmation(
                distribution if isinstance(distribution, dict) else {},
                candidate_generation,
                listener_started_at=listener_started_at,
                listener_stopped_at=listener_stopped_at,
            )
        )
    errors.extend(
        _validate_volatility_summary(
            market.get("volatilitySummary"),
            listener_started_at=_parse_utc(payload.get("listenerStartedAt")),
        )
    )
    return errors


def _validate_pre_pause_evidence(payload: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    pre_pause = payload.get("prePause")
    if not isinstance(pre_pause, dict) or pre_pause.get("prePause") is not True:
        return errors
    trigger_source = str(pre_pause.get("pauseTriggerSource") or "")
    if trigger_source not in {"manual", "circuit_breaker", "watchdog", "external_alert"}:
        errors.append("pre_pause_trigger_source_invalid")
    detected = _parse_utc(pre_pause.get("pauseDetectedAt"))
    blocked = _parse_utc(pre_pause.get("signerBlockedAt"))
    propagation_ms = _non_negative_int(pre_pause.get("pausePropagationMs"))
    if detected is None:
        errors.append("pre_pause_detected_at_invalid")
    if blocked is None:
        errors.append("pre_pause_signer_blocked_at_invalid")
    if detected and blocked and blocked < detected:
        errors.append("pre_pause_timeline_invalid")
    if detected and blocked and propagation_ms is not None:
        expected_ms = max(0, int((blocked - detected).total_seconds() * 1000))
        if abs(propagation_ms - expected_ms) > 1:
            errors.append("pre_pause_propagation_ms_mismatch")
    elif propagation_ms is None:
        errors.append("pre_pause_propagation_ms_invalid")
    return errors


def _non_negative_int(value: Any) -> int | None:
    try:
        parsed = int(str(value))
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def _non_negative_number(value: Any) -> float | None:
    try:
        parsed = float(str(value))
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def _status_family(status: str) -> str:
    if status in {"preview_passed"}:
        return "preview_only"
    if status in {"static_call_passed"}:
        return "shadow_static_success"
    if status in {"submitted_success"}:
        return "broadcast_success"
    if status in {"submitted_failed", "submission_failed"}:
        return "broadcast_failed"
    if status in {
        "broadcast_disabled",
        "gas_price_cap_exceeded",
        "net_profit_not_positive",
        "direct_pre_pause_active",
        "direct_circuit_breaker_paused",
    }:
        return "broadcast_blocked"
    if status in {
        "gas_token_price_source_health_failed",
        "runtime_cache_unverified",
        "aave_non_usdc_borrow_unverified",
        "net_profit_model_incomplete",
    }:
        return "preflight_blocked"
    if status in {
        "direct_protocol_incomplete",
        "network_config_missing",
        "execution_preflight_failed",
    }:
        return "diagnostic"
    if status in {"unknown", ""}:
        return "unknown"
    return "other"


def build_unified_live_signal_evidence(
    *,
    run_id: str,
    signal_detected_at: str,
    validated_at: str,
    submitted_or_dropped_at: str,
    result: dict[str, Any],
    source: dict[str, Any] | None = None,
    mode: str = "shadow",
    process: dict[str, Any] | None = None,
    live_signal_only: bool = True,
    positive_profit_proven: bool | None = None,
) -> dict[str, Any]:
    """Build a report without claiming that a shadow result is a live profit proof."""
    payload = result if isinstance(result, dict) else {}
    request = payload.get("request") if isinstance(payload.get("request"), dict) else {}
    runtime_trades = _runtime_trades_from_result(payload)
    signal_at = _parse_utc(signal_detected_at)
    validated = _parse_utc(validated_at)
    terminal = _parse_utc(submitted_or_dropped_at)
    signal_age_ms = (
        max(0, int((validated - signal_at).total_seconds() * 1000))
        if signal_at and validated
        else None
    )
    latency_budget_ms = (
        max(0, int((terminal - signal_at).total_seconds() * 1000))
        if signal_at and terminal
        else None
    )
    static_call = payload.get("static_call") if isinstance(payload.get("static_call"), dict) else {}
    preflight = payload.get("preflight") if isinstance(payload.get("preflight"), dict) else {}
    is_shadow = mode == "shadow"
    timing_source = process or {}
    min_capturable_window_ms = timing_source.get("minCapturableWindowMs")
    if min_capturable_window_ms is None:
        min_capturable_window_ms = request.get("minCapturableWindowMs")
    if min_capturable_window_ms is None:
        min_capturable_window_ms = latency_budget_ms
    request_manual_review_threshold = _non_negative_int(request.get("manualReviewThresholdBps")) if isinstance(request, dict) else None
    manual_review_threshold_bps = (
        request_manual_review_threshold
        if request_manual_review_threshold is not None
        else MANUAL_REVIEW_THRESHOLD_BPS
    )
    result_summary = {
        "ok": bool(payload.get("ok")),
        "submitted": bool(payload.get("submitted")),
        "status": str(payload.get("status") or ""),
        "statusFamily": _status_family(str(payload.get("status") or "")),
        "blockedReason": payload.get("blocked_reason"),
        "network": payload.get("network"),
        "chainId": payload.get("chain_id"),
        "executorAddress": payload.get("executor_address"),
        "selectedStrategyStatus": request.get("selectedStrategyStatus"),
        "selectedExecutionKind": request.get("selectedExecutionKind"),
    }
    process_payload = process or {}
    listener_started_at = (
        process_payload.get("listenerStartedAt")
        or request.get("listenerStartedAt")
        or signal_detected_at
    )
    market_feasibility = _market_feasibility_from_inputs(
        request=request,
        source=source,
        payload=payload,
    )
    volatility = market_feasibility.get("volatilitySummary")
    if isinstance(volatility, dict) and not volatility.get("regimeDefinitionDeclaredAt"):
        volatility["regimeDefinitionDeclaredAt"] = listener_started_at
    evidence = {
        "schemaVersion": UNIFIED_LIVE_SIGNAL_SCHEMA_VERSION,
        "schemaConstantVersion": UNIFIED_LIVE_SIGNAL_SCHEMA_CONSTANT_VERSION,
        "runId": str(run_id),
        "mode": mode,
        "evidenceSemantics": {
            "liveSignalOnly": bool(live_signal_only),
            "historicalForkOnly": False,
            "broadcastPerformed": bool(payload.get("submitted")),
            "shadowOnly": is_shadow,
            "positiveProfitProven": bool(positive_profit_proven) if positive_profit_proven is not None else False,
        },
        "timestamps": {
            "signalDetectedAt": signal_detected_at,
            "validatedAt": validated_at,
            "submittedOrDroppedAt": submitted_or_dropped_at,
            "signalAgeMs": signal_age_ms,
            "latencyBudgetMs": latency_budget_ms,
        },
        "minCapturableWindowMs": min_capturable_window_ms,
        "source": source or {},
        "process": process_payload,
        "listenerStartedAt": listener_started_at,
        "result": result_summary,
        "request": request,
        "runtimeTrades": runtime_trades,
        "runtimeTradesHash": canonical_json_hash(runtime_trades),
        "requestHash": canonical_json_hash(request),
        "resultHash": canonical_json_hash(result_summary),
        "preview": preflight,
        "staticCall": static_call,
        "routeEvaluations": request.get("routeEvaluations") if isinstance(request.get("routeEvaluations"), list) else [],
        "netProfitModel": request.get("netProfitModel") if isinstance(request.get("netProfitModel"), dict) else {},
        "marketFeasibility": market_feasibility,
        "selectedTokenRisk": request.get("selectedTokenRisk") if isinstance(request.get("selectedTokenRisk"), dict) else {},
        "cacheValidation": request.get("cacheValidation") if isinstance(request.get("cacheValidation"), dict) else {},
        "deliveryPolicy": _delivery_policy_from_result(payload, request),
        "circuitBreaker": request.get("circuitBreaker") if isinstance(request.get("circuitBreaker"), dict) else {},
        "prePause": payload.get("prePause") if isinstance(payload.get("prePause"), dict) else {},
        "schemaValidation": {"ok": True, "errors": []},
        "reviewFlags": [],
        "manualReviewThresholdBps": str(manual_review_threshold_bps),
        "manualReviewThresholdBpsSource": (
            request.get("manualReviewThresholdBpsSource")
            or "conservative_experience_default_50pct_expected_profit"
        ),
        "manualReviewThresholdRationale": (
            request.get("manualReviewThresholdRationale")
            or MANUAL_REVIEW_THRESHOLD_RATIONALE
        ),
        "manualReviewThresholdAdjustmentHistory": (
            request.get("manualReviewThresholdAdjustmentHistory")
            if isinstance(request.get("manualReviewThresholdAdjustmentHistory"), list)
            else []
        ),
    }
    evidence["evidenceHash"] = canonical_json_hash(evidence)
    return evidence


def validate_unified_live_signal_evidence(payload: dict[str, Any]) -> dict[str, Any]:
    """Return deterministic validation errors so report failure cannot look successful."""
    errors: list[str] = []
    if not isinstance(payload, dict):
        return {"ok": False, "errors": ["evidence_not_object"]}
    if payload.get("schemaVersion") != UNIFIED_LIVE_SIGNAL_SCHEMA_VERSION:
        errors.append("unsupported_schema_version")
    if payload.get("schemaConstantVersion") != UNIFIED_LIVE_SIGNAL_SCHEMA_CONSTANT_VERSION:
        errors.append("unsupported_schema_constant_version")
    semantics = payload.get("evidenceSemantics")
    if not isinstance(semantics, dict) or semantics.get("liveSignalOnly") is not True:
        errors.append("live_signal_semantics_missing")
    if semantics and semantics.get("positiveProfitProven") is True and not semantics.get("broadcastPerformed"):
        errors.append("positive_profit_claim_without_broadcast")
    if payload.get("mode") == "shadow":
        if not isinstance(semantics, dict) or semantics.get("shadowOnly") is not True:
            errors.append("shadow_mode_semantics_missing")
        if isinstance(semantics, dict) and semantics.get("broadcastPerformed") is True:
            errors.append("shadow_broadcast_detected")
    timestamps = payload.get("timestamps")
    if not isinstance(timestamps, dict):
        errors.append("timestamps_missing")
        timestamps = {}
    timeline = [
        _parse_utc(timestamps.get("signalDetectedAt")),
        _parse_utc(timestamps.get("validatedAt")),
        _parse_utc(timestamps.get("submittedOrDroppedAt")),
    ]
    if any(value is None for value in timeline):
        errors.append("timestamp_invalid")
    elif timeline[0] > timeline[1] or timeline[1] > timeline[2]:
        errors.append("timestamp_order_invalid")
    runtime_trades = payload.get("runtimeTrades")
    if not isinstance(runtime_trades, list):
        errors.append("runtime_trades_missing")
    elif not runtime_trades:
        errors.append("runtime_trades_empty")
    else:
        errors.extend(_validate_runtime_trade_shape(runtime_trades))
        if payload.get("runtimeTradesHash") != canonical_json_hash(runtime_trades):
            errors.append("runtime_trades_hash_mismatch")
    request = payload.get("request")
    if not isinstance(request, dict):
        errors.append("request_missing")
        request = {}
    elif payload.get("requestHash") != canonical_json_hash(request):
        errors.append("request_hash_mismatch")
    result = payload.get("result")
    if not isinstance(result, dict):
        errors.append("result_missing")
        result = {}
    elif payload.get("resultHash") != canonical_json_hash(result):
        errors.append("result_hash_mismatch")
    result_status = str(result.get("status") or "")
    result_status_family = str(result.get("statusFamily") or "")
    if result_status_family != _status_family(result_status):
        errors.append("result_status_family_mismatch")
    request_hash_payload = {
        key: value
        for key, value in payload.items()
        if key != "evidenceHash"
    }
    if payload.get("evidenceHash") != canonical_json_hash(request_hash_payload):
        errors.append("evidence_hash_mismatch")
    if not result.get("status"):
        errors.append("result_status_missing")
    errors.extend(_validate_delivery_and_net_profit(payload, request))
    errors.extend(_validate_market_feasibility(payload))
    errors.extend(_validate_pre_pause_evidence(payload))
    errors.extend(_validate_manual_review_threshold(payload))
    schema_validation = payload.get("schemaValidation")
    if not isinstance(schema_validation, dict) or "ok" not in schema_validation:
        errors.append("schema_validation_missing")
    elif schema_validation.get("ok") is not True:
        errors.append("schema_validation_failed")
    elif schema_validation.get("errors") not in (None, []):
        errors.append("schema_validation_errors_present")
    review_flags = _net_profit_review_flags(payload)
    disposition = "g07d_passed"
    if errors:
        disposition = "diagnostic_only_notify_ops"
    elif review_flags:
        disposition = "manual_review_required"
    threshold_bps = _non_negative_int(payload.get("manualReviewThresholdBps"))
    return {
        "ok": not errors,
        "errors": errors,
        "reviewFlags": review_flags,
        "disposition": disposition,
        "manualReviewThresholdBps": str(
            threshold_bps if threshold_bps is not None else MANUAL_REVIEW_THRESHOLD_BPS
        ),
    }


def write_unified_live_signal_evidence(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(f"{path.suffix}.tmp")
    tmp_path.write_text(
        json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True, default=str),
        encoding="utf-8",
    )
    tmp_path.replace(path)
