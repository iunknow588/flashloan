from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any


DEFAULT_AVALANCHE_MARKET_ID = "avalanche-aave-v3"
DEFAULT_AVALANCHE_CHAIN_ID = 43114
DEFAULT_AVALANCHE_ROUTER_ADDRESS = "0x60aE616a2155Ee3d9A68541Ba4544862310933d4"
DEFAULT_BNB_MARKET_ID = "bnb-venus-core"
DEFAULT_BNB_CHAIN_ID = 56
DEFAULT_BNB_PANCAKE_ROUTER_ADDRESS = "0x10ED43C718714eb63d5aA57B78B54704E256024E"
DEFAULT_ETHEREUM_UNISWAP_V2_ROUTER_ADDRESS = "0x7a250d5630B4cF539739dF2C5dAcb4c659F2488D"
DEFAULT_POLYGON_QUICKSWAP_V2_ROUTER_ADDRESS = "0xa5E0829CaCED8fFDD4De3c43696c57F7D7A678ff"
DEFAULT_BNB_DEX_MARKET_ID = "bnb-dex-arbitrage"
DEFAULT_ETHEREUM_DEX_MARKET_ID = "ethereum-dex-arbitrage"
DEFAULT_POLYGON_DEX_MARKET_ID = "polygon-dex-arbitrage"
DEFAULT_AVALANCHE_DEX_MARKET_ID = "avalanche-dex-arbitrage"
DEFAULT_COW_DEX_MARKET_ID = "cow-dex-arbitrage"
DEFAULT_ETHEREUM_CHAIN_ID = 1
DEFAULT_POLYGON_CHAIN_ID = 137
DEFAULT_COW_VIRTUAL_CHAIN_ID = 0
DEFAULT_ROBINHOOD_BOT_MARKET_ID = "robinhood-liquidation-shadow"
DEFAULT_ROBINHOOD_TESTNET_BOT_MARKET_ID = "robinhood-testnet-liquidation-shadow"
DEFAULT_ROBINHOOD_DEX_MARKET_ID = "robinhood-dex-shadow"
DEFAULT_ROBINHOOD_TESTNET_DEX_MARKET_ID = "robinhood-testnet-dex-paused"
DEFAULT_ROBINHOOD_CHAIN_ID = 4663
DEFAULT_ROBINHOOD_TESTNET_CHAIN_ID = 46630
DEFAULT_ROBINHOOD_WETH_ADDRESS = "0x0Bd7D308f8E1639FAb988df18A8011f41EAcAD73"
DEFAULT_ROBINHOOD_MULTICALL_ADDRESS = "0x2cAC2D899eCC914d704FeaAE33ac1bF36277DaD1"
DEFAULT_ROBINHOOD_EXPLORER_URL = "https://robinhoodchain.blockscout.com"
DEFAULT_ROBINHOOD_TESTNET_EXPLORER_URL = "https://explorer.testnet.chain.robinhood.com"
DEFAULT_ROBINHOOD_TESTNET_RPC_URL = "https://rpc.testnet.chain.robinhood.com"
DEFAULT_ROBINHOOD_MORPHO_MARKET_ID = "robinhood-morpho-blue-flash"
DEFAULT_ROBINHOOD_DEX_MORPHO_MARKET_ID = "robinhood-dex-morpho-blue-flash"
DEFAULT_ROBINHOOD_MORPHO_BLUE_CORE_ADDRESS = "0x9D53d5E3bd5E8d4Cbfa6DB1ca238AEA02E651010"
DEFAULT_ROBINHOOD_UNISWAP_V2_FACTORY_ADDRESS = "0x8bCeAA40b9ACdFAEDF85Adf4FF01f5aD6517937f"

MARKET_TYPE_NATIVE_CHAIN = "native_chain"
MARKET_TYPE_SHADOW_CHAIN = "shadow_chain"
MARKET_TYPE_EXECUTION_RAIL = "execution_rail"


KNOWN_EVM_MARKETS: dict[str, dict[str, Any]] = {
    DEFAULT_AVALANCHE_MARKET_ID: {
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
    "bnb-aave-v3": {
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
    "polygon-aave-v3-shadow": {
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
    "ethereum-aave-v3-shadow": {
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
    "cow-liquidation-shadow": {
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
    "bnb-pancakeswap-v2": {
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
    "bnb-biswap-v2": {
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
    "ethereum-uniswap-v2": {
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
    "ethereum-balancer-v2": {
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
    "polygon-quickswap-v2": {
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
    "polygon-balancer-v2": {
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
    "avalanche-traderjoe-v2": {
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
    "avalanche-curve": {
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
    "robinhood-dex-v4-shadow": {
        "feature": "dex_arbitrage",
        "display_name": "Robinhood Uniswap V4 Shadow",
        "network": "robinhood",
        "chain_id": DEFAULT_ROBINHOOD_CHAIN_ID,
        "native_symbol": "ETH",
        "protocol": "uniswap_v4",
        "rpc_env_prefixes": ("ROBINHOOD_ALCHEMY", "ROBINHOOD", "ROBINHOOD_MAINNET"),
        "wrapped_native_address": DEFAULT_ROBINHOOD_WETH_ADDRESS,
        "multicall3_address": DEFAULT_ROBINHOOD_MULTICALL_ADDRESS,
        "explorer_url": DEFAULT_ROBINHOOD_EXPLORER_URL,
        "observation_only": True,
        "protocol_supported": False,
        "market_type": MARKET_TYPE_SHADOW_CHAIN,
        "migration_type": "robinhood_mainnet_v4_ui_prepared_contract_deploy_deferred",
    },
    DEFAULT_ROBINHOOD_TESTNET_DEX_MARKET_ID: {
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
    "robinhood-dex-v2-shadow": {
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
    "hyperliquid-dex-shadow": {
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

KNOWN_CHAIN_ID_MARKETS: dict[int, str] = {}
for _market_id, _profile in KNOWN_EVM_MARKETS.items():
    if not isinstance(_profile.get("chain_id"), int):
        continue
    _chain_id = int(_profile["chain_id"])
    _feature = str(_profile.get("feature") or "")
    _existing_market_id = KNOWN_CHAIN_ID_MARKETS.get(_chain_id)
    _existing_feature = (
        str(KNOWN_EVM_MARKETS[_existing_market_id].get("feature") or "")
        if _existing_market_id
        else ""
    )
    if (
        _existing_market_id is None
        or (
            _feature == "liquidation"
            and (
                _existing_feature != "liquidation"
                or not bool(KNOWN_EVM_MARKETS[_existing_market_id].get("is_default"))
            )
        )
        or (
            _existing_feature != "liquidation"
            and bool(_profile.get("is_default"))
        )
    ):
        KNOWN_CHAIN_ID_MARKETS[_chain_id] = _market_id

KNOWN_CHAIN_FEATURE_MARKETS: dict[tuple[int, str], str] = {}
for _market_id, _profile in KNOWN_EVM_MARKETS.items():
    if not isinstance(_profile.get("chain_id"), int):
        continue
    _key = (int(_profile["chain_id"]), str(_profile.get("feature") or "liquidation"))
    if (
        _key not in KNOWN_CHAIN_FEATURE_MARKETS
        or bool(_profile.get("chain_default"))
        or bool(_profile.get("is_default"))
    ):
        KNOWN_CHAIN_FEATURE_MARKETS[_key] = _market_id


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


@dataclass(frozen=True)
class ChainMarketConfig:
    market_id: str
    feature: str
    display_name: str
    network: str
    market_type: str
    protocol: str
    chain_id: int
    native_symbol: str
    rpc_urls: tuple[str, ...]
    pool_address: str
    protocol_data_provider_address: str
    liquidation_data_provider_address: str
    dex_router_address: str
    executor_address: str
    executor_owner_address: str
    multicall3_address: str
    protocol_supported: bool
    evm_compatible: bool
    protocol_adapter: str
    executor_adapter: str
    wrapped_native_address: str = ""
    explorer_url: str = ""
    observation_only: bool = False
    enabled: bool = True
    is_default: bool = False

    @property
    def namespace(self) -> str:
        return market_namespace(self.market_id)

    def as_dict(self) -> dict[str, Any]:
        return {
            "market_id": self.market_id,
            "namespace": self.namespace,
            "feature": self.feature,
            "display_name": self.display_name,
            "network": self.network,
            "market_type": self.market_type,
            "protocol": self.protocol,
            "chain_id": self.chain_id,
            "native_symbol": self.native_symbol,
            "wrapped_native_address": self.wrapped_native_address,
            "explorer_url": self.explorer_url,
            "rpc_urls": list(self.rpc_urls),
            "pool_address": self.pool_address,
            "protocol_data_provider_address": self.protocol_data_provider_address,
            "liquidation_data_provider_address": self.liquidation_data_provider_address,
            "dex_router_address": self.dex_router_address,
            "executor_address": self.executor_address,
            "executor_owner_address": self.executor_owner_address,
            "multicall3_address": self.multicall3_address,
            "protocol_supported": self.protocol_supported,
            "evm_compatible": self.evm_compatible,
            "protocol_adapter": self.protocol_adapter,
            "executor_adapter": self.executor_adapter,
            "observation_only": self.observation_only,
            "enabled": self.enabled,
            "is_default": self.is_default,
        }


def _env(name: str) -> str:
    return os.getenv(name, "").strip()


def _first_env(*names: str) -> str:
    for name in names:
        value = _env(name)
        if value:
            return value
    return ""


def _parse_int(value: str, default: int) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return int(default)


def _split_urls(value: str) -> list[str]:
    urls: list[str] = []
    for item in str(value or "").replace("\n", ",").replace(";", ",").split(","):
        candidate = item.strip().rstrip("/")
        if candidate and candidate not in urls:
            urls.append(candidate)
    return urls


def market_namespace(market_id: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", str(market_id or "").lower()).strip("-")
    return slug or DEFAULT_AVALANCHE_MARKET_ID


def _known_profile(market_id: str) -> dict[str, Any]:
    normalized = market_namespace(market_id)
    if normalized in KNOWN_EVM_MARKETS:
        return dict(KNOWN_EVM_MARKETS[normalized])
    network = _env("LIQUIDATION_NETWORK") or normalized.split("-")[0] or "custom"
    protocol = _env("LIQUIDATION_PROTOCOL") or ("aave_v3" if "aave" in normalized else "custom")
    return {
        "network": network,
        "chain_id": DEFAULT_AVALANCHE_CHAIN_ID,
        "native_symbol": _env("LIQUIDATION_NATIVE_SYMBOL") or "ETH",
        "protocol": protocol,
        "market_type": MARKET_TYPE_NATIVE_CHAIN,
        "rpc_env_prefixes": (network.upper().replace("-", "_"),),
    }


def _configured_default_market_id(feature: str) -> str:
    try:
        from core.chain_data_registry import default_market_id_for_feature

        return default_market_id_for_feature(feature)
    except Exception:
        return ""


def market_id_for_chain_id(chain_id: int | None) -> str | None:
    if chain_id is None:
        return None
    return KNOWN_CHAIN_ID_MARKETS.get(int(chain_id))


def market_id_for_scope(chain_id: int | None = None, feature: str | None = None) -> str | None:
    if chain_id is None:
        return None
    normalized_feature = str(feature or "").strip().lower()
    if normalized_feature:
        scoped = KNOWN_CHAIN_FEATURE_MARKETS.get((int(chain_id), normalized_feature))
        if scoped:
            return scoped
    return market_id_for_chain_id(chain_id)


def _rpc_urls_for(profile: dict[str, Any]) -> tuple[str, ...]:
    candidates: list[str] = []
    for raw in (
        _env("LIQUIDATION_RPCS"),
        _env("LIQUIDATION_RPC_URLS"),
        _env("LIQUIDATION_RPC"),
        _env("LIQUIDATION_RPC_URL"),
    ):
        candidates.extend(_split_urls(raw))
    for prefix in profile.get("rpc_env_prefixes") or ():
        candidates.extend(_split_urls(_env(f"{prefix}_RPCS")))
        candidates.extend(_split_urls(_env(f"{prefix}_RPC")))
        candidates.extend(_split_urls(_env(f"{prefix}_RPC_URL")))
    if profile.get("network") == "avalanche":
        candidates.extend(
            [
                "https://api.avax.network/ext/bc/C/rpc",
                "https://rpc.ankr.com/avalanche",
                "https://avalanche-c-chain-rpc.publicnode.com",
            ]
        )
    if profile.get("network") == "bnb":
        candidates.extend(
            [
                "https://bsc-dataseed.binance.org",
                "https://rpc.ankr.com/bsc",
                "https://bsc.publicnode.com",
            ]
        )
    if profile.get("network") == "ethereum":
        candidates.extend(
            [
                "https://cloudflare-eth.com",
                "https://rpc.ankr.com/eth",
                "https://ethereum.publicnode.com",
            ]
        )
    if profile.get("network") == "polygon":
        candidates.extend(
            [
                "https://polygon-rpc.com",
                "https://rpc.ankr.com/polygon",
                "https://polygon-bor.publicnode.com",
            ]
        )
    if profile.get("network") == "robinhood":
        candidates.extend(["https://rpc.mainnet.chain.robinhood.com"])
    if profile.get("network") == "robinhood-testnet":
        candidates.extend([DEFAULT_ROBINHOOD_TESTNET_RPC_URL])
    return tuple(dict.fromkeys(candidate for candidate in candidates if candidate))


def _dex_router_for(profile: dict[str, Any]) -> str:
    explicit = _first_env("LIQUIDATION_DEX_ROUTER_ADDRESS")
    if explicit:
        return explicit
    network = str(profile.get("network") or "").strip().lower()
    if network == "bnb":
        return _first_env("BNB_DEX_ROUTER_ADDRESS", "PANCAKE_ROUTER_ADDRESS", "PANCAKESWAP_V2_ROUTER_ADDRESS") or DEFAULT_BNB_PANCAKE_ROUTER_ADDRESS
    if network == "ethereum":
        return _first_env("ETHEREUM_DEX_ROUTER_ADDRESS", "UNISWAP_DEX_ROUTER_ADDRESS", "UNISWAP_V2_ROUTER_ADDRESS") or DEFAULT_ETHEREUM_UNISWAP_V2_ROUTER_ADDRESS
    if network == "polygon":
        return _first_env("POLYGON_DEX_ROUTER_ADDRESS", "QUICKSWAP_DEX_ROUTER_ADDRESS", "QUICKSWAP_V2_ROUTER_ADDRESS") or DEFAULT_POLYGON_QUICKSWAP_V2_ROUTER_ADDRESS
    if network == "robinhood":
        return _first_env("ROBINHOOD_DEX_ROUTER_ADDRESS")
    if network == "robinhood-testnet":
        return _first_env("ROBINHOOD_TESTNET_DEX_ROUTER_ADDRESS", "ROBINHOOD_TESTNET_V4_UNIVERSAL_ROUTER")
    return _first_env("DEX_ROUTER_ADDRESS") or DEFAULT_AVALANCHE_ROUTER_ADDRESS


def liquidation_market_config() -> ChainMarketConfig:
    raw_chain_id = _first_env("LIQUIDATION_CHAIN_ID", "CHAIN_ID")
    return liquidation_market_config_for(
        market_id=_env("LIQUIDATION_MARKET_ID") or _configured_default_market_id("liquidation") or DEFAULT_AVALANCHE_MARKET_ID,
        chain_id=_parse_int(raw_chain_id, DEFAULT_AVALANCHE_CHAIN_ID) if raw_chain_id else None,
    )


def liquidation_market_config_for(
    market_id: str | None = None,
    *,
    chain_id: int | None = None,
    feature: str | None = None,
) -> ChainMarketConfig:
    resolved_feature = str(feature or "liquidation").strip().lower()
    explicit_market_id = market_id is not None and str(market_id).strip() != ""
    if explicit_market_id:
        resolved_market_id = market_namespace(str(market_id))
        if resolved_market_id not in KNOWN_EVM_MARKETS and resolved_market_id != "hyperliquid-native":
            raise ValueError(f"unknown market_id: {resolved_market_id}")
    elif chain_id is not None:
        resolved_market_id = KNOWN_CHAIN_FEATURE_MARKETS.get((int(chain_id), resolved_feature), "")
        if not resolved_market_id:
            raise ValueError(f"no {resolved_feature} market is registered for chain_id {int(chain_id)}")
    else:
        resolved_market_id = _configured_default_market_id(resolved_feature) or DEFAULT_AVALANCHE_MARKET_ID
    profile = _known_profile(resolved_market_id)
    profile_chain_value = profile.get("chain_id")
    profile_chain_id = int(
        DEFAULT_AVALANCHE_CHAIN_ID
        if profile_chain_value is None
        else profile_chain_value
    )
    profile_feature = str(profile.get("feature") or "liquidation").strip().lower()
    if chain_id is not None and int(chain_id) != profile_chain_id:
        raise ValueError(
            f"market_id {resolved_market_id} belongs to chain_id {profile_chain_id}, not {int(chain_id)}"
        )
    if feature is not None and profile_feature != resolved_feature:
        raise ValueError(
            f"market_id {resolved_market_id} belongs to feature {profile_feature}, not {resolved_feature}"
        )
    protocol = (_env("LIQUIDATION_PROTOCOL") or str(profile.get("protocol") or "aave_v3")).strip().lower()
    protocol_supported = bool(profile.get("protocol_supported", PROTOCOL_CAPABILITIES.get(protocol, {}).get("supported")))
    resolved_chain_id = profile_chain_id
    native_symbol = (_env("LIQUIDATION_NATIVE_SYMBOL") or str(profile.get("native_symbol") or "ETH")).upper()
    capabilities = PROTOCOL_CAPABILITIES.get(protocol, {})
    configured_default_market_id = _configured_default_market_id(profile_feature)
    return ChainMarketConfig(
        market_id=resolved_market_id,
        feature=str(profile.get("feature") or resolved_feature or "liquidation"),
        display_name=str(profile.get("display_name") or resolved_market_id),
        network=(_env("LIQUIDATION_NETWORK") or str(profile.get("network") or "custom")).strip().lower(),
        market_type=str(profile.get("market_type") or MARKET_TYPE_NATIVE_CHAIN),
        protocol=protocol,
        chain_id=resolved_chain_id,
        native_symbol=native_symbol,
        rpc_urls=_rpc_urls_for(profile),
        pool_address=_first_env("LIQUIDATION_POOL_ADDRESS")
        or str(profile.get("pool_address") or "")
        or (_first_env("AAVE_POOL_ADDRESS") if protocol in {"aave_v3", "hyper_evm_aave_v3"} else ""),
        protocol_data_provider_address=_first_env("LIQUIDATION_PROTOCOL_DATA_PROVIDER_ADDRESS", "AAVE_PROTOCOL_DATA_PROVIDER_ADDRESS"),
        liquidation_data_provider_address=_first_env("LIQUIDATION_DATA_PROVIDER_ADDRESS", "AAVE_LIQUIDATION_DATA_PROVIDER_ADDRESS"),
        dex_router_address=_dex_router_for(profile),
        executor_address=_first_env("LIQUIDATION_EXECUTOR_ADDRESS"),
        executor_owner_address=_first_env("LIQUIDATION_EXECUTOR_OWNER_ADDRESS"),
        multicall3_address=_first_env(
            "LIQUIDATION_MULTICALL3_ADDRESS",
        )
        or str(profile.get("multicall3_address") or "0xcA11bde05977b3631167028862bE2a173976CA11"),
        protocol_supported=protocol_supported,
        evm_compatible=bool(capabilities.get("evm")),
        protocol_adapter=str(capabilities.get("adapter") or "ProtocolAdapter"),
        executor_adapter=str(capabilities.get("executor") or "LiquidationExecutor"),
        wrapped_native_address=str(profile.get("wrapped_native_address") or ""),
        explorer_url=str(profile.get("explorer_url") or ""),
        observation_only=bool(profile.get("observation_only")) or not protocol_supported,
        enabled=bool(profile.get("enabled", True)),
        is_default=resolved_market_id == configured_default_market_id
        or (not configured_default_market_id and bool(profile.get("is_default")))
        or (not configured_default_market_id and resolved_market_id == DEFAULT_AVALANCHE_MARKET_ID),
    )


def _builtin_supported_market_summaries() -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    default_market_ids: dict[str, str] = {}
    for market_id, profile in KNOWN_EVM_MARKETS.items():
        feature = str(profile.get("feature") or "liquidation")
        if feature not in default_market_ids:
            default_market_ids[feature] = _configured_default_market_id(feature)
        protocol = str(profile["protocol"])
        capabilities = PROTOCOL_CAPABILITIES.get(protocol, {})
        summaries.append(
            {
                "market_id": market_id,
                "feature": feature,
                "display_name": profile.get("display_name") or market_id,
                "network": profile["network"],
                "market_type": profile.get("market_type") or MARKET_TYPE_NATIVE_CHAIN,
                "chain_id": profile["chain_id"],
                "native_symbol": profile["native_symbol"],
                "protocol": protocol,
                "protocol_supported": bool(profile.get("protocol_supported", capabilities.get("supported"))),
                "observation_only": bool(profile.get("observation_only"))
                or not bool(profile.get("protocol_supported", capabilities.get("supported"))),
                "is_default": market_id == default_market_ids.get(feature)
                or (not default_market_ids.get(feature) and bool(profile.get("is_default")))
                or (not default_market_ids.get(feature) and market_id == DEFAULT_AVALANCHE_MARKET_ID),
                "migration_type": profile.get("migration_type") or "config_and_new_executor_deployment",
                "pool_address": profile.get("pool_address") or "",
            }
        )
    summaries.append(
        {
            "market_id": "hyperliquid-native",
            "feature": "liquidation",
            "network": "hyperliquid",
            "chain_id": None,
            "native_symbol": "HYPE",
            "protocol": "hyperliquid_native",
            "market_type": MARKET_TYPE_NATIVE_CHAIN,
            "protocol_supported": False,
            "migration_type": "new_protocol_adapter_required",
        }
    )
    return summaries


def supported_market_summaries(database_url: str | None = None) -> list[dict[str, Any]]:
    if database_url:
        from db.storage_common import (
            database_unavailable_reason,
            is_database_unavailable_error,
            mark_database_unavailable,
        )

        if database_unavailable_reason(database_url):
            return _builtin_supported_market_summaries()
        try:
            from db.storage_chain_markets import load_chain_market_summaries

            rows = load_chain_market_summaries(database_url)
            if rows:
                return rows
        except Exception as exc:
            if is_database_unavailable_error(exc):
                mark_database_unavailable(database_url, exc)
    return _builtin_supported_market_summaries()
