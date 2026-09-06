"""BNB UI-signal observer using only static high-quality pools and read-only quotes."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from web3 import Web3

from dex.intent_trade.chains.bnb import config as bnb
from dex.intent_trade.chains.bnb.intent import load_static_table
from dex.intent_trade.chains.bnb.quote import quote_route


BINANCE_TO_BNB_SYMBOL = {
    "BNB": "WBNB",
    "BTC": "BTCB",
    "BTCB": "BTCB",
    "ETH": "ETH",
    "CAKE": "CAKE",
    "USDT": "USDT",
    "USDC": "USDC",
    "BUSD": "BUSD",
    "DAI": "DAI",
}
_QUOTE_SUFFIXES = ("USDT", "USDC", "FDUSD", "BUSD", "TUSD")
_STABLE_BORROW_SYMBOLS = ("USDT", "USDC")


def canonical_bnb_symbol(value: Any) -> str | None:
    """Map an exact Binance base/pair symbol to one configured BNB token."""
    text = str(value or "").strip().upper()
    if not text:
        return None
    for suffix in _QUOTE_SUFFIXES:
        if text.endswith(suffix) and len(text) > len(suffix):
            text = text[: -len(suffix)]
            break
    mapped = BINANCE_TO_BNB_SYMBOL.get(text, text)
    return mapped if mapped in bnb.TOKENS else None


def _best_edges() -> dict[tuple[str, str], dict[str, Any]]:
    table = load_static_table()
    best: dict[tuple[str, str], dict[str, Any]] = {}
    for pool in table.get("pools") or []:
        if not isinstance(pool, dict) or str(pool.get("status") or "") != "recommended":
            continue
        pair = str(pool.get("symbolPair") or "")
        symbols = [part.strip().upper() for part in pair.split(":") if part.strip()]
        if len(symbols) != 2:
            continue
        key = tuple(sorted(symbols))
        current = best.get(key)
        if current is None or float(pool.get("tvlUsd") or 0) > float(current.get("tvlUsd") or 0):
            best[key] = pool
    return best


def high_quality_routes_for_signal(x_symbol: Any, y_symbol: Any) -> list[dict[str, Any]]:
    """Build ordered three-hop routes for one UI top/bottom signal."""
    x = canonical_bnb_symbol(x_symbol)
    y = canonical_bnb_symbol(y_symbol)
    if not x or not y or x == y:
        return []
    edges = _best_edges()
    routes = []
    seen: set[tuple[str, str, str, str]] = set()
    for borrow in _STABLE_BORROW_SYMBOLS:
        for first, second in ((x, y), (y, x)):
            path = (borrow, first, second, borrow)
            if path in seen or borrow in {first, second}:
                continue
            pair_keys = [tuple(sorted((path[index], path[index + 1]))) for index in range(3)]
            if not all(key in edges for key in pair_keys):
                continue
            selected = [edges[key] for key in pair_keys]
            seen.add(path)
            routes.append(
                {
                    "routeSymbols": list(path),
                    "path": " -> ".join(path),
                    "minTvlUsd": min(float(edge.get("tvlUsd") or 0) for edge in selected),
                    "pools": [
                        {
                            "pool": edge.get("pool"),
                            "symbolPair": edge.get("symbolPair"),
                            "protocol": edge.get("protocol"),
                            "fee": edge.get("fee"),
                            "tvlUsd": edge.get("tvlUsd"),
                        }
                        for edge in selected
                    ],
                }
            )
    routes.sort(key=lambda item: -float(item["minTvlUsd"]))
    return routes


def _decimal_text(value: Any) -> str | None:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return format(number.normalize(), "f") if number.is_finite() else None


def _return_ratio(quote: dict[str, Any]) -> Decimal | None:
    try:
        initial = Decimal(str(quote.get("initialAmount")))
        final = Decimal(str(quote.get("finalAmount")))
    except (InvalidOperation, TypeError, ValueError):
        return None
    if initial <= 0 or not initial.is_finite() or not final.is_finite():
        return None
    return final / initial


def quote_ui_signal_routes(
    w3: Web3,
    market_state: dict[str, Any],
    *,
    amount: str | int | float = "1000",
    min_return_ratio: str | int | float = "0.95",
) -> dict[str, Any]:
    """Quote every static BNB route that can be assembled from UI signal pairs."""
    min_ratio = Decimal(str(min_return_ratio))
    observations = []
    for pair in market_state.get("pairs") or []:
        if not isinstance(pair, dict):
            continue
        x = pair.get("x_base_symbol")
        y = pair.get("y_base_symbol")
        routes = high_quality_routes_for_signal(x, y)
        signal = {
            "pair": pair.get("pair"),
            "xBinanceSymbol": pair.get("x_symbol"),
            "yBinanceSymbol": pair.get("y_symbol"),
            "xBaseSymbol": x,
            "yBaseSymbol": y,
            "xChangePercent": pair.get("x_change_percent"),
            "yChangePercent": pair.get("y_change_percent"),
            "windowSpreadPercent": pair.get("window_spread_percent"),
            "routes": routes,
        }
        for route in routes:
            path = tuple(route["routeSymbols"])
            try:
                quote = quote_route(w3, path, amount=amount)
                ratio = _return_ratio(quote)
                quality_ok = ratio is not None and ratio >= min_ratio
                observations.append(
                    {
                        "signal": signal,
                        "routeAssembly": route,
                        "quoteOk": True,
                        "routeQualityOk": quality_ok,
                        "qualityRejected": not quality_ok,
                        "qualityRejectReason": None if quality_ok else "exact_return_below_min_return_ratio",
                        "minReturnRatio": format(min_ratio.normalize(), "f"),
                        "returnRatio": format(ratio.normalize(), "f") if ratio is not None else None,
                        "quote": quote,
                        "grossProfit": _decimal_text(quote.get("grossProfit")),
                        "profitableBeforeCosts": quality_ok and bool(quote.get("profitableBeforeFlashloanAndGas")),
                        "executionEnabled": False,
                        "broadcastEnabled": False,
                    }
                )
            except Exception as exc:
                observations.append(
                    {
                        "signal": signal,
                        "routeAssembly": route,
                        "quoteOk": False,
                        "routeQualityOk": False,
                        "qualityRejected": True,
                        "qualityRejectReason": "quote_failed",
                        "minReturnRatio": format(min_ratio.normalize(), "f"),
                        "returnRatio": None,
                        "error": str(exc),
                        "grossProfit": None,
                        "profitableBeforeCosts": False,
                        "executionEnabled": False,
                        "broadcastEnabled": False,
                    }
                )
    assembled_signals = {
        (item["signal"].get("xBinanceSymbol"), item["signal"].get("yBinanceSymbol"))
        for item in observations
    }
    successful = [item for item in observations if item.get("quoteOk") and item.get("routeQualityOk")]
    profitable = [item for item in successful if item.get("profitableBeforeCosts")]
    quality_rejected = [item for item in observations if item.get("qualityRejected")]
    return {
        "network": bnb.NETWORK,
        "chainId": bnb.CHAIN_ID,
        "mode": "shadow_quote_only",
        "executionEnabled": False,
        "broadcastEnabled": False,
        "uiSignalCount": len(market_state.get("pairs") or []),
        "assembledSignalCount": len(assembled_signals),
        "assembledRouteCount": len(observations),
        "successfulQuoteCount": len(successful),
        "qualityRejectedRouteCount": len(quality_rejected),
        "profitableBeforeCostsCount": len(profitable),
        "observations": observations,
    }
