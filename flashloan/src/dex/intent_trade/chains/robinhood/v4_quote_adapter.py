from __future__ import annotations

from decimal import Decimal, getcontext
import json
import logging
from pathlib import Path
from typing import Any
import os
import threading
import time

from eth_abi import decode as abi_decode
from eth_utils import keccak
from web3 import Web3

from dex.intent_trade.chains.robinhood_common.v4_quote_core import (
    V4_CUSTOM_ERROR_SELECTORS,
    V4_POOL_MANAGER_EXTSLOAD_ABI,
    V4_POOLS_SLOT,
    V4_QUOTER_ABI,
    chain_row,
    normalize_address,
    normalize_pool_id,
    text,
)
from dex.intent_trade.chains.robinhood import config as robinhood
from dex.intent_trade.chains.robinhood.shared import ROBINHOOD_NETWORK
from market.binance_market.dex_session_cache import (
    session_cached_call,
    session_fingerprint,
)


getcontext().prec = 80
logger = logging.getLogger(__name__)

# A single quote route can contain several hops. Reusing the successful
# read-only Web3 provider avoids repeating the slow TLS/RPC handshake for
# every hop while still allowing a changed endpoint or timeout to select a
# fresh connection.
_RPC_CONNECTION_CACHE: dict[tuple[str, int | None, tuple[str, ...], float], tuple[Any, str]] = {}
_RPC_CONNECTION_CACHE_LOCK = threading.Lock()
_POOL_ROWS_CACHE: dict[tuple[str, int, str], list[dict[str, Any]]] = {}
_POOL_ROWS_CACHE_LOCK = threading.Lock()

_V4_POOLS_SLOT = V4_POOLS_SLOT
_V4_POOL_MANAGER_EXTSLOAD_ABI = V4_POOL_MANAGER_EXTSLOAD_ABI
_V4_CUSTOM_ERROR_SELECTORS = V4_CUSTOM_ERROR_SELECTORS
_V4_QUOTER_ABI = V4_QUOTER_ABI


def _text(value: object) -> str:
    return text(value)


def _normalize_address(value: object) -> str:
    return normalize_address(value)


def _normalize_pool_id(value: object) -> str:
    return normalize_pool_id(value)


def _pool_index_path(network: str | None = None, chain_id: int | None = None) -> None:
    """Validate chain scope only; SQLite is the sole pool-index source."""
    if int(chain_id or 0) not in (robinhood.CHAIN_ID, None):
        raise ValueError(f"{_chain_label(network, chain_id)} adapter cannot serve chain_id={chain_id}")
    if str(network or "").strip().lower() not in ("robinhood", ""):
        raise ValueError(f"{_chain_label(network, chain_id)} adapter cannot serve network={network}")
    return None


def _chain_row(network: str | None, chain_id: int | None) -> dict[str, Any]:
    return chain_row(network, chain_id)


def _chain_rpc_urls(network: str | None, chain_id: int | None) -> tuple[str, ...]:
    normalized = _text(network).lower()
    urls: list[str] = []
    if int(chain_id or 0) == robinhood.CHAIN_ID or normalized == "robinhood" or normalized == "":
        urls.extend(robinhood.rpc_urls_from_env())
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
    return "robinhood mainnet v4"


def _connect_chain_rpc(network: str | None, chain_id: int | None) -> tuple[Any, str]:
    failures: list[str] = []
    timeouts = robinhood.mainnet_rpc_connect_timeouts()
    urls = _chain_rpc_urls(network, chain_id)
    cache_key = (_text(network).lower(), int(chain_id) if chain_id is not None else None, urls, timeouts)
    with _RPC_CONNECTION_CACHE_LOCK:
        cached = _RPC_CONNECTION_CACHE.get(cache_key)
    if cached is not None:
        return cached

    def _probe_one(url: str) -> tuple[Any, str]:
        last_exc: Exception | None = None
        for attempt, timeout in enumerate(timeouts, start=1):
            w3 = Web3(
                Web3.HTTPProvider(
                    url,
                    request_kwargs={
                        "timeout": timeout,
                        "verify": False,
                        "headers": {
                            "Content-Type": "application/json",
                            "User-Agent": "flashloan-robinhood-mainnet-v4/1.1",
                            "Connection": "close",
                        },
                    },
                )
            )
            try:
                if not w3.is_connected():
                    raise RuntimeError("HTTP endpoint did not answer")
                actual_chain_id = int(w3.eth.chain_id)
                if chain_id is not None and actual_chain_id != int(chain_id):
                    raise RuntimeError(f"wrong chain id {actual_chain_id}, expected {chain_id}")
                return w3, url
            except Exception as exc:
                last_exc = exc
                failures.append(f"{url} attempt={attempt}: {exc}")
                if attempt < len(timeouts):
                    time.sleep(0.25)
        raise RuntimeError(f"HTTP endpoint did not answer: {last_exc}")

    if not urls:
        raise RuntimeError("Robinhood RPC unavailable: no RPC URLs configured")
    if len(urls) == 1:
        try:
            connection = _probe_one(urls[0])
        except Exception as exc:
            failures.append(f"{urls[0]}: {exc}")
        else:
            with _RPC_CONNECTION_CACHE_LOCK:
                _RPC_CONNECTION_CACHE[cache_key] = connection
            return connection
    else:
        for url in urls:
            try:
                connection = _probe_one(url)
            except Exception as exc:
                failures.append(f"{url}: {exc}")
                continue
            with _RPC_CONNECTION_CACHE_LOCK:
                _RPC_CONNECTION_CACHE[cache_key] = connection
            return connection
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
    # block in the evidence path; some RPCs prune state shortly after the
    # block and return "historical state ... is not available".
    call_kwargs: dict[str, Any] = {"block_identifier": "latest"}
    slot0_raw = contract.functions.extsload(state_slot).call(**call_kwargs)
    liquidity_slot = (int.from_bytes(state_slot, "big") + 2).to_bytes(32, "big")
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


def _pool_rows_cache_key(network: str | None, chain_id: int | None) -> tuple[str, int, str]:
    from core.db.database_path import absolute_database_path

    resolved_chain_id = int(chain_id or robinhood.CHAIN_ID)
    return (
        str(network or "robinhood").strip().lower(),
        resolved_chain_id,
        str(absolute_database_path()).lower(),
    )


def _build_pool_row(
    pool_row: dict[str, Any],
    liquidity: dict[str, Any],
) -> dict[str, Any] | None:
    """Build the merged V4 quote pool row from exchange/liquidity rows."""
    pool_id = _normalize_pool_id(pool_row.get("pool_id") or pool_row.get("poolId"))
    if not pool_id:
        return None
    raw_key = pool_row.get("pool_key_json") or pool_row.get("poolKeyJson")
    pool_key = {}
    if isinstance(raw_key, str) and raw_key:
        try:
            pool_key = json.loads(raw_key)
        except (TypeError, ValueError, json.JSONDecodeError):
            pool_key = {}
    if not isinstance(pool_key, dict):
        pool_key = {}
    has_liquidity = liquidity.get("has_liquidity")
    if has_liquidity is None:
        has_liquidity = liquidity.get("hasLiquidity")
    liquidity_index = liquidity.get("liquidity_index")
    if liquidity_index is None:
        liquidity_index = liquidity.get("liquidityIndex")
    if has_liquidity is None and liquidity_index is not None:
        try:
            has_liquidity = float(liquidity_index) > 0
        except (TypeError, ValueError):
            has_liquidity = None
    if isinstance(has_liquidity, str):
        has_liquidity = has_liquidity.strip().lower() in {"1", "true", "yes"}
    return {
        "pool": pool_id,
        "poolId": pool_id,
        "poolManager": pool_row.get("pool_manager") or pool_row.get("poolManager") or pool_key.get("poolManager") or "",
        "token0": pool_row.get("token0") or pool_key.get("token0") or pool_key.get("currency0"),
        "token1": pool_row.get("token1") or pool_key.get("token1") or pool_key.get("currency1"),
        "fee": (
            pool_row.get("fee")
            if pool_row.get("fee") is not None
            else pool_key.get("fee")
        ),
        "tickSpacing": (
            pool_row.get("tick_spacing")
            if pool_row.get("tick_spacing") is not None
            else (
                pool_row.get("tickSpacing")
                if pool_row.get("tickSpacing") is not None
                else pool_key.get("tickSpacing")
            )
        ),
        "hooks": pool_row.get("hooks") or pool_key.get("hooks") or "",
        "poolKey": pool_key,
        "token0Symbol": pool_key.get("token0Symbol") or "",
        "token1Symbol": pool_key.get("token1Symbol") or "",
        "token0Decimals": pool_key.get("token0Decimals"),
        "token1Decimals": pool_key.get("token1Decimals"),
        "sqrtPriceX96": (
            liquidity.get("sqrt_price_x96")
            or liquidity.get("sqrtPriceX96")
            or pool_key.get("sqrtPriceX96")
            or ""
        ),
        "tick": (
            liquidity.get("tick")
            if liquidity.get("tick") is not None
            else pool_key.get("tick")
        ),
        "hasLiquidity": has_liquidity,
        "liquidity": liquidity.get("liquidity") or liquidity_index or "",
        "liquidityIndex": liquidity_index,
        "source": "robinhood_mainnet_v4_sqlite",
    }


def _pool_rows(
    network: str | None = None,
    chain_id: int | None = None,
    *,
    pool_ids: set[str] | None = None,
    token0: str | None = None,
    token1: str | None = None,
) -> list[dict[str, Any]]:
    """Read the V4 pool index from SQLite only."""
    from core.db.database_path import absolute_database_path
    from core.rs_bridge import load_v4_exchange_pools, load_v4_pool_liquidity

    _pool_index_path(network, chain_id)
    resolved_chain_id = int(chain_id or robinhood.CHAIN_ID)
    database_url = str(absolute_database_path())
    cache_key = _pool_rows_cache_key(network, chain_id)
    with _POOL_ROWS_CACHE_LOCK:
        cached = _POOL_ROWS_CACHE.get(cache_key)
        if cached is not None:
            return cached
    if pool_ids or token0 or token1:
        return _pool_rows_scoped(
            network=network,
            chain_id=chain_id,
            pool_ids=pool_ids,
            token0=token0,
            token1=token1,
        )
    pool_rows = load_v4_exchange_pools(
        "robinhood",
        resolved_chain_id,
        "uniswap_v4",
        database_url=database_url,
    )
    liquidity_rows = load_v4_pool_liquidity(
        "robinhood",
        resolved_chain_id,
        "uniswap_v4",
        database_url=database_url,
    )
    liquidity_by_id = {
        str(row.get("pool_id") or row.get("poolId") or "").lower(): row
        for row in liquidity_rows
    }
    rows: list[dict[str, Any]] = []
    for row in pool_rows:
        built = _build_pool_row(row, liquidity_by_id.get(_normalize_pool_id(row.get("pool_id") or row.get("poolId")).lower(), {}))
        if built is not None:
            rows.append(built)
    with _POOL_ROWS_CACHE_LOCK:
        _POOL_ROWS_CACHE[cache_key] = rows
    return rows


def _pool_rows_scoped(
    *,
    network: str | None = None,
    chain_id: int | None = None,
    pool_ids: set[str] | None = None,
    token0: str | None = None,
    token1: str | None = None,
) -> list[dict[str, Any]]:
    """Read only the V4 pools relevant to a quote lookup."""
    from core.db.database_path import absolute_database_path
    from core.db.storage_business_io import query

    _pool_index_path(network, chain_id)
    resolved_chain_id = int(chain_id or robinhood.CHAIN_ID)
    database_url = str(absolute_database_path())
    conditions = ["network = ?", "chain_id = ?", "protocol = ?"]
    params: list[Any] = ["robinhood", resolved_chain_id, "uniswap_v4"]
    normalized_ids = sorted(
        {
            _normalize_pool_id(pool_id).lower()
            for pool_id in pool_ids or []
            if _normalize_pool_id(pool_id)
        }
    )
    if normalized_ids:
        placeholders = ",".join("?" for _ in normalized_ids)
        conditions.append(f"LOWER(pool_id) IN ({placeholders})")
        params.extend(normalized_ids)
    if token0 or token1:
        normalized_token0 = _normalize_address(token0)
        normalized_token1 = _normalize_address(token1)
        if normalized_token0 and normalized_token1:
            conditions.append(
                "(LOWER(token0) = ? AND LOWER(token1) = ?)"
                " OR (LOWER(token0) = ? AND LOWER(token1) = ?)"
            )
            params.extend((normalized_token0, normalized_token1, normalized_token1, normalized_token0))
    pool_rows = query(
        database_url,
        f"""
        SELECT network, chain_id, protocol, pool_id, pool_identity_mode,
               pool_manager, pool_key_json, token0, token1, fee, fee_label,
               dynamic_fee, special_fee, tick_spacing, hooks, address_pair,
               created_at, updated_at
        FROM v4_exchange_pools
        WHERE {' AND '.join(conditions)}
        ORDER BY pool_id
        """,
        tuple(params),
    )
    if not pool_rows:
        return []
    liquidity_rows = query(
        database_url,
        f"""
        SELECT network, chain_id, protocol, pool_id, has_liquidity, liquidity,
               liquidity_index, estimated_tvl_usd, balances_json, depth_status,
               depth_band_json, depth_score_usd, sqrt_price_x96, price, tick,
               liquidity_read_at, liquidity_block_number, recent_swap_active,
               observed_at, refreshed_at
        FROM v4_exchange_pool_liquidity_snapshots
        WHERE network = ? AND chain_id = ? AND protocol = ?
          AND LOWER(pool_id) IN (
              {','.join('?' for _ in pool_rows)}
          )
        ORDER BY pool_id
        """,
        (
            "robinhood",
            resolved_chain_id,
            "uniswap_v4",
            *[str(_normalize_pool_id(row.get("pool_id") or row.get("poolId")) or "").lower() for row in pool_rows],
        ),
    )
    liquidity_by_id = {
        str(row.get("pool_id") or row.get("poolId") or "").lower(): row
        for row in liquidity_rows
    }
    rows: list[dict[str, Any]] = []
    for row in pool_rows:
        built = _build_pool_row(
            row,
            liquidity_by_id.get(str(_normalize_pool_id(row.get("pool_id") or row.get("poolId")) or "").lower(), {}),
        )
        if built is not None:
            rows.append(built)
    return rows


_DEFAULT_POOL_ROWS = _pool_rows


def clear_robinhood_v4_pool_rows_cache() -> int:
    """Drop cached merged pool rows after a SQLite import/rebuild."""
    with _POOL_ROWS_CACHE_LOCK:
        cleared = len(_POOL_ROWS_CACHE)
        _POOL_ROWS_CACHE.clear()
        return cleared


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


def _pool_liquidity_evidence(pool: dict[str, Any]) -> bool | None:
    """Return True/False for explicit depth evidence and None when unknown."""
    raw = pool.get("hasLiquidity")
    if raw is None:
        raw = pool.get("has_liquidity")
    if raw is not None:
        if isinstance(raw, str):
            return raw.strip().lower() in {"1", "true", "yes"}
        return bool(raw)
    for key in ("liquidity", "liquidityIndex"):
        value = pool.get(key)
        if value in (None, ""):
            continue
        try:
            return float(str(value)) > 0
        except (TypeError, ValueError):
            return None
    return None


def _pool_lookup(pool_address: str, network: str | None = None, chain_id: int | None = None) -> dict[str, Any]:
    wanted = _normalize_pool_id(pool_address)
    if not wanted:
        return {}
    pool_rows = (
        _pool_rows(network, chain_id)
        if _pool_rows is not _DEFAULT_POOL_ROWS
        else _pool_rows(network, chain_id, pool_ids={wanted})
    )
    for pool in pool_rows:
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
    pool_rows = (
        _pool_rows(network, chain_id)
        if _pool_rows is not _DEFAULT_POOL_ROWS
        else _pool_rows(
            network,
            chain_id,
            token0=_normalize_address(token_in),
            token1=_normalize_address(token_out),
        )
    )
    rows = [
        pool
        for pool in pool_rows
        if (
            (pool_token0 := _normalize_address(pool.get("token0")))
            and (pool_token1 := _normalize_address(pool.get("token1")))
            and {pool_token0, pool_token1} == wanted
        )
    ]
    if not rows:
        return {}

    zero_hooks = _normalize_address("0x0000000000000000000000000000000000000000")

    def is_special_fee(pool: dict[str, Any]) -> bool:
        try:
            fee = int(str(pool.get("fee") or pool.get("poolKey", {}).get("fee") or "0"))
        except (TypeError, ValueError):
            return False
        return bool(fee & 0x800000) or fee > 100_000

    usable = [
        pool
        for pool in rows
        if not is_special_fee(pool)
    ]
    if not usable:
        return {}
    for bucket in (
        [pool for pool in usable if _pool_liquidity_evidence(pool) is True],
        [pool for pool in usable if _pool_liquidity_evidence(pool) is None],
        [pool for pool in usable if _pool_liquidity_evidence(pool) is False],
    ):
        if not bucket:
            continue
        no_hook_bucket = [
            pool
            for pool in bucket
            if _normalize_address(pool.get("hooks") or pool.get("poolKey", {}).get("hooks")) == zero_hooks
        ]
        if no_hook_bucket:
            return no_hook_bucket[0]
        if bucket:
            return bucket[0]
    return usable[0]


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
    network = _text(request.get("network") or pool_state.get("network") or ROBINHOOD_NETWORK).lower()
    chain_id = request.get("chainId") or request.get("chain_id")
    try:
        chain_id = int(chain_id) if chain_id not in (None, "") else None
    except (TypeError, ValueError):
        chain_id = None
    requested_token_in = _normalize_address(request.get("tokenIn"))
    requested_token_out = _normalize_address(request.get("tokenOut"))
    pool = _pool_lookup(request.get("pool"), network, chain_id)
    if not pool:
        if not pool_state and (requested_token_in or requested_token_out):
            raise ValueError(f"requested hop tokens do not match {_chain_label(network, chain_id)} pool index")
        pool = _request_pool_state(request)
    if not pool:
        raise ValueError(f"{_chain_label(network, chain_id)} pool index missing requested pool")

    indexed_pool = _pool_lookup(request.get("pool"), network, chain_id)
    if indexed_pool:
        pool = indexed_pool
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

    static_live_state = _static_live_state_from_pool(
        pool=pool,
        pool_id=pool_id,
        pool_manager=pool_manager,
    )
    if static_live_state is not None and block_number is None:
        live_state = static_live_state
        w3, rpc_url = None, ""
    else:
        try:
            w3, rpc_url = _connect_chain_rpc(network, chain_id)
        except RuntimeError:
            # With no usable RPC we cannot read live pool state. The caller can
            # map this to live_evidence_deferred instead of mistaking it for
            # a liquidity problem.
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


def _fee_bps(value: object) -> int | None:
    try:
        parsed = int(str(value or "").strip())
    except (TypeError, ValueError):
        return None
    if parsed <= 0:
        return None
    # Uniswap V4 packs dynamic-fee and other flags into the uint24 fee field.
    # Treat a non-zero flag-only value as unknown rather than a giant bps value.
    if parsed & 0x800000:
        return None
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
    from core.rs_bridge import quote_v4_exact_input_single as _rust_quote_v4_exact_input_single
    from core.rs_bridge import rust_available

    if w3 is None:
        w3, rpc_url = _connect_chain_rpc(network, chain_id)
    else:
        rpc_url = _text(getattr(getattr(w3, "provider", None), "endpoint_uri", ""))
    contract = w3.eth.contract(address=Web3.to_checksum_address(quoter_address), abi=_V4_QUOTER_ABI)
    call_kwargs: dict[str, Any] = {"gas": int(os.getenv("DEX_ONCHAIN_QUOTE_CALL_GAS_LIMIT", "30000000") or 30000000)}
    if call_from:
        call_kwargs["from"] = Web3.to_checksum_address(call_from)
    quote_call = contract.functions.quoteExactInputSingle(
        (
            pool_key,
            bool(zero_for_one),
            int(amount_in_raw),
            hook_data,
        )
    )
    rust_path = rust_available() and bool(rpc_url)
    rust_request: dict[str, Any] = {}
    if rust_path:
        rust_request = {
            "currency0": _normalize_address(pool_key[0]),
            "currency1": _normalize_address(pool_key[1]),
            "fee": int(pool_key[2]),
            "tickSpacing": int(pool_key[3]),
            "hooks": _normalize_address(pool_key[4]),
            "zeroForOne": bool(zero_for_one),
            "amountInRaw": str(int(amount_in_raw)),
            "hookData": "0x" + bytes(hook_data).hex() if hook_data else "",
        }
    retries = max(0, int(os.getenv("DEX_ONCHAIN_QUOTE_RETRY_ATTEMPTS", "2") or 2))
    last_exc: Exception | None = None
    for attempt in range(retries + 1):
        try:
            if rust_path:
                payload = _rust_quote_v4_exact_input_single(
                    rust_request,
                    rpc_url,
                    quoter_address,
                    call_from=call_from,
                    timeout_seconds=int(os.getenv("DEX_ONCHAIN_RPC_TIMEOUT_SECONDS", "12") or 12),
                )
                if not payload.get("ok"):
                    raised = RuntimeError(str(payload.get("error") or "quoter eth_call failed"))
                    setattr(raised, "data", str(payload.get("revertData") or ""))
                    raise raised
                return (
                    int(str(payload.get("amountOutRaw") or "0x0"), 16),
                    int(str(payload.get("gasEstimate") or "0x0"), 16),
                )
            # Use the live head, not a numeric snapshot block. The block_number
            # argument is retained only as audit metadata for the evidence record.
            amount_out, gas_estimate = quote_call.call(call_kwargs, block_identifier="latest")
            return int(amount_out), int(gas_estimate)
        except Exception as exc:
            last_exc = exc
            data = _extract_revert_data(exc)
            summary = _summarize_v4_revert_data(data)
            if summary:
                label = _chain_label(network, chain_id)
                raise RuntimeError(f"{label} quoter reverted: {summary}") from exc
            if rust_path:
                break
            text = _text(exc)
            transient_markers = (
                "unexpected_eof",
                "connection reset",
                "connection aborted",
                "remote disconnected",
                "read timeout",
                "temporarily unavailable",
                "bad gateway",
                "service unavailable",
                "rpc unavailable",
                "eth_call",
            )
            if not any(marker in text.lower() for marker in transient_markers) or attempt >= retries:
                break
            time.sleep(0.5 * (attempt + 1))
    raise last_exc
    return int(amount_out), int(gas_estimate)


def quote_v4_exact_input_single_from_pool_index(request: dict[str, Any]) -> dict[str, Any]:
    context = _resolve_v4_quote_context(request)
    cache_key = (
        context["network"],
        (
            int(context["chainId"])
            if context["chainId"] not in (None, "")
            else None
        ),
        _normalize_pool_id(context["poolId"]),
        bool(context["zeroForOne"]),
        int(context["amountInRaw"]),
        session_fingerprint(
            block_number=context.get("blockNumber"),
            sqrt_price_x96=context["liveState"].get("sqrtPriceX96"),
        ),
    )

    def _build_quote() -> dict[str, Any]:
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
            "quoteMethod": "robinhood_mainnet_v4_quoter_quoteExactInputSingle_eth_call",
            "quoteMode": request.get("quoteMode") or "uniswap_v4_sdk_exact_input_single",
            "amountOutRaw": str(amount_out_raw),
            "quoterAddress": context["quoterAddress"],
            "quoterGasEstimate": int(gas_estimate),
            "quoteSource": "robinhood_mainnet_v4_quoter",
            "sdkAdapter": "intent_trade.chains.robinhood.v4_quote_adapter:quote_v4_exact_input_single_from_pool_index",
            "quoteAdapter": "intent_trade.chains.robinhood.v4_quote_adapter:quote_v4_exact_input_single_from_pool_index",
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

    return session_cached_call(
        kind="robinhood_v4_single_quote",
        key_parts=cache_key,
        builder=_build_quote,
    )


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


def _static_live_state_from_pool(
    *,
    pool: dict[str, Any],
    pool_id: str,
    pool_manager: str,
) -> dict[str, Any] | None:
    """Build a minimal pool-state view without a live extsload call."""
    pool_key = pool.get("poolKey") if isinstance(pool.get("poolKey"), dict) else {}
    token0 = _normalize_address(pool.get("token0") or pool_key.get("currency0") or pool_key.get("token0"))
    token1 = _normalize_address(pool.get("token1") or pool_key.get("currency1") or pool_key.get("token1"))
    if not token0 or not token1:
        return None
    fee = int(str(pool.get("fee") or pool_key.get("fee") or "").strip() or 0)
    if fee <= 0:
        return None
    tick_spacing = int(str(pool.get("tickSpacing") or pool_key.get("tickSpacing") or "0").strip() or 0)
    hooks = _normalize_address(pool.get("hooks") or pool_key.get("hooks"))
    sqrt_price_x96 = _pool_sqrt_price_x96(pool)
    if sqrt_price_x96 is None:
        return None
    try:
        tick = int(str(pool.get("tick") or pool_key.get("tick") or "0").strip())
    except (TypeError, ValueError):
        tick = 0
    liquidity = _text(pool.get("liquidity") or pool_key.get("liquidity") or "0")
    try:
        liquidity_int = int(liquidity)
    except (TypeError, ValueError):
        liquidity_int = 0
    return {
        "source": "pool_index_static_fallback",
        "poolManager": Web3.to_checksum_address(pool_manager),
        "poolId": _normalize_pool_id(pool_id),
        "sqrtPriceX96": str(sqrt_price_x96),
        "tick": int(tick),
        "protocolFee": 0,
        "lpFee": int(fee),
        "liquidity": str(max(0, liquidity_int)),
        "rpcUrl": None,
        "blockNumber": None,
    }


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
            "quoteMethod": "robinhood_mainnet_v4_quoter_quoteExactInputSingle_eth_call",
            "quoteMode": request.get("quoteMode") or "uniswap_v4_sdk_exact_input_single",
            "amountInRaw": str(int(amount_raw)),
            "amountOutRaw": str(int(amount_out_raw)),
            "quoterAddress": context["quoterAddress"],
            "quoterGasEstimate": int(gas_estimate),
            "quoteSource": "robinhood_mainnet_v4_quoter",
            "sdkAdapter": "intent_trade.chains.robinhood.v4_quote_adapter:probe_v4_executable_amount_from_pool_index",
            "quoteAdapter": "intent_trade.chains.robinhood.v4_quote_adapter:probe_v4_executable_amount_from_pool_index",
            "poolState": pool_state,
        }

    try:
        last_success = _attempt(target_amount)
        lower_bound = target_amount
    except Exception as exc:
        if _v4_liquidity_revert_reason(exc) == "pool_not_initialized":
            return {
                "status": "liquidity_probe_unexecutable",
                "quoteMethod": "robinhood_mainnet_v4_liquidity_probe_binary_search",
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
                "quoteMethod": "robinhood_mainnet_v4_liquidity_probe_binary_search",
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
            "quoteMethod": "robinhood_mainnet_v4_liquidity_probe_binary_search",
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
        "quoteMethod": "robinhood_mainnet_v4_liquidity_probe_binary_search",
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
