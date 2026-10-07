"""Candidate route selection and replacement for quote/routing pages.

The market-observation layer preserves every per-hop candidate pool so the
quote/routing page can replace the default depth-first working pools when the
current best route is not profitable.  The public functions here are the
extension point for future profit-aware selectors.
"""

from __future__ import annotations

from itertools import product
from typing import Any, Callable

from market.binance_market.dex_surface_observation_helpers import _pool_usable_for_observation
from market.binance_market.dex_surface_types import _number, _pool_address


LEG_ORDER = ("x-y", "y-z", "z-x")
DEFAULT_REPLACEMENT_LEGS = ("x-y", "z-x")
DEFAULT_OUTPUT_BASIS = "max_price_preferred_output_per_hop"
DEFAULT_NET_PROFIT_BASIS = "best_candidate_net_profit"
DEFAULT_CANDIDATE_POOL_LIMIT = 2
DEFAULT_MAX_REPLACEMENT_VARIANTS = 8


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
    ]
    return bool(usable_counts) and all(count > 0 for count in usable_counts) and any(count > 1 for count in usable_counts)


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
    replacement_legs: tuple[str, ...] = DEFAULT_REPLACEMENT_LEGS,
    output_selector: Callable[[list[dict[str, Any]]], list[dict[str, Any]]] | None = None,
    candidate_pool_limit: int = DEFAULT_CANDIDATE_POOL_LIMIT,
    max_variants: int = DEFAULT_MAX_REPLACEMENT_VARIANTS,
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Replace unprofitable working pools with a better candidate route.

    The default policy follows the design intent: when the best route
    ``A -> B -> C -> A`` (or ``A -> C -> B -> A``) is not profitable, try
    ``x-y`` pools that exchange the most B/C and ``z-x`` pools that exchange
    the most A/B.  ``output_selector`` and ``route_quote_analysis_fn`` are the
    injection points for a future live per-pool quote ranking.
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
    base_profit = _analysis_profit(quote_analysis)
    variants = replacement_route_variants(
        route,
        replacement_legs=replacement_legs,
        output_selector=output_selector,
        candidate_pool_limit=candidate_pool_limit,
        max_variants=max_variants,
    )
    if not variants:
        return (
            route,
            pools,
            quote_analysis,
            size_analysis,
            {
                "attempted": True,
                "basis": DEFAULT_NET_PROFIT_BASIS,
                "reason": "no_candidate_variants",
                "variantsEvaluated": 0,
            },
        )

    evaluated: list[dict[str, Any]] = []
    best_variant: dict[str, Any] | None = None
    best_analysis: dict[str, Any] | None = None
    best_profit = base_profit
    for variant in variants:
        variant_analysis = route_quote_analysis_fn(variant, price_map=price_map, chain_id=chain_id)
        variant_profit = _analysis_profit(variant_analysis)
        evaluated.append(
            {
                "poolPathKey": variant.get("poolPathKey") or "",
                "preferredPoolIds": (variant.get("hopCandidateSelection") or {}).get("preferredPoolIds") or [],
                "netProfitUsd": variant_profit,
                "profitable": bool((variant_analysis or {}).get("profitable")),
            }
        )
        if variant_profit is not None and (best_profit is None or variant_profit > best_profit):
            best_profit = variant_profit
            best_variant = variant
            best_analysis = variant_analysis

    if best_variant is None or best_profit == base_profit:
        return (
            route,
            pools,
            quote_analysis,
            size_analysis,
            {
                "attempted": True,
                "basis": DEFAULT_NET_PROFIT_BASIS,
                "reason": "no_better_candidate",
                "variantsEvaluated": len(evaluated),
                "currentNetProfitUsd": base_profit,
                "bestNetProfitUsd": best_profit,
                "evaluated": evaluated,
            },
        )

    if route_size_analysis_fn is not None:
        size_analysis = dict(route_size_analysis_fn(best_variant, price_map=price_map, chain_id=chain_id) or {})
    replacement = {
        "attempted": True,
        "basis": DEFAULT_NET_PROFIT_BASIS,
        "reason": "better_candidate_found",
        "variantsEvaluated": len(evaluated),
        "currentNetProfitUsd": base_profit,
        "bestNetProfitUsd": best_profit,
        "changedLegs": (best_variant.get("hopCandidateReplacement") or {}).get("changedLegs") or [],
        "selectedPoolPathKey": best_variant.get("poolPathKey") or "",
        "selectedPreferredPoolIds": (best_variant.get("hopCandidateSelection") or {}).get("preferredPoolIds") or [],
        "evaluated": evaluated,
        "temporaryRoute": True,
        "notForDefaultScan": True,
        "historyOnlyAfterExecution": True,
        "executeLast": True,
        "referencePoolPathKey": best_variant.get("referencePoolPathKey") or route.get("poolPathKey") or "",
    }
    return (
        best_variant,
        list(best_variant.get("routePools") or pools),
        best_analysis or quote_analysis,
        size_analysis,
        replacement,
    )


__all__ = [
    "DEFAULT_REPLACEMENT_LEGS",
    "DEFAULT_OUTPUT_BASIS",
    "DEFAULT_NET_PROFIT_BASIS",
    "output_leader_pool_ids",
    "build_route_variant",
    "replacement_route_variants",
    "maybe_replace_route_with_better_candidates",
]
