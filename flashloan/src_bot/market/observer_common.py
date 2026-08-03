import asyncio
import json
import logging
import os
import signal
import sys
import time
from collections import deque
from itertools import combinations
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

from core.config_schema import parse_env_float, parse_env_int
from market.aave_reserve_cache import load_aave_reserve_assets, load_aave_reserve_symbols, parse_rpc_urls
from market.dex_usdc_targets import (
    load_borrow_pool_binance_symbols,
    load_stable_pool_binance_symbols,
    load_usdc_pool_binance_symbols,
)
from core.env_loader import load_env_files, resolve_env_path
from db.storage_observer import (
    append_arbitrage_simulation,
    append_binance_candidate_price_history,
    append_binance_extremes,
    append_binance_pair_price_history,
    append_observations,
)
from db.storage_schema import ensure_database_schema
from db.storage_liquidation import try_acquire_observer_lock
from strategy.arbitrage import ArbitrageConfig, simulate_basket
from strategy.trigger_signal import TriggerConfig


load_env_files(__file__, override=False)

APP_DIR = str(Path(__file__).resolve().parents[1])
RUNTIME_DIR = resolve_env_path("FLASHLOAN_RUNTIME_DIR", "runtime", APP_DIR)
STATE_DIR = RUNTIME_DIR / "state"
LATEST_ARBITRAGE_PATH = str(STATE_DIR / "latest_arbitrage.json")
LATEST_EXTREMES_PATH = str(STATE_DIR / "latest_extremes.json")
DEFAULT_BINANCE_WS_BASES = "wss://stream.binance.com:9443,wss://data-stream.binance.vision:443"
DEFAULT_BINANCE_REST_BASES = "https://api.binance.com,https://data-api.binance.vision"
DEFAULT_BINANCE_WS_CHUNK_SIZE = 200
DEFAULT_RPC = "https://api.avax.network/ext/bc/C/rpc"
DEFAULT_RPC_CANDIDATES = (
    "https://api.avax.network/ext/bc/C/rpc,"
    "https://rpc.ankr.com/avalanche,"
    "https://avalanche-c-chain-rpc.publicnode.com"
)
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
    {"inputs": [{"internalType": "address[]", "name": "assets", "type": "address[]"}], "name": "getAssetsPrices", "outputs": [{"internalType": "uint256[]", "name": "", "type": "uint256[]"}], "stateMutability": "view", "type": "function"},
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
    rpc_urls: list[str]
    asset_lookup: dict[str, AssetConfig]
    binance_ws_bases: list[str]
    binance_rest_bases: list[str]
    binance_rest_poll_seconds: float
    binance_top_symbols: list[str]
    binance_change_window_seconds: float
    binance_velocity_min_change_percent: float
    binance_velocity_side_limit: int
    binance_extreme_write_seconds: float
    binance_candidate_db_side_limit: int
    binance_pair_price_write_seconds: float
    binance_pair_price_flush_seconds: float
    binance_pair_history_writes: bool
    observation_db_writes: bool
    aave_verification_enabled: bool
    trigger: TriggerConfig
    arbitrage: ArbitrageConfig
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
    market_divergence_trigger_min: float


LEGACY_ASSETS = {
    "AVAXUSDT": AssetConfig("WAVAX", "0xB31f66AA3C1e785363F0875A1B74E27b85FD66c7", "AVAXUSDT"),
    "ETHUSDT": AssetConfig("WETH.e", "0x49D5c2BdFfac6CE2BFdB6640F4F80f226bc10bAB", "ETHUSDT"),
    "BTCUSDT": AssetConfig("BTC.b", "0x152b9d0FdC40C096757F570A51E494bd4b943E50", "BTCUSDT"),
    "AAVEUSDT": AssetConfig("AAVE.e", "0x63a72806098Bd3D9520cC43356dD78afe5D386D9", "AAVEUSDT"),
    "USDCUSDT": AssetConfig("USDC", "0xB97EF9Ef8734C71904D8002F8b6Bc66Dd9c48a6E", "USDCUSDT"),
}

ASSETS = LEGACY_ASSETS



class UtcFormatter(logging.Formatter):
    converter = time.gmtime

def setup_logging() -> None:
    level = getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO)
    handler = logging.StreamHandler()
    handler.setFormatter(UtcFormatter("%(asctime)sZ %(levelname)s %(message)s", "%Y-%m-%dT%H:%M:%S"))
    logging.basicConfig(level=level, handlers=[handler], force=True)


def env_float(name: str, default: float) -> float:
    value, error = parse_env_float(name, default)
    if error:
        LOG.warning("invalid float env %s; using default=%s", name, default)
    return value


def env_int(name: str, default: int, minimum: int = 0) -> int:
    value, error = parse_env_int(name, default, minimum=minimum)
    if error:
        LOG.warning("invalid int env %s; using default=%s", name, default)
    return value


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
    return supported if limit <= 0 else supported[:limit]


def resolve_dex_usdc_pool_symbols(rest_bases: list[str], limit: int) -> list[str]:
    supported = fetch_binance_usdt_symbols(rest_bases)
    rpc_urls = avalanche_rpc_urls()
    return load_usdc_pool_binance_symbols(rpc_urls, supported_symbols=supported, limit=limit)


def resolve_dex_stable_pool_symbols(rest_bases: list[str], limit: int) -> list[str]:
    supported = fetch_binance_usdt_symbols(rest_bases)
    rpc_urls = avalanche_rpc_urls()
    return load_stable_pool_binance_symbols(rpc_urls, supported_symbols=supported, limit=limit)


def resolve_dex_borrow_pool_symbols(rest_bases: list[str], limit: int, borrow_assets: list[dict]) -> list[str]:
    supported = fetch_binance_usdt_symbols(rest_bases)
    rpc_urls = avalanche_rpc_urls()
    return load_borrow_pool_binance_symbols(rpc_urls, borrow_assets, supported_symbols=supported, limit=limit)


def resolve_aave_binance_overlap_symbols(rest_bases: list[str], limit: int) -> list[str]:
    supported = fetch_binance_usdt_symbols(rest_bases)
    reserve_assets = load_aave_reserve_assets(
        avalanche_rpc_urls(),
        os.getenv("AAVE_POOL_ADDRESS", "").strip(),
        limit=max(1, env_int("AAVE_RESERVE_SYMBOL_LIMIT", 1000)),
        exclude_stables=False,
    ) if os.getenv("AAVE_POOL_ADDRESS", "").strip() else []
    reserve_symbols = [
        str(asset.get("binance_symbol", "")).upper()
        for asset in reserve_assets
        if asset.get("binance_symbol")
    ]
    overlap = [symbol for symbol in reserve_symbols if symbol in supported]
    return overlap if limit <= 0 else overlap[:limit]


def resolve_binance_top_symbols(rest_bases: list[str], limit: int) -> list[str]:
    mode = os.getenv("BINANCE_SYMBOL_SELECTION", "market_cap").strip().lower()
    if mode in {"aave_binance_overlap", "aave-binance-overlap", "public_overlap", "public-overlap"}:
        return resolve_aave_binance_overlap_symbols(rest_bases, limit)
    if mode in {"stable_pools", "stable-pools", "dex_stable", "dex-stable"}:
        return resolve_dex_stable_pool_symbols(rest_bases, limit)
    if mode in {"usdc_pools", "usdc-pools", "dex_usdc", "dex-usdc"}:
        return resolve_dex_usdc_pool_symbols(rest_bases, limit)
    if mode in {"movers", "gainers_losers", "gainers-losers"}:
        return resolve_binance_mover_symbols(rest_bases, limit)
    if mode in {"all", "all_usdt", "all-usdt", "velocity", "speed"}:
        return resolve_binance_all_usdt_symbols(rest_bases, limit)
    return resolve_binance_market_cap_symbols(rest_bases, limit)


def load_config() -> ObserverConfig:
    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        raise ValueError("DATABASE_URL is required.")
    rpc_urls = avalanche_rpc_urls()
    rest_bases = env_urls("BINANCE_REST_BASES", DEFAULT_BINANCE_REST_BASES, "https://")
    pool_address = os.getenv("AAVE_POOL_ADDRESS", "").strip()
    reserve_symbol_limit = env_int("AAVE_RESERVE_SYMBOL_LIMIT", 1000)
    reserve_assets = load_aave_reserve_assets(
        rpc_urls,
        pool_address,
        limit=reserve_symbol_limit or None,
        exclude_stables=False,
    ) if pool_address else []
    supported_symbols = fetch_binance_usdt_symbols(rest_bases)
    asset_lookup = {
        str(asset.get("binance_symbol", "")).upper(): AssetConfig(
            str(asset.get("token_symbol", "")).upper(),
            str(asset.get("token_address", "")).strip(),
            str(asset.get("binance_symbol", "")).upper(),
        )
        for asset in reserve_assets
        if asset.get("binance_symbol") and asset.get("token_address")
    }
    asset_lookup.setdefault("USDCUSDT", LEGACY_ASSETS["USDCUSDT"])
    if not asset_lookup:
        asset_lookup = dict(LEGACY_ASSETS)
    reserve_symbols = list(dict.fromkeys(
        [
            str(asset.get("binance_symbol", "")).upper()
            for asset in reserve_assets
            if asset.get("binance_symbol")
        ]
    ))
    if supported_symbols:
        reserve_symbols = [symbol for symbol in reserve_symbols if symbol in supported_symbols]
    top_symbol_limit = int(env_float("BINANCE_TOP_SYMBOL_LIMIT", 100))
    selection_mode = os.getenv("BINANCE_SYMBOL_SELECTION", "market_cap").strip().lower()
    explicit_symbol_mode = selection_mode in {"explicit", "liquidation_assets", "liquidation-assets", "borrow_assets", "borrow-assets"}
    if explicit_symbol_mode:
        raw_symbols = env_list("SYMBOLS", DEFAULT_SYMBOLS)
        symbols = [symbol for symbol in raw_symbols if symbol in asset_lookup]
        if not symbols:
            symbols = list(dict.fromkeys([*asset_lookup.keys()]))[:100]
    elif reserve_symbols:
        symbols = [*reserve_symbols, "USDCUSDT"]
    else:
        raw_symbols = env_list("SYMBOLS", DEFAULT_SYMBOLS)
        symbols = [symbol for symbol in raw_symbols if symbol in asset_lookup]
        if not symbols:
            symbols = list(dict.fromkeys([*asset_lookup.keys()]))[:100]
    tracked_symbols = [symbol for symbol in symbols if symbol != "USDCUSDT"]
    if explicit_symbol_mode:
        if supported_symbols:
            symbols = [symbol for symbol in symbols if symbol in supported_symbols]
            if not symbols:
                symbols = [symbol for symbol in DEFAULT_SYMBOLS.split(",") if symbol in supported_symbols and symbol in asset_lookup]
        tracked_symbols = [symbol for symbol in symbols if symbol != "USDCUSDT"]
        top_symbols = []
    elif selection_mode in {"aave_binance_overlap", "aave-binance-overlap", "public_overlap", "public-overlap"}:
        top_symbols = [
            symbol
            for symbol in reserve_symbols
            if symbol in supported_symbols
        ]
    elif selection_mode in {"aave_borrow_pools", "aave-borrow-pools", "borrow_pools", "borrow-pools"}:
        top_symbols = resolve_dex_borrow_pool_symbols(rest_bases, top_symbol_limit, reserve_assets)
    else:
        top_symbols = resolve_binance_top_symbols(rest_bases, top_symbol_limit)
    if not top_symbols:
        top_symbols = tracked_symbols
    executable_raw = os.getenv("TRIGGER_EXECUTABLE_SYMBOLS", DEFAULT_EXECUTABLE_SYMBOLS).strip()
    if executable_raw.upper() in {"AAVE", "AAVE_RESERVES", "AAVE_POOL"}:
        executable_symbols = tuple(sorted(set(tracked_symbols or top_symbols)))
    else:
        executable_symbols = tuple(
            symbol
            for symbol in env_list("TRIGGER_EXECUTABLE_SYMBOLS", DEFAULT_EXECUTABLE_SYMBOLS)
            if symbol in asset_lookup
        )
    return ObserverConfig(
        rpc_url=rpc_urls[0],
        rpc_urls=rpc_urls,
        asset_lookup=asset_lookup,
        binance_ws_bases=env_urls("BINANCE_WS_BASES", DEFAULT_BINANCE_WS_BASES, "wss://"),
        binance_rest_bases=rest_bases,
        binance_rest_poll_seconds=max(1.0, env_float("BINANCE_REST_POLL_SECONDS", 3.0)),
        binance_top_symbols=top_symbols,
        binance_change_window_seconds=max(0.2, env_float("BINANCE_CHANGE_WINDOW_SECONDS", 1.0)),
        binance_velocity_min_change_percent=max(0.0, env_float("BINANCE_VELOCITY_MIN_CHANGE_PERCENT", 0.2)),
        binance_velocity_side_limit=max(1, int(env_float("BINANCE_VELOCITY_SIDE_LIMIT", 10))),
        binance_extreme_write_seconds=max(0.2, env_float("BINANCE_EXTREME_WRITE_SECONDS", 1.0)),
        binance_candidate_db_side_limit=max(1, int(env_float("BINANCE_CANDIDATE_DB_SIDE_LIMIT", 10))),
        binance_pair_price_write_seconds=max(0.2, env_float("BINANCE_PAIR_PRICE_WRITE_SECONDS", 1.0)),
        binance_pair_price_flush_seconds=max(1.0, env_float("BINANCE_PAIR_PRICE_FLUSH_SECONDS", 5.0)),
        binance_pair_history_writes=env_bool("BINANCE_PAIR_HISTORY_WRITES", True),
        observation_db_writes=env_bool("OBSERVATION_DB_WRITES", False),
        aave_verification_enabled=env_bool("AAVE_VERIFICATION_ENABLED", True),
        trigger=TriggerConfig(
            min_up_change_percent=max(0.0, env_float("TRIGGER_MIN_UP_CHANGE_PERCENT", 1.0)),
            min_down_change_percent=max(0.0, env_float("TRIGGER_MIN_DOWN_CHANGE_PERCENT", 1.0)),
            executable_symbols=executable_symbols,
        ),
        arbitrage=ArbitrageConfig(
            notional_usd=max(0.0, env_float("ARBITRAGE_NOTIONAL_USD", 1000.0)),
            trade_fee_percent=max(0.0, env_float("ARBITRAGE_TRADE_FEE_PERCENT", 0.10)),
            flashloan_fee_percent=max(0.0, env_float("ARBITRAGE_FLASHLOAN_FEE_PERCENT", 0.05)),
            min_window_spread_percent=max(0.0, env_float("ARBITRAGE_MIN_WINDOW_SPREAD_PERCENT", 0.30)),
            min_paper_profit_usd=max(0.0, env_float("ARBITRAGE_MIN_PAPER_PROFIT_USD", 0.0)),
            fee_reserve_percent=max(0.0, env_float("ARBITRAGE_FEE_RESERVE_PERCENT", 0.0)),
            basket_size=max(1, int(env_float("ARBITRAGE_BASKET_SIZE", 5))),
            executable_symbols=(),
        ),
        symbols=list(dict.fromkeys(symbols)),
        sample_seconds=max(0.2, env_float("SAMPLE_SECONDS", 1.0)),
        observation_write_seconds=max(0.2, env_float("OBSERVATION_WRITE_SECONDS", env_float("SAMPLE_SECONDS", 1.0))),
        poll_seconds=max(0.2, env_float("AAVE_POLL_SECONDS", 1.0)),
        report_seconds=max(0.5, env_float("REPORT_SECONDS", 2.0)),
        alert_diff_percent=max(0.0, env_float("ALERT_DIFF_PERCENT", 0.30)),
        database_url=database_url,
        stale_seconds=max(1.0, env_float("STALE_SECONDS", 30.0)),
        run_seconds=max(0.0, env_float("RUN_SECONDS", 0.0)),
        report_only_alerts=env_bool("REPORT_ONLY_ALERTS", False),
        require_binance_ws_for_arbitrage=env_bool("ARBITRAGE_REQUIRE_BINANCE_WS", True),
        market_divergence_trigger_min=max(0.0, env_float("MARKET_DIVERGENCE_TRIGGER_MIN", 1.0)),
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


def avalanche_rpc_urls() -> list[str]:
    candidates: list[str] = []
    for item in parse_rpc_urls(os.getenv("AVALANCHE_WSS", "")):
        if item not in candidates:
            candidates.append(item)
    for item in parse_rpc_urls(os.getenv("AVALANCHE_WSSS", "")):
        if item not in candidates:
            candidates.append(item)
    primary = os.getenv("AVALANCHE_RPC", "").strip()
    for item in parse_rpc_urls(primary):
        if item not in candidates:
            candidates.append(item)
    for item in parse_rpc_urls(os.getenv("AVALANCHE_RPCS", "")):
        if item not in candidates:
            candidates.append(item)
    for item in parse_rpc_urls(DEFAULT_RPC_CANDIDATES):
        if item not in candidates:
            candidates.append(item)
    return candidates or [DEFAULT_RPC]


def web3_for_rpc_url(rpc_url: str, timeout: int = 10) -> Web3:
    if rpc_url.lower().startswith(("ws://", "wss://")):
        return Web3(Web3.WebsocketProvider(rpc_url, websocket_timeout=timeout))
    return Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": timeout}))


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



def should_compute_conversion_profits(extremes: dict, threshold: float = 1.0) -> bool:
    return float(extremes.get("market_divergence_index") or 0.0) > float(threshold)


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


