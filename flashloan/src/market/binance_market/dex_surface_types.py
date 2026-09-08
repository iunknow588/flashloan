"""Small, side-effect-free helpers for DEX surface payloads."""

from __future__ import annotations

from decimal import Decimal
from typing import Any


STABLE_ANCHOR_SYMBOLS = {
    "AUSD", "BUSD", "DAI", "DAI.E", "EURC", "FDUSD", "FRAX", "FRAX.E",
    "GHO", "PYUSD", "RLUSD", "TUSD", "USDC", "USDC.E", "USDD", "USDE",
    "USDG", "USDF", "USD1", "USDT", "USDT.E",
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
    return [
        symbol
        for symbol in (_norm_symbol(pair.get("tokenXSymbol")), _norm_symbol(pair.get("tokenYSymbol")))
        if symbol
    ]


def _pool_symbols(pool: dict[str, Any]) -> list[str]:
    return [
        symbol
        for symbol in (_norm_symbol(pool.get("token0Symbol")), _norm_symbol(pool.get("token1Symbol")))
        if symbol
    ]


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


def _pool_protocol(pool: dict[str, Any]) -> str:
    return str(pool.get("protocol") or pool.get("dex") or "").strip().lower()


def _pool_side_index(
    pool: dict[str, Any],
    symbol: str | None = None,
    address: str | None = None,
) -> int | None:
    symbols = _pool_symbols(pool)
    tokens = _pool_tokens(pool)
    for index, token in enumerate(tokens):
        if symbol and _norm_symbol(token.get("symbol")) == _norm_symbol(symbol):
            return index
        token_address = _token_address(token.get("address"))
        if address and token_address and token_address == _token_address(address):
            return index
    if symbol and len(symbols) >= 2:
        if _norm_symbol(symbol) == _norm_symbol(symbols[0]):
            return 0
        if _norm_symbol(symbol) == _norm_symbol(symbols[1]):
            return 1
    return None


__all__ = [
    "STABLE_ANCHOR_SYMBOLS", "PRICE_ALIAS_GROUPS", "INVALID_ROUTE_SYMBOL_PREFIXES",
    "_norm_symbol", "_text", "_token_address", "_pool_address", "_int", "_number",
    "_optional_number", "_raw_from_human", "_human_from_raw", "_is_stable_anchor",
    "_split_symbol_components", "_route_symbol_allowed", "_pair_symbols", "_pool_symbols",
    "_pool_tokens",
]
