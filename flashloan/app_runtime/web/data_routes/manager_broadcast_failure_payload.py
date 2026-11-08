"""Broadcast Manager projections for the submission failure audit panel."""

from __future__ import annotations

from typing import Any

from .manager_page_compare_common import (
    SUBMISSION_SNAPSHOT_LIMIT,
    _int_or_none,
    _manager_read_error,
    _manager_snapshot,
)


MANAGER_BROADCAST_FAILURE_SCHEMA = "manager-broadcast-failure-audit-v1"


def _text(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None


def _diagnostics_by_attempt(rows: object) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    if not isinstance(rows, list):
        return grouped
    for row in rows:
        if not isinstance(row, dict):
            continue
        attempt_id = _text(row.get("attemptId"))
        if attempt_id:
            grouped.setdefault(attempt_id, []).append(row)
    return grouped


def _signed_profit(attempt: dict[str, Any]) -> dict[str, Any]:
    signed_pnl = attempt.get("signedPnl")
    return signed_pnl if isinstance(signed_pnl, dict) else {}


def _manager_failure_record(
    attempt: dict[str, Any],
    diagnostics: list[dict[str, Any]],
) -> dict[str, Any]:
    primary = diagnostics[0] if diagnostics else {}
    signed_pnl = _signed_profit(attempt)
    observed_at = (
        _text(primary.get("observedAt"))
        or _text(attempt.get("terminalAt"))
        or _text(attempt.get("updatedAt"))
        or _text(attempt.get("createdAt"))
    )
    diagnostic_message = _text(primary.get("message"))
    diagnostic_code = _text(primary.get("diagnosticCode"))
    status_reason = _text(attempt.get("statusReason"))
    broadcast_error = (
        diagnostic_message
        or diagnostic_code
        or status_reason
        or "manager_broadcast_failed"
    )
    amount = signed_pnl.get("amount")
    return {
        "schemaVersion": MANAGER_BROADCAST_FAILURE_SCHEMA,
        "recordedAt": observed_at,
        "observedAt": observed_at,
        "groupKey": _text(attempt.get("loopKey")),
        "routePath": _text(attempt.get("pathText")),
        "status": _text(primary.get("diagnosticType"))
        or status_reason
        or _text(attempt.get("status")),
        "broadcastStatus": _text(attempt.get("status")),
        "broadcastError": broadcast_error,
        "selectedProfitRaw": None if amount is None else str(amount),
        "selectedProfitCurrency": _text(signed_pnl.get("currency")),
        "selectedProfitSource": _text(signed_pnl.get("source")),
        "attemptFingerprint": _text(attempt.get("attemptId")),
        "attemptId": _text(attempt.get("attemptId")),
        "loopKey": _text(attempt.get("loopKey")),
        "txHash": _text(attempt.get("txHash")),
        "nonce": attempt.get("nonce"),
        "rpcUrl": _text(attempt.get("rpcUrl")),
        "rpcName": _text(attempt.get("rpcName")),
        "diagnostics": diagnostics,
        "managerAttempt": attempt,
    }


def broadcast_failure_manager_payload(
    *,
    chain_id: int | None,
    limit: int = 20,
    chain_selection: dict[str, Any] | None = None,
    request_id: str | None = None,
) -> dict[str, Any]:
    """Read failed broadcast attempts from Broadcast Manager."""

    if chain_id is None or int(chain_id) <= 0:
        return _manager_read_error(
            "MANAGER_CHAIN_ID_REQUIRED",
            "broadcast failure audit requires a selected chain id",
            upstream_status=400,
        )
    requested_limit = min(SUBMISSION_SNAPSHOT_LIMIT, max(0, int(limit)))
    manager_limit = max(1, requested_limit)
    snapshot = _manager_snapshot(
        "/v1/broadcasts",
        query={
            "chainId": int(chain_id),
            "status": "failed",
            "offset": 0,
            "limit": manager_limit,
        },
        request_id=request_id,
        invalid_detail="broadcast failure audit response must contain an object data field",
    )
    if not snapshot.get("ok"):
        return {
            **snapshot,
            "mode": "dex_closed_loop_broadcast_failures",
            "enabled": True,
            "dataSource": "manager",
            "available": False,
            "records": [],
            "recordCount": 0,
            "chain_selection": chain_selection or {},
        }

    data = snapshot["data"]
    status = data.get("status") if isinstance(data.get("status"), dict) else {}
    diagnostics_by_attempt = _diagnostics_by_attempt(data.get("diagnostics"))
    attempts = data.get("attempts") if isinstance(data.get("attempts"), list) else []
    records = []
    for attempt in attempts:
        if not isinstance(attempt, dict):
            continue
        attempt_id = _text(attempt.get("attemptId")) or ""
        records.append(
            _manager_failure_record(
                attempt,
                diagnostics_by_attempt.get(attempt_id, []),
            )
        )
    records = records[:requested_limit]
    return {
        "ok": True,
        "mode": "dex_closed_loop_broadcast_failures",
        "dataSource": "manager",
        "available": bool(status.get("enabled", True)),
        "recordCount": len(records),
        "records": records,
        "managerEnvelopeVersion": snapshot.get("managerEnvelopeVersion"),
        "managerStatus": {
            "enabled": bool(status.get("enabled")),
            "network": status.get("network"),
            "chainId": _int_or_none(status.get("chainId")),
            "broadcastVersion": status.get("broadcastVersion"),
            "broadcastChecksum": status.get("materializedChecksum"),
            "failedCount": _int_or_none(status.get("failedCount")),
        },
        "chain_selection": chain_selection or {},
    }
