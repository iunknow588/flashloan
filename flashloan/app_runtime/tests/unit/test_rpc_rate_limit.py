from __future__ import annotations

from dex.intent_trade.chains.robinhood_common import rpc_rate_limit as rrl


def test_dex_rpc_min_interval_seconds_reads_env(monkeypatch):
    monkeypatch.setenv("DEX_RPC_MIN_INTERVAL_SECONDS", "0.7")
    assert rrl.dex_rpc_min_interval_seconds() == 0.7
    monkeypatch.setenv("DEX_RPC_MIN_INTERVAL_SECONDS", "bad")
    assert rrl.dex_rpc_min_interval_seconds() == rrl.DEFAULT_MIN_INTERVAL_SECONDS


def test_throttle_rpc_call_disabled_returns_zero(monkeypatch):
    monkeypatch.setenv("DEX_RPC_MIN_INTERVAL_SECONDS", "0")
    rrl.reset_dex_rpc_rate_limit_for_tests()
    assert rrl.throttle_rpc_call("https://rpc.example/") == 0.0
    assert rrl.throttle_rpc_call("") == 0.0


def test_throttle_rpc_call_waits_between_calls(monkeypatch):
    monkeypatch.setenv("DEX_RPC_MIN_INTERVAL_SECONDS", "0.5")
    sleeps: list[float] = []
    monkeypatch.setattr(rrl.time, "sleep", lambda seconds: sleeps.append(seconds))
    rrl.reset_dex_rpc_rate_limit_for_tests()
    url = "https://rpc.example/unique"
    assert rrl.throttle_rpc_call(url) == 0.0
    assert rrl.throttle_rpc_call(url) >= 0.5
    assert sleeps and sleeps[-1] >= 0.5
