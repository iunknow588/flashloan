from datetime import datetime, timedelta, timezone

from execution.liquidation_preflight import evaluate_liquidation_submission


def base_payload():
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return {
        "executor": "0x0000000000000000000000000000000000000004",
        "payload_built_at": now,
        "request": {
            "user": "0x0000000000000000000000000000000000000001",
            "debtToCover": "1000",
            "minProfitAmount": "100",
        },
        "preflight": {
            "static_call_required": True,
            "static_call_status": "passed",
            "static_call_passed": True,
        },
        "account_report": {"summary": {"status": "liquidatable"}},
        "dex_quote": {"viable": True, "quote_at": now},
    }


def base_controls():
    return {
        "execution_enabled": True,
        "require_static_call": True,
        "flashloan_executor_configured": True,
        "max_debt_to_cover": 0,
        "min_profit_base": 1,
        "max_payload_age_seconds": 30,
        "max_quote_age_seconds": 15,
    }


def test_liquidation_submission_allows_fresh_static_call_passed_payload():
    state = evaluate_liquidation_submission(base_payload(), base_controls())

    assert state["state"] == "submission_allowed"
    assert state["submission_allowed"] is True
    assert state["blocked_reasons"] == []


def test_liquidation_submission_blocks_without_static_call():
    payload = base_payload()
    payload["preflight"]["static_call_passed"] = False
    payload["preflight"]["static_call_status"] = "pending"

    state = evaluate_liquidation_submission(payload, base_controls())

    assert state["submission_allowed"] is False
    assert "static_call_required" in state["blocked_reasons"]


def test_liquidation_submission_blocks_expired_quote_and_low_profit():
    payload = base_payload()
    old = (datetime.now(timezone.utc) - timedelta(seconds=60)).isoformat(timespec="seconds")
    payload["dex_quote"]["quote_at"] = old
    payload["request"]["minProfitAmount"] = "0"

    state = evaluate_liquidation_submission(payload, base_controls())

    assert state["submission_allowed"] is False
    assert "quote_expired" in state["blocked_reasons"]
    assert "profit_below_minimum" in state["blocked_reasons"]


def test_liquidation_submission_blocks_debt_limit_and_healthy_account():
    payload = base_payload()
    payload["account_report"]["summary"]["status"] = "healthy"
    controls = {**base_controls(), "max_debt_to_cover": 500}

    state = evaluate_liquidation_submission(payload, controls)

    assert state["submission_allowed"] is False
    assert "debt_exceeds_limit" in state["blocked_reasons"]
    assert "account_not_liquidatable" in state["blocked_reasons"]


def test_liquidation_submission_blocks_config_errors():
    controls = {
        **base_controls(),
        "config_valid": False,
        "config_errors": ["chain id is 1, expected 43114"],
        "config_blocked_reasons": ["chain_id_mismatch", "private_key_mismatch"],
        "chain_id": 1,
        "expected_chain_id": 43114,
    }

    state = evaluate_liquidation_submission(base_payload(), controls)

    assert state["submission_allowed"] is False
    assert "chain_id_mismatch" in state["blocked_reasons"]
    assert "private_key_mismatch" in state["blocked_reasons"]
    assert state["checks"]["config_valid"] is False
    assert state["checks"]["chain_id"] == 1


def test_liquidation_submission_blocks_expired_deadline():
    current = datetime(2026, 7, 30, 10, 0, 0, tzinfo=timezone.utc)
    payload = base_payload()
    payload["request"]["deadline"] = str(int(current.timestamp()) - 1)

    state = evaluate_liquidation_submission(payload, base_controls(), now=current)

    assert state["submission_allowed"] is False
    assert "payload_expired" in state["blocked_reasons"]
    assert state["checks"]["deadline"] == int(current.timestamp()) - 1
