from eth_account import Account

from core.config_schema import liquidation_config_health


def clear_liquidation_env(monkeypatch):
    names = [
        "DATABASE_URL",
        "AAVE_POOL_ADDRESS",
        "AAVE_PROTOCOL_DATA_PROVIDER_ADDRESS",
        "AAVE_LIQUIDATION_DATA_PROVIDER_ADDRESS",
        "DEX_ROUTER_ADDRESS",
        "LIQUIDATION_EXECUTOR_ADDRESS",
        "LIQUIDATION_EXECUTOR_OWNER_ADDRESS",
        "LIQUIDATION_EXECUTION_PRIVATE_KEY",
        "DEPLOYER_PRIVATE_KEY",
        "LIQUIDATION_EXECUTION_ENABLED",
        "LIQUIDATION_SWAP_SLIPPAGE_BPS",
        "LIQUIDATION_MIN_PROFIT_BASE",
        "LIQUIDATION_MAX_DEBT_TO_COVER",
        "LIQUIDATION_MAX_GAS_COST_USD",
        "LIQUIDATION_MEV_BUFFER_USD",
        "LIQUIDATION_RETRY_BUFFER_USD",
        "LIQUIDATION_MIN_OPERATOR_NET_PROFIT_USD",
        "CHAIN_ID",
    ]
    for name in names:
        monkeypatch.delenv(name, raising=False)


def test_liquidation_config_health_reports_missing_execution_config(monkeypatch):
    clear_liquidation_env(monkeypatch)
    monkeypatch.setenv("LIQUIDATION_EXECUTION_ENABLED", "true")

    health = liquidation_config_health(chain_id=43114)

    assert health["valid"] is False
    assert health["execution_blocked"] is True
    assert any("AAVE_POOL_ADDRESS" in error for error in health["errors"])
    assert any("LIQUIDATION_EXECUTOR_ADDRESS" in error for error in health["errors"])


def test_liquidation_config_health_accepts_matching_owner_key(monkeypatch):
    clear_liquidation_env(monkeypatch)
    account = Account.create()
    private_key = account.key.hex()
    owner = account.address
    address = "0x0000000000000000000000000000000000000001"
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost:5432/db")
    monkeypatch.setenv("AAVE_POOL_ADDRESS", address)
    monkeypatch.setenv("AAVE_PROTOCOL_DATA_PROVIDER_ADDRESS", address)
    monkeypatch.setenv("AAVE_LIQUIDATION_DATA_PROVIDER_ADDRESS", address)
    monkeypatch.setenv("DEX_ROUTER_ADDRESS", address)
    monkeypatch.setenv("LIQUIDATION_EXECUTOR_ADDRESS", address)
    monkeypatch.setenv("LIQUIDATION_EXECUTOR_OWNER_ADDRESS", owner)
    monkeypatch.setenv("LIQUIDATION_EXECUTION_PRIVATE_KEY", private_key)
    monkeypatch.setenv("LIQUIDATION_EXECUTION_ENABLED", "true")
    monkeypatch.setenv("CHAIN_ID", "43114")

    health = liquidation_config_health(chain_id=43114)

    assert health["valid"] is True
    assert health["execution_blocked"] is False
    assert health["errors"] == []


def test_liquidation_config_health_blocks_wrong_chain(monkeypatch):
    clear_liquidation_env(monkeypatch)
    monkeypatch.setenv("LIQUIDATION_EXECUTION_ENABLED", "true")

    health = liquidation_config_health(chain_id=1)

    assert health["valid"] is False
    assert any("chain id" in error for error in health["errors"])


def test_liquidation_config_health_reports_invalid_chain_id_without_raising(monkeypatch):
    clear_liquidation_env(monkeypatch)
    monkeypatch.setenv("CHAIN_ID", "not-a-number")

    health = liquidation_config_health()

    assert health["valid"] is False
    assert "CHAIN_ID must be an integer" in health["errors"]
