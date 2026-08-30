from __future__ import annotations

from decimal import ROUND_FLOOR, Decimal, getcontext
from pathlib import Path
from typing import Any
import json
import os

from eth_utils import keccak
from web3 import Web3

from core.chain_data_registry import chain_data_by_chain_id, chain_data_by_network
from core.chain_data_registry import chain_data_file_path
from intent_trade.chains.robinhood import config as robinhood
from intent_trade.chains.robinhood.shared import ROBINHOOD_TESTNET_NETWORK


getcontext().prec = 80

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


def _text(value: object) -> str:
    return str(value or "").strip()


def _normalize_address(value: object) -> str:
    text = _text(value)
    return text.lower() if text.startswith("0x") and len(text) == 42 else ""


def _normalize_pool_id(value: object) -> str:
    text = _text(value)
    return text.lower() if text.startswith("0x") and len(text) == 66 else _normalize_address(value)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _pool_index_path() -> Path | None:
    path = chain_data_file_path(ROBINHOOD_TESTNET_NETWORK, "v4_pool_index")
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
    normalized = _text(network).lower()
    urls: list[str] = []
    if int(chain_id or 0) == 46630 or normalized == ROBINHOOD_TESTNET_NETWORK:
        urls.extend(robinhood.testnet_rpc_urls_from_env())
    elif int(chain_id or 0) == 4663 or normalized == "robinhood":
        urls.extend(robinhood.rpc_urls_from_env())
    row = _chain_row(network, chain_id)
    rpc_url = _text(row.get("rpc_url") or row.get("rpcUrl"))
    if rpc_url:
        urls.append(rpc_url)
    deduped: list[str] = []
    for url in urls:
        if url and url not in deduped:
            deduped.append(url)
    return tuple(deduped)


def _connect_chain_rpc(network: str | None, chain_id: int | None) -> tuple[Any, str]:
    failures: list[str] = []
    timeout = float(os.getenv("DEX_ONCHAIN_RPC_TIMEOUT_SECONDS", "6") or 6)
    for url in _chain_rpc_urls(network, chain_id):
        w3 = Web3(Web3.HTTPProvider(url, request_kwargs={"timeout": timeout}))
        try:
            if w3.is_connected() and (chain_id is None or int(w3.eth.chain_id) == int(chain_id)):
                return w3, url
        except Exception as exc:
            failures.append(f"{url}: {exc}")
    raise RuntimeError("Robinhood RPC unavailable: " + "; ".join(failures[:3]))


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


def _read_live_pool_state(*, network: str | None, chain_id: int | None, pool_manager: str, pool_id: str) -> dict[str, Any]:
    w3, rpc_url = _connect_chain_rpc(network, chain_id)
    contract = w3.eth.contract(address=Web3.to_checksum_address(pool_manager), abi=_V4_POOL_MANAGER_EXTSLOAD_ABI)
    pool_id_bytes = bytes.fromhex(_normalize_pool_id(pool_id)[2:])
    state_slot = keccak(pool_id_bytes + _V4_POOLS_SLOT)
    slot0_raw = contract.functions.extsload(state_slot).call()
    liquidity_slot = (int.from_bytes(state_slot, "big") + 3).to_bytes(32, "big")
    liquidity_raw = contract.functions.extsload(liquidity_slot).call()
    slot0_value = int.from_bytes(bytes(slot0_raw), "big")
    sqrt_price_x96 = slot0_value & ((1 << 160) - 1)
    tick = (slot0_value >> 160) & ((1 << 24) - 1)
    if tick & (1 << 23):
        tick -= 1 << 24
    protocol_fee = (slot0_value >> 184) & 0xFFFFFF
    lp_fee = (slot0_value >> 208) & 0xFFFFFF
    liquidity = int.from_bytes(bytes(liquidity_raw), "big") & ((1 << 128) - 1)
    if sqrt_price_x96 <= 0:
        raise ValueError("robinhood testnet v4 pool manager live slot0 missing sqrtPriceX96")
    if liquidity <= 0:
        raise ValueError("robinhood testnet v4 pool manager live liquidity is zero")
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
    }


def _pool_rows() -> list[dict[str, Any]]:
    path = _pool_index_path()
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


def _pool_lookup(pool_address: str) -> dict[str, Any]:
    wanted = _normalize_pool_id(pool_address)
    for pool in _pool_rows():
        if _normalize_pool_id(pool.get("pool")) == wanted or _normalize_pool_id(pool.get("poolId")) == wanted:
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
    }


def _fee_bps(value: object) -> int:
    try:
        parsed = int(str(value or "").strip())
    except (TypeError, ValueError):
        return 0
    return max(0, parsed)


def quote_v4_exact_input_single_from_pool_index(request: dict[str, Any]) -> dict[str, Any]:
    pool = _pool_lookup(request.get("pool"))
    if not pool:
        pool = _request_pool_state(request)
    if not pool:
        raise ValueError("robinhood testnet v4 pool index missing requested pool")

    token_in = _normalize_address(request.get("tokenIn")) or _normalize_address(pool.get("token0"))
    token_out = _normalize_address(request.get("tokenOut")) or _normalize_address(pool.get("token1"))
    pool_token0 = _normalize_address(pool.get("token0"))
    pool_token1 = _normalize_address(pool.get("token1"))
    if {token_in, token_out} != {pool_token0, pool_token1}:
        raise ValueError("requested hop tokens do not match robinhood testnet v4 pool index")

    pool_id = _normalize_pool_id(pool.get("poolId") or pool.get("pool"))
    if not pool_id:
        raise ValueError("robinhood testnet v4 pool index missing poolId")
    network = _text(request.get("network") or pool.get("network") or ROBINHOOD_TESTNET_NETWORK).lower()
    chain_id = request.get("chainId")
    try:
        chain_id = int(chain_id) if chain_id not in (None, "") else None
    except (TypeError, ValueError):
        chain_id = None
    pool_manager = _pool_manager_address(network, chain_id, pool)
    if not pool_manager:
        raise ValueError("robinhood testnet v4 pool manager address missing")
    live_state = _read_live_pool_state(network=network, chain_id=chain_id, pool_manager=pool_manager, pool_id=pool_id)
    sqrt_price_x96 = int(live_state["sqrtPriceX96"])
    live_lp_fee = int(live_state["lpFee"])

    fee_bps = _fee_bps(request.get("fee") or pool.get("fee") or live_lp_fee)
    fee_factor = Decimal(10_000 - fee_bps) / Decimal(10_000)
    amount_in_raw = Decimal(int(str(request.get("amountInRaw") or "0")))
    if amount_in_raw <= 0:
        raise ValueError("amountInRaw must be positive")

    raw_price = Decimal(sqrt_price_x96) * Decimal(sqrt_price_x96) / Decimal(2**192)
    if token_in == pool_token0 and token_out == pool_token1:
        amount_out_raw = int((amount_in_raw * raw_price * fee_factor).to_integral_value(rounding=ROUND_FLOOR))
    else:
        amount_out_raw = int((amount_in_raw / raw_price * fee_factor).to_integral_value(rounding=ROUND_FLOOR))

    return {
        "status": "quoted",
        "quoteMethod": "robinhood_testnet_v4_poolmanager_live_state_spot_quote",
        "quoteMode": request.get("quoteMode") or "uniswap_v4_sdk_exact_input_single",
        "amountOutRaw": str(amount_out_raw),
        "sdkAdapter": "intent_trade.chains.robinhood_testnet.v4_quote_adapter:quote_v4_exact_input_single_from_pool_index",
        "quoteAdapter": "intent_trade.chains.robinhood_testnet.v4_quote_adapter:quote_v4_exact_input_single_from_pool_index",
        "poolState": {
            "protocol": "uniswap_v4",
            "pool": _normalize_pool_id(pool.get("pool") or pool.get("poolId")),
            "poolId": pool_id,
            "poolManager": pool_manager,
            "token0": pool_token0,
            "token1": pool_token1,
            "fee": int(pool.get("fee") or live_lp_fee or 0),
            "tickSpacing": int(pool.get("tickSpacing") or 0),
            "sqrtPriceX96": str(sqrt_price_x96),
            "tick": int(live_state["tick"]),
            "protocolFee": int(live_state["protocolFee"]),
            "lpFee": int(live_state["lpFee"]),
            "liquidity": str(live_state["liquidity"]),
            "source": live_state["source"],
        },
        "feeBps": fee_bps,
    }


__all__ = ["quote_v4_exact_input_single_from_pool_index"]
