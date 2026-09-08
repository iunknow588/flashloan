import inspect
import json
import sys
from datetime import datetime, timezone
from types import ModuleType, SimpleNamespace

from dex.intent_trade import (
    bind_cow_intent_context,
    build_cow_intent_trade,
    build_triangular_onchain_intent_trade,
    pair_index_from_tokenx_tokeny,
    set_usdc_pair_memory_table,
    submit_cow_intent_trade,
)
from dex.intent_trade import direct
from dex.intent_trade import direct_utils
from dex.intent_trade import live_evidence
from dex.intent_trade.unified_live_signal_schema import (
    UNIFIED_LIVE_SIGNAL_REQUIRED_FIELDS,
    UNIFIED_LIVE_SIGNAL_SCHEMA_CONSTANT_VERSION,
    UNIFIED_LIVE_SIGNAL_SCHEMA_VERSION,
    unified_live_signal_schema,
)


def _addr(n: int) -> str:
    return "0x" + f"{n:040x}"


def _clear_direct_pair_env(monkeypatch):
    for name in (
        "TRIANGULAR_USDC_PAIR_ID",
        "TRIANGULAR_PAIR_ID",
        "TRIANGULAR_USDC_PAIRS_JSON",
        "TRIANGULAR_TOKEN_X",
        "TRIANGULAR_TOKEN_Y",
        "TRIANGULAR_DEX_ROUTER",
        "DEX_ROUTER_ADDRESS",
        "FUJI_DEX_ROUTER",
        "TRIANGULAR_RUNTIME_TRADES_JSON",
        "UNIFIED_EXECUTOR_ADDRESS",
        "TRIANGULAR_UNIFIED_EXECUTOR_ADDRESS",
        "UNIFIED_EXECUTOR_ARTIFACT",
        "TRIANGULAR_DIRECT_USE_DYNAMIC_CANDIDATES",
        "TRIANGULAR_DIRECT_CANDIDATE_STRATEGY",
        "TRIANGULAR_ENABLE_NON_USDC_CROSS_POOL",
        "TRIANGULAR_DIRECT_ENABLE_TOKEN_X_CROSS_POOL",
        "TRIANGULAR_MIN_PROFIT_USDC",
        "TRIANGULAR_MIN_PROFIT_USDC_BASE_UNITS",
        "TRIANGULAR_MIN_NET_PROFIT_USDC",
        "TRIANGULAR_MIN_NET_PROFIT_USDC_BASE_UNITS",
        "TRIANGULAR_EXECUTION_MIN_PROFIT_USDC",
        "TRIANGULAR_USDC_ADDRESS",
        "USDC_ADDRESS",
        "TRIANGULAR_DIRECT_NETWORK",
        "TRIANGULAR_ONCHAIN_NETWORK",
        "TRIANGULAR_TESTNET_NAME",
        "TRIANGULAR_NETWORK",
        "TRIANGULAR_TESTNET_CHAIN_ID",
        "ROBINHOOD_RPC_URL",
        "ROBINHOOD_RPC",
        "ROBINHOOD_RPCS",
        "ROBINHOOD_MAINNET_RPC_URL",
        "ROBINHOOD_MAINNET_RPC",
        "ROBINHOOD_TESTNET_RPC_URL",
        "ROBINHOOD_TESTNET_RPC",
        "ROBINHOOD_TESTNET_RPCS",
        "ROBINHOOD_USDC_ADDRESS",
        "ROBINHOOD_TESTNET_USDC_ADDRESS",
        "AVALANCHE_USDC_ADDRESS",
        "AVALANCHE_USDC",
        "AAVE_RESERVE_CACHE_FILE",
        "AVAX_USDC_PRICE",
        "GAS_TOKEN_USDC_PRICE",
        "AVAX_USDC_PRICE_UPDATED_AT",
        "GAS_TOKEN_USDC_PRICE_UPDATED_AT",
        "AVAX_USDC_PRICE_MAX_AGE_SECONDS",
        "GAS_TOKEN_USDC_PRICE_MAX_AGE_SECONDS",
        "UNIFIED_EXECUTOR_CACHE_RISK_PENALTY_USDC",
        "TRIANGULAR_CACHE_RISK_PENALTY_USDC",
        "UNIFIED_EXECUTOR_CACHE_RISK_PENALTY_PER_BLOCK_USDC",
        "TRIANGULAR_CACHE_RISK_PENALTY_PER_BLOCK_USDC",
        "UNIFIED_EXECUTOR_TOKEN_REGISTRY_FILE",
        "TRIANGULAR_TOKEN_REGISTRY_FILE",
        "DIRECT_ONCHAIN_PRE_PAUSE_FILE",
        "UNIFIED_EXECUTOR_PRE_PAUSE_FILE",
        "UNIFIED_EXECUTOR_PRICE_SOURCES_JSON",
        "UNIFIED_EXECUTOR_PRIVATE_RELAY_RESEARCH_ENABLED",
        "TRIANGULAR_PRIVATE_RELAY_RESEARCH_ENABLED",
        "LIQUIDATION_PRIVATE_RELAY_RESEARCH_ENABLED",
        "UNIFIED_EXECUTOR_ALLOW_PUBLIC_FALLBACK",
        "TRIANGULAR_ALLOW_PUBLIC_FALLBACK",
        "UNIFIED_EXECUTOR_PUBLIC_MEMPOOL_RISK_PENALTY_USDC",
        "TRIANGULAR_PUBLIC_MEMPOOL_RISK_PENALTY_USDC",
        "UNIFIED_EXECUTOR_PUBLIC_MEMPOOL_RISK_PENALTY_BPS",
        "TRIANGULAR_PUBLIC_MEMPOOL_RISK_PENALTY_BPS",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(direct, "_USDC_PAIR_MEMORY_TABLE", [])
    monkeypatch.setattr(direct, "_UNIFIED_EXECUTOR_ABI_CACHE", None)


def _runtime_trades_json(token_x: str | None = None, token_y: str | None = None, pool: str | None = None) -> str:
    return json.dumps([
        {
            "tradeIndex": 0,
            "tokenX": token_x or _addr(101),
            "tokenY": token_y or _addr(102),
            "pools": [
                {"adapterKind": 1, "pool": pool or _addr(103)},
                {"adapterKind": 1, "pool": _addr(104)},
            ],
        }
    ])


def _unified_runtime_trades_json(
    usdc: str | None = None,
    token_x: str | None = None,
    token_y: str | None = None,
) -> str:
    usdc = usdc or _addr(500)
    token_x = token_x or _addr(501)
    token_y = token_y or _addr(502)
    return json.dumps([
        {
            "tradeIndex": 0,
            "tokenX": usdc,
            "tokenY": token_x,
            "strategyStatus": 1,
            "routeKey": "AAA:BBB",
            "pools": [{"adapterKind": 1, "pool": _addr(601)}, {"adapterKind": 1, "pool": _addr(602)}],
        },
        {
            "tradeIndex": 1,
            "tokenX": usdc,
            "tokenY": token_y,
            "strategyStatus": 2,
            "routeKey": "AAA:BBB",
            "pools": [{"adapterKind": 1, "pool": _addr(603)}, {"adapterKind": 1, "pool": _addr(604)}],
        },
        {
            "tradeIndex": 2,
            "tokenX": token_x,
            "tokenY": token_y,
            "strategyStatus": 3,
            "routeKey": "AAA:BBB",
            "pools": [{"adapterKind": 1, "pool": _addr(605)}, {"adapterKind": 1, "pool": _addr(606)}],
        },
        {
            "tradeIndex": 3,
            "tokenX": token_y,
            "tokenY": token_x,
            "strategyStatus": 5,
            "routeKey": "AAA:BBB",
            "pools": [{"adapterKind": 1, "pool": _addr(607)}, {"adapterKind": 1, "pool": _addr(608)}],
        },
    ])


def _live_runtime_trade(
    token_x: str | None = None,
    token_y: str | None = None,
    pool: str | None = None,
) -> dict:
    return {
        "tradeIndex": 0,
        "tokenX": token_x or _addr(500),
        "tokenY": token_y or _addr(501),
        "pools": [{"adapterKind": 1, "pool": pool or _addr(601)}],
    }


def _live_signal_result(
    *,
    runtime_trades: list[dict] | None = None,
    status: str = "static_call_passed",
    submitted: bool = False,
    request_overrides: dict | None = None,
) -> dict:
    request = {
        "runtimeTrades": runtime_trades if runtime_trades is not None else [_live_runtime_trade()],
        "deliveryPolicy": {
            "mode": "public_rpc_direct_after_fresh_gates",
            "privateRelayResearchEnabled": False,
            "privateFirst": False,
            "publicFallbackRequested": False,
            "publicFallbackRequiresRevalidation": False,
            "privacyBoundary": "deferred_to_cow_or_intent_layer",
        },
        "netProfitModel": {
            "enabled": "false",
            "relayCostWei": "0",
            "deliveryCostWei": "0",
            "publicMempoolRiskPenaltyUsdc": "0",
            "publicMempoolRiskPenaltyBps": "0",
        },
        "routeEvaluations": [
            {
                "netProfit": {
                    "relayCostWei": "0",
                    "relayCostUsdc": "0",
                    "deliveryCostWei": "0",
                    "deliveryCostUsdc": "0",
                    "publicMempoolRiskPenaltyUsdc": "0",
                    "publicMempoolRiskPenaltyFixedUsdc": "0",
                    "publicMempoolRiskPenaltyBps": "0",
                    "publicMempoolRiskPenaltyBpsUsdc": "0",
                }
            }
        ],
    }
    if request_overrides:
        request.update(request_overrides)
    return {
        "ok": status in {"static_call_passed", "submitted_success"},
        "submitted": submitted,
        "status": status,
        "request": request,
    }


def _market_row(symbol: str, change: float) -> dict:
    return {
        "symbol": f"{symbol}USDT",
        "base_symbol": symbol,
        "change_percent": change,
        "current_price": 1.0,
        "window_ready": True,
    }


def _pool_cache_for_symbols(symbols: list[str]) -> dict:
    token_by_symbol = {symbol: _addr(500 + index) for index, symbol in enumerate(symbols)}
    pools = []
    pool_id = 700
    for x_symbol in symbols:
        for y_symbol in symbols:
            if x_symbol == y_symbol:
                continue
            pools.append(
                {
                    "tokenX_symbol": x_symbol,
                    "tokenY_symbol": y_symbol,
                    "tokenX": token_by_symbol[x_symbol],
                    "tokenY": token_by_symbol[y_symbol],
                    "pools": [
                        {"adapterKind": 1, "pool": _addr(pool_id)},
                        {"adapterKind": 1, "pool": _addr(pool_id + 1)},
                        {"adapterKind": 2, "pool": _addr(pool_id + 2)},
                    ],
                }
            )
            pool_id += 3
    return {"network": "avalanche", "protocols": ["uniswap_v3"], "pools": pools}


def _pool_cache_with_metadata(symbols: list[str], *, chain_id: int = 43114, block_number: int = 100) -> dict:
    cache = _pool_cache_for_symbols(symbols)
    cache["chain_id"] = chain_id
    cache["block_number"] = block_number
    return cache


def test_live_signal_evidence_rejects_tampered_runtime_trades_hash():
    result = _live_signal_result()
    evidence = live_evidence.build_unified_live_signal_evidence(
        run_id="run-1",
        signal_detected_at="2026-08-12T00:00:00+00:00",
        validated_at="2026-08-12T00:00:00.050000+00:00",
        submitted_or_dropped_at="2026-08-12T00:00:00.060000+00:00",
        result=result,
    )

    assert live_evidence.validate_unified_live_signal_evidence(evidence)["ok"] is True
    evidence["runtimeTrades"][0]["tokenY"] = _addr(502)
    validation = live_evidence.validate_unified_live_signal_evidence(evidence)

    assert validation["ok"] is False
    assert "runtime_trades_hash_mismatch" in validation["errors"]


def test_live_signal_evidence_rejects_schema_constant_version_drift():
    evidence = live_evidence.build_unified_live_signal_evidence(
        run_id="run-schema-version",
        signal_detected_at="2026-08-12T00:00:00+00:00",
        validated_at="2026-08-12T00:00:00.050000+00:00",
        submitted_or_dropped_at="2026-08-12T00:00:00.060000+00:00",
        result=_live_signal_result(),
    )
    evidence["schemaConstantVersion"] = "unified_live_signal_schema:v0"
    validation = live_evidence.validate_unified_live_signal_evidence(evidence)

    assert validation["ok"] is False
    assert "unsupported_schema_constant_version" in validation["errors"]


def test_live_signal_evidence_rejects_result_hash_tampering():
    result = _live_signal_result()
    evidence = live_evidence.build_unified_live_signal_evidence(
        run_id="run-1",
        signal_detected_at="2026-08-12T00:00:00+00:00",
        validated_at="2026-08-12T00:00:00.050000+00:00",
        submitted_or_dropped_at="2026-08-12T00:00:00.060000+00:00",
        result=result,
    )

    evidence["result"]["status"] = "submitted_success"
    validation = live_evidence.validate_unified_live_signal_evidence(evidence)

    assert validation["ok"] is False
    assert "result_hash_mismatch" in validation["errors"]


def test_live_signal_evidence_rejects_result_status_family_tampering():
    result = _live_signal_result()
    evidence = live_evidence.build_unified_live_signal_evidence(
        run_id="run-family-tamper",
        signal_detected_at="2026-08-12T00:00:00+00:00",
        validated_at="2026-08-12T00:00:00.050000+00:00",
        submitted_or_dropped_at="2026-08-12T00:00:00.060000+00:00",
        result=result,
    )

    evidence["result"]["statusFamily"] = "broadcast_blocked"
    validation = live_evidence.validate_unified_live_signal_evidence(evidence)

    assert validation["ok"] is False
    assert "result_status_family_mismatch" in validation["errors"]


def test_live_signal_evidence_records_default_delivery_policy_and_delivery_cost_aliases():
    evidence = live_evidence.build_unified_live_signal_evidence(
        run_id="run-delivery-policy",
        signal_detected_at="2026-08-12T00:00:00+00:00",
        validated_at="2026-08-12T00:00:00.050000+00:00",
        submitted_or_dropped_at="2026-08-12T00:00:00.060000+00:00",
        result=_live_signal_result(),
    )

    validation = live_evidence.validate_unified_live_signal_evidence(evidence)

    assert validation["ok"] is True
    assert evidence["deliveryPolicy"]["mode"] == "public_rpc_direct_after_fresh_gates"
    assert evidence["deliveryPolicy"]["privateFirst"] is False
    assert evidence["deliveryPolicy"]["privacyBoundary"] == "deferred_to_cow_or_intent_layer"
    assert evidence["netProfitModel"]["deliveryCostWei"] == evidence["netProfitModel"]["relayCostWei"]
    assert evidence["marketFeasibility"]["conclusion"] == "insufficient_data"
    assert evidence["marketFeasibility"]["competitorPressure"]["emptyObservationMeaning"] == (
        "no_sufficient_data_to_infer_competitor_pressure"
    )


def test_live_signal_evidence_rejects_missing_market_feasibility():
    evidence = live_evidence.build_unified_live_signal_evidence(
        run_id="run-market-feasibility-missing",
        signal_detected_at="2026-08-12T00:00:00+00:00",
        validated_at="2026-08-12T00:00:00.050000+00:00",
        submitted_or_dropped_at="2026-08-12T00:00:00.060000+00:00",
        result=_live_signal_result(),
    )
    del evidence["marketFeasibility"]
    evidence["evidenceHash"] = live_evidence.canonical_json_hash(
        {key: value for key, value in evidence.items() if key != "evidenceHash"}
    )

    validation = live_evidence.validate_unified_live_signal_evidence(evidence)

    assert validation["ok"] is False
    assert "market_feasibility_missing" in validation["errors"]


def test_live_signal_evidence_rejects_competitor_pressure_without_empty_meaning():
    evidence = live_evidence.build_unified_live_signal_evidence(
        run_id="run-market-feasibility-empty-meaning",
        signal_detected_at="2026-08-12T00:00:00+00:00",
        validated_at="2026-08-12T00:00:00.050000+00:00",
        submitted_or_dropped_at="2026-08-12T00:00:00.060000+00:00",
        result=_live_signal_result(),
    )
    del evidence["marketFeasibility"]["competitorPressure"]["emptyObservationMeaning"]
    evidence["evidenceHash"] = live_evidence.canonical_json_hash(
        {key: value for key, value in evidence.items() if key != "evidenceHash"}
    )

    validation = live_evidence.validate_unified_live_signal_evidence(evidence)

    assert validation["ok"] is False
    assert "market_feasibility_empty_observation_meaning_missing" in validation["errors"]


def test_live_signal_evidence_rejects_delivery_cost_alias_mismatch():
    result = _live_signal_result()
    result["request"]["netProfitModel"]["deliveryCostWei"] = "1"
    evidence = live_evidence.build_unified_live_signal_evidence(
        run_id="run-delivery-cost-mismatch",
        signal_detected_at="2026-08-12T00:00:00+00:00",
        validated_at="2026-08-12T00:00:00.050000+00:00",
        submitted_or_dropped_at="2026-08-12T00:00:00.060000+00:00",
        result=result,
    )

    validation = live_evidence.validate_unified_live_signal_evidence(evidence)

    assert validation["ok"] is False
    assert "delivery_cost_wei_relay_alias_mismatch" in validation["errors"]


def test_live_signal_evidence_requires_public_mempool_risk_penalty_fields():
    result = _live_signal_result()
    del result["request"]["netProfitModel"]["publicMempoolRiskPenaltyUsdc"]
    del result["request"]["routeEvaluations"][0]["netProfit"]["publicMempoolRiskPenaltyUsdc"]
    evidence = live_evidence.build_unified_live_signal_evidence(
        run_id="run-public-mempool-risk-missing",
        signal_detected_at="2026-08-12T00:00:00+00:00",
        validated_at="2026-08-12T00:00:00.050000+00:00",
        submitted_or_dropped_at="2026-08-12T00:00:00.060000+00:00",
        result=result,
    )

    validation = live_evidence.validate_unified_live_signal_evidence(evidence)

    assert validation["ok"] is False
    assert "public_mempool_risk_penalty_usdc_missing" in validation["errors"]
    assert "route_evaluation_0_public_mempool_risk_penalty_usdc_missing" in validation["errors"]


def test_live_signal_evidence_rejects_public_mempool_risk_penalty_component_mismatch():
    result = _live_signal_result()
    result["request"]["routeEvaluations"][0]["netProfit"]["publicMempoolRiskPenaltyFixedUsdc"] = "3"
    result["request"]["routeEvaluations"][0]["netProfit"]["publicMempoolRiskPenaltyBpsUsdc"] = "4"
    result["request"]["routeEvaluations"][0]["netProfit"]["publicMempoolRiskPenaltyUsdc"] = "8"
    evidence = live_evidence.build_unified_live_signal_evidence(
        run_id="run-public-mempool-risk-mismatch",
        signal_detected_at="2026-08-12T00:00:00+00:00",
        validated_at="2026-08-12T00:00:00.050000+00:00",
        submitted_or_dropped_at="2026-08-12T00:00:00.060000+00:00",
        result=result,
    )

    validation = live_evidence.validate_unified_live_signal_evidence(evidence)

    assert validation["ok"] is False
    assert "route_evaluation_0_public_mempool_risk_penalty_mismatch" in validation["errors"]
    assert validation["disposition"] == "diagnostic_only_notify_ops"


def test_live_signal_evidence_flags_large_public_mempool_penalty_for_manual_review():
    result = _live_signal_result()
    net_profit = result["request"]["routeEvaluations"][0]["netProfit"]
    net_profit["expectedProfit"] = "1000"
    net_profit["publicMempoolRiskPenaltyFixedUsdc"] = "600"
    net_profit["publicMempoolRiskPenaltyBpsUsdc"] = "0"
    net_profit["publicMempoolRiskPenaltyUsdc"] = "600"
    evidence = live_evidence.build_unified_live_signal_evidence(
        run_id="run-public-mempool-risk-review",
        signal_detected_at="2026-08-12T00:00:00+00:00",
        validated_at="2026-08-12T00:00:00.050000+00:00",
        submitted_or_dropped_at="2026-08-12T00:00:00.060000+00:00",
        result=result,
    )

    validation = live_evidence.validate_unified_live_signal_evidence(evidence)

    assert validation["ok"] is True
    assert validation["disposition"] == "manual_review_required"
    assert validation["reviewFlags"] == [
        "route_evaluation_0_public_mempool_penalty_gt_50pct_expected_profit"
    ]
    assert validation["manualReviewThresholdBps"] == "5000"


def test_live_signal_evidence_uses_configured_public_mempool_review_threshold():
    result = _live_signal_result()
    result["request"]["manualReviewThresholdBps"] = "7000"
    result["request"]["manualReviewThresholdAdjustmentHistory"] = [
        {
            "previousThresholdBps": "5000",
            "newThresholdBps": "7000",
            "proposedAt": "2026-08-12T00:00:00+00:00",
            "effectiveScope": "manual_review_candidate_only",
            "independentWindowStatistics": [
                {"runId": "window-1"},
                {"runId": "window-2"},
                {"runId": "window-3"},
            ],
            "competitorPressureSummary": "independent_provider_evidence_reviewed",
            "reviewConclusion": "approved_for_manual_review_only",
            "approvedBy": "reviewer@example.invalid",
            "rollbackCondition": "revert_on_any_window_quality_regression",
        }
    ]
    net_profit = result["request"]["routeEvaluations"][0]["netProfit"]
    net_profit["expectedProfit"] = "1000"
    net_profit["publicMempoolRiskPenaltyFixedUsdc"] = "600"
    net_profit["publicMempoolRiskPenaltyBpsUsdc"] = "0"
    net_profit["publicMempoolRiskPenaltyUsdc"] = "600"
    evidence = live_evidence.build_unified_live_signal_evidence(
        run_id="run-public-mempool-risk-custom-threshold",
        signal_detected_at="2026-08-12T00:00:00+00:00",
        validated_at="2026-08-12T00:00:00.050000+00:00",
        submitted_or_dropped_at="2026-08-12T00:00:00.060000+00:00",
        result=result,
    )

    validation = live_evidence.validate_unified_live_signal_evidence(evidence)

    assert validation["ok"] is True
    assert validation["disposition"] == "g07d_passed"
    assert validation["reviewFlags"] == []
    assert validation["manualReviewThresholdBps"] == "7000"


def test_live_signal_evidence_rejects_collected_competitor_source_without_provider_or_retention():
    evidence = live_evidence.build_unified_live_signal_evidence(
        run_id="run-metric-source-missing",
        signal_detected_at="2026-08-12T00:00:00+00:00",
        validated_at="2026-08-12T00:00:00.050000+00:00",
        submitted_or_dropped_at="2026-08-12T00:00:00.060000+00:00",
        result=_live_signal_result(),
    )
    source = evidence["marketFeasibility"]["competitorPressure"]["metricSources"][
        "sameBlockHigherGasIndicator"
    ]
    source.update(
        {
            "sourceKind": "confirmed_block_receipts",
            "unavailableReason": None,
            "endpointHost": "rpc.example.invalid",
            "blockRange": {"from": 1, "to": 2},
            "transactionFilter": "target_pair_pool_interactions",
            "collectedAt": "2026-08-12T00:00:00+00:00",
            "rawEvidencePath": "evidence/raw.json",
            "rawEvidenceHash": "sha256:" + "a" * 64,
            "retentionUntil": "2026-08-13T00:00:00+00:00",
        }
    )
    evidence["evidenceHash"] = live_evidence.canonical_json_hash(
        {key: value for key, value in evidence.items() if key != "evidenceHash"}
    )

    validation = live_evidence.validate_unified_live_signal_evidence(evidence)

    assert validation["ok"] is False
    assert "market_feasibility_competitor_metric_source_provider_missing" in validation["errors"]
    assert "market_feasibility_competitor_metric_source_retention_too_short" in validation["errors"]


def test_live_signal_evidence_rejects_high_confidence_with_same_provider_sources():
    evidence = live_evidence.build_unified_live_signal_evidence(
        run_id="run-provider-independence",
        signal_detected_at="2026-08-12T00:00:00+00:00",
        validated_at="2026-08-12T00:00:00.050000+00:00",
        submitted_or_dropped_at="2026-08-12T00:00:00.060000+00:00",
        result=_live_signal_result(),
    )
    competitor = evidence["marketFeasibility"]["competitorPressure"]
    competitor["confidence"] = "high_confidence"
    collected_source = {
        "sourceKind": "confirmed_block_receipts",
        "sourceProvider": "provider-a",
        "endpointHost": "rpc.provider-a.invalid",
        "blockRange": {"from": 1, "to": 2},
        "transactionFilter": "target_pair_pool_interactions",
        "collectedAt": "2026-08-12T00:00:00+00:00",
        "rawEvidencePath": "evidence/raw-a.json",
        "rawEvidenceHash": "sha256:" + "a" * 64,
        "retentionUntil": "2026-11-10T00:00:00+00:00",
        "unavailableReason": None,
    }
    competitor["metricSources"]["sameBlockHigherGasIndicator"] = collected_source
    competitor["metricSources"]["independentSources"] = [
        {
            **collected_source,
            "sourceKind": "pending_websocket",
            "endpointHost": "ws.provider-a.invalid",
            "rawEvidencePath": "evidence/raw-b.json",
            "rawEvidenceHash": "sha256:" + "b" * 64,
        }
    ]
    evidence["evidenceHash"] = live_evidence.canonical_json_hash(
        {key: value for key, value in evidence.items() if key != "evidenceHash"}
    )

    validation = live_evidence.validate_unified_live_signal_evidence(evidence)

    assert validation["ok"] is False
    assert "market_feasibility_competitor_sources_not_independent" in validation["errors"]


def test_live_signal_evidence_rejects_low_frequency_without_24h_chain_confirmation():
    evidence = live_evidence.build_unified_live_signal_evidence(
        run_id="run-low-frequency-incomplete",
        signal_detected_at="2026-08-12T00:00:00+00:00",
        validated_at="2026-08-12T00:00:00.050000+00:00",
        submitted_or_dropped_at="2026-08-12T00:00:00.060000+00:00",
        result=_live_signal_result(),
    )
    market = evidence["marketFeasibility"]
    market["expectedProfitDistribution"].update(
        {
            "sampleSufficiency": "low_frequency_market",
            "sampleSufficiencyReason": "candidate_count_below_100",
            "candidateGenerationRatePerHour": 1.5,
            "lowFrequencyConfirmation": None,
        }
    )
    market["candidateGeneration"].update(
        {"listenerHealth": "healthy", "rpcHealth": "healthy", "cacheHealth": "healthy"}
    )
    evidence["listenerStartedAt"] = "2026-08-12T00:00:00+00:00"
    evidence["listenerStoppedAt"] = "2026-08-12T01:00:00+00:00"
    evidence["evidenceHash"] = live_evidence.canonical_json_hash(
        {key: value for key, value in evidence.items() if key != "evidenceHash"}
    )

    validation = live_evidence.validate_unified_live_signal_evidence(evidence)

    assert validation["ok"] is False
    assert "market_feasibility_low_frequency_requires_healthy_24h_window" in validation["errors"]
    assert "market_feasibility_low_frequency_confirmation_missing" in validation["errors"]


def test_live_signal_evidence_rejects_regime_declared_after_listener_start():
    evidence = live_evidence.build_unified_live_signal_evidence(
        run_id="run-regime-declared-late",
        signal_detected_at="2026-08-12T00:00:00+00:00",
        validated_at="2026-08-12T00:00:00.050000+00:00",
        submitted_or_dropped_at="2026-08-12T00:00:00.060000+00:00",
        result=_live_signal_result(),
    )
    evidence["listenerStartedAt"] = "2026-08-12T00:00:00+00:00"
    evidence["marketFeasibility"]["volatilitySummary"]["regimeDefinitionDeclaredAt"] = (
        "2026-08-12T00:00:01+00:00"
    )
    evidence["evidenceHash"] = live_evidence.canonical_json_hash(
        {key: value for key, value in evidence.items() if key != "evidenceHash"}
    )

    validation = live_evidence.validate_unified_live_signal_evidence(evidence)

    assert validation["ok"] is False
    assert "market_feasibility_regime_definition_declared_after_listener_start" in validation["errors"]


def test_live_signal_evidence_rejects_non_default_threshold_without_approval_history():
    result = _live_signal_result()
    result["request"]["manualReviewThresholdBps"] = "7000"
    evidence = live_evidence.build_unified_live_signal_evidence(
        run_id="run-threshold-history-missing",
        signal_detected_at="2026-08-12T00:00:00+00:00",
        validated_at="2026-08-12T00:00:00.050000+00:00",
        submitted_or_dropped_at="2026-08-12T00:00:00.060000+00:00",
        result=result,
    )

    validation = live_evidence.validate_unified_live_signal_evidence(evidence)

    assert validation["ok"] is False
    assert "manual_review_threshold_non_default_without_adjustment_history" in validation["errors"]


def test_live_signal_evidence_requires_pre_pause_trigger_source():
    result = _live_signal_result()
    result["prePause"] = {
        "prePause": True,
        "pauseDetectedAt": "2026-08-12T00:00:00+00:00",
        "signerBlockedAt": "2026-08-12T00:00:01+00:00",
        "pausePropagationMs": "1000",
    }
    evidence = live_evidence.build_unified_live_signal_evidence(
        run_id="run-pre-pause-trigger-missing",
        signal_detected_at="2026-08-12T00:00:00+00:00",
        validated_at="2026-08-12T00:00:00.050000+00:00",
        submitted_or_dropped_at="2026-08-12T00:00:00.060000+00:00",
        result=result,
    )

    validation = live_evidence.validate_unified_live_signal_evidence(evidence)

    assert validation["ok"] is False
    assert "pre_pause_trigger_source_invalid" in validation["errors"]


def test_live_signal_evidence_rejects_diagnostic_input_claiming_live_evidence():
    evidence = live_evidence.build_unified_live_signal_evidence(
        run_id="run-2",
        signal_detected_at="2026-08-12T00:00:00+00:00",
        validated_at="2026-08-12T00:00:00.050000+00:00",
        submitted_or_dropped_at="2026-08-12T00:00:00.060000+00:00",
        result=_live_signal_result(runtime_trades=[]),
        live_signal_only=False,
    )

    validation = live_evidence.validate_unified_live_signal_evidence(evidence)

    assert validation["ok"] is False
    assert "live_signal_semantics_missing" in validation["errors"]
    assert "runtime_trades_empty" in validation["errors"]


def test_live_signal_evidence_rejects_empty_runtime_trades_for_live_signal():
    evidence = live_evidence.build_unified_live_signal_evidence(
        run_id="run-empty-runtime-trades",
        signal_detected_at="2026-08-12T00:00:00+00:00",
        validated_at="2026-08-12T00:00:00.050000+00:00",
        submitted_or_dropped_at="2026-08-12T00:00:00.060000+00:00",
        result=_live_signal_result(runtime_trades=[]),
    )

    validation = live_evidence.validate_unified_live_signal_evidence(evidence)

    assert validation["ok"] is False
    assert "runtime_trades_empty" in validation["errors"]


def test_live_signal_evidence_rejects_incomplete_runtime_trade_shape():
    evidence = live_evidence.build_unified_live_signal_evidence(
        run_id="run-incomplete-runtime-trades",
        signal_detected_at="2026-08-12T00:00:00+00:00",
        validated_at="2026-08-12T00:00:00.050000+00:00",
        submitted_or_dropped_at="2026-08-12T00:00:00.060000+00:00",
        result=_live_signal_result(
            runtime_trades=[
                {"tradeIndex": 0, "tokenX": _addr(500), "tokenY": _addr(501)}
            ],
        ),
    )

    validation = live_evidence.validate_unified_live_signal_evidence(evidence)

    assert validation["ok"] is False
    assert "runtime_trade_0_pools_missing" in validation["errors"]
    assert "runtime_trade_0_pools_empty" in validation["errors"]


def test_live_signal_evidence_requires_schema_validation_and_no_positive_profit_claim_without_broadcast():
    result = _live_signal_result()
    evidence = live_evidence.build_unified_live_signal_evidence(
        run_id="run-3",
        signal_detected_at="2026-08-12T00:00:00+00:00",
        validated_at="2026-08-12T00:00:00.050000+00:00",
        submitted_or_dropped_at="2026-08-12T00:00:00.060000+00:00",
        result=result,
        positive_profit_proven=True,
    )

    validation = live_evidence.validate_unified_live_signal_evidence(evidence)

    assert validation["ok"] is False
    assert "positive_profit_claim_without_broadcast" in validation["errors"]
    assert "schema_validation_missing" not in validation["errors"]


def test_live_signal_evidence_rejects_failed_schema_validation():
    result = _live_signal_result()
    evidence = live_evidence.build_unified_live_signal_evidence(
        run_id="run-4",
        signal_detected_at="2026-08-12T00:00:00+00:00",
        validated_at="2026-08-12T00:00:00.050000+00:00",
        submitted_or_dropped_at="2026-08-12T00:00:00.060000+00:00",
        result=result,
    )
    evidence["schemaValidation"] = {"ok": False, "errors": ["manual_failure"]}
    evidence["evidenceHash"] = live_evidence.canonical_json_hash(
        {key: value for key, value in evidence.items() if key != "evidenceHash"}
    )

    validation = live_evidence.validate_unified_live_signal_evidence(evidence)

    assert validation["ok"] is False
    assert "schema_validation_failed" in validation["errors"]


def test_live_signal_evidence_rejects_broadcast_in_shadow_mode():
    result = _live_signal_result(status="submitted_success", submitted=True)
    evidence = live_evidence.build_unified_live_signal_evidence(
        run_id="run-shadow-broadcast",
        signal_detected_at="2026-08-12T00:00:00+00:00",
        validated_at="2026-08-12T00:00:00.050000+00:00",
        submitted_or_dropped_at="2026-08-12T00:00:00.060000+00:00",
        result=result,
        mode="shadow",
    )

    validation = live_evidence.validate_unified_live_signal_evidence(evidence)

    assert validation["ok"] is False
    assert "shadow_broadcast_detected" in validation["errors"]


