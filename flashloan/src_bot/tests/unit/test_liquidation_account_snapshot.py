from db.storage_liquidation_accounts import _preserve_existing_positions
from db.storage_liquidation_pool import (
    _account_report_with_summary,
    _account_summary_is_valid,
    _merge_account_report_sources,
    _merge_summary_with_registry,
)
from db.storage_liquidation_reports import (
    account_report_recovery_needs,
    load_historical_account_report_sources,
)


def test_health_only_report_does_not_overwrite_existing_positions():
    existing = {
        "positions_complete": True,
        "positions": [{"symbol": "AVAX", "collateral_amount": 2.0}],
    }
    incoming = {
        "summary": {"health_factor": 1.01},
        "liquidation_profit": {},
    }

    stored = _preserve_existing_positions(existing, incoming)

    assert stored["positions"] == existing["positions"]
    assert stored["positions_complete"] is True
    assert stored["summary"] == incoming["summary"]


def test_full_account_report_can_replace_existing_positions():
    existing = {
        "positions_complete": True,
        "positions": [{"symbol": "AVAX"}],
    }
    incoming = {
        "positions_complete": True,
        "positions": [{"symbol": "USDC"}],
    }

    stored = _preserve_existing_positions(existing, incoming)

    assert stored == incoming


def test_failed_scan_does_not_clear_existing_health_factor():
    existing = {
        "summary": {
            "health_factor": 0.767,
            "status": "liquidatable",
            "health_factor_band": "red",
            "total_collateral_base": 0.00004598,
            "total_debt_base": 0.00004763,
        }
    }
    incoming = {
        "summary": {
            "health_factor": None,
            "status": "error",
            "health_factor_band": None,
        }
    }

    stored = _preserve_existing_positions(existing, incoming)

    assert stored["summary"]["health_factor"] == 0.767
    assert stored["summary"]["status"] == "liquidatable"
    assert stored["summary"]["health_factor_band"] == "red"
    assert stored["summary"]["total_debt_base"] == 0.00004763


def test_account_report_merge_restores_positions_from_scan_report():
    pool_report = {
        "summary": {
            "health_factor": 1.005,
            "total_collateral_base": 100.0,
            "total_debt_base": 90.0,
        },
        "positions": [],
        "liquidation_candidates": [],
    }
    scan_report = {
        "positions": [
            {
                "symbol": "AVAX",
                "collateral_amount": 10.0,
                "stable_debt_amount": 0.0,
                "variable_debt_amount": 2.0,
                "usage_as_collateral_enabled": True,
            }
        ],
        "liquidation_candidates": [{"collateral_symbol": "AVAX", "debt_symbol": "USDC"}],
    }

    merged = _merge_account_report_sources([pool_report, scan_report])

    assert merged["positions"] == scan_report["positions"]
    assert merged["liquidation_candidates"] == scan_report["liquidation_candidates"]
    assert merged["summary"] == pool_report["summary"]


def test_account_report_merge_ignores_invalid_sources_and_keeps_first_values():
    first = {
        "positions": [{"symbol": "WAVAX"}],
        "realtime_params": {"source": "aave"},
        "summary": {"health_factor": 0.99},
    }

    merged = _merge_account_report_sources([None, "invalid", first, {"positions": [{"symbol": "USDC"}]}])

    assert merged == first


def test_account_report_merge_replaces_error_summary_with_valid_history():
    current = {
        "summary": {
            "health_factor": None,
            "status": "error",
            "total_collateral_base": None,
            "total_debt_base": None,
        },
        "liquidation_profit": {"operator_net_profit_usd": 100.0},
    }
    history = {
        "summary": {
            "health_factor": 1.006,
            "status": "warning",
            "health_factor_band": "orange",
            "total_collateral_base": 460000.0,
            "total_debt_base": 434000.0,
        }
    }

    merged = _merge_account_report_sources([current, history])

    assert merged["summary"] == history["summary"]
    assert merged["liquidation_profit"] == current["liquidation_profit"]


def test_account_report_uses_valid_summary_json_when_report_summary_failed():
    report = {
        "summary": {
            "health_factor": None,
            "status": "error",
            "total_collateral_base": None,
            "total_debt_base": None,
        },
        "positions": [{"symbol": "SAVAX"}],
    }
    summary = {
        "health_factor": 1.006,
        "status": "warning",
        "total_collateral_base": 460000.0,
        "total_debt_base": 434000.0,
    }

    restored = _account_report_with_summary(report, summary)

    assert _account_summary_is_valid(restored["summary"]) is True
    assert restored["summary"] == summary
    assert restored["positions"] == report["positions"]


def test_historical_report_lookup_can_query_only_missing_positions():
    positions_row = ("2026-08-03T07:33:10Z", "{}", '{"positions":[{"symbol":"SAVAX"}]}')

    class Cursor:
        def __init__(self):
            self.queries = []

        def execute(self, query, params):
            self.queries.append((query, params))

        def fetchone(self):
            return positions_row

    cursor = Cursor()
    latest_valid, latest_positions = load_historical_account_report_sources(
        cursor,
        market_id="avalanche-aave-v3",
        chain_id=43114,
        account="0x1",
        include_summary=False,
        include_positions=True,
    )

    assert latest_valid is None
    assert latest_positions == positions_row
    assert len(cursor.queries) == 1
    assert "jsonb_array_length" in cursor.queries[0][0]
    assert cursor.queries[0][1] == ("avalanche-aave-v3", 43114, "0x1")


def test_valid_health_scan_does_not_query_history_only_to_fill_positions():
    existing = {"summary": {"status": "error"}}
    incoming = {
        "summary": {
            "health_factor": 1.006,
            "status": "warning",
            "total_collateral_base": 460000.0,
            "total_debt_base": 434000.0,
        }
    }

    assert account_report_recovery_needs(existing, incoming) == (False, False)


def test_failed_scan_recovers_summary_and_positions_once_current_report_is_invalid():
    existing = {"summary": {"status": "error"}}
    incoming = {"summary": {"health_factor": None, "status": "error"}}

    assert account_report_recovery_needs(existing, incoming) == (True, True)


def test_summary_merge_restores_missing_values_from_account_registry():
    summary = {"health_factor": None, "status": "error", "candidate_count": 0}
    registry = (
        "avalanche-aave-v3",
        43114,
        "avalanche",
        "aave_v3",
        "0x1",
        "scan",
        True,
        None,
        0.767,
        "liquidatable",
        "red",
        0,
        0.00004598,
        0.00004763,
    )

    merged = _merge_summary_with_registry(summary, registry)

    assert merged["health_factor"] == 0.767
    assert merged["status"] == "liquidatable"
    assert merged["health_factor_band"] == "red"
    assert merged["total_collateral_base"] == 0.00004598
    assert merged["total_debt_base"] == 0.00004763
