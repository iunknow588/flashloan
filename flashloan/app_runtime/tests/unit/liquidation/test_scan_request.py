from bot.liquidation.scan_request import (
    parse_bool,
    parse_market_request,
    parse_scan_request,
)
from bot.liquidation.scan_presenter import build_borrow_pool_scan_summary


def test_parse_scan_request_normalizes_pagination_scope_and_force():
    parsed = parse_scan_request(
        {
            "force": "yes",
            "page_size": "500",
            "risk_page": "0",
            "high_page": "2",
            "core_page": "bad",
            "market_id": " bnb-venus-core ",
            "chain_id": "56",
        }
    )

    assert parsed.force is True
    assert parsed.pagination_requested is True
    assert parsed.pagination.as_kwargs() == {
        "page_size": 100,
        "risk_page": 1,
        "high_page": 2,
        "core_page": 1,
    }
    assert parsed.market.as_kwargs() == {"market_id": "bnb-venus-core", "chain_id": 56}


def test_parse_scan_request_accepts_force_from_json_body_without_pagination():
    parsed = parse_scan_request({}, {"force": "true"})

    assert parsed.force is True
    assert parsed.pagination_requested is False
    assert parsed.pagination.as_kwargs() == {
        "page_size": 20,
        "risk_page": 1,
        "high_page": 1,
        "core_page": 1,
    }
    assert parsed.market.as_kwargs() == {}


def test_market_request_discards_invalid_chain_id_and_boolean_parser_is_strict():
    assert parse_market_request({"market_id": "", "chain_id": "not-a-number"}).as_kwargs() == {}
    assert parse_bool("on") is True
    assert parse_bool("false", default=True) is False


def test_borrow_pool_scan_summary_preserves_scope_and_scan_evidence():
    summary = build_borrow_pool_scan_summary(
        database_url_fingerprint="db:abc",
        scope={"market_id": "avalanche-aave-v3", "chain_id": 43114, "network": "avalanche", "protocol": "aave_v3"},
        force=False,
        source_account_count=10,
        scanned_count=4,
        selected_account_count=4,
        scan_selection={
            "strategy": "core_only",
            "included_tiers": ["core_opportunity"],
            "core_due": True,
            "core_account_count": 4,
        },
        tiers={"borrow_health_count": 2},
        sync_result={"entered_count": 1, "exited_count": 0},
        rpc_url="https://rpc.example",
        block_number=123,
        latest_batch={"id": 9, "status": "success"},
        account_tiers={"active_count": 4},
        error=None,
    )

    assert summary["market_id"] == "avalanche-aave-v3"
    assert summary["chain_id"] == 43114
    assert summary["scan_response_source"] == "chain_scan"
    assert summary["scanned_count"] == 4
    assert summary["pool_counts"] == {"borrow_health_count": 2}
    assert summary["latest_batch"]["status"] == "success"
