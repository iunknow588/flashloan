from core.market_observation_store import (
    load_market_observation_static,
    market_observation_static_path,
    observation_payload_from_static,
    write_market_observation_static,
)


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
        "closedLoopRoutes": [{"pathText": "ETH -> AAA -> BBB -> ETH"}],
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
            {"pathText": "ETH -> AAA -> BBB -> ETH"},
            {"pathText": "ETH -> CCC -> DDD -> ETH"},
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
    assert loaded["list_counts"]["closedLoopRoutes"] == 2
    assert loaded["chainSpecificObservationPairs"][0]["poolCount"] == 2
    assert loaded["poolStaticTable"]["network"] == "robinhood-testnet"
    assert loaded["poolStaticTable"]["chainId"] == 46630
    assert loaded["surface"] == "dex_market_observation"
    assert loaded["chain_key"] == "robinhood-testnet:46630:uniswap_v4"
    assert loaded["processing"]["flow_name"] == "market_observation"
    assert market_observation_static_path(chain_id=46630, protocols=["uniswap_v4"], root=tmp_path).exists()


def test_market_observation_static_payload_backfills_anchor_lists():
    snapshot = {
        "exists": True,
        "path": "observation.json",
        "chain_id": 46630,
        "protocols": ["uniswap_v4"],
        "summary": {},
        "anchorGroups": [
            {"anchorSymbol": "ETH"},
            {"anchorSymbol": "USDC"},
        ],
        "closedLoopRoutes": [{"pathText": "ETH -> A -> B -> ETH"}],
    }

    payload = observation_payload_from_static(snapshot)

    assert payload["marketObservationSource"] == "static_file"
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
    assert payload["summary"]["closedLoopRouteCount"] == 1
    assert payload["marketObservationStaticTable"]["surface"] == "dex_market_observation"
    assert payload["marketObservationStaticTable"]["chain_key"] == "robinhood-testnet:46630:uniswap_v4"
