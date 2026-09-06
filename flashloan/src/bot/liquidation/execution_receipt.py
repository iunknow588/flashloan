from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from bot.page_state import ExecutionStatus


@dataclass(frozen=True)
class ReceiptTransition:
    status: ExecutionStatus
    phase: str
    message: str
    receipt_status: int


def receipt_transition(receipt_data: dict[str, Any], *, success_message: str, failure_message: str) -> ReceiptTransition:
    status = int(receipt_data.get("status") or 0)
    success = status == 1
    return ReceiptTransition(
        ExecutionStatus.SUCCESS if success else ExecutionStatus.CONFIRMED_FAILED,
        "confirmed_success" if success else "confirmed_failed",
        success_message if success else failure_message,
        status,
    )


def _execution_plan(account_report: Any) -> Any:
    return account_report.get("execution_plan") if isinstance(account_report, dict) else None


def _execution_controls(source_controls: dict[str, Any] | None, fallback_controls: dict[str, Any], *, force: bool) -> dict[str, Any]:
    return dict(source_controls or fallback_controls) | {"manual_force": bool(force)}


def build_flashloan_receipt_result(
    *,
    preflight: dict[str, Any],
    preflight_info: dict[str, Any],
    controls: dict[str, Any],
    request: dict[str, Any],
    receipt_data: dict[str, Any],
    tx_hash: str,
    broadcast: dict[str, Any],
    force: bool,
    execution_phase: str,
) -> dict[str, Any]:
    account_report = preflight.get("account_report") or {}
    return {
        "mode": "flashloan",
        "executor": preflight.get("executor"),
        "request": request,
        "preflight": {
            **preflight_info,
            "static_call_required": bool(controls["require_static_call"]),
            "static_call_passed": bool(preflight_info.get("static_call_passed")),
        },
        "receipt": receipt_data,
        "tx_hash": tx_hash,
        "broadcast": broadcast,
        "account_report": account_report,
        "execution_plan": _execution_plan(account_report),
        "execution_controls": _execution_controls(preflight.get("execution_controls"), controls, force=force),
        "execution_phase": execution_phase,
    }


def build_self_funded_receipt_result(
    *,
    payload: dict[str, Any],
    controls: dict[str, Any],
    pool_address: str,
    sender: str,
    request: dict[str, Any],
    approval_receipt: dict[str, Any],
    receipt_data: dict[str, Any],
    tx_hash: str,
    approval_broadcast: dict[str, Any],
    broadcast: dict[str, Any],
    force: bool,
    execution_phase: str,
) -> dict[str, Any]:
    account_report = payload.get("account_report") or {}
    return {
        "mode": "self_funded",
        "pool": pool_address,
        "sender": sender,
        "request": request,
        "approval_receipt": approval_receipt,
        "receipt": receipt_data,
        "tx_hash": tx_hash,
        "approval_broadcast": approval_broadcast,
        "broadcast": broadcast,
        "account_report": account_report,
        "execution_plan": _execution_plan(account_report),
        "execution_controls": _execution_controls(payload.get("execution_controls"), controls, force=force),
        "execution_phase": execution_phase,
    }
