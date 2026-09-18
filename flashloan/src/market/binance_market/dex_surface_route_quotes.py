"""Quote calculation helpers for DEX surface routes."""

from __future__ import annotations

from typing import Any

from bot.execution.uniswap_v4 import (
    build_v4_quote_request,
    quote_v4_exact_input_single as _default_quote_v4_exact_input_single,
)
from bot.execution.uniswap_v4_sdk_adapter import load_v4_sdk_adapter as _default_load_v4_sdk_adapter
from core.chain_data_registry import chain_data_by_chain_id
from dex.intent_trade.chains.robinhood.shared import (
    ROBINHOOD_CHAIN_ID as _ROBINHOOD_CHAIN_ID,
    ROBINHOOD_TESTNET_CHAIN_ID as _ROBINHOOD_TESTNET_CHAIN_ID,
)
from market.binance_market.dex_surface_graph import (
    _pool_matches_edge,
    _route_addresses_from_tokens,
    _route_pools,
    _route_symbols_from_tokens,
)
from market.binance_market.dex_surface_types import (
    _human_from_raw,
    _is_stable_anchor,
    _norm_symbol,
    _number,
    _optional_number,
    _pool_address,
    _pool_protocol,
    _pool_side_index,
    _pool_tokens,
    _raw_from_human,
    _route_symbol_allowed,
    _token_address,
)
from market.binance_market.dex_surface_v4 import (
    _fee_bps,
    _is_constant_product_pool,
    _market_reference_rate,
    _pool_reserve_amount,
    _pool_reserve_usd,
    _pool_token_decimals_trusted,
    _pool_tvl_usd,
    _reference_price_quote,
    _static_depth_available,
    _v4_live_token_metadata_enabled,
    _v4_spot_rate,
    _v4_token_decimals,
)
from market.strategy.limits import DEFAULT_ARBITRAGE_MIN_PAPER_PROFIT_USD

QUOTE_PROBE_NOTIONAL_USD = 1000.0
DEFAULT_FLASH_LOAN_FEE_BPS = 5.0
DEFAULT_GAS_RESERVE_USD = 1.0
ROBINHOOD_CHAIN_ID = _ROBINHOOD_CHAIN_ID
ROBINHOOD_TESTNET_CHAIN_ID = _ROBINHOOD_TESTNET_CHAIN_ID
_ROBINHOOD_CHAIN_IDS = {ROBINHOOD_CHAIN_ID, ROBINHOOD_TESTNET_CHAIN_ID}


def _chain_network_name(chain_id: int | None) -> str:
    if chain_id is None:
        return ""
    row = chain_data_by_chain_id(chain_id) or {}
    return str(row.get("network") or "").strip().lower()


def _quote_failure_status(error: object) -> str:
    message = str(error or "").lower()
    markers = (
        "not enough liquidity",
        "insufficient liquidity",
        "notenoughliquidity",
        "liquidity is zero",
        "live liquidity is zero",
    )
    if any(marker in message for marker in markers):
        return "not_enough_liquidity"
    return "live_quote_failed"


def _v4_live_quote_enabled(chain_id: int | None = None) -> bool:
    try:
        from market.binance_market import dex_surfaces

        compatibility = getattr(dex_surfaces, "_v4_live_quote_enabled", None)
        if callable(compatibility) and getattr(compatibility, "__module__", "") != __name__:
            if chain_id is None:
                return bool(compatibility())
            try:
                return bool(compatibility(chain_id=chain_id))
            except TypeError:
                return bool(compatibility())
    except (AttributeError, ImportError, TypeError):
        pass
    import os
    raw = os.getenv("DEX_V4_LIVE_QUOTE_ENABLED")
    if raw is None:
        return int(chain_id or 0) in _ROBINHOOD_CHAIN_IDS
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def load_v4_sdk_adapter(*args: Any, **kwargs: Any):
    compatibility = None
    try:
        from market.binance_market import dex_surfaces
        compatibility = getattr(dex_surfaces, "load_v4_sdk_adapter", None)
    except (AttributeError, ImportError, TypeError):
        compatibility = None
    if callable(compatibility) and compatibility is not load_v4_sdk_adapter:
        return compatibility(*args, **kwargs)
    return _default_load_v4_sdk_adapter(*args, **kwargs)


def quote_v4_exact_input_single(*args: Any, **kwargs: Any):
    compatibility = None
    try:
        from market.binance_market import dex_surfaces
        compatibility = getattr(dex_surfaces, "quote_v4_exact_input_single", None)
    except (AttributeError, ImportError, TypeError):
        compatibility = None
    if callable(compatibility) and compatibility is not quote_v4_exact_input_single:
        return compatibility(*args, **kwargs)
    return _default_quote_v4_exact_input_single(*args, **kwargs)


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

def _route_quote_analysis(
    route: dict[str, Any],
    *,
    price_map: dict[str, float],
    chain_id: int | None = None,
    notional_usd: float = QUOTE_PROBE_NOTIONAL_USD,
    token_basis_input_amount: float | None = None,
) -> dict[str, Any]:
    strict_live_price_only = chain_id in _ROBINHOOD_CHAIN_IDS
    overview_route = route.get("overviewRoute") or {}
    route_tokens = [token for token in route.get("routeTokenPath") or overview_route.get("pathTokens") or [] if isinstance(token, dict)]
    route_symbols = _route_symbols_from_tokens(route_tokens) if route_tokens else list(overview_route.get("pathSymbols") or route.get("routeSymbols") or [])
    route_addresses = _route_addresses_from_tokens(route_tokens) if route_tokens else []
    pools = _route_pools(route)
    static_depth_available = bool(pools) and all(_static_depth_available(pool) for pool in pools)
    warnings: list[str] = []
    if route_symbols and not route_tokens:
        warnings.append("route_identity_symbol_fallback")
    invalid_symbols = [symbol for symbol in route_symbols if not _route_symbol_allowed(symbol)]
    if invalid_symbols:
        warnings.append(f"invalid_route_symbol:{','.join(invalid_symbols)}")
        return {
            "method": "static_amm_depth_probe",
            "notionalUsd": notional_usd,
            "inputSymbol": route_symbols[0] if route_symbols else None,
            "outputSymbol": route_symbols[-1] if route_symbols else None,
            "routeSymbols": route_symbols,
            "routeAddresses": route_addresses,
            "complete": False,
            "requiresLiveQuoter": False,
            "adapterRequired": False,
            "stepCount": 0,
            "steps": [],
            "finalOutputUsd": None,
            "referenceFinalOutputUsd": None,
            "grossProfitUsd": None,
            "referenceGrossProfitUsd": None,
            "flashLoanFeeBps": DEFAULT_FLASH_LOAN_FEE_BPS,
            "flashLoanFeeUsd": notional_usd * DEFAULT_FLASH_LOAN_FEE_BPS / 10_000.0,
            "gasReserveUsd": DEFAULT_GAS_RESERVE_USD,
            "minProfitUsd": DEFAULT_ARBITRAGE_MIN_PAPER_PROFIT_USD,
            "offchainMinProfitUsd": DEFAULT_ARBITRAGE_MIN_PAPER_PROFIT_USD,
            "submissionMinProfitUsd": notional_usd * DEFAULT_FLASH_LOAN_FEE_BPS / 10_000.0,
            "submissionNetProfitUsd": None,
            "offchainNetProfitUsd": None,
            "netProfitUsd": None,
            "referenceNetProfitUsd": None,
            "netProfitBps": None,
            "referenceNetProfitBps": None,
            "profitable": False,
            "executableCandidate": False,
            "sdkAdapterRequiredStepCount": 0,
            "recommendation": {
                "level": "blocked",
                "action": "拒绝污染路径",
                "reasons": ["路径包含明显的污染符号，不能进入报价或预检。"],
            },
            "warnings": warnings,
        }
    if len(route_symbols) < 2:
        warnings.append("route_symbols_missing")
    input_symbol = route_symbols[0] if route_symbols else None
    output_symbol = route_symbols[-1] if route_symbols else None
    current_usd = float(notional_usd)
    closed_loop_same_symbol = bool(input_symbol and output_symbol == input_symbol)
    requested_token_basis_input_amount = token_basis_input_amount
    if closed_loop_same_symbol and requested_token_basis_input_amount is not None:
        token_basis_input_amount = requested_token_basis_input_amount
    elif closed_loop_same_symbol and input_symbol and price_map.get(input_symbol):
        token_basis_input_amount = current_usd / price_map[input_symbol]
    elif closed_loop_same_symbol:
        token_basis_input_amount = 1.0
    else:
        token_basis_input_amount = None
    current_amount = (
        token_basis_input_amount
        if token_basis_input_amount is not None
        else current_usd / price_map[input_symbol]
        if input_symbol and price_map.get(input_symbol)
        else token_basis_input_amount
    )
    steps: list[dict[str, Any]] = []
    for index, (from_symbol, to_symbol) in enumerate(zip(route_symbols, route_symbols[1:]), start=1):
        from_address = route_addresses[index - 1] if index - 1 < len(route_addresses) else ""
        to_address = route_addresses[index] if index < len(route_addresses) else ""
        quote = _best_edge_quote(
            pools=pools,
            from_symbol=from_symbol,
            to_symbol=to_symbol,
            from_address=from_address,
            to_address=to_address,
            input_amount=current_amount,
            input_usd=current_usd,
            price_map=price_map,
            chain_id=chain_id,
        )
        if quote is None:
            steps.append(
                {
                    "hop": index,
                    "fromSymbol": from_symbol,
                    "toSymbol": to_symbol,
                    "tokenIn": from_address,
                    "tokenOut": to_address,
                    "status": "missing_pool_or_depth",
                    "inputAmount": current_amount,
                    "inputUsd": current_usd,
                    "outputAmount": None,
                    "outputUsd": None,
                }
            )
            warnings.append(f"hop_{index}_quote_missing")
            break
        quote["hop"] = index
        quote["status"] = "quoted"
        quote.setdefault("tokenIn", from_address)
        quote.setdefault("tokenOut", to_address)
        steps.append(quote)
        trust = str(quote.get("executionTrust") or "").strip().lower()
        has_reference_output = quote.get("referenceOutputUsd") is not None or quote.get("referenceOutputAmount") is not None
        if trust in {"live_quote_failed", "not_enough_liquidity"}:
            warnings.append(
                f"hop_{index}_not_enough_liquidity"
                if trust == "not_enough_liquidity"
                else f"hop_{index}_live_quote_failed"
            )
            if quote.get("liveQuoteError"):
                warnings.append(f"hop_{index}_live_quote_error:{quote['liveQuoteError']}")
            warnings.append(f"route_truncated_at_hop_{index}_{trust}")
            break
        if trust in {"reference_only", "sdk_adapter_required"} and not has_reference_output:
            warnings.append(f"hop_{index}_requires_live_quoter")
            warnings.append(f"route_truncated_at_hop_{index}_requires_live_quoter")
            break
        if trust in {"reference_only", "sdk_adapter_required"}:
            warnings.append(f"hop_{index}_reference_quote_requires_live_quoter")
        if chain_id is None and trust == "sdk_adapter_required" and quote.get("trustedUsdPrice") is False:
            warnings.append(f"route_truncated_at_hop_{index}_requires_live_quoter")
            break
        step_output_usd = quote.get("outputUsd")
        if step_output_usd is None:
            step_output_usd = quote.get("referenceOutputUsd")
        current_usd = _number(step_output_usd)
        step_output_amount = quote.get("outputAmount")
        if step_output_amount is None:
            step_output_amount = quote.get("referenceOutputAmount")
        current_amount = step_output_amount if step_output_amount is not None else (
            current_usd / price_map[to_symbol] if price_map.get(to_symbol) else None
        )
        if current_amount is not None and _number(current_amount) <= 0:
            warnings.append(f"route_truncated_at_hop_{index}_zero_output")
            break
    complete = len(steps) == max(0, len(route_symbols) - 1) and all(step.get("status") == "quoted" for step in steps)
    requires_live_quoter = any(
        step.get("executionTrust") in {"reference_only", "live_quote_failed", "not_enough_liquidity"}
        for step in steps
    )
    adapter_required = any(step.get("executionTrust") == "sdk_adapter_required" for step in steps)
    requires_live_quoter = requires_live_quoter or adapter_required
    untrusted_usd_reference = any(step.get("trustedUsdPrice") is False for step in steps)
    assumed_token_decimals = any(step.get("tokenDecimalsTrusted") is not True for step in steps)
    if requires_live_quoter:
        warnings.append("reference_price_step_requires_live_quoter")
    if untrusted_usd_reference:
        warnings.append("reference_usd_price_missing_or_untrusted")
    if assumed_token_decimals:
        warnings.append("v4_token_decimals_missing_requires_metadata")
    reference_final_output_usd = current_usd if complete and not untrusted_usd_reference else None
    reference_gross_profit_usd = (reference_final_output_usd - notional_usd) if reference_final_output_usd is not None else None
    flash_loan_fee_usd = notional_usd * DEFAULT_FLASH_LOAN_FEE_BPS / 10_000.0
    submission_min_profit_usd = flash_loan_fee_usd
    submission_net_profit_usd = (
        reference_gross_profit_usd - flash_loan_fee_usd if reference_gross_profit_usd is not None else None
    )
    reference_net_profit_usd = (
        submission_net_profit_usd - DEFAULT_GAS_RESERVE_USD if submission_net_profit_usd is not None else None
    )
    reference_net_profit_bps = (
        reference_net_profit_usd / notional_usd * 10_000.0
        if reference_net_profit_usd is not None and notional_usd > 0
        else None
    )
    token_basis_complete = bool(
        complete
        and token_basis_input_amount is not None
        and current_amount is not None
        and input_symbol
        and output_symbol == input_symbol
    )
    reference_final_output_amount = current_amount if token_basis_complete else None
    reference_gross_profit_amount = (
        reference_final_output_amount - token_basis_input_amount
        if reference_final_output_amount is not None and token_basis_input_amount is not None
        else None
    )
    reference_flash_loan_fee_amount = (
        token_basis_input_amount * DEFAULT_FLASH_LOAN_FEE_BPS / 10_000.0
        if token_basis_input_amount is not None
        else None
    )
    reference_net_profit_amount = (
        reference_gross_profit_amount - reference_flash_loan_fee_amount
        if reference_gross_profit_amount is not None and reference_flash_loan_fee_amount is not None
        else None
    )
    reference_gross_return_bps = (
        reference_gross_profit_amount / token_basis_input_amount * 10_000.0
        if reference_gross_profit_amount is not None and token_basis_input_amount
        else None
    )
    reference_net_return_bps = (
        reference_net_profit_amount / token_basis_input_amount * 10_000.0
        if reference_net_profit_amount is not None and token_basis_input_amount
        else None
    )
    depth_impact_bps = [
        _optional_number(step.get("priceImpactBps"))
        for step in steps
        if step.get("priceImpactBps") is not None
    ]
    max_static_depth_impact_bps = max(depth_impact_bps) if depth_impact_bps else None
    depth_status = "available" if static_depth_available else "unavailable"
    if static_depth_available and max_static_depth_impact_bps is not None and max_static_depth_impact_bps > 10.0:
        depth_status = "exceeds_10_bps"
    live_price_verified_now = bool(steps) and all(
        str(step.get("executionTrust") or "").strip().lower() == "live_v4_adapter"
        for step in steps
    )
    if not static_depth_available and not live_price_verified_now:
        warnings.append("static_depth_evidence_unavailable")
    elif depth_status == "exceeds_10_bps" and not live_price_verified_now:
        warnings.append("static_price_impact_exceeds_10_bps_reduce_borrow_amount")
    borrow_asset_is_stable = bool(input_symbol and _is_stable_anchor(input_symbol))
    borrow_asset_class = "stable" if borrow_asset_is_stable else "non_stable"
    token_basis_trusted = bool(token_basis_complete and not assumed_token_decimals)
    token_basis_reference_profitable = bool(reference_net_profit_amount is not None and reference_net_profit_amount > 0)
    token_basis_profitable = bool(token_basis_trusted and token_basis_reference_profitable)
    non_stable_chain_submission_candidate = bool(token_basis_profitable and not borrow_asset_is_stable)
    executable_ready = bool(complete and not requires_live_quoter)
    final_output_usd = reference_final_output_usd if executable_ready else None
    gross_profit_usd = reference_gross_profit_usd if executable_ready else None
    net_profit_usd = (
        gross_profit_usd - flash_loan_fee_usd - DEFAULT_GAS_RESERVE_USD if gross_profit_usd is not None else None
    )
    net_profit_bps = net_profit_usd / notional_usd * 10_000.0 if net_profit_usd is not None and notional_usd > 0 else None
    min_profit_usd = float(DEFAULT_ARBITRAGE_MIN_PAPER_PROFIT_USD)
    fully_live_adapter = bool(steps) and all(
        str(step.get("executionTrust") or "").strip().lower() == "live_v4_adapter"
        for step in steps
    )
    if strict_live_price_only and not fully_live_adapter:
        warnings.append("robinhood_live_price_only_enforced")
        requires_live_quoter = True
        executable_ready = False
        final_output_usd = None
        gross_profit_usd = None
        net_profit_usd = None
        net_profit_bps = None
        non_stable_chain_submission_candidate = False
    live_price_verified = bool(steps) and all(
        str(step.get("executionTrust") or "").strip().lower() == "live_v4_adapter" and step.get("trustedUsdPrice") is True
        for step in steps
    )
    live_quote_failures = [
        f"第 {step.get('hop') or '?'} 跳链上报价失败（流动性不足或 Quoter 错误）：{step.get('liveQuoteError')}"
        for step in steps
        if step.get("executionTrust") in {"live_quote_failed", "not_enough_liquidity"} and step.get("liveQuoteError")
    ]
    if not complete:
        action = "等待真实报价"
        level = "blocked"
        reasons = live_quote_failures or ["部分跳数缺少可计算池子或深度，不能进入自动交易"]
    elif requires_live_quoter:
        action = (
            f"{input_symbol} 本位参考收益为正，待链上复核"
            if untrusted_usd_reference and token_basis_profitable and input_symbol
            else f"{input_symbol} 本位收益未确认，缺少精确 decimals，不能按默认值假设"
            if untrusted_usd_reference and token_basis_reference_profitable and assumed_token_decimals and input_symbol
            else f"{input_symbol} 本位参考收益为负，待链上复核"
            if untrusted_usd_reference and token_basis_complete and input_symbol
            else "缺少可信 USD 锚点，待链上复核"
            if untrusted_usd_reference
            else "参考测算完成，待链上复核"
            if reference_final_output_usd is not None
            else "等待链上 Quoter 复核"
        )
        level = "requires_live_quote"
        reasons = (
            [
                "已基于本地 JSON 的 PoolKey、sqrtPriceX96、fee 和池内方向完成参考测算",
                "路径包含 V3/V4 或参考价报价跳，参考测算不能作为自动交易依据",
                "必须用链上 Quoter、协议 SDK 或 eth_call 复核每一跳输出后才能进入预检",
            ]
            if reference_final_output_usd is not None
            else ["路径包含 V3/V4 或参考价报价跳，静态余额不能作为自动交易依据", "必须用链上 Quoter 或协议 SDK 复核每一跳输出后才能进入预检"]
        )
        if strict_live_price_only:
            reasons = live_quote_failures or [
                "Robinhood Chain 强制链上实时价格，静态参考价已禁用",
                "路由框架可以保留，但任何价格、收益和 bps 都必须来自 live quoter / onchain evidence",
            ]
        if depth_status == "unavailable":
            action = "已生成保守初始量，继续探索深度"
            reasons = [
                "当前池子缺少可用于离线深度计算的 liquidity 或 TVL，收益只保留为参考值",
                "初始借贷量采用交易量阶梯中的最小探针，继续观察各跳输出和滑点",
                "补充深度快照后，按 10 bps 滑点限制重新放大量化",
            ]
        elif depth_status == "exceeds_10_bps":
            action = "滑点超过 10 bps，降低初始交易额度"
            reasons = [
                "离线深度估算发现至少一跳超过 10 bps 滑点限制",
                "应沿交易量阶梯降低初始借贷量，直到所有跳数满足 10 bps",
                *reasons,
            ]
        elif untrusted_usd_reference:
            reasons = [
                "当前路径缺少可信 USD 价格锚点，美元利润不可判定",
                "闭环主评估已统一改用初始借入 token X 作为计价基准，计算最终返回 X 的参考百分点",
                "X 本位测算仍来自 V4 PoolKey / sqrtPriceX96 静态参考，必须链上复核真实 amountOut",
                *reasons,
            ]
        if assumed_token_decimals:
            reasons = [
                "部分 V4 token decimals 未在本地 PoolKey 数据中明示，当前 X 本位数量不按 18 位默认值换算，必须先补齐精确 decimals",
                *reasons,
            ]
        if non_stable_chain_submission_candidate:
            reasons = [
                "非 U 资金池 X 不套用 6U 固定利润门槛；X 本位净收益为正即可进入链上利润控制候选",
                *reasons,
            ]
    elif token_basis_profitable and token_basis_complete and not borrow_asset_is_stable and reference_net_profit_usd is None:
        action = f"{input_symbol} 本位闭环已验证正收益，进入链上预检"
        level = "ready_for_precheck"
        reasons = [
            "链上 live quote 已验证闭环 token 本位正收益",
            "当前缺少可信 USD 锚点，但这不应阻断非稳定 X 本位候选",
            "仍需 static-call / gas / repayment 复核后再广播",
        ]
    elif net_profit_usd is not None and net_profit_usd >= min_profit_usd:
        action = "进入自动交易预检"
        level = "ready_for_precheck"
        reasons = ["1000U 静态探针净利润超过最低阈值", "仍需链上 Quoter、滑点、gas 与闪电贷费复核"]
    elif net_profit_usd is not None and net_profit_usd > 0:
        action = "继续观察"
        level = "watch"
        reasons = ["扣除闪电贷费和 gas 储备后仍为正，但低于自动交易阈值"]
    else:
        action = "不建议执行"
        level = "reject"
        reasons = ["1000U 静态探针未覆盖交易成本"]
    executable_candidate = bool(
        executable_ready
        and not assumed_token_decimals
        and net_profit_usd is not None
        and net_profit_usd >= min_profit_usd
    )
    testnet_live_only = bool(chain_id == ROBINHOOD_TESTNET_CHAIN_ID and requires_live_quoter and not live_price_verified)
    if testnet_live_only:
        executable_candidate = False

    payload = {
        "method": "static_amm_depth_probe",
        "notionalUsd": notional_usd,
        "inputSymbol": input_symbol,
        "outputSymbol": output_symbol,
        "routeSymbols": route_symbols,
        "routeAddresses": route_addresses,
        "routeFrameworkSource": "market_observation",
        "priceEvidenceSource": "chain_live_quote" if live_price_verified else ("chain_live_quote_required" if strict_live_price_only else "static_reference_probe"),
        "priceEvidenceMode": "live_verified" if live_price_verified else ("live_required" if strict_live_price_only else "reference_only"),
        "livePriceVerified": live_price_verified,
        "complete": complete,
        "requiresLiveQuoter": requires_live_quoter,
        "adapterRequired": adapter_required,
        "stepCount": len(steps),
        "steps": steps,
        "finalOutputUsd": final_output_usd,
        "referenceFinalOutputUsd": reference_final_output_usd,
        "grossProfitUsd": gross_profit_usd,
        "referenceGrossProfitUsd": reference_gross_profit_usd,
        "flashLoanFeeBps": DEFAULT_FLASH_LOAN_FEE_BPS,
        "flashLoanFeeUsd": flash_loan_fee_usd,
        "gasReserveUsd": DEFAULT_GAS_RESERVE_USD,
        "minProfitUsd": min_profit_usd,
        "offchainMinProfitUsd": min_profit_usd,
        "submissionMinProfitUsd": submission_min_profit_usd,
        "submissionNetProfitUsd": submission_net_profit_usd,
        "offchainNetProfitUsd": net_profit_usd,
        "netProfitUsd": net_profit_usd,
        "referenceNetProfitUsd": reference_net_profit_usd,
        "netProfitBps": net_profit_bps,
        "referenceNetProfitBps": reference_net_profit_bps,
        "referenceInputAmount": token_basis_input_amount,
        "referenceInputSymbol": input_symbol if token_basis_input_amount is not None else None,
        "referenceFinalOutputAmount": reference_final_output_amount,
        "referenceFinalOutputSymbol": output_symbol if reference_final_output_amount is not None else None,
        "referenceGrossProfitAmount": reference_gross_profit_amount,
        "referenceFlashLoanFeeAmount": reference_flash_loan_fee_amount,
        "referenceNetProfitAmount": reference_net_profit_amount,
        "referenceGrossReturnBps": reference_gross_return_bps,
        "referenceNetReturnBps": reference_net_return_bps,
        "staticDepthStatus": depth_status,
        "staticDepthAvailable": static_depth_available,
        "maxStaticDepthImpactBps": max_static_depth_impact_bps,
        "staticDepthImpactLimitBps": 10.0,
        "referenceGasExcluded": token_basis_complete,
        "referenceReturnBasis": "borrow_token" if token_basis_complete else ("usd" if reference_net_profit_usd is not None else None),
        "referenceReturnTrusted": bool(
            reference_final_output_usd is not None
            and not assumed_token_decimals
            and (token_basis_trusted or reference_net_profit_usd is not None or live_price_verified)
        ),
        "borrowAssetClass": borrow_asset_class,
        "borrowAssetIsStable": borrow_asset_is_stable,
        "tokenBasisReferenceProfitable": token_basis_reference_profitable,
        "tokenBasisProfitable": token_basis_profitable,
        "chainSubmissionCandidate": bool(
            not testnet_live_only
            and not assumed_token_decimals
            and (
                non_stable_chain_submission_candidate
                or (executable_ready and net_profit_usd is not None and net_profit_usd >= min_profit_usd)
            )
        ),
        "chainSubmissionPolicy": (
            "non_stable_x_positive_token_profit_onchain_control"
            if non_stable_chain_submission_candidate
            else "stable_x_usd_min_profit"
            if borrow_asset_is_stable
            else "requires_positive_x_profit"
        ),
        "chainSubmissionMinProfitAmount": 0.0 if not borrow_asset_is_stable else None,
        "chainSubmissionMinProfitSymbol": input_symbol if not borrow_asset_is_stable else None,
        "profitable": bool(
            (not untrusted_usd_reference and not assumed_token_decimals and net_profit_usd is not None and net_profit_usd > 0)
            or token_basis_profitable
        ),
        "executableCandidate": executable_candidate,
        "sdkAdapterRequiredStepCount": sum(1 for step in steps if step.get("executionTrust") == "sdk_adapter_required"),
        "trustedUsdReference": bool(
            reference_final_output_usd is not None
            and not assumed_token_decimals
            and (not untrusted_usd_reference or live_price_verified)
        ),
        "tokenDecimalsTrusted": not assumed_token_decimals,
        "recommendation": {"level": level, "action": action, "reasons": reasons},
        "warnings": warnings,
    }
    return payload
