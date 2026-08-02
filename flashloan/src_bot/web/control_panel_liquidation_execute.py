import os
import time

from eth_account import Account
from web3 import Web3

from execution.liquidation_payload import LiquidationExecutionPayloadConfig, build_liquidation_execution_payload
from execution.liquidation_preflight import attach_liquidation_preflight_state, force_remaining_blockers
from execution.nonce_manager import NonceManager
from execution.parallel_submitter import SubmissionAttempt, run_parallel_submissions
from execution.private_tx import send_raw_transaction_private_first
from execution.receipt_formatter import format_tx_receipt
from execution.revert_parser import build_failure_record
from execution.static_call import LIQUIDATION_EXECUTOR_ABI, simulate_request_liquidation_static_call
import web.control_panel_liquidation_base as liquidation_base
from web.control_panel_liquidation_scan import liquidation_account_payload, scan_context_assets
from web.liquidation_execution_service import prepare_execution_payload, summarize_execution_result
from web.liquidation_submission_service import archive_submission_failure, build_submission_summary
from web.page_state import ExecutionStatus, PageName
from web.page_state_service import store_page_state

globals().update({name: value for name, value in vars(liquidation_base).items() if not name.startswith("_")})


_NONCE_MANAGERS: dict[tuple[int, str], NonceManager] = {}


def _nonce_manager(w3: Web3, sender: str) -> NonceManager:
    checksum_sender = Web3.to_checksum_address(sender)
    key = (int(w3.eth.chain_id), checksum_sender.lower())
    manager = _NONCE_MANAGERS.get(key)
    if manager is None:
        manager = NonceManager(w3, checksum_sender)
        manager.initialize()
        _NONCE_MANAGERS[key] = manager
    return manager


def liquidation_self_funded_private_keys() -> list[str]:
    raw = os.getenv("LIQUIDATION_SELF_FUNDED_PRIVATE_KEYS", "").strip()
    if raw:
        keys = [item.strip() for item in raw.split(",") if item.strip()]
    else:
        keys = [liquidation_self_funded_private_key()]
    return list(dict.fromkeys(key for key in keys if key))


def apply_liquidation_submission_state(payload: dict, *, mode: str = "flashloan") -> dict:
    controls = payload.get("execution_controls") or liquidation_execution_controls()
    payload["execution_controls"] = controls
    return attach_liquidation_preflight_state(payload, controls, mode=mode)


def liquidation_execution_payload_for_account(
    account: str,
    deadline_seconds: int = 300,
    allow_zero_min_out: bool = False,
    require_executor: bool = True,
    force: bool = False,
) -> dict:
    phase = "context_received"
    _record_execution_state(
        {"request": {"account": account}, "preflight": {}, "account_report": {}},
        ExecutionStatus.CONTEXT_RECEIVED,
        mode="flashloan",
        message="loading liquidation account context",
        extra={"account": account, "phase": phase},
    )
    executor_address = liquidation_executor_address()
    if require_executor and not executor_address:
        _record_execution_state(
            {"request": {"account": account}, "preflight": {}, "account_report": {}},
            ExecutionStatus.ERROR,
            mode="flashloan",
            message="missing LIQUIDATION_EXECUTOR_ADDRESS",
            extra={"account": account, "phase": phase},
        )
        raise RuntimeError("missing LIQUIDATION_EXECUTOR_ADDRESS")
    executor_address = executor_address or "0x0000000000000000000000000000000000000000"
    _record_execution_state(
        {"request": {"account": account}, "preflight": {}, "account_report": {}},
        ExecutionStatus.LOADING_ACCOUNT,
        mode="flashloan",
        message="loading liquidation account report",
        extra={"account": account, "phase": "loading_account"},
    )
    try:
        report = liquidation_account_payload(account)
    except Exception as exc:
        _record_execution_state(
            {"request": {"account": account}, "preflight": {}, "account_report": {}},
            ExecutionStatus.ERROR,
            mode="flashloan",
            message=str(exc),
            extra={"account": account, "phase": "loading_account"},
        )
        raise
    phase = "building_prediction"
    try:
        deadline = int(time.time()) + max(30, int(deadline_seconds))
        controls = liquidation_execution_controls()
        _record_execution_state(
            {"request": {"account": account}, "preflight": {}, "account_report": report},
            ExecutionStatus.BUILDING_PREDICTION,
            mode="flashloan",
            message="building liquidation prediction",
            extra={"account": account, "phase": phase},
        )
        payload = build_liquidation_execution_payload(
            report,
            executor_address=executor_address,
            router_address=dex_router_address(),
            deadline=deadline,
            config=LiquidationExecutionPayloadConfig(
                allow_zero_min_collateral_out=allow_zero_min_out,
                slippage_bps=controls["slippage_bps"],
            ),
        )
        phase = "building_quote"
        _record_execution_state(
            {"request": {"account": account}, "preflight": {}, "account_report": report},
            ExecutionStatus.BUILDING_QUOTE,
            mode="flashloan",
            message="building liquidation quote",
            extra={"account": account, "phase": phase},
        )
        candidate = payload.get("request") or {}
        if not force and controls["max_debt_to_cover"] > 0 and int(candidate.get("debtToCover") or 0) > controls["max_debt_to_cover"]:
            raise ValueError("debtToCover exceeds LIQUIDATION_MAX_DEBT_TO_COVER")
        profit_amount = int(candidate.get("minProfitAmount") or 0)
        if not force and profit_amount < controls["min_profit_base"]:
            raise ValueError("minProfitAmount is below LIQUIDATION_MIN_PROFIT_BASE")
        phase = "building_payload"
        _record_execution_state(
            {"request": {"account": account}, "preflight": {}, "account_report": report},
            ExecutionStatus.BUILDING_PAYLOAD,
            mode="flashloan",
            message="building liquidation payload",
            extra={"account": account, "phase": phase},
        )
        preflight = dict(payload.get("preflight") or {})
        preflight["static_call_required"] = bool(controls["require_static_call"])
        preflight["execution_enabled"] = bool(controls["execution_enabled"])
        preflight["static_call_status"] = "pending"
        preflight["static_call_passed"] = False
        preflight["static_call_error"] = None
        preflight["static_call_simulated_at"] = None
        payload["preflight"] = preflight
        payload["account_report"] = report
        payload["execution_controls"] = controls
        payload = apply_liquidation_submission_state(payload, mode="flashloan")
        if payload.get("submission_allowed"):
            status = ExecutionStatus.READY_TO_SUBMIT
            message = "execution payload is ready to submit"
            phase = "ready_to_submit"
        elif payload.get("block_level") == "soft":
            status = ExecutionStatus.SOFT_BLOCKED
            message = "execution payload is soft blocked"
            phase = "soft_blocked"
        elif payload.get("block_level") == "hard":
            status = ExecutionStatus.HARD_BLOCKED
            message = "execution payload is hard blocked"
            phase = "hard_blocked"
        else:
            status = ExecutionStatus.READY_FOR_PREFLIGHT
            message = "execution payload is ready for preflight"
            phase = "ready_for_preflight"
        payload["execution_phase"] = phase
        _record_execution_state(payload, status, mode="flashloan", result=payload.get("state"), message=message, extra={"account": account, "phase": phase})
        return payload
    except Exception as exc:
        _record_execution_state(
            {"request": {"account": account}, "preflight": locals().get("preflight", {}), "account_report": locals().get("report", {})},
            ExecutionStatus.ERROR,
            mode="flashloan",
            message=str(exc),
            extra={"account": account, "phase": phase},
        )
        raise


def _force_remaining_blockers(blockers: list[str]) -> list[str]:
    return force_remaining_blockers(blockers)


def _execution_state_context(payload: dict, *, mode: str, extra: dict | None = None) -> dict:
    preflight = payload.get("preflight") or {}
    account_report = payload.get("account_report") or {}
    summary = account_report.get("summary") if isinstance(account_report, dict) else {}
    context = {
        "mode": mode,
        "account": payload.get("account"),
        "request": dict(payload.get("request") or {}),
        "execution_phase": payload.get("execution_phase"),
        "phase": payload.get("execution_phase") or payload.get("phase"),
        "submission_allowed": payload.get("submission_allowed"),
        "block_level": payload.get("block_level"),
        "blocked_reasons": list(payload.get("blocked_reasons") or []),
        "force_allowed": payload.get("force_allowed"),
        "static_call_status": preflight.get("static_call_status"),
        "static_call_passed": preflight.get("static_call_passed"),
        "static_call_error": preflight.get("static_call_error"),
        "candidate_status": summary.get("status") if isinstance(summary, dict) else None,
        "candidate_health_factor": summary.get("health_factor") if isinstance(summary, dict) else None,
        "tx_hash": payload.get("tx_hash"),
        "receipt_status": (payload.get("receipt") or {}).get("status"),
    }
    if extra:
        context.update(extra)
    return {key: value for key, value in context.items() if value is not None or key in {"request", "blocked_reasons"}}


def _record_execution_state(payload: dict, status: ExecutionStatus, *, mode: str, result: str | None = None, message: str | None = None, extra: dict | None = None) -> None:
    store_page_state(
        PageName.EXECUTION,
        status.value,
        result=result,
        message=message,
        last_error=message if status in {ExecutionStatus.SOFT_BLOCKED, ExecutionStatus.HARD_BLOCKED, ExecutionStatus.CONFIRMED_FAILED, ExecutionStatus.ERROR, ExecutionStatus.SCAN_ERROR} else None,
        context=_execution_state_context(payload, mode=mode, extra=extra),
    )


LIQUIDATION_POOL_ABI = [
    {
        "inputs": [
            {"internalType": "address", "name": "collateralAsset", "type": "address"},
            {"internalType": "address", "name": "debtAsset", "type": "address"},
            {"internalType": "address", "name": "user", "type": "address"},
            {"internalType": "uint256", "name": "debtToCover", "type": "uint256"},
            {"internalType": "bool", "name": "receiveAToken", "type": "bool"},
        ],
        "name": "liquidationCall",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function",
    }
]

LIQUIDATION_ERC20_ABI = [
    {
        "inputs": [
            {"internalType": "address", "name": "spender", "type": "address"},
            {"internalType": "uint256", "name": "amount", "type": "uint256"},
        ],
        "name": "approve",
        "outputs": [{"internalType": "bool", "name": "", "type": "bool"}],
        "stateMutability": "nonpayable",
        "type": "function",
    }
]




def _archive_static_call_failure(payload: dict, parsed: dict) -> None:
    """Archive a staticCall failure to the liquidation_failure_samples table."""
    archive_submission_failure(payload, parsed=parsed)

def simulate_liquidation_static_call(payload: dict) -> dict:
    phase = "preflighting"
    _record_execution_state(
        payload,
        ExecutionStatus.PREFLIGHTING,
        mode=str(payload.get("mode") or "flashloan"),
        message="running liquidation static call",
        extra={"phase": phase},
    )
    try:
        executor_address = str(payload.get("executor") or "").strip()
        owner_address = liquidation_executor_owner_address()
        request = payload.get("request") or {}
        if not executor_address:
            raise ValueError("executor is required")
        if not owner_address:
            raise ValueError("missing LIQUIDATION_EXECUTOR_OWNER_ADDRESS")

        rpc_url, _, asset_error = scan_context_assets()
        if not rpc_url:
            raise RuntimeError(asset_error or "unable to resolve rpc_url")

        w3 = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": 10}))
        result = simulate_request_liquidation_static_call(
            w3,
            executor_address=executor_address,
            owner_address=owner_address,
            request=request,
        )
        status = result["status"]
        error = result["error"]
        parsed = result["parsed"]
        preflight = dict(payload.get("preflight") or {})
        preflight.update(
            {
                "static_call_required": True,
                "static_call_status": status,
                "static_call_passed": status == "passed",
                "static_call_error": error,
                "static_call_error_category": parsed.get("category"),
                "static_call_error_label": parsed.get("label"),
                "static_call_simulated_at": result["simulated_at"],
            }
        )
        payload["preflight"] = preflight
        if status == "error":
            _archive_static_call_failure(payload, parsed)
        payload = apply_liquidation_submission_state(payload, mode="flashloan")
        if payload.get("submission_allowed"):
            state = ExecutionStatus.READY_TO_SUBMIT
            message = "execution payload passed preflight"
            phase = "ready_to_submit"
        elif payload.get("block_level") == "soft":
            state = ExecutionStatus.SOFT_BLOCKED
            message = "execution payload is soft blocked after preflight"
            phase = "soft_blocked_after_preflight"
        elif payload.get("block_level") == "hard":
            state = ExecutionStatus.HARD_BLOCKED
            message = "execution payload is hard blocked after preflight"
            phase = "hard_blocked_after_preflight"
        else:
            state = ExecutionStatus.PREFLIGHTING
            message = "execution payload is still preflighting"
            phase = "preflighting"
        payload["execution_phase"] = phase
        _record_execution_state(payload, state, mode="flashloan", result=payload.get("state"), message=message, extra={"phase": phase})
        return payload
    except Exception as exc:
        _record_execution_state(
            payload,
            ExecutionStatus.ERROR,
            mode=str(payload.get("mode") or "flashloan"),
            message=str(exc),
            extra={"phase": phase},
        )
        raise


def execute_flashloan_liquidation_transaction(payload: dict, force: bool = False) -> dict:
    controls = liquidation_execution_controls()
    payload["execution_controls"] = controls
    gated_payload = apply_liquidation_submission_state(dict(payload), mode="flashloan")
    initial_blockers = [
        reason
        for reason in gated_payload.get("blocked_reasons", [])
        if reason not in {"static_call_required", "static_call_failed"}
    ]
    if force:
        initial_blockers = _force_remaining_blockers(initial_blockers)
    if initial_blockers:
        _record_execution_state(
            gated_payload,
            ExecutionStatus.HARD_BLOCKED if gated_payload.get("block_level") == "hard" else ExecutionStatus.SOFT_BLOCKED,
            mode="flashloan",
            result=gated_payload.get("state"),
            message=f"submission blocked: {', '.join(initial_blockers)}",
        )
        raise RuntimeError(f"submission blocked: {', '.join(initial_blockers)}")
    if not controls["execution_enabled"] and not force:
        _record_execution_state(
            gated_payload,
            ExecutionStatus.HARD_BLOCKED,
            mode="flashloan",
            result=gated_payload.get("state"),
            message="LIQUIDATION_EXECUTION_ENABLED is false",
        )
        raise RuntimeError("LIQUIDATION_EXECUTION_ENABLED is false")

    private_key = liquidation_executor_private_key()
    if not private_key:
        raise RuntimeError("missing LIQUIDATION_EXECUTION_PRIVATE_KEY")

    owner_address = liquidation_executor_owner_address()
    if not owner_address:
        raise RuntimeError("missing LIQUIDATION_EXECUTOR_OWNER_ADDRESS")

    rpc_url, _, asset_error = scan_context_assets()
    if not rpc_url:
        raise RuntimeError(asset_error or "unable to resolve rpc_url")

    w3 = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": 20}))
    chain_health = liquidation_config_health(chain_id=int(w3.eth.chain_id))
    if not chain_health.get("valid"):
        raise RuntimeError(f"submission blocked: {', '.join(chain_health.get('errors') or ['invalid chain config'])}")
    account = Account.from_key(private_key)
    checksum_sender = Web3.to_checksum_address(account.address)
    if checksum_sender.lower() != Web3.to_checksum_address(owner_address).lower():
        raise RuntimeError("execution private key does not match LIQUIDATION_EXECUTOR_OWNER_ADDRESS")

    preflight = simulate_liquidation_static_call(dict(payload))
    preflight_info = preflight.get("preflight") or {}
    preflight = apply_liquidation_submission_state(preflight, mode="flashloan")
    blockers = preflight.get("blocked_reasons", [])
    if force:
        blockers = _force_remaining_blockers(blockers)
    if blockers:
        _record_execution_state(
            preflight,
            ExecutionStatus.HARD_BLOCKED if preflight.get("block_level") == "hard" else ExecutionStatus.SOFT_BLOCKED,
            mode="flashloan",
            result=preflight.get("state"),
            message=f"submission blocked: {', '.join(blockers)}",
        )
        raise RuntimeError(f"submission blocked: {', '.join(blockers)}")
    if controls["require_static_call"] and not preflight_info.get("static_call_passed") and not force:
        _record_execution_state(
            preflight,
            ExecutionStatus.SOFT_BLOCKED,
            mode="flashloan",
            result=preflight.get("state"),
            message=preflight_info.get("static_call_error") or "static call preflight failed",
        )
        raise RuntimeError(preflight_info.get("static_call_error") or "static call preflight failed")

    request = preflight.get("request") or {}
    executor_address = Web3.to_checksum_address(str(preflight.get("executor") or ""))
    contract = w3.eth.contract(address=executor_address, abi=LIQUIDATION_EXECUTOR_ABI)
    tx_builder = contract.functions.requestLiquidation(
        (
            Web3.to_checksum_address(str(request.get("user") or "")),
            Web3.to_checksum_address(str(request.get("collateralAsset") or "")),
            Web3.to_checksum_address(str(request.get("debtAsset") or "")),
            int(request.get("debtToCover") or 0),
            int(request.get("minCollateralSwapOut") or 0),
            int(request.get("minProfitAmount") or 0),
            int(request.get("deadline") or 0),
            int(request.get("gasLimit") or 0),
            [Web3.to_checksum_address(str(item)) for item in (request.get("swapPath") or []) if str(item).strip()],
        )
    )
    nonce_manager = _nonce_manager(w3, checksum_sender)
    nonce = nonce_manager.acquire()
    _record_execution_state(
        preflight,
        ExecutionStatus.SUBMITTING_FORCE if force else ExecutionStatus.SUBMITTING,
        mode="flashloan",
        result=preflight.get("state"),
        message="broadcasting liquidation transaction",
        extra={"phase": "submitting"},
    )
    tx_params = {
        "from": checksum_sender,
        "nonce": nonce,
        "chainId": int(w3.eth.chain_id),
    }
    try:
        estimated_gas = tx_builder.estimate_gas({"from": checksum_sender})
        tx_params["gas"] = max(350000, int(estimated_gas * 12 // 10))
    except Exception:
        tx_params["gas"] = 900000

    latest_block = w3.eth.get_block("latest")
    base_fee = int(getattr(latest_block, "baseFeePerGas", 0) or 0)
    priority_fee = w3.to_wei(max(0.0, float(controls["priority_fee_gwei"])), "gwei")
    if base_fee > 0:
        tx_params["maxPriorityFeePerGas"] = int(priority_fee)
        tx_params["maxFeePerGas"] = int(base_fee * 2 + priority_fee)
    else:
        tx_params["gasPrice"] = int(w3.eth.gas_price)

    try:
        built_tx = tx_builder.build_transaction(tx_params)
        signed = w3.eth.account.sign_transaction(built_tx, private_key=private_key)
        raw_tx = getattr(signed, "raw_transaction", None) or getattr(signed, "rawTransaction")
        broadcast = send_raw_transaction_private_first(raw_tx, public_w3=w3)
        tx_hash = broadcast["tx_hash"]
    except Exception:
        nonce_manager.release(nonce)
        raise
    timeout_seconds = max(30, int(controls["tx_timeout_seconds"]))
    execution_phase = "waiting_receipt"
    _record_execution_state(
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
        _record_execution_state(
            {**preflight, "tx_hash": tx_hash},
            ExecutionStatus.ERROR,
            mode="flashloan",
            result=preflight.get("state"),
            message=str(exc),
            extra={"tx_hash": tx_hash, "phase": execution_phase},
        )
        raise
    receipt_data = format_tx_receipt(receipt)
    account_report = preflight.get("account_report") or {}
    receipt_status = int(receipt_data.get("status") or 0)
    execution_phase = "confirmed_success" if receipt_status == 1 else "confirmed_failed"
    _record_execution_state(
        {**preflight, "tx_hash": tx_hash, "receipt": receipt_data},
        ExecutionStatus.SUCCESS if receipt_status == 1 else ExecutionStatus.CONFIRMED_FAILED,
        mode="flashloan",
        result=preflight.get("state"),
        message="liquidation receipt confirmed" if receipt_status == 1 else "liquidation receipt failed",
        extra={"tx_hash": tx_hash, "phase": execution_phase},
    )
    return summarize_execution_result(
        {
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
            "execution_plan": account_report.get("execution_plan") if isinstance(account_report, dict) else None,
            "execution_controls": (preflight.get("execution_controls") or controls) | {"manual_force": bool(force)},
            "execution_phase": execution_phase,
        },
        receipt_data,
    )


def _execute_self_funded_liquidation_for_key(payload: dict, private_key: str, force: bool = False) -> dict:
    controls = liquidation_execution_controls()
    payload["execution_controls"] = controls
    gated_payload = apply_liquidation_submission_state(dict(payload), mode="self_funded")
    blockers = gated_payload.get("blocked_reasons", [])
    if force:
        blockers = _force_remaining_blockers(blockers)
    if blockers:
        _record_execution_state(
            gated_payload,
            ExecutionStatus.HARD_BLOCKED if gated_payload.get("block_level") == "hard" else ExecutionStatus.SOFT_BLOCKED,
            mode="self_funded",
            result=gated_payload.get("state"),
            message=f"submission blocked: {', '.join(blockers)}",
        )
        raise RuntimeError(f"submission blocked: {', '.join(blockers)}")
    if not controls["execution_enabled"] and not force:
        _record_execution_state(
            gated_payload,
            ExecutionStatus.HARD_BLOCKED,
            mode="self_funded",
            result=gated_payload.get("state"),
            message="LIQUIDATION_EXECUTION_ENABLED is false",
        )
        raise RuntimeError("LIQUIDATION_EXECUTION_ENABLED is false")

    if not private_key:
        raise RuntimeError("missing LIQUIDATION_SELF_FUNDED_PRIVATE_KEY")

    rpc_url, _, asset_error = scan_context_assets()
    if not rpc_url:
        raise RuntimeError(asset_error or "unable to resolve rpc_url")
    pool_address = os.getenv("AAVE_POOL_ADDRESS", "").strip()
    if not pool_address:
        raise RuntimeError("missing AAVE_POOL_ADDRESS")

    request = dict(payload.get("request") or {})
    account = Account.from_key(private_key)
    sender = Web3.to_checksum_address(account.address)
    pool_address = Web3.to_checksum_address(pool_address)
    collateral_asset = Web3.to_checksum_address(str(request.get("collateralAsset") or ""))
    debt_asset = Web3.to_checksum_address(str(request.get("debtAsset") or ""))
    user = Web3.to_checksum_address(str(request.get("user") or payload.get("account") or ""))
    debt_to_cover = int(request.get("debtToCover") or 0)
    if debt_to_cover <= 0:
        raise ValueError("debtToCover must be greater than zero")

    w3 = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": 20}))
    chain_health = liquidation_config_health(chain_id=int(w3.eth.chain_id))
    if not chain_health.get("valid"):
        raise RuntimeError(f"submission blocked: {', '.join(chain_health.get('errors') or ['invalid chain config'])}")
    debt_token = w3.eth.contract(address=debt_asset, abi=LIQUIDATION_ERC20_ABI)
    pool = w3.eth.contract(address=pool_address, abi=LIQUIDATION_POOL_ABI)
    nonce_manager = _nonce_manager(w3, sender)
    nonce = nonce_manager.acquire()
    approval_builder = debt_token.functions.approve(pool_address, debt_to_cover)
    approval_params = {"from": sender, "nonce": nonce, "chainId": int(w3.eth.chain_id)}
    try:
        approval_gas = approval_builder.estimate_gas({"from": sender})
        approval_params["gas"] = max(60000, int(approval_gas * 12 // 10))
    except Exception:
        approval_params["gas"] = 100000
    latest_block = w3.eth.get_block("latest")
    base_fee = int(getattr(latest_block, "baseFeePerGas", 0) or 0)
    priority_fee = w3.to_wei(max(0.0, float(controls["priority_fee_gwei"])), "gwei")
    if base_fee > 0:
        approval_params["maxPriorityFeePerGas"] = int(priority_fee)
        approval_params["maxFeePerGas"] = int(base_fee * 2 + priority_fee)
    else:
        approval_params["gasPrice"] = int(w3.eth.gas_price)
    try:
        approval_tx = approval_builder.build_transaction(approval_params)
        signed_approval = w3.eth.account.sign_transaction(approval_tx, private_key=private_key)
        raw_approval = getattr(signed_approval, "raw_transaction", None) or getattr(signed_approval, "rawTransaction")
        approval_broadcast = send_raw_transaction_private_first(raw_approval, public_w3=w3)
        approval_hash = approval_broadcast["tx_hash"]
    except Exception:
        nonce_manager.release(nonce)
        raise
    timeout_seconds = max(30, int(controls["tx_timeout_seconds"]))
    approval_receipt = w3.eth.wait_for_transaction_receipt(approval_hash, timeout=timeout_seconds)
    approval_data = format_tx_receipt(approval_receipt)

    liquidation_builder = pool.functions.liquidationCall(
        collateral_asset,
        debt_asset,
        user,
        debt_to_cover,
        False,
    )
    liquidation_nonce = nonce_manager.acquire()
    tx_params = {"from": sender, "nonce": liquidation_nonce, "chainId": int(w3.eth.chain_id)}
    try:
        estimated_gas = liquidation_builder.estimate_gas({"from": sender})
        tx_params["gas"] = max(300000, int(estimated_gas * 12 // 10))
    except Exception:
        tx_params["gas"] = 700000
    if base_fee > 0:
        tx_params["maxPriorityFeePerGas"] = int(priority_fee)
        tx_params["maxFeePerGas"] = int(base_fee * 2 + priority_fee)
    else:
        tx_params["gasPrice"] = int(w3.eth.gas_price)
    try:
        liquidation_tx = liquidation_builder.build_transaction(tx_params)
        signed_liquidation = w3.eth.account.sign_transaction(liquidation_tx, private_key=private_key)
        raw_liquidation = getattr(signed_liquidation, "raw_transaction", None) or getattr(signed_liquidation, "rawTransaction")
        broadcast = send_raw_transaction_private_first(raw_liquidation, public_w3=w3)
        tx_hash = broadcast["tx_hash"]
    except Exception:
        nonce_manager.release(liquidation_nonce)
        raise
    _record_execution_state(
        {**payload, "approval_receipt": approval_data, "tx_hash": tx_hash},
        ExecutionStatus.WAITING_RECEIPT,
        mode="self_funded",
        result=gated_payload.get("state"),
        message="waiting for self-funded liquidation receipt",
        extra={"phase": "waiting_receipt"},
    )
    execution_phase = "waiting_receipt"
    try:
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=timeout_seconds)
    except Exception as exc:
        _record_execution_state(
            {**payload, "approval_receipt": approval_data, "tx_hash": tx_hash},
            ExecutionStatus.ERROR,
            mode="self_funded",
            result=payload.get("state"),
            message=str(exc),
            extra={"phase": execution_phase},
        )
        raise
    account_report = payload.get("account_report") or {}
    receipt_data = format_tx_receipt(receipt)
    receipt_status = int(receipt.status) if getattr(receipt, "status", None) is not None else int(receipt_data.get("status") or 0)
    execution_phase = "confirmed_success" if receipt_status == 1 else "confirmed_failed"
    _record_execution_state(
        {**payload, "tx_hash": tx_hash, "receipt": receipt_data},
        ExecutionStatus.SUCCESS if receipt_status == 1 else ExecutionStatus.CONFIRMED_FAILED,
        mode="self_funded",
        result=payload.get("state"),
        message="self-funded liquidation receipt confirmed" if receipt_status == 1 else "self-funded liquidation receipt failed",
        extra={"phase": execution_phase},
    )
    return summarize_execution_result(
        {
            "mode": "self_funded",
            "pool": pool_address,
            "sender": sender,
            "request": request,
            "approval_receipt": approval_data,
            "receipt": receipt_data,
            "tx_hash": tx_hash,
            "approval_broadcast": approval_broadcast,
            "broadcast": broadcast,
            "account_report": account_report,
            "execution_plan": account_report.get("execution_plan") if isinstance(account_report, dict) else None,
            "execution_controls": (payload.get("execution_controls") or controls) | {"manual_force": bool(force)},
            "execution_phase": execution_phase,
        },
        receipt_data,
    )


def execute_self_funded_liquidation_transaction(payload: dict, force: bool = False) -> dict:
    private_keys = liquidation_self_funded_private_keys()
    if not private_keys:
        raise RuntimeError("missing LIQUIDATION_SELF_FUNDED_PRIVATE_KEY")
    if len(private_keys) == 1:
        return _execute_self_funded_liquidation_for_key(payload, private_keys[0], force=force)
    attempts = [
        SubmissionAttempt(
            name=f"self_funded_{index + 1}",
            submit=lambda key=key: _execute_self_funded_liquidation_for_key(dict(payload), key, force=force),
        )
        for index, key in enumerate(private_keys)
    ]
    return run_parallel_submissions(attempts, max_workers=min(3, len(attempts)))


