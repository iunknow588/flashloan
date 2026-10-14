from __future__ import annotations

from typing import Any

from dex.intent_trade.chains.robinhood.chain_test_policy import ROBINHOOD_TESTNET_CHAIN_ID
from dex.intent_trade.chains.robinhood_testnet.v4_pool_index import load_robinhood_testnet_v4_pool_index


def _number(value: object) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return 0.0
    return parsed if parsed == parsed and parsed not in (float("inf"), float("-inf")) else 0.0


def _pool_address(pool: dict[str, Any]) -> str:
    return str(pool.get("pool") or pool.get("address") or "").strip()


def _hop_fee_bps(pool: dict[str, Any]) -> float | None:
    if not isinstance(pool, dict):
        return None
    if "feeBps" in pool and pool.get("feeBps") not in (None, ""):
        return _number(pool.get("feeBps"))
    return None


def _find_hop_pool(pools: list[dict[str, Any]], *, from_addr: str, to_addr: str, from_sym: str, to_sym: str) -> dict[str, Any]:
    from_l = str(from_addr or "").strip().lower()
    to_l = str(to_addr or "").strip().lower()
    from_sym_n = str(from_sym or "").strip().upper()
    to_sym_n = str(to_sym or "").strip().upper()
    for pool in pools:
        if not isinstance(pool, dict):
            continue
        tokens = [str(pool.get("token0") or "").strip().lower(), str(pool.get("token1") or "").strip().lower()]
        if from_l and to_l:
            if from_l in tokens and to_l in tokens:
                return pool
            token_match = str(pool.get("pairAddress") or "").strip().lower()
            if token_match and from_l in token_match and to_l in token_match:
                return pool
        route_symbols = [str(item or "").strip().upper() for item in pool.get("routeSymbols") or []]
        if from_sym_n and to_sym_n and {from_sym_n, to_sym_n}.issubset(set(route_symbols)):
            return pool
        route_text = str(pool.get("routeText") or "").upper()
        if from_sym_n and to_sym_n and route_text and f"{from_sym_n} -> {to_sym_n}" in route_text:
            return pool
    return {}


def _static_hop_steps(
    *,
    pools: list[dict[str, Any]],
    route_symbols: list[str],
    route_addresses: list[str],
    route_quote_analysis: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = []
    if len(route_symbols) < 2:
        return steps
    analysis = route_quote_analysis or {}
    step_count = len(route_symbols) - 1
    ref_input_amount = analysis.get("referenceInputAmount")
    ref_output_amount = analysis.get("referenceFinalOutputAmount")
    ref_output_usd = analysis.get("referenceFinalOutputUsd")
    hop_ref_output_amount = ref_output_amount / step_count if ref_output_amount is not None else None
    hop_ref_output_usd = ref_output_usd / step_count if ref_output_usd is not None else None
    for index in range(len(route_symbols) - 1):
        from_symbol = route_symbols[index]
        to_symbol = route_symbols[index + 1]
        from_address = route_addresses[index] if index < len(route_addresses) else ""
        to_address = route_addresses[index + 1] if index + 1 < len(route_addresses) else ""
        pool = _find_hop_pool(
            pools,
            from_addr=from_address,
            to_addr=to_address,
            from_sym=from_symbol,
            to_sym=to_symbol,
        )
        steps.append(
            {
                "hop": index + 1,
                "fromSymbol": from_symbol,
                "toSymbol": to_symbol,
                "fromAddress": from_address,
                "toAddress": to_address,
                "tokenIn": from_address,
                "tokenOut": to_address,
                "pool": _pool_address(pool) or str(pool.get("pool") or ""),
                "protocol": str(pool.get("protocol") or pool.get("dex") or "uniswap_v4").lower(),
                "feeBps": _hop_fee_bps(pool),
                "priceImpactBps": pool.get("priceImpactBps"),
                "status": "static_reference_pending_live_review",
                "quoteSource": "market_observation_static_pool_route",
                "inputAmount": ref_input_amount if index == 0 else None,
                "outputAmount": None,
                "outputUsd": None,
                "referenceOutputAmount": hop_ref_output_amount,
                "referenceOutputUsd": hop_ref_output_usd,
                "effectiveRate": None,
            }
        )
    if analysis.get("referenceFinalOutputUsd") is not None:
        for step in steps:
            step["quoteSource"] = "reference_price_requires_live_quoter"
            step["status"] = "reference_price_requires_live_quoter"
    return steps


def _framework_route_pools(route_payload: dict[str, Any]) -> list[dict[str, Any]]:
    def _pool_identity(pool: dict[str, Any]) -> str:
        return str(
            pool.get("pool")
            or pool.get("poolId")
            or pool.get("pool_id")
            or pool.get("address")
            or ""
        ).strip()

    pools: list[dict[str, Any]] = []
    quote_inputs = route_payload.get("quoteInputs") if isinstance(route_payload.get("quoteInputs"), dict) else {}
    for key in ("routePools", "candidatePools"):
        for pool in quote_inputs.get(key) or []:
            if isinstance(pool, dict):
                pools.append(pool)
    for key in ("routePools", "preferredPools", "strictQualityPools", "executionPools", "rejectedPools"):
        for pool in route_payload.get(key) or []:
            if isinstance(pool, dict):
                pools.append(pool)

    merged_by_id: dict[str, dict[str, Any]] = {}
    ordered_ids: list[str] = []
    for pool in pools:
        pool_id = _pool_identity(pool).lower()
        if not pool_id:
            continue
        base = merged_by_id.get(pool_id)
        if base is None:
            base = {}
            merged_by_id[pool_id] = base
            ordered_ids.append(pool_id)
        for key, value in pool.items():
            if key == "pool":
                continue
            if key not in base or (value not in (None, "") and not str(base.get(key) or "").strip()):
                base[key] = value
        base["pool"] = base.get("pool") or _pool_identity(pool)
        base.setdefault("poolId", base.get("pool"))
    return [merged_by_id[pool_id] for pool_id in ordered_ids]


def _v4_pool_index_by_id(chain_id: int | None) -> dict[str, dict[str, Any]]:
    if chain_id != ROBINHOOD_TESTNET_CHAIN_ID:
        return {}
    index = load_robinhood_testnet_v4_pool_index()
    by_id: dict[str, dict[str, Any]] = {}
    for pool in index.get("pools") or []:
        if not isinstance(pool, dict):
            continue
        pool_id = str(pool.get("poolId") or pool.get("pool_id") or pool.get("id") or "").strip().lower()
        if pool_id:
            by_id[pool_id] = pool
    return by_id


def _enrich_route_pools_from_index(
    pools: list[dict[str, Any]],
    *,
    index_by_pool: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    if not index_by_pool:
        return pools
    enriched: list[dict[str, Any]] = []
    for pool in pools:
        if not isinstance(pool, dict):
            enriched.append(pool)
            continue
        pool_id = str(pool.get("pool") or pool.get("poolId") or pool.get("pool_id") or "").strip().lower()
        index_pool = index_by_pool.get(pool_id) if pool_id else None
        if not index_pool:
            enriched.append(pool)
            continue
        merged = dict(pool)
        for key, value in index_pool.items():
            if key not in merged or (value not in (None, "") and not str(merged.get(key) or "").strip()):
                merged[key] = value
        merged["pool"] = merged.get("pool") or index_pool.get("poolId") or index_pool.get("pool_id") or pool_id
        merged["poolId"] = merged.get("poolId") or index_pool.get("poolId") or merged.get("pool")
        enriched.append(merged)
    return enriched


def _overview_path_static_profit(path: dict[str, Any]) -> float | None:
    """Return the best static profit signal available on an overview path."""
    analysis = path.get("overviewProfit") if isinstance(path.get("overviewProfit"), dict) else {}
    if not analysis:
        analysis = path.get("quoteAnalysis") if isinstance(path.get("quoteAnalysis"), dict) else {}
    for key in ("observedNetProfitUsd", "netProfitUsd", "referenceNetProfitUsd"):
        value = analysis.get(key)
        if value is None:
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def _best_per_anchor_direction_paths(paths: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep only the highest-static-profit direction set per flash-loan anchor.

    All pool/address variants inside the selected direction remain available
    as hop candidates and backups; exact duplicate routes are dropped.
    """
    selected: list[dict[str, Any]] = []
    seen_routes: set[tuple[tuple[str, str], tuple[str, ...]]] = set()
    direction_sets_by_anchor: dict[
        tuple[str, str],
        dict[tuple[str, ...], list[tuple[int, dict[str, Any], float | None]]],
    ] = {}
    for index, path in enumerate(paths):
        route = path.get("overviewRoute") if isinstance(path.get("overviewRoute"), dict) else {}
        symbols = [
            str(symbol or "").strip().upper()
            for symbol in path.get("routeSymbols") or route.get("pathSymbols") or []
            if str(symbol or "").strip()
        ]
        anchor_symbol = str(
            route.get("anchorBorrowSymbol")
            or (symbols[0] if symbols else "")
        ).strip().upper()
        if not anchor_symbol:
            continue
        route_addresses = [
            str(item or "").strip()
            for item in path.get("routeAddresses") or []
            if str(item or "").strip()
        ]
        anchor_address = str(
            route.get("anchorBorrowAddress")
            or route.get("anchorAddress")
            or ""
        ).strip().lower()
        if not anchor_address and route_addresses:
            anchor_address = route_addresses[0].lower()
        middle_direction = tuple(symbols[1:-1]) if len(symbols) >= 4 else tuple(symbols)
        anchor_key = (anchor_symbol, anchor_address)
        identity_parts = (
            [address.lower() for address in route_addresses]
            if route_addresses
            else list(symbols)
        )
        pool_path_key = str(
            path.get("poolPathKey")
            or path.get("referencePoolPathKey")
            or ""
        ).strip().lower()
        if pool_path_key:
            identity_parts.append(pool_path_key)
        else:
            identity_parts.extend(
                str(pool.get("pool") or pool.get("poolId") or "").strip().lower()
                for pool in path.get("executionPools") or path.get("preferredPools") or []
                if isinstance(pool, dict)
                and str(pool.get("pool") or pool.get("poolId") or "").strip()
            )
        route_identity = tuple(identity_parts)
        dedup_key = (anchor_key, route_identity)
        if dedup_key in seen_routes:
            continue
        seen_routes.add(dedup_key)
        direction_sets_by_anchor.setdefault(anchor_key, {}).setdefault(
            middle_direction, []
        ).append((index, path, _overview_path_static_profit(path)))
    for anchor_key in direction_sets_by_anchor:
        direction_sets = direction_sets_by_anchor[anchor_key]
        best_direction_key = max(
            direction_sets,
            key=lambda direction_key: (
                max(
                    (
                        profit
                        for _, _, profit in direction_sets[direction_key]
                        if profit is not None
                    ),
                    default=float("-inf"),
                ),
                -min(index for index, _, _ in direction_sets[direction_key]),
            ),
        )
        selected.extend(
            path
            for _, path, _ in sorted(
                direction_sets[best_direction_key],
                key=lambda item: item[0],
            )
        )
    return selected


__all__ = [
    "_best_per_anchor_direction_paths",
    "_enrich_route_pools_from_index",
    "_find_hop_pool",
    "_framework_route_pools",
    "_hop_fee_bps",
    "_number",
    "_overview_path_static_profit",
    "_pool_address",
    "_static_hop_steps",
    "_v4_pool_index_by_id",
]
