from web.control_panel import (
    app,
    LIQUIDATION_SCAN_CACHE,
    LIQUIDATION_DISCOVERY_CACHE,
    discovery_window_continuity_error,
    liquidation_account_payload,
    liquidation_discovery_window,
    liquidation_health_payload,
    opportunity_health_rows,
    opportunity_health_summary,
    restrict_extremes_to_symbols,
)
from execution.liquidation_scan import LiquidationScanConfig
from datetime import datetime
from web3 import Web3


def test_restrict_extremes_to_current_observation_basket():
    extremes = {
        "sample_count": 3,
        "observation_universe_size": 470,
        "gainer_count": 2,
        "loser_count": 1,
        "market_divergence_index": 2 / 470,
        "basket": [
            {"symbol": "BTCUSDT", "current_price": 100.0, "change_percent": 1.0, "window_ready": True},
            {"symbol": "ETHUSDT", "current_price": 50.0, "change_percent": -1.0, "window_ready": True},
            {"symbol": "DOGEUSDT", "current_price": 0.1, "change_percent": 2.0, "window_ready": True},
        ],
    }

    filtered = restrict_extremes_to_symbols(extremes, ["BTCUSDT", "ETHUSDT"])

    assert [row["symbol"] for row in filtered["basket"]] == ["BTCUSDT", "ETHUSDT"]
    assert filtered["sample_count"] == 2
    assert filtered["observation_universe_size"] == 2
    assert filtered["gainer_count"] == 1
    assert filtered["loser_count"] == 1
    assert filtered["market_divergence_index"] == 0.5


def test_opportunity_health_rows_and_summary_rank_by_threshold():
    extremes = {
        "observed_at": "2026-07-29T10:00:00+00:00",
        "window_seconds": 0.2,
        "basket": [
            {
                "symbol": "BTCUSDT",
                "current_price": 100.0,
                "start_price": 98.0,
                "change_percent": 2.0,
                "window_ready": True,
                "price_source": "aave",
            },
            {
                "symbol": "ETHUSDT",
                "current_price": 50.0,
                "start_price": 49.8,
                "change_percent": 0.2,
                "window_ready": True,
                "price_source": "aave",
            },
            {
                "symbol": "SOLUSDT",
                "current_price": 20.0,
                "start_price": 20.0,
                "change_percent": -1.5,
                "window_ready": False,
                "price_source": "binance",
            },
        ],
    }
    config = {
        "TRIGGER_MIN_UP_CHANGE_PERCENT": 1.0,
        "TRIGGER_MIN_DOWN_CHANGE_PERCENT": 1.0,
        "BINANCE_CHANGE_WINDOW_SECONDS": 0.2,
    }

    rows = opportunity_health_rows(extremes, config)
    summary = opportunity_health_summary(rows, config)

    assert [row["symbol"] for row in rows] == ["BTCUSDT", "SOLUSDT", "ETHUSDT"]
    assert rows[0]["health_score"] == 200.0
    assert rows[0]["status"] == "selected"
    assert rows[1]["status"] == "watching"
    assert summary["total"] == 3
    assert summary["candidate_count"] == 1
    assert summary["selected_count"] == 1
    assert summary["best_symbol"] == "BTCUSDT"
    assert summary["monitor_window_seconds"] == 0.2


def test_liquidation_health_payload_includes_scan_summary(monkeypatch):
    from web import control_panel

    LIQUIDATION_SCAN_CACHE["updated_at"] = 0.0
    LIQUIDATION_SCAN_CACHE["payload"] = None
    monkeypatch.setattr(control_panel, "database_url_or_none", lambda: "postgresql://example")
    monkeypatch.setattr(control_panel, "ensure_database_schema", lambda database_url: None)
    monkeypatch.setattr(control_panel, "db_prune_liquidation_accounts", lambda database_url, retained_days=365: 0)
    monkeypatch.setattr(control_panel, "db_load_liquidation_accounts", lambda database_url, retained_days=365, scan_start_after=None, scan_end_before=None: ["0x0000000000000000000000000000000000000001", "0x0000000000000000000000000000000000000003"])
    monkeypatch.setattr(control_panel, "db_liquidation_account_registry_stats", lambda database_url, retained_days=365: {"total_count": 2, "active_count": 2, "earliest_scan_start_at": "2026-01-01T00:00:00+00:00", "latest_scan_end_at": "2026-01-02T00:00:00+00:00", "retained_days": retained_days})
    monkeypatch.setattr(control_panel, "aave_rpc_urls", lambda: ["https://rpc.example"])
    monkeypatch.setattr(control_panel, "liquidation_scan_config", lambda: LiquidationScanConfig())
    monkeypatch.setattr(
        control_panel,
        "scan_account_health",
        lambda accounts, pool_address, rpc_url, config: [
            {
                "account": accounts[0],
                "health_factor": 0.98,
                "status": "liquidatable",
                "ltv": 7500,
                "current_liquidation_threshold": 8000,
                "total_debt_base": 1000,
                "liquidation_profit": {"net_profit_base": 23.25},
            },
            {
                "account": accounts[1],
                "health_factor": 2.4,
                "status": "healthy",
                "ltv": 7500,
                "current_liquidation_threshold": 8000,
                "total_debt_base": 500,
                "liquidation_profit": {"net_profit_base": 0},
            },
        ],
    )
    monkeypatch.setenv("AAVE_POOL_ADDRESS", "0x0000000000000000000000000000000000000002")

    payload = liquidation_health_payload(force=True)

    assert payload["summary"]["account_count"] == 2
    assert payload["summary"]["liquidatable_count"] == 1
    assert payload["summary"]["healthy_count"] == 1
    assert payload["summary"]["watch_count"] == 1
    assert payload["summary"]["displayed_count"] == 2
    assert payload["rows"][0]["status"] == "liquidatable"
    assert payload["rows"][1]["status"] == "healthy"
    assert len(payload["watched_rows"]) == 1
    assert "scan_interval_seconds" in payload["summary"]
    assert payload["summary"]["account_source"] == "database"
    assert payload["summary"]["retention_days"] == 365
    assert payload["summary"]["registry_window"]["total_count"] == 2


def test_liquidation_account_registry_prefers_database(monkeypatch):
    from web import control_panel

    control_panel.LIQUIDATION_ACCOUNT_CACHE["updated_at"] = 0.0
    control_panel.LIQUIDATION_ACCOUNT_CACHE["accounts"] = None
    control_panel.LIQUIDATION_ACCOUNT_CACHE["source"] = None
    monkeypatch.setattr(control_panel, "database_url_or_none", lambda: "postgresql://example")
    monkeypatch.setattr(control_panel, "ensure_database_schema", lambda database_url: None)
    monkeypatch.setattr(control_panel, "db_prune_liquidation_accounts", lambda database_url, retained_days=365: 0)
    monkeypatch.setattr(control_panel, "db_load_liquidation_accounts", lambda database_url, retained_days=365, scan_start_after=None, scan_end_before=None: ["0x0000000000000000000000000000000000000001"])
    monkeypatch.setattr(control_panel, "db_liquidation_account_registry_stats", lambda database_url, retained_days=365: {"total_count": 1, "active_count": 1, "earliest_scan_start_at": None, "latest_scan_end_at": None, "retained_days": retained_days})
    monkeypatch.setattr(control_panel, "db_upsert_liquidation_accounts", lambda database_url, accounts, source="manual", active=True: accounts)

    accounts, source = control_panel.load_liquidation_account_registry(force=True)

    assert accounts == ["0x0000000000000000000000000000000000000001"]
    assert source == "database"


def test_liquidation_account_registry_falls_back_to_file(monkeypatch, tmp_path):
    from web import control_panel

    account_path = tmp_path / "liquidation_accounts.txt"
    account_path.write_text(
        "0x0000000000000000000000000000000000000001\n"
        "bad\n"
        "0x0000000000000000000000000000000000000001\n",
        encoding="utf-8",
    )
    control_panel.LIQUIDATION_ACCOUNT_CACHE["updated_at"] = 0.0
    control_panel.LIQUIDATION_ACCOUNT_CACHE["accounts"] = None
    control_panel.LIQUIDATION_ACCOUNT_CACHE["source"] = None
    monkeypatch.setattr(control_panel, "LIQUIDATION_ACCOUNTS_PATH", account_path)
    monkeypatch.setattr(control_panel, "database_url_or_none", lambda: "postgresql://example")
    monkeypatch.setattr(control_panel, "ensure_database_schema", lambda database_url: None)
    monkeypatch.setattr(control_panel, "db_prune_liquidation_accounts", lambda database_url, retained_days=365: 0)
    monkeypatch.setattr(control_panel, "db_load_liquidation_accounts", lambda database_url, retained_days=365, scan_start_after=None, scan_end_before=None: [])

    accounts, source = control_panel.load_liquidation_account_registry(force=True)

    assert accounts == ["0x0000000000000000000000000000000000000001"]
    assert source == "file-fallback"


def test_liquidation_discovery_window_scans_from_one_year_start(monkeypatch):
    from web import control_panel

    LIQUIDATION_DISCOVERY_CACHE["historical_cursor_at"] = None
    monkeypatch.setenv("LIQUIDATION_ACCOUNT_SCAN_START_DAYS", "365")
    monkeypatch.setenv("LIQUIDATION_BACKFILL_WINDOW_DAYS", "7")
    monkeypatch.setenv("LIQUIDATION_BLOCK_SECONDS", "2")
    monkeypatch.setattr(
        control_panel,
        "liquidation_account_registry_window",
        lambda: {"total_count": 0, "active_count": 0, "earliest_scan_start_at": None, "latest_scan_end_at": None, "retained_days": 365},
    )
    monkeypatch.setattr(
        control_panel,
        "liquidation_discovery_progress",
        lambda pool_address: {"latest_recent_scan_end_at": None, "earliest_backfill_scan_start_at": None, "success_count": 0, "error_count": 0, "scanned_block_count": 0},
    )

    scan_start_at, scan_end_at, from_block, to_block, lookback_blocks, _, mode = liquidation_discovery_window(force_full=False)

    assert mode == "recent"
    assert to_block is None
    assert from_block < 0
    assert 364.9 <= (datetime.now(scan_start_at.tzinfo) - scan_start_at).total_seconds() / 86400 <= 365.1
    assert 15_700_000 <= lookback_blocks <= 15_800_000


def test_liquidation_discovery_window_resumes_recent_from_block_cursor(monkeypatch):
    from web import control_panel

    monkeypatch.setenv("LIQUIDATION_DISCOVERY_BLOCK_OVERLAP", "1")
    monkeypatch.setattr(
        control_panel,
        "liquidation_account_registry_window",
        lambda: {"total_count": 1, "active_count": 1, "earliest_scan_start_at": None, "latest_scan_end_at": None, "retained_days": 365},
    )
    monkeypatch.setattr(
        control_panel,
        "liquidation_discovery_progress",
        lambda pool_address: {
            "latest_recent_scan_end_at": "2026-07-30T00:00:00+00:00",
            "earliest_backfill_scan_start_at": None,
            "latest_recent_to_block": 100,
            "earliest_backfill_from_block": None,
            "success_count": 1,
            "error_count": 0,
            "scanned_block_count": 100,
        },
    )

    _, _, from_block, to_block, _, registry, mode = liquidation_discovery_window(force_full=False)

    assert mode == "recent"
    assert from_block == 100
    assert to_block is None
    assert registry["discovery_cursor"]["source"] == "block-ledger"
    assert registry["discovery_cursor"]["previous_latest_to_block"] == 100


def test_liquidation_discovery_window_force_full_restarts_from_one_year(monkeypatch):
    from web import control_panel

    LIQUIDATION_DISCOVERY_CACHE["historical_cursor_at"] = None
    monkeypatch.setenv("LIQUIDATION_ACCOUNT_SCAN_START_DAYS", "365")
    monkeypatch.setenv("LIQUIDATION_BACKFILL_WINDOW_DAYS", "7")
    monkeypatch.setenv("LIQUIDATION_BLOCK_SECONDS", "2")
    monkeypatch.setattr(
        control_panel,
        "liquidation_account_registry_window",
        lambda: {"total_count": 0, "active_count": 0, "earliest_scan_start_at": None, "latest_scan_end_at": None, "retained_days": 365},
    )
    monkeypatch.setattr(
        control_panel,
        "liquidation_discovery_progress",
        lambda pool_address: {"latest_recent_scan_end_at": None, "earliest_backfill_scan_start_at": None, "success_count": 0, "error_count": 0, "scanned_block_count": 0},
    )

    scan_start_at, scan_end_at, from_block, to_block, lookback_blocks, _, mode = liquidation_discovery_window(force_full=True)

    assert mode == "recent"
    assert to_block is None
    assert from_block < 0
    assert 364.9 <= (datetime.now(scan_start_at.tzinfo) - scan_start_at).total_seconds() / 86400 <= 365.1
    assert 15_700_000 <= lookback_blocks <= 15_800_000


def test_liquidation_discovery_window_force_full_ignores_old_backfill_cursor(monkeypatch):
    from web import control_panel

    LIQUIDATION_DISCOVERY_CACHE["historical_cursor_at"] = None
    monkeypatch.setenv("LIQUIDATION_BACKFILL_WINDOW_DAYS", "7")
    monkeypatch.setenv("LIQUIDATION_BLOCK_SECONDS", "2")
    monkeypatch.setenv("LIQUIDATION_DISCOVERY_BLOCK_OVERLAP", "1")
    monkeypatch.setattr(
        control_panel,
        "liquidation_account_registry_window",
        lambda: {"total_count": 1, "active_count": 1, "earliest_scan_start_at": None, "latest_scan_end_at": None, "retained_days": 365},
    )
    monkeypatch.setattr(
        control_panel,
        "liquidation_discovery_progress",
        lambda pool_address: {
            "latest_recent_scan_end_at": None,
            "earliest_backfill_scan_start_at": "2026-07-20T00:00:00+00:00",
            "latest_recent_to_block": None,
            "earliest_backfill_from_block": 1000,
            "success_count": 1,
            "error_count": 0,
            "scanned_block_count": 100,
        },
    )

    scan_start_at, scan_end_at, from_block, to_block, lookback_blocks, registry, mode = liquidation_discovery_window(force_full=True)

    assert mode == "recent"
    assert to_block is None
    assert from_block < 0
    assert 15_700_000 <= lookback_blocks <= 15_800_000
    assert registry["discovery_cursor"]["source"] == "full-year-bootstrap"


def test_discovery_window_continuity_allows_connected_or_overlapped_ranges():
    assert discovery_window_continuity_error(
        "recent",
        101,
        200,
        {"latest_recent_to_block": 100},
    ) is None
    assert discovery_window_continuity_error(
        "recent",
        95,
        200,
        {"latest_recent_to_block": 100},
    ) is None
    assert discovery_window_continuity_error(
        "historical-backfill",
        1,
        99,
        {"earliest_backfill_from_block": 100},
    ) is None
    assert discovery_window_continuity_error(
        "historical-backfill",
        1,
        105,
        {"earliest_backfill_from_block": 100},
    ) is None


def test_discovery_window_continuity_rejects_gaps():
    assert discovery_window_continuity_error(
        "recent",
        102,
        200,
        {"latest_recent_to_block": 100},
    ) == "recent scan gap: previous to_block 100, next from_block 102"
    assert discovery_window_continuity_error(
        "historical-backfill",
        1,
        98,
        {"earliest_backfill_from_block": 100},
    ) == "historical backfill gap: next to_block 98, previous from_block 100"


def test_discovery_writes_accounts_before_rejecting_progress_gap(monkeypatch):
    from web import control_panel

    captured = {"accounts": None, "progress_records": []}
    account = "0x0000000000000000000000000000000000000001"
    control_panel.LIQUIDATION_DISCOVERY_CACHE["last_result"] = None
    monkeypatch.setattr(control_panel, "database_url_or_none", lambda: "postgresql://example")
    monkeypatch.setenv("AAVE_POOL_ADDRESS", "0x0000000000000000000000000000000000000002")
    monkeypatch.setattr(control_panel, "aave_rpc_urls", lambda: ["https://rpc.example"])
    monkeypatch.setattr(control_panel, "liquidation_scan_config", lambda: LiquidationScanConfig())
    monkeypatch.setattr(
        control_panel,
        "liquidation_discovery_window",
        lambda force_full=False: (
            __import__("datetime").datetime(2026, 1, 1, tzinfo=__import__("datetime").timezone.utc),
            __import__("datetime").datetime(2026, 1, 2, tzinfo=__import__("datetime").timezone.utc),
            -100,
            None,
            100,
            {"discovery_scan_progress": {"latest_recent_to_block": 100}},
            "recent",
        ),
    )
    monkeypatch.setattr(control_panel, "resolve_discovery_block_range", lambda rpc_url, from_block, to_block: (200, 102, 200))
    monkeypatch.setattr(control_panel, "discover_borrower_addresses", lambda *args, **kwargs: [account])
    monkeypatch.setattr(
        control_panel,
        "sync_liquidation_accounts_to_database",
        lambda accounts, source="manual", scan_start_at=None, scan_end_at=None, update_existing=True: captured.update({"accounts": accounts, "update_existing": update_existing}),
    )
    monkeypatch.setattr(
        control_panel,
        "record_liquidation_discovery_window",
        lambda **kwargs: captured["progress_records"].append(kwargs),
    )

    result = control_panel.discover_and_sync_liquidation_accounts(force_full=False)

    assert captured["accounts"] == [account]
    assert captured["update_existing"] is True
    assert captured["progress_records"] == []
    assert result["skipped"] is True
    assert "gap" in result["reason"]


def test_liquidation_account_payload_normalizes_address(monkeypatch):
    from web import control_panel

    captured = {}
    account = "0xa845Cbe370B99AdDaB67AfE442F2cF5784d4dC29"
    checksum = Web3.to_checksum_address(account)

    monkeypatch.setattr(control_panel, "scan_context_assets", lambda: ("https://rpc.example", [{"token_address": "0x0000000000000000000000000000000000000001"}], None))
    monkeypatch.setattr(control_panel, "protocol_data_provider_address", lambda: "0x0000000000000000000000000000000000000002")
    monkeypatch.setattr(control_panel, "liquidation_data_provider_address", lambda: "0x0000000000000000000000000000000000000003")
    monkeypatch.setattr(control_panel, "liquidation_scan_config", lambda: LiquidationScanConfig())

    def fake_report(user, rpc_url, pool_address, reserve_assets, protocol_addr, liquidation_addr, config):
        captured["user"] = user
        captured["rpc_url"] = rpc_url
        return {
            "account": user,
            "summary": {"health_factor": 1.058, "health_factor_band": "yellow"},
            "positions": [],
            "liquidation_candidates": [],
            "recommended_candidate": None,
        }

    monkeypatch.setattr(control_panel, "build_user_liquidation_report", fake_report)
    monkeypatch.setenv("AAVE_POOL_ADDRESS", "0x0000000000000000000000000000000000000004")

    payload = liquidation_account_payload(account)

    assert captured["user"] == checksum
    assert payload["account"] == checksum
    assert payload["context"]["rpc_url"] == "https://rpc.example"


def test_liquidation_account_api_returns_payload(monkeypatch):
    from web import control_panel

    monkeypatch.setattr(
        control_panel,
        "liquidation_account_payload",
        lambda account: {"account": account, "summary": {"health_factor": 1.0}, "positions": [], "liquidation_candidates": []},
    )

    client = app.test_client()
    response = client.get("/api/liquidation/account?account=0x0000000000000000000000000000000000000001")

    assert response.status_code == 200
    assert response.get_json()["account"] == "0x0000000000000000000000000000000000000001"


def test_liquidation_borrow_pool_api_reads_persisted_top_accounts(monkeypatch):
    from web import control_panel

    monkeypatch.setattr(
        control_panel,
        "liquidation_borrow_pool_payload",
        lambda: {
            "rows": [{"account": "0x1", "health_factor": 0.98}],
            "summary": {"count": 1, "display_limit": 100, "watch_health_factor": 1.5},
        },
    )

    response = app.test_client().get("/api/liquidation/borrow-pool")
    data = response.get_json()

    assert response.status_code == 200
    assert data["rows"][0]["account"] == "0x1"
    assert data["summary"]["display_limit"] == 100


def test_liquidation_borrow_pool_scan_api_triggers_dynamic_scan(monkeypatch):
    from web import control_panel

    captured = {}

    def fake_scan(force=True):
        captured["force"] = force
        return {"rows": [], "summary": {"scanned": True, "display_limit": 100}}

    monkeypatch.setattr(control_panel, "liquidation_borrow_pool_scan_payload", fake_scan)

    response = app.test_client().post("/api/liquidation/borrow-pool/scan", json={})

    assert response.status_code == 200
    assert response.get_json()["summary"]["scanned"] is True
    assert captured["force"] is True


def test_liquidation_borrow_pool_latest_batch_api(monkeypatch):
    from web import control_panel

    monkeypatch.setattr(control_panel, "database_url_or_none", lambda: "postgresql://example")
    monkeypatch.setattr(control_panel, "ensure_database_schema", lambda database_url: None)
    monkeypatch.setattr(
        control_panel,
        "db_load_liquidation_borrow_health_scan_batches",
        lambda database_url, limit=1: [{"id": 9, "status": "success", "scanned_count": 2}],
    )

    response = app.test_client().get("/api/liquidation/borrow-pool/latest-batch")
    data = response.get_json()

    assert response.status_code == 200
    assert data["batch"]["id"] == 9
    assert data["batch"]["scanned_count"] == 2


def test_liquidation_core_opportunities_api_returns_priority_rows(monkeypatch):
    from web import control_panel

    monkeypatch.setattr(control_panel, "database_url_or_none", lambda: "postgresql://example")
    monkeypatch.setattr(control_panel, "ensure_database_schema", lambda database_url: None)
    monkeypatch.setattr(
        control_panel,
        "db_load_liquidation_core_opportunity_pool",
        lambda database_url, limit=100: [{"account": "0x1", "priority_score": 500.0}],
    )

    response = app.test_client().get("/api/liquidation/core-opportunities")
    data = response.get_json()

    assert response.status_code == 200
    assert data["count"] == 1
    assert data["rows"][0]["priority_score"] == 500.0


def test_liquidation_account_attempts_and_samples_api(monkeypatch):
    from web import control_panel

    monkeypatch.setattr(
        control_panel,
        "liquidation_execution_attempts_for_account",
        lambda account, limit=20: {"configured": True, "account": account, "attempts": [{"id": 1, "mode": "static_call"}]},
    )
    monkeypatch.setattr(
        control_panel,
        "liquidation_failure_samples_for_account",
        lambda account, limit=20: {"configured": True, "account": account, "samples": [{"id": 2, "failure_type": "static_call_failed"}]},
    )

    client = app.test_client()
    attempts = client.get("/api/liquidation/account/0x0000000000000000000000000000000000000001/attempts?limit=5").get_json()
    samples = client.get("/api/liquidation/account/0x0000000000000000000000000000000000000001/samples?limit=5").get_json()

    assert attempts["attempts"][0]["mode"] == "static_call"
    assert samples["samples"][0]["failure_type"] == "static_call_failed"


def test_liquidation_control_summary_api(monkeypatch):
    from web import control_panel

    monkeypatch.setattr(control_panel, "database_url_or_none", lambda: "postgresql://example")
    monkeypatch.setattr(control_panel, "ensure_database_schema", lambda database_url: None)
    monkeypatch.setattr(control_panel, "schema_status_payload", lambda: {"configured": True, "up_to_date": True, "missing_migrations": []})
    monkeypatch.setattr(control_panel, "liquidation_pause_guard_status", lambda: {"configured": True, "paused": False})
    monkeypatch.setattr(control_panel, "recent_liquidation_execution_attempts", lambda limit=5: {"stats": {"total": 2, "blocked": 1, "confirmed_success": 1}})
    monkeypatch.setattr(control_panel, "recent_liquidation_failure_samples", lambda limit=5: {"samples": [{"id": 1}]})
    monkeypatch.setattr(control_panel, "db_load_liquidation_borrow_health_scan_batches", lambda database_url, limit=1: [{"id": 5, "status": "success", "scanned_count": 7, "account_count": 10}])
    monkeypatch.setattr(control_panel, "db_load_liquidation_core_opportunity_pool", lambda database_url, limit=5: [{"account": "0xcore"}])
    monkeypatch.setattr(control_panel, "db_load_liquidation_high_frequency_pool", lambda database_url, limit=5: [{"account": "0xhigh"}])
    monkeypatch.setattr(control_panel, "liquidation_account_tier_summary", lambda: {"hot_count": 1, "warm_count": 2, "cold_count": 3})
    monkeypatch.setattr(control_panel, "control_status_payload", lambda: {"state": "running"})

    response = app.test_client().get("/api/liquidation/control-summary")
    data = response.get_json()

    assert response.status_code == 200
    assert data["schema"]["up_to_date"] is True
    assert data["latest_batch"]["id"] == 5
    assert data["core_opportunities"][0]["account"] == "0xcore"
    assert data["account_tiers"]["hot_count"] == 1


def test_liquidation_daily_report_api_rebuilds_and_reads_file(monkeypatch, tmp_path):
    from web import control_panel_data_routes

    report_path = tmp_path / "daily.json"
    monkeypatch.setattr(control_panel_data_routes, "liquidation_observation_report_path", lambda: report_path)
    monkeypatch.setattr(control_panel_data_routes, "database_url_or_none", lambda: "postgresql://example")
    monkeypatch.setattr(control_panel_data_routes, "build_liquidation_observation_report", lambda database_url: {"generated_at": "2026-07-31T00:00:00+00:00", "borrow_pool": {"count": 1}, "core_opportunities": {"count": 2}, "execution": {"stats": {"total": 3}}})
    monkeypatch.setattr(control_panel_data_routes, "write_liquidation_observation_report", lambda report, path: path.write_text(__import__("json").dumps(report), encoding="utf-8") or path)

    response = app.test_client().post("/api/liquidation/reports/daily")
    data = response.get_json()

    assert response.status_code == 200
    assert data["report"]["core_opportunities"]["count"] == 2
    assert report_path.exists()


def test_liquidation_samples_api_returns_manifest(monkeypatch, tmp_path):
    from web import control_panel

    sample_path = tmp_path / "index.json"
    sample_path.write_text(
        '{"schema_version":1,"generated_at":"2026-07-30T10:00:00+00:00","source_count":2,"samples":[{"label":"healthy","status":"ready","file":"healthy.json"}]}',
        encoding="utf-8",
    )
    monkeypatch.setattr(control_panel, "LIQUIDATION_SAMPLE_LIBRARY_PATH", sample_path)

    client = app.test_client()
    response = client.get("/api/liquidation/samples")
    data = response.get_json()

    assert response.status_code == 200
    assert data["samples"][0]["label"] == "healthy"


def test_liquidation_account_payload_api_requires_executor(monkeypatch):
    from web import control_panel

    monkeypatch.setattr(control_panel, "liquidation_executor_address", lambda: "")

    client = app.test_client()
    response = client.get("/api/liquidation/account/payload?account=0x0000000000000000000000000000000000000001")

    assert response.status_code == 400
    assert "LIQUIDATION_EXECUTOR_ADDRESS" in response.get_json()["error"]


def test_liquidation_accounts_api_persists_to_database(monkeypatch):
    from web import control_panel

    captured = {}
    monkeypatch.setattr(control_panel, "database_url_or_none", lambda: "postgresql://example")
    monkeypatch.setattr(control_panel, "ensure_database_schema", lambda database_url: None)
    monkeypatch.setattr(control_panel, "db_prune_liquidation_accounts", lambda database_url, retained_days=365: 0)
    monkeypatch.setattr(
        control_panel,
        "db_upsert_liquidation_accounts",
        lambda database_url, accounts, source="manual", active=True: captured.update({"database_url": database_url, "accounts": accounts, "source": source, "active": active}),
    )

    client = app.test_client()
    response = client.post(
        "/api/liquidation/accounts",
        json={"accounts": ["0x0000000000000000000000000000000000000001", "bad", "0x0000000000000000000000000000000000000001"]},
    )

    assert response.status_code == 200
    assert response.get_json()["source"] == "database"
    assert captured["accounts"] == ["0x0000000000000000000000000000000000000001"]


def test_clear_database_api_rejects_when_background_scan_is_running(monkeypatch):
    from web import control_panel

    monkeypatch.setitem(control_panel.LIQUIDATION_DISCOVERY_CACHE, "running", True)
    monkeypatch.setitem(control_panel.LIQUIDATION_SCAN_CACHE, "running", False)
    monkeypatch.setattr(control_panel, "database_url_or_none", lambda: "postgresql://example")
    monkeypatch.setattr(control_panel, "configured_database_url", lambda: "postgresql://example")
    monkeypatch.setattr(control_panel, "set_control_status", lambda *args, **kwargs: None)
    monkeypatch.setattr(control_panel, "is_observer_running", lambda: False)
    monkeypatch.setattr(control_panel, "observer_starting", False)

    assert control_panel.LIQUIDATION_DISCOVERY_LOCK.acquire(blocking=False)
    try:
        response = app.test_client().post("/api/clear", json={})
        data = response.get_json()
    finally:
        control_panel.LIQUIDATION_DISCOVERY_LOCK.release()

    assert response.status_code == 400
    assert data["blockers"]
    assert "后台任务结束" in data["error"]


def test_status_reports_liquidation_background_scan(monkeypatch):
    from web import control_panel

    monkeypatch.setitem(control_panel.LIQUIDATION_DISCOVERY_CACHE, "running", False)
    monkeypatch.setitem(control_panel.LIQUIDATION_SCAN_CACHE, "running", True)
    monkeypatch.setitem(control_panel.LIQUIDATION_SCAN_CACHE, "stage", "debt_pool")
    monkeypatch.setitem(control_panel.LIQUIDATION_SCAN_CACHE, "started_at", "2020-01-01T00:00:00+00:00")
    monkeypatch.setitem(control_panel.LIQUIDATION_SCAN_CACHE, "progress", {"account_count": 10, "scanned_count": 3})
    monkeypatch.setattr(control_panel, "quick_observer_running", lambda: False)
    monkeypatch.setattr(control_panel, "quick_observer_pid", lambda: None)
    monkeypatch.setattr(control_panel, "observer_starting", False)
    monkeypatch.setattr(control_panel, "safe_latest", lambda source: None)
    monkeypatch.setattr(control_panel, "strategy_config", lambda: {})
    monkeypatch.setattr(control_panel, "displayed_symbols", lambda running: [])
    monkeypatch.setattr(control_panel, "restrict_extremes_to_symbols", lambda data, symbols: data)
    monkeypatch.setattr(control_panel, "opportunity_health_rows", lambda data, config: [])
    monkeypatch.setattr(control_panel, "opportunity_health_summary", lambda rows, config: {})
    monkeypatch.setattr(control_panel, "unified_sampling_profile", lambda config: {})

    assert control_panel.LIQUIDATION_SCAN_LOCK.acquire(blocking=False)
    try:
        response = app.test_client().get("/api/status")
        data = response.get_json()
    finally:
        control_panel.LIQUIDATION_SCAN_LOCK.release()

    assert response.status_code == 200
    assert data["running"] is False
    assert data["background_activity"]["liquidation_health_scan"]["running"] is True
    assert data["background_activity"]["liquidation_health_scan"]["stage_label"] == "扫描债务池"
    assert data["background_activity"]["liquidation_health_scan"]["percent"] == 30
    assert data["system_monitor"]["state"] == "initializing"
    assert "后台清算扫描中" in data["system_monitor"]["action"]
    assert "账户 3/10" in data["system_monitor"]["action"]
    assert data["system_monitor"]["background_stage"] == "扫描债务池"
    assert data["system_monitor"]["background_detail"] == "账户 3/10"
    assert data["system_monitor"]["percent"] == 30


def test_clear_database_api_truncates_without_schema_init(monkeypatch):
    from web import control_panel

    monkeypatch.setitem(control_panel.LIQUIDATION_DISCOVERY_CACHE, "running", False)
    monkeypatch.setitem(control_panel.LIQUIDATION_SCAN_CACHE, "running", False)
    monkeypatch.setattr(control_panel, "database_url_or_none", lambda: "postgresql://example")
    monkeypatch.setattr(control_panel, "configured_database_url", lambda: "postgresql://example")
    monkeypatch.setattr(control_panel, "set_control_status", lambda *args, **kwargs: None)
    monkeypatch.setattr(control_panel, "is_observer_running", lambda: False)
    monkeypatch.setattr(control_panel, "observer_starting", False)

    executed = []

    class FakeCursor:
        def execute(self, sql):
            executed.append(sql)

    class FakeConnection:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def cursor(self):
            class CursorContext:
                def __enter__(self_inner):
                    return FakeCursor()

                def __exit__(self_inner, exc_type, exc, tb):
                    return False

            return CursorContext()

    class FakePsycopg:
        def connect(self, database_url, connect_timeout=8):
            assert database_url == "postgresql://example"
            return FakeConnection()

    monkeypatch.setattr(control_panel, "require_psycopg", lambda: FakePsycopg())

    response = app.test_client().post("/api/clear", json={})
    data = response.get_json()

    assert response.status_code == 200
    assert data["cleared"] is True
    assert any("TRUNCATE TABLE observations" in sql for sql in executed)
    assert all("CREATE TABLE" not in sql for sql in executed)


def test_clear_database_api_resets_stale_scan_running_flag(monkeypatch):
    from web import control_panel

    monkeypatch.setitem(control_panel.LIQUIDATION_DISCOVERY_CACHE, "running", True)
    monkeypatch.setitem(control_panel.LIQUIDATION_DISCOVERY_CACHE, "stage", "borrowers")
    monkeypatch.setitem(control_panel.LIQUIDATION_SCAN_CACHE, "running", False)
    monkeypatch.setattr(control_panel, "database_url_or_none", lambda: "postgresql://example")
    monkeypatch.setattr(control_panel, "configured_database_url", lambda: "postgresql://example")
    monkeypatch.setattr(control_panel, "set_control_status", lambda *args, **kwargs: None)
    monkeypatch.setattr(control_panel, "is_observer_running", lambda: False)
    monkeypatch.setattr(control_panel, "observer_starting", False)

    executed = []

    class FakeCursor:
        def execute(self, sql):
            executed.append(sql)

    class FakeConnection:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def cursor(self):
            class CursorContext:
                def __enter__(self_inner):
                    return FakeCursor()

                def __exit__(self_inner, exc_type, exc, tb):
                    return False

            return CursorContext()

    class FakePsycopg:
        def connect(self, database_url, connect_timeout=8):
            assert database_url == "postgresql://example"
            return FakeConnection()

    monkeypatch.setattr(control_panel, "require_psycopg", lambda: FakePsycopg())

    response = app.test_client().post("/api/clear", json={})

    assert response.status_code == 200
    assert control_panel.LIQUIDATION_DISCOVERY_CACHE["running"] is False
    assert control_panel.LIQUIDATION_DISCOVERY_CACHE["stage"] == "idle"
    assert any("TRUNCATE TABLE observations" in sql for sql in executed)


def test_liquidation_discovery_coverage_api_reports_gap(monkeypatch):
    from web import control_panel

    monkeypatch.setattr(
        control_panel,
        "liquidation_discovery_progress",
        lambda pool_address: {
            "latest_recent_to_block": 100,
            "earliest_backfill_from_block": 110,
            "success_count": 2,
            "error_count": 0,
            "scanned_block_count": 20,
        },
    )

    response = app.test_client().get(
        "/api/liquidation/discovery-coverage?pool=0x0000000000000000000000000000000000000002"
    )
    data = response.get_json()

    assert response.status_code == 200
    assert data["has_gap"] is True
    assert data["latest_gap_from_block"] == 101
    assert data["latest_gap_to_block"] == 109


def test_liquidation_execution_attempts_api_returns_recent_attempts(monkeypatch):
    from web import control_panel

    monkeypatch.setattr(
        control_panel,
        "recent_liquidation_execution_attempts",
        lambda limit=20: {
            "configured": True,
            "attempts": [{"id": 7, "state": "submission_blocked"}],
            "stats": {"total": 1, "blocked": 1},
        },
    )

    response = app.test_client().get("/api/liquidation/execution-attempts?limit=5")
    data = response.get_json()

    assert response.status_code == 200
    assert data["attempts"][0]["id"] == 7
    assert data["stats"]["blocked"] == 1


def test_liquidation_failure_samples_api_returns_recent_samples(monkeypatch):
    from web import control_panel

    monkeypatch.setattr(
        control_panel,
        "recent_liquidation_failure_samples",
        lambda limit=20: {
            "configured": True,
            "samples": [{"id": 3, "failure_type": "quote_expired"}],
        },
    )

    response = app.test_client().get("/api/liquidation/failure-samples?limit=5")
    data = response.get_json()

    assert response.status_code == 200
    assert data["samples"][0]["failure_type"] == "quote_expired"


def test_liquidation_pause_guard_apis_return_and_clear_state(monkeypatch):
    from web import control_panel

    monkeypatch.setattr(
        control_panel,
        "liquidation_pause_guard_status",
        lambda: {"configured": True, "paused": True, "pause_reason": "static_call_failed"},
    )
    monkeypatch.setattr(
        control_panel,
        "clear_liquidation_pause_guard_status",
        lambda: {"configured": True, "paused": False, "pause_reason": None},
    )

    client = app.test_client()
    status = client.get("/api/liquidation/pause-guard").get_json()
    cleared = client.post("/api/liquidation/pause-guard/clear").get_json()

    assert status["paused"] is True
    assert status["pause_reason"] == "static_call_failed"
    assert cleared["paused"] is False
