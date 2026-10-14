from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import threading
import time
from typing import Any

from web3 import Web3

from core.chain_data_registry import chain_contracts
from dex.intent_trade.chains.robinhood.config import (
    TESTNET_MULTICALL_ADDRESS,
    TESTNET_V4_POOL_MANAGER_ADDRESS,
    testnet_rpc_urls_from_env,
)
from dex.intent_trade.chains.robinhood.shared import (
    ROBINHOOD_TESTNET_CHAIN_ID as _ROBINHOOD_TESTNET_CHAIN_ID,
    ROBINHOOD_TESTNET_NETWORK as _ROBINHOOD_TESTNET_NETWORK,
)

ROBINHOOD_TESTNET_CHAIN_ID = _ROBINHOOD_TESTNET_CHAIN_ID
ROBINHOOD_TESTNET_NETWORK = _ROBINHOOD_TESTNET_NETWORK
V4_INITIALIZE_TOPIC = Web3.keccak(
    text="Initialize(bytes32,address,address,uint24,int24,address,uint160,int24)"
).hex()
V4_SWAP_TOPIC = Web3.keccak(text="Swap(bytes32,address,int128,int128,uint160,uint128,int24,uint24)").hex()
ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"
ERC20_ABI = [
    {
        "constant": True,
        "inputs": [],
        "name": "symbol",
        "outputs": [{"type": "string"}],
        "type": "function",
    },
    {
        "constant": True,
        "inputs": [],
        "name": "decimals",
        "outputs": [{"type": "uint8"}],
        "type": "function",
    },
    {
        "constant": True,
        "inputs": [{"type": "address"}],
        "name": "balanceOf",
        "outputs": [{"type": "uint256"}],
        "type": "function",
    },
]
MULTICALL3_ABI = [
    {
        "inputs": [
            {
                "components": [
                    {"internalType": "address", "name": "target", "type": "address"},
                    {"internalType": "bool", "name": "allowFailure", "type": "bool"},
                    {"internalType": "bytes", "name": "callData", "type": "bytes"},
                ],
                "internalType": "struct Multicall3.Call3[]",
                "name": "calls",
                "type": "tuple[]",
            }
        ],
        "name": "aggregate3",
        "outputs": [
            {
                "components": [
                    {"internalType": "bool", "name": "success", "type": "bool"},
                    {"internalType": "bytes", "name": "returnData", "type": "bytes"},
                ],
                "internalType": "struct Multicall3.Result[]",
                "name": "returnData",
                "type": "tuple[]",
            }
        ],
        "stateMutability": "payable",
        "type": "function",
    }
]
TOKEN_BALANCE_SCAN_CACHE_SCHEMA = "robinhood-testnet-v4-poolmanager-token-balance-scan-v1"
_JSON_READ_CACHE: dict[str, tuple[tuple[int, int], dict[str, Any]]] = {}
_JSON_READ_CACHE_LOCK = threading.Lock()


def _stamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _as_list(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _read_json(path: Path) -> dict[str, Any]:
    try:
        stat = path.stat()
    except FileNotFoundError:
        return {}
    cache_key = (stat.st_mtime_ns, stat.st_size)
    path_key = str(path)
    with _JSON_READ_CACHE_LOCK:
        cached = _JSON_READ_CACHE.get(path_key)
        if cached is not None and cached[0] == cache_key:
            return cached[1]
    try:
        # Some migrated chain metadata files retain a UTF-8 BOM from the
        # original Windows tooling. Treat it as encoding metadata, not JSON.
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError as exc:
        payload = {"_error": f"invalid_json:{exc.msg}"}
    else:
        if not isinstance(payload, dict):
            payload = {"_error": "invalid_json_root"}
    with _JSON_READ_CACHE_LOCK:
        _JSON_READ_CACHE[path_key] = (cache_key, payload)
    return payload


def _is_address(value: object) -> bool:
    text = str(value or "").strip()
    return text.startswith("0x") and len(text) == 42 and all(char in "0123456789abcdefABCDEF" for char in text[2:])


def _configured_contract(key: str, fallback: str = "") -> str:
    try:
        configured = chain_contracts(ROBINHOOD_TESTNET_NETWORK)
    except Exception:
        configured = {}
    return str(configured.get(key) or fallback).strip()


ROBINHOOD_TESTNET_V4_POOL_MANAGER = _configured_contract(
    "v4_pool_manager",
    TESTNET_V4_POOL_MANAGER_ADDRESS,
)


def _multicall3_address() -> str:
    return _configured_contract("multicall3", TESTNET_MULTICALL_ADDRESS)


def _rpc_url_from_env_or_default() -> str:
    urls = testnet_rpc_urls_from_env()
    return urls[0] if urls else ""


def _web3(rpc_url: str) -> Web3:
    return Web3(
        Web3.HTTPProvider(
            rpc_url,
            request_kwargs={
                "timeout": 20,
                "verify": False,
                "headers": {
                    "content-type": "application/json",
                    "user-agent": "flashloan-robinhood-testnet-v4/1.0",
                },
            },
        )
    )


def _topic_address(topic: object) -> str:
    raw = topic.hex() if hasattr(topic, "hex") else str(topic or "")
    address = "0x" + raw.removeprefix("0x")[-40:]
    if int(address, 16) == 0:
        return ZERO_ADDRESS
    return Web3.to_checksum_address(address)


def _currency_metadata(w3: Web3, currency: str, pool_manager: str) -> dict[str, Any]:
    if str(currency).lower() == ZERO_ADDRESS.lower():
        balance = w3.eth.get_balance(Web3.to_checksum_address(pool_manager))
        return {
            "address": ZERO_ADDRESS,
            "symbol": "ETH",
            "decimals": 18,
            "poolManagerBalanceRaw": str(balance),
            "poolManagerBalance": float(balance) / 10**18,
            "metadataOk": True,
        }
    checksum = Web3.to_checksum_address(currency)
    contract = w3.eth.contract(address=checksum, abi=ERC20_ABI)
    symbol = ""
    decimals = None
    balance = None
    metadata_ok = True
    try:
        symbol = str(contract.functions.symbol().call()).strip()
    except Exception:
        symbol = checksum[:8]
        metadata_ok = False
    try:
        decimals = int(contract.functions.decimals().call())
    except Exception:
        metadata_ok = False
    try:
        balance = int(contract.functions.balanceOf(Web3.to_checksum_address(pool_manager)).call())
    except Exception:
        balance = None
    return {
        "address": checksum,
        "symbol": symbol or checksum[:8],
        "decimals": decimals,
        "poolManagerBalanceRaw": str(balance) if balance is not None else None,
        "poolManagerBalance": (float(balance) / 10**decimals) if balance is not None and decimals is not None else None,
        "metadataOk": metadata_ok,
    }


def _latest_swap_liquidity(
    w3: Web3,
    *,
    pool_manager: str,
    pool_id: str,
    from_block: int,
    to_block: int | str,
) -> dict[str, Any]:
    logs = w3.eth.get_logs(
        {
            "address": Web3.to_checksum_address(pool_manager),
            "fromBlock": max(0, int(from_block)),
            "toBlock": to_block,
            "topics": [V4_SWAP_TOPIC, pool_id],
        }
    )
    for log in reversed(logs):
        amount0, amount1, sqrt_price_x96, liquidity, tick, fee = w3.codec.decode(
            ["int128", "int128", "uint160", "uint128", "int24", "uint24"],
            bytes(log["data"]),
        )
        if int(liquidity) > 0:
            return {
                "latestSwapBlockNumber": int(log["blockNumber"]),
                "latestSwapTransactionHash": log["transactionHash"].hex()
                if hasattr(log["transactionHash"], "hex")
                else str(log["transactionHash"]),
                "latestSwapLiquidity": str(liquidity),
                "latestSwapAmount0": str(amount0),
                "latestSwapAmount1": str(amount1),
                "latestSwapSqrtPriceX96": str(sqrt_price_x96),
                "latestSwapTick": int(tick),
                "latestSwapFee": int(fee),
            }
    return {}


def _decode_initialize_log(
    w3: Web3,
    *,
    pool_manager: str,
    pool_id: str,
    from_block: int = 0,
    to_block: int | str = "latest",
) -> dict[str, Any] | None:
    logs = w3.eth.get_logs(
        {
            "address": Web3.to_checksum_address(pool_manager),
            "fromBlock": max(0, int(from_block)),
            "toBlock": to_block,
            "topics": [V4_INITIALIZE_TOPIC, pool_id],
        }
    )
    if not logs:
        return None
    log = logs[0]
    topics = list(log["topics"])
    fee, tick_spacing, hooks, sqrt_price_x96, tick = w3.codec.decode(
        ["uint24", "int24", "address", "uint160", "int24"],
        bytes(log["data"]),
    )
    currency0 = _currency_metadata(w3, _topic_address(topics[2]), pool_manager)
    currency1 = _currency_metadata(w3, _topic_address(topics[3]), pool_manager)
    return {
        "poolId": pool_id,
        "poolKeyDecoded": True,
        "currency0": currency0["address"],
        "currency1": currency1["address"],
        "token0": currency0["address"],
        "token1": currency1["address"],
        "token0Symbol": currency0["symbol"],
        "token1Symbol": currency1["symbol"],
        "token0Decimals": currency0["decimals"],
        "token1Decimals": currency1["decimals"],
        "symbolPair": f"{currency0['symbol']}:{currency1['symbol']}",
        "fee": int(fee),
        "tickSpacing": int(tick_spacing),
        "hooks": Web3.to_checksum_address(hooks) if int(str(hooks), 16) else ZERO_ADDRESS,
        "sqrtPriceX96": str(sqrt_price_x96),
        "tick": int(tick),
        "initializeBlockNumber": int(log["blockNumber"]),
        "initializeTransactionHash": log["transactionHash"].hex()
        if hasattr(log["transactionHash"], "hex")
        else str(log["transactionHash"]),
        "currency0Metadata": currency0,
        "currency1Metadata": currency1,
    }


def _enrich_pool_ids_with_rpc(
    pool_ids: list[str],
    *,
    pool_manager: str,
    rpc_url: str,
    swap_from_block: int,
    to_block: int | str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    w3 = _web3(rpc_url)
    chain_id = int(w3.eth.chain_id)
    if chain_id != ROBINHOOD_TESTNET_CHAIN_ID:
        raise ValueError(f"RPC returned chainId {chain_id}, expected {ROBINHOOD_TESTNET_CHAIN_ID}")
    pool_keys: list[dict[str, Any]] = []
    pools: list[dict[str, Any]] = []
    errors: list[str] = []
    for pool_id in pool_ids:
        try:
            decoded = _decode_initialize_log(w3, pool_manager=pool_manager, pool_id=pool_id)
            if decoded is None:
                decoded = {"poolId": pool_id, "poolKeyDecoded": False}
            swap_state = _latest_swap_liquidity(
                w3,
                pool_manager=pool_manager,
                pool_id=pool_id,
                from_block=swap_from_block,
                to_block=to_block,
            )
            pool_keys.append(decoded)
            symbol_pair = decoded.get("symbolPair") or f"poolId:{pool_id[2:10]}"
            pools.append(
                {
                    "protocol": "uniswap_v4",
                    "poolId": pool_id,
                    "poolKey": decoded,
                    "poolManager": pool_manager,
                    "symbolPair": symbol_pair,
                    "token0": decoded.get("token0") or decoded.get("currency0"),
                    "token1": decoded.get("token1") or decoded.get("currency1"),
                    "token0Symbol": decoded.get("token0Symbol") or "",
                    "token1Symbol": decoded.get("token1Symbol") or "",
                    "token0Decimals": decoded.get("token0Decimals"),
                    "token1Decimals": decoded.get("token1Decimals"),
                    "fee": decoded.get("fee"),
                    "tickSpacing": decoded.get("tickSpacing"),
                    "hooks": decoded.get("hooks"),
                    "liquidity": swap_state.get("latestSwapLiquidity"),
                    "tvlUsd": 0,
                    "status": "observable_token_pair_nonzero_liquidity_swap"
                    if decoded.get("poolKeyDecoded") and swap_state.get("latestSwapLiquidity")
                    else "observable_poolid_nonzero_liquidity_swap",
                    "depthStatus": "unpriced_token_pair"
                    if decoded.get("poolKeyDecoded")
                    else "unpriced_poolid_only",
                    "source": "poolmanager_initialize_and_swap_logs",
                    **swap_state,
                }
            )
        except Exception as exc:
            errors.append(f"{pool_id}:{str(exc)[:160]}")
            pool_keys.append({"poolId": pool_id, "poolKeyDecoded": False, "error": str(exc)[:200]})
    return pool_keys, pools, errors


