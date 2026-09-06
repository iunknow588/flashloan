from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from bot.execution.parallel_submitter import SubmissionAttempt, run_parallel_submissions
from bot.execution.receipt_formatter import format_tx_receipt
from bot.execution.static_call import LIQUIDATION_EXECUTOR_ABI
from .execution_broadcast import build_transaction_params, sign_and_broadcast_transaction
from .execution_receipt import (
    build_flashloan_receipt_result,
    build_self_funded_receipt_result,
    receipt_transition,
)
from .execution_service import (
    blocked_submission_status,
    summarize_execution_result,
)
from .execution_state import (
    force_remaining_blockers,
    raise_execution_error,
    record_execution_error,
    record_execution_state,
)
from .execution_submission_gate import validate_execution_submission_target
from bot.page_state import ExecutionStatus


@dataclass(frozen=True)
class LiquidationExecutionRuntime:
    """Runtime adapters owned by the Web compatibility facade."""

    web3: Any
    account: Any
    liquidation_pool_abi: list[dict[str, Any]]
    liquidation_erc20_abi: list[dict[str, Any]]
    execution_controls: Any
    apply_submission_state: Any
    simulate_static_call: Any
    simulate_fork_transaction: Any
    scan_context_assets: Any
    liquidation_config_health: Any
    executor_private_key: Any
    executor_owner_address: Any
    aave_pool_address: Any
    nonce_manager: Any
    send_raw_transaction: Any
    redact_error: Any
    self_funded_private_keys: Any


def execute_flashloan_liquidation(
    payload: dict[str, Any],
    *,
    runtime: LiquidationExecutionRuntime,
    force: bool = False,
) -> dict[str, Any]:
    controls = runtime.execution_controls()
    payload["execution_controls"] = controls
    gated_payload = runtime.apply_submission_state(dict(payload), mode="flashloan")
    payload.update(gated_payload)
    initial_blockers = [
        reason
        for reason in gated_payload.get("blocked_reasons", [])
        if reason not in {"static_call_required", "static_call_failed", "fork_simulation_required"}
    ]
    if force:
        initial_blockers = force_remaining_blockers(initial_blockers)
    if initial_blockers:
        record_execution_state(
            gated_payload,
            blocked_submission_status(gated_payload),
            mode="flashloan",
            result=gated_payload.get("state"),
            message=f"submission blocked: {', '.join(initial_blockers)}",
        )
        raise RuntimeError(f"submission blocked: {', '.join(initial_blockers)}")
    if not controls["execution_enabled"] and not force:
        record_execution_state(
            gated_payload,
            ExecutionStatus.HARD_BLOCKED,
            mode="flashloan",
            result=gated_payload.get("state"),
            message="LIQUIDATION_EXECUTION_ENABLED is false",
        )
        raise RuntimeError("LIQUIDATION_EXECUTION_ENABLED is false")

    private_key = runtime.executor_private_key()
    if not private_key:
        raise_execution_error(
            payload,
            mode="flashloan",
            message="missing LIQUIDATION_EXECUTION_PRIVATE_KEY",
            phase="missing_private_key",
        )

    owner_address = runtime.executor_owner_address()
    if not owner_address:
        raise_execution_error(
            payload,
            mode="flashloan",
            message="missing LIQUIDATION_EXECUTOR_OWNER_ADDRESS",
            phase="missing_owner",
        )

    rpc_url, _, asset_error = runtime.scan_context_assets()
    if not rpc_url:
        raise_execution_error(
            payload,
            mode="flashloan",
            message=asset_error or "unable to resolve rpc_url",
            phase="missing_rpc",
        )

    w3 = runtime.web3(runtime.web3.HTTPProvider(rpc_url, request_kwargs={"timeout": 20}))
    chain_health = runtime.liquidation_config_health(chain_id=int(w3.eth.chain_id))
    if not chain_health.get("valid"):
        raise_execution_error(
            payload,
            mode="flashloan",
            message=f"submission blocked: {', '.join(chain_health.get('errors') or ['invalid chain config'])}",
            phase="config_blocked",
        )
    account = runtime.account.from_key(private_key)
    checksum_sender = runtime.web3.to_checksum_address(account.address)
    if checksum_sender.lower() != runtime.web3.to_checksum_address(owner_address).lower():
        raise_execution_error(
            payload,
            mode="flashloan",
            message="execution private key does not match LIQUIDATION_EXECUTOR_OWNER_ADDRESS",
            phase="private_key_mismatch",
        )

    preflight = runtime.simulate_static_call(dict(payload))
    preflight_info = preflight.get("preflight") or {}
    preflight = runtime.apply_submission_state(preflight, mode="flashloan")
    payload.update(preflight)
    blockers = preflight.get("blocked_reasons", [])
    if controls.get("require_fork_simulation") and preflight_info.get("static_call_passed"):
        try:
            preflight = runtime.simulate_fork_transaction(preflight)
        except Exception:
            preflight = runtime.apply_submission_state(preflight, mode="flashloan")
            payload.update(preflight)
            blockers = preflight.get("blocked_reasons", [])
        else:
            preflight_info = preflight.get("preflight") or {}
            preflight = runtime.apply_submission_state(preflight, mode="flashloan")
            payload.update(preflight)
            blockers = preflight.get("blocked_reasons", [])
    if force:
        blockers = force_remaining_blockers(blockers)
    if blockers:
        record_execution_state(
            preflight,
            blocked_submission_status(preflight),
            mode="flashloan",
            result=preflight.get("state"),
            message=f"submission blocked: {', '.join(blockers)}",
        )
        raise RuntimeError(f"submission blocked: {', '.join(blockers)}")
    if controls["require_static_call"] and not preflight_info.get("static_call_passed") and not force:
        record_execution_state(
            preflight,
            ExecutionStatus.SOFT_BLOCKED,
            mode="flashloan",
            result=preflight.get("state"),
            message=preflight_info.get("static_call_error") or "static call preflight failed",
        )
        raise RuntimeError(preflight_info.get("static_call_error") or "static call preflight failed")

    target_validation = validate_execution_submission_target(
        preflight,
        chain_id=int(w3.eth.chain_id),
        max_age_seconds=max(30, int(controls["tx_timeout_seconds"])),
    )
    if not target_validation["ready"]:
        message = f"submission blocked: {', '.join(target_validation['blocked_reasons'])}"
        record_execution_state(
            preflight,
            ExecutionStatus.HARD_BLOCKED,
            mode="flashloan",
            result=preflight.get("state"),
            message=message,
            extra={"phase": "target_validation", "blocked_reasons": target_validation["blocked_reasons"]},
        )
        raise RuntimeError(message)

    request = preflight.get("request") or {}
    executor_address = runtime.web3.to_checksum_address(str(preflight.get("executor") or ""))
    contract = w3.eth.contract(address=executor_address, abi=LIQUIDATION_EXECUTOR_ABI)
    tx_builder = contract.functions.requestLiquidation(
        (
            runtime.web3.to_checksum_address(str(request.get("user") or "")),
            runtime.web3.to_checksum_address(str(request.get("collateralAsset") or "")),
            runtime.web3.to_checksum_address(str(request.get("debtAsset") or "")),
            int(request.get("debtToCover") or 0),
            int(request.get("minCollateralSwapOut") or 0),
            int(request.get("minProfitAmount") or 0),
            int(request.get("deadline") or 0),
            int(request.get("gasLimit") or 0),
            [
                runtime.web3.to_checksum_address(str(item))
                for item in (request.get("swapPath") or [])
                if str(item).strip()
            ],
        )
    )
    nonce_manager = runtime.nonce_manager(w3, checksum_sender)
    nonce = nonce_manager.acquire()
    record_execution_state(
        preflight,
        ExecutionStatus.SUBMITTING_FORCE if force else ExecutionStatus.SUBMITTING,
        mode="flashloan",
        result=preflight.get("state"),
        message="broadcasting liquidation transaction",
        extra={"phase": "submitting"},
    )
    tx_params = build_transaction_params(
        w3,
        tx_builder,
        sender=checksum_sender,
        nonce=nonce,
        controls=controls,
        gas_floor=350000,
        gas_fallback=900000,
    )

    try:
        broadcast = sign_and_broadcast_transaction(
            w3,
            tx_builder,
            tx_params,
            private_key=private_key,
            send_raw_transaction=runtime.send_raw_transaction,
        )
        tx_hash = broadcast["tx_hash"]
    except Exception as exc:
        nonce_manager.release(nonce)
        record_execution_error(
            payload,
            mode="flashloan",
            message=runtime.redact_error(exc),
            phase="submitting",
            result=preflight.get("state"),
        )
        raise
    timeout_seconds = max(30, int(controls["tx_timeout_seconds"]))
    execution_phase = "waiting_receipt"
    payload.update({**preflight, "tx_hash": tx_hash, "execution_phase": execution_phase})
    record_execution_state(
        preflight,
        ExecutionStatus.WAITING_RECEIPT,
        mode="flashloan",
        result=preflight.get("state"),
        message="waiting for liquidation receipt",
        extra={"tx_hash": tx_hash, "phase": execution_phase},
    )
    try:
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=timeout_seconds)
    except Exception as exc:
        record_execution_error(
            payload,
            mode="flashloan",
            message=runtime.redact_error(exc),
            phase=execution_phase,
            result=preflight.get("state"),
            tx_hash=tx_hash,
        )
        raise
    receipt_data = format_tx_receipt(receipt)
    transition = receipt_transition(
        receipt_data,
        success_message="liquidation receipt confirmed",
        failure_message="liquidation receipt failed",
    )
    record_execution_state(
        {**preflight, "tx_hash": tx_hash, "receipt": receipt_data},
        transition.status,
        mode="flashloan",
        result=preflight.get("state"),
        message=transition.message,
        extra={"tx_hash": tx_hash, "phase": transition.phase},
    )
    return summarize_execution_result(
        build_flashloan_receipt_result(
            preflight=preflight,
            preflight_info=preflight_info,
            controls=controls,
            request=request,
            receipt_data=receipt_data,
            tx_hash=tx_hash,
            broadcast=broadcast,
            force=force,
            execution_phase=transition.phase,
        ),
        receipt_data,
    )


def execute_self_funded_liquidation_for_key(
    payload: dict[str, Any],
    private_key: str,
    *,
    runtime: LiquidationExecutionRuntime,
    force: bool = False,
) -> dict[str, Any]:
    controls = runtime.execution_controls()
    payload["execution_controls"] = controls
    gated_payload = runtime.apply_submission_state(dict(payload), mode="self_funded")
    payload.update(gated_payload)
    blockers = gated_payload.get("blocked_reasons", [])
    if force:
        blockers = force_remaining_blockers(blockers)
    if blockers:
        record_execution_state(
            gated_payload,
            blocked_submission_status(gated_payload),
            mode="self_funded",
            result=gated_payload.get("state"),
            message=f"submission blocked: {', '.join(blockers)}",
        )
        raise RuntimeError(f"submission blocked: {', '.join(blockers)}")
    if not controls["execution_enabled"] and not force:
        record_execution_state(
            gated_payload,
            ExecutionStatus.HARD_BLOCKED,
            mode="self_funded",
            result=gated_payload.get("state"),
            message="LIQUIDATION_EXECUTION_ENABLED is false",
        )
        raise RuntimeError("LIQUIDATION_EXECUTION_ENABLED is false")

    if not private_key:
        raise_execution_error(
            payload,
            mode="self_funded",
            message="missing LIQUIDATION_SELF_FUNDED_PRIVATE_KEY",
            phase="missing_private_key",
        )

    rpc_url, _, asset_error = runtime.scan_context_assets()
    if not rpc_url:
        raise_execution_error(
            payload,
            mode="self_funded",
            message=asset_error or "unable to resolve rpc_url",
            phase="missing_rpc",
        )
    pool_address = runtime.aave_pool_address()
    if not pool_address:
        raise_execution_error(
            payload,
            mode="self_funded",
            message="missing AAVE_POOL_ADDRESS",
            phase="missing_pool",
        )

    request = dict(payload.get("request") or {})
    account = runtime.account.from_key(private_key)
    sender = runtime.web3.to_checksum_address(account.address)
    pool_address = runtime.web3.to_checksum_address(pool_address)
    collateral_asset = runtime.web3.to_checksum_address(str(request.get("collateralAsset") or ""))
    debt_asset = runtime.web3.to_checksum_address(str(request.get("debtAsset") or ""))
    user = runtime.web3.to_checksum_address(str(request.get("user") or payload.get("account") or ""))
    debt_to_cover = int(request.get("debtToCover") or 0)
    if debt_to_cover <= 0:
        record_execution_error(
            payload,
            mode="self_funded",
            message="debtToCover must be greater than zero",
            phase="invalid_request",
        )
        raise ValueError("debtToCover must be greater than zero")

    w3 = runtime.web3(runtime.web3.HTTPProvider(rpc_url, request_kwargs={"timeout": 20}))
    chain_health = runtime.liquidation_config_health(chain_id=int(w3.eth.chain_id))
    if not chain_health.get("valid"):
        raise_execution_error(
            payload,
            mode="self_funded",
            message=f"submission blocked: {', '.join(chain_health.get('errors') or ['invalid chain config'])}",
            phase="config_blocked",
        )
    target_validation = validate_execution_submission_target(
        payload,
        chain_id=int(w3.eth.chain_id),
        max_age_seconds=max(30, int(controls["tx_timeout_seconds"])),
    )
    if not target_validation["ready"]:
        message = f"submission blocked: {', '.join(target_validation['blocked_reasons'])}"
        record_execution_state(
            payload,
            ExecutionStatus.HARD_BLOCKED,
            mode="self_funded",
            result=gated_payload.get("state"),
            message=message,
            extra={"phase": "target_validation", "blocked_reasons": target_validation["blocked_reasons"]},
        )
        raise RuntimeError(message)
    debt_token = w3.eth.contract(address=debt_asset, abi=runtime.liquidation_erc20_abi)
    pool = w3.eth.contract(address=pool_address, abi=runtime.liquidation_pool_abi)
    nonce_manager = runtime.nonce_manager(w3, sender)
    nonce = nonce_manager.acquire()
    approval_builder = debt_token.functions.approve(pool_address, debt_to_cover)
    approval_params = build_transaction_params(
        w3,
        approval_builder,
        sender=sender,
        nonce=nonce,
        controls=controls,
        gas_floor=60000,
        gas_fallback=100000,
    )
    try:
        approval_broadcast = sign_and_broadcast_transaction(
            w3,
            approval_builder,
            approval_params,
            private_key=private_key,
            send_raw_transaction=runtime.send_raw_transaction,
        )
        approval_hash = approval_broadcast["tx_hash"]
    except Exception as exc:
        nonce_manager.release(nonce)
        record_execution_error(
            payload,
            mode="self_funded",
            message=runtime.redact_error(exc),
            phase="submitting_approval",
            result=gated_payload.get("state"),
        )
        raise
    timeout_seconds = max(30, int(controls["tx_timeout_seconds"]))
    try:
        approval_receipt = w3.eth.wait_for_transaction_receipt(approval_hash, timeout=timeout_seconds)
    except Exception as exc:
        record_execution_error(
            payload,
            mode="self_funded",
            message=runtime.redact_error(exc),
            phase="waiting_approval_receipt",
            result=gated_payload.get("state"),
            tx_hash=approval_hash,
        )
        raise
    approval_data = format_tx_receipt(approval_receipt)
    if int(approval_data.get("status") or 0) != 1:
        payload.update(
            {
                "approval_receipt": approval_data,
                "tx_hash": approval_hash,
                "execution_phase": "approval_failed",
            }
        )
        record_execution_error(
            payload,
            mode="self_funded",
            message="approval transaction failed",
            phase="approval_failed",
            result=gated_payload.get("state"),
            tx_hash=approval_hash,
        )
        raise RuntimeError("approval transaction failed")

    liquidation_builder = pool.functions.liquidationCall(
        collateral_asset,
        debt_asset,
        user,
        debt_to_cover,
        False,
    )
    liquidation_nonce = nonce_manager.acquire()
    tx_params = build_transaction_params(
        w3,
        liquidation_builder,
        sender=sender,
        nonce=liquidation_nonce,
        controls=controls,
        gas_floor=300000,
        gas_fallback=700000,
    )
    try:
        broadcast = sign_and_broadcast_transaction(
            w3,
            liquidation_builder,
            tx_params,
            private_key=private_key,
            send_raw_transaction=runtime.send_raw_transaction,
        )
        tx_hash = broadcast["tx_hash"]
    except Exception as exc:
        nonce_manager.release(liquidation_nonce)
        record_execution_error(
            payload,
            mode="self_funded",
            message=runtime.redact_error(exc),
            phase="submitting",
            result=gated_payload.get("state"),
        )
        raise
    record_execution_state(
        {**payload, "approval_receipt": approval_data, "tx_hash": tx_hash},
        ExecutionStatus.WAITING_RECEIPT,
        mode="self_funded",
        result=gated_payload.get("state"),
        message="waiting for self-funded liquidation receipt",
        extra={"phase": "waiting_receipt"},
    )
    execution_phase = "waiting_receipt"
    payload.update({"tx_hash": tx_hash, "execution_phase": execution_phase})
    try:
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=timeout_seconds)
    except Exception as exc:
        record_execution_error(
            payload,
            mode="self_funded",
            message=runtime.redact_error(exc),
            phase=execution_phase,
            result=payload.get("state"),
            tx_hash=tx_hash,
        )
        raise
    receipt_data = format_tx_receipt(receipt)
    transition = receipt_transition(
        receipt_data,
        success_message="self-funded liquidation receipt confirmed",
        failure_message="self-funded liquidation receipt failed",
    )
    record_execution_state(
        {**payload, "tx_hash": tx_hash, "receipt": receipt_data},
        transition.status,
        mode="self_funded",
        result=payload.get("state"),
        message=transition.message,
        extra={"phase": transition.phase},
    )
    return summarize_execution_result(
        build_self_funded_receipt_result(
            payload=payload,
            controls=controls,
            pool_address=pool_address,
            sender=sender,
            request=request,
            approval_receipt=approval_data,
            receipt_data=receipt_data,
            tx_hash=tx_hash,
            approval_broadcast=approval_broadcast,
            broadcast=broadcast,
            force=force,
            execution_phase=transition.phase,
        ),
        receipt_data,
    )


def execute_self_funded_liquidation(
    payload: dict[str, Any],
    *,
    runtime: LiquidationExecutionRuntime,
    force: bool = False,
) -> dict[str, Any]:
    private_keys = runtime.self_funded_private_keys()
    if not private_keys:
        raise_execution_error(
            payload,
            mode="self_funded",
            message="missing LIQUIDATION_SELF_FUNDED_PRIVATE_KEY",
            phase="missing_private_key",
        )
    if len(private_keys) == 1:
        return execute_self_funded_liquidation_for_key(
            payload,
            private_keys[0],
            runtime=runtime,
            force=force,
        )
    attempts = [
        SubmissionAttempt(
            name=f"self_funded_{index + 1}",
            submit=lambda key=key: execute_self_funded_liquidation_for_key(
                dict(payload),
                key,
                runtime=runtime,
                force=force,
            ),
        )
        for index, key in enumerate(private_keys)
    ]
    return run_parallel_submissions(attempts, max_workers=min(3, len(attempts)))
