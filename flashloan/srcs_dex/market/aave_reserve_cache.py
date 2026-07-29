import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from web3 import Web3

SRC_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_AAVE_ORACLE = "0xEBd36016B3eD09D4693Ed4251c67Bd858c3c7C9C"
STABLE_BASE_SYMBOLS = {
    "AUSD",
    "DAI",
    "EURC",
    "FRAX",
    "GHO",
    "LUSD",
    "MAI",
    "SUSDE",
    "TUSD",
    "USDC",
    "USDT",
    "USDP",
    "USDE",
    "USD",
}


POOL_ABI = [
    {
        "inputs": [],
        "name": "getReservesList",
        "outputs": [{"internalType": "address[]", "name": "", "type": "address[]"}],
        "stateMutability": "view",
        "type": "function",
    }
]

POOL_SUPPLY_ABI = [
    {
        "inputs": [{"internalType": "address", "name": "asset", "type": "address"}],
        "name": "getATokenTotalSupply",
        "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function",
    }
]

POOL_RESERVE_DATA_ABI = [
    {
        "inputs": [{"internalType": "address", "name": "asset", "type": "address"}],
        "name": "getReserveData",
        "outputs": [{"internalType": "bytes", "name": "", "type": "bytes"}],
        "stateMutability": "view",
        "type": "function",
    }
]

ORACLE_ABI = [
    {
        "inputs": [{"internalType": "address[]", "name": "assets", "type": "address[]"}],
        "name": "getAssetsPrices",
        "outputs": [{"internalType": "uint256[]", "name": "", "type": "uint256[]"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "BASE_CURRENCY_UNIT",
        "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function",
    },
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
    {
        "inputs": [{"internalType": "address", "name": "account", "type": "address"}],
        "name": "balanceOf",
        "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function",
    },
]


def default_cache_path() -> Path:
    raw = os.getenv("AAVE_RESERVE_CACHE_FILE", "runtime/cache/aave_reserve_assets.json")
    path = Path(raw)
    return path if path.is_absolute() else SRC_ROOT / path


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_rpc_urls(rpc_url: str | list[str] | tuple[str, ...] | None) -> list[str]:
    values: list[str] = []
    if isinstance(rpc_url, (list, tuple)):
        raw_items = list(rpc_url)
    elif isinstance(rpc_url, str):
        raw_items = rpc_url.replace("\n", ",").split(",")
    else:
        raw_items = []
    for item in raw_items:
        candidate = str(item).strip().rstrip("/")
        if candidate and candidate not in values:
            values.append(candidate)
    return values


def web3_for_rpc_url(rpc_url: str, timeout: int = 15) -> Web3:
    if rpc_url.lower().startswith(("ws://", "wss://")):
        return Web3(Web3.WebsocketProvider(rpc_url, websocket_timeout=timeout))
    return Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": timeout}))


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


def normalize_token_symbol(token_symbol: str) -> str:
    symbol = str(token_symbol or "").strip().upper()
    if not symbol:
        return ""
    if "." in symbol:
        symbol = symbol.split(".", 1)[0]
    return symbol


def decode_available_liquidity(raw: bytes) -> int:
    if len(raw) < 13 * 32:
        return 0
    return int.from_bytes(raw[12 * 32 : 13 * 32], "big")


def is_stable_token_symbol(token_symbol: str) -> bool:
    return normalize_token_symbol(token_symbol) in STABLE_BASE_SYMBOLS


def fetch_aave_reserve_assets(
    rpc_url: str,
    pool_address: str,
    oracle_address: str = DEFAULT_AAVE_ORACLE,
) -> list[dict]:
    w3 = web3_for_rpc_url(rpc_url, timeout=15)
    pool = w3.eth.contract(address=Web3.to_checksum_address(pool_address), abi=POOL_ABI)
    oracle = w3.eth.contract(address=Web3.to_checksum_address(oracle_address), abi=ORACLE_ABI)
    reserve_addresses = pool.functions.getReservesList().call()
    reserve_meta: list[dict] = []
    for address in reserve_addresses:
        token = w3.eth.contract(address=Web3.to_checksum_address(address), abi=ERC20_ABI)
        try:
            token_symbol = token.functions.symbol().call()
            decimals = int(token.functions.decimals().call())
        except Exception:
            continue
        token_symbol = str(token_symbol)
        binance_symbol = normalize_binance_symbol(token_symbol)
        if not binance_symbol:
            continue
        reserve_meta.append(
            {
                "token_symbol": token_symbol,
                "binance_symbol": binance_symbol,
                "token_address": Web3.to_checksum_address(address),
                "decimals": decimals,
                "is_stable": is_stable_token_symbol(token_symbol),
            }
        )

    if not reserve_meta:
        return []

    addresses = [item["token_address"] for item in reserve_meta]
    prices = [int(value) for value in oracle.functions.getAssetsPrices(addresses).call()]
    try:
        base_unit = int(oracle.functions.BASE_CURRENCY_UNIT().call())
    except Exception:
        base_unit = 1

    assets: list[dict] = []
    for item, price in zip(reserve_meta, prices):
        token = w3.eth.contract(address=Web3.to_checksum_address(item["token_address"]), abi=ERC20_ABI)
        try:
            available_liquidity = int(token.functions.balanceOf(Web3.to_checksum_address(pool_address)).call())
        except Exception:
            available_liquidity = 0
        try:
            selector = Web3.keccak(text="getReserveData(address)")[:4].hex()[2:]
            asset_arg = Web3.to_hex(Web3.to_bytes(hexstr=item["token_address"]))[2:].rjust(64, "0")
            raw_reserve_data = w3.eth.call(
                {
                    "to": Web3.to_checksum_address(pool_address),
                    "data": f"0x{selector}{asset_arg}",
                }
            )
            reserve_data_liquidity = decode_available_liquidity(raw_reserve_data)
        except Exception:
            reserve_data_liquidity = 0
        try:
            a_token_supply = int(pool.functions.getATokenTotalSupply(item["token_address"]).call())
        except Exception:
            a_token_supply = 0
        liquidity_units = max(available_liquidity, a_token_supply, reserve_data_liquidity)
        depth_score_usd = (liquidity_units * float(price)) / max(1.0, float(base_unit) * (10 ** item["decimals"]))
        assets.append(
            {
                **item,
                "oracle_price": float(price) / max(1.0, float(base_unit)),
                "available_liquidity": available_liquidity,
                "reserve_data_liquidity": reserve_data_liquidity,
                "a_token_total_supply": a_token_supply,
                "depth_score_usd": depth_score_usd,
            }
        )
    deduped: dict[str, dict] = {}
    for asset in sorted(assets, key=lambda row: float(row.get("depth_score_usd") or 0.0), reverse=True):
        symbol = str(asset.get("binance_symbol", "")).upper()
        if not symbol:
            continue
        if symbol not in deduped:
            deduped[symbol] = asset
    return list(deduped.values())


def load_aave_reserve_assets(
    rpc_url: str | list[str] | tuple[str, ...],
    pool_address: str,
    cache_path: Path | None = None,
    refresh_seconds: int | None = None,
    limit: int | None = None,
    exclude_stables: bool = False,
) -> list[dict]:
    cache_path = cache_path or default_cache_path()
    refresh_seconds = int(os.getenv("AAVE_RESERVE_CACHE_SECONDS", "3600")) if refresh_seconds is None else int(refresh_seconds)
    cache = read_cache(cache_path)
    cache_assets = list((cache or {}).get("assets") or [])
    cache_version = int(cache.get("schema_version") or 0) if cache else 0
    cache_is_usable = bool(cache_assets) and cache_version >= 4 and all("depth_score_usd" in asset for asset in cache_assets[: min(3, len(cache_assets))])
    if cache and cache_is_fresh(cache, refresh_seconds) and cache_is_usable:
        assets = cache_assets
        if exclude_stables:
            assets = [asset for asset in assets if not bool(asset.get("is_stable"))]
        return assets[:limit] if limit else assets
    if not pool_address:
        assets = list((cache or {}).get("assets") or [])
        if exclude_stables:
            assets = [asset for asset in assets if not bool(asset.get("is_stable"))]
        return assets[:limit] if limit else assets
    rpc_candidates = parse_rpc_urls(rpc_url)
    if not rpc_candidates:
        assets = list((cache or {}).get("assets") or [])
        if exclude_stables:
            assets = [asset for asset in assets if not bool(asset.get("is_stable"))]
        return assets[:limit] if limit else assets
    last_error: Exception | None = None
    for candidate in rpc_candidates:
        try:
            assets = fetch_aave_reserve_assets(candidate, pool_address)
            if exclude_stables:
                assets = [asset for asset in assets if not bool(asset.get("is_stable"))]
            selected = assets[:limit] if limit else assets
            write_cache(
                cache_path,
                {
                    "schema_version": 4,
                    "refreshed_at": now_iso(),
                    "pool_address": pool_address,
                    "rpc_url": candidate,
                    "assets": assets,
                    "selected": selected,
                },
            )
            return selected
        except Exception as exc:
            last_error = exc
            continue
    if cache:
        assets = list(cache.get("assets") or [])
        if exclude_stables and not assets:
            assets = list(cache.get("selected") or [])
        if exclude_stables:
            assets = [asset for asset in assets if not bool(asset.get("is_stable"))]
        return assets[:limit] if limit else assets
    if last_error:
        raise last_error
    return []


def load_aave_reserve_symbol_list(
    rpc_url: str | list[str] | tuple[str, ...],
    pool_address: str,
    limit: int = 1000,
    exclude_stables: bool = True,
) -> list[str]:
    return list(
        dict.fromkeys(
            [
                str(asset.get("binance_symbol", "")).upper()
                for asset in load_aave_reserve_assets(
                    rpc_url,
                    pool_address,
                    limit=limit,
                    exclude_stables=exclude_stables,
                )
                if asset.get("binance_symbol")
            ]
        )
    )


def load_aave_reserve_symbols(
    rpc_url: str,
    pool_address: str,
    supported_symbols: set[str] | None = None,
    limit: int = 1000,
) -> set[str]:
    symbols = set(load_aave_reserve_symbol_list(rpc_url, pool_address, limit=limit))
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
