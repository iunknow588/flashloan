from datetime import datetime, timedelta, timezone

from market_events import (
    build_market_volatility_event,
    consume_market_volatility_event,
    latest_market_volatility_event,
    latest_pending_market_volatility_event,
    record_market_volatility_event,
)


def _event():
    observed_at = datetime.now(timezone.utc)
    return build_market_volatility_event(
        {
            "observed_at": observed_at.isoformat(timespec="seconds"),
            "window_seconds": 1.0,
            "sample_count": 12,
            "active_sample_count": 6,
            "gainer_count": 3,
            "loser_count": 3,
            "market_divergence_index": 2.0,
            "min_change_percent": 0.3,
            "top": [{"symbol": "AVAXUSDT", "change_percent": 3.2}],
            "bottom": [{"symbol": "BTCUSDT", "change_percent": -2.4}],
        },
        max_age_seconds=300,
    )


def test_market_volatility_event_store_dedupes_and_tracks_consumption(tmp_path):
    path = tmp_path / "market_events.jsonl"
    event = _event()

    first = record_market_volatility_event(event, path=path)
    duplicate = record_market_volatility_event(event, path=path)

    assert first["event_id"] == event["event_id"]
    assert duplicate["event_id"] == event["event_id"]
    assert len(path.read_text(encoding="utf-8").splitlines()) == 1
    assert latest_pending_market_volatility_event(path=path)["event_id"] == event["event_id"]

    consumed = consume_market_volatility_event(event, "debt_pool", path=path)

    assert consumed["status"] == "consumed"
    assert consumed["consumer_page"] == "debt_pool"
    assert consumed["consumed_at"]
    assert latest_pending_market_volatility_event(path=path) is None
    assert latest_market_volatility_event(path=path)["status"] == "consumed"
    assert len(path.read_text(encoding="utf-8").splitlines()) == 2


def test_market_volatility_event_store_ignores_expired_pending_events(tmp_path):
    path = tmp_path / "market_events.jsonl"
    event = _event()
    event["expires_at"] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(timespec="seconds")

    record_market_volatility_event(event, path=path)

    assert latest_pending_market_volatility_event(path=path) is None
