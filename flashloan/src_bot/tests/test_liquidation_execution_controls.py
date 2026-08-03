from eth_account import Account

from execution.liquidation_preflight import evaluate_liquidation_submission
from web import control_panel_liquidation_base as base


LIQUIDATION_ENV_NAMES = [
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
    "LIQUIDATION_REQUIRE_FORK_SIMULATION",
    "LIQUIDATION_FORK_USE_CONFIGURED_EXECUTOR",
    "LIQUIDATION_SWAP_SLIPPAGE_BPS",
    "EXECUTION_SLIPPAGE_BPS",
    "LIQUIDATION_MIN_PROFIT_BASE",
    "LIQUIDATION_MAX_DEBT_TO_COVER",
    "LIQUIDATION_MAX_GAS_COST_USD",
    "LIQUIDATION_MEV_BUFFER_USD",
    "LIQUIDATION_RETRY_BUFFER_USD",
    "LIQUIDATION_MIN_OPERATOR_NET_PROFIT_USD",
    "LIQUIDATION_EXECUTION_PRIORITY_FEE_GWEI",
    "LIQUIDATION_EXECUTION_TIMEOUT_SECONDS",
    "LIQUIDATION_FORK_SIMULATION_TIMEOUT_SECONDS",
    "LIQUIDATION_MAX_PAYLOAD_AGE_SECONDS",
    "LIQUIDATION_MAX_QUOTE_AGE_SECONDS",
    "LIQUIDATION_MIN_DEADLINE_REMAINING_SECONDS",
    "LIQUIDATION_AUTO_PAUSE_ENABLED",
    "LIQUIDATION_AUTO_PAUSE_FAILURE_THRESHOLD",
    "LIQUIDATION_WIDE_SCAN_SECONDS",
    "LIQUIDATION_NEAR_SCAN_SECONDS",
    "LIQUIDATION_WARNING_HEALTH_FACTOR",
    "LIQUIDATION_TRIGGER_HEALTH_FACTOR",
    "LIQUIDATION_MAX_CANDIDATES",
    "LIQUIDATION_BONUS_PERCENT",
    "LIQUIDATION_FLASHLOAN_FEE_PERCENT",
    "LIQUIDATION_DEX_SLIPPAGE_PERCENT",
    "LIQUIDATION_GAS_COST_USD",
    "LIQUIDATION_WATCH_HEALTH_FACTOR",
    "LIQUIDATION_CLOSE_FACTOR",
    "LIQUIDATION_SCAN_PARALLEL_WORKERS",
    "LIQUIDATION_SCAN_BATCH_SIZE",
    "LIQUIDATION_BACKFILL_INTERVAL_SECONDS",
    "LIQUIDATION_RECENT_DISCOVERY_DAYS",
    "LIQUIDATION_BACKFILL_WINDOW_DAYS",
    "LIQUIDATION_ACCOUNT_SCAN_START_DAYS",
    "LIQUIDATION_BLOCK_SECONDS",
    "LIQUIDATION_DISCOVERY_BLOCK_OVERLAP",
    "LIQUIDATION_HEALTH_DISPLAY_LIMIT",
    "LIQUIDATION_BORROW_POOL_DISPLAY_LIMIT",
    "CHAIN_ID",
]


def _clear_liquidation_env(monkeypatch):
    for name in LIQUIDATION_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)


def _set_valid_execution_env(monkeypatch):
    _clear_liquidation_env(monkeypatch)
    account = Account.create()
    address = "0x0000000000000000000000000000000000000001"
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost:5432/db")
    monkeypatch.setenv("AAVE_POOL_ADDRESS", address)
    monkeypatch.setenv("AAVE_PROTOCOL_DATA_PROVIDER_ADDRESS", address)
    monkeypatch.setenv("AAVE_LIQUIDATION_DATA_PROVIDER_ADDRESS", address)
    monkeypatch.setenv("DEX_ROUTER_ADDRESS", address)
    monkeypatch.setenv("LIQUIDATION_EXECUTOR_ADDRESS", address)
    monkeypatch.setenv("LIQUIDATION_EXECUTOR_OWNER_ADDRESS", account.address)
    monkeypatch.setenv("LIQUIDATION_EXECUTION_PRIVATE_KEY", account.key.hex())
    monkeypatch.setenv("LIQUIDATION_EXECUTION_ENABLED", "true")
    monkeypatch.setenv("CHAIN_ID", "43114")


def test_liquidation_execution_controls_reports_invalid_numeric_env_without_raising(monkeypatch):
    _set_valid_execution_env(monkeypatch)
    monkeypatch.setenv("LIQUIDATION_MAX_DEBT_TO_COVER", "not-an-int")
    monkeypatch.setenv("LIQUIDATION_SWAP_SLIPPAGE_BPS", "-1")
    monkeypatch.setenv("LIQUIDATION_EXECUTION_TIMEOUT_SECONDS", "never")
    monkeypatch.setenv("LIQUIDATION_FORK_SIMULATION_TIMEOUT_SECONDS", "never")
    monkeypatch.setenv("LIQUIDATION_MIN_DEADLINE_REMAINING_SECONDS", "-1")
    monkeypatch.setenv("LIQUIDATION_AUTO_PAUSE_FAILURE_THRESHOLD", "0")

    controls = base.liquidation_execution_controls()

    assert controls["config_valid"] is False
    assert "config_invalid" in controls["config_blocked_reasons"]
    assert "LIQUIDATION_MAX_DEBT_TO_COVER must be an integer" in controls["config_errors"]
    assert "LIQUIDATION_SWAP_SLIPPAGE_BPS must be >= 0" in controls["config_errors"]
    assert "LIQUIDATION_EXECUTION_TIMEOUT_SECONDS must be an integer" in controls["config_errors"]
    assert "LIQUIDATION_FORK_SIMULATION_TIMEOUT_SECONDS must be an integer" in controls["config_errors"]
    assert "LIQUIDATION_MIN_DEADLINE_REMAINING_SECONDS must be >= 0" in controls["config_errors"]
    assert "LIQUIDATION_AUTO_PAUSE_FAILURE_THRESHOLD must be >= 1" in controls["config_errors"]
    assert controls["max_debt_to_cover"] == 0
    assert controls["slippage_bps"] == 50
    assert controls["tx_timeout_seconds"] == 180
    assert controls["fork_simulation_timeout_seconds"] == 180
    assert controls["min_deadline_remaining_seconds"] == 60
    assert controls["auto_pause_threshold"] == 3


def test_liquidation_execution_controls_default_operator_profit_floor_is_one_usd(monkeypatch):
    _clear_liquidation_env(monkeypatch)

    controls = base.liquidation_execution_controls()

    assert controls["min_operator_net_profit_usd"] == 1.0
    assert controls["require_fork_simulation"] is True
    assert controls["min_deadline_remaining_seconds"] == 60


def test_invalid_control_config_blocks_submission_as_hard_gate(monkeypatch):
    _set_valid_execution_env(monkeypatch)
    monkeypatch.setenv("LIQUIDATION_MAX_PAYLOAD_AGE_SECONDS", "bad")

    controls = base.liquidation_execution_controls()
    payload = {
        "executor": "0x0000000000000000000000000000000000000001",
        "payload_built_at": "2026-08-02T00:00:00+00:00",
        "request": {
            "user": "0x0000000000000000000000000000000000000002",
            "debtToCover": "1000",
            "minProfitAmount": "100",
        },
        "preflight": {"static_call_required": True, "static_call_passed": True},
        "account_report": {"summary": {"status": "liquidatable"}},
        "dex_quote": {"viable": True, "quote_at": "2026-08-02T00:00:00+00:00"},
    }

    state = evaluate_liquidation_submission(payload, controls)

    assert state["submission_allowed"] is False
    assert state["block_level"] == "hard"
    assert "config_invalid" in state["blocked_reasons"]


def test_liquidation_scan_config_uses_defaults_for_invalid_numeric_env(monkeypatch):
    _clear_liquidation_env(monkeypatch)
    monkeypatch.setenv("LIQUIDATION_WIDE_SCAN_SECONDS", "bad")
    monkeypatch.setenv("LIQUIDATION_NEAR_SCAN_SECONDS", "bad")
    monkeypatch.setenv("LIQUIDATION_WARNING_HEALTH_FACTOR", "bad")
    monkeypatch.setenv("LIQUIDATION_TRIGGER_HEALTH_FACTOR", "bad")
    monkeypatch.setenv("LIQUIDATION_MAX_CANDIDATES", "bad")
    monkeypatch.setenv("LIQUIDATION_BONUS_PERCENT", "bad")
    monkeypatch.setenv("LIQUIDATION_FLASHLOAN_FEE_PERCENT", "bad")
    monkeypatch.setenv("LIQUIDATION_DEX_SLIPPAGE_PERCENT", "bad")
    monkeypatch.setenv("LIQUIDATION_GAS_COST_USD", "bad")
    monkeypatch.setenv("LIQUIDATION_MEV_BUFFER_USD", "bad")
    monkeypatch.setenv("LIQUIDATION_RETRY_BUFFER_USD", "bad")
    monkeypatch.setenv("LIQUIDATION_WATCH_HEALTH_FACTOR", "bad")
    monkeypatch.setenv("LIQUIDATION_CLOSE_FACTOR", "bad")
    monkeypatch.setenv("LIQUIDATION_SCAN_PARALLEL_WORKERS", "bad")
    monkeypatch.setenv("LIQUIDATION_SCAN_BATCH_SIZE", "bad")

    config = base.liquidation_scan_config()

    assert config.wide_scan_seconds == 1800
    assert config.near_scan_seconds == 0.2
    assert config.warning_health_factor == 1.05
    assert config.liquidation_health_factor == 1.0
    assert config.max_candidates == 5000
    assert config.liquidation_bonus_percent == 5.0
    assert config.flashloan_fee_percent == 0.05
    assert config.dex_slippage_percent == 0.10
    assert config.gas_cost_usd == 0
    assert config.mev_buffer_usd == 0
    assert config.retry_buffer_usd == 0
    assert config.watch_health_factor == 1.5
    assert config.close_factor == 0.5
    assert config.parallel_workers == 8
    assert config.batch_size == 100


def test_liquidation_scan_window_helpers_use_safe_defaults(monkeypatch):
    _clear_liquidation_env(monkeypatch)
    monkeypatch.setenv("LIQUIDATION_BACKFILL_INTERVAL_SECONDS", "bad")
    monkeypatch.setenv("LIQUIDATION_RECENT_DISCOVERY_DAYS", "bad")
    monkeypatch.setenv("LIQUIDATION_BACKFILL_WINDOW_DAYS", "bad")
    monkeypatch.setenv("LIQUIDATION_ACCOUNT_SCAN_START_DAYS", "bad")
    monkeypatch.setenv("LIQUIDATION_BLOCK_SECONDS", "bad")
    monkeypatch.setenv("LIQUIDATION_DISCOVERY_BLOCK_OVERLAP", "bad")
    monkeypatch.setenv("LIQUIDATION_HEALTH_DISPLAY_LIMIT", "bad")
    monkeypatch.setenv("LIQUIDATION_BORROW_POOL_DISPLAY_LIMIT", "bad")

    assert base.liquidation_backfill_interval_seconds() == 3600
    assert base.liquidation_recent_discovery_days() == 7
    assert base.liquidation_backfill_window_days() == 7
    assert base.liquidation_account_scan_start_days() == 365
    assert base.liquidation_block_seconds() == 2.0
    assert base.liquidation_discovery_block_overlap() == 1
    assert base.liquidation_health_display_limit() == 200
    assert base.liquidation_borrow_pool_display_limit() == 100
