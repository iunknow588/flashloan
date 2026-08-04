from web.control_panel_config import sanitize_strategy_config, unified_sampling_profile


def test_sampling_profile_allows_configured_200ms_window():
    config = sanitize_strategy_config({"BINANCE_CHANGE_WINDOW_SECONDS": 0.2})
    profile = unified_sampling_profile(config)

    assert config["BINANCE_CHANGE_WINDOW_SECONDS"] == 0.2
    assert profile["seconds"] == 0.2
