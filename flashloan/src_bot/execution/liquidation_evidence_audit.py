from __future__ import annotations

import json
from typing import Any

from core.sensitive_data import redact_sensitive_text
from web.page_state import normalize_execution_phase, normalize_tx_hash, receipt_status


REQUIRED_SAMPLE_LABELS = (
    "healthy",
    "warning",
    "liquidatable",
    "close_factor_failure",
    "dust_leftover",
    "low_profit",
    "high_slippage_failure",
)
REQUIRED_FAILURE_LABELS = (
    "close_factor_failure",
    "dust_leftover",
    "low_profit",
    "high_slippage_failure",
)


def _json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _sensitive_clean(value: Any) -> bool:
    text = _json_text(value)
    return redact_sensitive_text(text) == text


def _sample_payload(sample: dict[str, Any]) -> dict[str, Any]:
    payload = sample.get("payload")
    return payload if isinstance(payload, dict) else {}


def _sample_phase(sample: dict[str, Any]) -> str | None:
    payload = _sample_payload(sample)
    return payload.get("execution_phase") or sample.get("execution_phase")


def _sample_tx_hash(sample: dict[str, Any]) -> str | None:
    payload = _sample_payload(sample)
    return normalize_tx_hash(payload) or normalize_tx_hash(sample)


def _sample_receipt_status(sample: dict[str, Any]) -> int | None:
    payload = _sample_payload(sample)
    value = payload.get("receipt_status")
    if value is not None:
        try:
            return int(value)
        except (TypeError, ValueError):
            return None
    return receipt_status(payload.get("receipt") or sample.get("receipt") or {})


def _sample_retryable(sample: dict[str, Any]) -> bool | None:
    payload = _sample_payload(sample)
    value = payload.get("retryable", sample.get("retryable"))
    return bool(value) if value is not None else None


def audit_attempt_failure_sample_pair(
    attempt: dict[str, Any],
    sample: dict[str, Any],
    *,
    require_tx_hash_when_submitted: bool = True,
) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    attempt_phase = normalize_execution_phase(attempt)
    sample_phase = _sample_phase(sample)
    attempt_tx_hash = normalize_tx_hash(attempt)
    sample_tx_hash = _sample_tx_hash(sample)
    attempt_receipt_status = receipt_status(attempt.get("receipt") or {})
    sample_receipt_status = _sample_receipt_status(sample)

    if attempt.get("account") and sample.get("account"):
        if str(attempt["account"]).lower() != str(sample["account"]).lower():
            errors.append("account_mismatch")
    else:
        warnings.append("account_missing")

    attempt_block = (attempt.get("quote") or {}).get("quote_block") or (attempt.get("preflight") or {}).get("block_number")
    sample_block = sample.get("block_number")
    if attempt_block is not None and sample_block is not None:
        try:
            if int(attempt_block) != int(sample_block):
                errors.append("block_number_mismatch")
        except (TypeError, ValueError):
            warnings.append("block_number_unparseable")

    if not attempt_phase:
        errors.append("attempt_phase_missing")
    if not sample_phase:
        errors.append("sample_phase_missing")
    if attempt_phase and sample_phase and attempt_phase != sample_phase:
        errors.append("execution_phase_mismatch")

    submitted_states = {"waiting_receipt", "confirmed_success", "confirmed_failed"}
    if require_tx_hash_when_submitted and attempt_phase in submitted_states and not (attempt_tx_hash or sample_tx_hash):
        errors.append("submitted_tx_hash_missing")
    if attempt_tx_hash and sample_tx_hash and attempt_tx_hash.lower() != sample_tx_hash.lower():
        errors.append("tx_hash_mismatch")

    if attempt_receipt_status is not None and sample_receipt_status is not None:
        if attempt_receipt_status != sample_receipt_status:
            errors.append("receipt_status_mismatch")

    if _sample_retryable(sample) is None:
        errors.append("sample_retryable_missing")
    if not sample.get("failure_type"):
        errors.append("sample_failure_type_missing")
    if not _sensitive_clean(attempt) or not _sensitive_clean(sample):
        errors.append("unredacted_sensitive_value")

    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "attempt": {
            "id": attempt.get("id"),
            "account": attempt.get("account"),
            "state": attempt.get("state"),
            "phase": attempt_phase,
            "tx_hash": attempt_tx_hash,
            "receipt_status": attempt_receipt_status,
        },
        "sample": {
            "id": sample.get("id"),
            "account": sample.get("account"),
            "failure_type": sample.get("failure_type"),
            "phase": sample_phase,
            "tx_hash": sample_tx_hash,
            "receipt_status": sample_receipt_status,
            "retryable": _sample_retryable(sample),
        },
    }


def audit_sample_manifest(
    manifest: dict[str, Any],
    *,
    require_failure_replayable: bool = True,
) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    samples = manifest.get("samples") if isinstance(manifest.get("samples"), list) else []
    by_label = {str(item.get("label")): item for item in samples if isinstance(item, dict)}

    for label in REQUIRED_SAMPLE_LABELS:
        if label not in by_label:
            errors.append(f"sample_missing:{label}")

    ready_labels = [
        label
        for label, item in by_label.items()
        if item.get("status") == "ready"
    ]
    pending_labels = [
        label
        for label, item in by_label.items()
        if item.get("status") == "pending_real_sample"
    ]
    replayable_labels = [
        label
        for label, item in by_label.items()
        if (item.get("replay") or {}).get("replayable") is True
    ]
    missing_replay_fields = {
        label: (item.get("replay") or {}).get("missing_fields", [])
        for label, item in by_label.items()
        if (item.get("replay") or {}).get("missing_fields")
    }

    for label in REQUIRED_FAILURE_LABELS:
        item = by_label.get(label)
        if not item:
            continue
        if item.get("status") != "ready":
            warnings.append(f"failure_sample_pending:{label}")
            if require_failure_replayable:
                errors.append(f"failure_sample_not_ready:{label}")
        elif require_failure_replayable and (item.get("replay") or {}).get("replayable") is not True:
            errors.append(f"failure_sample_not_replayable:{label}")

    if not _sensitive_clean(manifest):
        errors.append("unredacted_sensitive_value")

    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "schema_version": manifest.get("schema_version"),
        "source_count": manifest.get("source_count"),
        "ready_labels": ready_labels,
        "pending_labels": pending_labels,
        "replayable_labels": replayable_labels,
        "missing_replay_fields": missing_replay_fields,
    }
