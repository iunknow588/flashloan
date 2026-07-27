import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from web3 import Web3


POOL_ABI = [
    {
        "inputs": [],
        "name": "getReservesList",
        "outputs": [{"internalType": "address[]", "name": "", "type": "address[]"}],
        "stateMutability": "view",
        "type": "function",
    }
]

ERC20_ABI = [
    {
        "inputs": [],
        "name": "symbol",
        "outputs": [{"internalType": "string", "name": "", "type": "string"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "decimals",
        "outputs": [{"internalType": "uint8", "name": "", "type": "uint8"}],
        "stateMutability": "view",
        "type": "function",
    },
]


def default_cache_path() -> Path:
    return Path(os.getenv("AAVE_RESERVE_CACHE_FILE", "flashloan/srcs/runtime/cache/aave_reserve_assets.json"))


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_time(value: str) -> Optional[datetime]:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def read_cache(path: Path) -> Optional[dict]:
    try:
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def cache_is_fresh(cache: dict, refresh_seconds: int) -> bool:
    refreshed_at = parse_time(str(cache.get("refreshed_at", "")))
    if not refreshed_at:
        return False
    age = (datetime.now(timezone.utc) - refreshed_at.astimezone(timezone.utc)).total_seconds()
    return age < refresh_seconds


def write_cache(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f"{path.suffix}.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")
    tmp.replace(path)


def normalize_binance_symbol(token_symbol: str) -> Optional[str]:
    symbol = token_symbol.strip().upper()
    if not symbol:
        return None
    aliases = {
        "WAVAX": "AVAXUSDT",
        "WETH": "ETHUSDT",
        "WETH.E": "ETHUSDT",
        "WBTC": "BTCUSDT",
        "WBTC.E": "BTCUSDT",
        "BTC.B": "BTCUSDT",
        "AAVE.E": "AAVEUSDT",
        "USDC.E": "USDCUSDT",
    }
    if symbol in aliases:
        return aliases[symbol]
    if "." in symbol:
        symbol = symbol.split(".", 1)[0]
    return f"{symbol}USDT"


def fetch_aave_reserve_assets(rpc_url: str, pool_address: str) -> list[dict]:
    w3 = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": 15}))
    pool = w3.eth.contract(address=Web3.to_checksum_address(pool_address), abi=POOL_ABI)
    reserve_addresses = pool.functions.getReservesList().call()
    assets = []
    for address in reserve_addresses:
        token = w3.eth.contract(address=Web3.to_checksum_address(address), abi=ERC20_ABI)
        try:
            token_symbol = token.functions.symbol().call()
            decimals = int(token.functions.decimals().call())
        except Exception:
            continue
        binance_symbol = normalize_binance_symbol(str(token_symbol))
        if not binance_symbol:
            continue
        assets.append(
            {
                "token_symbol": str(token_symbol),
                "binance_symbol": binance_symbol,
                "token_address": Web3.to_checksum_address(address),
                "decimals": decimals,
            }
        )
    return assets


def load_aave_reserve_assets(
    rpc_url: str,
    pool_address: str,
    cache_path: Path | None = None,
    refresh_seconds: int | None = None,
) -> list[dict]:
    cache_path = cache_path or default_cache_path()
    refresh_seconds = refresh_seconds or int(os.getenv("AAVE_RESERVE_CACHE_SECONDS", "3600"))
    cache = read_cache(cache_path)
    if cache and cache_is_fresh(cache, refresh_seconds):
        return list(cache.get("assets") or [])
    if not rpc_url or not pool_address:
        return list((cache or {}).get("assets") or [])
    assets = fetch_aave_reserve_assets(rpc_url, pool_address)
    write_cache(
        cache_path,
        {
            "refreshed_at": now_iso(),
            "pool_address": pool_address,
            "assets": assets,
        },
    )
    return assets


def load_aave_reserve_symbols(
    rpc_url: str,
    pool_address: str,
    supported_symbols: set[str] | None = None,
) -> set[str]:
    symbols = {
        str(asset.get("binance_symbol", "")).upper()
        for asset in load_aave_reserve_assets(rpc_url, pool_address)
        if asset.get("binance_symbol")
    }
    if supported_symbols:
        symbols &= {symbol.upper() for symbol in supported_symbols}
    return symbols


def cached_token_address_for_symbol(symbol: str, cache_path: Path | None = None) -> Optional[str]:
    target = symbol.strip().upper()
    cache = read_cache(cache_path or default_cache_path()) or {}
    for asset in cache.get("assets") or []:
        if str(asset.get("binance_symbol", "")).upper() == target:
            return asset.get("token_address")
    return None
