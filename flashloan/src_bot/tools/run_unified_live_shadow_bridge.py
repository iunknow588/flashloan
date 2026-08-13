"""Bridge live market snapshots into unified-executor shadow evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SRC_ROOT = Path(__file__).resolve().parents[1]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from market.observer_common import write_json_atomic
from market.velocity_candidates import top_bottom_from_extremes
from tools import run_unified_live_shadow
from intent_trade.unified_live_signal_schema import (
    UNIFIED_LIVE_SIGNAL_SCHEMA_CONSTANT_VERSION,
    UNIFIED_LIVE_SIGNAL_SCHEMA_VERSION,
)
from intent_trade.live_evidence import (
    MANUAL_REVIEW_THRESHOLD_RATIONALE,
    default_market_feasibility,
)


DEFAULT_EXTREMES_PATH = SRC_ROOT / "runtime" / "state" / "latest_extremes.json"
DEFAULT_INPUT_PATH = SRC_ROOT / "runtime" / "state" / "unified_live_shadow_input.json"
DEFAULT_BRIDGE_EVIDENCE_PREFIX = "_unified-live-bridge"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_utc(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc) if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _age_seconds(value: Any) -> float | None:
    parsed = _parse_utc(value)
    if parsed is None:
        return None
    return max(0.0, (datetime.now(timezone.utc) - parsed).total_seconds())


def _read_json_object(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def build_shadow_input_from_extremes(
    extremes: dict[str, Any],
    *,
    source_path: Path | str | None = None,
    side_limit: int = 5,
    max_age_seconds: float = 5.0,
) -> dict[str, Any]:
    observed_raw = extremes.get("observed_at") or extremes.get("observedAt")
    observed_at = str(observed_raw or "")
    observed_age_seconds = _age_seconds(observed_at)
    top, bottom = top_bottom_from_extremes(extremes, side_limit=max(1, int(side_limit)))
    candidate_ready = bool(top) and bool(bottom)
    freshness_passed = (
        observed_age_seconds is not None
        and observed_age_seconds <= max(0.0, float(max_age_seconds))
        and candidate_ready
    )
    market_state = {
        "network": "avalanche",
        "observed_at": observed_at,
        "window_seconds": extremes.get("window_seconds"),
        "sample_count": extremes.get("sample_count"),
        "price_source": extremes.get("price_source"),
        "market_state_source": extremes.get("market_state_source") or "latest_extremes_bridge",
        "top": top,
        "bottom": bottom,
    }
    return {
        "schemaVersion": UNIFIED_LIVE_SIGNAL_SCHEMA_VERSION,
        "schemaConstantVersion": UNIFIED_LIVE_SIGNAL_SCHEMA_CONSTANT_VERSION,
        "evidenceSemantics": {
            "liveSignalOnly": freshness_passed,
            "historicalForkOnly": False,
        },
        "observedAt": observed_at,
        "signalDetectedAt": observed_at,
        "source": {
            "kind": "latest_extremes",
            "path": str(source_path or DEFAULT_EXTREMES_PATH),
            "bridgeObservedAt": _utc_now_iso(),
            "observedAgeSeconds": observed_age_seconds,
            "maxAgeSeconds": max(0.0, float(max_age_seconds)),
            "candidateReady": candidate_ready,
            "freshnessPassed": freshness_passed,
            "freshnessReason": (
                "fresh"
                if freshness_passed
                else (
                    "observed_at_missing_or_invalid"
                    if observed_age_seconds is None
                    else "latest_extremes_missing_candidate"
                    if not candidate_ready
                    else "latest_extremes_stale"
                )
            ),
        },
        "market_state": market_state,
    }


def write_shadow_input(path: Path, payload: dict[str, Any]) -> None:
    write_json_atomic(str(path), payload)


def _canonical_hash(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), default=str)
    return "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    write_json_atomic(str(path), payload)


def _bridge_report(
    *,
    run_id: str,
    started_at: str,
    stopped_at: str | None,
    input_path: Path,
    extremes_path: Path,
    child_index_path: Path,
    iterations_requested: int,
    iterations_completed: int,
    fresh_count: int,
    diagnostic_count: int,
    positive_signal_count: int,
    child_report_count: int,
    eligible_child_report_count: int,
    invalid_child_report_count: int,
    last_child_report: str | None,
    interval_ms: int | None = None,
    max_extremes_age_seconds: float | None = None,
    stale_alert_seconds: float | None = None,
    freshness_reason_counts: dict[str, int] | None = None,
) -> dict[str, Any]:
    window_complete = (
        iterations_requested > 0
        and iterations_completed == iterations_requested
    )
    evidence_eligible = (
        window_complete
        and fresh_count == iterations_completed
        and diagnostic_count == 0
        and child_report_count == iterations_completed
        and eligible_child_report_count == child_report_count
        and invalid_child_report_count == 0
    )
    stale_reasons = {
        "latest_extremes_stale",
        "observed_at_missing_or_invalid",
    }
    stale_snapshot_count = sum(
        int((freshness_reason_counts or {}).get(reason) or 0)
        for reason in stale_reasons
    )
    stale_alert_iteration_threshold = (
        int((float(stale_alert_seconds) * 1000 + max(1, int(interval_ms or 0)) - 1) // max(1, int(interval_ms or 0)))
        if stale_alert_seconds is not None and interval_ms is not None and int(interval_ms or 0) > 0
        else None
    )
    stale_snapshot_alert = (
        stale_alert_iteration_threshold is not None
        and stale_alert_iteration_threshold > 0
        and stale_snapshot_count >= stale_alert_iteration_threshold
    )
    report = {
        "schemaVersion": UNIFIED_LIVE_SIGNAL_SCHEMA_VERSION,
        "schemaConstantVersion": UNIFIED_LIVE_SIGNAL_SCHEMA_CONSTANT_VERSION,
        "runId": run_id,
        "mode": "shadow_bridge",
        "evidenceSemantics": {
            "liveSignalOnly": fresh_count > 0 and diagnostic_count == 0,
            "historicalForkOnly": False,
            "shadowOnly": True,
            "broadcastPerformed": False,
            "positiveProfitProven": False,
            "evidenceEligible": evidence_eligible,
            "windowComplete": window_complete,
        },
        "listenerStartedAt": started_at,
        "listenerStoppedAt": stopped_at,
        "inputPath": str(input_path),
        "extremesPath": str(extremes_path),
        "childIndexPath": str(child_index_path),
        "iterationsRequested": iterations_requested,
        "iterationsCompleted": iterations_completed,
        "freshSnapshotCount": fresh_count,
        "diagnosticSnapshotCount": diagnostic_count,
        "positiveSignalCount": positive_signal_count,
        "childReportCount": child_report_count,
        "eligibleChildReportCount": eligible_child_report_count,
        "invalidChildReportCount": invalid_child_report_count,
        "manualReviewThresholdBps": 5000,
        "manualReviewThresholdRationale": MANUAL_REVIEW_THRESHOLD_RATIONALE,
        "manualReviewThresholdAdjustmentHistory": [],
        "marketFeasibility": default_market_feasibility(
            conclusion=(
                "positive_signal_requires_review"
                if positive_signal_count > 0
                else (
                    "observe_longer"
                    if window_complete and fresh_count > 0
                    else "insufficient_data"
                )
            )
        ),
        "windowComplete": window_complete,
        "lastChildReport": last_child_report,
        "polling": {
            "intervalMs": int(interval_ms or 0),
            "maxExtremesAgeSeconds": float(max_extremes_age_seconds or 0.0),
            "staleAlertSeconds": float(stale_alert_seconds or 0.0),
            "staleSnapshotCount": stale_snapshot_count,
            "staleAlertIterationThreshold": stale_alert_iteration_threshold,
            "intervalWithinFreshnessWindow": (
                int(interval_ms or 0) <= int(float(max_extremes_age_seconds or 0.0) * 1000)
                if max_extremes_age_seconds is not None
                else None
            ),
            "freshnessReasonCounts": dict(freshness_reason_counts or {}),
        },
        "freshnessRate": (
            round(fresh_count / iterations_completed, 6)
            if iterations_completed
            else 0.0
        ),
        "health": {
            "completed": iterations_completed,
            "requested": iterations_requested,
            "windowComplete": window_complete,
            "interrupted": bool(stopped_at and iterations_completed < iterations_requested),
            "freshnessPassed": fresh_count,
            "freshnessFailed": diagnostic_count,
            "staleSnapshotAlert": (
                "bridge_no_fresh_extremes_for_stale_alert_window"
                if stale_snapshot_alert
                else ""
            ),
            "pollingFreshnessRatioWarning": (
                "bridge_interval_exceeds_extremes_freshness_window"
                if interval_ms is not None
                and max_extremes_age_seconds is not None
                and int(interval_ms) > float(max_extremes_age_seconds) * 1000
                else ""
            ),
        },
        "runClassification": (
            "live_shadow_bridge_window"
            if evidence_eligible
            else (
                "bridge_window_incomplete"
                if not window_complete
                else (
                    "bridge_window_contains_diagnostic_samples"
                    if diagnostic_count > 0 or fresh_count < iterations_completed
                    else (
                        "bridge_window_contains_invalid_child_reports"
                        if invalid_child_report_count > 0
                        else "bridge_window_without_eligible_child_reports"
                    )
                )
            )
        ),
        "zeroSignalReason": (
            ""
            if positive_signal_count > 0 and evidence_eligible
            else (
                "bridge_window_incomplete"
                if not window_complete
                else (
                    "bridge_contains_invalid_child_reports"
                    if invalid_child_report_count > 0
                    else "no_candidate_completed_positive_shadow_validation"
                )
            )
        ),
        "schemaValidation": {
            "ok": iterations_completed >= 0
            and child_report_count == iterations_completed
            and fresh_count + diagnostic_count == iterations_completed
            and eligible_child_report_count + invalid_child_report_count == child_report_count,
            "errors": [],
        },
    }
    if report["schemaValidation"]["ok"] is not True:
        report["schemaValidation"]["errors"].append("bridge_counter_inconsistent")
    report["reportHash"] = _canonical_hash(report)
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--extremes", type=Path, default=DEFAULT_EXTREMES_PATH)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT_PATH)
    parser.add_argument("--side-limit", type=int, default=5)
    parser.add_argument(
        "--max-extremes-age-seconds",
        type=float,
        default=float(os.getenv("UNIFIED_SHADOW_MAX_EXTREMES_AGE_SECONDS", "5")),
    )
    parser.add_argument("--iterations", type=int, default=1)
    parser.add_argument("--interval-ms", type=int, default=1_000)
    parser.add_argument(
        "--stale-alert-seconds",
        type=float,
        default=float(os.getenv("UNIFIED_SHADOW_STALE_ALERT_SECONDS", "600")),
    )
    parser.add_argument("--shadow-iterations", type=int, default=1)
    parser.add_argument("--timeout-seconds", type=int, default=20)
    parser.add_argument("--evidence-root", type=Path, default=run_unified_live_shadow.DEFAULT_EVIDENCE_ROOT)
    parser.add_argument("--bridge-report", type=Path, default=None)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    os.environ["UNIFIED_EXECUTOR_BROADCAST_ENABLED"] = "false"
    os.environ["TRIANGULAR_DIRECT_BROADCAST_ENABLED"] = "false"
    iterations = max(1, int(args.iterations))
    input_path = args.input if args.input.is_absolute() else SRC_ROOT / args.input
    extremes_path = args.extremes if args.extremes.is_absolute() else SRC_ROOT / args.extremes
    evidence_root = args.evidence_root if args.evidence_root.is_absolute() else (SRC_ROOT.parents[1] / args.evidence_root)
    bridge_run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ_unified-live-bridge")
    bridge_directory = evidence_root / bridge_run_id
    bridge_directory.mkdir(parents=True, exist_ok=True)
    bridge_report_path = (
        args.bridge_report
        if isinstance(args.bridge_report, Path) and args.bridge_report.is_absolute()
        else bridge_directory / "report.json"
    )
    child_index_path = bridge_directory / "child_reports.jsonl"
    started_at = _utc_now_iso()
    completed = fresh_count = diagnostic_count = positive_signal_count = child_report_count = 0
    eligible_child_report_count = invalid_child_report_count = 0
    freshness_reason_counts: dict[str, int] = {}
    last_child_report: str | None = None
    stop_requested = False

    def request_stop(_signum: int, _frame: Any) -> None:
        nonlocal stop_requested
        stop_requested = True

    previous_sigint = signal.getsignal(signal.SIGINT)
    signal.signal(signal.SIGINT, request_stop)

    try:
        for index in range(iterations):
            if stop_requested:
                break
            extremes = _read_json_object(extremes_path)
            shadow_input = build_shadow_input_from_extremes(
                extremes,
                source_path=extremes_path,
                side_limit=max(1, int(args.side_limit)),
                max_age_seconds=max(0.0, float(args.max_extremes_age_seconds)),
            )
            shadow_input["source"]["bridgeIteration"] = index
            write_shadow_input(input_path, shadow_input)

            shadow_args = argparse.Namespace(
                input=input_path,
                iterations=max(1, int(args.shadow_iterations)),
                interval_ms=max(0, int(args.interval_ms)),
                timeout_seconds=max(1, int(args.timeout_seconds)),
                evidence_root=args.evidence_root,
            )
            report = run_unified_live_shadow.run_shadow_once(shadow_args)
            fresh = bool(shadow_input.get("source", {}).get("freshnessPassed"))
            freshness_reason = str(shadow_input.get("source", {}).get("freshnessReason") or "unknown")
            freshness_reason_counts[freshness_reason] = freshness_reason_counts.get(freshness_reason, 0) + 1
            fresh_count += int(fresh)
            diagnostic_count += int(not fresh)
            positive_signal_count += int(report.get("positiveSignalCount") or 0)
            child_evidence_eligible = bool(
                report.get("evidenceSemantics", {}).get("evidenceEligible")
            )
            eligible_child_report_count += int(child_evidence_eligible)
            invalid_child_report_count += int(not child_evidence_eligible)
            child_report_count += 1
            completed += 1
            last_child_report = str(report.get("report") or "")
            child_record = {
                "iteration": index,
                "observedAt": shadow_input.get("observedAt"),
                "freshnessPassed": fresh,
                "freshnessReason": freshness_reason,
                "observedAgeSeconds": shadow_input.get("source", {}).get("observedAgeSeconds"),
                "maxAgeSeconds": shadow_input.get("source", {}).get("maxAgeSeconds"),
                "candidateReady": bool(
                    shadow_input.get("source", {}).get("candidateReady")
                ),
                "topCount": len(shadow_input.get("market_state", {}).get("top") or []),
                "bottomCount": len(shadow_input.get("market_state", {}).get("bottom") or []),
                "report": report.get("report"),
                "reportHash": report.get("reportHash"),
                "runId": report.get("runId"),
                "positiveSignalCount": report.get("positiveSignalCount"),
                "evidenceEligible": child_evidence_eligible,
                "validSampleCount": report.get("validSampleCount"),
                "invalidSampleCount": report.get("invalidSampleCount"),
            }
            with child_index_path.open("a", encoding="utf-8") as child_file:
                child_file.write(json.dumps(child_record, ensure_ascii=True, sort_keys=True) + "\n")
            _write_json(
                bridge_report_path,
                _bridge_report(
                    run_id=bridge_run_id,
                    started_at=started_at,
                    stopped_at=None,
                    input_path=input_path,
                    extremes_path=extremes_path,
                    child_index_path=child_index_path,
                    iterations_requested=iterations,
                    iterations_completed=completed,
                    fresh_count=fresh_count,
                    diagnostic_count=diagnostic_count,
                    positive_signal_count=positive_signal_count,
                    child_report_count=child_report_count,
                    eligible_child_report_count=eligible_child_report_count,
                    invalid_child_report_count=invalid_child_report_count,
                    last_child_report=last_child_report,
                    interval_ms=max(0, int(args.interval_ms)),
                    max_extremes_age_seconds=max(0.0, float(args.max_extremes_age_seconds)),
                    stale_alert_seconds=max(0.0, float(args.stale_alert_seconds)),
                    freshness_reason_counts=freshness_reason_counts,
                ),
            )
            if index + 1 < iterations and not stop_requested:
                time.sleep(max(0, int(args.interval_ms)) / 1000)
    finally:
        signal.signal(signal.SIGINT, previous_sigint)

    final_report = _bridge_report(
        run_id=bridge_run_id,
        started_at=started_at,
        stopped_at=_utc_now_iso(),
        input_path=input_path,
        extremes_path=extremes_path,
        child_index_path=child_index_path,
        iterations_requested=iterations,
        iterations_completed=completed,
        fresh_count=fresh_count,
        diagnostic_count=diagnostic_count,
        positive_signal_count=positive_signal_count,
        child_report_count=child_report_count,
        eligible_child_report_count=eligible_child_report_count,
        invalid_child_report_count=invalid_child_report_count,
        last_child_report=last_child_report,
        interval_ms=max(0, int(args.interval_ms)),
        max_extremes_age_seconds=max(0.0, float(args.max_extremes_age_seconds)),
        stale_alert_seconds=max(0.0, float(args.stale_alert_seconds)),
        freshness_reason_counts=freshness_reason_counts,
    )
    _write_json(bridge_report_path, final_report)
    print(json.dumps({"ok": True, "report": str(bridge_report_path), **final_report}, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
