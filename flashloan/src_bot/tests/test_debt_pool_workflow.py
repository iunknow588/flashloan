from web.debt_pool_workflow import decide_debt_pool_layers, decision_from_borrow_pool_payload


def test_debt_pool_decision_routes_only_core_liquidatable_to_execution():
    decision = decide_debt_pool_layers(
        core_rows=[{"account": "0x1", "status": "liquidatable", "health_factor": 0.98, "last_scanned_at": "2026-08-02T00:00:00+00:00"}],
        high_frequency_rows=[{"account": "0x2", "health_factor": 1.02}],
        normal_rows=[{"account": "0x3", "health_factor": 1.2}],
        block_number=123,
        checked_at="2026-08-02T01:00:00+00:00",
    )

    assert decision["result"] == "CORE_POOL_LIQUIDATABLE"
    assert decision["route_intent"] == "execution"
    assert decision["liquidatable_context"]["account"] == "0x1"
    assert decision["liquidatable_context"]["source_pool"] == "core"
    assert decision["liquidatable_context"]["block_number"] == 123
    assert decision["liquidatable_context"]["candidate_hash"]


def test_debt_pool_decision_syncs_high_frequency_before_execution():
    decision = decide_debt_pool_layers(
        core_rows=[],
        high_frequency_rows=[{"account": "0x2", "health_factor": 1.02}],
        normal_rows=[{"account": "0x3", "health_factor": 1.2}],
    )

    assert decision["result"] == "HIGH_FREQUENCY_RISK_FOUND"
    assert decision["route_intent"] == "sync_core_then_rejudge"
    assert decision["liquidatable_context"] is None
    assert decision["risk_context"]["source_pool"] == "high_frequency"


def test_debt_pool_decision_syncs_normal_pool_only_after_high_frequency_empty():
    decision = decide_debt_pool_layers(
        core_rows=[],
        high_frequency_rows=[],
        normal_rows=[{"account": "0x3", "health_factor": 1.2}],
    )

    assert decision["result"] == "NORMAL_POOL_RISK_FOUND"
    assert decision["route_intent"] == "sync_core_then_rejudge"
    assert decision["risk_context"]["source_pool"] == "normal"


def test_debt_pool_decision_from_payload_excludes_core_and_high_rows_from_normal():
    payload = {
        "rows": [
            {"account": "0x1", "health_factor": 0.98},
            {"account": "0x2", "health_factor": 1.02},
            {"account": "0x3", "health_factor": 1.2},
        ],
        "tiers": {
            "core_opportunity_rows": [{"account": "0x1", "health_factor": 0.98}],
            "high_frequency_rows": [{"account": "0x2", "health_factor": 1.02}],
        },
        "summary": {"block_number": 456},
    }

    decision = decision_from_borrow_pool_payload(payload)

    assert decision["result"] == "CORE_POOL_LIQUIDATABLE"
    assert decision["counts"] == {"core": 1, "high_frequency": 1, "normal": 1}

