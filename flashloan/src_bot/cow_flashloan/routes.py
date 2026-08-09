from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation, ROUND_FLOOR
from pathlib import Path
from typing import Any


ETHEREUM_CHAIN_ID = 1
GNOSIS_CHAIN_ID = 100
ARBITRUM_ONE_CHAIN_ID = 42161
BASE_CHAIN_ID = 8453
POLYGON_CHAIN_ID = 137
AVALANCHE_CHAIN_ID = 43114
BNB_CHAIN_ID = 56
LINEA_CHAIN_ID = 59144
PLASMA_CHAIN_ID = 9745
INK_CHAIN_ID = 57073
SEPOLIA_CHAIN_ID = 11155111
DEFAULT_OWNER = "0x0000000000000000000000000000000000000001"
NETWORK_OWNER_ENV_NAMES = {
    "ethereum": ("COW_OWNER_ETHEREUM", "COW_OWNER_MAINNET"),
    "gnosis": ("COW_OWNER_GNOSIS", "COW_OWNER_XDAI"),
    "arbitrum_one": ("COW_OWNER_ARBITRUM_ONE", "COW_OWNER_ARBITRUM"),
    "base": ("COW_OWNER_BASE",),
    "polygon": ("COW_OWNER_POLYGON",),
    "avalanche": ("COW_OWNER_AVALANCHE",),
    "bnb": ("COW_OWNER_BNB", "COW_OWNER_BINANCE"),
    "linea": ("COW_OWNER_LINEA",),
    "plasma": ("COW_OWNER_PLASMA",),
    "ink": ("COW_OWNER_INK",),
    "sepolia": ("COW_OWNER_SEPOLIA",),
}


@dataclass(frozen=True)
class CowNetworkConfig:
    network: str
    chain_id: int
    quote_api: str
    token_list_url: str
    testnet: bool = False


@dataclass(frozen=True)
class CowAccountConfig:
    owner: str
    owner_source: str
    network: str


@dataclass(frozen=True)
class CowToken:
    symbol: str
    address: str
    decimals: int
    source: str


def _cow_quote_api(network_slug: str) -> str:
    return f"https://api.cow.fi/{network_slug}/api/v1/quote"


def _cow_coingecko_token_list(chain_id: int) -> str:
    return f"https://raw.githubusercontent.com/cowprotocol/token-lists/main/src/public/CoinGecko.{int(chain_id)}.json"


SUPPORTED_COW_NETWORKS = {
    "ethereum": CowNetworkConfig(
        network="ethereum",
        chain_id=ETHEREUM_CHAIN_ID,
        quote_api=_cow_quote_api("mainnet"),
        token_list_url=_cow_coingecko_token_list(ETHEREUM_CHAIN_ID),
    ),
    "gnosis": CowNetworkConfig(
        network="gnosis",
        chain_id=GNOSIS_CHAIN_ID,
        quote_api=_cow_quote_api("xdai"),
        token_list_url=_cow_coingecko_token_list(GNOSIS_CHAIN_ID),
    ),
    "arbitrum_one": CowNetworkConfig(
        network="arbitrum_one",
        chain_id=ARBITRUM_ONE_CHAIN_ID,
        quote_api=_cow_quote_api("arbitrum_one"),
        token_list_url=_cow_coingecko_token_list(ARBITRUM_ONE_CHAIN_ID),
    ),
    "base": CowNetworkConfig(
        network="base",
        chain_id=BASE_CHAIN_ID,
        quote_api=_cow_quote_api("base"),
        token_list_url=_cow_coingecko_token_list(BASE_CHAIN_ID),
    ),
    "polygon": CowNetworkConfig(
        network="polygon",
        chain_id=POLYGON_CHAIN_ID,
        quote_api=_cow_quote_api("polygon"),
        token_list_url=_cow_coingecko_token_list(POLYGON_CHAIN_ID),
    ),
    "avalanche": CowNetworkConfig(
        network="avalanche",
        chain_id=AVALANCHE_CHAIN_ID,
        quote_api=_cow_quote_api("avalanche"),
        token_list_url=_cow_coingecko_token_list(AVALANCHE_CHAIN_ID),
    ),
    "bnb": CowNetworkConfig(
        network="bnb",
        chain_id=BNB_CHAIN_ID,
        quote_api=_cow_quote_api("bnb"),
        token_list_url=_cow_coingecko_token_list(BNB_CHAIN_ID),
    ),
    "linea": CowNetworkConfig(
        network="linea",
        chain_id=LINEA_CHAIN_ID,
        quote_api=_cow_quote_api("linea"),
        token_list_url=_cow_coingecko_token_list(LINEA_CHAIN_ID),
    ),
    "plasma": CowNetworkConfig(
        network="plasma",
        chain_id=PLASMA_CHAIN_ID,
        quote_api=_cow_quote_api("plasma"),
        token_list_url=_cow_coingecko_token_list(PLASMA_CHAIN_ID),
    ),
    "ink": CowNetworkConfig(
        network="ink",
        chain_id=INK_CHAIN_ID,
        quote_api=_cow_quote_api("ink"),
        token_list_url=_cow_coingecko_token_list(INK_CHAIN_ID),
    ),
    "sepolia": CowNetworkConfig(
        network="sepolia",
        chain_id=SEPOLIA_CHAIN_ID,
        quote_api=_cow_quote_api("sepolia"),
        token_list_url="",
        testnet=True,
    ),
}
_COW_NETWORK_ALIASES = {
    "mainnet": "ethereum",
    "eth": "ethereum",
    "1": "ethereum",
    "xdai": "gnosis",
    "gno": "gnosis",
    "100": "gnosis",
    "arbitrum": "arbitrum_one",
    "arbitrum-one": "arbitrum_one",
    "arb": "arbitrum_one",
    "42161": "arbitrum_one",
    "8453": "base",
    "matic": "polygon",
    "polygon-pos": "polygon",
    "137": "polygon",
    "avax": "avalanche",
    "avalanche-c-chain": "avalanche",
    "avalanche-c": "avalanche",
    "43114": "avalanche",
    "binance": "bnb",
    "binance-smart-chain": "bnb",
    "bsc": "bnb",
    "56": "bnb",
    "59144": "linea",
    "9745": "plasma",
    "57073": "ink",
    "11155111": "sepolia",
}
_UNSUPPORTED_COW_TESTNETS = {
    "fuji": "Avalanche Fuji",
    "avalanche-fuji": "Avalanche Fuji",
    "43113": "Avalanche Fuji",
}


def _normalize_cow_network(value: str) -> str:
    key = value.strip().lower().replace("_", "-")
    if key in _UNSUPPORTED_COW_TESTNETS:
        raise ValueError(
            f"CoW Protocol testnet quotes only support Sepolia ({SEPOLIA_CHAIN_ID}); "
            f"{_UNSUPPORTED_COW_TESTNETS[key]} is not supported."
        )
    return _COW_NETWORK_ALIASES.get(key, key)


def cow_account_config(network: str | None = None) -> CowAccountConfig:
    selected = ""
    if network:
        try:
            selected = cow_network_config(network=network).network
        except Exception:
            selected = str(network).strip().lower()
    if selected:
        for env_name in NETWORK_OWNER_ENV_NAMES.get(selected, ()):
            value = os.getenv(env_name, "").strip()
            if value:
                return CowAccountConfig(owner=value, owner_source=env_name, network=selected)
    fallback = os.getenv("LIQUIDATION_EXECUTOR_OWNER_ADDRESS", "").strip()
    if fallback:
        return CowAccountConfig(owner=fallback, owner_source="LIQUIDATION_EXECUTOR_OWNER_ADDRESS", network=selected)
    return CowAccountConfig(owner=DEFAULT_OWNER, owner_source="DEFAULT_OWNER", network=selected)


def default_cow_owner(network: str | None = None) -> str:
    return cow_account_config(network).owner


def resolve_cow_owner(owner: str | None = None, *, network: str | None = None) -> str:
    text = str(owner or "").strip()
    return text or default_cow_owner(network)


def cow_network_config(
    network: str | None = None,
    chain_id: int | str | None = None,
    *,
    quote_api: str | None = None,
    token_list_url: str | None = None,
) -> CowNetworkConfig:
    selected = str(chain_id) if chain_id not in (None, "") else str(network or "avalanche")
    selected = _normalize_cow_network(selected)
    config = SUPPORTED_COW_NETWORKS.get(selected)
    if config is None:
        supported = ", ".join(sorted(SUPPORTED_COW_NETWORKS))
        raise ValueError(f"unsupported CoW network: {selected}; supported networks: {supported}")
    custom_quote_api = str(quote_api or "").strip()
    custom_token_list_url = str(token_list_url or "").strip()
    if custom_quote_api or custom_token_list_url:
        config = replace(
            config,
            quote_api=custom_quote_api or config.quote_api,
            token_list_url=custom_token_list_url or config.token_list_url,
        )
    return config


def normalize_address(value: str) -> str:
    text = str(value or "").strip()
    if not text.startswith("0x") or len(text) != 42:
        raise ValueError(f"invalid token address: {value}")
    return text.lower()


def is_address(value: Any) -> bool:
    text = str(value or "").strip()
    return text.startswith("0x") and len(text) == 42


def to_units(amount: str | int | float | Decimal, decimals: int) -> str:
    value = Decimal(str(amount))
    if value <= 0:
        raise ValueError("amount must be positive")
    units = value * (Decimal(10) ** int(decimals))
    return str(int(units.to_integral_value(rounding=ROUND_FLOOR)))


def from_units(amount: str | int, decimals: int) -> str:
    value = Decimal(str(amount)) / (Decimal(10) ** int(decimals))
    return format(value.normalize(), "f")


def exchange_rate(
    *,
    sell_amount_units: str | int,
    sell_decimals: int,
    buy_amount_units: str | int,
    buy_decimals: int,
) -> str | None:
    sell_amount = Decimal(str(sell_amount_units)) / (Decimal(10) ** int(sell_decimals))
    buy_amount = Decimal(str(buy_amount_units)) / (Decimal(10) ** int(buy_decimals))
    if sell_amount <= 0:
        return None
    return format((buy_amount / sell_amount).normalize(), "f")


def _token_from_mapping(item: dict[str, Any], source: str) -> CowToken | None:
    try:
        symbol = str(item.get("symbol") or item.get("token_symbol") or "").strip()
        address = normalize_address(str(item.get("address") or item.get("token_address") or ""))
        decimals = int(item.get("decimals"))
    except Exception:
        return None
    if not symbol:
        return None
    return CowToken(symbol=symbol, address=address, decimals=decimals, source=source)


def builtin_cow_tokens(config: CowNetworkConfig) -> list[CowToken]:
    if config.network != "sepolia":
        return []
    return [
        CowToken("WETH", "0xfff9976782d46cc05630d1f6ebab18b2324d6b14", 18, "cow_sepolia_builtin"),
        CowToken("USDC", "0xbe72e441bf55620febc26715db68d3494213d8cb", 18, "cow_sepolia_builtin"),
        CowToken("COW", "0x0625afb445c3b6b7b929342a04a22599fd5dbb59", 18, "cow_sepolia_builtin"),
    ]


def load_local_aave_tokens(cache_path: Path) -> list[CowToken]:
    if not cache_path.exists():
        return []
    payload = json.loads(cache_path.read_text(encoding="utf-8"))
    assets = payload.get("assets") or []
    return [
        token
        for item in assets
        for token in [_token_from_mapping(item, "aave_cache")]
        if token is not None
    ]


def load_cow_token_list(
    url: str | None = None,
    *,
    network: str | None = None,
    chain_id: int | str | None = None,
) -> list[CowToken]:
    config = cow_network_config(network=network, chain_id=chain_id)
    token_list_url = url or config.token_list_url
    if not token_list_url:
        return builtin_cow_tokens(config)
    request = urllib.request.Request(
        token_list_url,
        headers={"Accept": "application/json", "User-Agent": "src_bot_cow_route_optimizer/1.0"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.loads(response.read().decode("utf-8"))
    tokens = []
    for item in payload.get("tokens") or []:
        if int(item.get("chainId") or 0) != config.chain_id:
            continue
        token = _token_from_mapping(item, "cow_token_list")
        if token is not None:
            tokens.append(token)
    return tokens


def build_token_registry(
    *,
    aave_cache_path: Path | None = None,
    include_cow_token_list: bool = True,
    cow_network: str | None = None,
    cow_chain_id: int | str | None = None,
) -> dict[str, CowToken]:
    tokens: list[CowToken] = []
    network_config = cow_network_config(network=cow_network, chain_id=cow_chain_id)
    if include_cow_token_list:
        tokens.extend(load_cow_token_list(network=network_config.network, chain_id=network_config.chain_id))
    if aave_cache_path is not None and network_config.network == "avalanche":
        tokens.extend(load_local_aave_tokens(aave_cache_path))

    by_address: dict[str, CowToken] = {}
    for token in tokens:
        by_address.setdefault(token.address, token)

    symbol_counts: dict[str, int] = {}
    for token in by_address.values():
        symbol_counts[token.symbol.upper()] = symbol_counts.get(token.symbol.upper(), 0) + 1

    registry = dict(by_address)
    for token in by_address.values():
        key = token.symbol.upper()
        if symbol_counts.get(key) == 1:
            registry[key] = token
    return registry


def resolve_token(value: Any, registry: dict[str, CowToken]) -> CowToken:
    text = str(value or "").strip()
    key = text.lower() if is_address(text) else text.upper()
    token = registry.get(key)
    if token is None:
        raise ValueError(f"unknown or ambiguous token: {value}")
    return token


def parse_route_path(value: Any) -> list[str]:
    if isinstance(value, str):
        separators = ["->", ">", ","]
        text = value
        for separator in separators:
            text = text.replace(separator, ",")
        parts = [part.strip() for part in text.split(",") if part.strip()]
    elif isinstance(value, list):
        parts = [str(part).strip() for part in value if str(part).strip()]
    else:
        raise ValueError("route path must be a list or string")
    if len(parts) < 2:
        raise ValueError("route path must contain at least two tokens")
    return parts


def read_route_specs(path: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    defaults = payload if isinstance(payload, dict) else {}
    raw_routes = payload.get("routes") if isinstance(payload, dict) else payload
    if not isinstance(raw_routes, list):
        raise ValueError("routes file must contain a list or an object with routes")
    routes = []
    for index, item in enumerate(raw_routes, start=1):
        if isinstance(item, str) or isinstance(item, list):
            routes.append({"name": f"route_{index}", "path": item})
        elif isinstance(item, dict):
            routes.append({"name": item.get("name") or f"route_{index}", **item})
        else:
            raise ValueError(f"route {index} must be a string, list, or object")
    return routes, defaults


def post_cow_quote(
    *,
    sell_token: CowToken,
    buy_token: CowToken,
    sell_amount_units: str,
    owner: str | None = None,
    quote_api: str | None = None,
    cow_network: str | None = None,
    price_quality: str = "fast",
    valid_for: int = 180,
    timeout_seconds: int | float = 30,
) -> dict[str, Any]:
    owner = resolve_cow_owner(owner, network=cow_network)
    config = cow_network_config(network=cow_network, quote_api=quote_api)
    body = {
        "sellToken": sell_token.address,
        "buyToken": buy_token.address,
        "from": owner,
        "receiver": owner,
        "kind": "sell",
        "sellAmountBeforeFee": str(sell_amount_units),
        "priceQuality": price_quality,
        "validFor": int(valid_for),
    }
    request = urllib.request.Request(
        config.quote_api,
        data=json.dumps(body).encode("utf-8"),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "src_bot_cow_route_optimizer/1.0",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=max(1.0, float(timeout_seconds))) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(detail) from exc


def evaluate_cow_route(
    route: dict[str, Any],
    *,
    registry: dict[str, CowToken],
    default_amount: str | int | float | Decimal,
    owner: str | None = None,
    quote_api: str | None = None,
    cow_network: str | None = None,
    price_quality: str = "fast",
    valid_for: int = 180,
    quote_timeout_seconds: int | float = 30,
) -> dict[str, Any]:
    owner = resolve_cow_owner(owner, network=cow_network)
    amount = route.get("amount", default_amount)
    try:
        path_symbols = parse_route_path(route.get("path"))
    except Exception as exc:
        return {
            "name": str(route.get("name") or ""),
            "path": [],
            "input_amount": str(amount),
            "input_symbol": None,
            "final_symbol": None,
            "viable": False,
            "error": str(exc),
            "hops": [],
        }
    try:
        tokens = [resolve_token(part, registry) for part in path_symbols]
        current_units = to_units(amount, tokens[0].decimals)
    except Exception as exc:
        return {
            "name": str(route.get("name") or ""),
            "path": path_symbols,
            "input_amount": str(amount),
            "input_symbol": path_symbols[0] if path_symbols else None,
            "final_symbol": path_symbols[-1] if path_symbols else None,
            "viable": False,
            "error": str(exc),
            "hops": [],
        }
    hops = []

    for index, (sell_token, buy_token) in enumerate(zip(tokens, tokens[1:]), start=1):
        try:
            payload = post_cow_quote(
                sell_token=sell_token,
                buy_token=buy_token,
                sell_amount_units=current_units,
                owner=owner,
                quote_api=quote_api,
                cow_network=cow_network,
                price_quality=price_quality,
                valid_for=valid_for,
                timeout_seconds=quote_timeout_seconds,
            )
            quote = payload.get("quote") or {}
            buy_amount = str(quote.get("buyAmount") or "0")
            sell_amount = str(quote.get("sellAmount") or current_units)
            fee_amount = str(quote.get("feeAmount") or "0")
            hops.append(
                {
                    "hop": index,
                    "sell_symbol": sell_token.symbol,
                    "buy_symbol": buy_token.symbol,
                    "sell_token": sell_token.address,
                    "buy_token": buy_token.address,
                    "sell_amount_units": sell_amount,
                    "buy_amount_units": buy_amount,
                    "fee_amount_units": fee_amount,
                    "sell_amount": from_units(sell_amount, sell_token.decimals),
                    "buy_amount": from_units(buy_amount, buy_token.decimals),
                    "fee_amount": from_units(fee_amount, sell_token.decimals),
                    "exchange_rate": exchange_rate(
                        sell_amount_units=sell_amount,
                        sell_decimals=sell_token.decimals,
                        buy_amount_units=buy_amount,
                        buy_decimals=buy_token.decimals,
                    ),
                    "protocol_fee_bps": payload.get("protocolFeeBps"),
                    "cow_sdk_response": payload,
                    "cow_sdk_status": "quote_success",
                }
            )
            current_units = buy_amount
        except Exception as exc:
            return {
                "name": str(route.get("name") or ""),
                "path": [token.symbol for token in tokens],
                "input_amount": str(amount),
                "input_symbol": tokens[0].symbol,
                "final_symbol": tokens[-1].symbol,
                "viable": False,
                "error": str(exc),
                "cow_sdk_result": {
                    "status": "quote_failed",
                    "error": str(exc),
                    "successful_hop_count": len(hops),
                    "failed_hop": len(hops) + 1,
                    "controller": "cow_sdk",
                },
                "hops": hops,
            }

    try:
        final_amount = from_units(current_units, tokens[-1].decimals)
    except (InvalidOperation, ValueError):
        final_amount = "0"
    return {
        "name": str(route.get("name") or ""),
        "path": [token.symbol for token in tokens],
        "input_amount": str(amount),
        "input_symbol": tokens[0].symbol,
        "final_symbol": tokens[-1].symbol,
        "final_amount_units": str(current_units),
        "final_amount": final_amount,
        "viable": Decimal(str(current_units or "0")) > 0,
        "cow_sdk_result": {
            "status": "quote_success",
            "successful_hop_count": len(hops),
            "controller": "cow_sdk",
        },
        "hops": hops,
    }


def rank_cow_routes(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    def sort_key(item: dict[str, Any]) -> tuple[int, int, int]:
        # Route selection must not use a sequential quote as a profit estimate.
        viable_rank = 0 if item.get("viable") else 1
        try:
            pair_rank = int(item.get("pair_rank") or 10**9)
        except (TypeError, ValueError):
            pair_rank = 10**9
        priority_rank = 0 if item.get("priority_reason") == "buy_loser_then_gainer" else 1
        return viable_rank, pair_rank, priority_rank

    return sorted(results, key=sort_key)
