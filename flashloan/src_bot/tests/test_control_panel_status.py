from web.control_panel import (
    app,
    LIQUIDATION_SCAN_CACHE,
    liquidation_account_payload,
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
    monkeypatch.setattr(control_panel, "db_load_liquidation_accounts", lambda database_url, retained_days=365, scan_start_after=None, scan_end_before=None: ["0x0000000000000000000000000000000000000001"])
    monkeypatch.setattr(control_panel, "db_liquidation_account_registry_stats", lambda database_url, retained_days=365: {"total_count": 1, "active_count": 1, "earliest_scan_start_at": "2026-01-01T00:00:00+00:00", "latest_scan_end_at": "2026-01-02T00:00:00+00:00", "retained_days": retained_days})
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
            }
        ],
    )
    monkeypatch.setenv("AAVE_POOL_ADDRESS", "0x0000000000000000000000000000000000000002")

    payload = liquidation_health_payload(force=True)

    assert payload["summary"]["account_count"] == 1
    assert payload["summary"]["liquidatable_count"] == 1
    assert payload["rows"][0]["status"] == "liquidatable"
    assert "scan_interval_seconds" in payload["summary"]
    assert payload["summary"]["account_source"] == "database"
    assert payload["summary"]["retention_days"] == 365
    assert payload["summary"]["registry_window"]["total_count"] == 1


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
