"""Robinhood Chain DEX arbitrage configuration.

Only official chain-level contracts are hard-coded here. DEX factories,
routers, quoters, pools, and non-WETH token addresses must come from verified
static tables or the market registry.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Any

from core.chain_data_registry import chain_contracts, chain_data_by_network
from dex.intent_trade.chains.common.routes import (
    all_unordered_pairs,
    best_edges_by_pair,
    pair_key,
    triangle_routes,
)
from dex.intent_trade.chains.robinhood.shared import (
    ROBINHOOD_CHAIN_ID as CHAIN_ID,
    ROBINHOOD_NETWORK as NETWORK,
    ROBINHOOD_TESTNET_CHAIN_ID as TESTNET_CHAIN_ID,
    ROBINHOOD_TESTNET_NETWORK as TESTNET_NETWORK,
)


def _chain_row(network: str) -> dict[str, Any]:
    try:
        row = chain_data_by_network(network) or {}
    except Exception:
        return {}
    return row if isinstance(row, dict) else {}


def _chain_value(network: str, key: str, *env_names: str) -> str:
    for name in env_names:
        value = str(os.getenv(name, "") or "").strip()
        if value:
            return value
    row = _chain_row(network)
    return str(row.get(key) or "").strip()


def _chain_contract_value(network: str, key: str, *env_names: str) -> str:
    for name in env_names:
        value = str(os.getenv(name, "") or "").strip()
        if value:
            return value
    try:
        contracts = chain_contracts(network) or {}
    except Exception:
        contracts = {}
    return str(contracts.get(key) or "").strip()


def chain_contract_value(network: str, key: str, *env_names: str) -> str:
    """Read a chain-owned contract from its registry, with env override."""

    return _chain_contract_value(network, key, *env_names)


NATIVE_SYMBOL = _chain_value("robinhood", "native_symbol", "ROBINHOOD_NATIVE_SYMBOL") or "ETH"
EXPLORER_URL = _chain_value("robinhood", "explorer_url", "ROBINHOOD_EXPLORER_URL")
TESTNET_EXPLORER_URL = _chain_value("robinhood-testnet", "explorer_url", "ROBINHOOD_TESTNET_EXPLORER_URL")
WETH_ADDRESS = _chain_contract_value("robinhood", "wrapped_native", "ROBINHOOD_WETH_ADDRESS", "ROBINHOOD_WRAPPED_NATIVE")
MULTICALL_ADDRESS = _chain_contract_value("robinhood", "multicall3", "ROBINHOOD_MULTICALL_ADDRESS")
TESTNET_WETH_ADDRESS = _chain_contract_value("robinhood-testnet", "wrapped_native", "ROBINHOOD_TESTNET_WETH_ADDRESS", "ROBINHOOD_TESTNET_WRAPPED_NATIVE")
TESTNET_MULTICALL_ADDRESS = _chain_contract_value("robinhood-testnet", "multicall3", "ROBINHOOD_TESTNET_MULTICALL_ADDRESS")
PERMIT2_ADDRESS = _chain_contract_value("robinhood", "permit2", "ROBINHOOD_PERMIT2_ADDRESS")
V4_POOL_MANAGER_ADDRESS = _chain_contract_value("robinhood", "v4_pool_manager", "ROBINHOOD_V4_POOL_MANAGER_ADDRESS")
V4_QUOTER_ADDRESS = _chain_contract_value("robinhood", "v4_quoter", "ROBINHOOD_V4_QUOTER_ADDRESS")
POSITION_MANAGER_ADDRESS = _chain_contract_value("robinhood", "position_manager", "ROBINHOOD_POSITION_MANAGER_ADDRESS")
V4_UNIVERSAL_ROUTER_ADDRESS = _chain_contract_value("robinhood", "universal_router", "ROBINHOOD_V4_UNIVERSAL_ROUTER_ADDRESS")
MORPHO_BLUE_CORE_ADDRESS = _chain_contract_value("robinhood", "morpho_blue_core", "ROBINHOOD_MORPHO_BLUE_CORE_ADDRESS")
V3_FACTORY_ADDRESS = _chain_contract_value("robinhood", "v3_factory", "ROBINHOOD_V3_FACTORY_ADDRESS")
V3_QUOTER_ADDRESS = _chain_contract_value("robinhood", "v3_quoter", "ROBINHOOD_V3_QUOTER_ADDRESS", "ROBINHOOD_UNISWAP_V3_QUOTER_ADDRESS")
V3_ROUTER_ADDRESS = _chain_contract_value("robinhood", "v3_router", "ROBINHOOD_V3_ROUTER_ADDRESS", "ROBINHOOD_UNISWAP_V3_ROUTER_ADDRESS")
UNISWAP_V2_FACTORY_ADDRESS = _chain_contract_value("robinhood", "v2_factory", "ROBINHOOD_UNISWAP_V2_FACTORY_ADDRESS")
UNISWAP_V2_POOL_ADDRESS = _chain_contract_value("robinhood", "uniswap_v2_pool", "ROBINHOOD_UNISWAP_V2_POOL_ADDRESS")

TESTNET_PERMIT2_ADDRESS = _chain_contract_value("robinhood-testnet", "permit2", "ROBINHOOD_TESTNET_PERMIT2_ADDRESS")
TESTNET_V4_POOL_MANAGER_ADDRESS = _chain_contract_value("robinhood-testnet", "v4_pool_manager", "ROBINHOOD_TESTNET_V4_POOL_MANAGER_ADDRESS")
TESTNET_V4_QUOTER_ADDRESS = _chain_contract_value("robinhood-testnet", "v4_quoter", "ROBINHOOD_TESTNET_V4_QUOTER_ADDRESS")
TESTNET_POSITION_MANAGER_ADDRESS = _chain_contract_value("robinhood-testnet", "position_manager", "ROBINHOOD_TESTNET_POSITION_MANAGER_ADDRESS")
TESTNET_V4_UNIVERSAL_ROUTER_ADDRESS = _chain_contract_value("robinhood-testnet", "universal_router", "ROBINHOOD_TESTNET_V4_UNIVERSAL_ROUTER_ADDRESS")

DEFAULT_RPC_URLS: tuple[str, ...] = tuple()
DEFAULT_TESTNET_RPC_URLS: tuple[str, ...] = tuple()
DEFAULT_WSS_RPC_URLS: tuple[str, ...] = tuple()


def _split_env_urls(*names: str) -> tuple[str, ...]:
    urls: list[str] = []
    for name in names:
        for item in os.getenv(name, "").split(","):
            value = item.strip()
            if value and value not in urls:
                urls.append(value)
    return tuple(urls)


def rpc_urls_from_env(default: tuple[str, ...] | None = None) -> tuple[str, ...]:
    env_urls = _split_env_urls(
            "ROBINHOOD_MAINNET_RPC_URLS",
            "ROBINHOOD_RPCS",
            "ROBINHOOD_RPC_URLS",
            "ROBINHOOD_RPC_URL",
            "ROBINHOOD_RPC",
            "ROBINHOOD_ALCHEMY_RPC_URL",
            "ROBINHOOD_ALCHEMY_RPC",
            "ROBINHOOD_MAINNET_RPC_URL",
            "ROBINHOOD_MAINNET_RPC",
        )
    if env_urls:
        return env_urls
    if default:
        return tuple(default)
    row = _chain_row("robinhood")
    rpc_urls = tuple(
        str(url).strip()
        for url in row.get("rpc_urls") or []
        if str(url).strip()
    )
    rpc_url = _chain_value("robinhood", "rpc_url", "ROBINHOOD_RPC_URL")
    if rpc_url and rpc_url not in rpc_urls:
        rpc_urls = (*rpc_urls, rpc_url)
    return rpc_urls


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


def testnet_rpc_urls_from_env(default: tuple[str, ...] | None = None) -> tuple[str, ...]:
    env_urls = _split_env_urls(
            "ROBINHOOD_TESTNET_RPCS",
            "ROBINHOOD_TESTNET_RPC_URLS",
            "ROBINHOOD_TESTNET_RPC_URL",
            "ROBINHOOD_TESTNET_RPC",
            "ROBINHOOD_TESTNET_ALCHEMY_RPC_URL",
            "ROBINHOOD_TESTNET_ALCHEMY_RPC",
        )
    if env_urls:
        return env_urls
    if default:
        return tuple(default)
    row = _chain_row("robinhood-testnet")
    rpc_urls = tuple(
        str(url).strip()
        for url in row.get("rpc_urls") or []
        if str(url).strip()
    )
    rpc_url = _chain_value("robinhood-testnet", "rpc_url", "ROBINHOOD_TESTNET_RPC_URL")
    if rpc_url and rpc_url not in rpc_urls:
        rpc_urls = (*rpc_urls, rpc_url)
    return rpc_urls


def wss_urls_from_env(default: tuple[str, ...] | None = None) -> tuple[str, ...]:
    env_urls = _split_env_urls("ROBINHOOD_WSS", "ROBINHOOD_WS_URLS", "ROBINHOOD_WS_URL", "ROBINHOOD_WS")
    if env_urls:
        return env_urls
    if default:
        return tuple(default)
    wss_url = _chain_value("robinhood", "wss_url", "ROBINHOOD_WSS")
    return (wss_url,) if wss_url else ()


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
