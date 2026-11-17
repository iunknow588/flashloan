"""Shared liquidity reserve/depth helpers for Robinhood testnet V4 pools.

The depth verdict of a testnet pool follows exactly the same structural rule
as the mainnet pool index: it is expressed in the pool's own tokens through
the on-chain price-impact capacity, never through a USD-denominated TVL
reading.

``capacity(i) = max { a : priceImpact(a) <= epsilon }``

The price impact is measured against the pool's own mid price, so no external
ETH/USD or equity quote is involved. A pool whose two sides are both stock
tokens has no USDG route on the testnet venue, yet it is a perfectly usable
candidate: its depth is fully determined by its own on-chain state. The
earlier TVL-first classification marked every such pool ``unpriced`` and
therefore excluded it from the observation pool, which in turn starved the
closed-loop builder of the middle hop and left the review evidence
incomplete.

The USD reading stays a human-facing observation only. It never changes the
admission verdict.
"""

from __future__ import annotations

import os
from decimal import Decimal
from typing import Any

from core.config.fee_policy import MAX_CANDIDATE_FIXED_FEE
from dex.intent_trade.chains.robinhood.v4_pool_capacity import (
    DEPTH_BAND_BASIS as CAPACITY_DEPTH_BAND_BASIS,
    DEPTH_BASIS as CAPACITY_DEPTH_BASIS,
    DEFAULT_HOP_PRICE_IMPACT_LIMIT_BPS as CAPACITY_PRICE_IMPACT_LIMIT_BPS,
    binding_side_capacity,
    capacity_band,
    capacity_from_sqrt_price,
    raw_to_decimal_text,
)
from dex.intent_trade.chains.robinhood_testnet.v4_pool_rpc import ZERO_ADDRESS


Q96 = Decimal(2) ** 96
DEFAULT_ETH_PRICE_USD = Decimal(str(os.getenv("ROBINHOOD_TESTNET_ETH_PRICE_USD", "3000")))


def _int_or_none(value: object) -> int | None:
    try:
        return int(str(value or "").strip())
    except (TypeError, ValueError):
        return None


def _liquidity_reserve_metrics(pool: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    """Estimate active-liquidity token reserves and the capacity verdict.

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
        return {
            "liquidityIndex": 0,
            "hasLiquidity": False,
            "estimatedTvlUsd": 0,
            "depthBasis": CAPACITY_DEPTH_BASIS,
        }
    decimals0 = _int_or_none(pool.get("token0Decimals") or pool.get("token0_decimals")) or 18
    decimals1 = _int_or_none(pool.get("token1Decimals") or pool.get("token1_decimals")) or 18
    sqrt_price = Decimal(sqrt_price_x96) / Q96
    liquidity_dec = Decimal(liquidity)
    amount0_raw = liquidity_dec / sqrt_price
    amount1_raw = liquidity_dec * sqrt_price
    amount0 = amount0_raw / (Decimal(10) ** decimals0)
    amount1 = amount1_raw / (Decimal(10) ** decimals1)
    # The capacity is the primary depth signal. It is denominated in the
    # pool's own tokens and never in USD, so it does not depend on any
    # external price feed. The same maths and the same epsilon are used by
    # the Rust pool manager and by the mainnet pool index.
    capacity: dict[str, Any] = {}
    try:
        fee_pips = int(state.get("lpFee") or 0)
        capacity = capacity_from_sqrt_price(
            sqrt_price_x96,
            liquidity,
            fee_pips,
            float(CAPACITY_PRICE_IMPACT_LIMIT_BPS),
        )
    except Exception:
        capacity = {}
    # DEP-4.2: the depth band is decided on the decimals-normalised
    # binding-side capacity, not on the raw maximum of the two sides. The
    # raw maximum mixed 6-decimal and 18-decimal amounts in one comparison
    # and hid pools whose weaker side cannot absorb anything.
    binding_capacity = binding_side_capacity(
        capacity.get("capacityToken0Raw"),
        capacity.get("capacityToken1Raw"),
        decimals0,
        decimals1,
    )
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
        "capacityToken0Raw": capacity.get("capacityToken0Raw"),
        "capacityToken1Raw": capacity.get("capacityToken1Raw"),
        "capacityRaw": capacity.get("capacityRaw"),
        "capacityToken0": (
            raw_to_decimal_text(capacity["capacityToken0Raw"], decimals0)
            if capacity.get("capacityToken0Raw") is not None
            else None
        ),
        "capacityToken1": (
            raw_to_decimal_text(capacity["capacityToken1Raw"], decimals1)
            if capacity.get("capacityToken1Raw") is not None
            else None
        ),
        "capacityPriceImpactBps": capacity.get("priceImpactBps"),
        "capacityPriceImpactLimitBps": capacity.get("priceImpactLimitBps"),
        "capacityBasis": capacity.get("depthBasis") or CAPACITY_DEPTH_BASIS,
        "capacityBindingRaw": binding_capacity,
        "capacityBand": capacity_band(binding_capacity),
        "depthBasis": CAPACITY_DEPTH_BASIS,
    }


def _liquidity_depth_status(pool: dict[str, Any]) -> dict[str, Any]:
    """Classify a pool into a depth band.

    The band is derived from the on-chain price-impact capacity, which is
    denominated in the pool's own tokens. The USD reading is reported for
    human observation only and can never change the verdict: a TVL estimate
    depends on an external price feed that does not exist inside the
    arbitrage environment, and on the testnet venue the only quote asset is
    ETH, so a stock-to-stock pool has no USD route at all.

    The band boundaries are shared with the Rust pool manager so both sides
    reach the same verdict for the same on-chain state. The boundary operator
    is ``>=`` on both sides.
    """
    try:
        liquidity_index = float(str(pool.get("liquidityIndex") or "0"))
        tvl_text = pool.get("estimatedTvlUsd")
        tvl = float(tvl_text) if tvl_text not in (None, "") else 0.0
    except (TypeError, ValueError):
        liquidity_index, tvl = 0.0, 0.0
    capacity_raw = _int_or_none(pool.get("capacityRaw"))
    # DEP-4.2: the band is decided on the decimals-normalised binding-side
    # capacity. A pool is only as deep as its weaker side, and raw units are
    # not comparable across tokens with different decimals.
    binding_capacity = pool.get("capacityBindingRaw")
    if binding_capacity is None:
        binding_capacity = binding_side_capacity(
            pool.get("capacityToken0Raw"),
            pool.get("capacityToken1Raw"),
            _int_or_none(pool.get("token0Decimals") or pool.get("token0_decimals")),
            _int_or_none(pool.get("token1Decimals") or pool.get("token1_decimals")),
        )
    band = capacity_band(binding_capacity)
    if liquidity_index <= 0:
        label, level, reason = "no_liquidity", "empty", "liquidity_is_zero"
    elif capacity_raw is None:
        # The pool has liquidity but the capacity could not be computed. The
        # state is unknown, not shallow.
        label, level, reason = "unpriced", "unpriced", "liquidity_present_but_no_capacity"
    elif binding_capacity is None:
        # The capacity is known but the token decimals are not, so the
        # normalised capacity cannot be decided. The state is unknown, not
        # shallow.
        label, level, reason = "unpriced", "unpriced", "liquidity_present_but_no_capacity"
    elif band == "deep":
        label, level, reason = "deep", "deep", "capacity_within_10bps_limit"
    elif band == "medium":
        # The band labels are shared with the Rust pool manager, which uses
        # `medium` rather than `liquid` for the middle capacity band.
        label, level, reason = "medium", "medium", "capacity_within_10bps_limit"
    elif band == "shallow":
        label, level, reason = "thin", "thin", "capacity_within_10bps_limit"
    else:
        label, level, reason = "thin", "thin", "binding_capacity_below_shallow_band"
    return {
        "label": label,
        "level": level,
        "reason": reason,
        "liquidityIndex": float(liquidity_index),
        "estimatedTvlUsd": tvl if tvl > 0 else None,
        "capacityRaw": capacity_raw,
        "capacityBindingRaw": binding_capacity,
        "capacityBand": band,
        "depthBandBasis": CAPACITY_DEPTH_BAND_BASIS,
        "depthBasis": CAPACITY_DEPTH_BASIS,
    }


def _is_usable_liquidity_target(pool: dict[str, Any]) -> bool:
    try:
        fee = int(str(pool.get("fee") or "0"))
    except (TypeError, ValueError):
        return False
    if fee <= 0 or fee > MAX_CANDIDATE_FIXED_FEE or fee & 8388608:
        return False
    hooks = str(pool.get("hooks") or "").lower()
    return hooks in {"", ZERO_ADDRESS.lower()}
