from __future__ import annotations

from web3 import Web3


VENUS_CORE_POOL_REGISTRY_ADDRESS = "0x9F7b01A536aFA00EF10310A162877fd792cD0666"
VENUS_CORE_POOL_LENS_ADDRESS = "0xA179d2f1Fd53D15Bc790bE91d5fF4a0108E29621"
VENUS_CORE_COMPTROLLER_ADDRESS = "0xfD36E2c2a6789Db23113685031d7F16329158384"
VENUS_COMPTROLLER_LENS_ADDRESS = "0xd701C1fDAE34f9Cf242a4de19a2e7288f924EA1C"
VENUS_LENS_ADDRESS = "0xf15A9c5aaDc327B383945D5821C7aC08Cdac7430"
VENUS_SWAP_ROUTER_ADDRESS = "0xde7E4f67Af577F29e5F3B995f9e67FD425F73621"
VENUS_OFFICIAL_MARKETS_SOURCE = "https://docs-v4.venus.io/deployed-contracts/markets"

NATIVE_BNB_PLACEHOLDER = "0x0000000000000000000000000000000000000000"
WBNB_ADDRESS = "0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c"

VENUS_CORE_MARKET_ADDRESSES = {
    "vBNB": "0xA07c5b74C9B40447a954e1466938b865b6BBea36",
    "vUSDT": "0xfD5840Cd36d94D7229439859C0112a4185BC0255",
    "vUSDC": "0xecA88125a5ADbe82614ffC12D0DB554E2e2867C8",
    "vBTC": "0x882C173bC7Ff3b7786CA16DfeD3DFFfb9Ee7847B",
    "vETH": "0xf508fCD89b8bd15579dc79A6827cB4686A3592c8",
    "vFDUSD": "0xC4eF4229FEc74Ccfe17B2bdeF7715fAC740BA0ba",
    "vXVS": "0x151B1e2635A717bcDc836ECd6FbB62B674FE3E1D",
}

COMPTROLLER_ABI = [
    {
        "inputs": [],
        "name": "getAllMarkets",
        "outputs": [{"internalType": "address[]", "name": "", "type": "address[]"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [{"internalType": "address", "name": "vToken", "type": "address"}],
        "name": "markets",
        "outputs": [
            {"internalType": "bool", "name": "isListed", "type": "bool"},
            {"internalType": "uint256", "name": "collateralFactorMantissa", "type": "uint256"},
            {"internalType": "bool", "name": "isVenus", "type": "bool"},
        ],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "closeFactorMantissa",
        "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "liquidationIncentiveMantissa",
        "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "protocolSeizeShareMantissa",
        "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "oracle",
        "outputs": [{"internalType": "address", "name": "", "type": "address"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [{"internalType": "address", "name": "account", "type": "address"}],
        "name": "getAccountLiquidity",
        "outputs": [
            {"internalType": "uint256", "name": "error", "type": "uint256"},
            {"internalType": "uint256", "name": "liquidity", "type": "uint256"},
            {"internalType": "uint256", "name": "shortfall", "type": "uint256"},
        ],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [{"internalType": "address", "name": "account", "type": "address"}],
        "name": "getAssetsIn",
        "outputs": [{"internalType": "address[]", "name": "", "type": "address[]"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [
            {"internalType": "address", "name": "vTokenBorrowed", "type": "address"},
            {"internalType": "address", "name": "vTokenCollateral", "type": "address"},
            {"internalType": "uint256", "name": "actualRepayAmount", "type": "uint256"},
        ],
        "name": "liquidateCalculateSeizeTokens",
        "outputs": [
            {"internalType": "uint256", "name": "error", "type": "uint256"},
            {"internalType": "uint256", "name": "seizeTokens", "type": "uint256"},
        ],
        "stateMutability": "view",
        "type": "function",
    },
]

VTOKEN_ABI = [
    {"inputs": [], "name": "symbol", "outputs": [{"internalType": "string", "name": "", "type": "string"}], "stateMutability": "view", "type": "function"},
    {"inputs": [], "name": "name", "outputs": [{"internalType": "string", "name": "", "type": "string"}], "stateMutability": "view", "type": "function"},
    {"inputs": [], "name": "decimals", "outputs": [{"internalType": "uint8", "name": "", "type": "uint8"}], "stateMutability": "view", "type": "function"},
    {"inputs": [], "name": "underlying", "outputs": [{"internalType": "address", "name": "", "type": "address"}], "stateMutability": "view", "type": "function"},
    {"inputs": [], "name": "exchangeRateStored", "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}], "stateMutability": "view", "type": "function"},
    {"inputs": [], "name": "borrowIndex", "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}], "stateMutability": "view", "type": "function"},
    {"inputs": [], "name": "totalSupply", "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}], "stateMutability": "view", "type": "function"},
    {"inputs": [], "name": "totalBorrows", "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}], "stateMutability": "view", "type": "function"},
    {"inputs": [], "name": "getCash", "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}], "stateMutability": "view", "type": "function"},
    {"inputs": [], "name": "isVToken", "outputs": [{"internalType": "bool", "name": "", "type": "bool"}], "stateMutability": "view", "type": "function"},
    {"inputs": [{"internalType": "address", "name": "account", "type": "address"}], "name": "borrowBalanceStored", "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}], "stateMutability": "view", "type": "function"},
    {"inputs": [{"internalType": "address", "name": "account", "type": "address"}], "name": "balanceOf", "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}], "stateMutability": "view", "type": "function"},
]

VENUS_ORACLE_ABI = [
    {
        "inputs": [{"internalType": "address", "name": "vToken", "type": "address"}],
        "name": "getUnderlyingPrice",
        "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function",
    }
]

ERC20_ABI = [
    {"inputs": [], "name": "symbol", "outputs": [{"internalType": "string", "name": "", "type": "string"}], "stateMutability": "view", "type": "function"},
    {"inputs": [], "name": "decimals", "outputs": [{"internalType": "uint8", "name": "", "type": "uint8"}], "stateMutability": "view", "type": "function"},
]

WBNB_ABI = [
    {"inputs": [], "name": "deposit", "outputs": [], "stateMutability": "payable", "type": "function"},
    {"inputs": [{"internalType": "uint256", "name": "wad", "type": "uint256"}], "name": "withdraw", "outputs": [], "stateMutability": "nonpayable", "type": "function"},
]

LIQUIDATOR_ABI = [
    {
        "inputs": [
            {"internalType": "address", "name": "vTokenBorrowed", "type": "address"},
            {"internalType": "address", "name": "vTokenCollateral", "type": "address"},
            {"internalType": "address", "name": "borrower", "type": "address"},
            {"internalType": "uint256", "name": "repayAmount", "type": "uint256"},
        ],
        "name": "liquidateBorrow",
        "outputs": [],
        "stateMutability": "payable",
        "type": "function",
    }
]

VENUS_EVENT_SPECS = {
    "Borrow": {
        "topic": Web3.keccak(text="Borrow(address,uint256,uint256,uint256)").hex(),
        "types": ["address", "uint256", "uint256", "uint256"],
        "account_index": 0,
    },
    "RepayBorrow": {
        "topic": Web3.keccak(text="RepayBorrow(address,address,uint256,uint256,uint256)").hex(),
        "types": ["address", "address", "uint256", "uint256", "uint256"],
        "account_index": 1,
    },
    "Mint": {
        "topic": Web3.keccak(text="Mint(address,uint256,uint256)").hex(),
        "types": ["address", "uint256", "uint256"],
        "account_index": 0,
    },
    "Redeem": {
        "topic": Web3.keccak(text="Redeem(address,uint256,uint256)").hex(),
        "types": ["address", "uint256", "uint256"],
        "account_index": 0,
    },
    "LiquidateBorrow": {
        "topic": Web3.keccak(text="LiquidateBorrow(address,address,uint256,address,uint256)").hex(),
        "types": ["address", "address", "uint256", "address", "uint256"],
        "account_index": 1,
    },
}
