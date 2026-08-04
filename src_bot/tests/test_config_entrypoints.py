from datetime import datetime, timezone

from db import storage_common
from market import aave_reserve_cache, dex_target_loaders
from market.observer_common import env_float, env_int
from strategy import build_executable_signal
from tools.quote_dynamic_candidates import dynamic_quote_cli_defaults
from web import control_panel_liquidation_base


def test_build_executable_signal_uses_defaults_for_invalid_trigger_env(monkeypatch):
    monkeypatch.setenv("TRIGGER_MIN_UP_CHANGE_PERCENT", "bad")
    monkeypatch.setenv("TRIGGER_MIN_DOWN_CHANGE_PERCENT", "also-bad")

    candidate = build_executable_signal.build_candidate(
        {
            "observed_at": "2026-08-02T00:00:00+00:00",
            "window_seconds": 1.0,
            "sample_count": 10,
            "x_symbol": "AVAXUSDT",
            "x_change_percent": 1.2,
            "x_start_price": 20.0,
            "x_end_price": 20.24,
            "y_symbol": "AAVEUSDT",
            "y_change_percent": -1.1,
            "y_start_price": 100.0,
            "y_end_price": 98.9,
        }
    )

    assert candidate["min_window_spread_percent"] == 2.0


def test_executable_symbols_uses_default_reserve_limit_for_invalid_env(monkeypatch):
    captured = {}
    monkeypatch.setenv("TRIGGER_EXECUTABLE_SYMBOLS", "AAVE")
    monkeypatch.setenv("AAVE_RESERVE_SYMBOL_LIMIT", "bad")

    def fake_reserve_symbols(rpc_url, pool_address, limit):
        captured["limit"] = limit
        return {"AVAXUSDT"}

    monkeypatch.setattr(build_executable_signal, "load_aave_reserve_symbols", fake_reserve_symbols)

    assert build_executable_signal.executable_symbols() == {"AVAXUSDT"}
    assert captured["limit"] == 1000


def test_dynamic_quote_cli_defaults_use_safe_env_parsing(monkeypatch):
    monkeypatch.setenv("DYNAMIC_QUOTE_USD_AMOUNT", "bad")
    monkeypatch.setenv("DYNAMIC_SLIPPAGE_BPS", "-10")
    monkeypatch.setenv("DYNAMIC_GAS_COST_USDC", "bad")
    monkeypatch.setenv("DYNAMIC_MIN_NET_PROFIT_USDC", "bad")
    monkeypatch.setenv("DYNAMIC_SAFETY_MARGIN_USDC", "-1")

    defaults = dynamic_quote_cli_defaults()

    assert defaults == {
        "usd_amount": 100.0,
        "slippage_bps": 50,
        "gas_cost_usdc": 0.0,
        "min_net_profit_usdc": 0.01,
        "safety_margin_usdc": 0.0,
    }


def test_dex_target_symbol_loaders_use_defaults_for_invalid_env(monkeypatch):
    captured = {}
    monkeypatch.setenv("DEX_USDC_POOL_FROM_BLOCK", "bad")
    monkeypatch.setenv("DEX_USDC_POOL_SCAN_CHUNK_SIZE", "bad")
    monkeypatch.setenv("DEX_USDC_TARGET_CACHE_SECONDS", "bad")

    def fake_usdc_assets(rpc_urls, *, from_block, chunk_size, refresh_seconds):
        captured.update(
            {
                "rpc_urls": rpc_urls,
                "from_block": from_block,
                "chunk_size": chunk_size,
                "refresh_seconds": refresh_seconds,
            }
        )
        return [{"binance_symbol": "AVAXUSDT"}]

    monkeypatch.setattr(dex_target_loaders, "load_usdc_pool_assets", fake_usdc_assets)

    assert dex_target_loaders.load_usdc_pool_binance_symbols("https://rpc.example") == ["AVAXUSDT"]
    assert captured == {
        "rpc_urls": "https://rpc.example",
        "from_block": 0,
        "chunk_size": 50000,
        "refresh_seconds": 3600,
    }


def test_aave_reserve_cache_uses_default_refresh_for_invalid_env(monkeypatch, tmp_path):
    cache_path = tmp_path / "aave_reserve_assets.json"
    aave_reserve_cache.write_cache(
        cache_path,
        {
            "schema_version": 4,
            "refreshed_at": datetime.now(timezone.utc).isoformat(),
            "assets": [{"symbol": "WAVAX", "depth_score_usd": 1.0}],
        },
    )
    monkeypatch.setenv("AAVE_RESERVE_CACHE_SECONDS", "bad")

    assets = aave_reserve_cache.load_aave_reserve_assets(
        "https://rpc.example",
        "",
        cache_path=cache_path,
    )

    assert assets == [{"symbol": "WAVAX", "depth_score_usd": 1.0}]


def test_database_pool_bounds_use_safe_env_parsing(monkeypatch):
    monkeypatch.setenv("DATABASE_POOL_MIN_SIZE", "bad")
    monkeypatch.setenv("DATABASE_POOL_MAX_SIZE", "0")

    assert storage_common._pool_bounds() == (2, 10)


def test_observer_common_env_helpers_use_shared_safe_parsing(monkeypatch):
    monkeypatch.setenv("SAMPLE_FLOAT", "bad")
    monkeypatch.setenv("SAMPLE_INT", "-5")

    assert env_float("SAMPLE_FLOAT", 1.5) == 1.5
    assert env_int("SAMPLE_INT", 3, minimum=1) == 3


def test_liquidation_runtime_config_uses_safe_env_parsing(monkeypatch, tmp_path):
    monkeypatch.setattr(control_panel_liquidation_base, "LIQUIDATION_CONFIG_PATH", tmp_path / "missing.json")
    monkeypatch.setenv("LIQUIDATION_RETENTION_DAYS", "bad")
    monkeypatch.setenv("LIQUIDATION_SCAN_INTERVAL_SECONDS", "also-bad")
    monkeypatch.setenv("LIQUIDATION_DISCOVERY_INTERVAL_SECONDS", "still-bad")

    config = control_panel_liquidation_base.liquidation_runtime_config()

    assert config == {
        "LIQUIDATION_RETENTION_DAYS": 365,
        "LIQUIDATION_SCAN_INTERVAL_SECONDS": 300,
        "LIQUIDATION_DISCOVERY_INTERVAL_SECONDS": 3600,
    }


def test_liquidation_base_schema_errors_are_redacted(monkeypatch):
    database_url = "postgresql://user:secret-pass@example.com:5432/db?token=abc123"
    private_key = "0x" + "6" * 64
    monkeypatch.setenv("DATABASE_URL", database_url)

    def fail_schema(_database_url):
        raise RuntimeError(f"schema failed: {database_url} private_key={private_key}")

    monkeypatch.setattr(control_panel_liquidation_base, "ensure_database_schema", fail_schema)

    payload = control_panel_liquidation_base.schema_status_payload()
    error = payload["error"]

    assert database_url not in error
    assert private_key not in error
    assert "secret-pass" not in error
    assert "abc123" not in error
    assert "[REDACTED]" in error

