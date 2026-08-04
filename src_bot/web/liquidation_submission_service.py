from __future__ import annotations

import os
from typing import Any

from db.storage_liquidation_attempts import record_liquidation_execution_attempt
from execution.receipt_formatter import format_tx_receipt
from execution.revert_parser import build_failure_record


def archive_submission_failure(payload: dict[str, Any], *, parsed: dict[str, Any] | None = None) -> None:
    try:
        database_url = os.getenv("DATABASE_URL", "").strip()
        if not database_url:
            return
        record = build_failure_record(parsed or {}, payload=payload)
        record_liquidation_execution_attempt(
            database_url,
            account=record.get("account"),
            mode=str(payload.get("mode") or "flashloan"),
            state="confirmed_failed",
            error=record.get("failure_reason"),
            request_payload=payload.get("request") or {},
            quote=payload.get("quote") or {},
            preflight=payload.get("preflight") or {},
        )
    except Exception:
        return


def build_submission_summary(payload: dict[str, Any], receipt: dict[str, Any] | None = None) -> dict[str, Any]:
    summary = dict(payload)
    if receipt is not None:
        summary["receipt"] = format_tx_receipt(receipt)
    summary.setdefault("execution_summary", {
        "mode": summary.get("mode"),
        "status": "submitted" if summary.get("tx_hash") else "pending",
        "tx_hash": summary.get("tx_hash"),
    })
    return summary
