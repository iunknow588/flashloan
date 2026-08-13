"""Run the unified executor in shadow mode and export G-07 evidence."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SRC_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = SRC_ROOT.parents[1]
DEFAULT_INPUT_PATH = SRC_ROOT / "runtime" / "state" / "unified_live_shadow_input.json"
DEFAULT_EVIDENCE_ROOT = PROJECT_ROOT / "contract" / "contracts-dex" / "deployments" / "evidence"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from core.env_loader import load_env_files
from intent_trade.direct import build_triangular_onchain_intent_trade, submit_direct_onchain_trade
from intent_trade.live_evidence import (
    MANUAL_REVIEW_THRESHOLD_RATIONALE,
    build_unified_live_signal_evidence,
    canonical_json_hash,
    default_market_feasibility,
    utc_now_iso,
    validate_unified_live_signal_evidence,
    write_unified_live_signal_evidence,
)
from intent_trade.unified_live_signal_schema import (
    UNIFIED_LIVE_SIGNAL_SCHEMA_CONSTANT_VERSION,
    UNIFIED_LIVE_SIGNAL_SCHEMA_VERSION,
)


def _read_input(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"shadow input is unavailable or invalid: {path}") from exc
    if not isinstance(payload, dict):
        raise ValueError("shadow input must be a JSON object")
    return payload


def _quote_and_opportunity(source: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    quote_payload = source.get("quote_payload")
    opportunity = source.get("opportunity")
    if isinstance(quote_payload, dict) and isinstance(opportunity, dict):
        return quote_payload, opportunity

    market_state = source.get("market_state")
    if not isinstance(market_state, dict):
        raise ValueError("shadow input requires quote_payload/opportunity or market_state")
    top = market_state.get("top") or market_state.get("cow_top") or []
    bottom = market_state.get("bottom") or market_state.get("cow_bottom") or []
    if not isinstance(top, list) or not isinstance(bottom, list):
        raise ValueError("market_state top and bottom must be lists")
    intent = build_triangular_onchain_intent_trade(
        source.get("link_name") or "USDC->X->Y->USDC",
        source.get("expected_profit_usdc") or "0",
        top,
        bottom,
    )
    return {"cow_flashloan_intent": intent}, {
        "market_state": market_state,
        "tokenX": source.get("tokenX"),
        "tokenY": source.get("tokenY"),
    }


def _signal_timestamp(source: dict[str, Any]) -> str:
    for key in ("signalDetectedAt", "signal_detected_at", "observedAt", "observed_at"):
        value = source.get(key)
        if value:
            return str(value)
    return utc_now_iso()


def _input_is_live_signal(source: dict[str, Any]) -> bool:
    semantics = source.get("evidenceSemantics")
    if not isinstance(semantics, dict) or semantics.get("liveSignalOnly") is not True:
        return False
    if semantics.get("historicalForkOnly") is True:
        return False
    return any(
        source.get(key)
        for key in ("signalDetectedAt", "signal_detected_at", "observedAt", "observed_at")
    )


def _new_run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ_unified-live-signal")


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


def _order_optimization_evidence(strategy_statuses: dict[str, int]) -> dict[str, Any]:
    early_hits = sum(strategy_statuses.get(str(status), 0) for status in (1, 2))
    late_hits = sum(strategy_statuses.get(str(status), 0) for status in (4, 5))
    current_window_signal = late_hits >= 3 and late_hits > max(0, early_hits * 2)
    return {
        "recommended": False,
        "currentWindowSignal": current_window_signal,
        "requiredIndependentWindows": 3,
        "independentWindowsObserved": 1 if current_window_signal else 0,
        "independenceCriteria": {
            "minimumGapHours": 4,
            "alternativeRequirement": "cover_distinct_low_medium_high_volatility_regimes",
            "sameRegimeAdjacentWindowsAllowed": False,
        },
        "independenceDefinition": (
            "non_overlapping_completed_runs_with_distinct_run_ids_and_4h_gap_or_"
            "distinct_predeclared_volatility_regimes"
        ),
        "reason": (
            "requires_three_independent_windows_and_net_profit_distribution"
            if current_window_signal
            else "current_window_does_not_meet_late_hit_threshold"
        ),
        "earlyHitCount": early_hits,
        "lateHitCount": late_hits,
    }


def _window_market_feasibility(
    samples: list[dict[str, Any]],
    live_signal_only: bool,
    *,
    started_at: str | None = None,
    stopped_at: str | None = None,
) -> dict[str, Any]:
    markets = [
        sample.get("marketFeasibility")
        for sample in samples
        if isinstance(sample.get("marketFeasibility"), dict)
    ]
    if not markets:
        conclusion = "observe_longer" if live_signal_only and samples else "insufficient_data"
        report = default_market_feasibility(conclusion=conclusion)
        return report
    selected = dict(markets[-1])
    selected.setdefault("source", "last_sample_market_feasibility")
    candidate_generation = selected.setdefault("candidateGeneration", {})
    candidate_count = sum(
        int(sample.get("candidateCount") or 0)
        for sample in samples
        if str(sample.get("candidateCount") or "0").isdigit()
    )
    candidate_generation.setdefault("candidateCount", candidate_count)
    expected_distribution = selected.setdefault("expectedProfitDistribution", {})
    if expected_distribution.get("candidateGenerationRatePerHour") is None:
        duration_hours = None
        if started_at and stopped_at:
            try:
                from datetime import datetime

                start = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
                stop = datetime.fromisoformat(stopped_at.replace("Z", "+00:00"))
                duration_hours = max((stop - start).total_seconds() / 3600, 0.0)
            except ValueError:
                duration_hours = None
        expected_distribution["candidateGenerationRatePerHour"] = (
            round(candidate_count / duration_hours, 4)
            if duration_hours and duration_hours > 0
            else None
        )
    expected_distribution.setdefault("sampleCount", candidate_count)
    invalid_samples = sum(
        int((sample.get("validation") or {}).get("ok") is not True)
        for sample in samples
    )
    duration_hours = None
    if started_at and stopped_at:
        try:
            from datetime import datetime

            start = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
            stop = datetime.fromisoformat(stopped_at.replace("Z", "+00:00"))
            duration_hours = max((stop - start).total_seconds() / 3600, 0.0)
        except ValueError:
            duration_hours = None
    low_frequency_confirmed = (
        duration_hours is not None
        and duration_hours >= 24
        and invalid_samples == 0
        and all(
            candidate_generation.get(field) == "healthy"
            for field in ("listenerHealth", "rpcHealth", "cacheHealth")
        )
        and isinstance(expected_distribution.get("lowFrequencyConfirmation"), dict)
        and expected_distribution.get("candidateGenerationRatePerHour") is not None
    )
    if candidate_count >= 100:
        expected_distribution["sampleSufficiency"] = "sufficient"
        expected_distribution.setdefault("sampleSufficiencyReason", "candidate_count_at_least_100")
    elif expected_distribution.get("sampleSufficiency") == "low_frequency_market" and low_frequency_confirmed:
        expected_distribution.setdefault("sampleSufficiencyReason", "healthy_24h_chain_confirmed_low_frequency")
    else:
        expected_distribution["sampleSufficiency"] = "insufficient"
        expected_distribution["sampleSufficiencyReason"] = (
            "invalid_shadow_samples"
            if invalid_samples
            else "low_frequency_confirmation_or_healthy_24h_window_missing"
        )
        expected_distribution["lowFrequencyConfirmation"] = None
    return selected


def _distribution(values: list[int]) -> dict[str, int | None]:
    if not values:
        return {"p50": None, "p95": None, "max": None}
    ordered = sorted(values)
    return {
        "p50": ordered[(len(ordered) - 1) // 2],
        "p95": ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))],
        "max": ordered[-1],
    }


def _write_window_report(
    *,
    path: Path,
    run_id: str,
    input_path: Path,
    started_at: str,
    stopped_at: str,
    samples: list[dict[str, Any]],
    input_refresh_count: int,
    live_signal_only: bool,
) -> dict[str, Any]:
    statuses: dict[str, int] = {}
    status_families: dict[str, int] = {}
    strategy_statuses: dict[str, int] = {}
    execution_kinds: dict[str, int] = {}
    positive_signal_count = 0
    valid_sample_count = 0
    invalid_sample_count = 0
    manual_review_count = 0
    diagnostic_disposition_count = 0
    manual_review_threshold_bps = 5000
    manual_review_threshold_rationale = MANUAL_REVIEW_THRESHOLD_RATIONALE
    manual_review_threshold_adjustment_history: list[dict[str, Any]] = []
    min_capturable_window_ms: list[int] = []
    signal_age_ms: list[int] = []
    for sample in samples:
        validation = sample.get("validation") if isinstance(sample.get("validation"), dict) else {}
        try:
            manual_review_threshold_bps = int(
                sample.get("manualReviewThresholdBps")
                or validation.get("manualReviewThresholdBps")
                or manual_review_threshold_bps
            )
        except (TypeError, ValueError):
            manual_review_threshold_bps = 5000
        rationale = sample.get("manualReviewThresholdRationale")
        if isinstance(rationale, str) and rationale:
            manual_review_threshold_rationale = rationale
        history = sample.get("manualReviewThresholdAdjustmentHistory")
        if isinstance(history, list):
            manual_review_threshold_adjustment_history = history
        status = str(((sample.get("result") or {}).get("status")) or "unknown")
        result = sample.get("result") if isinstance(sample.get("result"), dict) else {}
        for key, target in (
            ("minCapturableWindowMs", min_capturable_window_ms),
            ("signalAgeMs", signal_age_ms),
        ):
            value = sample.get(key)
            if value is not None:
                try:
                    parsed = int(value)
                except (TypeError, ValueError):
                    parsed = -1
                if parsed >= 0:
                    target.append(parsed)
        for key, target in (
            ("strategyStatus", strategy_statuses),
            ("selectedStrategyStatus", strategy_statuses),
            ("executionKind", execution_kinds),
            ("selectedExecutionKind", execution_kinds),
        ):
            value = result.get(key)
            if value not in (None, ""):
                text = str(value)
                target[text] = target.get(text, 0) + 1
        statuses[status] = statuses.get(status, 0) + 1
        family = _status_family(status)
        status_families[family] = status_families.get(family, 0) + 1
        valid_sample_count += int(validation.get("ok") is True)
        invalid_sample_count += int(validation.get("ok") is not True)
        manual_review_count += int(validation.get("disposition") == "manual_review_required")
        diagnostic_disposition_count += int(validation.get("disposition") == "diagnostic_only_notify_ops")
        if validation.get("ok") is True and status in {"preview_passed", "static_call_passed"}:
            positive_signal_count += 1
    order_optimization = _order_optimization_evidence(strategy_statuses)
    report = {
        "schemaVersion": UNIFIED_LIVE_SIGNAL_SCHEMA_VERSION,
        "schemaConstantVersion": UNIFIED_LIVE_SIGNAL_SCHEMA_CONSTANT_VERSION,
        "runId": run_id,
        "evidenceSemantics": {
            "liveSignalOnly": live_signal_only,
            "historicalForkOnly": False,
            "shadowOnly": True,
            "broadcastPerformed": False,
            "evidenceEligible": live_signal_only and valid_sample_count > 0 and invalid_sample_count == 0,
        },
        "listenerStartedAt": started_at,
        "listenerStoppedAt": stopped_at,
        "inputPath": str(input_path),
        "inputRefreshCount": input_refresh_count,
        "sampleCount": len(samples),
        "validSampleCount": valid_sample_count,
        "invalidSampleCount": invalid_sample_count,
        "manualReviewSampleCount": manual_review_count,
        "diagnosticDispositionCount": diagnostic_disposition_count,
        "manualReviewRate": (
            round(manual_review_count / len(samples), 6)
            if samples
            else 0.0
        ),
        "manualReviewThresholdBps": manual_review_threshold_bps,
        "manualReviewThresholdRationale": manual_review_threshold_rationale,
        "manualReviewThresholdAdjustmentHistory": manual_review_threshold_adjustment_history,
        "positiveSignalCount": positive_signal_count if live_signal_only else 0,
        "statusCounts": statuses,
        "statusFamilyCounts": status_families,
        "strategyStatusCounts": strategy_statuses,
        "executionKindCounts": execution_kinds,
        "orderOptimization": order_optimization,
        "orderOptimizationRecommended": order_optimization["recommended"],
        "minCapturableWindowMs": _distribution(min_capturable_window_ms),
        "signalAgeMs": _distribution(signal_age_ms),
        "marketFeasibility": _window_market_feasibility(
            samples,
            live_signal_only,
            started_at=started_at,
            stopped_at=stopped_at,
        ),
        "sampleFiles": [sample["file"] for sample in samples],
        "sampleEvidenceHashes": [
            sample["evidenceHash"]
            for sample in samples
            if isinstance(sample.get("evidenceHash"), str) and sample.get("evidenceHash")
        ],
        "runClassification": (
            "input_bridge_diagnostic_only"
            if not live_signal_only
            else (
                "live_shadow_candidate_evidence"
                if valid_sample_count > 0 and invalid_sample_count == 0
                else (
                    "live_shadow_window_contains_invalid_samples"
                    if valid_sample_count > 0
                    else "live_input_without_valid_shadow_samples"
                )
            )
        ),
        "zeroSignalReason": (
            ""
            if positive_signal_count and live_signal_only and invalid_sample_count == 0
            else (
                "input_not_marked_live_signal_only"
                if not live_signal_only
                else (
                    "shadow_samples_failed_schema_validation"
                    if invalid_sample_count > 0
                    else "no_valid_shadow_samples"
                )
            )
        ),
        "schemaValidation": {"ok": True, "errors": []},
        "g07dFailureAction": "diagnostic_only_notify_ops",
    }
    report["reportHash"] = canonical_json_hash({key: value for key, value in report.items() if key != "reportHash"})
    write_unified_live_signal_evidence(path, report)
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT_PATH)
    parser.add_argument("--iterations", type=int, default=1)
    parser.add_argument("--interval-ms", type=int, default=1_000)
    parser.add_argument("--timeout-seconds", type=int, default=20)
    parser.add_argument("--evidence-root", type=Path, default=DEFAULT_EVIDENCE_ROOT)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = run_shadow_once(args)
    print(json.dumps(report, ensure_ascii=True))
    return 0


def run_shadow_once(args: argparse.Namespace) -> dict[str, Any]:
    load_env_files(SRC_ROOT, override=False)
    input_path = args.input if args.input.is_absolute() else (SRC_ROOT / args.input)
    evidence_root = args.evidence_root if args.evidence_root.is_absolute() else (PROJECT_ROOT / args.evidence_root)
    requested_broadcast = os.getenv("UNIFIED_EXECUTOR_BROADCAST_ENABLED", "")
    os.environ["UNIFIED_EXECUTOR_BROADCAST_ENABLED"] = "false"
    os.environ["TRIANGULAR_DIRECT_BROADCAST_ENABLED"] = "false"
    run_id = _new_run_id()
    run_directory = evidence_root / run_id
    signal_directory = run_directory / "signals"
    started_at = utc_now_iso()
    samples: list[dict[str, Any]] = []
    input_refresh_count = 0
    live_signal_only = True
    iterations = max(1, int(args.iterations))

    for index in range(iterations):
        source = _read_input(input_path)
        input_refresh_count += 1
        live_signal_only = live_signal_only and _input_is_live_signal(source)
        detected_at = _signal_timestamp(source)
        quote_payload, opportunity = _quote_and_opportunity(source)
        result = submit_direct_onchain_trade(
            quote_payload=quote_payload,
            opportunity=opportunity,
            timeout_seconds=args.timeout_seconds,
        )
        validated_at = utc_now_iso()
        terminal_at = utc_now_iso()
        evidence = build_unified_live_signal_evidence(
            run_id=run_id,
            signal_detected_at=detected_at,
            validated_at=validated_at,
            submitted_or_dropped_at=terminal_at,
            result=result,
            source={
                "inputPath": str(input_path),
                "inputObservedAt": source.get("observedAt") or source.get("observed_at"),
                "marketFeasibility": source.get("marketFeasibility"),
                "broadcastForcedOff": True,
                "requestedBroadcastValue": requested_broadcast,
            },
            process={
                "sampleIndex": index,
                "listenerMode": "polling_input_bridge",
                "listenerStartedAt": started_at,
            },
            live_signal_only=_input_is_live_signal(source),
        )
        validation = validate_unified_live_signal_evidence(evidence)
        evidence["schemaValidation"] = validation
        evidence["evidenceHash"] = canonical_json_hash(
            {key: value for key, value in evidence.items() if key != "evidenceHash"}
        )
        final_validation = validate_unified_live_signal_evidence(evidence)
        evidence["schemaValidation"] = final_validation
        evidence["evidenceHash"] = canonical_json_hash(
            {key: value for key, value in evidence.items() if key != "evidenceHash"}
        )
        if not evidence.get("evidenceSemantics", {}).get("positiveProfitProven"):
            evidence["evidenceSemantics"]["positiveProfitProven"] = False
        evidence_path = signal_directory / f"{index:06d}.json"
        write_unified_live_signal_evidence(evidence_path, evidence)
        samples.append(
            {
                "file": str(evidence_path),
                "result": evidence.get("result"),
                "validation": final_validation,
                "evidenceHash": evidence.get("evidenceHash"),
                "manualReviewThresholdBps": evidence.get("manualReviewThresholdBps"),
                "manualReviewThresholdBpsSource": evidence.get("manualReviewThresholdBpsSource"),
                "manualReviewThresholdRationale": evidence.get("manualReviewThresholdRationale"),
                "manualReviewThresholdAdjustmentHistory": evidence.get(
                    "manualReviewThresholdAdjustmentHistory"
                ),
                "minCapturableWindowMs": evidence.get("minCapturableWindowMs"),
                "signalAgeMs": (evidence.get("timestamps") or {}).get("signalAgeMs"),
                "marketFeasibility": evidence.get("marketFeasibility"),
                "candidateCount": source.get("candidateCount")
                or (
                    (evidence.get("marketFeasibility") or {})
                    .get("expectedProfitDistribution", {})
                    .get("sampleCount")
                )
                or 0,
            }
        )
        if index + 1 < iterations:
            time.sleep(max(0, args.interval_ms) / 1000)

    report = _write_window_report(
        path=run_directory / "report.json",
        run_id=run_id,
        input_path=input_path,
        started_at=started_at,
        stopped_at=utc_now_iso(),
        samples=samples,
        input_refresh_count=input_refresh_count,
        live_signal_only=live_signal_only,
    )
    return {"ok": True, "runId": run_id, "report": str(run_directory / "report.json"), **report}


if __name__ == "__main__":
    raise SystemExit(main())
