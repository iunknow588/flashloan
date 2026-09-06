from datetime import datetime, timezone

import pytest

from bot.liquidation.execution_target import normalize_execution_target, select_execution_candidate
from bot.liquidation.target_reassessment import reassess_execution_target
from core.validation.cow_settlement import summarize_cow_attempts
from core.validation.liquidation_profit import LiquidationProfitInput, calculate_liquidation_profit, gas_stress_table
from core.validation.scan_windows import summarize_opportunity_windows


def test_liquidation_profit_includes_gas_slippage_premium_and_mev():
    result = calculate_liquidation_profit(
        LiquidationProfitInput(
            debt_to_cover_usd=1000,
            liquidation_bonus_bps=500,
            protocol_fee_bps=50,
            slippage_bps=100,
            gas_used=500000,
            gas_price_wei=30_000_000_000,
            native_usd=35,
            flashloan_premium_bps=5,
            mev_loss_usd=2,
        )
    )
    assert result["status"] == "calculated"
    assert result["gross_bonus_usd"] == 50
    assert result["gas_cost_usd"] == pytest.approx(0.525)
    assert result["net_profit_usd"] == pytest.approx(31.975)


def test_profit_model_reports_missing_price_inputs_instead_of_zero_profit():
    result = calculate_liquidation_profit(LiquidationProfitInput(debt_to_cover_usd=1000))
    assert result["status"] == "missing_price_inputs"
    assert "gas_used" in result["missing_inputs"]
    assert result["net_profit_usd"] is None


def test_gas_stress_table_changes_net_profit():
    item = LiquidationProfitInput(
        debt_to_cover_usd=1000,
        liquidation_bonus_bps=500,
        gas_used=500000,
        gas_price_wei=25_000_000_000,
        native_usd=30,
    )
    rows = gas_stress_table(item, [25, 100])
    assert rows[0]["net_profit_usd"] > rows[1]["net_profit_usd"]


def test_cow_summary_requires_explicit_settlement_evidence():
    rows = [
        {"state": "quoted", "quote": {"quote_call": {"finishedAt": "2026-08-18T00:00:00Z"}}},
        {
            "state": "settled",
            "created_at": "2026-08-18T00:00:00Z",
            "quote": {"quote_call": {"finishedAt": "2026-08-18T00:00:00Z"}},
            "settled_at": "2026-08-18T00:00:04Z",
        },
    ]
    result = summarize_cow_attempts(rows)
    assert result["quote_success_count"] == 2
    assert result["settled_count"] == 1
    assert result["quote_to_settlement_rate"] == 0.5
    assert result["quote_to_settlement_delay_seconds"]["p50"] == 4


def test_scan_window_summary_marks_short_lived_opportunities():
    rows = [
        {"account": "0x1", "health_factor": 0.99, "observed_at": "2026-08-18T00:00:00Z"},
        {"account": "0x1", "health_factor": 0.98, "observed_at": "2026-08-18T00:00:20Z"},
        {"account": "0x2", "status": "liquidatable", "observed_at": "2026-08-18T00:00:00Z"},
        {"account": "0x2", "status": "liquidatable", "observed_at": "2026-08-18T00:06:00Z"},
    ]
    result = summarize_opportunity_windows(rows, intervals={"core": 1, "high_frequency": 300})
    assert result["status"] == "calculated"
    assert result["episode_count"] == 2
    assert result["coverage"]["high_frequency"]["episodes_shorter_than_interval"] == 1


def test_target_reassessment_rejects_changed_candidate_and_old_report():
    report = {
        "account": "0x0000000000000000000000000000000000000001",
        "summary": {"status": "liquidatable", "health_factor": 0.98},
        "observed_at": "2026-08-18T00:00:00Z",
        "liquidation_candidates": [
            {"collateral_asset": "0x2", "debt_asset": "0x3", "amount_to_pass_to_liquidation_call": 1},
        ],
    }
    target = normalize_execution_target(report["account"], borrow_index=1, market_id="avalanche-aave-v3")
    _, target_payload = select_execution_candidate(report, target=target)
    result = reassess_execution_target(
        {**report, "liquidation_candidates": [{**report["liquidation_candidates"][0], "amount_to_pass_to_liquidation_call": 2}]},
        target_payload,
        max_age_seconds=30,
        now=datetime(2026, 8, 18, 0, 1, tzinfo=timezone.utc),
    )
    assert result["fresh"] is False
    assert "target_report_expired" in result["blocked_reasons"]
    assert "target_candidate_changed" in result["blocked_reasons"]
