from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_FLOOR
from pathlib import Path
from typing import Any


AVALANCHE_CHAIN_ID = 43114
COW_AVALANCHE_QUOTE_API = "https://api.cow.fi/avalanche/api/v1/quote"
COW_AVALANCHE_COINGECKO_TOKEN_LIST = (
    "https://raw.githubusercontent.com/cowprotocol/token-lists/main/src/public/CoinGecko.43114.json"
)
DEFAULT_OWNER = "0x0000000000000000000000000000000000000001"


@dataclass(frozen=True)
class CowToken:
    symbol: str
    address: str
    decimals: int
    source: str


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


def load_cow_token_list(url: str = COW_AVALANCHE_COINGECKO_TOKEN_LIST) -> list[CowToken]:
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/json", "User-Agent": "src_bot_cow_route_optimizer/1.0"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.loads(response.read().decode("utf-8"))
    tokens = []
    for item in payload.get("tokens") or []:
        if int(item.get("chainId") or 0) != AVALANCHE_CHAIN_ID:
            continue
        token = _token_from_mapping(item, "cow_token_list")
        if token is not None:
            tokens.append(token)
    return tokens


def build_token_registry(
    *,
    aave_cache_path: Path | None = None,
    include_cow_token_list: bool = True,
) -> dict[str, CowToken]:
    tokens: list[CowToken] = []
    if include_cow_token_list:
        tokens.extend(load_cow_token_list())
    if aave_cache_path is not None:
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
    owner: str = DEFAULT_OWNER,
    quote_api: str = COW_AVALANCHE_QUOTE_API,
    price_quality: str = "fast",
    valid_for: int = 180,
) -> dict[str, Any]:
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
        quote_api,
        data=json.dumps(body).encode("utf-8"),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "src_bot_cow_route_optimizer/1.0",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(detail) from exc


def evaluate_cow_route(
    route: dict[str, Any],
    *,
    registry: dict[str, CowToken],
    default_amount: str | int | float | Decimal,
    owner: str = DEFAULT_OWNER,
    quote_api: str = COW_AVALANCHE_QUOTE_API,
    price_quality: str = "fast",
    valid_for: int = 180,
) -> dict[str, Any]:
    path_symbols = parse_route_path(route.get("path"))
    tokens = [resolve_token(part, registry) for part in path_symbols]
    amount = route.get("amount", default_amount)
    current_units = to_units(amount, tokens[0].decimals)
    hops = []

    for index, (sell_token, buy_token) in enumerate(zip(tokens, tokens[1:]), start=1):
        try:
            payload = post_cow_quote(
                sell_token=sell_token,
                buy_token=buy_token,
                sell_amount_units=current_units,
                owner=owner,
                quote_api=quote_api,
                price_quality=price_quality,
                valid_for=valid_for,
            )
            quote = payload.get("quote") or {}
            buy_amount = str(quote.get("buyAmount") or "0")
            hops.append(
                {
                    "hop": index,
                    "sell_symbol": sell_token.symbol,
                    "buy_symbol": buy_token.symbol,
                    "sell_token": sell_token.address,
                    "buy_token": buy_token.address,
                    "sell_amount_units": str(quote.get("sellAmount") or current_units),
                    "buy_amount_units": buy_amount,
                    "fee_amount_units": str(quote.get("feeAmount") or "0"),
                    "protocol_fee_bps": payload.get("protocolFeeBps"),
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
        "hops": hops,
    }


def rank_cow_routes(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    def score(item: dict[str, Any]) -> Decimal:
        if not item.get("viable"):
            return Decimal("-1")
        try:
            return Decimal(str(item.get("final_amount_units") or "0"))
        except InvalidOperation:
            return Decimal("-1")

    return sorted(results, key=score, reverse=True)
