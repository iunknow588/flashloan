import json

from core.market_observation_compact import _compact_observation_pair, _merge_dynamic_hop_candidates_into_sequences
from core.market_observation_store import (
    compact_market_observation_static,
    load_market_observation_static,
    load_market_observation_overview_static,
    market_observation_static_path,
    market_observation_overview_static_path,
    observation_payload_from_static,
    overview_snapshot_from_observation,
    static_snapshot_from_observation,
    write_market_observation_static,
)


def test_compact_observation_pair_keeps_direct_swap_pool_display_fields():
    pair = {
        "symbolPair": "AAA:BBB",
        "addressPair": "0xaaa:0xbbb",
        "directSwapPoolCount": 1,
        "directSwapPools": [
            {
                "pool": "pool-1",
                "poolId": "pool-1",
                "protocol": "uniswap_v4",
                "dex": "uniswap_v4",
                "fee": 3000,
                "token0": "0xaaa",
                "token1": "0xbbb",
                "token0Symbol": "AAA",
                "token1Symbol": "BBB",
                "symbolPair": "AAA:BBB",
                "addressPair": "0xaaa:0xbbb",
                "depthBand": {"label": "active"},
            }
        ],
    }

    compact = _compact_observation_pair(pair)

    assert compact["directSwapPoolCount"] == 1
    assert compact["directSwapPools"][0]["symbolPair"] == "AAA:BBB"
    assert compact["directSwapPools"][0]["token0Symbol"] == "AAA"
    assert compact["directSwapPools"][0]["token1Symbol"] == "BBB"
    assert compact["directSwapPools"][0]["depthBand"] == {"label": "active"}


def test_market_observation_static_store_full_and_incremental_merge(tmp_path):
    first = {
        "mode": "dex_market_observation",
        "chain_id": 46630,
        "selected_protocols": ["uniswap_v4"],
        "processing": {
            "flow_name": "market_observation",
            "handler": "intent_trade.chains.robinhood_testnet.dex_surfaces:market_observation_flow",
            "global_chain_id": "robinhood-testnet-46630",
        },
        "summary": {"closedLoopRouteCount": 1},
        "poolStaticTable": {"network": "robinhood-testnet", "chainId": 46630, "exists": True},
        "chainSpecificObservationPairs": [{"symbolPair": "AAA:BBB", "addressPair": "0xaaa:0xbbb"}],
        "closedLoopRoutes": [{
            "pathText": "ETH -> AAA -> BBB -> ETH",
            "pathSymbols": ["ETH", "AAA", "BBB", "ETH"],
            "routePools": [{"pool": "pool-1"}, {"pool": "pool-2"}, {"pool": "pool-3"}],
        }],
    }
    second = {
        "mode": "dex_market_observation",
        "chain_id": 46630,
        "selected_protocols": ["uniswap_v4"],
        "summary": {"closedLoopRouteCount": 2},
        "chainSpecificObservationPairs": [
            {"symbolPair": "AAA:BBB", "addressPair": "0xaaa:0xbbb", "poolCount": 2},
            {"symbolPair": "CCC:DDD", "addressPair": "0xccc:0xddd"},
        ],
        "closedLoopRoutes": [
            {
                "pathText": "ETH -> AAA -> BBB -> ETH",
                "pathSymbols": ["ETH", "AAA", "BBB", "ETH"],
                "routePools": [{"pool": "pool-1"}, {"pool": "pool-2"}, {"pool": "pool-3"}],
            },
            {
                "pathText": "ETH -> CCC -> DDD -> ETH",
                "pathSymbols": ["ETH", "CCC", "DDD", "ETH"],
                "routePools": [{"pool": "pool-4"}, {"pool": "pool-5"}, {"pool": "pool-6"}],
            },
        ],
    }

    full = write_market_observation_static(
        first,
        chain_id=46630,
        protocols=["uniswap_v4"],
        refresh_mode="full",
        root=tmp_path,
    )
    merged = write_market_observation_static(
        second,
        chain_id=46630,
        protocols=["uniswap_v4"],
        refresh_mode="incremental",
        root=tmp_path,
    )
    loaded = load_market_observation_static(chain_id=46630, protocols=["uniswap_v4"], root=tmp_path)

    assert full["refresh_mode"] == "full"
    assert merged["refresh_mode"] == "incremental"
    assert loaded["exists"] is True
    assert loaded["list_counts"]["chainSpecificObservationPairs"] == 2
    assert loaded["list_counts"]["observationSequences"] == 2
    assert loaded["chainSpecificObservationPairs"][0]["poolCount"] == 2
    assert loaded["poolStaticTable"]["network"] == "robinhood-testnet"
    assert loaded["poolStaticTable"]["chainId"] == 46630
    assert loaded["surface"] == "dex_market_observation"
    assert loaded["chain_key"] == "robinhood-testnet:46630:uniswap_v4"
    assert loaded["processing"]["flow_name"] == "market_observation"
    assert market_observation_static_path(chain_id=46630, protocols=["uniswap_v4"], root=tmp_path).exists()
    overview = load_market_observation_overview_static(
        chain_id=46630,
        protocols=["uniswap_v4"],
        root=tmp_path,
    )
    assert overview["exists"] is True
    assert overview["snapshot_kind"] == "dex_overview_compact"
    assert overview["chainSpecificObservationPairs"][0]["poolCount"] == 2
    assert "observationSequences" not in overview
    assert [row["pathText"] for row in overview["closedLoopRouteSummaries"]] == [
        "ETH -> AAA -> BBB -> ETH",
        "ETH -> CCC -> DDD -> ETH",
    ]
    assert overview["observationSnapshotPath"] == str(
        market_observation_static_path(chain_id=46630, protocols=["uniswap_v4"], root=tmp_path)
    )
    assert market_observation_overview_static_path(
        chain_id=46630,
        protocols=["uniswap_v4"],
        root=tmp_path,
    ).exists()


def test_incremental_merge_replaces_candidate_group_by_anchor(tmp_path):
    write_market_observation_static(
        {
            "chain_id": 46630,
            "selected_protocols": ["uniswap_v4"],
            "observationAnchorSymbols": ["ETH"],
            "candidateObservationGroups": [
                {"anchorSymbol": "ETH", "observationSequenceCount": 1}
            ],
            "observationSequences": [
                {"anchorSymbol": "ETH", "pathText": "ETH -> AAA -> BBB -> ETH"}
            ],
        },
        chain_id=46630,
        protocols=["uniswap_v4"],
        refresh_mode="full",
        root=tmp_path,
    )
    stored = write_market_observation_static(
        {
            "chain_id": 46630,
            "selected_protocols": ["uniswap_v4"],
            "observationAnchorSymbols": ["ETH"],
            "candidateObservationGroups": [
                {"anchorSymbol": "ETH", "observationSequenceCount": 2}
            ],
            "observationSequences": [
                {"anchorSymbol": "ETH", "pathText": "ETH -> AAA -> BBB -> ETH"},
                {"anchorSymbol": "ETH", "pathText": "ETH -> CCC -> DDD -> ETH"},
            ],
        },
        chain_id=46630,
        protocols=["uniswap_v4"],
        refresh_mode="incremental",
        root=tmp_path,
    )

    assert len(stored["candidateObservationGroups"]) == 1
    assert stored["candidateObservationGroups"][0]["observationSequenceCount"] == 2
    assert len(stored["observationSequences"]) == 2


def test_incremental_merge_preserves_anchor_symbol_lists(tmp_path):
    write_market_observation_static(
        {
            "chain_id": 46630,
            "selected_protocols": ["uniswap_v4"],
            "borrowableSymbols": ["ETH"],
            "observationAnchorSymbols": ["ETH"],
        },
        chain_id=46630,
        protocols=["uniswap_v4"],
        refresh_mode="full",
        root=tmp_path,
    )
    stored = write_market_observation_static(
        {
            "chain_id": 46630,
            "selected_protocols": ["uniswap_v4"],
            "borrowableSymbols": ["USDC"],
            "observationAnchorSymbols": ["USDC"],
        },
        chain_id=46630,
        protocols=["uniswap_v4"],
        refresh_mode="incremental",
        root=tmp_path,
    )

    assert stored["borrowableSymbols"] == ["ETH", "USDC"]
    assert stored["observationAnchorSymbols"] == ["ETH", "USDC"]


def test_market_observation_static_store_isolated_by_chain(tmp_path):
    write_market_observation_static(
        {
            "mode": "dex_market_observation",
            "chain_id": 46630,
            "selected_protocols": ["uniswap_v4"],
            "summary": {"closedLoopRouteCount": 1},
            "chainSpecificObservationPairs": [{"symbolPair": "AAA:BBB"}],
        },
        chain_id=46630,
        protocols=["uniswap_v4"],
        refresh_mode="full",
        root=tmp_path,
    )

    testnet_path = market_observation_static_path(chain_id=46630, protocols=["uniswap_v4"], root=tmp_path)
    mainnet_path = market_observation_static_path(chain_id=4663, protocols=["uniswap_v4"], root=tmp_path)
    testnet_loaded = load_market_observation_static(chain_id=46630, protocols=["uniswap_v4"], root=tmp_path)
    mainnet_loaded = load_market_observation_static(chain_id=4663, protocols=["uniswap_v4"], root=tmp_path)

    assert testnet_path != mainnet_path
    assert testnet_path.as_posix().endswith("robinhood-testnet/46630/uniswap_v4/observation.json")
    assert mainnet_path.as_posix().endswith("robinhood/4663/uniswap_v4/observation.json")
    assert testnet_loaded["exists"] is True
    assert mainnet_loaded["exists"] is False
    assert testnet_loaded["network"] == "robinhood-testnet"
    assert mainnet_loaded["network"] == "robinhood"


def test_market_observation_static_payload_backfills_anchor_lists():
    snapshot = {
        "exists": True,
        "path": "observation.json",
        "chain_id": 46630,
        "protocols": ["uniswap_v4"],
        "marketObservationSourceStage": "generated_live_snapshot",
        "summary": {},
        "anchorGroups": [
            {"anchorSymbol": "ETH"},
            {"anchorSymbol": "USDC"},
        ],
        "closedLoopRoutes": [{
            "pathText": "ETH -> A -> B -> ETH",
            "pathSymbols": ["ETH", "A", "B", "ETH"],
            "routePools": [{"pool": "pool-a"}, {"pool": "pool-b"}, {"pool": "pool-c"}],
        }],
    }

    payload = observation_payload_from_static(snapshot)

    assert payload["marketObservationSource"] == "cached_snapshot"
    assert payload["marketObservationSourceStage"] == "cached_snapshot"
    assert payload["observationAnchorSymbols"] == ["ETH", "USDC"]
    assert payload["borrowableSymbols"] == ["ETH", "USDC"]
    assert payload["directPools"] == []
    assert payload["poolStaticTable"] == {
        "path": None,
        "exists": False,
        "network": None,
        "chainId": 46630,
    }
    assert payload["summary"]["observationAnchorCount"] == 2
    assert payload["summary"]["closedLoopRouteCount"] == 0
    assert payload["closedLoopRoutes"] == []
    assert payload["observationSequences"] == []
    assert payload["marketObservationStaticTable"]["surface"] == "dex_market_observation"
    assert payload["marketObservationStaticTable"]["chain_key"] == "robinhood-testnet:46630:uniswap_v4"


def test_static_snapshot_keeps_middle_hop_pool_exports_but_drops_index_exports():
    snapshot = static_snapshot_from_observation(
        {
            "chain_id": 46630,
            "selected_protocols": ["uniswap_v4"],
            "chainSpecificObservationPools": [{"poolId": "pool-1"}],
            "chainSpecificObservationTokens": [{"address": "0xtoken"}],
            "chainSpecificObservation": {"fullIndex": [{"poolId": "pool-1"}]},
            "bridgePoolIndexLookup": {"pools": [{"poolId": "pool-1"}]},
            "chainSpecificObservationPairs": [{"symbolPair": "AAA:BBB"}],
            "observationSequences": [{"pathText": "ETH -> AAA -> BBB -> ETH"}],
        }
    )

    assert snapshot["chainSpecificObservationPools"] == []
    assert "chainSpecificObservationTokens" not in snapshot
    assert "chainSpecificObservation" not in snapshot
    assert "bridgePoolIndexLookup" not in snapshot
    assert snapshot["chainSpecificObservationPairs"] == [{"symbolPair": "AAA:BBB"}]
    assert "closedLoopRoutes" not in snapshot
    assert snapshot["observationSequences"][0]["pathText"] == "ETH -> AAA -> BBB -> ETH"


def test_static_snapshot_merges_generic_closed_loop_routes_with_observation_sequences():
    snapshot = static_snapshot_from_observation(
        {
            "chain_id": 46630,
            "selected_protocols": ["uniswap_v4"],
            "chainSpecificObservationPairs": [{"symbolPair": "TB:TA"}],
            "observationSequences": [
                {
                    "anchorSymbol": "ETH",
                    "pathSymbols": ["ETH", "AAA", "BBB", "ETH"],
                    "pathText": "ETH -> AAA -> BBB -> ETH",
                    "routePools": [{"pool": "pool-1"}, {"pool": "pool-2"}, {"pool": "pool-3"}],
                }
            ],
            "closedLoopRoutes": [
                {
                    "anchorSymbol": "TZ",
                    "pathSymbols": ["TZ", "TB", "TA", "TZ"],
                    "pathText": "TZ -> TB -> TA -> TZ",
                    "routePools": [
                        {"pool": "pool-4", "routeSymbols": ["TZ", "TB"], "pairSymbol": "TZ:TB"},
                        {"pool": "pool-5", "routeSymbols": ["TB", "TA"], "pairSymbol": "TB:TA"},
                        {"pool": "pool-6", "routeSymbols": ["TA", "TZ"], "pairSymbol": "TA:TZ"},
                    ],
                }
            ],
        }
    )

    path_texts = [sequence.get("pathText") for sequence in snapshot["observationSequences"]]
    assert "ETH -> AAA -> BBB -> ETH" in path_texts
    assert "TZ -> TB -> TA -> TZ" in path_texts


def test_static_snapshot_collapses_reverse_direction_pool_address_closed_loops():
    def sequence(path_symbols, path_text, route_addresses, route_pools):
        return {
            "anchorSymbol": "A",
            "pathSymbols": path_symbols,
            "pathText": path_text,
            "routeAddresses": route_addresses,
            "routePools": route_pools,
        }

    observation = {
        "chain_id": 46630,
        "selected_protocols": ["uniswap_v4"],
        "closedLoopSequences": [
            sequence(
                ["A", "B", "C", "A"],
                "A -> B -> C -> A",
                ["0xa", "0xb", "0xc", "0xa"],
                [
                    {"pool": "pool-ab", "routeSymbols": ["A", "B"], "routeAddresses": ["0xa", "0xb"]},
                    {"pool": "pool-bc", "routeSymbols": ["B", "C"], "routeAddresses": ["0xb", "0xc"]},
                    {"pool": "pool-ca", "routeSymbols": ["C", "A"], "routeAddresses": ["0xc", "0xa"]},
                ],
            ),
            sequence(
                ["A", "C", "B", "A"],
                "A -> C -> B -> A",
                ["0xa", "0xc", "0xb", "0xa"],
                [
                    {"pool": "pool-ac", "routeSymbols": ["A", "C"], "routeAddresses": ["0xa", "0xc"]},
                    {"pool": "pool-cb", "routeSymbols": ["C", "B"], "routeAddresses": ["0xc", "0xb"]},
                    {"pool": "pool-ba", "routeSymbols": ["B", "A"], "routeAddresses": ["0xb", "0xa"]},
                ],
            ),
            sequence(
                ["A", "B", "C", "A"],
                "A -> B -> C -> A",
                ["0xa_alt", "0xb", "0xc", "0xa_alt"],
                [
                    {"pool": "pool-ab-alt", "routeSymbols": ["A", "B"], "routeAddresses": ["0xa_alt", "0xb"]},
                    {"pool": "pool-bc-alt", "routeSymbols": ["B", "C"], "routeAddresses": ["0xb", "0xc"]},
                    {"pool": "pool-ca-alt", "routeSymbols": ["C", "A"], "routeAddresses": ["0xc", "0xa_alt"]},
                ],
            ),
        ],
    }

    snapshot = static_snapshot_from_observation(
        observation,
        network="robinhood-testnet",
        chain_id=46630,
        protocols=["uniswap_v4"],
        refresh_mode="full",
    )
    overview = overview_snapshot_from_observation(snapshot)

    assert len(snapshot["observationSequences"]) == 3
    assert {tuple(row["routeAddresses"]) for row in snapshot["observationSequences"]} == {
        ("0xa", "0xb", "0xc", "0xa"),
        ("0xa", "0xc", "0xb", "0xa"),
        ("0xa_alt", "0xb", "0xc", "0xa_alt"),
    }
    assert len(overview["closedLoopRouteSummaries"]) == 2


def test_static_snapshot_persists_pool_registry_for_restore():
    snapshot = static_snapshot_from_observation(
        {
            "chain_id": 46630,
            "selected_protocols": ["uniswap_v4"],
            "closedLoopRoutes": [
                {
                    "anchorSymbol": "TZ",
                    "pathSymbols": ["TZ", "TB", "TA", "TZ"],
                    "pathText": "TZ -> TB -> TA -> TZ",
                    "routePools": [
                        {
                            "pool": "pool-a",
                            "poolManager": "0xmanager",
                            "balances": {"tvlUsd": 10},
                        },
                        {"pool": "pool-b"},
                        {"pool": "pool-c"},
                    ],
                }
            ],
        }
    )

    assert snapshot["poolRegistry"] == {
        "pool-a": {
            "pool": "pool-a",
            "poolManager": "0xmanager",
            "balances": {"tvlUsd": 10},
        },
        "pool-b": {"pool": "pool-b"},
        "pool-c": {"pool": "pool-c"},
    }


def test_observation_payload_from_static_hydrates_route_pools_from_registry():
    snapshot = {
        "chain_id": 46630,
        "protocols": ["uniswap_v4"],
        "selected_protocols": ["uniswap_v4"],
        "poolRegistry": {
            "pool-a": {
                "pool": "pool-a",
                "protocol": "uniswap_v4",
                "poolManager": "0xmanager",
                "balances": {"tvlUsd": 10},
            }
        },
        "observationSequences": [
            {
                "anchorSymbol": "TZ",
                "pathSymbols": ["TZ", "TB", "TA", "TZ"],
                "pathText": "TZ -> TB -> TA -> TZ",
                "routePools": [
                    {"pool": "pool-a"},
                    {"pool": "pool-b"},
                    {"pool": "pool-c"},
                ],
            }
        ],
    }

    payload = observation_payload_from_static(snapshot)

    assert len(payload["observationSequences"]) == 1
    assert payload["observationSequences"][0]["routePools"][0]["poolManager"] == "0xmanager"
    assert payload["summary"]["observationSequenceCount"] == 1
    assert payload["closedLoopRoutes"] == []
    assert payload["indirectRoutes"] == []


def test_static_snapshot_keeps_only_route_handoff_fields():
    snapshot = static_snapshot_from_observation(
        {
            "chain_id": 46630,
            "selected_protocols": ["uniswap_v4"],
            "anchorGroups": [
                {
                    "anchorSymbol": "ETH",
                    "sharedPools": [{"pool": "candidate-only"}],
                    "sharedTokens": [{"symbol": "AAA", "pools": [{"pool": "candidate-only"}]}],
                    "ycPoolCount": "2",
                    "zcPoolCount": "2",
                }
            ],
            "candidateObservationGroups": [
                {
                    "anchorSymbol": "ETH",
                    "sharedPools": [{"pool": "candidate-only"}],
                    "sharedTokens": [{"symbol": "AAA", "address": "0xaaa", "poolCount": "2", "pools": [{"pool": "candidate-only"}]}],
                    "observationSequences": [{"pathText": "duplicate"}],
                    "closedLoopRoutes": [{"pathText": "duplicate"}],
                }
            ],
            "observationSequences": [
                {
                    "anchorSymbol": "ETH",
                    "pathText": "ETH -> AAA -> BBB -> ETH",
                    "ycPool": {"pool": "pool-1"},
                    "bridgePool": {"pool": "pool-2"},
                    "zcPool": {"pool": "pool-3"},
                    "routePools": [{"pool": "pool-1"}, {"pool": "pool-2"}, {"pool": "pool-3"}],
                }
            ],
            "closedLoopRoutes": [{
                "pathText": "ETH -> AAA -> BBB -> ETH",
                "pathSymbols": ["ETH", "AAA", "BBB", "ETH"],
                "routePools": [{"pool": "pool-1"}, {"pool": "pool-2"}, {"pool": "pool-3"}],
            }],
            "indirectRoutes": [{
                "pathText": "ETH -> AAA -> BBB -> ETH",
                "pathSymbols": ["ETH", "AAA", "BBB", "ETH"],
                "routePools": [{"pool": "pool-1"}, {"pool": "pool-2"}, {"pool": "pool-3"}],
            }],
            "directPools": [{"symbolPair": "ETH:AAA"}],
            "unmatchedPools": [{"symbolPair": "CCC:DDD"}],
        }
    )

    assert snapshot["anchorGroups"] == [
        {
            "anchorSymbol": "ETH",
            "sharedPools": [{"pool": "candidate-only"}],
            "sharedTokens": [{"symbol": "AAA", "pools": [{"pool": "candidate-only"}]}],
            "ycPoolCount": "2",
            "zcPoolCount": "2",
        }
    ]
    assert "closedLoopRoutes" not in snapshot
    assert "indirectRoutes" not in snapshot
    assert "directPools" not in snapshot
    assert "unmatchedPools" not in snapshot
    assert snapshot["candidateObservationGroups"] == [
        {
            "anchorSymbol": "ETH",
            "sharedTokens": [{"symbol": "AAA", "address": "0xaaa", "poolCount": "2"}],
            "ycTokensRef": "sharedTokens",
            "zcTokensRef": "sharedTokens",
            "combinationGroups": [
                {
                    "anchorSymbol": "ETH",
                    "bridgeSymbol": "AAA",
                    "counterSymbol": "BBB",
                    "pathSymbols": ["ETH", "AAA", "BBB", "ETH"],
                    "pathText": "ETH -> AAA -> BBB -> ETH",
                    "pool": "pool-2",
                    "poolCount": 3,
                    "observationState": "reachable",
                    "observationReachable": True,
                    "observationEmptyReason": None,
                    "ycPool": None,
                    "bridgePool": None,
                    "zcPool": None,
                    "routePools": [
                        {"pool": "pool-1"},
                        {"pool": "pool-2"},
                        {"pool": "pool-3"},
                    ],
                    "ycPoolCount": 0,
                    "zcPoolCount": 0,
                    "candidatePoolCount": "3",
                    "matchedPoolCount": "3",
                    "hopCandidateCounts": {},
                    "poolPathKey": "",
                }
            ],
            "observationSequenceCount": 1,
            "observationSequencePreview": ["ETH -> AAA -> BBB -> ETH"],
            "candidateCombinationCount": 1,
                "reachableCombinationCount": 1,
                "noPoolCombinationCount": 0,
                "observationReachable": True,
                "bridgePoolObservationGroups": [
                    {
                        "bridgePool": "pool-2",
                        "directionCount": 1,
                        "preview": "ETH -> AAA -> BBB -> ETH",
                    }
                ],
                "observationState": {"status": "reachable", "reachable": True, "groupCount": 1},
                "observationCombinationCount": 1,
            "observationCombinationPreview": ["ETH -> AAA -> BBB -> ETH"],
        }
    ]
    assert "ycPool" not in snapshot["observationSequences"][0]
    assert snapshot["observationSequences"][0]["routePools"] == [
        {"pool": "pool-1"},
        {"pool": "pool-2"},
        {"pool": "pool-3"},
    ]


def test_incremental_merge_removes_legacy_full_exports(tmp_path):
    path = market_observation_static_path(
        chain_id=46630,
        protocols=["uniswap_v4"],
        root=tmp_path,
    )
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "chain_id": 46630,
                "protocols": ["uniswap_v4"],
                "chainSpecificObservationPools": [{"poolId": "legacy"}],
                "chainSpecificObservationTokens": [{"address": "0xlegacy"}],
                "chainSpecificObservation": {"legacy": True},
                "closedLoopRoutes": [{
                    "pathText": "ETH -> AAA -> BBB -> ETH",
                    "pathSymbols": ["ETH", "AAA", "BBB", "ETH"],
                    "routePools": [{"pool": "pool-1"}, {"pool": "pool-2"}, {"pool": "pool-3"}],
                }],
            }
        ),
        encoding="utf-8",
    )

    stored = write_market_observation_static(
        {
            "chain_id": 46630,
            "selected_protocols": ["uniswap_v4"],
            "closedLoopRoutes": [{
                "pathText": "ETH -> AAA -> BBB -> ETH",
                "pathSymbols": ["ETH", "AAA", "BBB", "ETH"],
                "routePools": [{"pool": "pool-1"}, {"pool": "pool-2"}, {"pool": "pool-3"}],
            }],
        },
        chain_id=46630,
        protocols=["uniswap_v4"],
        refresh_mode="incremental",
        root=tmp_path,
    )

    assert {
        row.get("pool") or row.get("poolId")
        for row in stored["chainSpecificObservationPools"]
    } >= {
        "pool-1",
        "pool-2",
        "pool-3",
    }
    assert "chainSpecificObservationTokens" not in stored
    assert "chainSpecificObservation" not in stored
    assert stored["observationSequences"][0]["pathText"] == "ETH -> AAA -> BBB -> ETH"


def test_compact_market_observation_static_migrates_legacy_snapshot(tmp_path):
    path = market_observation_static_path(
        chain_id=46630,
        protocols=["uniswap_v4"],
        root=tmp_path,
    )
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "ok": True,
                "network": "robinhood-testnet",
                "chain_id": 46630,
                "protocols": ["uniswap_v4"],
                "selected_protocols": ["uniswap_v4"],
                "generated_at": "2026-09-01T00:00:00Z",
                "chainSpecificObservationPools": [{"poolId": "legacy"}],
                "chainSpecificObservationTokens": [{"address": "0xlegacy"}],
                "closedLoopRoutes": [{
                    "pathText": "ETH -> AAA -> BBB -> ETH",
                    "pathSymbols": ["ETH", "AAA", "BBB", "ETH"],
                    "routePools": [{"pool": "pool-1"}, {"pool": "pool-2"}, {"pool": "pool-3"}],
                }],
            }
        ),
        encoding="utf-8",
    )

    compact = compact_market_observation_static(
        chain_id=46630,
        protocols=["uniswap_v4"],
        root=tmp_path,
    )
    loaded = load_market_observation_static(
        chain_id=46630,
        protocols=["uniswap_v4"],
        root=tmp_path,
    )
    overview = load_market_observation_overview_static(
        chain_id=46630,
        protocols=["uniswap_v4"],
        root=tmp_path,
    )

    assert compact["observationSequences"][0]["pathText"] == "ETH -> AAA -> BBB -> ETH"
    assert [row["pool"] for row in loaded["chainSpecificObservationPools"]] == [
        "pool-1",
        "pool-2",
        "pool-3",
    ]
    assert "chainSpecificObservationTokens" not in loaded
    assert overview["snapshot_kind"] == "dex_overview_compact"
    assert "observationSequences" not in overview
    assert overview["closedLoopRouteSummaries"][0]["pathText"] == "ETH -> AAA -> BBB -> ETH"
    static_payload = observation_payload_from_static(loaded)
    assert static_payload["closedLoopRoutes"] == []
    assert [sequence["pathText"] for sequence in static_payload["observationSequences"]] == [
        "ETH -> AAA -> BBB -> ETH",
    ]


def test_observation_payload_from_static_filters_two_hop_closed_loop():
    snapshot = {
        "network": "robinhood-testnet",
        "chain_id": 46630,
        "protocols": ["uniswap_v4"],
        "selected_protocols": ["uniswap_v4"],
        "closedLoopRoutes": [
            {
                "pathText": "ETH -> AAA -> ETH",
                "pathSymbols": ["ETH", "AAA", "ETH"],
                "routePools": [{"pool": "pool-1"}, {"pool": "pool-2"}],
            },
            {
                "pathText": "ETH -> AAA -> BBB -> ETH",
                "pathSymbols": ["ETH", "AAA", "BBB", "ETH"],
                "routePools": [{"pool": "pool-3"}, {"pool": "pool-4"}, {"pool": "pool-5"}],
            },
        ],
    }

    payload = observation_payload_from_static(snapshot)

    assert payload["closedLoopRoutes"] == []
    assert payload["observationSequences"] == []


def test_static_snapshot_persists_ob_table_graph_and_provenance():
    snapshot = static_snapshot_from_observation(
        {
            "chain_id": 46630,
            "selected_protocols": ["uniswap_v4"],
            "tableSource": "ob_table",
            "referencedTable": "ch_table",
            "poolIndexVersion": {"path": "v4_pool_index/latest.json", "generatedAt": "2026-10-01T00:00:00Z"},
            "anchorGroups": [{"anchorSymbol": "ETH"}],
            "pairs": [{"symbolPair": "ETH:AAA"}],
            "bridgePairs": [{"symbolPair": "AAA:BBB"}],
        }
    )

    assert snapshot["tableSource"] == "ob_table"
    assert snapshot["referencedTable"] == "ch_table"
    assert snapshot["poolIndexVersion"]["path"] == "v4_pool_index/latest.json"
    assert snapshot["anchorGroups"] == [{"anchorSymbol": "ETH"}]
    assert snapshot["pairs"] == [{"symbolPair": "ETH:AAA"}]
    assert snapshot["bridgePairs"] == [{"symbolPair": "AAA:BBB"}]
    payload = observation_payload_from_static(snapshot)
    assert payload["pairs"] == [{"symbolPair": "ETH:AAA"}]
    assert payload["bridgePairs"] == [{"symbolPair": "AAA:BBB"}]


def test_static_snapshot_keeps_dynamic_hop_candidates_in_overview_summary():
    observation = {
        "chain_id": 46630,
        "selected_protocols": ["uniswap_v4"],
        "observationAnchorSymbols": ["ETH"],
        "observationSequences": [
            {
                "kind": "anchor_closed_loop_token_bridge",
                "anchorSymbol": "ETH",
                "bridgeSymbol": "AAA",
                "counterSymbol": "BBB",
                "pathSymbols": ["ETH", "AAA", "BBB", "ETH"],
                "pathText": "ETH -> AAA -> BBB -> ETH",
                "hopCandidates": [
                    {
                        "leg": "x-y",
                        "direction": "yc",
                        "symbolPair": "ETH -> AAA",
                        "poolCount": 1,
                        "usablePoolCount": 1,
                        "selectionBasis": "tvl_depth_desc",
                        "preferredPoolId": "pool-a",
                        "preferredPoolUsable": True,
                        "pools": [{"pool": "pool-a", "usableForObservation": True}],
                    },
                    {
                        "leg": "y-z",
                        "direction": "bridge",
                        "symbolPair": "AAA -> BBB",
                        "poolCount": 2,
                        "usablePoolCount": 2,
                        "selectionBasis": "tvl_depth_desc",
                        "preferredPoolId": "pool-b",
                        "preferredPoolUsable": True,
                        "pools": [
                            {"pool": "pool-b", "usableForObservation": True},
                            {"pool": "pool-b2", "usableForObservation": True},
                        ],
                    },
                    {
                        "leg": "z-x",
                        "direction": "zc",
                        "symbolPair": "BBB -> ETH",
                        "poolCount": 1,
                        "usablePoolCount": 1,
                        "selectionBasis": "tvl_depth_desc",
                        "preferredPoolId": "pool-c",
                        "preferredPoolUsable": True,
                        "pools": [{"pool": "pool-c", "usableForObservation": True}],
                    },
                ],
                "routePools": [
                    {"pool": "pool-a", "routeSymbols": ["ETH", "AAA"], "routeAddresses": ["0xeth", "0xaaa"]},
                    {"pool": "pool-b", "routeSymbols": ["AAA", "BBB"], "routeAddresses": ["0xaaa", "0xbbb"]},
                    {"pool": "pool-c", "routeSymbols": ["BBB", "ETH"], "routeAddresses": ["0xbbb", "0xeth"]},
                ],
            }
        ],
    }

    snapshot = static_snapshot_from_observation(
        observation,
        network="robinhood-testnet",
        chain_id=46630,
        protocols=["uniswap_v4"],
        refresh_mode="full",
    )
    overview = overview_snapshot_from_observation(snapshot)

    assert len(overview["closedLoopRouteSummaries"]) == 1
    summary = overview["closedLoopRouteSummaries"][0]
    assert summary["pathText"] == "ETH -> AAA -> BBB -> ETH"
    assert len(summary["hopCandidates"]) == 3
    assert summary["hopCandidateCounts"] == {"x-y": 1, "y-z": 2, "z-x": 1}
    assert summary["poolPathKey"] == "pool-a|pool-b|pool-c"


def test_merge_dynamic_hop_candidates_into_sequences_backfills_legacy_static_rows():
    sequences = [
        {
            "kind": "anchor_closed_loop_token_bridge",
            "anchorSymbol": "ETH",
            "bridgeSymbol": "AAA",
            "counterSymbol": "BBB",
            "pathSymbols": ["ETH", "AAA", "BBB", "ETH"],
            "pathText": "ETH -> AAA -> BBB -> ETH",
            "routePools": [
                {"pool": "pool-a", "routeAddresses": ["0xeth", "0xaaa"]},
                {"pool": "pool-b", "routeAddresses": ["0xaaa", "0xbbb"]},
                {"pool": "pool-c", "routeAddresses": ["0xbbb", "0xeth"]},
            ],
        }
    ]
    dynamic_paths = [
        {
            "overviewRoute": {
                "pathSymbols": ["ETH", "AAA", "BBB", "ETH"],
            },
            "routeTokenPath": [
                {"symbol": "ETH", "address": "0xeth"},
                {"symbol": "AAA", "address": "0xaaa"},
                {"symbol": "BBB", "address": "0xbbb"},
                {"symbol": "ETH", "address": "0xeth"},
            ],
            "strictQualityPools": [
                {"pool": "pool-a"},
                {"pool": "pool-b"},
                {"pool": "pool-c"},
            ],
            "hopCandidates": [
                {
                    "leg": "x-y",
                    "poolCount": 1,
                    "pools": [{"pool": "pool-a"}],
                },
                {
                    "leg": "y-z",
                    "poolCount": 2,
                    "pools": [{"pool": "pool-b"}, {"pool": "pool-b2"}],
                },
                {
                    "leg": "z-x",
                    "poolCount": 1,
                    "pools": [{"pool": "pool-c"}],
                },
            ],
            "hopCandidateCounts": {"x-y": 1, "y-z": 2, "z-x": 1},
            "hopCandidateCombinationCount": 2,
            "poolPathKey": "pool-a|pool-b|pool-c",
        }
    ]

    merged = _merge_dynamic_hop_candidates_into_sequences(sequences, dynamic_paths)

    assert len(merged) == 1
    assert len(merged[0]["hopCandidates"]) == 3
    assert merged[0]["hopCandidateCounts"] == {"x-y": 1, "y-z": 2, "z-x": 1}
    assert merged[0]["poolPathKey"] == "pool-a|pool-b|pool-c"
