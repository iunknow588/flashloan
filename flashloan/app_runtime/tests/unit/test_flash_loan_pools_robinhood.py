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
    assert view["summary"]["flash_accounting_pool_key_count"] == 2
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


def test_robinhood_testnet_v4_known_token_pool_ids_read_static_allowlist(tmp_path, monkeypatch):
    from dex.intent_trade.chains.robinhood_testnet import v4_pool_liquidity

    allowlist_path = tmp_path / "v4_token_allowlist.json"
    allowlist_address = "0x1111111111111111111111111111111111111111"
    _write_json(
        allowlist_path,
        {
            "network": "robinhood-testnet",
            "tokens": {
                "fake": {
                    "symbol": "FAKE",
                    "address": allowlist_address,
                    "decimals": 18,
                }
            },
        },
    )

    def fake_chain_data_file_path(network, key, **kwargs):
        if key == "v4_token_allowlist":
            return allowlist_path
        return None

    monkeypatch.setattr(v4_pool_liquidity, "chain_data_file_path", fake_chain_data_file_path)

    known = v4_pool_liquidity._known_token_pool_ids()

    assert allowlist_address.lower() in known
    assert "v4-token-allowlist.fake" in known[allowlist_address.lower()]


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


def test_flash_loan_pools_uses_robinhood_mainnet_v4_pool_index(monkeypatch):
    monkeypatch.setattr(
        flash_loan_pools,
        "supported_market_summaries",
        lambda database_url=None: [
            {
                "market_id": "dex-arbitrage-4663-uniswap-v4",
                "feature": "dex_arbitrage",
                "display_name": "Robinhood Uniswap V4",
                "network": "robinhood",
                "chain_id": 4663,
                "market_type": "shadow_chain",
                "protocol": "uniswap_v4",
                "protocol_supported": False,
                "observation_only": True,
            },
        ],
    )

    def load_v4_index():
        return {
            "schemaVersion": "robinhood-v4-pool-index-v1",
            "chainId": 4663,
            "generatedAt": "2026-09-11T03:28:22Z",
            "generatedBlockNumber": 59938454,
            "poolManager": "0x8366a39CC670B4001A1121B8F6A443A643e40951",
            "pools": [
                {
                    "protocol": "uniswap_v4",
                    "poolId": "0x" + "a" * 64,
                    "pool": "0x" + "a" * 64,
                    "poolManager": "0x8366a39CC670B4001A1121B8F6A443A643e40951",
                    "poolKeyDecoded": True,
                    "poolKey": {
                        "currency0": "0x0Bd7D308f8E1639FAb988df18A8011f41EAcAD73",
                        "currency1": "0x5fc5360D0400a0Fd4f2af552ADD042D716F1d168",
                        "fee": 200,
                    },
                    "token0": "0x0Bd7D308f8E1639FAb988df18A8011f41EAcAD73",
                    "token1": "0x5fc5360D0400a0Fd4f2af552ADD042D716F1d168",
                    "token0Symbol": "0x0Bd7D308",
                    "token1Symbol": "0x5fc5360D",
                    "symbolPair": "0x0Bd7D308:0x5fc5360D",
                    "fee": 200,
                    "status": "initialized_pool",
                    "liquidity": "",
                    "tvlUsd": 0,
                    "initializeBlockNumber": 11334809,
                }
            ],
        }

    payload = flash_loan_pools.flash_loan_pools_payload(
        chain_id=4663,
        protocols=["uniswap_v4"],
        robinhood_v4_pool_index_loader=load_v4_index,
    )

    view = payload["protocol_views"][0]
    assert "status" not in view
    assert view["display_mode"] == "observable_dex_pool"
    assert view["summary"]["observable_pool_count"] == 2
    assert view["summary"]["borrow_asset_count"] == 2
    assert view["summary"]["observed_borrow_token_count"] == 2
    assert view["summary"]["v4_pool_key_count"] == 1
    assert [row["token_symbol"] for row in view["stable_borrow_pools"]] == ["USDG"]
    assert view["non_stable_borrow_pools"][0]["token_symbol"] == "ETH"
    assert view["non_stable_borrow_pools"][0]["pool_count"] == 1
    assert view["non_stable_borrow_pools"][0]["paired_symbol_count"] == 1
    assert view["cache"]["identities"][0]["kind"] == "pool_manager"
    assert view["cache"]["identities"][0]["value"] == "0x8366a39CC670B4001A1121B8F6A443A643e40951"


def test_flash_loan_pools_robinhood_mainnet_v4_rejects_testnet_scoped_index(monkeypatch):
    monkeypatch.setattr(
        flash_loan_pools,
        "supported_market_summaries",
        lambda database_url=None: [
            {
                "market_id": "dex-arbitrage-4663-uniswap-v4",
                "feature": "dex_arbitrage",
                "display_name": "Robinhood Uniswap V4",
                "network": "robinhood",
                "chain_id": 4663,
                "market_type": "shadow_chain",
                "protocol": "uniswap_v4",
                "protocol_supported": False,
                "observation_only": True,
            },
        ],
    )

    def load_v4_index():
        return {
            "schemaVersion": "robinhood-v4-pool-index-v1",
            "network": "robinhood-testnet",
            "chainId": 46630,
            "generatedAt": "2026-09-11T03:28:22Z",
            "generatedBlockNumber": 59938454,
            "poolManager": "0x8366a39CC670B4001A1121B8F6A443A643e40951",
            "pools": [
                {
                    "protocol": "uniswap_v4",
                    "poolId": "0x" + "a" * 64,
                    "pool": "0x" + "a" * 64,
                    "poolManager": "0x8366a39CC670B4001A1121B8F6A443A643e40951",
                    "token0": "0x0Bd7D308f8E1639FAb988df18A8011f41EAcAD73",
                    "token1": "0x5fc5360D0400a0Fd4f2af552ADD042D716F1d168",
                    "token0Symbol": "WETH",
                    "token1Symbol": "USDG",
                    "symbolPair": "WETH:USDG",
                    "fee": 200,
                    "status": "initialized_pool",
                }
            ],
        }

    payload = flash_loan_pools.flash_loan_pools_payload(
        chain_id=4663,
        protocols=["uniswap_v4"],
        robinhood_v4_pool_index_loader=load_v4_index,
    )

    view = payload["protocol_views"][0]
    assert view["status"] == "protocol_pool_cache_not_available"
    assert "not scoped" in view.get("status_message", "")
    assert view["cache"]["asset_count"] == 0
    assert view["stable_borrow_pools"] == []
    assert view["non_stable_borrow_pools"] == []
