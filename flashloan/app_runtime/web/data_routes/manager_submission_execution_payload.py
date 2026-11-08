"""Broadcast attempt detail, execution and clear adapters."""

from __future__ import annotations

from typing import Any

from flask import has_app_context

from .manager_page_compare_common import _int_or_none, _manager_snapshot, _shadow_error


def submission_detail_manager_payload(
    legacy_attempt: dict[str, Any],
    *,
    chain_id: int | None,
    attempt_id: int | str,
    request_id: str | None = None,
) -> dict[str, Any]:
    """Read one manager broadcast attempt without changing legacy detail fields."""

    requested_attempt_id = str(attempt_id).strip()
    if chain_id is None:
        return _shadow_error(
            "MANAGER_CHAIN_ID_REQUIRED",
            "submission detail shadow requires a selected chain id",
            data_source="legacy",
        )
    snapshot = _manager_snapshot(
        "/v1/broadcasts",
        query={
            "chainId": int(chain_id),
            "attemptId": requested_attempt_id,
            "limit": 1,
        },
        request_id=request_id,
        invalid_detail="broadcast detail response must contain an object data field",
    )
    if not snapshot.get("ok"):
        return {
            **snapshot,
            "enabled": True,
            "requestedAttemptId": requested_attempt_id,
        }
    data = snapshot["data"]
    status = data.get("status") if isinstance(data.get("status"), dict) else {}
    attempts = data.get("attempts") if isinstance(data.get("attempts"), list) else []
    manager_attempt = next(
        (
            row
            for row in attempts
            if isinstance(row, dict)
            and str(row.get("attemptId") or "").strip() == requested_attempt_id
        ),
        None,
    )
    diagnostics = [
        row
        for row in (
            data.get("diagnostics") if isinstance(data.get("diagnostics"), list) else []
        )
        if isinstance(row, dict)
        and str(row.get("attemptId") or "").strip() == requested_attempt_id
    ]
    from .manager_submission_payload import _manager_attempt_detail_dto

    manager_attempt_dto = _manager_attempt_detail_dto(
        manager_attempt,
        requested_attempt_id=requested_attempt_id,
    )
    if manager_attempt_dto is not None:
        manager_attempt_dto["network"] = (
            status.get("network") or manager_attempt_dto.get("network")
        )
    return {
        "ok": True,
        "source": "manager",
        "dataSource": "manager",
        "enabled": True,
        "managerEnvelopeVersion": snapshot.get("managerEnvelopeVersion"),
        "requestedAttemptId": requested_attempt_id,
        "found": manager_attempt is not None,
        "snapshotLimit": 1,
        "attemptSnapshotCount": len(attempts),
        "diagnosticSnapshotCount": len(diagnostics),
        "managerStatus": {
            "enabled": bool(status.get("enabled")),
            "network": status.get("network"),
            "chainId": _int_or_none(status.get("chainId")),
            "protocol": status.get("protocol"),
            "scopeKey": status.get("scopeKey"),
            "broadcastVersion": status.get("broadcastVersion"),
            "broadcastChecksum": status.get("materializedChecksum"),
            "reviewVersion": status.get("reviewVersion"),
            "reviewChecksum": status.get("reviewChecksum"),
            "closedLoopVersion": status.get("closedLoopVersion"),
            "closedLoopChecksum": status.get("closedLoopChecksum"),
            "attemptCount": _int_or_none(status.get("attemptCount")),
            "pendingCount": _int_or_none(status.get("pendingCount")),
            "successCount": _int_or_none(status.get("successCount")),
            "failedCount": _int_or_none(status.get("failedCount")),
            "terminalCount": _int_or_none(status.get("terminalCount")),
        },
        "attempt": manager_attempt_dto,
        "managerAttempt": manager_attempt,
        "diagnostics": diagnostics,
        "legacyAttempt": {
            "id": legacy_attempt.get("id"),
            "status": legacy_attempt.get("status") or legacy_attempt.get("state"),
            "nonce": _int_or_none(legacy_attempt.get("nonce")),
            "txHash": legacy_attempt.get("txHash") or legacy_attempt.get("tx_hash"),
        },
        "comparisonReadyForFieldSwitch": False,
        "comparisonReason": "The manager attempt query is read-only and manager attempt ids are strings; production identity mapping to legacy SQLite integer ids remains unverified.",
    }


def submission_execution_manager_payload(
    *,
    chain_id: int | None,
    status: str,
    mode: str,
    page: int,
    page_size: int,
    chain_selection: dict[str, Any] | None,
    request_id: str | None = None,
) -> dict[str, Any]:
    """Read a manager-filtered execution page without applying UI-side status rules."""

    manager_status = str(status or "").strip().lower()
    if manager_status not in {"success", "failed"}:
        return _shadow_error(
            "MANAGER_STATUS_INVALID",
            "execution status must be success or failed",
            data_source="legacy",
        )
    if chain_id is None:
        return _shadow_error(
            "MANAGER_CHAIN_ID_REQUIRED",
            "submission execution read requires a selected chain id",
            data_source="legacy",
        )
    safe_page = max(1, int(page))
    safe_page_size = max(1, min(100, int(page_size)))
    offset = (safe_page - 1) * safe_page_size
    snapshot = _manager_snapshot(
        "/v1/broadcasts",
        query={
            "chainId": int(chain_id),
            "status": manager_status,
            "offset": offset,
            "limit": safe_page_size,
        },
        request_id=request_id,
        invalid_detail="broadcast execution response must contain an object data field",
    )
    if not snapshot.get("ok"):
        return {
            **snapshot,
            "enabled": True,
            "dataSource": "manager",
        }
    data = snapshot["data"]
    manager_status_payload = (
        data.get("status") if isinstance(data.get("status"), dict) else {}
    )
    from .manager_submission_payload import _manager_attempt_detail_dto

    manager_attempts = [
        mapped
        for mapped in (
            _manager_attempt_detail_dto(
                row,
                requested_attempt_id=str(row.get("attemptId") or ""),
            )
            for row in (
                data.get("attempts") if isinstance(data.get("attempts"), list) else []
            )
            if isinstance(row, dict)
        )
        if mapped is not None
    ]
    for attempt in manager_attempts:
        attempt["network"] = manager_status_payload.get("network") or attempt.get("network")
    total = _int_or_none(manager_status_payload.get(f"{manager_status}Count")) or 0
    total_pages = max(1, -(-total // safe_page_size))
    unique_tx_hashes = {
        str(attempt.get("tx_hash") or "").lower()
        for attempt in manager_attempts
        if attempt.get("tx_hash")
    }
    return {
        "ok": True,
        "source": "manager",
        "enabled": True,
        "dataSource": "manager",
        "mode": mode,
        "recordKind": "submission",
        "summary": {
            "attemptCount": total,
            "pageAttemptCount": len(manager_attempts),
            "successCount": total if manager_status == "success" else 0,
            "failedCount": total if manager_status == "failed" else 0,
            "systemErrorCount": total if manager_status == "failed" else 0,
            "businessBlockedCount": 0,
            "blockedCount": 0,
            "uniqueTxHash": {
                "total": len(unique_tx_hashes),
                "success": len(unique_tx_hashes)
                if manager_status == "success"
                else 0,
                "failed": len(unique_tx_hashes)
                if manager_status == "failed"
                else 0,
                "blocked": 0,
            },
        },
        "groups": {},
        "attempts": manager_attempts,
        "pagination": {
            "page": safe_page,
            "page_size": safe_page_size,
            "total": total,
            "total_pages": total_pages,
        },
        "chain_selection": chain_selection or {},
        "dex_strategy": "closed_loop_routes",
        "managerStatus": {
            "enabled": bool(manager_status_payload.get("enabled")),
            "network": manager_status_payload.get("network"),
            "chainId": _int_or_none(manager_status_payload.get("chainId")),
            "protocol": manager_status_payload.get("protocol"),
            "scopeKey": manager_status_payload.get("scopeKey"),
            "broadcastVersion": manager_status_payload.get("broadcastVersion"),
            "broadcastChecksum": manager_status_payload.get("materializedChecksum"),
            "reviewVersion": manager_status_payload.get("reviewVersion"),
            "reviewChecksum": manager_status_payload.get("reviewChecksum"),
            "closedLoopVersion": manager_status_payload.get("closedLoopVersion"),
            "closedLoopChecksum": manager_status_payload.get("closedLoopChecksum"),
            "attemptCount": _int_or_none(manager_status_payload.get("attemptCount")),
            "pendingCount": _int_or_none(manager_status_payload.get("pendingCount")),
            "successCount": _int_or_none(manager_status_payload.get("successCount")),
            "failedCount": _int_or_none(manager_status_payload.get("failedCount")),
            "terminalCount": _int_or_none(manager_status_payload.get("terminalCount")),
        },
        "managerSnapshotCount": len(manager_attempts),
        "comparisonReadyForFieldSwitch": False,
        "comparisonReason": "Execution status filtering and pagination are manager-owned, but production identity continuity with legacy SQLite ids is still unverified.",
    }


def submission_clear_manager_payload(
    *,
    chain_id: int | None,
    dry_run: bool,
    request_id: str | None,
    idempotency_key: str,
) -> dict[str, Any]:
    """Clear terminal broadcast attempts through the request-bound RuntimeCore."""

    if chain_id is None:
        return _shadow_error(
            "MANAGER_CHAIN_ID_REQUIRED",
            "submission clear requires a selected chain id",
            data_source="manager",
            upstream_status=400,
        )
    if not str(request_id or "").strip():
        return _shadow_error(
            "MANAGER_REQUEST_ID_REQUIRED",
            "submission clear requires a request id",
            data_source="manager",
            upstream_status=400,
        )
    if not str(idempotency_key or "").strip():
        return _shadow_error(
            "MANAGER_IDEMPOTENCY_KEY_REQUIRED",
            "submission clear requires an idempotency key",
            data_source="manager",
            upstream_status=400,
        )
    if not has_app_context():
        return _shadow_error(
            "RUNTIME_CORE_UNAVAILABLE",
            "chain runtime is not bound to this request",
            data_source="manager",
            upstream_status=503,
        )
    from flask import g

    try:
        chain_runtime = getattr(g, "chain_runtime", None)
    except RuntimeError:
        chain_runtime = None
    if chain_runtime is None:
        return _shadow_error(
            "RUNTIME_CORE_UNAVAILABLE",
            "chain runtime is not bound to this request",
            data_source="manager",
            upstream_status=503,
        )
    if not hasattr(chain_runtime, "execute_command"):
        return _shadow_error(
            "RUNTIME_CORE_COMMAND_UNAVAILABLE",
            "chain runtime does not expose RuntimeCore commands",
            data_source="manager",
            upstream_status=503,
        )
    try:
        envelope = chain_runtime.execute_command(
            "clear_broadcasts",
            request_id=str(request_id).strip(),
            idempotency_key=str(idempotency_key).strip(),
            dry_run=bool(dry_run),
        )
    except (TypeError, ValueError, RuntimeError) as exc:
        return _shadow_error(
            "RUNTIME_CORE_COMMAND_FAILED",
            f"RuntimeCore clear_broadcasts command failed: {str(exc)[:500]}",
            data_source="manager",
            upstream_status=503,
        )
    if not isinstance(envelope, dict):
        return _shadow_error(
            "RUNTIME_CORE_INVALID_RESPONSE",
            "RuntimeCore clear_broadcasts command must return an object",
            data_source="manager",
            upstream_status=503,
        )
    if envelope.get("ok") is False:
        return _shadow_error(
            str(envelope.get("code") or "RUNTIME_CORE_COMMAND_FAILED"),
            str(envelope.get("detail") or "RuntimeCore clear_broadcasts command failed"),
            data_source="manager",
            upstream_status=(
                envelope.get("upstreamStatus")
                if isinstance(envelope.get("upstreamStatus"), int)
                else 503
            ),
        )
    if envelope.get("command") != "clear_broadcasts":
        return _shadow_error(
            "RUNTIME_CORE_INVALID_RESPONSE",
            "RuntimeCore returned an unexpected clear command",
            data_source="manager",
            upstream_status=503,
        )
    if envelope.get("dataSource") != "rust_runtime_core":
        return _shadow_error(
            "RUNTIME_CORE_INVALID_RESPONSE",
            "RuntimeCore clear command has an unexpected dataSource",
            data_source="manager",
            upstream_status=503,
        )
    if _int_or_none(envelope.get("chainId")) != int(chain_id):
        return _shadow_error(
            "RUNTIME_CORE_SCOPE_MISMATCH",
            "RuntimeCore clear command chainId does not match the requested chain",
            data_source="manager",
            upstream_status=503,
        )
    if not isinstance(envelope.get("dryRun"), bool):
        return _shadow_error(
            "RUNTIME_CORE_INVALID_RESPONSE",
            "RuntimeCore clear command must include a Boolean dryRun",
            data_source="manager",
            upstream_status=503,
        )
    if not isinstance(envelope.get("idempotentReplay"), bool):
        return _shadow_error(
            "RUNTIME_CORE_INVALID_RESPONSE",
            "RuntimeCore clear command must include Boolean idempotentReplay",
            data_source="manager",
            upstream_status=503,
        )
    return {
        "ok": True,
        "source": "manager",
        "dataSource": "manager",
        "runtimeDataSource": envelope.get("dataSource"),
        "managerEnvelopeVersion": envelope.get("version"),
        "cleared": _int_or_none(envelope.get("clearedAttempts")) or 0,
        "remaining": _int_or_none(envelope.get("remainingAttempts")) or 0,
        "clearedDiagnostics": _int_or_none(envelope.get("clearedDiagnostics")) or 0,
        "remainingDiagnostics": _int_or_none(envelope.get("remainingDiagnostics")) or 0,
        "dryRun": bool(envelope.get("dryRun")),
        "idempotentReplay": bool(envelope.get("idempotentReplay")),
        "managerResult": envelope,
    }


__all__ = [
    "submission_clear_manager_payload",
    "submission_detail_manager_payload",
    "submission_execution_manager_payload",
]
