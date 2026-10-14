"""Shared liquidity reserve/depth helpers for Robinhood testnet V4 pools."""

from __future__ import annotations

import os
from decimal import Decimal
from typing import Any

from dex.intent_trade.chains.robinhood_testnet.v4_pool_rpc import ZERO_ADDRESS


Q96 = Decimal(2) ** 96
DEFAULT_ETH_PRICE_USD = Decimal(str(os.getenv("ROBINHOOD_TESTNET_ETH_PRICE_USD", "3000")))


def _int_or_none(value: object) -> int | None:
    try:
        return int(str(value or "").strip())
    except (TypeError, ValueError):
        return None


def _liquidity_reserve_metrics(pool: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    """Estimate active-liquidity token reserves using current sqrtPrice.

    Uniswap v4 stores active liquidity ``L`` in PoolManager storage. For a
    given current sqrt price the full-range approximation is::

        amount0 = L / sqrtPrice
        amount1 = L * sqrtPrice

    The raw liquidity number is authoritative; the token/USD values are an
    index used for candidate depth ranking.
    """
    sqrt_price_x96 = _int_or_none(state.get("sqrtPriceX96"))
    liquidity = _int_or_none(state.get("liquidity"))
    if not sqrt_price_x96 or sqrt_price_x96 <= 0 or liquidity is None or liquidity <= 0:
        return {"liquidityIndex": 0, "hasLiquidity": False, "estimatedTvlUsd": 0}
    decimals0 = _int_or_none(pool.get("token0Decimals") or pool.get("token0_decimals")) or 18
    decimals1 = _int_or_none(pool.get("token1Decimals") or pool.get("token1_decimals")) or 18
    sqrt_price = Decimal(sqrt_price_x96) / Q96
    liquidity_dec = Decimal(liquidity)
    amount0_raw = liquidity_dec / sqrt_price
    amount1_raw = liquidity_dec * sqrt_price
    amount0 = amount0_raw / (Decimal(10) ** decimals0)
    amount1 = amount1_raw / (Decimal(10) ** decimals1)
    token0 = str(pool.get("token0") or "").lower()
    token1 = str(pool.get("token1") or "").lower()
    usd_price0: Decimal | None = None
    usd_price1: Decimal | None = None
    if token0 == ZERO_ADDRESS.lower():
        usd_price0 = DEFAULT_ETH_PRICE_USD
    elif token1 == ZERO_ADDRESS.lower():
        usd_price1 = DEFAULT_ETH_PRICE_USD
    estimated_tvl_usd = Decimal(0)
    reserve_usd0: Decimal | None = amount0 * usd_price0 if usd_price0 is not None else None
    reserve_usd1: Decimal | None = amount1 * usd_price1 if usd_price1 is not None else None
    if reserve_usd0 is not None:
        estimated_tvl_usd += reserve_usd0
    if reserve_usd1 is not None:
        estimated_tvl_usd += reserve_usd1
    return {
        "hasLiquidity": True,
        "liquidityIndex": float((liquidity_dec / Decimal(10) ** 18).quantize(Decimal("0.00000001"))),
        "token0Reserve": str(amount0.normalize()),
        "token1Reserve": str(amount1.normalize()),
        "token0ReserveUsd": str(reserve_usd0.normalize()) if reserve_usd0 is not None else None,
        "token1ReserveUsd": str(reserve_usd1.normalize()) if reserve_usd1 is not None else None,
        "estimatedTvlUsd": str(estimated_tvl_usd.normalize()) if estimated_tvl_usd > 0 else None,
        "sqrtPriceX96": str(sqrt_price_x96),
        "tick": state.get("tick"),
        "lpFee": state.get("lpFee"),
        "protocolFee": state.get("protocolFee"),
    }


def _liquidity_depth_status(pool: dict[str, Any]) -> dict[str, Any]:
    try:
        liquidity_index = float(str(pool.get("liquidityIndex") or "0"))
        tvl_text = pool.get("estimatedTvlUsd")
        tvl = float(tvl_text) if tvl_text not in (None, "") else 0.0
    except (TypeError, ValueError):
        liquidity_index, tvl = 0.0, 0.0
    if liquidity_index <= 0:
        label, level, reason = "no_liquidity", "empty", "liquidity_is_zero"
    elif tvl > 1_000_000:
        label, level, reason = "deep", "deep", "liquidity_available"
    elif tvl > 100_000:
        label, level, reason = "liquid", "liquid", "liquidity_available"
    elif tvl > 10_000:
        label, level, reason = "thin", "thin", "liquidity_available"
    elif tvl > 0:
        label, level, reason = "thin", "thin", "liquidity_below_10k_usd"
    elif liquidity_index > 0:
        label, level, reason = "unpriced", "unpriced", "liquidity_present_but_no_usd_price"
    else:
        label, level, reason = "no_liquidity", "empty", "liquidity_is_zero"
    return {
        "label": label,
        "level": level,
        "reason": reason,
        "liquidityIndex": float(liquidity_index),
        "estimatedTvlUsd": tvl if tvl > 0 else None,
    }


def _is_usable_liquidity_target(pool: dict[str, Any]) -> bool:
    try:
        fee = int(str(pool.get("fee") or "0"))
    except (TypeError, ValueError):
        return False
    if fee <= 0 or fee > 3000 or fee & 8388608:
        return False
    hooks = str(pool.get("hooks") or "").lower()
    return hooks in {"", ZERO_ADDRESS.lower()}
