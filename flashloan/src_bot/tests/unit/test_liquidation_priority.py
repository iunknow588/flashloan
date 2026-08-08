from execution.liquidation_priority import (
    liquidation_account_activity_tier,
    liquidation_pool_tier,
    liquidation_priority_score,
)


def test_liquidation_pool_tier_splits_watch_high_frequency_and_core():
    assert liquidation_pool_tier(1.2) == "borrow_health"
    assert liquidation_pool_tier(1.05) == "high_frequency"
    assert liquidation_pool_tier(1.01) == "core"
    assert liquidation_pool_tier(1.8) == "healthy"


def test_liquidation_priority_score_prefers_profit_and_debt_over_hf_only():
    small = {"health_factor": 0.99, "total_debt_base": 100, "recommended_candidate": {"estimated_profit": {"net_profit_base": 10}}}
    large = {"health_factor": 1.0, "total_debt_base": 10_000_000, "recommended_candidate": {"estimated_profit": {"net_profit_base": 100_000}}}

    assert liquidation_priority_score(large) > liquidation_priority_score(small)


def test_liquidation_account_activity_tier_marks_recent_risk_hot():
    assert liquidation_account_activity_tier({"last_status": "warning", "last_health_factor": 1.04}) == "hot"
    assert liquidation_account_activity_tier({"last_status": "healthy", "last_total_debt_base": 1}) == "warm"
    assert liquidation_account_activity_tier({}) == "cold"
