from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from execution.liquidation_payload import LiquidationExecutionPayloadConfig, build_liquidation_execution_payload


SAMPLE_LABELS = (
    "healthy",
    "warning",
    "liquidatable",
    "close_factor_failure",
    "dust_leftover",
    "low_profit",
    "high_slippage_failure",
)
FAILURE_SAMPLE_LABELS = (
    "close_factor_failure",
    "dust_leftover",
    "low_profit",
    "high_slippage_failure",
)
FailureSampleRecorder = Callable[..., int]


@dataclass(frozen=True)
class LiquidationSampleSelection:
    label: str
    report: dict[str, Any]
    score: float


def _summary(report: dict[str, Any]) -> dict[str, Any]:
    return report.get("summary") or {}


def _recommended_candidate(report: dict[str, Any]) -> dict[str, Any]:
    return report.get("recommended_candidate") or {}


def _health_factor(report: dict[str, Any]) -> float:
    summary = _summary(report)
    value = summary.get("health_factor", report.get("health_factor", 0.0))
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _net_profit(report: dict[str, Any]) -> float:
    candidate = _recommended_candidate(report)
    profit = candidate.get("estimated_profit") or {}
    try:
        return float(profit.get("net_profit_base") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _debt_to_cover(report: dict[str, Any]) -> int:
    candidate = _recommended_candidate(report)
    try:
        return int(candidate.get("amount_to_pass_to_liquidation_call") or candidate.get("max_debt_to_liquidate") or 0)
    except (TypeError, ValueError):
        return 0


def _repay_base_source(report: dict[str, Any]) -> str:
    candidate = _recommended_candidate(report)
    profit = candidate.get("estimated_profit") or {}
    return str(profit.get("repay_base_source") or candidate.get("repay_base_source") or "")


def classify_liquidation_sample_label(report: dict[str, Any]) -> str | None:
    summary = _summary(report)
    status = str(summary.get("status") or report.get("status") or "").lower()
    if status == "healthy":
        return "healthy"
    if status == "warning":
        return "warning"
    if status != "liquidatable":
        return None

    net_profit = _net_profit(report)
    debt_to_cover = _debt_to_cover(report)
    repay_source = _repay_base_source(report)
    execution_plan = report.get("execution_plan") or {}
    reason = str(execution_plan.get("reason") or "").lower()
    preflight = report.get("preflight") or {}
    preflight_error = str(preflight.get("static_call_error") or "").lower()

    if "close factor" in reason or repay_source == "close_factor_fallback":
        return "close_factor_failure"
    if debt_to_cover > 0 and debt_to_cover <= 100:
        return "dust_leftover"
    if preflight.get("static_call_status") == "error" and ("slippage" in preflight_error or "amountoutmin" in preflight_error):
        return "high_slippage_failure"
    if net_profit <= 5.0:
        return "low_profit"
    return "liquidatable"


def _selection_score(label: str, report: dict[str, Any]) -> float:
    health_factor = _health_factor(report)
    net_profit = _net_profit(report)
    debt_to_cover = _debt_to_cover(report)
    if label == "healthy":
        return health_factor
    if label == "warning":
        return -abs(health_factor - 1.0)
    if label == "liquidatable":
        return net_profit
    if label == "close_factor_failure":
        return -float(debt_to_cover)
    if label == "dust_leftover":
        return -float(debt_to_cover)
    if label == "low_profit":
        return -abs(net_profit)
    if label == "high_slippage_failure":
        preflight = report.get("preflight") or {}
        return 1.0 if preflight.get("static_call_status") == "error" else 0.0
    return 0.0


def select_liquidation_sample_reports(reports: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    selected: dict[str, LiquidationSampleSelection] = {}
    for report in reports:
        label = classify_liquidation_sample_label(report)
        if not label:
            continue
        score = _selection_score(label, report)
        current = selected.get(label)
        if current is None or score > current.score:
            selected[label] = LiquidationSampleSelection(label=label, report=report, score=score)
    return {label: item.report for label, item in selected.items()}


def build_liquidation_sample_record(
    report: dict[str, Any],
    *,
    label: str,
    executor_address: str = "",
    router_address: str = "",
    deadline_seconds: int = 300,
) -> dict[str, Any]:
    summary = _summary(report)
    record: dict[str, Any] = {
        "label": label,
        "account": report.get("account"),
        "source": report.get("source") or report.get("context", {}).get("source"),
        "summary": summary,
        "recommended_candidate": _recommended_candidate(report),
        "execution_plan": report.get("execution_plan") or {},
        "context": report.get("context") or {},
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "input_status": summary.get("status"),
    }
    if label in {"liquidatable", "low_profit", "close_factor_failure", "dust_leftover", "high_slippage_failure"}:
        try:
            payload = build_liquidation_execution_payload(
                report,
                executor_address=executor_address or str(report.get("context", {}).get("executor_address") or ""),
                router_address=router_address or str(report.get("context", {}).get("router_address") or ""),
                deadline=int(datetime.now(timezone.utc).timestamp()) + max(30, int(deadline_seconds)),
                config=LiquidationExecutionPayloadConfig(),
            )
            record["payload"] = payload
            record["preflight"] = payload.get("preflight") or {}
        except Exception as exc:
            record["payload_error"] = str(exc)
    return record


def build_liquidation_failure_sample_record(
    report: dict[str, Any],
    *,
    label: str,
    executor_address: str = "",
    router_address: str = "",
    deadline_seconds: int = 300,
) -> dict[str, Any]:
    candidate = _recommended_candidate(report)
    payload = build_liquidation_sample_record(
        report,
        label=label,
        executor_address=executor_address,
        router_address=router_address,
        deadline_seconds=deadline_seconds,
    )
    preflight = payload.get("preflight") or report.get("preflight") or {}
    execution_plan = report.get("execution_plan") or {}
    reason = (
        preflight.get("static_call_error")
        or execution_plan.get("reason")
        or payload.get("payload_error")
        or f"sample classified as {label}"
    )
    return {
        "account": report.get("account"),
        "block_number": report.get("block_number") or (report.get("context") or {}).get("block_number"),
        "collateral_asset": candidate.get("collateral_asset") or candidate.get("collateral_symbol"),
        "debt_asset": candidate.get("debt_asset") or candidate.get("debt_symbol"),
        "failure_type": label,
        "failure_reason": str(reason),
        "payload": payload,
        "source": "liquidation_sample_library",
    }


def serialize_liquidation_failure_samples(
    database_url: str,
    reports: list[dict[str, Any]],
    *,
    recorder: FailureSampleRecorder | None = None,
    executor_address: str = "",
    router_address: str = "",
    deadline_seconds: int = 300,
) -> dict[str, Any]:
    if recorder is None:
        from db.storage import record_liquidation_failure_sample as recorder

    selected = select_liquidation_sample_reports(reports)
    inserted: list[dict[str, Any]] = []
    pending: list[str] = []
    for label in FAILURE_SAMPLE_LABELS:
        report = selected.get(label)
        if not report:
            pending.append(label)
            continue
        record = build_liquidation_failure_sample_record(
            report,
            label=label,
            executor_address=executor_address,
            router_address=router_address,
            deadline_seconds=deadline_seconds,
        )
        sample_id = recorder(database_url, **record)
        inserted.append(
            {
                "id": int(sample_id or 0),
                "label": label,
                "account": record.get("account"),
                "failure_reason": record.get("failure_reason"),
            }
        )
    return {
        "database_url_configured": bool(database_url),
        "inserted_count": len(inserted),
        "inserted": inserted,
        "pending_labels": pending,
    }


def build_liquidation_sample_manifest(
    reports: list[dict[str, Any]],
    *,
    executor_address: str = "",
    router_address: str = "",
    deadline_seconds: int = 300,
) -> dict[str, Any]:
    selected = select_liquidation_sample_reports(reports)
    files: dict[str, str] = {}
    samples = []
    for label in SAMPLE_LABELS:
        report = selected.get(label)
        if report:
            files[label] = f"{label}.json"
            samples.append(
                {
                    "label": label,
                    "status": "ready",
                    "file": files[label],
                    "account": report.get("account"),
                    "health_factor": _health_factor(report),
                }
            )
        else:
            samples.append({"label": label, "status": "pending_real_sample", "file": None})
    return {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source_count": len(reports),
        "samples": samples,
        "selected": selected,
        "files": files,
    }


def write_liquidation_sample_library(
    reports: list[dict[str, Any]],
    output_dir: str | Path,
    *,
    executor_address: str = "",
    router_address: str = "",
    deadline_seconds: int = 300,
) -> dict[str, Any]:
    manifest = build_liquidation_sample_manifest(
        reports,
        executor_address=executor_address,
        router_address=router_address,
        deadline_seconds=deadline_seconds,
    )
    raw_output = Path(output_dir)
    raw_output.mkdir(parents=True, exist_ok=True)
    files: dict[str, str] = manifest.get("files") or {}
    for label, file_name in files.items():
        report = manifest["selected"][label]
        record = build_liquidation_sample_record(
            report,
            label=label,
            executor_address=executor_address,
            router_address=router_address,
            deadline_seconds=deadline_seconds,
        )
        (raw_output / file_name).write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    index = dict(manifest)
    index.pop("selected", None)
    index.pop("files", None)
    (raw_output / "index.json").write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
    return index
