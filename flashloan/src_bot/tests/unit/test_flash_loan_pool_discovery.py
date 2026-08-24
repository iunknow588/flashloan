import json

from core import flash_loan_pool_discovery as discovery


def _write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_flash_loan_pool_discovery_builds_cache_from_real_pool_sources(tmp_path):
    aave_path = tmp_path / "aave_reserve_assets.json"
    venus_path = tmp_path / "venus_market_static_table.json"
    bnb_dex_path = tmp_path / "bnb_arbitrage_pool_static_table.json"
    avalanche_v3_path = tmp_path / "avalanche_l1_pool_static_table.json"
    avalanche_v2_path = tmp_path / "avalanche_multi_dex_v2_pool_discovery.json"
    _write_json(
        aave_path,
        {
            "chain_id": 43114,
            "assets": [
                {"token_symbol": "USDC", "available_liquidity": 100, "depth_score_usd": 100},
            ],
        },
    )
    _write_json(
        venus_path,
        {
            "chain_id": 56,
            "markets": [
                {"is_listed": True, "underlying_symbol": "USDT", "cash": "1000000"},
            ],
        },
    )
    _write_json(
        bnb_dex_path,
        {
            "chainId": 56,
            "pools": [
                {"dex": "pancakeswap_v3", "pool": "0x1", "tvlUsd": "1000"},
                {"dex": "biswap_v2", "pool": "0x2", "tvlUsd": "0"},
            ],
        },
    )
    _write_json(
        avalanche_v3_path,
        {
            "chainId": 43114,
            "pools": [
                {"pools": [{"pool": "0x3", "tvlUsd": "2000"}]},
            ],
        },
    )
    _write_json(
        avalanche_v2_path,
        {
            "chainId": 43114,
            "pools": [
                {"dex": "trader_joe_v1", "pool": "0x4", "tvlUsd": "3000"},
            ],
        },
    )
    robinhood = {
        "chainId": 4663,
        "generatedAt": "2026-08-24T00:00:00+00:00",
        "sources": [
            {
                "protocol": "morpho_blue",
                "source_kind": "flash_loan",
                "token_symbol": "USDG",
                "flash_borrowable": True,
                "depth_score_usd": 10,
            },
            {
                "protocol": "uniswap_v2",
                "source_kind": "flash_swap",
                "token_symbol": "WETH:USDG",
                "flash_borrowable": True,
                "depth_score_usd": 20,
            },
            {
                "protocol": "uniswap_v3",
                "source_kind": "observable_dex_pool",
                "token_symbol": "WETH:USDG",
                "flash_borrowable": False,
                "depth_score_usd": 30,
            },
        ],
    }

    snapshot = discovery.discover_flash_loan_pool_snapshot(
        aave_cache_path=aave_path,
        venus_market_path=venus_path,
        bnb_dex_pool_static_path=bnb_dex_path,
        avalanche_v3_pool_static_path=avalanche_v3_path,
        avalanche_v2_pool_discovery_path=avalanche_v2_path,
        robinhood_loader=lambda: robinhood,
    )

    protocols = {(item["chain_id"], item["protocol"]) for item in snapshot["protocols"]}
    assert (56, "venus_core") in protocols
    assert (56, "pancakeswap_v3") in protocols
    assert (43114, "aave_v3") in protocols
    assert (43114, "uniswap_v3") in protocols
    assert (43114, "traderjoe_v2") in protocols
    assert (43114, "pangolin_v2") not in protocols
    assert (43114, "sushi_v2") not in protocols
    assert (4663, "morpho_blue") in protocols
    assert (4663, "uniswap_v2") in protocols
    assert (4663, "uniswap_v3") not in protocols
    removed = {(item["chain_id"], item["protocol"], item["removed_reason"]) for item in snapshot["removed_protocols"]}
    assert (56, "biswap_v2", "protocol_pool_cache_not_available") in removed
    assert (4663, "uniswap_v3", "protocol_pool_cache_not_available") in removed
    assert (43114, "curve", "protocol_pool_cache_not_available") in removed
    scanned_by_chain = {item["chain_id"]: item for item in snapshot["scanned_chains"]}
    assert {56, 43114, 137, 4663, 1}.issubset(scanned_by_chain)
    assert "biswap_v2" in scanned_by_chain[56]["candidate_protocols"]
    assert "biswap_v2" not in scanned_by_chain[56]["available_protocols"]
    assert snapshot["raw_snapshots"]["robinhood_flash_liquidity"] is robinhood


def test_flash_loan_pool_discovery_query_and_robinhood_cache(tmp_path):
    snapshot_path = tmp_path / "flash_loan_pool_discovery_snapshot.json"
    snapshot = {
        "ok": True,
        "generated_at": "2026-08-24T00:00:00+00:00",
        "chains": [
            {
                "chain_id": 4663,
                "chain_key": "chain:4663",
                "network": "robinhood",
                "protocols": [{"protocol": "morpho_blue", "display_name": "Morpho Blue"}],
            }
        ],
        "protocols": [
            {"chain_id": 4663, "protocol": "morpho_blue", "display_name": "Morpho Blue"},
            {"chain_id": 56, "protocol": "venus_core", "display_name": "Venus Core"},
        ],
        "removed_protocols": [
            {
                "chain_id": 4663,
                "protocol": "uniswap_v3",
                "display_name": "Uniswap V3",
                "removed_reason": "protocol_pool_cache_not_available",
            },
            {
                "chain_id": 56,
                "protocol": "pancakeswap_v3",
                "display_name": "PancakeSwap V3",
                "removed_reason": "protocol_pool_cache_not_available",
            },
        ],
        "scanned_chains": [
            {
                "chain_id": 4663,
                "candidate_protocols": ["morpho_blue", "uniswap_v3"],
                "available_protocols": ["morpho_blue"],
                "removed_protocols": [{"protocol": "uniswap_v3", "display_name": "Uniswap V3"}],
            },
            {
                "chain_id": 56,
                "candidate_protocols": ["venus_core", "pancakeswap_v3"],
                "available_protocols": ["venus_core"],
                "removed_protocols": [{"protocol": "pancakeswap_v3", "display_name": "PancakeSwap V3"}],
            },
        ],
        "raw_snapshots": {"robinhood_flash_liquidity": {"chainId": 4663, "sources": [{"protocol": "morpho_blue"}]}},
        "errors": [],
    }
    _write_json(snapshot_path, snapshot)

    scoped = discovery.query_flash_loan_pool_discovery(chain_id=4663, snapshot_path=snapshot_path)

    assert scoped["protocols"] == [{"chain_id": 4663, "protocol": "morpho_blue", "display_name": "Morpho Blue"}]
    assert [item["protocol"] for item in scoped["removed_protocols"]] == ["uniswap_v3"]
    assert [item["chain_id"] for item in scoped["scanned_chains"]] == [4663]
    assert discovery.protocol_options_for_chain(chain_id=4663, snapshot_path=snapshot_path) == [
        {"protocol": "morpho_blue", "display_name": "Morpho Blue"}
    ]
    assert discovery.cached_robinhood_flash_liquidity_snapshot(snapshot_path=snapshot_path)["sources"] == [
        {"protocol": "morpho_blue"}
    ]
