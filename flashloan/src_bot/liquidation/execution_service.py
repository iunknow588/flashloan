from __future__ import annotations

from typing import Any


def prepare_execution_payload(payload: dict[str, Any], *, controls: dict[str, Any] | None = None) -> dict[str, Any]:
    prepared = dict(payload)
    prepared["execution_controls"] = dict(controls or prepared.get("execution_controls") or {})
    return prepared


def summarize_execution_result(payload: dict[str, Any], receipt: dict[str, Any] | None = None) -> dict[str, Any]:
    summarized = dict(payload)
    if receipt is not None:
        summarized["receipt"] = receipt
    summarized.setdefault("execution_summary", {
        "mode": summarized.get("mode"),
        "status": "submitted" if summarized.get("tx_hash") else "pending",
        "tx_hash": summarized.get("tx_hash"),
    })
    return summarized
