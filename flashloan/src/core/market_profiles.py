from __future__ import annotations

from typing import Any

import os

from core.chain_data_registry import chain_contracts, chain_data_by_network
from core.market_identity import FEATURE_LIQUIDATION, market_key_from_summary


def _chain_int_value(network: str, env_name: str, fallback: int) -> int:
    raw = str(os.getenv(env_name, "")).strip()
    if raw:
        try:
            return int(raw)
        except ValueError as exc:
            raise ValueError(f"{env_name} must be an integer") from exc
    try:
        value = int((chain_data_by_network(network) or {}).get("chain_id"))
        if value > 0:
            return value
    except (TypeError, ValueError, KeyError):
        pass
    return int(fallback)


def _chain_value(
    network: str,
    key: str,
    env_names: tuple[str, ...],
    fallback: str,
) -> str:
    for name in env_names:
        value = str(os.getenv(name, "")).strip()
        if value:
            return value
    try:
        value = str((chain_data_by_network(network) or {}).get(key) or "").strip()
        if value:
            return value
    except (KeyError, TypeError, ValueError, AttributeError, OSError):
        pass
    try:
        value = str((chain_contracts(network) or {}).get(key) or "").strip()
        if value:
            return value
    except (KeyError, TypeError, ValueError, AttributeError, OSError):
        pass
    return fallback


LEGACY_AVALANCHE_MARKET_ID = "avalanche-aave-v3"
DEFAULT_AVALANCHE_MARKET_ID = "liquidation-43114-aave-v3"
DEFAULT_AVALANCHE_CHAIN_ID = 43114
DEFAULT_AVALANCHE_ROUTER_ADDRESS = "0x60aE616a2155Ee3d9A68541Ba4544862310933d4"
LEGACY_BNB_MARKET_ID = "bnb-venus-core"
DEFAULT_BNB_MARKET_ID = "liquidation-56-venus-core"
DEFAULT_BNB_CHAIN_ID = 56
DEFAULT_BNB_PANCAKE_ROUTER_ADDRESS = "0x10ED43C718714eb63d5aA57B78B54704E256024E"
DEFAULT_ETHEREUM_UNISWAP_V2_ROUTER_ADDRESS = "0x7a250d5630B4cF539739dF2C5dAcb4c659F2488D"
DEFAULT_POLYGON_QUICKSWAP_V2_ROUTER_ADDRESS = "0xa5E0829CaCED8fFDD4De3c43696c57F7D7A678ff"
LEGACY_BNB_DEX_MARKET_ID = "bnb-dex-arbitrage"
DEFAULT_BNB_DEX_MARKET_ID = "dex-arbitrage-56-pancakeswap-v3"
LEGACY_ETHEREUM_DEX_MARKET_ID = "ethereum-dex-arbitrage"
DEFAULT_ETHEREUM_DEX_MARKET_ID = "dex-arbitrage-1-uniswap-v3"
LEGACY_POLYGON_DEX_MARKET_ID = "polygon-dex-arbitrage"
DEFAULT_POLYGON_DEX_MARKET_ID = "dex-arbitrage-137-uniswap-v3"
LEGACY_AVALANCHE_DEX_MARKET_ID = "avalanche-dex-arbitrage"
DEFAULT_AVALANCHE_DEX_MARKET_ID = "dex-arbitrage-43114-uniswap-v3"
LEGACY_COW_DEX_MARKET_ID = "cow-dex-arbitrage"
DEFAULT_COW_DEX_MARKET_ID = "dex-arbitrage-0-cow-protocol"
DEFAULT_ETHEREUM_CHAIN_ID = 1
DEFAULT_POLYGON_CHAIN_ID = 137
DEFAULT_COW_VIRTUAL_CHAIN_ID = 0
LEGACY_ROBINHOOD_BOT_MARKET_ID = "robinhood-liquidation-shadow"
DEFAULT_ROBINHOOD_BOT_MARKET_ID = "liquidation-4663-aave-v3"
LEGACY_ROBINHOOD_TESTNET_BOT_MARKET_ID = "robinhood-testnet-liquidation-shadow"
DEFAULT_ROBINHOOD_TESTNET_BOT_MARKET_ID = "liquidation-46630-morpho-blue"
LEGACY_ROBINHOOD_DEX_MARKET_ID = "robinhood-dex-shadow"
DEFAULT_ROBINHOOD_DEX_MARKET_ID = "dex-arbitrage-4663-uniswap-v3"
LEGACY_ROBINHOOD_TESTNET_DEX_MARKET_ID = "robinhood-testnet-dex-paused"
DEFAULT_ROBINHOOD_TESTNET_DEX_MARKET_ID = "dex-arbitrage-46630-uniswap-v4"
DEFAULT_ROBINHOOD_CHAIN_ID = _chain_int_value("robinhood", "ROBINHOOD_CHAIN_ID", 4663)
DEFAULT_ROBINHOOD_TESTNET_CHAIN_ID = _chain_int_value(
    "robinhood-testnet",
    "ROBINHOOD_TESTNET_CHAIN_ID",
    46630,
)
DEFAULT_ROBINHOOD_WETH_ADDRESS = _chain_value(
    "robinhood",
    "wrapped_native",
    ("ROBINHOOD_WETH_ADDRESS", "ROBINHOOD_WRAPPED_NATIVE"),
    "",
)
DEFAULT_ROBINHOOD_MULTICALL_ADDRESS = _chain_value(
    "robinhood",
    "multicall3",
    ("ROBINHOOD_MULTICALL_ADDRESS",),
    "",
)
DEFAULT_ROBINHOOD_EXPLORER_URL = _chain_value(
    "robinhood",
    "explorer_url",
    ("ROBINHOOD_EXPLORER_URL",),
    "",
)
DEFAULT_ROBINHOOD_TESTNET_EXPLORER_URL = _chain_value(
    "robinhood-testnet",
    "explorer_url",
    ("ROBINHOOD_TESTNET_EXPLORER_URL",),
    "",
)
DEFAULT_ROBINHOOD_RPC_URL = _chain_value(
    "robinhood",
    "rpc_url",
    (
        "ROBINHOOD_RPC_URL",
        "ROBINHOOD_RPC",
        "ROBINHOOD_MAINNET_RPC_URL",
        "ROBINHOOD_MAINNET_RPC",
    ),
    "",
)
DEFAULT_ROBINHOOD_TESTNET_RPC_URL = _chain_value(
    "robinhood-testnet",
    "rpc_url",
    (
        "ROBINHOOD_TESTNET_RPC_URL",
        "ROBINHOOD_TESTNET_RPC",
        "ROBINHOOD_TESTNET_RPC_URLS",
        "ROBINHOOD_TESTNET_RPCS",
    ),
    "",
)
DEFAULT_ROBINHOOD_TESTNET_WETH_ADDRESS = _chain_value(
    "robinhood-testnet",
    "wrapped_native",
    ("ROBINHOOD_TESTNET_WETH_ADDRESS", "ROBINHOOD_TESTNET_WRAPPED_NATIVE"),
    "",
)
LEGACY_ROBINHOOD_MORPHO_MARKET_ID = "robinhood-morpho-blue-flash"
DEFAULT_ROBINHOOD_MORPHO_MARKET_ID = "liquidation-4663-morpho-blue"
LEGACY_ROBINHOOD_DEX_MORPHO_MARKET_ID = "robinhood-dex-morpho-blue-flash"
DEFAULT_ROBINHOOD_DEX_MORPHO_MARKET_ID = "dex-arbitrage-4663-morpho-blue"
DEFAULT_ROBINHOOD_MORPHO_BLUE_CORE_ADDRESS = _chain_value(
    "robinhood",
    "morpho_blue_core",
    ("ROBINHOOD_MORPHO_BLUE_CORE_ADDRESS",),
    "",
)
DEFAULT_ROBINHOOD_UNISWAP_V2_FACTORY_ADDRESS = _chain_value(
    "robinhood",
    "v2_factory",
    ("ROBINHOOD_UNISWAP_V2_FACTORY_ADDRESS",),
    "",
)

MARKET_TYPE_NATIVE_CHAIN = "native_chain"
MARKET_TYPE_SHADOW_CHAIN = "shadow_chain"
MARKET_TYPE_EXECUTION_RAIL = "execution_rail"


KNOWN_EVM_MARKETS: dict[str, dict[str, Any]] = {
    DEFAULT_AVALANCHE_MARKET_ID: {
        "legacy_market_id": LEGACY_AVALANCHE_MARKET_ID,
        "feature": "liquidation",
        "display_name": "Avalanche Aave V3 Liquidation",
        "network": "avalanche",
        "chain_id": DEFAULT_AVALANCHE_CHAIN_ID,
        "native_symbol": "AVAX",
        "protocol": "aave_v3",
        "rpc_env_prefixes": ("AVALANCHE",),
        "observation_only": False,
        "market_type": MARKET_TYPE_NATIVE_CHAIN,
        "migration_type": "config_and_new_executor_deployment",
    },
    "liquidation-56-aave-v3": {
        "legacy_market_id": "bnb-aave-v3",
        "feature": "liquidation",
        "display_name": "BNB Aave V3 Liquidation",
        "network": "bnb",
        "chain_id": DEFAULT_BNB_CHAIN_ID,
        "native_symbol": "BNB",
        "protocol": "aave_v3",
        "rpc_env_prefixes": ("BNB", "BSC"),
        "observation_only": False,
        "market_type": MARKET_TYPE_NATIVE_CHAIN,
        "migration_type": "config_and_new_executor_deployment",
    },
    DEFAULT_BNB_MARKET_ID: {
        "legacy_market_id": LEGACY_BNB_MARKET_ID,
        "feature": "liquidation",
        "display_name": "BNB Venus Core Liquidation",
        "network": "bnb",
        "chain_id": DEFAULT_BNB_CHAIN_ID,
        "native_symbol": "BNB",
        "protocol": "venus_core",
        "rpc_env_prefixes": ("BNB", "BSC"),
        "observation_only": True,
        "market_type": MARKET_TYPE_NATIVE_CHAIN,
        "chain_default": True,
        "migration_type": "config_and_new_executor_deployment",
    },
    "liquidation-137-aave-v3": {
        "legacy_market_id": "polygon-aave-v3-shadow",
        "feature": "liquidation",
        "display_name": "Polygon Aave V3 Liquidation Shadow",
        "network": "polygon",
        "chain_id": DEFAULT_POLYGON_CHAIN_ID,
        "native_symbol": "POL",
        "protocol": "aave_v3",
        "rpc_env_prefixes": ("POLYGON", "MATIC"),
        "observation_only": True,
        "protocol_supported": False,
        "market_type": MARKET_TYPE_NATIVE_CHAIN,
        "chain_default": True,
        "migration_type": "liquidation_protocol_placeholder",
    },
    "liquidation-1-aave-v3": {
        "legacy_market_id": "ethereum-aave-v3-shadow",
        "feature": "liquidation",
        "display_name": "Ethereum Aave V3 Liquidation Shadow",
        "network": "ethereum",
        "chain_id": DEFAULT_ETHEREUM_CHAIN_ID,
        "native_symbol": "ETH",
        "protocol": "aave_v3",
        "rpc_env_prefixes": ("ETHEREUM", "MAINNET"),
        "observation_only": True,
        "protocol_supported": False,
        "market_type": MARKET_TYPE_NATIVE_CHAIN,
        "chain_default": True,
        "migration_type": "liquidation_protocol_placeholder",
    },
    "liquidation-0-cow-protocol": {
        "legacy_market_id": "cow-liquidation-shadow",
        "feature": "liquidation",
        "display_name": "CoW Liquidation Rail Shadow",
        "network": "cow",
        "chain_id": DEFAULT_COW_VIRTUAL_CHAIN_ID,
        "native_symbol": "COW",
        "protocol": "cow_protocol",
        "rpc_env_prefixes": (),
        "observation_only": True,
        "protocol_supported": False,
        "market_type": MARKET_TYPE_EXECUTION_RAIL,
        "chain_default": True,
        "migration_type": "liquidation_execution_rail_placeholder",
    },
    DEFAULT_BNB_DEX_MARKET_ID: {
        "legacy_market_id": LEGACY_BNB_DEX_MARKET_ID,
        "feature": "dex_arbitrage",
        "display_name": "BNB PancakeSwap V3 Arbitrage",
        "network": "bnb",
        "chain_id": DEFAULT_BNB_CHAIN_ID,
        "native_symbol": "BNB",
        "protocol": "pancakeswap_v3",
        "rpc_env_prefixes": ("BNB", "BSC"),
        "observation_only": True,
        "market_type": MARKET_TYPE_NATIVE_CHAIN,
        "chain_default": True,
        "migration_type": "dex_executor_and_pool_verification",
    },
    "dex-arbitrage-56-pancakeswap-v2": {
        "legacy_market_id": "bnb-pancakeswap-v2",
        "feature": "dex_arbitrage",
        "display_name": "BNB PancakeSwap V2 Arbitrage",
        "network": "bnb",
        "chain_id": DEFAULT_BNB_CHAIN_ID,
        "native_symbol": "BNB",
        "protocol": "pancakeswap_v2",
        "rpc_env_prefixes": ("BNB", "BSC"),
        "observation_only": True,
        "market_type": MARKET_TYPE_NATIVE_CHAIN,
        "migration_type": "dex_protocol_placeholder",
    },
    "dex-arbitrage-56-biswap-v2": {
        "legacy_market_id": "bnb-biswap-v2",
        "feature": "dex_arbitrage",
        "display_name": "BNB Biswap V2 Arbitrage",
        "network": "bnb",
        "chain_id": DEFAULT_BNB_CHAIN_ID,
        "native_symbol": "BNB",
        "protocol": "biswap_v2",
        "rpc_env_prefixes": ("BNB", "BSC"),
        "observation_only": True,
        "market_type": MARKET_TYPE_NATIVE_CHAIN,
        "migration_type": "dex_protocol_placeholder",
    },
    DEFAULT_ETHEREUM_DEX_MARKET_ID: {
        "legacy_market_id": LEGACY_ETHEREUM_DEX_MARKET_ID,
        "feature": "dex_arbitrage",
        "display_name": "Ethereum DEX Arbitrage",
        "network": "ethereum",
        "chain_id": DEFAULT_ETHEREUM_CHAIN_ID,
        "native_symbol": "ETH",
        "protocol": "uniswap_v3",
        "rpc_env_prefixes": ("ETHEREUM", "MAINNET"),
        "observation_only": True,
        "market_type": MARKET_TYPE_NATIVE_CHAIN,
        "chain_default": True,
        "migration_type": "dex_single_chain_market_registry",
    },
    "dex-arbitrage-1-uniswap-v2": {
        "legacy_market_id": "ethereum-uniswap-v2",
        "feature": "dex_arbitrage",
        "display_name": "Ethereum Uniswap V2 Arbitrage",
        "network": "ethereum",
        "chain_id": DEFAULT_ETHEREUM_CHAIN_ID,
        "native_symbol": "ETH",
        "protocol": "uniswap_v2",
        "rpc_env_prefixes": ("ETHEREUM", "MAINNET"),
        "observation_only": True,
        "market_type": MARKET_TYPE_NATIVE_CHAIN,
        "migration_type": "dex_protocol_placeholder",
    },
    "dex-arbitrage-1-balancer-v2": {
        "legacy_market_id": "ethereum-balancer-v2",
        "feature": "dex_arbitrage",
        "display_name": "Ethereum Balancer V2 Arbitrage",
        "network": "ethereum",
        "chain_id": DEFAULT_ETHEREUM_CHAIN_ID,
        "native_symbol": "ETH",
        "protocol": "balancer_v2",
        "rpc_env_prefixes": ("ETHEREUM", "MAINNET"),
        "observation_only": True,
        "market_type": MARKET_TYPE_NATIVE_CHAIN,
        "migration_type": "dex_protocol_placeholder",
    },
    DEFAULT_POLYGON_DEX_MARKET_ID: {
        "legacy_market_id": LEGACY_POLYGON_DEX_MARKET_ID,
        "feature": "dex_arbitrage",
        "display_name": "Polygon DEX Arbitrage",
        "network": "polygon",
        "chain_id": DEFAULT_POLYGON_CHAIN_ID,
        "native_symbol": "POL",
        "protocol": "uniswap_v3",
        "rpc_env_prefixes": ("POLYGON", "MATIC"),
        "observation_only": True,
        "market_type": MARKET_TYPE_NATIVE_CHAIN,
        "chain_default": True,
        "migration_type": "dex_single_chain_market_registry",
    },
    "dex-arbitrage-137-quickswap-v2": {
        "legacy_market_id": "polygon-quickswap-v2",
        "feature": "dex_arbitrage",
        "display_name": "Polygon QuickSwap V2 Arbitrage",
        "network": "polygon",
        "chain_id": DEFAULT_POLYGON_CHAIN_ID,
        "native_symbol": "POL",
        "protocol": "quickswap_v2",
        "rpc_env_prefixes": ("POLYGON", "MATIC"),
        "observation_only": True,
        "market_type": MARKET_TYPE_NATIVE_CHAIN,
        "migration_type": "dex_protocol_placeholder",
    },
    "dex-arbitrage-137-balancer-v2": {
        "legacy_market_id": "polygon-balancer-v2",
        "feature": "dex_arbitrage",
        "display_name": "Polygon Balancer V2 Arbitrage",
        "network": "polygon",
        "chain_id": DEFAULT_POLYGON_CHAIN_ID,
        "native_symbol": "POL",
        "protocol": "balancer_v2",
        "rpc_env_prefixes": ("POLYGON", "MATIC"),
        "observation_only": True,
        "market_type": MARKET_TYPE_NATIVE_CHAIN,
        "migration_type": "dex_protocol_placeholder",
    },
    DEFAULT_AVALANCHE_DEX_MARKET_ID: {
        "legacy_market_id": LEGACY_AVALANCHE_DEX_MARKET_ID,
        "feature": "dex_arbitrage",
        "display_name": "Avalanche DEX Arbitrage",
        "network": "avalanche",
        "chain_id": DEFAULT_AVALANCHE_CHAIN_ID,
        "native_symbol": "AVAX",
        "protocol": "uniswap_v3",
        "rpc_env_prefixes": ("AVALANCHE",),
        "observation_only": True,
        "market_type": MARKET_TYPE_NATIVE_CHAIN,
        "chain_default": True,
        "is_default": True,
        "migration_type": "dex_single_chain_market_registry",
    },
    "dex-arbitrage-43114-traderjoe-v2": {
        "legacy_market_id": "avalanche-traderjoe-v2",
        "feature": "dex_arbitrage",
        "display_name": "Avalanche Trader Joe V2 Arbitrage",
        "network": "avalanche",
        "chain_id": DEFAULT_AVALANCHE_CHAIN_ID,
        "native_symbol": "AVAX",
        "protocol": "traderjoe_v2",
        "rpc_env_prefixes": ("AVALANCHE",),
        "observation_only": True,
        "market_type": MARKET_TYPE_NATIVE_CHAIN,
        "migration_type": "dex_protocol_placeholder",
    },
    "dex-arbitrage-43114-curve": {
        "legacy_market_id": "avalanche-curve",
        "feature": "dex_arbitrage",
        "display_name": "Avalanche Curve Arbitrage",
        "network": "avalanche",
        "chain_id": DEFAULT_AVALANCHE_CHAIN_ID,
        "native_symbol": "AVAX",
        "protocol": "curve",
        "rpc_env_prefixes": ("AVALANCHE",),
        "observation_only": True,
        "market_type": MARKET_TYPE_NATIVE_CHAIN,
        "migration_type": "dex_protocol_placeholder",
    },
    DEFAULT_COW_DEX_MARKET_ID: {
        "legacy_market_id": LEGACY_COW_DEX_MARKET_ID,
        "feature": "dex_arbitrage",
        "display_name": "COW Cross-Chain Execution",
        "network": "cow",
        "chain_id": DEFAULT_COW_VIRTUAL_CHAIN_ID,
        "native_symbol": "COW",
        "protocol": "cow_protocol",
        "rpc_env_prefixes": (),
        "observation_only": True,
        "market_type": MARKET_TYPE_EXECUTION_RAIL,
        "migration_type": "dex_cross_chain_execution_rail",
    },
    DEFAULT_ROBINHOOD_DEX_MARKET_ID: {
        "legacy_market_id": LEGACY_ROBINHOOD_DEX_MARKET_ID,
        "feature": "dex_arbitrage",
        "display_name": "Robinhood DEX Shadow",
        "network": "robinhood",
        "chain_id": DEFAULT_ROBINHOOD_CHAIN_ID,
        "native_symbol": "ETH",
        "protocol": "uniswap_v3",
        "rpc_env_prefixes": ("ROBINHOOD_ALCHEMY", "ROBINHOOD", "ROBINHOOD_MAINNET"),
        "wrapped_native_address": DEFAULT_ROBINHOOD_WETH_ADDRESS,
        "multicall3_address": DEFAULT_ROBINHOOD_MULTICALL_ADDRESS,
        "explorer_url": DEFAULT_ROBINHOOD_EXPLORER_URL,
        "observation_only": True,
        "market_type": MARKET_TYPE_SHADOW_CHAIN,
        "chain_default": True,
        "migration_type": "dex_shadow_market_registry",
    },
    "dex-arbitrage-4663-uniswap-v4": {
        "legacy_market_id": "robinhood-dex-v4-shadow",
        "feature": "dex_arbitrage",
        "display_name": "Robinhood Uniswap V4",
        "network": "robinhood",
        "chain_id": DEFAULT_ROBINHOOD_CHAIN_ID,
        "native_symbol": "ETH",
        "protocol": "uniswap_v4",
        "rpc_env_prefixes": ("ROBINHOOD_ALCHEMY", "ROBINHOOD", "ROBINHOOD_MAINNET"),
        "wrapped_native_address": DEFAULT_ROBINHOOD_WETH_ADDRESS,
        "multicall3_address": DEFAULT_ROBINHOOD_MULTICALL_ADDRESS,
        "explorer_url": DEFAULT_ROBINHOOD_EXPLORER_URL,
        "observation_only": True,
        "protocol_supported": True,
        "market_type": MARKET_TYPE_SHADOW_CHAIN,
        "migration_type": "robinhood_mainnet_v4_read_only_quote_supported_execution_deferred",
    },
    DEFAULT_ROBINHOOD_TESTNET_DEX_MARKET_ID: {
        "legacy_market_id": LEGACY_ROBINHOOD_TESTNET_DEX_MARKET_ID,
        "feature": "dex_arbitrage",
        "display_name": "Robinhood Testnet Paused Executor",
        "network": "robinhood-testnet",
        "chain_id": DEFAULT_ROBINHOOD_TESTNET_CHAIN_ID,
        "native_symbol": "ETH",
        "protocol": "uniswap_v4",
        "rpc_env_prefixes": ("ROBINHOOD_TESTNET", "ROBINHOOD_TESTNET_ALCHEMY"),
        "explorer_url": DEFAULT_ROBINHOOD_TESTNET_EXPLORER_URL,
        "observation_only": True,
        "protocol_supported": False,
        "market_type": MARKET_TYPE_NATIVE_CHAIN,
        "chain_default": False,
        "migration_type": "robinhood_testnet_paused_deploy_readback",
    },
    DEFAULT_ROBINHOOD_TESTNET_BOT_MARKET_ID: {
        "legacy_market_id": LEGACY_ROBINHOOD_TESTNET_BOT_MARKET_ID,
        "feature": "liquidation",
        "display_name": "Robinhood Testnet Liquidation Shadow",
        "network": "robinhood-testnet",
        "chain_id": DEFAULT_ROBINHOOD_TESTNET_CHAIN_ID,
        "native_symbol": "ETH",
        "protocol": "morpho_blue",
        "rpc_env_prefixes": ("ROBINHOOD_TESTNET", "ROBINHOOD_TESTNET_ALCHEMY"),
        "explorer_url": DEFAULT_ROBINHOOD_TESTNET_EXPLORER_URL,
        "observation_only": True,
        "protocol_supported": False,
        "market_type": MARKET_TYPE_NATIVE_CHAIN,
        "chain_default": True,
        "migration_type": "robinhood_testnet_liquidation_shadow",
    },
    "dex-arbitrage-4663-uniswap-v2": {
        "legacy_market_id": "robinhood-dex-v2-shadow",
        "feature": "dex_arbitrage",
        "display_name": "Robinhood Uniswap V2 Flash Swap",
        "network": "robinhood",
        "chain_id": DEFAULT_ROBINHOOD_CHAIN_ID,
        "native_symbol": "ETH",
        "protocol": "uniswap_v2",
        "rpc_env_prefixes": ("ROBINHOOD_ALCHEMY", "ROBINHOOD", "ROBINHOOD_MAINNET"),
        "wrapped_native_address": DEFAULT_ROBINHOOD_WETH_ADDRESS,
        "multicall3_address": DEFAULT_ROBINHOOD_MULTICALL_ADDRESS,
        "explorer_url": DEFAULT_ROBINHOOD_EXPLORER_URL,
        "pool_address": DEFAULT_ROBINHOOD_UNISWAP_V2_FACTORY_ADDRESS,
        "observation_only": True,
        "protocol_supported": False,
        "market_type": MARKET_TYPE_SHADOW_CHAIN,
        "migration_type": "rh_flash_toolkit_uniswap_v2_flash_swap",
    },
    DEFAULT_ROBINHOOD_DEX_MORPHO_MARKET_ID: {
        "legacy_market_id": LEGACY_ROBINHOOD_DEX_MORPHO_MARKET_ID,
        "feature": "dex_arbitrage",
        "display_name": "Robinhood Morpho Blue Flash Loan",
        "network": "robinhood",
        "chain_id": DEFAULT_ROBINHOOD_CHAIN_ID,
        "native_symbol": "ETH",
        "protocol": "morpho_blue",
        "rpc_env_prefixes": ("ROBINHOOD_ALCHEMY", "ROBINHOOD", "ROBINHOOD_MAINNET"),
        "wrapped_native_address": DEFAULT_ROBINHOOD_WETH_ADDRESS,
        "multicall3_address": DEFAULT_ROBINHOOD_MULTICALL_ADDRESS,
        "explorer_url": DEFAULT_ROBINHOOD_EXPLORER_URL,
        "pool_address": DEFAULT_ROBINHOOD_MORPHO_BLUE_CORE_ADDRESS,
        "observation_only": True,
        "protocol_supported": False,
        "market_type": MARKET_TYPE_SHADOW_CHAIN,
        "migration_type": "rh_flash_toolkit_morpho_blue_flash_loan",
    },
    DEFAULT_ROBINHOOD_MORPHO_MARKET_ID: {
        "legacy_market_id": LEGACY_ROBINHOOD_MORPHO_MARKET_ID,
        "feature": "liquidation",
        "display_name": "Robinhood Morpho Blue Flash Loan",
        "network": "robinhood",
        "chain_id": DEFAULT_ROBINHOOD_CHAIN_ID,
        "native_symbol": "ETH",
        "protocol": "morpho_blue",
        "rpc_env_prefixes": ("ROBINHOOD_ALCHEMY", "ROBINHOOD", "ROBINHOOD_MAINNET"),
        "wrapped_native_address": DEFAULT_ROBINHOOD_WETH_ADDRESS,
        "multicall3_address": DEFAULT_ROBINHOOD_MULTICALL_ADDRESS,
        "explorer_url": DEFAULT_ROBINHOOD_EXPLORER_URL,
        "pool_address": DEFAULT_ROBINHOOD_MORPHO_BLUE_CORE_ADDRESS,
        "observation_only": True,
        "protocol_supported": False,
        "market_type": MARKET_TYPE_SHADOW_CHAIN,
        "chain_default": True,
        "migration_type": "rh_flash_toolkit_morpho_blue_flash_loan",
    },
    DEFAULT_ROBINHOOD_BOT_MARKET_ID: {
        "legacy_market_id": LEGACY_ROBINHOOD_BOT_MARKET_ID,
        "feature": "liquidation",
        "display_name": "Robinhood Liquidation Shadow",
        "network": "robinhood",
        "chain_id": DEFAULT_ROBINHOOD_CHAIN_ID,
        "native_symbol": "ETH",
        "protocol": "aave_v3",
        "rpc_env_prefixes": ("ROBINHOOD_ALCHEMY", "ROBINHOOD", "ROBINHOOD_MAINNET"),
        "wrapped_native_address": DEFAULT_ROBINHOOD_WETH_ADDRESS,
        "multicall3_address": DEFAULT_ROBINHOOD_MULTICALL_ADDRESS,
        "explorer_url": DEFAULT_ROBINHOOD_EXPLORER_URL,
        "observation_only": True,
        "protocol_supported": False,
        "market_type": MARKET_TYPE_SHADOW_CHAIN,
        "migration_type": "liquidation_shadow_protocol_placeholder",
    },
    "dex-arbitrage-hyperliquid-hyperliquid-native": {
        "legacy_market_id": "hyperliquid-dex-shadow",
        "aliases": ("hyperliquid-native",),
        "feature": "dex_arbitrage",
        "display_name": "Hyperliquid DEX Shadow",
        "network": "hyperliquid",
        "chain_id": None,
        "native_symbol": "HYPE",
        "protocol": "hyperliquid_native",
        "rpc_env_prefixes": (),
        "observation_only": True,
        "protocol_supported": False,
        "market_type": MARKET_TYPE_NATIVE_CHAIN,
        "chain_default": True,
        "migration_type": "dex_native_protocol_placeholder",
    },
}

def _builtin_market_summary(market_id: str, profile: dict[str, Any]) -> dict[str, Any]:
    row = {"market_id": market_id, **dict(profile)}
    key = market_key_from_summary(row)
    summary = {
        **dict(profile),
        "market_id": key.market_id,
        "canonical_market_id": key.market_id,
        "legacy_market_id": key.legacy_market_id or market_id,
        "feature": key.feature,
        "protocol": key.protocol_id,
        "protocol_id": key.protocol_id,
    }
    return summary


BUILTIN_MARKET_SUMMARIES: dict[str, dict[str, Any]] = {
    market_id: _builtin_market_summary(market_id, profile)
    for market_id, profile in KNOWN_EVM_MARKETS.items()
}


KNOWN_CHAIN_ID_MARKETS: dict[int, str] = {}
for _market_id, _summary in BUILTIN_MARKET_SUMMARIES.items():
    if not isinstance(_summary.get("chain_id"), int):
        continue
    _chain_id = int(_summary["chain_id"])
    _feature = str(_summary.get("feature") or "")
    _existing_market_id = KNOWN_CHAIN_ID_MARKETS.get(_chain_id)
    _existing_summary = BUILTIN_MARKET_SUMMARIES.get(_existing_market_id or "", {})
    _existing_feature = str(_existing_summary.get("feature") or "") if _existing_market_id else ""
    if (
        _existing_market_id is None
        or (
            _feature == FEATURE_LIQUIDATION
            and (
                _existing_feature != FEATURE_LIQUIDATION
                or not bool(_existing_summary.get("is_default"))
            )
        )
        or (
            _existing_feature != FEATURE_LIQUIDATION
            and bool(_summary.get("is_default"))
        )
    ):
        KNOWN_CHAIN_ID_MARKETS[_chain_id] = _summary["market_id"]

KNOWN_CHAIN_FEATURE_MARKETS: dict[tuple[int, str], str] = {}
for _market_id, _summary in BUILTIN_MARKET_SUMMARIES.items():
    if not isinstance(_summary.get("chain_id"), int):
        continue
    _key = (int(_summary["chain_id"]), str(_summary.get("feature") or FEATURE_LIQUIDATION))
    if (
        _key not in KNOWN_CHAIN_FEATURE_MARKETS
        or bool(_summary.get("chain_default"))
        or bool(_summary.get("is_default"))
    ):
        KNOWN_CHAIN_FEATURE_MARKETS[_key] = _summary["market_id"]


PROTOCOL_CAPABILITIES: dict[str, dict[str, Any]] = {
    "aave_v3": {
        "evm": True,
        "adapter": "AaveV3Adapter",
        "executor": "EvmLiquidationExecutor",
        "supported": True,
        "notes": "EVM Aave V3 style health factor, reserve data, liquidationCall, and executor payloads.",
    },
    "venus_core": {
        "evm": True,
        "adapter": "VenusCoreAdapter",
        "executor": "VenusLiquidationExecutor",
        "supported": False,
        "notes": "Venus Core Pool observation mode uses a dedicated protocol adapter; execution remains gated until the Venus executor is deployed.",
    },
    "morpho_blue": {
        "evm": True,
        "adapter": "MorphoBlueFlashLoanAdapter",
        "executor": "RobinhoodFlashLoanExecutor",
        "supported": False,
        "notes": "Robinhood Morpho Blue flashLoan is modeled as a reusable flash liquidity source; execution remains gated until local callback contracts and fork simulation are verified.",
    },
    "uniswap_v3": {
        "evm": True,
        "adapter": "UniswapV3DexAdapter",
        "executor": "DexArbitrageExecutor",
        "supported": False,
        "notes": "DEX arbitrage starts in shadow mode until DEX addresses, pools, executor, and flash-loan path are verified.",
    },
    "uniswap_v4": {
        "evm": True,
        "adapter": "UniswapV4DexAdapter",
        "executor": "DexArbitrageExecutor",
        "supported": False,
        "notes": "Uniswap V4 route discovery and quote execution use a live SDK adapter plus on-chain preflight before execution is allowed.",
    },
    "uniswap_v2": {
        "evm": True,
        "adapter": "UniswapV2DexAdapter",
        "executor": "DexArbitrageExecutor",
        "supported": False,
        "notes": "V2-style DEX routes are modeled for discovery and UI filtering before executor enablement.",
    },
    "pancakeswap_v3": {
        "evm": True,
        "adapter": "PancakeSwapV3DexAdapter",
        "executor": "DexArbitrageExecutor",
        "supported": False,
        "notes": "BNB PancakeSwap V3 route discovery placeholder.",
    },
    "pancakeswap_v2": {
        "evm": True,
        "adapter": "PancakeSwapV2DexAdapter",
        "executor": "DexArbitrageExecutor",
        "supported": False,
        "notes": "BNB PancakeSwap V2 route discovery placeholder.",
    },
    "biswap_v2": {
        "evm": True,
        "adapter": "BiswapV2DexAdapter",
        "executor": "DexArbitrageExecutor",
        "supported": False,
        "notes": "BNB Biswap V2 route discovery placeholder.",
    },
    "quickswap_v2": {
        "evm": True,
        "adapter": "QuickSwapV2DexAdapter",
        "executor": "DexArbitrageExecutor",
        "supported": False,
        "notes": "Polygon QuickSwap V2 route discovery placeholder.",
    },
    "traderjoe_v2": {
        "evm": True,
        "adapter": "TraderJoeV2DexAdapter",
        "executor": "DexArbitrageExecutor",
        "supported": False,
        "notes": "Avalanche Trader Joe V2 route discovery placeholder.",
    },
    "curve": {
        "evm": True,
        "adapter": "CurveDexAdapter",
        "executor": "DexArbitrageExecutor",
        "supported": False,
        "notes": "Curve pool routing placeholder.",
    },
    "balancer_v2": {
        "evm": True,
        "adapter": "BalancerV2DexAdapter",
        "executor": "DexArbitrageExecutor",
        "supported": False,
        "notes": "Balancer V2 pool routing placeholder.",
    },
    "cow_protocol": {
        "evm": False,
        "adapter": "CowExecutionRailAdapter",
        "executor": "CowIntentExecutor",
        "supported": False,
        "notes": "CoW is a cross-chain execution rail; real execution networks are selected by cow_networks.",
    },
    "hyperliquid_native": {
        "evm": False,
        "adapter": "HyperliquidNativeAdapter",
        "executor": "HyperliquidLiquidationExecutor",
        "supported": False,
        "notes": "Native margin/position liquidation needs a separate protocol adapter; Aave executor contracts do not apply.",
    },
    "hyper_evm_aave_v3": {
        "evm": True,
        "adapter": "AaveV3Adapter",
        "executor": "EvmLiquidationExecutor",
        "supported": True,
        "notes": "Only valid when the market exposes Aave V3 compatible contracts on HyperEVM.",
    },
}
