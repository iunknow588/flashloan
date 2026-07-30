import os
import time
from datetime import datetime, timezone

from eth_account import Account
from web3 import Web3

from execution.liquidation_payload import LiquidationExecutionPayloadConfig, build_liquidation_execution_payload
from web.control_panel_liquidation_base import *
from web.control_panel_liquidation_scan import liquidation_account_payload, scan_context_assets
def liquidation_execution_payload_for_account(
    account: str,
    deadline_seconds: int = 300,
    allow_zero_min_out: bool = False,
    require_executor: bool = True,
) -> dict:
    executor_address = liquidation_executor_address()
    if require_executor and not executor_address:
        raise RuntimeError("missing LIQUIDATION_EXECUTOR_ADDRESS")
    executor_address = executor_address or "0x0000000000000000000000000000000000000000"
    report = liquidation_account_payload(account)
    deadline = int(time.time()) + max(30, int(deadline_seconds))
    controls = liquidation_execution_controls()
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
    candidate = payload.get("request") or {}
    if controls["max_debt_to_cover"] > 0 and int(candidate.get("debtToCover") or 0) > controls["max_debt_to_cover"]:
        raise ValueError("debtToCover exceeds LIQUIDATION_MAX_DEBT_TO_COVER")
    profit_amount = int(candidate.get("minProfitAmount") or 0)
    if profit_amount < controls["min_profit_base"]:
        raise ValueError("minProfitAmount is below LIQUIDATION_MIN_PROFIT_BASE")
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
    return payload


LIQUIDATION_EXECUTOR_ABI = [
    {
        "inputs": [
            {
                "components": [
                    {"internalType": "address", "name": "user", "type": "address"},
                    {"internalType": "address", "name": "collateralAsset", "type": "address"},
                    {"internalType": "address", "name": "debtAsset", "type": "address"},
                    {"internalType": "uint256", "name": "debtToCover", "type": "uint256"},
                    {"internalType": "uint256", "name": "minCollateralSwapOut", "type": "uint256"},
                    {"internalType": "uint256", "name": "minProfitAmount", "type": "uint256"},
                    {"internalType": "uint256", "name": "deadline", "type": "uint256"},
                    {"internalType": "address[]", "name": "swapPath", "type": "address[]"},
                ],
                "internalType": "struct AaveV3LiquidationExecutor.LiquidationRequest",
                "name": "request",
                "type": "tuple",
            }
        ],
        "name": "requestLiquidation",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function",
    }
]

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


def simulate_liquidation_static_call(payload: dict) -> dict:
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
    executor = w3.eth.contract(address=Web3.to_checksum_address(executor_address), abi=LIQUIDATION_EXECUTOR_ABI)
    checksum_owner = Web3.to_checksum_address(owner_address)
    try:
        executor.functions.requestLiquidation(
            (
                Web3.to_checksum_address(str(request.get("user") or "")),
                Web3.to_checksum_address(str(request.get("collateralAsset") or "")),
                Web3.to_checksum_address(str(request.get("debtAsset") or "")),
                int(request.get("debtToCover") or 0),
                int(request.get("minCollateralSwapOut") or 0),
                int(request.get("minProfitAmount") or 0),
                int(request.get("deadline") or 0),
                [Web3.to_checksum_address(str(item)) for item in (request.get("swapPath") or []) if str(item).strip()],
            )
        ).call({"from": checksum_owner})
        status = "passed"
        error = None
    except Exception as exc:
        status = "error"
        error = str(exc)
    simulated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    preflight = dict(payload.get("preflight") or {})
    preflight.update(
        {
            "static_call_required": True,
            "static_call_status": status,
            "static_call_passed": status == "passed",
            "static_call_error": error,
            "static_call_simulated_at": simulated_at,
        }
    )
    payload["preflight"] = preflight
    return payload


def _format_tx_receipt(receipt) -> dict:
    return {
        "transaction_hash": receipt.transactionHash.hex() if hasattr(receipt.transactionHash, "hex") else str(receipt.transactionHash),
        "block_number": int(receipt.blockNumber or 0),
        "gas_used": int(receipt.gasUsed or 0),
        "effective_gas_price": int(getattr(receipt, "effectiveGasPrice", 0) or 0),
        "status": int(receipt.status or 0),
    }


def execute_flashloan_liquidation_transaction(payload: dict) -> dict:
    controls = liquidation_execution_controls()
    if not controls["execution_enabled"]:
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
    account = Account.from_key(private_key)
    checksum_sender = Web3.to_checksum_address(account.address)
    if checksum_sender.lower() != Web3.to_checksum_address(owner_address).lower():
        raise RuntimeError("execution private key does not match LIQUIDATION_EXECUTOR_OWNER_ADDRESS")

    preflight = simulate_liquidation_static_call(dict(payload))
    preflight_info = preflight.get("preflight") or {}
    if controls["require_static_call"] and not preflight_info.get("static_call_passed"):
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
            [Web3.to_checksum_address(str(item)) for item in (request.get("swapPath") or []) if str(item).strip()],
        )
    )
    nonce = w3.eth.get_transaction_count(checksum_sender)
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

    built_tx = tx_builder.build_transaction(tx_params)
    signed = w3.eth.account.sign_transaction(built_tx, private_key=private_key)
    raw_tx = getattr(signed, "raw_transaction", None) or getattr(signed, "rawTransaction")
    tx_hash = w3.eth.send_raw_transaction(raw_tx)
    timeout_seconds = max(30, int(controls["tx_timeout_seconds"]))
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=timeout_seconds)
    receipt_data = _format_tx_receipt(receipt)
    account_report = preflight.get("account_report") or {}
    return {
        "mode": "flashloan",
        "executor": preflight.get("executor"),
        "request": request,
        "preflight": preflight_info | {
            "static_call_required": bool(controls["require_static_call"]),
            "static_call_passed": bool(preflight_info.get("static_call_passed")),
        },
        "receipt": receipt_data,
        "tx_hash": tx_hash.hex(),
        "account_report": account_report,
        "execution_plan": account_report.get("execution_plan") if isinstance(account_report, dict) else None,
        "execution_controls": preflight.get("execution_controls") or controls,
    }


def execute_self_funded_liquidation_transaction(payload: dict) -> dict:
    controls = liquidation_execution_controls()
    if not controls["execution_enabled"]:
        raise RuntimeError("LIQUIDATION_EXECUTION_ENABLED is false")

    private_key = liquidation_self_funded_private_key()
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
    debt_token = w3.eth.contract(address=debt_asset, abi=LIQUIDATION_ERC20_ABI)
    pool = w3.eth.contract(address=pool_address, abi=LIQUIDATION_POOL_ABI)
    nonce = w3.eth.get_transaction_count(sender)
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
    approval_tx = approval_builder.build_transaction(approval_params)
    signed_approval = w3.eth.account.sign_transaction(approval_tx, private_key=private_key)
    raw_approval = getattr(signed_approval, "raw_transaction", None) or getattr(signed_approval, "rawTransaction")
    approval_hash = w3.eth.send_raw_transaction(raw_approval)
    timeout_seconds = max(30, int(controls["tx_timeout_seconds"]))
    approval_receipt = w3.eth.wait_for_transaction_receipt(approval_hash, timeout=timeout_seconds)
    approval_data = _format_tx_receipt(approval_receipt)

    liquidation_builder = pool.functions.liquidationCall(
        collateral_asset,
        debt_asset,
        user,
        debt_to_cover,
        False,
    )
    liquidation_nonce = nonce + 1
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
    liquidation_tx = liquidation_builder.build_transaction(tx_params)
    signed_liquidation = w3.eth.account.sign_transaction(liquidation_tx, private_key=private_key)
    raw_liquidation = getattr(signed_liquidation, "raw_transaction", None) or getattr(signed_liquidation, "rawTransaction")
    tx_hash = w3.eth.send_raw_transaction(raw_liquidation)
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=timeout_seconds)
    account_report = payload.get("account_report") or {}
    return {
        "mode": "self_funded",
        "pool": pool_address,
        "sender": sender,
        "request": request,
        "approval_receipt": approval_data,
        "receipt": _format_tx_receipt(receipt),
        "tx_hash": tx_hash.hex(),
        "account_report": account_report,
        "execution_plan": account_report.get("execution_plan") if isinstance(account_report, dict) else None,
        "execution_controls": payload.get("execution_controls") or controls,
    }
