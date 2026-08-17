"""Robinhood Chain DEX arbitrage configuration.

Only official chain-level contracts are hard-coded here. DEX factories,
routers, quoters, pools, and non-WETH token addresses must come from verified
static tables or the market registry.
"""

from __future__ import annotations

from dataclasses import dataclass
import os

from intent_trade.chains.common.routes import (
    all_unordered_pairs,
    best_edges_by_pair,
    pair_key,
    triangle_routes,
)


CHAIN_ID = 4663
TESTNET_CHAIN_ID = 46630
NETWORK = "robinhood"
TESTNET_NETWORK = "robinhood-testnet"
NATIVE_SYMBOL = "ETH"
EXPLORER_URL = "https://robinhoodchain.blockscout.com"
TESTNET_EXPLORER_URL = "https://explorer.testnet.chain.robinhood.com"
WETH_ADDRESS = "0x0Bd7D308f8E1639FAb988df18A8011f41EAcAD73"
MULTICALL_ADDRESS = "0x2cAC2D899eCC914d704FeaAE33ac1bF36277DaD1"
PERMIT2_ADDRESS = "0x000000000022D473030F116dDEE9F6B43aC78BA3"

DEFAULT_RPC_URLS = (
    "https://rpc.mainnet.chain.robinhood.com",
)
DEFAULT_TESTNET_RPC_URLS = (
    "https://rpc.testnet.chain.robinhood.com",
)
DEFAULT_WSS_RPC_URLS = (
    "wss://feed.mainnet.chain.robinhood.com",
)


def _split_env_urls(*names: str) -> tuple[str, ...]:
    urls: list[str] = []
    for name in names:
        for item in os.getenv(name, "").split(","):
            value = item.strip()
            if value and value not in urls:
                urls.append(value)
    return tuple(urls)


def rpc_urls_from_env(default: tuple[str, ...] = DEFAULT_RPC_URLS) -> tuple[str, ...]:
    return (
        _split_env_urls(
            "ROBINHOOD_RPCS",
            "ROBINHOOD_RPC_URLS",
            "ROBINHOOD_RPC_URL",
            "ROBINHOOD_RPC",
            "ROBINHOOD_ALCHEMY_RPC_URL",
            "ROBINHOOD_ALCHEMY_RPC",
            "ROBINHOOD_MAINNET_RPC_URL",
            "ROBINHOOD_MAINNET_RPC",
        )
        or default
    )


def testnet_rpc_urls_from_env(default: tuple[str, ...] = DEFAULT_TESTNET_RPC_URLS) -> tuple[str, ...]:
    return (
        _split_env_urls(
            "ROBINHOOD_TESTNET_RPCS",
            "ROBINHOOD_TESTNET_RPC_URLS",
            "ROBINHOOD_TESTNET_RPC_URL",
            "ROBINHOOD_TESTNET_RPC",
            "ROBINHOOD_TESTNET_ALCHEMY_RPC_URL",
            "ROBINHOOD_TESTNET_ALCHEMY_RPC",
        )
        or default
    )


def wss_urls_from_env(default: tuple[str, ...] = DEFAULT_WSS_RPC_URLS) -> tuple[str, ...]:
    return _split_env_urls("ROBINHOOD_WSS", "ROBINHOOD_WS_URLS", "ROBINHOOD_WS_URL", "ROBINHOOD_WS") or default


@dataclass(frozen=True)
class TokenConfig:
    symbol: str
    address: str
    decimals: int
    stable: bool = False
    source: str = "official"


@dataclass(frozen=True)
class DexConfig:
    name: str
    protocol: str
    factory: str = ""
    router: str = ""
    quoter: str = ""
    source: str = "market_registry_required"


TOKENS: dict[str, TokenConfig] = {
    "WETH": TokenConfig("WETH", WETH_ADDRESS, 18, source="official_protocol_contracts"),
}

DEXES: tuple[DexConfig, ...] = ()


def stable_symbols() -> set[str]:
    return {symbol for symbol, token in TOKENS.items() if token.stable}


def token_symbols() -> list[str]:
    return list(TOKENS)


def token_pairs() -> list[tuple[str, str]]:
    return all_unordered_pairs(token_symbols())
