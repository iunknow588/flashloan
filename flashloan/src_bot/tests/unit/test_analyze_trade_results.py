import pytest

from tools.analyze_trade_results import summarize_trades


def test_summarize_trades_reports_success_rate_and_external_net_after_gas():
    rows = [
        {"success": True, "gasUsed": "100000", "profitUsdc": 2.0},
        {"success": False, "gasUsed": "50000", "error": "NoViableRoute"},
    ]

    summary = summarize_trades(rows, gas_price_gwei=25, native_price_usdc=20)

    assert summary["trade_count"] == 2
    assert summary["success_count"] == 1
    assert summary["failure_count"] == 1
    assert summary["success_rate"] == 0.5
    assert summary["gross_profit_usdc"] == 2.0
    assert summary["gas_cost_usdc"] == pytest.approx(0.075)
    assert summary["external_net_profit_usdc"] == pytest.approx(1.925)
    assert summary["failure_reasons"] == {"NoViableRoute": 1}
