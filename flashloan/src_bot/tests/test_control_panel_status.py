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


def test_liquidation_discovery_window_scans_recent_week_first(monkeypatch):
    from web import control_panel

    LIQUIDATION_DISCOVERY_CACHE["historical_cursor_at"] = None
    monkeypatch.setenv("LIQUIDATION_RECENT_DISCOVERY_DAYS", "7")
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
    assert 6.9 <= (scan_end_at - scan_start_at).total_seconds() / 86400 <= 7.1
    assert 300_000 <= lookback_blocks <= 305_000


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


def test_liquidation_discovery_window_backfills_previous_week(monkeypatch):
    from web import control_panel

    LIQUIDATION_DISCOVERY_CACHE["historical_cursor_at"] = None
    monkeypatch.setenv("LIQUIDATION_RECENT_DISCOVERY_DAYS", "7")
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

    assert mode == "historical-backfill"
    assert to_block is not None and to_block < 0
    assert from_block < to_block
    assert 6.9 <= (scan_end_at - scan_start_at).total_seconds() / 86400 <= 7.1
    assert 300_000 <= lookback_blocks <= 305_000


def test_liquidation_discovery_window_resumes_backfill_from_block_cursor(monkeypatch):
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

    _, _, from_block, to_block, _, registry, mode = liquidation_discovery_window(force_full=True)

    assert mode == "historical-backfill"
    assert to_block == 1000
    assert from_block == 0
    assert registry["discovery_cursor"]["source"] == "block-ledger"
    assert registry["discovery_cursor"]["previous_earliest_from_block"] == 1000


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
    assert captured["update_existing"] is False
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
