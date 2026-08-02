from datetime import datetime, timedelta, timezone

from execution.liquidation_preflight import evaluate_liquidation_submission, force_remaining_blockers


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
    assert state["block_level"] == "none"
    assert state["force_allowed"] is False


def test_liquidation_submission_blocks_without_static_call():
    payload = base_payload()
    payload["preflight"]["static_call_passed"] = False
    payload["preflight"]["static_call_status"] = "pending"

    state = evaluate_liquidation_submission(payload, base_controls())

    assert state["submission_allowed"] is False
    assert "static_call_required" in state["blocked_reasons"]
    assert state["block_level"] == "soft"
    assert state["soft_blocked_reasons"] == ["static_call_required"]
    assert state["hard_blocked_reasons"] == []
    assert state["force_allowed"] is True


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
    assert state["block_level"] == "hard"
    assert state["force_allowed"] is False


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
    assert state["hard_blocked_reasons"] == ["chain_id_mismatch", "private_key_mismatch"]
    assert state["force_allowed"] is False
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


def test_liquidation_submission_blocks_fallback_close_factor_and_premium():
    payload = base_payload()
    payload["account_report"]["recommended_candidate"] = {
        "repay_base_source": "close_factor_fallback",
        "estimated_profit": {"flashloan_premium_source": "fallback_config"},
    }

    state = evaluate_liquidation_submission(payload, base_controls())

    assert state["submission_allowed"] is False
    assert "fallback_close_factor" in state["blocked_reasons"]
    assert "fallback_flashloan_premium" in state["blocked_reasons"]
    assert state["checks"]["repay_base_source"] == "close_factor_fallback"


def test_liquidation_submission_allows_verified_parameter_sources():
    payload = base_payload()
    payload["account_report"]["recommended_candidate"] = {
        "repay_base_source": "amount_to_pass_to_liquidation_call",
        "parameter_sources": {
            "amount_to_pass_source": "amount_to_pass_to_liquidation_call",
            "close_factor_source": "liquidation_data_provider",
            "liquidation_bonus_source": "fallback_config",
            "protocol_fee_source": "liquidation_data_provider",
            "flashloan_premium_source": "aave_pool",
            "flashloan_premium_block_number": 123,
        },
        "estimated_profit": {"flashloan_premium_source": "aave_pool"},
    }

    state = evaluate_liquidation_submission(payload, base_controls())

    assert state["submission_allowed"] is True
    assert state["checks"]["flashloan_premium_source"] == "aave_pool"
    assert state["checks"]["flashloan_premium_block_number"] == 123
    assert state["checks"]["close_factor_source"] == "liquidation_data_provider"


def test_liquidation_submission_blocks_high_gas_and_operator_profit():
    payload = base_payload()
    payload["amounts"] = {
        "profit": {
            "gas_cost_usd": 12.5,
            "operator_net_profit_estimate_usd": 0.75,
        }
    }
    controls = {
        **base_controls(),
        "max_gas_cost_usd": 10,
        "min_operator_net_profit_usd": 1,
    }

    state = evaluate_liquidation_submission(payload, controls)

    assert state["submission_allowed"] is False
    assert "gas_cost_too_high" in state["blocked_reasons"]
    assert "profit_below_minimum" in state["blocked_reasons"]
    assert state["checks"]["protected_operator_net_profit_usd"] == 0.75


def test_liquidation_submission_blocks_auto_pause():
    controls = {
        **base_controls(),
        "auto_pause_active": True,
        "auto_pause_failure_count": 3,
        "auto_pause_threshold": 3,
        "auto_pause_reason": "static_call_failed",
    }

    state = evaluate_liquidation_submission(base_payload(), controls)

    assert state["submission_allowed"] is False
    assert "auto_pause_active" in state["blocked_reasons"]
    assert state["block_level"] == "hard"
    assert state["force_allowed"] is False
    assert state["checks"]["auto_pause_reason"] == "static_call_failed"


def test_force_remaining_blockers_keeps_hard_and_config_blockers_only():
    blockers = [
        "static_call_required",
        "profit_below_minimum",
        "execution_disabled",
        "account_not_liquidatable",
    ]

    assert force_remaining_blockers(blockers) == ["execution_disabled", "account_not_liquidatable"]
