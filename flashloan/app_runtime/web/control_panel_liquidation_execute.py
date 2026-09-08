import os
import shutil
import subprocess
import time
from pathlib import Path

from eth_account import Account
from web3 import Web3

from core.sensitive_data import redact_sensitive_text
from core.contract_project import resolve_contract_project_from_env
from bot.execution.liquidation_payload import LiquidationExecutionPayloadConfig, build_liquidation_execution_payload
from bot.execution.liquidation_preflight import attach_liquidation_preflight_state
from bot.execution.nonce_manager import NonceManager
from bot.execution.private_tx import send_raw_transaction_private_first
from bot.execution.static_call import LIQUIDATION_EXECUTOR_ABI, simulate_request_liquidation_static_call
from bot.liquidation.execution_state import (
    record_execution_state as _record_execution_state,
)
from bot.liquidation.execution_orchestrator import (
    LiquidationExecutionRuntime,
    execute_flashloan_liquidation,
    execute_self_funded_liquidation,
    execute_self_funded_liquidation_for_key,
)
import web.control_panel_liquidation_base as liquidation_base
from web.control_panel_liquidation_scan import liquidation_account_payload, scan_context_assets
from web.control_panel_liquidation_support import run_liquidation_fork_simulation as _run_liquidation_fork_simulation_support
from bot.liquidation import (
    archive_submission_failure,
    apply_submission_gate,
    apply_static_call_result,
    submission_gate_transition,
)
from bot.page_state import ExecutionStatus

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
    return apply_submission_gate(
        payload,
        controls=controls,
        mode=mode,
        preflight_attacher=attach_liquidation_preflight_state,
    )


def liquidation_contracts_bot_dir() -> Path:
    repo_root = Path(__file__).resolve().parents[3]
    chain_id = int(os.getenv("LIQUIDATION_CHAIN_ID", "43114"))
    return resolve_contract_project_from_env(
        chain_id,
        "BOT",
        env_name="LIQUIDATION_CONTRACTS_BOT_DIR",
        root=repo_root,
    )


def run_liquidation_fork_simulation(payload: dict, *, timeout_seconds: int = 180) -> dict:
    return _run_liquidation_fork_simulation_support(
        payload,
        timeout_seconds=timeout_seconds,
        contracts_dir=liquidation_contracts_bot_dir(),
        shutil_module=shutil,
        subprocess_module=subprocess,
        aave_pool_address_fn=aave_pool_address,
        dex_router_address_fn=dex_router_address,
        aave_rpc_urls_fn=aave_rpc_urls,
    )


def simulate_liquidation_fork_transaction(payload: dict) -> dict:
    controls = payload.get("execution_controls") or liquidation_execution_controls()
    timeout_seconds = int(controls.get("fork_simulation_timeout_seconds") or 180)
    phase = "fork_simulating"
    _record_execution_state(
        payload,
        ExecutionStatus.PREFLIGHTING,
        mode="flashloan",
        message="running liquidation fork transaction simulation",
        extra={"phase": phase},
    )
    preflight = dict(payload.get("preflight") or {})
    preflight.update(
        {
            "fork_simulation_required": True,
            "fork_simulation_status": "running",
            "fork_simulation_passed": False,
            "fork_simulation_error": None,
        }
    )
    payload["preflight"] = preflight
    try:
        result = run_liquidation_fork_simulation(payload, timeout_seconds=timeout_seconds)
        preflight.update(result)
        payload["preflight"] = preflight
        return payload
    except Exception as exc:
        preflight.update(
            {
                "fork_simulation_status": "error",
                "fork_simulation_passed": False,
                "fork_simulation_error": redact_sensitive_text(exc),
            }
        )
        payload["preflight"] = preflight
        _record_execution_state(
            payload,
            ExecutionStatus.HARD_BLOCKED,
            mode="flashloan",
            result="submission_blocked",
            message=preflight["fork_simulation_error"],
            extra={"phase": "fork_simulation_failed"},
        )
        raise


def liquidation_execution_payload_for_account(
    account: str,
    deadline_seconds: int = 300,
    allow_zero_min_out: bool = False,
    require_executor: bool = True,
    force: bool = False,
    borrow_index: int | None = None,
    candidate_index: int | None = None,
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
            message=redact_sensitive_text(exc),
            extra={"account": account, "phase": "loading_account"},
        )
        raise
    phase = "building_prediction"
    try:
        controls = liquidation_execution_controls()
        effective_deadline_seconds = max(30, int(deadline_seconds))
        if controls.get("require_fork_simulation"):
            effective_deadline_seconds = max(
                effective_deadline_seconds,
                int(controls.get("fork_simulation_timeout_seconds") or 0)
                + int(controls.get("min_deadline_remaining_seconds") or 0)
                + 30,
            )
        deadline = int(time.time()) + effective_deadline_seconds
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
            borrow_index=borrow_index,
            candidate_index=candidate_index,
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
        preflight["fork_simulation_required"] = bool(controls.get("require_fork_simulation"))
        preflight["fork_simulation_status"] = "pending" if controls.get("require_fork_simulation") else "skipped"
        preflight["fork_simulation_passed"] = False
        preflight["fork_simulation_error"] = None
        preflight["deadline_seconds_requested"] = int(deadline_seconds)
        preflight["deadline_seconds_effective"] = int(effective_deadline_seconds)
        payload["preflight"] = preflight
        payload["account_report"] = report
        payload["execution_controls"] = controls
        payload = apply_liquidation_submission_state(payload, mode="flashloan")
        transition = submission_gate_transition(payload)
        payload["execution_phase"] = transition.phase
        _record_execution_state(
            payload,
            transition.status,
            mode="flashloan",
            result=payload.get("state"),
            message=transition.message,
            extra={"account": account, "phase": transition.phase},
        )
        return payload
    except Exception as exc:
        _record_execution_state(
            {"request": {"account": account}, "preflight": locals().get("preflight", {}), "account_report": locals().get("report", {})},
            ExecutionStatus.ERROR,
            mode="flashloan",
            message=redact_sensitive_text(exc),
            extra={"account": account, "phase": phase},
        )
        raise


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
        payload = apply_static_call_result(payload, result, archive_failure=_archive_static_call_failure)
        payload = apply_liquidation_submission_state(payload, mode="flashloan")
        transition = submission_gate_transition(payload, after_preflight=True)
        payload["execution_phase"] = transition.phase
        _record_execution_state(
            payload,
            transition.status,
            mode="flashloan",
            result=payload.get("state"),
            message=transition.message,
            extra={"phase": transition.phase},
        )
        return payload
    except Exception as exc:
        _record_execution_state(
            payload,
            ExecutionStatus.ERROR,
            mode=str(payload.get("mode") or "flashloan"),
            message=redact_sensitive_text(exc),
            extra={"phase": phase},
        )
        raise


def _execution_runtime() -> LiquidationExecutionRuntime:
    return LiquidationExecutionRuntime(
        web3=Web3,
        account=Account,
        liquidation_pool_abi=LIQUIDATION_POOL_ABI,
        liquidation_erc20_abi=LIQUIDATION_ERC20_ABI,
        execution_controls=liquidation_execution_controls,
        apply_submission_state=apply_liquidation_submission_state,
        simulate_static_call=simulate_liquidation_static_call,
        simulate_fork_transaction=simulate_liquidation_fork_transaction,
        scan_context_assets=scan_context_assets,
        liquidation_config_health=liquidation_config_health,
        executor_private_key=liquidation_executor_private_key,
        executor_owner_address=liquidation_executor_owner_address,
        aave_pool_address=aave_pool_address,
        nonce_manager=_nonce_manager,
        send_raw_transaction=send_raw_transaction_private_first,
        redact_error=redact_sensitive_text,
        self_funded_private_keys=liquidation_self_funded_private_keys,
    )


def execute_flashloan_liquidation_transaction(payload: dict, force: bool = False) -> dict:
    return execute_flashloan_liquidation(payload, runtime=_execution_runtime(), force=force)


def _execute_self_funded_liquidation_for_key(payload: dict, private_key: str, force: bool = False) -> dict:
    return execute_self_funded_liquidation_for_key(
        payload,
        private_key,
        runtime=_execution_runtime(),
        force=force,
    )


def execute_self_funded_liquidation_transaction(payload: dict, force: bool = False) -> dict:
    return execute_self_funded_liquidation(payload, runtime=_execution_runtime(), force=force)
