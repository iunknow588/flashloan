from datetime import datetime, timezone

import pytest

from core.db.cow_execution_mapping import scope_consistency_reasons
from core.db.liquidation_attempts_mapping import execution_attempt_from_row, json_or_default
from core.db.liquidation_failures_mapping import failure_row
from core.db.liquidation_pool_rows import borrow_health_scan_batch_from_row


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
