from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from functools import lru_cache
from importlib import import_module
import os
import sys
from typing import Any
from urllib.parse import quote

from core.chain_data_registry import chain_data_by_chain_id
from core.flash_loan_pool_discovery import query_flash_loan_pool_discovery
from core.market_observation_store import (
    load_market_observation_overview_static,
    load_market_observation_static,
    observation_payload_from_static,
)
from execution.uniswap_v4 import build_v4_quote_request, quote_v4_exact_input_single
from execution.uniswap_v4_sdk_adapter import load_v4_sdk_adapter
from intent_trade.chains.robinhood_testnet.v4_quote_adapter import probe_v4_executable_amount_from_pool_index
from intent_trade.chains.robinhood_testnet.v4_pool_liquidity import _load_token_balance_scan_cache
from market.binance_market.pool_candidates import dex_pool_candidates_payload
from market.binance_market.dex_quote_surface import (
    build_quote_request_payload as build_dex_quote_request_payload,
    build_quote_routes_payload_from_overview,
    build_submission_review_payload as build_dex_submission_review_payload,
)
from market.binance_market.dex_surface_cache import (
    _DEX_SURFACE_CACHE,
    _DEX_SURFACE_CACHE_LOCK,
    _cache_surface_payload,
    _cached_framework_surface_payload,
    _cached_surface_payload,
    _protocols_key,
    _surface_cache_key,
    _surface_payload_has_framework_data,
)
from market.binance_market.dex_surface_analysis import (
    apply_route_overview_profit,
    build_overview_profit_payload,
    build_route_size_analysis,
)
from strategy.limits import DEFAULT_ARBITRAGE_MIN_PAPER_PROFIT_USD
from web3 import Web3

QUOTE_PROBE_NOTIONAL_USD = 1000.0
DEFAULT_FLASH_LOAN_FEE_BPS = 5.0
DEFAULT_GAS_RESERVE_USD = 1.0
DEFAULT_AMM_FEE_BPS = 30.0
ROBINHOOD_TESTNET_CHAIN_ID = 46630
ERC20_DECIMALS_ABI = [
    {
        "inputs": [],
        "name": "decimals",
        "outputs": [{"type": "uint8"}],
        "stateMutability": "view",
        "type": "function",
    }
]


def _env_flag(name: str, *, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _v4_live_quote_enabled() -> bool:
    return _env_flag("DEX_V4_LIVE_QUOTE_ENABLED", default=False)


def _v4_execution_size_probe_enabled() -> bool:
    return _env_flag("DEX_V4_EXECUTION_SIZE_PROBE_ENABLED", default=False)


def _v4_live_token_metadata_enabled() -> bool:
    return _env_flag("DEX_V4_LIVE_TOKEN_METADATA_ENABLED", default=False)


def _chain_observation_module(chain_id: int | None):
    if chain_id is None:
        return None
    row = chain_data_by_chain_id(chain_id)
    network = str((row or {}).get("network") or "").strip().lower()
    if not network:
        return None
    module_name = f"intent_trade.chains.{network.replace('-', '_')}.market_observation"
    try:
        return import_module(module_name)
    except ModuleNotFoundError as exc:
        if exc.name == module_name:
            return None
        raise


def _chain_observation_function(chain_id: int | None, name: str):
    module = _chain_observation_module(chain_id)
    if module is None:
        return None
    function = getattr(module, name, None)
    return function if callable(function) else None


def _chain_network_name(chain_id: int | None) -> str:
    if chain_id is None:
        return ""
    row = chain_data_by_chain_id(chain_id) or {}
    return str(row.get("network") or "").strip().lower()


def robinhood_testnet_v4_bridge_pairs_for_anchor_groups(
    anchor_groups: list[dict[str, Any]],
    *,
    chain_id: int | None,
    protocols: list[str] | None,
):
    function = _chain_observation_function(chain_id, "bridge_pairs_for_anchor_groups")
    if function is None:
        return [], {"enabled": False, "status": "chain_observation_extension_missing"}
    return function(anchor_groups, chain_id=chain_id, protocols=protocols)


def robinhood_testnet_v4_fund_anchor_pair_observation(
    *,
    chain_id: int | None,
    protocols: list[str] | None,
):
    function = _chain_observation_function(chain_id, "fund_anchor_pair_observation")
    if function is None:
        return {
            "enabled": False,
            "status": "chain_observation_extension_missing",
            "routeCount": 0,
            "associatedObservationPairCount": 0,
            "associatedObservationPoolCount": 0,
            "associatedObservationTokenCount": 0,
            "observationSequences": [],
            "associatedObservationPairs": [],
            "associatedObservationPools": [],
            "associatedObservationTokens": [],
        }
    return function(chain_id=chain_id, protocols=protocols)

STABLE_ANCHOR_SYMBOLS = {
    "AUSD",
    "BUSD",
    "DAI",
    "DAI.E",
    "EURC",
    "FDUSD",
    "FRAX",
    "FRAX.E",
    "GHO",
    "PYUSD",
    "RLUSD",
    "TUSD",
    "USDC",
    "USDC.E",
    "USDD",
    "USDE",
    "USDG",
    "USDF",
    "USD1",
    "USDT",
    "USDT.E",
}
PRICE_ALIAS_GROUPS = (
    {"ETH", "WETH", "WETH.E"},
    {"BTC", "WBTC", "BTCB", "BTC.B"},
)
INVALID_ROUTE_SYMBOL_PREFIXES = ("ROBINHOOD",)


def _norm_symbol(value: object) -> str:
    return str(value or "").strip().upper()


def _text(value: object) -> str:
    return str(value or "").strip()


def _token_address(value: object) -> str:
    text = str(value or "").strip()
    return text.lower() if text.startswith("0x") and len(text) == 42 else ""


def _pool_address(value: object) -> str:
    if isinstance(value, dict):
        return str(
            value.get("pool")
            or value.get("poolId")
            or value.get("pool_id")
            or value.get("pool_address")
            or value.get("address")
            or ""
        ).strip()
    return str(value or "").strip()


def _int(value: object) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _number(value: object) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return 0.0
    return parsed if parsed == parsed and parsed not in (float("inf"), float("-inf")) else 0.0


def _optional_number(value: object) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if parsed != parsed or parsed in (float("inf"), float("-inf")):
        return None
    return parsed


def _raw_from_human(value: object, decimals: int) -> int:
    try:
        amount = Decimal(str(value))
    except Exception:
        return 0
    if amount <= 0:
        return 0
    return int((amount * (Decimal(10) ** int(decimals))).to_integral_value())


def _human_from_raw(value: int, decimals: int) -> float:
    return float(Decimal(int(value)) / (Decimal(10) ** int(decimals)))


def _is_stable_anchor(symbol: object) -> bool:
    text = _norm_symbol(symbol)
    bridge_base = text[:-2] if text.endswith(".E") else text
    return text in STABLE_ANCHOR_SYMBOLS or bridge_base in STABLE_ANCHOR_SYMBOLS or text.endswith("USD")


def _split_symbol_components(symbol: object) -> list[str]:
    text = _norm_symbol(symbol)
    if not text:
        return []
    return [part for part in text.replace("/", ":").split(":") if part]


def _route_symbol_allowed(symbol: object) -> bool:
    text = _norm_symbol(symbol)
    if not text:
        return False
    return not any(text.startswith(prefix) for prefix in INVALID_ROUTE_SYMBOL_PREFIXES)


def _pair_symbols(pair: dict[str, Any]) -> list[str]:
    symbols = [
        _norm_symbol(pair.get("tokenXSymbol")),
        _norm_symbol(pair.get("tokenYSymbol")),
    ]
    return [symbol for symbol in symbols if symbol]


def _pool_symbols(pool: dict[str, Any]) -> list[str]:
    symbols = [
        _norm_symbol(pool.get("token0Symbol")),
        _norm_symbol(pool.get("token1Symbol")),
    ]
    return [symbol for symbol in symbols if symbol]


def _pool_tokens(pool: dict[str, Any]) -> list[dict[str, Any]]:
    tokens: list[dict[str, Any]] = []
    for index in (0, 1):
        symbol = _norm_symbol(pool.get(f"token{index}Symbol"))
        address = _token_address(pool.get(f"token{index}"))
        if symbol:
            tokens.append(
                {
                    "symbol": symbol,
                    "address": address,
                    "decimals": pool.get(f"token{index}Decimals") or pool.get(f"token{index}_decimals"),
                    "side": index,
                }
            )
    return tokens


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


@lru_cache(maxsize=16)
def _v4_token_metadata_cache(pool_manager: str) -> dict[str, dict[str, Any]]:
    normalized_pool_manager = _text(pool_manager).lower()
    if not normalized_pool_manager:
        return {}
    try:
        cache = _load_token_balance_scan_cache(pool_manager=normalized_pool_manager)
    except Exception:
        return {}
    entries = cache.get("entries") if isinstance(cache.get("entries"), dict) else {}
    metadata: dict[str, dict[str, Any]] = {}
    for address, entry in entries.items():
        if not isinstance(entry, dict):
            continue
        normalized = _token_address(address) or _token_address(entry.get("address"))
        if not normalized:
            continue
        symbol = _norm_symbol(entry.get("symbol") or entry.get("token_symbol"))
        decimals = entry.get("decimals")
        try:
            decimals = int(decimals) if decimals not in (None, "") else None
        except (TypeError, ValueError):
            decimals = None
        metadata[normalized] = {
            "address": normalized,
            "symbol": symbol,
            "decimals": decimals,
            "metadataOk": bool(symbol and decimals is not None),
            "source": "token_balance_scan_cache",
        }
    return metadata


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
        cached = _v4_token_metadata_cache(_pool_manager_address(pool, chain_id)).get(address.lower())
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
                    "source": "token_balance_scan_cache",
                }
        if _v4_live_token_metadata_enabled():
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


@lru_cache(maxsize=2048)
def _live_v4_token_decimals(address: str, *, chain_id: int | None = None) -> int | None:
    normalized = _token_address(address)
    if not normalized or normalized == "0x0000000000000000000000000000000000000000":
        return 18
    row = chain_data_by_chain_id(chain_id or 0) or {}
    rpc_url = str(row.get("rpc_url") or row.get("rpcUrl") or "").strip()
    if not rpc_url:
        return None
    timeout = float(os.getenv("DEX_ONCHAIN_RPC_TIMEOUT_SECONDS", "6") or 6)
    try:
        w3 = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": timeout}))
        if not w3.is_connected():
            return None
        contract = w3.eth.contract(address=Web3.to_checksum_address(normalized), abi=ERC20_DECIMALS_ABI)
        return int(contract.functions.decimals().call())
    except Exception:
        return None


def _pair_tokens(pair: dict[str, Any]) -> list[dict[str, Any]]:
    tokens = []
    for address_key, symbol_key in (("tokenX", "tokenXSymbol"), ("tokenY", "tokenYSymbol")):
        symbol = _norm_symbol(pair.get(symbol_key))
        address = _token_address(pair.get(address_key))
        if symbol:
            tokens.append({"symbol": symbol, "address": address})
    if len(tokens) >= 2 and all(token.get("address") for token in tokens):
        return tokens[:2]
    for pool in _pools_for_pair(pair):
        pool_tokens = _pool_tokens(pool)
        if len(pool_tokens) >= 2:
            return [{"symbol": token["symbol"], "address": token["address"]} for token in pool_tokens[:2]]
    return tokens[:2]


def _token_key(token: dict[str, Any]) -> str:
    address = _token_address(token.get("address"))
    if address:
        return f"address:{address}"
    return f"symbol:{_norm_symbol(token.get('symbol'))}"


def _route_symbols_from_tokens(tokens: list[dict[str, Any]]) -> list[str]:
    return [_norm_symbol(token.get("symbol")) for token in tokens if _norm_symbol(token.get("symbol"))]


def _route_addresses_from_tokens(tokens: list[dict[str, Any]]) -> list[str]:
    return [_token_address(token.get("address")) for token in tokens]


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


def _propagate_price_aliases(price_map: dict[str, float]) -> None:
    for group in PRICE_ALIAS_GROUPS:
        known = [price_map[symbol] for symbol in group if price_map.get(symbol)]
        if not known:
            continue
        price = sum(known) / len(known)
        for symbol in group:
            price_map.setdefault(symbol, price)


def _price_map_from_pairs(pairs: list[dict[str, Any]]) -> dict[str, float]:
    price_map: dict[str, float] = {symbol: 1.0 for symbol in STABLE_ANCHOR_SYMBOLS}
    pools = [pool for pair in pairs for pool in _pools_for_pair(pair)]
    _propagate_price_aliases(price_map)
    for pool in pools:
        if _pool_protocol(pool) == "uniswap_v4":
            continue
        symbols = _pool_symbols(pool)
        for index, symbol in enumerate(symbols[:2]):
            amount = _pool_reserve_amount(pool, index)
            usd = _pool_reserve_usd(pool, index, price_map={})
            if amount and usd and usd > 0:
                price_map[symbol] = usd / amount
    _propagate_price_aliases(price_map)
    for _ in range(4):
        changed = False
        for pool in pools:
            if _pool_protocol(pool) == "uniswap_v4":
                continue
            symbols = _pool_symbols(pool)
            if len(symbols) < 2:
                continue
            amounts = [_pool_reserve_amount(pool, 0), _pool_reserve_amount(pool, 1)]
            tvl = _pool_tvl_usd(pool)
            for known_index, missing_index in ((0, 1), (1, 0)):
                known_symbol = symbols[known_index]
                missing_symbol = symbols[missing_index]
                if price_map.get(missing_symbol) or not price_map.get(known_symbol) or not amounts[missing_index]:
                    continue
                if tvl > 0 and amounts[known_index]:
                    missing_usd = tvl - amounts[known_index] * price_map[known_symbol]
                    if missing_usd > 0:
                        price_map[missing_symbol] = missing_usd / amounts[missing_index]
                        changed = True
                        continue
                if amounts[known_index] and amounts[missing_index]:
                    price_map[missing_symbol] = price_map[known_symbol] * amounts[known_index] / amounts[missing_index]
                    changed = True
        _propagate_price_aliases(price_map)
        if not changed:
            break
    return {symbol: price for symbol, price in price_map.items() if price > 0}


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
    decimals0 = 18 if decimals0 is None else decimals0
    decimals1 = 18 if decimals1 is None else decimals1
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
        return _optional_number(pool.get("liquidity")) not in (None, 0) and _pool_tvl_usd(pool) > 0
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
        "tokenDecimalAssumption": None if token_decimals_trusted else "missing_token_decimals_assumed_18",
        "warning": reason,
    }


def _unique_symbols(rows: list[dict[str, Any]]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for row in rows:
        for symbol in _pool_symbols(row):
            if symbol and symbol not in seen:
                seen.add(symbol)
                result.append(symbol)
    return result


def _pool_count(pair: dict[str, Any], key: str) -> int:
    return len(pair.get(key) or []) or _int(pair.get(key.replace("Pools", "PoolCount")))


def _pools_for_pair(pair: dict[str, Any]) -> list[dict[str, Any]]:
    return list(pair.get("executionPools") or pair.get("preferredPools") or pair.get("strictQualityPools") or [])


def _pool_protocol(pool: dict[str, Any]) -> str:
    return _norm_symbol(pool.get("protocol") or pool.get("dex")).strip().lower()


def _route_pools(route: dict[str, Any]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    pools: list[dict[str, Any]] = []
    for key in ("executionPools", "preferredPools", "strictQualityPools"):
        for pool in route.get(key) or []:
            pool_id = str(pool.get("pool") or id(pool)).lower()
            if pool_id in seen:
                continue
            seen.add(pool_id)
            pools.append(pool)
    for pool in route.get("routePools") or []:
        if not isinstance(pool, dict):
            continue
        pool_id = str(pool.get("pool") or pool.get("poolId") or id(pool)).lower()
        if pool_id in seen:
            continue
        seen.add(pool_id)
        pools.append(pool)
    for pair in route.get("routePairs") or []:
        if not isinstance(pair, dict):
            continue
        for pool in pair.get("pools") or []:
            if not isinstance(pool, dict):
                continue
            pool_id = str(pool.get("pool") or pool.get("poolId") or id(pool)).lower()
            if pool_id in seen:
                continue
            seen.add(pool_id)
            pools.append(pool)
    return pools


def _route_score(route: dict[str, Any]) -> tuple[float, int, str]:
    pools = _route_pools(route)
    tvl = sum(_number((pool.get("balances") or {}).get("tvlUsd")) for pool in pools)
    return (tvl, len(pools), str(route.get("symbolPair") or ""))


def _pair_score(pair: dict[str, Any]) -> tuple[float, int, str]:
    pools = _pools_for_pair(pair)
    tvl = sum(_number((pool.get("balances") or {}).get("tvlUsd")) for pool in pools)
    return (tvl, len(pools), str(pair.get("symbolPair") or ""))


def _pair_token_entry(pair: dict[str, Any], token_key: str) -> dict[str, Any] | None:
    for token in _pair_tokens(pair):
        if _token_key(token) == token_key:
            return token
    return None


def _token_graph(
    pairs: list[dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], dict[str, set[str]], dict[str, set[str]], dict[frozenset[str], dict[str, Any]]]:
    token_by_key: dict[str, dict[str, Any]] = {}
    symbol_to_keys: dict[str, set[str]] = {}
    neighbor_keys: dict[str, set[str]] = {}
    pair_by_edge: dict[frozenset[str], dict[str, Any]] = {}
    for pair in pairs:
        tokens = _pair_tokens(pair)
        if len(tokens) < 2:
            continue
        left, right = tokens[:2]
        left_key = _token_key(left)
        right_key = _token_key(right)
        if not left_key or not right_key or left_key == right_key:
            continue
        token_by_key.setdefault(left_key, left)
        token_by_key.setdefault(right_key, right)
        symbol_to_keys.setdefault(_norm_symbol(left.get("symbol")), set()).add(left_key)
        symbol_to_keys.setdefault(_norm_symbol(right.get("symbol")), set()).add(right_key)
        neighbor_keys.setdefault(left_key, set()).add(right_key)
        neighbor_keys.setdefault(right_key, set()).add(left_key)
        edge_key = frozenset((left_key, right_key))
        current = pair_by_edge.get(edge_key)
        if current is None or _pair_score(pair) > _pair_score(current):
            pair_by_edge[edge_key] = pair
    return token_by_key, symbol_to_keys, neighbor_keys, pair_by_edge


def _pair_symbol_graph(pairs: list[dict[str, Any]]) -> dict[frozenset[str], dict[str, Any]]:
    pair_by_edge: dict[frozenset[str], dict[str, Any]] = {}
    for pair in pairs:
        symbols = [symbol for symbol in _pair_symbols(pair) if symbol]
        if len(symbols) < 2:
            continue
        left, right = symbols[:2]
        if left == right:
            continue
        edge_key = frozenset((left, right))
        current = pair_by_edge.get(edge_key)
        if current is None or _pair_score(pair) > _pair_score(current):
            pair_by_edge[edge_key] = pair
    return pair_by_edge


def _pool_matches_edge(
    pool: dict[str, Any],
    from_symbol: str,
    to_symbol: str,
    from_address: str | None = None,
    to_address: str | None = None,
) -> bool:
    tokens = _pool_tokens(pool)
    from_key = _token_address(from_address)
    to_key = _token_address(to_address)
    if from_key and to_key:
        addresses = {_token_address(token.get("address")) for token in tokens[:2]}
        return from_key in addresses and to_key in addresses and from_key != to_key
    symbols = set(_pool_symbols(pool)[:2])
    return from_symbol in symbols and to_symbol in symbols and len(symbols) == 2


def _pool_side_index(pool: dict[str, Any], symbol: str, address: str | None = None) -> int | None:
    address_key = _token_address(address)
    for token in _pool_tokens(pool):
        if address_key and _token_address(token.get("address")) == address_key:
            return int(token["side"])
    symbols = _pool_symbols(pool)
    for index, candidate in enumerate(symbols[:2]):
        if candidate == symbol:
            return index
    return None


def _edge_quote(
    *,
    pool: dict[str, Any],
    from_symbol: str,
    to_symbol: str,
    from_address: str | None = None,
    to_address: str | None = None,
    input_amount: float | None,
    input_usd: float,
    price_map: dict[str, float],
    chain_id: int | None = None,
) -> dict[str, Any] | None:
    in_index = _pool_side_index(pool, from_symbol, from_address)
    out_index = _pool_side_index(pool, to_symbol, to_address)
    if in_index is None or out_index is None or in_index == out_index:
        return None
    protocol = _pool_protocol(pool)
    if protocol == "uniswap_v4":
        if _v4_live_quote_enabled() and input_amount is not None and input_amount > 0:
            network = _chain_network_name(chain_id) or str(pool.get("network") or "").strip().lower()
            adapter = None
            if network:
                try:
                    adapter = load_v4_sdk_adapter(network=network, chain_id=chain_id)
                except Exception:
                    adapter = None
            if adapter is not None:
                try:
                    input_decimals = _v4_token_decimals(pool, in_index, chain_id=chain_id)
                    output_decimals = _v4_token_decimals(pool, out_index, chain_id=chain_id)
                    pool_tokens = _pool_tokens(pool)
                    token_in = pool_tokens[in_index].get("address") if len(pool_tokens) > in_index else None
                    token_out = pool_tokens[out_index].get("address") if len(pool_tokens) > out_index else None
                    if token_in and token_out and input_decimals is not None and output_decimals is not None:
                        amount_in_raw = _raw_from_human(input_amount, input_decimals)
                        request = build_v4_quote_request(
                            network=network,
                            chain_id=chain_id,
                            pool=_pool_address(pool),
                            token_in=_token_address(token_in),
                            token_out=_token_address(token_out),
                            amount_in_raw=amount_in_raw,
                            block_number=None,
                            fee=pool.get("fee"),
                            route_symbols=(from_symbol, to_symbol),
                        )
                        request["poolState"] = {
                            "pool": _pool_address(pool),
                            "token0": _token_address(pool.get("token0")),
                            "token1": _token_address(pool.get("token1")),
                            "token0Symbol": _norm_symbol(pool.get("token0Symbol")),
                            "token1Symbol": _norm_symbol(pool.get("token1Symbol")),
                            "token0Decimals": _v4_token_decimals(pool, 0, chain_id=chain_id),
                            "token1Decimals": _v4_token_decimals(pool, 1, chain_id=chain_id),
                            "tickSpacing": pool.get("tickSpacing"),
                            "sqrtPriceX96": pool.get("sqrtPriceX96") or pool.get("sqrt_price_x96"),
                        }
                        quote = quote_v4_exact_input_single(adapter=adapter, request=request)
                        amount_out_raw = int(quote["amountOutRaw"])
                        output_amount = _human_from_raw(amount_out_raw, output_decimals)
                        output_price = _number(price_map.get(to_symbol))
                        token_decimals_trusted = _pool_token_decimals_trusted(pool, in_index, chain_id=chain_id) and _pool_token_decimals_trusted(
                            pool,
                            out_index,
                            chain_id=chain_id,
                        )
                        output_usd = output_amount * output_price if output_price > 0 else None
                        return {
                            "pool": pool.get("pool"),
                            "protocol": pool.get("protocol") or pool.get("dex"),
                            "dex": pool.get("dex") or pool.get("protocol"),
                            "fee": pool.get("fee"),
                            "feeBps": _fee_bps(pool),
                            "fromSymbol": from_symbol,
                            "toSymbol": to_symbol,
                            "tokenIn": _token_address(from_address),
                            "tokenOut": _token_address(to_address),
                            "amountInRaw": str(amount_in_raw),
                            "amountOutRaw": str(amount_out_raw),
                            "inputAmount": input_amount,
                            "inputUsd": input_usd,
                            "outputAmount": output_amount,
                            "outputUsd": output_usd,
                            "effectiveRate": amount_out_raw / amount_in_raw if amount_in_raw > 0 else None,
                            "reserveInUsd": _pool_reserve_usd(pool, in_index, price_map),
                            "reserveOutUsd": _pool_reserve_usd(pool, out_index, price_map),
                            "priceImpactBps": None,
                            "depthBand": pool.get("depthBand") or {},
                            "quoteSource": "uniswap_v4_live_adapter_quote",
                            "executionTrust": "live_v4_adapter",
                            "priceEvidenceSource": "chain_live_quote",
                            "priceEvidenceMode": "live_verified",
                            "livePriceVerified": bool(output_usd is not None),
                            "trustedUsdPrice": bool(output_usd is not None),
                            "tokenDecimalsTrusted": token_decimals_trusted,
                            "tokenDecimalAssumption": None if token_decimals_trusted else "missing_token_decimals_untrusted",
                            "quoteMode": quote.get("quoteMode") or "uniswap_v4_sdk_exact_input_single",
                            "adapterRequired": False,
                            "sdkAdapter": quote.get("sdkAdapter") or quote.get("quoteAdapter"),
                            "quoteAdapter": quote.get("quoteAdapter") or quote.get("sdkAdapter"),
                        }
                except Exception as exc:
                    live_quote_error = str(exc)
                else:
                    live_quote_error = ""
            else:
                live_quote_error = ""
            if live_quote_error:
                quote = _reference_price_quote(
                    pool=pool,
                    from_symbol=from_symbol,
                    to_symbol=to_symbol,
                    from_address=from_address,
                    to_address=to_address,
                    input_amount=input_amount,
                    input_usd=input_usd,
                    price_map=price_map,
                    reason="uniswap_v4_live_quote_failed",
                    chain_id=chain_id,
                )
                if quote is None:
                    quote = {
                        "pool": pool.get("pool"),
                        "protocol": pool.get("protocol") or pool.get("dex"),
                        "dex": pool.get("dex") or pool.get("protocol"),
                        "fee": pool.get("fee"),
                        "feeBps": _fee_bps(pool),
                        "fromSymbol": from_symbol,
                        "toSymbol": to_symbol,
                        "tokenIn": _token_address(from_address),
                        "tokenOut": _token_address(to_address),
                        "inputAmount": input_amount,
                        "inputUsd": input_usd,
                        "outputAmount": None,
                        "outputUsd": None,
                        "effectiveRate": None,
                        "reserveInUsd": _pool_reserve_usd(pool, in_index, price_map),
                        "reserveOutUsd": _pool_reserve_usd(pool, out_index, price_map),
                        "priceImpactBps": None,
                        "depthBand": pool.get("depthBand") or {},
                    }
                quote.update(
                    {
                        "quoteSource": "uniswap_v4_live_quote_failed",
                        "executionTrust": "live_quote_failed",
                        "quoteMode": "uniswap_v4_sdk_exact_input_single",
                        "adapterRequired": False,
                        "liveQuoteError": live_quote_error,
                        "warning": "uniswap_v4_live_quote_failed",
                    }
                )
                return quote
        quote = _reference_price_quote(
            pool=pool,
            from_symbol=from_symbol,
            to_symbol=to_symbol,
            from_address=from_address,
            to_address=to_address,
            input_amount=input_amount,
            input_usd=input_usd,
            price_map=price_map,
            reason="uniswap_v4_sdk_adapter_required",
            chain_id=chain_id,
        )
        if quote is None:
            return {
                "pool": pool.get("pool"),
                "protocol": pool.get("protocol") or pool.get("dex"),
                "dex": pool.get("dex") or pool.get("protocol"),
                "fee": pool.get("fee"),
                "feeBps": _fee_bps(pool),
                "fromSymbol": from_symbol,
                "toSymbol": to_symbol,
                "tokenIn": _token_address(from_address),
                "tokenOut": _token_address(to_address),
                "inputAmount": input_amount,
                "inputUsd": input_usd,
                "outputAmount": None,
                "outputUsd": None,
                "effectiveRate": None,
                "reserveInUsd": _pool_reserve_usd(pool, in_index, price_map),
                "reserveOutUsd": _pool_reserve_usd(pool, out_index, price_map),
                "priceImpactBps": None,
                "depthBand": pool.get("depthBand") or {},
                "quoteSource": "uniswap_v4_sdk_adapter_required",
                "executionTrust": "sdk_adapter_required",
                "quoteMode": "uniswap_v4_sdk_exact_input_single",
                "adapterRequired": True,
                "warning": "uniswap_v4_sdk_adapter_required",
            }
        quote["quoteSource"] = "uniswap_v4_sdk_adapter_required"
        quote["executionTrust"] = "sdk_adapter_required"
        quote["quoteMode"] = "uniswap_v4_sdk_exact_input_single"
        quote["adapterRequired"] = True
        return quote
    if not _is_constant_product_pool(pool):
        return _reference_price_quote(
            pool=pool,
            from_symbol=from_symbol,
            to_symbol=to_symbol,
            from_address=from_address,
            to_address=to_address,
            input_amount=input_amount,
            input_usd=input_usd,
            price_map=price_map,
            reason="concentrated_liquidity_pool_requires_quoter",
            chain_id=chain_id,
        )
    fee_bps = _fee_bps(pool)
    fee_factor = max(0.0, 1.0 - fee_bps / 10_000.0)
    reserve_in_amount = _pool_reserve_amount(pool, in_index)
    reserve_out_amount = _pool_reserve_amount(pool, out_index)
    reserve_in_usd = _pool_reserve_usd(pool, in_index, price_map)
    reserve_out_usd = _pool_reserve_usd(pool, out_index, price_map)
    if reserve_in_usd is None or reserve_out_usd is None:
        tvl = _pool_tvl_usd(pool)
        if tvl > 0:
            reserve_in_usd = reserve_in_usd or tvl / 2
            reserve_out_usd = reserve_out_usd or tvl / 2
    if not reserve_in_usd or not reserve_out_usd or input_usd <= 0:
        return None
    reference_rate = _market_reference_rate(pool, from_symbol, to_symbol)
    implied_rate = reserve_out_amount / reserve_in_amount if reserve_in_amount and reserve_out_amount else None
    if reference_rate and implied_rate:
        deviation_bps = abs(implied_rate / reference_rate - 1.0) * 10_000.0
        if deviation_bps > 500.0:
            return _reference_price_quote(
                pool=pool,
                from_symbol=from_symbol,
                to_symbol=to_symbol,
                from_address=from_address,
                to_address=to_address,
                input_amount=input_amount,
                input_usd=input_usd,
                price_map=price_map,
                reason="pool_reserve_price_deviates_from_market_reference",
                chain_id=chain_id,
            )
    input_after_fee_usd = input_usd * fee_factor
    output_usd = reserve_out_usd * input_after_fee_usd / (reserve_in_usd + input_after_fee_usd)
    spot_output_usd = input_after_fee_usd * reserve_out_usd / reserve_in_usd if reserve_in_usd > 0 else 0.0
    price_impact_bps = max(0.0, (1.0 - output_usd / spot_output_usd) * 10_000.0) if spot_output_usd > 0 else None
    output_amount = None
    if input_amount is not None and reserve_in_amount and reserve_out_amount:
        input_after_fee = input_amount * fee_factor
        output_amount = reserve_out_amount * input_after_fee / (reserve_in_amount + input_after_fee)
        price = price_map.get(to_symbol)
        if price and price > 0:
            output_usd = output_amount * price
    if output_amount is None:
        price = price_map.get(to_symbol)
        if price and price > 0:
            output_amount = output_usd / price
    effective_rate = output_amount / input_amount if input_amount and output_amount is not None else None
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
        "outputAmount": output_amount,
        "outputUsd": output_usd,
        "effectiveRate": effective_rate,
        "reserveInUsd": reserve_in_usd,
        "reserveOutUsd": reserve_out_usd,
        "priceImpactBps": price_impact_bps,
        "depthBand": pool.get("depthBand") or {},
        "quoteSource": "constant_product_v2_static_reserve",
        "executionTrust": "static_v2_probe",
    }


def _best_edge_quote(
    *,
    pools: list[dict[str, Any]],
    from_symbol: str,
    to_symbol: str,
    from_address: str | None = None,
    to_address: str | None = None,
    input_amount: float | None,
    input_usd: float,
    price_map: dict[str, float],
    chain_id: int | None = None,
) -> dict[str, Any] | None:
    quotes = [
        quote
        for pool in pools
        if _pool_matches_edge(pool, from_symbol, to_symbol, from_address, to_address)
        for quote in [
            _edge_quote(
                pool=pool,
                from_symbol=from_symbol,
                to_symbol=to_symbol,
                from_address=from_address,
                to_address=to_address,
                input_amount=input_amount,
                input_usd=input_usd,
                price_map=price_map,
                chain_id=chain_id,
            )
        ]
        if quote is not None
    ]
    if not quotes:
        return None
    return max(quotes, key=lambda item: (_number(item.get("outputUsd")), _number(item.get("reserveOutUsd"))))


def _route_quote_analysis(
    route: dict[str, Any],
    *,
    price_map: dict[str, float],
    chain_id: int | None = None,
    notional_usd: float = QUOTE_PROBE_NOTIONAL_USD,
    token_basis_input_amount: float | None = None,
) -> dict[str, Any]:
    strict_live_price_only = chain_id == ROBINHOOD_TESTNET_CHAIN_ID
    overview_route = route.get("overviewRoute") or {}
    route_tokens = [token for token in route.get("routeTokenPath") or overview_route.get("pathTokens") or [] if isinstance(token, dict)]
    route_symbols = _route_symbols_from_tokens(route_tokens) if route_tokens else list(overview_route.get("pathSymbols") or route.get("routeSymbols") or [])
    route_addresses = _route_addresses_from_tokens(route_tokens) if route_tokens else []
    pools = _route_pools(route)
    static_depth_available = bool(pools) and all(_static_depth_available(pool) for pool in pools)
    warnings: list[str] = []
    if route_symbols and not route_tokens:
        warnings.append("route_identity_symbol_fallback")
    invalid_symbols = [symbol for symbol in route_symbols if not _route_symbol_allowed(symbol)]
    if invalid_symbols:
        warnings.append(f"invalid_route_symbol:{','.join(invalid_symbols)}")
        return {
            "method": "static_amm_depth_probe",
            "notionalUsd": notional_usd,
            "inputSymbol": route_symbols[0] if route_symbols else None,
            "outputSymbol": route_symbols[-1] if route_symbols else None,
            "complete": False,
            "requiresLiveQuoter": False,
            "adapterRequired": False,
            "stepCount": 0,
            "steps": [],
            "finalOutputUsd": None,
            "referenceFinalOutputUsd": None,
            "grossProfitUsd": None,
            "referenceGrossProfitUsd": None,
            "flashLoanFeeBps": DEFAULT_FLASH_LOAN_FEE_BPS,
            "flashLoanFeeUsd": notional_usd * DEFAULT_FLASH_LOAN_FEE_BPS / 10_000.0,
            "gasReserveUsd": DEFAULT_GAS_RESERVE_USD,
            "minProfitUsd": DEFAULT_ARBITRAGE_MIN_PAPER_PROFIT_USD,
            "offchainMinProfitUsd": DEFAULT_ARBITRAGE_MIN_PAPER_PROFIT_USD,
            "submissionMinProfitUsd": notional_usd * DEFAULT_FLASH_LOAN_FEE_BPS / 10_000.0,
            "submissionNetProfitUsd": None,
            "offchainNetProfitUsd": None,
            "netProfitUsd": None,
            "referenceNetProfitUsd": None,
            "netProfitBps": None,
            "referenceNetProfitBps": None,
            "profitable": False,
            "executableCandidate": False,
            "sdkAdapterRequiredStepCount": 0,
            "recommendation": {
                "level": "blocked",
                "action": "拒绝污染路径",
                "reasons": ["路径包含明显的污染符号，不能进入报价或预检。"],
            },
            "warnings": warnings,
        }
    if len(route_symbols) < 2:
        warnings.append("route_symbols_missing")
    input_symbol = route_symbols[0] if route_symbols else None
    output_symbol = route_symbols[-1] if route_symbols else None
    current_usd = float(notional_usd)
    closed_loop_same_symbol = bool(input_symbol and output_symbol == input_symbol)
    requested_token_basis_input_amount = token_basis_input_amount
    if closed_loop_same_symbol and requested_token_basis_input_amount is not None:
        token_basis_input_amount = requested_token_basis_input_amount
    elif closed_loop_same_symbol and input_symbol and price_map.get(input_symbol):
        token_basis_input_amount = current_usd / price_map[input_symbol]
    elif closed_loop_same_symbol:
        token_basis_input_amount = 1.0
    else:
        token_basis_input_amount = None
    current_amount = (
        token_basis_input_amount
        if token_basis_input_amount is not None
        else current_usd / price_map[input_symbol]
        if input_symbol and price_map.get(input_symbol)
        else token_basis_input_amount
    )
    steps: list[dict[str, Any]] = []
    for index, (from_symbol, to_symbol) in enumerate(zip(route_symbols, route_symbols[1:]), start=1):
        from_address = route_addresses[index - 1] if index - 1 < len(route_addresses) else ""
        to_address = route_addresses[index] if index < len(route_addresses) else ""
        quote = _best_edge_quote(
            pools=pools,
            from_symbol=from_symbol,
            to_symbol=to_symbol,
            from_address=from_address,
            to_address=to_address,
            input_amount=current_amount,
            input_usd=current_usd,
            price_map=price_map,
            chain_id=chain_id,
        )
        if quote is None:
            steps.append(
                {
                    "hop": index,
                    "fromSymbol": from_symbol,
                    "toSymbol": to_symbol,
                    "tokenIn": from_address,
                    "tokenOut": to_address,
                    "status": "missing_pool_or_depth",
                    "inputAmount": current_amount,
                    "inputUsd": current_usd,
                    "outputAmount": None,
                    "outputUsd": None,
                }
            )
            warnings.append(f"hop_{index}_quote_missing")
            break
        quote["hop"] = index
        quote["status"] = "quoted"
        steps.append(quote)
        trust = str(quote.get("executionTrust") or "").strip().lower()
        has_reference_output = quote.get("referenceOutputUsd") is not None or quote.get("referenceOutputAmount") is not None
        if trust == "live_quote_failed":
            warnings.append(f"hop_{index}_live_quote_failed")
            if quote.get("liveQuoteError"):
                warnings.append(f"hop_{index}_live_quote_error:{quote['liveQuoteError']}")
            warnings.append(f"route_truncated_at_hop_{index}_live_quote_failed")
            break
        if trust in {"reference_only", "sdk_adapter_required"} and not has_reference_output:
            warnings.append(f"hop_{index}_requires_live_quoter")
            warnings.append(f"route_truncated_at_hop_{index}_requires_live_quoter")
            break
        if trust in {"reference_only", "sdk_adapter_required"}:
            warnings.append(f"hop_{index}_reference_quote_requires_live_quoter")
        if chain_id is None and trust == "sdk_adapter_required" and quote.get("trustedUsdPrice") is False:
            warnings.append(f"route_truncated_at_hop_{index}_requires_live_quoter")
            break
        step_output_usd = quote.get("outputUsd")
        if step_output_usd is None:
            step_output_usd = quote.get("referenceOutputUsd")
        current_usd = _number(step_output_usd)
        step_output_amount = quote.get("outputAmount")
        if step_output_amount is None:
            step_output_amount = quote.get("referenceOutputAmount")
        current_amount = step_output_amount if step_output_amount is not None else (
            current_usd / price_map[to_symbol] if price_map.get(to_symbol) else None
        )
        if current_amount is not None and _number(current_amount) <= 0:
            warnings.append(f"route_truncated_at_hop_{index}_zero_output")
            break
    complete = len(steps) == max(0, len(route_symbols) - 1) and all(step.get("status") == "quoted" for step in steps)
    requires_live_quoter = any(
        step.get("executionTrust") in {"reference_only", "live_quote_failed"}
        for step in steps
    )
    adapter_required = any(step.get("executionTrust") == "sdk_adapter_required" for step in steps)
    requires_live_quoter = requires_live_quoter or adapter_required
    untrusted_usd_reference = any(step.get("trustedUsdPrice") is False for step in steps)
    assumed_token_decimals = any(step.get("tokenDecimalsTrusted") is False for step in steps)
    if requires_live_quoter:
        warnings.append("reference_price_step_requires_live_quoter")
    if untrusted_usd_reference:
        warnings.append("reference_usd_price_missing_or_untrusted")
    if assumed_token_decimals:
        warnings.append("v4_token_decimals_missing_or_assumed")
    reference_final_output_usd = current_usd if complete and not untrusted_usd_reference else None
    reference_gross_profit_usd = (reference_final_output_usd - notional_usd) if reference_final_output_usd is not None else None
    flash_loan_fee_usd = notional_usd * DEFAULT_FLASH_LOAN_FEE_BPS / 10_000.0
    submission_min_profit_usd = flash_loan_fee_usd
    submission_net_profit_usd = (
        reference_gross_profit_usd - flash_loan_fee_usd if reference_gross_profit_usd is not None else None
    )
    reference_net_profit_usd = (
        submission_net_profit_usd - DEFAULT_GAS_RESERVE_USD if submission_net_profit_usd is not None else None
    )
    reference_net_profit_bps = (
        reference_net_profit_usd / notional_usd * 10_000.0
        if reference_net_profit_usd is not None and notional_usd > 0
        else None
    )
    token_basis_complete = bool(
        complete
        and token_basis_input_amount is not None
        and current_amount is not None
        and input_symbol
        and output_symbol == input_symbol
    )
    reference_final_output_amount = current_amount if token_basis_complete else None
    reference_gross_profit_amount = (
        reference_final_output_amount - token_basis_input_amount
        if reference_final_output_amount is not None and token_basis_input_amount is not None
        else None
    )
    reference_flash_loan_fee_amount = (
        token_basis_input_amount * DEFAULT_FLASH_LOAN_FEE_BPS / 10_000.0
        if token_basis_input_amount is not None
        else None
    )
    reference_net_profit_amount = (
        reference_gross_profit_amount - reference_flash_loan_fee_amount
        if reference_gross_profit_amount is not None and reference_flash_loan_fee_amount is not None
        else None
    )
    reference_gross_return_bps = (
        reference_gross_profit_amount / token_basis_input_amount * 10_000.0
        if reference_gross_profit_amount is not None and token_basis_input_amount
        else None
    )
    reference_net_return_bps = (
        reference_net_profit_amount / token_basis_input_amount * 10_000.0
        if reference_net_profit_amount is not None and token_basis_input_amount
        else None
    )
    depth_impact_bps = [
        _optional_number(step.get("priceImpactBps"))
        for step in steps
        if step.get("priceImpactBps") is not None
    ]
    max_static_depth_impact_bps = max(depth_impact_bps) if depth_impact_bps else None
    depth_status = "available" if static_depth_available else "unavailable"
    if static_depth_available and max_static_depth_impact_bps is not None and max_static_depth_impact_bps > 10.0:
        depth_status = "exceeds_10_bps"
    live_price_verified_now = bool(steps) and all(
        str(step.get("executionTrust") or "").strip().lower() == "live_v4_adapter"
        for step in steps
    )
    if not static_depth_available and not live_price_verified_now:
        warnings.append("static_depth_evidence_unavailable")
    elif depth_status == "exceeds_10_bps" and not live_price_verified_now:
        warnings.append("static_price_impact_exceeds_10_bps_reduce_borrow_amount")
    borrow_asset_is_stable = bool(input_symbol and _is_stable_anchor(input_symbol))
    borrow_asset_class = "stable" if borrow_asset_is_stable else "non_stable"
    token_basis_trusted = bool(token_basis_complete and not assumed_token_decimals)
    token_basis_reference_profitable = bool(reference_net_profit_amount is not None and reference_net_profit_amount > 0)
    token_basis_profitable = bool(token_basis_trusted and token_basis_reference_profitable)
    non_stable_chain_submission_candidate = bool(token_basis_profitable and not borrow_asset_is_stable)
    executable_ready = bool(complete and not requires_live_quoter)
    final_output_usd = reference_final_output_usd if executable_ready else None
    gross_profit_usd = reference_gross_profit_usd if executable_ready else None
    net_profit_usd = (
        gross_profit_usd - flash_loan_fee_usd - DEFAULT_GAS_RESERVE_USD if gross_profit_usd is not None else None
    )
    net_profit_bps = net_profit_usd / notional_usd * 10_000.0 if net_profit_usd is not None and notional_usd > 0 else None
    min_profit_usd = float(DEFAULT_ARBITRAGE_MIN_PAPER_PROFIT_USD)
    if strict_live_price_only:
        warnings.append("robinhood_testnet_live_price_only_enforced")
        fully_live_adapter = bool(steps) and all(str(step.get("executionTrust") or "").strip().lower() == "live_v4_adapter" for step in steps)
        if not fully_live_adapter:
            requires_live_quoter = True
            executable_ready = False
            final_output_usd = None
            gross_profit_usd = None
            net_profit_usd = None
            net_profit_bps = None
            non_stable_chain_submission_candidate = False
    live_price_verified = bool(steps) and all(
        str(step.get("executionTrust") or "").strip().lower() == "live_v4_adapter" and step.get("trustedUsdPrice") is True
        for step in steps
    )
    live_quote_failures = [
        f"第 {step.get('hop') or '?'} 跳链上 Quoter 失败：{step.get('liveQuoteError')}"
        for step in steps
        if step.get("executionTrust") == "live_quote_failed" and step.get("liveQuoteError")
    ]
    if not complete:
        action = "等待真实报价"
        level = "blocked"
        reasons = live_quote_failures or ["部分跳数缺少可计算池子或深度，不能进入自动交易"]
    elif requires_live_quoter:
        action = (
            f"{input_symbol} 本位参考收益为正，待链上复核"
            if untrusted_usd_reference and token_basis_profitable and input_symbol
            else f"{input_symbol} 本位按 decimals 假设为正，待确认"
            if untrusted_usd_reference and token_basis_reference_profitable and assumed_token_decimals and input_symbol
            else f"{input_symbol} 本位参考收益为负，待链上复核"
            if untrusted_usd_reference and token_basis_complete and input_symbol
            else "缺少可信 USD 锚点，待链上复核"
            if untrusted_usd_reference
            else "参考测算完成，待链上复核"
            if reference_final_output_usd is not None
            else "等待链上 Quoter 复核"
        )
        level = "requires_live_quote"
        reasons = (
            [
                "已基于本地 JSON 的 PoolKey、sqrtPriceX96、fee 和池内方向完成参考测算",
                "路径包含 V3/V4 或参考价报价跳，参考测算不能作为自动交易依据",
                "必须用链上 Quoter、协议 SDK 或 eth_call 复核每一跳输出后才能进入预检",
            ]
            if reference_final_output_usd is not None
            else ["路径包含 V3/V4 或参考价报价跳，静态余额不能作为自动交易依据", "必须用链上 Quoter 或协议 SDK 复核每一跳输出后才能进入预检"]
        )
        if strict_live_price_only:
            reasons = live_quote_failures or [
                "Robinhood Testnet 强制链上实时价格，静态参考价已禁用",
                "路由框架可以保留，但任何价格、收益和 bps 都必须来自 live quoter / onchain evidence",
            ]
        if depth_status == "unavailable":
            action = "已生成保守初始量，继续探索深度"
            reasons = [
                "当前池子缺少可用于离线深度计算的 liquidity 或 TVL，收益只保留为参考值",
                "初始借贷量采用交易量阶梯中的最小探针，继续观察各跳输出和滑点",
                "补充深度快照后，按 10 bps 滑点限制重新放大量化",
            ]
        elif depth_status == "exceeds_10_bps":
            action = "滑点超过 10 bps，降低初始交易额度"
            reasons = [
                "离线深度估算发现至少一跳超过 10 bps 滑点限制",
                "应沿交易量阶梯降低初始借贷量，直到所有跳数满足 10 bps",
                *reasons,
            ]
        elif untrusted_usd_reference:
            reasons = [
                "当前路径缺少可信 USD 价格锚点，美元利润不可判定",
                "闭环主评估已统一改用初始借入 token X 作为计价基准，计算最终返回 X 的参考百分点",
                "X 本位测算仍来自 V4 PoolKey / sqrtPriceX96 静态参考，必须链上复核真实 amountOut",
                *reasons,
            ]
        if assumed_token_decimals:
            reasons = [
                "部分 V4 token decimals 未在本地 PoolKey 数据中明示，当前 X 本位数量按 18 位 decimals 参考换算",
                *reasons,
            ]
        if non_stable_chain_submission_candidate:
            reasons = [
                "非 U 资金池 X 不套用 6U 固定利润门槛；X 本位净收益为正即可进入链上利润控制候选",
                *reasons,
            ]
    elif token_basis_profitable and token_basis_complete and not borrow_asset_is_stable and reference_net_profit_usd is None:
        action = f"{input_symbol} 本位闭环已验证正收益，进入链上预检"
        level = "ready_for_precheck"
        reasons = [
            "链上 live quote 已验证闭环 token 本位正收益",
            "当前缺少可信 USD 锚点，但这不应阻断非稳定 X 本位候选",
            "仍需 static-call / gas / repayment 复核后再广播",
        ]
    elif net_profit_usd is not None and net_profit_usd >= min_profit_usd:
        action = "进入自动交易预检"
        level = "ready_for_precheck"
        reasons = ["1000U 静态探针净利润超过最低阈值", "仍需链上 Quoter、滑点、gas 与闪电贷费复核"]
    elif net_profit_usd is not None and net_profit_usd > 0:
        action = "继续观察"
        level = "watch"
        reasons = ["扣除闪电贷费和 gas 储备后仍为正，但低于自动交易阈值"]
    else:
        action = "不建议执行"
        level = "reject"
        reasons = ["1000U 静态探针未覆盖交易成本"]
    executable_candidate = bool(
        executable_ready
        and not assumed_token_decimals
        and net_profit_usd is not None
        and net_profit_usd >= min_profit_usd
    )
    testnet_live_only = bool(chain_id == ROBINHOOD_TESTNET_CHAIN_ID and requires_live_quoter and not live_price_verified)
    if testnet_live_only:
        executable_candidate = False

    payload = {
        "method": "static_amm_depth_probe",
        "notionalUsd": notional_usd,
        "inputSymbol": input_symbol,
        "outputSymbol": output_symbol,
        "routeFrameworkSource": "market_observation",
        "priceEvidenceSource": "chain_live_quote" if live_price_verified else ("chain_live_quote_required" if strict_live_price_only else "static_reference_probe"),
        "priceEvidenceMode": "live_verified" if live_price_verified else ("live_required" if strict_live_price_only else "reference_only"),
        "livePriceVerified": live_price_verified,
        "complete": complete,
        "requiresLiveQuoter": requires_live_quoter,
        "adapterRequired": adapter_required,
        "stepCount": len(steps),
        "steps": steps,
        "finalOutputUsd": final_output_usd,
        "referenceFinalOutputUsd": reference_final_output_usd,
        "grossProfitUsd": gross_profit_usd,
        "referenceGrossProfitUsd": reference_gross_profit_usd,
        "flashLoanFeeBps": DEFAULT_FLASH_LOAN_FEE_BPS,
        "flashLoanFeeUsd": flash_loan_fee_usd,
        "gasReserveUsd": DEFAULT_GAS_RESERVE_USD,
        "minProfitUsd": min_profit_usd,
        "offchainMinProfitUsd": min_profit_usd,
        "submissionMinProfitUsd": submission_min_profit_usd,
        "submissionNetProfitUsd": submission_net_profit_usd,
        "offchainNetProfitUsd": net_profit_usd,
        "netProfitUsd": net_profit_usd,
        "referenceNetProfitUsd": reference_net_profit_usd,
        "netProfitBps": net_profit_bps,
        "referenceNetProfitBps": reference_net_profit_bps,
        "referenceInputAmount": token_basis_input_amount,
        "referenceInputSymbol": input_symbol if token_basis_input_amount is not None else None,
        "referenceFinalOutputAmount": reference_final_output_amount,
        "referenceFinalOutputSymbol": output_symbol if reference_final_output_amount is not None else None,
        "referenceGrossProfitAmount": reference_gross_profit_amount,
        "referenceFlashLoanFeeAmount": reference_flash_loan_fee_amount,
        "referenceNetProfitAmount": reference_net_profit_amount,
        "referenceGrossReturnBps": reference_gross_return_bps,
        "referenceNetReturnBps": reference_net_return_bps,
        "staticDepthStatus": depth_status,
        "staticDepthAvailable": static_depth_available,
        "maxStaticDepthImpactBps": max_static_depth_impact_bps,
        "staticDepthImpactLimitBps": 10.0,
        "referenceGasExcluded": token_basis_complete,
        "referenceReturnBasis": "borrow_token" if token_basis_complete else ("usd" if reference_net_profit_usd is not None else None),
        "referenceReturnTrusted": bool(
            reference_final_output_usd is not None
            and not assumed_token_decimals
            and (token_basis_trusted or reference_net_profit_usd is not None or live_price_verified)
        ),
        "borrowAssetClass": borrow_asset_class,
        "borrowAssetIsStable": borrow_asset_is_stable,
        "tokenBasisReferenceProfitable": token_basis_reference_profitable,
        "tokenBasisProfitable": token_basis_profitable,
        "chainSubmissionCandidate": bool(
            not testnet_live_only
            and not assumed_token_decimals
            and (
                non_stable_chain_submission_candidate
                or (executable_ready and net_profit_usd is not None and net_profit_usd >= min_profit_usd)
            )
        ),
        "chainSubmissionPolicy": (
            "non_stable_x_positive_token_profit_onchain_control"
            if non_stable_chain_submission_candidate
            else "stable_x_usd_min_profit"
            if borrow_asset_is_stable
            else "requires_positive_x_profit"
        ),
        "chainSubmissionMinProfitAmount": 0.0 if not borrow_asset_is_stable else None,
        "chainSubmissionMinProfitSymbol": input_symbol if not borrow_asset_is_stable else None,
        "profitable": bool(
            (not untrusted_usd_reference and not assumed_token_decimals and net_profit_usd is not None and net_profit_usd > 0)
            or token_basis_profitable
        ),
        "executableCandidate": executable_candidate,
        "sdkAdapterRequiredStepCount": sum(1 for step in steps if step.get("executionTrust") == "sdk_adapter_required"),
        "trustedUsdReference": bool(
            reference_final_output_usd is not None
            and not assumed_token_decimals
            and (not untrusted_usd_reference or live_price_verified)
        ),
        "tokenDecimalsTrusted": not assumed_token_decimals,
        "recommendation": {"level": level, "action": action, "reasons": reasons},
        "warnings": warnings,
    }
    return payload


def _borrowable_symbols_from_discovery(chain_id: int | None) -> list[str]:
    try:
        snapshot = query_flash_loan_pool_discovery(chain_id=chain_id)
    except Exception:
        return []
    symbols: list[str] = []
    for raw in (snapshot.get("raw_snapshots") or {}).values():
        if not isinstance(raw, dict):
            continue
        raw_chain_id = _int(raw.get("chainId") or raw.get("chain_id"))
        if chain_id is not None and raw_chain_id and raw_chain_id != int(chain_id):
            continue
        for source in raw.get("sources") or []:
            symbol = _norm_symbol((source or {}).get("token_symbol"))
            if symbol and bool((source or {}).get("flash_borrowable", True)):
                symbols.append(symbol)
    if symbols:
        return sorted(set(symbols))
    fallback: set[str] = set()
    for protocol in snapshot.get("protocols") or []:
        for key in ("token_symbol", "asset_symbol", "symbol"):
            symbol = _norm_symbol(protocol.get(key))
            if symbol:
                fallback.add(symbol)
    return sorted(fallback)


def _v4_poolmanager_balance_symbols(pairs: list[dict[str, Any]]) -> list[str]:
    symbols: set[str] = set()
    for pair in pairs:
        for pool in _pools_for_pair(pair):
            if _pool_protocol(pool) != "uniswap_v4":
                continue
            if not (pool.get("poolManager") or pool.get("pool_manager") or pool.get("poolIdentityMode") == "pool_id"):
                continue
            balances = pool.get("balances") if isinstance(pool.get("balances"), dict) else {}
            for index in (0, 1):
                symbol = _norm_symbol(pool.get(f"token{index}Symbol"))
                if not symbol or not _route_symbol_allowed(symbol):
                    continue
                if _number(balances.get(f"token{index}")) > 0:
                    symbols.add(symbol)
    return sorted(symbols)


def _chain_borrowable_symbols_from_pool_payload(
    pool_payload: dict[str, Any],
    *,
    chain_id: int | None,
    protocols: list[str] | None = None,
) -> list[str]:
    function = _chain_observation_function(chain_id, "borrowable_symbols_from_pool_payload")
    if function is None:
        return []
    result = function(pool_payload, chain_id=chain_id, protocols=protocols)
    return [_norm_symbol(symbol) for symbol in result or [] if _route_symbol_allowed(symbol)]


def _borrowable_symbols(
    pool_payload: dict[str, Any],
    chain_id: int | None,
    protocols: list[str] | None = None,
) -> list[str]:
    discovered = set(_borrowable_symbols_from_discovery(chain_id))
    for pair in pool_payload.get("pairs") or []:
        linkage = pair.get("flashLoanLinkage") or {}
        for symbol in linkage.get("borrow_symbols") or []:
            normalized = _norm_symbol(symbol)
            if normalized:
                discovered.add(normalized)
    discovered.update(_v4_poolmanager_balance_symbols(pool_payload.get("pairs") or []))
    discovered.update(_chain_borrowable_symbols_from_pool_payload(pool_payload, chain_id=chain_id, protocols=protocols))
    expanded = {part for symbol in discovered for part in _split_symbol_components(symbol)}
    return sorted(discovered | expanded)


def _graph_stable_anchor_symbols(pairs: list[dict[str, Any]]) -> list[str]:
    anchors: set[str] = set()
    for pair in pairs:
        for symbol in _pair_symbols(pair):
            if _is_stable_anchor(symbol) and _route_symbol_allowed(symbol):
                anchors.add(symbol)
    return sorted(anchors)


def _observation_anchor_symbols(
    pool_payload: dict[str, Any],
    *,
    chain_id: int | None,
    protocols: list[str] | None = None,
    borrow_symbols: list[str] | None = None,
) -> list[str]:
    pairs = pool_payload.get("pairs") or []
    anchors = _candidate_anchor_symbols(pool_payload, chain_id, protocols=protocols)
    if anchors:
        return anchors
    anchors = {_norm_symbol(symbol) for symbol in (borrow_symbols or _borrowable_symbols(pool_payload, chain_id, protocols=protocols))}
    anchors = {symbol for symbol in anchors if symbol and ":" not in symbol and _route_symbol_allowed(symbol)}
    if anchors:
        return sorted(anchors)
    return _graph_stable_anchor_symbols(pairs)


def _candidate_payload(chain_id: int | None, protocols: list[str] | None = None) -> dict[str, Any]:
    return dex_pool_candidates_payload(chain_id=chain_id, protocols=protocols or [])


def _overview_pool_payload_from_static_observation(observation: dict[str, Any]) -> dict[str, Any]:
    return {
        "summary": {},
        "pairs": [],
        "poolStaticTable": observation.get("poolStaticTable") or {},
        "flashLoanLiquidity": observation.get("flashLoanLiquidity") or {},
        "dexSettlement": observation.get("dexSettlement") or {},
    }


def _overview_paths_from_static_summary(snapshot: dict[str, Any], *, chain_id: int | None) -> list[dict[str, Any]]:
    paths = []
    for summary in snapshot.get("closedLoopRouteSummaries") or []:
        if not isinstance(summary, dict):
            continue
        path_symbols = [
            _norm_symbol(symbol)
            for symbol in summary.get("pathSymbols") or []
            if _norm_symbol(symbol)
        ]
        if not path_symbols:
            path_symbols = [
                _norm_symbol(symbol)
                for symbol in str(summary.get("pathText") or "").split("->")
                if _norm_symbol(symbol)
            ]
        path_text = str(summary.get("pathText") or " -> ".join(path_symbols)).strip()
        if len(path_symbols) < 2 or not path_text:
            continue
        anchor = _norm_symbol(summary.get("anchorSymbol") or path_symbols[0])
        target = _norm_symbol(
            summary.get("counterSymbol")
            or (path_symbols[-2] if len(path_symbols) > 2 else path_symbols[-1])
        )
        kind = "direct_anchor_pair" if len(path_symbols) <= 3 else "bridge_pair"
        route = {
            "symbolPair": path_text,
            "routeSymbols": path_symbols,
            "routeAddresses": list(summary.get("anchorAddresses") or []),
            "routeTokenPath": [{"symbol": symbol, "address": ""} for symbol in path_symbols],
            "tokenXSymbol": anchor,
            "tokenYSymbol": target,
            "executionPools": [],
            "overviewRoute": {
                "kind": kind,
                "anchorBorrowSymbol": anchor,
                "targetSymbol": target,
                "bridgeSymbols": path_symbols[1:-1],
                "pathSymbols": path_symbols,
                "pathText": path_text,
                "candidatePairSymbols": list(summary.get("pairSymbols") or []),
                "analysisStage": "market_observation_closed_loop",
                "nextStep": "quotes_and_route_analysis",
                "observationSource": summary.get("source") or "market_observation",
                "marketObservationRoute": True,
            },
            "flashLoanLinkage": {
                "strategy": (
                    "robinhood_testnet_market_observation_closed_loop"
                    if chain_id == ROBINHOOD_TESTNET_CHAIN_ID
                    else "market_observation_closed_loop"
                ),
                "borrow_symbols": [anchor] if anchor else [],
                "route_symbols": path_symbols,
                "route_addresses": list(summary.get("anchorAddresses") or []),
                "ready_for_atomic_arbitrage": False,
            },
            "overviewProfit": {
                "method": "static_overview_summary",
                "notionalUsd": QUOTE_PROBE_NOTIONAL_USD,
                "complete": False,
                "requiresLiveQuoter": True,
                "adapterRequired": True,
                "profitable": False,
                "executableCandidate": False,
                "chainSubmissionCandidate": False,
                "referenceReturnBasis": "borrow_token",
                "referenceReturnTrusted": False,
                "recommendationLevel": "requires_live_quote",
                "recommendationAction": "等待链上 Quoter/SDK 复核",
                "warnings": ["overview_summary_without_live_quote"],
                "priceEvidenceSource": "chain_live_quote_required",
                "priceEvidenceMode": "live_required",
            },
            "routeFrameworkSource": "market_observation",
            "pathSource": "market_observation",
            "marketObservationSourceStage": "cached_snapshot",
            "priceEvidenceSource": "chain_live_quote_required",
        }
        paths.append(route)
    return paths


def _candidate_anchor_symbols(
    pool_payload: dict[str, Any],
    chain_id: int | None,
    *,
    protocols: list[str] | None = None,
) -> list[str]:
    function = _chain_observation_function(chain_id, "observation_anchor_symbols")
    if function is None:
        return []
    result = function(pool_payload, chain_id=chain_id, protocols=protocols)
    return sorted({_norm_symbol(symbol) for symbol in result or [] if _route_symbol_allowed(symbol)})


def _candidate_pairs(pairs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        pair
        for pair in pairs
        if _pool_count(pair, "preferredPools") or _pool_count(pair, "executionPools") or _pool_count(pair, "strictQualityPools")
    ]


def _borrow_anchor_symbols(pool_payload: dict[str, Any], chain_id: int | None, protocols: list[str] | None = None) -> list[str]:
    anchors = _candidate_anchor_symbols(pool_payload, chain_id, protocols=protocols)
    if anchors:
        return anchors
    borrow_symbols = _borrowable_symbols(pool_payload, chain_id, protocols=protocols)
    anchors = [symbol for symbol in borrow_symbols if _is_stable_anchor(symbol) and ":" not in symbol]
    if anchors:
        return sorted(set(anchors))
    return sorted({symbol for symbol in borrow_symbols if ":" not in symbol})


def _sequence_rows_by_counter(rows: list[dict[str, Any]]) -> dict[str, dict[str, dict[str, Any]]]:
    grouped: dict[str, dict[str, dict[str, Any]]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        counter_symbol = _norm_symbol(row.get("counterSymbol"))
        pool_id = _pool_address(row)
        if not counter_symbol or not pool_id:
            continue
        grouped.setdefault(counter_symbol, {})[pool_id] = row
    return grouped


def _group_token_pool_rows(token: dict[str, Any], rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    token_symbol = _norm_symbol(token.get("symbol"))
    token_address = _token_address(token.get("address"))
    candidates = [
        row
        for row in token.get("pools") or []
        if isinstance(row, dict) and _pool_address(row)
    ]
    if not candidates:
        candidates = [
            row
            for row in rows
            if isinstance(row, dict)
            and _pool_address(row)
            and _norm_symbol(row.get("counterSymbol")) == token_symbol
        ]
    if token_address:
        addressed = [
            row
            for row in candidates
            if _token_address(row.get("counterAddress")) == token_address
        ]
        if addressed:
            candidates = addressed
    return _best_pool_rows(candidates)


def _token_instance_keys(token: dict[str, Any], symbol_to_keys: dict[str, set[str]]) -> list[str]:
    address = _token_address(token.get("address"))
    if address:
        return [f"address:{address}"]
    symbol = _norm_symbol(token.get("symbol"))
    if not symbol:
        return []
    return sorted(symbol_to_keys.get(symbol) or [f"symbol:{symbol}"])


def _anchor_group_direction_rows(group: dict[str, Any], direction: str) -> list[dict[str, Any]]:
    rows = [row for row in group.get(f"{direction}Pools") or [] if isinstance(row, dict) and _pool_address(row)]
    if rows or group.get(f"{direction}PoolsRef") != "sharedPools":
        return rows
    directional_rows: list[dict[str, Any]] = []
    route_text_key = f"{direction}RouteText"
    route_key_key = f"{direction}RouteKey"
    for row in group.get("sharedPools") or []:
        if not isinstance(row, dict) or not _pool_address(row):
            continue
        copied = dict(row)
        copied["direction"] = direction
        if row.get(route_text_key):
            copied["routeText"] = row.get(route_text_key)
        if row.get(route_key_key):
            copied["routeKey"] = row.get(route_key_key)
        directional_rows.append(copied)
    return directional_rows


def _anchor_group_direction_tokens(group: dict[str, Any], direction: str) -> list[dict[str, Any]]:
    tokens = [
        row
        for row in group.get(f"{direction}Tokens") or []
        if isinstance(row, dict) and _norm_symbol(row.get("symbol"))
    ]
    if tokens or group.get(f"{direction}TokensRef") != "sharedTokens":
        return tokens
    return [
        row
        for row in group.get("sharedTokens") or []
        if isinstance(row, dict) and _norm_symbol(row.get("symbol"))
    ]


def _v4_index_bridge_pairs_for_anchor_groups(
    anchor_groups: list[dict[str, Any]],
    *,
    chain_id: int | None,
    protocols: list[str] | None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    return robinhood_testnet_v4_bridge_pairs_for_anchor_groups(
        anchor_groups,
        chain_id=chain_id,
        protocols=protocols,
    )


def _chain_fund_anchor_pair_observation(
    *,
    chain_id: int | None,
    protocols: list[str] | None,
) -> dict[str, Any]:
    return robinhood_testnet_v4_fund_anchor_pair_observation(chain_id=chain_id, protocols=protocols)


def _best_pool_rows(pools: list[dict[str, Any]], *, exclude_pool_ids: set[str] | None = None) -> list[dict[str, Any]]:
    exclude = {str(pool_id).strip().lower() for pool_id in (exclude_pool_ids or set()) if str(pool_id or "").strip()}
    rows = [pool for pool in pools if isinstance(pool, dict) and _pool_address(pool)]
    if exclude:
        rows = [pool for pool in rows if _pool_address(pool).lower() not in exclude]
    rows.sort(
        key=lambda pool: (
            _pool_tvl_usd(pool),
            _number((pool.get("balances") or {}).get("tvlUsd")),
            str(_pool_address(pool)),
        ),
        reverse=True,
    )
    return rows


def _reentry_route_has_reference_profit(
    *,
    anchor_token: dict[str, Any],
    counter_token: dict[str, Any],
    first_pool: dict[str, Any],
    second_pool: dict[str, Any],
    price_map: dict[str, float],
) -> bool:
    route = {
        "routeTokenPath": [anchor_token, counter_token, anchor_token],
        "executionPools": [first_pool, second_pool],
        "preferredPools": [first_pool, second_pool],
        "strictQualityPools": [first_pool, second_pool],
    }
    analysis = _route_quote_analysis(route, price_map=price_map)
    reference_net_profit_usd = analysis.get("referenceNetProfitUsd")
    return reference_net_profit_usd is not None and float(reference_net_profit_usd) > 0.0


def _route_step_row(
    pool: dict[str, Any],
    *,
    from_token: dict[str, Any],
    to_token: dict[str, Any],
    direction: str,
) -> dict[str, Any]:
    from_symbol = _norm_symbol(from_token.get("symbol"))
    to_symbol = _norm_symbol(to_token.get("symbol"))
    from_address = _token_address(from_token.get("address"))
    to_address = _token_address(to_token.get("address"))
    route_symbols = [from_symbol, to_symbol]
    route_addresses = [from_address, to_address]
    return {
        "pool": _pool_address(pool),
        "direction": direction,
        "anchorSymbol": from_symbol,
        "anchorAddress": from_address,
        "counterSymbol": to_symbol,
        "counterAddress": to_address,
        "routeSymbols": route_symbols,
        "routeAddresses": route_addresses,
        "routeText": " -> ".join(route_symbols),
        "poolIdentityMode": pool.get("poolIdentityMode") or "",
        "protocol": pool.get("protocol") or pool.get("dex") or "",
        "dex": pool.get("dex") or pool.get("protocol") or "",
        "fee": pool.get("fee"),
        "quality": pool.get("quality"),
        "classification": pool.get("classification") or {},
        "balances": pool.get("balances") or {},
        "depthBand": pool.get("depthBand") or {},
        "quoteSummary": pool.get("quoteSummary") or {},
        "tick": pool.get("tick"),
        "sqrtPriceX96": pool.get("sqrtPriceX96"),
        "liquidity": pool.get("liquidity"),
        "token0": pool.get("token0"),
        "token1": pool.get("token1"),
        "token0Symbol": pool.get("token0Symbol"),
        "token1Symbol": pool.get("token1Symbol"),
        "pairSymbol": pool.get("symbolPair") or "",
        "pairAddress": pool.get("addressPair") or "",
        "source": pool.get("source") or "",
        "evidence": pool.get("evidence") or {},
    }


def _anchor_observation_sequences(
    anchor_groups: list[dict[str, Any]],
    pairs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    _, symbol_to_keys, _, pair_by_edge = _token_graph(pairs)
    pair_by_symbol_edge = _pair_symbol_graph(pairs)
    sequences: list[dict[str, Any]] = []
    seen: set[tuple[str, ...]] = set()

    def _best_pool_from_pair(pair: dict[str, Any], *, exclude_pool_ids: set[str] | None = None) -> dict[str, Any] | None:
        pools = _best_pool_rows(_pools_for_pair(pair), exclude_pool_ids=exclude_pool_ids)
        return pools[0] if pools else None

    def _best_pair_for_tokens(y_token: dict[str, Any], z_token: dict[str, Any]) -> dict[str, Any] | None:
        best: dict[str, Any] | None = None
        for y_key in _token_instance_keys(y_token, symbol_to_keys):
            for z_key in _token_instance_keys(z_token, symbol_to_keys):
                if y_key == z_key:
                    continue
                candidate = pair_by_edge.get(frozenset((y_key, z_key)))
                if candidate is not None and (best is None or _pair_score(candidate) > _pair_score(best)):
                    best = candidate
        if best is not None:
            return best
        y_symbol = _norm_symbol(y_token.get("symbol"))
        z_symbol = _norm_symbol(z_token.get("symbol"))
        if y_symbol and z_symbol and y_symbol != z_symbol:
            return pair_by_symbol_edge.get(frozenset((y_symbol, z_symbol)))
        return None

    for group in anchor_groups:
        if not isinstance(group, dict):
            continue
        anchor_symbol = _norm_symbol(group.get("anchorSymbol"))
        if not anchor_symbol or not _route_symbol_allowed(anchor_symbol):
            continue
        anchor_address = _token_address((group.get("anchorAddresses") or [""])[0])
        anchor_token = {"symbol": anchor_symbol, "address": anchor_address}
        yc_tokens = _anchor_group_direction_tokens(group, "yc")
        zc_tokens = _anchor_group_direction_tokens(group, "zc")
        yc_rows = _anchor_group_direction_rows(group, "yc")
        zc_rows = _anchor_group_direction_rows(group, "zc")
        for yc_token in yc_tokens:
            y_symbol = _norm_symbol(yc_token.get("symbol"))
            if not y_symbol or not _route_symbol_allowed(y_symbol):
                continue
            yc_pool = (_group_token_pool_rows(yc_token, yc_rows) or [None])[0]
            if yc_pool is None:
                continue
            yc_pool_id = _pool_address(yc_pool)
            if not yc_pool_id:
                continue
            for zc_token in zc_tokens:
                z_symbol = _norm_symbol(zc_token.get("symbol"))
                if not z_symbol or not _route_symbol_allowed(z_symbol):
                    continue
                if _token_key(yc_token) == _token_key(zc_token):
                    continue
                zc_pool = (_group_token_pool_rows(zc_token, zc_rows) or [None])[0]
                if zc_pool is None:
                    continue
                zc_pool_id = _pool_address(zc_pool)
                if not zc_pool_id:
                    continue
                pair_yz = _best_pair_for_tokens(yc_token, zc_token)
                if pair_yz is None:
                    continue
                yz_pool = _best_pool_from_pair(pair_yz)
                if yz_pool is None:
                    continue
                yz_pool_id = _pool_address(yz_pool)
                if not yz_pool_id:
                    continue
                key = (
                    anchor_symbol,
                    y_symbol,
                    z_symbol,
                    yc_pool_id.lower(),
                    yz_pool_id.lower(),
                    zc_pool_id.lower(),
                )
                if key in seen:
                    continue
                seen.add(key)
                route_rows = [
                    _route_step_row(yc_pool, from_token=anchor_token, to_token=yc_token, direction="yc"),
                    _route_step_row(yz_pool, from_token=yc_token, to_token=zc_token, direction="bridge"),
                    _route_step_row(zc_pool, from_token=zc_token, to_token=anchor_token, direction="zc"),
                ]
                route_pool_ids = {yc_pool_id, yz_pool_id, zc_pool_id}
                sequences.append(
                    {
                        "kind": "anchor_closed_loop_token_bridge",
                        "anchorSymbol": anchor_symbol,
                        "counterSymbol": z_symbol,
                        "bridgeSymbol": y_symbol,
                        "pathSymbols": [anchor_symbol, y_symbol, z_symbol, anchor_symbol],
                        "pathText": f"{anchor_symbol} -> {y_symbol} -> {z_symbol} -> {anchor_symbol}",
                        "pool": yz_pool_id,
                        "ycPool": route_rows[0],
                        "bridgePool": route_rows[1],
                        "zcPool": route_rows[-1],
                        "routePools": route_rows,
                        "ycPoolCount": str(len(yc_rows)),
                        "zcPoolCount": str(len(zc_rows)),
                        "candidatePoolCount": str(len(route_pool_ids)),
                        "matchedPoolCount": str(len(route_rows)),
                        "anchorAddresses": list(group.get("anchorAddresses") or []),
                        "counterTokenCount": str(len({y_symbol, z_symbol} - {anchor_symbol})),
                    }
                )
    sequences.sort(
        key=lambda item: (
            str(item.get("anchorSymbol") or ""),
            str(item.get("counterSymbol") or ""),
            str(item.get("pool") or ""),
        )
    )
    return sequences


def _route_anchor_symbol(route: dict[str, Any]) -> str:
    return _norm_symbol(route.get("anchorSymbol") or route.get("borrowSymbol"))


def _sequence_pair_row(step: dict[str, Any]) -> dict[str, Any]:
    route_symbols = [symbol for symbol in step.get("routeSymbols") or [] if symbol]
    return {
        "symbolPair": step.get("pairSymbol") or " -> ".join(route_symbols),
        "tradeSymbols": route_symbols,
        "poolCount": 1 if _pool_address(step) else 0,
        "classification": step.get("classification") or {},
        "pool": _pool_address(step),
        "protocol": step.get("protocol") or step.get("dex") or "",
        "fee": step.get("fee"),
        "pools": [step] if _pool_address(step) else [],
    }


def _closed_route_from_sequence(sequence: dict[str, Any]) -> dict[str, Any]:
    route_pools = [step for step in sequence.get("routePools") or [] if isinstance(step, dict)]
    route_pairs = [_sequence_pair_row(step) for step in route_pools]
    first_leg = route_pairs[0] if route_pairs else {}
    second_legs = route_pairs[1:] if len(route_pairs) > 1 else []
    return {
        "kind": "bridge_closed_loop",
        "borrowSymbol": sequence.get("anchorSymbol"),
        "anchorSymbol": sequence.get("anchorSymbol"),
        "bridgeSymbol": sequence.get("bridgeSymbol"),
        "targetSymbol": sequence.get("counterSymbol"),
        "pathSymbols": list(sequence.get("pathSymbols") or []),
        "pathText": sequence.get("pathText") or "",
        "closedLoop": True,
        "firstLegCount": 1 if first_leg else 0,
        "secondLegCount": len(second_legs),
        "firstLegPair": first_leg,
        "secondLegPairs": second_legs,
        "routePairs": route_pairs,
        "routePools": route_pools,
        "source": sequence.get("source") or "yc_zc_bridge_observation",
    }


def _candidate_observation_groups(
    anchor_groups: list[dict[str, Any]],
    observation_sequences: list[dict[str, Any]],
    closed_routes: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    sequences_by_anchor: dict[str, list[dict[str, Any]]] = {}
    direct_routes_by_anchor: dict[str, list[dict[str, Any]]] = {}
    bridge_routes_by_anchor: dict[str, list[dict[str, Any]]] = {}
    closed_routes_by_anchor: dict[str, list[dict[str, Any]]] = {}

    for sequence in observation_sequences:
        anchor_symbol = _norm_symbol(sequence.get("anchorSymbol"))
        if anchor_symbol:
            sequences_by_anchor.setdefault(anchor_symbol, []).append(sequence)

    for route in closed_routes:
        anchor_symbol = _route_anchor_symbol(route)
        if not anchor_symbol:
            continue
        closed_routes_by_anchor.setdefault(anchor_symbol, []).append(route)
        kind = str(route.get("kind") or "").strip().lower()
        if kind == "direct_closed_pair":
            direct_routes_by_anchor.setdefault(anchor_symbol, []).append(route)
        elif kind == "bridge_closed_loop":
            bridge_routes_by_anchor.setdefault(anchor_symbol, []).append(route)

    def route_sort_key(route: dict[str, Any]) -> tuple[float, int, str]:
        pools = [pool for pair in route.get("routePairs") or [] for pool in pair.get("pools") or []]
        if not pools:
            pools = [pool for pool in route.get("routePools") or [] if isinstance(pool, dict)]
        tvl = sum(_number((pool.get("balances") or {}).get("tvlUsd")) for pool in pools)
        return (tvl, len(route.get("routePairs") or []), str(route.get("pathText") or route.get("symbolPair") or ""))

    def sequence_sort_key(sequence: dict[str, Any]) -> tuple[str, str, str]:
        return (
            str(sequence.get("counterSymbol") or ""),
            str(sequence.get("pool") or ""),
            str(sequence.get("pathText") or ""),
        )

    grouped: list[dict[str, Any]] = []
    for group in anchor_groups:
        if not isinstance(group, dict):
            continue
        anchor_symbol = _norm_symbol(group.get("anchorSymbol"))
        if not anchor_symbol:
            continue
        sequences = sorted(sequences_by_anchor.get(anchor_symbol) or [], key=sequence_sort_key)
        direct_routes = sorted(direct_routes_by_anchor.get(anchor_symbol) or [], key=route_sort_key, reverse=True)
        bridge_routes = sorted(bridge_routes_by_anchor.get(anchor_symbol) or [], key=route_sort_key, reverse=True)
        closed = sorted(closed_routes_by_anchor.get(anchor_symbol) or [], key=route_sort_key, reverse=True)
        yc_token_count = _int(group.get("ycTokenCount"))
        zc_token_count = _int(group.get("zcTokenCount"))
        if sequences:
            observation_status = "reachable"
            empty_reason = None
        elif not yc_token_count or not zc_token_count:
            observation_status = "missing_yc_or_zc"
            empty_reason = "YC or ZC candidate set is empty"
        else:
            observation_status = "no_yz_pair"
            empty_reason = "YC and ZC candidates exist, but no Y-Z trading pair pool is available"
        copied = dict(group)
        copied["observationSequences"] = sequences
        copied["observationSequenceCount"] = len(sequences)
        copied["observationSequencePreview"] = [sequence.get("pathText") or sequence.get("pool") or "" for sequence in sequences[:6]]
        copied["observationReachable"] = bool(sequences)
        copied["observationEmptyReason"] = empty_reason
        copied["observationState"] = {
            "status": observation_status,
            "reachable": bool(sequences),
            "reason": empty_reason,
            "ycTokenCount": yc_token_count,
            "zcTokenCount": zc_token_count,
            "groupCount": len(sequences),
        }
        copied["directClosedLoopRoutes"] = direct_routes[:10]
        copied["directClosedLoopCount"] = len(direct_routes)
        copied["bridgeClosedLoopRoutes"] = bridge_routes[:10]
        copied["bridgeClosedLoopCount"] = len(bridge_routes)
        copied["closedLoopRoutes"] = closed[:20]
        copied["closedLoopRouteCount"] = len(closed)
        copied["observationCombinationCount"] = len(sequences)
        copied["observationCombinationPreview"] = copied["observationSequencePreview"]
        grouped.append(copied)
    return grouped


def _route_candidate(
    *,
    kind: str,
    anchor: str,
    target: str,
    path_symbols: list[str],
    pairs: list[dict[str, Any]],
    bridge_symbols: list[str] | None = None,
    path_tokens: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    pools = [pool for pair in pairs for pool in _pools_for_pair(pair)]
    primary = pairs[-1] if pairs else {}
    route_tokens = path_tokens or [{"symbol": symbol, "address": ""} for symbol in path_symbols if symbol]
    route_symbols = _route_symbols_from_tokens(route_tokens)
    route_addresses = _route_addresses_from_tokens(route_tokens)
    return {
        "symbolPair": " -> ".join(route_symbols),
        "addressPair": " -> ".join(address for address in route_addresses if address),
        "routeTokenPath": route_tokens,
        "routeAddresses": route_addresses,
        "tokenX": primary.get("tokenX"),
        "tokenY": primary.get("tokenY"),
        "tokenXSymbol": anchor,
        "tokenYSymbol": target,
        "classification": primary.get("classification") or {},
        "allPoolCount": str(len(pools)),
        "strictQualityPoolCount": str(len(pools)),
        "preferredPoolCount": str(len(pools)),
        "executionPoolCount": str(len(pools[:5])),
        "rejectedPoolCount": "0",
        "hasStrictQualityPools": bool(pools),
        "hasPreferredPools": bool(pools),
        "strictQualityPools": pools,
        "preferredPools": pools,
        "executionPools": pools[:5],
        "rejectedPools": [],
        "rejectReasonCounts": {},
        "flashLoanLinkage": {
            "strategy": "stable_anchor_candidate_path" if kind == "direct_anchor_pair" else "stable_anchor_bridge_candidate_path",
            "borrow_symbols": [anchor],
            "borrow_tokens": [route_addresses[0]] if route_addresses and route_addresses[0] else [],
            "route_symbols": route_symbols,
            "route_addresses": route_addresses,
            "ready_for_atomic_arbitrage": bool(pools),
            "conditions": [
                "quote page must verify executable output after slippage",
                "route must repay the borrowed anchor asset in the same transaction",
                "gas and flash loan fee must be below gross spread",
            ],
        },
        "overviewRoute": {
            "kind": kind,
            "anchorBorrowSymbol": anchor,
            "targetSymbol": target,
            "bridgeSymbols": bridge_symbols or [],
            "pathSymbols": route_symbols,
            "pathTokens": route_tokens,
            "pathAddresses": route_addresses,
            "pathText": " -> ".join(route_symbols),
            "pathAddressText": " -> ".join(address for address in route_addresses if address),
            "candidatePairSymbols": [pair.get("symbolPair") for pair in pairs],
            "candidatePairAddresses": [pair.get("addressPair") for pair in pairs],
            "analysisStage": "overview_candidate_only",
            "nextStep": "quotes_and_route_analysis",
        },
    }


def _token_from_pool_for_symbol(pool: dict[str, Any], symbol: str) -> dict[str, Any]:
    normalized = _norm_symbol(symbol)
    for token in _pool_tokens(pool):
        if _norm_symbol(token.get("symbol")) == normalized:
            return {"symbol": normalized, "address": _token_address(token.get("address"))}
    return {"symbol": normalized, "address": ""}


def _token_path_from_route_steps(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    tokens: list[dict[str, Any]] = []
    for step in steps:
        if not isinstance(step, dict):
            continue
        symbols = [_norm_symbol(symbol) for symbol in step.get("routeSymbols") or [] if _norm_symbol(symbol)]
        addresses = [_token_address(address) for address in step.get("routeAddresses") or []]
        if len(symbols) < 2:
            symbols = [_norm_symbol(step.get("anchorSymbol")), _norm_symbol(step.get("counterSymbol"))]
            addresses = [_token_address(step.get("anchorAddress")), _token_address(step.get("counterAddress"))]
        if len(symbols) < 2 or not symbols[0] or not symbols[1]:
            continue
        first = {"symbol": symbols[0], "address": addresses[0] if len(addresses) > 0 else ""}
        second = {"symbol": symbols[1], "address": addresses[1] if len(addresses) > 1 else ""}
        if not tokens:
            tokens.append(first)
        tokens.append(second)
    return tokens


def _token_path_from_observation_route(route: dict[str, Any]) -> list[dict[str, Any]]:
    route_tokens = [token for token in route.get("routeTokenPath") or [] if isinstance(token, dict)]
    if route_tokens:
        return route_tokens
    route_steps = [step for step in route.get("routePools") or [] if isinstance(step, dict)]
    if route_steps:
        tokens = _token_path_from_route_steps(route_steps)
        if tokens:
            return tokens
    path_symbols = [_norm_symbol(symbol) for symbol in route.get("pathSymbols") or [] if _norm_symbol(symbol)]
    path_addresses = [_token_address(address) for address in route.get("routeAddresses") or []]
    if path_symbols and len(path_addresses) == len(path_symbols):
        return [{"symbol": symbol, "address": path_addresses[index]} for index, symbol in enumerate(path_symbols)]
    route_pairs = [pair for pair in route.get("routePairs") or [] if isinstance(pair, dict)]
    tokens: list[dict[str, Any]] = []
    for index, symbol in enumerate(path_symbols):
        if not symbol:
            continue
        pair = None
        if index < len(route_pairs):
            pair = route_pairs[index]
        elif index > 0 and index - 1 < len(route_pairs):
            pair = route_pairs[index - 1]
        pool = (_pools_for_pair(pair) or [{}])[0] if pair else {}
        tokens.append(_token_from_pool_for_symbol(pool, symbol))
    return tokens or [{"symbol": symbol, "address": ""} for symbol in path_symbols]


def _candidate_pair_symbols_from_observation_route(route: dict[str, Any]) -> list[str]:
    symbols: list[str] = []
    for pair in route.get("routePairs") or []:
        if isinstance(pair, dict):
            symbol = str(pair.get("symbolPair") or pair.get("pairSymbol") or "").strip()
            if symbol and symbol not in symbols:
                symbols.append(symbol)
    for step in route.get("routePools") or []:
        if isinstance(step, dict):
            symbol = str(step.get("pairSymbol") or step.get("symbolPair") or "").strip()
            if symbol and symbol not in symbols:
                symbols.append(symbol)
    return symbols


def _observation_route_to_path_candidate(route: dict[str, Any], *, chain_id: int | None) -> dict[str, Any]:
    route_tokens = _token_path_from_observation_route(route)
    route_symbols = _route_symbols_from_tokens(route_tokens) or [_norm_symbol(symbol) for symbol in route.get("pathSymbols") or [] if _norm_symbol(symbol)]
    route_addresses = _route_addresses_from_tokens(route_tokens)
    pools = _route_pools(route)
    anchor = _norm_symbol(route.get("anchorSymbol") or route.get("borrowSymbol") or (route_symbols[0] if route_symbols else ""))
    target = _norm_symbol(route.get("targetSymbol") or route.get("counterSymbol") or (route_symbols[-2] if len(route_symbols) > 2 else ""))
    kind = "direct_anchor_pair" if str(route.get("kind") or "").lower() == "direct_closed_pair" or len(route_symbols) <= 3 else "bridge_pair"
    candidate_pair_symbols = _candidate_pair_symbols_from_observation_route(route)
    symbol_pair = route.get("pathText") or " -> ".join(route_symbols)
    strategy = (
        "robinhood_testnet_market_observation_closed_loop"
        if chain_id == ROBINHOOD_TESTNET_CHAIN_ID
        else "market_observation_closed_loop"
    )
    return {
        "symbolPair": symbol_pair,
        "addressPair": " -> ".join(address for address in route_addresses if address),
        "routeTokenPath": route_tokens,
        "routeAddresses": route_addresses,
        "tokenX": route_addresses[0] if route_addresses else "",
        "tokenY": route_addresses[-2] if len(route_addresses) > 2 else (route_addresses[1] if len(route_addresses) > 1 else ""),
        "tokenXSymbol": anchor,
        "tokenYSymbol": target,
        "classification": {"pairClass": "market_observation_closed_loop", "pairClassLabel": "市场观察闭环"},
        "allPoolCount": str(len(pools)),
        "strictQualityPoolCount": str(len(pools)),
        "preferredPoolCount": str(len(pools)),
        "executionPoolCount": str(len(pools)),
        "rejectedPoolCount": "0",
        "hasStrictQualityPools": bool(pools),
        "hasPreferredPools": bool(pools),
        "strictQualityPools": pools,
        "preferredPools": pools,
        "executionPools": pools,
        "rejectedPools": [],
        "rejectReasonCounts": {},
        "marketObservationRoute": route,
        "flashLoanLinkage": {
            "strategy": strategy,
            "borrow_symbols": [anchor] if anchor else [],
            "borrow_tokens": [route_addresses[0]] if route_addresses and route_addresses[0] else [],
            "route_symbols": route_symbols,
            "route_addresses": route_addresses,
            "ready_for_atomic_arbitrage": bool(pools and len(route_symbols) >= 2 and route_symbols[0] == route_symbols[-1]),
            "conditions": [
                "路径必须来自市场观察闭环集合，不能绕过链路专用观察算法。",
                "报价页必须复核每一跳池子输出、滑点、gas 和闪电贷费用。",
                "执行前必须确认借入锚点资产能在同一交易内归还。",
            ],
        },
        "overviewRoute": {
            "kind": kind,
            "anchorBorrowSymbol": anchor,
            "targetSymbol": target,
            "bridgeSymbols": route_symbols[1:-1],
            "pathSymbols": route_symbols,
            "pathTokens": route_tokens,
            "pathAddresses": route_addresses,
            "pathText": symbol_pair,
            "pathAddressText": " -> ".join(address for address in route_addresses if address),
            "candidatePairSymbols": candidate_pair_symbols,
            "candidatePairAddresses": [
                str(pair.get("addressPair") or pair.get("pairAddress") or "").strip()
                for pair in route.get("routePairs") or []
                if isinstance(pair, dict) and str(pair.get("addressPair") or pair.get("pairAddress") or "").strip()
            ],
            "analysisStage": "market_observation_closed_loop",
            "nextStep": "quotes_and_route_analysis",
            "observationSource": route.get("source") or route.get("kind") or "dex_market_observation",
            "marketObservationRoute": True,
        },
    }


def _path_candidates_from_market_observation(observation: dict[str, Any], *, chain_id: int | None) -> list[dict[str, Any]]:
    routes = [
        route
        for route in observation.get("closedLoopRoutes") or []
        if isinstance(route, dict) and route.get("closedLoop") is not False
    ]
    candidates = [_observation_route_to_path_candidate(route, chain_id=chain_id) for route in routes]
    candidates = [candidate for candidate in candidates if candidate.get("routeTokenPath") and _route_pools(candidate)]
    candidates.sort(key=_route_score, reverse=True)
    return candidates


def _route_framework_context_from_observation(
    observation: dict[str, Any],
    pool_payload: dict[str, Any],
    *,
    chain_id: int | None,
    protocols: list[str] | None,
) -> dict[str, Any]:
    overview_paths = _path_candidates_from_market_observation(observation, chain_id=chain_id)
    path_source = "market_observation"
    watch_pairs = list(observation.get("chainSpecificObservationPairs") or [])
    if not overview_paths:
        overview_paths, watch_pairs, anchor_symbols = _overview_path_candidates(pool_payload, chain_id, protocols=protocols)
        closed_loop_anchor_symbols = _observation_anchor_symbols(pool_payload, chain_id=chain_id, protocols=protocols)
        used_market_observation = False
        path_source = "candidate_pool_fallback"
    else:
        anchor_symbols = sorted(
            {
                str((path.get("overviewRoute") or {}).get("anchorBorrowSymbol") or "").strip().upper()
                for path in overview_paths
                if str((path.get("overviewRoute") or {}).get("anchorBorrowSymbol") or "").strip()
            }
        )
        closed_loop_anchor_symbols = anchor_symbols
        used_market_observation = True
    observation_anchor_symbols = list(observation.get("observationAnchorSymbols") or [])
    return {
        "overview_paths": overview_paths,
        "watch_pairs": watch_pairs,
        "anchor_symbols": anchor_symbols,
        "closed_loop_anchor_symbols": closed_loop_anchor_symbols,
        "observation_anchor_symbols": observation_anchor_symbols,
        "path_source": path_source,
        "used_market_observation": used_market_observation,
    }


def _overview_path_candidates(
    pool_payload: dict[str, Any],
    chain_id: int | None,
    protocols: list[str] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    pairs = _candidate_pairs(pool_payload.get("pairs") or [])
    anchors = _borrow_anchor_symbols(pool_payload, chain_id, protocols=protocols)
    token_by_key, symbol_to_keys, neighbor_keys, pair_by_edge = _token_graph(pairs)
    direct_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    routes: list[dict[str, Any]] = []
    route_keys: set[tuple[str, ...]] = set()

    def _route_key_from_route(route: dict[str, Any], *pair_keys: str) -> tuple[str, ...]:
        path_tokens = route.get("routeTokenPath") or []
        token_keys = tuple(_token_key(token) for token in path_tokens if isinstance(token, dict))
        return (*token_keys, *pair_keys)

    for anchor in anchors:
        anchor_keys = sorted(symbol_to_keys.get(anchor) or [])
        for anchor_key in anchor_keys:
            neighbor_keys_for_anchor = sorted(neighbor_keys.get(anchor_key) or [])
            for target_key in neighbor_keys_for_anchor:
                if target_key == anchor_key:
                    continue
                pair = pair_by_edge.get(frozenset((anchor_key, target_key)))
                if pair is None:
                    continue
                anchor_token = _pair_token_entry(pair, anchor_key)
                target_token = _pair_token_entry(pair, target_key)
                if anchor_token is None or target_token is None:
                    continue
                target = _norm_symbol(target_token.get("symbol"))
                if not target or not _route_symbol_allowed(target):
                    continue
                route = _route_candidate(
                    kind="direct_anchor_pair",
                    anchor=anchor,
                    target=target,
                    path_symbols=[anchor, target, anchor],
                    path_tokens=[anchor_token, target_token, anchor_token],
                    pairs=[pair],
                )
                key = (anchor_key, target_key)
                current = direct_by_key.get(key)
                if current is None or _route_score(route) > _route_score(current):
                    direct_by_key[key] = route

    routes.extend(direct_by_key.values())
    route_keys.update(direct_by_key)
    for anchor in anchors:
        anchor_keys = sorted(symbol_to_keys.get(anchor) or [])
        for anchor_key in anchor_keys:
            for mid_key in sorted(neighbor_keys.get(anchor_key) or []):
                if mid_key == anchor_key:
                    continue
                pair_xy = pair_by_edge.get(frozenset((anchor_key, mid_key)))
                if pair_xy is None:
                    continue
                mid_token_xy = _pair_token_entry(pair_xy, mid_key)
                anchor_token_xy = _pair_token_entry(pair_xy, anchor_key)
                if mid_token_xy is None or anchor_token_xy is None:
                    continue
                mid_symbol = _norm_symbol(mid_token_xy.get("symbol"))
                if not mid_symbol or not _route_symbol_allowed(mid_symbol):
                    continue
                for target_key in sorted(neighbor_keys.get(mid_key) or []):
                    if target_key in {anchor_key, mid_key}:
                        continue
                    pair_yz = pair_by_edge.get(frozenset((mid_key, target_key)))
                    pair_zx = pair_by_edge.get(frozenset((target_key, anchor_key)))
                    if pair_yz is None or pair_zx is None:
                        continue
                    target_token_yz = _pair_token_entry(pair_yz, target_key)
                    anchor_token_zx = _pair_token_entry(pair_zx, anchor_key)
                    target_token_zx = _pair_token_entry(pair_zx, target_key)
                    mid_token_yz = _pair_token_entry(pair_yz, mid_key)
                    if any(token is None for token in (target_token_yz, anchor_token_zx, target_token_zx, mid_token_yz)):
                        continue
                    target = _norm_symbol(target_token_yz.get("symbol"))
                    if not target or not _route_symbol_allowed(target):
                        continue
                    route = _route_candidate(
                        kind="bridge_pair",
                        anchor=anchor,
                        target=target,
                        bridge_symbols=[mid_symbol, target],
                        path_symbols=[anchor, mid_symbol, target, anchor],
                        path_tokens=[anchor_token_xy, mid_token_xy, target_token_yz, anchor_token_zx],
                        pairs=[pair_xy, pair_yz, pair_zx],
                    )
                    key = _route_key_from_route(route, str(pair_xy.get("symbolPair") or ""), str(pair_yz.get("symbolPair") or ""), str(pair_zx.get("symbolPair") or ""))
                    if key in route_keys:
                        continue
                    routes.append(route)
                    route_keys.add(key)

    routes.sort(key=_route_score, reverse=True)
    used_pairs = {
        pair_symbol
        for route in routes
        for pair_symbol in (route.get("overviewRoute") or {}).get("candidatePairSymbols") or []
    }
    watch_pairs = [pair for pair in pairs if pair.get("symbolPair") not in used_pairs][:12]
    return routes, watch_pairs, anchors


def dex_market_observation_surface_payload(
    *,
    chain_id: int | None,
    protocols: list[str] | None,
    use_static_observation: bool = False,
) -> dict[str, Any]:
    cached = _cached_framework_surface_payload(
        "dex_market_observation",
        chain_id=chain_id,
        protocols=protocols,
        use_static_observation=use_static_observation,
    )
    if cached is not None:
        cached["marketObservationSourceStage"] = cached.get("marketObservationSourceStage") or cached.get("marketObservationSource") or "computed"
        return cached
    if use_static_observation:
        static_snapshot = load_market_observation_static(chain_id=chain_id, protocols=protocols)
        if static_snapshot.get("exists"):
            payload = observation_payload_from_static(static_snapshot, source="cached_snapshot")
            payload["marketObservationSourceStage"] = payload.get("marketObservationSourceStage") or payload.get("marketObservationSource") or "cached_snapshot"
            _cache_surface_payload(
                "dex_market_observation",
                payload,
                chain_id=chain_id,
                protocols=protocols,
                use_static_observation=use_static_observation,
            )
            return payload
    payload = dex_market_observation_payload(chain_id=chain_id, protocols=protocols)
    payload["marketObservationSourceStage"] = payload.get("marketObservationSourceStage") or payload.get("marketObservationSource") or "computed"
    return payload


def _overview_profit_from_analysis(analysis: dict[str, Any]) -> dict[str, Any]:
    return build_overview_profit_payload(analysis)


def _route_with_overview_profit(
    route: dict[str, Any],
    *,
    price_map: dict[str, float],
    chain_id: int | None = None,
) -> dict[str, Any]:
    return apply_route_overview_profit(
        route,
        price_map=price_map,
        chain_id=chain_id,
        route_quote_analysis_fn=_route_quote_analysis,
    )


def _route_execution_size_probe(
    route_analysis: dict[str, Any],
    *,
    chain_id: int | None = None,
) -> dict[str, Any] | None:
    if chain_id != ROBINHOOD_TESTNET_CHAIN_ID or not _v4_execution_size_probe_enabled():
        return None
    steps = [
        step
        for step in route_analysis.get("steps") or []
        if isinstance(step, dict)
        and str(step.get("protocol") or step.get("dex") or "").strip().lower() == "uniswap_v4"
    ]
    if not steps:
        return None
    network = _chain_network_name(chain_id)
    if not network:
        return None

    max_probe_attempts = max(1, int(os.getenv("DEX_V4_EXECUTION_SIZE_MAX_PROBE_ATTEMPTS", "24") or 24))
    max_probe_multiplier = max(1, int(os.getenv("DEX_V4_EXECUTION_SIZE_MAX_PROBE_MULTIPLIER", "64") or 64))
    min_probe_amount_raw = max(1, int(os.getenv("DEX_V4_EXECUTION_SIZE_MIN_PROBE_AMOUNT_RAW", "1") or 1))
    step_probes: list[dict[str, Any]] = []
    successful_maxima: list[int] = []
    first_failures: list[int] = []
    total_attempts = 0

    for index, step in enumerate(steps, start=1):
        input_decimals = _int(step.get("inputDecimals")) or 18
        amount_in_raw = _int(step.get("amountInRaw"))
        if amount_in_raw <= 0:
            amount_in_raw = _raw_from_human(
                step.get("inputAmount") or step.get("amountIn") or step.get("referenceInputAmount"),
                input_decimals,
            )
        request = {
            "network": network,
            "chainId": chain_id,
            "pool": step.get("pool"),
            "tokenIn": step.get("tokenIn"),
            "tokenOut": step.get("tokenOut"),
            "amountInRaw": str(amount_in_raw or 0),
            "fee": step.get("fee") or step.get("feeBps"),
            "quoteMode": step.get("quoteMode") or "uniswap_v4_sdk_exact_input_single",
            "minProbeAmountRaw": str(min_probe_amount_raw),
            "maxProbeAttempts": str(max_probe_attempts),
            "maxProbeMultiplier": str(max_probe_multiplier),
        }
        try:
            result = probe_v4_executable_amount_from_pool_index(request)
            total_attempts += _int(result.get("probeAttempts"))
            max_executable_amount_raw = _int(result.get("maxExecutableAmountRaw"))
            first_failure_amount_raw = _int(result.get("firstFailureAmountRaw"))
            probe_row = {
                "step": index,
                "pool": step.get("pool"),
                "tokenIn": step.get("tokenIn"),
                "tokenOut": step.get("tokenOut"),
                "amountInRaw": str(amount_in_raw or 0),
                "status": result.get("status"),
                "searchStatus": result.get("searchStatus"),
                "probeAttempts": result.get("probeAttempts"),
                "maxExecutableAmountRaw": result.get("maxExecutableAmountRaw"),
                "firstFailureAmountRaw": result.get("firstFailureAmountRaw"),
                "quoterAddress": result.get("quoterAddress"),
            }
            if max_executable_amount_raw > 0:
                successful_maxima.append(max_executable_amount_raw)
            if first_failure_amount_raw > 0:
                first_failures.append(first_failure_amount_raw)
        except Exception as exc:
            probe_row = {
                "step": index,
                "pool": step.get("pool"),
                "tokenIn": step.get("tokenIn"),
                "tokenOut": step.get("tokenOut"),
                "amountInRaw": str(amount_in_raw or 0),
                "status": "probe_failed",
                "searchStatus": "probe_failed",
                "error": str(exc),
            }
        step_probes.append(probe_row)

    if not step_probes:
        return None

    probe_success_count = len(successful_maxima)
    failed_count = len(step_probes) - probe_success_count
    if probe_success_count == 0:
        status = "liquidity_probe_unexecutable"
    elif failed_count > 0:
        status = "liquidity_probe_partial"
    else:
        status = "liquidity_probe_complete"
    search_status = "bounded" if first_failures else ("lower_bound_verified" if successful_maxima else "no_successful_amount_found")
    return {
        "status": status,
        "searchStatus": search_status,
        "probeAttempts": total_attempts,
        "stepProbes": step_probes,
        # Each hop is denominated in a different token, so these values cannot
        # be collapsed into a route-level borrow amount. The route probe below
        # derives that amount by quoting the complete path.
        "inputAmountUnit": "per_hop",
        "maxExecutableAmountRaw": None,
        "firstFailureAmountRaw": None,
    }


def _route_size_analysis(
    route: dict[str, Any],
    *,
    price_map: dict[str, float],
    chain_id: int | None = None,
) -> dict[str, Any]:
    route_analysis = _route_quote_analysis(route, price_map=price_map, chain_id=chain_id)
    execution_size_probe = _route_execution_size_probe(route_analysis, chain_id=chain_id)

    def route_quote_analysis_with_probe(
        inner_route: dict[str, Any],
        *,
        price_map: dict[str, float],
        chain_id: int | None = None,
        notional_usd: float = QUOTE_PROBE_NOTIONAL_USD,
        token_basis_input_amount: float | None = None,
    ) -> dict[str, Any]:
        analysis = _route_quote_analysis(
            inner_route,
            price_map=price_map,
            chain_id=chain_id,
            notional_usd=notional_usd,
            token_basis_input_amount=token_basis_input_amount,
        )
        if execution_size_probe and inner_route is route:
            analysis = {**analysis, **execution_size_probe, "executionSizeProbe": execution_size_probe}
        return analysis

    return build_route_size_analysis(
        route,
        price_map=price_map,
        chain_id=chain_id,
        route_quote_analysis_fn=route_quote_analysis_with_probe,
        quote_probe_notional_usd=QUOTE_PROBE_NOTIONAL_USD,
    )


def dex_overview_payload(
    *,
    chain_id: int | None = None,
    protocols: list[str] | None = None,
    use_static_observation: bool = False,
) -> dict[str, Any]:
    cached = _cached_framework_surface_payload(
        "dex_overview",
        chain_id=chain_id,
        protocols=protocols,
        use_static_observation=use_static_observation,
    )
    if cached is not None:
        cached["marketObservationSourceStage"] = cached.get("marketObservationSourceStage") or cached.get("marketObservationSource") or "computed"
        return cached
    use_compact_static_overview = bool(
        use_static_observation and chain_id == ROBINHOOD_TESTNET_CHAIN_ID
    )
    compact_snapshot = (
        load_market_observation_overview_static(chain_id=chain_id, protocols=protocols)
        if use_compact_static_overview
        else {}
    )
    if compact_snapshot.get("exists"):
        if compact_snapshot.get("closedLoopRouteSummaries"):
            overview_paths = _overview_paths_from_static_summary(compact_snapshot, chain_id=chain_id)
            pool_payload = _overview_pool_payload_from_static_observation(compact_snapshot)
            payload = {
                "mode": "dex_overview",
                "chain_id": chain_id,
                "selected_protocols": protocols or [],
                "summary": {
                    **(compact_snapshot.get("summary") or {}),
                    "arbitragePathCount": len(overview_paths),
                    "candidatePathCount": len(overview_paths),
                    "directAnchorPathCount": sum(1 for route in overview_paths if (route.get("overviewRoute") or {}).get("kind") == "direct_anchor_pair"),
                    "bridgePathCount": sum(1 for route in overview_paths if (route.get("overviewRoute") or {}).get("kind") == "bridge_pair"),
                    "watchPairCount": len(compact_snapshot.get("chainSpecificObservationPairs") or []),
                    "marketObservationRouteCount": len(overview_paths),
                    "overviewProbeNotionalUsd": QUOTE_PROBE_NOTIONAL_USD,
                    "overviewProbeCompleteCount": 0,
                    "overviewLiveQuoterRequiredCount": len(overview_paths),
                    "overviewProfitableRouteCount": 0,
                    "overviewExecutableCandidateCount": 0,
                    "overviewChainSubmissionCandidateCount": 0,
                },
                "decisionBasis": "robinhood_testnet_market_observation_closed_loop" if chain_id == ROBINHOOD_TESTNET_CHAIN_ID else "market_observation_closed_loop",
                "marketObservationSource": "cached_snapshot",
                "marketObservationSourceStage": "cached_snapshot",
                "routeFrameworkSource": "market_observation",
                "marketObservation": {
                    "summary": compact_snapshot.get("summary") or {},
                    "chainSpecificObservationPairs": compact_snapshot.get("chainSpecificObservationPairs") or [],
                },
                "marketObservationSnapshotKind": "dex_overview_compact",
                "poolStaticTable": pool_payload.get("poolStaticTable") or {},
                "flashLoanLiquidity": pool_payload.get("flashLoanLiquidity") or {},
                "dexSettlement": pool_payload.get("dexSettlement") or {},
                "arbitragePaths": overview_paths,
                "watchPairs": compact_snapshot.get("chainSpecificObservationPairs") or [],
            }
            _cache_surface_payload(
                "dex_overview",
                payload,
                chain_id=chain_id,
                protocols=protocols,
                use_static_observation=use_static_observation,
            )
            return payload
        # The overview index owns route summaries, while the observation
        # snapshot owns the compact per-hop evidence used for the rough probe.
        # Read that scoped snapshot here instead of rebuilding candidate pools.
        observation_snapshot = load_market_observation_static(
            chain_id=chain_id,
            protocols=protocols,
        )
        observation = observation_payload_from_static(
            observation_snapshot if observation_snapshot.get("exists") else compact_snapshot,
            source="cached_snapshot",
        )
        observation["marketObservationSnapshotKind"] = "dex_overview_compact"
        pool_payload = _overview_pool_payload_from_static_observation(observation)
    elif use_compact_static_overview:
        # A legacy full snapshot can exceed 100 MB because it retains every
        # observed V4 pool. Do not parse or rebuild it inside a page request.
        observation = {
            "mode": "dex_market_observation",
            "chain_id": chain_id,
            "selected_protocols": protocols or [],
            "summary": {},
            "marketObservationSource": "overview_snapshot_pending",
            "marketObservationSourceStage": "overview_snapshot_pending",
            "marketObservationSnapshotKind": "dex_overview_compact",
            "marketObservationStaticTable": {
                "exists": False,
                "network": _chain_network_name(chain_id),
                "chain_id": chain_id,
                "protocols": protocols or [],
                "source": "overview_snapshot_pending",
            },
        }
        pool_payload = _overview_pool_payload_from_static_observation(observation)
    else:
        observation = dex_market_observation_surface_payload(
            chain_id=chain_id,
            protocols=protocols,
            use_static_observation=use_static_observation,
        )
        pool_payload = _candidate_payload(chain_id, protocols)
    price_map = _price_map_from_pairs(pool_payload.get("pairs") or [])
    frame_context = _route_framework_context_from_observation(
        observation,
        pool_payload,
        chain_id=chain_id,
        protocols=protocols,
    )
    ready_paths = frame_context["overview_paths"]
    used_market_observation = bool(frame_context["used_market_observation"])
    watch_pairs = list(frame_context["watch_pairs"])
    anchor_symbols = list(frame_context["anchor_symbols"])
    closed_loop_anchor_symbols = list(frame_context["closed_loop_anchor_symbols"])
    observation_anchor_symbols = list(frame_context["observation_anchor_symbols"])
    ready_paths = [_route_with_overview_profit(route, price_map=price_map, chain_id=chain_id) for route in ready_paths]
    overview_profits = [route.get("overviewProfit") or {} for route in ready_paths]
    observed_net_profits = [
        _number(profit.get("observedNetProfitUsd"))
        for profit in overview_profits
        if profit.get("observedNetProfitUsd") is not None
    ]
    executable_net_profits = [
        _number(profit.get("netProfitUsd"))
        for profit in overview_profits
        if profit.get("executableCandidate") and profit.get("netProfitUsd") is not None
    ]
    token_basis_returns = [
        _number(profit.get("referenceNetReturnBps"))
        for profit in overview_profits
        if profit.get("referenceNetReturnBps") is not None
    ]
    market_observation_source = observation.get("marketObservationSource") or "computed"
    market_observation_source_stage = observation.get("marketObservationSourceStage") or market_observation_source
    if market_observation_source in {"generated_live_snapshot", "refreshed_live_snapshot"}:
        market_observation_source = "computed"
    decision_basis = (
        "robinhood_testnet_market_observation_closed_loop"
        if chain_id == ROBINHOOD_TESTNET_CHAIN_ID
        else "market_observation_closed_loop"
    )
    if not used_market_observation:
        decision_basis = (
            "symbol_anchor_to_non_anchor_candidate_path"
            if chain_id == ROBINHOOD_TESTNET_CHAIN_ID
            else "borrow_anchor_to_non_anchor_candidate_path"
        )
    payload = {
        "mode": "dex_overview",
        "chain_id": chain_id,
        "selected_protocols": protocols or [],
        "summary": {
            **(pool_payload.get("summary") or {}),
            "anchorBorrowSymbols": anchor_symbols,
            "closedLoopAnchorSymbols": closed_loop_anchor_symbols,
            "closedLoopAnchorCount": len(closed_loop_anchor_symbols),
            "observationAnchorSymbols": observation_anchor_symbols,
            "observationAnchorCount": len(observation_anchor_symbols),
            "arbitragePathCount": len(ready_paths),
            "candidatePathCount": len(ready_paths),
            "directAnchorPathCount": sum(1 for route in ready_paths if (route.get("overviewRoute") or {}).get("kind") == "direct_anchor_pair"),
            "bridgePathCount": sum(1 for route in ready_paths if (route.get("overviewRoute") or {}).get("kind") == "bridge_pair"),
            "watchPairCount": len(watch_pairs),
            "marketObservationRouteCount": len(observation.get("closedLoopRoutes") or []),
            "marketObservationSequenceCount": _int((observation.get("summary") or {}).get("observationSequenceCount")),
            "chainSpecificObservationPairCount": len(observation.get("chainSpecificObservationPairs") or []),
            "overviewProbeNotionalUsd": QUOTE_PROBE_NOTIONAL_USD,
            "overviewProbeCompleteCount": sum(1 for profit in overview_profits if profit.get("complete")),
            "overviewLiveQuoterRequiredCount": sum(1 for profit in overview_profits if profit.get("requiresLiveQuoter")),
            "overviewSdkAdapterRequiredCount": sum(1 for profit in overview_profits if profit.get("adapterRequired")),
            "overviewProfitableRouteCount": sum(1 for profit in overview_profits if profit.get("profitable")),
            "overviewExecutableCandidateCount": sum(1 for profit in overview_profits if profit.get("executableCandidate")),
            "overviewChainSubmissionCandidateCount": sum(1 for profit in overview_profits if profit.get("chainSubmissionCandidate")),
            "overviewNonStableChainSubmissionCandidateCount": sum(
                1
                for profit in overview_profits
                if profit.get("chainSubmissionCandidate") and not profit.get("borrowAssetIsStable")
            ),
            "overviewBestObservedNetProfitUsd": max(observed_net_profits) if observed_net_profits else None,
            "overviewBestExecutableNetProfitUsd": max(executable_net_profits) if executable_net_profits else None,
            "overviewTokenBasisRouteCount": sum(1 for profit in overview_profits if profit.get("referenceReturnBasis") == "borrow_token"),
            "overviewTokenBasisProfitableRouteCount": sum(1 for profit in overview_profits if profit.get("tokenBasisProfitable")),
            "overviewBestTokenBasisNetReturnBps": max(token_basis_returns) if token_basis_returns else None,
        },
        "decisionBasis": decision_basis,
        "marketObservationSource": market_observation_source,
        "marketObservationSourceStage": market_observation_source_stage,
        "routeFrameworkSource": "market_observation" if used_market_observation else "candidate_pool_fallback",
        "marketObservation": {
            "summary": observation.get("summary") or {},
            "chainSpecificObservationPairs": observation.get("chainSpecificObservationPairs") or [],
            "chainSpecificObservationPools": observation.get("chainSpecificObservationPools") or [],
        },
        "marketObservationSnapshotKind": observation.get("marketObservationSnapshotKind"),
        "poolStaticTable": pool_payload.get("poolStaticTable") or {},
        "flashLoanLiquidity": pool_payload.get("flashLoanLiquidity") or {},
        "dexSettlement": pool_payload.get("dexSettlement") or {},
        "arbitragePaths": ready_paths,
        "watchPairs": watch_pairs,
    }
    _cache_surface_payload(
        "dex_overview",
        payload,
        chain_id=chain_id,
        protocols=protocols,
        use_static_observation=use_static_observation,
    )
    return payload


def dex_market_observation_payload(*, chain_id: int | None = None, protocols: list[str] | None = None) -> dict[str, Any]:
    from market.binance_market.dex_observation_surface import build_market_observation_payload

    return build_market_observation_payload(
        sys.modules[__name__],
        chain_id=chain_id,
        protocols=protocols,
    )


def dex_quote_routes_payload(
    *,
    chain_id: int | None = None,
    protocols: list[str] | None = None,
    use_static_observation: bool = False,
) -> dict[str, Any]:
    cached = _cached_framework_surface_payload(
        "dex_quote_routes",
        chain_id=chain_id,
        protocols=protocols,
        use_static_observation=use_static_observation,
    )
    if cached is not None:
        cached["marketObservationSourceStage"] = cached.get("marketObservationSourceStage") or cached.get("marketObservationSource") or "computed"
        return cached
    pool_payload = _candidate_payload(chain_id, protocols)
    settlement = pool_payload.get("dexSettlement") or {}
    price_map = _price_map_from_pairs(pool_payload.get("pairs") or [])
    if chain_id == ROBINHOOD_TESTNET_CHAIN_ID:
        return _quote_routes_payload_for_robinhood_testnet(
            chain_id=chain_id,
            protocols=protocols,
            use_static_observation=use_static_observation,
            price_map=price_map,
        )

    observation = dex_market_observation_surface_payload(
        chain_id=chain_id,
        protocols=protocols,
        use_static_observation=use_static_observation,
    )
    frame_context = _route_framework_context_from_observation(
        observation,
        pool_payload,
        chain_id=chain_id,
        protocols=protocols,
    )
    overview = _quote_routes_overview_payload(
        observation=observation,
        pool_payload=pool_payload,
        frame_context=frame_context,
        settlement=settlement,
    )
    payload = build_quote_routes_payload_from_overview(
        overview,
        chain_id=chain_id,
        protocols=protocols,
        route_quote_analysis_fn=_route_quote_analysis,
        route_size_analysis_fn=_route_size_analysis,
        quote_probe_notional_usd=QUOTE_PROBE_NOTIONAL_USD,
        price_map=price_map,
    )
    payload["marketObservationSource"] = observation.get("marketObservationSource") or "computed"
    payload["marketObservationSourceStage"] = (
        observation.get("marketObservationSourceStage")
        or payload["marketObservationSource"]
    )
    _cache_surface_payload(
        "dex_quote_routes",
        payload,
        chain_id=chain_id,
        protocols=protocols,
        use_static_observation=use_static_observation,
    )
    return payload


def _quote_routes_payload_from_overview(
    overview: dict[str, Any],
    *,
    chain_id: int | None,
    protocols: list[str] | None,
    price_map: dict[str, float] | None = None,
) -> dict[str, Any]:
    return build_quote_routes_payload_from_overview(
        overview,
        chain_id=chain_id,
        protocols=protocols,
        route_quote_analysis_fn=_route_quote_analysis,
        route_size_analysis_fn=_route_size_analysis,
        quote_probe_notional_usd=QUOTE_PROBE_NOTIONAL_USD,
        price_map=price_map,
    )


def _quote_routes_payload_for_robinhood_testnet(
    *,
    chain_id: int | None,
    protocols: list[str] | None,
    use_static_observation: bool,
    price_map: dict[str, float],
) -> dict[str, Any]:
    observation = _cached_framework_surface_payload(
        "dex_market_observation",
        chain_id=chain_id,
        protocols=protocols,
        use_static_observation=use_static_observation,
    )
    if observation is None and use_static_observation:
        observation = dex_market_observation_surface_payload(
            chain_id=chain_id,
            protocols=protocols,
            use_static_observation=True,
        )
    if observation is not None:
        pool_payload = _candidate_payload(chain_id, protocols)
        frame_context = _route_framework_context_from_observation(
            observation,
            pool_payload,
            chain_id=chain_id,
            protocols=protocols,
        )
        overview = _quote_routes_overview_payload(
            observation=observation,
            pool_payload=pool_payload,
            frame_context=frame_context,
            settlement=pool_payload.get("dexSettlement") or {},
        )
    else:
        overview = _cached_surface_payload(
            "dex_overview",
            chain_id=chain_id,
            protocols=protocols,
            use_static_observation=use_static_observation,
        )
        if overview is None:
            overview = dex_overview_payload(
                chain_id=chain_id,
                protocols=protocols,
                use_static_observation=use_static_observation,
            )
            _cache_surface_payload(
                "dex_overview",
                overview,
                chain_id=chain_id,
                protocols=protocols,
                use_static_observation=use_static_observation,
            )
    payload = _quote_routes_payload_from_overview(
        overview,
        chain_id=chain_id,
        protocols=protocols,
        price_map=price_map,
    )
    source_observation = observation if observation is not None else overview
    payload["marketObservationSource"] = source_observation.get("marketObservationSource") or "computed"
    payload["marketObservationSourceStage"] = (
        source_observation.get("marketObservationSourceStage")
        or payload["marketObservationSource"]
    )
    observed_anchor_symbols = list(observation.get("observationAnchorSymbols") or []) if observation is not None else []
    if observed_anchor_symbols:
        payload_summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
        payload_summary["observationAnchorSymbols"] = observed_anchor_symbols
        payload_summary.setdefault("anchorBorrowSymbols", observed_anchor_symbols)
        payload_summary.setdefault("closedLoopAnchorSymbols", observed_anchor_symbols)
        payload["summary"] = payload_summary
    _cache_surface_payload(
        "dex_quote_routes",
        payload,
        chain_id=chain_id,
        protocols=protocols,
        use_static_observation=use_static_observation,
    )
    return payload


def _quote_routes_overview_payload(
    *,
    observation: dict[str, Any],
    pool_payload: dict[str, Any],
    frame_context: dict[str, Any],
    settlement: dict[str, Any],
) -> dict[str, Any]:
    return {
        "arbitragePaths": frame_context["overview_paths"],
        "summary": {
            "probeNotionalUsd": QUOTE_PROBE_NOTIONAL_USD,
            "anchorBorrowSymbols": frame_context["anchor_symbols"],
            "closedLoopAnchorSymbols": frame_context["closed_loop_anchor_symbols"],
            "closedLoopAnchorCount": len(frame_context["closed_loop_anchor_symbols"]),
            "observationAnchorSymbols": frame_context["observation_anchor_symbols"],
            "observationAnchorCount": len(frame_context["observation_anchor_symbols"]),
            "pathSource": frame_context["path_source"],
        },
        "marketObservation": {
            "summary": observation.get("summary") or {},
            "chainSpecificObservationPairs": observation.get("chainSpecificObservationPairs") or [],
        },
        "routeFrameworkSource": frame_context["path_source"],
        "marketObservationSource": observation.get("marketObservationSource") or "computed",
        "marketObservationSourceStage": observation.get("marketObservationSourceStage")
        or observation.get("marketObservationSource")
        or "computed",
        "dexSettlement": settlement,
        "poolStaticTable": pool_payload.get("poolStaticTable") or {},
    }


def dex_quote_request_payload(
    *,
    chain_id: int | None = None,
    protocols: list[str] | None = None,
    pair: str | None = None,
    venue: str | None = None,
    amount: str | None = None,
    use_static_observation: bool = False,
) -> dict[str, Any]:
    routes = dex_quote_routes_payload(
        chain_id=chain_id,
        protocols=protocols,
        use_static_observation=use_static_observation,
    )
    return build_dex_quote_request_payload(
        quote_routes_payload=routes,
        pair=pair,
        venue=venue,
        amount=amount,
    )


def dex_submission_review_payload(
    *,
    attempts: list[dict[str, Any]] | None = None,
    groups: dict[str, Any] | None = None,
    source: str | None = None,
) -> dict[str, Any]:
    return build_dex_submission_review_payload(
        attempts=attempts,
        groups=groups,
        source=source,
    )


__all__ = [
    "dex_market_observation_payload",
    "dex_overview_payload",
    "dex_quote_request_payload",
    "dex_quote_routes_payload",
    "dex_submission_review_payload",
    "robinhood_testnet_v4_bridge_pairs_for_anchor_groups",
    "robinhood_testnet_v4_fund_anchor_pair_observation",
]
