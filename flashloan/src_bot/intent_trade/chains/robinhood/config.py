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
from intent_trade.chains.robinhood.shared import (
    ROBINHOOD_CHAIN_ID as CHAIN_ID,
    ROBINHOOD_NETWORK as NETWORK,
    ROBINHOOD_TESTNET_CHAIN_ID as TESTNET_CHAIN_ID,
    ROBINHOOD_TESTNET_NETWORK as TESTNET_NETWORK,
)


NATIVE_SYMBOL = "ETH"
EXPLORER_URL = "https://robinhoodchain.blockscout.com"
TESTNET_EXPLORER_URL = "https://explorer.testnet.chain.robinhood.com"
WETH_ADDRESS = "0x0Bd7D308f8E1639FAb988df18A8011f41EAcAD73"
MULTICALL_ADDRESS = "0x2cAC2D899eCC914d704FeaAE33ac1bF36277DaD1"
TESTNET_WETH_ADDRESS = "0x7943e237c7F95DA44E0301572D358911207852Fa"
TESTNET_MULTICALL_ADDRESS = "0xa432504b6F04Cafe775b09D8AA92e8dbe41Ec7a8"
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


def v3_quoter_addresses_from_env(default: tuple[str, ...] = ()) -> tuple[str, ...]:
    return (
        _split_env_urls(
            "ROBINHOOD_V3_QUOTER_ADDRESSES",
            "ROBINHOOD_V3_QUOTER_ADDRESS",
            "ROBINHOOD_UNISWAP_V3_QUOTER_ADDRESS",
            "DEX_V3_QUOTER_ADDRESSES",
            "DEX_V3_QUOTER_ADDRESS",
        )
        or default
    )


def v3_quoter_address_from_env(default: str = "") -> str:
    addresses = v3_quoter_addresses_from_env((default,))
    return addresses[0] if addresses else default


def default_v3_quoter_address(default: str = "") -> str:
    return v3_quoter_address_from_env(default)


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
TESTNET_TOKENS: dict[str, TokenConfig] = {
    "WETH": TokenConfig("WETH", TESTNET_WETH_ADDRESS, 18, source="official_testnet_protocol_contracts"),
}

DEXES: tuple[DexConfig, ...] = ()


def stable_symbols() -> set[str]:
    return {symbol for symbol, token in TOKENS.items() if token.stable}


def token_symbols() -> list[str]:
    return list(TOKENS)


def token_pairs() -> list[tuple[str, str]]:
    return all_unordered_pairs(token_symbols())
