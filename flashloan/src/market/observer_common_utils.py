"""Small reusable utilities used by market.observer_common."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from typing import Iterable, Optional
from urllib.parse import urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen

from core.config_schema import parse_env_float, parse_env_int
from core.aave_reserve_cache import parse_rpc_urls
from web3 import Web3


LOG = logging.getLogger("observer")
DEFAULT_RPC = "https://api.avax.network/ext/bc/C/rpc"
DEFAULT_RPC_CANDIDATES = "https://api.avax.network/ext/bc/C/rpc,https://rpc.ankr.com/avalanche,https://avalanche-c-chain-rpc.publicnode.com"


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


def fetch_json(url: str, timeout_seconds: float | None = None) -> object:
    timeout = max(0.5, float(timeout_seconds if timeout_seconds is not None else env_float("HTTP_JSON_TIMEOUT_SECONDS", 15.0)))
    with urlopen(Request(url, headers={"User-Agent": "flashloan-observer/1.0"}), timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


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
    payload = fetch_json(url, timeout_seconds=env_float("BINANCE_REST_TIMEOUT_SECONDS", 5.0))
    price = float(payload["price"])
    if price <= 0:
        raise ValueError(f"non-positive Binance REST price for {symbol}")
    return price


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


__all__ = [
    "age_seconds",
    "auto_stop_after",
    "avalanche_rpc_urls",
    "binance_stream_url",
    "env_bool",
    "env_float",
    "env_int",
    "env_list",
    "env_urls",
    "fetch_binance_rest_price",
    "fetch_json",
    "mask_url",
    "now_iso",
    "pct_diff",
    "should_compute_conversion_profits",
    "sleep_until_next",
    "utc_from_ms",
    "web3_for_rpc_url",
    "write_json_atomic",
]
