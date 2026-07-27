import asyncio
import json
import logging
import os
import signal
import sys
import time
from collections import deque
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Deque, Iterable, Optional
from urllib.parse import urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen

import websockets
from web3 import Web3

SRC_ROOT = Path(__file__).resolve().parents[1]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from market.aave_reserve_cache import load_aave_reserve_symbols
from core.env_loader import load_env_files
from db.storage import (
    append_arbitrage_simulation,
    append_binance_extremes,
    append_binance_price_history,
    append_observations,
    ensure_database_schema,
    require_psycopg,
)
from strategy.trigger_signal import TriggerConfig, build_trigger_signal


load_env_files(__file__)

APP_DIR = str(Path(__file__).resolve().parents[1])
RUNTIME_DIR = Path(os.getenv("FLASHLOAN_RUNTIME_DIR", str(Path(APP_DIR) / "runtime")))
STATE_DIR = RUNTIME_DIR / "state"
LATEST_ARBITRAGE_PATH = str(STATE_DIR / "latest_arbitrage.json")
LATEST_EXTREMES_PATH = str(STATE_DIR / "latest_extremes.json")
DEFAULT_BINANCE_WS_BASES = "wss://stream.binance.com:9443,wss://data-stream.binance.vision:443"
DEFAULT_BINANCE_REST_BASES = "https://api.binance.com,https://data-api.binance.vision"
DEFAULT_RPC = "https://api.avax.network/ext/bc/C/rpc"
DEFAULT_SYMBOLS = "AVAXUSDT,ETHUSDT,BTCUSDT,AAVEUSDT,USDCUSDT"
DEFAULT_EXECUTABLE_SYMBOLS = "AVAXUSDT,ETHUSDT,BTCUSDT,AAVEUSDT"
COINGECKO_MARKETS_URL = (
    "https://api.coingecko.com/api/v3/coins/markets?"
    "vs_currency=usd&order=market_cap_desc&per_page=250&page=1&sparkline=false"
)
STATIC_TOP_MARKET_SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT", "ADAUSDT", "TRXUSDT",
    "AVAXUSDT", "SHIBUSDT", "LINKUSDT", "DOTUSDT", "BCHUSDT", "NEARUSDT", "LTCUSDT", "UNIUSDT",
    "APTUSDT", "ICPUSDT", "ETCUSDT", "FILUSDT", "XLMUSDT", "HBARUSDT", "ATOMUSDT", "VETUSDT",
    "OPUSDT", "ARBUSDT", "INJUSDT", "AAVEUSDT", "GRTUSDT", "RUNEUSDT", "MKRUSDT", "ALGOUSDT",
    "SUIUSDT", "QNTUSDT", "STXUSDT", "IMXUSDT", "EGLDUSDT", "FLOWUSDT", "SANDUSDT", "MANAUSDT",
    "AXSUSDT", "THETAUSDT", "FTMUSDT", "XTZUSDT", "EOSUSDT", "KAVAUSDT", "GALAUSDT", "CHZUSDT",
    "SNXUSDT", "CRVUSDT", "COMPUSDT", "DYDXUSDT", "APEUSDT", "LDOUSDT", "WLDUSDT", "ARUSDT",
]
AAVE_ORACLE = "0xEBd36016B3eD09D4693Ed4251c67Bd858c3c7C9C"
LOG = logging.getLogger("observer")

ORACLE_ABI = [
    {"inputs": [{"internalType": "address", "name": "asset", "type": "address"}], "name": "getAssetPrice", "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}], "stateMutability": "view", "type": "function"},
    {"inputs": [], "name": "BASE_CURRENCY_UNIT", "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}], "stateMutability": "view", "type": "function"},
]


@dataclass(frozen=True)
class AssetConfig:
    symbol: str
    asset_address: str
    binance_symbol: str


@dataclass(frozen=True)
class ObserverConfig:
    rpc_url: str
    binance_ws_bases: list[str]
    binance_rest_bases: list[str]
    binance_rest_poll_seconds: float
    binance_top_symbols: list[str]
    binance_change_window_seconds: float
    binance_extreme_write_seconds: float
    binance_price_history_symbols: list[str]
    binance_price_history_write_seconds: float
    binance_price_history_flush_seconds: float
    trigger: TriggerConfig
    symbols: list[str]
    sample_seconds: float
    observation_write_seconds: float
    poll_seconds: float
    report_seconds: float
    alert_diff_percent: float
    database_url: str
    stale_seconds: float
    run_seconds: float
    report_only_alerts: bool
    require_binance_ws_for_arbitrage: bool


ASSETS = {
    "AVAXUSDT": AssetConfig("WAVAX", "0xB31f66AA3C1e785363F0875A1B74E27b85FD66c7", "AVAXUSDT"),
    "ETHUSDT": AssetConfig("WETH.e", "0x49D5c2BdFfac6CE2BFdB6640F4F80f226bc10bAB", "ETHUSDT"),
    "BTCUSDT": AssetConfig("BTC.b", "0x152b9d0FdC40C096757F570A51E494bd4b943E50", "BTCUSDT"),
    "AAVEUSDT": AssetConfig("AAVE.e", "0x63a72806098Bd3D9520cC43356dD78afe5D386D9", "AAVEUSDT"),
    "USDCUSDT": AssetConfig("USDC", "0xB97EF9Ef8734C71904D8002F8b6Bc66Dd9c48a6E", "USDCUSDT"),
}


class PriceState:
    def __init__(self) -> None:
        self.binance: dict[str, dict] = {}
        self.binance_history: dict[str, Deque[tuple[int, float, str]]] = {}
        self.aave: dict[str, dict] = {}
        self.lock = asyncio.Lock()

    async def update_binance(self, symbol: str, price: float, event_ms: int, source: str) -> None:
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
        limit: int = 5,
        source: str | None = None,
    ) -> dict:
        cutoff_ms = int(time.time() * 1000) - int(window_seconds * 1000)
        rows = []
        async with self.lock:
            for symbol in symbols:
                history = self.binance_history.get(symbol)
                if not history:
                    continue
                while history and history[0][0] < cutoff_ms:
                    history.popleft()
                source_history = [
                    (event_ms, price, item_source)
                    for event_ms, price, item_source in history
                    if source is None or item_source == source
                ]
                if len(source_history) < 2 or source_history[0][1] <= 0:
                    continue
                start_ms, start_price, _ = source_history[0]
                end_ms, end_price, _ = source_history[-1]
                rows.append(
                    {
                        "symbol": symbol,
                        "change_percent": (end_price - start_price) / start_price * 100,
                        "start_price": start_price,
                        "end_price": end_price,
                        "start_ms": start_ms,
                        "end_ms": end_ms,
                    }
                )
        top, bottom = [], []
        for row in rows:
            insert_extreme(top, row, limit, reverse=True)
            insert_extreme(bottom, row, limit, reverse=False)
        return {
            "observed_at": now_iso(),
            "window_seconds": window_seconds,
            "sample_count": len(rows),
            "price_source": source or "mixed",
            "top": top,
            "bottom": bottom,
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


def insert_extreme(items: list[dict], row: dict, limit: int, reverse: bool) -> None:
    index = 0
    while index < len(items):
        current = items[index]["change_percent"]
        if (reverse and row["change_percent"] > current) or (not reverse and row["change_percent"] < current):
            break
        index += 1
    items.insert(index, row)
    del items[limit:]

class UtcFormatter(logging.Formatter):
    converter = time.gmtime

def setup_logging() -> None:
    level = getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO)
    handler = logging.StreamHandler()
    handler.setFormatter(UtcFormatter("%(asctime)sZ %(levelname)s %(message)s", "%Y-%m-%dT%H:%M:%S"))
    logging.basicConfig(level=level, handlers=[handler], force=True)


def env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)) or default)
    except ValueError:
        LOG.warning("invalid float env %s; using default=%s", name, default)
        return default


def env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def env_list(name: str, default: str) -> list[str]:
    return list(dict.fromkeys(part.strip().rstrip("/").upper() for part in os.getenv(name, default).split(",") if part.strip()))


def env_urls(name: str, default: str, scheme: str) -> list[str]:
    values = [part.strip().rstrip("/") for part in os.getenv(name, default).split(",") if part.strip()]
    if not values or any(not value.startswith(scheme) for value in values):
        raise ValueError(f"{name} must contain {scheme} URLs")
    return list(dict.fromkeys(values))


def fetch_json(url: str) -> object:
    with urlopen(Request(url, headers={"User-Agent": "flashloan-observer/1.0"}), timeout=15) as response:
        return json.loads(response.read().decode("utf-8"))


def fetch_binance_usdt_symbols(rest_bases: list[str]) -> set[str]:
    for base in rest_bases:
        try:
            payload = fetch_json(f"{base}/api/v3/exchangeInfo")
            return {
                item["symbol"].upper()
                for item in payload.get("symbols", [])
                if item.get("status") == "TRADING" and item.get("quoteAsset") == "USDT"
            }
        except Exception as exc:
            LOG.warning("binance exchangeInfo failed base=%s error=%r", mask_url(base), exc)
    return set(STATIC_TOP_MARKET_SYMBOLS)


def fetch_binance_24h_tickers(rest_bases: list[str]) -> list[dict]:
    for base in rest_bases:
        try:
            payload = fetch_json(f"{base}/api/v3/ticker/24hr")
            if isinstance(payload, list):
                return payload
        except Exception as exc:
            LOG.warning("binance 24hr ticker failed base=%s error=%r", mask_url(base), exc)
    return []


def resolve_binance_market_cap_symbols(rest_bases: list[str], limit: int) -> list[str]:
    supported = fetch_binance_usdt_symbols(rest_bases)
    selected: list[str] = []
    try:
        for item in fetch_json(COINGECKO_MARKETS_URL):
            base_symbol = str(item.get("symbol", "")).upper()
            if base_symbol.isascii() and base_symbol.replace("1", "").replace("2", "").isalnum():
                symbol = f"{base_symbol}USDT"
                if symbol in supported and symbol not in selected:
                    selected.append(symbol)
            if len(selected) >= limit:
                return selected
    except Exception as exc:
        LOG.warning("coingecko lookup failed=%r; using fallback", exc)
    for symbol in STATIC_TOP_MARKET_SYMBOLS:
        if symbol in supported and symbol not in selected:
            selected.append(symbol)
        if len(selected) >= limit:
            break
    return selected


def resolve_binance_mover_symbols(rest_bases: list[str], limit: int) -> list[str]:
    supported = fetch_binance_usdt_symbols(rest_bases)
    tickers = []
    for item in fetch_binance_24h_tickers(rest_bases):
        symbol = str(item.get("symbol", "")).upper()
        if symbol not in supported:
            continue
        try:
            change = float(item.get("priceChangePercent", 0))
            quote_volume = float(item.get("quoteVolume", 0))
        except (TypeError, ValueError):
            continue
        tickers.append({"symbol": symbol, "change": change, "quote_volume": quote_volume})

    min_quote_volume = max(0.0, env_float("BINANCE_MIN_QUOTE_VOLUME_USDT", 0.0))
    if min_quote_volume:
        tickers = [item for item in tickers if item["quote_volume"] >= min_quote_volume]
    if not tickers:
        return resolve_binance_market_cap_symbols(rest_bases, limit)

    half = max(1, limit // 2)
    gainers = sorted(tickers, key=lambda item: item["change"], reverse=True)
    losers = sorted(tickers, key=lambda item: item["change"])
    selected: list[str] = []
    for item in [*gainers[:half], *losers[: max(1, limit - half)]]:
        if item["symbol"] not in selected:
            selected.append(item["symbol"])
        if len(selected) >= limit:
            break
    return selected


def resolve_binance_all_usdt_symbols(rest_bases: list[str], limit: int) -> list[str]:
    supported = sorted(fetch_binance_usdt_symbols(rest_bases))
    return supported[:limit]


def resolve_binance_top_symbols(rest_bases: list[str], limit: int) -> list[str]:
    mode = os.getenv("BINANCE_SYMBOL_SELECTION", "market_cap").strip().lower()
    if mode in {"movers", "gainers_losers", "gainers-losers"}:
        return resolve_binance_mover_symbols(rest_bases, limit)
    if mode in {"all", "all_usdt", "all-usdt", "velocity", "speed"}:
        return resolve_binance_all_usdt_symbols(rest_bases, limit)
    return resolve_binance_market_cap_symbols(rest_bases, limit)


def load_config() -> ObserverConfig:
    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        raise ValueError("DATABASE_URL is required.")
    rest_bases = env_urls("BINANCE_REST_BASES", DEFAULT_BINANCE_REST_BASES, "https://")
    symbols = [symbol for symbol in env_list("SYMBOLS", DEFAULT_SYMBOLS) if symbol in ASSETS]
    if not symbols:
        raise ValueError("No supported symbols selected.")
    top_symbols = resolve_binance_top_symbols(rest_bases, max(1, int(env_float("BINANCE_TOP_SYMBOL_LIMIT", 100))))
    price_history_raw = os.getenv("BINANCE_PRICE_HISTORY_SYMBOLS", DEFAULT_EXECUTABLE_SYMBOLS).strip()
    if price_history_raw.upper() in {"TOP", "TOP_SYMBOLS", "ALL_TOP"}:
        price_history_symbols = top_symbols
    else:
        price_history_symbols = env_list("BINANCE_PRICE_HISTORY_SYMBOLS", DEFAULT_EXECUTABLE_SYMBOLS)
    executable_raw = os.getenv("TRIGGER_EXECUTABLE_SYMBOLS", DEFAULT_EXECUTABLE_SYMBOLS).strip()
    if executable_raw.upper() in {"AAVE", "AAVE_RESERVES", "AAVE_POOL"}:
        executable_symbols = tuple(
            sorted(
                load_aave_reserve_symbols(
                    os.getenv("AVALANCHE_RPC", DEFAULT_RPC).strip(),
                    os.getenv("AAVE_POOL_ADDRESS", "").strip(),
                    set(top_symbols),
                )
            )
        )
    else:
        executable_symbols = tuple(
            symbol
            for symbol in env_list("TRIGGER_EXECUTABLE_SYMBOLS", DEFAULT_EXECUTABLE_SYMBOLS)
            if symbol in ASSETS
        )
    return ObserverConfig(
        rpc_url=os.getenv("AVALANCHE_RPC", DEFAULT_RPC).strip(),
        binance_ws_bases=env_urls("BINANCE_WS_BASES", DEFAULT_BINANCE_WS_BASES, "wss://"),
        binance_rest_bases=rest_bases,
        binance_rest_poll_seconds=max(1.0, env_float("BINANCE_REST_POLL_SECONDS", 3.0)),
        binance_top_symbols=top_symbols,
        binance_change_window_seconds=max(0.2, env_float("BINANCE_CHANGE_WINDOW_SECONDS", 0.2)),
        binance_extreme_write_seconds=max(0.2, env_float("BINANCE_EXTREME_WRITE_SECONDS", 1.0)),
        binance_price_history_symbols=price_history_symbols,
        binance_price_history_write_seconds=max(0.2, env_float("BINANCE_PRICE_HISTORY_WRITE_SECONDS", 1.0)),
        binance_price_history_flush_seconds=max(1.0, env_float("BINANCE_PRICE_HISTORY_FLUSH_SECONDS", 5.0)),
        trigger=TriggerConfig(
            min_up_change_percent=max(0.0, env_float("TRIGGER_MIN_UP_CHANGE_PERCENT", 1.0)),
            min_down_change_percent=max(0.0, env_float("TRIGGER_MIN_DOWN_CHANGE_PERCENT", 1.0)),
            executable_symbols=executable_symbols,
        ),
        symbols=list(dict.fromkeys(symbols)),
        sample_seconds=max(0.05, env_float("SAMPLE_SECONDS", 0.2)),
        observation_write_seconds=max(0.2, env_float("OBSERVATION_WRITE_SECONDS", env_float("SAMPLE_SECONDS", 0.2))),
        poll_seconds=max(0.2, env_float("AAVE_POLL_SECONDS", 5.0)),
        report_seconds=max(0.5, env_float("REPORT_SECONDS", 2.0)),
        alert_diff_percent=max(0.0, env_float("ALERT_DIFF_PERCENT", 0.30)),
        database_url=database_url,
        stale_seconds=max(1.0, env_float("STALE_SECONDS", 30.0)),
        run_seconds=max(0.0, env_float("RUN_SECONDS", 0.0)),
        report_only_alerts=env_bool("REPORT_ONLY_ALERTS", False),
        require_binance_ws_for_arbitrage=env_bool("ARBITRAGE_REQUIRE_BINANCE_WS", True),
    )


def mask_url(url: str) -> str:
    try:
        parts = urlsplit(url)
        netloc = parts.netloc
        if parts.username:
            port = f":{parts.port}" if parts.port else ""
            netloc = f"{parts.username}:***@{parts.hostname or ''}{port}"
        return urlunsplit((parts.scheme, netloc, parts.path, "***" if parts.query else "", ""))
    except ValueError:
        return "***"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def utc_from_ms(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat(timespec="milliseconds")


def age_seconds(iso_timestamp: str) -> float:
    return max(0.0, (datetime.now(timezone.utc) - datetime.fromisoformat(iso_timestamp)).total_seconds())


def pct_diff(binance_price: float, aave_price: float) -> Optional[float]:
    return None if aave_price <= 0 else (binance_price - aave_price) / aave_price * 100


def binance_stream_url(base: str, symbols: Iterable[str]) -> str:
    streams = "/".join(f"{symbol.lower()}@aggTrade" for symbol in symbols)
    return base.format(streams=streams) if "{streams}" in base else f"{base}/stream?streams={streams}"


def fetch_binance_rest_price(base: str, symbol: str) -> float:
    url = f"{base}/api/v3/ticker/price?{urlencode({'symbol': symbol})}"
    payload = fetch_json(url)
    return float(payload["price"])


async def binance_listener(symbols: Iterable[str], ws_bases: Iterable[str], state: PriceState, stop: asyncio.Event) -> None:
    symbol_list, base_list, base_index, delay = list(symbols), list(ws_bases), 0, 1.0
    while not stop.is_set():
        base = base_list[base_index % len(base_list)]
        try:
            LOG.info("binance connecting base=%s symbols=%s", mask_url(base), len(symbol_list))
            async with websockets.connect(binance_stream_url(base, symbol_list), ping_interval=20, ping_timeout=20, open_timeout=15, max_queue=2048) as ws:
                LOG.info("binance connected base=%s", mask_url(base))
                delay = 1.0
                async for raw in ws:
                    if stop.is_set():
                        break
                    data = json.loads(raw).get("data", {})
                    if data.get("s") and data.get("p"):
                        await state.update_binance(
                            data["s"],
                            float(data["p"]),
                            int(data.get("E", int(time.time() * 1000))),
                            "ws",
                        )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            LOG.warning("binance error=%r base=%s reconnect_in=%.1fs", exc, mask_url(base), delay)
            base_index += 1
            await sleep_until_next(stop, delay)
            delay = min(delay * 2, 60)


async def binance_rest_poller(config: ObserverConfig, state: PriceState, stop: asyncio.Event) -> None:
    base_index = 0
    while not stop.is_set():
        base = config.binance_rest_bases[base_index % len(config.binance_rest_bases)]
        try:
            for symbol in config.symbols:
                price = await asyncio.to_thread(fetch_binance_rest_price, base, symbol)
                await state.update_binance(symbol, price, int(time.time() * 1000), "rest")
        except Exception as exc:
            LOG.warning("binance_rest error=%r base=%s", exc, mask_url(base))
            base_index += 1
        await sleep_until_next(stop, config.binance_rest_poll_seconds)


async def aave_poller(config: ObserverConfig, state: PriceState, stop: asyncio.Event) -> None:
    w3 = Web3(Web3.HTTPProvider(config.rpc_url, request_kwargs={"timeout": 10}))
    oracle = w3.eth.contract(address=Web3.to_checksum_address(AAVE_ORACLE), abi=ORACLE_ABI)
    base_unit = await wait_for_oracle_base_unit(oracle, stop)
    while not stop.is_set():
        started = time.monotonic()
        try:
            block = await asyncio.to_thread(lambda: w3.eth.block_number)
            for symbol in config.symbols:
                raw = await asyncio.to_thread(oracle.functions.getAssetPrice(Web3.to_checksum_address(ASSETS[symbol].asset_address)).call)
                await state.update_aave(symbol, raw / base_unit, block)
        except Exception as exc:
            LOG.warning("aave_poll error=%r", exc)
        await sleep_until_next(stop, max(0.1, config.poll_seconds - (time.monotonic() - started)))


async def wait_for_oracle_base_unit(oracle, stop: asyncio.Event) -> int:
    delay = 1.0
    while not stop.is_set():
        try:
            base_unit = await asyncio.to_thread(oracle.functions.BASE_CURRENCY_UNIT().call)
            LOG.info("aave oracle ready base_unit=%s", base_unit)
            return base_unit
        except Exception as exc:
            LOG.warning("aave oracle init failed=%r retry_in=%.1fs", exc, delay)
            await sleep_until_next(stop, delay)
            delay = min(delay * 2, 60)
    raise asyncio.CancelledError


async def reporter(config: ObserverConfig, state: PriceState, stop: asyncio.Event) -> None:
    last_report_at = 0.0
    last_observation_write_at = 0.0
    last_db_error_at = 0.0
    while not stop.is_set():
        rows, snapshot = [], await state.snapshot()
        now = time.monotonic()
        report_due = time.monotonic() - last_report_at >= config.report_seconds
        observation_write_due = now - last_observation_write_at >= config.observation_write_seconds
        for symbol in config.symbols:
            b, a = snapshot["binance"].get(symbol), snapshot["aave"].get(symbol)
            if not b or not a:
                continue
            diff = pct_diff(b["price"], a["price"])
            if diff is None:
                continue
            row = {
                "observed_at": now_iso(),
                "symbol": symbol,
                "asset": ASSETS[symbol].symbol,
                "binance_price": f"{b['price']:.10f}",
                "binance_event_time": utc_from_ms(b["event_ms"]),
                "aave_price": f"{a['price']:.10f}",
                "aave_block": a["block"],
                "diff_percent": f"{diff:.6f}",
                "binance_age_seconds": f"{age_seconds(b['seen_at']):.3f}",
                "aave_age_seconds": f"{age_seconds(a['seen_at']):.3f}",
            }
            rows.append(row)
            if report_due and not config.report_only_alerts:
                LOG.info("OK %s binance=%.6f aave=%.6f diff=%+.4f%%", symbol, b["price"], a["price"], diff)
        if rows and observation_write_due:
            try:
                await asyncio.to_thread(append_observations, config.database_url, rows)
                last_observation_write_at = now
            except Exception as exc:
                if now - last_db_error_at >= config.report_seconds:
                    LOG.warning("observation database write failed error=%r", exc)
                    last_db_error_at = now
        if report_due:
            last_report_at = time.monotonic()
        await sleep_until_next(stop, config.sample_seconds)


async def extreme_and_arbitrage_reporter(config: ObserverConfig, state: PriceState, stop: asyncio.Event) -> None:
    last_write_at, last_price_sample_at, last_price_flush_at, last_log_at, last_db_error_at = 0.0, 0.0, 0.0, 0.0, 0.0
    price_history_buffer: list[dict] = []
    while not stop.is_set():
        extremes = await state.window_extremes(config.binance_top_symbols, config.binance_change_window_seconds, 5)
        executable_symbols = config.trigger.executable_symbols or tuple(config.symbols)
        simulation_extremes = await state.window_extremes(
            executable_symbols,
            config.binance_change_window_seconds,
            1,
            source="ws" if config.require_binance_ws_for_arbitrage else None,
        )
        simulation = build_trigger_signal(simulation_extremes, config.trigger)
        if extremes["top"] and extremes["bottom"]:
            write_json_atomic(LATEST_EXTREMES_PATH, extremes)
        if simulation:
            write_json_atomic(LATEST_ARBITRAGE_PATH, simulation)
        now = time.monotonic()
        if simulation and now - last_log_at >= config.report_seconds:
            LOG.info(
                "trigger signal=%s x=%s %+.4f%% y=%s %+.4f%% window=%.3fs",
                simulation["signal"],
                simulation["a_symbol"],
                simulation["a_change_percent"],
                simulation["b_symbol"],
                simulation["b_change_percent"],
                simulation["window_seconds"],
            )
            last_log_at = now
        if simulation and now - last_write_at >= config.binance_extreme_write_seconds:
            try:
                await asyncio.to_thread(append_binance_extremes, config.database_url, extremes)
                await asyncio.to_thread(append_arbitrage_simulation, config.database_url, simulation)
            except Exception as exc:
                if now - last_db_error_at >= config.report_seconds:
                    LOG.warning("arbitrage database write failed error=%r", exc)
                    last_db_error_at = now
            last_write_at = now
        if now - last_price_sample_at >= config.binance_price_history_write_seconds:
            price_rows = await state.binance_price_history_rows(
                config.binance_price_history_symbols,
                source="ws" if config.require_binance_ws_for_arbitrage else None,
            )
            price_history_buffer.extend(price_rows)
            last_price_sample_at = now
        if price_history_buffer and now - last_price_flush_at >= config.binance_price_history_flush_seconds:
            rows_to_write = price_history_buffer
            price_history_buffer = []
            try:
                await asyncio.to_thread(append_binance_price_history, config.database_url, rows_to_write)
            except Exception as exc:
                price_history_buffer = rows_to_write + price_history_buffer
                if now - last_db_error_at >= config.report_seconds:
                    LOG.warning("binance price history write failed error=%r", exc)
                    last_db_error_at = now
            last_price_flush_at = now
        await sleep_until_next(stop, config.sample_seconds)


def write_json_atomic(path: str, payload: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as file:
        json.dump(payload, file, ensure_ascii=True, separators=(",", ":"))
    os.replace(tmp_path, path)


async def auto_stop_after(seconds: float, stop: asyncio.Event) -> None:
    if seconds > 0:
        await sleep_until_next(stop, seconds)
        stop.set()


async def sleep_until_next(stop: asyncio.Event, seconds: float) -> None:
    try:
        await asyncio.wait_for(stop.wait(), timeout=seconds)
    except asyncio.TimeoutError:
        return


async def main() -> None:
    setup_logging()
    config, state, stop = load_config(), PriceState(), asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop.set)
    if env_bool("SKIP_DATABASE_SCHEMA", False):
        LOG.info("database schema initialization skipped")
    else:
        await asyncio.to_thread(ensure_database_schema, config.database_url)
    LOG.info(
        "observer started top_symbols=%s sample=%.3fs trigger_window=%.3fs trigger_up=%.2f%% trigger_down=%.2f%%",
        len(config.binance_top_symbols),
        config.sample_seconds,
        config.binance_change_window_seconds,
        config.trigger.min_up_change_percent,
        config.trigger.min_down_change_percent,
    )
    binance_symbols = list(dict.fromkeys([*config.symbols, *config.binance_top_symbols]))
    tasks = [
        asyncio.create_task(binance_listener(binance_symbols, config.binance_ws_bases, state, stop)),
        asyncio.create_task(binance_rest_poller(config, state, stop)),
        asyncio.create_task(aave_poller(config, state, stop)),
        asyncio.create_task(reporter(config, state, stop)),
        asyncio.create_task(extreme_and_arbitrage_reporter(config, state, stop)),
        asyncio.create_task(auto_stop_after(config.run_seconds, stop)),
    ]
    await stop.wait()
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


if __name__ == "__main__":
    asyncio.run(main())
