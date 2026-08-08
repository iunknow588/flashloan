from decimal import Decimal

from config import intent_costs


def test_intent_costs_prefers_link_specific_configuration(monkeypatch):
    monkeypatch.setenv("COW_INTENT_USDC_BBB_AAA_USDC_TRADE_FEE_PERCENT", "0.20")
    monkeypatch.setenv("COW_INTENT_USDC_BBB_AAA_USDC_FLASHLOAN_FEE_PERCENT", "0.08")
    monkeypatch.setenv("COW_INTENT_USDC_BBB_AAA_USDC_FEE_RESERVE_PERCENT", "0.12")
    monkeypatch.setenv("COW_INTENT_USDC_BBB_AAA_USDC_GAS_RESERVE_USDC", "0.30")
    monkeypatch.setenv("COW_INTENT_USDC_BBB_AAA_USDC_OTHER_KNOWN_COSTS_USDC", "0.15")

    costs = intent_costs("USDC->BBB->AAA->USDC", Decimal("1000"))

    assert costs["route_trade_fee_percent"] == Decimal("0.40")
    assert costs["route_trade_fee_amount"] == Decimal("4.00")
    assert costs["flashloan_fee_amount"] == Decimal("0.80")
    assert costs["fee_reserve_amount"] == Decimal("1.20")
    assert costs["gas_reserve_amount"] == Decimal("0.30")
    assert costs["other_known_costs_amount"] == Decimal("0.15")
