from __future__ import annotations

from flask import Flask, g

from web.data_routes import dex_surface_routes_quotes, manager_page_compare


def _pool(
    pool_id: str,
    token0: str,
    token1: str,
    symbol0: str,
    symbol1: str,
    *,
    fee: int,
    tick_spacing: int,
) -> dict:
    return {
        "pool": pool_id,
        "poolId": pool_id,
        "protocol": "uniswap_v4",
        "dex": "uniswap_v4",
        "poolManager": "0xpoolmanager",
        "token0": token0,
        "token1": token1,
        "token0Symbol": symbol0,
        "token1Symbol": symbol1,
        "token0KeyId": f"eip155:4663:{token0}",
        "token1KeyId": f"eip155:4663:{token1}",
        "fee": fee,
        "feeLabel": str(fee),
        "tickSpacing": tick_spacing,
        "hooks": "0x0000000000000000000000000000000000000000",
        "routeSymbols": [symbol0, symbol1],
        "routeText": f"{symbol0} -> {symbol1}",
        "dynamicFee": False,
        "specialFee": False,
        "hasLiquidity": True,
        "liquidity": "1000000",
        "liquidityIndex": 0.5,
        "depthStatus": "liquid",
        "depthBand": {"token": "live"},
        "usableForObservation": True,
    }


POOLS = [
    _pool("pool-a", "0xanchor", "0xa", "USDG", "A", fee=500, tick_spacing=10),
    _pool("pool-b", "0xa", "0xb", "A", "B", fee=3000, tick_spacing=60),
    _pool("pool-c", "0xb", "0xanchor", "B", "USDG", fee=10000, tick_spacing=200),
]


def _path(anchor: str, pools: list[dict], *, complete: bool = True) -> dict:
    symbols = [anchor, "A", "B", anchor]
    return {
        "symbolPair": f"{anchor}:A",
        "routeSymbols": symbols,
        "routeAddresses": ["0xanchor", "0xa", "0xb", "0xanchor"],
        "overviewRoute": {
            "kind": "closed_loop",
            "pathText": " -> ".join(symbols),
            "anchorBorrowSymbol": anchor,
            "targetSymbol": "A",
            "candidatePairSymbols": ["USDG:A", "A:B", "B:USDG"],
        },
        "overviewProfit": {
            "method": "manager_review_signed_pnl",
            "notionalUsd": 1000.0,
            "complete": complete,
            "requiresLiveQuoter": not complete,
            "adapterRequired": not complete,
            "observedNetProfitUsd": 12.5 if complete else None,
            "referenceNetProfitUsd": 12.5 if complete else None,
            "referenceInputAmount": 1000.0,
            "referenceInputSymbol": anchor,
            "referenceNetProfitAmount": 12.5 if complete else None,
            "referenceReturnBasis": "borrow_token",
            "tokenBasisProfitable": complete,
            "tokenBasisReferenceProfitable": complete,
            "chainSubmissionCandidate": complete,
            "profitable": complete,
            "executableCandidate": complete,
            "borrowAssetIsStable": anchor == "USDG",
            "priceEvidenceSource": (
                "manager_review_signed_pnl"
                if complete
                else "manager_review_pending"
            ),
            "priceEvidenceMode": "live_verified" if complete else "review_pending",
            "priceEvidenceSourceLabel": "Manager review signed PnL",
        },
        "routeFrameworkSource": "manager_closed_loop",
        "pathSource": "manager_closed_loop",
        "marketObservationSourceStage": "manager_closed_loop",
        "priceEvidenceSource": (
            "manager_review_signed_pnl" if complete else "manager_review_pending"
        ),
        "priceEvidenceSourceLabel": "Manager review signed PnL",
        "routeUsableForObservation": True,
        "routeHasLiquidityEvidence": True,
        "executionPools": pools,
        "preferredPools": pools,
        "hopCandidates": [],
        "hopCandidateCombinationCount": 1,
        "flashLoanLinkage": {
            "borrowSymbols": [anchor],
            "routeSymbols": symbols,
            "anchorTokenKeyIds": [f"eip155:4663:0xanchor"],
        },
    }


USDG_PATH = _path("USDG", POOLS)
WETH_PATH = _path("WETH", POOLS, complete=False)


OVERVIEW_DATA = {
    "enabled": True,
    "source": "manager",
    "network": "robinhood",
    "chainId": 4663,
    "protocol": "uniswap_v4",
    "scopeKey": "core4",
    "selectedProtocols": ["uniswap_v4"],
    "generatedAt": "2026-11-05T00:00:00Z",
    "summary": {
        "arbitragePathCount": 2,
        "candidatePathCount": 2,
        "marketObservationRouteCount": 2,
        "overviewPathGroupCount": 2,
    },
    "arbitragePaths": [USDG_PATH, WETH_PATH],
    "arbitragePathGroups": [
        {
            "flashLoanToken": {
                "tokenKeyId": "eip155:4663:0xanchor",
                "address": "0xanchor",
                "symbol": "USDG",
            },
            "startToken": {
                "tokenKeyId": "eip155:4663:0xanchor",
                "address": "0xanchor",
                "symbol": "USDG",
            },
            "endToken": {
                "tokenKeyId": "eip155:4663:0xanchor",
                "address": "0xanchor",
                "symbol": "USDG",
            },
            "paths": [USDG_PATH],
            "poolDependencies": POOLS,
        },
        {
            "flashLoanToken": {
                "tokenKeyId": "eip155:4663:0xweth",
                "address": "0xweth",
                "symbol": "WETH",
            },
            "startToken": {
                "tokenKeyId": "eip155:4663:0xweth",
                "address": "0xweth",
                "symbol": "WETH",
            },
            "endToken": {
                "tokenKeyId": "eip155:4663:0xweth",
                "address": "0xweth",
                "symbol": "WETH",
            },
            "paths": [WETH_PATH],
            "poolDependencies": POOLS,
        },
    ],
    "watchPairs": [],
    "liquidPoolRegistry": POOLS,
    "routeFrameworkSource": "manager_closed_loop",
    "marketObservationSource": "manager_closed_loop",
    "marketObservationSourceStage": "manager_closed_loop",
    "decisionBasis": "manager_closed_loop",
    "routeFramework": {
        "routeFrameworkSource": "manager_closed_loop",
        "marketObservationSourceStage": "manager_closed_loop",
        "totalLoops": 2,
    },
    "flashLoanLiquidity": {
        "chainId": 4663,
        "source": "manager",
        "availableProtocols": ["uniswap_v4"],
        "available": True,
    },
    "poolStaticTable": {
        "chainId": 4663,
        "network": "robinhood",
        "source": "manager",
        "exists": True,
        "generatedAt": "2026-11-05T00:00:00Z",
    },
    "versions": {
        "candidateEdgeVersion": 7,
        "observationVersion": 9,
        "closedLoopVersion": 11,
        "reviewVersion": 13,
        "broadcastVersion": 14,
        "versionsConsistent": True,
        "consistencyIssues": [],
    },
    "dataQuality": {
        "pnlSource": "review_manager_signed_pnl",
        "pnlMissingCount": 0,
        "usdDepthRequired": False,
        "crossVersionMixingDetected": False,
        "notes": [],
    },
    "pipelineStatus": {
        "latestCompletedStage": "broadcast",
        "checkpointVersion": 14,
    },
}


def _runtime_core_overview_envelope(data: dict | None = None) -> dict:
    return {
        "ok": True,
        "version": "rust_runtime_core_v1",
        "dataSource": "rust_runtime_core",
        "runtimeCore": {
            "chainId": 4663,
            "instanceId": "runtime-core-1",
            "generation": 7,
        },
        "data": dict(data or OVERVIEW_DATA),
    }


def test_quote_routes_manager_adapter_maps_paths_pools_groups_and_versions(
    monkeypatch,
):
    payload = manager_page_compare.quote_routes_manager_page_payload(
        chain_id=4663,
        protocols=["uniswap_v4"],
        chain_selection={"market_id": "dex-arbitrage-4663-uniswap-v4"},
        request_id="quote-routes-1",
        overview_envelope=_runtime_core_overview_envelope(),
    )

    assert payload["ok"] is True
    assert payload["dataSource"] == "manager"
    assert payload["runtimeDataSource"] == "rust_runtime_core"
    assert payload["runtimeCore"]["instanceId"] == "runtime-core-1"
    assert payload["mode"] == "dex_quote_routes"
    assert payload["processing"] == {
        "flow_name": "dex_quote_routes",
        "network": "robinhood",
        "handler": "runtime_core:overview",
    }
    assert payload["selected_protocols"] == ["uniswap_v4"]
    assert payload["summary"]["routeCount"] == 2
    assert payload["summary"]["managerReviewCompleteCount"] == 1
    assert payload["versions"]["closedLoopVersion"] == 11
    assert payload["versions"]["reviewVersion"] == 13
    assert payload["dataQuality"]["usdDepthRequired"] is False

    route = payload["routes"][0]
    assert route["kind"] == "closed_loop"
    assert route["source"] == "manager_closed_loop"
    assert route["routeSymbols"] == ["USDG", "A", "B", "USDG"]
    assert route["poolIds"] == ["pool-a", "pool-b", "pool-c"]
    assert route["routePools"][0]["poolKey"] == {
        "currency0": "0xanchor",
        "currency1": "0xa",
        "fee": 500,
        "tickSpacing": 10,
        "hooks": "0x0000000000000000000000000000000000000000",
    }
    assert route["quoteAnalysis"]["complete"] is True
    assert route["quoteAnalysis"]["observedNetProfitUsd"] == 12.5
    assert route["quoteAnalysis"]["referenceInputSymbol"] == "USDG"
    assert route["quoteAnalysis"]["stepCount"] == 3
    assert route["chainReviewEligible"] is True

    group = payload["closedLoopGroups"][0]
    assert group["flashLoanToken"]["symbol"] == "USDG"
    assert group["routeCount"] == 1
    assert group["poolDependencyCount"] == 3
    assert group["borrowable"] is True
    assert group["borrowableBasis"] == "manager_closed_loop_anchor"
    assert group["paths"][0]["routeKey"] == route["routeKey"]


def test_quote_routes_manager_adapter_uses_request_bound_runtime_core(monkeypatch):
    calls = []

    class FakeRuntime:
        def manager_view(self, view: str, limit: int) -> dict:
            calls.append((view, limit))
            return _runtime_core_overview_envelope()

    monkeypatch.setattr(
        manager_page_compare.ManagerApiClient,
        "from_env",
        lambda: (_ for _ in ()).throw(
            AssertionError("quote routes must not use ManagerApiClient HTTP reads")
        ),
    )
    app = Flask(__name__)
    with app.app_context():
        g.chain_runtime = FakeRuntime()
        payload = manager_page_compare.quote_routes_manager_page_payload(
            chain_id=4663,
            protocols=["uniswap_v4"],
        )

    assert calls == [("overview", 1000)]
    assert payload["ok"] is True
    assert payload["runtimeDataSource"] == "rust_runtime_core"
    assert payload["processing"]["handler"] == "runtime_core:overview"


def test_quote_routes_manager_adapter_anchor_filter_keeps_versions(monkeypatch):
    payload = manager_page_compare.quote_routes_manager_page_payload(
        chain_id=4663,
        protocols=["uniswap_v4"],
        anchor="WETH",
        overview_envelope=_runtime_core_overview_envelope(),
    )

    assert [route["overviewRoute"]["anchorBorrowSymbol"] for route in payload["routes"]] == [
        "WETH"
    ]
    assert payload["summary"]["routeCount"] == 1
    assert payload["summary"]["unfilteredRouteCount"] == 2
    assert payload["summary"]["anchorFilter"] == "WETH"
    assert len(payload["closedLoopGroups"]) == 1
    assert payload["closedLoopGroups"][0]["flashLoanToken"]["symbol"] == "WETH"
    assert payload["routes"][0]["quoteAnalysis"]["complete"] is False
    assert payload["versions"]["closedLoopVersion"] == 11


def test_quote_routes_manager_adapter_fails_closed_on_manager_error(monkeypatch):
    monkeypatch.setattr(
        manager_page_compare,
        "runtime_core_manager_overview_envelope",
        lambda **kwargs: {
            "ok": False,
            "source": "manager",
            "dataSource": "manager",
            "code": "RUNTIME_CORE_VIEW_READ_FAILED",
            "detail": "manager unavailable",
            "upstreamStatus": 503,
        },
    )

    payload = manager_page_compare.quote_routes_manager_page_payload(
        chain_id=4663,
        protocols=["uniswap_v4"],
    )

    assert payload == {
        "ok": False,
        "source": "manager",
        "dataSource": "manager",
        "code": "RUNTIME_CORE_VIEW_READ_FAILED",
        "detail": "manager unavailable",
        "upstreamStatus": 503,
    }


def test_quote_routes_manager_adapter_rejects_malformed_overview(monkeypatch):
    data = dict(OVERVIEW_DATA)
    data["arbitragePaths"] = ["not-an-object"]

    payload = manager_page_compare.quote_routes_manager_page_payload(
        chain_id=4663,
        protocols=["uniswap_v4"],
        overview_envelope=_runtime_core_overview_envelope(data),
    )

    assert payload["ok"] is False
    assert payload["code"] == "MANAGER_API_INVALID_RESPONSE"
    assert "arbitragePaths" in payload["detail"]


def test_quote_routes_route_is_manager_only_and_never_loads_legacy_data(
    monkeypatch,
):
    app = Flask(__name__)
    dex_surface_routes_quotes.register_dex_quote_routes(app)
    monkeypatch.setattr(
        dex_surface_routes_quotes._root,
        "selected_chain_id_from_request",
        lambda: (
            {"market_id": "dex-arbitrage-4663-uniswap-v4"},
            4663,
        ),
    )
    monkeypatch.setattr(
        dex_surface_routes_quotes._root,
        "request_dex_protocols_arg",
        lambda: ["uniswap_v4"],
    )
    monkeypatch.setattr(
        dex_surface_routes_quotes._root,
        "_attach_dex_strategy",
        lambda payload: {**payload, "dex_strategy": "closed_loop_routes"},
    )
    for symbol in (
        "_bootstrap_dex_surface_dependencies",
        "_dex_surface_payload_for_chain",
        "dex_quote_routes_payload",
        "dex_opportunity_snapshot",
        "run_dex_opportunity_scan_now",
        "load_observation_overview_for_scope",
    ):
        monkeypatch.setattr(
            dex_surface_routes_quotes._root,
            symbol,
            lambda *args, _symbol=symbol, **kwargs: (_ for _ in ()).throw(
                AssertionError(f"legacy path {_symbol} must not run")
            ),
        )
    calls = []
    monkeypatch.setattr(
        dex_surface_routes_quotes,
        "quote_routes_manager_page_payload",
        lambda **kwargs: calls.append(dict(kwargs))
        or {
            "ok": True,
            "source": "manager",
            "dataSource": "manager",
            "mode": "dex_quote_routes",
            "chain_id": kwargs["chain_id"],
            "selected_protocols": kwargs["protocols"],
            "processing": {
                "flow_name": "dex_quote_routes",
                "network": "robinhood",
                "handler": "runtime_core:overview",
            },
            "summary": {"routeCount": 0},
            "routes": [],
            "closedLoopGroups": [],
        },
    )

    client = app.test_client()
    response = client.get(
        "/api/dex/quote-routes?chain_id=4663&protocols=uniswap_v4&anchor=USDG"
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["dataSource"] == "manager"
    assert payload["dex_strategy"] == "closed_loop_routes"
    assert "managerShadow" not in payload
    assert calls == [
        {
            "chain_id": 4663,
            "protocols": ["uniswap_v4"],
            "anchor": "USDG",
            "chain_selection": {
                "market_id": "dex-arbitrage-4663-uniswap-v4"
            },
            "request_id": None,
        }
    ]

    monkeypatch.setattr(
        dex_surface_routes_quotes,
        "quote_routes_manager_page_payload",
        lambda **kwargs: {
            "ok": False,
            "source": "manager",
            "dataSource": "manager",
            "code": "MANAGER_API_UNAVAILABLE",
            "detail": "manager unavailable",
            "upstreamStatus": 503,
        },
    )
    failed = client.get("/api/dex/quote-routes?chain_id=4663")
    assert failed.status_code == 503
    assert failed.get_json() == {
        "ok": False,
        "mode": "dex_quote_routes",
        "dataSource": "manager",
        "error": "manager_read_failed",
        "managerDetail": {
            "ok": False,
            "source": "manager",
            "dataSource": "manager",
            "code": "MANAGER_API_UNAVAILABLE",
            "detail": "manager unavailable",
            "upstreamStatus": 503,
        },
    }


def test_quote_request_manager_adapter_selects_path_and_uses_manager_pnl(monkeypatch):
    payload = manager_page_compare.quote_request_manager_page_payload(
        chain_id=4663,
        protocols=["uniswap_v4"],
        pair="USDG -> A -> B -> USDG",
        venue="uniswap_v4",
        amount="1000",
        chain_selection={"market_id": "dex-arbitrage-4663-uniswap-v4"},
        request_id="quote-request-1",
        overview_envelope=_runtime_core_overview_envelope(),
    )

    assert payload["ok"] is True
    assert payload["source"] == "manager"
    assert payload["dataSource"] == "manager"
    assert payload["runtimeDataSource"] == "rust_runtime_core"
    assert payload["mode"] == "dex_quote_request"
    assert payload["processing"] == {
        "flow_name": "dex_quote_request",
        "network": "robinhood",
        "handler": "runtime_core:overview",
    }
    assert payload["pair"] == "USDG:A"
    assert payload["requestedPair"] == "USDG -> A -> B -> USDG"
    assert payload["routeSymbols"] == ["USDG", "A", "B", "USDG"]
    assert payload["routeAddresses"] == ["0xanchor", "0xa", "0xb", "0xanchor"]
    assert payload["poolIds"] == ["pool-a", "pool-b", "pool-c"]
    assert payload["quoteStatus"] == "manager_review_verified"
    assert payload["quoteVerification"]["status"] == "live_verified"
    assert payload["quoteVerification"]["trustedForExecution"] is True
    assert payload["quoteAnalysis"]["observedNetProfitUsd"] == 12.5
    assert payload["quoteAnalysis"]["referenceInputSymbol"] == "USDG"
    assert len(payload["quoteRequests"]) == 1
    request = payload["quoteRequests"][0]
    assert request["venue"] == "uniswap_v4"
    assert request["quoteMode"] == "manager_overview_projection"
    assert request["status"] == "manager_review_verified"
    assert request["managerOverview"] is True
    assert request["routeSymbols"] == payload["routeSymbols"]
    assert request["pools"] == payload["poolIds"]
    assert request["chainId"] == 4663
    assert [pool["poolId"] for pool in payload["candidatePools"]] == [
        "pool-a",
        "pool-b",
        "pool-c",
    ]
    assert [pool["poolId"] for pool in payload["routePools"]] == [
        "pool-a",
        "pool-b",
        "pool-c",
    ]
    assert payload["versions"]["closedLoopVersion"] == 11
    assert payload["dataQuality"]["usdDepthRequired"] is False
    assert payload["chain_selection"] == {
        "market_id": "dex-arbitrage-4663-uniswap-v4"
    }


def test_quote_request_manager_adapter_returns_structured_not_found(monkeypatch):
    payload = manager_page_compare.quote_request_manager_page_payload(
        chain_id=4663,
        protocols=["uniswap_v4"],
        pair="NOPE -> WETH -> NOPE",
        venue="uniswap_v4",
        amount=None,
        overview_envelope=_runtime_core_overview_envelope(),
    )

    assert payload["ok"] is False
    assert payload["dataSource"] == "manager"
    assert payload["mode"] == "dex_quote_request"
    assert payload["error"] == "candidate_pair_not_found"
    assert payload["availablePairs"] == ["USDG:A", "WETH:A"]
    assert payload["quoteRequests"] == []
    assert payload["errorStack"]["status"] == "candidate_pair_not_found"
    assert payload["errorStack"]["manager"] is True
    assert payload["versions"]["reviewVersion"] == 13


def test_quote_request_manager_adapter_fails_closed_on_manager_error(monkeypatch):
    monkeypatch.setattr(
        manager_page_compare,
        "runtime_core_manager_overview_envelope",
        lambda **kwargs: {
            "ok": False,
            "source": "manager",
            "dataSource": "manager",
            "code": "RUNTIME_CORE_VIEW_READ_FAILED",
            "detail": "manager unavailable",
            "upstreamStatus": 503,
        },
    )

    payload = manager_page_compare.quote_request_manager_page_payload(
        chain_id=4663,
        protocols=["uniswap_v4"],
        pair="USDG -> A -> B -> USDG",
        venue="uniswap_v4",
        amount="1000",
    )

    assert payload == {
        "ok": False,
        "source": "manager",
        "dataSource": "manager",
        "code": "RUNTIME_CORE_VIEW_READ_FAILED",
        "detail": "manager unavailable",
        "upstreamStatus": 503,
    }


def test_quote_request_route_is_manager_only_and_never_loads_legacy_data(
    monkeypatch,
):
    app = Flask(__name__)
    dex_surface_routes_quotes.register_dex_quote_request_routes(app)
    monkeypatch.setattr(
        dex_surface_routes_quotes._root,
        "selected_chain_id_from_request",
        lambda: (
            {"market_id": "dex-arbitrage-4663-uniswap-v4"},
            4663,
        ),
    )
    monkeypatch.setattr(
        dex_surface_routes_quotes._root,
        "request_dex_protocols_arg",
        lambda: ["uniswap_v4"],
    )
    monkeypatch.setattr(
        dex_surface_routes_quotes._root,
        "_attach_dex_strategy",
        lambda payload: {**payload, "dex_strategy": "manager_overview_request"},
    )
    for symbol in (
        "_bootstrap_dex_surface_dependencies",
        "_dex_surface_payload_for_chain",
        "dex_quote_request_payload",
        "dex_quote_routes_payload",
        "load_observation_overview_for_scope",
    ):
        monkeypatch.setattr(
            dex_surface_routes_quotes._root,
            symbol,
            lambda *args, _symbol=symbol, **kwargs: (_ for _ in ()).throw(
                AssertionError(f"legacy path {_symbol} must not run")
            ),
        )
    calls = []
    monkeypatch.setattr(
        dex_surface_routes_quotes,
        "quote_request_manager_page_payload",
        lambda **kwargs: calls.append(dict(kwargs))
        or {
            "ok": True,
            "source": "manager",
            "dataSource": "manager",
            "mode": "dex_quote_request",
            "chain_id": kwargs["chain_id"],
            "network": "robinhood",
            "selected_protocols": kwargs["protocols"],
            "processing": {
                "flow_name": "dex_quote_request",
                "network": "robinhood",
                "handler": "runtime_core:overview",
            },
            "pair": "USDG -> A -> B -> USDG",
            "venue": kwargs["venue"],
            "amount": kwargs["amount"],
            "quoteRequests": [{"venue": kwargs["venue"]}],
            "chain_selection": kwargs["chain_selection"],
        },
    )

    client = app.test_client()
    response = client.get(
        "/api/dex/quote-request?chain_id=4663&protocols=uniswap_v4"
        "&pair=USDG%20-%3E%20A%20-%3E%20B%20-%3E%20USDG"
        "&venue=uniswap_v4&amount=1000"
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["dataSource"] == "manager"
    assert payload["dex_strategy"] == "manager_overview_request"
    assert calls == [
        {
            "chain_id": 4663,
            "protocols": ["uniswap_v4"],
            "pair": "USDG -> A -> B -> USDG",
            "venue": "uniswap_v4",
            "amount": "1000",
            "chain_selection": {
                "market_id": "dex-arbitrage-4663-uniswap-v4"
            },
            "request_id": None,
        }
    ]

    monkeypatch.setattr(
        dex_surface_routes_quotes,
        "quote_request_manager_page_payload",
        lambda **kwargs: {
            "ok": False,
            "source": "manager",
            "dataSource": "manager",
            "mode": "dex_quote_request",
            "error": "candidate_pair_not_found",
            "detail": "manager overview does not contain the requested candidate pair",
        },
    )
    not_found = client.get("/api/dex/quote-request?chain_id=4663&pair=NOPE")
    assert not_found.status_code == 404
    assert not_found.get_json() == {
        "ok": False,
        "mode": "dex_quote_request",
        "dataSource": "manager",
        "error": "manager_request_not_found",
        "managerDetail": {
            "ok": False,
            "source": "manager",
            "dataSource": "manager",
            "mode": "dex_quote_request",
            "error": "candidate_pair_not_found",
            "detail": "manager overview does not contain the requested candidate pair",
        },
    }

    monkeypatch.setattr(
        dex_surface_routes_quotes,
        "quote_request_manager_page_payload",
        lambda **kwargs: {
            "ok": False,
            "source": "manager",
            "dataSource": "manager",
            "code": "MANAGER_API_UNAVAILABLE",
            "detail": "manager unavailable",
            "upstreamStatus": 503,
        },
    )
    failed = client.get("/api/dex/quote-request?chain_id=4663")
    assert failed.status_code == 503
    assert failed.get_json()["error"] == "manager_read_failed"
    assert failed.get_json()["dataSource"] == "manager"
