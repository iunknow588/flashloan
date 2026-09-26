"""Uniswap V4 edge quote helpers for DEX surface routes."""

from __future__ import annotations

from typing import Any


def _v4_edge_quote(
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
    from market.binance_market.dex_surface_route_quotes import (
        build_v4_quote_request,
        _chain_network_name,
        _quote_failure_status,
        _v4_live_quote_enabled,
        load_v4_sdk_adapter,
        quote_v4_exact_input_single,
    )
    from market.binance_market.dex_surface_v4 import (
        _fee_bps,
        _pool_reserve_usd,
        _pool_token_decimals_trusted,
        _reference_price_quote,
        _v4_token_decimals,
    )
    from market.binance_market.dex_surface_types import (
        _human_from_raw,
        _norm_symbol,
        _number,
        _pool_address,
        _pool_tokens,
        _raw_from_human,
        _token_address,
    )

    in_index = None
    out_index = None
    from market.binance_market.dex_surface_types import _pool_side_index

    in_index = _pool_side_index(pool, from_symbol, from_address)
    out_index = _pool_side_index(pool, to_symbol, to_address)
    if in_index is None or out_index is None or in_index == out_index:
        return None

    if _v4_live_quote_enabled(chain_id=chain_id) and input_amount is not None and input_amount > 0:
        network = _chain_network_name(chain_id) or str(pool.get("network") or "").strip().lower()
        adapter = None
        if network:
            try:
                adapter = load_v4_sdk_adapter(network=network, chain_id=chain_id)
            except (AttributeError, ImportError, RuntimeError, TypeError):
                adapter = None
        if adapter is not None:
            try:
                input_decimals = _v4_token_decimals(pool, in_index, chain_id=chain_id)
                output_decimals = _v4_token_decimals(pool, out_index, chain_id=chain_id)
                pool_tokens = _pool_tokens(pool)
                token_in = pool_tokens[in_index].get("address") if len(pool_tokens) > in_index else None
                token_out = pool_tokens[out_index].get("address") if len(pool_tokens) > out_index else None
                if token_in and token_out and input_decimals is not None and output_decimals is not None:
                    amount_in_raw = _raw_from_human(input_amount, input_decimals)
                    request = build_v4_quote_request(
                        network=network,
                        chain_id=chain_id,
                        pool=_pool_address(pool),
                        token_in=_token_address(token_in),
                        token_out=_token_address(token_out),
                        amount_in_raw=amount_in_raw,
                        block_number=None,
                        fee=pool.get("fee"),
                        route_symbols=(from_symbol, to_symbol),
                    )
                    request["poolState"] = {
                        "pool": _pool_address(pool),
                        "token0": _token_address(pool.get("token0")),
                        "token1": _token_address(pool.get("token1")),
                        "token0Symbol": _norm_symbol(pool.get("token0Symbol")),
                        "token1Symbol": _norm_symbol(pool.get("token1Symbol")),
                        "token0Decimals": _v4_token_decimals(pool, 0, chain_id=chain_id),
                        "token1Decimals": _v4_token_decimals(pool, 1, chain_id=chain_id),
                        "tickSpacing": pool.get("tickSpacing"),
                        "sqrtPriceX96": pool.get("sqrtPriceX96") or pool.get("sqrt_price_x96"),
                    }
                    quote = quote_v4_exact_input_single(adapter=adapter, request=request)
                    amount_out_raw = int(quote["amountOutRaw"])
                    output_amount = _human_from_raw(amount_out_raw, output_decimals)
                    output_price = _number(price_map.get(to_symbol))
                    token_decimals_trusted = _pool_token_decimals_trusted(pool, in_index, chain_id=chain_id) and _pool_token_decimals_trusted(
                        pool,
                        out_index,
                        chain_id=chain_id,
                    )
                    output_usd = output_amount * output_price if output_price > 0 else None
                    return {
                        "pool": pool.get("pool"),
                        "protocol": pool.get("protocol") or pool.get("dex"),
                        "dex": pool.get("dex") or pool.get("protocol"),
                        "fee": pool.get("fee"),
                        "feeBps": _fee_bps(pool),
                        "fromSymbol": from_symbol,
                        "toSymbol": to_symbol,
                        "tokenIn": _token_address(from_address),
                        "tokenOut": _token_address(to_address),
                        "amountInRaw": str(amount_in_raw),
                        "amountOutRaw": str(amount_out_raw),
                        "inputAmount": input_amount,
                        "inputUsd": input_usd,
                        "outputAmount": output_amount,
                        "outputUsd": output_usd,
                        "effectiveRate": amount_out_raw / amount_in_raw if amount_in_raw > 0 else None,
                        "reserveInUsd": _pool_reserve_usd(pool, in_index, price_map),
                        "reserveOutUsd": _pool_reserve_usd(pool, out_index, price_map),
                        "priceImpactBps": None,
                        "depthBand": pool.get("depthBand") or {},
                        "quoteSource": "uniswap_v4_live_adapter_quote",
                        "executionTrust": "live_v4_adapter",
                        "priceEvidenceSource": "chain_live_quote",
                        "priceEvidenceMode": "live_verified",
                        "livePriceVerified": bool(output_usd is not None),
                        "trustedUsdPrice": bool(output_usd is not None),
                        "tokenDecimalsTrusted": token_decimals_trusted,
                        "tokenDecimalAssumption": None if token_decimals_trusted else "missing_token_decimals_untrusted",
                        "quoteMode": quote.get("quoteMode") or "uniswap_v4_sdk_exact_input_single",
                        "adapterRequired": False,
                        "sdkAdapter": quote.get("sdkAdapter") or quote.get("quoteAdapter"),
                        "quoteAdapter": quote.get("quoteAdapter") or quote.get("sdkAdapter"),
                    }
            except Exception as exc:
                live_quote_error = str(exc)
            else:
                live_quote_error = ""
        else:
            live_quote_error = ""
        if live_quote_error:
            quote = _reference_price_quote(
                pool=pool,
                from_symbol=from_symbol,
                to_symbol=to_symbol,
                from_address=from_address,
                to_address=to_address,
                input_amount=input_amount,
                input_usd=input_usd,
                price_map=price_map,
                reason="uniswap_v4_live_quote_failed",
                chain_id=chain_id,
            )
            if quote is None:
                quote = {
                    "pool": pool.get("pool"),
                    "protocol": pool.get("protocol") or pool.get("dex"),
                    "dex": pool.get("dex") or pool.get("protocol"),
                    "fee": pool.get("fee"),
                    "feeBps": _fee_bps(pool),
                    "fromSymbol": from_symbol,
                    "toSymbol": to_symbol,
                    "tokenIn": _token_address(from_address),
                    "tokenOut": _token_address(to_address),
                    "inputAmount": input_amount,
                    "inputUsd": input_usd,
                    "outputAmount": None,
                    "outputUsd": None,
                    "effectiveRate": None,
                    "reserveInUsd": _pool_reserve_usd(pool, in_index, price_map),
                    "reserveOutUsd": _pool_reserve_usd(pool, out_index, price_map),
                    "priceImpactBps": None,
                    "depthBand": pool.get("depthBand") or {},
                }
            quote.update(
                {
                    "quoteSource": "uniswap_v4_live_quote_not_enough_liquidity"
                    if _quote_failure_status(live_quote_error) == "not_enough_liquidity"
                    else "uniswap_v4_live_quote_failed",
                    "executionTrust": _quote_failure_status(live_quote_error),
                    "quoteMode": "uniswap_v4_sdk_exact_input_single",
                    "adapterRequired": False,
                    "liveQuoteError": live_quote_error,
                    "warning": "uniswap_v4_live_quote_not_enough_liquidity"
                    if _quote_failure_status(live_quote_error) == "not_enough_liquidity"
                    else "uniswap_v4_live_quote_failed",
                }
            )
            return quote
    quote = _reference_price_quote(
        pool=pool,
        from_symbol=from_symbol,
        to_symbol=to_symbol,
        from_address=from_address,
        to_address=to_address,
        input_amount=input_amount,
        input_usd=input_usd,
        price_map=price_map,
        reason="uniswap_v4_sdk_adapter_required",
        chain_id=chain_id,
    )
    if quote is None:
        return {
            "pool": pool.get("pool"),
            "protocol": pool.get("protocol") or pool.get("dex"),
            "dex": pool.get("dex") or pool.get("protocol"),
            "fee": pool.get("fee"),
            "feeBps": _fee_bps(pool),
            "fromSymbol": from_symbol,
            "toSymbol": to_symbol,
            "tokenIn": _token_address(from_address),
            "tokenOut": _token_address(to_address),
            "inputAmount": input_amount,
            "inputUsd": input_usd,
            "outputAmount": None,
            "outputUsd": None,
            "effectiveRate": None,
            "reserveInUsd": _pool_reserve_usd(pool, in_index, price_map),
            "reserveOutUsd": _pool_reserve_usd(pool, out_index, price_map),
            "priceImpactBps": None,
            "depthBand": pool.get("depthBand") or {},
            "quoteSource": "uniswap_v4_sdk_adapter_required",
            "executionTrust": "sdk_adapter_required",
            "quoteMode": "uniswap_v4_sdk_exact_input_single",
            "adapterRequired": True,
            "warning": "uniswap_v4_sdk_adapter_required",
            "tokenDecimalsTrusted": False,
            "tokenDecimalAssumption": "missing_token_decimals_no_assumption",
        }
    quote["quoteSource"] = "uniswap_v4_sdk_adapter_required"
    quote["executionTrust"] = "sdk_adapter_required"
    quote["quoteMode"] = "uniswap_v4_sdk_exact_input_single"
    quote["adapterRequired"] = True
    return quote


__all__ = ["_v4_edge_quote"]
