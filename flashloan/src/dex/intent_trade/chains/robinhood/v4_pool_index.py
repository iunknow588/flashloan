"""Robinhood mainnet Uniswap V4 PoolKey index.

The event decoder is shared with the testnet implementation, but the chain
scope, RPC, contract address, and output file are resolved from the mainnet
chain registry.  Keeping this module separate prevents a mainnet refresh from
ever writing testnet-scoped evidence.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import time
from typing import Any

from web3 import Web3

from core.chain_data_registry import chain_data_file_path, chain_data_by_network

from eth_utils import event_signature_to_log_topic


ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"
V4_INITIALIZE_TOPIC = event_signature_to_log_topic(
    "Initialize(bytes32,address,address,uint24,int24,address,uint160,int24)"
).hex()
V4_INITIALIZE_TOPIC = "0x" + V4_INITIALIZE_TOPIC
V4_SWAP_TOPIC = "0x" + event_signature_to_log_topic(
    "Swap(bytes32,address,int128,int128,uint160,uint128,int24,uint24)"
).hex()
ERC20_ABI = [
    {"inputs": [], "name": "decimals", "outputs": [{"type": "uint8"}], "stateMutability": "view", "type": "function"},
    {"inputs": [], "name": "symbol", "outputs": [{"type": "string"}], "stateMutability": "view", "type": "function"},
]


NETWORK = "robinhood"
_CHAIN_ROW = chain_data_by_network(NETWORK) or {}
if not _CHAIN_ROW.get("chain_id"):
    raise RuntimeError(f"{NETWORK} chain configuration is missing chain_id")
CHAIN_ID = int(_CHAIN_ROW["chain_id"])


def _stamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _chain_row() -> dict[str, Any]:
    row = chain_data_by_network(NETWORK) or {}
    return row if isinstance(row, dict) else {}


def _contract_address(key: str) -> str:
    contracts = _chain_row().get("contracts")
    contracts = contracts if isinstance(contracts, dict) else {}
    value = str(contracts.get(key) or "").strip()
    if not value:
        raise ValueError(f"Robinhood mainnet contract {key} is not configured")
    return Web3.to_checksum_address(value)


def default_v4_pool_index_path() -> Path | None:
    return chain_data_file_path(NETWORK, "v4_pool_index", chain_id=CHAIN_ID)


def _topic_address(topic: Any) -> str:
    raw = bytes(topic) if not isinstance(topic, str) else bytes.fromhex(topic[2:] if topic.startswith("0x") else topic)
    return Web3.to_checksum_address("0x" + raw[-20:].hex())


def _erc20_metadata(w3: Web3, address: str, cache: dict[str, dict[str, Any]]) -> dict[str, Any]:
    if address.lower() == ZERO_ADDRESS.lower():
        return {"address": ZERO_ADDRESS, "symbol": "ETH", "decimals": 18, "metadataOk": True}
    key = address.lower()
    if key in cache:
        return cache[key]
    contract = w3.eth.contract(address=Web3.to_checksum_address(address), abi=ERC20_ABI)
    symbol, decimals, ok = "", None, True
    try:
        symbol = str(contract.functions.symbol().call()).strip()
    except Exception:
        ok = False
    try:
        decimals = int(contract.functions.decimals().call())
    except Exception:
        ok = False
    row = {"address": Web3.to_checksum_address(address), "symbol": symbol or address[:10], "decimals": decimals, "metadataOk": ok}
    cache[key] = row
    return row


def _pool_row_from_initialize_log(w3: Web3, log: Any, *, pool_manager: str, include_metadata: bool, metadata_cache: dict[str, dict[str, Any]]) -> dict[str, Any]:
    topics = list(log["topics"])
    pool_id = topics[1].hex() if hasattr(topics[1], "hex") else str(topics[1])
    fee, tick_spacing, hooks, sqrt_price_x96, tick = w3.codec.decode(["uint24", "int24", "address", "uint160", "int24"], bytes(log["data"]))
    currency0, currency1 = _topic_address(topics[2]), _topic_address(topics[3])
    metadata0 = _erc20_metadata(w3, currency0, metadata_cache) if include_metadata else {"address": currency0, "symbol": "ETH" if currency0.lower() == ZERO_ADDRESS.lower() else currency0[:10], "decimals": 18 if currency0.lower() == ZERO_ADDRESS.lower() else None, "metadataOk": currency0.lower() == ZERO_ADDRESS.lower()}
    metadata1 = _erc20_metadata(w3, currency1, metadata_cache) if include_metadata else {"address": currency1, "symbol": "ETH" if currency1.lower() == ZERO_ADDRESS.lower() else currency1[:10], "decimals": 18 if currency1.lower() == ZERO_ADDRESS.lower() else None, "metadataOk": currency1.lower() == ZERO_ADDRESS.lower()}
    tx_hash = log["transactionHash"].hex() if hasattr(log["transactionHash"], "hex") else str(log["transactionHash"])
    hooks_address = Web3.to_checksum_address(hooks) if int(str(hooks), 16) else ZERO_ADDRESS
    key = {"poolId": pool_id, "poolKeyDecoded": True, "currency0": currency0, "currency1": currency1, "token0": currency0, "token1": currency1, "token0Symbol": metadata0["symbol"], "token1Symbol": metadata1["symbol"], "token0Decimals": metadata0["decimals"], "token1Decimals": metadata1["decimals"], "fee": int(fee), "tickSpacing": int(tick_spacing), "hooks": hooks_address, "sqrtPriceX96": str(sqrt_price_x96), "tick": int(tick), "initializeBlockNumber": int(log["blockNumber"]), "initializeTransactionHash": tx_hash}
    return {"protocol": "uniswap_v4", "poolId": pool_id, "poolKeyDecoded": True, "poolKey": key, "poolManager": pool_manager, "symbolPair": f"{metadata0['symbol']}:{metadata1['symbol']}", "token0": currency0, "token1": currency1, "token0Symbol": metadata0["symbol"], "token1Symbol": metadata1["symbol"], "token0Decimals": metadata0["decimals"], "token1Decimals": metadata1["decimals"], "fee": int(fee), "tickSpacing": int(tick_spacing), "hooks": hooks_address, "sqrtPriceX96": str(sqrt_price_x96), "tick": int(tick), "initializeBlockNumber": int(log["blockNumber"]), "initializeTransactionHash": tx_hash, "liquidity": "", "tvlUsd": 0, "status": "initialized_pool", "depthStatus": "initialized_unpriced", "source": "poolmanager_initialize_logs"}


def _get_logs_chunked(w3: Web3, *, pool_manager: str, topic: str, from_block: int, to_block: int, chunk_size: int, min_chunk_size: int = 1_000) -> list[Any]:
    logs: list[Any] = []
    cursor, step = max(0, int(from_block)), max(1, int(chunk_size))
    while cursor <= int(to_block):
        end = min(int(to_block), cursor + step - 1)
        try:
            logs.extend(w3.eth.get_logs({"address": pool_manager, "fromBlock": cursor, "toBlock": end, "topics": [topic]}))
            cursor = end + 1
        except Exception:
            if step <= min_chunk_size:
                raise
            step = max(min_chunk_size, step // 2)
    return logs


def _connect_rpc(resolved_rpc_url: str) -> Web3:
    last_exc: Exception | None = None
    for attempt in range(3):
        provider = Web3.HTTPProvider(
            resolved_rpc_url,
            request_kwargs={
                "timeout": 20,
                "headers": {
                    "Content-Type": "application/json",
                    "User-Agent": "flashloan-robinhood-mainnet-v4/1.0",
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
    raise RuntimeError("Robinhood mainnet RPC unavailable") from last_exc


def _mark_recent_swap_activity(w3: Web3, pools: list[dict[str, Any]], *, pool_manager: str, latest_block: int, lookback_blocks: int) -> dict[str, Any]:
    logs = w3.eth.get_logs({"address": pool_manager, "fromBlock": max(0, latest_block - lookback_blocks), "toBlock": latest_block, "topics": [V4_SWAP_TOPIC]})
    active = {log["topics"][1].hex() if hasattr(log["topics"][1], "hex") else str(log["topics"][1]) for log in logs if len(log.get("topics") or []) > 1}
    for pool in pools:
        if str(pool.get("poolId") or "") in active:
            pool["recentSwapActive"] = True
            pool["status"] = "initialized_pool_recent_swap"
        else:
            pool["recentSwapActive"] = False
    return {"recentSwapLookbackBlocks": int(lookback_blocks), "recentSwapLogCount": len(logs), "recentSwapPoolCount": len(active)}


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _iter_observation_pool_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(row: Any) -> None:
        if not isinstance(row, dict):
            return
        pool_id = str(row.get("pool") or row.get("poolId") or "").strip().lower()
        if not pool_id or pool_id in seen:
            return
        seen.add(pool_id)
        rows.append(row)

    for sequence in payload.get("observationSequences") or []:
        if isinstance(sequence, dict):
            for step in sequence.get("routePools") or []:
                add(step)
            for pool_id in sequence.get("poolIds") or []:
                add({"pool": pool_id})
            if sequence.get("bridgePool"):
                add(sequence.get("bridgePool"))
    for summary in payload.get("closedLoopRouteSummaries") or []:
        if isinstance(summary, dict):
            for pool_id in summary.get("poolIds") or []:
                add({"pool": pool_id})
    return rows


def _observation_pool_rows() -> list[dict[str, Any]]:
    candidates: list[Path] = []
    # v4_pool_index.py lives under src/dex/intent_trade/chains/robinhood;
    # runtime evidence is rooted at src/runtime.
    base = Path(__file__).resolve().parents[4] / "runtime" / "market_observation" / NETWORK / str(CHAIN_ID)
    for rel in ("uniswap_v4", "all", "uniswap_v2+uniswap_v3+uniswap_v4"):
        path = base / rel / "observation.json"
        if path.exists():
            candidates.append(path)
    merged: dict[str, dict[str, Any]] = {}
    for path in candidates:
        for row in _iter_observation_pool_rows(_read_json(path)):
            protocol = str(row.get("protocol") or row.get("dex") or "").strip().lower()
            if protocol and protocol != "uniswap_v4":
                continue
            key = str(row.get("pool") or row.get("poolId") or "").lower()
            if key:
                merged[key] = {**row, "pool": key}
    return list(merged.values())


def _decode_initialize_log(w3: Web3, *, pool_manager: str, pool_id: str, to_block: int) -> dict[str, Any] | None:
    logs = w3.eth.get_logs(
        {
            "address": pool_manager,
            "fromBlock": 0,
            "toBlock": int(to_block),
            "topics": [V4_INITIALIZE_TOPIC, pool_id],
        }
    )
    if not logs:
        return None
    log = logs[0]
    return _pool_row_from_initialize_log(
        w3,
        log,
        pool_manager=pool_manager,
        include_metadata=False,
        metadata_cache={},
    )


def build_v4_pool_index_from_observation_pool_ids(
    *,
    rpc_url: str | None = None,
    pool_manager: str | None = None,
    pool_ids: list[str] | None = None,
    to_block: int | None = None,
) -> dict[str, Any]:
    row = _chain_row()
    resolved_rpc_url = rpc_url or str(row.get("rpc_url") or "").strip()
    if not resolved_rpc_url:
        raise ValueError("Robinhood mainnet RPC URL is not configured")
    resolved_pool_manager = Web3.to_checksum_address(pool_manager or _contract_address("v4_pool_manager"))
    w3 = _connect_rpc(resolved_rpc_url)
    actual_chain_id = int(w3.eth.chain_id)
    if actual_chain_id != CHAIN_ID:
        raise ValueError(f"RPC returned chainId {actual_chain_id}, expected {CHAIN_ID}")
    latest_block = int(w3.eth.block_number)
    resolved_to_block = latest_block if to_block is None else int(to_block)

    requested = list(pool_ids or [])
    if not requested:
        requested = [str(p.get("pool") or p.get("poolId") or "").strip() for p in _observation_pool_rows()]
    requested = [item for item in requested if str(item).startswith("0x")]

    pools: list[dict[str, Any]] = []
    pool_keys: list[dict[str, Any]] = []
    errors: list[str] = []
    seen: set[str] = set()
    for raw_pool_id in requested:
        pool_id = raw_pool_id.lower()
        if pool_id in seen:
            continue
        seen.add(pool_id)
        try:
            decoded = _decode_initialize_log(
                w3,
                pool_manager=resolved_pool_manager,
                pool_id=pool_id,
                to_block=resolved_to_block,
            )
        except Exception as exc:
            errors.append(f"{pool_id}:{str(exc)[:160]}")
            decoded = None
        if decoded is None:
            continue
        pool_keys.append(decoded["poolKey"])
        pools.append(decoded)

    return {
        "schemaVersion": "robinhood-v4-pool-index-v1",
        "network": NETWORK,
        "chainId": CHAIN_ID,
        "poolManager": resolved_pool_manager,
        "generatedAt": _stamp(),
        "generatedBlockNumber": latest_block,
        "discoveryMethod": "poolmanager_initialize_logs_for_observation_pool_ids",
        "rpcUrl": resolved_rpc_url,
        "requestedPoolIdCount": len(seen),
        "decodedPoolKeyCount": len(pool_keys),
        "errorCount": len(errors),
        "errors": errors,
        "poolKeys": pool_keys,
        "pools": pools,
        "blockingReasons": errors[:20],
    }


def write_v4_pool_index_from_observation_pool_ids(output_path: Path | None = None, **kwargs: Any) -> dict[str, Any]:
    payload = build_v4_pool_index_from_observation_pool_ids(**kwargs)
    resolved = output_path or default_v4_pool_index_path()
    if resolved is None:
        raise ValueError("Robinhood mainnet v4_pool_index data file is not configured")
    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {**payload, "outputPath": str(resolved)}


def build_v4_pool_index_from_initialize_logs(
    *,
    rpc_url: str | None = None,
    pool_manager: str | None = None,
    from_block: int = 0,
    to_block: int | None = None,
    chunk_size: int = 100_000,
    pool_limit: int = 0,
    non_eth_only: bool = False,
    include_metadata: bool = False,
    mark_recent_swaps: bool = True,
    recent_swap_lookback_blocks: int = 20_000,
) -> dict[str, Any]:
    row = _chain_row()
    resolved_rpc_url = rpc_url or str(row.get("rpc_url") or "").strip()
    if not resolved_rpc_url:
        raise ValueError("Robinhood mainnet RPC URL is not configured")
    resolved_pool_manager = Web3.to_checksum_address(pool_manager or _contract_address("v4_pool_manager"))
    w3 = _connect_rpc(resolved_rpc_url)
    actual_chain_id = int(w3.eth.chain_id)
    if actual_chain_id != CHAIN_ID:
        raise ValueError(f"RPC returned chainId {actual_chain_id}, expected {CHAIN_ID}")
    latest_block = int(w3.eth.block_number)
    resolved_to_block = latest_block if to_block is None else int(to_block)
    logs = _get_logs_chunked(
        w3,
        pool_manager=resolved_pool_manager,
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
    for log in logs:
        pool = _pool_row_from_initialize_log(
            w3,
            log,
            pool_manager=resolved_pool_manager,
            include_metadata=include_metadata,
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
        pool_keys.append(pool["poolKey"])
        pools.append(pool)
        if pool_limit > 0 and len(pools) >= int(pool_limit):
            break
    activity = {}
    if mark_recent_swaps and pools:
        activity = _mark_recent_swap_activity(
            w3,
            pools,
            pool_manager=resolved_pool_manager,
            latest_block=latest_block,
            lookback_blocks=max(1, int(recent_swap_lookback_blocks)),
        )
    return {
        "schemaVersion": "robinhood-v4-pool-index-v1",
        "network": NETWORK,
        "chainId": CHAIN_ID,
        "poolManager": resolved_pool_manager,
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
        raise ValueError("Robinhood mainnet v4_pool_index data file is not configured")
    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {**payload, "outputPath": str(resolved)}


__all__ = [
    "build_v4_pool_index_from_initialize_logs",
    "build_v4_pool_index_from_observation_pool_ids",
    "default_v4_pool_index_path",
    "write_v4_pool_index_from_observation_pool_ids",
    "write_v4_pool_index_from_initialize_logs",
]
