from execution.venus_liquidation_scan import (
    build_venus_liquidation_candidates,
    classify_venus_risk,
    close_factor_repay_amount,
    summarize_venus_reports,
)


def test_classify_venus_risk_uses_shortfall_first():
    assert classify_venus_risk(10**30, 1) == "liquidatable"
    assert classify_venus_risk(1, 0, warning_liquidity_raw=10) == "warning"
    assert classify_venus_risk(100, 0, warning_liquidity_raw=10) == "healthy"


def test_close_factor_and_candidate_generation_with_fallback_seize():
    assert close_factor_repay_amount(1000, 500_000_000_000_000_000) == 500
    risk = {
        "account": "0x00000000000000000000000000000000000000AA",
        "status": "liquidatable",
        "positions": [
            {
                "vtoken_address": "0x0000000000000000000000000000000000000011",
                "underlying_symbol": "USDT",
                "wrapped_underlying_address": "0x0000000000000000000000000000000000000022",
                "borrow_balance_raw": 1000,
                "supply_balance_raw": 0,
                "close_factor_mantissa": 500_000_000_000_000_000,
            },
            {
                "vtoken_address": "0x0000000000000000000000000000000000000033",
                "underlying_symbol": "BNB",
                "wrapped_underlying_address": "0x0000000000000000000000000000000000000044",
                "underlying_decimals": 18,
                "exchange_rate_stored": 10**18,
                "oracle_price_usd": 600,
                "borrow_balance_raw": 0,
                "supply_balance_raw": 2000,
                "entered_market": True,
                "expected_seize_tokens_raw": 650,
            },
        ],
    }

    candidates = build_venus_liquidation_candidates(risk, min_repay_raw=1, min_seize_raw=1)

    assert candidates[0]["status"] == "candidate"
    assert candidates[0]["repay_amount_raw"] == 500
    assert candidates[0]["seize_tokens_raw"] == 650
    assert candidates[0]["collateral_underlying_amount"] == 2e-15
    assert candidates[0]["estimated_profit"]["net_profit_base"] >= 0
    assert candidates[0]["liquidation_profit"]["net_profit_base"] >= 0
    assert candidates[0]["estimated_profit"]["profit_bps"] >= 0


def test_summarize_venus_reports_counts_profit_signs():
    reports = [
        {
            "summary": {"status": "liquidatable"},
            "liquidation_candidates": [
                {"status": "candidate", "estimated_profit": {"net_profit_base": 2}},
                {"status": "candidate", "estimated_profit": {"net_profit_base": -1}},
            ],
        }
    ]

    summary = summarize_venus_reports(reports)

    assert summary["positive_net_profit_count"] == 1
    assert summary["negative_net_profit_count"] == 1


def test_venus_candidate_profit_applies_costs_and_minimum():
    risk = {
        "account": "0x00000000000000000000000000000000000000AA",
        "status": "liquidatable",
        "positions": [
            {
                "vtoken_address": "0x0000000000000000000000000000000000000011",
                "underlying_symbol": "USDT",
                "wrapped_underlying_address": "0x0000000000000000000000000000000000000022",
                "underlying_decimals": 18,
                "oracle_price_usd": 1,
                "borrow_balance_raw": 100 * 10**18,
                "supply_balance_raw": 0,
            },
            {
                "vtoken_address": "0x0000000000000000000000000000000000000033",
                "underlying_symbol": "BNB",
                "wrapped_underlying_address": "0x0000000000000000000000000000000000000044",
                "underlying_decimals": 18,
                "liquidation_incentive": 1.10,
                "protocol_share_mantissa": 50_000_000_000_000_000,
                "borrow_balance_raw": 0,
                "supply_balance_raw": 200 * 10**18,
                "entered_market": True,
                "expected_seize_tokens_raw": 60 * 10**18,
            },
        ],
    }

    candidates = build_venus_liquidation_candidates(
        risk,
        dex_swap_fee_percent=0.25,
        gas_cost_usd=1.0,
        mev_buffer_usd=0.5,
        retry_buffer_usd=0.5,
        min_operator_net_profit_usd=10.0,
    )

    assert candidates[0]["status"] == "rejected"
    assert candidates[0]["rejected_reason"] == "net_profit_below_minimum"
    assert candidates[0]["estimated_profit"]["gas_cost_usd"] == 1.0
    assert candidates[0]["estimated_profit"]["mev_buffer_usd"] == 0.5
    assert candidates[0]["estimated_profit"]["dex_swap_fee_percent"] == 0.25


def test_venus_candidate_filter_only_applies_to_flashloan_supported_debt_assets():
    risk = {
        "account": "0x00000000000000000000000000000000000000AA",
        "status": "liquidatable",
        "positions": [
            {
                "vtoken_address": "0x0000000000000000000000000000000000000011",
                "underlying_symbol": "XVS",
                "wrapped_underlying_address": "0x0000000000000000000000000000000000000022",
                "underlying_decimals": 18,
                "oracle_price_usd": 8,
                "borrow_balance_raw": 100 * 10**18,
                "supply_balance_raw": 0,
            },
            {
                "vtoken_address": "0x0000000000000000000000000000000000000033",
                "underlying_symbol": "USDT",
                "wrapped_underlying_address": "0x0000000000000000000000000000000000000044",
                "underlying_decimals": 18,
                "oracle_price_usd": 1,
                "borrow_balance_raw": 0,
                "supply_balance_raw": 200 * 10**18,
                "entered_market": True,
                "expected_seize_tokens_raw": 60 * 10**18,
            },
        ],
    }

    candidates = build_venus_liquidation_candidates(
        risk,
        flashloan_supported_debt_assets=("USDT", "USDC", "WBNB"),
    )

    assert candidates[0]["status"] == "rejected"
    assert candidates[0]["rejected_reason"] == "debt_asset_not_flashloan_supported"
    assert candidates[0]["debt_flashloan_supported"] is False
