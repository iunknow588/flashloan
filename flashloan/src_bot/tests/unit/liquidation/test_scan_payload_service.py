from liquidation.scan_payload_service import (
    build_borrow_pool_blocked_payload,
    build_borrow_pool_display_payload,
    core_execution_summary,
    page_size_value,
    page_value,
    pagination,
)


def test_pagination_values_are_bounded_and_page_count_is_stable():
    assert page_value("0") == 1
    assert page_value("bad", default=3) == 3
    assert page_size_value("500") == 100
    assert page_size_value("bad", default=25) == 25
    assert pagination(2, 20, 21) == {
        "page": 2,
        "page_size": 20,
        "total_count": 21,
        "page_count": 2,
    }


def test_blocked_payload_classifies_database_endpoint_and_preserves_independent_pages():
    payload = build_borrow_pool_blocked_payload(
        configured=True,
        error="endpoint has been disabled",
        decision_from_payload=lambda current: {
            "route_intent": "blocked",
            "row_count": len(current["rows"]),
        },
        page_size=25,
        risk_page=3,
        high_page=2,
        core_page=4,
    )

    assert payload["summary"]["scan_blocked_reason"] == "database_endpoint_disabled"
    assert payload["pagination"]["borrow_health"]["page"] == 3
    assert payload["tiers"]["pagination"]["high_frequency"]["page"] == 2
    assert payload["tiers"]["pagination"]["core_opportunity"]["page"] == 4
    assert payload["debt_pool_decision"] == {"route_intent": "blocked", "row_count": 0}


def test_core_execution_summary_hides_stale_attempt_result():
    row = {
        "auto_execution_blocked": False,
        "best_debt_asset": "USDC",
        "best_collateral_asset": "WAVAX",
        "quote_viable": True,
        "static_call_status": "passed",
        "estimated_operator_net_profit_usd": 12.5,
        "last_scanned_at": "2026-08-03T01:00:00+00:00",
    }
    attempt = {
        "state": "confirmed_success",
        "execution_phase": "confirmed_success",
        "tx_hash": "0xabc",
        "created_at": "2026-08-03T00:00:00+00:00",
    }

    summary = core_execution_summary(row, attempt)

    assert summary["value_state"] == "worth_executing"
    assert summary["execution_status"] == "preflight_passed"
    assert summary["execution_result"] == "not_submitted"
    assert summary["latest_attempt"]["stale"] is True


def test_display_payload_passes_market_scope_to_storage_and_summary():
    calls = []

    def load_borrow_health(database_url, *, limit, offset, **kwargs):
        calls.append(("borrow", database_url, limit, offset, kwargs))
        return [{"account": "0x1", "health_factor": 0.9}]

    def build_tiers(database_url, limit, *, high_page, core_page, **kwargs):
        calls.append(("tiers", database_url, limit, high_page, core_page, kwargs))
        return {
            "borrow_health_count": 1,
            "high_frequency_rows": [],
            "core_opportunity_rows": [],
            "high_frequency_count": 0,
            "core_opportunity_count": 0,
            "pagination": {
                "high_frequency": pagination(high_page, limit, 0),
                "core_opportunity": pagination(core_page, limit, 0),
            },
        }

    payload = build_borrow_pool_display_payload(
        database_url="postgresql://example",
        page_size=20,
        risk_page=2,
        high_page=3,
        core_page=4,
        scope={
            "market_id": "bnb-aave-v3",
            "chain_id": 56,
            "network": "bnb",
            "protocol": "aave_v3",
        },
        market_kwargs={"market_id": "bnb-aave-v3", "chain_id": 56},
        ensure_schema=lambda database_url: calls.append(("schema", database_url)),
        load_borrow_health=load_borrow_health,
        build_tiers=build_tiers,
        load_latest_batch=lambda database_url, **kwargs: {"id": 7},
        load_scan_configs=lambda database_url, limit, **kwargs: [{"name": "default"}],
        build_summary=lambda rows, **kwargs: {
            "count": len(rows),
            "display_limit": kwargs["display_limit"],
        },
        decision_from_payload=lambda current: {"route_intent": "sync_core_then_rejudge"},
        database_fingerprint=lambda database_url: "db:fingerprint",
    )

    assert payload["summary"]["market_id"] == "bnb-aave-v3"
    assert payload["summary"]["chain_id"] == 56
    assert payload["summary"]["scan_response_source"] == "database_display"
    assert payload["pagination"]["borrow_health"]["page"] == 2
    assert payload["debt_pool_decision"]["route_intent"] == "sync_core_then_rejudge"
    assert calls[0] == ("schema", "postgresql://example")
    assert calls[1][-1] == {"market_id": "bnb-aave-v3", "chain_id": 56}
    assert calls[2][-1] == {"market_id": "bnb-aave-v3", "chain_id": 56}
