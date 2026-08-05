import pytest

from market import observer
from market import observer_common


def _stub_flashloan_premium(monkeypatch, premium_percent=0.05):
    monkeypatch.setattr(
        observer,
        "read_aave_flashloan_premium",
        lambda *args, **kwargs: {
            "premium_bps": int(round(premium_percent * 100)),
            "premium_percent": premium_percent,
            "source": "aave_pool",
            "block_number": 123,
            "read_at": "2026-08-05T00:00:00+00:00",
            "error": None,
        },
    )


def test_aave_reserves_do_not_shrink_binance_velocity_universe(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost:5432/db")
    monkeypatch.setenv("AAVE_POOL_ADDRESS", "0x0000000000000000000000000000000000000001")
    monkeypatch.setenv("AVALANCHE_RPCS", "https://rpc.example")
    monkeypatch.setenv("AVALANCHE_RPC", "https://rpc.example")
    monkeypatch.setenv("BINANCE_REST_BASES", "https://binance.example")
    monkeypatch.setenv("BINANCE_SYMBOL_SELECTION", "velocity")
    monkeypatch.setenv("BINANCE_TOP_SYMBOL_LIMIT", "0")
    monkeypatch.setenv("TRIGGER_EXECUTABLE_SYMBOLS", "AAVE_RESERVES")

    reserve_assets = [
        {
            "token_symbol": "AVAX",
            "token_address": "0x0000000000000000000000000000000000000002",
            "binance_symbol": "AVAXUSDT",
        },
        {
            "token_symbol": "AAVE",
            "token_address": "0x0000000000000000000000000000000000000003",
            "binance_symbol": "AAVEUSDT",
        },
    ]
    exchange_symbols = {"AAVEUSDT", "AVAXUSDT", "BTCUSDT", "ETHUSDT"}

    monkeypatch.setattr(observer, "load_aave_reserve_assets", lambda *args, **kwargs: reserve_assets)
    monkeypatch.setattr(observer, "fetch_binance_usdt_symbols", lambda *args, **kwargs: exchange_symbols)
    _stub_flashloan_premium(monkeypatch)

    config = observer.load_config()

    assert set(config.symbols) == {"AAVEUSDT", "AVAXUSDT", "USDCUSDT"}
    assert set(config.trigger.executable_symbols) == {"AAVEUSDT", "AVAXUSDT"}
    assert set(config.binance_top_symbols) == exchange_symbols


def test_usdc_pool_mode_uses_dex_targets_for_binance_universe(monkeypatch):
    monkeypatch.setenv("BINANCE_SYMBOL_SELECTION", "usdc_pools")
    monkeypatch.setattr(observer, "fetch_binance_usdt_symbols", lambda *args, **kwargs: {"AVAXUSDT", "JOEUSDT"})
    monkeypatch.setattr(
        observer,
        "load_usdc_pool_binance_symbols",
        lambda *args, **kwargs: ["AVAXUSDT", "JOEUSDT"],
    )

    symbols = observer.resolve_binance_top_symbols(["https://binance.example"], 0)

    assert symbols == ["AVAXUSDT", "JOEUSDT"]


def test_binance_symbol_discovery_can_use_fast_price_list(monkeypatch):
    observer._sync_common_overrides()
    calls = []

    def fake_fetch_json(url, timeout_seconds=None):
        calls.append(url)
        return [
            {"symbol": "AVAXUSDT", "price": "6.6"},
            {"symbol": "ETHBTC", "price": "0.02"},
            {"symbol": "DAIUSDT", "price": "0"},
            {"symbol": "AAVEUSDT", "price": "91"},
        ]

    monkeypatch.setenv("BINANCE_SYMBOLS_FAST_PRICE_LIST", "true")
    monkeypatch.setattr(observer_common, "fetch_json", fake_fetch_json)

    symbols = observer_common.fetch_binance_usdt_symbols(["https://binance.example"])

    assert symbols == {"AVAXUSDT", "AAVEUSDT"}
    assert calls == ["https://binance.example/api/v3/ticker/price"]


def test_stable_pool_mode_uses_union_dex_targets_for_binance_universe(monkeypatch):
    monkeypatch.setenv("BINANCE_SYMBOL_SELECTION", "stable_pools")
    monkeypatch.setattr(observer, "fetch_binance_usdt_symbols", lambda *args, **kwargs: {"AVAXUSDT", "JOEUSDT", "LINKUSDT"})
    monkeypatch.setattr(
        observer,
        "load_stable_pool_binance_symbols",
        lambda *args, **kwargs: ["AVAXUSDT", "JOEUSDT", "LINKUSDT"],
    )

    symbols = observer.resolve_binance_top_symbols(["https://binance.example"], 0)

    assert symbols == ["AVAXUSDT", "JOEUSDT", "LINKUSDT"]


def test_aave_borrow_pool_mode_uses_borrow_reserve_targets(monkeypatch):
    monkeypatch.setenv("BINANCE_SYMBOL_SELECTION", "aave_borrow_pools")
    borrow_assets = [
        {
            "token_symbol": "USDC",
            "token_address": "0x0000000000000000000000000000000000000001",
            "binance_symbol": "USDCUSDT",
        }
    ]
    monkeypatch.setattr(observer, "fetch_binance_usdt_symbols", lambda *args, **kwargs: {"JOEUSDT", "LINKUSDT"})
    monkeypatch.setattr(
        observer,
        "load_borrow_pool_binance_symbols",
        lambda *args, **kwargs: ["JOEUSDT", "LINKUSDT"],
    )

    symbols = observer.resolve_dex_borrow_pool_symbols(["https://binance.example"], 0, borrow_assets)

    assert symbols == ["JOEUSDT", "LINKUSDT"]


def test_aave_binance_overlap_mode_uses_public_intersection(monkeypatch):
    monkeypatch.setenv("BINANCE_SYMBOL_SELECTION", "aave_binance_overlap")
    monkeypatch.setenv("AAVE_POOL_ADDRESS", "0x0000000000000000000000000000000000000001")
    reserve_assets = [
        {"binance_symbol": "BTCUSDT"},
        {"binance_symbol": "ETHUSDT"},
        {"binance_symbol": "NOPEUSDT"},
    ]
    monkeypatch.setattr(observer, "load_aave_reserve_assets", lambda *args, **kwargs: reserve_assets)
    monkeypatch.setattr(observer, "fetch_binance_usdt_symbols", lambda *args, **kwargs: {"BTCUSDT", "ETHUSDT", "AVAXUSDT"})

    symbols = observer.resolve_binance_top_symbols(["https://binance.example"], 0)

    assert symbols == ["BTCUSDT", "ETHUSDT"]


def test_explicit_mode_filters_unsupported_symbols(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost:5432/db")
    monkeypatch.setenv("AAVE_POOL_ADDRESS", "0x0000000000000000000000000000000000000001")
    monkeypatch.setenv("AVALANCHE_RPCS", "https://rpc.example")
    monkeypatch.setenv("AVALANCHE_RPC", "https://rpc.example")
    monkeypatch.setenv("BINANCE_REST_BASES", "https://binance.example")
    monkeypatch.setenv("BINANCE_SYMBOL_SELECTION", "explicit")
    monkeypatch.setenv("SYMBOLS", "AVAXUSDT,SAVAXUSDT,USDCUSDT")
    monkeypatch.setattr(
        observer,
        "load_aave_reserve_assets",
        lambda *args, **kwargs: [
            {"token_symbol": "WAVAX", "token_address": "0xavax", "binance_symbol": "AVAXUSDT"},
            {"token_symbol": "SAVAX", "token_address": "0xsavax", "binance_symbol": "SAVAXUSDT"},
            {"token_symbol": "USDC", "token_address": "0xusdc", "binance_symbol": "USDCUSDT"},
        ],
    )
    monkeypatch.setattr(observer, "fetch_binance_usdt_symbols", lambda *args, **kwargs: {"AVAXUSDT", "USDCUSDT"})
    _stub_flashloan_premium(monkeypatch)

    config = observer.load_config()

    assert config.symbols == ["AVAXUSDT", "USDCUSDT"]
    assert config.binance_top_symbols == ["AVAXUSDT"]


def test_binance_scan_profile_sets_short_window_cadence(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost:5432/db")
    monkeypatch.setenv("AVALANCHE_RPCS", "https://rpc.example")
    monkeypatch.setenv("AVALANCHE_RPC", "https://rpc.example")
    monkeypatch.setenv("BINANCE_REST_BASES", "https://binance.example")
    monkeypatch.setenv("BINANCE_SYMBOL_SELECTION", "velocity")
    monkeypatch.setenv("BINANCE_TOP_SYMBOL_LIMIT", "0")
    monkeypatch.setenv("BINANCE_SCAN_PROFILE", "200ms")
    monkeypatch.setenv("BINANCE_CHANGE_WINDOW_SECONDS", "1")
    monkeypatch.setattr(observer, "load_aave_reserve_assets", lambda *args, **kwargs: [])
    monkeypatch.setattr(observer, "fetch_binance_usdt_symbols", lambda *args, **kwargs: {"AVAXUSDT", "ETHUSDT"})
    _stub_flashloan_premium(monkeypatch)

    config = observer.load_config()

    assert config.binance_change_window_seconds == 0.2
    assert config.sample_seconds == 0.2
    assert config.binance_extreme_write_seconds == 0.2
    assert config.binance_pair_price_write_seconds == 0.2


def test_load_config_uses_realtime_fee_thresholds_and_dynamic_profit_floor(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost:5432/db")
    monkeypatch.setenv("AAVE_POOL_ADDRESS", "0x0000000000000000000000000000000000000001")
    monkeypatch.setenv("AVALANCHE_RPCS", "https://rpc.example")
    monkeypatch.setenv("AVALANCHE_RPC", "https://rpc.example")
    monkeypatch.setenv("BINANCE_REST_BASES", "https://binance.example")
    monkeypatch.setenv("BINANCE_SYMBOL_SELECTION", "velocity")
    monkeypatch.setenv("BINANCE_TOP_SYMBOL_LIMIT", "0")
    monkeypatch.setenv("ARBITRAGE_TRADE_FEE_PERCENT", "0.10")
    monkeypatch.setenv("ARBITRAGE_FLASHLOAN_FEE_PERCENT", "1.00")
    monkeypatch.setenv("ARBITRAGE_TARGET_PROFIT_PERCENT", "0.618")
    monkeypatch.setenv("ARBITRAGE_MIN_PAPER_PROFIT_USD", "0")
    monkeypatch.setattr(observer, "load_aave_reserve_assets", lambda *args, **kwargs: [])
    monkeypatch.setattr(observer, "fetch_binance_usdt_symbols", lambda *args, **kwargs: {"AVAXUSDT", "ETHUSDT"})
    _stub_flashloan_premium(monkeypatch, premium_percent=0.05)

    config = observer.load_config()

    expected_spread = (1 - (1 - 0.001) ** 3) * 100 + 0.05 + 0.618
    assert config.trigger.min_up_change_percent == pytest.approx(expected_spread / 2)
    assert config.trigger.min_down_change_percent == pytest.approx(expected_spread / 2)
    assert config.arbitrage.flashloan_fee_percent == 0.05
    assert config.arbitrage.min_window_spread_percent == pytest.approx(expected_spread)
    assert config.arbitrage.min_paper_profit_usd == pytest.approx(6.18)
