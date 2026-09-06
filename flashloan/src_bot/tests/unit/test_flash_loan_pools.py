import inspect
import json
from pathlib import Path

from web import flash_loan_pools


def _write_json(path, payload):
    path.write_text(json.dumps(payload), encoding="utf-8")


def _robinhood_flash_snapshot():
    return {
        "ok": True,
        "network": "robinhood",
        "chainId": 4663,
        "generatedAt": "2026-08-24T00:00:00+00:00",
        "morphoBlueCore": "0x9D53d5E3bd5E8d4Cbfa6DB1ca238AEA02E651010",
        "uniswapV2Factory": "0x8bCeAA40b9ACdFAEDF85Adf4FF01f5aD6517937f",
        "rpcUrl": "https://rpc.example",
        "sources": [
            {
                "protocol": "morpho_blue",
                "source_kind": "flash_loan",
                "display_name": "Morpho Blue USDG",
                "provider": "Morpho Blue Core",
                "provider_address": "0x9D53d5E3bd5E8d4Cbfa6DB1ca238AEA02E651010",
                "token_symbol": "USDG",
                "token_address": "0x5fc5360D0400a0Fd4f2af552ADD042D716F1d168",
                "available_liquidity": 41_660_722.032807,
                "depth_score_usd": 41_660_722.032807,
                "fee_bps": 0,
                "flash_capable": True,
                "flash_borrowable": True,
                "borrowable": True,
                "repayment_model": "exact_amount_no_protocol_fee",
            },
            {
                "protocol": "morpho_blue",
                "source_kind": "flash_loan",
                "display_name": "Morpho Blue WETH",
                "provider": "Morpho Blue Core",
                "provider_address": "0x9D53d5E3bd5E8d4Cbfa6DB1ca238AEA02E651010",
                "token_symbol": "WETH",
                "token_address": "0x0Bd7D308f8E1639FAb988df18A8011f41EAcAD73",
                "available_liquidity": 0.017349,
                "depth_score_usd": 42.5,
                "fee_bps": 0,
                "flash_capable": True,
                "flash_borrowable": True,
                "borrowable": True,
                "repayment_model": "exact_amount_no_protocol_fee",
            },
            {
                "protocol": "uniswap_v2",
                "source_kind": "flash_swap",
                "display_name": "Uniswap V2 WETH:USDG",
                "provider": "Uniswap V2 Pair",
                "provider_address": "0x8803c117ccae7b5146297876c2a25df135141c4d",
                "factory_address": "0x8bCeAA40b9ACdFAEDF85Adf4FF01f5aD6517937f",
                "token_symbol": "WETH:USDG",
                "token_address": "0x8803c117ccae7b5146297876c2a25df135141c4d",
                "token0_symbol": "WETH",
                "token1_symbol": "USDG",
                "token0_address": "0x0Bd7D308f8E1639FAb988df18A8011f41EAcAD73",
                "token1_address": "0x5fc5360D0400a0Fd4f2af552ADD042D716F1d168",
                "reserve0": 45.36597264479032,
                "reserve1": 110_985.20182,
                "available_liquidity": 110_985.20182,
                "depth_score_usd": 222_082.83,
                "fee_bps": 30,
                "flash_capable": True,
                "flash_borrowable": True,
                "borrowable": True,
                "repayment_model": "uniswap_v2_repay_amount_out_times_1000_div_997_plus_one",
            },
        ],
        "errors": [],
        "evidence": ["rh-flash-toolkit", "readonly_test"],
    }


def test_flash_loan_pools_recognizes_bridged_stable_symbols():
    assert flash_loan_pools._is_stable_symbol("DAI.e") is True
    assert flash_loan_pools._is_stable_symbol("USDC.e") is True
    assert flash_loan_pools._is_stable_symbol("USDt") is True
    assert flash_loan_pools._is_stable_symbol("WAVAX") is False


def test_flash_loan_pools_reuses_reserve_cache_classification_and_targets(tmp_path, monkeypatch):
    aave_cache_path = tmp_path / "aave_reserve_assets.json"
    target_cache_path = tmp_path / "stable_target_assets.json"
    _write_json(
        aave_cache_path,
        {
            "schema_version": 4,
            "chain_id": 43114,
            "block_number": 123456,
            "refreshed_at": "2026-08-24T01:02:03+00:00",
            "pool_address": "0xpool",
            "rpc_url": "https://rpc.example",
            "assets": [
                {
                    "token_symbol": "USDC",
                    "binance_symbol": "USDCUSDT",
                    "is_stable": True,
                    "depth_score_usd": 1_500_000,
                    "available_liquidity": 10,
                    "reserve_data_liquidity": 20,
                    "a_token_total_supply": 30,
                    "token_address": "0x" + "1" * 40,
                },
                {
                    "token_symbol": "USDT",
                    "binance_symbol": "USDTUSDT",
                    "is_stable": True,
                    "depth_score_usd": 50_000,
                    "available_liquidity": 15,
                    "reserve_data_liquidity": 10,
                    "a_token_total_supply": 5,
                    "token_address": "0x" + "2" * 40,
                },
                {
                    "token_symbol": "WAVAX",
                    "binance_symbol": "AVAXUSDT",
                    "is_stable": False,
                    "depth_score_usd": 250_000,
                    "available_liquidity": 3,
                    "reserve_data_liquidity": 7,
                    "a_token_total_supply": 2,
                    "token_address": "0x" + "3" * 40,
                },
                {
                    "token_symbol": "AAVE",
                    "binance_symbol": "AAVEUSDT",
                    "is_stable": False,
                    "depth_score_usd": 0,
                    "available_liquidity": 0,
                    "reserve_data_liquidity": 0,
                    "a_token_total_supply": 0,
                    "token_address": "0x" + "4" * 40,
                },
            ],
        },
    )
    _write_json(
        target_cache_path,
        {
            "source": "dex_stable_targets",
            "assets": [
                {"binance_symbol": "USDCUSDT", "via_borrows": ["AVAXUSDT"], "via_stables": ["USDTUSDT"]},
                {"binance_symbol": "USDTUSDT", "via_borrows": ["AVAXUSDT"], "via_stables": []},
            ],
        },
    )
    monkeypatch.setattr(
        flash_loan_pools,
        "supported_market_summaries",
        lambda database_url=None: [
            {
                "market_id": "avalanche-aave-v3",
                "feature": "liquidation",
                "display_name": "Avalanche Aave V3",
                "network": "avalanche",
                "chain_id": 43114,
                "market_type": "native_chain",
                "protocol": "aave_v3",
                "protocol_supported": True,
            },
            {
                "market_id": "avalanche-traderjoe",
                "feature": "dex_arbitrage",
                "display_name": "Avalanche Trader Joe",
                "network": "avalanche",
                "chain_id": 43114,
                "market_type": "native_chain",
                "protocol": "traderjoe_v2",
                "protocol_supported": True,
            },
        ],
    )

    payload = flash_loan_pools.flash_loan_pools_payload(
        chain_id=43114,
        protocols=["aave_v3"],
        aave_cache_path=aave_cache_path,
        stable_target_cache_path=target_cache_path,
    )

    view = payload["protocol_views"][0]
    assert payload["ok"] is True
    assert payload["scope"] == {"chain_id": 43114, "protocols": ["aave_v3"]}
    assert payload["summary"]["provider_count"] == 1
    assert payload["summary"]["bot_consumable_count"] == 1
    assert payload["summary"]["dex_consumable_count"] == 1
    assert view["protocol"] == "aave_v3"
    assert view["cache"]["asset_count"] == 4
    assert view["summary"]["stable_borrow_pool_count"] == 2
    assert view["summary"]["non_stable_borrow_pool_count"] == 2
    assert view["summary"]["depth_counts"] == {
        "deep": 1,
        "shallow": 1,
        "medium": 1,
        "unavailable": 1,
    }
    assert [row["token_symbol"] for row in view["stable_borrow_pools"]] == ["USDC", "USDT"]
    assert [row["token_symbol"] for row in view["non_stable_borrow_pools"]] == ["WAVAX", "AAVE"]
    assert view["stable_borrow_pools"][0]["depth_level"] == "deep"
    assert view["stable_borrow_pools"][0]["liquidity_units"] == 30
    assert view["non_stable_borrow_pools"][0]["depth_level"] == "medium"
    assert view["non_stable_borrow_pools"][1]["depth_level"] == "unavailable"
    assert view["borrow_target_universe"]["borrow_count"] == 4
    assert view["borrow_target_universe"]["target_count"] == 2
    avax_targets = next(
        row for row in view["borrow_target_universe"]["borrow_rows"] if row["borrow_symbol"] == "AVAXUSDT"
    )
    assert avax_targets["target_symbols"] == ["USDCUSDT", "USDTUSDT"]


def test_flash_loan_pools_returns_one_view_per_requested_protocol(tmp_path, monkeypatch):
    aave_cache_path = tmp_path / "aave_reserve_assets.json"
    _write_json(
        aave_cache_path,
        {
            "assets": [
                {
                    "token_symbol": "USDC",
                    "binance_symbol": "USDCUSDT",
                    "is_stable": True,
                    "depth_score_usd": 1,
                }
            ]
        },
    )
    monkeypatch.setattr(
        flash_loan_pools,
        "supported_market_summaries",
        lambda database_url=None: [
            {
                "market_id": "bnb-aave-v3",
                "feature": "liquidation",
                "network": "bnb",
                "chain_id": 56,
                "market_type": "native_chain",
                "protocol": "aave_v3",
                "protocol_supported": True,
            },
            {
                "market_id": "bnb-venus-core",
                "feature": "liquidation",
                "network": "bnb",
                "chain_id": 56,
                "market_type": "native_chain",
                "protocol": "venus_core",
                "protocol_supported": False,
                "observation_only": True,
            },
        ],
    )

    payload = flash_loan_pools.flash_loan_pools_payload(
        chain_id=56,
        protocols=["venus_core", "aave_v3", "venus_core"],
        aave_cache_path=aave_cache_path,
        stable_target_cache_path=tmp_path / "missing_targets.json",
        venus_market_path=tmp_path / "missing_venus_market_static_table.json",
    )

    assert payload["scope"]["protocols"] == ["venus_core", "aave_v3"]
    assert [view["protocol"] for view in payload["protocol_views"]] == ["venus_core", "aave_v3"]
    assert payload["protocol_views"][0]["status"] == "protocol_pool_cache_not_available"
    assert payload["protocol_views"][1]["summary"]["stable_borrow_pool_count"] == 1
    assert {provider["protocol"] for provider in payload["providers"]} == {"aave_v3", "venus_core"}


def test_flash_loan_pools_defaults_to_all_available_protocols_in_priority_order(monkeypatch):
    monkeypatch.setattr(
        flash_loan_pools,
        "supported_market_summaries",
        lambda database_url=None: [
            {
                "market_id": "bnb-aave-v3",
                "feature": "liquidation",
                "network": "bnb",
                "chain_id": 56,
                "market_type": "native_chain",
                "protocol": "aave_v3",
                "protocol_supported": True,
            },
            {
                "market_id": "bnb-venus-core",
                "feature": "liquidation",
                "network": "bnb",
                "chain_id": 56,
                "market_type": "native_chain",
                "protocol": "venus_core",
                "protocol_supported": True,
            },
            {
                "market_id": "bnb-dex-arbitrage",
                "feature": "dex_arbitrage",
                "network": "bnb",
                "chain_id": 56,
                "market_type": "native_chain",
                "protocol": "pancakeswap_v3",
                "protocol_supported": True,
                "observation_only": True,
            },
        ],
    )
    monkeypatch.setattr(flash_loan_pools, "_chain_default_protocol_preferences", lambda: {56: ["venus_core"]})

    payload = flash_loan_pools.flash_loan_pools_payload(chain_id=56)

    assert payload["scope"]["protocols"] == []
    assert [view["protocol"] for view in payload["protocol_views"]] == ["venus_core", "aave_v3", "pancakeswap_v3"]


def test_flash_loan_pools_filters_chain_protocols_by_discovery_snapshot(monkeypatch):
    monkeypatch.setattr(
        flash_loan_pools,
        "supported_market_summaries",
        lambda database_url=None: [
            {
                "market_id": "bnb-venus-core",
                "feature": "liquidation",
                "network": "bnb",
                "chain_id": 56,
                "market_type": "native_chain",
                "protocol": "venus_core",
                "protocol_supported": False,
                "observation_only": True,
            },
            {
                "market_id": "bnb-dex-arbitrage",
                "feature": "dex_arbitrage",
                "network": "bnb",
                "chain_id": 56,
                "market_type": "native_chain",
                "protocol": "pancakeswap_v3",
                "protocol_supported": False,
                "observation_only": True,
            },
        ],
    )

    payload = flash_loan_pools.flash_loan_pools_payload(
        chain_id=56,
        discovered_protocols=["venus_core"],
        protocols=["pancakeswap_v3"],
    )

    assert payload["providers"] == []
    assert payload["summary"]["provider_count"] == 0
    assert payload["protocol_views"][0]["protocol"] == "pancakeswap_v3"
    assert payload["protocol_views"][0]["status"] == "chain_protocol_not_supported"


def test_flash_loan_pools_treats_empty_discovery_as_no_available_protocols(monkeypatch):
    monkeypatch.setattr(
        flash_loan_pools,
        "supported_market_summaries",
        lambda database_url=None: [
            {
                "market_id": "polygon-aave-v3-shadow",
                "feature": "liquidation",
                "network": "polygon",
                "chain_id": 137,
                "market_type": "native_chain",
                "protocol": "aave_v3",
                "protocol_supported": False,
                "observation_only": True,
            },
            {
                "market_id": "polygon-dex-arbitrage",
                "feature": "dex_arbitrage",
                "network": "polygon",
                "chain_id": 137,
                "market_type": "native_chain",
                "protocol": "uniswap_v3",
                "protocol_supported": False,
                "observation_only": True,
            },
        ],
    )

    payload = flash_loan_pools.flash_loan_pools_payload(
        chain_id=137,
        discovered_protocols=[],
        protocols=["aave_v3"],
    )

    assert payload["providers"] == []
    assert payload["summary"]["provider_count"] == 0
    assert payload["protocol_views"][0]["status"] == "chain_protocol_not_supported"


def test_flash_loan_pools_reports_protocol_not_supported_for_selected_chain(monkeypatch):
    monkeypatch.setattr(
        flash_loan_pools,
        "supported_market_summaries",
        lambda database_url=None: [
            {
                "market_id": "bnb-aave-v3",
                "feature": "liquidation",
                "network": "bnb",
                "chain_id": 56,
                "market_type": "native_chain",
                "protocol": "aave_v3",
                "protocol_supported": True,
            },
        ],
    )

    payload = flash_loan_pools.flash_loan_pools_payload(
        chain_id=56,
        protocols=["balancer_v2"],
    )

    assert payload["providers"] == []
    assert payload["protocol_views"][0]["protocol"] == "balancer_v2"
    assert payload["protocol_views"][0]["status"] == "chain_protocol_not_supported"
    assert payload["protocol_views"][0]["status_message"] == "当前链不支持该闪电贷协议"


def test_flash_loan_pools_scopes_market_without_chain_id_by_market_network(monkeypatch):
    monkeypatch.setattr(
        flash_loan_pools,
        "supported_market_summaries",
        lambda database_url=None: [
            {
                "market_id": "hyperliquid-dex-shadow",
                "feature": "dex_arbitrage",
                "network": "hyperliquid",
                "chain_id": None,
                "market_type": "native_chain",
                "protocol": "hyperliquid_native",
                "protocol_supported": False,
                "observation_only": True,
            },
            {
                "market_id": "avalanche-aave-v3",
                "feature": "liquidation",
                "network": "avalanche",
                "chain_id": 43114,
                "market_type": "native_chain",
                "protocol": "aave_v3",
                "protocol_supported": True,
            },
        ],
    )

    payload = flash_loan_pools.flash_loan_pools_payload(
        market_id="hyperliquid-dex-shadow",
        protocols=["aave_v3"],
    )

    assert payload["scope"]["market_id"] == "hyperliquid-dex-shadow"
    assert payload["summary"]["provider_count"] == 0
    assert payload["protocol_views"][0]["status"] == "chain_protocol_not_supported"


def test_flash_loan_pools_does_not_reuse_aave_cache_from_another_chain(tmp_path, monkeypatch):
    aave_cache_path = tmp_path / "aave_reserve_assets.json"
    _write_json(
        aave_cache_path,
        {
            "chain_id": 43114,
            "assets": [
                {
                    "token_symbol": "USDC",
                    "binance_symbol": "USDCUSDT",
                    "is_stable": True,
                    "depth_score_usd": 1_000_000,
                }
            ],
        },
    )
    monkeypatch.setattr(
        flash_loan_pools,
        "supported_market_summaries",
        lambda database_url=None: [
            {
                "market_id": "bnb-aave-v3",
                "feature": "liquidation",
                "network": "bnb",
                "chain_id": 56,
                "market_type": "native_chain",
                "protocol": "aave_v3",
                "protocol_supported": True,
            },
        ],
    )

    payload = flash_loan_pools.flash_loan_pools_payload(
        chain_id=56,
        protocols=["aave_v3"],
        aave_cache_path=aave_cache_path,
    )

    assert payload["protocol_views"][0]["status"] == "protocol_pool_cache_not_available"
    assert payload["protocol_views"][0]["stable_borrow_pools"] == []


def test_flash_loan_pools_reports_missing_aave_cache_as_unavailable(tmp_path, monkeypatch):
    monkeypatch.setattr(
        flash_loan_pools,
        "supported_market_summaries",
        lambda database_url=None: [
            {
                "market_id": "avalanche-aave-v3",
                "feature": "liquidation",
                "network": "avalanche",
                "chain_id": 43114,
                "market_type": "native_chain",
                "protocol": "aave_v3",
                "protocol_supported": True,
            },
        ],
    )

    payload = flash_loan_pools.flash_loan_pools_payload(
        chain_id=43114,
        protocols=["aave_v3"],
        aave_cache_path=tmp_path / "missing_aave_reserve_assets.json",
    )

    assert payload["protocol_views"][0]["status"] == "protocol_pool_cache_not_available"
    assert payload["protocol_views"][0]["status_message"] == "Aave V3 闪电贷可用资金池缓存尚未生成"
    assert payload["protocol_views"][0]["summary"]["stable_borrow_pool_count"] == 0


def test_flash_loan_pools_uses_bnb_venus_market_static_table(tmp_path, monkeypatch):
    venus_path = tmp_path / "venus_market_static_table.json"
    _write_json(
        venus_path,
        {
            "chain_id": 56,
            "generated_at": "2026-08-24T00:00:00Z",
            "block_number": 100,
            "comptroller_address": "0xvenus",
            "markets": [
                {
                    "is_listed": True,
                    "underlying_symbol": "USDC",
                    "underlying_decimals": 6,
                    "underlying_address": "0x" + "1" * 40,
                    "vtoken_address": "0x" + "2" * 40,
                    "vtoken_symbol": "vUSDC",
                    "vtoken_decimals": 8,
                    "cash": 2_000_000_000000,
                    "total_borrows": 1_000_000_000000,
                    "total_supply": 10_000_000_000000,
                    "oracle_price_usd": 1,
                    "stable": True,
                },
                {
                    "is_listed": True,
                    "underlying_symbol": "BNB",
                    "underlying_decimals": 18,
                    "underlying_address": "0x" + "3" * 40,
                    "vtoken_address": "0x" + "4" * 40,
                    "vtoken_symbol": "vBNB",
                    "vtoken_decimals": 8,
                    "cash": 10 * 10**18,
                    "total_borrows": 2 * 10**18,
                    "total_supply": 50 * 10**8,
                    "oracle_price_usd": 600,
                    "stable": False,
                },
            ],
        },
    )
    monkeypatch.setattr(
        flash_loan_pools,
        "supported_market_summaries",
        lambda database_url=None: [
            {
                "market_id": "bnb-venus-core",
                "feature": "liquidation",
                "network": "bnb",
                "chain_id": 56,
                "market_type": "native_chain",
                "protocol": "venus_core",
                "protocol_supported": False,
                "observation_only": True,
            },
        ],
    )

    payload = flash_loan_pools.flash_loan_pools_payload(
        chain_id=56,
        protocols=["venus_core"],
        venus_market_path=venus_path,
    )

    view = payload["protocol_views"][0]
    assert "status" not in view
    assert view["summary"]["stable_borrow_pool_count"] == 1
    assert view["summary"]["non_stable_borrow_pool_count"] == 1
    assert view["stable_borrow_pools"][0]["token_symbol"] == "USDC"
    assert view["stable_borrow_pools"][0]["depth_level"] == "deep"
    assert view["non_stable_borrow_pools"][0]["token_symbol"] == "BNB"
    assert view["cache"]["pool_address"] == ""
    assert view["cache"]["identities"] == [
        {
            "kind": "comptroller",
            "label": "Comptroller",
            "value": "0xvenus",
            "address": "",
            "role": "protocol_interface",
            "is_concrete_pool": False,
        }
    ]


def test_flash_loan_pools_uses_bnb_dex_static_pool_table(tmp_path, monkeypatch):
    dex_path = tmp_path / "bnb_arbitrage_pool_static_table.json"
    _write_json(
        dex_path,
        {
            "network": "bnb",
            "chainId": 56,
            "generatedAt": "2026-08-24T00:00:00Z",
            "router": "0x" + "a" * 40,
            "factory": "0x" + "b" * 40,
            "pools": [
                {
                    "dex": "pancakeswap_v3",
                    "pool": "0x" + "1" * 40,
                    "symbolPair": "USDC:USDT",
                    "token0Symbol": "USDC",
                    "token1Symbol": "USDT",
                    "token0Usd": 800_000,
                    "token1Usd": 900_000,
                    "tvlUsd": 1_700_000,
                    "status": "recommended",
                    "fee": 100,
                },
                {
                    "dex": "pancakeswap_v3",
                    "pool": "0x" + "2" * 40,
                    "symbolPair": "BTCB:WBNB",
                    "token0Symbol": "BTCB",
                    "token1Symbol": "WBNB",
                    "token0Usd": 300_000,
                    "token1Usd": 300_000,
                    "tvlUsd": 600_000,
                    "status": "recommended",
                    "fee": 2500,
                },
            ],
        },
    )
    monkeypatch.setattr(
        flash_loan_pools,
        "supported_market_summaries",
        lambda database_url=None: [
            {
                "market_id": "bnb-dex-arbitrage",
                "feature": "dex_arbitrage",
                "network": "bnb",
                "chain_id": 56,
                "market_type": "native_chain",
                "protocol": "pancakeswap_v3",
                "protocol_supported": True,
            },
        ],
    )

    payload = flash_loan_pools.flash_loan_pools_payload(
        chain_id=56,
        protocols=["pancakeswap_v3"],
        dex_pool_static_path=dex_path,
    )

    view = payload["protocol_views"][0]
    assert "status" not in view
    assert view["summary"]["stable_borrow_pool_count"] == 1
    assert view["summary"]["non_stable_borrow_pool_count"] == 1
    assert view["summary"]["borrow_target_count"] == 2
    assert view["stable_borrow_pools"][0]["token_symbol"] == "USDC:USDT"
    assert view["cache"]["pool_address"] == ""
    assert {item["kind"]: item["value"] for item in view["cache"]["identities"]} == {
        "router": "0x" + "a" * 40,
        "factory": "0x" + "b" * 40,
    }


def test_flash_loan_pools_uses_avalanche_uniswap_v3_static_table(tmp_path, monkeypatch):
    avalanche_path = tmp_path / "avalanche_l1_pool_static_table.json"
    _write_json(
        avalanche_path,
        {
            "network": "avalanche",
            "chainId": 43114,
            "generatedAt": "2026-08-24T00:00:00Z",
            "generatedBlockNumber": 123,
            "router": "0xrouter",
            "factory": "0xfactory",
            "pools": [
                {
                    "tokenX_symbol": "USDC",
                    "tokenY_symbol": "WAVAX",
                    "pools": [
                        {
                            "pool": "0x" + "1" * 40,
                            "token0_symbol": "USDC",
                            "token1_symbol": "WAVAX",
                            "token0Usd": "250000",
                            "token1Usd": "260000",
                            "tvlUsd": "510000",
                            "fee": 500,
                            "liquidity": "123",
                            "quality": "recommended",
                        }
                    ],
                }
            ],
        },
    )
    monkeypatch.setattr(
        flash_loan_pools,
        "supported_market_summaries",
        lambda database_url=None: [
            {
                "market_id": "avalanche-dex-arbitrage",
                "feature": "dex_arbitrage",
                "network": "avalanche",
                "chain_id": 43114,
                "market_type": "native_chain",
                "protocol": "uniswap_v3",
                "protocol_supported": True,
            },
        ],
    )

    payload = flash_loan_pools.flash_loan_pools_payload(
        chain_id=43114,
        protocols=["uniswap_v3"],
        avalanche_v3_pool_static_path=avalanche_path,
    )

    view = payload["protocol_views"][0]
    assert "status" not in view
    assert view["cache"]["source"] == str(avalanche_path)
    assert view["cache"]["pool_address"] == ""
    assert {item["kind"]: item["value"] for item in view["cache"]["identities"]} == {
        "router": "0xrouter",
        "factory": "0xfactory",
    }
    assert view["summary"]["stable_borrow_pool_count"] == 1
    assert view["summary"]["borrow_target_count"] == 1
    assert view["stable_borrow_pools"][0]["token_symbol"] == "USDC:WAVAX"


def test_flash_loan_pools_uses_avalanche_traderjoe_v2_discovery_table(tmp_path, monkeypatch):
    discovery_path = tmp_path / "avalanche_multi_dex_v2_pool_discovery.json"
    _write_json(
        discovery_path,
        {
            "network": "avalanche",
            "chainId": 43114,
            "generatedAt": "2026-08-24T00:00:00Z",
            "pools": [
                {
                    "dex": "trader_joe_v1",
                    "pool": "0x" + "1" * 40,
                    "symbolPair": "USDC:WAVAX",
                    "token0Symbol": "USDC",
                    "token1Symbol": "WAVAX",
                    "token0Usd": "120000",
                    "token1Usd": "130000",
                    "tvlUsd": "250000",
                    "feeBps": 30,
                    "status": "recommended",
                },
                {
                    "dex": "pangolin_v2",
                    "pool": "0x" + "2" * 40,
                    "symbolPair": "USDC:DAI.e",
                    "token0Symbol": "USDC",
                    "token1Symbol": "DAI.e",
                    "tvlUsd": "10",
                },
            ],
        },
    )
    monkeypatch.setattr(
        flash_loan_pools,
        "supported_market_summaries",
        lambda database_url=None: [
            {
                "market_id": "avalanche-traderjoe-v2",
                "feature": "dex_arbitrage",
                "network": "avalanche",
                "chain_id": 43114,
                "market_type": "native_chain",
                "protocol": "traderjoe_v2",
                "protocol_supported": True,
            },
        ],
    )

    payload = flash_loan_pools.flash_loan_pools_payload(
        chain_id=43114,
        protocols=["traderjoe_v2"],
        avalanche_v2_pool_discovery_path=discovery_path,
    )

    view = payload["protocol_views"][0]
    assert "status" not in view
    assert view["summary"]["stable_borrow_pool_count"] == 1
    assert view["summary"]["borrow_target_count"] == 1
    assert view["stable_borrow_pools"][0]["token_symbol"] == "USDC:WAVAX"
    assert view["cache"]["source"] == str(discovery_path)


def test_flash_loan_pools_distinguishes_supported_protocol_without_pool_cache(tmp_path, monkeypatch):
    dex_path = tmp_path / "bnb_arbitrage_pool_static_table.json"
    _write_json(dex_path, {"network": "bnb", "chainId": 56, "pools": []})
    monkeypatch.setattr(
        flash_loan_pools,
        "supported_market_summaries",
        lambda database_url=None: [
            {
                "market_id": "bnb-biswap-v2",
                "feature": "dex_arbitrage",
                "network": "bnb",
                "chain_id": 56,
                "market_type": "native_chain",
                "protocol": "biswap_v2",
                "protocol_supported": True,
            },
        ],
    )

    payload = flash_loan_pools.flash_loan_pools_payload(
        chain_id=56,
        protocols=["biswap_v2"],
        dex_pool_static_path=dex_path,
    )

    assert payload["summary"]["provider_count"] == 1
    assert payload["protocol_views"][0]["status"] == "protocol_pool_cache_not_available"
    assert payload["protocol_views"][0]["status_message"] == "Biswap V2 暂无静态池缓存"


def test_flash_loan_pools_uses_robinhood_uniswap_v3_evidence_pool_table(tmp_path, monkeypatch):
    evidence_dir = tmp_path / "robinhood" / "dex_discovery" / "20260824T000000Z"
    evidence_dir.mkdir(parents=True)
    _write_json(
        evidence_dir / "pool_static_table.json",
        {
            "network": "robinhood",
            "chainId": 4663,
            "generatedAt": "2026-08-24T00:00:00Z",
            "factory": "0x" + "f" * 40,
            "pools": [
                {
                    "chainId": 4663,
                    "protocol": "uniswap_v3",
                    "status": "observable",
                    "pool": "0x" + "1" * 40,
                    "symbolPair": "WETH:USDG",
                    "token0Symbol": "WETH",
                    "token1Symbol": "USDG",
                    "token0": "0x" + "2" * 40,
                    "token1": "0x" + "3" * 40,
                    "fee": "500",
                    "liquidity": "290336060850729630",
                    "evidence": {
                        "blockNumber": 43037858,
                        "factoryQueryVerified": True,
                        "eventVerified": False,
                        "poolCodeVerified": True,
                        "poolStateVerified": True,
                    },
                },
                {
                    "chainId": 4663,
                    "protocol": "uniswap_v2",
                    "status": "observable",
                    "pool": "0x" + "4" * 40,
                    "symbolPair": "WETH:FAKE",
                    "token0Symbol": "WETH",
                    "token1Symbol": "FAKE",
                    "evidence": {"poolCodeVerified": False},
                },
            ],
        },
    )
    monkeypatch.setattr(
        flash_loan_pools,
        "supported_market_summaries",
        lambda database_url=None: [
            {
                "market_id": "robinhood-dex-shadow",
                "feature": "dex_arbitrage",
                "network": "robinhood",
                "chain_id": 4663,
                "market_type": "shadow_chain",
                "protocol": "uniswap_v3",
                "protocol_supported": False,
                "observation_only": True,
            },
        ],
    )

    payload = flash_loan_pools.flash_loan_pools_payload(
        chain_id=4663,
        protocols=["uniswap_v3"],
        robinhood_evidence_root=evidence_dir.parent,
    )

    view = payload["protocol_views"][0]
    assert "status" not in view
    assert view["protocol"] == "uniswap_v3"
    assert view["display_mode"] == "observable_dex_pool"
    assert view["summary"]["stable_borrow_pool_count"] == 0
    assert view["summary"]["borrow_target_count"] == 0
    assert view["summary"]["borrow_asset_count"] == 0
    assert view["summary"]["stable_observable_pool_count"] == 1
    assert view["summary"]["observable_pool_count"] == 1
    assert view["summary"]["flash_capable_pool_count"] == 1
    assert view["summary"]["flashloan_borrowable_pool_count"] == 0
    assert view["summary"]["flashloan_unverified_pool_count"] == 1
    assert view["cache"]["pool_address"] == ""
    assert view["cache"]["identities"][0]["kind"] == "factory"
    assert view["cache"]["identities"][0]["value"] == "0x" + "f" * 40
    assert view["stable_borrow_pools"][0]["token_symbol"] == "WETH:USDG"
    assert view["stable_borrow_pools"][0]["depth_label"] == "可观察"
    assert view["stable_borrow_pools"][0]["borrowable"] is False
    assert view["stable_borrow_pools"][0]["flash_capable"] is True
    assert view["stable_borrow_pools"][0]["flash_borrowable"] is False


def test_flash_loan_pools_defaults_robinhood_to_morpho_when_flash_source_is_available(tmp_path, monkeypatch):
    evidence_dir = tmp_path / "robinhood" / "dex_discovery" / "20260824T000000Z"
    evidence_dir.mkdir(parents=True)
    _write_json(
        evidence_dir / "pool_static_table.json",
        {
            "network": "robinhood",
            "chainId": 4663,
            "pools": [
                {
                    "protocol": "uniswap_v3",
                    "status": "observable",
                    "pool": "0x" + "1" * 40,
                    "symbolPair": "WETH:USDG",
                    "token0Symbol": "WETH",
                    "token1Symbol": "USDG",
                    "evidence": {
                        "factoryQueryVerified": True,
                        "poolCodeVerified": True,
                        "poolStateVerified": True,
                    },
                }
            ],
        },
    )
    monkeypatch.setattr(
        flash_loan_pools,
        "supported_market_summaries",
        lambda database_url=None: [
            {
                "market_id": "robinhood-morpho-blue-flash",
                "feature": "liquidation",
                "network": "robinhood",
                "chain_id": 4663,
                "market_type": "shadow_chain",
                "protocol": "morpho_blue",
                "protocol_supported": False,
                "observation_only": True,
            },
            {
                "market_id": "robinhood-liquidation-shadow",
                "feature": "liquidation",
                "network": "robinhood",
                "chain_id": 4663,
                "market_type": "shadow_chain",
                "protocol": "aave_v3",
                "protocol_supported": False,
                "observation_only": True,
            },
            {
                "market_id": "robinhood-dex-shadow",
                "feature": "dex_arbitrage",
                "network": "robinhood",
                "chain_id": 4663,
                "market_type": "shadow_chain",
                "protocol": "uniswap_v3",
                "protocol_supported": False,
                "observation_only": True,
            },
        ],
    )

    payload = flash_loan_pools.flash_loan_pools_payload(
        chain_id=4663,
        robinhood_evidence_root=evidence_dir.parent,
        robinhood_flash_liquidity_loader=_robinhood_flash_snapshot,
    )

    assert payload["scope"]["protocols"] == []
    assert payload["protocol_views"][0]["protocol"] == "morpho_blue"
    assert "status" not in payload["protocol_views"][0]
    assert payload["protocol_views"][0]["display_mode"] == "flash_loan_source"
    assert payload["protocol_views"][0]["summary"]["borrow_asset_count"] == 2
    assert payload["protocol_views"][0]["summary"]["stable_borrow_pool_count"] == 1
    assert payload["protocol_views"][0]["summary"]["non_stable_borrow_pool_count"] == 1
    assert payload["protocol_views"][0]["summary"]["flashloan_borrowable_pool_count"] == 2


def test_flash_loan_pools_reports_robinhood_supported_protocol_without_evidence(tmp_path, monkeypatch):
    evidence_dir = tmp_path / "robinhood" / "dex_discovery" / "20260824T000000Z"
    evidence_dir.mkdir(parents=True)
    _write_json(
        evidence_dir / "pool_static_table.json",
        {
            "network": "robinhood",
            "chainId": 4663,
            "pools": [
                {
                    "protocol": "uniswap_v3",
                    "status": "observable",
                    "pool": "0x" + "1" * 40,
                    "symbolPair": "WETH:USDG",
                    "evidence": {"factoryQueryVerified": True, "poolCodeVerified": True},
                }
            ],
        },
    )
    monkeypatch.setattr(
        flash_loan_pools,
        "supported_market_summaries",
        lambda database_url=None: [
            {
                "market_id": "robinhood-dex-v2-shadow",
                "feature": "dex_arbitrage",
                "network": "robinhood",
                "chain_id": 4663,
                "market_type": "shadow_chain",
                "protocol": "uniswap_v2",
                "protocol_supported": False,
                "observation_only": True,
            },
        ],
    )

    payload = flash_loan_pools.flash_loan_pools_payload(
        chain_id=4663,
        protocols=["uniswap_v2"],
        robinhood_evidence_root=evidence_dir.parent,
        robinhood_flash_liquidity_loader=lambda: {**_robinhood_flash_snapshot(), "sources": []},
    )

    assert payload["summary"]["provider_count"] == 1
    assert payload["protocol_views"][0]["status"] == "protocol_pool_cache_not_available"
    assert payload["protocol_views"][0]["status_message"] == "Robinhood Uniswap V2 暂无可展示闪电资金来源"


def test_flash_loan_pools_uses_robinhood_morpho_blue_flash_liquidity(monkeypatch):
    monkeypatch.setattr(
        flash_loan_pools,
        "supported_market_summaries",
        lambda database_url=None: [
            {
                "market_id": "robinhood-morpho-blue-flash",
                "feature": "liquidation",
                "display_name": "Robinhood Morpho Blue Flash Loan",
                "network": "robinhood",
                "chain_id": 4663,
                "market_type": "shadow_chain",
                "protocol": "morpho_blue",
                "protocol_supported": False,
                "observation_only": True,
                "pool_address": "0x9D53d5E3bd5E8d4Cbfa6DB1ca238AEA02E651010",
            },
        ],
    )

    payload = flash_loan_pools.flash_loan_pools_payload(
        protocols=["morpho_blue"],
        robinhood_flash_liquidity_loader=_robinhood_flash_snapshot,
    )

    view = payload["protocol_views"][0]
    assert view["protocol"] == "morpho_blue"
    assert view["display_mode"] == "flash_loan_source"
    assert view["cache"]["pool_address"] == ""
    assert view["cache"]["identities"][0]["kind"] == "core"
    assert view["cache"]["identities"][0]["value"] == "0x9D53d5E3bd5E8d4Cbfa6DB1ca238AEA02E651010"
    assert view["summary"]["stable_borrow_pool_count"] == 1
    assert view["summary"]["non_stable_borrow_pool_count"] == 1
    assert view["summary"]["borrow_asset_count"] == 2
    assert view["summary"]["borrow_target_count"] == 2
    assert view["stable_borrow_pools"][0]["token_symbol"] == "USDG"
    assert view["stable_borrow_pools"][0]["fee"] == 0
    assert view["stable_borrow_pools"][0]["pool_address"] == ""
    assert view["stable_borrow_pools"][0]["provider_address"] == "0x9D53d5E3bd5E8d4Cbfa6DB1ca238AEA02E651010"
    assert view["non_stable_borrow_pools"][0]["token_symbol"] == "WETH"
    assert payload["providers"][0]["provider_kind"] == "lending_pool"
    assert payload["providers"][0]["bot_consumable"] is True
    assert payload["providers"][0]["dex_consumable"] is True


def test_flash_loan_pools_keeps_robinhood_testnet_isolated_from_mainnet_flash_liquidity(monkeypatch):
    monkeypatch.setattr(
        flash_loan_pools,
        "supported_market_summaries",
        lambda database_url=None: [
            {
                "market_id": "robinhood-morpho-blue-flash",
                "feature": "liquidation",
                "display_name": "Robinhood Morpho Blue Flash Loan",
                "network": "robinhood",
                "chain_id": 4663,
                "market_type": "shadow_chain",
                "protocol": "morpho_blue",
                "protocol_supported": False,
                "observation_only": True,
            },
            {
                "market_id": "robinhood-testnet-dex-paused",
                "feature": "dex_arbitrage",
                "display_name": "Robinhood Testnet Paused Executor",
                "network": "robinhood-testnet",
                "chain_id": 46630,
                "market_type": "native_chain",
                "protocol": "uniswap_v4",
                "protocol_supported": False,
                "observation_only": True,
            },
        ],
    )

    def fail_if_mainnet_loader_is_used():
        raise AssertionError("Robinhood mainnet flash liquidity loader must not be used for chain 46630")

    payload = flash_loan_pools.flash_loan_pools_payload(
        chain_id=46630,
        robinhood_flash_liquidity_loader=fail_if_mainnet_loader_is_used,
        robinhood_testnet_v4_pool_liquidity_loader=lambda: {
            "ok": False,
            "status": "v4_pool_liquidity_cache_not_available",
            "source": "runtime/evidence/robinhood-testnet/v4_pool_liquidity/latest.json",
            "pools": [],
            "pool_keys": [],
        },
    )

    assert payload["scope"]["chain_id"] == 46630
    assert [provider["chain_id"] for provider in payload["providers"]] == [46630]
    assert payload["protocol_views"][0]["protocol"] == "uniswap_v4"
    assert payload["protocol_views"][0]["status"] == "v4_pool_liquidity_cache_not_available"
    assert payload["protocol_views"][0]["cache"]["source"] == "chains/robinhood-testnet/chain.json"
    assert payload["protocol_views"][0]["cache"]["chain_id"] == 46630
    assert payload["protocol_views"][0]["cache"]["pool_address"] == ""
    assert payload["protocol_views"][0]["cache"]["pool_manager_address"] == "0x8366a39CC670B4001A1121B8F6A443A643e40951"
    assert payload["protocol_views"][0]["cache"]["identities"][0]["kind"] == "pool_manager"
    assert payload["protocol_views"][0]["cache"]["identities"][0]["value"] == "0x8366a39CC670B4001A1121B8F6A443A643e40951"
    assert payload["protocol_views"][0]["cache"]["pool_key_count"] == 0
    assert payload["protocol_views"][0]["summary"]["v4_pool_key_count"] == 0
    assert payload["protocol_views"][0]["summary"]["v4_pool_liquidity_pool_count"] == 0
    assert payload["protocol_views"][0]["processing"]["global_chain_id"] == "chain:46630"
    assert payload["protocol_views"][0]["processing"]["handler"] == (
        "intent_trade.chains.robinhood_testnet.flash_loan_pools:flash_loan_pool_protocol_view"
    )
    assert payload["protocol_views"][0]["chainIsolation"]["usesMainnetRobinhoodCache"] is False
    assert payload["protocol_views"][0]["chainIsolation"]["usesAaveOrMorphoPool"] is False
    assert payload["protocol_views"][0]["chainIsolation"]["poolManagerIsConcretePool"] is False
    assert payload["protocol_views"][0]["chainIsolation"]["poolLiquidityCacheAvailable"] is False
    assert "No Aave or Morpho lending pool is verified" in payload["protocol_views"][0]["status_message"]


def test_flash_loan_pools_uses_robinhood_testnet_v4_pool_liquidity_loader(monkeypatch):
    monkeypatch.setattr(
        flash_loan_pools,
        "supported_market_summaries",
        lambda database_url=None: [
            {
                "market_id": "robinhood-testnet-dex-paused",
                "feature": "dex_arbitrage",
                "display_name": "Robinhood Testnet Paused Executor",
                "network": "robinhood-testnet",
                "chain_id": 46630,
                "market_type": "native_chain",
                "protocol": "uniswap_v4",
                "protocol_supported": False,
                "observation_only": True,
            },
        ],
    )

    def load_v4_pool_liquidity():
        return {
            "ok": True,
            "source": "runtime/evidence/robinhood-testnet/v4_pool_liquidity/latest.json",
            "pool_keys": [{"currency0": "0x" + "1" * 40, "currency1": "0x" + "2" * 40, "fee": 3000}],
            "pools": [
                {
                    "poolId": "0x" + "a" * 64,
                    "token0Symbol": "USDC",
                    "token1Symbol": "WETH",
                    "tvlUsd": "250000",
                    "token0Usd": "200000",
                    "token1Usd": "50000",
                    "liquidity": "123",
                    "fee": 3000,
                    "status": "recommended",
                    "poolKey": {
                        "poolId": "0x" + "a" * 64,
                        "currency0": "0x" + "1" * 40,
                        "currency1": "0x" + "2" * 40,
                        "currency0Metadata": {
                            "address": "0x" + "1" * 40,
                            "symbol": "USDC",
                            "decimals": 6,
                            "poolManagerBalanceRaw": "200000000000",
                            "poolManagerBalance": 200000,
                        },
                        "currency1Metadata": {
                            "address": "0x" + "2" * 40,
                            "symbol": "WETH",
                            "decimals": 18,
                            "poolManagerBalanceRaw": "50000000000000000000",
                            "poolManagerBalance": 50,
                        },
                    },
                }
            ],
        }

    payload = flash_loan_pools.flash_loan_pools_payload(
        chain_id=46630,
        protocols=["uniswap_v4"],
        robinhood_testnet_v4_pool_liquidity_loader=load_v4_pool_liquidity,
    )

    view = payload["protocol_views"][0]
    assert "status" not in view
    assert view["cache"]["pool_address"] == ""
    assert view["cache"]["pool_manager_address"] == "0x8366a39CC670B4001A1121B8F6A443A643e40951"
    assert view["cache"]["asset_count"] == 0
    assert view["cache"]["flash_accounting_asset_count"] == 2
    assert view["cache"]["observable_pool_key_count"] == 1
    assert view["cache"]["pool_key_count"] == 1
    assert view["summary"]["v4_pool_key_count"] == 1
    assert view["summary"]["v4_pool_liquidity_pool_count"] == 1
    assert view["summary"]["stable_borrow_pool_count"] == 1
    assert view["summary"]["non_stable_borrow_pool_count"] == 1
    assert view["summary"]["stable_flash_accounting_source_count"] == 1
    assert view["summary"]["non_stable_flash_accounting_source_count"] == 1
    assert view["summary"]["stable_pool_key_count"] == 1
    assert view["summary"]["borrow_asset_count"] == 2
    assert view["summary"]["verified_lending_borrow_asset_count"] == 0
    assert view["summary"]["flash_accounting_asset_count"] == 2
    assert view["summary"]["flash_borrowable_asset_count"] == 2
    assert view["summary"]["eth_call_verified_flash_asset_count"] == 0
    assert len(view["stable_borrow_pools"]) == 1
    assert view["stable_borrow_pools"][0]["token_symbol"] == "USDC"
    assert view["stable_borrow_pools"][0]["currency_address"] == "0x" + "1" * 40
    assert view["non_stable_borrow_pools"][0]["token_symbol"] == "WETH"
    assert view["borrow_target_universe"] is None
    assert view["stable_v4_flash_source_rows"][0]["token_symbol"] == "USDC"
    assert view["stable_v4_flash_source_rows"][0]["poolmanager_balance_raw"] == "200000000000"
    assert view["stable_v4_flash_source_rows"][0]["flash_borrowable"] is True
    assert view["stable_v4_flash_source_rows"][0]["execution_verified"] is False
    assert view["non_stable_v4_flash_source_rows"][0]["token_symbol"] == "WETH"
    assert view["non_stable_v4_flash_source_rows"][0]["pool_key_count"] == 1
    assert view["stable_pool_key_rows"][0]["token_symbol"] == "USDC:WETH"
    assert view["stable_pool_key_rows"][0]["pool_id"] == "0x" + "a" * 64
    assert view["stable_pool_key_rows"][0]["pool_identifier"] == "0x" + "a" * 64
    assert view["stable_pool_key_rows"][0]["pool_address"] == ""
    assert view["stable_pool_key_rows"][0]["available_liquidity"] == 0
    assert view["stable_pool_key_rows"][0]["pool_liquidity_units"] == 123
    assert view["stable_pool_key_rows"][0]["pool_liquidity_units_raw"] == "123"
    assert view["stable_pool_key_rows"][0]["pool_manager_balance0"] == 200000
    assert view["stable_pool_key_rows"][0]["pool_manager_balance1"] == 50
    assert view["stable_pool_key_rows"][0]["borrowable"] is False
    assert view["stable_pool_key_rows"][0]["flash_borrowable"] is False
    assert view["stable_pool_key_rows"][0]["pool_role"] == "v4_observable_pool_key"
    assert view["stable_pool_key_rows"][0]["flash_borrowable_reason"] == "poolkey_is_observation_only_not_flash_borrowable_asset"
    assert view["chainIsolation"]["poolLiquidityCacheAvailable"] is True


def test_robinhood_testnet_v4_flash_sources_can_come_from_chain_owned_local_json(monkeypatch):
    def load_v4_pool_liquidity():
        return {
            "ok": False,
            "status": "v4_pool_liquidity_cache_not_available",
            "source": "local-v4-flash-accounting-sources.json",
            "payload": {
                "schemaVersion": "robinhood-testnet-v4-pool-liquidity-v1",
                "network": "robinhood-testnet",
                "chainId": 46630,
                "nativePoolManagerBalanceWei": "1000000000000000000",
                "flashAccountingSources": [
                    {
                        "address": "0x" + "3" * 40,
                        "symbol": "WETH",
                        "decimals": 18,
                        "poolManagerBalanceRaw": "2500000000000000000",
                        "poolManagerBalance": 2.5,
                        "source": "local_token_universe_balance_snapshot",
                    }
                ],
            },
            "pools": [],
            "pool_keys": [],
            "blocking_reasons": ["no_poolkey_rows_but_flash_sources_available"],
        }

    payload = flash_loan_pools.flash_loan_pools_payload(
        chain_id=46630,
        protocols=["uniswap_v4"],
        robinhood_testnet_v4_pool_liquidity_loader=load_v4_pool_liquidity,
    )

    view = payload["protocol_views"][0]
    assert "status" not in view
    assert view["summary"]["v4_pool_liquidity_pool_count"] == 0
    assert view["summary"]["flash_accounting_asset_count"] == 2
    assert view["summary"]["executor_supported_flash_asset_count"] == 1
    assert view["summary"]["native_currency_flash_asset_count"] == 1
    assert view["summary"]["eth_call_verified_flash_asset_count"] == 0
    assert view["non_stable_v4_flash_source_rows"][0]["token_symbol"] == "ETH"
    assert view["non_stable_v4_flash_source_rows"][0]["executor_supported"] is False
    assert view["non_stable_v4_flash_source_rows"][1]["token_symbol"] == "WETH"
    assert view["non_stable_v4_flash_source_rows"][1]["source"] == "local_token_universe_balance_snapshot"
    assert view["stable_borrow_pools"] == []
    assert [row["token_symbol"] for row in view["non_stable_borrow_pools"]] == ["ETH", "WETH"]


def test_robinhood_testnet_v4_filters_low_capacity_low_candidate_flash_assets(monkeypatch):
    def load_v4_pool_liquidity():
        return {
            "ok": True,
            "source": "test-v4-liquidity.json",
            "payload": {
                "schemaVersion": "robinhood-testnet-v4-pool-liquidity-v1",
                "network": "robinhood-testnet",
                "chainId": 46630,
                "nativePoolManagerBalanceWei": "0",
                "flashAccountingSources": [
                    {
                        "address": "0x" + "5" * 40,
                        "symbol": "DUST",
                        "decimals": 18,
                        "poolManagerBalanceRaw": "100000000000000000",
                        "poolManagerBalance": 0.1,
                        "poolIds": ["0x" + "c" * 64],
                        "source": "v4_pool_index_active_plus_known_token_balanceOf_poolmanager",
                    }
                ],
            },
            "pools": [{"poolId": "0x" + "c" * 64, "liquidity": "1"}],
            "pool_keys": [{"poolId": "0x" + "c" * 64}],
        }

    payload = flash_loan_pools.flash_loan_pools_payload(
        chain_id=46630,
        protocols=["uniswap_v4"],
        robinhood_testnet_v4_pool_liquidity_loader=load_v4_pool_liquidity,
    )

    view = payload["protocol_views"][0]
    assert view["summary"]["flash_accounting_evidence_asset_count"] == 1
    assert view["summary"]["flash_accounting_excluded_asset_count"] == 1
    assert view["summary"]["flash_accounting_asset_count"] == 0
    assert view["stable_borrow_pools"] == []
    assert view["non_stable_borrow_pools"] == []


def test_robinhood_testnet_v4_borrow_pools_use_scanned_poolmanager_balances_not_pool_keys():
    pool_ids = ["0x" + "a" * 64, "0x" + "b" * 64]

    def load_v4_pool_liquidity():
        return {
            "ok": True,
            "source": "test-v4-liquidity.json",
            "payload": {
                "schemaVersion": "robinhood-testnet-v4-pool-liquidity-v1",
                "network": "robinhood-testnet",
                "chainId": 46630,
                "nativePoolManagerBalanceWei": "1000000000000000000",
                "flashAccountingSources": [
                    {
                        "address": "0x" + "3" * 40,
                        "symbol": "FRAX",
                        "decimals": 18,
                        "poolManagerBalanceRaw": "220643627094560557634862935",
                        "poolManagerBalance": 220643627.09456056,
                        "source": "v4_pool_index_active_token_balanceOf_poolmanager",
                    },
                    {
                        "address": "0x" + "4" * 40,
                        "symbol": "APE",
                        "decimals": 18,
                        "poolManagerBalanceRaw": "10610271461633481666883130",
                        "poolManagerBalance": 10610271.461633481,
                        "source": "v4_pool_index_active_token_balanceOf_poolmanager",
                    },
                ],
            },
            "pools": [{"poolId": pool_id, "liquidity": "1"} for pool_id in pool_ids],
            "pool_keys": [{"poolId": pool_id} for pool_id in pool_ids],
        }

    def load_v4_pool_index():
        return {
            "ok": True,
            "source": "test-v4-pool-index.json",
            "pool_keys": [{"poolId": pool_id} for pool_id in pool_ids],
            "pools": [
                {
                    "poolId": pool_ids[0],
                    "token0Symbol": "ETH",
                    "token1Symbol": "ABC",
                    "token0": "0x0000000000000000000000000000000000000000",
                    "token1": "0x" + "1" * 40,
                },
                {
                    "poolId": pool_ids[1],
                    "token0Symbol": "ETH",
                    "token1Symbol": "XYZ",
                    "token0": "0x0000000000000000000000000000000000000000",
                    "token1": "0x" + "2" * 40,
                },
            ],
        }

    payload = flash_loan_pools.flash_loan_pools_payload(
        chain_id=46630,
        protocols=["uniswap_v4"],
        robinhood_testnet_v4_pool_liquidity_loader=load_v4_pool_liquidity,
        robinhood_testnet_v4_pool_index_loader=load_v4_pool_index,
    )

    view = payload["protocol_views"][0]
    assert view["summary"]["stable_borrow_pool_count"] == 1
    assert view["summary"]["non_stable_borrow_pool_count"] == 2
    assert view["summary"]["stable_flash_accounting_source_count"] == 1
    assert view["summary"]["stable_pool_key_count"] == 0
    assert view["summary"]["non_stable_pool_key_count"] == 2
    assert view["stable_borrow_pools"][0]["token_symbol"] == "FRAX"
    assert [row["token_symbol"] for row in view["non_stable_borrow_pools"]] == ["APE", "ETH"]
    assert [row["token_symbol"] for row in view["non_stable_pool_key_rows"]] == ["ETH:ABC", "ETH:XYZ"]


def test_robinhood_testnet_v4_pool_liquidity_builds_from_local_contract_evidence(tmp_path):
    from dex.intent_trade.chains.robinhood_testnet.v4_pool_liquidity import (
        build_v4_pool_liquidity_from_contract_evidence,
    )

    report_path = tmp_path / "report.json"
    metadata_path = tmp_path / "contracts.json"
    pool_id = "0x" + "a" * 64
    _write_json(
        report_path,
        {
            "runAt": "2026-08-25T16:22:12.883Z",
            "expectedChainId": 46630,
            "rpc": {"chainId": 46630, "blockNumber": 107211039},
            "flashAccountingLiquidity": {
                "address": "0x8366a39CC670B4001A1121B8F6A443A643e40951",
                "lookbackBlocks": 20000,
                "swapLogCount": 10,
                "usableFlashAccountingPoolCount": 1,
                "activePoolIds": [pool_id],
            },
        },
    )
    _write_json(
        metadata_path,
        {
            "network": "robinhood-testnet",
            "chainId": 46630,
            "lastReadinessEvidence": str(report_path),
        },
    )

    payload = build_v4_pool_liquidity_from_contract_evidence(
        contract_metadata_path=metadata_path,
        pool_limit=10,
    )

    assert payload["network"] == "robinhood-testnet"
    assert payload["chainId"] == 46630
    assert payload["poolManager"] == "0x8366a39CC670B4001A1121B8F6A443A643e40951"
    assert payload["activePoolIdCount"] == 1
    assert payload["poolKeys"] == [{"poolId": pool_id, "poolKeyDecoded": False}]
    assert payload["pools"][0]["poolId"] == pool_id
    assert payload["pools"][0]["depthStatus"] == "unpriced_poolid_only"


def test_flash_loan_pools_filters_providers_to_chain_supported_protocols(monkeypatch):
    monkeypatch.setattr(
        flash_loan_pools,
        "supported_market_summaries",
        lambda database_url=None: [
            {
                "market_id": "robinhood-testnet-dex-paused",
                "feature": "dex_arbitrage",
                "display_name": "Robinhood Testnet Paused Executor",
                "network": "robinhood-testnet",
                "chain_id": 46630,
                "market_type": "native_chain",
                "protocol": "uniswap_v4",
                "protocol_supported": False,
                "observation_only": True,
            },
            {
                "market_id": "robinhood-testnet-morpho-wrong",
                "feature": "liquidation",
                "display_name": "Wrong Testnet Morpho",
                "network": "robinhood-testnet",
                "chain_id": 46630,
                "market_type": "native_chain",
                "protocol": "morpho_blue",
                "protocol_supported": False,
                "observation_only": True,
            },
        ],
    )

    payload = flash_loan_pools.flash_loan_pools_payload(chain_id=46630)

    assert [provider["protocol"] for provider in payload["providers"]] == ["uniswap_v4"]
    assert payload["summary"]["provider_count"] == 1


def test_flash_loan_pools_payload_uses_chain_flow_dispatcher():
    source = Path(flash_loan_pools.__file__).read_text(encoding="utf-8")
    start = source.index("def flash_loan_pools_payload(")
    payload_source = source[start:]
    signature = inspect.signature(flash_loan_pools.flash_loan_pools_payload)
    forbidden_parameters = {
        "aave_cache_path",
        "venus_market_path",
        "dex_pool_static_path",
        "robinhood_evidence_root",
        "robinhood_flash_liquidity_loader",
        "avalanche_v3_pool_static_path",
        "avalanche_v2_pool_discovery_path",
    }

    assert "def _aave_protocol_view(" not in source
    assert "def _venus_protocol_view(" not in source
    assert "def _bnb_dex_protocol_view(" not in source
    assert "def _avalanche_dex_protocol_view(" not in source
    assert "def _robinhood_flash_protocol_view(" not in source
    assert "_dispatch_flash_loan_pool_protocol_view(" in payload_source
    assert "_robinhood_flash_protocol_view(" not in payload_source
    assert "_venus_protocol_view(" not in payload_source
    assert "_aave_protocol_view(" not in payload_source
    assert not forbidden_parameters.intersection(signature.parameters)
    assert signature.parameters["provider_options"].kind == inspect.Parameter.VAR_KEYWORD


def test_flash_loan_pools_uses_robinhood_uniswap_v2_flash_swap_liquidity(monkeypatch):
    monkeypatch.setattr(
        flash_loan_pools,
        "supported_market_summaries",
        lambda database_url=None: [
            {
                "market_id": "robinhood-dex-v2-shadow",
                "feature": "dex_arbitrage",
                "display_name": "Robinhood Uniswap V2 Flash Swap",
                "network": "robinhood",
                "chain_id": 4663,
                "market_type": "shadow_chain",
                "protocol": "uniswap_v2",
                "protocol_supported": False,
                "observation_only": True,
                "pool_address": "0x8bCeAA40b9ACdFAEDF85Adf4FF01f5aD6517937f",
            },
        ],
    )

    payload = flash_loan_pools.flash_loan_pools_payload(
        chain_id=4663,
        protocols=["uniswap_v2"],
        robinhood_flash_liquidity_loader=_robinhood_flash_snapshot,
    )

    view = payload["protocol_views"][0]
    assert view["protocol"] == "uniswap_v2"
    assert view["display_mode"] == "flash_swap_source"
    assert view["summary"]["stable_borrow_pool_count"] == 1
    assert view["summary"]["borrow_asset_count"] == 2
    assert view["summary"]["flashloan_borrowable_pool_count"] == 1
    assert view["stable_borrow_pools"][0]["token_symbol"] == "WETH:USDG"
    assert view["stable_borrow_pools"][0]["fee"] == 30
    assert view["stable_borrow_pools"][0]["pool_address"] == "0x8803c117ccae7b5146297876c2a25df135141c4d"
    assert view["stable_borrow_pools"][0]["provider_address"] == "0x8803c117ccae7b5146297876c2a25df135141c4d"
    assert view["cache"]["pool_address"] == ""
    assert view["cache"]["identities"][0]["kind"] == "factory"
    assert view["cache"]["identities"][0]["value"] == "0x8bCeAA40b9ACdFAEDF85Adf4FF01f5aD6517937f"
