from pathlib import Path

import pytest

from execution.liquidation_scan import (
    LiquidationScanConfig,
    build_liquidation_execution_plan,
    health_factor_band,
    classify_health_factor,
    estimate_liquidation_profit,
    load_account_addresses,
    scan_account_health,
    watched_health_rows,
    split_candidate_accounts,
)
from execution.liquidation_payload import build_liquidation_execution_payload


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


def test_health_factor_band_thresholds():
    assert health_factor_band(1.30) == "green"
    assert health_factor_band(1.20) == "beige"
    assert health_factor_band(1.10) == "yellow"
    assert health_factor_band(1.00) == "orange"
    assert health_factor_band(0.99) == "red"


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


def test_watched_health_rows_filters_above_threshold():
    rows = watched_health_rows(
        [
            {"account": "a", "health_factor": 1.49},
            {"account": "b", "health_factor": 1.31},
            {"account": "c", "health_factor": 1.29},
            {"account": "d", "health_factor": 0.99},
            {"account": "e", "health_factor": 1.50},
        ]
    )

    assert [item["account"] for item in rows] == ["d", "c", "b", "a"]
    assert [item["health_factor_band"] for item in rows] == ["red", "beige", "green", "green"]


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


def test_build_liquidation_execution_plan_marks_readiness():
    plan = build_liquidation_execution_plan(
        "0x0000000000000000000000000000000000000001",
        {"health_factor": 0.98},
        {
            "collateral_symbol": "WETH",
            "debt_symbol": "USDC",
            "estimated_profit": {"net_profit_base": 12.5, "gross_profit_base": 15.0},
        },
        LiquidationScanConfig(close_factor=0.5),
    )

    assert plan["execution_ready"]
    assert plan["profitable"]
    assert plan["reason"] == "ready for execution preflight"


def test_near_threshold_healthy_account_is_not_liquidatable(monkeypatch):
    from execution import liquidation_scan

    account = "0xa845Cbe370B99AdDaB67AfE442F2cF5784d4dC29"

    def fake_fetch(pool_address, account, rpc_url):
        return {
            "account": account,
            "total_collateral_base": 347728081162567,
            "total_debt_base": 312865305356406,
            "available_borrows_base": 10521810124781,
            "current_liquidation_threshold": 9500,
            "ltv": 9300,
            "health_factor": 1.0558590915925437,
        }

    monkeypatch.setattr(liquidation_scan, "fetch_user_account_data", fake_fetch)

    rows = scan_account_health(
        [account],
        "0x794a61358D6845594F94dc1db02a252b5b4814aD",
        "https://rpc.example",
        LiquidationScanConfig(warning_health_factor=1.05, liquidation_health_factor=1.0),
    )
    plan = build_liquidation_execution_plan(
        account,
        rows[0],
        recommended_candidate=None,
        config=LiquidationScanConfig(warning_health_factor=1.05, liquidation_health_factor=1.0),
    )

    assert rows[0]["status"] == "healthy"
    assert rows[0]["health_factor"] == pytest.approx(1.0558590915925437)
    assert rows[0]["health_factor"] > 1.0
    assert plan["liquidation_ready"] is False
    assert plan["execution_ready"] is False


def test_build_liquidation_execution_payload_requires_static_preflight():
    report = {
        "account": "0x0000000000000000000000000000000000000001",
        "summary": {"status": "liquidatable"},
        "execution_plan": {"execution_ready": True},
        "recommended_candidate": {
            "collateral_asset": "0x0000000000000000000000000000000000000002",
            "debt_asset": "0x0000000000000000000000000000000000000003",
            "amount_to_pass_to_liquidation_call": 1000,
            "min_collateral_swap_out": 900,
            "estimated_profit": {"net_profit_base": 123},
        },
    }

    payload = build_liquidation_execution_payload(
        report,
        executor_address="0x0000000000000000000000000000000000000004",
        router_address="0x0000000000000000000000000000000000000005",
        deadline=123456,
    )

    assert payload["method"] == "requestLiquidation"
    assert payload["request"]["debtToCover"] == "1000"
    assert payload["request"]["minCollateralSwapOut"] == "900"
    assert payload["request"]["minProfitAmount"] == "113"
    assert payload["preflight"]["static_call_required"] is True


def test_build_liquidation_execution_payload_rejects_healthy_account():
    report = {
        "account": "0x0000000000000000000000000000000000000001",
        "summary": {"status": "healthy"},
        "execution_plan": {"execution_ready": False},
        "recommended_candidate": {
            "collateral_asset": "0x0000000000000000000000000000000000000002",
            "debt_asset": "0x0000000000000000000000000000000000000003",
            "amount_to_pass_to_liquidation_call": 1000,
            "min_collateral_swap_out": 900,
            "estimated_profit": {"net_profit_base": 123},
        },
    }

    with pytest.raises(ValueError, match="not liquidatable"):
        build_liquidation_execution_payload(
            report,
            executor_address="0x0000000000000000000000000000000000000004",
            router_address="0x0000000000000000000000000000000000000005",
            deadline=123456,
        )


def test_build_liquidation_execution_payload_requires_min_swap_output():
    report = {
        "account": "0x0000000000000000000000000000000000000001",
        "summary": {"status": "liquidatable"},
        "execution_plan": {"execution_ready": True},
        "recommended_candidate": {
            "collateral_asset": "0x0000000000000000000000000000000000000002",
            "debt_asset": "0x0000000000000000000000000000000000000003",
            "amount_to_pass_to_liquidation_call": 1000,
            "estimated_profit": {"net_profit_base": 123},
        },
    }

    with pytest.raises(ValueError, match="min_collateral_swap_out"):
        build_liquidation_execution_payload(
            report,
            executor_address="0x0000000000000000000000000000000000000004",
            router_address="0x0000000000000000000000000000000000000005",
            deadline=123456,
        )
