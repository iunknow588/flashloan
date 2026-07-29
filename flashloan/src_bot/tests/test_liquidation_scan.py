from pathlib import Path

import pytest

from execution.liquidation_scan import (
    LiquidationScanConfig,
    classify_health_factor,
    estimate_liquidation_profit,
    load_account_addresses,
    scan_account_health,
    split_candidate_accounts,
)


def test_load_account_addresses_deduplicates_and_skips_invalid(tmp_path: Path):
    path = tmp_path / "accounts.txt"
    path.write_text(
        "\n".join(
            [
                "0x0000000000000000000000000000000000000001",
                "bad",
                "0x0000000000000000000000000000000000000001",
                "0x0000000000000000000000000000000000000002",
            ]
        ),
        encoding="utf-8",
    )

    assert load_account_addresses(path) == [
        "0x0000000000000000000000000000000000000001",
        "0x0000000000000000000000000000000000000002",
    ]


def test_classify_health_factor():
    assert classify_health_factor(0.99, 1.05, 1.0) == "liquidatable"
    assert classify_health_factor(1.02, 1.05, 1.0) == "warning"
    assert classify_health_factor(1.20, 1.05, 1.0) == "healthy"


def test_estimate_liquidation_profit_subtracts_flashloan_slippage_and_gas():
    result = estimate_liquidation_profit(
        total_debt_base=1000,
        liquidation_bonus_percent=5,
        flashloan_fee_percent=0.05,
        dex_slippage_percent=0.10,
        gas_cost_usd=1,
        repay_fraction=0.5,
    )

    assert result["repay_base"] == 500
    assert result["gross_profit_base"] == pytest.approx(25)
    assert result["fee_base"] == pytest.approx(0.75)
    assert result["net_profit_base"] == pytest.approx(23.25)
    assert result["profitable"]


def test_split_candidate_accounts():
    groups = split_candidate_accounts(
        [
            {"account": "a", "health_factor": 0.99},
            {"account": "b", "health_factor": 1.02},
            {"account": "c", "health_factor": 1.20},
        ],
        warning_threshold=1.05,
        liquidation_threshold=1.0,
    )

    assert [item["account"] for item in groups["liquidation_accounts"]] == ["a"]
    assert [item["account"] for item in groups["warning_accounts"]] == ["b"]
    assert [item["account"] for item in groups["healthy_accounts"]] == ["c"]


def test_scan_account_health_uses_fetcher(monkeypatch):
    from execution import liquidation_scan

    def fake_fetch(pool_address, account, rpc_url):
        return {
            "account": account,
            "total_collateral_base": 1200,
            "total_debt_base": 1000,
            "available_borrows_base": 0,
            "current_liquidation_threshold": 8000,
            "ltv": 7500,
            "health_factor": 0.98,
        }

    monkeypatch.setattr(liquidation_scan, "fetch_user_account_data", fake_fetch)

    rows = scan_account_health(
        ["0x0000000000000000000000000000000000000001"],
        "0x0000000000000000000000000000000000000002",
        "https://rpc.example",
        LiquidationScanConfig(max_candidates=10),
    )

    assert rows[0]["status"] == "liquidatable"
    assert rows[0]["liquidation_profit"]["profitable"]
