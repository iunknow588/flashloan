"""V4 and pool-level helpers for DEX surface payloads."""

from __future__ import annotations

from decimal import Decimal
from functools import lru_cache
import os
import threading
from typing import Any

from core.chain_data_registry import chain_data_by_chain_id
from dex.intent_trade.chains.robinhood import config as robinhood_config
from dex.intent_trade.chains.robinhood.shared import ROBINHOOD_TESTNET_CHAIN_ID
from dex.intent_trade.chains.robinhood_common.rpc_gate import (
    v4_live_quote_enabled as _rpc_gate_v4_live_quote_enabled,
)
from market.binance_market.dex_surface_types import (
    _norm_symbol,
    _number,
    _optional_number,
    _pool_protocol,
    _pool_side_index,
    _pool_symbols,
    _pool_tokens,
    _text,
    _token_address,
)
from web3 import Web3

DEFAULT_AMM_FEE_BPS = 30.0
_V4_TOKEN_METADATA_CACHE: dict[tuple[str, int | None], dict[str, dict[str, Any]]] = {}
_V4_TOKEN_METADATA_CACHE_LOCK = threading.Lock()
ERC20_DECIMALS_ABI = [
    {
        "inputs": [],
        "name": "decimals",
        "outputs": [{"type": "uint8"}],
        "stateMutability": "view",
        "type": "function",
    }
]


def _v4_live_token_metadata_enabled(chain_id: int | None = None) -> bool:
    if not _env_flag("DEX_V4_LIVE_TOKEN_METADATA_ENABLED", default=False):
        return False
    if chain_id is None:
        return True
    return _rpc_gate_v4_live_quote_enabled(chain_id=chain_id)


def _env_flag(name: str, *, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _pool_manager_address(pool: dict[str, Any], chain_id: int | None = None) -> str:
    candidates = [pool.get("poolManager"), pool.get("pool_manager")]
    if chain_id is not None:
        row = chain_data_by_chain_id(chain_id) or {}
        contracts = row.get("contracts") if isinstance(row.get("contracts"), dict) else {}
        candidates.extend([contracts.get("v4_pool_manager"), row.get("defaultV4PoolManager")])
    for candidate in candidates:
        text = _text(candidate)
        if text.startswith("0x") and len(text) == 42:
            return text.lower()
    return ""
def _v4_token_metadata_cache(pool_manager: str, *, chain_id: int | None = None) -> dict[str, dict[str, Any]]:
    normalized_pool_manager = _text(pool_manager).lower()
    cache_key = (normalized_pool_manager, chain_id)
    with _V4_TOKEN_METADATA_CACHE_LOCK:
        cached = _V4_TOKEN_METADATA_CACHE.get(cache_key)
    if cached is not None:
        return cached
    try:
        from core.db.database_path import absolute_database_path
        from core.db.storage_chain_core import load_chain_tokens

        resolved_chain_id = int(chain_id or 0)
        network = (
            "robinhood-testnet"
            if resolved_chain_id == ROBINHOOD_TESTNET_CHAIN_ID
            else "robinhood"
        )
        rows = load_chain_tokens(
            str(absolute_database_path()),
            network=network,
            chain_id=resolved_chain_id,
        )
    except Exception:
        rows = []
    metadata: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        normalized = _token_address(row.get("token_address"))
        if not normalized:
            continue
        symbol = _norm_symbol(row.get("token_symbol"))
        decimals = row.get("token_decimals")
        try:
            decimals = int(decimals) if decimals not in (None, "") else None
        except (TypeError, ValueError):
            decimals = None
        metadata[normalized] = {
            "address": normalized,
            "symbol": symbol,
            "decimals": decimals,
            "metadataOk": bool(symbol and decimals is not None),
            "source": "sqlite_db",
        }
    with _V4_TOKEN_METADATA_CACHE_LOCK:
        _V4_TOKEN_METADATA_CACHE[cache_key] = metadata
    return metadata


def clear_v4_token_metadata_cache() -> int:
    """Drop cached token metadata after a token-balance scan rebuild."""
    with _V4_TOKEN_METADATA_CACHE_LOCK:
        cleared = len(_V4_TOKEN_METADATA_CACHE)
        _V4_TOKEN_METADATA_CACHE.clear()
        return cleared
def _v4_pool_token_metadata(pool: dict[str, Any], index: int, *, chain_id: int | None = None) -> dict[str, Any]:
    address = _token_address(pool.get(f"token{index}"))
    symbol = _norm_symbol(pool.get(f"token{index}Symbol"))
    decimals_value = pool.get(f"token{index}Decimals") or pool.get(f"token{index}_decimals")
    try:
        decimals = int(decimals_value) if decimals_value not in (None, "") else None
    except (TypeError, ValueError):
        decimals = None
    if address == "0x0000000000000000000000000000000000000000":
        return {
            "address": address,
            "symbol": symbol or "ETH",
            "decimals": 18,
            "metadataOk": True,
            "source": "native_currency",
        }
    if address:
        cached = _v4_token_metadata_cache(_pool_manager_address(pool, chain_id), chain_id=chain_id).get(address.lower())
        if cached:
            symbol = symbol or _norm_symbol(cached.get("symbol"))
            if decimals is None:
                try:
                    cached_decimals = cached.get("decimals")
                    decimals = int(cached_decimals) if cached_decimals not in (None, "") else None
                except (TypeError, ValueError):
                    decimals = None
            if decimals is not None:
                return {
                    "address": address,
                    "symbol": symbol or address[:8].upper(),
                    "decimals": decimals,
                    "metadataOk": bool(symbol and decimals is not None),
                    "source": "sqlite_db",
                }
        if _v4_live_token_metadata_enabled(chain_id=chain_id):
            live_decimals = _live_v4_token_decimals(address, chain_id=chain_id)
            if live_decimals is not None:
                if not symbol:
                    symbol = address[:8].upper()
                return {
                    "address": address,
                    "symbol": symbol,
                    "decimals": live_decimals,
                    "metadataOk": bool(symbol and live_decimals is not None),
                    "source": "chain_live_erc20_decimals",
                }
    if not symbol and address:
        symbol = address[:8].upper()
    payload = {
        "address": address,
        "symbol": symbol,
        "decimals": decimals,
        "metadataOk": bool(symbol and decimals is not None),
        "source": "pool_index",
    }
    return payload
def _v4_token_decimals(pool: dict[str, Any], index: int, *, chain_id: int | None = None) -> int | None:
    metadata = _v4_pool_token_metadata(pool, index, chain_id=chain_id)
    decimals = metadata.get("decimals")
    try:
        return int(decimals) if decimals not in (None, "") else None
    except (TypeError, ValueError):
        return None
def _live_v4_token_decimals(address: str, *, chain_id: int | None = None) -> int | None:
    normalized = _token_address(address)
    if not normalized or normalized == "0x0000000000000000000000000000000000000000":
        return 18
    row = chain_data_by_chain_id(chain_id or 0) or {}
    rpc_url = str(row.get("rpc_url") or row.get("rpcUrl") or "").strip()
    if not rpc_url:
        return None
    timeouts = (
        robinhood_config.testnet_rpc_connect_timeouts()
        if int(chain_id or 0) == ROBINHOOD_TESTNET_CHAIN_ID
        else (float(os.getenv("DEX_ONCHAIN_RPC_TIMEOUT_SECONDS", "12") or 12),)
    )
    for timeout in timeouts:
        try:
            w3 = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": timeout}))
            if not w3.is_connected():
                continue
            contract = w3.eth.contract(address=Web3.to_checksum_address(normalized), abi=ERC20_DECIMALS_ABI)
            return int(contract.functions.decimals().call())
        except Exception:
            continue
    return None
def _pool_tvl_usd(pool: dict[str, Any]) -> float:
    balances = pool.get("balances") if isinstance(pool.get("balances"), dict) else {}
    return _number(balances.get("tvlUsd") if balances else pool.get("tvlUsd"))
def _pool_reserve_amount(pool: dict[str, Any], index: int) -> float | None:
    balances = pool.get("balances") if isinstance(pool.get("balances"), dict) else {}
    keys = ("token0", "token1")
    legacy_keys = ("token0Amount", "token1Amount")
    value = balances.get(keys[index]) if balances else pool.get(legacy_keys[index])
    parsed = _optional_number(value)
    return parsed if parsed is not None and parsed > 0 else None
def _pool_reserve_usd(pool: dict[str, Any], index: int, price_map: dict[str, float] | None = None) -> float | None:
    balances = pool.get("balances") if isinstance(pool.get("balances"), dict) else {}
    usd_keys = ("token0Usd", "token1Usd")
    value = balances.get(usd_keys[index]) if balances else pool.get(usd_keys[index])
    parsed = _optional_number(value)
    if parsed is not None and parsed > 0:
        return parsed
    symbols = _pool_symbols(pool)
    amount = _pool_reserve_amount(pool, index)
    if amount is not None and index < len(symbols) and price_map:
        price = price_map.get(symbols[index])
        if price is not None and price > 0:
            return amount * price
    return None
def _pool_token_decimals_trusted(pool: dict[str, Any], index: int, *, chain_id: int | None = None) -> bool:
    return _v4_token_decimals(pool, index, chain_id=chain_id) is not None
def _fee_bps(pool: dict[str, Any]) -> float:
    raw = str(pool.get("fee") or "").strip().lower()
    protocol = str(pool.get("protocol") or pool.get("dex") or "").strip().lower()
    if "bps" in raw:
        parsed = _optional_number(raw.replace("bps", "").strip())
        return parsed if parsed is not None else DEFAULT_AMM_FEE_BPS
    parsed = _optional_number(raw)
    if parsed is not None:
        if parsed > 100:
            return parsed / 100.0
        if protocol in {"uniswap_v3", "pancakeswap_v3", "v3"} and parsed in {1, 5, 30, 100}:
            return parsed
        if protocol in {"uniswap_v3", "pancakeswap_v3", "v3"}:
            return parsed / 100.0
        return parsed
    if "v2" in raw or "v2" in protocol:
        return DEFAULT_AMM_FEE_BPS
    return DEFAULT_AMM_FEE_BPS
def _is_constant_product_pool(pool: dict[str, Any]) -> bool:
    text = f"{pool.get('protocol') or ''} {pool.get('dex') or ''} {pool.get('fee') or ''}".lower()
    return "v2" in text and "v3" not in text and "v4" not in text
def _market_reference_rate(pool: dict[str, Any], from_symbol: str, to_symbol: str) -> float | None:
    market = pool.get("market") if isinstance(pool.get("market"), dict) else {}
    price_native = _optional_number(market.get("priceNative"))
    if price_native is None or price_native <= 0:
        return None
    symbols = _pool_symbols(pool)
    if len(symbols) < 2:
        return None
    token0, token1 = symbols[:2]
    if from_symbol == token0 and to_symbol == token1:
        return price_native
    if from_symbol == token1 and to_symbol == token0:
        return 1.0 / price_native
    return None
def _pool_sqrt_price_x96(pool: dict[str, Any]) -> int | None:
    candidates = [
        pool.get("sqrtPriceX96"),
        pool.get("sqrt_price_x96"),
        pool.get("latestSwapSqrtPriceX96"),
        pool.get("latest_swap_sqrt_price_x96"),
    ]
    pool_key = pool.get("poolKey") if isinstance(pool.get("poolKey"), dict) else {}
    if isinstance(pool_key, dict):
        candidates.extend([
            pool_key.get("sqrtPriceX96"),
            pool_key.get("sqrt_price_x96"),
        ])
    for value in candidates:
        try:
            parsed = int(str(value or "").strip())
        except (TypeError, ValueError):
            continue
        if parsed > 0:
            return parsed
    return None
def _v4_spot_rate(pool: dict[str, Any], from_symbol: str, to_symbol: str, *, chain_id: int | None = None) -> float | None:
    if _pool_protocol(pool) != "uniswap_v4":
        return None
    sqrt_price_x96 = _pool_sqrt_price_x96(pool)
    if not sqrt_price_x96:
        return None
    tokens = _pool_tokens(pool)
    if len(tokens) < 2:
        return None
    token0, token1 = tokens[:2]
    if {from_symbol, to_symbol} != {token0.get("symbol"), token1.get("symbol")}:
        return None
    decimals0 = _v4_token_decimals(pool, 0, chain_id=chain_id)
    decimals1 = _v4_token_decimals(pool, 1, chain_id=chain_id)
    if decimals0 is None or decimals1 is None:
        return None
    raw_price = Decimal(sqrt_price_x96) * Decimal(sqrt_price_x96) / Decimal(2**192)
    human_price = raw_price * (Decimal(10) ** (decimals0 - decimals1))
    if human_price <= 0:
        return None
    if from_symbol == token0.get("symbol") and to_symbol == token1.get("symbol"):
        return float(human_price)
    if from_symbol == token1.get("symbol") and to_symbol == token0.get("symbol"):
        return float(Decimal(1) / human_price)
    return None
def _v4_static_depth_impact_bps(
    pool: dict[str, Any],
    *,
    from_symbol: str,
    input_amount: float | None,
    from_index: int | None,
    chain_id: int | None = None,
) -> float | None:
    if input_amount is None or input_amount <= 0 or from_index not in {0, 1}:
        return None
    liquidity = _optional_number(pool.get("liquidity"))
    sqrt_price_x96 = _pool_sqrt_price_x96(pool)
    decimals = _v4_token_decimals(pool, from_index, chain_id=chain_id)
    if liquidity is None or liquidity <= 0 or sqrt_price_x96 is None or decimals is None:
        return None
    sqrt_price = Decimal(sqrt_price_x96) / Decimal(2**96)
    amount_raw = Decimal(str(input_amount)) * (Decimal(10) ** decimals)
    if from_index == 0:
        next_sqrt = Decimal(1) / (Decimal(1) / sqrt_price + amount_raw / Decimal(str(liquidity)))
    else:
        next_sqrt = sqrt_price + amount_raw / Decimal(str(liquidity))
    price_ratio = (next_sqrt / sqrt_price) ** 2
    return float(abs(Decimal(1) - price_ratio) * Decimal(10_000))
def _static_depth_available(pool: dict[str, Any]) -> bool:
    protocol = _pool_protocol(pool)
    if protocol == "uniswap_v4":
        liquidity_available = _optional_number(pool.get("liquidity")) not in (None, 0)
        tvl_available = _pool_tvl_usd(pool) > 0
        # Indexed V4 pools often lack a liquidity field until a swap refreshes
        # the liquidity cache.  A decoded PoolKey with sqrtPriceX96 remains a
        # valid quote candidate: the on-chain quoter will verify whether
        # enough liquidity exists, instead of treating the route as missing.
        pool_state_available = bool(pool.get("sqrtPriceX96") or pool.get("sqrt_price_x96"))
        return liquidity_available or tvl_available or pool_state_available
    return (
        _pool_reserve_amount(pool, 0) is not None and _pool_reserve_amount(pool, 1) is not None
    ) or (
        _pool_reserve_usd(pool, 0) is not None and _pool_reserve_usd(pool, 1) is not None
    )
def _reference_price_quote(
    *,
    pool: dict[str, Any],
    from_symbol: str,
    to_symbol: str,
    from_address: str | None = None,
    to_address: str | None = None,
    input_amount: float | None,
    input_usd: float,
    price_map: dict[str, float],
    reason: str,
    chain_id: int | None = None,
) -> dict[str, Any] | None:
    fee_bps = _fee_bps(pool)
    fee_factor = max(0.0, 1.0 - fee_bps / 10_000.0)
    rate = _market_reference_rate(pool, from_symbol, to_symbol)
    if rate is None and _pool_protocol(pool) == "uniswap_v4":
        rate = _v4_spot_rate(pool, from_symbol, to_symbol, chain_id=chain_id)
    if input_amount is None:
        input_price = price_map.get(from_symbol)
        if input_price and input_price > 0:
            input_amount = input_usd / input_price
    output_amount = input_amount * rate * fee_factor if input_amount is not None and rate is not None else None
    output_usd = None
    output_price = price_map.get(to_symbol)
    reference_usd_mode = "priced_output"
    in_index = _pool_side_index(pool, from_symbol, from_address)
    out_index = _pool_side_index(pool, to_symbol, to_address)
    token_decimals_trusted = True
    if _pool_protocol(pool) == "uniswap_v4" and in_index is not None and out_index is not None:
        token_decimals_trusted = _pool_token_decimals_trusted(pool, in_index, chain_id=chain_id) and _pool_token_decimals_trusted(pool, out_index, chain_id=chain_id)
    if output_amount is not None and output_price and output_price > 0:
        output_usd = output_amount * output_price
    elif rate is not None:
        output_usd = input_usd * fee_factor
        reference_usd_mode = "fee_only_without_trusted_output_usd_price"
    else:
        return None
    price_impact_bps = _v4_static_depth_impact_bps(
        pool,
        from_symbol=from_symbol,
        input_amount=input_amount,
        from_index=in_index,
        chain_id=chain_id,
    ) if _pool_protocol(pool) == "uniswap_v4" else None

    return {
        "pool": pool.get("pool"),
        "protocol": pool.get("protocol") or pool.get("dex"),
        "dex": pool.get("dex") or pool.get("protocol"),
        "fee": pool.get("fee"),
        "feeBps": fee_bps,
        "fromSymbol": from_symbol,
        "toSymbol": to_symbol,
        "tokenIn": _token_address(from_address),
        "tokenOut": _token_address(to_address),
        "inputAmount": input_amount,
        "inputUsd": input_usd,
        "outputAmount": None,
        "outputUsd": None,
        "effectiveRate": None,
        "referenceOutputAmount": output_amount,
        "referenceOutputUsd": output_usd,
        "referenceEffectiveRate": rate * fee_factor if rate is not None else None,
        "reserveInUsd": _pool_reserve_usd(pool, _pool_side_index(pool, from_symbol, from_address) or 0, price_map),
        "reserveOutUsd": _pool_reserve_usd(pool, _pool_side_index(pool, to_symbol, to_address) or 0, price_map),
        "priceImpactBps": price_impact_bps,
        "priceImpactLimitBps": 10.0,
        "staticDepthAvailable": _static_depth_available(pool),
        "depthBand": pool.get("depthBand") or {},
        "quoteSource": "reference_price_requires_live_quoter",
        "executionTrust": "reference_only",
        "referenceUsdMode": reference_usd_mode,
        "trustedUsdPrice": reference_usd_mode == "priced_output",
        "tokenDecimalsTrusted": token_decimals_trusted,
        "tokenDecimalAssumption": None if token_decimals_trusted else "missing_token_decimals_no_assumption",
        "warning": reason,
    }
