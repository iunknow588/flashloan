import time
from types import SimpleNamespace

import pytest

from market.observer import PriceState, should_compute_conversion_profits
from market import observer_runtime


@pytest.mark.asyncio
async def test_window_extremes_keeps_losers_negative_when_all_symbols_rise():
    state = PriceState()
    start_ms = int(time.time() * 1000) - 100
    end_ms = start_ms + 50
    await state.update_binance("AAAUSDT", 100.0, start_ms, "ws")
    await state.update_binance("BBBUSDT", 100.0, start_ms, "ws")
    await state.update_binance("AAAUSDT", 101.0, end_ms, "ws")
    await state.update_binance("BBBUSDT", 100.5, end_ms, "ws")

    extremes = await state.window_extremes(
        ["AAAUSDT", "BBBUSDT"],
        window_seconds=999,
        limit=5,
    )

    assert [row["symbol"] for row in extremes["top"]] == ["AAAUSDT", "BBBUSDT"]
    assert extremes["bottom"] == []


@pytest.mark.asyncio
async def test_window_extremes_keeps_gainers_positive_when_all_symbols_fall():
    state = PriceState()
    start_ms = int(time.time() * 1000) - 100
    end_ms = start_ms + 50
    await state.update_binance("AAAUSDT", 100.0, start_ms, "ws")
    await state.update_binance("BBBUSDT", 100.0, start_ms, "ws")
    await state.update_binance("AAAUSDT", 99.0, end_ms, "ws")
    await state.update_binance("BBBUSDT", 99.5, end_ms, "ws")

    extremes = await state.window_extremes(
        ["AAAUSDT", "BBBUSDT"],
        window_seconds=999,
        limit=5,
    )

    assert extremes["top"] == []
    assert [row["symbol"] for row in extremes["bottom"]] == ["AAAUSDT", "BBBUSDT"]


@pytest.mark.asyncio
async def test_window_extremes_reports_market_divergence_index():
    state = PriceState()
    start_ms = int(time.time() * 1000) - 100
    end_ms = start_ms + 50
    symbols = [f"S{i}USDT" for i in range(8)]
    for symbol in symbols:
        await state.update_binance(symbol, 100.0, start_ms, "ws")
    for symbol in symbols[:3]:
        await state.update_binance(symbol, 101.0, end_ms, "ws")
    for symbol in symbols[3:6]:
        await state.update_binance(symbol, 99.0, end_ms, "ws")
    for symbol in symbols[6:]:
        await state.update_binance(symbol, 100.0, end_ms, "ws")

    extremes = await state.window_extremes(symbols, window_seconds=999, limit=8)

    assert extremes["gainer_count"] == 3
    assert extremes["loser_count"] == 3
    assert extremes["observation_universe_size"] == 8
    assert extremes["market_divergence_index"] == pytest.approx(9 / 8)
    assert [row["symbol"] for row in extremes["basket"]] == [
        "S0USDT",
        "S1USDT",
        "S2USDT",
        "S6USDT",
        "S7USDT",
        "S3USDT",
        "S4USDT",
        "S5USDT",
    ]
    assert extremes["basket"][0]["current_price"] == 101.0
    assert extremes["basket"][0]["change_percent"] == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_window_extremes_lists_full_basket_before_window_is_ready():
    state = PriceState()
    event_ms = int(time.time() * 1000)
    await state.update_binance("AAAUSDT", 100.0, event_ms, "ws")

    extremes = await state.window_extremes(
        ["AAAUSDT", "BBBUSDT"],
        window_seconds=999,
        limit=5,
    )

    assert extremes["sample_count"] == 1
    assert extremes["observation_universe_size"] == 2
    assert extremes["gainer_count"] == 0
    assert extremes["loser_count"] == 0
    assert extremes["basket"] == [
        {
            "symbol": "AAAUSDT",
            "change_percent": 0.0,
            "start_price": 100.0,
            "end_price": 100.0,
            "current_price": 100.0,
            "start_ms": event_ms,
            "end_ms": event_ms,
            "start_source": "ws",
            "price_source": "ws",
            "window_ready": False,
        },
        {
            "symbol": "BBBUSDT",
            "change_percent": None,
            "start_price": None,
            "end_price": None,
            "current_price": None,
            "start_ms": None,
            "end_ms": None,
            "start_source": None,
            "price_source": "waiting",
            "window_ready": False,
        },
    ]


@pytest.mark.asyncio
async def test_binance_state_ignores_non_positive_prices():
    state = PriceState()
    event_ms = int(time.time() * 1000)

    await state.update_binance("DAIUSDT", 0.0, event_ms, "rest")

    snapshot = await state.snapshot()
    assert "DAIUSDT" not in snapshot["binance"]


def test_market_divergence_gate_requires_index_above_one():
    assert should_compute_conversion_profits({"market_divergence_index": 1.125})
    assert not should_compute_conversion_profits({"market_divergence_index": 1.0})
    assert not should_compute_conversion_profits({"market_divergence_index": 0.875})


@pytest.mark.asyncio
async def test_binance_rest_poller_fails_over_to_next_base(monkeypatch):
    stop = observer_runtime.asyncio.Event()
    state = PriceState()
    calls = []

    def fake_fetch(base, symbol):
        calls.append((base, symbol))
        if base == "https://bad.example":
            raise TimeoutError("rest timeout")
        return 123.45

    async def fake_sleep(stop_event, seconds):
        stop_event.set()

    monkeypatch.setattr(observer_runtime, "fetch_binance_rest_price", fake_fetch)
    monkeypatch.setattr(observer_runtime, "sleep_until_next", fake_sleep)
    config = SimpleNamespace(
        binance_rest_bases=["https://bad.example", "https://good.example"],
        symbols=["AVAXUSDT", "ETHUSDT"],
        binance_rest_poll_seconds=1.0,
    )

    await observer_runtime.binance_rest_poller(config, state, stop)

    snapshot = await state.snapshot()
    assert snapshot["binance"]["AVAXUSDT"]["price"] == 123.45
    assert snapshot["binance"]["ETHUSDT"]["price"] == 123.45
    assert ("https://bad.example", "AVAXUSDT") in calls
    assert ("https://good.example", "AVAXUSDT") in calls
