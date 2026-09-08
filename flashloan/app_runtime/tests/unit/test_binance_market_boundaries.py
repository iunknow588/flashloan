from pathlib import Path

from market.binance_market.state_service import select_market_state
from market.binance_market.submission_readiness import build_submission_readiness
from market.binance_market.candidate_service import build_pair_candidates
from market.binance_market.submission_service import submit_ready_quote_orders


def test_market_state_uses_explicit_stale_snapshot_fallback():
    calls = []

    def needs_refresh(state, _limit):
        return not bool(state and state.get("usable"))

    def read_snapshot(_path):
        return {"usable": True, "value": "cached"}

    def previous_snapshot(snapshot, _max_age):
        return snapshot

    def build_snapshot(_previous, _limit):
        raise RuntimeError("rpc unavailable")

    selected = select_market_state(
        None,
        side_limit=5,
        snapshot_path=Path("snapshot.json"),
        max_previous_age_seconds=60,
        needs_refresh=needs_refresh,
        read_snapshot=read_snapshot,
        previous_snapshot=previous_snapshot,
        build_snapshot=build_snapshot,
        persist_snapshot=lambda *_args: calls.append("persist"),
    )

    assert selected["market_state_source"] == "snapshot_file_stale_fallback"
    assert selected["fallback_reason"] == "rest_interval_refresh_failed"
    assert calls == []


def test_submission_readiness_distinguishes_quote_failure_from_submission_failure():
    result = build_submission_readiness(
        checks_passed=True,
        route_supported=True,
        quote_available=False,
        quote_verified=False,
        submission_requested=True,
        adapter_available=True,
        sdk_ready=True,
        network_supported=True,
        signer_ready=True,
        quote_error_type="quote_api_timeout",
    )

    assert result["ready"] is False
    assert result["can_submit_order"] is False
    assert result["blocked_reasons"] == ["quote_api_timeout", "quote_not_verified"]


def test_submission_readiness_is_ready_only_when_all_execution_inputs_pass():
    result = build_submission_readiness(
        checks_passed=True,
        route_supported=True,
        quote_available=True,
        quote_verified=True,
        submission_requested=True,
        adapter_available=True,
        sdk_ready=True,
        network_supported=True,
        signer_ready=True,
    )

    assert result["ready"] is True
    assert result["blocked_reasons"] == []


def test_candidate_service_keeps_pool_rejections_structured():
    pairs, rejected = build_pair_candidates(
        [{"symbol": "A", "base_symbol": "A", "change_percent": 5}],
        [{"symbol": "B", "base_symbol": "B", "change_percent": -2}],
        min_spread_percent=1,
        network="bnb",
        pool_filter={},
        route_support=lambda *_args: {"supported": False, "reason": "missing_pool", "missingPairs": ["USDT:A"]},
        pair_builder=lambda _x, _y: {"pair": "A:B"},
    )

    assert pairs == []
    assert rejected == [{"pair": "A:B", "missingPairs": ["USDT:A"], "reason": "missing_pool"}]


def test_submission_service_updates_legacy_payload_shape():
    payload = {
        "ranking": [
            {
                "pair": "A / B",
                "execution_precheck": {
                    "can_submit_order": True,
                    "checks_passed": True,
                    "quote_rule_verified": True,
                    "submission_gate": {"ready": True, "submission_rule_verified": True},
                },
                "cow_sdk_result": {},
            }
        ],
        "cow_network": "bnb",
        "cow_chain_id": 56,
    }
    result = submit_ready_quote_orders(
        payload,
        market_state={"source": "unit"},
        submitter=lambda **_kwargs: {"submitted": True, "status": "submitted", "order_id": "order-1"},
    )

    assert result["submission_summary"]["submitted"] == 1
    assert result["ranking"][0]["execution_precheck"]["status"] == "submitted_success"


def test_submission_service_skips_rows_without_rule_verification():
    payload = {
        "ranking": [
            {
                "pair": "A / B",
                "execution_precheck": {"can_submit_order": True, "checks_passed": True, "quote_rule_verified": False},
                "cow_sdk_result": {},
            }
        ],
        "cow_network": "bnb",
        "cow_chain_id": 56,
    }
    result = submit_ready_quote_orders(
        payload,
        market_state={"source": "unit"},
        submitter=lambda **_kwargs: {"submitted": True, "status": "submitted", "order_id": "order-1"},
    )

    assert result["submission_summary"]["submitted"] == 0
    assert result["submission_summary"]["skipped"] == 1
