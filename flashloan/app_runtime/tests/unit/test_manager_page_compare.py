from __future__ import annotations

from flask import Flask, g

from core.manager_api_client import ManagerApiClientError
from web.data_routes import (
    dex_surface_routes_config,
    dex_surface_routes_ops,
    manager_page_compare,
)


LEGACY_PAYLOAD = {
    "summary": {
        "staticPairCount": "12",
        "staticPoolCount": "30",
        "abnormalStaticPoolCount": "2",
        "tokenCount": 44,
        "anchorGroupCount": "5",
    }
}

OVERVIEW_MANAGER_DATA = {
    "enabled": True,
    "source": "manager",
    "network": "robinhood",
    "chainId": 4663,
    "protocol": "uniswap_v4",
    "scopeKey": "core4",
    "selectedProtocols": ["uniswap_v4"],
    "generatedAt": "2026-11-05T00:00:00Z",
    "summary": {"arbitragePathCount": 3},
    "arbitragePaths": [],
    "arbitragePathGroups": [],
    "watchPairs": [],
    "liquidPoolRegistry": [],
    "routeFramework": {},
    "flashLoanLiquidity": {},
    "poolStaticTable": {},
    "versions": {},
    "dataQuality": {},
    "pipelineStatus": {},
    "decisionBasis": "manager_closed_loop",
    "marketObservationSource": "manager_closed_loop",
    "marketObservationSourceStage": "manager_closed_loop",
}

CANDIDATE_MANAGER_DATA = {
    "status": {
        "enabled": True,
        "network": "robinhood",
        "chainId": 4663,
        "protocol": "uniswap_v4",
        "scopeKey": "core4",
        "candidateVersion": 7,
        "materializedChecksum": "sha256:candidate",
        "poolScopeChecksum": "sha256:scope",
        "generatedAt": "2026-11-05T00:00:00Z",
        "anchorTokenCount": 1,
        "candidateTokenCount": 1,
        "eligiblePoolCount": 2,
        "recommendationCount": 2,
        "abnormalPoolCount": 0,
        "libraryVersions": {"core": 2},
        "libraryChecksums": {"core": "sha256:core"},
    },
    "tokens": [
        {
            "tokenKeyId": "eip155:4663:0xanchor",
            "tokenAddress": "0xanchor",
            "tokenSymbol": "USDG",
            "tokenName": "USDG",
            "tokenDecimals": 6,
            "tokenLibraryKey": "core",
            "admissionStatus": "admitted",
        },
        {
            "tokenKeyId": "eip155:4663:0xcandidate",
            "tokenAddress": "0xcandidate",
            "tokenSymbol": "ABC",
            "tokenName": "Example",
            "tokenDecimals": 18,
            "tokenLibraryKey": "stocks",
            "admissionStatus": "admitted",
        },
    ],
    "anchorTokens": [
        {
            "anchorTokenKeyId": "eip155:4663:0xanchor",
            "tokenKeyId": "eip155:4663:0xanchor",
            "tokenRole": "anchor",
            "anchorLibraryKey": "core",
            "tokenLibraryKey": "core",
            "tokenSymbol": "USDG",
            "poolCount": 2,
            "ycPrimaryPoolId": "pool-a",
            "zcPrimaryPoolId": "pool-a",
            "ycBackupPoolIds": ["pool-b"],
            "zcBackupPoolIds": ["pool-b"],
            "ycZcSameSet": True,
        }
    ],
    "poolPairs": [
        {
            "anchorTokenKeyId": "eip155:4663:0xanchor",
            "candidateTokenKeyId": "eip155:4663:0xcandidate",
            "pairKeyId": "pair-abc",
            "poolCount": 2,
            "firstPrimaryPoolId": "pool-a",
            "thirdPrimaryPoolId": "pool-a",
            "firstBackupPoolIds": ["pool-b"],
            "thirdBackupPoolIds": ["pool-b"],
        }
    ],
    "pools": [
        {
            "anchorTokenKeyId": "eip155:4663:0xanchor",
            "candidateTokenKeyId": "eip155:4663:0xcandidate",
            "pairKeyId": "pair-abc",
            "poolId": "pool-a",
            "poolManager": "0xpoolmanager",
            "poolKeyJson": '{"fee":500}',
            "token0": "0xanchor",
            "token1": "0xcandidate",
            "token0KeyId": "eip155:4663:0xanchor",
            "token1KeyId": "eip155:4663:0xcandidate",
            "fee": 500,
            "tickSpacing": 10,
            "hooks": "0x0000000000000000000000000000000000000000",
            "liquidity": "1000000",
            "liquidityIndex": 0.5,
            "depthStatus": "liquid",
            "isFirstHopRecommended": True,
            "isThirdHopRecommended": True,
        },
        {
            "anchorTokenKeyId": "eip155:4663:0xanchor",
            "candidateTokenKeyId": "eip155:4663:0xcandidate",
            "pairKeyId": "pair-abc",
            "poolId": "pool-b",
            "poolManager": "0xpoolmanager",
            "poolKeyJson": '{"fee":3000}',
            "token0": "0xanchor",
            "token1": "0xcandidate",
            "token0KeyId": "eip155:4663:0xanchor",
            "token1KeyId": "eip155:4663:0xcandidate",
            "fee": 3000,
            "tickSpacing": 60,
            "hooks": "0x0000000000000000000000000000000000000000",
            "liquidity": "0",
            "liquidityIndex": 0,
            "depthStatus": "empty",
            "isFirstHopRecommended": False,
            "isThirdHopRecommended": False,
        },
    ],
    "recommendations": [],
    "abnormalAnchorTokens": [
        {
            "anchorTokenKeyId": "eip155:4663:0xanchor",
            "tokenKeyId": "eip155:4663:0xcandidate",
            "anchorLibraryKey": "core",
            "tokenLibraryKey": "stocks",
            "tokenSymbol": "ABC",
            "poolCount": 1,
            "reasons": ["zero_liquidity"],
        }
    ],
    "abnormalPoolPairs": [],
    "abnormalPools": [],
}

CLOSED_LOOP_MANAGER_DATA = {
    "status": {
        "enabled": True,
        "network": "robinhood",
        "chainId": 4663,
        "protocol": "uniswap_v4",
        "scopeKey": "core4",
        "closedLoopVersion": 11,
        "materializedChecksum": "sha256:loops",
        "candidateEdgeVersion": 7,
        "candidateEdgeChecksum": "sha256:candidate",
        "observationVersion": 9,
        "observationChecksum": "sha256:observation",
        "poolScopeChecksum": "sha256:scope",
        "anchorCount": 1,
        "loopCount": 2,
        "hopCount": 6,
        "optimizationCount": 0,
        "skippedTokenCount": 0,
        "consumersNeedRebuild": False,
    },
    "loops": [
        {
            "loopKey": "loop-1",
            "routeKeyId": "route-1",
            "anchorTokenKeyId": "eip155:4663:0xanchor",
            "anchorAddress": "0xanchor",
            "anchorSymbol": "USDG",
            "tokenYKeyId": "eip155:4663:0xy",
            "tokenZKeyId": "eip155:4663:0xz",
            "pathText": "USDG -> Y -> Z -> USDG",
            "pathSymbols": ["USDG", "Y", "Z", "USDG"],
            "pathAddresses": ["0xanchor", "0xy", "0xz", "0xanchor"],
            "poolIds": ["pool-a", "pool-b", "pool-c"],
            "candidateEdgeVersion": 7,
            "candidateEdgeChecksum": "sha256:candidate",
            "observationVersion": 9,
            "observationChecksum": "sha256:observation",
            "status": "ready_for_review",
            "source": "closed_loop_manager",
        },
        {
            "loopKey": "loop-2",
            "routeKeyId": "route-2",
            "anchorTokenKeyId": "eip155:4663:0xanchor",
            "anchorAddress": "0xanchor",
            "anchorSymbol": "USDG",
            "tokenYKeyId": "eip155:4663:0xy2",
            "tokenZKeyId": "eip155:4663:0xz2",
            "pathText": "USDG -> Y2 -> Z2 -> USDG",
            "pathSymbols": ["USDG", "Y2", "Z2", "USDG"],
            "pathAddresses": ["0xanchor", "0xy2", "0xz2", "0xanchor"],
            "poolIds": ["pool-c", "pool-d", "pool-e"],
            "candidateEdgeVersion": 7,
            "candidateEdgeChecksum": "sha256:candidate",
            "observationVersion": 9,
            "observationChecksum": "sha256:observation",
            "status": "ready_for_review",
            "source": "closed_loop_manager",
        },
    ],
    "hops": [
        {
            "loopKey": "loop-1",
            "hopIndex": 0,
            "hopName": "first",
            "inputTokenKeyId": "eip155:4663:0xanchor",
            "outputTokenKeyId": "eip155:4663:0xy",
            "inputAddress": "0xanchor",
            "outputAddress": "0xy",
            "poolId": "pool-a",
        },
        {
            "loopKey": "loop-1",
            "hopIndex": 1,
            "hopName": "bridge",
            "inputTokenKeyId": "eip155:4663:0xy",
            "outputTokenKeyId": "eip155:4663:0xz",
            "inputAddress": "0xy",
            "outputAddress": "0xz",
            "poolId": "pool-b",
        },
        {
            "loopKey": "loop-1",
            "hopIndex": 2,
            "hopName": "third",
            "inputTokenKeyId": "eip155:4663:0xz",
            "outputTokenKeyId": "eip155:4663:0xanchor",
            "inputAddress": "0xz",
            "outputAddress": "0xanchor",
            "poolId": "pool-c",
        },
    ],
    "optimizations": [],
    "runtimeStates": [],
    "skippedTokens": [],
}

MARKET_OBSERVATION_MANAGER_DATA = {
    "status": {
        "enabled": True,
        "network": "robinhood",
        "chainId": 4663,
        "protocol": "uniswap_v4",
        "scopeKey": "core4",
        "observationVersion": 9,
        "materializedChecksum": "sha256:observation",
        "candidateEdgeVersion": 7,
        "candidateEdgeChecksum": "sha256:candidate",
        "poolScopeChecksum": "sha256:scope",
        "anchorCount": 1,
        "tokenCount": 4,
        "directionCount": 2,
        "emptyDirectionCount": 1,
        "poolCount": 1,
        "abnormalDirectionCount": 1,
        "abnormalPoolCount": 1,
        "libraryVersions": {"core": 2, "stocks": 1, "extension": 1},
        "libraryChecksums": {
            "core": "sha256:core",
            "stocks": "sha256:stocks",
            "extension": "sha256:extension",
        },
    },
    "tokens": [
        {
            "tokenKeyId": "eip155:4663:0xanchor",
            "tokenAddress": "0xanchor",
            "tokenSymbol": "USDG",
            "tokenName": "USDG",
            "tokenDecimals": 6,
            "tokenLibraryKey": "core",
            "admissionStatus": "admitted",
        },
        {
            "tokenKeyId": "eip155:4663:0xamzn",
            "tokenAddress": "0xamzn",
            "tokenSymbol": "AMZN",
            "tokenName": "Amazon",
            "tokenDecimals": 18,
            "tokenLibraryKey": "stocks",
            "admissionStatus": "admitted",
        },
        {
            "tokenKeyId": "eip155:4663:0xta",
            "tokenAddress": "0xta",
            "tokenSymbol": "TA",
            "tokenName": "Token A",
            "tokenDecimals": 18,
            "tokenLibraryKey": "extension",
            "admissionStatus": "admitted",
        },
        {
            "tokenKeyId": "eip155:4663:0xtb",
            "tokenAddress": "0xtb",
            "tokenSymbol": "TB",
            "tokenName": "Token B",
            "tokenDecimals": 18,
            "tokenLibraryKey": "extension",
            "admissionStatus": "admitted",
        },
    ],
    "yzPairs": [
        {
            "anchorTokenKeyId": "eip155:4663:0xanchor",
            "directionKey": "eip155:4663:0xta->eip155:4663:0xamzn",
            "tokenXKeyId": "eip155:4663:0xta",
            "tokenYKeyId": "eip155:4663:0xamzn",
            "hasDirectSwapPool": True,
            "directSwapPoolCount": 1,
            "poolIds": ["pool-yz"],
            "optimalPoolId": "pool-yz",
            "observationEmptyReason": None,
        },
        {
            "anchorTokenKeyId": "eip155:4663:0xanchor",
            "directionKey": "eip155:4663:0xtb->eip155:4663:0xamzn",
            "tokenXKeyId": "eip155:4663:0xtb",
            "tokenYKeyId": "eip155:4663:0xamzn",
            "hasDirectSwapPool": False,
            "directSwapPoolCount": 0,
            "poolIds": [],
            "optimalPoolId": None,
            "observationEmptyReason": "no_reachable_v4_pool",
        },
    ],
    "pools": [
        {
            "anchorTokenKeyId": "eip155:4663:0xanchor",
            "poolId": "pool-yz",
            "poolManager": "0xpoolmanager",
            "poolKeyJson": "{\"currency0\":\"0xta\"}",
            "token0": "0xta",
            "token1": "0xamzn",
            "token0KeyId": "eip155:4663:0xta",
            "token1KeyId": "eip155:4663:0xamzn",
            "fee": 3000,
            "tickSpacing": 60,
            "hooks": "0x0000000000000000000000000000000000000000",
            "liquidityIndex": 5.0,
            "liquidity": "100000",
            "depthStatus": "unpriced",
            "selectionRank": 1,
        }
    ],
    "abnormalYzPairs": [
        {
            "anchorTokenKeyId": "eip155:4663:0xanchor",
            "directionKey": "eip155:4663:0xtb->eip155:4663:0xta",
            "tokenXKeyId": "eip155:4663:0xtb",
            "tokenYKeyId": "eip155:4663:0xta",
            "abnormalPoolCount": 1,
            "poolIds": ["pool-bad"],
            "reasons": ["dynamic_fee"],
        }
    ],
    "abnormalPools": [
        {
            "anchorTokenKeyId": "eip155:4663:0xanchor",
            "poolId": "pool-bad",
            "poolStatus": "dynamic_fee",
            "poolKeyJson": "{\"currency0\":\"0xtb\"}",
            "token0KeyId": "eip155:4663:0xtb",
            "token1KeyId": "eip155:4663:0xta",
            "fee": 8388608,
            "tickSpacing": 10,
            "hooks": "0x0000000000000000000000000000000000000000",
            "reasons": ["dynamic_fee"],
        }
    ],
    "liquiditySnapshots": [
        {
            "anchorTokenKeyId": "eip155:4663:0xanchor",
            "poolId": "pool-yz",
            "hasLiquidity": True,
            "liquidity": "100000",
            "liquidityIndex": 5.0,
            "observedAt": "2026-11-05T00:00:00Z",
        }
    ],
}


def test_candidate_manager_shadow_is_disabled_without_network(monkeypatch):
    monkeypatch.delenv("MANAGER_UI_DATA_SOURCE", raising=False)

    payload = manager_page_compare.candidate_manager_shadow_payload(
        LEGACY_PAYLOAD,
        chain_id=4663,
    )

    assert payload["ok"] is False
    assert payload["enabled"] is False
    assert payload["code"] == "MANAGER_UI_DATA_SOURCE_LEGACY"


def test_candidate_manager_page_payload_maps_dto_and_paginates(monkeypatch):
    calls = []

    class FakeClient:
        def get_json(self, path, *, query=None, request_id=None):
            calls.append((path, dict(query or {}), request_id))
            return {
                "ok": True,
                "version": "M8",
                "data": CANDIDATE_MANAGER_DATA,
            }

    monkeypatch.setattr(
        manager_page_compare.ManagerApiClient,
        "from_env",
        lambda: FakeClient(),
    )

    payload = manager_page_compare.candidate_manager_page_payload(
        chain_id=4663,
        protocols=["uniswap_v4"],
        page=1,
        page_size=1,
        query="",
        quality="all",
        abnormal_pool_page=2,
        abnormal_token_page=3,
        chain_selection={"market_id": "dex-arbitrage-4663-uniswap-v4"},
        request_id="candidate-page-1",
    )

    assert payload["ok"] is True
    assert payload["dataSource"] == "manager"
    assert payload["summary"]["staticPoolCount"] == "2"
    assert payload["summary"]["confirmedPoolCount"] == "2"
    assert payload["summary"]["liquidPoolCount"] == "1"
    assert payload["summary"]["usdDepthRequired"] is False
    assert payload["pagination"]["candidate_groups"]["total_count"] == 2
    assert len(payload["anchorGroups"]) == 1
    group = payload["anchorGroups"][0]
    assert group["anchorSymbol"] == "USDG"
    assert group["ycZcSameSet"] is True
    assert [row["poolId"] for row in group["sharedPools"]] == ["pool-a"]
    assert group["sharedTokens"][0]["symbol"] == "ABC"
    assert group["sharedTokens"][0]["address"] == "0xcandidate"
    assert group["abnormalTokens"][0]["tokenKeyId"] == "eip155:4663:0xcandidate"
    assert group["abnormalTokens"][0]["reasons"] == ["zero_liquidity"]
    assert payload["pairs"][0]["symbolPair"] == "USDG:ABC"
    assert payload["pairs"][0]["addressPair"] == "0xanchor:0xcandidate"
    assert group["abnormalPoolPagination"]["page"] == 1
    assert group["abnormalTokenPagination"]["page"] == 1
    assert calls == [
        (
            "/v1/candidate-edge-pools",
            {"chainId": 4663, "limit": 1000},
            "candidate-page-1",
        )
    ]


def test_candidate_manager_page_payload_accepts_empty_snapshot(monkeypatch):
    empty_data = {
        "status": {
            **CANDIDATE_MANAGER_DATA["status"],
            "anchorTokenCount": 0,
            "candidateTokenCount": 0,
            "eligiblePoolCount": 0,
            "recommendationCount": 0,
            "abnormalPoolCount": 0,
        },
        "tokens": [],
        "anchorTokens": [],
        "poolPairs": [],
        "pools": [],
        "recommendations": [],
        "abnormalAnchorTokens": [],
        "abnormalPoolPairs": [],
        "abnormalPools": [],
    }

    class EmptyClient:
        def get_json(self, path, *, query=None, request_id=None):
            return {"ok": True, "version": "M8", "data": empty_data}

    monkeypatch.setattr(
        manager_page_compare.ManagerApiClient,
        "from_env",
        lambda: EmptyClient(),
    )

    payload = manager_page_compare.candidate_manager_page_payload(
        chain_id=4663,
        protocols=["uniswap_v4"],
        page=1000,
        page_size=60,
        query="",
        quality="all",
    )

    assert payload["ok"] is True
    assert payload["anchorGroups"] == []
    assert payload["pairs"] == []
    assert payload["summary"]["staticPoolCount"] == "0"
    assert payload["pagination"]["candidate_groups"]["page"] == 1
    assert payload["pagination"]["candidate_groups"]["total_count"] == 0


def test_candidate_manager_page_payload_rejects_scope_mismatch(monkeypatch):
    class WrongChainClient:
        def get_json(self, path, *, query=None, request_id=None):
            return {
                "ok": True,
                "version": "M8",
                "data": {
                    **CANDIDATE_MANAGER_DATA,
                    "status": {
                        **CANDIDATE_MANAGER_DATA["status"],
                        "chainId": 46630,
                    },
                },
            }

    monkeypatch.setattr(
        manager_page_compare.ManagerApiClient,
        "from_env",
        lambda: WrongChainClient(),
    )

    payload = manager_page_compare.candidate_manager_page_payload(
        chain_id=4663,
        protocols=["uniswap_v4"],
        page=1,
        page_size=20,
        query="",
        quality="all",
    )

    assert payload["ok"] is False
    assert payload["code"] == "MANAGER_CANDIDATE_SCOPE_MISMATCH"


def test_candidate_manager_page_payload_rejects_malformed_snapshot(monkeypatch):
    class MalformedClient:
        def get_json(self, path, *, query=None, request_id=None):
            return {
                "ok": True,
                "version": "M8",
                "data": {
                    **CANDIDATE_MANAGER_DATA,
                    "tokens": {},
                },
            }

    monkeypatch.setattr(
        manager_page_compare.ManagerApiClient,
        "from_env",
        lambda: MalformedClient(),
    )

    payload = manager_page_compare.candidate_manager_page_payload(
        chain_id=4663,
        protocols=["uniswap_v4"],
        page=1,
        page_size=20,
        query="",
        quality="all",
    )

    assert payload["ok"] is False
    assert payload["code"] == "MANAGER_API_INVALID_RESPONSE"


def test_candidate_manager_page_payload_fails_closed(monkeypatch):
    class FailingClient:
        def get_json(self, path, *, query=None, request_id=None):
            raise ManagerApiClientError(
                "MANAGER_API_UNAVAILABLE",
                "connection refused",
                status=503,
            )

    monkeypatch.setattr(
        manager_page_compare.ManagerApiClient,
        "from_env",
        lambda: FailingClient(),
    )

    payload = manager_page_compare.candidate_manager_page_payload(
        chain_id=4663,
        protocols=["uniswap_v4"],
        page=1,
        page_size=20,
        query="",
        quality="all",
    )

    assert payload["ok"] is False
    assert payload["code"] == "MANAGER_API_UNAVAILABLE"
    assert payload["upstreamStatus"] == 503


def test_closed_loop_manager_page_payload_maps_groups_and_version_contract(monkeypatch):
    calls = []

    class FakeClient:
        def get_json(self, path, *, query=None, request_id=None):
            calls.append((path, dict(query or {}), request_id))
            return {
                "ok": True,
                "version": "M8",
                "data": CLOSED_LOOP_MANAGER_DATA,
            }

    monkeypatch.setattr(
        manager_page_compare.ManagerApiClient,
        "from_env",
        lambda: FakeClient(),
    )

    payload = manager_page_compare.closed_loop_manager_page_payload(
        chain_id=4663,
        protocols=["uniswap_v4"],
        include_routes=False,
        chain_selection={"market_id": "dex-arbitrage-4663-uniswap-v4"},
        request_id="closed-loop-page-1",
    )

    assert payload["ok"] is True
    assert payload["dataSource"] == "manager"
    assert payload["summary"]["groupCount"] == 1
    assert payload["summary"]["closedLoopRouteCount"] == 2
    assert payload["summary"]["borrowableSymbolCount"] == 1
    assert payload["summary"]["usdDepthRequired"] is False
    assert payload["versions"]["closedLoopVersion"] == 11
    assert payload["versions"]["closedLoopChecksum"] == "sha256:loops"
    assert payload["versions"]["candidateEdgeChecksum"] == "sha256:candidate"
    assert payload["managerSnapshotCounts"]["hopSnapshotCount"] == 3
    group = payload["groups"][0]
    assert group["flashLoanToken"] == {"symbol": "USDG", "address": "0xanchor"}
    assert group["borrowable"] is True
    assert group["borrowableBasis"] == "closed_loop_manager_anchor"
    assert group["routeCount"] == 2
    assert group["poolDependencyCount"] == 5
    assert "routes" not in group
    assert calls == [
        (
            "/v1/closed-loops",
            {"chainId": 4663, "limit": 1000},
            "closed-loop-page-1",
        )
    ]


def test_closed_loop_manager_page_payload_projects_route_hops_when_requested(monkeypatch):
    class FakeClient:
        def get_json(self, path, *, query=None, request_id=None):
            return {
                "ok": True,
                "version": "M8",
                "data": CLOSED_LOOP_MANAGER_DATA,
            }

    monkeypatch.setattr(
        manager_page_compare.ManagerApiClient,
        "from_env",
        lambda: FakeClient(),
    )

    payload = manager_page_compare.closed_loop_manager_page_payload(
        chain_id=4663,
        protocols=["uniswap_v4"],
        include_routes=True,
    )

    route = payload["groups"][0]["routes"][0]
    assert route["routeKey"] == "route-1"
    assert route["pathText"] == "USDG -> Y -> Z -> USDG"
    assert route["poolIds"] == ["pool-a", "pool-b", "pool-c"]
    assert [pool["poolId"] for pool in route["routePools"]] == [
        "pool-a",
        "pool-b",
        "pool-c",
    ]
    assert route["routePools"][1]["routeSymbols"] == ["Y", "Z"]
    assert route["routePools"][1]["direction"] == "bridge"


def test_closed_loop_manager_page_payload_rejects_scope_mismatch(monkeypatch):
    class WrongChainClient:
        def get_json(self, path, *, query=None, request_id=None):
            return {
                "ok": True,
                "version": "M8",
                "data": {
                    **CLOSED_LOOP_MANAGER_DATA,
                    "status": {
                        **CLOSED_LOOP_MANAGER_DATA["status"],
                        "chainId": 46630,
                    },
                },
            }

    monkeypatch.setattr(
        manager_page_compare.ManagerApiClient,
        "from_env",
        lambda: WrongChainClient(),
    )

    payload = manager_page_compare.closed_loop_manager_page_payload(
        chain_id=4663,
        protocols=["uniswap_v4"],
        include_routes=False,
    )

    assert payload["ok"] is False
    assert payload["code"] == "MANAGER_CLOSED_LOOP_SCOPE_MISMATCH"


def test_closed_loop_manager_shadow_is_disabled_without_network(monkeypatch):
    monkeypatch.delenv("MANAGER_UI_DATA_SOURCE", raising=False)

    payload = manager_page_compare.closed_loop_manager_shadow_payload(
        {"summary": {"groupCount": 2}},
        chain_id=4663,
    )

    assert payload["ok"] is False
    assert payload["enabled"] is False
    assert payload["code"] == "MANAGER_UI_DATA_SOURCE_LEGACY"


def test_market_observation_manager_shadow_is_disabled_without_network(monkeypatch):
    monkeypatch.delenv("MANAGER_UI_DATA_SOURCE", raising=False)

    payload = manager_page_compare.market_observation_manager_shadow_payload(
        {"summary": {"routeCount": 5}},
        chain_id=4663,
    )

    assert payload["ok"] is False
    assert payload["enabled"] is False
    assert payload["code"] == "MANAGER_UI_DATA_SOURCE_LEGACY"


def test_submission_manager_shadow_is_disabled_without_network(monkeypatch):
    monkeypatch.delenv("MANAGER_UI_DATA_SOURCE", raising=False)

    payload = manager_page_compare.submission_manager_shadow_payload(
        {"summary": {"attemptCount": 3}},
        chain_id=4663,
    )

    assert payload["ok"] is False
    assert payload["enabled"] is False
    assert payload["code"] == "MANAGER_UI_DATA_SOURCE_LEGACY"


def test_review_manager_page_payload_maps_manager_rows_to_ui_dto(monkeypatch):
    calls = []

    class FakeClient:
        def get_json(self, path, *, query=None, request_id=None):
            calls.append((path, dict(query or {}), request_id))
            signed_pnl = {
                "raw": "12.5 USDG",
                "amount": 12.5,
                "currency": "USDG",
                "address": "0xusdg",
                "unit": "token",
                "source": "runArbitrage",
            }
            return {
                "ok": True,
                "version": "M8",
                "data": {
                    "status": {
                        "enabled": True,
                        "network": "robinhood",
                        "chainId": 4663,
                        "protocol": "uniswap_v4",
                        "scopeKey": "core4",
                        "reviewVersion": 13,
                        "materializedChecksum": "sha256:review",
                        "closedLoopVersion": 11,
                        "closedLoopChecksum": "sha256:loops",
                        "loopCount": 3,
                        "reviewedCount": 3,
                        "completedCount": 1,
                        "dataIncompleteCount": 1,
                        "notExecutableCount": 1,
                        "failedCount": 0,
                        "broadcastRequestCount": 1,
                    },
                    "categoryCounts": {
                        "total": 3,
                        "completed": 1,
                        "readyNotSubmitted": 1,
                        "confirmedNoProfit": 0,
                        "dataIncomplete": 1,
                        "reviewFailed": 1,
                        "other": 0,
                    },
                    "filteredCount": 3,
                    "offset": 0,
                    "limit": 2,
                    "reviews": [
                        {
                            "loopKey": "loop-ready",
                            "closedLoopVersion": 11,
                            "closedLoopChecksum": "sha256:loops",
                            "candidateEdgeVersion": 7,
                            "candidateEdgeChecksum": "sha256:edges",
                            "observationVersion": 4,
                            "observationChecksum": "sha256:obs",
                            "poolScopeChecksum": "sha256:scope",
                            "anchorTokenKeyId": "robinhood:4663:0xusdg",
                            "anchorAddress": "0xusdg",
                            "anchorSymbol": "USDG",
                            "pathText": "USDG -> AMZN -> TA -> USDG",
                            "pathAddresses": ["0xusdg", "0xamzn", "0xta", "0xusdg"],
                            "pathSymbols": ["USDG", "AMZN", "TA", "USDG"],
                            "poolPathKey": "pool-path-1",
                            "poolIds": ["pool-a", "pool-yz", "pool-b"],
                            "reviewStatus": "completed",
                            "pageCategory": "ready_not_submitted",
                            "reviewReason": None,
                            "staticPreview": {"ok": True},
                            "runArbitrage": {
                                "ok": True,
                                "executable": True,
                                "blockNumber": 100,
                            },
                            "signedPnl": signed_pnl,
                            "reviewedAt": "2026-11-05T00:00:06Z",
                        },
                        {
                            "loopKey": "loop-incomplete",
                            "closedLoopVersion": 11,
                            "closedLoopChecksum": "sha256:loops",
                            "candidateEdgeVersion": 7,
                            "candidateEdgeChecksum": "sha256:edges",
                            "observationVersion": 4,
                            "observationChecksum": "sha256:obs",
                            "poolScopeChecksum": "sha256:scope",
                            "anchorTokenKeyId": "robinhood:4663:0xusdg",
                            "anchorAddress": "0xusdg",
                            "anchorSymbol": "USDG",
                            "pathText": "USDG -> WETH -> USDG",
                            "pathAddresses": ["0xusdg", "0xweth", "0xusdg"],
                            "pathSymbols": ["USDG", "WETH", "USDG"],
                            "poolPathKey": "pool-path-2",
                            "poolIds": ["pool-c", "pool-d"],
                            "reviewStatus": "data_incomplete",
                            "pageCategory": "data_incomplete",
                            "reviewReason": "signed PnL is missing",
                            "staticPreview": {"ok": True},
                            "runArbitrage": {"ok": True, "executable": True},
                            "signedPnl": None,
                            "reviewedAt": "2026-11-05T00:00:04Z",
                        },
                    ],
                    "diagnostics": [
                        {
                            "loopKey": "loop-ready",
                            "diagnosticId": "diag-1",
                            "diagnosticType": "other",
                            "diagnosticCode": "fixture",
                            "message": "fixture diagnostic",
                            "payload": {},
                            "observedAt": "2026-11-05T00:00:00Z",
                        },
                        {
                            "loopKey": "loop-incomplete",
                            "diagnosticId": "diag-2",
                            "diagnosticType": "evidence_missing",
                            "message": "missing signed PnL",
                            "payload": {},
                            "observedAt": "2026-11-05T00:00:00Z",
                        },
                    ],
                    "broadcastRequests": [
                        {
                            "loopKey": "loop-ready",
                            "requestStatus": "generated",
                            "signedPnl": signed_pnl,
                        }
                    ],
                },
            }

    monkeypatch.setattr(
        manager_page_compare.ManagerApiClient,
        "from_env",
        lambda: FakeClient(),
    )

    payload = manager_page_compare.review_manager_page_payload(
        chain_id=4663,
        mode="dex_submission_review_records",
        category="all",
        status=None,
        outcome=None,
        page=1,
        page_size=2,
        chain_selection={"market_id": "dex-arbitrage-4663-uniswap-v4"},
        request_id="review-page-1",
    )

    assert payload["ok"] is True
    assert payload["dataSource"] == "manager"
    assert payload["recordKind"] == "review"
    assert payload["pagination"]["total"] == 3
    assert payload["pagination"]["total_pages"] == 2
    assert payload["summary"]["pageAttemptCount"] == 2
    assert payload["categoryCounts"]["ready_not_submitted"] == 1
    assert len(payload["attempts"]) == 2
    ready = payload["attempts"][0]
    assert ready["pair"] == "USDG -> AMZN -> TA -> USDG"
    assert ready["review_category"] == "ready_not_submitted"
    assert ready["selectedProfitAmount"] == 12.5
    assert ready["selectedProfitSource"] == "runArbitrage"
    assert ready["review_summary"]["plan"]["finalDeltaAmount"] == 12.5
    assert ready["reasons"] == ["fixture diagnostic", "other:fixture"]
    incomplete = payload["attempts"][1]
    assert incomplete["page_category"] == "data_incomplete"
    assert incomplete["diagnostics"][0]["message"] == "missing signed PnL"
    assert incomplete["reasons"] == [
        "signed PnL is missing",
        "missing signed PnL",
    ]
    assert calls == [
        (
            "/v1/reviews",
            {
                "chainId": 4663,
                "offset": 0,
                "limit": 2,
            },
            "review-page-1",
        )
    ]

    calls.clear()
    manager_page_compare.review_manager_page_payload(
        chain_id=4663,
        mode="dex_submission_review_failed_records",
        category="review_failed",
        status="failed",
        outcome="failed",
        page=3,
        page_size=10,
        chain_selection={},
        request_id="review-page-filtered",
    )
    assert calls == [
        (
            "/v1/reviews",
            {
                "chainId": 4663,
                "offset": 20,
                "limit": 10,
                "category": "review_failed",
                "status": "failed",
                "outcome": "failed",
            },
            "review-page-filtered",
        )
    ]


def test_submission_detail_manager_payload_is_manager_only_without_legacy_gate(
    monkeypatch,
):
    class UnavailableClient:
        def get_json(self, path, *, query=None, request_id=None):
            raise ManagerApiClientError(
                "MANAGER_API_UNAVAILABLE",
                "manager is not reachable",
                status=503,
            )

    monkeypatch.setattr(
        manager_page_compare.ManagerApiClient,
        "from_env",
        lambda: UnavailableClient(),
    )

    payload = manager_page_compare.submission_detail_manager_payload(
        {"id": 42},
        chain_id=4663,
        attempt_id="att-42",
    )

    assert payload["ok"] is False
    assert payload["source"] == "manager"
    assert payload["code"] == "MANAGER_API_UNAVAILABLE"
    assert payload.get("dataSource") is None
    assert "legacyUnaffected" not in payload


def test_overview_manager_payload_reads_runtime_core_even_when_ui_default_is_legacy(
    monkeypatch,
):
    monkeypatch.delenv("MANAGER_UI_DATA_SOURCE", raising=False)

    payload = manager_page_compare.overview_manager_page_payload(
        chain_id=4663,
        protocols=["requested_protocol"],
        chain_selection={"market_id": "dex-arbitrage-4663-uniswap-v4"},
        request_id="overview-manager-1",
        overview_envelope={
            "ok": True,
            "version": "rust_runtime_core_v1",
            "dataSource": "rust_runtime_core",
            "runtimeCore": {"chainId": 4663, "instanceId": "runtime-core-1"},
            "data": dict(OVERVIEW_MANAGER_DATA),
        },
    )

    assert payload["ok"] is True
    assert payload["dataSource"] == "manager"
    assert payload["runtimeDataSource"] == "rust_runtime_core"
    assert payload["runtimeCore"]["instanceId"] == "runtime-core-1"
    assert payload["summary"]["arbitragePathCount"] == 3
    assert payload["selectedProtocols"] == ["uniswap_v4"]
    assert payload["requested_protocols"] == ["requested_protocol"]


def test_overview_manager_payload_uses_request_bound_runtime_core_without_http(
    monkeypatch,
):
    calls = []

    class FakeRuntime:
        def manager_view(self, view: str, limit: int) -> dict:
            calls.append((view, limit))
            return {
                "ok": True,
                "view": view,
                "version": "rust_runtime_core_v1",
                "dataSource": "rust_runtime_core",
                "runtimeCore": {"chainId": 4663, "instanceId": "runtime-core-1"},
                "data": dict(OVERVIEW_MANAGER_DATA),
            }

    monkeypatch.setattr(
        manager_page_compare.ManagerApiClient,
        "from_env",
        lambda: (_ for _ in ()).throw(
            AssertionError("overview must not use ManagerApiClient HTTP reads")
        ),
    )
    app = Flask(__name__)
    with app.app_context():
        g.chain_runtime = FakeRuntime()
        payload = manager_page_compare.overview_manager_page_payload(
            chain_id=4663,
            protocols=["uniswap_v4"],
        )

    assert calls == [("overview", 1000)]
    assert payload["ok"] is True
    assert payload["dataSource"] == "manager"
    assert payload["runtimeDataSource"] == "rust_runtime_core"
    assert payload["runtimeCore"]["instanceId"] == "runtime-core-1"


def test_overview_manager_payload_runtime_core_error_has_no_shadow_fields(monkeypatch):
    monkeypatch.setattr(
        manager_page_compare,
        "runtime_core_manager_overview_envelope",
        lambda **kwargs: {
            "ok": False,
            "source": "manager",
            "dataSource": "manager",
            "code": "RUNTIME_CORE_VIEW_READ_FAILED",
            "detail": "runtime core is not reachable",
            "upstreamStatus": 503,
        },
    )

    payload = manager_page_compare.overview_manager_page_payload(
        chain_id=4663,
        protocols=["uniswap_v4"],
    )

    assert payload == {
        "ok": False,
        "source": "manager",
        "dataSource": "manager",
        "code": "RUNTIME_CORE_VIEW_READ_FAILED",
        "detail": "runtime core is not reachable",
        "upstreamStatus": 503,
    }


def test_overview_manager_payload_rejects_incomplete_dto_without_defaults(monkeypatch):
    payload = manager_page_compare.overview_manager_page_payload(
        chain_id=4663,
        protocols=["uniswap_v4"],
        overview_envelope={
            "ok": True,
            "version": "rust_runtime_core_v1",
            "dataSource": "rust_runtime_core",
            "runtimeCore": {"chainId": 4663},
            "data": {
                **OVERVIEW_MANAGER_DATA,
                "dataQuality": None,
            },
        },
    )

    assert payload["ok"] is False
    assert payload["dataSource"] == "manager"
    assert payload["code"] == "MANAGER_API_INVALID_RESPONSE"
    assert "dataQuality" in payload["detail"]
    assert "legacyUnaffected" not in payload


def test_overview_manager_payload_rejects_wrong_chain(monkeypatch):
    payload = manager_page_compare.overview_manager_page_payload(
        chain_id=4663,
        protocols=["uniswap_v4"],
        overview_envelope={
            "ok": True,
            "version": "rust_runtime_core_v1",
            "dataSource": "rust_runtime_core",
            "runtimeCore": {"chainId": 4663},
            "data": {
                **OVERVIEW_MANAGER_DATA,
                "chainId": 46630,
            },
        },
    )

    assert payload["ok"] is False
    assert payload["code"] == "MANAGER_OVERVIEW_SCOPE_MISMATCH"


def test_candidate_manager_shadow_reports_versions_and_count_deltas(monkeypatch):
    calls = []

    class FakeClient:
        def get_json(self, path, *, query=None, request_id=None):
            calls.append((path, dict(query or {}), request_id))
            if path == "/v1/candidate-edge-pools":
                return {
                    "ok": True,
                    "version": "M8",
                    "data": {
                        "status": {
                            "candidateVersion": 7,
                            "materializedChecksum": "sha256:candidate",
                            "poolScopeChecksum": "sha256:scope",
                            "eligiblePoolCount": 33,
                            "abnormalPoolCount": 1,
                            "recommendationCount": 18,
                            "candidateTokenCount": 50,
                            "anchorTokenCount": 6,
                        },
                        "poolPairs": [{}, {}],
                        "pools": [{}, {}, {}],
                        "recommendations": [{}],
                        "abnormalPools": [],
                    },
                }
            assert path == "/v1/candidate-edge-pools/compare"
            return {
                "ok": True,
                "version": "M8",
                "data": {
                    "managerChecksum": "sha256:compare",
                    "readyForCutover": False,
                    "differencesTruncated": True,
                    "unresolvedLegacyAnchors": ["USDG"],
                    "causeCounts": {"metadata": 2},
                    "anchorTokens": {
                        "managerRowCount": 5,
                        "legacyRowCount": 4,
                        "managerOnlyCount": 1,
                        "legacyOnlyCount": 0,
                        "fieldMismatchCount": 1,
                    },
                    "poolPairs": {
                        "managerRowCount": 2,
                        "legacyRowCount": 2,
                        "managerOnlyCount": 1,
                        "legacyOnlyCount": 2,
                        "fieldMismatchCount": 3,
                    },
                    "pools": {
                        "managerRowCount": 3,
                        "legacyRowCount": 3,
                        "managerOnlyCount": 0,
                        "legacyOnlyCount": 0,
                        "fieldMismatchCount": 0,
                    },
                    "recommendations": {
                        "managerRowCount": 1,
                        "legacyRowCount": 1,
                        "managerOnlyCount": 0,
                        "legacyOnlyCount": 0,
                        "fieldMismatchCount": 0,
                    },
                },
            }

    monkeypatch.setenv("MANAGER_UI_DATA_SOURCE", "manager")
    monkeypatch.setattr(
        manager_page_compare.ManagerApiClient,
        "from_env",
        lambda: FakeClient(),
    )

    payload = manager_page_compare.candidate_manager_shadow_payload(
        LEGACY_PAYLOAD,
        chain_id=4663,
        request_id="shadow-1",
    )

    assert payload["ok"] is True
    assert payload["source"] == "manager"
    assert payload["legacyUnaffected"] is True
    assert payload["candidateVersion"] == 7
    assert payload["candidateChecksum"] == "sha256:candidate"
    assert payload["snapshotLimit"] == 1000
    assert payload["snapshotCountSemantics"]["statusCounts"] == "full_materialized_scope"
    assert payload["snapshotCountSemantics"]["rowCounts"] == "bounded_snapshot_rows"
    assert payload["countComparisons"]["eligiblePoolCount"]["delta"] == 3
    assert payload["managerStatusCounts"]["eligiblePoolCount"] == 33
    assert payload["managerSnapshotCounts"]["poolPairSnapshotCount"] == 2
    assert payload["managerSnapshotCounts"]["poolSnapshotCount"] == 3
    assert payload["managerSnapshotCounts"]["recommendationSnapshotCount"] == 1
    assert payload["countComparisons"]["poolPairCount"]["manager"] is None
    assert calls == [
        (
            "/v1/candidate-edge-pools",
            {"chainId": 4663, "limit": 1000},
            "shadow-1",
        ),
        (
            "/v1/candidate-edge-pools/compare",
            {
                "chainId": 4663,
                "sampleLimit": 200,
                "legacyAnchorScope": "dex-arbitrage-4663-uniswap-v4",
            },
            "shadow-1",
        ),
    ]
    assert payload["rowComparison"]["ok"] is True
    assert payload["rowComparison"]["sampleLimit"] == 200
    assert payload["rowComparison"]["sampleReadyForCutover"] is False
    assert payload["rowComparison"]["differenceCounts"]["poolPairs"]["totalDifferenceCount"] == 6
    assert payload["rowComparison"]["differenceCounts"]["pools"]["totalDifferenceCount"] == 0
    assert payload["rowComparison"]["unresolvedLegacyAnchors"] == ["USDG"]
    assert payload["comparisonReadyForFieldSwitch"] is False


def test_candidate_manager_shadow_keeps_snapshot_usable_when_row_compare_fails(monkeypatch):
    class FakeClient:
        def get_json(self, path, *, query=None, request_id=None):
            if path == "/v1/candidate-edge-pools":
                return {
                    "ok": True,
                    "version": "M8",
                    "data": {
                        "status": {
                            "candidateVersion": 7,
                            "materializedChecksum": "sha256:candidate",
                            "eligiblePoolCount": 33,
                            "abnormalPoolCount": 1,
                            "recommendationCount": 18,
                            "candidateTokenCount": 50,
                            "anchorTokenCount": 6,
                        },
                        "poolPairs": [{}],
                        "pools": [{}],
                        "recommendations": [],
                        "abnormalPools": [],
                    },
                }
            raise ManagerApiClientError(
                "MANAGER_API_UNAVAILABLE",
                "compare is unavailable",
                status=503,
            )

    monkeypatch.setenv("MANAGER_UI_DATA_SOURCE", "manager")
    monkeypatch.setattr(
        manager_page_compare.ManagerApiClient,
        "from_env",
        lambda: FakeClient(),
    )

    payload = manager_page_compare.candidate_manager_shadow_payload(
        LEGACY_PAYLOAD,
        chain_id=4663,
    )

    assert payload["ok"] is True
    assert payload["legacyUnaffected"] is True
    assert payload["rowComparison"]["ok"] is False
    assert payload["rowComparison"]["code"] == "MANAGER_API_UNAVAILABLE"
    assert payload["rowComparison"]["readyForCutover"] is False
    assert payload["comparisonReadyForFieldSwitch"] is False


def test_candidate_manager_shadow_keeps_legacy_payload_usable_on_error(monkeypatch):
    class FailingClient:
        def get_json(self, path, *, query=None, request_id=None):
            raise ManagerApiClientError(
                "MANAGER_API_UNAVAILABLE",
                "connection refused",
                status=503,
            )

    monkeypatch.setenv("MANAGER_UI_DATA_SOURCE", "manager")
    monkeypatch.setattr(
        manager_page_compare.ManagerApiClient,
        "from_env",
        lambda: FailingClient(),
    )

    payload = manager_page_compare.candidate_manager_shadow_payload(
        LEGACY_PAYLOAD,
        chain_id=4663,
    )

    assert payload["ok"] is False
    assert payload["legacyUnaffected"] is True
    assert payload["code"] == "MANAGER_API_UNAVAILABLE"
    assert payload["upstreamStatus"] == 503


def test_candidate_page_route_is_manager_only_and_fail_closed(monkeypatch):
    app = Flask(__name__)
    dex_surface_routes_ops.register_dex_pool_routes(app)
    monkeypatch.setattr(
        dex_surface_routes_ops._root,
        "selected_chain_id_from_request",
        lambda: ({"market_id": "dex-arbitrage-4663-uniswap-v4"}, 4663),
    )
    monkeypatch.setattr(
        dex_surface_routes_ops._root,
        "request_dex_protocols_arg",
        lambda: ["uniswap_v4"],
    )
    monkeypatch.setattr(
        dex_surface_routes_ops._root,
        "candidate_pagination_args",
        lambda: (1, 20, "", "all"),
    )
    monkeypatch.setattr(
        dex_surface_routes_ops._root,
        "section_pagination_args",
        lambda: (1, 1),
    )
    monkeypatch.setattr(
        dex_surface_routes_ops._root,
        "_attach_dex_strategy",
        lambda payload: {**payload, "dex_strategy": "closed_loop_routes"},
    )
    monkeypatch.setattr(
        dex_surface_routes_ops,
        "mask_dex_surface_placeholder_symbols",
        lambda payload: payload,
    )
    manager_calls = []
    monkeypatch.setattr(
        dex_surface_routes_ops,
        "candidate_manager_page_payload",
        lambda **kwargs: manager_calls.append(kwargs)
        or {
            "ok": True,
            "source": "manager",
            "dataSource": "manager",
            "mode": "dex_pool_candidates",
            "chain_id": kwargs["chain_id"],
            "chain_selection": kwargs["chain_selection"],
            "selected_protocols": kwargs["protocols"],
            "summary": {"staticPairCount": "2"},
            "anchorGroups": [],
            "pairs": [],
        },
    )
    legacy_calls = []
    monkeypatch.setattr(
        dex_surface_routes_ops._root,
        "_bootstrap_dex_surface_dependencies",
        lambda **kwargs: legacy_calls.append("bootstrap"),
    )
    monkeypatch.setattr(
        dex_surface_routes_ops._root,
        "dex_pool_candidates_payload",
        lambda **kwargs: legacy_calls.append("payload") or {},
    )
    monkeypatch.setattr(
        dex_surface_routes_ops._root,
        "paginate_candidate_payload",
        lambda payload, **kwargs: legacy_calls.append("paginate") or payload,
    )

    client = app.test_client()
    response = client.get("/api/dex-pools/candidates?chain_id=4663&manager_compare=true")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["dataSource"] == "manager"
    assert payload["dex_strategy"] == "closed_loop_routes"
    assert "managerShadow" not in payload
    assert legacy_calls == []
    assert len(manager_calls) == 1
    assert manager_calls[0]["chain_id"] == 4663
    assert manager_calls[0]["protocols"] == ["uniswap_v4"]
    assert manager_calls[0]["page"] == 1
    assert manager_calls[0]["page_size"] == 20
    assert manager_calls[0]["abnormal_pool_page"] == 1
    assert manager_calls[0]["abnormal_token_page"] == 1
    assert manager_calls[0]["chain_selection"]["market_id"] == (
        "dex-arbitrage-4663-uniswap-v4"
    )

    monkeypatch.setattr(
        dex_surface_routes_ops,
        "candidate_manager_page_payload",
        lambda **kwargs: {
            "ok": False,
            "source": "manager",
            "dataSource": "manager",
            "code": "MANAGER_API_UNAVAILABLE",
            "detail": "manager unavailable",
            "upstreamStatus": 503,
        },
    )
    failed = client.get("/api/dex-pools/candidates?chain_id=4663")
    assert failed.status_code == 503
    failed_payload = failed.get_json()
    assert failed_payload["error"] == "manager_read_failed"
    assert failed_payload["dataSource"] == "manager"
    assert failed_payload["managerDetail"]["code"] == "MANAGER_API_UNAVAILABLE"
    assert legacy_calls == []


def test_closed_loop_manager_shadow_reports_versions_counts_and_comparison(monkeypatch):
    calls = []

    class FakeClient:
        def get_json(self, path, *, query=None, request_id=None):
            calls.append((path, dict(query or {}), request_id))
            if path == "/v1/closed-loops":
                return {
                    "ok": True,
                    "version": "M8",
                    "data": {
                        "status": {
                            "closedLoopVersion": 11,
                            "materializedChecksum": "sha256:loops",
                            "candidateEdgeVersion": 7,
                            "candidateEdgeChecksum": "sha256:candidate",
                            "observationVersion": 9,
                            "observationChecksum": "sha256:observation",
                            "poolScopeChecksum": "sha256:scope",
                            "anchorCount": 4,
                            "loopCount": 8,
                            "hopCount": 24,
                            "optimizationCount": 2,
                            "skippedTokenCount": 1,
                            "consumersNeedRebuild": True,
                        },
                        "loops": [{}, {}],
                        "hops": [{}, {}, {}],
                        "optimizations": [{}],
                        "runtimeStates": [{}],
                        "skippedTokens": [],
                    },
                }
            assert path == "/v1/closed-loops/compare"
            return {
                "ok": True,
                "version": "M8",
                "data": {
                    "managerChecksum": "sha256:loops",
                    "legacyTablesAvailable": True,
                    "readyForCutover": False,
                    "differencesTruncated": False,
                    "causeCounts": {"poolSelection": 1},
                    "loops": {
                        "managerRowCount": 2,
                        "legacyRowCount": 2,
                        "managerOnlyCount": 1,
                        "legacyOnlyCount": 0,
                        "fieldMismatchCount": 1,
                    },
                    "hops": {
                        "managerRowCount": 3,
                        "legacyRowCount": 3,
                        "managerOnlyCount": 0,
                        "legacyOnlyCount": 0,
                        "fieldMismatchCount": 0,
                    },
                },
            }

    monkeypatch.setenv("MANAGER_UI_DATA_SOURCE", "manager")
    monkeypatch.setattr(
        manager_page_compare.ManagerApiClient,
        "from_env",
        lambda: FakeClient(),
    )

    payload = manager_page_compare.closed_loop_manager_shadow_payload(
        {
            "summary": {
                "borrowableSymbolCount": 3,
                "groupCount": 2,
                "closedLoopRouteCount": 5,
            }
        },
        chain_id=4663,
        request_id="loops-shadow-1",
    )

    assert payload["ok"] is True
    assert payload["closedLoopVersion"] == 11
    assert payload["candidateEdgeVersion"] == 7
    assert payload["observationVersion"] == 9
    assert payload["consumersNeedRebuild"] is True
    assert payload["managerStatusCounts"]["loopCount"] == 8
    assert payload["managerSnapshotCounts"]["loopSnapshotCount"] == 2
    assert payload["managerSnapshotCounts"]["hopSnapshotCount"] == 3
    assert payload["rowComparison"]["legacyTablesAvailable"] is True
    assert payload["rowComparison"]["differenceCounts"]["loops"]["totalDifferenceCount"] == 2
    assert payload["rowComparison"]["differenceCounts"]["hops"]["totalDifferenceCount"] == 0
    assert payload["comparisonReadyForFieldSwitch"] is False
    assert calls == [
        (
            "/v1/closed-loops",
            {"chainId": 4663, "limit": 1000},
            "loops-shadow-1",
        ),
        (
            "/v1/closed-loops/compare",
            {
                "chainId": 4663,
                "sampleLimit": 200,
                "legacyMarketId": "dex-arbitrage-4663-uniswap-v4",
            },
            "loops-shadow-1",
        ),
    ]


def test_closed_loop_page_route_is_manager_only_and_fails_closed(monkeypatch):
    app = Flask(__name__)
    dex_surface_routes_config.register_dex_overview_routes(app)
    base_payload = {
        "ok": True,
        "dataSource": "manager",
        "mode": "dex_closed_loop_groups",
        "summary": {"groupCount": 1, "closedLoopRouteCount": 2},
        "groups": [
            {
                "flashLoanToken": {"symbol": "USDG", "address": "0xanchor"},
                "borrowable": True,
                "routeCount": 2,
                "poolDependencyCount": 2,
            }
        ],
    }
    monkeypatch.setattr(
        dex_surface_routes_config._root,
        "selected_chain_id_from_request",
        lambda: ({"market_id": "dex-arbitrage-4663-uniswap-v4"}, 4663),
    )
    monkeypatch.setattr(
        dex_surface_routes_config._root,
        "request_dex_protocols_arg",
        lambda: ["uniswap_v4"],
    )
    monkeypatch.setattr(
        dex_surface_routes_config._root,
        "_attach_dex_strategy",
        lambda payload: {**payload},
    )
    calls = []
    monkeypatch.setattr(
        dex_surface_routes_config,
        "closed_loop_manager_page_payload",
        lambda **kwargs: calls.append(dict(kwargs)) or dict(base_payload),
    )

    client = app.test_client()
    success = client.get("/api/dex/closed-loop-groups?chain_id=4663")
    assert success.status_code == 200
    payload = success.get_json()
    assert payload["dataSource"] == "manager"
    assert payload["summary"]["groupCount"] == 1
    assert calls
    assert calls[0]["chain_id"] == 4663
    assert calls[0]["protocols"] == ["uniswap_v4"]

    monkeypatch.setattr(
        dex_surface_routes_config,
        "closed_loop_manager_page_payload",
        lambda **kwargs: {
            "ok": False,
            "source": "manager",
            "dataSource": "manager",
            "code": "MANAGER_API_UNAVAILABLE",
            "detail": "manager unavailable",
            "upstreamStatus": 503,
        },
    )
    failed = client.get("/api/dex/closed-loop-groups?chain_id=4663")
    assert failed.status_code == 503
    failed_payload = failed.get_json()
    assert failed_payload["error"] == "manager_read_failed"
    assert failed_payload["dataSource"] == "manager"
    assert failed_payload["managerDetail"]["code"] == "MANAGER_API_UNAVAILABLE"


def test_market_observation_manager_shadow_reports_versions_counts_and_comparison(monkeypatch):
    calls = []

    class FakeClient:
        def get_json(self, path, *, query=None, request_id=None):
            calls.append((path, dict(query or {}), request_id))
            if path == "/v1/candidate-exchange-pools":
                return {
                    "ok": True,
                    "version": "M8",
                    "data": {
                        "status": {
                            "observationVersion": 9,
                            "materializedChecksum": "sha256:observation",
                            "candidateEdgeVersion": 7,
                            "candidateEdgeChecksum": "sha256:candidate",
                            "poolScopeChecksum": "sha256:scope",
                            "anchorCount": 5,
                            "tokenCount": 30,
                            "directionCount": 12,
                            "emptyDirectionCount": 1,
                            "poolCount": 20,
                            "abnormalDirectionCount": 1,
                            "abnormalPoolCount": 2,
                        },
                        "yzPairs": [{}],
                        "pools": [{}, {}],
                        "abnormalYzPairs": [{}],
                        "abnormalPools": [{}, {}],
                        "liquiditySnapshots": [{}],
                    },
                }
            assert path == "/v1/candidate-exchange-pools/compare"
            return {
                "ok": True,
                "version": "M8",
                "data": {
                    "managerChecksum": "sha256:observation",
                    "readyForCutover": False,
                    "differencesTruncated": True,
                    "causeCounts": {"poolSelection": 1},
                    "yzPairs": {
                        "managerRowCount": 1,
                        "legacyRowCount": 1,
                        "managerOnlyCount": 1,
                        "legacyOnlyCount": 0,
                        "fieldMismatchCount": 0,
                    },
                    "pools": {
                        "managerRowCount": 2,
                        "legacyRowCount": 2,
                        "managerOnlyCount": 0,
                        "legacyOnlyCount": 0,
                        "fieldMismatchCount": 0,
                    },
                    "abnormalYzPairs": {
                        "managerRowCount": 1,
                        "legacyRowCount": 1,
                        "managerOnlyCount": 0,
                        "legacyOnlyCount": 0,
                        "fieldMismatchCount": 0,
                    },
                    "abnormalPools": {
                        "managerRowCount": 2,
                        "legacyRowCount": 2,
                        "managerOnlyCount": 0,
                        "legacyOnlyCount": 0,
                        "fieldMismatchCount": 0,
                    },
                },
            }

    monkeypatch.setenv("MANAGER_UI_DATA_SOURCE", "manager")
    monkeypatch.setattr(
        manager_page_compare.ManagerApiClient,
        "from_env",
        lambda: FakeClient(),
    )

    payload = manager_page_compare.market_observation_manager_shadow_payload(
        {
            "summary": {
                "routeCount": 5,
                "candidateYzPairCount": 1,
                "verifiedYzPairCount": 1,
                "missingYzPairCount": 0,
            },
            "matchedPoolCount": 4,
        },
        chain_id=4663,
        request_id="observation-shadow-1",
    )

    assert payload["ok"] is True
    assert payload["observationVersion"] == 9
    assert payload["candidateEdgeVersion"] == 7
    assert payload["managerStatusCounts"]["directionCount"] == 12
    assert payload["managerStatusCounts"]["poolCount"] == 20
    assert payload["managerSnapshotCounts"]["poolSnapshotCount"] == 2
    assert payload["managerSnapshotCounts"]["liquiditySnapshotCount"] == 1
    assert payload["rowComparison"]["differenceCounts"]["yzPairs"]["totalDifferenceCount"] == 1
    assert payload["rowComparison"]["differenceCounts"]["pools"]["totalDifferenceCount"] == 0
    assert payload["comparisonReadyForFieldSwitch"] is False
    assert calls == [
        (
            "/v1/candidate-exchange-pools",
            {"chainId": 4663, "limit": 1000},
            "observation-shadow-1",
        ),
        (
            "/v1/candidate-exchange-pools/compare",
            {
                "chainId": 4663,
                "sampleLimit": 200,
                "legacyAnchorScope": "dex-arbitrage-4663-uniswap-v4",
            },
            "observation-shadow-1",
        ),
    ]


def test_market_observation_manager_page_payload_maps_dto_and_versions(monkeypatch):
    calls = []

    class FakeClient:
        def get_json(self, path, *, query=None, request_id=None):
            calls.append((path, dict(query or {}), request_id))
            return {
                "ok": True,
                "version": "M8",
                "data": MARKET_OBSERVATION_MANAGER_DATA,
            }

    monkeypatch.setattr(
        manager_page_compare.ManagerApiClient,
        "from_env",
        lambda: FakeClient(),
    )

    payload = manager_page_compare.market_observation_manager_page_payload(
        chain_id=4663,
        protocols=["uniswap_v4"],
        include_all=True,
        chain_selection={"market_id": "dex-arbitrage-4663-uniswap-v4"},
        request_id="observation-page-1",
    )

    assert payload["ok"] is True
    assert payload["dataSource"] == "manager"
    assert payload["summary"]["candidateObservationGroupCount"] == 1
    assert payload["summary"]["fundAnchorPairObservationAssociatedPairCount"] == 2
    assert payload["summary"]["fundAnchorPairObservationAssociatedObservationPoolCount"] == 1
    assert payload["summary"]["fundAnchorPairObservationAssociatedObservationTokenCount"] == 3
    assert payload["summary"]["unmatchedPairCount"] == 1
    assert payload["summary"]["snapshotLimited"] is False
    assert payload["summary"]["usdDepthRequired"] is False
    assert payload["versions"]["observationVersion"] == 9
    assert payload["versions"]["observationChecksum"] == "sha256:observation"
    assert len(payload["candidateObservationGroups"]) == 1
    group = payload["candidateObservationGroups"][0]
    assert group["anchorSymbol"] == "USDG"
    assert group["ycZcSameSet"] is True
    assert len(group["sharedTokens"]) == 3
    assert group["candidateCombinationCount"] == "2"
    assert group["reachableCombinationCount"] == "1"
    assert group["noPoolCombinationCount"] == "1"
    assert len(group["bridgePoolObservationGroups"]) == 1
    assert group["bridgePoolObservationGroups"][0]["bridgePool"] == "pool-yz"
    assert group["observationSequences"][0]["hopCandidates"][0]["pools"][0]["poolId"] == "pool-yz"
    assert payload["chainSpecificObservationPools"][0]["symbolPair"] == "TA:AMZN"
    assert payload["chainSpecificObservationPools"][0]["hasLiquidity"] is True
    assert payload["summary"]["dataSource"] == "manager"
    assert payload["managerSnapshotCounts"]["poolSnapshotCount"] == 1
    assert payload["managerEnvelopeVersion"] == "M8"
    assert calls == [
        (
            "/v1/candidate-exchange-pools",
            {"chainId": 4663, "limit": 1000},
            "observation-page-1",
        )
    ]


def test_market_observation_manager_page_payload_rejects_scope_or_schema_failure(monkeypatch):
    class WrongChainClient:
        def get_json(self, path, *, query=None, request_id=None):
            return {
                "ok": True,
                "version": "M8",
                "data": {
                    **MARKET_OBSERVATION_MANAGER_DATA,
                    "status": {
                        **MARKET_OBSERVATION_MANAGER_DATA["status"],
                        "chainId": 46630,
                    },
                },
            }

    monkeypatch.setattr(
        manager_page_compare.ManagerApiClient,
        "from_env",
        lambda: WrongChainClient(),
    )

    payload = manager_page_compare.market_observation_manager_page_payload(
        chain_id=4663,
        protocols=["uniswap_v4"],
    )

    assert payload["ok"] is False
    assert payload["code"] == "MANAGER_OBSERVATION_SCOPE_MISMATCH"

    class MissingTokensClient:
        def get_json(self, path, *, query=None, request_id=None):
            data = dict(MARKET_OBSERVATION_MANAGER_DATA)
            data.pop("tokens")
            return {"ok": True, "version": "M8", "data": data}

    monkeypatch.setattr(
        manager_page_compare.ManagerApiClient,
        "from_env",
        lambda: MissingTokensClient(),
    )

    payload = manager_page_compare.market_observation_manager_page_payload(
        chain_id=4663,
        protocols=["uniswap_v4"],
    )

    assert payload["ok"] is False
    assert payload["code"] == "MANAGER_API_INVALID_RESPONSE"
    assert "tokens" in payload["detail"]


def test_market_observation_page_route_is_manager_only_success_and_fail_closed(monkeypatch):
    app = Flask(__name__)
    dex_surface_routes_config.register_dex_overview_routes(app)
    monkeypatch.setattr(
        dex_surface_routes_config._root,
        "selected_chain_id_from_request",
        lambda: ({"market_id": "dex-arbitrage-4663-uniswap-v4"}, 4663),
    )
    monkeypatch.setattr(
        dex_surface_routes_config._root,
        "request_dex_protocols_arg",
        lambda: ["uniswap_v4"],
    )
    monkeypatch.setattr(
        dex_surface_routes_config,
        "_protocol_not_supported_response",
        lambda chain_id, protocols: None,
    )
    monkeypatch.setattr(
        dex_surface_routes_config._root,
        "_attach_dex_strategy",
        lambda payload: {**payload, "dataSource": payload.get("dataSource")},
    )
    monkeypatch.setattr(
        dex_surface_routes_config,
        "mask_dex_surface_placeholder_symbols",
        lambda payload: payload,
    )

    runtime_state = {"fail": False}

    class FakeRuntime:
        def manager_view(self, view, limit, *, options=None):
            if runtime_state["fail"]:
                return {
                    "ok": False,
                    "source": "manager",
                    "code": "MANAGER_API_UNAVAILABLE",
                    "detail": "manager unavailable",
                    "upstreamStatus": 503,
                }
            return {
                "ok": True,
                "version": "M8",
                "view": view,
                "chainId": 4663,
                "dataSource": "rust_runtime_core",
                "data": MARKET_OBSERVATION_MANAGER_DATA,
                "runtimeCore": {"chainId": 4663},
            }

    @app.before_request
    def _bind_chain_runtime():
        g.chain_runtime = FakeRuntime()

    monkeypatch.setattr(
        manager_page_compare.ManagerApiClient,
        "from_env",
        lambda: (_ for _ in ()).throw(
            AssertionError("market observation must not use ManagerApiClient HTTP reads")
        ),
    )

    client = app.test_client()
    response = client.get(
        "/api/dex/market-observation?chain_id=4663&include_all=true"
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["dataSource"] == "manager"
    assert payload["summary"]["usdDepthRequired"] is False
    assert "managerShadow" not in payload
    assert "legacyUnaffected" not in payload

    runtime_state["fail"] = True
    failed = client.get("/api/dex/market-observation?chain_id=4663")
    assert failed.status_code == 503
    failed_payload = failed.get_json()
    assert failed_payload["error"] == "manager_read_failed"
    assert failed_payload["dataSource"] == "manager"
    assert failed_payload["managerDetail"]["code"] == "MANAGER_API_UNAVAILABLE"


def test_market_observation_refresh_is_runtime_core_command_only(monkeypatch):
    app = Flask(__name__)
    dex_surface_routes_config.register_dex_overview_routes(app)
    monkeypatch.setattr(
        dex_surface_routes_config._root,
        "selected_chain_id_from_request",
        lambda: ({"market_id": "dex-arbitrage-4663-uniswap-v4"}, 4663),
    )
    monkeypatch.setattr(
        dex_surface_routes_config._root,
        "request_dex_protocols_arg",
        lambda: ["uniswap_v4"],
    )
    monkeypatch.setattr(
        dex_surface_routes_config,
        "_protocol_not_supported_response",
        lambda chain_id, protocols: None,
    )
    monkeypatch.setattr(
        dex_surface_routes_config._root,
        "_attach_dex_strategy",
        lambda payload: payload,
    )
    monkeypatch.setattr(
        dex_surface_routes_config,
        "mask_dex_surface_placeholder_symbols",
        lambda payload: payload,
    )
    for symbol in (
        "_clear_dex_surface_cache",
        "_dex_surface_payload_for_chain",
        "_finalize_market_observation_payload",
        "_market_observation_page_payload",
        "dex_market_observation_payload",
    ):
        monkeypatch.setattr(
            dex_surface_routes_config._root,
            symbol,
            lambda *args, **kwargs: (_ for _ in ()).throw(
                AssertionError(f"market observation refresh must not call {symbol}")
            ),
        )

    command_calls = []
    view_calls = []

    class FakeRuntime:
        def execute_command(
            self,
            command,
            *,
            request_id,
            idempotency_key,
            dry_run,
        ):
            command_calls.append((command, request_id, idempotency_key, dry_run))
            return {
                "ok": True,
                "accepted": True,
                "command": "rebuild_candidate_exchange_pools",
                "version": "rust_runtime_core_v1",
                "chainId": 4663,
                "dataSource": "rust_runtime_core",
                "dryRun": False,
                "idempotentReplay": False,
                "candidateEdgeVersion": 7,
                "observationVersion": 9,
                "materializedChecksum": "sha256:observation",
                "changedRows": 11,
                "consumersNeedRebuild": True,
            }

        def manager_view(self, view, limit, *, options=None):
            view_calls.append((view, limit, options))
            return {
                "ok": True,
                "version": "M8",
                "view": view,
                "chainId": 4663,
                "dataSource": "rust_runtime_core",
                "data": MARKET_OBSERVATION_MANAGER_DATA,
                "runtimeCore": {"chainId": 4663},
            }

    @app.before_request
    def _bind_chain_runtime():
        g.chain_runtime = FakeRuntime()

    response = app.test_client().post(
        "/api/dex/market-observation/refresh?chain_id=4663&full=1",
        json={"full": True, "dryRun": False},
        headers={
            "X-Request-Id": "market-refresh-request",
            "Idempotency-Key": "market-refresh-idempotency",
        },
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["dataSource"] == "manager"
    assert payload["refresh_state"]["command"] == "rebuild_candidate_exchange_pools"
    assert payload["refresh_state"]["dryRun"] is False
    assert payload["refresh_state"]["changedRows"] == 11
    assert payload["refresh_state"]["observationVersion"] == 9
    assert command_calls == [
        (
            "rebuild_candidate_exchange_pools",
            "market-refresh-request",
            "market-refresh-idempotency",
            False,
        )
    ]
    assert view_calls == [
        ("candidate_exchange", 1000, None),
    ]


def test_market_observation_refresh_dry_run_has_no_legacy_fallback(monkeypatch):
    app = Flask(__name__)
    dex_surface_routes_config.register_dex_overview_routes(app)
    monkeypatch.setattr(
        dex_surface_routes_config._root,
        "selected_chain_id_from_request",
        lambda: ({"market_id": "dex-arbitrage-4663-uniswap-v4"}, 4663),
    )
    monkeypatch.setattr(
        dex_surface_routes_config._root,
        "request_dex_protocols_arg",
        lambda: ["uniswap_v4"],
    )
    monkeypatch.setattr(
        dex_surface_routes_config,
        "_protocol_not_supported_response",
        lambda chain_id, protocols: None,
    )
    monkeypatch.setattr(
        dex_surface_routes_config._root,
        "_attach_dex_strategy",
        lambda payload: payload,
    )
    monkeypatch.setattr(
        dex_surface_routes_config,
        "mask_dex_surface_placeholder_symbols",
        lambda payload: payload,
    )

    class FakeRuntime:
        def execute_command(
            self,
            command,
            *,
            request_id,
            idempotency_key,
            dry_run,
        ):
            assert command == "rebuild_candidate_exchange_pools"
            assert dry_run is True
            return {
                "ok": True,
                "command": command,
                "version": "rust_runtime_core_v1",
                "chainId": 4663,
                "dataSource": "rust_runtime_core",
                "dryRun": True,
                "idempotentReplay": False,
                "candidateEdgeVersion": 7,
                "observationVersion": 9,
                "materializedChecksum": "sha256:observation",
                "changedRows": 0,
                "consumersNeedRebuild": False,
            }

        def manager_view(self, view, limit, *, options=None):
            return {
                "ok": True,
                "version": "M8",
                "view": view,
                "chainId": 4663,
                "dataSource": "rust_runtime_core",
                "data": MARKET_OBSERVATION_MANAGER_DATA,
                "runtimeCore": {"chainId": 4663},
            }

    @app.before_request
    def _bind_chain_runtime():
        g.chain_runtime = FakeRuntime()

    response = app.test_client().post(
        "/api/dex/market-observation/refresh?chain_id=4663",
        json={"dryRun": True},
        headers={
            "X-Request-Id": "market-refresh-dry-request",
            "Idempotency-Key": "market-refresh-dry-idempotency",
        },
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["refresh_state"]["dryRun"] is True
    assert payload["refresh_state"]["changedRows"] == 0
    assert "预演" in payload["refresh_state"]["message"]


def test_market_observation_refresh_requires_explicit_write_contract(monkeypatch):
    app = Flask(__name__)
    dex_surface_routes_config.register_dex_overview_routes(app)
    monkeypatch.setattr(
        dex_surface_routes_config._root,
        "selected_chain_id_from_request",
        lambda: ({"market_id": "dex-arbitrage-4663-uniswap-v4"}, 4663),
    )
    monkeypatch.setattr(
        dex_surface_routes_config._root,
        "request_dex_protocols_arg",
        lambda: ["uniswap_v4"],
    )
    monkeypatch.setattr(
        dex_surface_routes_config,
        "_protocol_not_supported_response",
        lambda chain_id, protocols: None,
    )
    monkeypatch.setattr(
        dex_surface_routes_config,
        "candidate_exchange_refresh_manager_payload",
        lambda **kwargs: (_ for _ in ()).throw(
            AssertionError("invalid write contract must fail before RuntimeCore")
        ),
    )

    client = app.test_client()
    missing_dry_run = client.post(
        "/api/dex/market-observation/refresh?chain_id=4663",
        headers={
            "X-Request-Id": "market-request",
            "Idempotency-Key": "market-idempotency",
        },
    )
    assert missing_dry_run.status_code == 400
    assert missing_dry_run.get_json()["error"] == "dry_run_required"

    missing_request_id = client.post(
        "/api/dex/market-observation/refresh?chain_id=4663",
        json={"dryRun": False},
        headers={"Idempotency-Key": "market-idempotency"},
    )
    assert missing_request_id.status_code == 400
    assert missing_request_id.get_json()["error"] == "request_id_required"

    missing_idempotency = client.post(
        "/api/dex/market-observation/refresh?chain_id=4663",
        json={"dryRun": False},
        headers={"X-Request-Id": "market-request"},
    )
    assert missing_idempotency.status_code == 400
    assert missing_idempotency.get_json()["error"] == "idempotency_key_required"


def test_submission_manager_shadow_reports_review_and_broadcast_counts(monkeypatch):
    calls = []

    class FakeClient:
        def get_json(self, path, *, query=None, request_id=None):
            calls.append((path, dict(query or {}), request_id))
            if path == "/v1/reviews":
                return {
                    "ok": True,
                    "version": "M8",
                    "data": {
                        "status": {
                            "reviewVersion": 13,
                            "materializedChecksum": "sha256:review",
                            "closedLoopVersion": 11,
                            "closedLoopChecksum": "sha256:loops",
                            "candidateEdgeVersion": 7,
                            "candidateEdgeChecksum": "sha256:candidate",
                            "observationVersion": 9,
                            "observationChecksum": "sha256:observation",
                            "poolScopeChecksum": "sha256:scope",
                            "loopCount": 8,
                            "reviewedCount": 7,
                            "completedCount": 4,
                            "dataIncompleteCount": 1,
                            "notExecutableCount": 2,
                            "failedCount": 1,
                            "broadcastRequestCount": 3,
                        },
                        "reviews": [{}, {}],
                        "diagnostics": [{}],
                        "broadcastRequests": [{}, {}, {}],
                    },
                }
            assert path == "/v1/broadcasts"
            return {
                "ok": True,
                "version": "M8",
                "data": {
                    "status": {
                        "broadcastVersion": 15,
                        "materializedChecksum": "sha256:broadcast",
                        "reviewVersion": 13,
                        "reviewChecksum": "sha256:review",
                        "closedLoopVersion": 11,
                        "closedLoopChecksum": "sha256:loops",
                        "requestCount": 3,
                        "attemptCount": 2,
                        "pendingCount": 0,
                        "successCount": 1,
                        "failedCount": 1,
                        "terminalCount": 2,
                    },
                    "attempts": [{}, {}],
                    "diagnostics": [{}],
                },
            }

    monkeypatch.setenv("MANAGER_UI_DATA_SOURCE", "manager")
    monkeypatch.setattr(
        manager_page_compare.ManagerApiClient,
        "from_env",
        lambda: FakeClient(),
    )

    payload = manager_page_compare.submission_manager_shadow_payload(
        {
            "summary": {
                "attemptCount": 5,
                "successCount": 2,
                "failedCount": 1,
                "blockedCount": 1,
                "deferredReviewCount": 1,
            }
        },
        chain_id=4663,
        request_id="submission-shadow-1",
    )

    assert payload["ok"] is True
    assert payload["partial"] is False
    assert payload["review"]["reviewVersion"] == 13
    assert payload["review"]["statusCounts"]["dataIncompleteCount"] == 1
    assert payload["review"]["snapshotCounts"]["reviewSnapshotCount"] == 2
    assert payload["broadcast"]["broadcastVersion"] == 15
    assert payload["broadcast"]["statusCounts"]["terminalCount"] == 2
    assert payload["broadcast"]["snapshotCounts"]["attemptSnapshotCount"] == 2
    assert payload["comparisonReadyForFieldSwitch"] is False
    assert calls == [
        (
            "/v1/reviews",
            {"chainId": 4663, "limit": 1000},
            "submission-shadow-1",
        ),
        (
            "/v1/broadcasts",
            {"chainId": 4663, "limit": 1000},
            "submission-shadow-1",
        ),
    ]


def test_submission_manager_shadow_reports_partial_failure(monkeypatch):
    class FakeClient:
        def get_json(self, path, *, query=None, request_id=None):
            if path == "/v1/reviews":
                raise ManagerApiClientError(
                    "MANAGER_API_UNAVAILABLE",
                    "review unavailable",
                    status=503,
                )
            return {
                "ok": True,
                "version": "M8",
                "data": {
                    "status": {
                        "broadcastVersion": 15,
                        "requestCount": 0,
                        "attemptCount": 0,
                        "pendingCount": 0,
                        "successCount": 0,
                        "failedCount": 0,
                        "terminalCount": 0,
                    },
                    "attempts": [],
                    "diagnostics": [],
                },
            }

    monkeypatch.setenv("MANAGER_UI_DATA_SOURCE", "manager")
    monkeypatch.setattr(
        manager_page_compare.ManagerApiClient,
        "from_env",
        lambda: FakeClient(),
    )

    payload = manager_page_compare.submission_manager_shadow_payload(
        {"summary": {"attemptCount": 0}},
        chain_id=4663,
    )

    assert payload["ok"] is False
    assert payload["partial"] is True
    assert payload["legacyUnaffected"] is True
    assert payload["review"]["ok"] is False
    assert payload["review"]["code"] == "MANAGER_API_UNAVAILABLE"
    assert payload["broadcast"]["ok"] is True


def test_submission_detail_manager_shadow_queries_one_attempt(monkeypatch):
    calls = []

    class FakeClient:
        def get_json(self, path, *, query=None, request_id=None):
            calls.append((path, dict(query or {}), request_id))
            return {
                "ok": True,
                "version": "M8",
                "data": {
                    "status": {
                        "enabled": True,
                        "network": "robinhood",
                        "chainId": 4663,
                        "protocol": "uniswap_v4",
                        "scopeKey": "robinhood:4663",
                        "broadcastVersion": 15,
                        "materializedChecksum": "sha256:broadcast",
                        "reviewVersion": 13,
                        "reviewChecksum": "sha256:review",
                        "closedLoopVersion": 11,
                        "closedLoopChecksum": "sha256:loops",
                        "attemptCount": 1,
                        "pendingCount": 0,
                        "successCount": 0,
                        "failedCount": 1,
                        "terminalCount": 1,
                    },
                    "attempts": [
                        {
                            "attemptId": "42",
                            "loopKey": "loop-42",
                            "pathText": "USDG -> WETH -> USDG",
                            "pathAddresses": ["0xusdg", "0xweth", "0xusdg"],
                            "pathSymbols": ["USDG", "WETH", "USDG"],
                            "poolPathKey": "pool-path-42",
                            "poolIds": ["pool-1", "pool-2"],
                            "signedPnl": {
                                "amount": "12",
                                "symbol": "USDG",
                                "unit": "token",
                            },
                            "signerAddress": "0xsigner",
                            "nonce": 7,
                            "gasLimit": 500000,
                            "rpcUrl": "https://rpc.example",
                            "txHash": "0xabc",
                            "status": "failed",
                            "statusReason": "revert",
                            "receipt": {"status": "0x0"},
                            "evidence": {"error": "revert"},
                            "createdAt": "2026-11-05T00:00:00Z",
                            "updatedAt": "2026-11-05T00:01:00Z",
                            "terminalAt": "2026-11-05T00:01:00Z",
                        }
                    ],
                    "diagnostics": [
                        {"attemptId": "42", "diagnosticType": "broadcast_failed"},
                        {"attemptId": "other", "diagnosticType": "broadcast_failed"},
                    ],
                },
            }

    monkeypatch.setenv("MANAGER_UI_DATA_SOURCE", "manager")
    monkeypatch.setattr(
        manager_page_compare.ManagerApiClient,
        "from_env",
        lambda: FakeClient(),
    )

    payload = manager_page_compare.submission_detail_manager_payload(
        {"id": 42, "status": "failed", "tx_hash": "0xabc"},
        chain_id=4663,
        attempt_id=42,
        request_id="submission-detail-1",
    )

    assert payload["ok"] is True
    assert payload["dataSource"] == "manager"
    assert payload["found"] is True
    assert payload["attemptSnapshotCount"] == 1
    assert payload["diagnosticSnapshotCount"] == 1
    assert payload["attempt"]["id"] == "42"
    assert payload["attempt"]["network"] == "robinhood"
    assert payload["attempt"]["tx_hash"] == "0xabc"
    assert payload["attempt"]["signedPnl"]["symbol"] == "USDG"
    assert payload["managerAttempt"]["status"] == "failed"
    assert payload["diagnostics"] == [
        {"attemptId": "42", "diagnosticType": "broadcast_failed"}
    ]
    assert calls == [
        (
            "/v1/broadcasts",
            {"chainId": 4663, "attemptId": "42", "limit": 1},
            "submission-detail-1",
        )
    ]


def test_submission_detail_route_uses_manager_without_sqlite_unconditionally(
    monkeypatch,
):
    app = Flask(__name__)
    dex_surface_routes_ops.register_dex_submission_routes(app)
    monkeypatch.setattr(
        dex_surface_routes_ops._root,
        "selected_chain_id_from_request",
        lambda: ({"market_id": "dex-arbitrage-4663-uniswap-v4"}, 4663),
    )
    monkeypatch.setattr(
        dex_surface_routes_ops,
        "_submission_database_url",
        lambda: (_ for _ in ()).throw(AssertionError("legacy sqlite must not be read")),
    )
    calls = []
    monkeypatch.setattr(
        dex_surface_routes_ops,
        "submission_detail_manager_payload",
        lambda payload, chain_id, attempt_id, request_id=None: calls.append(
            {"chainId": chain_id, "attemptId": attempt_id, "requestId": request_id}
        )
        or {
            "ok": True,
            "dataSource": "manager",
            "found": True,
            "attempt": {"id": "42", "status": "failed", "tx_hash": "0xabc"},
        },
    )

    response = app.test_client().get(
        "/api/dex/submissions/detail?chain_id=4663&attempt_id=att-42"
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["dataSource"] == "manager"
    assert payload["attempt"]["id"] == "42"
    assert calls == [{"chainId": 4663, "attemptId": "att-42", "requestId": None}]


def test_submission_detail_route_manager_error_does_not_fallback_to_sqlite(
    monkeypatch,
):
    app = Flask(__name__)
    dex_surface_routes_ops.register_dex_submission_routes(app)
    monkeypatch.setattr(
        dex_surface_routes_ops._root,
        "selected_chain_id_from_request",
        lambda: ({"market_id": "dex-arbitrage-4663-uniswap-v4"}, 4663),
    )
    monkeypatch.setattr(
        dex_surface_routes_ops,
        "_submission_database_url",
        lambda: (_ for _ in ()).throw(AssertionError("legacy sqlite must not be read")),
    )
    monkeypatch.setattr(
        dex_surface_routes_ops,
        "submission_detail_manager_payload",
        lambda *args, **kwargs: {
            "ok": False,
            "code": "MANAGER_API_UNAVAILABLE",
            "upstreamStatus": 503,
        },
    )

    response = app.test_client().get(
        "/api/dex/submissions/detail?chain_id=4663&attempt_id=42"
    )

    assert response.status_code == 503
    payload = response.get_json()
    assert payload["dataSource"] == "manager"
    assert payload["error"] == "manager_read_failed"


def test_submission_detail_route_uses_manager_even_when_legacy_configured(
    monkeypatch,
):
    app = Flask(__name__)
    dex_surface_routes_ops.register_dex_submission_routes(app)
    monkeypatch.setenv("MANAGER_UI_DATA_SOURCE", "legacy")
    monkeypatch.setattr(
        dex_surface_routes_ops._root,
        "selected_chain_id_from_request",
        lambda: ({"market_id": "dex-arbitrage-4663-uniswap-v4"}, 4663),
    )
    monkeypatch.setattr(
        dex_surface_routes_ops,
        "_submission_database_url",
        lambda: (_ for _ in ()).throw(AssertionError("legacy sqlite must not be read")),
    )
    calls = []
    monkeypatch.setattr(
        dex_surface_routes_ops,
        "submission_detail_manager_payload",
        lambda payload, chain_id, attempt_id, request_id=None: calls.append(
            {"chainId": chain_id, "attemptId": attempt_id, "requestId": request_id}
        )
        or {
            "ok": True,
            "dataSource": "manager",
            "found": True,
            "attempt": {"id": attempt_id, "status": "failed", "tx_hash": "0xabc"},
        },
    )

    response = app.test_client().get(
        "/api/dex/submissions/detail?chain_id=4663&attempt_id=42"
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["dataSource"] == "manager"
    assert payload["attempt"]["id"] == "42"
    assert calls == [{"chainId": 4663, "attemptId": "42", "requestId": None}]


def test_submission_execution_manager_payload_delegates_filter_and_pagination(
    monkeypatch,
):
    calls = []

    class FakeClient:
        def get_json(self, path, *, query=None, request_id=None):
            calls.append((path, dict(query or {}), request_id))
            return {
                "ok": True,
                "version": "M8",
                "data": {
                    "status": {
                        "enabled": True,
                        "network": "robinhood",
                        "chainId": 4663,
                        "protocol": "uniswap_v4",
                        "scopeKey": "robinhood:4663",
                        "broadcastVersion": 15,
                        "materializedChecksum": "sha256:broadcast",
                        "reviewVersion": 13,
                        "reviewChecksum": "sha256:review",
                        "closedLoopVersion": 11,
                        "closedLoopChecksum": "sha256:loops",
                        "attemptCount": 3,
                        "pendingCount": 0,
                        "successCount": 1,
                        "failedCount": 2,
                        "terminalCount": 3,
                    },
                    "attempts": [
                        {
                            "attemptId": "21",
                            "pathText": "USDG -> WETH -> USDG",
                            "pathAddresses": ["0xusdg", "0xweth", "0xusdg"],
                            "pathSymbols": ["USDG", "WETH", "USDG"],
                            "signedPnl": {
                                "amount": "1.5",
                                "symbol": "USDG",
                                "unit": "token",
                            },
                            "nonce": 9,
                            "rpcUrl": "https://rpc.example",
                            "txHash": "0xsuccess",
                            "status": "success",
                            "receipt": {"status": "0x1"},
                            "evidence": {},
                            "createdAt": "2026-11-05T00:00:00Z",
                            "updatedAt": "2026-11-05T00:01:00Z",
                        }
                    ],
                    "diagnostics": [],
                },
            }

    monkeypatch.setenv("MANAGER_UI_DATA_SOURCE", "manager")
    monkeypatch.setattr(
        manager_page_compare.ManagerApiClient,
        "from_env",
        lambda: FakeClient(),
    )

    payload = manager_page_compare.submission_execution_manager_payload(
        chain_id=4663,
        status="success",
        mode="dex_submission_execution_success_records",
        page=2,
        page_size=20,
        chain_selection={"market_id": "dex-arbitrage-4663-uniswap-v4"},
        request_id="execution-page-2",
    )

    assert payload["ok"] is True
    assert payload["dataSource"] == "manager"
    assert payload["summary"]["attemptCount"] == 1
    assert payload["summary"]["successCount"] == 1
    assert payload["summary"]["failedCount"] == 0
    assert payload["pagination"] == {
        "page": 2,
        "page_size": 20,
        "total": 1,
        "total_pages": 1,
    }
    assert payload["attempts"][0]["tx_hash"] == "0xsuccess"
    assert calls == [
        (
            "/v1/broadcasts",
            {
                "chainId": 4663,
                "status": "success",
                "offset": 20,
                "limit": 20,
            },
            "execution-page-2",
        )
    ]


def test_submission_overview_manager_payload_maps_counts_categories_and_page(
    monkeypatch,
):
    calls = []

    class FakeClient:
        def get_json(self, path, *, query=None, request_id=None):
            query = dict(query or {})
            calls.append((path, query, request_id))
            if path == "/v1/reviews":
                return {
                    "ok": True,
                    "version": "M8",
                    "data": {
                        "status": {
                            "enabled": True,
                            "network": "robinhood",
                            "chainId": 4663,
                            "reviewVersion": 13,
                            "materializedChecksum": "sha256:review",
                            "closedLoopVersion": 11,
                            "closedLoopChecksum": "sha256:loops",
                            "loopCount": 5,
                            "reviewedCount": 4,
                            "completedCount": 2,
                            "failedCount": 1,
                            "notExecutableCount": 1,
                            "broadcastRequestCount": 3,
                        },
                        "categoryCounts": {
                            "confirmedNoProfit": 1,
                        },
                        "reviews": [],
                        "diagnostics": [],
                    },
                }
            attempts = [
                {
                    "attemptId": "attempt-success",
                    "pathText": "USDG -> WETH -> USDG",
                    "status": "success",
                    "signedPnl": {
                        "amount": "1.5",
                        "symbol": "USDG",
                        "unit": "token",
                    },
                    "txHash": "0xsuccess",
                    "createdAt": "2026-11-05T00:03:00Z",
                },
                {
                    "attemptId": "attempt-blocked",
                    "pathText": "USDG -> AMZN -> USDG",
                    "status": "failed",
                    "statusReason": "route is not executable: non_positive_pnl",
                    "signedPnl": {
                        "amount": "0",
                        "symbol": "USDG",
                        "unit": "token",
                    },
                    "createdAt": "2026-11-05T00:02:00Z",
                },
                {
                    "attemptId": "attempt-rpc",
                    "pathText": "USDG -> USDE -> USDG",
                    "status": "failed",
                    "statusReason": "RPC unavailable",
                    "signedPnl": {
                        "amount": "2",
                        "symbol": "USDG",
                        "unit": "token",
                    },
                    "createdAt": "2026-11-05T00:01:00Z",
                },
            ]
            if query.get("status") == "failed":
                attempts = [row for row in attempts if row["status"] == "failed"]
            return {
                "ok": True,
                "version": "M8",
                "data": {
                    "status": {
                        "enabled": True,
                        "network": "robinhood",
                        "chainId": 4663,
                        "broadcastVersion": 15,
                        "materializedChecksum": "sha256:broadcast",
                        "reviewVersion": 13,
                        "reviewChecksum": "sha256:review",
                        "closedLoopVersion": 11,
                        "closedLoopChecksum": "sha256:loops",
                        "attemptCount": 3,
                        "pendingCount": 0,
                        "successCount": 1,
                        "failedCount": 2,
                        "terminalCount": 3,
                    },
                    "attempts": attempts,
                    "diagnostics": [
                        {
                            "attemptId": "attempt-rpc",
                            "diagnosticType": "rpc_unavailable",
                            "diagnosticCode": "upstream_timeout",
                            "message": "RPC request timed out",
                        }
                    ],
                },
            }

    monkeypatch.setattr(
        manager_page_compare.ManagerApiClient,
        "from_env",
        lambda: FakeClient(),
    )

    overview = manager_page_compare.submission_overview_manager_payload(
        chain_id=4663,
        mode="dex_submission_review",
        failure_category=None,
        page=1,
        page_size=2,
        chain_selection={"market_id": "dex-arbitrage-4663-uniswap-v4"},
        request_id="submission-overview-1",
    )

    assert overview["ok"] is True
    assert overview["dataSource"] == "manager"
    assert overview["summary"]["attemptCount"] == 3
    assert overview["summary"]["successCount"] == 1
    assert overview["summary"]["failedCount"] == 2
    assert overview["summary"]["blockedCount"] == 2
    assert overview["pagination"] == {
        "page": 1,
        "page_size": 2,
        "total": 3,
        "total_pages": 2,
    }
    assert overview["routeFramework"]["routeFrameworkSource"] == "manager_broadcast"
    assert overview["routeFramework"]["topRouteText"] == "USDG -> WETH -> USDG"
    assert overview["loopStateSummary"]["missingBroadcast"] == 0
    assert overview["managerStatus"]["reviewChecksum"] == "sha256:review"
    assert overview["managerStatus"]["versionsConsistent"] is True
    assert calls == [
        (
            "/v1/reviews",
            {"chainId": 4663, "offset": 0, "limit": 1},
            "submission-overview-1",
        ),
        (
            "/v1/broadcasts",
            {"chainId": 4663, "offset": 0, "limit": 2},
            "submission-overview-1",
        ),
    ]

    calls.clear()
    failures = manager_page_compare.submission_overview_manager_payload(
        chain_id=4663,
        mode="dex_submission_failed_review",
        failure_category="rpc",
        page=1,
        page_size=1,
        chain_selection={},
        request_id="submission-failed-rpc",
    )

    assert failures["ok"] is True
    assert failures["categoryCounts"]["rpc"] == 1
    assert failures["categoryCounts"]["blocked"] == 1
    assert failures["pagination"]["total"] == 1
    assert failures["attempts"][0]["id"] == "attempt-rpc"
    assert failures["attempts"][0]["failureCategory"] == "rpc"
    assert failures["attempts"][0]["diagnostics"][0]["diagnosticType"] == (
        "rpc_unavailable"
    )
    assert calls[1] == (
        "/v1/broadcasts",
        {
            "chainId": 4663,
            "offset": 0,
            "limit": manager_page_compare.SUBMISSION_SNAPSHOT_LIMIT,
            "status": "failed",
        },
        "submission-failed-rpc",
    )


def test_broadcast_failure_manager_payload_maps_failed_attempt_and_diagnostic(
    monkeypatch,
):
    calls = []

    class FakeClient:
        def get_json(self, path, *, query=None, request_id=None):
            calls.append((path, dict(query or {}), request_id))
            return {
                "ok": True,
                "version": "M8",
                "data": {
                    "status": {
                        "enabled": True,
                        "network": "robinhood",
                        "chainId": 4663,
                        "broadcastVersion": 15,
                        "materializedChecksum": "sha256:broadcast",
                        "failedCount": 1,
                    },
                    "attempts": [
                        {
                            "attemptId": "attempt-rpc",
                            "loopKey": "aaa|bbb|ccc",
                            "pathText": "AAA -> BBB -> CCC -> AAA",
                            "status": "failed",
                            "statusReason": "RPC request timed out",
                            "signedPnl": {
                                "amount": "0x0493e0",
                                "currency": "USDG",
                                "source": "review_manager",
                            },
                            "nonce": 19,
                            "rpcUrl": "https://rpc.example",
                            "rpcName": "primary",
                            "txHash": "0xdeadbeef",
                            "createdAt": "2026-11-05T00:00:00Z",
                        }
                    ],
                    "diagnostics": [
                        {
                            "attemptId": "attempt-rpc",
                            "diagnosticType": "rpc_unavailable",
                            "diagnosticCode": "upstream_timeout",
                            "message": "RPC request timed out",
                            "observedAt": "2026-11-05T00:00:01Z",
                        }
                    ],
                },
            }

    monkeypatch.setattr(
        manager_page_compare.ManagerApiClient,
        "from_env",
        lambda: FakeClient(),
    )

    payload = manager_page_compare.broadcast_failure_manager_payload(
        chain_id=4663,
        limit=20,
        chain_selection={"market_id": "dex-arbitrage-4663-uniswap-v4"},
        request_id="broadcast-failure-1",
    )

    assert payload["ok"] is True
    assert payload["dataSource"] == "manager"
    assert payload["available"] is True
    assert payload["recordCount"] == 1
    assert payload["records"][0]["attemptFingerprint"] == "attempt-rpc"
    assert payload["records"][0]["groupKey"] == "aaa|bbb|ccc"
    assert payload["records"][0]["broadcastError"] == "RPC request timed out"
    assert payload["records"][0]["selectedProfitRaw"] == "0x0493e0"
    assert payload["managerStatus"]["broadcastChecksum"] == "sha256:broadcast"
    assert calls == [
        (
            "/v1/broadcasts",
            {
                "chainId": 4663,
                "status": "failed",
                "offset": 0,
                "limit": 20,
            },
            "broadcast-failure-1",
        )
    ]


def test_broadcast_failure_manager_payload_preserves_zero_limit(monkeypatch):
    calls = []

    class FakeClient:
        def get_json(self, path, *, query=None, request_id=None):
            calls.append((path, dict(query or {}), request_id))
            return {
                "ok": True,
                "data": {
                    "status": {"enabled": True, "chainId": 4663},
                    "attempts": [{"attemptId": "attempt-rpc", "status": "failed"}],
                    "diagnostics": [],
                },
            }

    monkeypatch.setattr(
        manager_page_compare.ManagerApiClient,
        "from_env",
        lambda: FakeClient(),
    )

    payload = manager_page_compare.broadcast_failure_manager_payload(
        chain_id=4663,
        limit=0,
        request_id="broadcast-failure-zero",
    )

    assert payload["ok"] is True
    assert payload["available"] is True
    assert payload["records"] == []
    assert payload["recordCount"] == 0
    assert calls[0][1]["limit"] == 1


def test_submission_clear_manager_payload_delegates_to_runtime_core():
    calls = []

    class FakeRuntime:
        def execute_command(
            self,
            command,
            *,
            request_id,
            idempotency_key,
            dry_run,
        ):
            calls.append((command, request_id, idempotency_key, dry_run))
            return {
                "ok": True,
                "accepted": True,
                "command": "clear_broadcasts",
                "version": "rust_runtime_core_v1",
                "chainId": 4663,
                "dataSource": "rust_runtime_core",
                "clearedAttempts": 2,
                "remainingAttempts": 0,
                "clearedDiagnostics": 3,
                "remainingDiagnostics": 0,
                "dryRun": False,
                "idempotentReplay": False,
            }

    app = Flask(__name__)
    with app.test_request_context():
        g.chain_runtime = FakeRuntime()
        payload = manager_page_compare.submission_clear_manager_payload(
            chain_id=4663,
            dry_run=False,
            request_id="clear-request-1",
            idempotency_key="clear-idem-1",
        )

    assert payload["ok"] is True
    assert payload["dataSource"] == "manager"
    assert payload["runtimeDataSource"] == "rust_runtime_core"
    assert payload["cleared"] == 2
    assert payload["remaining"] == 0
    assert payload["clearedDiagnostics"] == 3
    assert payload["dryRun"] is False
    assert calls == [
        (
            "clear_broadcasts",
            "clear-request-1",
            "clear-idem-1",
            False,
        )
    ]


def test_candidate_edge_refresh_manager_payload_delegates_to_runtime_core():
    calls = []

    class FakeRuntime:
        def execute_command(
            self,
            command,
            *,
            request_id,
            idempotency_key,
            dry_run,
        ):
            calls.append((command, request_id, idempotency_key, dry_run))
            return {
                "ok": True,
                "accepted": True,
                "command": "rebuild_candidate_edges",
                "version": "rust_runtime_core_v1",
                "chainId": 4663,
                "dataSource": "rust_runtime_core",
                "candidateVersion": 12,
                "materializedChecksum": "sha256:candidate",
                "changedRows": 7,
                "consumersNeedRebuild": True,
                "dryRun": False,
                "idempotentReplay": False,
            }

    app = Flask(__name__)
    with app.test_request_context():
        g.chain_runtime = FakeRuntime()
        payload = manager_page_compare.candidate_edge_refresh_manager_payload(
            chain_id=4663,
            dry_run=False,
            request_id="candidate-request-1",
            idempotency_key="candidate-idem-1",
        )

    assert payload["ok"] is True
    assert payload["dataSource"] == "manager"
    assert payload["runtimeDataSource"] == "rust_runtime_core"
    assert payload["candidateVersion"] == 12
    assert payload["materializedChecksum"] == "sha256:candidate"
    assert payload["changedRows"] == 7
    assert payload["consumersNeedRebuild"] is True
    assert payload["dryRun"] is False
    assert calls == [
        (
            "rebuild_candidate_edges",
            "candidate-request-1",
            "candidate-idem-1",
            False,
        )
    ]


def test_candidate_edge_refresh_manager_payload_preserves_runtime_error():
    app = Flask(__name__)

    class FakeRuntime:
        def execute_command(self, command, **kwargs):
            return {
                "ok": False,
                "command": command,
                "code": "CANDIDATE_AUTHORITY_WRITE_DENIED",
                "detail": "candidate manager authority is required",
                "upstreamStatus": 403,
                "dataSource": "rust_runtime_core",
            }

    with app.test_request_context():
        g.chain_runtime = FakeRuntime()
        payload = manager_page_compare.candidate_edge_refresh_manager_payload(
            chain_id=4663,
            dry_run=False,
            request_id="candidate-request-denied",
            idempotency_key="candidate-idem-denied",
        )

    assert payload["ok"] is False
    assert payload["code"] == "CANDIDATE_AUTHORITY_WRITE_DENIED"
    assert payload["upstreamStatus"] == 403


def test_candidate_exchange_refresh_manager_payload_delegates_to_runtime_core():
    calls = []

    class FakeRuntime:
        def execute_command(
            self,
            command,
            *,
            request_id,
            idempotency_key,
            dry_run,
        ):
            calls.append((command, request_id, idempotency_key, dry_run))
            return {
                "ok": True,
                "accepted": True,
                "command": "rebuild_candidate_exchange_pools",
                "version": "rust_runtime_core_v1",
                "chainId": 4663,
                "dataSource": "rust_runtime_core",
                "dryRun": False,
                "idempotentReplay": False,
                "candidateEdgeVersion": 12,
                "candidateEdgeChecksum": "sha256:candidate",
                "poolScopeChecksum": "sha256:scope",
                "anchorCount": 1,
                "tokenCount": 4,
                "directionCount": 12,
                "poolCount": 3,
                "observationVersion": 8,
                "materializedChecksum": "sha256:observation",
                "changedRows": 19,
                "consumersNeedRebuild": True,
            }

    app = Flask(__name__)
    with app.test_request_context():
        g.chain_runtime = FakeRuntime()
        payload = manager_page_compare.candidate_exchange_refresh_manager_payload(
            chain_id=4663,
            dry_run=False,
            request_id="exchange-request-1",
            idempotency_key="exchange-idem-1",
        )

    assert payload["ok"] is True
    assert payload["dataSource"] == "manager"
    assert payload["runtimeDataSource"] == "rust_runtime_core"
    assert payload["candidateEdgeVersion"] == 12
    assert payload["observationVersion"] == 8
    assert payload["materializedChecksum"] == "sha256:observation"
    assert payload["changedRows"] == 19
    assert payload["consumersNeedRebuild"] is True
    assert payload["dryRun"] is False
    assert calls == [
        (
            "rebuild_candidate_exchange_pools",
            "exchange-request-1",
            "exchange-idem-1",
            False,
        )
    ]


def test_candidate_exchange_refresh_manager_payload_preserves_runtime_error():
    app = Flask(__name__)

    class FakeRuntime:
        def execute_command(self, command, **kwargs):
            return {
                "ok": False,
                "command": command,
                "code": "CANDIDATE_EXCHANGE_AUTHORITY_WRITE_DENIED",
                "detail": "candidate exchange manager authority is required",
                "upstreamStatus": 403,
                "dataSource": "rust_runtime_core",
            }

    with app.test_request_context():
        g.chain_runtime = FakeRuntime()
        payload = manager_page_compare.candidate_exchange_refresh_manager_payload(
            chain_id=4663,
            dry_run=False,
            request_id="exchange-request-denied",
            idempotency_key="exchange-idem-denied",
        )

    assert payload["ok"] is False
    assert payload["code"] == "CANDIDATE_EXCHANGE_AUTHORITY_WRITE_DENIED"
    assert payload["upstreamStatus"] == 403


def test_submission_clear_manager_payload_fails_closed_without_runtime(monkeypatch):
    monkeypatch.setattr(
        manager_page_compare.ManagerApiClient,
        "from_env",
        lambda: (_ for _ in ()).throw(
            AssertionError("submission clear must not fall back to manager_api")
        ),
    )

    payload = manager_page_compare.submission_clear_manager_payload(
        chain_id=4663,
        dry_run=False,
        request_id="clear-request-no-runtime",
        idempotency_key="clear-idem-no-runtime",
    )

    assert payload["ok"] is False
    assert payload["code"] == "RUNTIME_CORE_UNAVAILABLE"
    assert payload["upstreamStatus"] == 503


def test_submission_clear_manager_payload_preserves_runtime_error_envelope():
    app = Flask(__name__)
    calls = []

    class FakeRuntime:
        def execute_command(self, command, **kwargs):
            calls.append((command, kwargs))
            return {
                "ok": False,
                "command": command,
                "code": "BROADCAST_AUTHORITY_WRITE_DENIED",
                "detail": "manager authority is required",
                "upstreamStatus": 403,
                "dataSource": "rust_runtime_core",
            }

    with app.test_request_context():
        g.chain_runtime = FakeRuntime()
        payload = manager_page_compare.submission_clear_manager_payload(
            chain_id=4663,
            dry_run=False,
            request_id="clear-request-denied",
            idempotency_key="clear-idem-denied",
        )

    assert payload["ok"] is False
    assert payload["code"] == "BROADCAST_AUTHORITY_WRITE_DENIED"
    assert payload["upstreamStatus"] == 403
    assert calls == [
        (
            "clear_broadcasts",
            {
                "request_id": "clear-request-denied",
                "idempotency_key": "clear-idem-denied",
                "dry_run": False,
            },
        )
    ]


def test_submission_execution_routes_use_manager_without_legacy_loaders(
    monkeypatch,
):
    app = Flask(__name__)
    dex_surface_routes_ops.register_dex_submission_routes(app)
    monkeypatch.setenv("MANAGER_UI_DATA_SOURCE", "legacy")
    monkeypatch.setattr(
        dex_surface_routes_ops._root,
        "selected_chain_id_from_request",
        lambda: ({"market_id": "dex-arbitrage-4663-uniswap-v4"}, 4663),
    )
    monkeypatch.setattr(
        dex_surface_routes_ops,
        "_submission_page_args",
        lambda: (2, 20),
    )
    monkeypatch.setattr(
        dex_surface_routes_ops._root,
        "request_dex_strategy_arg",
        lambda: "closed_loop_routes",
    )
    calls = []
    monkeypatch.setattr(
        dex_surface_routes_ops,
        "submission_execution_manager_payload",
        lambda **kwargs: calls.append(kwargs)
        or {
            "ok": True,
            "dataSource": "manager",
            "mode": kwargs["mode"],
            "summary": {},
            "attempts": [],
            "pagination": {},
        },
    )

    client = app.test_client()
    routes = [
        ("/api/dex/submissions/success?chain_id=4663", "success"),
        ("/api/dex/submissions/execution-success?chain_id=4663", "success"),
        ("/api/dex/submissions/execution-failed?chain_id=4663", "failed"),
    ]
    for route, expected_status in routes:
        calls.clear()
        response = client.get(route)
        assert response.status_code == 200
        assert response.get_json()["dataSource"] == "manager"
        assert calls[0]["chain_id"] == 4663
        assert calls[0]["status"] == expected_status
        assert calls[0]["page"] == 2
        assert calls[0]["page_size"] == 20


def test_submission_execution_route_manager_error_does_not_fallback(
    monkeypatch,
):
    app = Flask(__name__)
    dex_surface_routes_ops.register_dex_submission_routes(app)
    monkeypatch.setattr(
        dex_surface_routes_ops,
        "_submission_database_url",
        lambda: (_ for _ in ()).throw(AssertionError("legacy sqlite must not be read")),
    )
    monkeypatch.setattr(
        dex_surface_routes_ops._root,
        "selected_chain_id_from_request",
        lambda: ({"market_id": "dex-arbitrage-4663-uniswap-v4"}, 4663),
    )
    monkeypatch.setattr(
        dex_surface_routes_ops,
        "_submission_page_args",
        lambda: (1, 20),
    )
    monkeypatch.setattr(
        dex_surface_routes_ops,
        "submission_execution_manager_payload",
        lambda **kwargs: {
            "ok": False,
            "code": "MANAGER_API_UNAVAILABLE",
            "upstreamStatus": 503,
        },
    )

    response = app.test_client().get(
        "/api/dex/submissions/execution-failed?chain_id=4663"
    )

    assert response.status_code == 503
    assert response.get_json()["dataSource"] == "manager"
    assert response.get_json()["error"] == "manager_read_failed"


def test_review_routes_use_manager_without_legacy_loaders(monkeypatch):
    app = Flask(__name__)
    dex_surface_routes_ops.register_dex_submission_routes(app)
    monkeypatch.setenv("MANAGER_UI_DATA_SOURCE", "manager")
    monkeypatch.setattr(
        dex_surface_routes_ops._root,
        "selected_chain_id_from_request",
        lambda: ({"market_id": "dex-arbitrage-4663-uniswap-v4"}, 4663),
    )
    monkeypatch.setattr(
        dex_surface_routes_ops,
        "_submission_page_args",
        lambda: (2, 20),
    )
    monkeypatch.setattr(
        dex_surface_routes_ops._root,
        "request_dex_strategy_arg",
        lambda: "closed_loop_routes",
    )
    monkeypatch.setattr(
        dex_surface_routes_ops,
        "_submission_database_url",
        lambda: (_ for _ in ()).throw(AssertionError("legacy sqlite must not be read")),
    )
    calls = []
    monkeypatch.setattr(
        dex_surface_routes_ops,
        "review_manager_page_payload",
        lambda **kwargs: calls.append(kwargs)
        or {
            "ok": True,
            "dataSource": "manager",
            "source": "manager",
            "mode": kwargs["mode"],
            "recordKind": "review",
            "summary": {},
            "categoryCounts": {},
            "attempts": [],
            "pagination": {},
            "chain_selection": kwargs["chain_selection"],
            "managerStatus": {},
        },
    )

    client = app.test_client()
    routes = [
        ("/api/dex/submissions/review?chain_id=4663", "all", None),
        (
            "/api/dex/submissions/review/data_incomplete?chain_id=4663",
            "data_incomplete",
            None,
        ),
        ("/api/dex/submissions/review-success?chain_id=4663", None, "success"),
        ("/api/dex/submissions/review-failed?chain_id=4663", None, "failed"),
    ]
    for route, category, outcome in routes:
        calls.clear()
        response = client.get(route)
        assert response.status_code == 200
        assert response.get_json()["dataSource"] == "manager"
        assert calls[0]["chain_id"] == 4663
        assert calls[0]["page"] == 2
        assert calls[0]["page_size"] == 20
        assert calls[0]["category"] == category
        assert calls[0]["outcome"] == outcome


def test_review_route_manager_error_does_not_fallback(monkeypatch):
    app = Flask(__name__)
    dex_surface_routes_ops.register_dex_submission_routes(app)
    monkeypatch.setattr(
        dex_surface_routes_ops._root,
        "selected_chain_id_from_request",
        lambda: ({"market_id": "dex-arbitrage-4663-uniswap-v4"}, 4663),
    )
    monkeypatch.setattr(
        dex_surface_routes_ops,
        "_submission_page_args",
        lambda: (1, 20),
    )
    monkeypatch.setattr(
        dex_surface_routes_ops,
        "_submission_database_url",
        lambda: (_ for _ in ()).throw(AssertionError("legacy sqlite must not be read")),
    )
    monkeypatch.setattr(
        dex_surface_routes_ops,
        "review_manager_page_payload",
        lambda **kwargs: {
            "ok": False,
            "code": "MANAGER_API_UNAVAILABLE",
            "upstreamStatus": 503,
        },
    )

    response = app.test_client().get(
        "/api/dex/submissions/review-success?chain_id=4663"
    )

    assert response.status_code == 503
    assert response.get_json()["dataSource"] == "manager"
    assert response.get_json()["error"] == "manager_read_failed"


def test_submission_clear_route_uses_manager_without_legacy_sqlite(monkeypatch):
    app = Flask(__name__)
    dex_surface_routes_ops.register_dex_submission_routes(app)
    monkeypatch.setattr(
        dex_surface_routes_ops._root,
        "selected_chain_id_from_request",
        lambda: ({"market_id": "dex-arbitrage-4663-uniswap-v4"}, 4663),
    )
    monkeypatch.setattr(
        dex_surface_routes_ops._root,
        "request_dex_strategy_arg",
        lambda: "closed_loop_routes",
    )
    monkeypatch.setattr(
        dex_surface_routes_ops,
        "_submission_database_url",
        lambda: (_ for _ in ()).throw(AssertionError("legacy sqlite must not be written")),
    )
    calls = []
    monkeypatch.setattr(
        dex_surface_routes_ops,
        "submission_clear_manager_payload",
        lambda **kwargs: calls.append(kwargs)
        or {
            "ok": True,
            "dataSource": "manager",
            "cleared": 2,
            "remaining": 0,
            "clearedDiagnostics": 3,
            "remainingDiagnostics": 0,
            "dryRun": False,
            "idempotentReplay": False,
            "managerResult": {"clearedAttempts": 2},
        },
    )

    response = app.test_client().post(
        "/api/dex/submissions/clear",
        json={"recordKind": "submission", "dryRun": False},
        headers={
            "X-Request-Id": "clear-request-route",
            "Idempotency-Key": "clear-idem-route",
        },
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["ok"] is True
    assert payload["dataSource"] == "manager"
    assert payload["cleared"] == 2
    assert payload["remaining"] == 0
    assert calls == [
        {
            "chain_id": 4663,
            "dry_run": False,
            "request_id": "clear-request-route",
            "idempotency_key": "clear-idem-route",
        }
    ]


def test_submission_clear_route_requires_explicit_dry_run(monkeypatch):
    app = Flask(__name__)
    dex_surface_routes_ops.register_dex_submission_routes(app)
    monkeypatch.setattr(
        dex_surface_routes_ops._root,
        "selected_chain_id_from_request",
        lambda: ({"market_id": "dex-arbitrage-4663-uniswap-v4"}, 4663),
    )

    response = app.test_client().post(
        "/api/dex/submissions/clear",
        json={"recordKind": "submission"},
    )

    assert response.status_code == 400
    assert response.get_json()["error"] == "dry_run_required"


def test_submission_overview_and_failed_routes_use_manager_without_legacy_loaders(
    monkeypatch,
):
    app = Flask(__name__)
    dex_surface_routes_ops.register_dex_submission_routes(app)
    monkeypatch.setattr(
        dex_surface_routes_ops._root,
        "selected_chain_id_from_request",
        lambda: ({"market_id": "dex-arbitrage-4663-uniswap-v4"}, 4663),
    )
    monkeypatch.setattr(
        dex_surface_routes_ops,
        "_submission_page_args",
        lambda: (2, 15),
    )
    monkeypatch.setattr(
        dex_surface_routes_ops._root,
        "request_dex_strategy_arg",
        lambda: "closed_loop_routes",
    )
    monkeypatch.setattr(
        dex_surface_routes_ops._root,
        "dex_submission_review_payload",
        lambda **kwargs: (_ for _ in ()).throw(
            AssertionError("legacy submission payload must not be called")
        ),
    )
    monkeypatch.setattr(
        dex_surface_routes_ops,
        "_submission_database_url",
        lambda: (_ for _ in ()).throw(AssertionError("legacy sqlite must not be read")),
    )
    calls = []
    monkeypatch.setattr(
        dex_surface_routes_ops,
        "submission_overview_manager_payload",
        lambda **kwargs: calls.append(kwargs)
        or {
            "ok": True,
            "source": "manager",
            "dataSource": "manager",
            "mode": kwargs["mode"],
            "summary": {},
            "categoryCounts": {},
            "attempts": [],
            "pagination": {},
            "routeFramework": {},
            "chain_selection": kwargs["chain_selection"],
        },
    )

    client = app.test_client()
    routes = [
        ("/api/dex/submissions?chain_id=4663", "dex_submission_review", None),
        (
            "/api/dex/submissions/failed?chain_id=4663",
            "dex_submission_failed_review",
            "all",
        ),
        (
            "/api/dex/submissions/failed/deferred?chain_id=4663",
            "dex_submission_failed_review",
            "deferred",
        ),
    ]
    for route, mode, category in routes:
        calls.clear()
        response = client.get(route)
        assert response.status_code == 200
        payload = response.get_json()
        assert payload["dataSource"] == "manager"
        assert payload["dex_strategy"] == "closed_loop_routes"
        assert calls[0]["chain_id"] == 4663
        assert calls[0]["mode"] == mode
        assert calls[0]["failure_category"] == category
        assert calls[0]["page"] == 2
        assert calls[0]["page_size"] == 15


def test_submission_overview_route_manager_error_does_not_fallback(monkeypatch):
    app = Flask(__name__)
    dex_surface_routes_ops.register_dex_submission_routes(app)
    monkeypatch.setattr(
        dex_surface_routes_ops._root,
        "selected_chain_id_from_request",
        lambda: ({"market_id": "dex-arbitrage-4663-uniswap-v4"}, 4663),
    )
    monkeypatch.setattr(
        dex_surface_routes_ops,
        "_submission_page_args",
        lambda: (1, 20),
    )
    monkeypatch.setattr(
        dex_surface_routes_ops,
        "_submission_database_url",
        lambda: (_ for _ in ()).throw(AssertionError("legacy sqlite must not be read")),
    )
    monkeypatch.setattr(
        dex_surface_routes_ops,
        "submission_overview_manager_payload",
        lambda **kwargs: {
            "ok": False,
            "code": "MANAGER_API_UNAVAILABLE",
            "upstreamStatus": 503,
        },
    )

    response = app.test_client().get("/api/dex/submissions?chain_id=4663")

    assert response.status_code == 503
    assert response.get_json()["dataSource"] == "manager"
    assert response.get_json()["error"] == "manager_read_failed"


def test_overview_page_route_is_manager_only_and_never_uses_legacy_loader(
    monkeypatch,
):
    app = Flask(__name__)
    dex_surface_routes_config.register_dex_overview_routes(app)
    monkeypatch.setattr(
        dex_surface_routes_config._root,
        "selected_chain_id_from_request",
        lambda: (
            {"market_id": "dex-arbitrage-4663-uniswap-v4"},
            4663,
        ),
    )
    monkeypatch.setattr(
        dex_surface_routes_config._root,
        "request_dex_protocols_arg",
        lambda: ["uniswap_v4"],
    )
    monkeypatch.setattr(
        dex_surface_routes_config._root,
        "_bootstrap_dex_surface_dependencies",
        lambda **kwargs: (_ for _ in ()).throw(
            AssertionError("legacy bootstrap must not run")
        ),
    )
    monkeypatch.setattr(
        dex_surface_routes_config._root,
        "_dex_surface_payload_for_chain",
        lambda **kwargs: (_ for _ in ()).throw(
            AssertionError("legacy surface loader must not run")
        ),
    )
    monkeypatch.setattr(
        dex_surface_routes_config._root,
        "dex_overview_payload",
        lambda **kwargs: (_ for _ in ()).throw(
            AssertionError("legacy overview loader must not run")
        ),
    )
    monkeypatch.setattr(
        dex_surface_routes_config._root,
        "_overview_response_payload",
        lambda payload: payload,
    )
    monkeypatch.setattr(
        dex_surface_routes_config._root,
        "_attach_dex_strategy",
        lambda payload: payload,
    )
    calls = []
    monkeypatch.setattr(
        dex_surface_routes_config,
        "overview_manager_page_payload",
        lambda **kwargs: calls.append(kwargs)
        or {
            "ok": True,
            "source": "manager",
            "dataSource": "manager",
            "summary": {"arbitragePathCount": 4},
            "arbitragePaths": [],
        },
    )

    response = app.test_client().get(
        "/api/dex/overview?chain_id=4663&manager_compare=true"
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["dataSource"] == "manager"
    assert payload["summary"]["arbitragePathCount"] == 4
    assert "managerShadow" not in payload
    assert calls == [
        {
            "chain_id": 4663,
            "protocols": ["uniswap_v4"],
            "chain_selection": {
                "market_id": "dex-arbitrage-4663-uniswap-v4"
            },
            "request_id": None,
        }
    ]


def test_overview_page_route_fails_closed_when_manager_unavailable(monkeypatch):
    app = Flask(__name__)
    dex_surface_routes_config.register_dex_overview_routes(app)
    monkeypatch.setattr(
        dex_surface_routes_config._root,
        "selected_chain_id_from_request",
        lambda: ({"market_id": "dex-arbitrage-4663-uniswap-v4"}, 4663),
    )
    monkeypatch.setattr(
        dex_surface_routes_config._root,
        "request_dex_protocols_arg",
        lambda: ["uniswap_v4"],
    )
    monkeypatch.setattr(
        dex_surface_routes_config,
        "overview_manager_page_payload",
        lambda **kwargs: {
            "ok": False,
            "code": "MANAGER_API_UNAVAILABLE",
            "upstreamStatus": 503,
        },
    )

    response = app.test_client().get("/api/dex/overview?chain_id=4663")

    assert response.status_code == 503
    assert response.get_json() == {
        "ok": False,
        "mode": "dex_overview",
        "dataSource": "manager",
        "error": "manager_read_failed",
        "managerDetail": {
            "ok": False,
            "code": "MANAGER_API_UNAVAILABLE",
            "upstreamStatus": 503,
        },
    }
