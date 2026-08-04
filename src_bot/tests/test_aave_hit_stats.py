from tools.aave_hit_stats import summarize_aave_hits


def test_summarize_aave_hits_counts_unique_candidate_symbols():
    extremes = {
        "observed_at": "2026-07-28T00:00:00+00:00",
        "window_seconds": 0.2,
        "sample_count": 3,
        "top": [{"symbol": "AVAXUSDT"}, {"symbol": "ETHUSDT"}],
        "bottom": [{"symbol": "ETHUSDT"}, {"symbol": "AAVEUSDT"}],
    }

    summary = summarize_aave_hits(extremes, {"AVAXUSDT", "AAVEUSDT"})

    assert summary["candidate_symbol_count"] == 3
    assert summary["aave_hit_count"] == 2
    assert summary["aave_hit_symbols"] == ["AAVEUSDT", "AVAXUSDT"]
