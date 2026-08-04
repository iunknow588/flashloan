import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.request import Request, urlopen

from web3 import Web3

from execution.dex_costs import TRADER_JOE_V2_ROUTER, USDC
from market.aave_reserve_cache import (
    ERC20_ABI,
    cache_is_fresh,
    is_stable_token_symbol,
    normalize_binance_symbol,
    parse_rpc_urls,
    web3_for_rpc_url,
    write_cache,
)


SRC_ROOT = Path(__file__).resolve().parents[1]
ROUTER_FACTORY_ABI = [
    {
        "inputs": [],
        "name": "factory",
        "outputs": [{"internalType": "address", "name": "", "type": "address"}],
        "stateMutability": "view",
        "type": "function",
    }
]
PAIR_CREATED_TOPIC = Web3.keccak(text="PairCreated(address,address,address,uint256)").hex()
PAIR_ABI = [
    {
        "inputs": [],
        "name": "token0",
        "outputs": [{"internalType": "address", "name": "", "type": "address"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "token1",
        "outputs": [{"internalType": "address", "name": "", "type": "address"}],
        "stateMutability": "view",
        "type": "function",
    },
]
DEFAULT_STABLE_TOKENS = {
    "USDC": USDC,
    "USDT": "0x9702230A8Ea53601f5cD2dc00fDBc13d4dF4A8c7",
    "USDC.e": "0xA7D7079b0FEaD91F3e65f86E8915Cb59c1a4C664",
    "USDT.e": "0xc7198437980c041c805A1EDcbA50c1Ce5db95118",
    "DAI.e": "0xd586E7F844cEa2F87f50152665BCbc2C279D8d70",
}
STABLE_BASE_SYMBOLS = {"USDC", "USDT", "DAI", "FRAX", "MIM", "USDE", "USD", "AUSD"}


def default_cache_path() -> Path:
    raw = os.getenv(
        "DEX_STABLE_TARGET_CACHE_FILE",
        "runtime/cache/dex_stable_targets.json",
    )
    path = Path(raw)
    return path if path.is_absolute() else SRC_ROOT / path


def default_usdc_cache_path() -> Path:
    raw = os.getenv("DEX_USDC_TARGET_CACHE_FILE", "runtime/cache/dex_usdc_targets.json")
    path = Path(raw)
    return path if path.is_absolute() else SRC_ROOT / path


def default_borrow_cache_path() -> Path:
    raw = os.getenv("DEX_BORROW_TARGET_CACHE_FILE", "runtime/cache/dex_borrow_targets.json")
    path = Path(raw)
    return path if path.is_absolute() else SRC_ROOT / path


def parse_stable_tokens(raw: str | None = None) -> dict[str, str]:
    text = (raw if raw is not None else os.getenv("DEX_TARGET_STABLE_TOKENS", "")).strip()
    if not text:
        return dict(DEFAULT_STABLE_TOKENS)
    tokens: dict[str, str] = {}
    for index, part in enumerate(text.replace("\n", ",").split(","), start=1):
        item = part.strip()
        if not item:
            continue
        if ":" in item:
            label, address = item.split(":", 1)
            label = label.strip() or f"STABLE{index}"
        else:
            label, address = f"STABLE{index}", item
        tokens[label] = Web3.to_checksum_address(address.strip())
    return tokens


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def read_cache(path: Path) -> Optional[dict]:
    try:
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def topic_for_address(address: str) -> str:
    return "0x" + Web3.to_checksum_address(address)[2:].lower().rjust(64, "0")


def event_pair_address(log: dict) -> str:
    data = log.get("data")
    if isinstance(data, bytes):
        raw = data.hex()
    else:
        raw = str(data)
    raw = raw[2:] if raw.startswith("0x") else raw
    if len(raw) < 64:
        return ""
    return Web3.to_checksum_address("0x" + raw[-40:])


def token_meta(w3: Web3, address: str) -> Optional[dict]:
    token = w3.eth.contract(address=Web3.to_checksum_address(address), abi=ERC20_ABI)
    try:
        token_symbol = str(token.functions.symbol().call())
        decimals = int(token.functions.decimals().call())
    except Exception:
        return None
    binance_symbol = normalize_binance_symbol(token_symbol)
    if not binance_symbol:
        return None
    return {
        "token_symbol": token_symbol,
        "binance_symbol": binance_symbol,
        "token_address": Web3.to_checksum_address(address),
        "decimals": decimals,
    }


def fetch_json(url: str) -> object:
    with urlopen(Request(url, headers={"User-Agent": "flashloan-observer/1.0"}), timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def fetch_dexscreener_stable_pool_assets(stable_tokens: dict[str, str]) -> list[dict]:
    by_symbol: dict[str, dict] = {}
    for stable_label, stable_address in stable_tokens.items():
        try:
            payload = fetch_json(f"https://api.dexscreener.com/token-pairs/v1/avalanche/{stable_address}")
        except Exception:
            continue
        if not isinstance(payload, list):
            continue
        stable_address_lower = stable_address.lower()
        for pair in payload:
            if not isinstance(pair, dict):
                continue
            base = pair.get("baseToken") or {}
            quote = pair.get("quoteToken") or {}
            base_address = str(base.get("address") or "").lower()
            quote_address = str(quote.get("address") or "").lower()
            other = quote if base_address == stable_address_lower else base if quote_address == stable_address_lower else None
            if not other:
                continue
            token_symbol = str(other.get("symbol") or "")
            normalized = token_symbol.strip().upper().split(".", 1)[0]
            if normalized in STABLE_BASE_SYMBOLS or is_stable_token_symbol(token_symbol):
                continue
            binance_symbol = normalize_binance_symbol(token_symbol)
            token_address = str(other.get("address") or "").strip()
            if not binance_symbol or not token_address:
                continue
            existing = by_symbol.setdefault(
                binance_symbol,
                {
                    "token_symbol": token_symbol,
                    "binance_symbol": binance_symbol,
                    "token_address": Web3.to_checksum_address(token_address),
                    "decimals": 0,
                    "via_stables": [],
                    "source": "dexscreener",
                },
            )
            if stable_label not in existing["via_stables"]:
                existing["via_stables"].append(stable_label)
    return sorted(by_symbol.values(), key=lambda item: item["binance_symbol"])


def borrow_tokens_from_assets(assets: list[dict]) -> dict[str, str]:
    tokens: dict[str, str] = {}
    for index, asset in enumerate(assets, start=1):
        address = str(asset.get("token_address") or "").strip()
        if not address:
            continue
        label = str(asset.get("binance_symbol") or asset.get("token_symbol") or f"BORROW{index}").upper()
        tokens[label] = Web3.to_checksum_address(address)
    return tokens


def fetch_dexscreener_borrow_pool_assets(borrow_tokens: dict[str, str]) -> list[dict]:
    ignored_addresses = {address.lower() for address in borrow_tokens.values()}
    by_symbol: dict[str, dict] = {}
    for borrow_label, borrow_address in borrow_tokens.items():
        try:
            payload = fetch_json(f"https://api.dexscreener.com/token-pairs/v1/avalanche/{borrow_address}")
        except Exception:
            continue
        if not isinstance(payload, list):
            continue
        borrow_address_lower = borrow_address.lower()
        for pair in payload:
            if not isinstance(pair, dict):
                continue
            base = pair.get("baseToken") or {}
            quote = pair.get("quoteToken") or {}
            base_address = str(base.get("address") or "").lower()
            quote_address = str(quote.get("address") or "").lower()
            other = quote if base_address == borrow_address_lower else base if quote_address == borrow_address_lower else None
            if not other:
                continue
            token_address = str(other.get("address") or "").strip()
            if not token_address or token_address.lower() in ignored_addresses:
                continue
            token_symbol = str(other.get("symbol") or "")
            if is_stable_token_symbol(token_symbol):
                continue
            binance_symbol = normalize_binance_symbol(token_symbol)
            if not binance_symbol:
                continue
            existing = by_symbol.setdefault(
                binance_symbol,
                {
                    "token_symbol": token_symbol,
                    "binance_symbol": binance_symbol,
                    "token_address": Web3.to_checksum_address(token_address),
                    "decimals": 0,
                    "via_borrows": [],
                    "source": "dexscreener",
                },
            )
            if borrow_label not in existing["via_borrows"]:
                existing["via_borrows"].append(borrow_label)
    return sorted(by_symbol.values(), key=lambda item: item["binance_symbol"])


def pair_token_addresses(w3: Web3, pair_address: str) -> tuple[str, str] | None:
    pair = w3.eth.contract(address=Web3.to_checksum_address(pair_address), abi=PAIR_ABI)
    try:
        return (
            Web3.to_checksum_address(pair.functions.token0().call()),
            Web3.to_checksum_address(pair.functions.token1().call()),
        )
    except Exception:
        return None

