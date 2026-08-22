from liquidation import (
    ExecutionTransition,
    ReceiptTransition,
    apply_submission_gate,
    apply_static_call_result,
    blocked_submission_status,
    build_flashloan_receipt_result,
    build_self_funded_receipt_result,
    prepare_execution_payload,
    receipt_transition,
    normalize_execution_target,
    select_execution_candidate,
    validate_execution_submission_target,
    submission_gate_transition,
    summarize_execution_result,
)
from page_state import ExecutionStatus
from datetime import datetime, timezone


def test_prepare_execution_payload_keeps_controls_and_summarizes_submission():
    payload = {"request": {"user": "0x1"}, "execution_controls": {"execution_enabled": True}}

    prepared = prepare_execution_payload(payload, controls={"slippage_bps": 50})
    summarized = summarize_execution_result({**prepared, "tx_hash": "0xabc"}, {"status": 1})

    assert prepared["execution_controls"]["slippage_bps"] == 50
    assert summarized["execution_summary"]["status"] == "submitted"
    assert summarized["receipt"]["status"] == 1


def test_apply_submission_gate_uses_domain_controls_and_injectable_preflight():
    calls = {}

    def attach(payload, controls, *, mode):
        calls.update(controls=controls, mode=mode)
        return {**payload, "submission_allowed": True}

    result = apply_submission_gate(
        {"request": {"user": "0x1"}},
        controls={"execution_enabled": True},
        mode="self_funded",
        preflight_attacher=attach,
    )

    assert result["submission_allowed"] is True
    assert calls == {"controls": {"execution_enabled": True}, "mode": "self_funded"}


def test_submission_gate_transition_preserves_preflight_phases_and_messages():
    cases = [
        (
            {"submission_allowed": True},
            False,
            ExecutionTransition(ExecutionStatus.READY_TO_SUBMIT, "ready_to_submit", "execution payload is ready to submit"),
        ),
        (
            {"block_level": "soft"},
            True,
            ExecutionTransition(ExecutionStatus.SOFT_BLOCKED, "soft_blocked_after_preflight", "execution payload is soft blocked after preflight"),
        ),
        (
            {"block_level": "hard"},
            False,
            ExecutionTransition(ExecutionStatus.HARD_BLOCKED, "hard_blocked", "execution payload is hard blocked"),
        ),
        (
            {},
            True,
            ExecutionTransition(ExecutionStatus.PREFLIGHTING, "preflighting", "execution payload is still preflighting"),
        ),
    ]

    for payload, after_preflight, expected in cases:
        assert submission_gate_transition(payload, after_preflight=after_preflight) == expected


def test_blocked_submission_status_defaults_to_soft_for_unknown_block_level():
    assert blocked_submission_status({"block_level": "hard"}) is ExecutionStatus.HARD_BLOCKED
    assert blocked_submission_status({"block_level": "unknown"}) is ExecutionStatus.SOFT_BLOCKED


def test_receipt_transition_maps_success_and_failure_states():
    assert receipt_transition({"status": 1}, success_message="ok", failure_message="bad") == ReceiptTransition(
        ExecutionStatus.SUCCESS,
        "confirmed_success",
        "ok",
        1,
    )
    assert receipt_transition({"status": 0}, success_message="ok", failure_message="bad") == ReceiptTransition(
        ExecutionStatus.CONFIRMED_FAILED,
        "confirmed_failed",
        "bad",
        0,
    )


def test_build_flashloan_receipt_result_preserves_preflight_and_manual_force():
    result = build_flashloan_receipt_result(
        preflight={
            "executor": "0xexec",
            "account_report": {"execution_plan": {"strategy": "flashloan"}},
            "execution_controls": {"execution_enabled": True},
        },
        preflight_info={"static_call_passed": True, "static_call_status": "passed"},
        controls={"require_static_call": True},
        request={"user": "0x1"},
        receipt_data={"status": 1},
        tx_hash="0xtx",
        broadcast={"tx_hash": "0xtx"},
        force=True,
        execution_phase="confirmed_success",
    )

    assert result["mode"] == "flashloan"
    assert result["preflight"]["static_call_required"] is True
    assert result["execution_controls"] == {"execution_enabled": True, "manual_force": True}
    assert result["execution_plan"] == {"strategy": "flashloan"}


def test_build_self_funded_receipt_result_uses_payload_controls_and_approval_receipt():
    result = build_self_funded_receipt_result(
        payload={"account_report": {"execution_plan": {"strategy": "self"}}, "execution_controls": {"execution_enabled": False}},
        controls={"execution_enabled": True},
        pool_address="0xpool",
        sender="0xsender",
        request={"user": "0x1"},
        approval_receipt={"status": 1},
        receipt_data={"status": 0},
        tx_hash="0xtx",
        approval_broadcast={"tx_hash": "0xapprove"},
        broadcast={"tx_hash": "0xtx"},
        force=False,
        execution_phase="confirmed_failed",
    )

    assert result["mode"] == "self_funded"
    assert result["pool"] == "0xpool"
    assert result["approval_receipt"] == {"status": 1}
    assert result["execution_controls"] == {"execution_enabled": False, "manual_force": False}


def test_apply_static_call_result_updates_preflight_and_archives_error():
    archived = []
    payload = apply_static_call_result(
        {"request": {"user": "0x1"}, "preflight": {"existing": True}},
        {
            "status": "error",
            "error": "execution reverted",
            "parsed": {"category": "slippage", "label": "AmountOutMin"},
            "simulated_at": "2026-08-19T00:00:00Z",
        },
        archive_failure=lambda failure_payload, parsed: archived.append((failure_payload, parsed)),
    )

    assert payload["preflight"] == {
        "existing": True,
        "static_call_required": True,
        "static_call_status": "error",
        "static_call_passed": False,
        "static_call_error": "execution reverted",
        "static_call_error_category": "slippage",
        "static_call_error_label": "AmountOutMin",
        "static_call_simulated_at": "2026-08-19T00:00:00Z",
    }
    assert archived[0][0]["preflight"]["static_call_status"] == "error"
    assert archived[0][1] == {"category": "slippage", "label": "AmountOutMin"}


def test_apply_static_call_result_does_not_archive_passed_result():
    archived = []
    payload = apply_static_call_result(
        {"preflight": {}},
        {"status": "passed", "error": None, "parsed": {}, "simulated_at": "now"},
        archive_failure=lambda failure_payload, parsed: archived.append(parsed),
    )

    assert payload["preflight"]["static_call_passed"] is True
    assert archived == []


def test_validate_execution_submission_target_blocks_market_chain_and_expired_target():
    account = "0x0000000000000000000000000000000000000001"
    report = {
        "context": {"market_id": "bnb-aave-v3", "chain_id": 56},
        "summary": {"status": "liquidatable", "health_factor": 0.95},
        "observed_at": "2026-08-18T00:00:00Z",
        "liquidation_candidates": [{"collateral_asset": "0x2", "debt_asset": "0x3", "amount_to_pass_to_liquidation_call": 1}],
    }
    _, target = select_execution_candidate(
        {**report, "account": account},
        target=normalize_execution_target(account, market_id="avalanche-aave-v3"),
    )
    payload = {
        "account": account,
        "execution_target": target,
        "account_report": report,
    }

    result = validate_execution_submission_target(
        payload,
        chain_id=43114,
        max_age_seconds=10,
        now=datetime(2026, 8, 19, 0, 0, tzinfo=timezone.utc),
    )

    assert result["ready"] is False
    assert result["blocked_reasons"] == ["market_id_mismatch", "chain_id_mismatch", "target_report_expired"]
