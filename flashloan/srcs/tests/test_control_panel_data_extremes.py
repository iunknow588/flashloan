from web.control_panel_data import compact_extremes_payload


def test_compact_extremes_keeps_one_sided_window():
    payload = compact_extremes_payload(
        {
            "observed_at": "2026-07-29T00:00:00+00:00",
            "window_seconds": 0.2,
            "sample_count": 2,
            "price_source": "ws",
            "top": [{"symbol": "AAAUSDT", "change_percent": 1.0}],
            "bottom": [],
        }
    )

    assert payload is not None
    assert payload["a"]["symbol"] == "AAAUSDT"
    assert payload["b"]["symbol"] is None


def test_compact_extremes_keeps_basket_rows():
    payload = compact_extremes_payload(
        {
            "observed_at": "2026-07-29T00:00:00+00:00",
            "window_seconds": 0.2,
            "sample_count": 2,
            "observation_universe_size": 2,
            "basket": [
                {
                    "symbol": "AAAUSDT",
                    "change_percent": 1.25,
                    "start_price": 100.0,
                    "current_price": 101.25,
                    "price_source": "ws",
                },
                {
                    "symbol": "BBBUSDT",
                    "change_percent": -0.5,
                    "start_price": 200.0,
                    "end_price": 199.0,
                    "price_source": "rest",
                },
            ],
        }
    )

    assert payload is not None
    assert payload["basket"] == [
        {
            "symbol": "AAAUSDT",
            "change_percent": 1.25,
            "start_price": 100.0,
            "current_price": 101.25,
            "price_source": "ws",
            "end_ms": None,
            "window_ready": False,
        },
        {
            "symbol": "BBBUSDT",
            "change_percent": -0.5,
            "start_price": 200.0,
            "current_price": 199.0,
            "price_source": "rest",
            "end_ms": None,
            "window_ready": False,
        },
    ]


def test_compact_extremes_keeps_unpriced_basket_rows():
    payload = compact_extremes_payload(
        {
            "observed_at": "2026-07-29T00:00:00+00:00",
            "window_seconds": 0.2,
            "sample_count": 0,
            "observation_universe_size": 1,
            "basket": [
                {
                    "symbol": "AAAUSDT",
                    "change_percent": None,
                    "start_price": None,
                    "current_price": None,
                    "price_source": "waiting",
                    "window_ready": False,
                }
            ],
        }
    )

    assert payload is not None
    assert payload["basket"][0]["symbol"] == "AAAUSDT"
    assert payload["basket"][0]["current_price"] is None
