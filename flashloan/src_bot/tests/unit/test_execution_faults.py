from core.execution_faults import evaluate_execution_risks


def test_execution_risk_faults_fail_closed_for_each_cost_dimension():
    base = {"gas_cost_usd": 1, "slippage_usd": 1, "flash_fee_usd": 1, "mev_loss_usd": 1, "quote_age_seconds": 1}
    for field, value, reason in (("gas_cost_usd", 20, "execution_cost_too_high"), ("slippage_usd", 20, "execution_cost_too_high"), ("flash_fee_usd", 20, "execution_cost_too_high"), ("mev_loss_usd", 20, "execution_cost_too_high"), ("balance_shortfall_usd", 1, "balance_insufficient"), ("quote_age_seconds", 99, "quote_stale")):
        facts = {**base, field: value}
        allowed, reasons, _ = evaluate_execution_risks(facts, max_cost_usd=10)
        assert not allowed
        assert reason in reasons


def test_execution_risk_faults_allow_clean_facts():
    allowed, reasons, costs = evaluate_execution_risks({"gas_cost_usd": 1, "slippage_usd": 2, "flash_fee_usd": 1, "mev_loss_usd": 0, "quote_age_seconds": 2}, max_cost_usd=10)
    assert allowed and reasons == () and costs["total_cost_usd"] == 4
