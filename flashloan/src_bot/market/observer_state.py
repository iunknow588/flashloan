import asyncio
import time
from collections import deque
from itertools import combinations
from typing import Deque, Iterable

from market.observer_common import now_iso, utc_from_ms
from strategy.limits import DEFAULT_BINANCE_RAW_SIDE_LIMIT


class PriceState:
    def __init__(self) -> None:
        self.binance: dict[str, dict] = {}
        self.binance_history: dict[str, Deque[tuple[int, float, str]]] = {}
        self.aave: dict[str, dict] = {}
        self.lock = asyncio.Lock()

    async def update_binance(self, symbol: str, price: float, event_ms: int, source: str) -> None:
        if float(price) <= 0:
            return
        async with self.lock:
            self.binance[symbol] = {
                "price": price,
                "event_ms": event_ms,
                "seen_at": now_iso(),
                "source": source,
            }
            history = self.binance_history.setdefault(symbol, deque())
            history.append((event_ms, price, source))
            while len(history) > 5000:
                history.popleft()

    async def update_aave(self, symbol: str, price: float, block: int) -> None:
        async with self.lock:
            self.aave[symbol] = {"price": price, "block": block, "seen_at": now_iso()}

    async def snapshot(self) -> dict:
        async with self.lock:
            return {"binance": dict(self.binance), "aave": dict(self.aave)}

    async def window_extremes(
        self,
        symbols: Iterable[str],
        window_seconds: float,
        limit: int = DEFAULT_BINANCE_RAW_SIDE_LIMIT,
        source: str | None = None,
        min_change_percent: float = 0.0,
    ) -> dict:
        symbol_list = list(dict.fromkeys(symbols))
        cutoff_ms = int(time.time() * 1000) - int(window_seconds * 1000)
        rows = []
        async with self.lock:
            for symbol in symbol_list:
                history = self.binance_history.get(symbol)
                if not history:
                    current = self.binance.get(symbol)
                    if current and (source is None or current.get("source") == source):
                        rows.append(
                            {
                                "symbol": symbol,
                                "change_percent": 0.0,
                                "start_price": float(current["price"]),
                                "end_price": float(current["price"]),
                                "current_price": float(current["price"]),
                                "start_ms": current.get("event_ms"),
                                "end_ms": current.get("event_ms"),
                                "start_source": current.get("source"),
                                "price_source": current.get("source"),
                                "window_ready": False,
                            }
                        )
                    else:
                        rows.append(
                            {
                                "symbol": symbol,
                                "change_percent": None,
                                "start_price": None,
                                "end_price": None,
                                "current_price": None,
                                "start_ms": None,
                                "end_ms": None,
                                "start_source": None,
                                "price_source": source or "waiting",
                                "window_ready": False,
                            }
                        )
                else:
                    while history and history[0][0] < cutoff_ms:
                        history.popleft()
                    source_history = [
                        (event_ms, price, item_source)
                        for event_ms, price, item_source in history
                        if source is None or item_source == source
                    ]
                    if len(source_history) < 2 or source_history[0][1] <= 0:
                        current = self.binance.get(symbol)
                        if current and (source is None or current.get("source") == source):
                            current_price = float(current["price"])
                            current_ms = current.get("event_ms")
                            current_source = current.get("source")
                        elif source_history:
                            current_ms, current_price, current_source = source_history[-1]
                        else:
                            current_ms, current_price, current_source = None, None, source or "waiting"
                        rows.append(
                            {
                                "symbol": symbol,
                                "change_percent": 0.0 if current_price is not None else None,
                                "start_price": current_price,
                                "end_price": current_price,
                                "current_price": current_price,
                                "start_ms": current_ms,
                                "end_ms": current_ms,
                                "start_source": current_source,
                                "price_source": current_source,
                                "window_ready": False,
                            }
                        )
                    else:
                        start_ms, start_price, start_source = source_history[0]
                        end_ms, end_price, end_source = source_history[-1]
                        rows.append(
                            {
                                "symbol": symbol,
                                "change_percent": (end_price - start_price) / start_price * 100,
                                "start_price": start_price,
                                "end_price": end_price,
                                "current_price": end_price,
                                "start_ms": start_ms,
                                "end_ms": end_ms,
                                "start_source": start_source,
                                "price_source": end_source,
                                "window_ready": True,
                            }
                        )
        threshold = max(0.0, float(min_change_percent))
        gainers = [
            row
            for row in rows
            if row.get("window_ready")
            and float(row.get("change_percent") or 0.0) > 0
            and float(row.get("change_percent") or 0.0) >= threshold
        ]
        losers = [
            row
            for row in rows
            if row.get("window_ready")
            and float(row.get("change_percent") or 0.0) < 0
            and abs(float(row.get("change_percent") or 0.0)) >= threshold
        ]
        top, bottom = [], []
        for row in gainers:
            insert_extreme(top, row, limit, reverse=True)
        for row in losers:
            insert_extreme(bottom, row, limit, reverse=False)
        priced_rows = [row for row in rows if row.get("current_price") is not None]
        basket = sorted(rows, key=lambda row: float(row.get("change_percent") or 0.0), reverse=True)
        return {
            "observed_at": now_iso(),
            "window_seconds": window_seconds,
            "sample_count": len(priced_rows),
            "active_sample_count": len(gainers) + len(losers),
            "gainer_count": len(gainers),
            "loser_count": len(losers),
            "observation_universe_size": len(symbol_list),
            "market_divergence_index": (len(gainers) * len(losers) / len(symbol_list)) if symbol_list else 0.0,
            "price_source": source or "mixed",
            "min_change_percent": threshold,
            "top": top,
            "bottom": bottom,
            "basket": basket,
        }

    async def binance_price_history_rows(
        self,
        symbols: Iterable[str],
        source: str | None = None,
    ) -> list[dict]:
        observed_at = now_iso()
        rows = []
        async with self.lock:
            for symbol in symbols:
                item = self.binance.get(symbol)
                if not item or (source is not None and item.get("source") != source):
                    continue
                rows.append(
                    {
                        "observed_at": observed_at,
                        "symbol": symbol,
                        "price": item["price"],
                        "event_time": utc_from_ms(item["event_ms"]),
                        "source": item.get("source", "unknown"),
                    }
                )
        return rows

    async def candidate_and_pair_price_rows(
        self,
        extremes: dict,
        side_limit: int,
    ) -> tuple[list[dict], list[dict]]:
        observed_at = now_iso()
        usdc_usdt_price = 1.0
        async with self.lock:
            usdc_item = self.binance.get("USDCUSDT")
            if usdc_item and usdc_item.get("price", 0) > 0:
                usdc_usdt_price = float(usdc_item["price"])
            candidates = []
            seen: set[str] = set()
            for side, items in (("top", extremes.get("top") or []), ("bottom", extremes.get("bottom") or [])):
                for position, item in enumerate(items[:side_limit], start=1):
                    symbol = str(item.get("symbol") or "").upper()
                    if not symbol or symbol in seen:
                        continue
                    current = self.binance.get(symbol)
                    if not current:
                        continue
                    source_price = float(current["price"])
                    if source_price <= 0 or usdc_usdt_price <= 0:
                        continue
                    price_usdc = source_price / usdc_usdt_price
                    event_time = utc_from_ms(int(current["event_ms"]))
                    row = {
                        "observed_at": observed_at,
                        "symbol": symbol,
                        "price_usdc": price_usdc,
                        "source_price": source_price,
                        "usdc_usdt_price": usdc_usdt_price,
                        "change_percent": float(item.get("change_percent") or 0),
                        "rank_side": side,
                        "rank_position": position,
                        "event_time": event_time,
                        "source": current.get("source", "unknown"),
                    }
                    candidates.append(row)
                    seen.add(symbol)

        pair_rows = []
        for x, y in combinations(candidates, 2):
            if x["price_usdc"] <= 0 or y["price_usdc"] <= 0:
                continue
            pair_rows.append(
                {
                    "observed_at": observed_at,
                    "x_symbol": x["symbol"],
                    "y_symbol": y["symbol"],
                    "x_usdc_price": x["price_usdc"],
                    "y_usdc_price": y["price_usdc"],
                    "x_y_price": x["price_usdc"] / y["price_usdc"],
                    "window_seconds": float(extremes.get("window_seconds") or 0),
                    "event_time": max(x["event_time"], y["event_time"]),
                    "source": "binance_candidate",
                }
            )
        return candidates, pair_rows


def insert_extreme(items: list[dict], row: dict, limit: int, reverse: bool) -> None:
    index = 0
    while index < len(items):
        current = items[index]["change_percent"]
        if (reverse and row["change_percent"] > current) or (not reverse and row["change_percent"] < current):
            break
        index += 1
    items.insert(index, row)
    del items[limit:]
