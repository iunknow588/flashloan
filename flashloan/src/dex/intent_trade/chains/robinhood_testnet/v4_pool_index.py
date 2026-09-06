from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from web3 import Web3

from core.chain_data_registry import chain_data_file_path
from dex.intent_trade.chains.robinhood_testnet.v4_pool_liquidity import (
    ERC20_ABI,
    ROBINHOOD_TESTNET_CHAIN_ID,
    ROBINHOOD_TESTNET_NETWORK,
    ROBINHOOD_TESTNET_RPC_URL,
    ROBINHOOD_TESTNET_V4_POOL_MANAGER,
    V4_INITIALIZE_TOPIC,
    V4_SWAP_TOPIC,
    ZERO_ADDRESS,
    _as_list,
    _currency_metadata,
    _read_json,
    _rpc_url_from_env_or_default,
    _topic_address,
    _web3,
)


def default_v4_pool_index_path() -> Path | None:
    return chain_data_file_path(ROBINHOOD_TESTNET_NETWORK, "v4_pool_index")


def _chain_config_path() -> Path:
    return Path(__file__).resolve().parents[3] / "chains" / ROBINHOOD_TESTNET_NETWORK / "chain.json"


def _chain_contract_address(key: str, fallback: str = "") -> str:
    payload = _read_json(_chain_config_path())
    contracts = payload.get("contracts") if isinstance(payload.get("contracts"), dict) else {}
    return str(contracts.get(key) or fallback).strip()


def _stamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _erc20_symbol_decimals(w3: Web3, address: str, metadata_cache: dict[str, dict[str, Any]]) -> dict[str, Any]:
    normalized = str(address or "").lower()
    if normalized == ZERO_ADDRESS.lower():
        return {"address": ZERO_ADDRESS, "symbol": "ETH", "decimals": 18, "metadataOk": True}
    if normalized in metadata_cache:
        return metadata_cache[normalized]
    checksum = Web3.to_checksum_address(address)
    contract = w3.eth.contract(address=checksum, abi=ERC20_ABI)
    symbol = ""
    decimals = None
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
    row = {"address": checksum, "symbol": symbol or checksum[:8], "decimals": decimals, "metadataOk": metadata_ok}
    metadata_cache[normalized] = row
    return row


def _pool_row_from_initialize_log(
    w3: Web3,
    log: Any,
    *,
    pool_manager: str,
    include_metadata: bool,
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
        metadata0 = _erc20_symbol_decimals(w3, currency0, metadata_cache)
        metadata1 = _erc20_symbol_decimals(w3, currency1, metadata_cache)
    else:
        metadata0 = {"address": currency0, "symbol": "ETH" if currency0.lower() == ZERO_ADDRESS.lower() else currency0[:8], "decimals": 18 if currency0.lower() == ZERO_ADDRESS.lower() else None, "metadataOk": currency0.lower() == ZERO_ADDRESS.lower()}
        metadata1 = {"address": currency1, "symbol": "ETH" if currency1.lower() == ZERO_ADDRESS.lower() else currency1[:8], "decimals": 18 if currency1.lower() == ZERO_ADDRESS.lower() else None, "metadataOk": currency1.lower() == ZERO_ADDRESS.lower()}
    token0_symbol = str(metadata0.get("symbol") or currency0[:8]).strip()
    token1_symbol = str(metadata1.get("symbol") or currency1[:8]).strip()
    return {
        "protocol": "uniswap_v4",
        "poolId": pool_id,
        "poolKeyDecoded": True,
        "poolKey": {
            "poolId": pool_id,
            "poolKeyDecoded": True,
            "currency0": currency0,
            "currency1": currency1,
            "token0": currency0,
            "token1": currency1,
            "token0Symbol": token0_symbol,
            "token1Symbol": token1_symbol,
            "token0Decimals": metadata0.get("decimals"),
            "token1Decimals": metadata1.get("decimals"),
            "fee": int(fee),
            "tickSpacing": int(tick_spacing),
            "hooks": Web3.to_checksum_address(hooks) if int(str(hooks), 16) else ZERO_ADDRESS,
            "sqrtPriceX96": str(sqrt_price_x96),
            "tick": int(tick),
            "initializeBlockNumber": int(log["blockNumber"]),
            "initializeTransactionHash": log["transactionHash"].hex()
            if hasattr(log["transactionHash"], "hex")
            else str(log["transactionHash"]),
            "currency0Metadata": metadata0,
            "currency1Metadata": metadata1,
        },
        "poolManager": pool_manager,
        "symbolPair": f"{token0_symbol}:{token1_symbol}",
        "token0": currency0,
        "token1": currency1,
        "token0Symbol": token0_symbol,
        "token1Symbol": token1_symbol,
        "token0Decimals": metadata0.get("decimals"),
        "token1Decimals": metadata1.get("decimals"),
        "fee": int(fee),
        "tickSpacing": int(tick_spacing),
        "hooks": Web3.to_checksum_address(hooks) if int(str(hooks), 16) else ZERO_ADDRESS,
        "sqrtPriceX96": str(sqrt_price_x96),
        "tick": int(tick),
        "initializeBlockNumber": int(log["blockNumber"]),
        "initializeTransactionHash": log["transactionHash"].hex()
        if hasattr(log["transactionHash"], "hex")
        else str(log["transactionHash"]),
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
    min_chunk_size: int = 100_000,
) -> list[Any]:
    logs: list[Any] = []
    cursor = max(0, int(from_block))
    end = max(cursor, int(to_block))
    step = max(1, int(chunk_size))
    while cursor <= end:
        chunk_end = min(end, cursor + step - 1)
        try:
            logs.extend(
                w3.eth.get_logs(
                    {
                        "address": Web3.to_checksum_address(pool_manager),
                        "fromBlock": cursor,
                        "toBlock": chunk_end,
                        "topics": [topic],
                    }
                )
            )
            cursor = chunk_end + 1
            step = max(step, int(chunk_size))
        except Exception:
            if step <= min_chunk_size:
                raise
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
    active_pool_ids = {
        (log["topics"][1].hex() if hasattr(log["topics"][1], "hex") else str(log["topics"][1]))
        for log in logs
        if len(log.get("topics") or []) > 1
    }
    for pool in pools:
        active = str(pool.get("poolId") or "") in active_pool_ids
        pool["recentSwapActive"] = active
        if active:
            pool["status"] = "initialized_pool_recent_swap"
    return {
        "recentSwapLookbackBlocks": int(lookback_blocks),
        "recentSwapLogCount": len(logs),
        "recentSwapPoolCount": len(active_pool_ids),
    }


def build_v4_pool_index_from_initialize_logs(
    *,
    rpc_url: str | None = None,
    pool_manager: str | None = None,
    from_block: int = 0,
    to_block: int | None = None,
    chunk_size: int = 5_000_000,
    pool_limit: int = 0,
    non_eth_only: bool = False,
    include_metadata: bool = False,
    mark_recent_swaps: bool = True,
    recent_swap_lookback_blocks: int = 20_000,
) -> dict[str, Any]:
    resolved_rpc_url = rpc_url or _rpc_url_from_env_or_default() or ROBINHOOD_TESTNET_RPC_URL
    resolved_pool_manager = pool_manager or _chain_contract_address("v4_pool_manager", ROBINHOOD_TESTNET_V4_POOL_MANAGER)
    w3 = _web3(resolved_rpc_url)
    chain_id = int(w3.eth.chain_id)
    if chain_id != ROBINHOOD_TESTNET_CHAIN_ID:
        raise ValueError(f"RPC returned chainId {chain_id}, expected {ROBINHOOD_TESTNET_CHAIN_ID}")
    latest_block = int(w3.eth.block_number)
    resolved_to_block = latest_block if to_block is None else int(to_block)
    logs = _get_logs_chunked(
        w3,
        pool_manager=resolved_pool_manager,
        topic=V4_INITIALIZE_TOPIC,
        from_block=from_block,
        to_block=resolved_to_block,
        chunk_size=chunk_size,
    )
    metadata_cache: dict[str, dict[str, Any]] = {}
    pools: list[dict[str, Any]] = []
    pool_keys: list[dict[str, Any]] = []
    seen_pool_ids: set[str] = set()
    eth_related_count = 0
    non_eth_count = 0
    for log in logs:
        row = _pool_row_from_initialize_log(
            w3,
            log,
            pool_manager=resolved_pool_manager,
            include_metadata=include_metadata,
            metadata_cache=metadata_cache,
        )
        pool_id = str(row.get("poolId") or "")
        if pool_id in seen_pool_ids:
            continue
        seen_pool_ids.add(pool_id)
        is_eth_related = row["token0"].lower() == ZERO_ADDRESS.lower() or row["token1"].lower() == ZERO_ADDRESS.lower()
        if is_eth_related:
            eth_related_count += 1
        else:
            non_eth_count += 1
        if non_eth_only and is_eth_related:
            continue
        pool_keys.append(row["poolKey"])
        pools.append(row)
        if pool_limit > 0 and len(pools) >= int(pool_limit):
            break
    activity = {}
    if mark_recent_swaps and pools:
        activity = _mark_recent_swap_activity(
            w3,
            pools,
            pool_manager=resolved_pool_manager,
            latest_block=latest_block,
            lookback_blocks=recent_swap_lookback_blocks,
        )
    return {
        "schemaVersion": "robinhood-testnet-v4-pool-index-v1",
        "network": ROBINHOOD_TESTNET_NETWORK,
        "chainId": ROBINHOOD_TESTNET_CHAIN_ID,
        "poolManager": resolved_pool_manager,
        "generatedAt": _stamp(),
        "generatedBlockNumber": latest_block,
        "fromBlock": int(from_block),
        "toBlock": resolved_to_block,
        "scanChunkSize": int(chunk_size),
        "poolLimit": int(pool_limit),
        "nonEthOnly": bool(non_eth_only),
        "includeMetadata": bool(include_metadata),
        "initializeLogCount": len(logs),
        "initializedPoolCount": len(seen_pool_ids),
        "ethRelatedPoolCount": eth_related_count,
        "nonEthPoolCount": non_eth_count,
        "selectedPoolCount": len(pools),
        "discoveryMethod": "poolmanager_initialize_log_index",
        "rpcUrl": resolved_rpc_url,
        **activity,
        "poolKeys": pool_keys,
        "pools": pools,
        "blockingReasons": [],
    }


def write_v4_pool_index_from_initialize_logs(output_path: Path | None = None, **kwargs: Any) -> dict[str, Any]:
    payload = build_v4_pool_index_from_initialize_logs(**kwargs)
    resolved = output_path or default_v4_pool_index_path()
    if resolved is None:
        raise ValueError("v4_pool_index data file is not configured")
    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {**payload, "outputPath": str(resolved)}


def load_robinhood_testnet_v4_pool_index(path: Path | None = None) -> dict[str, Any]:
    resolved = path or default_v4_pool_index_path()
    if resolved is None:
        return {
            "ok": False,
            "status": "v4_pool_index_not_configured",
            "source": None,
            "pools": [],
            "pool_keys": [],
            "blocking_reasons": ["v4_pool_index_data_file_not_configured"],
        }
    payload = _read_json(resolved)
    if not payload:
        return {
            "ok": False,
            "status": "v4_pool_index_missing",
            "source": str(resolved),
            "pools": [],
            "pool_keys": [],
            "blocking_reasons": ["v4_pool_index_data_file_missing"],
        }
    if payload.get("_error"):
        return {
            "ok": False,
            "status": "v4_pool_index_invalid",
            "source": str(resolved),
            "pools": [],
            "pool_keys": [],
            "blocking_reasons": [str(payload["_error"])],
        }
    scope_ok = str(payload.get("network") or "").strip().lower() == ROBINHOOD_TESTNET_NETWORK and int(payload.get("chainId") or payload.get("chain_id") or 0) == ROBINHOOD_TESTNET_CHAIN_ID
    pools = _as_list(payload.get("pools"))
    pool_keys = _as_list(payload.get("poolKeys") or payload.get("pool_keys"))
    return {
        "ok": bool(scope_ok and pools),
        "status": "v4_pool_index_available" if scope_ok and pools else "v4_pool_index_unavailable",
        "source": str(resolved),
        "payload": payload,
        "pools": pools if scope_ok else [],
        "pool_keys": pool_keys if scope_ok else [],
        "pool_manager": payload.get("poolManager") or payload.get("pool_manager"),
        "block_number": payload.get("generatedBlockNumber") or payload.get("block_number"),
        "refreshed_at": payload.get("generatedAt") or payload.get("fetched_at"),
        "blocking_reasons": [str(item) for item in payload.get("blockingReasons") or payload.get("blocking_reasons") or []],
    }


__all__ = [
    "build_v4_pool_index_from_initialize_logs",
    "default_v4_pool_index_path",
    "load_robinhood_testnet_v4_pool_index",
    "write_v4_pool_index_from_initialize_logs",
]
