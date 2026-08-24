from __future__ import annotations

from typing import Any
from urllib.parse import quote

from core.flash_loan_pool_discovery import query_flash_loan_pool_discovery
from market.binance_market.pool_candidates import dex_pool_candidates_payload

QUOTE_PROBE_NOTIONAL_USD = 1000.0
DEFAULT_FLASH_LOAN_FEE_BPS = 5.0
DEFAULT_GAS_RESERVE_USD = 1.0
DEFAULT_MIN_PROFIT_BPS = 20.0
DEFAULT_AMM_FEE_BPS = 30.0

STABLE_ANCHOR_SYMBOLS = {
    "AUSD",
    "BUSD",
    "DAI",
    "DAI.E",
    "EURC",
    "FDUSD",
    "FRAX",
    "FRAX.E",
    "GHO",
    "PYUSD",
    "RLUSD",
    "TUSD",
    "USDC",
    "USDC.E",
    "USDD",
    "USDE",
    "USDG",
    "USDF",
    "USD1",
    "USDT",
    "USDT.E",
}
PRICE_ALIAS_GROUPS = (
    {"ETH", "WETH", "WETH.E"},
    {"BTC", "WBTC", "BTCB", "BTC.B"},
)


def _norm_symbol(value: object) -> str:
    return str(value or "").strip().upper()


def _int(value: object) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _number(value: object) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return 0.0
    return parsed if parsed == parsed and parsed not in (float("inf"), float("-inf")) else 0.0


def _optional_number(value: object) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if parsed != parsed or parsed in (float("inf"), float("-inf")):
        return None
    return parsed


def _is_stable_anchor(symbol: object) -> bool:
    text = _norm_symbol(symbol)
    bridge_base = text[:-2] if text.endswith(".E") else text
    return text in STABLE_ANCHOR_SYMBOLS or bridge_base in STABLE_ANCHOR_SYMBOLS or text.endswith("USD")


def _split_symbol_components(symbol: object) -> list[str]:
    text = _norm_symbol(symbol)
    if not text:
        return []
    return [part for part in text.replace("/", ":").split(":") if part]


def _pair_symbols(pair: dict[str, Any]) -> list[str]:
    symbols = [
        _norm_symbol(pair.get("tokenXSymbol")),
        _norm_symbol(pair.get("tokenYSymbol")),
    ]
    return [symbol for symbol in symbols if symbol]


def _pool_symbols(pool: dict[str, Any]) -> list[str]:
    symbols = [
        _norm_symbol(pool.get("token0Symbol")),
        _norm_symbol(pool.get("token1Symbol")),
    ]
    return [symbol for symbol in symbols if symbol]


def _pool_tvl_usd(pool: dict[str, Any]) -> float:
    balances = pool.get("balances") if isinstance(pool.get("balances"), dict) else {}
    return _number(balances.get("tvlUsd") if balances else pool.get("tvlUsd"))


def _pool_reserve_amount(pool: dict[str, Any], index: int) -> float | None:
    balances = pool.get("balances") if isinstance(pool.get("balances"), dict) else {}
    keys = ("token0", "token1")
    legacy_keys = ("token0Amount", "token1Amount")
    value = balances.get(keys[index]) if balances else pool.get(legacy_keys[index])
    parsed = _optional_number(value)
    return parsed if parsed is not None and parsed > 0 else None


def _pool_reserve_usd(pool: dict[str, Any], index: int, price_map: dict[str, float] | None = None) -> float | None:
    balances = pool.get("balances") if isinstance(pool.get("balances"), dict) else {}
    usd_keys = ("token0Usd", "token1Usd")
    value = balances.get(usd_keys[index]) if balances else pool.get(usd_keys[index])
    parsed = _optional_number(value)
    if parsed is not None and parsed > 0:
        return parsed
    symbols = _pool_symbols(pool)
    amount = _pool_reserve_amount(pool, index)
    if amount is not None and index < len(symbols) and price_map:
        price = price_map.get(symbols[index])
        if price is not None and price > 0:
            return amount * price
    return None


def _propagate_price_aliases(price_map: dict[str, float]) -> None:
    for group in PRICE_ALIAS_GROUPS:
        known = [price_map[symbol] for symbol in group if price_map.get(symbol)]
        if not known:
            continue
        price = sum(known) / len(known)
        for symbol in group:
            price_map.setdefault(symbol, price)


def _price_map_from_pairs(pairs: list[dict[str, Any]]) -> dict[str, float]:
    price_map: dict[str, float] = {symbol: 1.0 for symbol in STABLE_ANCHOR_SYMBOLS}
    pools = [pool for pair in pairs for pool in _pools_for_pair(pair)]
    for pool in pools:
        symbols = _pool_symbols(pool)
        for index, symbol in enumerate(symbols[:2]):
            amount = _pool_reserve_amount(pool, index)
            usd = _pool_reserve_usd(pool, index, price_map={})
            if amount and usd and usd > 0:
                price_map[symbol] = usd / amount
    _propagate_price_aliases(price_map)
    for _ in range(4):
        changed = False
        for pool in pools:
            symbols = _pool_symbols(pool)
            if len(symbols) < 2:
                continue
            amounts = [_pool_reserve_amount(pool, 0), _pool_reserve_amount(pool, 1)]
            tvl = _pool_tvl_usd(pool)
            for known_index, missing_index in ((0, 1), (1, 0)):
                known_symbol = symbols[known_index]
                missing_symbol = symbols[missing_index]
                if price_map.get(missing_symbol) or not price_map.get(known_symbol) or not amounts[missing_index]:
                    continue
                if tvl > 0 and amounts[known_index]:
                    missing_usd = tvl - amounts[known_index] * price_map[known_symbol]
                    if missing_usd > 0:
                        price_map[missing_symbol] = missing_usd / amounts[missing_index]
                        changed = True
                        continue
                if amounts[known_index] and amounts[missing_index]:
                    price_map[missing_symbol] = price_map[known_symbol] * amounts[known_index] / amounts[missing_index]
                    changed = True
        _propagate_price_aliases(price_map)
        if not changed:
            break
    return {symbol: price for symbol, price in price_map.items() if price > 0}


def _fee_bps(pool: dict[str, Any]) -> float:
    raw = str(pool.get("fee") or "").strip().lower()
    protocol = str(pool.get("protocol") or pool.get("dex") or "").strip().lower()
    if "bps" in raw:
        parsed = _optional_number(raw.replace("bps", "").strip())
        return parsed if parsed is not None else DEFAULT_AMM_FEE_BPS
    parsed = _optional_number(raw)
    if parsed is not None:
        if parsed > 100:
            return parsed / 100.0
        if protocol in {"uniswap_v3", "pancakeswap_v3", "v3"} and parsed in {1, 5, 30, 100}:
            return parsed
        if protocol in {"uniswap_v3", "pancakeswap_v3", "v3"}:
            return parsed / 100.0
        return parsed
    if "v2" in raw or "v2" in protocol:
        return DEFAULT_AMM_FEE_BPS
    return DEFAULT_AMM_FEE_BPS


def _is_constant_product_pool(pool: dict[str, Any]) -> bool:
    text = f"{pool.get('protocol') or ''} {pool.get('dex') or ''} {pool.get('fee') or ''}".lower()
    return "v2" in text and "v3" not in text and "v4" not in text


def _market_reference_rate(pool: dict[str, Any], from_symbol: str, to_symbol: str) -> float | None:
    market = pool.get("market") if isinstance(pool.get("market"), dict) else {}
    price_native = _optional_number(market.get("priceNative"))
    if price_native is None or price_native <= 0:
        return None
    symbols = _pool_symbols(pool)
    if len(symbols) < 2:
        return None
    token0, token1 = symbols[:2]
    if from_symbol == token0 and to_symbol == token1:
        return price_native
    if from_symbol == token1 and to_symbol == token0:
        return 1.0 / price_native
    return None


def _reference_price_quote(
    *,
    pool: dict[str, Any],
    from_symbol: str,
    to_symbol: str,
    input_amount: float | None,
    input_usd: float,
    price_map: dict[str, float],
    reason: str,
) -> dict[str, Any] | None:
    fee_bps = _fee_bps(pool)
    fee_factor = max(0.0, 1.0 - fee_bps / 10_000.0)
    rate = _market_reference_rate(pool, from_symbol, to_symbol)
    if input_amount is None:
        input_price = price_map.get(from_symbol)
        if input_price and input_price > 0:
            input_amount = input_usd / input_price
    output_amount = input_amount * rate * fee_factor if input_amount is not None and rate is not None else None
    output_usd = None
    output_price = price_map.get(to_symbol)
    if output_amount is not None and output_price and output_price > 0:
        output_usd = output_amount * output_price
    elif rate is not None:
        output_usd = input_usd * fee_factor
    else:
        return None
    return {
        "pool": pool.get("pool"),
        "protocol": pool.get("protocol") or pool.get("dex"),
        "dex": pool.get("dex") or pool.get("protocol"),
        "fee": pool.get("fee"),
        "feeBps": fee_bps,
        "fromSymbol": from_symbol,
        "toSymbol": to_symbol,
        "inputAmount": input_amount,
        "inputUsd": input_usd,
        "outputAmount": output_amount,
        "outputUsd": output_usd,
        "effectiveRate": rate * fee_factor if rate is not None else None,
        "reserveInUsd": _pool_reserve_usd(pool, _pool_side_index(pool, from_symbol) or 0, price_map),
        "reserveOutUsd": _pool_reserve_usd(pool, _pool_side_index(pool, to_symbol) or 0, price_map),
        "priceImpactBps": None,
        "depthBand": pool.get("depthBand") or {},
        "quoteSource": "reference_price_requires_live_quoter",
        "executionTrust": "reference_only",
        "warning": reason,
    }


def _unique_symbols(rows: list[dict[str, Any]]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for row in rows:
        for symbol in _pool_symbols(row):
            if symbol and symbol not in seen:
                seen.add(symbol)
                result.append(symbol)
    return result


def _pool_count(pair: dict[str, Any], key: str) -> int:
    return len(pair.get(key) or []) or _int(pair.get(key.replace("Pools", "PoolCount")))


def _pools_for_pair(pair: dict[str, Any]) -> list[dict[str, Any]]:
    return list(pair.get("executionPools") or pair.get("preferredPools") or pair.get("strictQualityPools") or [])


def _pool_protocol(pool: dict[str, Any]) -> str:
    return _norm_symbol(pool.get("protocol") or pool.get("dex")).strip().lower()


def _route_pools(route: dict[str, Any]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    pools: list[dict[str, Any]] = []
    for key in ("executionPools", "preferredPools", "strictQualityPools"):
        for pool in route.get(key) or []:
            pool_id = str(pool.get("pool") or id(pool)).lower()
            if pool_id in seen:
                continue
            seen.add(pool_id)
            pools.append(pool)
    return pools


def _route_score(route: dict[str, Any]) -> tuple[float, int, str]:
    pools = _route_pools(route)
    tvl = sum(_number((pool.get("balances") or {}).get("tvlUsd")) for pool in pools)
    return (tvl, len(pools), str(route.get("symbolPair") or ""))


def _pool_matches_edge(pool: dict[str, Any], from_symbol: str, to_symbol: str) -> bool:
    symbols = set(_pool_symbols(pool)[:2])
    return from_symbol in symbols and to_symbol in symbols and len(symbols) == 2


def _pool_side_index(pool: dict[str, Any], symbol: str) -> int | None:
    symbols = _pool_symbols(pool)
    for index, candidate in enumerate(symbols[:2]):
        if candidate == symbol:
            return index
    return None


def _edge_quote(
    *,
    pool: dict[str, Any],
    from_symbol: str,
    to_symbol: str,
    input_amount: float | None,
    input_usd: float,
    price_map: dict[str, float],
) -> dict[str, Any] | None:
    in_index = _pool_side_index(pool, from_symbol)
    out_index = _pool_side_index(pool, to_symbol)
    if in_index is None or out_index is None or in_index == out_index:
        return None
    protocol = _pool_protocol(pool)
    if protocol == "uniswap_v4":
        quote = _reference_price_quote(
            pool=pool,
            from_symbol=from_symbol,
            to_symbol=to_symbol,
            input_amount=input_amount,
            input_usd=input_usd,
            price_map=price_map,
            reason="uniswap_v4_sdk_adapter_required",
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
            input_amount=input_amount,
            input_usd=input_usd,
            price_map=price_map,
            reason="concentrated_liquidity_pool_requires_quoter",
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
                input_amount=input_amount,
                input_usd=input_usd,
                price_map=price_map,
                reason="pool_reserve_price_deviates_from_market_reference",
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
    input_amount: float | None,
    input_usd: float,
    price_map: dict[str, float],
) -> dict[str, Any] | None:
    quotes = [
        quote
        for pool in pools
        if _pool_matches_edge(pool, from_symbol, to_symbol)
        for quote in [_edge_quote(pool=pool, from_symbol=from_symbol, to_symbol=to_symbol, input_amount=input_amount, input_usd=input_usd, price_map=price_map)]
        if quote is not None
    ]
    if not quotes:
        return None
    return max(quotes, key=lambda item: (_number(item.get("outputUsd")), _number(item.get("reserveOutUsd"))))


def _route_quote_analysis(
    route: dict[str, Any],
    *,
    price_map: dict[str, float],
    notional_usd: float = QUOTE_PROBE_NOTIONAL_USD,
) -> dict[str, Any]:
    route_symbols = list((route.get("overviewRoute") or {}).get("pathSymbols") or route.get("routeSymbols") or [])
    pools = _route_pools(route)
    warnings: list[str] = []
    if len(route_symbols) < 2:
        warnings.append("route_symbols_missing")
    input_symbol = route_symbols[0] if route_symbols else None
    output_symbol = route_symbols[-1] if route_symbols else None
    current_usd = float(notional_usd)
    current_amount = (current_usd / price_map[input_symbol]) if input_symbol and price_map.get(input_symbol) else None
    steps: list[dict[str, Any]] = []
    for index, (from_symbol, to_symbol) in enumerate(zip(route_symbols, route_symbols[1:]), start=1):
        quote = _best_edge_quote(
            pools=pools,
            from_symbol=from_symbol,
            to_symbol=to_symbol,
            input_amount=current_amount,
            input_usd=current_usd,
            price_map=price_map,
        )
        if quote is None:
            steps.append(
                {
                    "hop": index,
                    "fromSymbol": from_symbol,
                    "toSymbol": to_symbol,
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
        steps.append(quote)
        current_usd = _number(quote.get("outputUsd"))
        current_amount = quote.get("outputAmount") if quote.get("outputAmount") is not None else (
            current_usd / price_map[to_symbol] if price_map.get(to_symbol) else None
        )
    complete = len(steps) == max(0, len(route_symbols) - 1) and all(step.get("status") == "quoted" for step in steps)
    requires_live_quoter = any(step.get("executionTrust") == "reference_only" for step in steps)
    adapter_required = any(step.get("executionTrust") == "sdk_adapter_required" for step in steps)
    requires_live_quoter = requires_live_quoter or adapter_required
    if requires_live_quoter:
        warnings.append("reference_price_step_requires_live_quoter")
    final_output_usd = current_usd if complete else None
    gross_profit_usd = (final_output_usd - notional_usd) if final_output_usd is not None else None
    flash_loan_fee_usd = notional_usd * DEFAULT_FLASH_LOAN_FEE_BPS / 10_000.0
    net_profit_usd = (
        gross_profit_usd - flash_loan_fee_usd - DEFAULT_GAS_RESERVE_USD if gross_profit_usd is not None else None
    )
    net_profit_bps = net_profit_usd / notional_usd * 10_000.0 if net_profit_usd is not None and notional_usd > 0 else None
    min_profit_usd = max(1.0, notional_usd * DEFAULT_MIN_PROFIT_BPS / 10_000.0)
    if not complete:
        action = "等待真实报价"
        level = "blocked"
        reasons = ["部分跳数缺少可计算池子或深度，不能进入自动交易"]
    elif requires_live_quoter:
        action = "等待链上 Quoter 复核"
        level = "requires_live_quote"
        reasons = ["路径包含 V3/V4 或参考价报价跳，静态余额不能作为自动交易依据", "必须用链上 Quoter 或协议 SDK 复核每一跳输出后才能进入预检"]
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
    return {
        "method": "static_amm_depth_probe",
        "notionalUsd": notional_usd,
        "inputSymbol": input_symbol,
        "outputSymbol": output_symbol,
        "complete": complete,
        "requiresLiveQuoter": requires_live_quoter,
        "adapterRequired": adapter_required,
        "stepCount": len(steps),
        "steps": steps,
        "finalOutputUsd": final_output_usd,
        "grossProfitUsd": gross_profit_usd,
        "flashLoanFeeBps": DEFAULT_FLASH_LOAN_FEE_BPS,
        "flashLoanFeeUsd": flash_loan_fee_usd,
        "gasReserveUsd": DEFAULT_GAS_RESERVE_USD,
        "minProfitUsd": min_profit_usd,
        "netProfitUsd": net_profit_usd,
        "netProfitBps": net_profit_bps,
        "profitable": bool(net_profit_usd is not None and net_profit_usd > 0),
        "executableCandidate": bool(complete and not requires_live_quoter and net_profit_usd is not None and net_profit_usd >= min_profit_usd),
        "sdkAdapterRequiredStepCount": sum(1 for step in steps if step.get("executionTrust") == "sdk_adapter_required"),
        "recommendation": {"level": level, "action": action, "reasons": reasons},
        "warnings": warnings,
    }


def _borrowable_symbols_from_discovery(chain_id: int | None) -> list[str]:
    try:
        snapshot = query_flash_loan_pool_discovery(chain_id=chain_id)
    except Exception:
        return []
    symbols: list[str] = []
    for raw in (snapshot.get("raw_snapshots") or {}).values():
        if not isinstance(raw, dict):
            continue
        raw_chain_id = _int(raw.get("chainId") or raw.get("chain_id"))
        if chain_id is not None and raw_chain_id and raw_chain_id != int(chain_id):
            continue
        for source in raw.get("sources") or []:
            symbol = _norm_symbol((source or {}).get("token_symbol"))
            if symbol and bool((source or {}).get("flash_borrowable", True)):
                symbols.append(symbol)
    if symbols:
        return sorted(set(symbols))
    fallback: set[str] = set()
    for protocol in snapshot.get("protocols") or []:
        for key in ("token_symbol", "asset_symbol", "symbol"):
            symbol = _norm_symbol(protocol.get(key))
            if symbol:
                fallback.add(symbol)
    return sorted(fallback)


def _borrowable_symbols(pool_payload: dict[str, Any], chain_id: int | None) -> list[str]:
    discovered = set(_borrowable_symbols_from_discovery(chain_id))
    for pair in pool_payload.get("pairs") or []:
        linkage = pair.get("flashLoanLinkage") or {}
        for symbol in linkage.get("borrow_symbols") or []:
            normalized = _norm_symbol(symbol)
            if normalized:
                discovered.add(normalized)
    expanded = {part for symbol in discovered for part in _split_symbol_components(symbol)}
    return sorted(discovered | expanded)


def _candidate_payload(chain_id: int | None, protocols: list[str] | None = None) -> dict[str, Any]:
    return dex_pool_candidates_payload(chain_id=chain_id, protocols=protocols or [])


def _candidate_pairs(pairs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        pair
        for pair in pairs
        if _pool_count(pair, "preferredPools") or _pool_count(pair, "executionPools") or _pool_count(pair, "strictQualityPools")
    ]


def _borrow_anchor_symbols(pool_payload: dict[str, Any], chain_id: int | None) -> list[str]:
    borrow_symbols = _borrowable_symbols(pool_payload, chain_id)
    anchors = [symbol for symbol in borrow_symbols if _is_stable_anchor(symbol) and ":" not in symbol]
    if anchors:
        return sorted(set(anchors))
    return sorted({symbol for symbol in borrow_symbols if ":" not in symbol})


def _route_candidate(
    *,
    kind: str,
    anchor: str,
    target: str,
    path_symbols: list[str],
    pairs: list[dict[str, Any]],
    bridge_symbols: list[str] | None = None,
) -> dict[str, Any]:
    pools = [pool for pair in pairs for pool in _pools_for_pair(pair)]
    primary = pairs[-1] if pairs else {}
    route_symbols = [symbol for symbol in path_symbols if symbol]
    return {
        "symbolPair": " -> ".join(route_symbols),
        "addressPair": "",
        "tokenX": primary.get("tokenX"),
        "tokenY": primary.get("tokenY"),
        "tokenXSymbol": anchor,
        "tokenYSymbol": target,
        "classification": primary.get("classification") or {},
        "allPoolCount": str(len(pools)),
        "strictQualityPoolCount": str(len(pools)),
        "preferredPoolCount": str(len(pools)),
        "executionPoolCount": str(len(pools[:5])),
        "rejectedPoolCount": "0",
        "hasStrictQualityPools": bool(pools),
        "hasPreferredPools": bool(pools),
        "strictQualityPools": pools,
        "preferredPools": pools,
        "executionPools": pools[:5],
        "rejectedPools": [],
        "rejectReasonCounts": {},
        "flashLoanLinkage": {
            "strategy": "stable_anchor_candidate_path" if kind == "direct_anchor_pair" else "stable_anchor_bridge_candidate_path",
            "borrow_symbols": [anchor],
            "route_symbols": route_symbols,
            "ready_for_atomic_arbitrage": bool(pools),
            "conditions": [
                "quote page must verify executable output after slippage",
                "route must repay the borrowed anchor asset in the same transaction",
                "gas and flash loan fee must be below gross spread",
            ],
        },
        "overviewRoute": {
            "kind": kind,
            "anchorBorrowSymbol": anchor,
            "targetSymbol": target,
            "bridgeSymbols": bridge_symbols or [],
            "pathSymbols": route_symbols,
            "pathText": " -> ".join(route_symbols),
            "candidatePairSymbols": [pair.get("symbolPair") for pair in pairs],
            "analysisStage": "overview_candidate_only",
            "nextStep": "quotes_and_route_analysis",
        },
    }


def _overview_path_candidates(pool_payload: dict[str, Any], chain_id: int | None) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    pairs = _candidate_pairs(pool_payload.get("pairs") or [])
    anchors = _borrow_anchor_symbols(pool_payload, chain_id)
    direct_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    pair_by_symbols: dict[frozenset[str], list[dict[str, Any]]] = {}
    for pair in pairs:
        symbols = _pair_symbols(pair)
        if len(symbols) < 2:
            continue
        pair_by_symbols.setdefault(frozenset(symbols[:2]), []).append(pair)
        for anchor in anchors:
            if anchor not in symbols:
                continue
            for target in symbols:
                if target == anchor or target in anchors:
                    continue
                route = _route_candidate(
                    kind="direct_anchor_pair",
                    anchor=anchor,
                    target=target,
                    path_symbols=[anchor, target, anchor],
                    pairs=[pair],
                )
                key = (anchor, target)
                current = direct_by_key.get(key)
                if current is None or _route_score(route) > _route_score(current):
                    direct_by_key[key] = route

    routes = list(direct_by_key.values())
    route_keys = set(direct_by_key)
    for anchor in anchors:
        bridge_pairs = [
            pair
            for key, grouped in pair_by_symbols.items()
            if anchor in key
            for pair in grouped
        ]
        for bridge_pair in bridge_pairs:
            bridge_symbols = [symbol for symbol in _pair_symbols(bridge_pair) if symbol != anchor]
            for bridge in bridge_symbols:
                if not bridge:
                    continue
                for key, grouped in pair_by_symbols.items():
                    if bridge not in key or anchor in key:
                        continue
                    target_symbols = [symbol for symbol in key if symbol != bridge]
                    for target in target_symbols:
                        if not target or target in anchors or (anchor, target) in route_keys:
                            continue
                        route = _route_candidate(
                            kind="bridge_pair",
                            anchor=anchor,
                            target=target,
                            bridge_symbols=[bridge],
                            path_symbols=[anchor, bridge, target, bridge, anchor],
                            pairs=[bridge_pair, grouped[0]],
                        )
                        routes.append(route)
                        route_keys.add((anchor, target))

    routes.sort(key=_route_score, reverse=True)
    used_pairs = {
        pair_symbol
        for route in routes
        for pair_symbol in (route.get("overviewRoute") or {}).get("candidatePairSymbols") or []
    }
    watch_pairs = [pair for pair in pairs if pair.get("symbolPair") not in used_pairs][:12]
    return routes, watch_pairs, anchors


def dex_overview_payload(*, chain_id: int | None = None, protocols: list[str] | None = None) -> dict[str, Any]:
    pool_payload = _candidate_payload(chain_id, protocols)
    ready_paths, watch_pairs, anchor_symbols = _overview_path_candidates(pool_payload, chain_id)
    return {
        "mode": "dex_overview",
        "chain_id": chain_id,
        "selected_protocols": protocols or [],
        "summary": {
            **(pool_payload.get("summary") or {}),
            "anchorBorrowSymbols": anchor_symbols,
            "arbitragePathCount": len(ready_paths),
            "candidatePathCount": len(ready_paths),
            "directAnchorPathCount": sum(1 for route in ready_paths if (route.get("overviewRoute") or {}).get("kind") == "direct_anchor_pair"),
            "bridgePathCount": sum(1 for route in ready_paths if (route.get("overviewRoute") or {}).get("kind") == "bridge_pair"),
            "watchPairCount": len(watch_pairs),
        },
        "decisionBasis": "borrow_anchor_to_non_anchor_candidate_path",
        "poolStaticTable": pool_payload.get("poolStaticTable") or {},
        "flashLoanLiquidity": pool_payload.get("flashLoanLiquidity") or {},
        "dexSettlement": pool_payload.get("dexSettlement") or {},
        "arbitragePaths": ready_paths,
        "watchPairs": watch_pairs,
    }


def dex_market_observation_payload(*, chain_id: int | None = None, protocols: list[str] | None = None) -> dict[str, Any]:
    pool_payload = _candidate_payload(chain_id, protocols)
    pairs = pool_payload.get("pairs") or []
    borrow_symbols = _borrowable_symbols(pool_payload, chain_id)
    direct: list[dict[str, Any]] = []
    unmatched: list[dict[str, Any]] = []
    by_symbol: dict[str, list[dict[str, Any]]] = {symbol: [] for symbol in borrow_symbols}

    for pair in pairs:
        symbols = _pair_symbols(pair)
        pools = pair.get("preferredPools") or pair.get("executionPools") or []
        matched = [symbol for symbol in borrow_symbols if symbol in symbols]
        if matched:
            row = {
                "symbolPair": pair.get("symbolPair"),
                "matchedBorrowSymbols": matched,
                "tradeSymbols": symbols,
                "poolCount": len(pools),
                "classification": pair.get("classification") or {},
                "pools": pools[:5],
            }
            direct.append(row)
            for symbol in matched:
                by_symbol.setdefault(symbol, []).append(row)
        elif pools:
            unmatched.append(
                {
                    "symbolPair": pair.get("symbolPair"),
                    "tradeSymbols": symbols,
                    "poolCount": len(pools),
                    "classification": pair.get("classification") or {},
                    "pools": pools[:3],
                }
            )

    bridges: list[dict[str, Any]] = []
    for borrow_symbol, rows in by_symbol.items():
        connected = sorted({symbol for row in rows for symbol in row["tradeSymbols"] if symbol != borrow_symbol})
        for bridge_symbol in connected:
            second_leg = [
                row
                for row in direct + unmatched
                if bridge_symbol in row.get("tradeSymbols", []) and borrow_symbol not in row.get("tradeSymbols", [])
            ][:5]
            if second_leg:
                bridges.append(
                    {
                        "borrowSymbol": borrow_symbol,
                        "bridgeSymbol": bridge_symbol,
                        "firstLegCount": len(rows),
                        "secondLegCount": len(second_leg),
                        "secondLegPairs": second_leg,
                    }
                )

    return {
        "mode": "dex_market_observation",
        "chain_id": chain_id,
        "selected_protocols": protocols or [],
        "borrowableSymbols": borrow_symbols,
        "directPools": direct,
        "indirectRoutes": bridges,
        "unmatchedPools": unmatched[:20],
        "summary": {
            "borrowableTokenCount": len(borrow_symbols),
            "directPairCount": len(direct),
            "indirectRouteCount": len(bridges),
            "unmatchedPairCount": len(unmatched),
            "observableTokenCount": len(_unique_symbols([pool for pair in pairs for pool in pair.get("preferredPools") or []])),
        },
        "flashLoanLiquidity": pool_payload.get("flashLoanLiquidity") or {},
        "poolStaticTable": pool_payload.get("poolStaticTable") or {},
    }


def dex_quote_routes_payload(*, chain_id: int | None = None, protocols: list[str] | None = None) -> dict[str, Any]:
    pool_payload = _candidate_payload(chain_id, protocols)
    settlement = pool_payload.get("dexSettlement") or {}
    uniswapx = settlement.get("uniswapx") if isinstance(settlement.get("uniswapx"), dict) else {}
    overview_paths, _watch_pairs, anchor_symbols = _overview_path_candidates(pool_payload, chain_id)
    price_map = _price_map_from_pairs(pool_payload.get("pairs") or [])
    routes = []
    for pair in overview_paths:
        linkage = pair.get("flashLoanLinkage") or {}
        pools = _route_pools(pair)
        overview_route = pair.get("overviewRoute") or {}
        route_symbols = list(overview_route.get("pathSymbols") or linkage.get("route_symbols") or _pair_symbols(pair))
        quote_analysis = _route_quote_analysis(pair, price_map=price_map)
        if quote_analysis.get("executableCandidate"):
            quote_status = "static_probe_ready_for_precheck"
        elif quote_analysis.get("requiresLiveQuoter"):
            quote_status = "requires_live_quoter"
        elif quote_analysis.get("complete"):
            quote_status = "static_probe_not_profitable"
        else:
            quote_status = "static_probe_incomplete"
        quote_requests = [
            {
                "venue": "amm_pool",
                "quoteMode": "pool_exact_input_probe",
                "status": quote_status,
                "quoteRequestUrl": f"/api/dex/quote-request?pair={quote(str(pair.get('symbolPair') or ''), safe='')}&venue=amm_pool",
                "pool": pool.get("pool"),
                "protocol": pool.get("dex") or pool.get("protocol"),
                "fee": pool.get("fee"),
                "routeSymbols": route_symbols,
            }
            for pool in pools[:5]
        ]
        v4_pools = [pool for pool in pools if _pool_protocol(pool) == "uniswap_v4"]
        if v4_pools:
            quote_requests.append(
                {
                    "venue": "uniswap_v4",
                    "quoteMode": "uniswap_v4_sdk_exact_input_single",
                    "status": "sdk_adapter_required" if quote_analysis.get("requiresLiveQuoter") else "quoteable_after_static_probe",
                    "quoteRequestUrl": f"/api/dex/quote-request?pair={quote(str(pair.get('symbolPair') or ''), safe='')}&venue=uniswap_v4",
                    "routeSymbols": route_symbols,
                    "chainId": chain_id,
                    "adapterHints": [
                        "pool_manager_state_required",
                        "hook_aware_quote_required",
                        "sdk_or_adapter_must_return_amount_out_raw",
                    ],
                    "pools": [pool.get("pool") for pool in v4_pools[:5]],
                }
            )
        if uniswapx.get("supported") and len(route_symbols) >= 2:
            quote_requests.append(
                {
                    "venue": "uniswapx",
                    "quoteMode": "intent_exact_input",
                    "status": "quoteable_after_static_probe",
                    "quoteRequestUrl": f"/api/dex/quote-request?pair={quote(str(pair.get('symbolPair') or ''), safe='')}&venue=uniswapx",
                    "routeSymbols": route_symbols,
                    "chainId": chain_id,
                    "reactorFamily": uniswapx.get("reactor_family"),
                    "requires": ["sellToken", "buyToken", "amount", "userAddress"],
                }
            )
        routes.append(
            {
                "symbolPair": pair.get("symbolPair"),
                "overviewRoute": overview_route,
                "routeSymbols": route_symbols,
                "borrowSymbols": linkage.get("borrow_symbols") or [],
                "strategy": linkage.get("strategy") or "quote_candidate_from_borrow_anchor_path",
                "poolCount": len(pools),
                "quoteStatus": quote_status,
                "quoteAnalysis": quote_analysis,
                "quoteVenueCount": len(quote_requests),
                "quoteInputs": {
                    "candidatePools": pools[:5],
                    "routePools": pools,
                    "quoteRequests": quote_requests,
                    "conditions": [
                        "候选池必须保持可执行深度，不能只依赖静态池子列表。",
                        "AMM 报价必须通过滑点、gas、闪电贷费用和最小利润阈值检查。",
                        "UniswapX 需要意图报价请求，并确认求解器或填单方可用后才能执行。",
                    ],
                },
                "settlementRails": pair.get("settlementRails") or [],
            }
        )
    executable_net_profits = [
        _number((route.get("quoteAnalysis") or {}).get("netProfitUsd"))
        for route in routes
        if (route.get("quoteAnalysis") or {}).get("executableCandidate")
    ]
    observed_net_profits = [
        _number((route.get("quoteAnalysis") or {}).get("netProfitUsd"))
        for route in routes
        if (route.get("quoteAnalysis") or {}).get("netProfitUsd") is not None
    ]
    return {
        "mode": "dex_quote_routes",
        "chain_id": chain_id,
        "selected_protocols": protocols or [],
        "summary": {
            "probeNotionalUsd": QUOTE_PROBE_NOTIONAL_USD,
            "routeCount": len(routes),
            "candidateRouteCount": len(routes),
            "anchorBorrowSymbols": anchor_symbols,
            "executableCandidateCount": sum(1 for route in routes if (route.get("quoteAnalysis") or {}).get("executableCandidate")),
            "profitableRouteCount": sum(1 for route in routes if (route.get("quoteAnalysis") or {}).get("profitable")),
            "completeStaticQuoteCount": sum(1 for route in routes if (route.get("quoteAnalysis") or {}).get("complete")),
            "liveQuoterRequiredCount": sum(1 for route in routes if (route.get("quoteAnalysis") or {}).get("requiresLiveQuoter")),
            "sdkAdapterRequiredCount": sum(1 for route in routes if (route.get("quoteAnalysis") or {}).get("adapterRequired")),
            "bestNetProfitUsd": max(executable_net_profits) if executable_net_profits else None,
            "bestObservedNetProfitUsd": max(observed_net_profits) if observed_net_profits else None,
            "directAnchorRouteCount": sum(1 for route in routes if (route.get("overviewRoute") or {}).get("kind") == "direct_anchor_pair"),
            "bridgeRouteCount": sum(1 for route in routes if (route.get("overviewRoute") or {}).get("kind") == "bridge_pair"),
            "uniswapxQuoteableRouteCount": sum(
                1
                for route in routes
                for request in (route.get("quoteInputs") or {}).get("quoteRequests") or []
                if request.get("venue") == "uniswapx"
            ),
        },
        "routes": routes,
        "dexSettlement": settlement,
        "poolStaticTable": pool_payload.get("poolStaticTable") or {},
    }


def dex_quote_request_payload(
    *,
    chain_id: int | None = None,
    protocols: list[str] | None = None,
    pair: str | None = None,
    venue: str | None = None,
    amount: str | None = None,
) -> dict[str, Any]:
    normalized_pair = str(pair or "").strip().upper()
    normalized_venue = str(venue or "amm_pool").strip().lower()
    routes = dex_quote_routes_payload(chain_id=chain_id, protocols=protocols)
    matched = None
    for route in routes.get("routes") or []:
        if str(route.get("symbolPair") or "").strip().upper() == normalized_pair:
            matched = route
            break
    if matched is None:
        return {
            "ok": False,
            "mode": "dex_quote_request",
            "error": "candidate_pair_not_found",
            "pair": normalized_pair,
            "venue": normalized_venue,
            "availablePairs": [route.get("symbolPair") for route in routes.get("routes") or []],
        }
    requests = [
        request
        for request in (matched.get("quoteInputs") or {}).get("quoteRequests") or []
        if str(request.get("venue") or "").strip().lower() == normalized_venue
    ]
    return {
        "ok": bool(requests),
        "mode": "dex_quote_request",
        "pair": matched.get("symbolPair"),
        "venue": normalized_venue,
        "amount": amount,
        "routeSymbols": matched.get("routeSymbols") or [],
        "quoteAnalysis": matched.get("quoteAnalysis") or {},
        "quoteRequests": requests,
        "candidatePools": (matched.get("quoteInputs") or {}).get("candidatePools") or [],
        "status": "quote_request_ready" if requests else "venue_not_available_for_pair",
        "note": "This endpoint returns the exact quote request specification; live quote execution is handled by the configured venue adapter.",
    }


def dex_submission_review_payload(
    *,
    attempts: list[dict[str, Any]] | None = None,
    groups: dict[str, Any] | None = None,
    source: str | None = None,
) -> dict[str, Any]:
    rows = list(attempts or [])
    def row_status(row: dict[str, Any]) -> str:
        return str(row.get("review_category") or row.get("category") or row.get("status") or row.get("state") or "").lower()

    success = [row for row in rows if row_status(row).endswith("success")]
    failed = [row for row in rows if "fail" in row_status(row)]
    blocked = [row for row in rows if "not_executable" in row_status(row) or row_status(row) == "quote_required"]
    return {
        "mode": "dex_submission_review",
        "source": source or "execution_attempts",
        "summary": {
            "attemptCount": len(rows),
            "successCount": len(success),
            "failedCount": len(failed),
            "blockedCount": len(blocked),
        },
        "groups": groups or {},
        "attempts": rows[:100],
    }


__all__ = [
    "dex_market_observation_payload",
    "dex_overview_payload",
    "dex_quote_request_payload",
    "dex_quote_routes_payload",
    "dex_submission_review_payload",
]
