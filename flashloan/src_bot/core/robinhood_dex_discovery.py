from __future__ import annotations

import json
import re
from typing import Any, Callable

from web3 import Web3

from intent_trade.chains.robinhood import config as robinhood


_ADDRESS_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")
POOL_CREATED_TOPIC0 = Web3.keccak(
    text="PoolCreated(address,address,uint24,int24,address)"
).hex()
JsonRpcTransport = Callable[[str, dict[str, Any], float], dict[str, Any]]


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
