from __future__ import annotations

import json
import os
from typing import Any

from flask import Response, g, jsonify, request

from core.chain_selection import ChainSelectionError
from web.data_routes.manager_page_compare import (
    quote_request_manager_page_payload,
    quote_routes_manager_page_payload,
)
from web.data_routes import dex_surfaces as _root
from web.data_routes.dex_surface_helpers import (
    _infer_route_addresses_from_pools,
    _merge_manifest_live_evidence_into_routes,
)
from market.binance_market.dex_quote_surface_routes_builder import _pool_usable_for_observation


_STATIC_ROUTE_PAYLOAD_CACHE: dict[tuple[str, tuple[str, ...], str, float], dict[str, Any]] = {}
_STATIC_ROUTE_JSON_CACHE: dict[tuple[str, tuple[str, ...], str, float], bytes] = {}


def _is_zero_address(value: object) -> bool:
    text = str(value or "").strip().lower()
    if not text.startswith("0x"):
        return False
    try:
        return int(text, 16) == 0
    except ValueError:
        return False


def _route_has_zero_address(route: dict) -> bool:
    for key in ("routeAddresses", "route_addresses", "addresses", "pathAddresses"):
        items = route.get(key) if isinstance(route, dict) else None
        if isinstance(items, (list, tuple)) and any(_is_zero_address(item) for item in items):
            return True
    return False


def _protocols_include_uniswap_v4(protocols: list[str] | None) -> bool:
    lowered = {str(protocol or "").strip().lower() for protocol in (protocols or [])}
    return bool(lowered & {"uniswap_v4", "v4"})


def _active_v4_pool_ids_from_overview(overview: dict[str, Any]) -> set[str]:
    active: set[str] = set()
    for pool in _registry_pool_rows(overview.get("poolRegistry")):
        if isinstance(pool, dict) and _pool_has_liquidity(pool):
            raw = str(pool.get("pool") or pool.get("poolId") or "").strip().lower()
            if raw:
                active.add(raw)
    for summary in overview.get("closedLoopRouteSummaries") or []:
        if not isinstance(summary, dict):
            continue
        for pool in summary.get("routePools") or []:
            if isinstance(pool, dict) and _pool_has_liquidity(pool):
                raw = str(pool.get("pool") or pool.get("poolId") or "").strip().lower()
                if raw:
                    active.add(raw)
    return active


def _complete_route_addresses(
    route_symbols: list[str],
    route_addresses: list[str],
    *,
    chain_id: int,
    protocols: list[str],
) -> list[str]:
    """Resolve a quote-test route to the current closed-loop path addresses."""
    addresses = [
        str(address or "").strip().lower()
        for address in route_addresses
        if str(address or "").strip()
    ]
    if (
        len(addresses) == 4
        and addresses[0] == addresses[-1]
        and not any(_is_zero_address(address) for address in addresses)
    ):
        return addresses

    from market.binance_market.dex_route_helpers import (
        _official_route_addresses,
        _route_addresses_from_route,
    )
    from market.binance_market.dex_route_test import _match_route, _routes_payload

    official = [
        str(address or "").strip().lower()
        for address in _official_route_addresses(route_symbols, chain_id=chain_id)
        if str(address or "").strip()
    ]
    if (
        len(official) == 4
        and official[0] == official[-1]
        and not any(_is_zero_address(address) for address in official)
    ):
        return official

    try:
        routes_payload = _routes_payload(chain_id=chain_id, protocols=protocols)
        route = _match_route(routes_payload.get("routes") or [], route_symbols)
    except Exception:
        route = None
    if isinstance(route, dict):
        resolved = [
            str(address or "").strip().lower()
            for address in (
                _route_addresses_from_route(route)
                or _infer_route_addresses_from_pools(route)
                or []
            )
            if str(address or "").strip()
        ]
        if (
            len(resolved) == 4
            and resolved[0] == resolved[-1]
            and not any(_is_zero_address(address) for address in resolved)
        ):
            return resolved
    return []


def _registry_pool_rows(registry: object) -> list[dict[str, Any]]:
    """Normalize a persisted pool registry to row dicts.

    New snapshots persist ``poolRegistry`` as a dict keyed by pool id, but
    older files and hand-written fixtures may store a bare list of pool rows.
    """
    if isinstance(registry, dict):
        return [row for row in registry.values() if isinstance(row, dict)]
    if isinstance(registry, list):
        return [row for row in registry if isinstance(row, dict)]
    return []


def _hydrate_static_pool_liquidity(pool: dict[str, Any], overview: dict[str, Any]) -> dict[str, Any]:
    """Merge persisted liquidity evidence into one static route-pool row."""
    pool_id = str(pool.get("pool") or pool.get("poolId") or "").strip().lower()
    if not pool_id:
        return pool
    for key in ("liquidPoolRegistry", "poolRegistry"):
        registry = overview.get(key) or {}
        if not isinstance(registry, dict):
            continue
        row = registry.get(pool_id)
        if not isinstance(row, dict):
            continue
        hydrated = dict(pool)
        for evidence_key in (
            "usableForObservation",
            "hasLiquidity",
            "liquidityIndex",
            "liquidity",
            "liquiditySource",
            "depthStatus",
            "estimatedTvlUsd",
        ):
            if evidence_key in row:
                hydrated[evidence_key] = row[evidence_key]
        return hydrated
    return pool


def _pool_has_liquidity(pool: dict[str, Any]) -> bool:
    if pool.get("dynamicFee") or pool.get("specialFee"):
        return False
    try:
        if int(str(pool.get("fee") or "").strip() or 0) & 8388608:
            return False
        if int(str(pool.get("fee") or "").strip() or 0) > 100_000:
            return False
    except (TypeError, ValueError):
        pass
    if bool(pool.get("hasLiquidity")):
        return True
    try:
        liquidity = float(str(pool.get("liquidity") or pool.get("liquidityIndex") or "0"))
    except (TypeError, ValueError):
        liquidity = 0.0
    return liquidity > 0


def _route_pool_ids(route_pools: list[dict[str, Any]]) -> list[str]:
    pool_ids: list[str] = []
    for pool in route_pools:
        if not isinstance(pool, dict):
            continue
        raw = str(pool.get("pool") or pool.get("poolId") or "").strip().lower()
        if raw and raw not in pool_ids:
            pool_ids.append(raw)
    return pool_ids


def _route_pool_has_complete_v4_identity(pool: dict[str, Any]) -> bool:
    pool_key = pool.get("poolKey")
    if not isinstance(pool_key, dict):
        return False
    currency0 = str(pool_key.get("currency0") or pool_key.get("token0") or "").strip()
    currency1 = str(pool_key.get("currency1") or pool_key.get("token1") or "").strip()
    fee = pool_key.get("fee")
    tick_spacing = pool_key.get("tickSpacing")
    hooks = str(pool_key.get("hooks") or "").strip()
    return bool(currency0 and currency1 and fee is not None and tick_spacing is not None and hooks)


def _route_has_complete_v4_pool_identity(route: dict[str, Any]) -> bool:
    route_pools = [pool for pool in (route.get("routePools") or []) if isinstance(pool, dict)]
    if not route_pools:
        return False
    symbols = [
        str(item or "").strip().upper()
        for item in route.get("routeSymbols") or []
        if str(item or "").strip()
    ]
    if symbols:
        route_pools = route_pools[:len(symbols) - 1]
    return bool(route_pools) and all(_route_pool_has_complete_v4_identity(pool) for pool in route_pools)


def filter_quote_routes_with_complete_v4_pool_identity(payload: dict[str, Any]) -> dict[str, Any]:
    """Drop routes that lack a complete V4 pool identity from quote surfaces."""
    containers = []
    if isinstance(payload.get("routes"), list):
        containers.append(payload)
    snapshot = payload.get("snapshot")
    if isinstance(snapshot, dict):
        snapshot_payload = snapshot.get("payload")
        if isinstance(snapshot_payload, dict):
            containers.append(snapshot_payload)
    for container in containers:
        routes = container.get("routes")
        if not isinstance(routes, list):
            continue
        filtered = [
            _infer_route_addresses_from_pools(route)
            for route in routes
            if isinstance(route, dict) and _route_has_complete_v4_pool_identity(route)
        ]
        container["routes"] = filtered
        summary = container.get("summary")
        if isinstance(summary, dict):
            for key in ("routeCount", "candidateRouteCount", "chainReviewEligiblePathCount"):
                if key in summary:
                    summary[key] = len(filtered)
    return payload


def _route_uses_active_v4_pools(route_pools: list[dict[str, Any]], overview: dict[str, Any]) -> bool:
    active = _active_v4_pool_ids_from_overview(overview)
    if not active:
        return True
    pool_ids = _route_pool_ids(route_pools)
    if not pool_ids:
        return False
    return all(pool_id in active for pool_id in pool_ids)


def _route_pools_usable_for_observation(route: dict[str, Any]) -> bool:
    pools = [pool for pool in (route.get("routePools") or []) if isinstance(pool, dict)]
    if not pools:
        return False
    return all(_pool_usable_for_observation(pool) for pool in pools)


def _route_has_explicit_unusable_liquidity(route: dict[str, Any]) -> bool:
    """Return whether a route is explicitly unusable across every hop.

    Routes with missing liquidity evidence are kept so legacy snapshots remain
    quoteable until the market-observation handoff marks each hop liquid.
    """
    pools = [pool for pool in (route.get("routePools") or []) if isinstance(pool, dict)]
    if not pools:
        return True
    return any(
        not _pool_usable_for_observation(pool)
        and any(
            key in pool
            for key in ("usableForObservation", "hasLiquidity", "liquidityIndex", "liquidity")
        )
        for pool in pools
    )


def _static_route_liquidity_evidence(route_pools: list[dict[str, Any]]) -> tuple[bool | None, bool]:
    """Return (usable, has_explicit_evidence) for static route pools.

    ``usable`` stays ``None`` when the snapshot has not attached liquidity
    evidence, so the page can distinguish "no evidence yet" from "explicitly
    no liquidity" instead of treating initialized-but-unpriced pools as usable.
    """
    pools = [pool for pool in route_pools if isinstance(pool, dict)]
    if not pools:
        return False, True
    explicit_pools: list[dict[str, Any]] = []
    for pool in pools:
        for key in ("usableForObservation", "hasLiquidity", "liquidityIndex", "liquidity"):
            value = pool.get(key)
            if value is not None and str(value).strip() != "":
                explicit_pools.append(pool)
                break
    if not explicit_pools:
        return None, False
    return all(_pool_usable_for_observation(pool) for pool in explicit_pools), True


def _explicit_chain_id_is_robinhood_supported() -> bool:
    from core.chain_data_registry import chain_data_by_chain_id

    def chain_supports_official_v4(chain_id: int | None) -> bool:
        if chain_id is None:
            return False
        row = chain_data_by_chain_id(chain_id) or {}
        features = row.get("features") if isinstance(row.get("features"), dict) else {}
        dex = features.get("dex_arbitrage") if isinstance(features.get("dex_arbitrage"), dict) else {}
        flash_loan = row.get("flash_loan") if isinstance(row.get("flash_loan"), dict) else {}
        return bool(dex.get("protocol_supported")) and "uniswap_v4" in {
            str(protocol or "").strip().lower()
            for protocol in flash_loan.get("candidate_protocols") or []
        }

    raw_chain = request.args.get("chain_id", "").strip()
    if raw_chain:
        try:
            return chain_supports_official_v4(int(raw_chain))
        except (TypeError, ValueError):
            return False

    # A canonical market id like dex-arbitrage-46630-uniswap-v4 also explicitly
    # names the chain, but an explicit conflicting chain_id remains authoritative.
    import re

    raw_market = request.args.get("market_id", "").strip().lower()
    match = re.match(r"^(?:liquidation|dex-arbitrage)-(\d+)-[a-z0-9_-]+$", raw_market)
    if match:
        try:
            return chain_supports_official_v4(int(match.group(1)))
        except (TypeError, ValueError):
            return False
    return False


def _route_has_unresolved_zero_address(route: dict, protocols: list[str] | None) -> bool:
    """Zero address is a legal Uniswap V4 native-ETH sentinel.

    The candidate surface already keeps V4 zero-address pools as ETH. Quote and
    route pages must follow the same protocol rule instead of treating every
    ``0x0`` address as an invalid token address.
    """
    if not _route_has_zero_address(route):
        return False
    return not _protocols_include_uniswap_v4(protocols)


def register_dex_quote_routes(app) -> None:
    # Retained for legacy regression fixtures and diagnostics only. The quote
    # page must read the manager-only /api/dex/quote-routes projection.
    @app.get("/api/dex/quote-routes/static")
    def dex_quote_routes_static():
        try:
            chain_selection, selected_chain_id = _root.selected_chain_id_from_request()
        except ChainSelectionError as exc:
            return _root.shared._chain_selection_error_response(exc)
        protocols = _root.request_dex_protocols_arg()
        overview = _root.load_observation_overview_for_scope(
            chain_id=selected_chain_id,
            protocols=protocols,
        )
        if not overview.get("exists"):
            return jsonify(
                {
                    "ok": False,
                    "mode": "dex_quote_routes_static",
                    "chain_id": selected_chain_id,
                    "selected_protocols": protocols,
                    "error": "market_observation_overview_missing",
                    "detail": "static market observation overview is not ready for this chain/protocol",
                }
            )
        overview_path = str(overview.get("path") or "")
        try:
            overview_mtime = os.path.getmtime(overview_path) if overview_path else 0.0
        except OSError:
            overview_mtime = 0.0
        cache_key = (
            str(selected_chain_id),
            tuple(sorted(str(protocol or "").strip().lower() for protocol in (protocols or []))),
            str(request.args.get("dex_strategy") or "").strip().lower(),
            request.query_string.decode("utf-8"),
            overview_mtime,
            str(overview.get("snapshotFingerprint") or ""),
        )
        cached = _STATIC_ROUTE_PAYLOAD_CACHE.get(cache_key)
        if cached is not None:
            cached_json = _STATIC_ROUTE_JSON_CACHE.get(cache_key)
            if cached_json is None:
                cached_json = json.dumps(
                    cached,
                    ensure_ascii=True,
                    separators=(",", ":"),
                ).encode("utf-8")
                _STATIC_ROUTE_JSON_CACHE[cache_key] = cached_json
            return Response(cached_json, mimetype="application/json")
        routes = []
        summaries = overview.get("closedLoopRouteSummaries") or []
        seen = set()
        for summary in summaries:
            if not isinstance(summary, dict):
                continue
            path_symbols = list(summary.get("pathSymbols") or [])
            if not path_symbols:
                path_symbols = [
                    part.strip().upper()
                    for part in str(summary.get("pathText") or "").replace("->", " -> ").split()
                    if part.strip()
                ]
            if (
                len(path_symbols) < 4
                or str(path_symbols[0]).upper() != str(path_symbols[-1]).upper()
            ):
                continue
            path_text = str(summary.get("pathText") or " -> ".join(path_symbols))
            dedup_key = "|".join([str(s).upper() for s in path_symbols])
            if dedup_key in seen:
                continue
            seen.add(dedup_key)
            route_addresses = [
                str(address or "").strip()
                for address in summary.get("routeAddresses") or []
                if str(address or "").strip()
            ]
            if any(_is_zero_address(address) for address in route_addresses) and not _protocols_include_uniswap_v4(protocols):
                continue
            route_pools = [
                _compact_static_route_pool(
                    _hydrate_static_pool_liquidity(
                        pool,
                        overview,
                    )
                )
                for pool in summary.get("routePools") or []
                if isinstance(pool, dict)
            ]
            route_pools = [pool for pool in route_pools if pool]
            if len(route_pools) < 3:
                continue
            if not _route_has_complete_v4_pool_identity({"routeSymbols": path_symbols, "routePools": route_pools}):
                continue
            if not _route_uses_active_v4_pools(route_pools, overview):
                continue
            route_usable, route_has_explicit_liquidity = _static_route_liquidity_evidence(route_pools)
            if route_usable is False and route_has_explicit_liquidity:
                continue
            steps = [
                {
                    "hop": index + 1,
                    "fromSymbol": path_symbols[index],
                    "toSymbol": path_symbols[index + 1],
                    "protocol": "uniswap_v4",
                    "quoteSource": "chain_live_quote_required",
                    "status": "pending_offchain_review",
                }
                for index in range(len(path_symbols) - 1)
            ]
            routes.append(
                {
                    "symbolPair": path_text,
                    "routeSymbols": path_symbols,
                    "routeAddresses": route_addresses,
                    "routePools": route_pools,
                    "poolIds": list(summary.get("poolIds") or []),
                    "tableSource": "dex_table",
                    "referencedTable": "ob_table",
                    "pathSource": "market_observation",
                    "diagnosticOnly": False,
                    "chainReviewEligible": True,
                    "routeUsableForObservation": route_usable,
                    "routeHasLiquidityEvidence": route_has_explicit_liquidity,
                    "liquidPoolCount": sum(
                        1 for pool in route_pools if _pool_usable_for_observation(pool)
                    ),
                    "overviewRoute": {
                        "anchorBorrowSymbol": str(summary.get("anchorSymbol") or path_symbols[0]),
                        "targetSymbol": str(summary.get("counterSymbol") or path_symbols[-2]),
                        "pathSymbols": path_symbols,
                        "pathText": path_text,
                        "kind": "bridge_pair",
                        "routeSemantics": "x_y_entry_y_z_bridge_z_x_exit",
                        "marketObservationRoute": True,
                        "routeUsableForObservation": route_usable,
                        "hopLiquidityEvidence": [
                            {
                                "pool": pool.get("pool") or pool.get("poolId"),
                                "usableForObservation": _pool_usable_for_observation(pool),
                                "hasLiquidity": pool.get("hasLiquidity"),
                            }
                            for pool in route_pools
                        ],
                    },
                    "quoteAnalysis": {
                        "steps": steps,
                        "requiresLiveQuoter": True,
                        "adapterRequired": True,
                        "profitable": False,
                        "executableCandidate": False,
                        "chainSubmissionCandidate": False,
                        "referenceReturnBasis": "borrow_token",
                        "priceEvidenceSource": "chain_live_quote_required",
                        "priceEvidenceMode": "live_required",
                        "referenceInputSymbol": path_symbols[0],
                    },
                    "sizeAnalysis": {},
                    "quoteCosts": {},
                    "reviewMode": "pending_offchain_review",
                    "__offchainReviewed": False,
                }
            )
        routes = [_infer_route_addresses_from_pools(route) for route in routes]
        _merge_manifest_live_evidence_into_routes(routes, chain_id=int(selected_chain_id))
        overview_summary = overview.get("summary") or {}
        overview_liquid = overview.get("liquidityPoolFilter") or {}
        candidate_liquid_registry = overview.get("liquidPoolRegistry") or {}
        if isinstance(candidate_liquid_registry, dict):
            candidate_liquid_registry = {
                str(key or "").lower(): row
                for key, row in candidate_liquid_registry.items()
                if isinstance(row, dict)
            }
        if not candidate_liquid_registry:
            raw_registry = overview.get("poolRegistry") or {}
            if isinstance(raw_registry, dict):
                candidate_liquid_registry = {
                    str(key or "").lower(): row
                    for key, row in raw_registry.items()
                    if isinstance(row, dict)
                }
            else:
                candidate_liquid_registry = {
                    str(pool.get("pool") or pool.get("poolId") or "").strip().lower(): pool
                    for pool in _registry_pool_rows(raw_registry)
                    if isinstance(pool, dict)
                    and str(pool.get("pool") or pool.get("poolId") or "").strip()
                }
        candidate_liquid_pool_count = sum(
            1 for pool in candidate_liquid_registry.values() if _pool_has_liquidity(pool)
        )
        closed_loop_registry_pool_ids = {
            str(existing_pool.get("pool") or existing_pool.get("poolId") or "").strip().lower()
            for existing_pool in _registry_pool_rows(overview.get("poolRegistry"))
        }
        closed_loop_liquid_pool_count = sum(
            1
            for route_pool in _registry_pool_rows(overview.get("poolRegistry"))
            if _pool_has_liquidity(route_pool)
        ) + sum(
            1
            for pool_id, route_pool in candidate_liquid_registry.items()
            if pool_id not in closed_loop_registry_pool_ids
            and isinstance(route_pool, dict)
            and _pool_has_liquidity(route_pool)
        )
        payload = {
            "ok": True,
            "mode": "dex_quote_routes_static",
            "chain_id": selected_chain_id,
            "selected_protocols": protocols,
            "tableSource": "dex_table",
            "referencedTable": "ob_table",
            "poolIndexVersion": overview.get("poolIndexVersion") or {},
            "marketObservationSource": "cached_snapshot",
            "marketObservationSourceStage": "cached_snapshot",
            "liquidPoolRegistry": candidate_liquid_registry,
            "summary": {
                "routeCount": len(routes),
                "candidateRouteCount": len(routes),
                "chainReviewEligiblePathCount": len(routes),
                "diagnosticOnlyPathCount": 0,
                "marketObservationRouteCount": int(
                    overview_summary.get("closedLoopRouteCount")
                    or len(routes)
                ),
                "completeStaticQuoteCount": 0,
                "profitableRouteCount": 0,
                "chainSubmissionCandidateCount": 0,
                "candidateLiquidPoolCount": candidate_liquid_pool_count,
                "closedLoopLiquidPoolCount": closed_loop_liquid_pool_count,
                "liquidPoolRegistryCount": len(candidate_liquid_registry),
                "pathSource": "market_observation",
            },
            "routes": routes,
            "closedLoopGroups": _compact_static_groups(overview),
            "chain_selection": chain_selection,
        }
        payload = _root._attach_dex_strategy(payload)
        _STATIC_ROUTE_PAYLOAD_CACHE[cache_key] = payload
        payload_json = json.dumps(
            payload,
            ensure_ascii=True,
            separators=(",", ":"),
        ).encode("utf-8")
        _STATIC_ROUTE_JSON_CACHE[cache_key] = payload_json
        return Response(payload_json, mimetype="application/json")

    @app.get("/api/dex/quote-routes")
    def dex_quote_routes():
        try:
            chain_selection, selected_chain_id = _root.selected_chain_id_from_request()
        except ChainSelectionError as exc:
            return _root.shared._chain_selection_error_response(exc)
        protocols = _root.request_dex_protocols_arg()
        anchor = str(request.args.get("anchor") or "").strip() or None
        payload = quote_routes_manager_page_payload(
            chain_id=selected_chain_id,
            protocols=protocols,
            anchor=anchor,
            chain_selection=chain_selection,
            request_id=getattr(g, "request_id", None),
        )
        if not payload.get("ok"):
            upstream_status = payload.get("upstreamStatus")
            response_status = upstream_status if isinstance(upstream_status, int) else 503
            return (
                jsonify(
                    {
                        "ok": False,
                        "mode": "dex_quote_routes",
                        "dataSource": "manager",
                        "error": "manager_read_failed",
                        "managerDetail": payload,
                    }
                ),
                response_status,
            )
        return jsonify(_root._attach_dex_strategy(payload))


def _compact_static_route_pool(pool: dict) -> dict:
    try:
        _fee_int = int(str(pool.get("fee") or "").strip() or 0)
    except (TypeError, ValueError):
        _fee_int = 0
    _dynamic_fee = bool(_fee_int & 8388608)
    _special_fee = _dynamic_fee or _fee_int > 100_000
    compact = dict(pool)
    compact.update(
        {
            "pool": str(pool.get("pool") or pool.get("poolId") or "").strip(),
            "poolId": str(pool.get("poolId") or pool.get("pool") or "").strip(),
            "pairSymbol": str(pool.get("pairSymbol") or pool.get("symbolPair") or "").strip(),
            "symbolPair": str(pool.get("symbolPair") or pool.get("pairSymbol") or "").strip(),
            "routeSymbols": list(pool.get("routeSymbols") or []),
            "routeText": str(pool.get("routeText") or "").strip(),
            "fee": pool.get("fee"),
            "dynamicFee": pool.get("dynamicFee", _dynamic_fee),
            "specialFee": pool.get("specialFee", _special_fee),
            "feeLabel": pool.get("feeLabel") or ("dynamic" if _dynamic_fee else "special" if _special_fee else pool.get("fee")),
            "usableForObservation": pool.get("usableForObservation"),
            "status": str(pool.get("status") or pool.get("depthStatus") or "").strip(),
            "depthStatus": str(pool.get("depthStatus") or "").strip(),
        }
    )
    if isinstance(pool.get("poolKey"), dict):
        compact["poolKey"] = pool.get("poolKey")
    for key in (
        "routeAddresses",
        "token0",
        "token1",
        "token0Symbol",
        "token1Symbol",
        "token0Decimals",
        "token1Decimals",
        "tokens",
        "tick",
        "tickSpacing",
        "hooks",
        "sqrtPriceX96",
        "hasLiquidity",
        "liquidity",
        "liquidityIndex",
        "protocol",
        "dex",
    ):
        if pool.get(key) not in (None, ""):
            compact[key] = pool.get(key)
    return compact


def _compact_static_groups(overview: dict) -> list[dict]:
    borrowable = set(str(symbol or "").upper() for symbol in overview.get("borrowableSymbols") or [])
    counters: dict[str, dict] = {}
    for summary in overview.get("closedLoopRouteSummaries") or []:
        if not isinstance(summary, dict):
            continue
        anchor = str(summary.get("anchorSymbol") or "").upper()
        if not anchor:
            path_symbols = list(summary.get("pathSymbols") or [])
            anchor = str(path_symbols[0] or "").upper() if path_symbols else ""
        if not anchor:
            continue
        group = counters.setdefault(
            anchor,
            {
                "flashLoanToken": {"symbol": anchor},
                "startToken": {"symbol": anchor},
                "endToken": {"symbol": anchor},
                "borrowable": anchor in borrowable,
                "routeCount": 0,
                "poolDependencyCount": 0,
            },
        )
        group["routeCount"] += 1
    return sorted(
        counters.values(),
        key=lambda group: (0 if group["borrowable"] else 1, group["flashLoanToken"]["symbol"]),
    )


def register_dex_quote_route_test_routes(app) -> None:
    @app.post("/api/dex/quote-route/test")
    def dex_quote_route_test():
        from market.binance_market.dex_route_test import (
            _match_route,
            _routes_payload,
            best_amount_calculation_from_route,
            estimate_route_live_quote,
            is_official_triangle_route,
            normalize_route_symbols,
            recommended_route_test_amount,
            run_official_v4_arb_route_test,
        )

        try:
            chain_selection, selected_chain_id = _root.selected_chain_id_from_request()
        except ChainSelectionError as exc:
            return _root.shared._chain_selection_error_response(exc)
        protocols = _root.request_dex_protocols_arg()
        body = request.get_json(silent=True) or {}
        route_symbols = normalize_route_symbols(
            body.get("routeSymbols")
            or body.get("path")
            or body.get("pair")
            or body.get("symbolPair")
            or ""
        )
        route_addresses = [
            str(address or "").strip()
            for address in (body.get("routeAddresses") or [])
            if str(address or "").strip()
        ]
        amount_is_best = str(body.get("amountIsBest") or body.get("amount_is_best") or "").strip().lower() in {"1", "true", "yes", "on"}
        execution_reuse_amount = str(
            body.get("executionReuseAmount") or body.get("execution_reuse_amount") or ""
        ).strip().lower() in {"1", "true", "yes", "on"}
        if len(route_symbols) < 2:
            return (
                jsonify(
                    {
                        "ok": False,
                        "mode": "dex_quote_route_test",
                        "error": "route_symbols_required",
                        "detail": "routeSymbols or pair with a closed route is required",
                    }
                ),
                400,
            )
        amount = None
        amount_source = "recommended"
        requested_amount_raw = body.get("amount") if body.get("amount") is not None else body.get("inputAmount")
        if requested_amount_raw is not None and str(requested_amount_raw).strip() != "":
            amount_source = "requested"
            try:
                amount = float(str(requested_amount_raw).strip())
            except (TypeError, ValueError):
                amount = None
        else:
            try:
                amount = recommended_route_test_amount(
                    route_symbols=route_symbols,
                    chain_id=selected_chain_id,
                    protocols=protocols,
                )
            except Exception:
                amount = None
        if amount is None:
            return (
                jsonify(
                    {
                        "ok": False,
                        "mode": "dex_quote_route_test",
                        "error": "amount_invalid",
                        "detail": "amount must be a number or a recommended chain amount must be available",
                    }
                ),
                400,
            )
        if amount <= 0:
            return (
                jsonify(
                    {
                        "ok": False,
                        "mode": "dex_quote_route_test",
                        "error": "amount_invalid",
                        "detail": "amount must be greater than 0",
                    }
                ),
                400,
            )

        mode = str(body.get("mode") or "auto").strip().lower()
        broadcast = str(body.get("broadcast") or "").strip().lower() in {"1", "true", "yes", "on"}
        if mode == "estimate":
            resolved_addresses = _complete_route_addresses(
                route_symbols,
                route_addresses,
                chain_id=selected_chain_id,
                protocols=protocols,
            )
            if not resolved_addresses:
                return (
                    jsonify(
                        {
                            "ok": False,
                            "mode": "dex_quote_route_test",
                            "error": "route_not_found",
                            "detail": "route is not in the current closed-loop set",
                            "routeSymbols": route_symbols,
                            "amount": amount,
                        }
                    ),
                    400,
                )
            route_addresses = resolved_addresses
        try:
            if mode == "estimate":
                payload = estimate_route_live_quote(
                    chain_id=selected_chain_id,
                    protocols=protocols,
                    route_symbols=route_symbols,
                    amount=amount,
                )
            else:
                requires_official = broadcast or is_official_triangle_route(route_symbols)
                if requires_official and not _explicit_chain_id_is_robinhood_supported():
                    return (
                        jsonify(
                            {
                                "ok": False,
                                "mode": "dex_quote_route_test",
                                "error": "official_v4_arb_chain_required",
                                "detail": "official V4 triangle arbitrage is only supported on Robinhood chain 4663 or Robinhood Testnet 46630 when explicitly requested",
                                "routeSymbols": route_symbols,
                                "broadcast": broadcast,
                            }
                        ),
                        400,
                    )
                if broadcast:
                    payload = run_official_v4_arb_route_test(
                        route_symbols=route_symbols,
                        amount=amount,
                        broadcast=broadcast,
                        chain_id=selected_chain_id,
                        route_addresses=route_addresses,
                        amount_is_best=amount_is_best and execution_reuse_amount,
                    )
                elif is_official_triangle_route(route_symbols):
                    payload = run_official_v4_arb_route_test(
                        route_symbols=route_symbols,
                        amount=amount,
                        broadcast=False,
                        chain_id=selected_chain_id,
                        route_addresses=route_addresses,
                        amount_is_best=amount_is_best and execution_reuse_amount,
                    )
                else:
                    payload = estimate_route_live_quote(
                        chain_id=selected_chain_id,
                        protocols=protocols,
                        route_symbols=route_symbols,
                        amount=amount,
                    )
        except Exception as exc:
            payload = {
                "ok": False,
                "mode": "dex_quote_route_test",
                "error": "dex_route_test_failed",
                "detail": str(exc)[:500],
                "routeSymbols": route_symbols,
                "amount": amount,
                "broadcast": broadcast,
            }

        payload["chain_selection"] = chain_selection
        payload["broadcast"] = broadcast
        payload["broadcastAttempted"] = broadcast
        payload["broadcastPerformed"] = bool(
            broadcast
            and bool(
                payload.get("txHash")
                or payload.get("transactionHash")
                or payload.get("transaction_hash")
            )
        )
        payload["routeSymbols"] = route_symbols
        payload.setdefault("routeAddresses", route_addresses)
        payload["amount"] = amount
        payload["amountSource"] = amount_source
        if not payload.get("bestAmountCalculation"):
            try:
                routes_payload = _routes_payload(chain_id=selected_chain_id, protocols=protocols)
                route = _match_route(routes_payload.get("routes") or [], route_symbols)
                best_calculation = best_amount_calculation_from_route(route, amount)
                probe_records = best_calculation.get("probe_records") or best_calculation.get("probeRecords") or []
                payload["bestAmountCalculation"] = best_calculation
                payload["probeRecords"] = probe_records
                payload["probe_records"] = probe_records
                payload["analysis"] = {
                    **payload.get("analysis", {}),
                    "bestAmountCalculation": best_calculation,
                    "probeRecords": probe_records,
                    "probe_records": probe_records,
                }
            except Exception as exc:
                payload["bestAmountCalculationError"] = str(exc)[:300]
        # 交易方向与交易量估算是链上估算的前置功能：若用户指定金额导致链上验证亏损，则提示并回退到推荐量估算，避免展示大额亏损结论
        try:
            if amount_source == "requested" and is_official_triangle_route(route_symbols):
                net_profit = payload.get("netProfit")
                static_profit = payload.get("analysis", {}).get("referenceNetProfitAmount")
                # 对官方三角路线，若当前金额验证亏损而参考估算为正，标记为估算保护
                if isinstance(net_profit, (int, float)) and net_profit < 0:
                    if isinstance(static_profit, (int, float)) and static_profit > 0:
                        payload["estimationWarning"] = {
                            "code": "amount_too_large_leads_to_loss",
                            "message": "用户指定的交易量在当前池深度下导致链上验证亏损，已基于推荐量给出盈利参考方向。请使用推荐量或降低交易量。",
                            "requestedAmount": amount,
                            "recommendedAmount": payload.get("initialRecommendedAmount"),
                        }
                # 若链上验证通过但净收益为负，统一展示基于推荐量的方向选择，防止错误的大额亏损结论外露
                if is_official_triangle_route(route_symbols) and isinstance(net_profit, (int, float)) and net_profit < 0:
                    # 保留已计算的方向选择信息，但明确提示本次金额不适用
                    payload.setdefault("directionSelection", {}).setdefault("amountWarning", "当前金额导致链上亏损，建议使用推荐量")
        except Exception:
            _root.logger.warning("failed to decorate dex route quote payload", exc_info=True)
        # 补充初始推荐量与方向选择依据，便于用户核对
        try:
            payload["initialRecommendedAmount"] = recommended_route_test_amount(
                route_symbols=route_symbols,
                chain_id=selected_chain_id,
                protocols=protocols,
            )
        except Exception:
            payload["initialRecommendedAmount"] = None
        if is_official_triangle_route(route_symbols):
            selected_route_symbols = payload.get("selectedRouteSymbols") or payload.get("routeSymbols")
            route_index = payload.get("routeIndex")
            dir_basis = (
                "链上 static call 选择利润更大的方向：routeIndex=0 使用输入顺序，routeIndex=1 交换中间两跳"
                if route_index is not None
                else "官方 V4 三角套利脚本会对比原始方向与中间两跳互换方向，选择 static call 利润更大的方向"
            )
            payload["directionSelection"] = {
                "inputRoute": route_symbols,
                "selectedRoute": selected_route_symbols if isinstance(selected_route_symbols, list) else [],
                "routeIndex": route_index,
                "basis": dir_basis,
                "comparisonNote": "已在链上对比两种中间跳方向，保留利润更优的一条",
            }
            # 使用链上实际选择的路线展示，避免显示与跳数不一致的原始路径
            if isinstance(selected_route_symbols, list) and len(selected_route_symbols) >= 2:
                payload["routeSymbols"] = selected_route_symbols
        else:
            payload["directionSelection"] = None
        payload["record"] = _root.record_dex_route_test_attempt(
            payload,
            chain_id=selected_chain_id,
            route_symbols=route_symbols,
            amount=amount,
            amount_source=amount_source,
            mode=mode,
            broadcast=broadcast,
            fallback_networks=_root.submission_networks_from_request(chain_selection),
        )
        payload = _root._attach_dex_strategy(payload)
        return jsonify(payload), 200 if (payload.get("ok") or payload.get("broadcastDisabled")) else 400


def register_dex_estimate_fixed_pnl_routes(app) -> None:
    @app.post("/api/dex/estimate-fixed-pnl")
    def dex_estimate_fixed_pnl():
        from market.binance_market.dex_route_test import (
            estimate_fixed_amount_pnl,
            normalize_route_symbols,
        )

        body = request.get_json(silent=True) or {}
        chain_id = body.get("chain_id")
        if chain_id is not None:
            try:
                chain_id = int(chain_id)
            except (TypeError, ValueError):
                return (
                    jsonify(
                        {
                            "ok": False,
                            "mode": "dex_estimate_fixed_pnl",
                            "error": "chain_id_invalid",
                            "detail": "chain_id must be an integer",
                        }
                    ),
                    400,
                )
        else:
            try:
                _, chain_id = _root.selected_chain_id_from_request()
            except ChainSelectionError as exc:
                return _root.shared._chain_selection_error_response(exc)

        protocols = body.get("protocols") or _root.request_dex_protocols_arg()
        route_symbols = normalize_route_symbols(
            body.get("routeSymbols")
            or body.get("path")
            or body.get("pair")
            or body.get("symbolPair")
            or ""
        )
        if len(route_symbols) < 2:
            return (
                jsonify(
                    {
                        "ok": False,
                        "mode": "dex_estimate_fixed_pnl",
                        "error": "route_symbols_required",
                        "detail": "routeSymbols or pair with a closed route is required",
                    }
                ),
                400,
            )
        raw_amount = body.get("amount")
        if raw_amount is None or str(raw_amount).strip() == "":
            return (
                jsonify(
                    {
                        "ok": False,
                        "mode": "dex_estimate_fixed_pnl",
                        "error": "amount_required",
                        "detail": "amount is required",
                    }
                ),
                400,
            )
        try:
            amount = float(str(raw_amount).strip())
        except (TypeError, ValueError):
            return (
                jsonify(
                    {
                        "ok": False,
                        "mode": "dex_estimate_fixed_pnl",
                        "error": "amount_invalid",
                        "detail": "amount must be a number",
                    }
                ),
                400,
            )
        if amount <= 0:
            return (
                jsonify(
                    {
                        "ok": False,
                        "mode": "dex_estimate_fixed_pnl",
                        "error": "amount_invalid",
                        "detail": "amount must be greater than 0",
                    }
                ),
                400,
            )
        try:
            payload = estimate_fixed_amount_pnl(
                chain_id=chain_id,
                protocols=protocols,
                route_symbols=route_symbols,
                amount=amount,
            )
        except Exception as exc:
            payload = {
                "ok": False,
                "mode": "dex_estimate_fixed_pnl",
                "error": "estimate_fixed_pnl_failed",
                "detail": str(exc)[:500],
                "routeSymbols": route_symbols,
                "amount": amount,
            }
        payload = _root._attach_dex_strategy(payload)
        return jsonify(payload), 200 if payload.get("ok") else 400


def register_dex_submission_onchain_check_routes(app) -> None:
    @app.post("/api/dex/submissions/check-onchain")
    def dex_submission_onchain_check():
        from market.binance_market.dex_route_test import fetch_dex_tx_receipt

        try:
            chain_selection, selected_chain_id = _root.selected_chain_id_from_request()
        except ChainSelectionError as exc:
            return _root.shared._chain_selection_error_response(exc)
        body = request.get_json(silent=True) or {}
        tx_hash = str(body.get("tx_hash") or body.get("txHash") or "").strip()
        chain_id = body.get("chain_id")
        if chain_id is None:
            chain_id = selected_chain_id
        if not tx_hash:
            return jsonify({"ok": False, "error": "tx_hash_required", "detail": "tx_hash is required"}), 400
        try:
            result = fetch_dex_tx_receipt(chain_id=int(chain_id), tx_hash=tx_hash)
        except Exception as exc:
            return jsonify({"ok": False, "error": "tx_receipt_check_failed", "detail": str(exc)[:300]}), 502
        result["chain_selection"] = chain_selection
        return jsonify(result), 200 if result.get("ok") else 404


def register_dex_quote_request_routes(app) -> None:
    @app.get("/api/dex/quote-request")
    def dex_quote_request():
        try:
            chain_selection, selected_chain_id = _root.selected_chain_id_from_request()
        except ChainSelectionError as exc:
            return _root.shared._chain_selection_error_response(exc)
        protocols = _root.request_dex_protocols_arg()
        pair = request.args.get("pair", "").strip()
        venue = request.args.get("venue", "").strip()
        amount = request.args.get("amount", "").strip() or None
        payload = quote_request_manager_page_payload(
            chain_id=selected_chain_id,
            protocols=protocols,
            pair=pair,
            venue=venue,
            amount=amount,
            chain_selection=chain_selection,
            request_id=getattr(g, "request_id", None),
        )
        if not payload.get("ok"):
            if payload.get("error") == "candidate_pair_not_found":
                return (
                    jsonify(
                        {
                            "ok": False,
                            "mode": "dex_quote_request",
                            "dataSource": "manager",
                            "error": "manager_request_not_found",
                            "managerDetail": payload,
                        }
                    ),
                    404,
                )
            upstream_status = payload.get("upstreamStatus")
            response_status = upstream_status if isinstance(upstream_status, int) else 503
            return (
                jsonify(
                    {
                        "ok": False,
                        "mode": "dex_quote_request",
                        "dataSource": "manager",
                        "error": "manager_read_failed",
                        "managerDetail": payload,
                    }
                ),
                response_status,
            )
        return jsonify(_root._attach_dex_strategy(payload))
