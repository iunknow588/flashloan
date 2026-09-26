from __future__ import annotations

from decimal import Decimal, getcontext
import logging
from pathlib import Path
from typing import Any
import json
import os
import time
import urllib.request as urllib_request

from eth_abi import decode as abi_decode
from eth_utils import keccak
from web3 import Web3

from core.chain_data_registry import chain_data_by_chain_id, chain_data_by_network
from core.chain_data_registry import chain_data_file_path
from dex.intent_trade.chains.robinhood import config as robinhood
from dex.intent_trade.chains.robinhood.shared import (
    ROBINHOOD_TESTNET_NETWORK,
)


getcontext().prec = 80
logger = logging.getLogger(__name__)

_V4_POOLS_SLOT = (6).to_bytes(32, "big")
_V4_POOL_MANAGER_EXTSLOAD_ABI = [
    {
        "inputs": [{"internalType": "bytes32", "name": "slot", "type": "bytes32"}],
        "name": "extsload",
        "outputs": [{"internalType": "bytes32", "name": "value", "type": "bytes32"}],
        "stateMutability": "view",
        "type": "function",
    }
]
_V4_CUSTOM_ERROR_SELECTORS = {
    "0x6190b2b0": "UnexpectedRevertBytes(bytes)",
    "0x7a5ed734": "NotEnoughLiquidity(bytes32)",
    "0xe0752a5a": "UnexpectedCallSuccess()",
    "0x486aa307": "PoolNotInitialized()",
}
_V4_QUOTER_ABI = [
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


def _text(value: object) -> str:
    return str(value or "").strip()


def _canonical_testnet_network(network: object = None, chain_id: object = None) -> str:
    normalized = str(network or "").strip().lower()
    parsed_chain_id = int(chain_id) if chain_id is not None and str(chain_id).strip() else None
    if parsed_chain_id == robinhood.TESTNET_CHAIN_ID or normalized in {
        ROBINHOOD_TESTNET_NETWORK,
        "robinhood_testnet",
        "robinhood-sepolia",
        "chain_46630",
    }:
        return ROBINHOOD_TESTNET_NETWORK
    return normalized


def _normalize_address(value: object) -> str:
    text = _text(value)
    return text.lower() if text.startswith("0x") and len(text) == 42 else ""


def _normalize_pool_id(value: object) -> str:
    text = _text(value)
    return text.lower() if text.startswith("0x") and len(text) == 66 else _normalize_address(value)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _pool_index_path(network: str | None = None, chain_id: int | None = None) -> Path | None:
    normalized = _canonical_testnet_network(network, chain_id)
    if int(chain_id or 0) not in (robinhood.TESTNET_CHAIN_ID, None):
        raise ValueError(f"{_chain_label(network, chain_id)} adapter cannot serve chain_id={chain_id}")
    if normalized not in (ROBINHOOD_TESTNET_NETWORK, ""):
        raise ValueError(f"{_chain_label(network, chain_id)} adapter cannot serve network={network}")
    path = chain_data_file_path(ROBINHOOD_TESTNET_NETWORK, "v4_pool_index", chain_id=robinhood.TESTNET_CHAIN_ID)
    if path is not None:
        return path
    fallback = Path(__file__).resolve().parents[3] / "runtime" / "evidence" / ROBINHOOD_TESTNET_NETWORK / "v4_pool_index" / "latest.json"
    return fallback


def _chain_row(network: str | None, chain_id: int | None) -> dict[str, Any]:
    if chain_id is not None:
        try:
            row = chain_data_by_chain_id(int(chain_id)) or {}
        except Exception:
            row = {}
        if row:
            return row
    normalized = _text(network).lower()
    if not normalized:
        return {}
    try:
        row = chain_data_by_network(normalized) or {}
    except Exception:
        row = {}
    return row if isinstance(row, dict) else {}


def _chain_rpc_urls(network: str | None, chain_id: int | None) -> tuple[str, ...]:
    normalized = _canonical_testnet_network(network, chain_id)
    urls: list[str] = []
    if int(chain_id or 0) == robinhood.TESTNET_CHAIN_ID or normalized == ROBINHOOD_TESTNET_NETWORK or normalized == "":
        urls.extend(robinhood.testnet_rpc_urls_from_env())
    else:
        raise ValueError(f"{_chain_label(network, chain_id)} adapter cannot serve network={normalized}")
    row = _chain_row(network, chain_id)
    rpc_url = _text(row.get("rpc_url") or row.get("rpcUrl"))
    if rpc_url:
        urls.append(rpc_url)
    deduped: list[str] = []
    for url in urls:
        if url and url not in deduped:
            deduped.append(url)
    return tuple(deduped)


def _chain_label(network: str | None, chain_id: int | None) -> str:
    return "robinhood testnet v4"


def _connect_chain_rpc(network: str | None, chain_id: int | None) -> tuple[Any, str]:
    failures: list[str] = []
    timeouts = robinhood.testnet_rpc_connect_timeouts()
    for url in _chain_rpc_urls(network, chain_id):
        for attempt, timeout in enumerate(timeouts, start=1):
            w3 = Web3(
                Web3.HTTPProvider(
                    url,
                    request_kwargs={
                        "timeout": timeout,
                        "headers": {
                            "Content-Type": "application/json",
                            "User-Agent": "flashloan-robinhood-testnet-v4/1.1",
                            "Connection": "close",
                        },
                    },
                )
            )
            try:
                if not w3.is_connected():
                    failures.append(f"{url} attempt={attempt}: not connected")
                else:
                    actual_chain_id = int(w3.eth.chain_id)
                    if chain_id is None or actual_chain_id == int(chain_id):
                        return w3, url
                    failures.append(f"{url} attempt={attempt}: chain_id mismatch {actual_chain_id}, expected {chain_id}")
            except Exception as exc:
                failures.append(f"{url} attempt={attempt}: {exc}")
            if attempt < len(timeouts):
                time.sleep(0.25)
    raise RuntimeError(
        "Robinhood RPC unavailable after "
        f"{len(timeouts)} connect attempts ({'/'.join(str(int(t)) for t in timeouts)}s): "
        + "; ".join(failures[:5])
    )


def _pool_manager_address(network: str | None, chain_id: int | None, pool: dict[str, Any]) -> str:
    row = _chain_row(network, chain_id)
    contracts = row.get("contracts") if isinstance(row.get("contracts"), dict) else {}
    for candidate in (
        pool.get("poolManager"),
        pool.get("pool_manager"),
        contracts.get("v4_pool_manager"),
        row.get("defaultV4PoolManager"),
    ):
        text = _text(candidate)
        if text.startswith("0x") and len(text) == 42:
            return Web3.to_checksum_address(text)
    return ""


def _quoter_address(network: str | None, chain_id: int | None, pool: dict[str, Any]) -> str:
    row = _chain_row(network, chain_id)
    contracts = row.get("contracts") if isinstance(row.get("contracts"), dict) else {}
    for candidate in (
        pool.get("quoter"),
        pool.get("v4Quoter"),
        pool.get("v4_quoter"),
        contracts.get("v4_quoter"),
        row.get("defaultV4Quoter"),
    ):
        text = _text(candidate)
        if text.startswith("0x") and len(text) == 42:
            return Web3.to_checksum_address(text)
    return ""


def _read_live_pool_state(
    *,
    network: str | None,
    chain_id: int | None,
    pool_manager: str,
    pool_id: str,
    w3: Any | None = None,
    rpc_url: str | None = None,
    block_number: int | None = None,
) -> dict[str, Any]:
    if w3 is None:
        w3, rpc_url = _connect_chain_rpc(network, chain_id)
    return _read_live_pool_state_with_w3(
        w3=w3,
        rpc_url=rpc_url,
        pool_manager=pool_manager,
        pool_id=pool_id,
        block_number=block_number,
        network=network,
        chain_id=chain_id,
    )


def _read_live_pool_state_with_w3(
    *,
    w3: Any,
    rpc_url: str | None,
    pool_manager: str,
    pool_id: str,
    block_number: int | None = None,
    network: str | None = None,
    chain_id: int | None = None,
) -> dict[str, Any]:
    contract = w3.eth.contract(address=Web3.to_checksum_address(pool_manager), abi=_V4_POOL_MANAGER_EXTSLOAD_ABI)
    pool_id_bytes = bytes.fromhex(_normalize_pool_id(pool_id)[2:])
    state_slot = keccak(pool_id_bytes + _V4_POOLS_SLOT)
    # Live review must read the current chain head. Do not query a numeric
    # block in the evidence path; some testnet RPCs prune state shortly after
    # the block and return "historical state ... is not available".
    call_kwargs: dict[str, Any] = {"block_identifier": "latest"}
    slot0_raw = contract.functions.extsload(state_slot).call(**call_kwargs)
    liquidity_slot = (int.from_bytes(state_slot, "big") + 3).to_bytes(32, "big")
    liquidity_raw = contract.functions.extsload(liquidity_slot).call(**call_kwargs)
    slot0_value = int.from_bytes(bytes(slot0_raw), "big")
    sqrt_price_x96 = slot0_value & ((1 << 160) - 1)
    tick = (slot0_value >> 160) & ((1 << 24) - 1)
    if tick & (1 << 23):
        tick -= 1 << 24
    protocol_fee = (slot0_value >> 184) & 0xFFFFFF
    lp_fee = (slot0_value >> 208) & 0xFFFFFF
    liquidity = int.from_bytes(bytes(liquidity_raw), "big") & ((1 << 128) - 1)
    if sqrt_price_x96 <= 0:
        raise ValueError(f"{_chain_label(network, chain_id)} pool manager live slot0 missing sqrtPriceX96")
    if liquidity <= 0:
        raise ValueError(f"{_chain_label(network, chain_id)} pool manager live liquidity is zero")
    return {
        "source": "pool_manager_extsload_live_state",
        "poolManager": Web3.to_checksum_address(pool_manager),
        "poolId": _normalize_pool_id(pool_id),
        "stateSlot": state_slot.hex(),
        "sqrtPriceX96": str(sqrt_price_x96),
        "tick": int(tick),
        "protocolFee": int(protocol_fee),
        "lpFee": int(lp_fee),
        "liquidity": str(liquidity),
        "rpcUrl": rpc_url,
        "blockNumber": int(block_number) if block_number is not None else None,
    }


def _batch_read_live_pool_states(
    *,
    w3: Any,
    rpc_url: str | None,
    pool_manager: str,
    pool_ids: list[str],
    block_number: int | None = None,
    network: str | None = None,
    chain_id: int | None = None,
) -> dict[str, dict[str, Any]]:
    """Read multiple V4 pool states in one JSON-RPC batch request.

    The precheck previously read each pool with two sequential eth_calls. On
    slow testnet RPCs that can blow the scanner watchdog budget. Batching the
    slot0+liquidity reads keeps the precheck fast while still reading live
    chain head state.
    """
    results: dict[str, dict[str, Any]] = {}
    if not pool_ids:
        return results
    if not rpc_url:
        return results
    normalized_manager = Web3.to_checksum_address(pool_manager)
    payload: list[dict[str, Any]] = []
    id_map: dict[int, tuple[str, str, bytes]] = {}
    next_id = 1
    for pool_id in pool_ids:
        normalized_id = _normalize_pool_id(pool_id)
        pool_id_bytes = bytes.fromhex(normalized_id[2:])
        state_slot = keccak(pool_id_bytes + _V4_POOLS_SLOT)
        liquidity_slot = (int.from_bytes(state_slot, "big") + 3).to_bytes(32, "big")
        for slot, kind in ((state_slot, "slot0"), (liquidity_slot, "liquidity")):
            request_id = next_id
            next_id += 1
            id_map[request_id] = (normalized_id, kind, slot)
            selector = Web3.keccak(text="extsload(bytes32)")[:4]
            call_data = selector + slot
            payload.append(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "method": "eth_call",
                    "params": [{"to": normalized_manager, "data": "0x" + call_data.hex()}, "latest"],
                }
            )
    decoded_response = None
    last_error: Exception | None = None
    timeout = float(os.getenv("DEX_ONCHAIN_RPC_TIMEOUT_SECONDS", "10") or 10)
    for attempt in range(1, 3):
        try:
            body = json.dumps(payload).encode("utf-8")
            req = urllib_request.Request(
                rpc_url,
                data=body,
                headers={
                    "content-type": "application/json",
                    "user-agent": "flashloan-robinhood-testnet-v4-batch/1.0",
                },
                method="POST",
            )
            with urllib_request.urlopen(req, timeout=timeout) as response:
                decoded_response = json.loads(response.read().decode("utf-8"))
                break
        except Exception as exc:
            last_error = exc
            if attempt >= 2:
                raise RuntimeError(
                    f"{_chain_label(network, chain_id)} V4 pool state batch eth_call failed; {last_error}"
                ) from last_error
            time.sleep(0.4 * attempt)
    if decoded_response is None:
        return results
    rows = decoded_response if isinstance(decoded_response, list) else [decoded_response]
    values: dict[tuple[str, str], bytes] = {}
    errors: dict[tuple[str, str], str] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        request_id = row.get("id")
        if request_id not in id_map:
            continue
        pool_id, kind, _ = id_map[request_id]
        if "result" in row and row["result"]:
            values[(pool_id, kind)] = bytes.fromhex(str(row["result"]).removeprefix("0x"))
        elif "error" in row:
            errors[(pool_id, kind)] = str(row["error"])
    for pool_id in pool_ids:
        normalized_id = _normalize_pool_id(pool_id)
        key = normalized_id.lower()
        slot0_raw = values.get((normalized_id, "slot0"))
        liquidity_raw = values.get((normalized_id, "liquidity"))
        if slot0_raw is None or liquidity_raw is None:
            error = errors.get((normalized_id, "slot0")) or errors.get((normalized_id, "liquidity"))
            results[key] = {
                "error": error or "V4 pool state batch response missing slot0/liquidity",
                "poolId": normalized_id,
                "poolManager": normalized_manager,
                "blockNumber": int(block_number) if block_number is not None else None,
                "source": "pool_manager_extsload_live_state_batch",
            }
            continue
        slot0_value = int.from_bytes(slot0_raw, "big")
        sqrt_price_x96 = slot0_value & ((1 << 160) - 1)
        tick = (slot0_value >> 160) & ((1 << 24) - 1)
        if tick & (1 << 23):
            tick -= 1 << 24
        protocol_fee = (slot0_value >> 184) & 0xFFFFFF
        lp_fee = (slot0_value >> 208) & 0xFFFFFF
        liquidity = int.from_bytes(liquidity_raw, "big") & ((1 << 128) - 1)
        if sqrt_price_x96 <= 0:
            results[key] = {
                "error": f"{_chain_label(network, chain_id)} pool manager live slot0 missing sqrtPriceX96",
                "poolId": normalized_id,
                "poolManager": normalized_manager,
                "blockNumber": int(block_number) if block_number is not None else None,
                "source": "pool_manager_extsload_live_state_batch",
            }
            continue
        if liquidity <= 0:
            results[key] = {
                "error": f"{_chain_label(network, chain_id)} pool manager live liquidity is zero",
                "poolId": normalized_id,
                "poolManager": normalized_manager,
                "blockNumber": int(block_number) if block_number is not None else None,
                "source": "pool_manager_extsload_live_state_batch",
            }
            continue
        results[key] = {
            "source": "pool_manager_extsload_live_state_batch",
            "poolManager": normalized_manager,
            "poolId": normalized_id,
            "stateSlot": Web3.keccak(
                bytes.fromhex(normalized_id[2:]) + _V4_POOLS_SLOT
            ).hex(),
            "sqrtPriceX96": str(sqrt_price_x96),
            "tick": int(tick),
            "protocolFee": int(protocol_fee),
            "lpFee": int(lp_fee),
            "liquidity": str(liquidity),
            "rpcUrl": rpc_url,
            "blockNumber": int(block_number) if block_number is not None else None,
        }
    return results


def _pool_rows(network: str | None = None, chain_id: int | None = None) -> list[dict[str, Any]]:
    path = _pool_index_path(network, chain_id)
    if path is None:
        return []
    payload = _read_json(path)
    rows = payload.get("poolKeys")
    return [item for item in rows if isinstance(item, dict)] if isinstance(rows, list) else []


def _pool_sqrt_price_x96(pool: dict[str, Any]) -> int | None:
    for key in ("sqrtPriceX96", "sqrt_price_x96"):
        try:
            parsed = int(str(pool.get(key) or "").strip())
        except (TypeError, ValueError):
            continue
        if parsed > 0:
            return parsed
    pool_key = pool.get("poolKey") if isinstance(pool.get("poolKey"), dict) else {}
    for key in ("sqrtPriceX96", "sqrt_price_x96"):
        try:
            parsed = int(str(pool_key.get(key) or "").strip())
        except (TypeError, ValueError):
            continue
        if parsed > 0:
            return parsed
    return None


def _pool_lookup(pool_address: str, network: str | None = None, chain_id: int | None = None) -> dict[str, Any]:
    wanted = _normalize_pool_id(pool_address)
    for pool in _pool_rows(network, chain_id):
        if _normalize_pool_id(pool.get("pool")) == wanted or _normalize_pool_id(pool.get("poolId")) == wanted:
            return pool
    return {}


def _pool_lookup_by_tokens(
    token_in: object,
    token_out: object,
    network: str | None = None,
    chain_id: int | None = None,
) -> dict[str, Any]:
    wanted = {_normalize_address(token_in), _normalize_address(token_out)}
    if not all(wanted):
        return {}
    for pool in _pool_rows(network, chain_id):
        pool_token0 = _normalize_address(pool.get("token0"))
        pool_token1 = _normalize_address(pool.get("token1"))
        if pool_token0 and pool_token1 and {pool_token0, pool_token1} == wanted:
            return pool
    return {}


def _request_pool_state(request: dict[str, Any]) -> dict[str, Any]:
    pool_state = request.get("poolState") if isinstance(request.get("poolState"), dict) else {}
    return {
        "pool": _normalize_pool_id(pool_state.get("pool") or request.get("pool")),
        "poolId": _normalize_pool_id(pool_state.get("poolId") or request.get("poolId")),
        "token0": _normalize_address(pool_state.get("token0") or request.get("tokenIn")),
        "token1": _normalize_address(pool_state.get("token1") or request.get("tokenOut")),
        "token0Symbol": _text(pool_state.get("token0Symbol") or request.get("tokenInSymbol")),
        "token1Symbol": _text(pool_state.get("token1Symbol") or request.get("tokenOutSymbol")),
        "token0Decimals": pool_state.get("token0Decimals"),
        "token1Decimals": pool_state.get("token1Decimals"),
        "tickSpacing": pool_state.get("tickSpacing"),
        "sqrtPriceX96": pool_state.get("sqrtPriceX96") or request.get("sqrtPriceX96"),
        "poolManager": _text(pool_state.get("poolManager") or request.get("poolManager")),
        "quoter": _text(pool_state.get("quoter") or request.get("quoter")),
    }


def _resolve_v4_quote_context(request: dict[str, Any]) -> dict[str, Any]:
    pool_state = request.get("poolState") if isinstance(request.get("poolState"), dict) else {}
    raw_network = _text(request.get("network") or pool_state.get("network") or ROBINHOOD_TESTNET_NETWORK).lower()
    chain_id = request.get("chainId") or request.get("chain_id")
    try:
        chain_id = int(chain_id) if chain_id not in (None, "") else None
    except (TypeError, ValueError):
        chain_id = None
    network = _canonical_testnet_network(raw_network, chain_id)
    pool = _pool_lookup(request.get("pool"), network, chain_id)
    if not pool:
        pool = _request_pool_state(request)
    if not pool:
        raise ValueError(f"{_chain_label(network, chain_id)} pool index missing requested pool")

    indexed_pool = _pool_lookup(request.get("pool"), network, chain_id)
    if indexed_pool:
        pool = indexed_pool
    requested_token_in = _normalize_address(request.get("tokenIn"))
    requested_token_out = _normalize_address(request.get("tokenOut"))
    pool_token0 = _normalize_address(pool.get("token0"))
    pool_token1 = _normalize_address(pool.get("token1"))
    if requested_token_in or requested_token_out:
        matched_pool = _pool_lookup_by_tokens(requested_token_in, requested_token_out, network, chain_id)
        if matched_pool:
            pool = matched_pool
            pool_token0 = _normalize_address(pool.get("token0"))
            pool_token1 = _normalize_address(pool.get("token1"))
        elif requested_token_in and requested_token_out and {requested_token_in, requested_token_out} != {pool_token0, pool_token1}:
            raise ValueError(f"requested hop tokens do not match {_chain_label(network, chain_id)} pool index")
        elif requested_token_in and requested_token_in not in {pool_token0, pool_token1}:
            raise ValueError(f"requested hop tokens do not match {_chain_label(network, chain_id)} pool index")
        elif requested_token_out and requested_token_out not in {pool_token0, pool_token1}:
            raise ValueError(f"requested hop tokens do not match {_chain_label(network, chain_id)} pool index")
    token_in = requested_token_in if requested_token_in in {pool_token0, pool_token1} else pool_token0
    token_out = requested_token_out if requested_token_out in {pool_token0, pool_token1} else pool_token1
    if not token_in or not token_out or {token_in, token_out} != {pool_token0, pool_token1}:
        raise ValueError(f"requested hop tokens do not match {_chain_label(network, chain_id)} pool index")

    pool_id = _normalize_pool_id(pool.get("poolId") or pool.get("pool"))
    if not pool_id:
        raise ValueError(f"{_chain_label(network, chain_id)} pool index missing poolId")

    try:
        block_number = int(request.get("blockNumber")) if request.get("blockNumber") not in (None, "") else None
    except (TypeError, ValueError):
        block_number = None
    if block_number is not None and block_number <= 0:
        block_number = None

    pool_manager = _pool_manager_address(network, chain_id, pool)
    if not pool_manager:
        raise ValueError(f"{_chain_label(network, chain_id)} pool manager address missing")
    quoter_address = _quoter_address(network, chain_id, pool)
    if not quoter_address:
        raise ValueError(f"{_chain_label(network, chain_id)} quoter address missing")

    try:
        w3, rpc_url = _connect_chain_rpc(network, chain_id)
    except RuntimeError:
        live_state_fn_name = getattr(_read_live_pool_state, "__name__", "")
        if live_state_fn_name != "_read_live_pool_state":
            w3, rpc_url = None, ""
        else:
            raise
    live_state = _read_live_pool_state(
        w3=w3,
        rpc_url=rpc_url,
        network=network,
        chain_id=chain_id,
        pool_manager=pool_manager,
        pool_id=pool_id,
        block_number=block_number,
    )
    live_lp_fee = int(live_state["lpFee"])
    amount_in_raw = int(str(request.get("amountInRaw") or "0"))
    if amount_in_raw <= 0:
        raise ValueError("amountInRaw must be positive")
    fee_bps = _fee_bps(request.get("fee") or pool.get("fee") or live_lp_fee)
    hook_data = _hook_data_bytes(request.get("hookData") or pool.get("hookData") or pool.get("hook_data"))
    zero_for_one = token_in == pool_token0 and token_out == pool_token1
    if not zero_for_one and not (token_in == pool_token1 and token_out == pool_token0):
        raise ValueError(f"requested hop direction does not match {_chain_label(network, chain_id)} pool index")
    pool_key = (
        Web3.to_checksum_address(pool_token0),
        Web3.to_checksum_address(pool_token1),
        int(pool.get("fee") or live_lp_fee or 0),
        int(pool.get("tickSpacing") or 0),
        Web3.to_checksum_address(pool.get("hooks") or "0x0000000000000000000000000000000000000000"),
    )
    call_from = _text(request.get("callFrom") or request.get("from") or request.get("sender") or "")
    return {
        "pool": pool,
        "network": network,
        "chainId": chain_id,
        "poolId": pool_id,
        "poolManager": pool_manager,
        "quoterAddress": quoter_address,
        "w3": w3,
        "rpcUrl": rpc_url,
        "blockNumber": block_number,
        "liveState": live_state,
        "amountInRaw": amount_in_raw,
        "feeBps": fee_bps,
        "hookData": hook_data,
        "zeroForOne": zero_for_one,
        "poolKey": pool_key,
        "callFrom": call_from or None,
    }


def _fee_bps(value: object) -> int:
    try:
        parsed = int(str(value or "").strip())
    except (TypeError, ValueError):
        return 0
    if parsed <= 0:
        return 0
    # Uniswap V4 pool fees are surfaced as raw fee units such as 500 / 3000 / 10000,
    # which correspond to 5 / 30 / 100 bps respectively. Keep small values as-is so
    # unit tests and generic callers can still pass explicit bps.
    if parsed > 100:
        return max(0, parsed // 100)
    return parsed


def _hook_data_bytes(value: object) -> bytes:
    if value in (None, "", b""):
        return b""
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value)
    text = _text(value)
    if not text:
        return b""
    if text.startswith("0x"):
        try:
            return bytes.fromhex(text[2:])
        except ValueError as exc:
            raise ValueError("invalid hex hookData") from exc
    return text.encode("utf-8")


def _extract_revert_data(exc: Exception) -> str:
    candidates: list[object] = []
    data = getattr(exc, "data", None)
    if data is not None:
        candidates.append(data)
    args = getattr(exc, "args", None)
    if args:
        candidates.extend(list(args))
    info = getattr(exc, "info", None)
    if isinstance(info, dict):
        candidates.extend([info.get("error"), info.get("data")])
        nested_error = info.get("error")
        if isinstance(nested_error, dict):
            candidates.extend([nested_error.get("data"), nested_error.get("message")])
    for candidate in candidates:
        text = _text(candidate)
        if text.startswith("0x"):
            return text
    return ""


def _summarize_v4_revert_data(data: str) -> str:
    text = _text(data)
    if not text.startswith("0x") or len(text) < 10:
        return ""
    selector = text[:10].lower()
    name = _V4_CUSTOM_ERROR_SELECTORS.get(selector, selector)
    if selector == "0x6190b2b0":
        try:
            inner_bytes = abi_decode(["bytes"], bytes.fromhex(text[10:]))[0]
            if isinstance(inner_bytes, (bytes, bytearray)):
                inner_text = "0x" + bytes(inner_bytes).hex()
                inner_selector = inner_text[:10].lower()
                inner_name = _V4_CUSTOM_ERROR_SELECTORS.get(inner_selector, inner_selector)
                return f"{name} -> {inner_name}"
        except Exception:
            logger.debug("failed to decode nested V4 revert data", exc_info=True)
    return name


def _quote_v4_exact_input_single(
    *,
    w3: Any | None = None,
    network: str | None = None,
    chain_id: int | None = None,
    quoter_address: str,
    pool_key: tuple[str, str, int, int, str],
    zero_for_one: bool,
    amount_in_raw: int,
    hook_data: bytes,
    call_from: str | None = None,
    block_number: int | None = None,
) -> tuple[int, int]:
    if w3 is None:
        w3, _ = _connect_chain_rpc(network, chain_id)
    contract = w3.eth.contract(address=Web3.to_checksum_address(quoter_address), abi=_V4_QUOTER_ABI)
    call_kwargs: dict[str, Any] = {"gas": int(os.getenv("DEX_ONCHAIN_QUOTE_CALL_GAS_LIMIT", "30000000") or 30000000)}
    if call_from:
        call_kwargs["from"] = Web3.to_checksum_address(call_from)
    try:
        quote_call = contract.functions.quoteExactInputSingle(
            (
                pool_key,
                bool(zero_for_one),
                int(amount_in_raw),
                hook_data,
            )
        )
        # Use the live head, not a numeric snapshot block. The block_number
        # argument is retained only as audit metadata for the evidence record.
        amount_out, gas_estimate = quote_call.call(call_kwargs, block_identifier="latest")
    except Exception as exc:
        data = _extract_revert_data(exc)
        summary = _summarize_v4_revert_data(data)
        if summary:
            label = _chain_label(network, chain_id)
            raise RuntimeError(f"{label} quoter reverted: {summary}") from exc
        raise
    return int(amount_out), int(gas_estimate)


def quote_v4_exact_input_single_from_pool_index(request: dict[str, Any]) -> dict[str, Any]:
    context = _resolve_v4_quote_context(request)
    try:
        amount_out_raw, gas_estimate = _quote_v4_exact_input_single(
            w3=context["w3"],
            network=context["network"],
            chain_id=context["chainId"],
            quoter_address=context["quoterAddress"],
            pool_key=context["poolKey"],
            zero_for_one=context["zeroForOne"],
            amount_in_raw=context["amountInRaw"],
            hook_data=context["hookData"],
            call_from=context["callFrom"],
            block_number=context.get("blockNumber"),
        )
    except Exception as exc:
        label = _chain_label(context["network"], context["chainId"])
        raise RuntimeError(f"{label} quoter exact-input call failed; {exc}") from exc
    if amount_out_raw <= 0:
        label = _chain_label(context["network"], context["chainId"])
        raise RuntimeError(f"{label} quoter returned a non-positive amountOut")

    return {
        "status": "quoted",
        "quoteMethod": "robinhood_testnet_v4_quoter_quoteExactInputSingle_eth_call",
        "quoteMode": request.get("quoteMode") or "uniswap_v4_sdk_exact_input_single",
        "amountOutRaw": str(amount_out_raw),
        "quoterAddress": context["quoterAddress"],
        "quoterGasEstimate": int(gas_estimate),
        "quoteSource": "robinhood_testnet_v4_quoter",
        "sdkAdapter": "intent_trade.chains.robinhood_testnet.v4_quote_adapter:quote_v4_exact_input_single_from_pool_index",
        "quoteAdapter": "intent_trade.chains.robinhood_testnet.v4_quote_adapter:quote_v4_exact_input_single_from_pool_index",
        "poolState": {
            "protocol": "uniswap_v4",
            "pool": _normalize_pool_id(context["pool"].get("pool") or context["pool"].get("poolId")),
            "poolId": context["poolId"],
            "poolManager": context["poolManager"],
            "quoter": context["quoterAddress"],
            "token0": _normalize_address(context["pool"].get("token0")),
            "token1": _normalize_address(context["pool"].get("token1")),
            "fee": int(context["pool"].get("fee") or context["liveState"]["lpFee"] or 0),
            "tickSpacing": int(context["pool"].get("tickSpacing") or 0),
            "sqrtPriceX96": context["liveState"]["sqrtPriceX96"],
            "tick": int(context["liveState"]["tick"]),
            "protocolFee": int(context["liveState"]["protocolFee"]),
            "lpFee": int(context["liveState"]["lpFee"]),
            "liquidity": str(context["liveState"]["liquidity"]),
            "source": context["liveState"]["source"],
            "blockNumber": context["blockNumber"],
        },
        "feeBps": context["feeBps"],
    }


def _is_v4_liquidity_revert(exc: Exception) -> bool:
    message = _text(exc)
    if "NotEnoughLiquidity" in message:
        return True
    if "PoolNotInitialized" in message or "pool not initialized" in message.lower():
        return True
    cause = getattr(exc, "__cause__", None)
    if cause is not None and _is_v4_liquidity_revert(cause):
        return True
    return False


def _v4_liquidity_revert_reason(exc: Exception) -> str | None:
    if "PoolNotInitialized" in _text(exc) or "pool not initialized" in _text(exc).lower():
        return "pool_not_initialized"
    if "NotEnoughLiquidity" in _text(exc):
        return "not_enough_liquidity"
    cause = getattr(exc, "__cause__", None)
    if cause is not None:
        return _v4_liquidity_revert_reason(cause)
    return None


def probe_v4_executable_amount_from_pool_index(request: dict[str, Any]) -> dict[str, Any]:
    context = _resolve_v4_quote_context(request)
    target_amount = int(context["amountInRaw"])
    min_probe_amount = max(1, int(str(request.get("minProbeAmountRaw") or "1")))
    max_attempts = max(1, int(str(request.get("maxProbeAttempts") or "24")))
    max_multiplier = max(1, int(str(request.get("maxProbeMultiplier") or "8")))
    max_probe_amount = min((1 << 128) - 1, max(target_amount, target_amount * max_multiplier))
    attempts = 0
    last_success: dict[str, Any] | None = None
    lower_bound: int | None = None
    upper_bound: int | None = None
    pool_state = {
        "protocol": "uniswap_v4",
        "pool": _normalize_pool_id(context["pool"].get("pool") or context["pool"].get("poolId")),
        "poolId": context["poolId"],
        "poolManager": context["poolManager"],
        "quoter": context["quoterAddress"],
        "token0": _normalize_address(context["pool"].get("token0")),
        "token1": _normalize_address(context["pool"].get("token1")),
        "fee": int(context["pool"].get("fee") or context["liveState"]["lpFee"] or 0),
        "tickSpacing": int(context["pool"].get("tickSpacing") or 0),
        "sqrtPriceX96": context["liveState"]["sqrtPriceX96"],
        "tick": int(context["liveState"]["tick"]),
        "protocolFee": int(context["liveState"]["protocolFee"]),
        "lpFee": int(context["liveState"]["lpFee"]),
        "liquidity": str(context["liveState"]["liquidity"]),
        "source": context["liveState"]["source"],
    }

    def _attempt(amount_raw: int) -> dict[str, Any]:
        nonlocal attempts
        attempts += 1
        amount_out_raw, gas_estimate = _quote_v4_exact_input_single(
            w3=context["w3"],
            network=context["network"],
            chain_id=context["chainId"],
            quoter_address=context["quoterAddress"],
            pool_key=context["poolKey"],
            zero_for_one=context["zeroForOne"],
            amount_in_raw=int(amount_raw),
            hook_data=context["hookData"],
            call_from=context["callFrom"],
            block_number=context.get("blockNumber"),
        )
        if amount_out_raw <= 0:
            label = _chain_label(context["network"], context["chainId"])
            raise RuntimeError(f"{label} quoter returned a non-positive amountOut")
        return {
            "status": "quoted",
            "quoteMethod": "robinhood_testnet_v4_quoter_quoteExactInputSingle_eth_call",
            "quoteMode": request.get("quoteMode") or "uniswap_v4_sdk_exact_input_single",
            "amountInRaw": str(int(amount_raw)),
            "amountOutRaw": str(int(amount_out_raw)),
            "quoterAddress": context["quoterAddress"],
            "quoterGasEstimate": int(gas_estimate),
            "quoteSource": "robinhood_testnet_v4_quoter",
            "sdkAdapter": "intent_trade.chains.robinhood_testnet.v4_quote_adapter:probe_v4_executable_amount_from_pool_index",
            "quoteAdapter": "intent_trade.chains.robinhood_testnet.v4_quote_adapter:probe_v4_executable_amount_from_pool_index",
            "poolState": pool_state,
        }

    try:
        last_success = _attempt(target_amount)
        lower_bound = target_amount
    except Exception as exc:
        if _v4_liquidity_revert_reason(exc) == "pool_not_initialized":
            return {
                "status": "liquidity_probe_unexecutable",
                "quoteMethod": "robinhood_testnet_v4_liquidity_probe_binary_search",
                "quoteMode": request.get("quoteMode") or "uniswap_v4_sdk_exact_input_single",
                "targetAmountInRaw": str(target_amount),
                "minExecutableAmountRaw": None,
                "maxExecutableAmountRaw": None,
                "firstFailureAmountRaw": str(target_amount),
                "probeAttempts": attempts,
                "poolState": pool_state,
                "quoterAddress": context["quoterAddress"],
                "searchStatus": "pool_not_initialized",
                "failureReason": str(exc),
            }
        if not _is_v4_liquidity_revert(exc):
            raise
        upper_bound = target_amount
        probe = max(min_probe_amount, target_amount // 2)
        while probe >= min_probe_amount and attempts < max_attempts:
            try:
                last_success = _attempt(probe)
                lower_bound = probe
                break
            except Exception as inner_exc:
                if not _is_v4_liquidity_revert(inner_exc):
                    raise
                upper_bound = probe
                if probe <= min_probe_amount:
                    break
                next_probe = max(min_probe_amount, probe // 2)
                if next_probe == probe:
                    break
                probe = next_probe
        if lower_bound is None or last_success is None:
            return {
                "status": "liquidity_probe_unexecutable",
                "quoteMethod": "robinhood_testnet_v4_liquidity_probe_binary_search",
                "quoteMode": request.get("quoteMode") or "uniswap_v4_sdk_exact_input_single",
                "targetAmountInRaw": str(target_amount),
                "minExecutableAmountRaw": None,
                "maxExecutableAmountRaw": None,
                "firstFailureAmountRaw": str(upper_bound) if upper_bound is not None else None,
                "probeAttempts": attempts,
                "poolState": pool_state,
                "quoterAddress": context["quoterAddress"],
                "searchStatus": "no_successful_amount_found",
            }

    if upper_bound is None:
        probe = min(max_probe_amount, max(lower_bound * 2, lower_bound + 1))
        while probe <= max_probe_amount and attempts < max_attempts:
            try:
                last_success = _attempt(probe)
                lower_bound = probe
                if probe >= max_probe_amount:
                    break
                next_probe = min(max_probe_amount, max(probe * 2, probe + 1))
                if next_probe == probe:
                    break
                probe = next_probe
            except Exception as exc:
                if not _is_v4_liquidity_revert(exc):
                    raise
                upper_bound = probe
                break

    if upper_bound is not None and lower_bound is not None:
        while upper_bound - lower_bound > 1 and attempts < max_attempts:
            mid = (upper_bound + lower_bound) // 2
            try:
                last_success = _attempt(mid)
                lower_bound = mid
            except Exception as exc:
                if not _is_v4_liquidity_revert(exc):
                    raise
                upper_bound = mid

    if last_success is None or lower_bound is None:
        return {
            "status": "liquidity_probe_unexecutable",
            "quoteMethod": "robinhood_testnet_v4_liquidity_probe_binary_search",
            "quoteMode": request.get("quoteMode") or "uniswap_v4_sdk_exact_input_single",
            "targetAmountInRaw": str(target_amount),
            "minExecutableAmountRaw": None,
            "maxExecutableAmountRaw": None,
            "firstFailureAmountRaw": str(upper_bound) if upper_bound is not None else None,
            "probeAttempts": attempts,
            "poolState": context["liveState"],
            "quoterAddress": context["quoterAddress"],
            "searchStatus": "no_successful_amount_found",
        }

    return {
        "status": "liquidity_probe_complete",
        "quoteMethod": "robinhood_testnet_v4_liquidity_probe_binary_search",
        "quoteMode": request.get("quoteMode") or "uniswap_v4_sdk_exact_input_single",
        "targetAmountInRaw": str(target_amount),
        "minExecutableAmountRaw": str(lower_bound),
        "maxExecutableAmountRaw": str(lower_bound),
        "firstFailureAmountRaw": str(upper_bound) if upper_bound is not None else None,
        "probeAttempts": attempts,
        "poolState": pool_state,
        "quoterAddress": context["quoterAddress"],
        "lastSuccessAmountOutRaw": last_success.get("amountOutRaw"),
        "lastSuccessQuoterGasEstimate": last_success.get("quoterGasEstimate"),
        "lastSuccessQuoteMethod": last_success.get("quoteMethod"),
        "searchStatus": "bounded" if upper_bound is not None else "lower_bound_verified",
    }


__all__ = ["probe_v4_executable_amount_from_pool_index", "quote_v4_exact_input_single_from_pool_index"]
