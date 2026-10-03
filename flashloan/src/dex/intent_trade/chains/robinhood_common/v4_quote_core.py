"""Shared Uniswap V4 quote-adapter constants and neutral helpers.

The mainnet and testnet adapters differ only in chain configuration and a few
testnet-only RPC conveniences. This module keeps the identical ABI surface,
normalization helpers, and chain-data lookup in one place so both adapters stay
thin.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from core.chain_data_registry import chain_data_by_chain_id, chain_data_by_network, chain_data_file_path


V4_POOLS_SLOT = (6).to_bytes(32, "big")
V4_POOL_MANAGER_EXTSLOAD_ABI = [
    {
        "inputs": [{"internalType": "bytes32", "name": "slot", "type": "bytes32"}],
        "name": "extsload",
        "outputs": [{"internalType": "bytes32", "name": "value", "type": "bytes32"}],
        "stateMutability": "view",
        "type": "function",
    }
]
V4_CUSTOM_ERROR_SELECTORS = {
    "0x6190b2b0": "UnexpectedRevertBytes(bytes)",
    "0x7a5ed734": "NotEnoughLiquidity(bytes32)",
    "0xe0752a5a": "UnexpectedCallSuccess()",
    "0x486aa307": "PoolNotInitialized()",
}
V4_QUOTER_ABI = [
    {
        "inputs": [
            {
                "components": [
                    {
                        "components": [
                            {"internalType": "Currency", "name": "currency0", "type": "address"},
                            {"internalType": "Currency", "name": "currency1", "type": "address"},
                            {"internalType": "uint24", "name": "fee", "type": "uint24"},
                            {"internalType": "int24", "name": "tickSpacing", "type": "int24"},
                            {"internalType": "address", "name": "hooks", "type": "address"},
                        ],
                        "internalType": "struct PoolKey",
                        "name": "poolKey",
                        "type": "tuple",
                    },
                    {"internalType": "bool", "name": "zeroForOne", "type": "bool"},
                    {"internalType": "uint128", "name": "exactAmount", "type": "uint128"},
                    {"internalType": "bytes", "name": "hookData", "type": "bytes"},
                ],
                "internalType": "struct IV4Quoter.QuoteExactSingleParams",
                "name": "params",
                "type": "tuple",
            }
        ],
        "name": "quoteExactInputSingle",
        "outputs": [
            {"internalType": "uint256", "name": "amountOut", "type": "uint256"},
            {"internalType": "uint256", "name": "gasEstimate", "type": "uint256"},
        ],
        "stateMutability": "nonpayable",
        "type": "function",
    }
]


def text(value: object) -> str:
    return str(value or "").strip()


def normalize_address(value: object) -> str:
    raw = text(value)
    return raw.lower() if raw.startswith("0x") and len(raw) == 42 else ""


def normalize_pool_id(value: object) -> str:
    raw = text(value)
    return raw.lower() if raw.startswith("0x") and len(raw) == 66 else normalize_address(value)


def read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def chain_row(network: str | None, chain_id: int | None) -> dict[str, Any]:
    if chain_id is not None:
        try:
            row = chain_data_by_chain_id(int(chain_id)) or {}
        except Exception:
            row = {}
        if row:
            return row
    normalized = text(network).lower()
    if not normalized:
        return {}
    try:
        row = chain_data_by_network(normalized) or {}
    except Exception:
        row = {}
    return row if isinstance(row, dict) else {}


def pool_index_path(
    *,
    network: str,
    chain_id: int,
    fallback: Path,
) -> Path | None:
    path = chain_data_file_path(network, "v4_pool_index", chain_id=chain_id)
    if path is not None:
        return Path(path)
    return fallback


__all__ = [
    "V4_CUSTOM_ERROR_SELECTORS",
    "V4_POOL_MANAGER_EXTSLOAD_ABI",
    "V4_POOLS_SLOT",
    "V4_QUOTER_ABI",
    "chain_row",
    "normalize_address",
    "normalize_pool_id",
    "pool_index_path",
    "read_json",
    "text",
]
