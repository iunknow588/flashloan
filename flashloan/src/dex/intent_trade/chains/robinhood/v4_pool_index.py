"""Robinhood mainnet Uniswap V4 PoolKey index.

The event decoder is shared with the testnet implementation, but the chain
scope, RPC, contract address, and output file are resolved from the mainnet
chain registry.  Keeping this module separate prevents a mainnet refresh from
ever writing testnet-scoped evidence.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from decimal import Decimal, getcontext
import json
import os
from pathlib import Path
import time
from typing import Any

from web3 import Web3

from core.chain_data_registry import chain_data_file_path, chain_data_by_network

from eth_utils import event_signature_to_log_topic

from dex.intent_trade.chains.robinhood_common.v4_pool_index_core import (
    build_v4_directional_pool_backfill as _backfill_core,
    build_v4_pool_index_from_initialize_logs as _build_core,
    keep_token_addresses_from_payload,
    load_robinhood_v4_pool_index as _load_core,
    write_v4_pool_index_from_initialize_logs as _write_core,
)
from dex.intent_trade.chains.robinhood_common.v4_sqlite_view import (
    load_v4_pool_index_payload as _load_db_index_payload,
)
from dex.intent_trade.chains.robinhood.v4_quote_adapter import (
    _connect_chain_rpc,
    _read_live_pool_state_with_w3,
)
from dex.intent_trade.chains.robinhood.config import rpc_urls_from_env


getcontext().prec = 80


ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"
V4_DYNAMIC_FEE_FLAG = 8388608
RECENT_SWAP_LOOKBACK_DAYS = 60
ROBINHOOD_MAINNET_BLOCKS_PER_DAY = 43_200
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


Q96 = Decimal(2) ** 96
DEFAULT_ETH_PRICE_USD = Decimal(str(os.getenv("ROBINHOOD_MAINNET_ETH_PRICE_USD", "3000")))


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
    dynamic_fee = bool(fee) and bool(int(fee) & V4_DYNAMIC_FEE_FLAG)
    key = {"poolId": pool_id, "poolKeyDecoded": True, "currency0": currency0, "currency1": currency1, "token0": currency0, "token1": currency1, "token0Symbol": metadata0["symbol"], "token1Symbol": metadata1["symbol"], "token0Decimals": metadata0["decimals"], "token1Decimals": metadata1["decimals"], "fee": int(fee), "tickSpacing": int(tick_spacing), "hooks": hooks_address, "sqrtPriceX96": str(sqrt_price_x96), "tick": int(tick), "initializeBlockNumber": int(log["blockNumber"]), "initializeTransactionHash": tx_hash}
    return {"protocol": "uniswap_v4", "poolId": pool_id, "poolKeyDecoded": True, "poolKey": key, "poolManager": pool_manager, "symbolPair": f"{metadata0['symbol']}:{metadata1['symbol']}", "token0": currency0, "token1": currency1, "token0Symbol": metadata0["symbol"], "token1Symbol": metadata1["symbol"], "token0Decimals": metadata0["decimals"], "token1Decimals": metadata1["decimals"], "fee": int(fee), "dynamicFee": dynamic_fee, "feeLabel": "dynamic" if dynamic_fee else int(fee), "usableForObservation": not dynamic_fee, "tickSpacing": int(tick_spacing), "hooks": hooks_address, "sqrtPriceX96": str(sqrt_price_x96), "tick": int(tick), "initializeBlockNumber": int(log["blockNumber"]), "initializeTransactionHash": tx_hash, "liquidity": "", "tvlUsd": 0, "status": "initialized_pool", "depthStatus": "initialized_unpriced", "source": "poolmanager_initialize_logs"}


def _get_logs_chunked(w3: Web3, *, pool_manager: str, topic: str, from_block: int, to_block: int, chunk_size: int, min_chunk_size: int = 1_000) -> list[Any]:
    logs: list[Any] = []
    cursor, step = max(0, int(from_block)), max(1, int(chunk_size))
    while cursor <= int(to_block):
        end = min(int(to_block), cursor + step - 1)
        try:
            logs.extend(w3.eth.get_logs({"address": pool_manager, "fromBlock": cursor, "toBlock": end, "topics": [topic]}))
            cursor = end + 1
        except Exception:
            import sys
            exc_type, exc_value, _ = sys.exc_info()
            message = str(exc_value or "").lower()
            if step <= min_chunk_size and "exceeds limit" not in message and "limit" not in message:
                raise
            if "exceeds limit" in message or "limit" in message:
                step = max(1, step // 2)
            else:
                step = max(min_chunk_size, step // 2)
    return logs


def _connect_rpc(resolved_rpc_url: str) -> Web3:
    last_exc: Exception | None = None
    for attempt in range(3):
        provider = Web3.HTTPProvider(
            resolved_rpc_url,
            request_kwargs={
                "timeout": 20,
                "verify": False,
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


def _mark_recent_swap_activity(w3: Web3, pools: list[dict[str, Any]], *, pool_manager: str, latest_block: int, lookback_blocks: int, chunk_size: int = 50_000, min_chunk_size: int = 1_000) -> dict[str, Any]:
    logs = _get_logs_chunked(
        w3,
        pool_manager=pool_manager,
        topic=V4_SWAP_TOPIC,
        from_block=max(0, int(latest_block) - max(1, int(lookback_blocks))),
        to_block=int(latest_block),
        chunk_size=max(1, int(chunk_size)),
        min_chunk_size=max(1, int(min_chunk_size)),
    )
    active = {log["topics"][1].hex() if hasattr(log["topics"][1], "hex") else str(log["topics"][1]) for log in logs if len(log.get("topics") or []) > 1}
    for pool in pools:
        if str(pool.get("poolId") or "") in active:
            pool["recentSwapActive"] = True
            pool["status"] = "initialized_pool_recent_swap"
        else:
            pool["recentSwapActive"] = False
    return {"recentSwapLookbackBlocks": int(lookback_blocks), "recentSwapLogCount": len(logs), "recentSwapPoolCount": len(active)}


def _lookback_blocks_for_days(days: int) -> int:
    days = max(1, int(days))
    blocks_per_day = int(os.getenv("ROBINHOOD_MAINNET_BLOCKS_PER_DAY", str(ROBINHOOD_MAINNET_BLOCKS_PER_DAY)) or ROBINHOOD_MAINNET_BLOCKS_PER_DAY)
    return days * max(1, int(blocks_per_day))


def _binary_search_block_for_timestamp(w3: Web3, latest_block: int, target_timestamp: int) -> int:
    lo, hi = 0, int(latest_block)
    while lo < hi:
        mid = (lo + hi) // 2
        if int(w3.eth.get_block(mid)["timestamp"]) < target_timestamp:
            lo = mid + 1
        else:
            hi = mid
    return lo


def _get_logs_for_pool_batch(
    w3: Web3,
    *,
    pool_manager: str,
    from_block: int,
    to_block: int,
    pool_ids: list[str],
    min_batch_size: int = 1,
) -> list[Any]:
    if not pool_ids:
        return []
    try:
        return list(
            w3.eth.get_logs(
                {
                    "address": pool_manager,
                    "fromBlock": int(from_block),
                    "toBlock": int(to_block),
                    "topics": [V4_SWAP_TOPIC, pool_ids],
                }
            )
        )
    except Exception:
        if len(pool_ids) <= min_batch_size:
            raise
        midpoint = max(min_batch_size, len(pool_ids) // 2)
        left = _get_logs_for_pool_batch(
            w3,
            pool_manager=pool_manager,
            from_block=from_block,
            to_block=to_block,
            pool_ids=pool_ids[:midpoint],
            min_batch_size=min_batch_size,
        )
        right = _get_logs_for_pool_batch(
            w3,
            pool_manager=pool_manager,
            from_block=from_block,
            to_block=to_block,
            pool_ids=pool_ids[midpoint:],
            min_batch_size=min_batch_size,
        )
        return left + right


def _mark_recent_swap_activity_for_pool_ids(
    w3: Web3,
    pools: list[dict[str, Any]],
    *,
    pool_manager: str,
    latest_block: int,
    lookback_days: int,
    lookback_blocks: int | None = None,
    block_chunk_size: int = 50_000,
    pool_batch_size: int = 200,
) -> dict[str, Any]:
    normalized_pools = [
        pool
        for pool in pools
        if isinstance(pool, dict) and str(pool.get("poolId") or "").startswith("0x")
    ]
    all_pool_ids = sorted(
        {
            str(pool.get("poolId") or "").lower()
            for pool in normalized_pools
        }
    )
    if not all_pool_ids:
        return {
            "recentSwapLookbackDays": max(1, int(lookback_days)),
            "recentSwapLogCount": 0,
            "recentSwapPoolCount": 0,
            "recentSwapScannedPoolCount": 0,
        }
    if lookback_blocks is not None:
        start_block = max(0, int(latest_block) - max(1, int(lookback_blocks)))
    else:
        latest_timestamp = int(w3.eth.get_block(int(latest_block))["timestamp"])
        target_timestamp = latest_timestamp - max(1, int(lookback_days)) * 86_400
        start_block = _binary_search_block_for_timestamp(w3, int(latest_block), target_timestamp)

    active: set[str] = set()
    log_count = 0
    remaining_pool_ids = list(all_pool_ids)
    block_chunk_size = max(1, int(block_chunk_size))
    pool_batch_size = max(1, int(pool_batch_size))
    cursor = int(latest_block)
    while cursor >= start_block and remaining_pool_ids:
        chunk_start = max(start_block, cursor - block_chunk_size + 1)
        for offset in range(0, len(remaining_pool_ids), pool_batch_size):
            batch = remaining_pool_ids[offset : offset + pool_batch_size]
            logs = _get_logs_for_pool_batch(
                w3,
                pool_manager=pool_manager,
                from_block=chunk_start,
                to_block=cursor,
                pool_ids=batch,
            )
            log_count += len(logs)
            for log in logs:
                raw = log.get("topics") or []
                if len(raw) > 1:
                    topic = raw[1].hex() if hasattr(raw[1], "hex") else str(raw[1])
                    active.add(topic.lower())
        remaining_pool_ids = [
            pool_id
            for pool_id in remaining_pool_ids
            if pool_id not in active
        ]
        cursor = chunk_start - 1

    for pool in pools:
        pool["recentSwapActive"] = str(pool.get("poolId") or "").lower() in active
        if pool["recentSwapActive"]:
            pool["status"] = "initialized_pool_recent_swap"

    return {
        "recentSwapLookbackDays": max(1, int(lookback_days)),
        "recentSwapStartBlock": int(start_block),
        "recentSwapLogCount": int(log_count),
        "recentSwapPoolCount": len(active),
        "recentSwapScannedPoolCount": len(all_pool_ids),
    }


def _int_or_none(value: object) -> int | None:
    try:
        return int(str(value or "").strip())
    except (TypeError, ValueError):
        return None


def _decimal_text(value: object) -> Decimal | None:
    try:
        text = str(value or "").strip()
        if not text:
            return None
        return Decimal(text)
    except Exception:
        return None


def _liquidity_reserve_metrics(pool: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    """Estimate active-liquidity token reserves using current sqrtPrice.

    Uniswap v4 stores active liquidity ``L`` in PoolManager storage. For a
    given current sqrt price the full-range approximation is::

        amount0 = L / sqrtPrice
        amount1 = L * sqrtPrice

    Concentrated positions inside tighter ranges hold less than this bound, so
    the returned values are deliberately labelled as an index rather than an
    exact on-chain balance. The raw liquidity number is authoritative.
    """
    sqrt_price_x96 = _int_or_none(state.get("sqrtPriceX96"))
    liquidity = _int_or_none(state.get("liquidity"))
    if not sqrt_price_x96 or sqrt_price_x96 <= 0 or liquidity is None or liquidity <= 0:
        return {"liquidityIndex": 0, "hasLiquidity": False, "estimatedTvlUsd": 0}
    decimals0 = _int_or_none(pool.get("token0Decimals") or pool.get("token0_decimals")) or 18
    decimals1 = _int_or_none(pool.get("token1Decimals") or pool.get("token1_decimals")) or 18
    sqrt_price = Decimal(sqrt_price_x96) / Q96
    liquidity_dec = Decimal(liquidity)
    amount0_raw = liquidity_dec / sqrt_price
    amount1_raw = liquidity_dec * sqrt_price
    amount0 = amount0_raw / (Decimal(10) ** decimals0)
    amount1 = amount1_raw / (Decimal(10) ** decimals1)
    token0 = str(pool.get("token0") or "").lower()
    token1 = str(pool.get("token1") or "").lower()
    usd_price0: Decimal | None = None
    usd_price1: Decimal | None = None
    if token0 == ZERO_ADDRESS.lower():
        usd_price0 = DEFAULT_ETH_PRICE_USD
    elif token1 == ZERO_ADDRESS.lower():
        usd_price1 = DEFAULT_ETH_PRICE_USD
    if usd_price0 is None and usd_price1 is None and amount1_raw > 0:
        # For non-ETH pairs, infer an approximate price from the pool itself
        # only when one side is a known USD stable proxy. This keeps TVL useful
        # for ETH-anchored loops while avoiding unreliable generic token prices.
        symbol0 = str(pool.get("token0Symbol") or "").upper()
        symbol1 = str(pool.get("token1Symbol") or "").upper()
        if symbol1 in {"USDC", "USDT", "USDG", "DAI", "FRAX"} and amount1_raw > 0:
            usd_price1 = Decimal(1)
        elif symbol0 in {"USDC", "USDT", "USDG", "DAI", "FRAX"} and amount0_raw > 0:
            usd_price0 = Decimal(1)
    estimated_tvl_usd = Decimal(0)
    reserve_usd0: Decimal | None = amount0 * usd_price0 if usd_price0 is not None else None
    reserve_usd1: Decimal | None = amount1 * usd_price1 if usd_price1 is not None else None
    if reserve_usd0 is not None:
        estimated_tvl_usd += reserve_usd0
    if reserve_usd1 is not None:
        estimated_tvl_usd += reserve_usd1
    return {
        "hasLiquidity": True,
        "liquidityIndex": float((liquidity_dec / Decimal(10) ** 18).quantize(Decimal("0.00000001"))),
        "token0Reserve": str(amount0.normalize()),
        "token1Reserve": str(amount1.normalize()),
        "token0ReserveUsd": str(reserve_usd0.normalize()) if reserve_usd0 is not None else None,
        "token1ReserveUsd": str(reserve_usd1.normalize()) if reserve_usd1 is not None else None,
        "estimatedTvlUsd": str(estimated_tvl_usd.normalize()) if estimated_tvl_usd > 0 else None,
        "sqrtPriceX96": str(sqrt_price_x96),
        "tick": state.get("tick"),
        "lpFee": state.get("lpFee"),
        "protocolFee": state.get("protocolFee"),
    }


def _liquidity_depth_status(pool: dict[str, Any]) -> dict[str, Any]:
    try:
        liquidity_index = float(str(pool.get("liquidityIndex") or "0"))
        tvl_text = pool.get("estimatedTvlUsd")
        tvl = float(tvl_text) if tvl_text not in (None, "") else 0.0
    except (TypeError, ValueError):
        liquidity_index, tvl = 0.0, 0.0
    if liquidity_index <= 0:
        label, level, reason = "no_liquidity", "empty", "liquidity_is_zero"
    elif tvl > 1_000_000:
        label, level, reason = "deep", "deep", "liquidity_available"
    elif tvl > 100_000:
        label, level, reason = "liquid", "liquid", "liquidity_available"
    elif tvl > 10_000:
        label, level, reason = "thin", "thin", "liquidity_available"
    elif tvl > 0:
        label, level, reason = "thin", "thin", "liquidity_below_10k_usd"
    elif liquidity_index > 0:
        label, level, reason = "unpriced", "unpriced", "liquidity_present_but_no_usd_price"
    else:
        label, level, reason = "no_liquidity", "empty", "liquidity_is_zero"
    return {
        "label": label,
        "level": level,
        "reason": reason,
        "liquidityIndex": float(liquidity_index),
        "estimatedTvlUsd": tvl if tvl > 0 else None,
    }


def _enrich_pool_liquidity(
    w3: Web3,
    pools: list[dict[str, Any]],
    *,
    pool_manager: str,
    rpc_url: str | None = None,
    pool_ids: list[str] | None = None,
    block_number: int | None = None,
) -> dict[str, Any]:
    selected = [
        pool
        for pool in pools
        if isinstance(pool, dict)
        and str(pool.get("poolId") or "").startswith("0x")
        and (
            not pool_ids
            or str(pool.get("poolId") or "").lower()
            in {str(item).strip().lower() for item in pool_ids}
        )
    ]
    errors: list[str] = []
    confirmed_ids: list[str] = []

    def enrich_one(pool: dict[str, Any]) -> None:
        try:
            state = _read_live_pool_state_with_w3(
                w3=w3,
                rpc_url=rpc_url,
                pool_manager=pool_manager,
                pool_id=str(pool.get("poolId") or ""),
                network=NETWORK,
                chain_id=CHAIN_ID,
                block_number=block_number,
            )
        except Exception as exc:
            error_message = str(exc)
            if "liquidity is zero" in error_message.lower():
                pool["liquidityError"] = None
                pool["depthStatus"] = "no_liquidity"
                pool["status"] = "initialized_pool_no_liquidity"
                pool["hasLiquidity"] = False
                pool["liquidityIndex"] = 0
                pool["liquiditySource"] = "pool_manager_extsload_live_state"
            else:
                pool["liquidityError"] = error_message[:240]
                pool["depthStatus"] = "liquidity_read_failed"
                pool["liquiditySource"] = pool.get("liquiditySource") or "pool_manager_extsload_live_state"
                # A read failure is unknown, not proven empty. Preserve any
                # existing evidence instead of flipping the pool to false.
                pool["hasLiquidity"] = pool.get("hasLiquidity")
            errors.append(f"{pool.get('poolId')}:{str(exc)[:160]}")
            return
        pool["liquidity"] = str(state.get("liquidity") or "")
        pool["liquiditySource"] = state.get("source")
        pool["liquidityReadAt"] = _stamp()
        pool["liquidityBlockNumber"] = state.get("blockNumber")
        try:
            pool.update(_liquidity_reserve_metrics(pool, state))
            depth = _liquidity_depth_status(pool)
            pool["depthStatus"] = depth["label"]
            pool["depthBand"] = depth
            pool["balances"] = {
                **pool.get("balances", {}),
                "tvlUsd": pool.get("estimatedTvlUsd") or 0,
            }
            if pool.get("hasLiquidity"):
                pool["tvlUsd"] = pool.get("estimatedTvlUsd") or 0
                pool["status"] = "initialized_pool_with_liquidity"
                confirmed_ids.append(str(pool.get("poolId") or "").lower())
            else:
                pool["status"] = "initialized_pool_no_liquidity"
        except Exception as exc:
            pool["liquidityError"] = str(exc)[:240]
            pool["depthStatus"] = "liquidity_math_failed"
            pool["hasLiquidity"] = pool.get("hasLiquidity")

    with ThreadPoolExecutor(max_workers=min(6, max(1, len(selected)))) as executor:
        for _ in executor.map(enrich_one, selected, chunksize=1):
            pass
    marked = len(confirmed_ids)
    return {
        "liquidityMarkerActive": True,
        "liquidityMarkedPoolCount": marked,
        "liquidityErrorCount": len(errors),
        "liquidityErrors": errors[:20],
        "liquidityScanAt": _stamp(),
        "liquidityPoolIds": sorted(confirmed_ids),
    }


def mark_pool_liquidity_for_pool_index(
    *,
    rpc_url: str | None = None,
    pool_manager: str | None = None,
    output_path: Path | None = None,
    pool_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Refresh an existing pool index with live PoolManager liquidity markers."""
    resolved = output_path or default_v4_pool_index_path()
    if resolved is None:
        raise ValueError("Robinhood mainnet v4_pool_index data file is not configured")
    payload = _read_json(resolved)
    if not payload:
        raise ValueError(f"Robinhood mainnet v4_pool_index file is empty or invalid: {resolved}")
    row = _chain_row()
    resolved_pool_manager = Web3.to_checksum_address(pool_manager or _contract_address("v4_pool_manager"))
    if rpc_url:
        w3 = _connect_rpc(rpc_url)
        resolved_rpc_url = rpc_url
    else:
        w3, resolved_rpc_url = _connect_chain_rpc(NETWORK, CHAIN_ID)
    actual_chain_id = int(w3.eth.chain_id)
    if actual_chain_id != CHAIN_ID:
        raise ValueError(f"RPC returned chainId {actual_chain_id}, expected {CHAIN_ID}")
    pools = [pool for pool in payload.get("pools") or [] if isinstance(pool, dict)]
    scan_ids = [str(pool_id).strip().lower() for pool_id in (pool_ids or []) if str(pool_id).strip().startswith("0x")]
    pools_to_scan = pools
    if scan_ids:
        wanted = set(scan_ids)
        pools_to_scan = [pool for pool in pools if str(pool.get("poolId") or "").lower() in wanted]
    activity = _enrich_pool_liquidity(
        w3,
        pools_to_scan,
        pool_manager=resolved_pool_manager,
        rpc_url=resolved_rpc_url,
        pool_ids=scan_ids,
    )
    if scan_ids:
        activity["liquidityRequestedPoolIds"] = sorted(scan_ids)
    payload.update(activity)
    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {**payload, "outputPath": str(resolved)}


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
    """Return observed V4 pool ids from SQLite, not from legacy JSON shards."""
    from core.db.database_path import absolute_database_path
    from core.db.storage_business_io import query

    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for table in (
        "market_observation_intermediate_pools",
        "market_observation_abnormal_intermediate_pools",
    ):
        for row in query(
            str(absolute_database_path()),
            f"SELECT pool_id FROM {table} WHERE network = ? AND chain_id = ? AND protocol = ?",
            (NETWORK, CHAIN_ID, "uniswap_v4"),
        ):
            pool_id = str(row.get("pool_id") or "").strip().lower()
            if pool_id.startswith("0x") and pool_id not in seen:
                seen.add(pool_id)
                rows.append({"pool": pool_id})
    return rows


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
    mark_recent_swaps: bool = True,
    mark_liquidity: bool = True,
    recent_swap_lookback_blocks: int | None = None,
    recent_swap_lookback_days: int = RECENT_SWAP_LOOKBACK_DAYS,
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

    activity = {}
    if mark_recent_swaps and pools:
        activity = _mark_recent_swap_activity_for_pool_ids(
            w3,
            pools,
            pool_manager=resolved_pool_manager,
            latest_block=latest_block,
            lookback_days=recent_swap_lookback_days,
            lookback_blocks=recent_swap_lookback_blocks,
            block_chunk_size=50_000,
            pool_batch_size=200,
        )
        activity["recentSwapLookbackDays"] = max(1, int(recent_swap_lookback_days))
    liquidity_activity = {}
    if mark_liquidity and pools:
        liquidity_activity = _enrich_pool_liquidity(
            w3,
            pools,
            pool_manager=resolved_pool_manager,
            rpc_url=resolved_rpc_url,
        )

    payload = {
        "schemaVersion": "robinhood-v4-pool-index-v1",
        "network": NETWORK,
        "chainId": CHAIN_ID,
        "poolManager": resolved_pool_manager,
        "generatedAt": _stamp(),
        "generatedBlockNumber": latest_block,
        "discoveryMethod": "poolmanager_initialize_logs_for_observation_pool_ids",
        "rpcUrl": resolved_rpc_url,
        **activity,
        **liquidity_activity,
        "requestedPoolIdCount": len(seen),
        "decodedPoolKeyCount": len(pool_keys),
        "errorCount": len(errors),
        "errors": errors,
        "poolKeys": pool_keys,
        "pools": pools,
        "blockingReasons": errors[:20],
    }
    return payload


def mark_recent_swap_activity_for_pool_index(
    *,
    rpc_url: str | None = None,
    pool_manager: str | None = None,
    output_path: Path | None = None,
    recent_swap_lookback_days: int = RECENT_SWAP_LOOKBACK_DAYS,
    recent_swap_lookback_blocks: int | None = None,
    chunk_size: int = 50_000,
    pool_batch_size: int = 200,
    pool_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Refresh an existing pool index with recent Swap activity markers."""

    resolved = output_path or default_v4_pool_index_path()
    if resolved is None:
        raise ValueError("Robinhood mainnet v4_pool_index data file is not configured")
    payload = _read_json(resolved)
    if not payload:
        raise ValueError(f"Robinhood mainnet v4_pool_index file is empty or invalid: {resolved}")
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
    pools = [pool for pool in payload.get("pools") or [] if isinstance(pool, dict)]
    scan_ids = [str(pool_id).strip().lower() for pool_id in (pool_ids or []) if str(pool_id).strip().startswith("0x")]
    if scan_ids:
        pools_to_scan = [
            pool
            for pool in pools
            if str(pool.get("poolId") or "").lower() in scan_ids
        ]
    else:
        pools_to_scan = pools
    activity = _mark_recent_swap_activity_for_pool_ids(
        w3,
        pools_to_scan,
        pool_manager=resolved_pool_manager,
        latest_block=latest_block,
        lookback_days=recent_swap_lookback_days,
        lookback_blocks=recent_swap_lookback_blocks,
        block_chunk_size=chunk_size,
        pool_batch_size=pool_batch_size,
    )
    activity["recentSwapLookbackDays"] = max(1, int(recent_swap_lookback_days))
    activity["recentSwapScanAt"] = _stamp()
    activity["recentSwapScanBlockNumber"] = latest_block
    activity["recentSwapScanChunkSize"] = int(chunk_size)
    activity["recentSwapScanPoolBatchSize"] = int(pool_batch_size)
    if scan_ids:
        activity["recentSwapRequestedPoolIds"] = sorted(scan_ids)
    payload["recentSwapActive"] = True
    payload.update(activity)
    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {**payload, "outputPath": str(resolved)}


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
    rpc_urls: tuple[str, ...] | list[str] | None = None,
    hyperrpc_url: str = "",
    prefer_hyperrpc: bool = False,
    pool_manager: str | None = None,
    from_block: int = 0,
    to_block: int | None = None,
    chunk_size: int = 100_000,
    pool_limit: int = 0,
    non_eth_only: bool = False,
    include_metadata: bool = False,
    keep_token_addresses: set[str] | None = None,
    mark_recent_swaps: bool = True,
    recent_swap_lookback_blocks: int = 20_000,
) -> dict[str, Any]:
    row = _chain_row()
    resolved_rpc_url = rpc_url or str(row.get("rpc_url") or "").strip()
    if not resolved_rpc_url:
        rpc_block = row.get("rpc") if isinstance(row.get("rpc"), dict) else {}
        resolved_rpc_url = str(rpc_block.get("primary") or "").strip()
    if not resolved_rpc_url:
        raise ValueError("Robinhood mainnet RPC URL is not configured")
    resolved_pool_manager = Web3.to_checksum_address(pool_manager or _contract_address("v4_pool_manager"))
    resolved_rpc_urls = list(rpc_urls) if rpc_urls else list(rpc_urls_from_env())
    if resolved_rpc_url and resolved_rpc_url not in resolved_rpc_urls:
        resolved_rpc_urls.insert(0, resolved_rpc_url)
    return _build_core(
        network=NETWORK,
        chain_id=CHAIN_ID,
        pool_manager=resolved_pool_manager,
        rpc_url=resolved_rpc_url,
        rpc_urls=resolved_rpc_urls,
        hyperrpc_url=hyperrpc_url or os.getenv("ROBINHOOD_HYPERRPC_URL", os.getenv("ROBINHOOD_HyperRPC_URL", "")),
        prefer_hyperrpc=bool(prefer_hyperrpc),
        schema_version="robinhood-v4-pool-index-v1",
        include_currency_metadata=False,
        from_block=from_block,
        to_block=to_block,
        chunk_size=chunk_size,
        pool_limit=pool_limit,
        non_eth_only=non_eth_only,
        include_metadata=include_metadata,
        keep_token_addresses=keep_token_addresses,
        mark_recent_swaps=mark_recent_swaps,
        recent_swap_lookback_blocks=recent_swap_lookback_blocks,
        rpc_user_agent="flashloan-robinhood-mainnet-v4/1.0",
    )


def build_v4_directional_pool_backfill(
    *,
    rpc_url: str | None = None,
    rpc_urls: tuple[str, ...] | list[str] | None = None,
    hyperrpc_url: str = "",
    prefer_hyperrpc: bool = False,
    pool_manager: str | None = None,
    direction_targets: dict,
    direction_floor_block: int = 0,
    chunk_size: int = 100_000,
    confirm_pool_liquidity: object | None = None,
) -> dict:
    """Scan mainnet Initialize logs newest-to-oldest until directions are full."""
    row = _chain_row()
    resolved_rpc_url = rpc_url or str(row.get("rpc_url") or "").strip()
    if not resolved_rpc_url:
        rpc_block = row.get("rpc") if isinstance(row.get("rpc"), dict) else {}
        resolved_rpc_url = str(rpc_block.get("primary") or "").strip()
    if not resolved_rpc_url:
        raise ValueError("Robinhood mainnet RPC URL is not configured")
    resolved_pool_manager = Web3.to_checksum_address(
        pool_manager or _contract_address("v4_pool_manager")
    )
    resolved_rpc_urls = list(rpc_urls) if rpc_urls else list(rpc_urls_from_env())
    if resolved_rpc_url and resolved_rpc_url not in resolved_rpc_urls:
        resolved_rpc_urls.insert(0, resolved_rpc_url)
    return _backfill_core(
        network=NETWORK,
        chain_id=CHAIN_ID,
        pool_manager=resolved_pool_manager,
        rpc_url=resolved_rpc_url,
        rpc_urls=resolved_rpc_urls,
        hyperrpc_url=hyperrpc_url or os.getenv("ROBINHOOD_HYPERRPC_URL", os.getenv("ROBINHOOD_HyperRPC_URL", "")),
        prefer_hyperrpc=bool(prefer_hyperrpc),
        schema_version="robinhood-v4-pool-index-v1",
        direction_targets=direction_targets,
        direction_floor_block=direction_floor_block,
        include_currency_metadata=False,
        chunk_size=chunk_size,
        confirm_pool_liquidity=confirm_pool_liquidity,
        rpc_user_agent="flashloan-robinhood-mainnet-v4/1.0",
    )


def write_v4_pool_index_from_initialize_logs(output_path: Path | None = None, **kwargs: Any) -> dict[str, Any]:
    row = _chain_row()
    resolved_rpc_url = kwargs.pop("rpc_url", None) or str(row.get("rpc_url") or "").strip()
    if not resolved_rpc_url:
        rpc_block = row.get("rpc") if isinstance(row.get("rpc"), dict) else {}
        resolved_rpc_url = str(rpc_block.get("primary") or "").strip()
    if not resolved_rpc_url:
        raise ValueError("Robinhood mainnet RPC URL is not configured")
    resolved_pool_manager = Web3.to_checksum_address(kwargs.pop("pool_manager", None) or _contract_address("v4_pool_manager"))
    resolved_rpc_urls = list(kwargs.pop("rpc_urls", None) or ()) or list(rpc_urls_from_env())
    if resolved_rpc_url and resolved_rpc_url not in resolved_rpc_urls:
        resolved_rpc_urls.insert(0, resolved_rpc_url)
    prefer_hyperrpc = bool(kwargs.pop("prefer_hyperrpc", False))
    return _write_core(
        output_path=output_path,
        default_output_path=default_v4_pool_index_path(),
        network=NETWORK,
        chain_id=CHAIN_ID,
        pool_manager=resolved_pool_manager,
        rpc_url=resolved_rpc_url,
        rpc_urls=resolved_rpc_urls,
        hyperrpc_url=kwargs.pop("hyperrpc_url", None)
        or os.getenv("ROBINHOOD_HYPERRPC_URL", os.getenv("ROBINHOOD_HyperRPC_URL", "")),
        prefer_hyperrpc=prefer_hyperrpc,
        schema_version="robinhood-v4-pool-index-v1",
        include_currency_metadata=False,
        rpc_user_agent="flashloan-robinhood-mainnet-v4/1.0",
        **kwargs,
    )


def load_robinhood_mainnet_v4_pool_index(path: Path | None = None) -> dict[str, Any]:
    if path is None:
        return _load_db_index_payload(NETWORK, CHAIN_ID)
    return _load_core(
        path=path or default_v4_pool_index_path(),
        network=NETWORK,
        chain_id=CHAIN_ID,
    )


__all__ = [
    "build_v4_directional_pool_backfill",
    "build_v4_pool_index_from_initialize_logs",
    "build_v4_pool_index_from_observation_pool_ids",
    "default_v4_pool_index_path",
    "keep_token_addresses_from_payload",
    "load_robinhood_mainnet_v4_pool_index",
    "mark_pool_liquidity_for_pool_index",
    "mark_recent_swap_activity_for_pool_index",
    "write_v4_pool_index_from_observation_pool_ids",
    "write_v4_pool_index_from_initialize_logs",
]
