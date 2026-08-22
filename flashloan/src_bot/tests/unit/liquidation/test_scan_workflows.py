from types import SimpleNamespace

from liquidation.account_payload_service import (
    LiquidationAccountPayloadRuntime,
    liquidation_account_payload,
)
from liquidation.health_scan_workflow import (
    LiquidationHealthRuntime,
    liquidation_health_payload,
)


def test_health_workflow_returns_fresh_scope_cache_without_acquiring_scan_lock():
    class Lock:
        def acquire(self, blocking=False):
            raise AssertionError("fresh cache must not acquire the scan lock")

    cache = {"payload": {"rows": [{"account": "0x1"}]}, "updated_at": __import__("time").monotonic()}
    runtime = LiquidationHealthRuntime(
        scan_cache=cache,
        scan_lock=Lock(),
        scan_interval_seconds=lambda: 60.0,
        scan_config=lambda: None,
        load_account_registry=lambda force=False: ([], "none"),
        auto_discovery_enabled=lambda: False,
        discover_accounts=lambda force_full=False: {},
        rpc_urls=lambda: [],
        pool_address=lambda: "",
        scan_account_health=lambda *args: [],
        record_scan_rows=lambda rows: None,
        error_message=lambda error: str(error),
        display_limit=lambda: 20,
        watched_health_rows=lambda rows, threshold: [],
        display_rows=lambda rows, limit: rows,
        summary=lambda *args: {},
        attach_scan_state=lambda payload, ttl, **kwargs: {
            **payload,
            "scan_ttl": ttl,
            "scan_state": kwargs,
        },
    )

    payload = liquidation_health_payload(runtime=runtime)

    assert payload["rows"] == [{"account": "0x1"}]
    assert payload["scan_ttl"] == 60.0
    assert payload["scan_state"]["running"] is False


def test_account_payload_service_records_snapshot_and_preserves_market_context():
    recorded = {}
    runtime = LiquidationAccountPayloadRuntime(
        is_bnb_venus_scope=lambda market_id, chain_id: False,
        bnb_account_payload=lambda account: {"account": account, "bnb": True},
        checksum_address=lambda account: f"checksum:{account}",
        scan_context_assets=lambda: ("https://rpc.example", [{"asset": "USDC"}], None),
        build_user_report=lambda *args: {"account": args[0], "summary": {"status": "warning"}},
        pool_address=lambda: "0xpool",
        protocol_data_provider_address=lambda: "0xprovider",
        liquidation_data_provider_address=lambda: "0xliquidation-provider",
        scan_config=lambda: {"watch": 1},
        database_url=lambda: "postgresql://example",
        record_account_scan=lambda database_url, report: recorded.update(database_url=database_url, report=report),
        prune_accounts=lambda database_url, retained_days: recorded.update(retained_days=retained_days),
        retention_days=lambda: 14,
        market_config=lambda: SimpleNamespace(as_dict=lambda: {"market_id": "avalanche-aave-v3", "chain_id": 43114}),
        load_account_snapshot=lambda *args, **kwargs: {},
    )

    payload = liquidation_account_payload("0xabc", runtime=runtime)

    assert payload["account"] == "checksum:0xabc"
    assert recorded["database_url"] == "postgresql://example"
    assert recorded["retained_days"] == 14
    assert payload["context"]["market"]["market_id"] == "avalanche-aave-v3"
    assert payload["context"]["reserve_asset_count"] == 1
