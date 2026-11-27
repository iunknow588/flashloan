"""The review clock of a closed loop and the parameter that controls it.

The closed loop owns a **review clock**: the loop starts the clock when it
has been dealt with -- a broadcast that confirmed a profit, or a review
that found no profit -- and the clock runs until the loop is due again.
The length of that clock is **one** parameter, it lives in the
configuration parameters, and the control panel edits it.
"""

from pathlib import Path

from market.binance_market.control_panel_config import sanitize_strategy_config
from market.strategy.limits import strategy_defaults


APP_ROOT = Path(__file__).resolve().parents[2]


def test_the_review_interval_defaults_to_sixty_seconds():
    assert strategy_defaults()["CLOSED_LOOP_REVIEW_INTERVAL_SECONDS"] == 60


def test_the_review_interval_is_clamped_to_a_window_that_can_be_traded():
    assert sanitize_strategy_config(
        {"CLOSED_LOOP_REVIEW_INTERVAL_SECONDS": 1}
    )["CLOSED_LOOP_REVIEW_INTERVAL_SECONDS"] == 5
    assert sanitize_strategy_config(
        {"CLOSED_LOOP_REVIEW_INTERVAL_SECONDS": 99999}
    )["CLOSED_LOOP_REVIEW_INTERVAL_SECONDS"] == 3600
    assert sanitize_strategy_config(
        {"CLOSED_LOOP_REVIEW_INTERVAL_SECONDS": 30}
    )["CLOSED_LOOP_REVIEW_INTERVAL_SECONDS"] == 30


def test_the_control_panel_edits_the_review_interval():
    template = (APP_ROOT / "web" / "templates" / "control_panel.html").read_text(
        encoding="utf-8"
    )
    assert 'id="cfgClosedLoopReviewInterval"' in template
    assert "CLOSED_LOOP_REVIEW_INTERVAL_SECONDS" in template


def test_the_strategy_config_round_trips_the_review_interval():
    from market.binance_market import control_panel_config

    saved: dict[str, object] = {}

    def fake_save(values: dict) -> bool:
        saved.update(values)
        return True

    original_save = control_panel_config.save_strategy_config_parameters
    original_read = control_panel_config.read_strategy_config_parameters
    try:
        control_panel_config.save_strategy_config_parameters = fake_save
        control_panel_config.read_strategy_config_parameters = lambda path: dict(saved)
        written = control_panel_config.write_strategy_config(
            Path("unused.json"),
            {"CLOSED_LOOP_REVIEW_INTERVAL_SECONDS": 45},
        )
    finally:
        control_panel_config.save_strategy_config_parameters = original_save
        control_panel_config.read_strategy_config_parameters = original_read

    assert written["CLOSED_LOOP_REVIEW_INTERVAL_SECONDS"] == 45
    assert saved["CLOSED_LOOP_REVIEW_INTERVAL_SECONDS"] == 45


def test_the_review_start_spacing_defaults_to_two_seconds():
    # The loops of a fresh catalog do not all become due in the same
    # second: their review time points are spread by this spacing, so the
    # first round of the reviews does not overrun the RPC rate limit.
    assert strategy_defaults()["CLOSED_LOOP_REVIEW_START_SPACING_SECONDS"] == 2


def test_the_review_start_spacing_is_clamped_to_a_window_that_can_be_served():
    assert sanitize_strategy_config(
        {"CLOSED_LOOP_REVIEW_START_SPACING_SECONDS": -5}
    )["CLOSED_LOOP_REVIEW_START_SPACING_SECONDS"] == 0
    assert sanitize_strategy_config(
        {"CLOSED_LOOP_REVIEW_START_SPACING_SECONDS": 99999}
    )["CLOSED_LOOP_REVIEW_START_SPACING_SECONDS"] == 60
    assert sanitize_strategy_config(
        {"CLOSED_LOOP_REVIEW_START_SPACING_SECONDS": 3}
    )["CLOSED_LOOP_REVIEW_START_SPACING_SECONDS"] == 3


def test_the_control_panel_edits_the_review_start_spacing():
    template = (APP_ROOT / "web" / "templates" / "control_panel.html").read_text(
        encoding="utf-8"
    )
    assert 'id="cfgClosedLoopReviewStartSpacing"' in template
    assert "CLOSED_LOOP_REVIEW_START_SPACING_SECONDS" in template
