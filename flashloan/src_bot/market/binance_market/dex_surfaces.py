from __future__ import annotations

from decimal import Decimal
from importlib import import_module
from typing import Any
from urllib.parse import quote

from core.chain_data_registry import chain_data_by_chain_id
from core.flash_loan_pool_discovery import query_flash_loan_pool_discovery
from core.market_observation_store import (
    load_market_observation_static,
    observation_payload_from_static,
    market_observation_static_table,
    static_store_metadata as market_observation_static_store_metadata,
)
from market.binance_market.pool_candidates import dex_pool_candidates_payload
from strategy.limits import DEFAULT_ARBITRAGE_MIN_PAPER_PROFIT_USD

QUOTE_PROBE_NOTIONAL_USD = 1000.0
DEFAULT_FLASH_LOAN_FEE_BPS = 5.0
DEFAULT_GAS_RESERVE_USD = 1.0
DEFAULT_AMM_FEE_BPS = 30.0
ROBINHOOD_TESTNET_CHAIN_ID = 46630


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


def _token_address(value: object) -> str:
    text = str(value or "").strip()
    return text.lower() if text.startswith("0x") and len(text) == 42 else ""


def _pool_address(value: object) -> str:
    if isinstance(value, dict):
        return str(value.get("pool") or value.get("pool_address") or value.get("address") or "").strip()
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


def _pool_token_decimals_trusted(pool: dict[str, Any], index: int) -> bool:
    symbols = _pool_symbols(pool)
    symbol = symbols[index] if index < len(symbols) else ""
    if symbol in {"ETH", "WETH", "WETH.E"}:
        return True
    keys = (
        f"token{index}Decimals",
        f"token{index}_decimals",
    )
    pool_key = pool.get("poolKey") if isinstance(pool.get("poolKey"), dict) else {}
    return any(pool.get(key) not in (None, "") or pool_key.get(key) not in (None, "") for key in keys)


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


def _v4_spot_rate(pool: dict[str, Any], from_symbol: str, to_symbol: str) -> float | None:
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
    try:
        decimals0 = int(token0.get("decimals") or pool.get("token0Decimals") or 18)
        decimals1 = int(token1.get("decimals") or pool.get("token1Decimals") or 18)
    except (TypeError, ValueError):
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
) -> dict[str, Any] | None:
    fee_bps = _fee_bps(pool)
    fee_factor = max(0.0, 1.0 - fee_bps / 10_000.0)
    rate = _market_reference_rate(pool, from_symbol, to_symbol)
    if rate is None and _pool_protocol(pool) == "uniswap_v4":
        rate = _v4_spot_rate(pool, from_symbol, to_symbol)
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
        token_decimals_trusted = _pool_token_decimals_trusted(pool, in_index) and _pool_token_decimals_trusted(pool, out_index)
    if output_amount is not None and output_price and output_price > 0:
        output_usd = output_amount * output_price
    elif rate is not None:
        output_usd = input_usd * fee_factor
        reference_usd_mode = "fee_only_without_trusted_output_usd_price"
    else:
        return None
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
        "priceImpactBps": None,
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
) -> dict[str, Any] | None:
    in_index = _pool_side_index(pool, from_symbol, from_address)
    out_index = _pool_side_index(pool, to_symbol, to_address)
    if in_index is None or out_index is None or in_index == out_index:
        return None
    protocol = _pool_protocol(pool)
    if protocol == "uniswap_v4":
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
    notional_usd: float = QUOTE_PROBE_NOTIONAL_USD,
    token_basis_input_amount: float | None = None,
) -> dict[str, Any]:
    overview_route = route.get("overviewRoute") or {}
    route_tokens = [token for token in route.get("routeTokenPath") or overview_route.get("pathTokens") or [] if isinstance(token, dict)]
    route_symbols = _route_symbols_from_tokens(route_tokens) if route_tokens else list(overview_route.get("pathSymbols") or route.get("routeSymbols") or [])
    route_addresses = _route_addresses_from_tokens(route_tokens) if route_tokens else []
    pools = _route_pools(route)
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
        if trust in {"reference_only", "sdk_adapter_required"} and not has_reference_output:
            warnings.append(f"hop_{index}_requires_live_quoter")
            warnings.append(f"route_truncated_at_hop_{index}_requires_live_quoter")
            break
        if trust in {"reference_only", "sdk_adapter_required"}:
            warnings.append(f"hop_{index}_reference_quote_requires_live_quoter")
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
    complete = len(steps) == max(0, len(route_symbols) - 1) and all(step.get("status") == "quoted" for step in steps)
    requires_live_quoter = any(step.get("executionTrust") == "reference_only" for step in steps)
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
    borrow_asset_is_stable = bool(input_symbol and _is_stable_anchor(input_symbol))
    borrow_asset_class = "stable" if borrow_asset_is_stable else "non_stable"
    token_basis_trusted = bool(token_basis_complete and not assumed_token_decimals)
    token_basis_reference_profitable = bool(reference_net_profit_amount is not None and reference_net_profit_amount > 0)
    token_basis_profitable = bool(token_basis_trusted and token_basis_reference_profitable)
    non_stable_chain_submission_candidate = bool(
        token_basis_complete
        and not borrow_asset_is_stable
        and token_basis_reference_profitable
    )
    executable_ready = bool(complete and not requires_live_quoter)
    final_output_usd = reference_final_output_usd if executable_ready else None
    gross_profit_usd = reference_gross_profit_usd if executable_ready else None
    net_profit_usd = (
        gross_profit_usd - flash_loan_fee_usd - DEFAULT_GAS_RESERVE_USD if gross_profit_usd is not None else None
    )
    net_profit_bps = net_profit_usd / notional_usd * 10_000.0 if net_profit_usd is not None and notional_usd > 0 else None
    min_profit_usd = float(DEFAULT_ARBITRAGE_MIN_PAPER_PROFIT_USD)
    if not complete:
        action = "等待真实报价"
        level = "blocked"
        reasons = ["部分跳数缺少可计算池子或深度，不能进入自动交易"]
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
        if untrusted_usd_reference:
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
    return {
        "method": "static_amm_depth_probe",
        "notionalUsd": notional_usd,
        "inputSymbol": input_symbol,
        "outputSymbol": output_symbol,
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
        "referenceGasExcluded": token_basis_complete,
        "referenceReturnBasis": "borrow_token" if token_basis_complete else ("usd" if reference_net_profit_usd is not None else None),
        "referenceReturnTrusted": token_basis_trusted or reference_net_profit_usd is not None,
        "borrowAssetClass": borrow_asset_class,
        "borrowAssetIsStable": borrow_asset_is_stable,
        "tokenBasisReferenceProfitable": token_basis_reference_profitable,
        "tokenBasisProfitable": token_basis_profitable,
        "chainSubmissionCandidate": bool(
            non_stable_chain_submission_candidate
            or (executable_ready and net_profit_usd is not None and net_profit_usd >= min_profit_usd)
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
            (not untrusted_usd_reference and net_profit_usd is not None and net_profit_usd > 0)
            or token_basis_profitable
        ),
        "executableCandidate": bool(executable_ready and net_profit_usd is not None and net_profit_usd >= min_profit_usd),
        "sdkAdapterRequiredStepCount": sum(1 for step in steps if step.get("executionTrust") == "sdk_adapter_required"),
        "trustedUsdReference": not untrusted_usd_reference,
        "tokenDecimalsTrusted": not assumed_token_decimals,
        "recommendation": {"level": level, "action": action, "reasons": reasons},
        "warnings": warnings,
    }


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
    function = _chain_observation_function(chain_id, "bridge_pairs_for_anchor_groups")
    if function is None:
        return [], {"enabled": False, "status": "chain_observation_extension_missing"}
    return function(anchor_groups, chain_id=chain_id, protocols=protocols)


def _chain_fund_anchor_pair_observation(
    *,
    chain_id: int | None,
    protocols: list[str] | None,
) -> dict[str, Any]:
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


def _market_observation_for_surface(
    *,
    chain_id: int | None,
    protocols: list[str] | None,
    use_static_observation: bool = False,
) -> dict[str, Any]:
    if use_static_observation:
        snapshot = load_market_observation_static(chain_id=chain_id, protocols=protocols)
        if snapshot.get("exists"):
            return observation_payload_from_static(snapshot)
    payload = dex_market_observation_payload(chain_id=chain_id, protocols=protocols)
    payload.setdefault("marketObservationSource", "computed")
    return payload


def _overview_profit_from_analysis(analysis: dict[str, Any]) -> dict[str, Any]:
    observed_gross = analysis.get("grossProfitUsd")
    if observed_gross is None:
        observed_gross = analysis.get("referenceGrossProfitUsd")
    observed_net = analysis.get("netProfitUsd")
    if observed_net is None:
        observed_net = analysis.get("referenceNetProfitUsd")
    observed_bps = analysis.get("netProfitBps")
    if observed_bps is None:
        observed_bps = analysis.get("referenceNetProfitBps")
    recommendation = analysis.get("recommendation") if isinstance(analysis.get("recommendation"), dict) else {}
    return {
        "method": analysis.get("method"),
        "notionalUsd": analysis.get("notionalUsd"),
        "complete": bool(analysis.get("complete")),
        "requiresLiveQuoter": bool(analysis.get("requiresLiveQuoter")),
        "adapterRequired": bool(analysis.get("adapterRequired")),
        "stepCount": analysis.get("stepCount"),
        "finalOutputUsd": analysis.get("finalOutputUsd"),
        "referenceFinalOutputUsd": analysis.get("referenceFinalOutputUsd"),
        "grossProfitUsd": analysis.get("grossProfitUsd"),
        "referenceGrossProfitUsd": analysis.get("referenceGrossProfitUsd"),
        "observedGrossProfitUsd": observed_gross,
        "flashLoanFeeUsd": analysis.get("flashLoanFeeUsd"),
        "gasReserveUsd": analysis.get("gasReserveUsd"),
        "offchainMinProfitUsd": analysis.get("offchainMinProfitUsd") or analysis.get("minProfitUsd"),
        "submissionMinProfitUsd": analysis.get("submissionMinProfitUsd"),
        "submissionNetProfitUsd": analysis.get("submissionNetProfitUsd"),
        "netProfitUsd": analysis.get("netProfitUsd"),
        "referenceNetProfitUsd": analysis.get("referenceNetProfitUsd"),
        "observedNetProfitUsd": observed_net,
        "observedNetProfitBps": observed_bps,
        "referenceInputAmount": analysis.get("referenceInputAmount"),
        "referenceInputSymbol": analysis.get("referenceInputSymbol"),
        "referenceFinalOutputAmount": analysis.get("referenceFinalOutputAmount"),
        "referenceFinalOutputSymbol": analysis.get("referenceFinalOutputSymbol"),
        "referenceGrossProfitAmount": analysis.get("referenceGrossProfitAmount"),
        "referenceFlashLoanFeeAmount": analysis.get("referenceFlashLoanFeeAmount"),
        "referenceNetProfitAmount": analysis.get("referenceNetProfitAmount"),
        "referenceGrossReturnBps": analysis.get("referenceGrossReturnBps"),
        "referenceNetReturnBps": analysis.get("referenceNetReturnBps"),
        "referenceGasExcluded": analysis.get("referenceGasExcluded"),
        "referenceReturnBasis": analysis.get("referenceReturnBasis"),
        "referenceReturnTrusted": analysis.get("referenceReturnTrusted"),
        "borrowAssetClass": analysis.get("borrowAssetClass"),
        "borrowAssetIsStable": analysis.get("borrowAssetIsStable"),
        "tokenBasisReferenceProfitable": bool(analysis.get("tokenBasisReferenceProfitable")),
        "tokenBasisProfitable": bool(analysis.get("tokenBasisProfitable")),
        "chainSubmissionCandidate": bool(analysis.get("chainSubmissionCandidate")),
        "chainSubmissionPolicy": analysis.get("chainSubmissionPolicy"),
        "chainSubmissionMinProfitAmount": analysis.get("chainSubmissionMinProfitAmount"),
        "chainSubmissionMinProfitSymbol": analysis.get("chainSubmissionMinProfitSymbol"),
        "profitable": bool(analysis.get("profitable") or (observed_net is not None and _number(observed_net) > 0)),
        "executableCandidate": bool(analysis.get("executableCandidate")),
        "trustedUsdReference": analysis.get("trustedUsdReference"),
        "tokenDecimalsTrusted": analysis.get("tokenDecimalsTrusted"),
        "recommendation": recommendation,
        "recommendationLevel": recommendation.get("level"),
        "recommendationAction": recommendation.get("action"),
        "warnings": list(analysis.get("warnings") or []),
    }


def _route_with_overview_profit(route: dict[str, Any], *, price_map: dict[str, float]) -> dict[str, Any]:
    analysis = _route_quote_analysis(route, price_map=price_map)
    updated = dict(route)
    updated["overviewProfit"] = _overview_profit_from_analysis(analysis)
    return updated


def _route_size_analysis(route: dict[str, Any], *, price_map: dict[str, float]) -> dict[str, Any]:
    overview_route = route.get("overviewRoute") or {}
    route_tokens = [token for token in route.get("routeTokenPath") or overview_route.get("pathTokens") or [] if isinstance(token, dict)]
    route_symbols = _route_symbols_from_tokens(route_tokens) if route_tokens else list(overview_route.get("pathSymbols") or route.get("routeSymbols") or [])
    input_symbol = route_symbols[0] if route_symbols else None
    use_token_basis_ladder = bool(input_symbol and len(route_symbols) > 1 and route_symbols[-1] == input_symbol and not price_map.get(input_symbol))
    notionals = [] if use_token_basis_ladder else [100.0, 500.0, 1000.0, 2000.0, 5000.0, 10000.0]
    token_amounts = [0.01, 0.1, 1.0, 10.0, 100.0] if use_token_basis_ladder else []
    rows: list[dict[str, Any]] = []
    for notional in notionals:
        analysis = _route_quote_analysis(route, price_map=price_map, notional_usd=notional)
        summary = _overview_profit_from_analysis(analysis)
        rows.append(
            {
                "borrowBasis": "usd",
                "notionalUsd": notional,
                "borrowAmount": notional,
                "borrowSymbol": "USD",
                "complete": summary["complete"],
                "requiresLiveQuoter": summary["requiresLiveQuoter"],
                "adapterRequired": summary["adapterRequired"],
                "observedGrossProfitUsd": summary["observedGrossProfitUsd"],
                "flashLoanFeeUsd": summary["flashLoanFeeUsd"],
                "gasReserveUsd": summary["gasReserveUsd"],
                "observedNetProfitUsd": summary["observedNetProfitUsd"],
                "observedNetProfitBps": summary["observedNetProfitBps"],
                "referenceInputAmount": summary["referenceInputAmount"],
                "referenceInputSymbol": summary["referenceInputSymbol"],
                "referenceFinalOutputAmount": summary["referenceFinalOutputAmount"],
                "referenceFinalOutputSymbol": summary["referenceFinalOutputSymbol"],
                "referenceNetProfitAmount": summary["referenceNetProfitAmount"],
                "referenceNetReturnBps": summary["referenceNetReturnBps"],
                "referenceReturnBasis": summary["referenceReturnBasis"],
                "referenceReturnTrusted": summary["referenceReturnTrusted"],
                "tokenBasisReferenceProfitable": summary["tokenBasisReferenceProfitable"],
                "tokenBasisProfitable": summary["tokenBasisProfitable"],
                "chainSubmissionCandidate": summary["chainSubmissionCandidate"],
                "chainSubmissionPolicy": summary["chainSubmissionPolicy"],
                "chainSubmissionMinProfitAmount": summary["chainSubmissionMinProfitAmount"],
                "chainSubmissionMinProfitSymbol": summary["chainSubmissionMinProfitSymbol"],
                "executableCandidate": summary["executableCandidate"],
                "recommendationLevel": summary["recommendationLevel"],
            }
        )
    for token_amount in token_amounts:
        analysis = _route_quote_analysis(
            route,
            price_map=price_map,
            notional_usd=QUOTE_PROBE_NOTIONAL_USD,
            token_basis_input_amount=token_amount,
        )
        summary = _overview_profit_from_analysis(analysis)
        rows.append(
            {
                "borrowBasis": "borrow_token",
                "notionalUsd": None,
                "borrowAmount": token_amount,
                "borrowSymbol": input_symbol,
                "complete": summary["complete"],
                "requiresLiveQuoter": summary["requiresLiveQuoter"],
                "adapterRequired": summary["adapterRequired"],
                "observedGrossProfitUsd": summary["observedGrossProfitUsd"],
                "flashLoanFeeUsd": summary["flashLoanFeeUsd"],
                "gasReserveUsd": summary["gasReserveUsd"],
                "observedNetProfitUsd": summary["observedNetProfitUsd"],
                "observedNetProfitBps": summary["observedNetProfitBps"],
                "referenceInputAmount": summary["referenceInputAmount"],
                "referenceInputSymbol": summary["referenceInputSymbol"],
                "referenceFinalOutputAmount": summary["referenceFinalOutputAmount"],
                "referenceFinalOutputSymbol": summary["referenceFinalOutputSymbol"],
                "referenceNetProfitAmount": summary["referenceNetProfitAmount"],
                "referenceNetReturnBps": summary["referenceNetReturnBps"],
                "referenceReturnBasis": summary["referenceReturnBasis"],
                "referenceReturnTrusted": summary["referenceReturnTrusted"],
                "tokenBasisReferenceProfitable": summary["tokenBasisReferenceProfitable"],
                "tokenBasisProfitable": summary["tokenBasisProfitable"],
                "chainSubmissionCandidate": summary["chainSubmissionCandidate"],
                "chainSubmissionPolicy": summary["chainSubmissionPolicy"],
                "chainSubmissionMinProfitAmount": summary["chainSubmissionMinProfitAmount"],
                "chainSubmissionMinProfitSymbol": summary["chainSubmissionMinProfitSymbol"],
                "executableCandidate": summary["executableCandidate"],
                "recommendationLevel": summary["recommendationLevel"],
            }
        )
    observed = [row for row in rows if row.get("observedNetProfitUsd") is not None]
    executable = [row for row in rows if row.get("executableCandidate") and row.get("observedNetProfitUsd") is not None]
    positive = [row for row in observed if _number(row.get("observedNetProfitUsd")) > 0]
    token_basis_rows = [row for row in rows if row.get("referenceNetReturnBps") is not None]
    best_observed = max(observed, key=lambda row: _number(row.get("observedNetProfitUsd")), default=None)
    best_executable = max(executable, key=lambda row: _number(row.get("observedNetProfitUsd")), default=None)
    best_token_basis = max(token_basis_rows, key=lambda row: _number(row.get("referenceNetReturnBps")), default=None)
    return {
        "method": "static_probe_notional_ladder",
        "borrowBasis": "borrow_token" if use_token_basis_ladder else "usd",
        "notionalUsdSteps": notionals,
        "tokenAmountSteps": token_amounts,
        "rows": rows,
        "bestObservedNotionalUsd": best_observed.get("notionalUsd") if best_observed else None,
        "bestObservedNetProfitUsd": best_observed.get("observedNetProfitUsd") if best_observed else None,
        "bestExecutableNotionalUsd": best_executable.get("notionalUsd") if best_executable else None,
        "bestExecutableNetProfitUsd": best_executable.get("observedNetProfitUsd") if best_executable else None,
        "bestTokenBasisNotionalUsd": best_token_basis.get("notionalUsd") if best_token_basis else None,
        "bestTokenBasisInputSymbol": best_token_basis.get("referenceInputSymbol") if best_token_basis else None,
        "bestTokenBasisNetProfitAmount": best_token_basis.get("referenceNetProfitAmount") if best_token_basis else None,
        "bestTokenBasisNetReturnBps": best_token_basis.get("referenceNetReturnBps") if best_token_basis else None,
        "positiveRangeUsd": {
            "min": min((_number(row.get("notionalUsd")) for row in positive), default=None),
            "max": max((_number(row.get("notionalUsd")) for row in positive), default=None),
        },
    }


def dex_overview_payload(
    *,
    chain_id: int | None = None,
    protocols: list[str] | None = None,
    use_static_observation: bool = False,
) -> dict[str, Any]:
    observation = _market_observation_for_surface(
        chain_id=chain_id,
        protocols=protocols,
        use_static_observation=use_static_observation,
    )
    ready_paths = _path_candidates_from_market_observation(observation, chain_id=chain_id)
    used_market_observation = bool(ready_paths)
    pool_payload = _candidate_payload(chain_id, protocols)
    price_map = _price_map_from_pairs(pool_payload.get("pairs") or [])
    watch_pairs = list(observation.get("chainSpecificObservationPairs") or [])
    anchor_symbols = sorted(
        {
            str((path.get("overviewRoute") or {}).get("anchorBorrowSymbol") or "").strip().upper()
            for path in ready_paths
            if str((path.get("overviewRoute") or {}).get("anchorBorrowSymbol") or "").strip()
        }
    )
    if not ready_paths:
        ready_paths, watch_pairs, anchor_symbols = _overview_path_candidates(pool_payload, chain_id, protocols=protocols)
        used_market_observation = False
    ready_paths = [_route_with_overview_profit(route, price_map=price_map) for route in ready_paths]
    closed_loop_anchor_symbols = anchor_symbols if used_market_observation else list(observation.get("observationAnchorSymbols") or anchor_symbols)
    observation_anchor_symbols = list(observation.get("observationAnchorSymbols") or [])
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
    return {
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
        "marketObservationSource": observation.get("marketObservationSource") or "computed",
        "marketObservation": {
            "summary": observation.get("summary") or {},
            "chainSpecificObservationPairs": observation.get("chainSpecificObservationPairs") or [],
            "chainSpecificObservationPools": observation.get("chainSpecificObservationPools") or [],
        },
        "poolStaticTable": pool_payload.get("poolStaticTable") or {},
        "flashLoanLiquidity": pool_payload.get("flashLoanLiquidity") or {},
        "dexSettlement": pool_payload.get("dexSettlement") or {},
        "arbitragePaths": ready_paths,
        "watchPairs": watch_pairs,
    }


def dex_market_observation_payload(*, chain_id: int | None = None, protocols: list[str] | None = None) -> dict[str, Any]:
    pool_payload = _candidate_payload(chain_id, protocols)
    pairs = _candidate_pairs(pool_payload.get("pairs") or [])
    borrow_symbols = _borrowable_symbols(pool_payload, chain_id, protocols=protocols)
    observation_anchors = _observation_anchor_symbols(
        pool_payload,
        chain_id=chain_id,
        protocols=protocols,
        borrow_symbols=borrow_symbols,
    )
    anchor_groups = list(pool_payload.get("anchorGroups") or [])
    bridge_pairs, bridge_index_lookup = _v4_index_bridge_pairs_for_anchor_groups(
        anchor_groups,
        chain_id=chain_id,
        protocols=protocols,
    )
    fund_anchor_pair_observation = _chain_fund_anchor_pair_observation(chain_id=chain_id, protocols=protocols)
    chain_observation_sequences = [
        sequence
        for sequence in fund_anchor_pair_observation.get("observationSequences") or []
        if isinstance(sequence, dict)
    ]
    observation_sequences = _anchor_observation_sequences(anchor_groups, pairs + bridge_pairs) + chain_observation_sequences
    token_by_key, symbol_to_keys, neighbor_keys, pair_by_edge = _token_graph(pairs)
    direct: list[dict[str, Any]] = []
    unmatched: list[dict[str, Any]] = []
    by_symbol: dict[str, list[dict[str, Any]]] = {symbol: [] for symbol in observation_anchors}

    def pair_row(pair: dict[str, Any], *, limit: int = 5) -> dict[str, Any]:
        pools = _pools_for_pair(pair)
        return {
            "symbolPair": pair.get("symbolPair"),
            "tradeSymbols": _pair_symbols(pair),
            "poolCount": len(pools),
            "classification": pair.get("classification") or {},
            "pools": pools[:limit],
        }

    for pair in pairs:
        symbols = _pair_symbols(pair)
        pools = _pools_for_pair(pair)
        matched = [symbol for symbol in observation_anchors if symbol in symbols]
        if matched:
            row = pair_row(pair)
            row["matchedObservationSymbols"] = matched
            row["matchedBorrowSymbols"] = [symbol for symbol in matched if symbol in borrow_symbols] or matched
            direct.append(row)
            for symbol in matched:
                by_symbol.setdefault(symbol, []).append(row)
        elif pools:
            unmatched.append(pair_row(pair, limit=3))

    route_seen: set[tuple[str, ...]] = set()
    closed_routes: list[dict[str, Any]] = []
    direct_closed_routes: list[dict[str, Any]] = []
    bridge_routes: list[dict[str, Any]] = []
    used_pair_symbols: set[str] = {str(row.get("symbolPair") or "") for row in direct}
    price_map = _price_map_from_pairs(pairs)

    for anchor in observation_anchors:
        anchor_keys = sorted(symbol_to_keys.get(anchor) or [])
        for anchor_key in anchor_keys:
            for target_key in sorted(neighbor_keys.get(anchor_key) or []):
                if target_key == anchor_key:
                    continue
                first_pair = pair_by_edge.get(frozenset((anchor_key, target_key)))
                if first_pair is None:
                    continue
                target_token = _pair_token_entry(first_pair, target_key)
                anchor_token = _pair_token_entry(first_pair, anchor_key)
                if target_token is None or anchor_token is None:
                    continue
                target_symbol = _norm_symbol(target_token.get("symbol"))
                if not target_symbol or not _route_symbol_allowed(target_symbol):
                    continue
                first_pair_pools = _best_pool_rows(_pools_for_pair(first_pair))
                if len(first_pair_pools) < 2:
                    continue
                for first_pool_index, first_pool in enumerate(first_pair_pools):
                    for second_pool in first_pair_pools[first_pool_index + 1 :]:
                        first_pool_id = _pool_address(first_pool)
                        second_pool_id = _pool_address(second_pool)
                        if not first_pool_id or not second_pool_id or first_pool_id == second_pool_id:
                            continue
                        if not _reentry_route_has_reference_profit(
                            anchor_token=anchor_token,
                            counter_token=target_token,
                            first_pool=first_pool,
                            second_pool=second_pool,
                            price_map=price_map,
                        ):
                            continue
                        route_key = ("direct", anchor_key, target_key, first_pool_id.lower(), second_pool_id.lower())
                        if route_key in route_seen:
                            continue
                        route_seen.add(route_key)
                        used_pair_symbols.add(str(first_pair.get("symbolPair") or ""))
                        first_route_row = pair_row(first_pair, limit=3)
                        first_route_row["pool"] = first_pool_id
                        first_route_row["pools"] = [first_pool]
                        second_route_row = pair_row(first_pair, limit=3)
                        second_route_row["pool"] = second_pool_id
                        second_route_row["pools"] = [second_pool]
                        route = {
                            "kind": "direct_closed_pair",
                            "borrowSymbol": anchor,
                            "anchorSymbol": anchor,
                            "targetSymbol": target_symbol,
                            "bridgeSymbol": target_symbol,
                            "pathSymbols": [anchor, target_symbol, anchor],
                            "pathText": f"{anchor} -> {target_symbol} -> {anchor}",
                            "closedLoop": True,
                            "firstLegCount": 1,
                            "secondLegCount": 1,
                            "firstLegPair": first_route_row,
                            "secondLegPairs": [second_route_row],
                            "routePairs": [first_route_row, second_route_row],
                        }
                        direct_closed_routes.append(route)
                        closed_routes.append(route)

        for anchor_key in anchor_keys:
            # General case: X -> Y -> Z -> X, where Y and Z are distinct and the
            # Y-Z bridge pool exists.
            for mid_key in sorted(neighbor_keys.get(anchor_key) or []):
                if mid_key == anchor_key:
                    continue
                first_pair = pair_by_edge.get(frozenset((anchor_key, mid_key)))
                if first_pair is None:
                    continue
                first_row = pair_row(first_pair, limit=3)
                mid_token = _pair_token_entry(first_pair, mid_key)
                anchor_token = _pair_token_entry(first_pair, anchor_key)
                if mid_token is None or anchor_token is None:
                    continue
                mid_symbol = _norm_symbol(mid_token.get("symbol"))
                if not mid_symbol or not _route_symbol_allowed(mid_symbol):
                    continue
                for target_key in sorted(neighbor_keys.get(mid_key) or []):
                    if target_key in {anchor_key, mid_key}:
                        continue
                    second_pair = pair_by_edge.get(frozenset((mid_key, target_key)))
                    third_pair = pair_by_edge.get(frozenset((target_key, anchor_key)))
                    if second_pair is None or third_pair is None:
                        continue
                    second_row = pair_row(second_pair, limit=3)
                    third_row = pair_row(third_pair, limit=3)
                    target_token = _pair_token_entry(second_pair, target_key)
                    anchor_token_zx = _pair_token_entry(third_pair, anchor_key)
                    if target_token is None or anchor_token_zx is None:
                        continue
                    target_symbol = _norm_symbol(target_token.get("symbol"))
                    if not target_symbol or not _route_symbol_allowed(target_symbol):
                        continue
                    route_key = (
                        "triangle",
                        anchor_key,
                        mid_key,
                        target_key,
                        str(first_pair.get("symbolPair") or ""),
                        str(second_pair.get("symbolPair") or ""),
                        str(third_pair.get("symbolPair") or ""),
                    )
                    if route_key in route_seen:
                        continue
                    route_seen.add(route_key)
                    used_pair_symbols.add(str(first_pair.get("symbolPair") or ""))
                    used_pair_symbols.add(str(second_pair.get("symbolPair") or ""))
                    used_pair_symbols.add(str(third_pair.get("symbolPair") or ""))
                    route = {
                        "kind": "bridge_closed_loop",
                        "borrowSymbol": anchor,
                        "anchorSymbol": anchor,
                        "bridgeSymbol": mid_symbol,
                        "targetSymbol": target_symbol,
                        "pathSymbols": [anchor, mid_symbol, target_symbol, anchor],
                        "pathText": f"{anchor} -> {mid_symbol} -> {target_symbol} -> {anchor}",
                        "closedLoop": True,
                        "firstLegCount": 1,
                        "secondLegCount": 2,
                        "firstLegPair": first_row,
                        "secondLegPairs": [second_row, third_row],
                        "routePairs": [first_row, second_row, third_row],
                    }
                    bridge_routes.append(route)
                    closed_routes.append(route)

    for sequence in observation_sequences:
        route = _closed_route_from_sequence(sequence)
        route_pool_ids = tuple(
            _pool_address(step).lower()
            for step in route.get("routePools") or []
            if isinstance(step, dict) and _pool_address(step)
        )
        if not route_pool_ids:
            continue
        route_key = ("yc_zc_bridge", str(route.get("pathText") or ""), *route_pool_ids)
        if route_key in route_seen:
            continue
        route_seen.add(route_key)
        for step in route.get("routePools") or []:
            if isinstance(step, dict):
                used_pair_symbols.add(str(step.get("pairSymbol") or step.get("symbolPair") or ""))
        bridge_routes.append(route)
        closed_routes.append(route)

    def route_sort_key(route: dict[str, Any]) -> tuple[float, int, str]:
        pools = [pool for pair in route.get("routePairs") or [] for pool in pair.get("pools") or []]
        if not pools:
            pools = [pool for pool in route.get("routePools") or [] if isinstance(pool, dict)]
        tvl = sum(_number((pool.get("balances") or {}).get("tvlUsd")) for pool in pools)
        return (tvl, len(pools), str(route.get("pathText") or ""))

    def route_identity(route: dict[str, Any]) -> tuple[str, str, tuple[str, ...]]:
        pools = [pool for pair in route.get("routePairs") or [] for pool in pair.get("pools") or []]
        if not pools:
            pools = [pool for pool in route.get("routePools") or [] if isinstance(pool, dict)]
        pool_ids = tuple(_pool_address(pool).lower() for pool in pools if _pool_address(pool))
        return (str(route.get("kind") or ""), str(route.get("pathText") or ""), pool_ids)

    def dedupe_routes(routes: list[dict[str, Any]]) -> list[dict[str, Any]]:
        deduped: list[dict[str, Any]] = []
        seen_routes: set[tuple[str, str, tuple[str, ...]]] = set()
        for route in routes:
            identity = route_identity(route)
            if identity in seen_routes:
                continue
            seen_routes.add(identity)
            deduped.append(route)
        return deduped

    direct_closed_routes = sorted(dedupe_routes(direct_closed_routes), key=route_sort_key, reverse=True)
    bridge_routes = sorted(dedupe_routes(bridge_routes), key=route_sort_key, reverse=True)
    closed_routes = sorted(dedupe_routes(closed_routes), key=route_sort_key, reverse=True)[:200]
    bridges = bridge_routes[:120]
    candidate_observation_groups = _candidate_observation_groups(anchor_groups, observation_sequences, closed_routes)
    candidate_observation_groups.sort(
        key=lambda group: (
            -(_int(group.get("observationSequenceCount")) + _int(group.get("closedLoopRouteCount"))),
            str(group.get("anchorSymbol") or ""),
        ),
    )
    unmatched = [row for row in unmatched if str(row.get("symbolPair") or "") not in used_pair_symbols]

    sequence_anchor_symbols = sorted({str(sequence.get("anchorSymbol") or "").strip().upper() for sequence in observation_sequences if str(sequence.get("anchorSymbol") or "").strip()})
    sequence_pool_ids = sorted(
        {
            _pool_address(step)
            for sequence in observation_sequences
            for step in sequence.get("routePools") or []
            if isinstance(step, dict) and _pool_address(step)
        }
    )
    group_pool_ids = sorted(
        {
            _pool_address(step)
            for group in candidate_observation_groups
            for sequence in group.get("observationSequences") or []
            for step in sequence.get("routePools") or []
            if isinstance(step, dict) and _pool_address(step)
        }
    )

    return {
        "mode": "dex_market_observation",
        "chain_id": chain_id,
        "selected_protocols": protocols or [],
        "marketObservationStaticTable": market_observation_static_table(
            {
                **market_observation_static_store_metadata(
                    chain_id=chain_id,
                    protocols=protocols,
                ),
                "surface": "dex_market_observation",
                "source": "computed",
            }
        ),
        "borrowableSymbols": borrow_symbols,
        "observationAnchorSymbols": observation_anchors,
        "anchorGroups": anchor_groups,
        "candidateObservationGroups": candidate_observation_groups,
        "observationSequences": observation_sequences,
        "directPools": direct,
        "directClosedLoopRoutes": direct_closed_routes[:120],
        "closedLoopRoutes": closed_routes,
        "indirectRoutes": bridges,
        "unmatchedPools": unmatched[:20],
        "bridgePoolIndexLookup": bridge_index_lookup,
        "chainSpecificObservationPairs": fund_anchor_pair_observation.get("associatedObservationPairs") or [],
        "chainSpecificObservationPools": fund_anchor_pair_observation.get("associatedObservationPools") or [],
        "chainSpecificObservationTokens": fund_anchor_pair_observation.get("associatedObservationTokens") or [],
        "chainSpecificObservation": {
            "robinhoodTestnetV4FundAnchorPairObservation": {
                key: value
                for key, value in fund_anchor_pair_observation.items()
                if key
                not in {
                    "observationSequences",
                    "associatedObservationPairs",
                    "associatedObservationPools",
                    "associatedObservationTokens",
                }
            },
            "robinhoodTestnetV4NonEthPairFirst": {
                key: value
                for key, value in fund_anchor_pair_observation.items()
                if key
                not in {
                    "observationSequences",
                    "associatedObservationPairs",
                    "associatedObservationPools",
                    "associatedObservationTokens",
                }
            },
        },
        "summary": {
            "borrowableTokenCount": len(borrow_symbols),
            "observationAnchorCount": len(observation_anchors),
            "candidateObservationGroupCount": len(candidate_observation_groups),
            "bridgePoolIndexMatchedPairCount": len(bridge_pairs),
            "bridgePoolIndexMatchedPoolCount": _int(bridge_index_lookup.get("matchedPoolCount")),
            "chainSpecificObservationSequenceCount": len(chain_observation_sequences),
            "fundAnchorPairObservationRouteCount": _int(fund_anchor_pair_observation.get("routeCount")),
            "fundAnchorPairObservationAssociatedPairCount": _int(
                fund_anchor_pair_observation.get("associatedObservationPairCount")
                or fund_anchor_pair_observation.get("associatedFundAnchorPairCount")
            ),
            "fundAnchorPairObservationAssociatedObservationPoolCount": _int(fund_anchor_pair_observation.get("associatedObservationPoolCount")),
            "fundAnchorPairObservationAssociatedObservationTokenCount": _int(fund_anchor_pair_observation.get("associatedObservationTokenCount")),
            "fundAnchorPairObservationPoolCount": _int(fund_anchor_pair_observation.get("fundAnchorCandidatePairCount")),
            "nonEthPairFirstRouteCount": _int(fund_anchor_pair_observation.get("routeCount")),
            "nonEthPairFirstAssociatedPairCount": _int(
                fund_anchor_pair_observation.get("associatedObservationPairCount")
                or fund_anchor_pair_observation.get("associatedNonEthPairCount")
            ),
            "nonEthPairFirstAssociatedObservationPoolCount": _int(fund_anchor_pair_observation.get("associatedObservationPoolCount")),
            "nonEthPairFirstAssociatedObservationTokenCount": _int(fund_anchor_pair_observation.get("associatedObservationTokenCount")),
            "nonEthPairFirstPoolCount": _int(fund_anchor_pair_observation.get("nonEthPairCount")),
            "candidateObservationSequenceCount": len(observation_sequences),
            "candidateObservationDirectCount": len(direct_closed_routes),
            "candidateObservationBridgeCount": len(bridge_routes),
            "candidateObservationPoolCount": len(group_pool_ids),
            "observationSequenceCount": len(observation_sequences),
            "observationSequenceAnchorCount": len(sequence_anchor_symbols),
            "observationSequencePoolCount": len(sequence_pool_ids),
            "directPairCount": len(direct),
            "directClosedLoopCount": len(direct_closed_routes),
            "indirectRouteCount": len(bridges),
            "closedLoopRouteCount": len(closed_routes),
            "unmatchedPairCount": len(unmatched),
            "observableTokenCount": len(_unique_symbols([pool for pair in pairs for pool in pair.get("preferredPools") or []])),
        },
        "flashLoanLiquidity": pool_payload.get("flashLoanLiquidity") or {},
        "poolStaticTable": pool_payload.get("poolStaticTable") or {},
        "dexSettlement": pool_payload.get("dexSettlement") or {},
    }


def dex_quote_routes_payload(
    *,
    chain_id: int | None = None,
    protocols: list[str] | None = None,
    use_static_observation: bool = False,
) -> dict[str, Any]:
    pool_payload = _candidate_payload(chain_id, protocols)
    settlement = pool_payload.get("dexSettlement") or {}
    uniswapx = settlement.get("uniswapx") if isinstance(settlement.get("uniswapx"), dict) else {}
    observation = _market_observation_for_surface(
        chain_id=chain_id,
        protocols=protocols,
        use_static_observation=use_static_observation,
    )
    overview_paths = _path_candidates_from_market_observation(observation, chain_id=chain_id)
    path_source = "market_observation"
    if not overview_paths:
        overview_paths, _watch_pairs, anchor_symbols = _overview_path_candidates(pool_payload, chain_id, protocols=protocols)
        closed_loop_anchor_symbols = _observation_anchor_symbols(pool_payload, chain_id=chain_id, protocols=protocols)
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
    observation_anchor_symbols = list(observation.get("observationAnchorSymbols") or [])
    price_map = _price_map_from_pairs(pool_payload.get("pairs") or [])
    routes = []
    for pair in overview_paths:
        linkage = pair.get("flashLoanLinkage") or {}
        pools = _route_pools(pair)
        overview_route = pair.get("overviewRoute") or {}
        route_symbols = list(overview_route.get("pathSymbols") or linkage.get("route_symbols") or _pair_symbols(pair))
        route_tokens = list(overview_route.get("pathTokens") or pair.get("routeTokenPath") or [])
        route_addresses = list(overview_route.get("pathAddresses") or linkage.get("route_addresses") or pair.get("routeAddresses") or [])
        quote_analysis = _route_quote_analysis(pair, price_map=price_map)
        size_analysis = _route_size_analysis(pair, price_map=price_map)
        if quote_analysis.get("executableCandidate"):
            quote_status = "static_probe_ready_for_precheck"
        elif quote_analysis.get("chainSubmissionCandidate"):
            quote_status = "chain_profit_check_candidate"
        elif quote_analysis.get("requiresLiveQuoter"):
            quote_status = "requires_live_quoter"
        elif quote_analysis.get("complete"):
            quote_status = "static_probe_not_profitable"
        else:
            quote_status = "static_probe_incomplete"
        quote_requests = [
            {
                "venue": "amm_pool",
                "quoteMode": "pool_exact_input_probe",
                "status": quote_status,
                "quoteRequestUrl": f"/api/dex/quote-request?pair={quote(str(pair.get('symbolPair') or ''), safe='')}&venue=amm_pool",
                "pool": pool.get("pool"),
                "protocol": pool.get("dex") or pool.get("protocol"),
                "fee": pool.get("fee"),
                "routeSymbols": route_symbols,
                "routeAddresses": route_addresses,
                "routeTokenPath": route_tokens,
            }
            for pool in pools[:5]
        ]
        v4_pools = [pool for pool in pools if _pool_protocol(pool) == "uniswap_v4"]
        if v4_pools:
            v4_quote_mode = "uniswap_v4_sdk_exact_input_route" if len(route_symbols) > 2 else "uniswap_v4_sdk_exact_input_single"
            quote_requests.append(
                {
                    "venue": "uniswap_v4",
                    "quoteMode": v4_quote_mode,
                    "status": "sdk_adapter_required" if quote_analysis.get("requiresLiveQuoter") else "quoteable_after_static_probe",
                    "quoteRequestUrl": f"/api/dex/quote-request?pair={quote(str(pair.get('symbolPair') or ''), safe='')}&venue=uniswap_v4",
                    "routeSymbols": route_symbols,
                    "routeAddresses": route_addresses,
                    "routeTokenPath": route_tokens,
                    "chainId": chain_id,
                    "adapterHints": [
                        "pool_manager_state_required",
                        "hook_aware_quote_required",
                        "multi_hop_path_must_follow_market_observation_route",
                        "sdk_or_adapter_must_return_amount_out_raw",
                    ],
                    "pools": [pool.get("pool") for pool in v4_pools[:5]],
                }
            )
        if uniswapx.get("supported") and len(route_symbols) >= 2:
            quote_requests.append(
                {
                    "venue": "uniswapx",
                    "quoteMode": "intent_exact_input",
                    "status": "quoteable_after_static_probe",
                    "quoteRequestUrl": f"/api/dex/quote-request?pair={quote(str(pair.get('symbolPair') or ''), safe='')}&venue=uniswapx",
                    "routeSymbols": route_symbols,
                    "routeAddresses": route_addresses,
                    "routeTokenPath": route_tokens,
                    "chainId": chain_id,
                    "reactorFamily": uniswapx.get("reactor_family"),
                    "requires": ["sellToken", "buyToken", "amount", "userAddress"],
                }
            )
        routes.append(
            {
                "symbolPair": pair.get("symbolPair"),
                "overviewRoute": overview_route,
                "routeSymbols": route_symbols,
                "routeAddresses": route_addresses,
                "routeTokenPath": route_tokens,
                "borrowSymbols": linkage.get("borrow_symbols") or [],
                "strategy": linkage.get("strategy")
                or (
                    "quote_from_market_observation"
                    if path_source == "market_observation"
                    else "quote_candidate_from_symbol_anchor_path"
                    if chain_id == ROBINHOOD_TESTNET_CHAIN_ID
                    else "quote_candidate_from_borrow_anchor_path"
                ),
                "pathSource": path_source,
                "poolCount": len(pools),
                "quoteStatus": quote_status,
                "quoteAnalysis": quote_analysis,
                "sizeAnalysis": size_analysis,
                "quoteVenueCount": len(quote_requests),
                "quoteInputs": {
                    "candidatePools": pools[:5],
                    "routePools": pools,
                    "quoteRequests": quote_requests,
                    "conditions": [
                        "候选池必须保持可执行深度，不能只依赖静态池子列表。",
                        "路径来源必须与市场观察页面展示的闭环路径一致。",
                        "AMM 报价必须通过滑点、gas、闪电贷费用和最小利润阈值检查。",
                        "UniswapX 需要意图报价请求，并确认求解器或填单方可用后才能执行。",
                    ],
                },
                "settlementRails": pair.get("settlementRails") or [],
            }
        )
    executable_net_profits = [
        _number((route.get("quoteAnalysis") or {}).get("netProfitUsd"))
        for route in routes
        if (route.get("quoteAnalysis") or {}).get("executableCandidate")
    ]
    observed_net_profits = []
    for route in routes:
        analysis = route.get("quoteAnalysis") or {}
        observed_profit = analysis.get("netProfitUsd")
        if observed_profit is None:
            observed_profit = analysis.get("referenceNetProfitUsd")
        if observed_profit is not None:
            observed_net_profits.append(_number(observed_profit))
    best_size_observed = max(
        (
            route.get("sizeAnalysis") or {}
            for route in routes
            if (route.get("sizeAnalysis") or {}).get("bestObservedNetProfitUsd") is not None
        ),
        key=lambda row: _number(row.get("bestObservedNetProfitUsd")),
        default={},
    )
    best_size_executable = max(
        (
            route.get("sizeAnalysis") or {}
            for route in routes
            if (route.get("sizeAnalysis") or {}).get("bestExecutableNetProfitUsd") is not None
        ),
        key=lambda row: _number(row.get("bestExecutableNetProfitUsd")),
        default={},
    )
    best_size_token_basis = max(
        (
            route.get("sizeAnalysis") or {}
            for route in routes
            if (route.get("sizeAnalysis") or {}).get("bestTokenBasisNetReturnBps") is not None
        ),
        key=lambda row: _number(row.get("bestTokenBasisNetReturnBps")),
        default={},
    )
    return {
        "mode": "dex_quote_routes",
        "chain_id": chain_id,
        "selected_protocols": protocols or [],
        "summary": {
            "probeNotionalUsd": QUOTE_PROBE_NOTIONAL_USD,
            "routeCount": len(routes),
            "candidateRouteCount": len(routes),
            "anchorBorrowSymbols": anchor_symbols,
            "closedLoopAnchorSymbols": closed_loop_anchor_symbols,
            "closedLoopAnchorCount": len(closed_loop_anchor_symbols),
            "observationAnchorSymbols": observation_anchor_symbols,
            "observationAnchorCount": len(observation_anchor_symbols),
            "executableCandidateCount": sum(1 for route in routes if (route.get("quoteAnalysis") or {}).get("executableCandidate")),
            "profitableRouteCount": sum(1 for route in routes if (route.get("quoteAnalysis") or {}).get("profitable")),
            "chainSubmissionCandidateCount": sum(1 for route in routes if (route.get("quoteAnalysis") or {}).get("chainSubmissionCandidate")),
            "nonStableChainSubmissionCandidateCount": sum(
                1
                for route in routes
                if (route.get("quoteAnalysis") or {}).get("chainSubmissionCandidate")
                and not (route.get("quoteAnalysis") or {}).get("borrowAssetIsStable")
            ),
            "completeStaticQuoteCount": sum(1 for route in routes if (route.get("quoteAnalysis") or {}).get("complete")),
            "liveQuoterRequiredCount": sum(1 for route in routes if (route.get("quoteAnalysis") or {}).get("requiresLiveQuoter")),
            "sdkAdapterRequiredCount": sum(1 for route in routes if (route.get("quoteAnalysis") or {}).get("adapterRequired")),
            "bestNetProfitUsd": max(executable_net_profits) if executable_net_profits else None,
            "bestObservedNetProfitUsd": max(observed_net_profits) if observed_net_profits else None,
            "bestObservedBorrowNotionalUsd": best_size_observed.get("bestObservedNotionalUsd"),
            "bestObservedBorrowNetProfitUsd": best_size_observed.get("bestObservedNetProfitUsd"),
            "bestExecutableBorrowNotionalUsd": best_size_executable.get("bestExecutableNotionalUsd"),
            "bestExecutableBorrowNetProfitUsd": best_size_executable.get("bestExecutableNetProfitUsd"),
            "bestTokenBasisBorrowNotionalUsd": best_size_token_basis.get("bestTokenBasisNotionalUsd"),
            "bestTokenBasisInputSymbol": best_size_token_basis.get("bestTokenBasisInputSymbol"),
            "bestTokenBasisNetProfitAmount": best_size_token_basis.get("bestTokenBasisNetProfitAmount"),
            "bestTokenBasisNetReturnBps": best_size_token_basis.get("bestTokenBasisNetReturnBps"),
            "directAnchorRouteCount": sum(1 for route in routes if (route.get("overviewRoute") or {}).get("kind") == "direct_anchor_pair"),
            "bridgeRouteCount": sum(1 for route in routes if (route.get("overviewRoute") or {}).get("kind") == "bridge_pair"),
            "marketObservationRouteCount": len(observation.get("closedLoopRoutes") or []),
            "marketObservationSequenceCount": _int((observation.get("summary") or {}).get("observationSequenceCount")),
            "chainSpecificObservationPairCount": len(observation.get("chainSpecificObservationPairs") or []),
            "pathSource": path_source,
            "uniswapxQuoteableRouteCount": sum(
                1
                for route in routes
                for request in (route.get("quoteInputs") or {}).get("quoteRequests") or []
                if request.get("venue") == "uniswapx"
            ),
        },
        "routes": routes,
        "marketObservation": {
            "summary": observation.get("summary") or {},
            "chainSpecificObservationPairs": observation.get("chainSpecificObservationPairs") or [],
        },
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
    normalized_pair = str(pair or "").strip().upper()
    normalized_venue = str(venue or "amm_pool").strip().lower()
    routes = dex_quote_routes_payload(
        chain_id=chain_id,
        protocols=protocols,
        use_static_observation=use_static_observation,
    )
    matched = None
    for route in routes.get("routes") or []:
        if str(route.get("symbolPair") or "").strip().upper() == normalized_pair:
            matched = route
            break
    if matched is None:
        return {
            "ok": False,
            "mode": "dex_quote_request",
            "error": "candidate_pair_not_found",
            "pair": normalized_pair,
            "venue": normalized_venue,
            "availablePairs": [route.get("symbolPair") for route in routes.get("routes") or []],
        }
    requests = [
        request
        for request in (matched.get("quoteInputs") or {}).get("quoteRequests") or []
        if str(request.get("venue") or "").strip().lower() == normalized_venue
    ]
    return {
        "ok": bool(requests),
        "mode": "dex_quote_request",
        "pair": matched.get("symbolPair"),
        "venue": normalized_venue,
        "amount": amount,
        "routeSymbols": matched.get("routeSymbols") or [],
        "quoteAnalysis": matched.get("quoteAnalysis") or {},
        "quoteRequests": requests,
        "candidatePools": (matched.get("quoteInputs") or {}).get("candidatePools") or [],
        "status": "quote_request_ready" if requests else "venue_not_available_for_pair",
        "note": "This endpoint returns the exact quote request specification; live quote execution is handled by the configured venue adapter.",
    }


def dex_submission_review_payload(
    *,
    attempts: list[dict[str, Any]] | None = None,
    groups: dict[str, Any] | None = None,
    source: str | None = None,
) -> dict[str, Any]:
    rows = list(attempts or [])
    def row_status(row: dict[str, Any]) -> str:
        return str(row.get("review_category") or row.get("category") or row.get("status") or row.get("state") or "").lower()

    success = [row for row in rows if row_status(row).endswith("success")]
    failed = [row for row in rows if "fail" in row_status(row)]
    blocked = [row for row in rows if "not_executable" in row_status(row) or row_status(row) == "quote_required"]
    return {
        "mode": "dex_submission_review",
        "source": source or "execution_attempts",
        "summary": {
            "attemptCount": len(rows),
            "successCount": len(success),
            "failedCount": len(failed),
            "blockedCount": len(blocked),
        },
        "groups": groups or {},
        "attempts": rows[:100],
    }


__all__ = [
    "dex_market_observation_payload",
    "dex_overview_payload",
    "dex_quote_request_payload",
    "dex_quote_routes_payload",
    "dex_submission_review_payload",
]
