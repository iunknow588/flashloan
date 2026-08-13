"""JSON-schema style constants for unified live signal evidence."""

from __future__ import annotations

UNIFIED_LIVE_SIGNAL_SCHEMA_VERSION = 2
UNIFIED_LIVE_SIGNAL_SCHEMA_CONSTANT_VERSION = "unified_live_signal_schema:v2"

UNIFIED_LIVE_SIGNAL_REQUIRED_FIELDS = (
    "schemaVersion",
    "schemaConstantVersion",
    "runId",
    "mode",
    "evidenceSemantics",
    "timestamps",
    "result",
    "request",
    "runtimeTrades",
    "runtimeTradesHash",
    "requestHash",
    "resultHash",
    "minCapturableWindowMs",
    "routeEvaluations",
    "netProfitModel",
    "marketFeasibility",
    "deliveryPolicy",
    "manualReviewThresholdBps",
    "manualReviewThresholdRationale",
    "manualReviewThresholdAdjustmentHistory",
    "schemaValidation",
    "evidenceHash",
)

UNIFIED_RUNTIME_TRADE_REQUIRED_FIELDS = (
    "tradeIndex",
    "tokenX",
    "tokenY",
    "pools",
)

UNIFIED_RUNTIME_POOL_REQUIRED_FIELDS = (
    "adapterKind",
    "pool",
)

UNIFIED_LIVE_SIGNAL_SCHEMA: dict = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "https://opc.local/flashloan/unified-live-signal.schema.json",
    "title": "Unified live signal evidence",
    "type": "object",
    "required": list(UNIFIED_LIVE_SIGNAL_REQUIRED_FIELDS),
    "properties": {
        "schemaVersion": {"const": UNIFIED_LIVE_SIGNAL_SCHEMA_VERSION},
        "schemaConstantVersion": {"const": UNIFIED_LIVE_SIGNAL_SCHEMA_CONSTANT_VERSION},
        "runId": {"type": "string", "minLength": 1},
        "mode": {"type": "string"},
        "evidenceSemantics": {
            "type": "object",
            "required": ("liveSignalOnly", "historicalForkOnly", "broadcastPerformed"),
        },
        "timestamps": {
            "type": "object",
            "required": ("signalDetectedAt", "validatedAt", "submittedOrDroppedAt"),
        },
        "runtimeTrades": {
            "type": "array",
            "items": {
                "type": "object",
                "required": list(UNIFIED_RUNTIME_TRADE_REQUIRED_FIELDS),
                "properties": {
                    "pools": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "required": list(UNIFIED_RUNTIME_POOL_REQUIRED_FIELDS),
                        },
                    }
                },
            },
        },
        "deliveryPolicy": {"type": "object", "required": ("mode",)},
        "netProfitModel": {"type": "object"},
        "marketFeasibility": {
            "type": "object",
            "required": (
                "targetPairLiquidity",
                "volatilitySummary",
                "competitorPressure",
                "expectedProfitDistribution",
                "gasPriceRegime",
                "strategyDifferentiation",
                "conclusion",
            ),
            "properties": {
                "volatilitySummary": {
                    "type": "object",
                    "required": (
                        "regimeDefinition",
                        "regimeDefinitionDeclaredAt",
                        "priceSource",
                        "currentRegime",
                    ),
                },
                "competitorPressure": {
                    "type": "object",
                    "required": ("observations", "inferenceOnly", "confidence", "metrics", "metricSources"),
                },
                "expectedProfitDistribution": {
                    "type": "object",
                    "required": (
                        "sampleCount",
                        "sampleSufficiency",
                        "sampleSufficiencyReason",
                        "candidateGenerationRatePerHour",
                        "lowFrequencyConfirmation",
                    ),
                },
            },
        },
        "routeEvaluations": {"type": "array"},
        "manualReviewThresholdBps": {"type": ["string", "integer"]},
        "manualReviewThresholdRationale": {"type": "string", "minLength": 1},
        "manualReviewThresholdAdjustmentHistory": {"type": "array"},
        "schemaValidation": {"type": "object", "required": ("ok", "errors")},
        "evidenceHash": {"type": "string", "pattern": "^sha256:[0-9a-f]{64}$"},
    },
}


def unified_live_signal_schema() -> dict:
    return dict(UNIFIED_LIVE_SIGNAL_SCHEMA)
