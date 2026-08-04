from strategy.trigger_signal import TriggerConfig, build_trigger_signal


def extremes(up_change=1.2, down_change=-1.3):
    return {
        "observed_at": "2026-07-27T00:00:00.000+00:00",
        "window_seconds": 0.2,
        "sample_count": 2,
        "price_source": "ws",
        "top": [
            {
                "symbol": "AVAXUSDT",
                "change_percent": up_change,
                "start_price": 10,
                "end_price": 10 * (1 + up_change / 100),
            }
        ],
        "bottom": [
            {
                "symbol": "ETHUSDT",
                "change_percent": down_change,
                "start_price": 100,
                "end_price": 100 * (1 + down_change / 100),
            }
        ],
    }


def test_builds_onchain_dynamic_trigger_signal():
    signal = build_trigger_signal(extremes(), TriggerConfig())

    assert signal["trigger_signal"] is True
    assert signal["onchain_decision_required"] is True
    assert signal["x_symbol"] == "AVAXUSDT"
    assert signal["y_symbol"] == "ETHUSDT"
    assert signal["x_change_percent"] == 1.2
    assert signal["y_change_percent"] == -1.3
    assert signal["execution_plan"] is None
    assert signal["evaluated_strategy_count"] == 4


def test_blocks_when_thresholds_are_not_met():
    signal = build_trigger_signal(extremes(up_change=0.5, down_change=-0.4), TriggerConfig())

    assert signal["trigger_signal"] is False
    assert signal["blocked_reasons"] == [
        "top_gainer_below_threshold",
        "top_loser_below_threshold",
    ]


def test_filters_to_executable_symbols():
    signal = build_trigger_signal(
        extremes(),
        TriggerConfig(executable_symbols=("BTCUSDT", "AAVEUSDT")),
    )

    assert signal is None
