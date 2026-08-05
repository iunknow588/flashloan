from pathlib import Path

from tools.run_binance_market_snapshot_daemon import write_snapshot


def test_binance_market_snapshot_daemon_writes_snapshot_file(tmp_path, monkeypatch):
    path = tmp_path / "binance_market_snapshot.json"
    monkeypatch.setattr(
        "tools.run_binance_market_snapshot_daemon.build_binance_rest_market_snapshot",
        lambda *args, **kwargs: {
            "observed_at": "2026-08-04T00:00:00+00:00",
            "price_source": "rest_interval",
            "top": [{"symbol": "AAAUSDT"}],
            "bottom": [{"symbol": "BBBUSDT"}],
            "basket": [{"symbol": "AAAUSDT"}, {"symbol": "BBBUSDT"}],
            "observation_universe_size": 2,
            "sample_count": 2,
        },
    )

    payload = write_snapshot(path=path, side_limit=5)

    assert payload["market_state_source"] == "snapshot_daemon"
    assert path.exists()
