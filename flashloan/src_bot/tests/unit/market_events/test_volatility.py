from datetime import datetime, timedelta, timezone

from market_events import (
    build_market_volatility_event,
    market_volatility_event_is_fresh,
    market_volatility_route_intent,
)


def test_market_volatility_event_is_built_from_extremes():
    event = build_market_volatility_event(
        {
            "observed_at": "2026-08-02T01:02:03+00:00",
            "window_seconds": 1.0,
            "sample_count": 12,
            "active_sample_count": 6,
            "gainer_count": 3,
            "loser_count": 3,
            "market_divergence_index": 2.0,
            "min_change_percent": 0.3,
            "top": [{"symbol": "AVAXUSDT", "change_percent": 3.2}],
            "bottom": [{"symbol": "BTCUSDT", "change_percent": -2.4}],
        }
    )

    assert event["event_type"] == "MARKET_VOLATILITY_ALERT"
    assert event["severity"] == "medium"
    assert event["affected_assets"] == ["AVAXUSDT", "BTCUSDT"]
    assert event["event_id"]
    assert market_volatility_event_is_fresh(event, now=datetime.fromisoformat(event["observed_at"]) + timedelta(seconds=1))
    assert market_volatility_route_intent(event)["target_page"] == "debt_pool"


def test_market_volatility_event_is_none_without_extremes():
    assert build_market_volatility_event({}) is None
