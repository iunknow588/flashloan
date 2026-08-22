from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from page_state import ExecutionStatus


@dataclass(frozen=True)
class ExecutionTransition:
    status: ExecutionStatus
    phase: str
    message: str


def blocked_submission_status(payload: dict[str, Any]) -> ExecutionStatus:
    return ExecutionStatus.HARD_BLOCKED if payload.get("block_level") == "hard" else ExecutionStatus.SOFT_BLOCKED


def submission_gate_transition(payload: dict[str, Any], *, after_preflight: bool = False) -> ExecutionTransition:
    if payload.get("submission_allowed"):
        return ExecutionTransition(
            ExecutionStatus.READY_TO_SUBMIT,
            "ready_to_submit",
            "execution payload passed preflight" if after_preflight else "execution payload is ready to submit",
        )
    if payload.get("block_level") == "soft":
        return ExecutionTransition(
            ExecutionStatus.SOFT_BLOCKED,
            "soft_blocked_after_preflight" if after_preflight else "soft_blocked",
            "execution payload is soft blocked after preflight" if after_preflight else "execution payload is soft blocked",
        )
    if payload.get("block_level") == "hard":
        return ExecutionTransition(
            ExecutionStatus.HARD_BLOCKED,
            "hard_blocked_after_preflight" if after_preflight else "hard_blocked",
            "execution payload is hard blocked after preflight" if after_preflight else "execution payload is hard blocked",
        )
    return ExecutionTransition(
        ExecutionStatus.PREFLIGHTING if after_preflight else ExecutionStatus.READY_FOR_PREFLIGHT,
        "preflighting" if after_preflight else "ready_for_preflight",
        "execution payload is still preflighting" if after_preflight else "execution payload is ready for preflight",
    )


def apply_submission_gate(
    payload: dict[str, Any],
    *,
    controls: dict[str, Any] | None = None,
    mode: str = "flashloan",
    preflight_attacher=None,
) -> dict[str, Any]:
    """Apply the shared liquidation preflight gate at the domain boundary.

    ``preflight_attacher`` remains injectable so the Web compatibility facade
    and tests can keep their existing monkeypatch points without moving the
    execution policy back into the route module.
    """
    if preflight_attacher is None:
        from execution.liquidation_preflight import attach_liquidation_preflight_state

        preflight_attacher = attach_liquidation_preflight_state
    prepared = prepare_execution_payload(payload, controls=controls)
    return preflight_attacher(
        prepared,
        prepared["execution_controls"],
        mode=mode,
    )


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
