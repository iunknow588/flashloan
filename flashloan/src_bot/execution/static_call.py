from __future__ import annotations

from datetime import datetime, timezone

from web3 import Web3

from core.sensitive_data import redact_sensitive_text
from execution.revert_parser import parse_revert_reason


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
                    {"internalType": "uint256", "name": "gasLimit", "type": "uint256"},
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


def liquidation_request_tuple(request: dict) -> tuple:
    return (
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


def simulate_request_liquidation_static_call(
    w3: Web3,
    *,
    executor_address: str,
    owner_address: str,
    request: dict,
) -> dict:
    executor = w3.eth.contract(address=Web3.to_checksum_address(executor_address), abi=LIQUIDATION_EXECUTOR_ABI)
    checksum_owner = Web3.to_checksum_address(owner_address)
    try:
        executor.functions.requestLiquidation(liquidation_request_tuple(request)).call({"from": checksum_owner})
        status = "passed"
        error = None
        parsed = {"category": "success", "label": "staticCall passed", "raw": "", "confidence": "high"}
    except Exception as exc:
        status = "error"
        raw_error = str(exc)
        error = redact_sensitive_text(raw_error)
        parsed = parse_revert_reason(raw_error)
    return {
        "status": status,
        "error": error,
        "parsed": parsed,
        "simulated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
