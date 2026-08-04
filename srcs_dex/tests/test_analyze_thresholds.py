from datetime import datetime, timezone

from tools.analyze_thresholds import replay_windows, summarize


def ts(value: str):
    return datetime.fromisoformat(value).replace(tzinfo=timezone.utc)


def test_replay_windows_uses_binance_event_time_not_observed_at():
    observed = ts("2026-07-28T00:00:10")
    rows = [
        (observed, "AAAUSDT", 100.0, ts("2026-07-28T00:00:00.000"), "ws"),
        (observed, "BBBUSDT", 100.0, ts("2026-07-28T00:00:00.000"), "ws"),
        (observed, "AAAUSDT", 101.0, ts("2026-07-28T00:00:00.200"), "ws"),
        (observed, "BBBUSDT", 99.0, ts("2026-07-28T00:00:00.200"), "ws"),
    ]

    replayed = replay_windows(rows, 0.2)

    assert replayed[-1]["top_symbol"] == "AAAUSDT"
    assert replayed[-1]["bottom_symbol"] == "BBBUSDT"
    assert replayed[-1]["sample_count"] == 2


def test_replay_windows_skips_windows_without_both_gainers_and_losers():
    observed = ts("2026-07-28T00:00:10")
    rows = [
        (observed, "AAAUSDT", 100.0, ts("2026-07-28T00:00:00.000"), "ws"),
        (observed, "BBBUSDT", 100.0, ts("2026-07-28T00:00:00.000"), "ws"),
        (observed, "AAAUSDT", 101.0, ts("2026-07-28T00:00:00.200"), "ws"),
        (observed, "BBBUSDT", 100.5, ts("2026-07-28T00:00:00.200"), "ws"),
    ]

    replayed = replay_windows(rows, 0.2)

    assert replayed == []


def test_summarize_reports_threshold_counts():
    rows = [
        {
            "sample_count": 2,
            "top_change_percent": 1.2,
            "bottom_change_percent": -1.1,
        }
    ]

    summary = summarize(rows, [0.5, 1.0])

    assert summary["dual_0_5pct_trigger_count"] == 1
    assert summary["dual_1pct_trigger_count"] == 1
