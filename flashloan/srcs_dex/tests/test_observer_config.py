from market import observer


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
