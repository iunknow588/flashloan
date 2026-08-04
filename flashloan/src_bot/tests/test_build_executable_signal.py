from strategy.build_executable_signal import build_candidate, build_verified_signal


def sample_candidate():
    return {
        "observed_at": "2026-07-28T00:00:00+00:00",
        "window_seconds": 0.2,
        "sample_count": 200,
        "x_symbol": "AVAXUSDT",
        "x_change_percent": 1.2,
        "x_start_price": 20.0,
        "x_end_price": 20.24,
        "y_symbol": "AAVEUSDT",
        "y_change_percent": -1.1,
        "y_start_price": 100.0,
        "y_end_price": 98.9,
    }


def test_candidate_is_not_executable_without_quote_verification():
    candidate = build_candidate(sample_candidate())

    assert candidate["stage"] == "aave_candidate"
    assert candidate["signal"] is False
    assert candidate["profitable"] is False
    assert candidate["executable_signal"] is False
    assert candidate["dex_quote_verified"] is False
    assert candidate["net_profit_verified"] is False
    assert "dex_quote_not_verified" in candidate["blocked_reasons"]


def test_verified_signal_is_not_built_from_unquoted_candidate():
    assert build_verified_signal(sample_candidate()) is None
