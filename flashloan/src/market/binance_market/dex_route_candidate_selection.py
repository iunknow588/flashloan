"""Candidate route selection and replacement for quote/routing pages.

The market-observation layer preserves every per-hop candidate pool so the
quote/routing page can replace the default depth-first working pools when the
current best route is not profitable.  The public functions here are the
extension point for future profit-aware selectors.
"""

from __future__ import annotations

from itertools import product
from typing import Any, Callable
from urllib.parse import quote

from market.binance_market.dex_surface_observation_helpers import _pool_usable_for_observation
from market.binance_market.dex_surface_edge_quotes import _best_edge_quote
from market.binance_market.dex_surface_route_quotes import (
    DEFAULT_FLASH_LOAN_FEE_BPS as _UNISWAPX_FLASH_LOAN_FEE_BPS,
    DEFAULT_GAS_RESERVE_USD as _UNISWAPX_GAS_RESERVE_USD,
    QUOTE_PROBE_NOTIONAL_USD as _UNISWAPX_QUOTE_PROBE_NOTIONAL_USD,
)
from market.binance_market.dex_surface_types import _number, _pool_address


LEG_ORDER = ("x-y", "y-z", "z-x")
DEFAULT_REPLACEMENT_LEGS = ("x-y", "z-x")
DEFAULT_OUTPUT_BASIS = "max_price_preferred_output_per_hop"
DEFAULT_NET_PROFIT_BASIS = "best_candidate_net_profit"
DEFAULT_CANDIDATE_POOL_LIMIT = 2
DEFAULT_MAX_REPLACEMENT_VARIANTS = 8
UNISWAPX_MIDDLE_LEG = "y-z"
UNISWAPX_FALLBACK_BASIS = "uniswapx_middle_hop_fallback"
UNISWAPX_MIN_PROFIT_BPS = 100


def _pool_id(pool: dict[str, Any]) -> str:
    return str(_pool_address(pool) or "").strip()


def _hop_rows_by_leg(route: dict[str, Any]) -> dict[str, dict[str, Any]]:
    by_leg: dict[str, dict[str, Any]] = {}
    for candidate in route.get("hopCandidates") or []:
        if not isinstance(candidate, dict):
            continue
        leg = str(candidate.get("leg") or "").strip()
        if leg:
            by_leg[leg] = candidate
    return by_leg


def _hop_pool_rows(
    candidate: dict[str, Any],
    *,
    only_usable: bool = False,
) -> list[dict[str, Any]]:
    rows = [
        pool
        for pool in candidate.get("pools") or []
        if isinstance(pool, dict) and _pool_id(pool)
    ]
    if only_usable:
        rows = [pool for pool in rows if _pool_usable_for_observation(pool)]
    return rows


def _preferred_pool_id(candidate: dict[str, Any] | None) -> str:
    if not isinstance(candidate, dict):
        return ""
    return str(candidate.get("preferredPoolId") or "").strip()


PRICE_EVIDENCE_KEYS = (
    "effectiveRate",
    "referenceEffectiveRate",
    "price",
    "referencePrice",
    "outputPerInput",
    "outputAmount",
    "outputUsd",
    "amountOut",
    "amountOutUsd",
    "referenceOutputAmount",
    "referenceFinalOutputUsd",
    "finalOutputUsd",
)


def _pool_price_score(pool: dict[str, Any]) -> float | None:
    """Return price/output evidence for a candidate pool, or ``None``.

    Quote/routing replacement is price-driven only.  Depth/TVL is deliberately
    not used here: a deeper pool without better price evidence is not eligible
    for candidate replacement.
    """
    quote_summary = pool.get("quoteSummary") if isinstance(pool.get("quoteSummary"), dict) else {}
    for key in PRICE_EVIDENCE_KEYS:
        value = quote_summary.get(key)
        if value is not None:
            score = _number(value)
            if score > 0:
                return score
    return None


def _order_pools_by_output(pools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Order liquid pools by price evidence, never by depth."""
    scored = [
        (pool, score)
        for pool in pools
        if (score := _pool_price_score(pool)) is not None
    ]
    scored.sort(key=lambda item: (-item[1], _pool_id(item[0])))
    return [pool for pool, _score in scored]


def output_leader_pool_ids(
    route: dict[str, Any],
    *,
    replacement_legs: tuple[str, ...] = DEFAULT_REPLACEMENT_LEGS,
    output_selector: Callable[[list[dict[str, Any]]], list[dict[str, Any]]] | None = None,
) -> dict[str, str]:
    """Return the best-output pool id for each replacement leg."""
    by_leg = _hop_rows_by_leg(route)
    selected: dict[str, str] = {}
    for leg in replacement_legs:
        candidate = by_leg.get(leg)
        if not candidate:
            continue
        pools = _hop_pool_rows(candidate, only_usable=True)
        if not pools:
            continue
        ordered = output_selector(pools) if callable(output_selector) else _order_pools_by_output(pools)
        if ordered:
            selected[leg] = _pool_id(ordered[0])
    return selected


def _original_pool_by_id(route: dict[str, Any]) -> dict[str, dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    for pool in route.get("routePools") or []:
        if isinstance(pool, dict) and _pool_id(pool):
            by_id[_pool_id(pool).lower()] = pool
    return by_id


def _merged_pool_row(row: dict[str, Any], original_by_id: dict[str, dict[str, Any]]) -> dict[str, Any]:
    merged = dict(row)
    original = original_by_id.get(_pool_id(row).lower())
    if original:
        for key, value in original.items():
            if value is None or value == "":
                continue
            if merged.get(key) is None or merged.get(key) == "":
                merged[key] = value
    return merged


def _current_preferred_ids(route: dict[str, Any]) -> dict[str, str]:
    by_leg = _hop_rows_by_leg(route)
    return {
        leg: pool_id
        for leg, candidate in by_leg.items()
        if (pool_id := _preferred_pool_id(candidate))
    }


def _temporary_route_policy(
    route: dict[str, Any],
    *,
    selection_basis: str,
    reason: str,
) -> dict[str, Any]:
    """Describe a quote-page replacement path without claiming it is global.

    The original overview path remains the scan default.  A replacement is a
    one-shot local variant: it may be executed last and then lives only in the
    execution history, never as the next scan's default route.
    """
    reference_pool_path_key = str(
        route.get("referencePoolPathKey")
        or route.get("defaultPoolPathKey")
        or route.get("poolPathKey")
        or ""
    ).strip()
    return {
        "temporaryRoute": True,
        "notForDefaultScan": True,
        "historyOnlyAfterExecution": True,
        "executeLast": True,
        "selectionBasis": selection_basis,
        "reason": reason,
        "referencePoolPathKey": reference_pool_path_key,
        "referenceSymbolPair": str(route.get("symbolPair") or "").strip(),
    }


def build_route_variant(
    route: dict[str, Any],
    *,
    pool_ids_by_leg: dict[str, str],
    selection_basis: str,
    reason: str = "",
) -> dict[str, Any] | None:
    """Build a route copy whose working pools follow ``pool_ids_by_leg``."""
    by_leg = _hop_rows_by_leg(route)
    original_by_id = _original_pool_by_id(route)
    selected_rows: list[dict[str, Any]] = []
    changed_legs: list[str] = []
    for leg in LEG_ORDER:
        candidate = by_leg.get(leg)
        if candidate is None:
            continue
        chosen_id = str(pool_ids_by_leg.get(leg) or _preferred_pool_id(candidate) or "").strip()
        pools = _hop_pool_rows(candidate)
        row = next(
            (pool for pool in pools if _pool_id(pool).lower() == chosen_id.lower()),
            (pools[0] if pools else None),
        )
        if row is None:
            continue
        if chosen_id and chosen_id.lower() != _preferred_pool_id(candidate).lower():
            changed_legs.append(leg)
        selected_rows.append(_merged_pool_row(row, original_by_id))
    if not selected_rows:
        return None

    variant = dict(route)
    for key in ("routePools", "executionPools", "preferredPools", "strictQualityPools"):
        variant[key] = list(selected_rows)
    variant["poolPathKey"] = "|".join([_pool_id(pool) for pool in selected_rows])
    reference_pool_path_key = str(
        route.get("referencePoolPathKey")
        or route.get("defaultPoolPathKey")
        or route.get("poolPathKey")
        or ""
    ).strip()
    variant["referencePoolPathKey"] = reference_pool_path_key
    variant["defaultPoolPathKey"] = reference_pool_path_key
    if isinstance(route.get("quoteInputs"), dict):
        quote_inputs = dict(route["quoteInputs"])
        quote_inputs["routePools"] = list(selected_rows)
        quote_inputs["candidatePools"] = list(selected_rows)
        variant["quoteInputs"] = quote_inputs

    hop_candidates: list[dict[str, Any]] = []
    for leg in LEG_ORDER:
        candidate = by_leg.get(leg)
        if candidate is None:
            continue
        copied = dict(candidate)
        chosen_id = str(pool_ids_by_leg.get(leg) or _preferred_pool_id(candidate) or "").strip()
        if chosen_id:
            copied["preferredPoolId"] = chosen_id
            copied["selectionBasis"] = selection_basis
            copied["preferredPoolUsable"] = any(
                _pool_id(pool).lower() == chosen_id.lower() and _pool_usable_for_observation(pool)
                for pool in _hop_pool_rows(candidate, only_usable=True)
            )
        hop_candidates.append(copied)

    hop_candidate_selection = {
        "selectionBasis": selection_basis,
        "preferredPoolIds": [
            str(candidate.get("preferredPoolId") or "").strip()
            for candidate in hop_candidates
        ],
    }
    variant["hopCandidates"] = hop_candidates
    variant["hopCandidateSelection"] = hop_candidate_selection
    variant["hopCandidateReplacement"] = {
        "basis": selection_basis,
        "reason": reason,
        "changedLegs": changed_legs,
        "temporaryRoute": True,
        "notForDefaultScan": True,
        "historyOnlyAfterExecution": True,
        "executeLast": True,
        "referencePoolPathKey": reference_pool_path_key,
    }
    variant["selectionBasis"] = selection_basis
    variant["temporaryRoute"] = True
    variant["notForDefaultScan"] = True
    variant["temporaryRoutePolicy"] = _temporary_route_policy(
        route,
        selection_basis=selection_basis,
        reason=reason,
    )
    variant["temporaryRouteSnapshot"] = {
        "poolPathKey": variant["poolPathKey"],
        "routePools": list(selected_rows),
        "hopCandidates": hop_candidates,
        "hopCandidateSelection": hop_candidate_selection,
    }
    return variant


def replacement_route_variants(
    route: dict[str, Any],
    *,
    replacement_legs: tuple[str, ...] = DEFAULT_REPLACEMENT_LEGS,
    output_selector: Callable[[list[dict[str, Any]]], list[dict[str, Any]]] | None = None,
    candidate_pool_limit: int = DEFAULT_CANDIDATE_POOL_LIMIT,
    max_variants: int = DEFAULT_MAX_REPLACEMENT_VARIANTS,
) -> list[dict[str, Any]]:
    """Generate bounded route variants that change at least one replacement leg."""
    by_leg = _hop_rows_by_leg(route)
    base = _current_preferred_ids(route)
    choices: dict[str, list[str]] = {}
    for leg in replacement_legs:
        candidate = by_leg.get(leg)
        if not candidate:
            continue
        pools = _hop_pool_rows(candidate, only_usable=True)
        if not pools:
            continue
        ordered = output_selector(pools) if callable(output_selector) else _order_pools_by_output(pools)
        if not ordered:
            # No static price evidence yet: do not fall back to depth. Keep
            # every liquid candidate so route_quote_analysis_fn can choose by
            # real quoted price/profit.
            ordered = pools
        ids: list[str] = []
        seen: set[str] = set()
        for pool in ordered:
            pool_id = _pool_id(pool)
            if pool_id and pool_id not in seen:
                seen.add(pool_id)
                ids.append(pool_id)
        current_id = base.get(leg)
        price_evidenced = any(_pool_price_score(pool) is not None for pool in pools)
        candidate_limit = candidate_pool_limit if price_evidenced else len(ids)
        if current_id:
            ids = [
                pool_id
                for pool_id in ids
                if pool_id.lower() != current_id.lower()
            ][:candidate_limit]
        else:
            ids = ids[:candidate_limit]
        if ids:
            choices[leg] = ids
    if not choices:
        return []

    variants: list[dict[str, Any]] = []
    ordered_legs = sorted(choices)
    for combo in product(*[choices[leg] for leg in ordered_legs]):
        if len(variants) >= max_variants:
            break
        pool_ids_by_leg = dict(base)
        for leg, pool_id in zip(ordered_legs, combo):
            pool_ids_by_leg[leg] = pool_id
        variant = build_route_variant(
            route,
            pool_ids_by_leg=pool_ids_by_leg,
            selection_basis=DEFAULT_NET_PROFIT_BASIS,
            reason="quote_profit_replacement",
        )
        if variant is not None:
            variants.append(variant)
    return variants


def _analysis_profit(analysis: dict[str, Any] | None) -> float | None:
    if not isinstance(analysis, dict):
        return None
    for key in ("referenceNetProfitUsd", "netProfitUsd", "referenceNetProfitAmount", "netProfitAmount"):
        value = analysis.get(key)
        if value is None:
            continue
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            continue
        return parsed
    return None


def _analysis_return_bps(analysis: dict[str, Any] | None) -> float | None:
    if not isinstance(analysis, dict):
        return None
    for key in ("referenceNetReturnBps", "netProfitBps", "referenceNetProfitBps"):
        value = analysis.get(key)
        if value is None:
            continue
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            continue
        return parsed
    return None


def _uniswapx_profit_meets_minimum(analysis: dict[str, Any] | None) -> bool:
    """Require at least 100 bps net profit for the UniswapX fallback."""
    return_bps = _analysis_return_bps(analysis)
    if return_bps is not None and return_bps >= UNISWAPX_MIN_PROFIT_BPS:
        return True
    profit = _analysis_profit(analysis)
    if profit is None:
        return False
    notional = analysis.get("notionalUsd") if isinstance(analysis, dict) else None
    if notional is not None:
        try:
            notional_value = float(notional)
        except (TypeError, ValueError):
            notional_value = None
        if notional_value and profit / notional_value * 10_000.0 >= UNISWAPX_MIN_PROFIT_BPS:
            return True
    input_amount = analysis.get("referenceInputAmount") if isinstance(analysis, dict) else None
    if input_amount is not None:
        try:
            input_value = float(input_amount)
        except (TypeError, ValueError):
            input_value = None
        if input_value and profit / input_value * 10_000.0 >= UNISWAPX_MIN_PROFIT_BPS:
            return True
    return False


def _analysis_step(quote_analysis: dict[str, Any] | None, hop: int) -> dict[str, Any] | None:
    if not isinstance(quote_analysis, dict):
        return None
    steps = quote_analysis.get("steps")
    if not isinstance(steps, list):
        return None
    for step in steps:
        if isinstance(step, dict) and int(step.get("hop") or 0) == hop:
            return step
    if 0 < hop <= len(steps) and isinstance(steps[hop - 1], dict):
        return steps[hop - 1]
    return None


def _quote_output_amount(quote: dict[str, Any] | None) -> float | None:
    if not isinstance(quote, dict):
        return None
    for key in ("outputAmount", "referenceOutputAmount", "amountOut", "buyAmount"):
        value = quote.get(key)
        if value is None:
            continue
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            continue
        if parsed > 0:
            return parsed
    return None


def _quote_input_amount(quote: dict[str, Any] | None) -> float | None:
    if not isinstance(quote, dict):
        return None
    for key in ("inputAmount", "referenceInputAmount", "amountIn", "sellAmount"):
        value = quote.get(key)
        if value is None:
            continue
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            continue
        if parsed > 0:
            return parsed
    return None


def _uniswapx_middle_hop_parameters(
    route: dict[str, Any],
    pools: list[dict[str, Any]],
    quote_analysis: dict[str, Any] | None,
    *,
    price_map: dict[str, float],
    notional_usd: float = _UNISWAPX_QUOTE_PROBE_NOTIONAL_USD,
    chain_id: int | None = None,
) -> dict[str, Any] | None:
    """Derive the UniswapX middle-hop quote constraint from the base route.

    ``y`` is the first hop's actual output (O -> X).  ``z`` is the minimum
    middle-hop output that lets the third hop (Y -> O) return at least 1% net
    profit after flash-loan fees and the USD gas reserve.
    """
    overview_route = route.get("overviewRoute") if isinstance(route.get("overviewRoute"), dict) else {}
    route_symbols = [
        str(symbol or "").strip()
        for symbol in route.get("routeSymbols") or overview_route.get("pathSymbols") or []
        if str(symbol or "").strip()
    ]
    route_addresses = [
        str(address or "").strip()
        for address in route.get("routeAddresses") or overview_route.get("pathAddresses") or []
        if str(address or "").strip()
    ]
    if len(route_symbols) < 4 or route_symbols[0] != route_symbols[-1]:
        return None
    input_symbol = route_symbols[0]
    input_price = _number(price_map.get(input_symbol))
    notional_amount = (notional_usd / input_price) if input_price > 0 else 1.0
    if notional_amount <= 0:
        return None

    def edge_quote(
        from_index: int,
        to_index: int,
        *,
        input_amount: float | None,
        input_usd: float,
    ) -> dict[str, Any] | None:
        return _best_edge_quote(
            pools=pools,
            from_symbol=route_symbols[from_index],
            to_symbol=route_symbols[to_index],
            from_address=route_addresses[from_index] if from_index < len(route_addresses) else None,
            to_address=route_addresses[to_index] if to_index < len(route_addresses) else None,
            input_amount=input_amount,
            input_usd=input_usd,
            price_map=price_map,
            chain_id=chain_id,
        )

    first_step = _analysis_step(quote_analysis, 1)
    first_quote = first_step or edge_quote(0, 1, input_amount=notional_amount, input_usd=float(notional_usd))
    first_hop_output = _quote_output_amount(first_quote)
    if first_hop_output is None:
        return None

    middle_step = _analysis_step(quote_analysis, 2)
    middle_quote = middle_step or edge_quote(1, 2, input_amount=first_hop_output, input_usd=float(notional_usd))
    middle_base_output = _quote_output_amount(middle_quote)
    third_probe_input = middle_base_output or first_hop_output
    third_step = _analysis_step(quote_analysis, 3)
    third_quote = third_step or edge_quote(2, 3, input_amount=third_probe_input, input_usd=float(notional_usd))
    third_hop_output = _quote_output_amount(third_quote)
    if third_hop_output is None:
        return None
    third_hop_input = _quote_input_amount(third_quote) or third_probe_input
    third_hop_rate = third_hop_output / third_hop_input if third_hop_input > 0 else None
    if third_hop_rate is None or third_hop_rate <= 0:
        return None

    flash_fee_amount = notional_amount * _UNISWAPX_FLASH_LOAN_FEE_BPS / 10_000.0
    gas_reserve_amount = _UNISWAPX_GAS_RESERVE_USD / input_price if input_price > 0 else 0.0
    target_profit_amount = notional_amount * UNISWAPX_MIN_PROFIT_BPS / 10_000.0
    required_final_output_amount = notional_amount + flash_fee_amount + gas_reserve_amount + target_profit_amount
    required_middle_output_amount = required_final_output_amount / third_hop_rate
    limit_price = (
        required_middle_output_amount / first_hop_output
        if first_hop_output > 0 and required_middle_output_amount > 0
        else None
    )
    return {
        "basis": "first_hop_actual_output_to_third_hop_1pct_profit",
        "targetProfitBps": UNISWAPX_MIN_PROFIT_BPS,
        "notionalUsd": float(notional_usd),
        "borrowSymbol": input_symbol,
        "notionalAmount": notional_amount,
        "flashLoanFeeBps": _UNISWAPX_FLASH_LOAN_FEE_BPS,
        "flashLoanFeeAmount": flash_fee_amount,
        "gasReserveUsd": _UNISWAPX_GAS_RESERVE_USD,
        "gasReserveAmount": gas_reserve_amount,
        "firstHopOutputAmount": first_hop_output,
        "middleInputSymbol": route_symbols[1],
        "middleOutputSymbol": route_symbols[2],
        "sellToken": route_symbols[1],
        "sellAmount": first_hop_output,
        "buyToken": route_symbols[2],
        "buyAmount": required_middle_output_amount,
        "minBuyAmount": required_middle_output_amount,
        "limitPrice": limit_price,
        "requiredFinalOutputAmount": required_final_output_amount,
        "thirdHopInputSymbol": route_symbols[2],
        "thirdHopOutputSymbol": route_symbols[3],
        "thirdHopEffectiveRate": third_hop_rate,
    }


def _uniswapx_analysis_requires_quote(analysis: dict[str, Any] | None) -> bool:
    if not isinstance(analysis, dict):
        return False
    if analysis.get("uniswapxQuoteRequired"):
        return True
    warnings = analysis.get("warnings") or []
    return any("uniswapx_quote_required" in str(warning) for warning in warnings)


def _candidate_replacement_eligible(route: dict[str, Any], quote_analysis: dict[str, Any] | None) -> bool:
    if not isinstance(quote_analysis, dict):
        return False
    if route.get("recommendedFirstPriority") is not True:
        return False
    if quote_analysis.get("profitable"):
        return False
    if not quote_analysis.get("complete"):
        return False
    if quote_analysis.get("requiresLiveQuoter") and not quote_analysis.get("steps"):
        return False
    hop_candidates = [
        candidate
        for candidate in route.get("hopCandidates") or []
        if isinstance(candidate, dict)
    ]
    if not hop_candidates:
        return False
    usable_counts = [
        len(_hop_pool_rows(candidate, only_usable=True))
        for candidate in hop_candidates
        if str(candidate.get("leg") or "").strip() != UNISWAPX_MIDDLE_LEG
    ]
    return bool(usable_counts) and all(count > 0 for count in usable_counts)


def _uniswapx_settlement(settlement: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(settlement, dict):
        return {}
    uniswapx = settlement.get("uniswapx")
    if isinstance(uniswapx, dict):
        return uniswapx
    if settlement.get("supported") is not None:
        return settlement
    return {}


def _normalized_pair_key(value: Any) -> tuple[str, str] | None:
    if isinstance(value, dict):
        from_value = value.get("fromSymbol") or value.get("fromAddress") or value.get("from_address") or value.get("from")
        to_value = value.get("toSymbol") or value.get("toAddress") or value.get("to_address") or value.get("to")
    elif isinstance(value, (list, tuple)) and len(value) == 2:
        from_value, to_value = value
    else:
        text = str(value or "").strip()
        for separator in (" -> ", "->", " - ", "-"):
            if separator in text:
                parts = [part.strip() for part in text.split(separator, 1)]
                if len(parts) == 2:
                    from_value, to_value = parts
                    break
        else:
            return None
    from_key = str(from_value or "").strip().upper()
    to_key = str(to_value or "").strip().upper()
    return (from_key, to_key) if from_key and to_key else None


def _uniswapx_pair_scope(uniswapx: dict[str, Any]) -> set[tuple[str, str]] | None:
    raw = None
    for key in ("pairs", "availablePairs", "supportedPairs"):
        if uniswapx.get(key) is not None:
            raw = uniswapx.get(key)
            break
    if not isinstance(raw, list):
        return None
    pairs: set[tuple[str, str]] = set()
    for item in raw:
        pair = _normalized_pair_key(item)
        if pair is not None:
            pairs.add(pair)
    return pairs


def _uniswapx_middle_hop_info(
    route: dict[str, Any],
    settlement: dict[str, Any] | None,
) -> tuple[dict[str, Any] | None, str]:
    uniswapx = _uniswapx_settlement(settlement)
    if not uniswapx.get("supported"):
        return None, "uniswapx_unsupported"
    middle = _hop_rows_by_leg(route).get(UNISWAPX_MIDDLE_LEG)
    if not isinstance(middle, dict):
        return None, "uniswapx_middle_hop_missing"
    from_symbol = str(middle.get("fromSymbol") or "").strip().upper()
    to_symbol = str(middle.get("toSymbol") or "").strip().upper()
    from_address = str(middle.get("fromAddress") or "").strip()
    to_address = str(middle.get("toAddress") or "").strip()
    if not (from_symbol and to_symbol and from_address and to_address):
        return None, "uniswapx_middle_hop_identity_missing"
    pair_scope = _uniswapx_pair_scope(uniswapx)
    if pair_scope is not None and (from_symbol, to_symbol) not in pair_scope and (to_symbol, from_symbol) not in pair_scope:
        return None, "uniswapx_middle_hop_unavailable"
    return {
        "middleLeg": UNISWAPX_MIDDLE_LEG,
        "fromSymbol": from_symbol,
        "toSymbol": to_symbol,
        "fromAddress": from_address,
        "toAddress": to_address,
        "symbolPair": f"{from_symbol} -> {to_symbol}",
    }, ""


def _resolve_uniswapx_middle_hop(
    route: dict[str, Any],
    settlement: dict[str, Any] | None,
    resolver: Callable[[dict[str, Any], dict[str, Any] | None], Any] | None,
) -> tuple[dict[str, Any] | None, str]:
    if callable(resolver):
        result = resolver(route, settlement)
        if result is False:
            return None, "uniswapx_middle_hop_unavailable"
        if isinstance(result, dict):
            if result.get("available") is False or result.get("suitable") is False:
                return None, "uniswapx_middle_hop_unavailable"
            middle_hop = result.get("middleHop")
            if isinstance(middle_hop, dict) and middle_hop.get("fromSymbol") and middle_hop.get("toSymbol"):
                return middle_hop, ""
    return _uniswapx_middle_hop_info(route, settlement)


def _uniswapx_middle_hop_quote_request(
    middle_hop: dict[str, Any],
    uniswapx: dict[str, Any],
    chain_id: int | None,
    *,
    quote_parameters: dict[str, Any] | None = None,
) -> dict[str, Any]:
    symbol_pair = str(middle_hop.get("symbolPair") or "").strip()
    if not symbol_pair:
        symbol_pair = f"{middle_hop.get('fromSymbol')} -> {middle_hop.get('toSymbol')}"
    request = {
        "venue": "uniswapx",
        "quoteMode": "intent_exact_input",
        "status": "waiting_live_price",
        "quoteRequestUrl": (
            f"/api/dex/quote-request?pair={quote(symbol_pair, safe='')}"
            f"&venue=uniswapx&hop={UNISWAPX_MIDDLE_LEG}"
        ),
        "routeSymbols": [middle_hop.get("fromSymbol"), middle_hop.get("toSymbol")],
        "routeAddresses": [middle_hop.get("fromAddress"), middle_hop.get("toAddress")],
        "chainId": uniswapx.get("chain_id") or chain_id,
        "reactorFamily": uniswapx.get("reactor_family"),
        "requires": ["sellToken", "buyToken", "amount", "userAddress"],
        "middleHop": dict(middle_hop),
    }
    if isinstance(quote_parameters, dict):
        request["quoteParameters"] = dict(quote_parameters)
        request["sellToken"] = quote_parameters.get("sellToken")
        request["buyToken"] = quote_parameters.get("buyToken")
        request["amount"] = quote_parameters.get("sellAmount")
        request["minBuyAmount"] = quote_parameters.get("buyAmount") or quote_parameters.get("minBuyAmount")
        request["limitPrice"] = quote_parameters.get("limitPrice")
        request["targetProfitBps"] = quote_parameters.get("targetProfitBps")
    return request


def _build_uniswapx_middle_hop_variant(
    route: dict[str, Any],
    pools: list[dict[str, Any]],
    *,
    middle_hop: dict[str, Any],
    settlement: dict[str, Any] | None,
    chain_id: int | None,
    quote_parameters: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    uniswapx = _uniswapx_settlement(settlement)
    variant = dict(route)
    hop_candidates: list[dict[str, Any]] = []
    middle_hop = dict(middle_hop)
    if isinstance(quote_parameters, dict):
        middle_hop["quoteParameters"] = dict(quote_parameters)
    changed = False
    for candidate in route.get("hopCandidates") or []:
        if not isinstance(candidate, dict):
            continue
        copied = dict(candidate)
        if str(candidate.get("leg") or "").strip() == UNISWAPX_MIDDLE_LEG:
            copied["venue"] = "uniswapx"
            copied["fillModel"] = "dutch_auction_reactor"
            copied["selectionBasis"] = UNISWAPX_FALLBACK_BASIS
            copied["preferredPoolId"] = ""
            copied["preferredPoolUsable"] = False
            copied["replacementVenue"] = "uniswapx"
            changed = True
        hop_candidates.append(copied)
    if not changed:
        return None
    reference_pool_path_key = str(
        route.get("referencePoolPathKey")
        or route.get("defaultPoolPathKey")
        or route.get("poolPathKey")
        or ""
    ).strip()
    replacement = {
        "attempted": True,
        "basis": UNISWAPX_FALLBACK_BASIS,
        "reason": "uniswapx_middle_hop_fallback",
        "venue": "uniswapx",
        "changedLegs": [UNISWAPX_MIDDLE_LEG],
        "middleLeg": UNISWAPX_MIDDLE_LEG,
        "middleHop": dict(middle_hop),
        "temporaryRoute": True,
        "notForDefaultScan": True,
        "historyOnlyAfterExecution": True,
        "executeLast": True,
        "referencePoolPathKey": reference_pool_path_key,
    }
    fallback = {
        "status": "selected",
        "venue": "uniswapx",
        "basis": UNISWAPX_FALLBACK_BASIS,
        "middleLeg": UNISWAPX_MIDDLE_LEG,
        "middleHop": dict(middle_hop),
        "referencePoolPathKey": reference_pool_path_key,
        "temporaryRoute": True,
        "notForDefaultScan": True,
        "historyOnlyAfterExecution": True,
        "executeLast": True,
    }
    if isinstance(quote_parameters, dict):
        fallback["quoteParameters"] = dict(quote_parameters)
        fallback["quoteRequest"] = {
            "venue": "uniswapx",
            "quoteMode": "intent_exact_input",
            "sellToken": quote_parameters.get("sellToken"),
            "buyToken": quote_parameters.get("buyToken"),
            "sellAmount": quote_parameters.get("sellAmount"),
            "minBuyAmount": quote_parameters.get("buyAmount") or quote_parameters.get("minBuyAmount"),
            "limitPrice": quote_parameters.get("limitPrice"),
            "targetProfitBps": quote_parameters.get("targetProfitBps"),
            "basis": quote_parameters.get("basis"),
        }
    variant["hopCandidates"] = hop_candidates
    variant["hopCandidateReplacement"] = replacement
    variant["uniswapxFallback"] = fallback
    variant["referencePoolPathKey"] = reference_pool_path_key
    variant["defaultPoolPathKey"] = reference_pool_path_key
    variant["selectionBasis"] = UNISWAPX_FALLBACK_BASIS
    variant["temporaryRoute"] = True
    variant["notForDefaultScan"] = True
    variant["temporaryRoutePolicy"] = _temporary_route_policy(
        route,
        selection_basis=UNISWAPX_FALLBACK_BASIS,
        reason="uniswapx_middle_hop_fallback",
    )
    variant["temporaryRouteSnapshot"] = {
        "poolPathKey": variant.get("poolPathKey") or route.get("poolPathKey") or "",
        "routePools": list(route.get("routePools") or pools),
        "hopCandidates": hop_candidates,
        "hopCandidateSelection": {
            "selectionBasis": UNISWAPX_FALLBACK_BASIS,
            "preferredPoolIds": [
                str(candidate.get("preferredPoolId") or "").strip()
                for candidate in hop_candidates
                if str(candidate.get("preferredPoolId") or "").strip()
            ],
        },
    }
    existing_inputs = route.get("quoteInputs") if isinstance(route.get("quoteInputs"), dict) else {}
    existing_requests = existing_inputs.get("quoteRequests") or []
    requests = [
        request
        for request in existing_requests
        if isinstance(request, dict) and str(request.get("venue") or "").strip().lower() != "uniswapx"
    ]
    requests.append(_uniswapx_middle_hop_quote_request(
        middle_hop,
        uniswapx,
        chain_id,
        quote_parameters=quote_parameters,
    ))
    variant["quoteInputs"] = {
        "candidatePools": list(pools[:5]),
        "routePools": list(pools),
        "quoteRequests": requests,
        "conditions": list(existing_inputs.get("conditions") or ["uniswapx_middle_hop_fallback"]),
    }
    return variant


def _uniswapx_selected_analysis(fallback_analysis: dict[str, Any] | None) -> dict[str, Any]:
    analysis = dict(fallback_analysis or {})
    warnings = list(analysis.get("warnings") or [])
    if "uniswapx_middle_hop_fallback_selected" not in warnings:
        warnings.append("uniswapx_middle_hop_fallback_selected")
    analysis.update(
        {
            "uniswapxFallbackSelected": True,
            "uniswapxMinProfitBps": UNISWAPX_MIN_PROFIT_BPS,
            "warnings": warnings,
        }
    )
    return analysis


def _uniswapx_failure(
    route: dict[str, Any],
    pools: list[dict[str, Any]],
    quote_analysis: dict[str, Any] | None,
    size_analysis: dict[str, Any],
    reason: str,
    detail: str | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any], dict[str, Any], dict[str, Any]]:
    reference_pool_path_key = str(
        route.get("referencePoolPathKey")
        or route.get("defaultPoolPathKey")
        or route.get("poolPathKey")
        or ""
    ).strip()
    replacement = {
        "attempted": True,
        "basis": UNISWAPX_FALLBACK_BASIS,
        "reason": reason,
        "venue": "uniswapx",
        "changedLegs": [],
        "stopSearching": True,
        "temporaryRoute": False,
        "referencePoolPathKey": reference_pool_path_key,
    }
    failed_route = dict(route)
    failed_route["hopCandidateReplacement"] = replacement
    failed_route["uniswapxFallback"] = {
        "status": "failed",
        "venue": "uniswapx",
        "reason": reason,
        "detail": detail or reason,
        "referencePoolPathKey": reference_pool_path_key,
    }
    failed_analysis = dict(quote_analysis or {})
    failed_analysis.update(
        {
            "terminalStatus": "uniswapx_fallback_failed",
            "failureReason": detail or reason,
            "blockers": [detail or reason],
            "profitable": False,
            "executableCandidate": False,
            "chainSubmissionCandidate": False,
            "uniswapxFallbackStatus": "failed",
            "recommendation": {
                "level": "blocked",
                "action": "UniswapX middle-hop fallback failed",
                "reasons": [detail or reason],
            },
        }
    )
    failed_route["quoteAnalysis"] = failed_analysis
    return failed_route, pools, failed_analysis, dict(size_analysis or {}), replacement


def maybe_replace_route_with_better_candidates(
    route: dict[str, Any],
    pools: list[dict[str, Any]],
    quote_analysis: dict[str, Any],
    *,
    route_quote_analysis_fn: Callable[..., dict[str, Any]],
    price_map: dict[str, float],
    chain_id: int | None,
    route_size_analysis_fn: Callable[..., dict[str, Any]] | None = None,
    size_analysis: dict[str, Any] | None = None,
    settlement: dict[str, Any] | None = None,
    uniswapx_middle_hop_available_fn: Callable[[dict[str, Any], dict[str, Any] | None], Any] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Apply the UniswapX middle-hop fallback to an unprofitable route.

    The old AMM pool-replacement policy is intentionally no longer used here.
    When the best closed route ``O -> X -> Y -> O`` cannot cover flash-loan
    fees and gas, the only fallback is to switch the middle ``Y-Z`` hop to a
    UniswapX intent fill.  If UniswapX has no suitable middle-hop fill, the
    route is marked failed and no other AMM candidate is searched.
    """
    size_analysis = dict(size_analysis or {})
    if not _candidate_replacement_eligible(route, quote_analysis):
        return (
            route,
            pools,
            quote_analysis,
            size_analysis,
            {
                "attempted": False,
                "basis": "tvl_depth_desc",
                "reason": "no_replacement_trigger",
            },
        )
    middle_hop, availability_reason = _resolve_uniswapx_middle_hop(
        route,
        settlement,
        uniswapx_middle_hop_available_fn,
    )
    if middle_hop is None:
        return _uniswapx_failure(
            route,
            pools,
            quote_analysis,
            size_analysis,
            availability_reason,
        )
    notional_usd = float(quote_analysis.get("notionalUsd") or _UNISWAPX_QUOTE_PROBE_NOTIONAL_USD)
    quote_parameters = _uniswapx_middle_hop_parameters(
        route,
        pools,
        quote_analysis,
        price_map=price_map,
        notional_usd=notional_usd,
        chain_id=chain_id,
    )
    if quote_parameters is None:
        return _uniswapx_failure(
            route,
            pools,
            quote_analysis,
            size_analysis,
            "uniswapx_middle_hop_parameters_unavailable",
        )
    variant = _build_uniswapx_middle_hop_variant(
        route,
        pools,
        middle_hop=middle_hop,
        settlement=settlement,
        chain_id=chain_id,
        quote_parameters=quote_parameters,
    )
    if variant is None:
        return _uniswapx_failure(
            route,
            pools,
            quote_analysis,
            size_analysis,
            "uniswapx_middle_hop_missing",
        )
    fallback_analysis = route_quote_analysis_fn(variant, price_map=price_map, chain_id=chain_id)
    if not isinstance(fallback_analysis, dict):
        return _uniswapx_failure(
            route,
            pools,
            quote_analysis,
            size_analysis,
            "uniswapx_fallback_quote_required",
        )
    if _uniswapx_analysis_requires_quote(fallback_analysis):
        return _uniswapx_failure(
            route,
            pools,
            quote_analysis,
            size_analysis,
            "uniswapx_fallback_quote_required",
        )
    if not _uniswapx_profit_meets_minimum(fallback_analysis):
        return _uniswapx_failure(
            route,
            pools,
            quote_analysis,
            size_analysis,
            "uniswapx_fallback_profit_below_minimum",
        )
    if route_size_analysis_fn is not None:
        size_analysis = dict(route_size_analysis_fn(variant, price_map=price_map, chain_id=chain_id) or {})
    return (
        variant,
        pools,
        _uniswapx_selected_analysis(fallback_analysis),
        size_analysis,
        variant.get("hopCandidateReplacement") or {},
    )


__all__ = [
    "DEFAULT_REPLACEMENT_LEGS",
    "DEFAULT_OUTPUT_BASIS",
    "DEFAULT_NET_PROFIT_BASIS",
    "UNISWAPX_MIDDLE_LEG",
    "UNISWAPX_FALLBACK_BASIS",
    "UNISWAPX_MIN_PROFIT_BPS",
    "output_leader_pool_ids",
    "build_route_variant",
    "replacement_route_variants",
    "maybe_replace_route_with_better_candidates",
]
