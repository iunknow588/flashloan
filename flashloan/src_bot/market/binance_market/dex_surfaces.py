from __future__ import annotations

from typing import Any
from urllib.parse import quote

from core.flash_loan_pool_discovery import query_flash_loan_pool_discovery
from market.binance_market.pool_candidates import dex_pool_candidates_payload


def _norm_symbol(value: object) -> str:
    return str(value or "").strip().upper()


def _int(value: object) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


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


def _borrowable_symbols_from_discovery(chain_id: int | None) -> list[str]:
    try:
        snapshot = query_flash_loan_pool_discovery(chain_id=chain_id)
    except Exception:
        return []
    symbols: list[str] = []
    for raw in (snapshot.get("raw_snapshots") or {}).values():
        if not isinstance(raw, dict):
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
    return sorted(discovered)


def _candidate_payload(chain_id: int | None, protocols: list[str] | None = None) -> dict[str, Any]:
    return dex_pool_candidates_payload(chain_id=chain_id, protocols=protocols or [])


def _candidate_pairs(pairs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        pair
        for pair in pairs
        if _pool_count(pair, "preferredPools") or _pool_count(pair, "executionPools") or _pool_count(pair, "strictQualityPools")
    ]


def dex_overview_payload(*, chain_id: int | None = None, protocols: list[str] | None = None) -> dict[str, Any]:
    pool_payload = _candidate_payload(chain_id, protocols)
    pairs = pool_payload.get("pairs") or []
    ready_pairs = _candidate_pairs(pairs)
    watch_pairs = [pair for pair in pairs if pair not in ready_pairs][:12]
    return {
        "mode": "dex_overview",
        "chain_id": chain_id,
        "selected_protocols": protocols or [],
        "summary": {
            **(pool_payload.get("summary") or {}),
            "arbitragePathCount": len(ready_pairs),
            "candidatePathCount": len(ready_pairs),
            "watchPairCount": len(watch_pairs),
        },
        "decisionBasis": "dex_candidate_pool_quality_depth",
        "poolStaticTable": pool_payload.get("poolStaticTable") or {},
        "flashLoanLiquidity": pool_payload.get("flashLoanLiquidity") or {},
        "dexSettlement": pool_payload.get("dexSettlement") or {},
        "arbitragePaths": ready_pairs,
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
    routes = []
    for pair in _candidate_pairs(pool_payload.get("pairs") or []):
        linkage = pair.get("flashLoanLinkage") or {}
        pools = pair.get("executionPools") or pair.get("preferredPools") or []
        route_symbols = _pair_symbols(pair)
        quote_requests = [
            {
                "venue": "amm_pool",
                "quoteMode": "pool_exact_input_probe",
                "status": "quoteable",
                "quoteRequestUrl": f"/api/dex/quote-request?pair={quote(str(pair.get('symbolPair') or ''), safe='')}&venue=amm_pool",
                "pool": pool.get("pool"),
                "protocol": pool.get("dex") or pool.get("protocol"),
                "fee": pool.get("fee"),
                "routeSymbols": route_symbols,
            }
            for pool in pools[:5]
        ]
        if uniswapx.get("supported") and len(route_symbols) >= 2:
            quote_requests.append(
                {
                    "venue": "uniswapx",
                    "quoteMode": "intent_exact_input",
                    "status": "quoteable",
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
                "routeSymbols": route_symbols,
                "borrowSymbols": linkage.get("borrow_symbols") or [],
                "strategy": "quote_candidate_from_high_quality_depth_pool",
                "poolCount": len(pools),
                "quoteStatus": "waiting_for_live_quote",
                "quoteVenueCount": len(quote_requests),
                "quoteInputs": {
                    "candidatePools": pools[:5],
                    "quoteRequests": quote_requests,
                    "conditions": [
                        "candidate pool must keep positive executable depth",
                        "AMM quote must pass slippage and gas checks",
                        "UniswapX quote requires an intent quote request and solver/filler availability",
                    ],
                },
                "settlementRails": pair.get("settlementRails") or [],
            }
        )
    return {
        "mode": "dex_quote_routes",
        "chain_id": chain_id,
        "selected_protocols": protocols or [],
        "summary": {
            "routeCount": len(routes),
            "candidateRouteCount": len(routes),
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
    success = [row for row in rows if str(row.get("category") or row.get("status") or "").lower().endswith("success")]
    failed = [row for row in rows if "fail" in str(row.get("category") or row.get("status") or "").lower()]
    blocked = [row for row in rows if "not_executable" in str(row.get("category") or row.get("status") or "").lower()]
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
