from web.control_panel import (
    opportunity_health_rows,
    opportunity_health_summary,
    restrict_extremes_to_symbols,
)


def test_restrict_extremes_to_current_observation_basket():
    extremes = {
        "sample_count": 3,
        "observation_universe_size": 470,
        "gainer_count": 2,
        "loser_count": 1,
        "market_divergence_index": 2 / 470,
        "basket": [
            {"symbol": "BTCUSDT", "current_price": 100.0, "change_percent": 1.0, "window_ready": True},
            {"symbol": "ETHUSDT", "current_price": 50.0, "change_percent": -1.0, "window_ready": True},
            {"symbol": "DOGEUSDT", "current_price": 0.1, "change_percent": 2.0, "window_ready": True},
        ],
    }

    filtered = restrict_extremes_to_symbols(extremes, ["BTCUSDT", "ETHUSDT"])

    assert [row["symbol"] for row in filtered["basket"]] == ["BTCUSDT", "ETHUSDT"]
    assert filtered["sample_count"] == 2
    assert filtered["observation_universe_size"] == 2
    assert filtered["gainer_count"] == 1
    assert filtered["loser_count"] == 1
    assert filtered["market_divergence_index"] == 0.5


def test_opportunity_health_rows_and_summary_rank_by_threshold():
    extremes = {
        "observed_at": "2026-07-29T10:00:00+00:00",
        "window_seconds": 0.2,
        "basket": [
            {
                "symbol": "BTCUSDT",
                "current_price": 100.0,
                "start_price": 98.0,
                "change_percent": 2.0,
                "window_ready": True,
                "price_source": "aave",
            },
            {
                "symbol": "ETHUSDT",
                "current_price": 50.0,
                "start_price": 49.8,
                "change_percent": 0.2,
                "window_ready": True,
                "price_source": "aave",
            },
            {
                "symbol": "SOLUSDT",
                "current_price": 20.0,
                "start_price": 20.0,
                "change_percent": -1.5,
                "window_ready": False,
                "price_source": "binance",
            },
        ],
    }
    config = {
        "TRIGGER_MIN_UP_CHANGE_PERCENT": 1.0,
        "TRIGGER_MIN_DOWN_CHANGE_PERCENT": 1.0,
        "BINANCE_CHANGE_WINDOW_SECONDS": 0.2,
    }

    rows = opportunity_health_rows(extremes, config)
    summary = opportunity_health_summary(rows, config)

    assert [row["symbol"] for row in rows] == ["BTCUSDT", "SOLUSDT", "ETHUSDT"]
    assert rows[0]["health_score"] == 200.0
    assert rows[0]["status"] == "selected"
    assert rows[1]["status"] == "watching"
    assert summary["total"] == 3
    assert summary["candidate_count"] == 1
    assert summary["selected_count"] == 1
    assert summary["best_symbol"] == "BTCUSDT"
    assert summary["monitor_window_seconds"] == 0.2
