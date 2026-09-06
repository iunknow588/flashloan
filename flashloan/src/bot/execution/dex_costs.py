from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from web3 import Web3


TRADER_JOE_V2_ROUTER = "0x60aE616a2155Ee3d9A68541Ba4544862310933d4"
USDC = "0xB97EF9Ef8734C71904D8002F8b6Bc66Dd9c48a6E"
USDC_DECIMALS = 6
DEFAULT_TRADE_USD_AMOUNTS = (100.0, 1000.0)

ROUTER_ABI = [
    {
        "inputs": [
            {"internalType": "uint256", "name": "amountIn", "type": "uint256"},
            {"internalType": "address[]", "name": "path", "type": "address[]"},
        ],
        "name": "getAmountsOut",
        "outputs": [{"internalType": "uint256[]", "name": "amounts", "type": "uint256[]"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [
            {"internalType": "uint256", "name": "amountOut", "type": "uint256"},
            {"internalType": "address[]", "name": "path", "type": "address[]"},
        ],
        "name": "getAmountsIn",
        "outputs": [{"internalType": "uint256[]", "name": "amounts", "type": "uint256[]"}],
        "stateMutability": "view",
        "type": "function",
    }
]


@dataclass(frozen=True)
class TokenCostConfig:
    symbol: str
    token_address: str
    decimals: int


@dataclass(frozen=True)
class DexCost:
    symbol: str
    amount_usd: float
    dex_name: str
    buy_cost_percent: float
    sell_cost_percent: float
    roundtrip_cost_percent: float
    buy_price_usd: float
    sell_price_usd: float
    reference_price_usd: float
    token_amount: float


TOKEN_COSTS: dict[str, TokenCostConfig] = {
    "AVAXUSDT": TokenCostConfig("WAVAX", "0xB31f66AA3C1e785363F0875A1B74E27b85FD66c7", 18),
    "ETHUSDT": TokenCostConfig("WETH.e", "0x49D5c2BdFfac6CE2BFdB6640F4F80f226bc10bAB", 18),
    "BTCUSDT": TokenCostConfig("BTC.b", "0x152b9d0FdC40C096757F570A51E494bd4b943E50", 8),
    "AAVEUSDT": TokenCostConfig("AAVE.e", "0x63a72806098Bd3D9520cC43356dD78afe5D386D9", 18),
}


def parse_trade_usd_amounts(raw: Optional[str]) -> list[float]:
    if not raw:
        return list(DEFAULT_TRADE_USD_AMOUNTS)
    values: list[float] = []
    for part in raw.split(","):
        text = part.strip()
        if not text:
            continue
        value = float(text)
        if value <= 0:
            raise ValueError("DEX_COST_USD_AMOUNTS values must be positive")
        values.append(value)
    return values or list(DEFAULT_TRADE_USD_AMOUNTS)


def _to_units(value: float, decimals: int) -> int:
    return int(round(value * (10**decimals)))


def _from_units(value: int, decimals: int) -> float:
    return value / float(10**decimals)


def _get_amount_out(router, amount_in: int, path: list[str]) -> int:
    return int(router.functions.getAmountsOut(amount_in, path).call()[-1])


def estimate_symbol_cost(
    rpc_url: str,
    symbol: str,
    amount_usd: float,
    reference_price_usd: float,
    router_address: str = TRADER_JOE_V2_ROUTER,
) -> DexCost | None:
    config = TOKEN_COSTS.get(symbol)
    if config is None or reference_price_usd <= 0:
        return None

    w3 = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": 10}))
    router = w3.eth.contract(address=Web3.to_checksum_address(router_address), abi=ROUTER_ABI)
    usdc = Web3.to_checksum_address(USDC)
    token = Web3.to_checksum_address(config.token_address)

    usdc_in = _to_units(amount_usd, USDC_DECIMALS)
    token_out_units = _get_amount_out(router, usdc_in, [usdc, token])
    token_out = _from_units(token_out_units, config.decimals)
    if token_out <= 0:
        return None

    buy_price = amount_usd / token_out
    buy_cost_percent = (buy_price / reference_price_usd - 1.0) * 100.0

    fair_token_units = _to_units(amount_usd / reference_price_usd, config.decimals)
    usdc_out_units = _get_amount_out(router, fair_token_units, [token, usdc])
    usdc_out = _from_units(usdc_out_units, USDC_DECIMALS)
    sell_price = usdc_out / (amount_usd / reference_price_usd)
    sell_cost_percent = (1.0 - sell_price / reference_price_usd) * 100.0

    roundtrip_usdc_out_units = _get_amount_out(router, token_out_units, [token, usdc])
    roundtrip_usdc_out = _from_units(roundtrip_usdc_out_units, USDC_DECIMALS)
    roundtrip_cost_percent = (1.0 - roundtrip_usdc_out / amount_usd) * 100.0

    return DexCost(
        symbol=symbol,
        amount_usd=amount_usd,
        dex_name="Trader Joe V2",
        buy_cost_percent=buy_cost_percent,
        sell_cost_percent=sell_cost_percent,
        roundtrip_cost_percent=roundtrip_cost_percent,
        buy_price_usd=buy_price,
        sell_price_usd=sell_price,
        reference_price_usd=reference_price_usd,
        token_amount=token_out,
    )
