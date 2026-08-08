from tools.run_market_observer_daemon import configure_market_observer_env


def test_market_observer_daemon_uses_full_binance_velocity_mode(monkeypatch):
    monkeypatch.setenv("BINANCE_SYMBOL_SELECTION", "explicit")
    monkeypatch.delenv("BINANCE_VELOCITY_SIDE_LIMIT", raising=False)

    updates = configure_market_observer_env()

    assert updates["BINANCE_SYMBOL_SELECTION"] == "velocity"
    assert updates["BINANCE_TOP_SYMBOL_LIMIT"] == "0"
    assert updates["BINANCE_VELOCITY_SIDE_LIMIT"] == "50"
