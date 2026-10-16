from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from core.chain_data_registry import chain_data_file_path
from core.chain_data_registry import chain_data_by_network
from core.chain_flow_dispatcher import ChainFlowRequest
from dex.intent_trade.chains.robinhood_common.v4_quote_core import V4_POOLS_SLOT
from dex.intent_trade.chains.robinhood_common.chain_config import (
    MAINNET_ANCHOR_ADDRESS_BY_SYMBOL,
)
from dex.intent_trade.chains.robinhood.config import rpc_urls_from_env as _mainnet_rpc_urls_from_env
from dex.intent_trade.chains.common.dex_candidate_tables import build_robinhood_pool_candidates, read_json_object
from dex.intent_trade.chains.common.dex_candidate_tables import build_anchor_candidate_groups, pair_classification

from web3 import Web3
from eth_utils import keccak


APP_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_ROBINHOOD_DEX_CANDIDATE_PATH = (
    chain_data_file_path("robinhood", "dex_candidates")
    or APP_ROOT / "runtime" / "cache" / "robinhood" / "robinhood_dex_candidates.json"
)
DEFAULT_ROBINHOOD_DEX_MARKET_POOL_PATH = (
    chain_data_file_path("robinhood", "dex_market_pools")
    or APP_ROOT / "runtime" / "cache" / "robinhood" / "robinhood_dex_market_pools.json"
)
DEFAULT_ROBINHOOD_DEX_EVIDENCE_ROOT = APP_ROOT / "runtime" / "evidence" / "robinhood" / "dex_discovery"
DEFAULT_ROBINHOOD_YC_ZC_TOKEN_CACHE = (
    chain_data_file_path("robinhood", "yc_zc_tokens")
    or APP_ROOT / "runtime" / "evidence" / "robinhood" / "yc_zc_tokens.json"
)

_OBSERVATION_YC_ZC_TOKEN_COUNT = 19

_ROBINHOOD_ROW = chain_data_by_network("robinhood") or {}
_ROBINHOOD_CONTRACTS = _ROBINHOOD_ROW.get("contracts") if isinstance(_ROBINHOOD_ROW.get("contracts"), dict) else {}
_WETH_ADDRESS = str(_ROBINHOOD_CONTRACTS.get("wrapped_native") or "").strip().lower()
_USDG_ADDRESS = str(_ROBINHOOD_CONTRACTS.get("usdg") or "").strip().lower()
_ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"
_ETH_SYMBOLS = {"ETH", "WETH"}
_ETH_ADDRESSES = {_ZERO_ADDRESS, _WETH_ADDRESS}
MAINNET_CONFIGURED_SYMBOLS = ("ETH", "WETH", "USDG", "USDE", "TZ")
MAINNET_STABLE_SYMBOLS = ("USDG", "USDE")
MAINNET_NON_STABLE_SYMBOLS = ("ETH", "WETH", "TZ")
MAINNET_REPRESENTATIVE_CANDIDATE_LIMIT = 6
_MAINNET_ETH_ALIASES = {_ZERO_ADDRESS, _WETH_ADDRESS}
MAINNET_V4_DYNAMIC_FEE_FLAG = 8388608
MAINNET_V4_SPECIAL_FEE_THRESHOLD = 100_000
MAINNET_V4_MAX_OBSERVATION_FIXED_FEE = 3000


def _read_json_object(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _mainnet_test_token_addresses() -> dict[str, str]:
    return {
        symbol: address
        for symbol, address in MAINNET_ANCHOR_ADDRESS_BY_SYMBOL.items()
        if symbol in {"TZ"}
    }


def _mainnet_stable_token_addresses() -> dict[str, tuple[str, int]]:
    result: dict[str, tuple[str, int]] = {}
    for symbol in MAINNET_STABLE_SYMBOLS:
        address = str(MAINNET_ANCHOR_ADDRESS_BY_SYMBOL.get(symbol) or "").strip().lower()
        if address:
            result[symbol] = (address, 6 if symbol == "USDG" else 18)
    return result


_MAINNET_TEST_ADDRESS_BY_SYMBOL = _mainnet_test_token_addresses()
_MAINNET_STABLE_TOKEN_DECIMALS_BY_SYMBOL = _mainnet_stable_token_addresses()
_MAINNET_STABLE_ADDRESS_BY_SYMBOL = {
    symbol: address for symbol, (address, _decimals) in _MAINNET_STABLE_TOKEN_DECIMALS_BY_SYMBOL.items()
}
_MAINNET_SYMBOL_BY_ADDRESS: dict[str, str] = {
    _ZERO_ADDRESS: "ETH",
}
if _WETH_ADDRESS:
    _MAINNET_SYMBOL_BY_ADDRESS[_WETH_ADDRESS] = "WETH"
if _USDG_ADDRESS:
    _MAINNET_SYMBOL_BY_ADDRESS[_USDG_ADDRESS] = "USDG"
_MAINNET_SYMBOL_BY_ADDRESS.update(_MAINNET_TEST_ADDRESS_BY_SYMBOL)
_MAINNET_SYMBOL_BY_ADDRESS.update(_MAINNET_STABLE_ADDRESS_BY_SYMBOL)
_MAINNET_STOCK_TOKEN_BY_ADDRESS: dict[str, dict[str, Any]] = {}
_MAINNET_STOCK_TOKENS_LOADED = False


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _mainnet_stock_token_by_address() -> dict[str, dict[str, Any]]:
    """Return official stock anchors from SQLite ``chain_tokens`` only."""
    global _MAINNET_STOCK_TOKEN_BY_ADDRESS, _MAINNET_STOCK_TOKENS_LOADED
    if _MAINNET_STOCK_TOKENS_LOADED:
        return _MAINNET_STOCK_TOKEN_BY_ADDRESS
    _MAINNET_STOCK_TOKENS_LOADED = True
    try:
        from core.db.database_path import absolute_database_path
        from core.db.storage_chain_core import load_chain_tokens

        rows = load_chain_tokens(
            str(absolute_database_path()),
            network="robinhood",
            chain_id=4663,
        )
    except Exception:
        rows = []
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        address = str(row.get("token_address") or "").strip().lower()
        symbol = str(row.get("token_symbol") or "").strip().upper()
        if not address or not symbol:
            continue
        metadata = _json_object(row.get("token_metadata_json"))
        if str(metadata.get("category") or "").strip().lower() != "robinhood_stock":
            continue
        try:
            decimals = int(row.get("token_decimals") or 18)
        except (TypeError, ValueError):
            decimals = 18
        result[address] = {
            "symbol": symbol,
            "address": address,
            "decimals": decimals,
        }
    _MAINNET_STOCK_TOKEN_BY_ADDRESS = result
    _MAINNET_SYMBOL_BY_ADDRESS.update(
        {
            address: str(item.get("symbol") or "").strip().upper()
            for address, item in result.items()
            if str(item.get("symbol") or "").strip()
        }
    )
    return result


def _mainnet_configured_addresses() -> set[str]:
    """Return every configured mainnet anchor address, including stocks."""
    addresses = set(_MAINNET_ETH_ALIASES)
    addresses.update(_MAINNET_STABLE_ADDRESS_BY_SYMBOL.values())
    if "TZ" in _MAINNET_TEST_ADDRESS_BY_SYMBOL:
        addresses.add(_MAINNET_TEST_ADDRESS_BY_SYMBOL["TZ"])
    addresses.update(_mainnet_stock_token_by_address())
    return {address for address in addresses if address}


def _mainnet_stock_token_addresses() -> set[str]:
    return set(_mainnet_stock_token_by_address())


def _mainnet_pair_in_configured_scope(token0: str, token1: str) -> bool:
    normalized0 = str(token0 or "").strip().lower()
    normalized1 = str(token1 or "").strip().lower()
    configured = _mainnet_configured_addresses()
    return normalized0 in configured and normalized1 in configured


def _mainnet_pair_in_candidate_scope(token0: str, token1: str) -> bool:
    normalized0 = str(token0 or "").strip().lower()
    normalized1 = str(token1 or "").strip().lower()
    configured = _mainnet_configured_addresses()
    # Candidate pools cover every first/third-hop edge that touches a
    # configured anchor. YC/ZC bridge-to-bridge edges are not part of the
    # static candidate surface; market observation discovers them dynamically
    # when it builds the y-z hop.
    return normalized0 in configured or normalized1 in configured


def _mainnet_configured_symbol_for_address(address: str, fallback: str = "") -> str:
    normalized = str(address or "").strip().lower()
    stock_item = _mainnet_stock_token_by_address().get(normalized)
    if stock_item:
        stock_symbol = str(stock_item.get("symbol") or "").strip().upper()
        if stock_symbol:
            return stock_symbol
    return _MAINNET_SYMBOL_BY_ADDRESS.get(normalized, str(fallback or "").strip())


def _mainnet_configured_anchor_symbols() -> list[str]:
    """Return base anchors plus official stocks as first-class anchors."""
    symbols = list(MAINNET_CONFIGURED_SYMBOLS)
    for item in _mainnet_stock_token_by_address().values():
        symbol = str(item.get("symbol") or "").strip().upper()
        if symbol and symbol not in symbols:
            symbols.append(symbol)
    return symbols


def _known_v4_token_metadata(address: str) -> tuple[str, int | None] | None:
    normalized = str(address or "").strip().lower()
    if normalized == _ZERO_ADDRESS:
        return "ETH", 18
    if normalized and normalized == _WETH_ADDRESS:
        return "WETH", 18
    if normalized and normalized == _USDG_ADDRESS:
        return "USDG", 6
    for symbol, address in _MAINNET_TEST_ADDRESS_BY_SYMBOL.items():
        if normalized and normalized == address:
            return symbol, 18
    for symbol, (address, decimals) in _MAINNET_STABLE_TOKEN_DECIMALS_BY_SYMBOL.items():
        if normalized and normalized == address:
            return symbol, decimals
    stock = _mainnet_stock_token_by_address().get(normalized)
    if stock:
        try:
            decimals = int(stock.get("decimals") or 18)
        except (TypeError, ValueError):
            decimals = 18
        return str(stock.get("symbol") or "").strip().upper() or None, decimals
    return None


def _v4_index_path(request: ChainFlowRequest) -> Path | None:
    return chain_data_file_path(request.network, "v4_pool_index", chain_id=request.chain_id)


def _v4_index_rows(path: Path | None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    payload = read_json_object(path)
    rows = [row for row in payload.get("pools") or [] if isinstance(row, dict)]
    return payload, rows


def _v4_symbol(row: dict[str, Any], index: int) -> str:
    value = str(row.get(f"token{index}Symbol") or "").strip().upper()
    address = str(row.get(f"token{index}") or row.get(f"currency{index}") or "").strip()
    known = _known_v4_token_metadata(address)
    if known is not None:
        return known[0]
    if value:
        return value
    if address.lower() == _ZERO_ADDRESS:
        return "ETH"
    return address[:10].upper() if address else ""


def _v4_is_eth_symbol_symbol(address: str, symbol: str) -> bool:
    return str(address or "").strip().lower() in _ETH_ADDRESSES or str(symbol or "").strip().upper() in _ETH_SYMBOLS


def _v4_eth_neighbor_candidate(
    candidate: dict[str, Any],
) -> dict[str, Any] | None:
    token0 = str(candidate.get("token0") or "").strip()
    token1 = str(candidate.get("token1") or "").strip()
    symbol0 = str(candidate.get("token0Symbol") or "").strip()
    symbol1 = str(candidate.get("token1Symbol") or "").strip()
    eth0 = _v4_is_eth_symbol_symbol(token0, symbol0)
    eth1 = _v4_is_eth_symbol_symbol(token1, symbol1)
    if eth0 and eth1:
        # Native/wrapped ETH pair is not a useful y-z route candidate.
        return None
    if eth0:
        return {"token": token1, "symbol": symbol1, "candidate": candidate}
    if eth1:
        return {"token": token0, "symbol": symbol0, "candidate": candidate}
    return None


def _v4_pool_candidate(row: dict[str, Any], pool_manager: str) -> dict[str, Any] | None:
    pool_id = str(row.get("poolId") or row.get("pool_id") or row.get("pool") or "").strip()
    token0 = str(row.get("token0") or row.get("currency0") or "").strip()
    token1 = str(row.get("token1") or row.get("currency1") or "").strip()
    symbol0 = _v4_symbol(row, 0)
    symbol1 = _v4_symbol(row, 1)
    decimals0 = row.get("token0Decimals")
    decimals1 = row.get("token1Decimals")
    known0 = _known_v4_token_metadata(token0)
    known1 = _known_v4_token_metadata(token1)
    if known0 is not None and decimals0 in (None, ""):
        decimals0 = known0[1]
    if known1 is not None and decimals1 in (None, ""):
        decimals1 = known1[1]
    if decimals0 in (None, ""):
        decimals0 = 18
    if decimals1 in (None, ""):
        decimals1 = 18
    if not pool_id or not token0 or not token1 or not symbol0 or not symbol1:
        return None
    raw_fee = row.get("fee")
    try:
        raw_fee_int = int(raw_fee) if raw_fee not in (None, "") else 0
    except (TypeError, ValueError):
        raw_fee_int = 0
    dynamic_fee = bool(raw_fee_int & MAINNET_V4_DYNAMIC_FEE_FLAG)
    special_fee = dynamic_fee or raw_fee_int > MAINNET_V4_SPECIAL_FEE_THRESHOLD
    liquidity_index = row.get("liquidityIndex")
    if liquidity_index is None:
        try:
            liquidity_index = float(str(row.get("liquidity") or "0"))
        except ValueError:
            liquidity_index = 0
    raw_has_liquidity = row.get("hasLiquidity")
    if raw_has_liquidity is None:
        raw_has_liquidity = row.get("has_liquidity")
    if raw_has_liquidity is not None:
        if isinstance(raw_has_liquidity, str):
            has_liquidity = raw_has_liquidity.strip().lower() in {"1", "true", "yes"}
        else:
            has_liquidity = bool(raw_has_liquidity)
    elif liquidity_index is not None and liquidity_index > 0:
        has_liquidity = True
    else:
        has_liquidity = None
    balances = row.get("balances")
    if not isinstance(balances, dict):
        balances = {"tvlUsd": row.get("estimatedTvlUsd") or row.get("tvlUsd") or 0}
    return {
        "pool": pool_id,
        "poolId": pool_id,
        "poolIdentityMode": "v4_pool_id",
        "poolManager": row.get("poolManager") or pool_manager,
        "poolKey": row.get("poolKey") or row,
        "dex": "uniswap_v4",
        "protocol": "uniswap_v4",
        "fee": row.get("fee"),
        "dynamicFee": dynamic_fee,
        "specialFee": special_fee,
        "feeLabel": ("dynamic" if dynamic_fee else "special") if special_fee else (int(raw_fee_int) if raw_fee_int else row.get("fee")),
        "usableForObservation": (
            not special_fee
            and 0 < raw_fee_int <= MAINNET_V4_MAX_OBSERVATION_FIXED_FEE
            and str(row.get("hooks") or "").strip().lower() in ("", _ZERO_ADDRESS)
        ),
        "hasLiquidity": has_liquidity,
        "liquidityIndex": liquidity_index,
        "estimatedTvlUsd": row.get("estimatedTvlUsd") or balances.get("tvlUsd"),
        "depthStatus": row.get("depthStatus") or "",
        "tickSpacing": row.get("tickSpacing"),
        "hooks": row.get("hooks"),
        "quality": "pass",
        "status": row.get("status") or "initialized_pool",
        "rejectReasons": [],
        "token0": token0,
        "token1": token1,
        "token0Symbol": symbol0,
        "token1Symbol": symbol1,
        "token0Decimals": decimals0,
        "token1Decimals": decimals1,
        "sqrtPriceX96": row.get("sqrtPriceX96"),
        "tick": row.get("tick"),
        "liquidity": row.get("liquidity") or "",
        "balances": balances,
        "tvlUsd": row.get("estimatedTvlUsd") or row.get("tvlUsd") or 0,
        "depthBand": row.get("depthBand") or {"level": "indexed", "label": "indexed V4 PoolKey"},
        "symbolPair": row.get("symbolPair") or f"{symbol0}:{symbol1}",
        "source": "robinhood_mainnet_v4_pool_index",
        "evidence": {
            **(row.get("evidence") or {}),
            "source": "robinhood_mainnet_v4_pool_index",
            "poolKeyDecoded": bool(row.get("poolKeyDecoded") or row.get("poolKey")),
            "initializeBlockNumber": row.get("initializeBlockNumber"),
            "initializeTransactionHash": row.get("initializeTransactionHash"),
        },
    }


def _v4_is_special_fee_candidate(candidate: dict[str, Any]) -> bool:
    """Return True when a candidate uses a dynamic or special fee flag.

    The static candidate handoff must stay executable on the first pass:
    dynamic/hook-managed fees need a live on-chain fee read before routing, so
    they are excluded from the fixed-fee closed-loop universe for now.
    """
    return bool(candidate.get("specialFee") or candidate.get("dynamicFee"))


def _v4_pool_observation_candidate(candidate: dict[str, Any]) -> bool:
    """Return whether a V4 pool belongs to the fixed-fee no-hook surface."""
    if _v4_is_special_fee_candidate(candidate):
        return False
    try:
        fee = int(str(candidate.get("fee") or "0").strip() or 0)
    except (TypeError, ValueError):
        return False
    if fee <= 0 or fee > MAINNET_V4_MAX_OBSERVATION_FIXED_FEE:
        return False
    hooks = str(candidate.get("hooks") or "").strip().lower()
    return not hooks or hooks == _ZERO_ADDRESS


def _batch_read_v4_pool_states(
    *,
    rpc_url: str,
    pool_manager: str,
    pool_ids: list[str],
    timeout: float = 20.0,
) -> dict[str, dict[str, Any]]:
    """Read live slot0+liquidity for many V4 pools in one JSON-RPC batch.

    Uniswap V4 stores each pool as a packed ``Pool.State`` struct starting at
    ``keccak256(poolId || 6)``. The liquidity field is the third 32-byte slot.
    This keeps candidate refresh fast enough to run against the full indexed
    set without thousands of sequential RPC calls.
    """
    results: dict[str, dict[str, Any]] = {}
    if not rpc_url or not pool_ids:
        return results
    payload: list[dict[str, Any]] = []
    id_map: dict[int, tuple[str, str, bytes]] = {}
    next_id = 1
    for pool_id in pool_ids:
        normalized_id = str(pool_id or "").strip().lower()
        if not normalized_id.startswith("0x") or len(normalized_id) != 66:
            continue
        pool_id_bytes = bytes.fromhex(normalized_id[2:])
        state_slot = keccak(pool_id_bytes + V4_POOLS_SLOT)
        liquidity_slot = (int.from_bytes(state_slot, "big") + 2).to_bytes(32, "big")
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
                    "params": [{"to": Web3.to_checksum_address(pool_manager), "data": "0x" + call_data.hex()}, "latest"],
                }
            )
    if not payload:
        return results
    decoded_response = None
    last_error: Exception | None = None
    for attempt in range(1, 3):
        try:
            body = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                rpc_url,
                data=body,
                headers={
                    "content-type": "application/json",
                    "user-agent": "flashloan-robinhood-mainnet-v4-candidate-batch/1.0",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=timeout) as response:
                decoded_response = json.loads(response.read().decode("utf-8"))
                break
        except Exception as exc:
            last_error = exc
            if attempt >= 2:
                raise RuntimeError(f"robinhood mainnet V4 pool state batch eth_call failed; {last_error}") from last_error
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
        pool_id, kind, _ = id_map[int(request_id)]
        if "result" in row and row["result"]:
            values[(pool_id, kind)] = bytes.fromhex(str(row["result"]).removeprefix("0x"))
        elif "error" in row:
            errors[(pool_id, kind)] = str(row["error"])
    for pool_id in pool_ids:
        normalized_id = str(pool_id or "").strip().lower()
        if not normalized_id.startswith("0x") or len(normalized_id) != 66:
            continue
        slot0_raw = values.get((normalized_id, "slot0"))
        liquidity_raw = values.get((normalized_id, "liquidity"))
        if slot0_raw is None or liquidity_raw is None:
            results[normalized_id] = {
                "error": errors.get((normalized_id, "slot0")) or errors.get((normalized_id, "liquidity")) or "V4 pool state batch response missing slot0/liquidity",
                "poolId": normalized_id,
                "poolManager": Web3.to_checksum_address(pool_manager),
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
            results[normalized_id] = {"error": "robinhood mainnet pool manager live slot0 missing sqrtPriceX96", "poolId": normalized_id, "poolManager": Web3.to_checksum_address(pool_manager), "source": "pool_manager_extsload_live_state_batch"}
            continue
        if liquidity <= 0:
            results[normalized_id] = {"error": "robinhood mainnet pool manager live liquidity is zero", "poolId": normalized_id, "poolManager": Web3.to_checksum_address(pool_manager), "source": "pool_manager_extsload_live_state_batch"}
            continue
        results[normalized_id] = {
            "source": "pool_manager_extsload_live_state_batch",
            "poolManager": Web3.to_checksum_address(pool_manager),
            "poolId": normalized_id,
            "stateSlot": state_slot.hex(),
            "sqrtPriceX96": str(sqrt_price_x96),
            "tick": int(tick),
            "protocolFee": int(protocol_fee),
            "lpFee": int(lp_fee),
            "liquidity": str(liquidity),
            "rpcUrl": rpc_url,
        }
    return results


def _mainnet_rpc_urls() -> tuple[str, ...]:
    """Return the configured Robinhood mainnet RPC URLs."""
    try:
        urls = _mainnet_rpc_urls_from_env()
    except Exception:
        urls = ()
    # HyperRPC/HyperSync are log-scanning providers, not eth_call endpoints.
    return tuple(
        url
        for url in urls
        if "hyperrpc" not in str(url).lower() and "hypersync" not in str(url).lower()
    )


def _mainnet_rpc_url() -> str:
    """Return the first configured Robinhood mainnet RPC URL."""
    urls = _mainnet_rpc_urls()
    return str(urls[0]) if urls else ""


def _decorate_candidates_with_live_state(
    candidates: list[dict[str, Any]],
    *,
    pool_manager: str,
    rpc_url: str,
) -> dict[str, Any]:
    """Attach live PoolManager state to candidate rows.

    The static candidate cache is built offline, but it must still reflect
    real pool depth. Dynamic/special fee pools are already excluded from the
    fixed-fee universe; this batch read makes ``hasLiquidity`` authoritative so
    the representative set is not filled with stale TVL fields.
    """
    summary: dict[str, Any] = {
        "liveStateReadAttempted": False,
        "liveStatePoolCount": 0,
        "liveLiquidPoolCount": 0,
        "liveNoLiquidityPoolCount": 0,
        "liveStateUnavailablePoolCount": 0,
        "error": None,
    }
    if not pool_manager:
        summary["error"] = "robinhood_mainnet_rpc_or_pool_manager_missing_for_live_state"
        return summary
    fixed_rows = [
        candidate
        for candidate in candidates
        if (
            not _v4_is_special_fee_candidate(candidate)
            and int(str(candidate.get("fee") or candidate.get("poolKey", {}).get("fee") or "0").strip() or 0) > 0
        )
    ]
    pool_ids = sorted({str(candidate.get("pool") or candidate.get("poolId") or "").strip().lower() for candidate in fixed_rows})
    pool_ids = [pool_id for pool_id in pool_ids if pool_id.startswith("0x") and len(pool_id) == 66]
    if not pool_ids:
        summary["error"] = "no_fixed_fee_pool_ids_for_live_state"
        return summary
    states: dict[str, dict[str, Any]] = {}
    try:
        # The public mainnet endpoint can 429 on large JSON-RPC batches. Keep
        # each batch small while still staying far cheaper than one eth_call
        # per pool. If the primary URL fails, fall back to the next configured
        # RPC before giving up.
        batch_size = 10
        provided = tuple(str(url) for url in ([rpc_url] if rpc_url else ()) if url)
        rpc_urls = tuple(dict.fromkeys(provided + _mainnet_rpc_urls()))
        states = {}
        errors: list[str] = []
        for start in range(0, len(pool_ids), batch_size):
            chunk = pool_ids[start:start + batch_size]
            chunk_states: dict[str, dict[str, Any]] = {}
            for candidate_url in rpc_urls:
                try:
                    chunk_states = _batch_read_v4_pool_states(
                        rpc_url=candidate_url,
                        pool_manager=pool_manager,
                        pool_ids=chunk,
                    )
                except Exception as exc:
                    errors.append(str(exc))
                    time.sleep(0.3)
                    continue
                if chunk_states and any(not state.get("error") for state in chunk_states.values()):
                    break
            if chunk_states and any(not state.get("error") for state in chunk_states.values()):
                states.update(chunk_states)
            else:
                errors.append(f"{chunk[0]}..{chunk[-1]}: no_state_success")
    except Exception as exc:
        summary["error"] = str(exc)
        return summary
    summary["liveStateReadAttempted"] = True
    summary["liveStatePoolCount"] = len(pool_ids)
    for candidate in candidates:
        pool_id = str(candidate.get("pool") or candidate.get("poolId") or "").strip().lower()
        state = states.get(pool_id) if pool_id else None
        if state is None or state.get("error"):
            error_message = str((state or {}).get("error") or "live_state_missing")
            if "liquidity is zero" in error_message.lower():
                candidate["hasLiquidity"] = False
                candidate["liquidityIndex"] = 0
                candidate["liquidity"] = ""
                candidate["estimatedTvlUsd"] = 0
                candidate["tvlUsd"] = 0
                candidate["balances"] = {"tvlUsd": 0}
                candidate["depthStatus"] = "live_liquidity_zero"
                candidate["liquiditySource"] = "pool_manager_extsload_live_state_batch"
                summary["liveNoLiquidityPoolCount"] = summary.get("liveNoLiquidityPoolCount") or 0
                summary["liveNoLiquidityPoolCount"] += 1
            else:
                candidate["depthStatus"] = "live_liquidity_unavailable"
                candidate["liquiditySource"] = candidate.get("liquiditySource") or "pool_manager_extsload_live_state_batch"
                summary["liveStateUnavailablePoolCount"] = summary.get("liveStateUnavailablePoolCount") or 0
                summary["liveStateUnavailablePoolCount"] += 1
            candidate["usableForObservation"] = _v4_pool_observation_candidate(candidate)
            candidate["liquidityError"] = (state or {}).get("error") or "live_state_missing"
            continue
        liquid = str(state.get("liquidity") or "0")
        has_liquidity = int(liquid) > 0
        candidate["hasLiquidity"] = has_liquidity
        candidate["liquidityIndex"] = float(liquid) if has_liquidity else 0
        candidate["liquidity"] = liquid
        candidate["usableForObservation"] = _v4_pool_observation_candidate(candidate)
        candidate["sqrtPriceX96"] = state.get("sqrtPriceX96") or candidate.get("sqrtPriceX96")
        candidate["tick"] = state.get("tick") if state.get("tick") is not None else candidate.get("tick")
        candidate["liquiditySource"] = state.get("source") or "pool_manager_extsload_live_state_batch"
        candidate["liquidityError"] = None
        candidate["depthStatus"] = "live_liquid" if has_liquidity else "live_liquidity_zero"
        if has_liquidity:
            candidate["balances"] = candidate.get("balances") or {}
            candidate["balances"]["v4Liquidity"] = liquid
            summary["liveLiquidPoolCount"] = summary.get("liveLiquidPoolCount") or 0
            summary["liveLiquidPoolCount"] += 1
        else:
            summary["liveNoLiquidityPoolCount"] = summary.get("liveNoLiquidityPoolCount") or 0
            summary["liveNoLiquidityPoolCount"] += 1
    return summary


def _pool_has_liquidity(candidate: dict[str, Any]) -> bool:
    has_liquidity = candidate.get("hasLiquidity")
    if has_liquidity is not None:
        if bool(has_liquidity):
            return True
    for key in ("liquidity", "liquidityIndex"):
        value = candidate.get(key)
        if value in (None, ""):
            continue
        try:
            if float(str(value)) > 0:
                return True
        except (TypeError, ValueError):
            pass
    for key in ("estimatedTvlUsd", "tvlUsd"):
        value = candidate.get(key)
        if value in (None, ""):
            continue
        try:
            if float(str(value)) > 0:
                return True
        except (TypeError, ValueError):
            pass
    balances = candidate.get("balances")
    if isinstance(balances, dict):
        value = balances.get("tvlUsd")
        if value not in (None, ""):
            try:
                return float(str(value)) > 0
            except (TypeError, ValueError):
                return False
    return False


def _pool_depth_value(candidate: dict[str, Any]) -> float:
    for key in ("estimatedTvlUsd", "tvlUsd"):
        value = candidate.get(key)
        if value in (None, ""):
            continue
        try:
            value = float(str(value))
            if value > 0:
                return value
        except (TypeError, ValueError):
            pass
    balances = candidate.get("balances")
    if isinstance(balances, dict):
        value = balances.get("tvlUsd")
        if value not in (None, ""):
            try:
                value = float(str(value))
                if value > 0:
                    return value
            except (TypeError, ValueError):
                pass
    for key in ("liquidity", "liquidityIndex"):
        value = candidate.get(key)
        if value in (None, ""):
            continue
        try:
            value = float(str(value))
            if value > 0:
                return value
        except (TypeError, ValueError):
            pass
    return 0.0


def _pool_reference_price(candidate: dict[str, Any]) -> float | None:
    """Return the token1-per-token0 spot reference from sqrtPriceX96."""
    sqrt_price = candidate.get("sqrtPriceX96")
    if sqrt_price in (None, ""):
        return None
    try:
        sqrt_price_int = int(str(sqrt_price))
    except (TypeError, ValueError):
        return None
    if sqrt_price_int <= 0:
        return None
    try:
        decimals0 = int(candidate.get("token0Decimals") or 18)
        decimals1 = int(candidate.get("token1Decimals") or 18)
    except (TypeError, ValueError):
        decimals0 = 18
        decimals1 = 18
    price = (sqrt_price_int / float(2**96)) ** 2
    return price * (10 ** (decimals0 - decimals1))


def _candidate_address_pair(candidate: dict[str, Any]) -> str:
    token0 = str(candidate.get("token0") or "").strip().lower()
    token1 = str(candidate.get("token1") or "").strip().lower()
    if token0 and token1:
        return ":".join(sorted((token0, token1)))
    symbols = sorted(
        (
            str(candidate.get("token0Symbol") or "").strip().upper(),
            str(candidate.get("token1Symbol") or "").strip().upper(),
        )
    )
    return "symbol:" + ":".join(symbols)


def _select_mainnet_representative_candidates(
    candidates: list[dict[str, Any]],
    *,
    max_candidates_per_pair: int = MAINNET_REPRESENTATIVE_CANDIDATE_LIMIT,
    fixed_fee_only: bool = True,
    liquid_only: bool = True,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Keep a bounded static candidate set that is still representative.

    Per token pair the cache keeps the deepest pool, the highest-priced and
    lowest-priced pools, and then fills the remaining slots with low-fee,
    liquid pools. When no pool has liquidity, the same rule runs over the
    indexed rows so a depth-less candidate remains visible for quote-time
    replacement without flooding the page with thousands of historical rows.
    """
    def _passes_fee_filter(candidate: dict[str, Any]) -> bool:
        if not fixed_fee_only:
            return True
        return _v4_pool_observation_candidate(candidate)

    def _passes_liquidity_filter(candidate: dict[str, Any]) -> bool:
        return bool(candidate.get("usableForObservation")) or _pool_has_liquidity(candidate)

    fixed_fee_candidates = [
        candidate
        for candidate in candidates
        if _passes_fee_filter(candidate)
    ]
    grouped: dict[str, list[dict[str, Any]]] = {}
    for candidate in fixed_fee_candidates:
        key = _candidate_address_pair(candidate)
        grouped.setdefault(key, []).append(candidate)

    selected: list[dict[str, Any]] = []
    pair_summaries: list[dict[str, Any]] = []
    kept_high_price: set[str] = set()
    kept_low_price: set[str] = set()
    kept_max_depth: set[str] = set()
    kept_low_fee: set[str] = set()

    def _sort_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return sorted(
            rows,
            key=lambda row: (
                not _pool_has_liquidity(row),
                -_pool_depth_value(row),
                int(row.get("fee") or 0) if str(row.get("fee") or "").lstrip("-").isdigit() else 0,
                str(row.get("pool") or row.get("poolId") or "").lower(),
            ),
        )

    for pair, rows in grouped.items():
        if max_candidates_per_pair <= 0:
            selected.extend(rows)
            continue
        liquid_rows = [row for row in rows if _pool_has_liquidity(row)]
        primary = liquid_rows if liquid_rows else rows
        ordered = _sort_rows(primary)
        required = set()

        if ordered:
            deepest = max(
                ordered,
                key=lambda row: (
                    _pool_has_liquidity(row),
                    _pool_depth_value(row),
                    -int(row.get("fee") or 0) if str(row.get("fee") or "").lstrip("-").isdigit() else 0,
                ),
            )
            required.add(str(deepest.get("pool") or deepest.get("poolId") or "").lower())
            kept_max_depth.add(str(deepest.get("pool") or deepest.get("poolId") or "").lower())

        priced = [row for row in primary if _pool_reference_price(row) is not None]
        if len(priced) >= 2:
            highest = max(priced, key=lambda row: _pool_reference_price(row) or 0.0)
            lowest = min(priced, key=lambda row: _pool_reference_price(row) or 0.0)
            required.add(str(highest.get("pool") or highest.get("poolId") or "").lower())
            required.add(str(lowest.get("pool") or lowest.get("poolId") or "").lower())
            kept_high_price.add(str(highest.get("pool") or highest.get("poolId") or "").lower())
            kept_low_price.add(str(lowest.get("pool") or lowest.get("poolId") or "").lower())

        ordered_by_depth = _sort_rows(primary)
        selected_ids: set[str] = set(required)
        selected_rows = [
            row
            for row in ordered_by_depth
            if str(row.get("pool") or row.get("poolId") or "").lower() in required
        ]
        for row in ordered_by_depth:
            if len(selected_rows) >= max_candidates_per_pair:
                break
            pool_id = str(row.get("pool") or row.get("poolId") or "").lower()
            if pool_id in selected_ids:
                continue
            selected_ids.add(pool_id)
            selected_rows.append(row)
        selected_rows = selected_rows[:max_candidates_per_pair]
        selected.extend(selected_rows)
        role_by_id: dict[str, list[str]] = {}
        for row in selected_rows:
            pool_id = str(row.get("pool") or row.get("poolId") or "").lower()
            roles: list[str] = []
            if pool_id in kept_max_depth:
                roles.append("max_depth")
            if pool_id in kept_high_price:
                roles.append("highest_price")
            if pool_id in kept_low_price:
                roles.append("lowest_price")
            if roles:
                role_by_id[pool_id] = roles
        for row in selected_rows:
            pool_id = str(row.get("pool") or row.get("poolId") or "").lower()
            row["representativeSelection"] = {
                "pair": pair,
                "maxCandidatesPerPair": max_candidates_per_pair,
                "totalPoolCount": len(rows),
                "liquidPoolCount": len(liquid_rows),
                "roles": role_by_id.get(pool_id) or [],
                "selectionBasis": "liquid_depth_desc_price_extremes_low_fee",
            }
        pair_summaries.append(
            {
                "pair": pair,
                "totalPoolCount": len(rows),
                "liquidPoolCount": len(liquid_rows),
                "selectedPoolCount": len(selected_rows),
                "keptHighPricePool": any(pool_id in kept_high_price for row in selected_rows for pool_id in [str(row.get("pool") or row.get("poolId") or "").lower()]),
                "keptLowPricePool": any(pool_id in kept_low_price for row in selected_rows for pool_id in [str(row.get("pool") or row.get("poolId") or "").lower()]),
                "keptMaxDepthPool": any(pool_id in kept_max_depth for row in selected_rows for pool_id in [str(row.get("pool") or row.get("poolId") or "").lower()]),
            }
        )

    return selected, pair_summaries


def build_mainnet_static_candidate_payload(
    index_payload: dict[str, Any],
    rows: list[dict[str, Any]],
    *,
    max_candidates_per_pair: int = MAINNET_REPRESENTATIVE_CANDIDATE_LIMIT,
    fixed_fee_only: bool = True,
    rpc_url: str | None = None,
) -> dict[str, Any]:
    pool_manager = str(index_payload.get("poolManager") or "").strip()
    all_candidates = [candidate for row in rows if (candidate := _v4_pool_candidate(row, pool_manager)) is not None]
    scoped_candidates = [candidate for candidate in all_candidates if _mainnet_pair_in_candidate_scope(candidate.get("token0"), candidate.get("token1"))]
    live_state_summary = _decorate_candidates_with_live_state(
        scoped_candidates,
        pool_manager=pool_manager,
        rpc_url=rpc_url or _mainnet_rpc_url(),
    )
    _selected, pair_summaries = _select_mainnet_representative_candidates(
        scoped_candidates,
        max_candidates_per_pair=max_candidates_per_pair,
        fixed_fee_only=fixed_fee_only,
        liquid_only=True,
    )
    abnormal_candidates = [
        candidate
        for candidate in scoped_candidates
        if not _v4_pool_observation_candidate(candidate)
    ]
    abnormal_selected: list[dict[str, Any]] = []
    if max_candidates_per_pair > 0 and abnormal_candidates:
        abnormal_selected, _ = _select_mainnet_representative_candidates(
            abnormal_candidates,
            max_candidates_per_pair=max_candidates_per_pair,
            fixed_fee_only=False,
            liquid_only=False,
        )
    selected_source = (
        scoped_candidates
        if max_candidates_per_pair <= 0
        else list(dict.fromkeys([*_selected, *abnormal_selected]))
    )
    selected = sorted(
        selected_source,
        key=lambda row: (
            _candidate_address_pair(row),
            not _pool_has_liquidity(row),
            -_pool_depth_value(row),
            int(row.get("fee") or 0) if str(row.get("fee") or "").lstrip("-").isdigit() else 0,
            str(row.get("pool") or row.get("poolId") or "").lower(),
        )
    )
    liquid_count = sum(1 for row in selected if _pool_has_liquidity(row))
    return {
        "schemaVersion": "robinhood-static-dex-candidates-v1",
        "mode": "mainnet_v4_configured_static_candidates",
        "network": "robinhood",
        "chainId": int(index_payload.get("chainId") or index_payload.get("chain_id") or 4663),
        "generatedAt": index_payload.get("generatedAt"),
        "generatedBlockNumber": index_payload.get("generatedBlockNumber"),
        "poolManager": pool_manager,
        "pools": selected,
        "summary": {
            "poolCount": len(selected),
            "candidateRouteCount": len(selected),
            "sourceIndexedPoolCount": len(rows),
            "sourceScopedPoolCount": len(scoped_candidates),
            "normalObservationPoolCount": len(
                [
                    candidate
                    for candidate in scoped_candidates
                    if _v4_pool_observation_candidate(candidate)
                ]
            ),
            "abnormalObservationPoolCount": len(abnormal_candidates),
            "fixedFeePoolCount": len(
                [
                    candidate
                    for candidate in scoped_candidates
                    if not _v4_is_special_fee_candidate(candidate)
                ]
            ),
            "specialFeePoolCount": len(
                [
                    candidate
                    for candidate in scoped_candidates
                    if _v4_is_special_fee_candidate(candidate)
                ]
            ),
            "representativeSelection": {
                "maxCandidatesPerPair": max_candidates_per_pair,
                "selectedPairCount": len(pair_summaries),
                "liquidPoolCount": liquid_count,
                "keptMaxDepthPoolCount": sum(1 for item in pair_summaries if item["keptMaxDepthPool"]),
                "keptHighPricePoolCount": sum(1 for item in pair_summaries if item["keptHighPricePool"]),
                "keptLowPricePoolCount": sum(1 for item in pair_summaries if item["keptLowPricePool"]),
                "pairSummaries": pair_summaries,
                "selectionBasis": "liquid_depth_desc_price_extremes_low_fee",
                "fixedFeeOnly": fixed_fee_only,
            },
            "liveState": live_state_summary,
        },
    }


def _symbol(value: object) -> str:
    return str(value or "").strip().upper()


def _address(value: object) -> str:
    text = str(value or "").strip()
    return text if text.startswith("0x") and len(text) == 42 else ""


def _shared_candidate_key(row: dict[str, Any]) -> str:
    pool_id = str(row.get("pool") or "").strip().lower()
    counter_address = _address(row.get("counterAddress")).lower()
    counter_symbol = _symbol(row.get("counterSymbol"))
    fee = str(row.get("fee") or "").strip().lower()
    if pool_id:
        return ":".join(("pool", pool_id, counter_address or counter_symbol, fee))
    token0 = _address(row.get("token0")).lower()
    token1 = _address(row.get("token1")).lower()
    token_pair = ":".join(sorted(address for address in (token0, token1) if address))
    return ":".join(("pair", token_pair, counter_address or counter_symbol, fee))


def _shared_candidate_row(anchor_symbol: str, yc_row: dict[str, Any] | None, zc_row: dict[str, Any] | None) -> dict[str, Any]:
    base = dict(yc_row or zc_row or {})
    counter_symbol = _symbol(base.get("counterSymbol"))
    route_text = f"{anchor_symbol} <-> {counter_symbol}" if counter_symbol else str(base.get("routeText") or "")
    base.update(
        {
            "direction": "shared",
            "directions": ["yc", "zc"],
            "candidateSetMode": "bidirectional_same_pool_key",
            "routeText": route_text,
            "ycRouteText": (yc_row or {}).get("routeText"),
            "zcRouteText": (zc_row or {}).get("routeText"),
            "ycRouteKey": (yc_row or {}).get("routeKey"),
            "zcRouteKey": (zc_row or {}).get("routeKey"),
        }
    )
    return base


def _shared_anchor_address(shared_pools: list[dict[str, Any]]) -> str:
    """Return the anchor currency address shared by a YC/ZC pool set."""
    for row in shared_pools:
        if isinstance(row, dict):
            address = _address(row.get("anchorAddress")).lower()
            if address:
                return address
    return ""


def _pool_fixed_fee_for_shared_token(row: dict[str, Any]) -> bool:
    if _v4_is_special_fee_candidate(row):
        return False
    try:
        return int(str(row.get("fee") or "").strip() or 0) > 0
    except (TypeError, ValueError):
        return False


def _pool_zero_hook(row: dict[str, Any]) -> bool:
    hooks = str(row.get("hooks") or "").strip().lower()
    return not hooks or hooks == "0x0000000000000000000000000000000000000000"


def _pool_anchor_counter_price(row: dict[str, Any], anchor_address: str) -> float | None:
    """Return counter tokens per one anchor token for this V4 pool.

    The reference is always the flash-fund anchor currency.  For the YC hop
    (anchor -> counter) the optimal pool sells the anchor at the highest
    counter-per-anchor price; for the ZC hop (counter -> anchor) it buys the
    anchor back at the lowest counter-per-anchor price.
    """
    token0 = _address(row.get("token0") or row.get("currency0")).lower()
    token1 = _address(row.get("token1") or row.get("currency1")).lower()
    anchor = str(anchor_address or "").strip().lower()
    if not token0 or not token1 or anchor not in (token0, token1):
        return None
    sqrt_price = row.get("sqrtPriceX96")
    if sqrt_price in (None, ""):
        return None
    try:
        sqrt_price_int = int(str(sqrt_price))
        decimals0 = int(row.get("token0Decimals") or 18)
        decimals1 = int(row.get("token1Decimals") or 18)
    except (TypeError, ValueError):
        return None
    if sqrt_price_int <= 0:
        return None
    token1_per_token0 = (sqrt_price_int / float(2**96)) ** 2
    token1_per_token0 *= 10 ** (decimals0 - decimals1)
    if token1_per_token0 <= 0:
        return None
    return token1_per_token0 if anchor == token0 else 1.0 / token1_per_token0


def _select_shared_token_optimal_pools(
    rows: list[dict[str, Any]],
    anchor_address: str,
) -> dict[str, Any]:
    """Select the unique YC and ZC optimal pools for one YC/ZC token.

    A pool must be a fixed-fee, liquid, no-hook pool when one exists.  If the
    token has only one pool that pool is used as the optimal pool.  Prices are
    compared in counter-per-anchor terms and obvious outliers are excluded.
    """
    if not rows:
        return {
            "ycOptimalPool": None,
            "zcOptimalPool": None,
            "ycOptimalPoolId": None,
            "zcOptimalPoolId": None,
            "hasYcOptimalPool": False,
            "hasZcOptimalPool": False,
            "optimalPoolCount": 0,
            "optimalPoolSelection": {"basis": "fixed_fee_liquid_no_hook_depth_price", "error": None},
        }
    preferred = [
        row
        for row in rows
        if _pool_fixed_fee_for_shared_token(row) and _pool_has_liquidity(row) and _pool_zero_hook(row)
    ]
    if not preferred:
        preferred = [row for row in rows if _pool_fixed_fee_for_shared_token(row) and _pool_has_liquidity(row)]
    if not preferred:
        preferred = [row for row in rows if _pool_has_liquidity(row)]
    if not preferred:
        preferred = list(rows)

    priced = [
        (row, price)
        for row in preferred
        if (price := _pool_anchor_counter_price(row, anchor_address)) is not None
    ]
    selected = preferred
    price_note = "price_outlier_filter_applied"
    if priced:
        prices = sorted(price for _row, price in priced)
        median = prices[len(prices) // 2]
        if median > 0:
            bounded = [item for item in priced if median / 2.0 <= item[1] <= median * 2.0]
            if bounded:
                selected = [row for row, _price in bounded]
        if len(priced) == 1:
            price_note = "single_priced_pool_used"

    def _sort_key(row: dict[str, Any], price: float | None) -> tuple:
        return (
            not _pool_fixed_fee_for_shared_token(row),
            not _pool_has_liquidity(row),
            -_pool_depth_value(row),
            -(price if price is not None else 0.0),
            int(row.get("fee") or 0) if str(row.get("fee") or "").lstrip("-").isdigit() else 0,
            str(row.get("pool") or row.get("poolId") or "").lower(),
        )

    yc_row = None
    zc_row = None
    if len(selected) == 1:
        yc_row = selected[0]
        zc_row = selected[0]
    else:
        price_by_row = {str(row.get("pool") or row.get("poolId") or "").lower(): price for row, price in priced}
        yc_candidates = sorted(
            selected,
            key=lambda row: _sort_key(row, price_by_row.get(str(row.get("pool") or row.get("poolId") or "").lower())),
        )
        zc_candidates = sorted(
            selected,
            key=lambda row: (
                not _pool_fixed_fee_for_shared_token(row),
                not _pool_has_liquidity(row),
                -_pool_depth_value(row),
                price_by_row.get(str(row.get("pool") or row.get("poolId") or "").lower()) or float("inf"),
                int(row.get("fee") or 0) if str(row.get("fee") or "").lstrip("-").isdigit() else 0,
                str(row.get("pool") or row.get("poolId") or "").lower(),
            ),
        )
        if yc_candidates:
            yc_row = yc_candidates[0]
        if zc_candidates:
            zc_row = zc_candidates[0]

    def _annotate(row: dict[str, Any] | None, role: str) -> dict[str, Any] | None:
        if row is None:
            return None
        copied = dict(row)
        copied["optimalRole"] = role
        copied["optimalBasis"] = "fixed_fee_liquid_no_hook_depth_price"
        copied["optimalSelection"] = {
            "reference": "anchor_counter_price",
            "candidatePoolCount": len(rows),
            "selectedPoolCount": len(selected),
            "pricedPoolCount": len(priced),
            "priceOutlierFilter": price_note,
        }
        return copied

    yc_optimal = _annotate(yc_row, "yc_anchor_to_counter_highest_price")
    zc_optimal = _annotate(zc_row, "zc_counter_to_anchor_lowest_price")
    return {
        "ycOptimalPool": yc_optimal,
        "zcOptimalPool": zc_optimal,
        "ycOptimalPoolId": str(yc_row.get("pool") or yc_row.get("poolId") or "") if yc_row else None,
        "zcOptimalPoolId": str(zc_row.get("pool") or zc_row.get("poolId") or "") if zc_row else None,
        "hasYcOptimalPool": bool(yc_row),
        "hasZcOptimalPool": bool(zc_row),
        "optimalPoolCount": int(bool(yc_row)) + int(bool(zc_row)),
        "optimalPoolSelection": {
            "basis": "fixed_fee_liquid_no_hook_depth_price",
            "reference": "anchor_counter_price",
            "candidatePoolCount": len(rows),
            "selectedPoolCount": len(selected),
            "pricedPoolCount": len(priced),
            "priceOutlierFilter": price_note,
        },
    }


def _decorate_shared_token_optimal_pools(
    token: dict[str, Any],
    rows: list[dict[str, Any]],
    anchor_address: str,
) -> dict[str, Any]:
    decorated = dict(token)
    decorated.update(_select_shared_token_optimal_pools(rows, anchor_address))
    return decorated


def _annotate_shared_pool_recommended_hops(
    shared_pools: list[dict[str, Any]],
    shared_tokens: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Mark shared pool rows that are the first/third-hop recommendation."""
    role_by_pool_id: dict[str, list[str]] = {}
    for token in shared_tokens:
        for key, role in (
            ("ycOptimalPoolId", "yc_anchor_to_counter_highest_price"),
            ("zcOptimalPoolId", "zc_counter_to_anchor_lowest_price"),
        ):
            pool_id = str(token.get(key) or "").strip().lower()
            if pool_id and role not in role_by_pool_id.setdefault(pool_id, []):
                role_by_pool_id[pool_id].append(role)
    annotated: list[dict[str, Any]] = []
    for row in shared_pools:
        copied = dict(row)
        pool_id = str(row.get("pool") or row.get("poolId") or "").strip().lower()
        roles = role_by_pool_id.get(pool_id) or []
        copied["optimalRoles"] = roles
        copied["hopRecommendation"] = roles
        copied["isFirstHopRecommended"] = "yc_anchor_to_counter_highest_price" in roles
        copied["isThirdHopRecommended"] = "zc_counter_to_anchor_lowest_price" in roles
        annotated.append(copied)
    return annotated


def _shared_tokens_from_pools(
    shared_pools: list[dict[str, Any]],
    *,
    anchor_address: str = "",
) -> list[dict[str, Any]]:
    buckets: dict[str, dict[str, Any]] = {}
    for row in shared_pools:
        symbol = _symbol(row.get("counterSymbol"))
        if not symbol:
            continue
        address = _address(row.get("counterAddress"))
        pool_id = str(row.get("pool") or "").strip()
        bucket = buckets.setdefault(symbol, {"symbol": symbol, "address": address, "pools": [], "poolIds": set()})
        if address and not bucket.get("address"):
            bucket["address"] = address
        if pool_id and pool_id not in bucket["poolIds"]:
            bucket["poolIds"].add(pool_id)
            bucket["pools"].append(row)
    tokens = []
    anchor_address = _address(anchor_address).lower() or _shared_anchor_address(shared_pools)
    for token in sorted(buckets.values(), key=lambda item: str(item.get("address") or "").lower()):
        tokens.append(
            _decorate_shared_token_optimal_pools(
                {
                    "symbol": token["symbol"],
                    "address": token.get("address") or "",
                    "poolCount": str(len(token["pools"])),
                    "pools": token["pools"],
                },
                token["pools"],
                anchor_address,
            )
        )
    return tokens


def _yc_zc_token_cache_path(request: ChainFlowRequest) -> Path | None:
    try:
        return chain_data_file_path(request.network, "yc_zc_tokens", chain_id=request.chain_id)
    except (TypeError, ValueError, OSError):
        return None


def _load_yc_zc_token_cache(request: ChainFlowRequest) -> dict[str, Any] | None:
    path = _yc_zc_token_cache_path(request) or DEFAULT_ROBINHOOD_YC_ZC_TOKEN_CACHE
    if not path.exists():
        return None
    payload = read_json_object(path)
    tokens = payload.get("tokens") if isinstance(payload.get("tokens"), list) else []
    if not tokens:
        return None
    return payload


def _shared_tokens_from_cache(
    cache: dict[str, Any],
    shared_pools: list[dict[str, Any]],
    *,
    anchor_address: str = "",
) -> list[dict[str, Any]]:
    pool_by_symbol: dict[str, list[dict[str, Any]]] = {}
    pool_by_address: dict[str, list[dict[str, Any]]] = {}
    for row in shared_pools:
        symbol = _symbol(row.get("counterSymbol"))
        address = _address(row.get("counterAddress")).lower()
        if symbol:
            pool_by_symbol.setdefault(symbol, []).append(row)
        if address:
            pool_by_address.setdefault(address, []).append(row)
    tokens: list[dict[str, Any]] = []
    seen_symbols: set[str] = set()
    anchor_address = _address(anchor_address).lower() or _shared_anchor_address(shared_pools)
    for cached in cache.get("tokens") or []:
        if not isinstance(cached, dict):
            continue
        symbol = _symbol(cached.get("symbol"))
        if not symbol or symbol in seen_symbols:
            continue
        seen_symbols.add(symbol)
        address = _address(cached.get("address"))
        address_key = address.lower()
        pools = pool_by_symbol.get(symbol) or pool_by_address.get(address_key) or []
        tokens.append(
            _decorate_shared_token_optimal_pools(
                {
                    "symbol": symbol,
                    "address": address,
                    "decimals": cached.get("decimals"),
                    "poolCount": str(len(pools)),
                    "source": cached.get("source") or "yc_zc_token_cache",
                    "pools": pools,
                },
                pools,
                anchor_address,
            )
        )
    tokens.sort(key=lambda item: (str(item.get("address") or "").lower(), str(item.get("symbol") or "")))
    return tokens


def _mark_v4_shared_yc_zc_sets(
    anchor_groups: list[dict[str, Any]],
    stable_token_cache: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    marked: list[dict[str, Any]] = []
    for group in anchor_groups:
        if not isinstance(group, dict):
            continue
        yc_rows = [row for row in group.get("ycPools") or [] if isinstance(row, dict)]
        zc_rows = [row for row in group.get("zcPools") or [] if isinstance(row, dict)]
        yc_by_key = {_shared_candidate_key(row): row for row in yc_rows}
        zc_by_key = {_shared_candidate_key(row): row for row in zc_rows}
        same_set = bool(yc_by_key) and set(yc_by_key) == set(zc_by_key)
        if not same_set:
            marked.append(group)
            continue
        anchor_symbol = _symbol(group.get("anchorSymbol"))
        normal_shared_pools = [
            _shared_candidate_row(anchor_symbol, yc_by_key.get(key), zc_by_key.get(key))
            for key in sorted(yc_by_key)
            if _v4_pool_observation_candidate(yc_by_key.get(key) or {})
        ]
        abnormal_shared_pools = [
            _shared_candidate_row(anchor_symbol, yc_by_key.get(key), zc_by_key.get(key))
            for key in sorted(yc_by_key)
            if not _v4_pool_observation_candidate(yc_by_key.get(key) or {})
        ]
        shared_pools = normal_shared_pools
        shared_anchor_address = _shared_anchor_address(shared_pools)
        shared_tokens = (
            _shared_tokens_from_cache(
                stable_token_cache,
                shared_pools,
                anchor_address=shared_anchor_address,
            )
            if stable_token_cache
            else _shared_tokens_from_pools(
                shared_pools,
                anchor_address=shared_anchor_address,
            )
        )
        if anchor_symbol == "ETH":
            # Candidate pools are the authority for first/third-hop reachability.
            # Every YC/ZC token must be reachable from the anchor through at
            # least one candidate pool; otherwise it is not a valid YC/ZC token
            # for this candidate set and would only appear as a no-pool row.
            address_by_symbol: dict[str, tuple[str, int]] = {}
            seen_addresses: set[str] = set()
            shared_tokens = []
            for row in shared_pools:
                counter_symbol = _symbol(row.get("counterSymbol"))
                counter_address = _address(row.get("counterAddress"))
                if not counter_symbol or not counter_address:
                    continue
                if counter_address in seen_addresses:
                    continue
                seen_addresses.add(counter_address)
                address_by_symbol[counter_symbol] = (counter_address, len(shared_pools))
            for counter_symbol, (counter_address, _pool_count) in sorted(
                address_by_symbol.items(),
                key=lambda item: str(item[1][0] or "").lower(),
            ):
                counter_pools = [
                    row
                    for row in shared_pools
                    if _address(row.get("counterAddress")) == counter_address
                ]
                shared_tokens.append(
                    _decorate_shared_token_optimal_pools(
                        {
                            "symbol": counter_symbol,
                            "address": counter_address,
                            "poolCount": str(len(counter_pools)),
                            "pools": counter_pools,
                        },
                        counter_pools,
                        shared_anchor_address,
                    )
                )
        shared_pools = _annotate_shared_pool_recommended_hops(shared_pools, shared_tokens)
        abnormal_anchor_address = _shared_anchor_address(abnormal_shared_pools)
        abnormal_tokens = _shared_tokens_from_pools(
            abnormal_shared_pools,
            anchor_address=abnormal_anchor_address,
        )
        marked.append(
            {
                **group,
                "candidateSetMode": "bidirectional_same_pool_key_set",
                "ycZcSameSet": True,
                "ycZcSharedPoolSet": True,
                "sharedPools": shared_pools,
                "sharedTokens": shared_tokens,
                "sharedPoolCount": str(len(shared_pools)),
                "sharedTokenCount": str(len(shared_tokens)),
                "ycPools": [],
                "zcPools": [],
                "ycTokens": [],
                "zcTokens": [],
                "ycPoolsRef": "sharedPools",
                "zcPoolsRef": "sharedPools",
                "ycTokensRef": "sharedTokens",
                "zcTokensRef": "sharedTokens",
                "ycPoolCount": str(len(shared_pools)),
                "zcPoolCount": str(len(shared_pools)),
                "ycTokenCount": str(len(shared_tokens)),
                "zcTokenCount": str(len(shared_tokens)),
                "candidatePoolCount": str(len(shared_pools)),
                "candidateQueueCount": str(len(shared_pools) * 2),
                "abnormalPools": abnormal_shared_pools,
                "abnormalTokens": abnormal_tokens,
                "abnormalPoolCount": str(len(abnormal_shared_pools)),
                "abnormalTokenCount": str(len(abnormal_tokens)),
                "abnormalDirectionCount": str(len(abnormal_shared_pools) * 2),
            }
        )
    return marked


def _mainnet_reconcile_shared_yc_zc_sets(payload: dict[str, Any]) -> dict[str, Any]:
    """Apply the V4 YC=ZC pool-set merge to a static candidate payload."""
    anchor_groups = _mark_v4_shared_yc_zc_sets(payload.get("anchorGroups") or [])
    shared_group_count = sum(1 for group in anchor_groups if group.get("ycZcSameSet"))
    shared_pool_count = sum(int(group.get("sharedPoolCount") or 0) for group in anchor_groups)
    abnormal_pool_count = sum(int(group.get("abnormalPoolCount") or 0) for group in anchor_groups)
    abnormal_token_count = sum(int(group.get("abnormalTokenCount") or 0) for group in anchor_groups)
    summary = dict(payload.get("summary") or {})
    summary.update(
        {
            "ycPoolCount": str(sum(int(group.get("ycPoolCount") or 0) for group in anchor_groups)),
            "zcPoolCount": str(sum(int(group.get("zcPoolCount") or 0) for group in anchor_groups)),
            "ycTokenCount": str(sum(int(group.get("ycTokenCount") or 0) for group in anchor_groups)),
            "zcTokenCount": str(sum(int(group.get("zcTokenCount") or 0) for group in anchor_groups)),
            "ycZcSameSetGroupCount": str(shared_group_count),
            "sharedPoolSetCount": str(shared_pool_count),
            "abnormalStaticPoolCount": str(abnormal_pool_count),
            "abnormalStaticTokenCount": str(abnormal_token_count),
            "candidateSetStorageMode": "shared_yc_zc_pool_set" if shared_group_count else "directional_pool_sets",
        }
    )
    payload["anchorGroups"] = anchor_groups
    payload["summary"] = summary
    return payload


def _mainnet_v4_payload(request: ChainFlowRequest, path: Path, index_payload: dict[str, Any], rows: list[dict[str, Any]]) -> dict[str, Any]:
    pool_manager = str(index_payload.get("poolManager") or "").strip()
    all_candidates = [candidate for row in rows if (candidate := _v4_pool_candidate(row, pool_manager)) is not None]
    candidates: list[dict[str, Any]] = []
    for candidate in all_candidates:
        token0 = str(candidate.get("token0") or "").strip().lower()
        token1 = str(candidate.get("token1") or "").strip().lower()
        if _mainnet_pair_in_candidate_scope(token0, token1):
            candidates.append(candidate)
    grouped: dict[str, dict[str, Any]] = {}
    for candidate in candidates:
        token_pair = tuple(sorted((str(candidate.get("token0") or "").lower(), str(candidate.get("token1") or "").lower())))
        key = ":".join(token_pair) if all(token_pair) else str(candidate.get("symbolPair") or "")
        bucket = grouped.setdefault(
            key,
            {
                "symbolPair": candidate.get("symbolPair"),
                "addressPair": ":".join(token_pair),
                "identityMode": "v4_pool_id",
                "tokenX": candidate.get("token0"),
                "tokenY": candidate.get("token1"),
                "tokenXSymbol": candidate.get("token0Symbol"),
                "tokenYSymbol": candidate.get("token1Symbol"),
                "classification": pair_classification(candidate.get("token0Symbol"), candidate.get("token1Symbol")),
                "allPools": [],
                "strictQualityPools": [],
                "preferredPools": [],
                "executionPools": [],
                "rejectedPools": [],
            },
        )
        bucket["allPools"].append(candidate)
        bucket["strictQualityPools"].append(candidate)
        bucket["preferredPools"].append(candidate)
        bucket["executionPools"].append(candidate)

    pairs: list[dict[str, Any]] = []
    for pair in grouped.values():
        pair["allPoolCount"] = str(len(pair["allPools"]))
        pair["strictQualityPoolCount"] = str(len(pair["allPools"]))
        pair["preferredPoolCount"] = str(len(pair["allPools"]))
        pair["executionPoolCount"] = str(min(5, len(pair["allPools"])))
        pair["rejectedPoolCount"] = "0"
        pair["hasStrictQualityPools"] = bool(pair["strictQualityPools"])
        pair["hasPreferredPools"] = bool(pair["preferredPools"])
        pair["rejectReasonCounts"] = {}
        pairs.append(pair)

    pairs.sort(key=lambda item: str(item.get("symbolPair") or ""))
    yc_zc_token_cache = _load_yc_zc_token_cache(request)
    # The cache is display metadata only. It must not filter the candidate pool
    # set: YC/ZC token discovery depends on pools, so filtering pools by the
    # YC/ZC cache would create a circular dependency and silently drop real
    # first/third-hop candidates.
    anchor_groups = _mark_v4_shared_yc_zc_sets(
        build_anchor_candidate_groups(pairs, anchor_symbols=_mainnet_configured_anchor_symbols()),
        stable_token_cache=yc_zc_token_cache,
    )
    shared_group_count = sum(1 for group in anchor_groups if group.get("ycZcSameSet"))
    shared_pool_count = sum(int(group.get("sharedPoolCount") or 0) for group in anchor_groups)
    all_pools_by_address_pair: dict[str, list[dict[str, Any]]] = {}
    for pair in pairs:
        address_pair = str(pair.get("addressPair") or "").strip()
        if not address_pair:
            continue
        all_pools_by_address_pair[address_pair] = list(pair.get("allPools") or [])
    return {
        "mode": "dex_pool_candidates",
        "source": "mainnet_v4_pool_index",
        "fallbackReason": None if all_candidates else "mainnet_v4_pool_index_has_no_decoded_pools",
        "generatedAt": index_payload.get("generatedAt"),
        "freshness": index_payload.get("generatedAt"),
        "poolStaticTable": {
            "path": str(path),
            "exists": bool(all_candidates),
            "mode": index_payload.get("schemaVersion") or "robinhood-v4-pool-index-v1",
            "network": request.network,
            "chainId": request.chain_id,
            "generatedAt": index_payload.get("generatedAt"),
            "generatedBlockNumber": index_payload.get("generatedBlockNumber"),
            "poolManager": pool_manager,
        },
        "filters": {"source": "mainnet v4 pool index", "protocol": "uniswap_v4", "identity": "poolId/poolKey"},
        "summary": {
            "candidateRouteCount": str(len(all_candidates)),
            "staticPoolCount": str(len(all_candidates)),
            "staticPairCount": str(len(pairs)),
            "pairTableCount": str(len(pairs)),
            "anchorGroupCount": str(len(anchor_groups)),
            "ycPoolCount": str(sum(int(group.get("ycPoolCount") or 0) for group in anchor_groups)),
            "zcPoolCount": str(sum(int(group.get("zcPoolCount") or 0) for group in anchor_groups)),
            "ycTokenCount": str(sum(int(group.get("ycTokenCount") or 0) for group in anchor_groups)),
            "zcTokenCount": str(sum(int(group.get("zcTokenCount") or 0) for group in anchor_groups)),
            "ycZcSameSetGroupCount": str(shared_group_count),
            "sharedPoolSetCount": str(shared_pool_count),
            "candidateSetStorageMode": "shared_yc_zc_pool_set" if shared_group_count else "directional_pool_sets",
            "ycZcTokenCache": "loaded" if yc_zc_token_cache else "missing",
            "ycZcTokenCacheCount": str(len(yc_zc_token_cache.get("tokens") or [])) if yc_zc_token_cache else "0",
            "ycZcTokenCacheSource": str(_yc_zc_token_cache_path(request) or "") if yc_zc_token_cache else "",
            "preferredPoolCount": str(len(all_candidates)),
            "executionPoolCount": str(sum(len(pair["executionPools"]) for pair in pairs)),
            "strictQualityPoolCount": str(len(all_candidates)),
            "pairsWithStrictQualityPools": str(sum(1 for pair in pairs if pair["hasStrictQualityPools"])),
            "pairsWithPreferredPools": str(sum(1 for pair in pairs if pair["hasPreferredPools"])),
            "v4IndexedPoolCount": str(len(rows)),
        },
        "pairs": pairs,
        "anchorGroups": anchor_groups,
        "allPoolsByAddressPair": all_pools_by_address_pair,
        "errors": [] if all_candidates else ["mainnet_v4_pool_index_has_no_decoded_pools"],
    }


def dex_pool_candidates_payload(
    *,
    request: ChainFlowRequest,
    pool_cache_path: Path | None = None,
    market_pool_path: Path | None = None,
    evidence_root: Path | None = None,
    **_: Any,
) -> dict[str, Any]:
    protocol_set = {str(item or "").strip().lower() for item in request.flow.get("allowed_protocols") or []}
    if (
        str(request.network or "").strip().lower() == "robinhood"
        and int(request.chain_id or 0) == 4663
        and (not protocol_set or "uniswap_v4" in protocol_set)
    ):
        from core.db.page_payloads import build_candidate_payload as _build_db_candidate_payload

        return _build_db_candidate_payload(
            chain_id=4663,
            protocols=sorted(protocol_set) if protocol_set else None,
        )
    path = (
        pool_cache_path
        or chain_data_file_path(request.network, "dex_candidates", chain_id=request.chain_id)
        or DEFAULT_ROBINHOOD_DEX_CANDIDATE_PATH
    )
    table = read_json_object(path)
    if table and (not protocol_set or "uniswap_v4" in protocol_set):
        payload = build_robinhood_pool_candidates(
            table,
            path,
            market_pool_path=market_pool_path
            or chain_data_file_path(request.network, "dex_market_pools", chain_id=request.chain_id)
            or DEFAULT_ROBINHOOD_DEX_MARKET_POOL_PATH,
            evidence_root=evidence_root
            or chain_data_file_path(request.network, "dex_discovery", chain_id=request.chain_id)
            or DEFAULT_ROBINHOOD_DEX_EVIDENCE_ROOT,
            source="runtime_cache" if table else "local_json_missing",
            fallback_reason=None if table else "dex_pool_static_table_unavailable",
        )
        payload["tokenSymbols"] = list(MAINNET_CONFIGURED_SYMBOLS)
        return _mainnet_reconcile_shared_yc_zc_sets(payload)
    if not protocol_set or "uniswap_v4" in protocol_set:
        v4_path = _v4_index_path(request)
        index_payload, v4_rows = _v4_index_rows(v4_path)
        if v4_rows:
            return _mainnet_v4_payload(request, v4_path or Path(""), index_payload, v4_rows)
    market_path = (
        market_pool_path
        or chain_data_file_path(request.network, "dex_market_pools", chain_id=request.chain_id)
        or DEFAULT_ROBINHOOD_DEX_MARKET_POOL_PATH
    )
    evidence_path = (
        evidence_root
        or chain_data_file_path(request.network, "dex_discovery", chain_id=request.chain_id)
        or DEFAULT_ROBINHOOD_DEX_EVIDENCE_ROOT
    )
    return _mainnet_reconcile_shared_yc_zc_sets(
        build_robinhood_pool_candidates(
            table,
            path,
            market_pool_path=market_path,
            evidence_root=evidence_path,
            source="runtime_cache" if table else "local_json_missing",
            fallback_reason=None if table else "dex_pool_static_table_unavailable",
        )
    )


__all__ = ["dex_pool_candidates_payload"]
