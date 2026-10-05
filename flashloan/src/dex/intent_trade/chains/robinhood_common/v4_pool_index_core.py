"""Shared Robinhood V4 PoolManager Initialize scan logic.

The mainnet and testnet chain modules should only provide configuration
(network, chain id, pool manager, RPC url, data file path). Everything else
in this file is intentionally chain-agnostic.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time
from typing import Any

from eth_utils import event_signature_to_log_topic
from web3 import Web3


ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"
V4_INITIALIZE_TOPIC = "0x" + event_signature_to_log_topic(
    "Initialize(bytes32,address,address,uint24,int24,address,uint160,int24)"
).hex()
V4_SWAP_TOPIC = "0x" + event_signature_to_log_topic(
    "Swap(bytes32,address,int128,int128,uint160,uint128,int24,uint24)"
).hex()
ERC20_ABI = [
    {"inputs": [], "name": "decimals", "outputs": [{"type": "uint8"}], "stateMutability": "view", "type": "function"},
    {"inputs": [], "name": "symbol", "outputs": [{"type": "string"}], "stateMutability": "view", "type": "function"},
]


def _stamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _topic_address(topic: Any) -> str:
    raw = bytes(topic) if not isinstance(topic, str) else bytes.fromhex(topic[2:] if topic.startswith("0x") else topic)
    return Web3.to_checksum_address("0x" + raw[-20:].hex())


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _connect_rpc(
    rpc_url: str,
    *,
    user_agent: str = "flashloan-robinhood-v4/1.0",
    timeout_seconds: float = 20.0,
    attempts: int = 3,
) -> Web3:
    last_exc: Exception | None = None
    for attempt in range(max(1, attempts)):
        provider = Web3.HTTPProvider(
            rpc_url,
            request_kwargs={
                "timeout": timeout_seconds,
                "headers": {
                    "Content-Type": "application/json",
                    "User-Agent": user_agent,
                    "Connection": "close",
                },
            },
        )
        w3 = Web3(provider)
        try:
            if w3.is_connected():
                return w3
        except Exception as exc:
            last_exc = exc
        time.sleep(1 + attempt)
    raise RuntimeError("Robinhood V4 RPC unavailable") from last_exc


def _erc20_metadata(w3: Web3, address: str, cache: dict[str, dict[str, Any]]) -> dict[str, Any]:
    if str(address or "").lower() == ZERO_ADDRESS.lower():
        return {"address": ZERO_ADDRESS, "symbol": "ETH", "decimals": 18, "metadataOk": True}
    key = str(address).lower()
    if key in cache:
        return cache[key]
    checksum = Web3.to_checksum_address(address)
    contract = w3.eth.contract(address=checksum, abi=ERC20_ABI)
    symbol, decimals, ok = "", None, True
    try:
        symbol = str(contract.functions.symbol().call()).strip()
    except Exception:
        ok = False
    try:
        decimals = int(contract.functions.decimals().call())
    except Exception:
        ok = False
    row = {
        "address": checksum,
        "symbol": symbol or checksum[:10],
        "decimals": decimals,
        "metadataOk": ok,
    }
    cache[key] = row
    return row


def _pool_row_from_initialize_log(
    w3: Web3,
    log: Any,
    *,
    pool_manager: str,
    include_metadata: bool,
    include_currency_metadata: bool,
    metadata_cache: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    topics = list(log["topics"])
    pool_id = topics[1].hex() if hasattr(topics[1], "hex") else str(topics[1])
    fee, tick_spacing, hooks, sqrt_price_x96, tick = w3.codec.decode(
        ["uint24", "int24", "address", "uint160", "int24"],
        bytes(log["data"]),
    )
    currency0 = _topic_address(topics[2])
    currency1 = _topic_address(topics[3])
    if include_metadata:
        metadata0 = _erc20_metadata(w3, currency0, metadata_cache)
        metadata1 = _erc20_metadata(w3, currency1, metadata_cache)
    else:
        metadata0 = {
            "address": currency0,
            "symbol": "ETH" if currency0.lower() == ZERO_ADDRESS.lower() else currency0[:10],
            "decimals": 18 if currency0.lower() == ZERO_ADDRESS.lower() else None,
            "metadataOk": currency0.lower() == ZERO_ADDRESS.lower(),
        }
        metadata1 = {
            "address": currency1,
            "symbol": "ETH" if currency1.lower() == ZERO_ADDRESS.lower() else currency1[:10],
            "decimals": 18 if currency1.lower() == ZERO_ADDRESS.lower() else None,
            "metadataOk": currency1.lower() == ZERO_ADDRESS.lower(),
        }
    tx_hash = log["transactionHash"].hex() if hasattr(log["transactionHash"], "hex") else str(log["transactionHash"])
    hooks_address = Web3.to_checksum_address(hooks) if int(str(hooks), 16) else ZERO_ADDRESS
    key: dict[str, Any] = {
        "poolId": pool_id,
        "poolKeyDecoded": True,
        "currency0": currency0,
        "currency1": currency1,
        "token0": currency0,
        "token1": currency1,
        "token0Symbol": metadata0["symbol"],
        "token1Symbol": metadata1["symbol"],
        "token0Decimals": metadata0.get("decimals"),
        "token1Decimals": metadata1.get("decimals"),
        "fee": int(fee),
        "tickSpacing": int(tick_spacing),
        "hooks": hooks_address,
        "sqrtPriceX96": str(sqrt_price_x96),
        "tick": int(tick),
        "initializeBlockNumber": int(log["blockNumber"]),
        "initializeTransactionHash": tx_hash,
    }
    if include_currency_metadata:
        key["currency0Metadata"] = metadata0
        key["currency1Metadata"] = metadata1
    return {
        "protocol": "uniswap_v4",
        "poolId": pool_id,
        "poolKeyDecoded": True,
        "poolKey": key,
        "poolManager": pool_manager,
        "symbolPair": f"{metadata0['symbol']}:{metadata1['symbol']}",
        "token0": currency0,
        "token1": currency1,
        "token0Symbol": metadata0["symbol"],
        "token1Symbol": metadata1["symbol"],
        "token0Decimals": metadata0.get("decimals"),
        "token1Decimals": metadata1.get("decimals"),
        "fee": int(fee),
        "tickSpacing": int(tick_spacing),
        "hooks": hooks_address,
        "sqrtPriceX96": str(sqrt_price_x96),
        "tick": int(tick),
        "initializeBlockNumber": int(log["blockNumber"]),
        "initializeTransactionHash": tx_hash,
        "liquidity": "",
        "tvlUsd": 0,
        "status": "initialized_pool",
        "depthStatus": "initialized_unpriced",
        "source": "poolmanager_initialize_logs",
    }


def _get_logs_chunked(
    w3: Web3,
    *,
    pool_manager: str,
    topic: str,
    from_block: int,
    to_block: int,
    chunk_size: int,
    min_chunk_size: int = 1_000,
) -> list[Any]:
    logs: list[Any] = []
    cursor, step = max(0, int(from_block)), max(1, int(chunk_size))
    while cursor <= int(to_block):
        end = min(int(to_block), cursor + step - 1)
        try:
            logs.extend(
                w3.eth.get_logs(
                    {
                        "address": Web3.to_checksum_address(pool_manager),
                        "fromBlock": cursor,
                        "toBlock": end,
                        "topics": [topic],
                    }
                )
            )
            cursor = end + 1
        except Exception:
            exc_type, exc_value, _ = sys.exc_info()
            message = str(exc_value or "").lower()
            if step <= min_chunk_size and "exceeds limit" not in message and "limit" not in message:
                raise
            if "exceeds limit" in message or "limit" in message:
                step = max(1, step // 2)
            else:
                step = max(min_chunk_size, step // 2)
    return logs


def _mark_recent_swap_activity(
    w3: Web3,
    pools: list[dict[str, Any]],
    *,
    pool_manager: str,
    latest_block: int,
    lookback_blocks: int,
) -> dict[str, Any]:
    from_block = max(0, int(latest_block) - max(1, int(lookback_blocks)))
    logs = w3.eth.get_logs(
        {
            "address": Web3.to_checksum_address(pool_manager),
            "fromBlock": from_block,
            "toBlock": int(latest_block),
            "topics": [V4_SWAP_TOPIC],
        }
    )
    active = {
        (log["topics"][1].hex() if hasattr(log["topics"][1], "hex") else str(log["topics"][1]))
        for log in logs
        if len(log.get("topics") or []) > 1
    }
    for pool in pools:
        pool["recentSwapActive"] = str(pool.get("poolId") or "") in active
        if pool["recentSwapActive"]:
            pool["status"] = "initialized_pool_recent_swap"
    return {
        "recentSwapLookbackBlocks": int(lookback_blocks),
        "recentSwapLogCount": len(logs),
        "recentSwapPoolCount": len(active),
    }


def keep_token_addresses_from_payload(payload: dict[str, Any]) -> set[str] | None:
    """Return lower-case token addresses from an ETH-pair candidate payload."""
    rows = list(payload.get("ycZc") or []) if isinstance(payload, dict) else []
    kept: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        raw = str(row.get("address") or "").strip().lower()
        if raw.startswith("0x") and len(raw) == 42 and raw != ZERO_ADDRESS.lower():
            kept.add(raw)
    return kept or None


def build_v4_pool_index_from_initialize_logs(
    *,
    network: str,
    chain_id: int,
    pool_manager: str,
    rpc_url: str,
    schema_version: str,
    include_currency_metadata: bool,
    from_block: int = 0,
    to_block: int | None = None,
    chunk_size: int = 100_000,
    pool_limit: int = 0,
    non_eth_only: bool = False,
    include_metadata: bool = False,
    keep_token_addresses: set[str] | None = None,
    mark_recent_swaps: bool = True,
    recent_swap_lookback_blocks: int = 20_000,
    rpc_connect_timeout: float = 20.0,
    rpc_connect_attempts: int = 3,
    rpc_user_agent: str = "flashloan-robinhood-v4/1.0",
) -> dict[str, Any]:
    w3 = _connect_rpc(
        rpc_url,
        user_agent=rpc_user_agent,
        timeout_seconds=rpc_connect_timeout,
        attempts=rpc_connect_attempts,
    )
    actual_chain_id = int(w3.eth.chain_id)
    if actual_chain_id != int(chain_id):
        raise ValueError(f"RPC returned chainId {actual_chain_id}, expected {chain_id}")
    latest_block = int(w3.eth.block_number)
    resolved_to_block = latest_block if to_block is None else int(to_block)
    logs = _get_logs_chunked(
        w3,
        pool_manager=pool_manager,
        topic=V4_INITIALIZE_TOPIC,
        from_block=max(0, int(from_block)),
        to_block=resolved_to_block,
        chunk_size=max(1, int(chunk_size)),
        min_chunk_size=1_000,
    )
    metadata_cache: dict[str, dict[str, Any]] = {}
    pools: list[dict[str, Any]] = []
    pool_keys: list[dict[str, Any]] = []
    seen_pool_ids: set[str] = set()
    eth_related_count = 0
    non_eth_count = 0
    skipped_non_relevant_count = 0
    for log in logs:
        pool = _pool_row_from_initialize_log(
            w3,
            log,
            pool_manager=pool_manager,
            include_metadata=include_metadata,
            include_currency_metadata=include_currency_metadata,
            metadata_cache=metadata_cache,
        )
        pool_id = str(pool.get("poolId") or "").lower()
        if not pool_id or pool_id in seen_pool_ids:
            continue
        seen_pool_ids.add(pool_id)
        is_eth_related = (
            str(pool.get("token0") or "").lower() == ZERO_ADDRESS.lower()
            or str(pool.get("token1") or "").lower() == ZERO_ADDRESS.lower()
        )
        if is_eth_related:
            eth_related_count += 1
        else:
            non_eth_count += 1
        if non_eth_only and is_eth_related:
            continue
        if keep_token_addresses and not is_eth_related:
            token0 = str(pool.get("token0") or "").lower()
            token1 = str(pool.get("token1") or "").lower()
            if token0 not in keep_token_addresses and token1 not in keep_token_addresses:
                skipped_non_relevant_count += 1
                continue
        pool_keys.append(pool["poolKey"])
        pools.append(pool)
        if pool_limit > 0 and len(pools) >= int(pool_limit):
            break
    activity = {}
    if mark_recent_swaps and pools:
        activity = _mark_recent_swap_activity(
            w3,
            pools,
            pool_manager=pool_manager,
            latest_block=latest_block,
            lookback_blocks=max(1, int(recent_swap_lookback_blocks)),
        )
    return {
        "schemaVersion": schema_version,
        "network": network,
        "chainId": int(chain_id),
        "poolManager": Web3.to_checksum_address(pool_manager),
        "generatedAt": _stamp(),
        "generatedBlockNumber": latest_block,
        "fromBlock": max(0, int(from_block)),
        "toBlock": resolved_to_block,
        "scanChunkSize": int(chunk_size),
        "poolLimit": int(pool_limit),
        "nonEthOnly": bool(non_eth_only),
        "includeMetadata": bool(include_metadata),
        "initializeLogCount": len(logs),
        "initializedPoolCount": len(seen_pool_ids),
        "ethRelatedPoolCount": eth_related_count,
        "nonEthPoolCount": non_eth_count,
        "skippedNonRelevantPoolCount": skipped_non_relevant_count,
        "keepTokenAddressCount": len(keep_token_addresses) if keep_token_addresses else 0,
        "selectedPoolCount": len(pools),
        "discoveryMethod": "poolmanager_initialize_log_index",
        "rpcUrl": rpc_url,
        **activity,
        "poolKeys": pool_keys,
        "pools": pools,
        "blockingReasons": [],
    }


def write_v4_pool_index_from_initialize_logs(
    *,
    output_path: Path | None = None,
    default_output_path: Path | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    payload = build_v4_pool_index_from_initialize_logs(**kwargs)
    resolved = output_path or default_output_path
    if resolved is None:
        raise ValueError("v4_pool_index data file is not configured")
    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {**payload, "outputPath": str(resolved)}


def load_robinhood_v4_pool_index(
    *,
    path: Path | None,
    network: str,
    chain_id: int,
) -> dict[str, Any]:
    if path is None:
        return {
            "ok": False,
            "status": "v4_pool_index_not_configured",
            "source": None,
            "pools": [],
            "pool_keys": [],
            "blocking_reasons": ["v4_pool_index_data_file_not_configured"],
        }
    payload = _read_json(path)
    if not payload:
        return {
            "ok": False,
            "status": "v4_pool_index_missing",
            "source": str(path),
            "pools": [],
            "pool_keys": [],
            "blocking_reasons": ["v4_pool_index_data_file_missing"],
        }
    if payload.get("_error"):
        return {
            "ok": False,
            "status": "v4_pool_index_invalid",
            "source": str(path),
            "pools": [],
            "pool_keys": [],
            "blocking_reasons": [str(payload["_error"])],
        }
    scope_ok = (
        str(payload.get("network") or "").strip().lower() == network
        and int(payload.get("chainId") or payload.get("chain_id") or 0) == int(chain_id)
    )
    pools = list(payload.get("pools") or []) if scope_ok else []
    pool_keys = list(payload.get("poolKeys") or payload.get("pool_keys") or []) if scope_ok else []
    return {
        "ok": bool(scope_ok and pools),
        "status": "v4_pool_index_available" if scope_ok and pools else "v4_pool_index_unavailable",
        "source": str(path),
        "payload": payload,
        "pools": pools,
        "pool_keys": pool_keys,
        "pool_manager": payload.get("poolManager") or payload.get("pool_manager"),
        "block_number": payload.get("generatedBlockNumber") or payload.get("block_number"),
        "refreshed_at": payload.get("generatedAt") or payload.get("fetched_at"),
        "blocking_reasons": [
            str(item)
            for item in payload.get("blockingReasons") or payload.get("blocking_reasons") or []
        ],
    }


__all__ = [
    "ERC20_ABI",
    "V4_INITIALIZE_TOPIC",
    "V4_SWAP_TOPIC",
    "ZERO_ADDRESS",
    "build_v4_pool_index_from_initialize_logs",
    "keep_token_addresses_from_payload",
    "load_robinhood_v4_pool_index",
    "write_v4_pool_index_from_initialize_logs",
]
