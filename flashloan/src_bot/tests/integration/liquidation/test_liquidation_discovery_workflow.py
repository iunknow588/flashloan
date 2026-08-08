from datetime import datetime, timezone
import threading

from liquidation import discovery_workflow as workflow
from web3 import Web3


class _FakeConfig:
    max_candidates = 10


class _FakeCtx:
    def __init__(self):
        self.database_url_or_none = lambda: "postgresql://example"
        self.LIQUIDATION_DISCOVERY_LOCK = threading.Lock()
        self.LIQUIDATION_DISCOVERY_CACHE = {"last_result": None}
        self.LIQUIDATION_ACCOUNT_CACHE = {}
        self.saved = None
        self.recorded = []
        self.registry = {"discovery_scan_progress": {}}

    def liquidation_scan_config(self):
        return _FakeConfig()

    def aave_rpc_urls(self):
        return ["https://rpc.example"]

    def resolve_discovery_block_range(self, rpc_url, from_block, to_block):
        return 123, 100, 120

    def discover_borrower_addresses(self, rpc_url, pool_address, actual_from_block, to_block=None, chunk_size=1000, limit=0, progress_callback=None):
        if progress_callback:
            progress_callback({"current_from_block": actual_from_block, "current_to_block": to_block, "discovered_count": 2, "stopped": False})
        return [
            "0x0000000000000000000000000000000000000001",
            "0x0000000000000000000000000000000000000002",
        ]

    def sync_liquidation_accounts_to_database(self, accounts, source="manual", scan_start_at=None, scan_end_at=None, update_existing=True):
        self.saved = {"accounts": list(accounts), "source": source, "scan_start_at": scan_start_at, "scan_end_at": scan_end_at, "update_existing": update_existing}

    def discovery_window_continuity_error(self, mode, from_block, to_block, progress):
        return None

    def record_liquidation_discovery_window(self, **kwargs):
        self.recorded.append(kwargs)

    def liquidation_retention_days(self):
        return 365

    def liquidation_recent_discovery_days(self):
        return 7

    def liquidation_backfill_window_days(self):
        return 7

    def liquidation_account_registry_window(self):
        return {"total_count": 2, "active_count": 2, "earliest_scan_start_at": None, "latest_scan_end_at": None, "retained_days": 365}

    def liquidation_discovery_interval_seconds(self):
        return 3600

    def liquidation_discovery_window(self, force_full=False):
        return (
            datetime(2026, 8, 1, tzinfo=timezone.utc),
            datetime(2026, 8, 2, tzinfo=timezone.utc),
            100,
            120,
            20,
            self.registry,
            "recent",
        )

    def build_discovery_window_result(self, **kwargs):
        return {"skipped": False, "mode": kwargs["mode"], "stage": "borrowers"}


def test_discovery_workflow_merges_external_index_and_onchain_candidates(monkeypatch):
    ctx = _FakeCtx()
    monkeypatch.setenv("AAVE_POOL_ADDRESS", "0x0000000000000000000000000000000000000009")
    monkeypatch.setenv("LIQUIDATION_AUTO_DISCOVER_ACCOUNTS", "true")
    monkeypatch.setattr(workflow, "fetch_external_borrower_accounts", lambda **kwargs: {"enabled": True, "configured": True, "source": "external-index-coarse", "count": 2, "accounts": ["0x0000000000000000000000000000000000000002", "0x0000000000000000000000000000000000000003"], "error": None, "requires_onchain_verification": True})

    result = workflow.discover_and_sync_liquidation_accounts(ctx, force_full=False)

    assert ctx.saved["accounts"] == [
        Web3.to_checksum_address("0x0000000000000000000000000000000000000001"),
        Web3.to_checksum_address("0x0000000000000000000000000000000000000002"),
        Web3.to_checksum_address("0x0000000000000000000000000000000000000003"),
    ]
    assert ctx.saved["source"] == "auto-discovery+external-index-coarse"
    assert result["count"] == 3
    assert result["external_index_count"] == 2
    assert result["onchain_log_count"] == 2
    assert result["requires_onchain_verification"] is True
    assert result["candidate_source_counts"] == {"onchain_borrow_logs": 2, "external_index_coarse": 2}
    assert result["external_index"]["enabled"] is True
    assert result["external_index"]["requires_onchain_verification"] is True
    assert ctx.LIQUIDATION_DISCOVERY_CACHE["progress"]["external_index"]["count"] == 2
    assert ctx.recorded


def test_external_index_disabled_keeps_existing_onchain_flow(monkeypatch):
    ctx = _FakeCtx()
    monkeypatch.setenv("AAVE_POOL_ADDRESS", "0x0000000000000000000000000000000000000009")
    monkeypatch.setenv("LIQUIDATION_AUTO_DISCOVER_ACCOUNTS", "true")
    monkeypatch.delenv("LIQUIDATION_EXTERNAL_INDEX_ENABLED", raising=False)
    monkeypatch.delenv("LIQUIDATION_EXTERNAL_INDEX_URL", raising=False)
    monkeypatch.setattr(workflow, "fetch_external_borrower_accounts", lambda **kwargs: {"enabled": False, "configured": False, "source": "external-index-coarse", "count": 0, "accounts": [], "error": None, "requires_onchain_verification": True})

    result = workflow.discover_and_sync_liquidation_accounts(ctx, force_full=False)

    assert ctx.saved["accounts"] == [
        Web3.to_checksum_address("0x0000000000000000000000000000000000000001"),
        Web3.to_checksum_address("0x0000000000000000000000000000000000000002"),
    ]
    assert ctx.saved["source"] == "auto-discovery"
    assert result["count"] == 2
    assert result["external_index_count"] == 0
    assert result["requires_onchain_verification"] is True
    assert ctx.LIQUIDATION_DISCOVERY_CACHE["progress"]["external_index"]["count"] == 0


def test_external_index_error_keeps_existing_onchain_flow(monkeypatch):
    ctx = _FakeCtx()
    monkeypatch.setenv("AAVE_POOL_ADDRESS", "0x0000000000000000000000000000000000000009")
    monkeypatch.setenv("LIQUIDATION_AUTO_DISCOVER_ACCOUNTS", "true")
    monkeypatch.setattr(
        workflow,
        "fetch_external_borrower_accounts",
        lambda **kwargs: {
            "enabled": True,
            "configured": True,
            "source": "external-index-coarse",
            "count": 0,
            "accounts": [],
            "error": "upstream timeout",
            "requires_onchain_verification": True,
        },
    )

    result = workflow.discover_and_sync_liquidation_accounts(ctx, force_full=False)

    assert ctx.saved["accounts"] == [
        Web3.to_checksum_address("0x0000000000000000000000000000000000000001"),
        Web3.to_checksum_address("0x0000000000000000000000000000000000000002"),
    ]
    assert ctx.saved["source"] == "auto-discovery"
    assert result["count"] == 2
    assert result["external_index_count"] == 0
    assert result["external_index"]["error"] == "upstream timeout"
    assert result["requires_onchain_verification"] is True
    assert ctx.LIQUIDATION_DISCOVERY_CACHE["progress"]["external_index"]["error"] == "upstream timeout"


def test_discovery_workflow_returns_configuration_error_for_invalid_chunk_size(monkeypatch):
    ctx = _FakeCtx()
    monkeypatch.setenv("AAVE_POOL_ADDRESS", "0x0000000000000000000000000000000000000009")
    monkeypatch.setenv("LIQUIDATION_AUTO_DISCOVER_ACCOUNTS", "true")
    monkeypatch.setenv("LIQUIDATION_BORROW_SCAN_CHUNK_SIZE", "invalid")
    monkeypatch.setattr(
        workflow,
        "fetch_external_borrower_accounts",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("external index must not run")),
    )

    result = workflow.discover_and_sync_liquidation_accounts(ctx, force_full=False)

    assert result["saved"] is False
    assert result["stage"] == "configuration"
    assert "LIQUIDATION_BORROW_SCAN_CHUNK_SIZE must be an integer" in result["error"]
    assert ctx.saved is None
    assert ctx.LIQUIDATION_DISCOVERY_CACHE["last_result"] == result
    assert ctx.LIQUIDATION_DISCOVERY_CACHE["running"] is False


def test_discovery_workflow_returns_configuration_error_for_negative_incremental_limit(monkeypatch):
    ctx = _FakeCtx()
    ctx.registry = {"discovery_scan_progress": {"latest_recent_to_block": 99}}
    monkeypatch.setenv("AAVE_POOL_ADDRESS", "0x0000000000000000000000000000000000000009")
    monkeypatch.setenv("LIQUIDATION_AUTO_DISCOVER_ACCOUNTS", "true")
    monkeypatch.setenv("LIQUIDATION_BORROW_DISCOVERY_LIMIT", "-1")
    monkeypatch.setattr(
        workflow,
        "fetch_external_borrower_accounts",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("external index must not run")),
    )

    result = workflow.discover_and_sync_liquidation_accounts(ctx, force_full=False)

    assert result["saved"] is False
    assert result["stage"] == "configuration"
    assert result["error"] == "LIQUIDATION_BORROW_DISCOVERY_LIMIT must be non-negative, got -1"
    assert ctx.saved is None
    assert ctx.LIQUIDATION_DISCOVERY_CACHE["last_result"] == result


def test_discovery_workflow_redacts_rpc_errors(monkeypatch):
    ctx = _FakeCtx()
    rpc_url = "https://rpc.example/path?token=abc123"
    private_key = "0x" + "5" * 64
    monkeypatch.setenv("AAVE_POOL_ADDRESS", "0x0000000000000000000000000000000000000009")
    monkeypatch.setenv("LIQUIDATION_AUTO_DISCOVER_ACCOUNTS", "true")
    monkeypatch.setenv("AVALANCHE_RPC_URL", rpc_url)
    monkeypatch.setattr(
        workflow,
        "fetch_external_borrower_accounts",
        lambda **kwargs: {
            "enabled": False,
            "configured": False,
            "source": "external-index-coarse",
            "count": 0,
            "accounts": [],
            "error": None,
            "requires_onchain_verification": True,
        },
    )

    ctx.aave_rpc_urls = lambda: [rpc_url]

    def fail_discover(*args, **kwargs):
        raise RuntimeError(f"rpc failed: {rpc_url} private_key={private_key}")

    ctx.discover_borrower_addresses = fail_discover

    result = workflow.discover_and_sync_liquidation_accounts(ctx, force_full=False)

    for value in (result["error"], ctx.recorded[0]["error"]):
        assert rpc_url not in value
        assert private_key not in value
        assert "abc123" not in value
        assert "[REDACTED]" in value
