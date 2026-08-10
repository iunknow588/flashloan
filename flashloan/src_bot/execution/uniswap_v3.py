from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from web3 import Web3


UNISWAP_V3_POOL_ABI = [
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
    {
        "inputs": [],
        "name": "fee",
        "outputs": [{"internalType": "uint24", "name": "", "type": "uint24"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "liquidity",
        "outputs": [{"internalType": "uint128", "name": "", "type": "uint128"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "slot0",
        "outputs": [
            {"internalType": "uint160", "name": "sqrtPriceX96", "type": "uint160"},
            {"internalType": "int24", "name": "tick", "type": "int24"},
            {"internalType": "uint16", "name": "observationIndex", "type": "uint16"},
            {"internalType": "uint16", "name": "observationCardinality", "type": "uint16"},
            {"internalType": "uint16", "name": "observationCardinalityNext", "type": "uint16"},
            {"internalType": "uint8", "name": "feeProtocol", "type": "uint8"},
            {"internalType": "bool", "name": "unlocked", "type": "bool"},
        ],
        "stateMutability": "view",
        "type": "function",
    },
]

UNISWAP_V3_QUOTER_V2_ABI = [
    {
        "inputs": [
            {
                "components": [
                    {"internalType": "address", "name": "tokenIn", "type": "address"},
                    {"internalType": "address", "name": "tokenOut", "type": "address"},
                    {"internalType": "uint256", "name": "amountIn", "type": "uint256"},
                    {"internalType": "uint24", "name": "fee", "type": "uint24"},
                    {"internalType": "uint160", "name": "sqrtPriceLimitX96", "type": "uint160"},
                ],
                "internalType": "struct IQuoterV2.QuoteExactInputSingleParams",
                "name": "params",
                "type": "tuple",
            }
        ],
        "name": "quoteExactInputSingle",
        "outputs": [
            {"internalType": "uint256", "name": "amountOut", "type": "uint256"},
            {"internalType": "uint160", "name": "sqrtPriceX96After", "type": "uint160"},
            {"internalType": "uint32", "name": "initializedTicksCrossed", "type": "uint32"},
            {"internalType": "uint256", "name": "gasEstimate", "type": "uint256"},
        ],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [
            {
                "components": [
                    {"internalType": "address", "name": "tokenIn", "type": "address"},
                    {"internalType": "address", "name": "tokenOut", "type": "address"},
                    {"internalType": "uint256", "name": "amountOut", "type": "uint256"},
                    {"internalType": "uint24", "name": "fee", "type": "uint24"},
                    {"internalType": "uint160", "name": "sqrtPriceLimitX96", "type": "uint160"},
                ],
                "internalType": "struct IQuoterV2.QuoteExactOutputSingleParams",
                "name": "params",
                "type": "tuple",
            }
        ],
        "name": "quoteExactOutputSingle",
        "outputs": [
            {"internalType": "uint256", "name": "amountIn", "type": "uint256"},
            {"internalType": "uint160", "name": "sqrtPriceX96After", "type": "uint160"},
            {"internalType": "uint32", "name": "initializedTicksCrossed", "type": "uint32"},
            {"internalType": "uint256", "name": "gasEstimate", "type": "uint256"},
        ],
        "stateMutability": "view",
        "type": "function",
    },
]

UNISWAP_V3_SWAP_ROUTER_ABI = [
    {
        "inputs": [
            {
                "components": [
                    {"internalType": "address", "name": "tokenIn", "type": "address"},
                    {"internalType": "address", "name": "tokenOut", "type": "address"},
                    {"internalType": "uint24", "name": "fee", "type": "uint24"},
                    {"internalType": "address", "name": "recipient", "type": "address"},
                    {"internalType": "uint256", "name": "deadline", "type": "uint256"},
                    {"internalType": "uint256", "name": "amountIn", "type": "uint256"},
                    {"internalType": "uint256", "name": "amountOutMinimum", "type": "uint256"},
                    {"internalType": "uint160", "name": "sqrtPriceLimitX96", "type": "uint160"},
                ],
                "internalType": "struct ISwapRouter.ExactInputSingleParams",
                "name": "params",
                "type": "tuple",
            }
        ],
        "name": "exactInputSingle",
        "outputs": [{"internalType": "uint256", "name": "amountOut", "type": "uint256"}],
        "stateMutability": "payable",
        "type": "function",
    },
    {
        "inputs": [
            {
                "components": [
                    {"internalType": "address", "name": "tokenIn", "type": "address"},
                    {"internalType": "address", "name": "tokenOut", "type": "address"},
                    {"internalType": "uint24", "name": "fee", "type": "uint24"},
                    {"internalType": "address", "name": "recipient", "type": "address"},
                    {"internalType": "uint256", "name": "deadline", "type": "uint256"},
                    {"internalType": "uint256", "name": "amountOut", "type": "uint256"},
                    {"internalType": "uint256", "name": "amountInMaximum", "type": "uint256"},
                    {"internalType": "uint160", "name": "sqrtPriceLimitX96", "type": "uint160"},
                ],
                "internalType": "struct ISwapRouter.ExactOutputSingleParams",
                "name": "params",
                "type": "tuple",
            }
        ],
        "name": "exactOutputSingle",
        "outputs": [{"internalType": "uint256", "name": "amountIn", "type": "uint256"}],
        "stateMutability": "payable",
        "type": "function",
    },
]


@dataclass(frozen=True)
class V3PoolSnapshot:
    pool_address: str
    token0: str
    token1: str
    fee: int
    liquidity: int
    sqrt_price_x96: int
    tick: int


@dataclass(frozen=True)
class V3PoolQuote:
    pool_address: str
    token_in: str
    token_out: str
    fee: int
    amount_in: int
    amount_out: int
    sqrt_price_x96_after: int
    initialized_ticks_crossed: int
    gas_estimate: int
    liquidity: int
    sqrt_price_x96: int
    tick: int


@dataclass(frozen=True)
class V3PoolExtrema:
    token_x: str
    token_y: str
    low: V3PoolSnapshot
    high: V3PoolSnapshot
    low_normalized_tick: int
    high_normalized_tick: int
    tick_delta: int
    scanned_count: int


def pool_contract(w3: Web3, pool_address: str):
    return w3.eth.contract(address=Web3.to_checksum_address(pool_address), abi=UNISWAP_V3_POOL_ABI)


def quoter_contract(w3: Web3, quoter_address: str):
    return w3.eth.contract(address=Web3.to_checksum_address(quoter_address), abi=UNISWAP_V3_QUOTER_V2_ABI)


def swap_router_contract(w3: Web3, router_address: str):
    return w3.eth.contract(address=Web3.to_checksum_address(router_address), abi=UNISWAP_V3_SWAP_ROUTER_ABI)


def snapshot_v3_pool(w3: Web3, pool_address: str) -> V3PoolSnapshot:
    contract = pool_contract(w3, pool_address)
    token0 = Web3.to_checksum_address(contract.functions.token0().call())
    token1 = Web3.to_checksum_address(contract.functions.token1().call())
    fee = int(contract.functions.fee().call())
    liquidity = int(contract.functions.liquidity().call())
    slot0 = contract.functions.slot0().call()
    sqrt_price_x96 = int(slot0[0])
    tick = int(slot0[1])
    return V3PoolSnapshot(
        pool_address=Web3.to_checksum_address(pool_address),
        token0=token0,
        token1=token1,
        fee=fee,
        liquidity=liquidity,
        sqrt_price_x96=sqrt_price_x96,
        tick=tick,
    )


def quote_v3_exact_input_single(
    w3: Web3,
    quoter_address: str,
    token_in: str,
    token_out: str,
    fee: int,
    amount_in: int,
    sqrt_price_limit_x96: int = 0,
) -> tuple[int, int, int, int]:
    quoter = quoter_contract(w3, quoter_address)
    amount_out, sqrt_price_x96_after, initialized_ticks_crossed, gas_estimate = quoter.functions.quoteExactInputSingle(
        {
            "tokenIn": Web3.to_checksum_address(token_in),
            "tokenOut": Web3.to_checksum_address(token_out),
            "amountIn": int(amount_in),
            "fee": int(fee),
            "sqrtPriceLimitX96": int(sqrt_price_limit_x96),
        }
    ).call()
    return int(amount_out), int(sqrt_price_x96_after), int(initialized_ticks_crossed), int(gas_estimate)


def quote_v3_exact_output_single(
    w3: Web3,
    quoter_address: str,
    token_in: str,
    token_out: str,
    fee: int,
    amount_out: int,
    sqrt_price_limit_x96: int = 0,
) -> tuple[int, int, int, int]:
    quoter = quoter_contract(w3, quoter_address)
    amount_in, sqrt_price_x96_after, initialized_ticks_crossed, gas_estimate = quoter.functions.quoteExactOutputSingle(
        {
            "tokenIn": Web3.to_checksum_address(token_in),
            "tokenOut": Web3.to_checksum_address(token_out),
            "amountOut": int(amount_out),
            "fee": int(fee),
            "sqrtPriceLimitX96": int(sqrt_price_limit_x96),
        }
    ).call()
    return int(amount_in), int(sqrt_price_x96_after), int(initialized_ticks_crossed), int(gas_estimate)


def quote_v3_pool(w3: Web3, pool_address: str, quoter_address: str, amount_in: int, token_in: str | None = None) -> V3PoolQuote:
    snapshot = snapshot_v3_pool(w3, pool_address)
    if token_in is None:
        token_in = snapshot.token0
    token_in = Web3.to_checksum_address(token_in)
    if token_in not in {snapshot.token0, snapshot.token1}:
        raise ValueError("token_in is not part of the provided V3 pool")
    token_out = snapshot.token1 if token_in == snapshot.token0 else snapshot.token0
    amount_out, sqrt_price_x96_after, initialized_ticks_crossed, gas_estimate = quote_v3_exact_input_single(
        w3,
        quoter_address,
        token_in,
        token_out,
        snapshot.fee,
        amount_in,
    )
    return V3PoolQuote(
        pool_address=snapshot.pool_address,
        token_in=token_in,
        token_out=token_out,
        fee=snapshot.fee,
        amount_in=int(amount_in),
        amount_out=amount_out,
        sqrt_price_x96_after=sqrt_price_x96_after,
        initialized_ticks_crossed=initialized_ticks_crossed,
        gas_estimate=gas_estimate,
        liquidity=snapshot.liquidity,
        sqrt_price_x96=snapshot.sqrt_price_x96,
        tick=snapshot.tick,
    )


def quote_v3_pool_pair(w3: Web3, pool_address: str, quoter_address: str, amount_in: int) -> dict[str, Any]:
    snapshot = snapshot_v3_pool(w3, pool_address)
    forward = quote_v3_pool(w3, pool_address, quoter_address, amount_in, token_in=snapshot.token0)
    reverse = quote_v3_pool(w3, pool_address, quoter_address, amount_in, token_in=snapshot.token1)
    return {
        "pool": snapshot.pool_address,
        "token0": snapshot.token0,
        "token1": snapshot.token1,
        "fee": snapshot.fee,
        "liquidity": snapshot.liquidity,
        "sqrtPriceX96": snapshot.sqrt_price_x96,
        "tick": snapshot.tick,
        "forward": forward,
        "reverse": reverse,
    }


def normalized_v3_tick(snapshot: V3PoolSnapshot, token_x: str, token_y: str) -> int:
    token_x = Web3.to_checksum_address(token_x)
    token_y = Web3.to_checksum_address(token_y)
    if snapshot.token0 == token_x and snapshot.token1 == token_y:
        return int(snapshot.tick)
    if snapshot.token0 == token_y and snapshot.token1 == token_x:
        return -int(snapshot.tick)
    raise ValueError("pool does not connect the requested token pair")


def select_v3_pool_extrema(
    w3: Web3,
    token_x: str,
    token_y: str,
    pool_addresses: list[str],
    *,
    min_liquidity: int = 1,
) -> V3PoolExtrema:
    token_x = Web3.to_checksum_address(token_x)
    token_y = Web3.to_checksum_address(token_y)
    low: V3PoolSnapshot | None = None
    high: V3PoolSnapshot | None = None
    low_tick: int | None = None
    high_tick: int | None = None
    scanned_count = 0

    for pool_address in pool_addresses:
        if not pool_address:
            continue
        snapshot = snapshot_v3_pool(w3, pool_address)
        if snapshot.liquidity < min_liquidity:
            continue
        tick = normalized_v3_tick(snapshot, token_x, token_y)
        scanned_count += 1
        if low is None or low_tick is None or tick < low_tick:
            low = snapshot
            low_tick = tick
        if high is None or high_tick is None or tick > high_tick:
            high = snapshot
            high_tick = tick

    if low is None or high is None or low_tick is None or high_tick is None:
        raise ValueError("not enough valid V3 pools for extrema selection")
    if low.pool_address == high.pool_address:
        raise ValueError("low and high extrema resolve to the same V3 pool")

    return V3PoolExtrema(
        token_x=token_x,
        token_y=token_y,
        low=low,
        high=high,
        low_normalized_tick=low_tick,
        high_normalized_tick=high_tick,
        tick_delta=high_tick - low_tick,
        scanned_count=scanned_count,
    )
