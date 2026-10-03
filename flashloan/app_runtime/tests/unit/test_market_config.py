import pytest

from core.config_schema import liquidation_config_health
from core.market_config import (
    DEFAULT_BNB_DEX_MARKET_ID,
    DEFAULT_COW_DEX_MARKET_ID,
    DEFAULT_COW_VIRTUAL_CHAIN_ID,
    DEFAULT_ETHEREUM_UNISWAP_V2_ROUTER_ADDRESS,
    DEFAULT_ETHEREUM_DEX_MARKET_ID,
    DEFAULT_POLYGON_QUICKSWAP_V2_ROUTER_ADDRESS,
    DEFAULT_POLYGON_DEX_MARKET_ID,
    DEFAULT_AVALANCHE_DEX_MARKET_ID,
    LEGACY_BNB_DEX_MARKET_ID,
    LEGACY_COW_DEX_MARKET_ID,
    LEGACY_ETHEREUM_DEX_MARKET_ID,
    LEGACY_POLYGON_DEX_MARKET_ID,
    MARKET_TYPE_EXECUTION_RAIL,
    MARKET_TYPE_NATIVE_CHAIN,
    MARKET_TYPE_SHADOW_CHAIN,
    DEFAULT_ROBINHOOD_BOT_MARKET_ID,
    DEFAULT_ROBINHOOD_TESTNET_BOT_MARKET_ID,
    DEFAULT_ROBINHOOD_DEX_MARKET_ID,
    DEFAULT_ROBINHOOD_MORPHO_MARKET_ID,
    DEFAULT_ROBINHOOD_TESTNET_DEX_MARKET_ID,
    LEGACY_ROBINHOOD_BOT_MARKET_ID,
    LEGACY_ROBINHOOD_TESTNET_BOT_MARKET_ID,
    LEGACY_ROBINHOOD_DEX_MARKET_ID,
    LEGACY_ROBINHOOD_MORPHO_MARKET_ID,
    LEGACY_ROBINHOOD_TESTNET_DEX_MARKET_ID,
    liquidation_market_config,
    liquidation_market_config_for,
    liquidation_market_scope_is_explicit,
    supported_market_summaries,
)


CANONICAL_AVALANCHE_AAVE = "liquidation-43114-aave-v3"
CANONICAL_BNB_AAVE = "liquidation-56-aave-v3"
CANONICAL_BNB_VENUS = "liquidation-56-venus-core"
CANONICAL_ETHEREUM_AAVE = "liquidation-1-aave-v3"
CANONICAL_ROBINHOOD_BOT = "liquidation-4663-aave-v3"
CANONICAL_ROBINHOOD_MORPHO = "liquidation-4663-morpho-blue"
CANONICAL_ROBINHOOD_TESTNET_BOT = "liquidation-46630-morpho-blue"
CANONICAL_BNB_DEX = "dex-arbitrage-56-pancakeswap-v3"
CANONICAL_ETHEREUM_DEX = "dex-arbitrage-1-uniswap-v3"
CANONICAL_POLYGON_DEX = "dex-arbitrage-137-uniswap-v3"
CANONICAL_AVALANCHE_DEX = "dex-arbitrage-43114-uniswap-v3"
CANONICAL_COW_DEX = "dex-arbitrage-0-cow-protocol"
CANONICAL_ROBINHOOD_DEX = "dex-arbitrage-4663-uniswap-v4"
CANONICAL_ROBINHOOD_DEX_V3 = "dex-arbitrage-4663-uniswap-v3"
CANONICAL_ROBINHOOD_DEX_V4_LEGACY = "robinhood-dex-shadow"
CANONICAL_ROBINHOOD_DEX_V3_LEGACY = "robinhood-dex-v3-shadow"
CANONICAL_ROBINHOOD_TESTNET_DEX = "dex-arbitrage-46630-uniswap-v4"
CANONICAL_HYPERLIQUID_DEX = "dex-arbitrage-hyperliquid-hyperliquid-native"


def _clear_market_env(monkeypatch):
    for name in (
        "CHAIN_ID",
        "LIQUIDATION_CHAIN_ID",
        "LIQUIDATION_MARKET_ID",
        "LIQUIDATION_NETWORK",
        "LIQUIDATION_PROTOCOL",
        "LIQUIDATION_NATIVE_SYMBOL",
        "LIQUIDATION_POOL_ADDRESS",
        "LIQUIDATION_PROTOCOL_DATA_PROVIDER_ADDRESS",
        "LIQUIDATION_DATA_PROVIDER_ADDRESS",
        "LIQUIDATION_DEX_ROUTER_ADDRESS",
        "LIQUIDATION_MULTICALL3_ADDRESS",
        "LIQUIDATION_RPCS",
        "LIQUIDATION_RPC",
        "BNB_RPCS",
        "BNB_RPC",
        "AVALANCHE_RPCS",
        "AVALANCHE_RPC",
        "ROBINHOOD_RPCS",
        "ROBINHOOD_RPC",
        "ROBINHOOD_RPC_URL",
        "ROBINHOOD_ALCHEMY_RPC_URL",
        "ROBINHOOD_TESTNET_RPCS",
        "ROBINHOOD_TESTNET_RPC",
        "ROBINHOOD_TESTNET_RPC_URL",
        "ROBINHOOD_TESTNET_ALCHEMY_RPC_URL",
    ):
        monkeypatch.delenv(name, raising=False)


def test_default_market_comes_from_chain_manager(monkeypatch):
    _clear_market_env(monkeypatch)

    market = liquidation_market_config()

    assert market.market_id == CANONICAL_ROBINHOOD_TESTNET_BOT
    assert market.legacy_market_id == LEGACY_ROBINHOOD_TESTNET_BOT_MARKET_ID
    assert market.chain_id == 46630
    assert market.protocol == "morpho_blue"
    assert market.evm_compatible is True
    assert market.protocol_supported is False
    assert market.observation_only is True
    assert market.rpc_urls


def test_liquidation_market_scope_explicit_detection(monkeypatch):
    _clear_market_env(monkeypatch)

    assert liquidation_market_scope_is_explicit() is False

    monkeypatch.setenv("LIQUIDATION_PROTOCOL", "venus_core")
    assert liquidation_market_scope_is_explicit() is True

    monkeypatch.delenv("LIQUIDATION_PROTOCOL", raising=False)
    monkeypatch.setenv("CHAIN_ID", "43114")
    assert liquidation_market_scope_is_explicit() is True


def test_bnb_aave_market_uses_bnb_rpc_env(monkeypatch):
    _clear_market_env(monkeypatch)
    monkeypatch.setenv("LIQUIDATION_MARKET_ID", "bnb-aave-v3")
    monkeypatch.setenv("BNB_RPCS", "https://bsc-rpc.example,https://bsc-fallback.example")

    market = liquidation_market_config()

    assert market.market_id == CANONICAL_BNB_AAVE
    assert market.legacy_market_id == "bnb-aave-v3"
    assert market.network == "bnb"
    assert market.chain_id == 56
    assert market.native_symbol == "BNB"
    assert market.rpc_urls[:2] == ("https://bsc-rpc.example", "https://bsc-fallback.example")
    assert "https://bsc-dataseed.binance.org" in market.rpc_urls


def test_bnb_venus_market_defaults_to_observation_mode(monkeypatch):
    _clear_market_env(monkeypatch)
    monkeypatch.setenv("BNB_RPCS", "https://bsc-rpc.example")

    market = liquidation_market_config_for(chain_id=56)

    assert market.market_id == CANONICAL_BNB_VENUS
    assert market.legacy_market_id == "bnb-venus-core"
    assert market.network == "bnb"
    assert market.protocol == "venus_core"
    assert market.protocol_supported is False
    assert market.protocol_adapter == "VenusCoreAdapter"
    assert market.executor_adapter == "VenusLiquidationExecutor"
    assert market.rpc_urls[0] == "https://bsc-rpc.example"
    assert "https://bsc-dataseed.binance.org" in market.rpc_urls
    assert market.dex_router_address == "0x10ED43C718714eb63d5aA57B78B54704E256024E"


def test_config_health_accepts_matching_bnb_chain(monkeypatch):
    _clear_market_env(monkeypatch)
    address = "0x0000000000000000000000000000000000000001"
    monkeypatch.setenv("FLASHLOAN_KV_PATH", "local_store.sqlite3")
    monkeypatch.setenv("LIQUIDATION_MARKET_ID", "bnb-aave-v3")
    monkeypatch.setenv("LIQUIDATION_POOL_ADDRESS", address)
    monkeypatch.setenv("LIQUIDATION_EXECUTOR_ADDRESS", address)
    monkeypatch.setenv("LIQUIDATION_EXECUTOR_OWNER_ADDRESS", address)
    monkeypatch.setenv("LIQUIDATION_EXECUTION_ENABLED", "false")

    health = liquidation_config_health(chain_id=56)

    assert health["expected_chain_id"] == 56
    assert health["chain_id"] == 56
    assert health["market"]["market_id"] == CANONICAL_BNB_AAVE
    assert health["market"]["legacy_market_id"] == "bnb-aave-v3"
    assert not any("chain id" in error for error in health["errors"])


def test_hyperliquid_native_is_not_treated_as_evm_aave(monkeypatch):
    _clear_market_env(monkeypatch)
    monkeypatch.setenv("LIQUIDATION_MARKET_ID", "hyperliquid-native")
    monkeypatch.setenv("LIQUIDATION_PROTOCOL", "hyperliquid_native")

    health = liquidation_config_health(chain_id=43114)

    assert health["market"]["protocol"] == "hyperliquid_native"
    assert health["market"]["protocol_supported"] is False
    assert any("not executable by the current protocol adapter" in warning for warning in health["warnings"])


def test_supported_market_summaries_classifies_migration_type():
    summaries = {row["market_id"]: row for row in supported_market_summaries()}

    assert summaries[CANONICAL_BNB_AAVE]["migration_type"] == "config_and_new_executor_deployment"
    assert summaries[CANONICAL_ROBINHOOD_BOT]["feature"] == "liquidation"
    assert summaries[CANONICAL_ROBINHOOD_BOT]["legacy_market_id"] == LEGACY_ROBINHOOD_BOT_MARKET_ID
    assert summaries[CANONICAL_ROBINHOOD_BOT]["protocol_supported"] is False
    assert summaries[CANONICAL_ROBINHOOD_BOT]["observation_only"] is True
    assert summaries[CANONICAL_ROBINHOOD_MORPHO]["feature"] == "liquidation"
    assert summaries[CANONICAL_ROBINHOOD_MORPHO]["legacy_market_id"] == LEGACY_ROBINHOOD_MORPHO_MARKET_ID
    assert summaries[CANONICAL_ROBINHOOD_MORPHO]["protocol"] == "morpho_blue"
    assert summaries[CANONICAL_ROBINHOOD_MORPHO]["pool_address"] == "0x9D53d5E3bd5E8d4Cbfa6DB1ca238AEA02E651010"
    assert summaries[CANONICAL_ROBINHOOD_TESTNET_BOT]["feature"] == "liquidation"
    assert summaries[CANONICAL_ROBINHOOD_TESTNET_BOT]["legacy_market_id"] == LEGACY_ROBINHOOD_TESTNET_BOT_MARKET_ID
    assert summaries[CANONICAL_ROBINHOOD_TESTNET_BOT]["network"] == "robinhood-testnet"
    assert summaries[CANONICAL_ROBINHOOD_TESTNET_BOT]["chain_id"] == 46630
    assert summaries[CANONICAL_ROBINHOOD_TESTNET_BOT]["observation_only"] is True
    assert summaries[CANONICAL_ROBINHOOD_DEX]["feature"] == "dex_arbitrage"
    assert summaries[CANONICAL_ROBINHOOD_DEX]["legacy_market_id"] == CANONICAL_ROBINHOOD_DEX_V4_LEGACY
    assert summaries[CANONICAL_ROBINHOOD_DEX]["protocol"] == "uniswap_v4"
    assert summaries[CANONICAL_ROBINHOOD_DEX_V3]["legacy_market_id"] == CANONICAL_ROBINHOOD_DEX_V3_LEGACY
    assert summaries[CANONICAL_ROBINHOOD_DEX]["observation_only"] is True
    assert summaries[CANONICAL_ROBINHOOD_TESTNET_DEX]["feature"] == "dex_arbitrage"
    assert summaries[CANONICAL_ROBINHOOD_TESTNET_DEX]["legacy_market_id"] == LEGACY_ROBINHOOD_TESTNET_DEX_MARKET_ID
    assert summaries[CANONICAL_ROBINHOOD_TESTNET_DEX]["network"] == "robinhood-testnet"
    assert summaries[CANONICAL_ROBINHOOD_TESTNET_DEX]["chain_id"] == 46630
    assert summaries[CANONICAL_BNB_DEX]["feature"] == "dex_arbitrage"
    assert summaries[CANONICAL_BNB_DEX]["legacy_market_id"] == LEGACY_BNB_DEX_MARKET_ID
    assert summaries[CANONICAL_BNB_DEX]["network"] == "bnb"
    assert summaries[CANONICAL_BNB_DEX]["protocol"] == "pancakeswap_v3"
    assert summaries[CANONICAL_BNB_DEX]["is_default"] is False
    assert summaries[CANONICAL_COW_DEX]["is_default"] is False
    assert summaries[CANONICAL_COW_DEX]["chain_id"] == DEFAULT_COW_VIRTUAL_CHAIN_ID
    assert summaries[CANONICAL_COW_DEX]["market_type"] == MARKET_TYPE_EXECUTION_RAIL
    assert summaries[CANONICAL_BNB_DEX]["market_type"] == MARKET_TYPE_NATIVE_CHAIN
    assert summaries[CANONICAL_ETHEREUM_DEX]["market_type"] == MARKET_TYPE_NATIVE_CHAIN
    assert summaries[CANONICAL_POLYGON_DEX]["market_type"] == MARKET_TYPE_NATIVE_CHAIN
    assert summaries[CANONICAL_AVALANCHE_DEX]["market_type"] == MARKET_TYPE_NATIVE_CHAIN
    assert summaries[CANONICAL_AVALANCHE_DEX]["is_default"] is False
    assert summaries[CANONICAL_ROBINHOOD_DEX]["market_type"] == MARKET_TYPE_SHADOW_CHAIN
    assert summaries[CANONICAL_ROBINHOOD_DEX]["is_default"] is True
    assert summaries[CANONICAL_ROBINHOOD_TESTNET_DEX]["is_default"] is False
    assert summaries[CANONICAL_HYPERLIQUID_DEX]["legacy_market_id"] == "hyperliquid-dex-shadow"
    assert summaries[CANONICAL_HYPERLIQUID_DEX]["migration_type"] == "dex_native_protocol_placeholder"


def test_robinhood_dex_market_is_shadow_only_and_feature_scoped(monkeypatch):
    _clear_market_env(monkeypatch)
    monkeypatch.setenv("ROBINHOOD_ALCHEMY_RPC_URL", "https://robinhood-alchemy.example")

    market = liquidation_market_config_for(chain_id=4663, feature="dex_arbitrage")

    assert market.market_id == CANONICAL_ROBINHOOD_DEX
    assert market.legacy_market_id == CANONICAL_ROBINHOOD_DEX_V4_LEGACY
    assert market.feature == "dex_arbitrage"
    assert market.network == "robinhood"
    assert market.chain_id == 4663
    assert market.native_symbol == "ETH"
    assert market.wrapped_native_address == "0x0Bd7D308f8E1639FAb988df18A8011f41EAcAD73"
    assert market.multicall3_address == "0x2cAC2D899eCC914d704FeaAE33ac1bF36277DaD1"
    assert market.rpc_urls[0] == "https://robinhood-alchemy.example"
    assert market.protocol_supported is True
    assert market.observation_only is True


def test_robinhood_liquidation_market_prefers_morpho_flash_source(monkeypatch):
    _clear_market_env(monkeypatch)
    monkeypatch.setenv("ROBINHOOD_ALCHEMY_RPC_URL", "https://robinhood-alchemy.example")

    market = liquidation_market_config_for(chain_id=4663, feature="liquidation")

    assert market.market_id == CANONICAL_ROBINHOOD_MORPHO
    assert market.legacy_market_id == LEGACY_ROBINHOOD_MORPHO_MARKET_ID
    assert market.feature == "liquidation"
    assert market.network == "robinhood"
    assert market.chain_id == 4663
    assert market.protocol == "morpho_blue"
    assert market.pool_address == "0x9D53d5E3bd5E8d4Cbfa6DB1ca238AEA02E651010"
    assert market.protocol_adapter == "MorphoBlueFlashLoanAdapter"
    assert market.rpc_urls[0] == "https://robinhood-alchemy.example"
    assert market.protocol_supported is False
    assert market.observation_only is True
    assert market.market_type == MARKET_TYPE_SHADOW_CHAIN


def test_ethereum_and_polygon_dex_markets_use_network_defaults(monkeypatch):
    _clear_market_env(monkeypatch)

    ethereum = liquidation_market_config_for(chain_id=1, feature="dex_arbitrage")
    polygon = liquidation_market_config_for(chain_id=137, feature="dex_arbitrage")

    assert ethereum.market_id == CANONICAL_ETHEREUM_DEX
    assert ethereum.legacy_market_id == LEGACY_ETHEREUM_DEX_MARKET_ID
    assert ethereum.dex_router_address == DEFAULT_ETHEREUM_UNISWAP_V2_ROUTER_ADDRESS
    assert ethereum.rpc_urls
    assert polygon.market_id == CANONICAL_POLYGON_DEX
    assert polygon.legacy_market_id == LEGACY_POLYGON_DEX_MARKET_ID
    assert polygon.dex_router_address == DEFAULT_POLYGON_QUICKSWAP_V2_ROUTER_ADDRESS
    assert polygon.rpc_urls


def test_cow_dex_market_keeps_virtual_chain_zero(monkeypatch):
    _clear_market_env(monkeypatch)

    market = liquidation_market_config_for(
        DEFAULT_COW_DEX_MARKET_ID,
        chain_id=DEFAULT_COW_VIRTUAL_CHAIN_ID,
        feature="dex_arbitrage",
    )

    assert market.chain_id == 0
    assert market.market_id == CANONICAL_COW_DEX
    assert market.legacy_market_id == LEGACY_COW_DEX_MARKET_ID
    assert market.network == "cow"
    assert market.market_type == MARKET_TYPE_EXECUTION_RAIL
    assert market.protocol == "cow_protocol"
    assert market.is_default is False


def test_supported_market_summaries_prefers_database_rows_when_available(monkeypatch):
    db_rows = [
        {
            "market_id": "db-robinhood-dex",
            "chain_id": 4663,
            "network": "robinhood",
            "feature": "dex_arbitrage",
            "protocol": "uniswap_v4",
            "native_symbol": "ETH",
        }
    ]

    import core.db.storage_chain_markets as storage_chain_markets

    monkeypatch.setattr(storage_chain_markets, "load_chain_market_summaries", lambda database_url: db_rows)

    summaries = supported_market_summaries("local_store.sqlite3")

    assert summaries[0]["market_id"] == CANONICAL_ROBINHOOD_DEX
    assert summaries[0]["legacy_market_id"] == "db-robinhood-dex"
    assert summaries[0]["protocol_id"] == "uniswap_v4"


def test_supported_market_summaries_falls_back_when_database_unavailable(monkeypatch):
    import core.db.storage_chain_markets as storage_chain_markets

    def fail(_database_url):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(storage_chain_markets, "load_chain_market_summaries", fail)

    summaries = {row["market_id"]: row for row in supported_market_summaries("local_store_disabled.sqlite3")}

    assert CANONICAL_ROBINHOOD_DEX in summaries
    assert summaries[CANONICAL_ROBINHOOD_DEX]["legacy_market_id"] == CANONICAL_ROBINHOOD_DEX_V4_LEGACY
    assert CANONICAL_ROBINHOOD_BOT in summaries
    assert summaries[CANONICAL_ROBINHOOD_BOT]["legacy_market_id"] == LEGACY_ROBINHOOD_BOT_MARKET_ID


def test_supported_market_summaries_short_circuits_repeated_database_failures(monkeypatch):
    import core.db.storage_chain_markets as storage_chain_markets

    database_url = "local_store_endpoint_disabled.sqlite3"
    calls = []

    def fail(database_url):
        calls.append(database_url)
        raise RuntimeError("the endpoint has been disabled; enable it using the API and retry")

    monkeypatch.setattr(storage_chain_markets, "load_chain_market_summaries", fail)

    first = supported_market_summaries(database_url)
    second = supported_market_summaries(database_url)

    assert first == second
    assert len(calls) == 2
    assert CANONICAL_ROBINHOOD_DEX in {row["market_id"] for row in first}
    assert CANONICAL_ROBINHOOD_BOT in {row["market_id"] for row in first}


def test_market_scope_rejects_chain_and_feature_mismatch(monkeypatch):
    _clear_market_env(monkeypatch)

    with pytest.raises(ValueError, match="belongs to chain_id"):
        liquidation_market_config_for("bnb-venus-core", chain_id=43114)
    ethereum = liquidation_market_config_for(chain_id=1)
    assert ethereum.market_id == CANONICAL_ETHEREUM_AAVE
    assert ethereum.legacy_market_id == "ethereum-aave-v3-shadow"
    assert ethereum.protocol_supported is False
    assert ethereum.observation_only is True


def test_robinhood_market_never_inherits_avalanche_router(monkeypatch):
    _clear_market_env(monkeypatch)

    market = liquidation_market_config_for(
        DEFAULT_ROBINHOOD_DEX_MARKET_ID,
        chain_id=4663,
        feature="dex_arbitrage",
    )

    assert market.dex_router_address == ""


def test_robinhood_testnet_dex_is_live_execution_chain(monkeypatch):
    _clear_market_env(monkeypatch)

    market = liquidation_market_config_for(chain_id=46630, feature="dex_arbitrage")

    assert market.market_id == CANONICAL_ROBINHOOD_TESTNET_DEX
    assert market.legacy_market_id == LEGACY_ROBINHOOD_TESTNET_DEX_MARKET_ID
    assert market.network == "robinhood-testnet"
    assert market.chain_id == 46630
    assert market.protocol == "uniswap_v4"
    assert market.rpc_urls[0] == "https://rpc.testnet.chain.robinhood.com/rpc"
    assert market.explorer_url == "https://explorer.testnet.chain.robinhood.com"
    assert market.protocol_supported is True
    assert market.observation_only is False
    assert market.dex_router_address == ""


def test_robinhood_testnet_liquidation_shadow_is_available_to_bot(monkeypatch):
    _clear_market_env(monkeypatch)

    market = liquidation_market_config_for(chain_id=46630, feature="liquidation")

    assert market.market_id == CANONICAL_ROBINHOOD_TESTNET_BOT
    assert market.legacy_market_id == LEGACY_ROBINHOOD_TESTNET_BOT_MARKET_ID
    assert market.feature == "liquidation"
    assert market.network == "robinhood-testnet"
    assert market.chain_id == 46630
    assert market.protocol == "morpho_blue"
    assert market.protocol_supported is False
    assert market.observation_only is True
    assert market.rpc_urls[0] == "https://rpc.testnet.chain.robinhood.com/rpc"


def test_market_scan_policy_prefers_database_rows(monkeypatch):
    import core.db.storage_chain_markets as storage_chain_markets
    from core.market_scan_policy import market_scan_refresh_profile

    monkeypatch.setattr(
        storage_chain_markets,
        "load_market_scan_policies",
        lambda _database_url, **_kwargs: [
            {
                "pool_tier": "normal",
                "refresh_interval_seconds": 77,
                "enabled": True,
            },
            {
                "pool_tier": "high_frequency",
                "refresh_interval_seconds": 33,
                "enabled": True,
            },
            {
                "pool_tier": "core",
                "refresh_interval_seconds": 4,
                "enabled": True,
            },
        ],
    )

    profile = market_scan_refresh_profile(
        "bnb-venus-core",
        database_url="local_store.sqlite3",
    )

    assert profile == {
        "borrow_health_refresh_seconds": 77.0,
        "high_frequency_refresh_seconds": 33.0,
        "core_opportunity_refresh_seconds": 4.0,
    }


def test_market_analysis_uses_chain_specific_dex_router(monkeypatch):
    _clear_market_env(monkeypatch)

    from core.dex_routing import dex_router_context_for

    context = dex_router_context_for(
        {
            "market_id": DEFAULT_BNB_DEX_MARKET_ID,
            "chain_id": 56,
        }
    )

    assert context["source"] == "market_config"
    assert context["market_id"] == CANONICAL_BNB_DEX
    assert context["chain_id"] == 56
    assert context["router_address"] == "0x10ED43C718714eb63d5aA57B78B54704E256024E"


def test_dex_quote_rpc_urls_prefers_router_context_urls():
    from core.dex_routing import dex_quote_rpc_urls_for

    assert dex_quote_rpc_urls_for({"rpc_urls": ["https://rpc.example", ""]}, ["https://fallback.example"]) == [
        "https://rpc.example",
    ]
    assert dex_quote_rpc_urls_for({}, ["https://fallback.example"]) == ["https://fallback.example"]
