from __future__ import annotations

from web3 import Web3


POOL_ACCOUNT_DATA_ABI = [
    {
        "inputs": [{"internalType": "address", "name": "user", "type": "address"}],
        "name": "getUserAccountData",
        "outputs": [
            {"internalType": "uint256", "name": "totalCollateralBase", "type": "uint256"},
            {"internalType": "uint256", "name": "totalDebtBase", "type": "uint256"},
            {"internalType": "uint256", "name": "availableBorrowsBase", "type": "uint256"},
            {"internalType": "uint256", "name": "currentLiquidationThreshold", "type": "uint256"},
            {"internalType": "uint256", "name": "ltv", "type": "uint256"},
            {"internalType": "uint256", "name": "healthFactor", "type": "uint256"},
        ],
        "stateMutability": "view",
        "type": "function",
    }
]

MULTICALL3_ABI = [
    {
        "inputs": [
            {
                "components": [
                    {"internalType": "address", "name": "target", "type": "address"},
                    {"internalType": "bool", "name": "allowFailure", "type": "bool"},
                    {"internalType": "bytes", "name": "callData", "type": "bytes"},
                ],
                "internalType": "struct Multicall3.Call3[]",
                "name": "calls",
                "type": "tuple[]",
            }
        ],
        "name": "aggregate3",
        "outputs": [
            {
                "components": [
                    {"internalType": "bool", "name": "success", "type": "bool"},
                    {"internalType": "bytes", "name": "returnData", "type": "bytes"},
                ],
                "internalType": "struct Multicall3.Result[]",
                "name": "returnData",
                "type": "tuple[]",
            }
        ],
        "stateMutability": "payable",
        "type": "function",
    }
]

BORROW_EVENT_TOPIC = Web3.keccak(text="Borrow(address,address,address,uint256,uint8,uint256,uint16)").hex()

AAVE_PROTOCOL_DATA_PROVIDER_ABI = [
    {
        "inputs": [
            {"internalType": "address", "name": "asset", "type": "address"},
            {"internalType": "address", "name": "user", "type": "address"},
        ],
        "name": "getUserReserveData",
        "outputs": [
            {"internalType": "uint256", "name": "currentATokenBalance", "type": "uint256"},
            {"internalType": "uint256", "name": "currentStableDebt", "type": "uint256"},
            {"internalType": "uint256", "name": "currentVariableDebt", "type": "uint256"},
            {"internalType": "uint256", "name": "principalStableDebt", "type": "uint256"},
            {"internalType": "uint256", "name": "scaledVariableDebt", "type": "uint256"},
            {"internalType": "uint256", "name": "stableBorrowRate", "type": "uint256"},
            {"internalType": "uint256", "name": "liquidityRate", "type": "uint256"},
            {"internalType": "uint40", "name": "stableRateLastUpdated", "type": "uint40"},
            {"internalType": "bool", "name": "usageAsCollateralEnabled", "type": "bool"},
        ],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [{"internalType": "address", "name": "asset", "type": "address"}],
        "name": "getReserveTokensAddresses",
        "outputs": [
            {"internalType": "address", "name": "aTokenAddress", "type": "address"},
            {"internalType": "address", "name": "stableDebtTokenAddress", "type": "address"},
            {"internalType": "address", "name": "variableDebtTokenAddress", "type": "address"},
        ],
        "stateMutability": "view",
        "type": "function",
    },
]

LIQUIDATION_DATA_PROVIDER_ABI = [
    {
        "inputs": [{"internalType": "address", "name": "user", "type": "address"}],
        "name": "getUserPositionFullInfo",
        "outputs": [
            {
                "components": [
                    {"internalType": "uint256", "name": "totalCollateralInBaseCurrency", "type": "uint256"},
                    {"internalType": "uint256", "name": "totalDebtInBaseCurrency", "type": "uint256"},
                    {"internalType": "uint256", "name": "availableBorrowsInBaseCurrency", "type": "uint256"},
                    {"internalType": "uint256", "name": "currentLiquidationThreshold", "type": "uint256"},
                    {"internalType": "uint256", "name": "ltv", "type": "uint256"},
                    {"internalType": "uint256", "name": "healthFactor", "type": "uint256"},
                ],
                "internalType": "struct UserPositionFullInfo",
                "name": "",
                "type": "tuple",
            }
        ],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [
            {"internalType": "address", "name": "user", "type": "address"},
            {"internalType": "address", "name": "collateralAsset", "type": "address"},
        ],
        "name": "getCollateralFullInfo",
        "outputs": [
            {
                "components": [
                    {"internalType": "uint256", "name": "assetUnit", "type": "uint256"},
                    {"internalType": "uint256", "name": "price", "type": "uint256"},
                    {"internalType": "address", "name": "aToken", "type": "address"},
                    {"internalType": "uint256", "name": "collateralBalance", "type": "uint256"},
                    {"internalType": "uint256", "name": "collateralBalanceInBaseCurrency", "type": "uint256"},
                ],
                "internalType": "struct CollateralFullInfo",
                "name": "",
                "type": "tuple",
            }
        ],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [
            {"internalType": "address", "name": "user", "type": "address"},
            {"internalType": "address", "name": "debtAsset", "type": "address"},
        ],
        "name": "getDebtFullInfo",
        "outputs": [
            {
                "components": [
                    {"internalType": "uint256", "name": "assetUnit", "type": "uint256"},
                    {"internalType": "uint256", "name": "price", "type": "uint256"},
                    {"internalType": "address", "name": "variableDebtToken", "type": "address"},
                    {"internalType": "uint256", "name": "debtBalance", "type": "uint256"},
                    {"internalType": "uint256", "name": "debtBalanceInBaseCurrency", "type": "uint256"},
                ],
                "internalType": "struct DebtFullInfo",
                "name": "",
                "type": "tuple",
            }
        ],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [
            {"internalType": "address", "name": "user", "type": "address"},
            {"internalType": "address", "name": "collateralAsset", "type": "address"},
            {"internalType": "address", "name": "debtAsset", "type": "address"},
        ],
        "name": "getLiquidationInfo",
        "outputs": [
            {
                "components": [
                    {
                        "components": [
                            {"internalType": "uint256", "name": "totalCollateralInBaseCurrency", "type": "uint256"},
                            {"internalType": "uint256", "name": "totalDebtInBaseCurrency", "type": "uint256"},
                            {"internalType": "uint256", "name": "availableBorrowsInBaseCurrency", "type": "uint256"},
                            {"internalType": "uint256", "name": "currentLiquidationThreshold", "type": "uint256"},
                            {"internalType": "uint256", "name": "ltv", "type": "uint256"},
                            {"internalType": "uint256", "name": "healthFactor", "type": "uint256"},
                        ],
                        "internalType": "struct UserPositionFullInfo",
                        "name": "userInfo",
                        "type": "tuple",
                    },
                    {
                        "components": [
                            {"internalType": "uint256", "name": "assetUnit", "type": "uint256"},
                            {"internalType": "uint256", "name": "price", "type": "uint256"},
                            {"internalType": "address", "name": "aToken", "type": "address"},
                            {"internalType": "uint256", "name": "collateralBalance", "type": "uint256"},
                            {"internalType": "uint256", "name": "collateralBalanceInBaseCurrency", "type": "uint256"},
                        ],
                        "internalType": "struct CollateralFullInfo",
                        "name": "collateralInfo",
                        "type": "tuple",
                    },
                    {
                        "components": [
                            {"internalType": "uint256", "name": "assetUnit", "type": "uint256"},
                            {"internalType": "uint256", "name": "price", "type": "uint256"},
                            {"internalType": "address", "name": "variableDebtToken", "type": "address"},
                            {"internalType": "uint256", "name": "debtBalance", "type": "uint256"},
                            {"internalType": "uint256", "name": "debtBalanceInBaseCurrency", "type": "uint256"},
                        ],
                        "internalType": "struct DebtFullInfo",
                        "name": "debtInfo",
                        "type": "tuple",
                    },
                    {"internalType": "uint256", "name": "maxCollateralToLiquidate", "type": "uint256"},
                    {"internalType": "uint256", "name": "maxDebtToLiquidate", "type": "uint256"},
                    {"internalType": "uint256", "name": "liquidationProtocolFee", "type": "uint256"},
                    {"internalType": "uint256", "name": "amountToPassToLiquidationCall", "type": "uint256"},
                ],
                "internalType": "struct LiquidationInfo",
                "name": "",
                "type": "tuple",
            }
        ],
        "stateMutability": "view",
        "type": "function",
    },
]
