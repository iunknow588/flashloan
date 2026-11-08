from __future__ import annotations

from copy import deepcopy
from typing import Any

from core.error_registry import ErrorCode
from dex.runtime import dex_opportunity_scanner_rust_mixin as rust_mixin
from dex.runtime import rust_closed_loop


def _result(groups: dict[str, Any]) -> dict[str, Any]:
    return deepcopy(groups)


def _mixin() -> rust_mixin.DexOpportunityNativeRustMixin:
    return rust_mixin.DexOpportunityNativeRustMixin()


def test_selected_route_keeps_broadcast_result_fields():
    mixin = _mixin()
    group_result = {
        "status": "profitable_amount_found",
        "broadcast": {"ok": True, "txHash": "0xabc"},
        "broadcastError": "",
        "executionQueued": False,
        "executionQueueAt": None,
        "selectedAmountRaw": "0x1",
        "selectedProfitRaw": "0x2",
    }
    route_result = {"status": "route_unprofitable"}

    merged = mixin._merge_group_execution_into_route_result(
        route_result=route_result,
        group_result=group_result,
        selected=True,
    )

    assert merged["broadcast"]["txHash"] == "0xabc"
    assert merged["broadcastError"] == ""
    assert merged["executionQueued"] is False
    assert merged["executionQueueAt"] is None
    assert merged["selectedProfitRaw"] == "0x2"


def test_defer_broadcast_queued_fields_pass_through_to_selected_route():
    mixin = _mixin()
    group_result = {
        "status": "profitable_amount_found",
        "deferBroadcast": True,
        "executionQueued": True,
        "executionQueueAt": "2026-10-28T00:00:00Z",
        "selectedAmountRaw": "0x1",
        "selectedProfitRaw": "0x2",
    }
    route_result = {"status": "route_unprofitable"}

    merged = mixin._merge_group_execution_into_route_result(
        route_result=route_result,
        group_result=group_result,
        selected=True,
    )

    assert merged["executionQueued"] is True
    assert merged["executionQueueAt"] == "2026-10-28T00:00:00Z"
    assert merged["deferBroadcast"] is True


def test_unselected_route_marks_broadcast_not_selected_without_missing_error():
    mixin = _mixin()
    group_result = {
        "status": "profitable_amount_found",
        "broadcast": {"ok": True, "txHash": "0xabc"},
        "selectedRoutePayload": {"routeSymbols": ["AAA", "BBB", "CCC", "AAA"]},
    }

    merged = mixin._merge_group_execution_into_route_result(
        route_result={"status": "route_unprofitable"},
        group_result=group_result,
        selected=False,
    )

    assert merged["broadcastNotSelected"] is True
    assert "broadcast" not in merged
    assert "executionQueued" not in merged


def test_native_attempt_marks_execution_queued_and_preserves_queue_time(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        rust_closed_loop,
        "_decimals_for_borrow",
        lambda chain_id, borrow_address: 6,
    )
    route = {
        "routeSymbols": ["AAA", "BBB", "CCC", "AAA"],
        "routeAddresses": [
            "0x" + ("1" * 40),
            "0x" + ("2" * 40),
            "0x" + ("3" * 40),
            "0x" + ("1" * 40),
        ],
        "tokenSymbols": ["AAA", "BBB", "CCC", "AAA"],
        "quoteStatus": "requires_live_quoter",
        "poolPathKey": "a|b|c",
        "pools": [],
    }
    result = {
        "method": "previewBestPool+previewArbitrageBestAmount+runArbitrage",
        "searchMode": "inner_single_call",
        "status": "profitable_amount_found",
        "selectedAmountRaw": "0x0f4240",
        "selectedProfitRaw": "0x0493e0",
        "initialAmountRaw": "0x0f4240",
        "deferBroadcast": True,
        "executionQueued": True,
        "executionQueueAt": "2026-10-28T00:00:00Z",
        "runArbitrage": {
            "ok": True,
            "profit": "0x493e0",
        },
        "attempts": [
            {
                "amountRaw": "0x0f4240",
                "profitRaw": "0x0493e0",
                "ok": True,
                "profitable": True,
                "routeIndex": "0x0",
                "attemptedCount": "0x1",
            }
        ],
    }

    attempt = rust_closed_loop.build_native_worker_attempt(
        route=route,
        result=result,
        chain_id=46630,
        observed_at="2026-10-28T00:00:00+00:00",
    )

    assert attempt is not None
    assert attempt["executionQueued"] is True
    assert attempt["executionQueueAt"] == "2026-10-28T00:00:00Z"
    assert attempt["auto_execute"]["queued"] is True
    assert attempt["auto_execute"]["broadcastResultMissing"] is False


def test_native_attempt_carries_structured_broadcast_error_fields(monkeypatch):
    monkeypatch.setattr(
        rust_closed_loop,
        "_decimals_for_borrow",
        lambda chain_id, borrow_address: 6,
    )
    route = {
        "routeSymbols": ["AAA", "BBB", "CCC", "AAA"],
        "routeAddresses": [
            "0x" + ("1" * 40),
            "0x" + ("2" * 40),
            "0x" + ("3" * 40),
            "0x" + ("1" * 40),
        ],
        "tokenSymbols": ["AAA", "BBB", "CCC", "AAA"],
        "quoteStatus": "requires_live_quoter",
        "poolPathKey": "a|b|c",
        "pools": [],
    }
    result = {
        "method": "previewBestPool+previewArbitrageBestAmount+runArbitrage",
        "searchMode": "inner_single_call",
        "status": "profitable_amount_found",
        "broadcastError": "nonce too low: expected 7, got 6",
        "broadcast": {},
        "selectedAmountRaw": "0x0f4240",
        "selectedProfitRaw": "0x0493e0",
        "initialAmountRaw": "0x0f4240",
        "runArbitrage": {
            "ok": True,
            "profit": "0x493e0",
        },
    }

    attempt = rust_closed_loop.build_native_worker_attempt(
        route=route,
        result=result,
        chain_id=46630,
        observed_at="2026-10-28T00:00:00+00:00",
    )

    assert attempt is not None
    assert attempt["error"] == "nonce too low: expected 7, got 6"
    assert attempt["disposition"] == "VERIFY_CHAIN_STATE"
    assert attempt["errorKey"] == "DEX.RPC.NONCE_TOO_LOW"
    assert attempt["errorCode"] == int(ErrorCode.DEX_RPC_NONCE_TOO_LOW)


def test_native_attempt_marks_submission_rejected_with_revert_evidence(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        rust_closed_loop,
        "_decimals_for_borrow",
        lambda chain_id, borrow_address: 6,
    )
    route = {
        "routeSymbols": ["AAA", "BBB", "CCC", "AAA"],
        "routeAddresses": [
            "0x" + ("1" * 40),
            "0x" + ("2" * 40),
            "0x" + ("3" * 40),
            "0x" + ("1" * 40),
        ],
        "tokenSymbols": ["AAA", "BBB", "CCC", "AAA"],
        "quoteStatus": "requires_live_quoter",
        "poolPathKey": "a|b|c",
        "pools": [],
    }
    result = {
        "method": "previewBestPool+previewArbitrageBestAmount+runArbitrage",
        "searchMode": "inner_single_call",
        "status": "profitable_amount_found",
        "broadcastError": {
            "error": "execution reverted",
            "rawError": "execution reverted: NoProfitableRoute()",
            "revertData": "0x4f422847",
            "revertSelector": "0x4f422847",
            "rpcMethod": "eth_sendRawTransaction",
            "broadcastChannel": "public_rpc",
            "relayErrors": [{"relay": "main", "error": "execution reverted"}],
            "submitted": False,
            "txHash": None,
        },
        "broadcast": {},
        "selectedAmountRaw": "0x0f4240",
        "selectedProfitRaw": "0x0493e0",
        "initialAmountRaw": "0x0f4240",
        "runArbitrage": {
            "ok": True,
            "profit": "0x493e0",
        },
    }

    attempt = rust_closed_loop.build_native_worker_attempt(
        route=route,
        result=result,
        chain_id=46630,
        observed_at="2026-10-28T00:00:00+00:00",
    )

    assert attempt is not None
    assert attempt["failureKind"] == "submission_rejected_before_acceptance"
    assert attempt["error"] == "execution reverted: NoProfitableRoute()"
    assert attempt["revertData"] == "0x4f422847"
    assert attempt["revertReasonVerifiable"] is True
    auto_execute = attempt["auto_execute"]
    assert auto_execute["reason"] == "broadcast_failed"
    assert auto_execute["submitted"] is False
    assert auto_execute["result"]["rawError"] == "execution reverted: NoProfitableRoute()"
    assert auto_execute["result"]["revertSelector"] == "0x4f422847"
    assert auto_execute["result"]["rpcMethod"] == "eth_sendRawTransaction"
    assert auto_execute["result"]["broadcastChannel"] == "public_rpc"
    assert auto_execute["result"]["relayErrors"][0]["relay"] == "main"
