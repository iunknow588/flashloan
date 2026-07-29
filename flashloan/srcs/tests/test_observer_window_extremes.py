import time

import pytest

from market.observer import PriceState, should_compute_conversion_profits


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


def test_market_divergence_gate_requires_index_above_one():
    assert should_compute_conversion_profits({"market_divergence_index": 1.125})
    assert not should_compute_conversion_profits({"market_divergence_index": 1.0})
    assert not should_compute_conversion_profits({"market_divergence_index": 0.875})
