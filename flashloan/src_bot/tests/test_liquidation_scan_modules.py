from execution.health_checker import classify_health_factor, estimate_liquidation_profit, health_factor_band
from execution.prioritizer import incremental_scan_account_groups, split_candidate_accounts, watched_health_rows
from execution.scanner import normalize_accounts


def test_scanner_normalizes_and_deduplicates_accounts():
    accounts = normalize_accounts(
        [
            "0x0000000000000000000000000000000000000001",
            "bad",
            "0x0000000000000000000000000000000000000001",
            "0x0000000000000000000000000000000000000002",
        ],
        max_accounts=10,
    )

    assert accounts == [
        "0x0000000000000000000000000000000000000001",
        "0x0000000000000000000000000000000000000002",
    ]


def test_health_checker_classifies_and_prices_profit():
    assert classify_health_factor(0.99, 1.05, 1.0) == "liquidatable"
    assert health_factor_band(1.2) == "beige"

    profit = estimate_liquidation_profit(
        1000,
        liquidation_bonus_percent=5,
        flashloan_fee_percent=0.05,
        dex_slippage_percent=0.10,
        gas_cost_usd=1,
        repay_fraction=0.5,
    )

    assert profit["repay_base"] == 500
    assert profit["net_profit_base"] == 23.25


def test_prioritizer_groups_watch_and_status_buckets():
    accounts = [
        "0x0000000000000000000000000000000000000001",
        "0x0000000000000000000000000000000000000002",
    ]
    previous = [{"account": accounts[0], "health_factor": 1.2}]

    groups = incremental_scan_account_groups(accounts, previous, full_scan_due=False)
    buckets = split_candidate_accounts(
        [{"health_factor": 0.99}, {"health_factor": 1.02}, {"health_factor": 1.2}],
        warning_threshold=1.05,
        liquidation_threshold=1.0,
    )

    assert watched_health_rows(previous)[0]["health_factor_band"] == "beige"
    assert groups["scan_accounts"] == [accounts[0]]
    assert len(buckets["liquidation_accounts"]) == 1
