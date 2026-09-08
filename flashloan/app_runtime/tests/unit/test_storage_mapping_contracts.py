from datetime import datetime, timezone

import pytest

from core.db.cow_execution_mapping import scope_consistency_reasons
from core.db.liquidation_account_mapping import account_report_from_row, core_opportunity_payload_from_row
from core.db.liquidation_attempts_mapping import execution_attempt_from_row, json_or_default
from core.db.liquidation_failures_mapping import failure_row
from core.db.liquidation_pool_rows import (
    borrow_health_candidate_from_row,
    borrow_health_scan_batch_from_row,
    core_opportunity_pool_row_from_row,
)


def _scope(*, market_id="liquidation-56-venus-core", chain_id=56, network="bnb", protocol="venus_core"):
    return {
        "market_id": market_id,
        "chain_id": chain_id,
        "network": network,
        "protocol": protocol,
    }


def test_json_or_default_handles_invalid_json_without_raising():
    assert json_or_default("not-json", []) == []
    assert json_or_default("", {}) == {}
    assert json_or_default(b"not-a-string", None) is None
    assert json_or_default('{"ok": true}', None) == {"ok": True}


def test_cow_execution_scope_consistency_rejects_mismatched_nested_evidence():
    row = {
        "network": "robinhood-testnet",
        "chain_id": 46630,
        "market_id": "dex-arbitrage-46630",
        "quote": {
            "live_onchain_evidence": {
                "network": "robinhood",
                "chainId": "4663",
                "market_id": "dex-arbitrage-4663",
            }
        },
    }

    reasons = scope_consistency_reasons(row)

    assert "network_scope_mismatch" in reasons
    assert "chain_id_scope_mismatch" in reasons
    assert "market_id_scope_mismatch" in reasons


def test_cow_execution_scope_consistency_accepts_matching_nested_evidence():
    row = {
        "network": "robinhood-testnet",
        "chain_id": 46630,
        "market_id": "dex-arbitrage-46630",
        "quote": {
            "live_onchain_evidence": {
                "network": "ROBINHOOD-TESTNET",
                "chainId": "46630",
                "market_id": "DEX-ARBITRAGE-46630",
            }
        },
    }

    assert scope_consistency_reasons(row) == []


def test_execution_attempt_from_row_expands_compact_scope():
    scope = _scope()
    row = (
        1,
        "0x0000000000000000000000000000000000000001",
        "manual",
        "failed",
        '["missing_gas"]',
        '{"chainId": 56}',
        "{}",
        "{}",
        None,
        "{}",
        "boom",
        datetime(2026, 1, 1, tzinfo=timezone.utc),
        datetime(2026, 1, 2, tzinfo=timezone.utc),
    )

    payload = execution_attempt_from_row(row, scope)

    assert payload["market_id"] == scope["market_id"]
    assert payload["chain_id"] == scope["chain_id"]
    assert payload["account"] == "0x0000000000000000000000000000000000000001"
    assert payload["blocked_reasons"] == ["missing_gas"]
    assert payload["error"] == "boom"
    assert payload["created_at"] == "2026-01-01T00:00:00+00:00"


def test_execution_attempt_from_row_rejects_wrong_scope():
    row = (
        "wrong-market",
        56,
        "bnb",
        "venus_core",
        1,
        "0x0000000000000000000000000000000000000001",
        "manual",
        "failed",
        "[]",
        "{}",
        "{}",
        "{}",
        None,
        "{}",
        None,
        None,
        None,
    )

    with pytest.raises(ValueError):
        execution_attempt_from_row(row, _scope())


def test_failure_row_keeps_scope_and_parses_payload_json():
    scope = _scope()
    row = (
        scope["market_id"],
        scope["chain_id"],
        scope["network"],
        scope["protocol"],
        1,
        "0x0000000000000000000000000000000000000001",
        123,
        "USDC",
        "WBNB",
        "execution_reverted",
        "failed",
        '{"reason": "out of gas"}',
        "manual",
        datetime(2026, 1, 3, tzinfo=timezone.utc),
    )

    payload = failure_row(row, scope)

    assert payload["market_id"] == scope["market_id"]
    assert payload["chain_id"] == scope["chain_id"]
    assert payload["payload"] == {"reason": "out of gas"}
    assert payload["created_at"] == "2026-01-03T00:00:00+00:00"


def test_borrow_health_scan_batch_from_row_expands_compact_scope_and_metadata():
    scope = _scope()
    row = (
        scope["market_id"],
        scope["chain_id"],
        scope["network"],
        scope["protocol"],
        1,
        datetime(2026, 1, 1, tzinfo=timezone.utc),
        datetime(2026, 1, 1, 1, 0, tzinfo=timezone.utc),
        "completed",
        100,
        90,
        5,
        2,
        3,
        4,
        "https://rpc.example.com",
        999999,
        1.08,
        None,
        '{"source": "test"}',
    )

    payload = borrow_health_scan_batch_from_row(row, scope)

    assert payload["market_id"] == scope["market_id"]
    assert payload["chain_id"] == scope["chain_id"]
    assert payload["status"] == "completed"
    assert payload["account_count"] == 100
    assert payload["block_number"] == 999999
    assert payload["metadata"] == {"source": "test"}


def test_account_report_from_row_parses_summary_report_and_scope_fields():
    scope = _scope()
    row = {
        "market_id": scope["market_id"],
        "chain_id": scope["chain_id"],
        "network": scope["network"],
        "protocol": scope["protocol"],
        "account": "0xabc",
        "source": "scan",
        "active": True,
        "scan_start_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
        "scan_end_at": datetime(2026, 1, 1, 1, tzinfo=timezone.utc),
        "last_scanned_at": datetime(2026, 1, 2, tzinfo=timezone.utc),
        "last_health_factor": 1.08,
        "last_status": "liquidatable",
        "last_health_factor_band": "risk",
        "last_candidate_count": 3,
        "last_total_collateral_base": 120.5,
        "last_total_debt_base": 80.25,
        "activity_tier": "hot",
        "last_summary_json": '{"health_factor": 1.08, "status": "liquidatable"}',
        "last_report_json": '{"positions": [{"asset": "USDC"}], "summary": {"health_factor": 1.08}}',
    }

    payload = account_report_from_row(row)

    assert payload["market_id"] == scope["market_id"]
    assert payload["chain_id"] == scope["chain_id"]
    assert payload["summary"] == {"health_factor": 1.08, "status": "liquidatable"}
    assert payload["report"]["positions"] == [{"asset": "USDC"}]
    assert payload["last_scanned_at"] == "2026-01-02T00:00:00+00:00"
    assert payload["activity_tier"] == "hot"


def test_account_report_from_row_tolerates_invalid_json_and_empty_rows():
    row = {
        "market_id": "liquidation-56-venus-core",
        "chain_id": 56,
        "network": "bnb",
        "protocol": "venus_core",
        "account": "0x1",
        "source": None,
        "active": False,
        "scan_start_at": None,
        "scan_end_at": None,
        "last_scanned_at": None,
        "last_health_factor": None,
        "last_status": None,
        "last_health_factor_band": None,
        "last_candidate_count": None,
        "last_total_collateral_base": None,
        "last_total_debt_base": None,
        "activity_tier": None,
        "last_summary_json": "not-json",
        "last_report_json": "",
    }

    payload = account_report_from_row(row)

    assert payload["summary"] == {}
    assert payload["report"] == {}
    assert payload["source"] is None
    assert payload["last_health_factor"] is None


def test_core_opportunity_payload_from_row_builds_assessment_and_recommended_candidate():
    core_row = (
        1,
        "0xabc",
        0.767,
        99.5,
        120.0,
        100.0,
        "USDC",
        "WBNB",
        90.0,
        1.4,
        0.25,
        True,
        "ok",
        "over_1u_candidate",
        "[]",
        datetime(2026, 1, 2, tzinfo=timezone.utc),
        '{"recommended_candidate": {"collateral_symbol": "WBNB", "debt_symbol": "USDC"}}',
    )

    payload = core_opportunity_payload_from_row(core_row, {"status": "liquidatable"}, min_profit_usd=1.0)

    assert payload["rank"] == 1
    assert payload["quote_viable"] is True
    assert payload["payload_state"] == "over_1u_candidate"
    assert payload["profit_assessment"]["above_auto_profit_threshold"] is True
    assert payload["blocked_reasons"] == []
    assert payload["last_scanned_at"] == "2026-01-02T00:00:00+00:00"


def test_borrow_health_candidate_from_row_expands_scope_and_parses_report_json():
    scope = _scope()
    row = (
        "0xabc",
        0.98,
        "warning",
        "orange",
        500.0,
        400.0,
        2,
        datetime(2026, 1, 2, tzinfo=timezone.utc),
        '{"positions": []}',
    )

    payload = borrow_health_candidate_from_row(row, scope)

    assert payload["market_id"] == scope["market_id"]
    assert payload["chain_id"] == scope["chain_id"]
    assert payload["account"] == "0xabc"
    assert payload["health_factor"] == 0.98
    assert payload["report"] == {"positions": []}
    assert payload["last_scanned_at"] == "2026-01-02T00:00:00+00:00"


def test_core_opportunity_pool_row_from_row_builds_fallback_profit_assessment():
    scope = _scope()
    row = (
        scope["market_id"],
        scope["chain_id"],
        scope["network"],
        scope["protocol"],
        "0xabc",
        0.767,
        88.0,
        100.0,
        120.0,
        "USDC",
        "WBNB",
        90.0,
        2.5,
        0.3,
        True,
        "ok",
        "over_1u_candidate",
        "[]",
        datetime(2026, 1, 2, tzinfo=timezone.utc),
        "{}",
    )

    payload = core_opportunity_pool_row_from_row(row, scope)

    assert payload["best_debt_asset"] == "USDC"
    assert payload["quote_viable"] is True
    assert payload["profit_assessment"]["above_auto_profit_threshold"] is True
    assert payload["profit_assessment_label"] == "over_1u_candidate"
    assert payload["auto_execution_blocked"] is False
