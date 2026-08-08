from market.velocity_candidates import (
    base_token_symbol,
    build_velocity_candidate_pairs,
    top_bottom_from_extremes,
)


def test_base_token_symbol_strips_common_binance_quote_suffixes():
    assert base_token_symbol("AAVEUSDT") == "AAVE"
    assert base_token_symbol("BTCUSDC") == "BTC"
    assert base_token_symbol("USDC") == "USDC"


def test_velocity_candidates_build_top_bottom_grid_from_raw_extremes():
    extremes = {
        "observed_at": "2026-08-04T00:00:00+00:00",
        "window_seconds": 1.0,
        "sample_count": 10,
        "observation_universe_size": 10,
        "price_source": "ws",
        "top": [
            {"symbol": f"T{i}USDT", "change_percent": 5 - i, "current_price": 10 + i}
            for i in range(5)
        ],
        "bottom": [
            {"symbol": f"B{i}USDT", "change_percent": -5 + i, "current_price": 20 + i}
            for i in range(5)
        ],
    }

    payload = build_velocity_candidate_pairs(extremes, side_limit=5)

    assert [row["symbol"] for row in payload["top"]] == ["T0USDT", "T1USDT", "T2USDT", "T3USDT", "T4USDT"]
    assert [row["symbol"] for row in payload["bottom"]] == ["B0USDT", "B1USDT", "B2USDT", "B3USDT", "B4USDT"]
    assert payload["candidate_count"] == 25
    assert payload["pairs"][0]["cow_path"] == ["USDC", "T0", "B0", "USDC"]
    assert payload["pairs"][0]["cow_reverse_path"] == ["USDC", "B0", "T0", "USDC"]


def test_velocity_candidates_fall_back_to_compact_basket():
    extremes = {
        "basket": [
            {"symbol": "MIDUSDT", "change_percent": 0.0, "current_price": 1.0},
            {"symbol": "AAAUSDT", "change_percent": 1.5, "current_price": 2.0},
            {"symbol": "BBBUSDT", "change_percent": 2.0, "current_price": 3.0},
            {"symbol": "CCCUSDT", "change_percent": -0.5, "current_price": 4.0},
            {"symbol": "DDDUSDT", "change_percent": -1.5, "current_price": 5.0},
        ],
    }

    top, bottom = top_bottom_from_extremes(extremes, side_limit=2)

    assert [row["symbol"] for row in top] == ["BBBUSDT", "AAAUSDT"]
    assert [row["symbol"] for row in bottom] == ["DDDUSDT", "CCCUSDT"]
