"""BNB Chain arbitrage configuration and static route helpers."""

from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Any
from core.chain_data_registry import chain_data_by_network
from dex.intent_trade.chains.common.routes import (
    all_unordered_pairs,
    best_edges_by_pair,
    pair_key,
    triangle_routes,
)


CHAIN_ID = 56
NETWORK = "bnb"


def _chain_row() -> dict[str, Any]:
    try:
        row = chain_data_by_network("bnb") or {}
    except Exception:
        return {}
    return row if isinstance(row, dict) else {}


def _chain_rpc_urls() -> tuple[str, ...]:
    row = _chain_row()
    rpc = row.get("rpc") if isinstance(row.get("rpc"), dict) else {}
    if rpc:
        primary = str(rpc.get("primary") or "").strip()
        fallbacks = rpc.get("fallbacks") or []
        values = [primary, *(str(url).strip() for url in fallbacks if str(url).strip())]
    else:
        values = row.get("rpc_urls") or row.get("officialRpcUrls") or [
            row.get("rpc_url"),
            row.get("officialRpc"),
        ]
    return tuple(dict.fromkeys(str(url).strip() for url in values if str(url).strip()))


DEFAULT_RPC_URLS = _chain_rpc_urls()
DEFAULT_LOG_RPC_URLS = _chain_rpc_urls()
DEFAULT_WSS_RPC_URLS: tuple[str, ...] = ()


def _split_env_urls(*names: str) -> tuple[str, ...]:
    urls: list[str] = []
    for name in names:
        for item in os.getenv(name, "").split(","):
            value = item.strip()
            if value and value not in urls:
                urls.append(value)
    return tuple(urls)


def rpc_urls_from_env(default: tuple[str, ...] | None = None) -> tuple[str, ...]:
    env_urls = _split_env_urls("BNB_RPCS", "BNB_RPC_URLS", "BSC_RPCS", "BSC_RPC_URLS", "BNB_RPC", "BSC_RPC")
    fallback = tuple(default or DEFAULT_RPC_URLS)
    if env_urls:
        return tuple(dict.fromkeys([*env_urls, *fallback]))
    return fallback


def log_rpc_urls_from_env(default: tuple[str, ...] | None = None) -> tuple[str, ...]:
    env_urls = _split_env_urls(
        "BNB_LOG_RPCS",
        "BNB_LOG_RPC_URLS",
        "BSC_LOG_RPCS",
        "BSC_LOG_RPC_URLS",
        "BNB_EVENT_RPCS",
        "BNB_EVENT_RPC_URLS",
    )
    fallback = tuple(default or DEFAULT_LOG_RPC_URLS)
    if env_urls:
        return tuple(dict.fromkeys([*env_urls, *fallback]))
    return fallback


@dataclass(frozen=True)
class TokenConfig:
    symbol: str
    address: str
    decimals: int
    binance_symbol: str | None = None
    stable: bool = False


@dataclass(frozen=True)
class DexConfig:
    name: str
    protocol: str
    factory: str
    router: str | None = None
    quoter: str | None = None
    fee_bps: int | None = None
    fee_tiers: tuple[int, ...] = ()


TOKENS: dict[str, TokenConfig] = {
    "USDT": TokenConfig("USDT", "0x55d398326f99059fF775485246999027B3197955", 18, "USDTUSDT", True),
    "USDC": TokenConfig("USDC", "0x8AC76a51cc950d9822D68b83fE1Ad97B32Cd580d", 18, "USDCUSDT", True),
    "WBNB": TokenConfig("WBNB", "0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c", 18, "BNBUSDT"),
    "BTCB": TokenConfig("BTCB", "0x7130d2A12B9BCbFAe4f2634d864A1Ee1Ce3Ead9c", 18, "BTCUSDT"),
    "ETH": TokenConfig("ETH", "0x2170Ed0880ac9A755fd29B2688956BD959F933F8", 18, "ETHUSDT"),
    "BUSD": TokenConfig("BUSD", "0xe9e7CEA3DedcA5984780Bafc599bD69ADd087D56", 18, "BUSDUSDT", True),
    "DAI": TokenConfig("DAI", "0x1AF3F329e8BE154074D8769D1FFa4eE058B1DBc3", 18, "DAIUSDT", True),
    "CAKE": TokenConfig("CAKE", "0x0E09FaBB73Bd3Ade0a17ECC321fD13a19e81cE82", 18, "CAKEUSDT"),
}

DEXES: tuple[DexConfig, ...] = (
    DexConfig(
        name="pancakeswap_v2",
        protocol="v2",
        factory="0xcA143Ce32Fe78f1f7019d7d551a6402fC5350c73",
        router="0x10ED43C718714eb63d5aA57B78B54704E256024E",
        fee_bps=25,
    ),
    DexConfig(
        name="pancakeswap_v3",
        protocol="v3",
        factory="0x0BFbCF9fa4f9C56B0F40a671Ad40E0805A091865",
        router="0x13f4EA83D0bd40E75C8222255bc855a974568Dd4",
        quoter="0x78D78E420Da98ad378D7799bE8f4AF69033EB077",
        fee_tiers=(100, 500, 2500, 10000),
    ),
)

V3_CORE_PAIRS: tuple[tuple[str, str], ...] = (
    ("USDT", "USDC"),
    ("USDT", "WBNB"),
    ("USDT", "BTCB"),
    ("USDT", "ETH"),
    ("USDC", "WBNB"),
    ("USDC", "ETH"),
    ("WBNB", "BTCB"),
    ("WBNB", "ETH"),
    ("WBNB", "CAKE"),
    ("BTCB", "ETH"),
)


def stable_symbols() -> set[str]:
    return {symbol for symbol, token in TOKENS.items() if token.stable}


def token_symbols() -> list[str]:
    return list(TOKENS)


def token_pairs() -> list[tuple[str, str]]:
    return all_unordered_pairs(token_symbols())
