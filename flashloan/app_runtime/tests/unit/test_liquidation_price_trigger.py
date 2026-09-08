import pytest

from runtime.liquidation_price_trigger import (
    accounts_triggered_by_prices,
    build_liquidation_price_triggers,
)


def test_single_collateral_downside_trigger_price():
    report = {
        "summary": {"health_factor": 1.1, "total_debt_base": 800},
        "positions": [
            {
                "symbol": "WAVAX",
                "collateral_value_base": 1000,
                "liquidation_threshold": 0.88,
            }
        ],
    }

    payload = build_liquidation_price_triggers(report, {"AVAXUSDT": 10.0})

    trigger = payload["triggers"][0]
    assert payload["enabled"] is True
    assert trigger["direction"] == "down"
    assert trigger["trigger_price"] == pytest.approx(9.090909, rel=1e-6)
    assert trigger["triggered"] is False


def test_multi_collateral_trigger_solves_only_watched_asset_contribution():
    report = {
        "summary": {"health_factor": 1.05, "total_debt_base": 1000},
        "positions": [
            {"symbol": "WAVAX", "collateral_value_base": 500, "liquidation_threshold": 0.8},
            {"symbol": "SAVAX", "collateral_value_base": 812.5, "liquidation_threshold": 0.8},
        ],
    }

    payload = build_liquidation_price_triggers(
        report,
        {"AVAXUSDT": 10.0, "SAVAXUSDT": 12.0},
        buffer_bps=0,
    )

    by_asset = {row["asset"]: row for row in payload["triggers"]}
    assert by_asset["WAVAX"]["trigger_price"] == pytest.approx(8.75)
    assert by_asset["SAVAX"]["trigger_price"] == pytest.approx(11.076923, rel=1e-6)


def test_debt_asset_upside_trigger_price():
    report = {
        "summary": {"total_debt_base": 1000},
        "positions": [
            {"symbol": "WAVAX", "collateral_value_base": 1250, "liquidation_threshold": 0.88},
            {"symbol": "AAVE", "debt_value_base": 1000},
        ],
    }

    payload = build_liquidation_price_triggers(report, {"AVAXUSDT": 10.0, "AAVEUSDT": 100.0})

    debt_trigger = [row for row in payload["triggers"] if row["direction"] == "up"][0]
    assert debt_trigger["asset"] == "AAVE"
    assert debt_trigger["trigger_price"] == pytest.approx(110.0)


def test_accounts_triggered_by_prices_filters_non_triggered_rows():
    rows = [
        {
            "account": "0xnear",
            "report": {
                "summary": {"health_factor": 1.001, "total_debt_base": 800},
                "positions": [{"symbol": "WAVAX", "collateral_value_base": 910, "liquidation_threshold": 0.88}],
            },
        },
        {
            "account": "0xsafe",
            "report": {
                "summary": {"health_factor": 1.2, "total_debt_base": 800},
                "positions": [{"symbol": "WAVAX", "collateral_value_base": 1091, "liquidation_threshold": 0.88}],
            },
        },
    ]

    accounts = accounts_triggered_by_prices(rows, {"AVAXUSDT": 10.0}, buffer_bps=25)

    assert accounts == ["0xnear"]


def test_accounts_triggered_by_prices_reads_core_pool_metadata_report():
    rows = [
        {
            "account": "0xmetadata",
            "metadata": {
                "report": {
                    "summary": {"health_factor": 1.001, "total_debt_base": 800},
                    "positions": [
                        {"symbol": "WAVAX", "collateral_value_base": 910, "liquidation_threshold": 0.88}
                    ],
                }
            },
        }
    ]

    accounts = accounts_triggered_by_prices(rows, {"AVAXUSDT": 10.0}, buffer_bps=25)

    assert accounts == ["0xmetadata"]
