from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from web3 import Web3

from core.sensitive_data import redact_sensitive_text


AAVE_POOL_FLASHLOAN_PREMIUM_ABI = [
    {
        "inputs": [],
        "name": "FLASHLOAN_PREMIUM_TOTAL",
        "outputs": [{"internalType": "uint128", "name": "", "type": "uint128"}],
        "stateMutability": "view",
        "type": "function",
    }
]


def fallback_flashloan_premium(fallback_percent: float, error: str | None = None) -> dict[str, Any]:
    return {
        "premium_bps": int(round(max(0.0, float(fallback_percent)) * 100.0)),
        "premium_percent": max(0.0, float(fallback_percent)),
        "source": "fallback_config",
        "block_number": None,
        "read_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "error": error,
    }


def read_aave_flashloan_premium(
    rpc_url: str,
    pool_address: str,
    *,
    fallback_percent: float,
    web3_class=Web3,
) -> dict[str, Any]:
    if not rpc_url or not pool_address:
        return fallback_flashloan_premium(fallback_percent, "missing rpc_url or pool_address")
    try:
        w3 = web3_class(web3_class.HTTPProvider(rpc_url, request_kwargs={"timeout": 8}))
        pool = w3.eth.contract(
            address=web3_class.to_checksum_address(pool_address),
            abi=AAVE_POOL_FLASHLOAN_PREMIUM_ABI,
        )
        premium_bps = int(pool.functions.FLASHLOAN_PREMIUM_TOTAL().call())
        block_number = None
        try:
            block_number = int(w3.eth.block_number)
        except Exception:
            block_number = None
        return {
            "premium_bps": premium_bps,
            "premium_percent": premium_bps / 100.0,
            "source": "aave_pool",
            "block_number": block_number,
            "read_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "error": None,
        }
    except Exception as exc:
        return fallback_flashloan_premium(fallback_percent, redact_sensitive_text(exc))
