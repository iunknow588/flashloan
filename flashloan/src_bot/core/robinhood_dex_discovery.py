from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any, Callable

from web3 import Web3

from execution.uniswap_v3 import snapshot_v3_pool
from intent_trade.chains.robinhood import config as robinhood


_ADDRESS_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")
POOL_CREATED_TOPIC0 = Web3.keccak(
    text="PoolCreated(address,address,uint24,int24,address)"
).hex()
JsonRpcTransport = Callable[[str, dict[str, Any], float], dict[str, Any]]

UNISWAP_V3_FACTORY_ABI = [
    {
        "inputs": [
            {"internalType": "address", "name": "tokenA", "type": "address"},
            {"internalType": "address", "name": "tokenB", "type": "address"},
            {"internalType": "uint24", "name": "fee", "type": "uint24"},
        ],
        "name": "getPool",
        "outputs": [{"internalType": "address", "name": "pool", "type": "address"}],
        "stateMutability": "view",
        "type": "function",
    }
]

DEFAULT_V3_TICK_SPACING = {100: 1, 500: 10, 3000: 60, 10000: 200}


def _rpc_transport(url: str, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
    import json as json_module
    from urllib import request

    req = request.Request(
        url,
        data=json_module.dumps(payload).encode("utf-8"),
        headers={"content-type": "application/json", "user-agent": "flashloan-robinhood-dex-discovery/1.0"},
        method="POST",
    )
    with request.urlopen(req, timeout=timeout) as response:
        return json_module.loads(response.read().decode("utf-8"))


def _rpc_call(
    rpc_url: str,
    method: str,
    params: list[Any],
    *,
    timeout_seconds: float,
    transport: JsonRpcTransport | None,
) -> Any:
    payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    result = (transport or _rpc_transport)(rpc_url, payload, timeout_seconds)
    if result.get("error"):
        raise RuntimeError(f"{method} failed: {result['error']}")
    return result.get("result")


def _word(value: str, index: int) -> str:
    raw = str(value or "").removeprefix("0x")
    start = index * 64
    return raw[start : start + 64].rjust(64, "0")


def _topic_address(value: str) -> str:
    return "0x" + str(value or "").removeprefix("0x")[-40:]


def decode_uniswap_v3_pool_created(log: dict[str, Any]) -> dict[str, Any]:
    topics = list(log.get("topics") or [])
    if len(topics) < 4 or str(topics[0]).lower() != POOL_CREATED_TOPIC0.lower():
        raise ValueError("log is not a Uniswap V3 PoolCreated event")
    data = str(log.get("data") or "")
    pool = "0x" + _word(data, 1)[-40:]
    return {
        "factory": str(log.get("address") or ""),
        "token0": _topic_address(topics[1]),
        "token1": _topic_address(topics[2]),
        "fee": int(topics[3], 16),
        "tickSpacing": int(_word(data, 0), 16),
        "pool": pool,
        "blockNumber": int(str(log.get("blockNumber") or "0x0"), 16),
        "transactionHash": log.get("transactionHash"),
        "logIndex": int(str(log.get("logIndex") or "0x0"), 16),
    }


def scan_uniswap_v3_pool_created(
    rpc_url: str,
    *,
    factory_address: str,
    source_url: str,
    from_block: int,
    to_block: int,
    chunk_size: int = 2000,
    timeout_seconds: float = 12,
    transport: JsonRpcTransport | None = None,
) -> dict[str, Any]:
    factory = str(factory_address or "").strip()
    source = str(source_url or "").strip()
    if not _ADDRESS_RE.fullmatch(factory):
        raise ValueError("factory_address must be a 20-byte EVM address")
    if not source.startswith(("https://", "http://")):
        raise ValueError("source_url must be an http(s) evidence URL")
    start = max(0, int(from_block))
    end = max(start, int(to_block))
    step = max(1, int(chunk_size))
    code = _rpc_call(rpc_url, "eth_getCode", [factory, "latest"], timeout_seconds=timeout_seconds, transport=transport)
    if str(code or "").lower() in {"", "0x", "0x0"}:
        raise ValueError("factory_address has no contract code on the selected Robinhood RPC")

    pools: list[dict[str, Any]] = []
    ranges: list[dict[str, int]] = []
    for range_start in range(start, end + 1, step):
        range_end = min(end, range_start + step - 1)
        ranges.append({"fromBlock": range_start, "toBlock": range_end})
        logs = _rpc_call(
            rpc_url,
            "eth_getLogs",
            [
                {
                    "address": factory,
                    "fromBlock": hex(range_start),
                    "toBlock": hex(range_end),
                    "topics": [POOL_CREATED_TOPIC0],
                }
            ],
            timeout_seconds=timeout_seconds,
            transport=transport,
        ) or []
        for log in logs:
            decoded = decode_uniswap_v3_pool_created(log)
            pool_code = _rpc_call(
                rpc_url,
                "eth_getCode",
                [decoded["pool"], "latest"],
                timeout_seconds=timeout_seconds,
                transport=transport,
            )
            decoded["poolCodeVerified"] = str(pool_code or "").lower() not in {"", "0x", "0x0"}
            pools.append(decoded)
    return {
        "network": robinhood.NETWORK,
        "chainId": robinhood.CHAIN_ID,
        "rpcUrl": rpc_url,
        "factory": factory,
        "factoryCodeVerified": True,
        "sourceUrl": source,
        "event": "PoolCreated(address,address,uint24,int24,address)",
        "topic0": POOL_CREATED_TOPIC0,
        "blockRange": {"fromBlock": start, "toBlock": end},
        "scannedRanges": ranges,
        "poolCount": len(pools),
        "pools": pools,
        "executionEnabled": False,
        "broadcastEnabled": False,
        "evidenceType": "readonly_factory_event_scan",
    }


def discover_uniswap_v3_pools_by_factory(
    rpc_url: str,
    *,
    factory_address: str,
    source_url: str,
    token_pairs: list[tuple[str, str]],
    fee_tiers: tuple[int, ...] = (100, 500, 3000, 10000),
    timeout_seconds: float = 20,
) -> dict[str, Any]:
    """Discover current pools through the live factory using read-only calls."""

    factory = Web3.to_checksum_address(str(factory_address))
    source = str(source_url or "").strip()
    if not source.startswith(("https://", "http://")):
        raise ValueError("source_url must be an http(s) evidence URL")
    w3 = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": timeout_seconds}))
    chain_id = int(w3.eth.chain_id)
    if chain_id != robinhood.CHAIN_ID:
        raise ValueError(f"RPC returned chain id {chain_id}, expected {robinhood.CHAIN_ID}")
    block_number = int(w3.eth.block_number)
    factory_code = w3.eth.get_code(factory, block_identifier=block_number)
    if str(factory_code or "").lower() in {"", "0x", "0x0"}:
        raise ValueError("factory_address has no contract code on the selected Robinhood RPC")
    contract = w3.eth.contract(address=factory, abi=UNISWAP_V3_FACTORY_ABI)

    pools: list[dict[str, Any]] = []
    queries: list[dict[str, Any]] = []
    seen: set[str] = set()
    for token_a, token_b in token_pairs:
        token_a_checksum = Web3.to_checksum_address(str(token_a))
        token_b_checksum = Web3.to_checksum_address(str(token_b))
        for fee in fee_tiers:
            queried_pool = contract.functions.getPool(token_a_checksum, token_b_checksum, int(fee)).call(
                block_identifier=block_number
            )
            pool = str(queried_pool)
            queries.append(
                {
                    "tokenA": token_a_checksum,
                    "tokenB": token_b_checksum,
                    "fee": int(fee),
                    "pool": pool,
                    "blockNumber": block_number,
                    "factoryQueryVerified": True,
                }
            )
            if not pool or pool.lower() == "0x0000000000000000000000000000000000000000":
                continue
            pool_key = pool.lower()
            if pool_key in seen:
                continue
            seen.add(pool_key)
            pool_code = w3.eth.get_code(Web3.to_checksum_address(pool), block_identifier=block_number)
            pool_code_verified = str(pool_code or "").lower() not in {"", "0x", "0x0"}
            record: dict[str, Any] = {
                "factory": factory,
                "token0": token_a_checksum,
                "token1": token_b_checksum,
                "fee": int(fee),
                "tickSpacing": DEFAULT_V3_TICK_SPACING.get(int(fee)),
                "pool": Web3.to_checksum_address(pool),
                "blockNumber": block_number,
                "poolCodeVerified": pool_code_verified,
                "factoryQueryVerified": True,
                "eventVerified": False,
            }
            if pool_code_verified:
                try:
                    snapshot = snapshot_v3_pool(w3, pool, block_identifier=block_number)
                    record.update(
                        {
                            "token0": snapshot.token0,
                            "token1": snapshot.token1,
                            "fee": snapshot.fee,
                            "liquidity": str(snapshot.liquidity),
                            "sqrtPriceX96": str(snapshot.sqrt_price_x96),
                            "tick": snapshot.tick,
                            "poolStateVerified": True,
                        }
                    )
                except Exception as exc:
                    record["poolStateVerified"] = False
                    record["poolStateError"] = str(exc)
            pools.append(record)
    return {
        "network": robinhood.NETWORK,
        "chainId": robinhood.CHAIN_ID,
        "rpcUrl": rpc_url,
        "factory": factory,
        "factoryCodeVerified": True,
        "sourceUrl": source,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "blockNumber": block_number,
        "discoveryMethod": "factory_getPool_readonly",
        "evidenceType": "readonly_factory_state_query",
        "queries": queries,
        "poolCount": len(pools),
        "pools": pools,
        "executionEnabled": False,
        "broadcastEnabled": False,
    }
