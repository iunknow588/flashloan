from __future__ import annotations

import os
from copy import deepcopy
from typing import Any

from flask import jsonify, request

from core.chain_selection import ChainSelectionError
from web.data_routes import dex_surfaces as _root
from web.data_routes.dex_surface_helpers import (
    _has_live_verified_evidence,
    _has_preserved_manifest_evidence,
    _infer_route_addresses_from_pools,
    _merge_manifest_live_evidence_into_routes,
)


_STATIC_ROUTE_PAYLOAD_CACHE: dict[tuple[str, tuple[str, ...], str, float], dict[str, Any]] = {}


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
    registry = overview.get("poolRegistry") or {}
    if isinstance(registry, dict):
        for pool in registry.values():
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
    @app.get("/api/dex/quote-routes/static")
    def dex_quote_routes_static():
        try:
            chain_selection, selected_chain_id = _root.selected_chain_id_from_request()
        except ChainSelectionError as exc:
            return _root.shared._chain_selection_error_response(exc)
        protocols = _root.request_dex_protocols_arg()
        overview = _root.load_market_observation_overview_static(
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
        )
        cached = _STATIC_ROUTE_PAYLOAD_CACHE.get(cache_key)
        if cached is not None:
            return jsonify(deepcopy(cached))
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
                _compact_static_route_pool(pool)
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
                    "overviewRoute": {
                        "anchorBorrowSymbol": str(summary.get("anchorSymbol") or path_symbols[0]),
                        "targetSymbol": str(summary.get("counterSymbol") or path_symbols[-2]),
                        "pathSymbols": path_symbols,
                        "pathText": path_text,
                        "kind": "bridge_pair",
                        "routeSemantics": "x_y_entry_y_z_bridge_z_x_exit",
                        "marketObservationRoute": True,
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
                "pathSource": "market_observation",
            },
            "routes": routes,
            "closedLoopGroups": _compact_static_groups(overview),
            "chain_selection": chain_selection,
        }
        payload = _root._attach_dex_strategy(payload)
        _STATIC_ROUTE_PAYLOAD_CACHE[cache_key] = payload
        return jsonify(deepcopy(payload))

    @app.get("/api/dex/quote-routes")
    def dex_quote_routes():
        try:
            chain_selection, selected_chain_id = _root.selected_chain_id_from_request()
        except ChainSelectionError as exc:
            return _root.shared._chain_selection_error_response(exc)
        protocols = _root.request_dex_protocols_arg()
        use_live_estimate_fallback = str(request.args.get("live_estimate") or "0").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        anchor = str(request.args.get("anchor") or "").strip() or None
        _root._bootstrap_dex_surface_dependencies(
            chain_id=selected_chain_id,
            protocols=protocols,
            flow_name="dex_quote_routes",
        )
        payload = _root._dex_surface_payload_for_chain(
            flow_name="dex_quote_routes",
            mode="dex_quote_routes",
            chain_id=selected_chain_id,
            protocols=protocols,
            anchor=anchor,
            fallback=lambda: _root.dex_quote_routes_payload(
                chain_id=selected_chain_id,
                protocols=protocols,
                use_static_observation=True,
                anchor=anchor,
            ),
        )
        snapshot_container = payload.get("snapshot") if isinstance(payload.get("snapshot"), dict) else {}
        snapshot_data = snapshot_container.get("payload") if isinstance(snapshot_container.get("payload"), dict) else {}
        snapshot_routes = [route for route in (snapshot_data.get("routes") or []) if isinstance(route, dict)]
        top_routes = [route for route in (payload.get("routes") or []) if isinstance(route, dict)]
        base_routes = top_routes or snapshot_routes
        base_routes = [_infer_route_addresses_from_pools(route) for route in base_routes]
        _merge_manifest_live_evidence_into_routes(base_routes, chain_id=int(selected_chain_id))
        try:
            scanner_payload = _root.dex_opportunity_snapshot(chain_id=selected_chain_id, protocols=protocols)
            scanner_snapshot = scanner_payload.get("snapshot") if isinstance(scanner_payload.get("snapshot"), dict) else {}
            snapshot_updated_at = str(scanner_snapshot.get("updated_at") or "")
            if _root._scanner_snapshot_is_stale(scanner_payload):
                wait_for_scan = str(request.args.get("wait") or "0").strip().lower() in {"1", "true", "yes", "on"}
                if wait_for_scan:
                    try:
                        scanner = _root.get_dex_opportunity_scanner()
                        scanner.scan_now(chain_id=selected_chain_id, protocols=protocols)
                        scanner_payload = _root.dex_opportunity_snapshot(chain_id=selected_chain_id, protocols=protocols)
                        scanner_snapshot = scanner_payload.get("snapshot") if isinstance(scanner_payload.get("snapshot"), dict) else {}
                        snapshot_updated_at = str(scanner_snapshot.get("updated_at") or "")
                    except Exception as exc:
                        _root.logger.warning("synchronous scan-now refresh failed for dex quote routes: %s", exc)
                        scanner_payload = {}
                        scanner_snapshot = {}
                else:
                    try:
                        if not any(_has_preserved_manifest_evidence(route) for route in base_routes):
                            _root.run_dex_opportunity_scan_now(chain_id=selected_chain_id, protocols=protocols)
                    except Exception as exc:
                        _root.logger.warning("scan-now refresh failed for dex quote routes: %s", exc)
                if isinstance(scanner_payload, dict) and not _root._scanner_snapshot_is_stale(scanner_payload):
                    scanner_container = scanner_snapshot
                    scanner_data = scanner_container.get("payload") if isinstance(scanner_container.get("payload"), dict) else {}
                    payload["scannerState"] = {
                        "status": "fresh",
                        "stale": False,
                        "snapshotUpdatedAt": snapshot_updated_at,
                        "message": "scanner snapshot refreshed synchronously; on-chain evidence merged.",
                    }
                    scanner_routes = [route for route in (scanner_data.get("routes") or []) if isinstance(route, dict)]
                    scanner_by_key = {
                        _root._route_dedup_key(route): route
                        for route in scanner_routes
                    }
                    top_routes = scanner_routes
                    snapshot_routes = scanner_routes
                    snapshot_data["routes"] = scanner_routes
                    payload["routes"] = scanner_routes
                else:
                    payload["scannerState"] = {
                        "status": "stale_refresh_queued",
                        "stale": True,
                        "snapshotUpdatedAt": snapshot_updated_at,
                        "message": "scanner snapshot was stale; queued real-time scan; old scanner evidence was not merged",
                    }
                    scanner_by_key = {}
                    _root.evict_stale_evidence_from_routes(
                        [route for route in base_routes if not _has_preserved_manifest_evidence(route)]
                    )
            else:
                scanner_container = scanner_snapshot
                scanner_data = scanner_container.get("payload") if isinstance(scanner_container.get("payload"), dict) else {}
                payload["scannerState"] = {
                    "status": "fresh",
                    "stale": False,
                    "snapshotUpdatedAt": snapshot_updated_at,
                    "message": "scanner snapshot is fresh; on-chain evidence merged.",
                }
                scanner_routes = [route for route in (scanner_data.get("routes") or []) if isinstance(route, dict)]
                scanner_by_key = {
                    _root._route_dedup_key(route): route
                    for route in scanner_routes
                }
            for route in base_routes:
                key = _root._route_dedup_key(route)
                scanner_route = scanner_by_key.get(key)
                if not isinstance(scanner_route, dict):
                    continue
                evidence = scanner_route.get("live_onchain_evidence")
                if not isinstance(evidence, dict):
                    quote_analysis = scanner_route.get("quoteAnalysis")
                    if isinstance(quote_analysis, dict):
                        evidence = quote_analysis.get("live_onchain_evidence")
                if isinstance(evidence, dict) and evidence:
                    _root._merge_onchain_evidence_into_route(route, evidence)
        except (KeyError, TypeError, ValueError, AttributeError, OSError) as exc:
            _root.logger.warning(
                "quote-routes scanner merge failed for chain_id=%s protocols=%s: %s",
                selected_chain_id,
                protocols,
                exc,
            )
        scanner_state = payload.get("scannerState") if isinstance(payload.get("scannerState"), dict) else {}
        if not use_live_estimate_fallback:
            _root._merge_recent_attempt_evidence_into_routes(
                base_routes,
                chain_id=selected_chain_id,
            )
        if use_live_estimate_fallback and (snapshot_routes or top_routes):
            _root.evict_stale_evidence_from_routes(
                [route for route in base_routes if not _has_preserved_manifest_evidence(route)]
            )
            scanner_routes = base_routes
            merged_by_key = {
                _root._route_dedup_key(route): route
                for route in scanner_routes
                if isinstance(route, dict)
            }
            try:
                from market.binance_market.dex_route_test import is_official_triangle_route
            except Exception:
                is_official_triangle_route = None

            def _priority(route):
                symbols = [str(s) for s in (route.get("routeSymbols") or []) if str(s).strip()]
                if is_official_triangle_route and is_official_triangle_route(symbols):
                    return 0
                return 1
            ordered_routes = sorted(base_routes, key=_priority)
            live_count = 0
            for route in ordered_routes:
                key = _root._route_dedup_key(route)
                if not key or live_count >= 1:
                    continue
                evidence = route.get("live_onchain_evidence") or {}
                if isinstance(evidence, dict):
                    status = str(evidence.get("status") or "").strip().lower()
                    if status not in {"live_evidence_deferred", "requires_live_quoter", ""}:
                        continue
                analysis = route.get("quoteAnalysis") or {}
                if isinstance(analysis, dict) and analysis.get("livePriceVerified"):
                    continue
                improved = _root._live_quote_deferred_route(
                    route,
                    chain_id=selected_chain_id,
                    protocols=protocols,
                )
                if improved is not None:
                    merged_by_key[key] = improved
                    live_count += 1
            if top_routes:
                payload["routes"] = list(merged_by_key.values())
                summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
                summary["routeCount"] = len(payload["routes"])
                payload["summary"] = summary
            else:
                snapshot_data["routes"] = list(merged_by_key.values())
                summary = snapshot_data.get("summary") if isinstance(snapshot_data.get("summary"), dict) else {}
                summary["routeCount"] = len(snapshot_data["routes"])
                snapshot_data["summary"] = summary
        payload["chain_selection"] = chain_selection
        payload.setdefault("routes", [])
        payload.setdefault("mode", "dex_quote_routes")
        if isinstance(payload.get("routes"), list):
            overview = _root.load_market_observation_overview_static(
                chain_id=selected_chain_id,
                protocols=protocols,
            )
            filtered_routes = [
                _infer_route_addresses_from_pools(route)
                for route in payload["routes"]
                if isinstance(route, dict)
                and not _route_has_unresolved_zero_address(route, protocols)
                and _route_has_complete_v4_pool_identity(route)
                and _route_uses_active_v4_pools(route.get("routePools") or [], overview)
            ]
            payload["routes"] = filtered_routes
            summary = payload.get("summary")
            if isinstance(summary, dict) and "routeCount" in summary:
                summary["routeCount"] = len(filtered_routes)
        try:
            overview = _root.load_market_observation_overview_static(
                chain_id=selected_chain_id,
                protocols=protocols,
            )
            if isinstance(overview, dict) and overview.get("exists"):
                groups_payload = _root.closed_loop_route_groups_payload(
                    overview,
                    include_routes=False,
                )
                payload["closedLoopGroups"] = groups_payload.get("groups") or []
            else:
                payload["closedLoopGroups"] = []
        except Exception as exc:
            # The quote-routes surface should remain useful even if the
            # grouped overview payload cannot be built for a chain.
            _root.logger.warning(
                "closed-loop groups overview unavailable for chain_id=%s protocols=%s: %s",
                selected_chain_id,
                protocols,
                exc,
            )
            payload["closedLoopGroups"] = []
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
        _root._bootstrap_dex_surface_dependencies(
            chain_id=selected_chain_id,
            protocols=protocols,
            flow_name="dex_quote_request",
        )
        payload = _root._dex_surface_payload_for_chain(
            flow_name="dex_quote_request",
            mode="dex_quote_request",
            chain_id=selected_chain_id,
            protocols=protocols,
            fallback=lambda: _root.dex_quote_request_payload(
                chain_id=selected_chain_id,
                protocols=protocols,
                pair=pair,
                venue=venue,
                amount=amount,
                use_static_observation=True,
            ),
            pair=pair,
            venue=venue,
            amount=amount,
        )
        payload["chain_selection"] = chain_selection
        return jsonify(_root._attach_dex_strategy(payload)), 200 if payload.get("ok") else 404
