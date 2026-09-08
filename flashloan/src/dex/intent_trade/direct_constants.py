from __future__ import annotations

from pathlib import Path

from dex.intent_trade.chains.avalanche.config import (
    AVALANCHE_CHAIN_ID,
    AVALANCHE_RPC_ENV,
    FUJI_CHAIN_ID,
    FUJI_RPC_ENV,
)

MAX_RUNTIME_POOL_SCAN = 5
MAX_RUNTIME_TRADE_SCAN = 5
MAX_UNIFIED_ROUTE_TRADE_INPUTS = 4
DEFAULT_RUNTIME_TRADE_SCAN = 5
RUNTIME_TOP_BOTTOM_LIMIT = 5
MAX_OFFCHAIN_RUNTIME_CANDIDATE_SCAN = RUNTIME_TOP_BOTTOM_LIMIT * RUNTIME_TOP_BOTTOM_LIMIT
DEFAULT_RUNTIME_ROUTE_GROUP_SCAN = 25
MAX_RUNTIME_ROUTE_GROUP_SCAN = 250
DEFAULT_RUNTIME_CACHE_MAX_AGE_BLOCKS = 30
DEFAULT_DIRECT_CIRCUIT_OFFCHAIN_THRESHOLD = 5
DEFAULT_DIRECT_CIRCUIT_ONCHAIN_THRESHOLD = 2
DEFAULT_DIRECT_CIRCUIT_LOSS_THRESHOLD_USDC = "1"
DEFAULT_DIRECT_CIRCUIT_COOLDOWN_SECONDS = 3600
DEFAULT_GAS_TOKEN_PRICE_MAX_AGE_SECONDS = 120
DEFAULT_GAS_TOKEN_PRICE_MAX_DEVIATION_BPS = 100
SRC_ROOT = Path(__file__).resolve().parents[1]
BNB_USDC_ADDRESS = "0x8ac76a51cc950d9822d68b83fe1ad97b32cd580d"
ROBINHOOD_WETH_ADDRESS = "0x0Bd7D308f8E1639FAb988df18A8011f41EAcAD73"
STABLE_EXERCISE_SYMBOLS = {
    "USDC",
    "USDC.E",
    "USDT",
    "USDT.E",
    "USDT0",
    "USDT0.E",
    "DAI",
    "DAI.E",
    "FRAX",
    "EURC",
    "USDE",
    "SUSDE",
}
DIRECT_ONCHAIN_NETWORKS = {
    "avalanche": {
        "chain_id": AVALANCHE_CHAIN_ID,
        "testnet": False,
        "rpc_env": AVALANCHE_RPC_ENV,
        "unified_executor_supported": True,
    },
    "fuji": {
        "chain_id": FUJI_CHAIN_ID,
        "testnet": True,
        "rpc_env": FUJI_RPC_ENV,
        "unified_executor_supported": True,
    },
    "bnb": {
        "chain_id": 56,
        "testnet": False,
        "rpc_env": ("BNB_RPC_URL", "BSC_RPC_URL", "BNB_RPC", "BSC_RPC"),
        "unified_executor_supported": False,
    },
    "robinhood": {
        "chain_id": 4663,
        "testnet": False,
        "rpc_env": (
            "ROBINHOOD_RPC_URL",
            "ROBINHOOD_RPC",
            "ROBINHOOD_RPCS",
            "ROBINHOOD_ALCHEMY_RPC_URL",
            "ROBINHOOD_ALCHEMY_RPC",
            "ROBINHOOD_MAINNET_RPC_URL",
            "ROBINHOOD_MAINNET_RPC",
        ),
        "unified_executor_supported": False,
    },
    "robinhood-testnet": {
        "chain_id": 46630,
        "testnet": True,
        "rpc_env": (
            "ROBINHOOD_TESTNET_RPC_URL",
            "ROBINHOOD_TESTNET_RPC",
            "ROBINHOOD_TESTNET_RPCS",
            "ROBINHOOD_TESTNET_ALCHEMY_RPC_URL",
            "ROBINHOOD_TESTNET_ALCHEMY_RPC",
        ),
        "unified_executor_supported": False,
    },
}
DIRECT_ONCHAIN_NETWORK_ALIASES = {
    "avax": "avalanche",
    "avalanche-c": "avalanche",
    "avalanche-c-chain": "avalanche",
    "43114": "avalanche",
    "avalanche-fuji": "fuji",
    "fuji": "fuji",
    "43113": "fuji",
    "bnb": "bnb",
    "bsc": "bnb",
    "bnb-chain": "bnb",
    "binance-smart-chain": "bnb",
    "56": "bnb",
    "robinhood": "robinhood",
    "robinhood-chain": "robinhood",
    "robinhood-mainnet": "robinhood",
    "4663": "robinhood",
    "robinhood-testnet": "robinhood-testnet",
    "robinhood-sepolia": "robinhood-testnet",
    "46630": "robinhood-testnet",
}
ROUTE_DECISION_COMPONENTS = [
    {"name": "viable", "type": "bool"},
    {"name": "reverse", "type": "bool"},
    {"name": "quotedFinalUsdc", "type": "uint256"},
    {"name": "profitUsdc", "type": "uint256"},
    {"name": "path", "type": "address[]"},
    {"name": "edgeBps", "type": "uint256"},
    {"name": "requiredEdgeBps", "type": "uint256"},
    {"name": "directComparableAmount", "type": "uint256"},
    {"name": "viaComparableAmount", "type": "uint256"},
    {"name": "failureCode", "type": "uint256"},
    {"name": "requiredFinalUsdc", "type": "uint256"},
    {"name": "minAfterSlippageUsdc", "type": "uint256"},
    {"name": "amountOutMinUsdc", "type": "uint256"},
    {"name": "selectedAmount", "type": "uint256"},
    {"name": "routeMaxBorrow", "type": "uint256"},
    {"name": "probeAmount", "type": "uint256"},
    {"name": "probeProfitUsdc", "type": "uint256"},
    {"name": "fundingCostUsdc", "type": "uint256"},
    {"name": "mBps", "type": "int256"},
]
USDC_PAIR_COMPONENTS = [
    {"name": "tokenX", "type": "address"},
    {"name": "tokenY", "type": "address"},
    {"name": "router", "type": "address"},
]
RUNTIME_POOL_COMPONENTS = [
    {"name": "adapterKind", "type": "uint8"},
    {"name": "pool", "type": "address"},
]
RUNTIME_TRADE_COMPONENTS = [
    {"name": "tradeIndex", "type": "uint256"},
    {"name": "tokenX", "type": "address"},
    {"name": "tokenY", "type": "address"},
    {"name": "pools", "type": "tuple[10]", "components": RUNTIME_POOL_COMPONENTS},
]
RUNTIME_DECISION_COMPONENTS = [
    {"name": "viable", "type": "bool"},
    {"name": "tradeIndex", "type": "uint256"},
    {"name": "tokenX", "type": "address"},
    {"name": "tokenY", "type": "address"},
    {"name": "lowPool", "type": "address"},
    {"name": "highPool", "type": "address"},
    {"name": "adapterKind", "type": "uint8"},
    {"name": "lowFee", "type": "uint24"},
    {"name": "highFee", "type": "uint24"},
    {"name": "lowLiquidity", "type": "uint128"},
    {"name": "highLiquidity", "type": "uint128"},
    {"name": "lowNormalizedTick", "type": "int24"},
    {"name": "highNormalizedTick", "type": "int24"},
    {"name": "tickDelta", "type": "int256"},
    {"name": "scannedPoolCount", "type": "uint256"},
    {"name": "validPoolCount", "type": "uint256"},
    {"name": "failureCode", "type": "uint256"},
]
RUNTIME_EXECUTION_PARAMS_COMPONENTS = [
    {"name": "amount", "type": "uint256"},
    {"name": "deadline", "type": "uint256"},
    {"name": "amountOutMinUsdc", "type": "uint256"},
    {"name": "minProfitUsdc", "type": "uint256"},
    {"name": "usdcToTokenXFee", "type": "uint24"},
    {"name": "tokenYToUsdcFee", "type": "uint24"},
]
RUNTIME_CROSS_POOL_EXECUTION_PARAMS_COMPONENTS = [
    {"name": "amount", "type": "uint256"},
    {"name": "deadline", "type": "uint256"},
    {"name": "minFinalTokenX", "type": "uint256"},
    {"name": "minProfitTokenX", "type": "uint256"},
]
RUNTIME_EXECUTION_PREVIEW_COMPONENTS = [
    {"name": "router", "type": "address"},
    {"name": "swapPath", "type": "bytes"},
    {"name": "quotedFinalUsdc", "type": "uint256"},
    {"name": "premiumUsdc", "type": "uint256"},
    {"name": "requiredFinalUsdc", "type": "uint256"},
    {"name": "protectedAmountOutMinUsdc", "type": "uint256"},
    {"name": "minProfitUsdc", "type": "uint256"},
]
# docs: docs/闂數璐烽槻MEV/鍗曞悎绾︾増鏈?01_鍗曞悎绾︽帴鍙ｈ璁?md
# Update this table, ABI docs, and tests together when executionKind/status changes.
_SELECTED_STRATEGY_STATUS_TRANSLATION: dict[tuple[str, int | None, int], int] = {
    ("auto", 1, 3): 4,
    ("auto", 2, 3): 3,
    ("triangular", 1, 3): 4,
}
TRIANGULAR_CONTROLLER_ABI = [
    {
        "type": "function",
        "name": "owner",
        "stateMutability": "view",
        "inputs": [],
        "outputs": [{"name": "", "type": "address"}],
    },
    {
        "type": "function",
        "name": "previewBestRuntimeTrades",
        "stateMutability": "view",
        "inputs": [{"name": "trades", "type": "tuple[]", "components": RUNTIME_TRADE_COMPONENTS}],
        "outputs": [
            {"name": "bestTradeArrayIndex", "type": "uint256"},
            {"name": "decision", "type": "tuple", "components": RUNTIME_DECISION_COMPONENTS},
        ],
    },
    {
        "type": "function",
        "name": "runBestRuntimeTrades",
        "stateMutability": "nonpayable",
        "inputs": [{"name": "trades", "type": "tuple[]", "components": RUNTIME_TRADE_COMPONENTS}],
        "outputs": [
            {"name": "bestTradeArrayIndex", "type": "uint256"},
            {"name": "decision", "type": "tuple", "components": RUNTIME_DECISION_COMPONENTS},
        ],
    },
    {
        "type": "function",
        "name": "runBestRuntimeTradesAndExecute",
        "stateMutability": "nonpayable",
        "inputs": [
            {"name": "trades", "type": "tuple[]", "components": RUNTIME_TRADE_COMPONENTS},
            {"name": "params", "type": "tuple", "components": RUNTIME_EXECUTION_PARAMS_COMPONENTS},
        ],
        "outputs": [
            {"name": "bestTradeArrayIndex", "type": "uint256"},
            {"name": "decision", "type": "tuple", "components": RUNTIME_DECISION_COMPONENTS},
            {"name": "profitSwept", "type": "uint256"},
        ],
    },
    {
        "type": "function",
        "name": "previewBestRuntimeExecution",
        "stateMutability": "view",
        "inputs": [
            {"name": "trades", "type": "tuple[]", "components": RUNTIME_TRADE_COMPONENTS},
            {"name": "params", "type": "tuple", "components": RUNTIME_EXECUTION_PARAMS_COMPONENTS},
        ],
        "outputs": [
            {"name": "bestTradeArrayIndex", "type": "uint256"},
            {"name": "decision", "type": "tuple", "components": RUNTIME_DECISION_COMPONENTS},
            {"name": "executionPreview", "type": "tuple", "components": RUNTIME_EXECUTION_PREVIEW_COMPONENTS},
        ],
    },
    {
        "type": "function",
        "name": "previewFirstProfitableRuntimeExecution",
        "stateMutability": "view",
        "inputs": [
            {"name": "trades", "type": "tuple[]", "components": RUNTIME_TRADE_COMPONENTS},
            {"name": "params", "type": "tuple", "components": RUNTIME_EXECUTION_PARAMS_COMPONENTS},
        ],
        "outputs": [
            {"name": "found", "type": "bool"},
            {"name": "selectedTradeArrayIndex", "type": "uint256"},
            {"name": "decision", "type": "tuple", "components": RUNTIME_DECISION_COMPONENTS},
            {"name": "executionPreview", "type": "tuple", "components": RUNTIME_EXECUTION_PREVIEW_COMPONENTS},
        ],
    },
    {
        "type": "function",
        "name": "runFirstProfitableRuntimeTradesAndExecute",
        "stateMutability": "nonpayable",
        "inputs": [
            {"name": "trades", "type": "tuple[]", "components": RUNTIME_TRADE_COMPONENTS},
            {"name": "params", "type": "tuple", "components": RUNTIME_EXECUTION_PARAMS_COMPONENTS},
        ],
        "outputs": [
            {"name": "selectedTradeArrayIndex", "type": "uint256"},
            {"name": "decision", "type": "tuple", "components": RUNTIME_DECISION_COMPONENTS},
            {"name": "profitSwept", "type": "uint256"},
        ],
    },
    {
        "type": "function",
        "name": "previewFirstProfitableRuntimeAutoExecution",
        "stateMutability": "view",
        "inputs": [
            {"name": "trades", "type": "tuple[]", "components": RUNTIME_TRADE_COMPONENTS},
            {"name": "triangularParams", "type": "tuple", "components": RUNTIME_EXECUTION_PARAMS_COMPONENTS},
            {
                "name": "crossPoolParams",
                "type": "tuple",
                "components": RUNTIME_CROSS_POOL_EXECUTION_PARAMS_COMPONENTS,
            },
        ],
        "outputs": [
            {"name": "found", "type": "bool"},
            {"name": "executionKind", "type": "uint8"},
            {"name": "selectedTradeArrayIndex", "type": "uint256"},
            {"name": "decision", "type": "tuple", "components": RUNTIME_DECISION_COMPONENTS},
            {"name": "executionPreview", "type": "tuple", "components": RUNTIME_EXECUTION_PREVIEW_COMPONENTS},
        ],
    },
    {
        "type": "function",
        "name": "runFirstProfitableRuntimeTradesAndExecuteAuto",
        "stateMutability": "nonpayable",
        "inputs": [
            {"name": "trades", "type": "tuple[]", "components": RUNTIME_TRADE_COMPONENTS},
            {"name": "triangularParams", "type": "tuple", "components": RUNTIME_EXECUTION_PARAMS_COMPONENTS},
            {
                "name": "crossPoolParams",
                "type": "tuple",
                "components": RUNTIME_CROSS_POOL_EXECUTION_PARAMS_COMPONENTS,
            },
        ],
        "outputs": [
            {"name": "executionKind", "type": "uint8"},
            {"name": "selectedTradeArrayIndex", "type": "uint256"},
            {"name": "decision", "type": "tuple", "components": RUNTIME_DECISION_COMPONENTS},
            {"name": "profitSwept", "type": "uint256"},
        ],
    },
    {
        "type": "function",
        "name": "previewOrderedRuntimeAutoExecution",
        "stateMutability": "view",
        "inputs": [
            {"name": "trades", "type": "tuple[]", "components": RUNTIME_TRADE_COMPONENTS},
            {"name": "triangularParams", "type": "tuple", "components": RUNTIME_EXECUTION_PARAMS_COMPONENTS},
            {
                "name": "crossPoolParams",
                "type": "tuple",
                "components": RUNTIME_CROSS_POOL_EXECUTION_PARAMS_COMPONENTS,
            },
            {"name": "enableNonUsdcCrossPool", "type": "bool"},
        ],
        "outputs": [
            {"name": "found", "type": "bool"},
            {"name": "strategyStatus", "type": "uint256"},
            {"name": "executionKind", "type": "uint8"},
            {"name": "selectedTradeArrayIndex", "type": "uint256"},
            {"name": "decision", "type": "tuple", "components": RUNTIME_DECISION_COMPONENTS},
            {"name": "executionPreview", "type": "tuple", "components": RUNTIME_EXECUTION_PREVIEW_COMPONENTS},
        ],
    },
    {
        "type": "function",
        "name": "runOrderedRuntimeTradesAndExecuteAuto",
        "stateMutability": "nonpayable",
        "inputs": [
            {"name": "trades", "type": "tuple[]", "components": RUNTIME_TRADE_COMPONENTS},
            {"name": "triangularParams", "type": "tuple", "components": RUNTIME_EXECUTION_PARAMS_COMPONENTS},
            {
                "name": "crossPoolParams",
                "type": "tuple",
                "components": RUNTIME_CROSS_POOL_EXECUTION_PARAMS_COMPONENTS,
            },
            {"name": "enableNonUsdcCrossPool", "type": "bool"},
        ],
        "outputs": [
            {"name": "strategyStatus", "type": "uint256"},
            {"name": "executionKind", "type": "uint8"},
            {"name": "selectedTradeArrayIndex", "type": "uint256"},
            {"name": "decision", "type": "tuple", "components": RUNTIME_DECISION_COMPONENTS},
            {"name": "profitSwept", "type": "uint256"},
        ],
    },
]
