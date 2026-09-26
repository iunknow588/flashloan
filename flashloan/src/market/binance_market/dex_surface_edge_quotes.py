"""Edge quote helpers for DEX surface route analysis."""

from __future__ import annotations

from typing import Any

from market.binance_market.dex_surface_graph import _pool_matches_edge
from market.binance_market.dex_surface_types import (
    _number,
    _pool_protocol,
    _pool_side_index,
    _token_address,
)
from market.binance_market.dex_surface_v4 import (
    _fee_bps,
    _is_constant_product_pool,
    _market_reference_rate,
    _pool_reserve_amount,
    _pool_reserve_usd,
    _pool_tvl_usd,
    _reference_price_quote,
)
from market.binance_market.dex_surface_v4_edge_quote import _v4_edge_quote


def _edge_quote(
    *,
    pool: dict[str, Any],
    from_symbol: str,
    to_symbol: str,
    from_address: str | None = None,
    to_address: str | None = None,
    input_amount: float | None,
    input_usd: float,
    price_map: dict[str, float],
    chain_id: int | None = None,
) -> dict[str, Any] | None:
    in_index = _pool_side_index(pool, from_symbol, from_address)
    out_index = _pool_side_index(pool, to_symbol, to_address)
    if in_index is None or out_index is None or in_index == out_index:
        return None
    protocol = _pool_protocol(pool)
    if protocol == "uniswap_v4":
        return _v4_edge_quote(
            pool=pool,
            from_symbol=from_symbol,
            to_symbol=to_symbol,
            from_address=from_address,
            to_address=to_address,
            input_amount=input_amount,
            input_usd=input_usd,
            price_map=price_map,
            chain_id=chain_id,
        )
    if not _is_constant_product_pool(pool):
        return _reference_price_quote(
            pool=pool,
            from_symbol=from_symbol,
            to_symbol=to_symbol,
            from_address=from_address,
            to_address=to_address,
            input_amount=input_amount,
            input_usd=input_usd,
            price_map=price_map,
            reason="concentrated_liquidity_pool_requires_quoter",
            chain_id=chain_id,
        )
    fee_bps = _fee_bps(pool)
    fee_factor = max(0.0, 1.0 - fee_bps / 10_000.0)
    reserve_in_amount = _pool_reserve_amount(pool, in_index)
    reserve_out_amount = _pool_reserve_amount(pool, out_index)
    reserve_in_usd = _pool_reserve_usd(pool, in_index, price_map)
    reserve_out_usd = _pool_reserve_usd(pool, out_index, price_map)
    if reserve_in_usd is None or reserve_out_usd is None:
        tvl = _pool_tvl_usd(pool)
        if tvl > 0:
            reserve_in_usd = reserve_in_usd or tvl / 2
            reserve_out_usd = reserve_out_usd or tvl / 2
    if not reserve_in_usd or not reserve_out_usd or input_usd <= 0:
        return None
    reference_rate = _market_reference_rate(pool, from_symbol, to_symbol)
    implied_rate = reserve_out_amount / reserve_in_amount if reserve_in_amount and reserve_out_amount else None
    if reference_rate and implied_rate:
        deviation_bps = abs(implied_rate / reference_rate - 1.0) * 10_000.0
        if deviation_bps > 500.0:
            return _reference_price_quote(
                pool=pool,
                from_symbol=from_symbol,
                to_symbol=to_symbol,
                from_address=from_address,
                to_address=to_address,
                input_amount=input_amount,
                input_usd=input_usd,
                price_map=price_map,
                reason="pool_reserve_price_deviates_from_market_reference",
                chain_id=chain_id,
            )
    input_after_fee_usd = input_usd * fee_factor
    output_usd = reserve_out_usd * input_after_fee_usd / (reserve_in_usd + input_after_fee_usd)
    spot_output_usd = input_after_fee_usd * reserve_out_usd / reserve_in_usd if reserve_in_usd > 0 else 0.0
    price_impact_bps = max(0.0, (1.0 - output_usd / spot_output_usd) * 10_000.0) if spot_output_usd > 0 else None
    output_amount = None
    if input_amount is not None and reserve_in_amount and reserve_out_amount:
        input_after_fee = input_amount * fee_factor
        output_amount = reserve_out_amount * input_after_fee / (reserve_in_amount + input_after_fee)
        price = price_map.get(to_symbol)
        if price and price > 0:
            output_usd = output_amount * price
    if output_amount is None:
        price = price_map.get(to_symbol)
        if price and price > 0:
            output_amount = output_usd / price
    effective_rate = output_amount / input_amount if input_amount and output_amount is not None else None
    return {
        "pool": pool.get("pool"),
        "protocol": pool.get("protocol") or pool.get("dex"),
        "dex": pool.get("dex") or pool.get("protocol"),
        "fee": pool.get("fee"),
        "feeBps": fee_bps,
        "fromSymbol": from_symbol,
        "toSymbol": to_symbol,
        "tokenIn": _token_address(from_address),
        "tokenOut": _token_address(to_address),
        "inputAmount": input_amount,
        "inputUsd": input_usd,
        "outputAmount": output_amount,
        "outputUsd": output_usd,
        "effectiveRate": effective_rate,
        "reserveInUsd": reserve_in_usd,
        "reserveOutUsd": reserve_out_usd,
        "priceImpactBps": price_impact_bps,
        "depthBand": pool.get("depthBand") or {},
        "quoteSource": "constant_product_v2_static_reserve",
        "executionTrust": "static_v2_probe",
    }


def _best_edge_quote(
    *,
    pools: list[dict[str, Any]],
    from_symbol: str,
    to_symbol: str,
    from_address: str | None = None,
    to_address: str | None = None,
    input_amount: float | None,
    input_usd: float,
    price_map: dict[str, float],
    chain_id: int | None = None,
) -> dict[str, Any] | None:
    quotes = [
        quote
        for pool in pools
        if _pool_matches_edge(pool, from_symbol, to_symbol, from_address, to_address)
        for quote in [
            _edge_quote(
                pool=pool,
                from_symbol=from_symbol,
                to_symbol=to_symbol,
                from_address=from_address,
                to_address=to_address,
                input_amount=input_amount,
                input_usd=input_usd,
                price_map=price_map,
                chain_id=chain_id,
            )
        ]
        if quote is not None
    ]
    if not quotes:
        return None
    return max(quotes, key=lambda item: (_number(item.get("outputUsd")), _number(item.get("reserveOutUsd"))))


__all__ = ["_best_edge_quote", "_edge_quote"]
