from core.config_schema import liquidation_config_health
from core.market_config import liquidation_market_config, supported_market_summaries


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
        "LIQUIDATION_RPCS",
        "LIQUIDATION_RPC",
        "BNB_RPCS",
        "BNB_RPC",
        "AVALANCHE_RPCS",
        "AVALANCHE_RPC",
    ):
        monkeypatch.delenv(name, raising=False)


def test_default_market_is_avalanche_aave(monkeypatch):
    _clear_market_env(monkeypatch)

    market = liquidation_market_config()

    assert market.market_id == "avalanche-aave-v3"
    assert market.chain_id == 43114
    assert market.protocol == "aave_v3"
    assert market.evm_compatible is True
    assert market.protocol_supported is True
    assert market.rpc_urls


def test_bnb_aave_market_uses_bnb_rpc_env(monkeypatch):
    _clear_market_env(monkeypatch)
    monkeypatch.setenv("LIQUIDATION_MARKET_ID", "bnb-aave-v3")
    monkeypatch.setenv("BNB_RPCS", "https://bsc-rpc.example,https://bsc-fallback.example")

    market = liquidation_market_config()

    assert market.market_id == "bnb-aave-v3"
    assert market.network == "bnb"
    assert market.chain_id == 56
    assert market.native_symbol == "BNB"
    assert market.rpc_urls == ("https://bsc-rpc.example", "https://bsc-fallback.example")


def test_config_health_accepts_matching_bnb_chain(monkeypatch):
    _clear_market_env(monkeypatch)
    address = "0x0000000000000000000000000000000000000001"
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost:5432/db")
    monkeypatch.setenv("LIQUIDATION_MARKET_ID", "bnb-aave-v3")
    monkeypatch.setenv("LIQUIDATION_POOL_ADDRESS", address)
    monkeypatch.setenv("LIQUIDATION_EXECUTOR_ADDRESS", address)
    monkeypatch.setenv("LIQUIDATION_EXECUTOR_OWNER_ADDRESS", address)
    monkeypatch.setenv("LIQUIDATION_EXECUTION_ENABLED", "false")

    health = liquidation_config_health(chain_id=56)

    assert health["expected_chain_id"] == 56
    assert health["chain_id"] == 56
    assert health["market"]["market_id"] == "bnb-aave-v3"
    assert not any("chain id" in error for error in health["errors"])


def test_hyperliquid_native_is_not_treated_as_evm_aave(monkeypatch):
    _clear_market_env(monkeypatch)
    monkeypatch.setenv("LIQUIDATION_MARKET_ID", "hyperliquid-native")
    monkeypatch.setenv("LIQUIDATION_PROTOCOL", "hyperliquid_native")

    health = liquidation_config_health(chain_id=43114)

    assert health["market"]["protocol"] == "hyperliquid_native"
    assert health["market"]["protocol_supported"] is False
    assert any("not executable by the current protocol adapter" in error for error in health["errors"])


def test_supported_market_summaries_classifies_migration_type():
    summaries = {row["market_id"]: row for row in supported_market_summaries()}

    assert summaries["bnb-aave-v3"]["migration_type"] == "config_and_new_executor_deployment"
    assert summaries["hyperliquid-native"]["migration_type"] == "new_protocol_adapter_required"
