from pathlib import Path
from urllib.parse import quote

from web.control_panel import app
from web.data_routes.dex_surfaces import _overview_response_payload


TEMPLATE_DIR = Path(__file__).resolve().parents[2] / "web" / "templates"
STATIC_DIR = Path(__file__).resolve().parents[2] / "web" / "static"


EXPECTED_LIQUIDATION_CHAIN_ORDER = [
    "AVALANCHE Chain",
    "BNB Chain",
    "POLYGON Chain",
    "ETHEREUM Chain",
    "Robinhood Chain",
    "Robinhood Testnet",
    "CoW Rail",
]
EXPECTED_DEX_CHAIN_ORDER = [
    "AVALANCHE Chain",
    "BNB Chain",
    "POLYGON Chain",
    "ETHEREUM Chain",
    "Robinhood Chain",
    "Robinhood Testnet",
    "HYPERLIQUID Chain",
    "CoW Rail",
]
CANONICAL_BNB_AAVE = "liquidation-56-aave-v3"
CANONICAL_BNB_VENUS = "liquidation-56-venus-core"
CANONICAL_BNB_DEX = "dex-arbitrage-56-pancakeswap-v3"
CANONICAL_AVALANCHE_AAVE = "liquidation-43114-aave-v3"
CANONICAL_ROBINHOOD_BOT = "liquidation-4663-aave-v3"
CANONICAL_ROBINHOOD_MORPHO = "liquidation-4663-morpho-blue"
CANONICAL_ROBINHOOD_TESTNET_BOT = "liquidation-46630-morpho-blue"
CANONICAL_ROBINHOOD_DEX = "dex-arbitrage-4663-uniswap-v3"
CANONICAL_ROBINHOOD_DEX_V2 = "dex-arbitrage-4663-uniswap-v2"
CANONICAL_ROBINHOOD_DEX_V4 = "dex-arbitrage-4663-uniswap-v4"
CANONICAL_ROBINHOOD_DEX_MORPHO = "dex-arbitrage-4663-morpho-blue"
CANONICAL_ROBINHOOD_TESTNET_DEX = "dex-arbitrage-46630-uniswap-v4"
CANONICAL_HYPERLIQUID_DEX = "dex-arbitrage-hyperliquid-hyperliquid-native"


def test_dex_pool_payload_marks_runtime_cache_source(monkeypatch):
    from market.binance_market import pool_candidates

    monkeypatch.setattr(
        pool_candidates,
        "_read_json_object",
        lambda _path: {
            "network": "bnb",
            "generatedAt": "2026-08-19T00:00:00Z",
            "pools": [],
            "summary": {},
        },
    )
    pool_candidates._dex_pool_candidates_payload_cached.cache_clear()

    payload = pool_candidates.dex_pool_candidates_payload()

    assert payload["source"] == "runtime_cache"
    assert payload["freshness"] == "2026-08-19T00:00:00Z"
    assert payload["fallback_reason"] is None
    pool_candidates._dex_pool_candidates_payload_cached.cache_clear()


def test_system_info_endpoint_owns_scan_version(monkeypatch):
    from web import control_panel

    monkeypatch.setattr(control_panel, "liquidation_scan_version", lambda: "test-version")
    monkeypatch.setattr(
        control_panel,
        "liquidation_scan_refresh_profile",
        lambda: {
            "core_opportunity_refresh_seconds": 1.0,
            "high_frequency_refresh_seconds": 300.0,
            "borrow_health_refresh_seconds": 1800.0,
        },
    )

    data = app.test_client().get("/api/system-info").get_json()

    assert data["version"] == "test-version"
    assert data["build"]["app"] == "flashloan-app-runtime"
    assert "git_commit_short" in data["build"]
    assert data["scan_policy"]["core_opportunity_refresh_seconds"] == 1.0
    assert data["scan_policy"]["high_frequency_refresh_seconds"] == 300.0
    assert data["scan_policy"]["borrow_health_refresh_seconds"] == 1800.0


def test_status_endpoint_includes_system_info(monkeypatch):
    from web import control_panel

    monkeypatch.setattr(control_panel, "quick_observer_running", lambda: False)
    monkeypatch.setattr(control_panel, "quick_observer_pid", lambda: None)
    monkeypatch.setattr(control_panel, "observer_starting", False)
    monkeypatch.setattr(control_panel, "observer_start_error", None)
    monkeypatch.setattr(control_panel, "latest_binance_extremes_file", lambda: {})
    monkeypatch.setattr(control_panel, "safe_latest", lambda loader: loader() if callable(loader) else {})
    monkeypatch.setattr(control_panel, "control_status_payload", lambda: {})
    monkeypatch.setattr(control_panel, "strategy_config", lambda: {})
    monkeypatch.setattr(control_panel, "displayed_symbols", lambda running: [])
    monkeypatch.setattr(control_panel, "restrict_extremes_to_symbols", lambda rows, symbols: rows)
    monkeypatch.setattr(control_panel, "opportunity_health_rows", lambda rows, config: [])
    monkeypatch.setattr(control_panel, "background_activity_payload", lambda running, starting: {})
    monkeypatch.setattr(control_panel, "observer_progress_payload", lambda *args: {})
    monkeypatch.setattr(control_panel, "system_monitor_payload", lambda *args: {})
    monkeypatch.setattr(control_panel, "opportunity_health_summary", lambda rows, config: {})
    monkeypatch.setattr(control_panel, "unified_sampling_profile", lambda config: {})
    monkeypatch.setattr(control_panel, "liquidation_scan_version", lambda: "status-version")
    monkeypatch.setattr(
        control_panel,
        "liquidation_scan_refresh_profile",
        lambda: {
            "core_opportunity_refresh_seconds": 1.0,
            "high_frequency_refresh_seconds": 300.0,
            "borrow_health_refresh_seconds": 1800.0,
        },
    )

    data = app.test_client().get("/api/status").get_json()

    assert data["system_info"]["version"] == "status-version"
    assert data["system_info"]["build"]["app"] == "flashloan-app-runtime"
    assert data["system_info"]["scan_policy"]["strategy"] == "core_every_base_cycle_high_frequency_after_5m_borrow_health_after_30m"
    assert "scan_version" not in data


def test_borrow_pool_endpoint_does_not_include_system_version(monkeypatch):
    from web import control_panel

    monkeypatch.setattr(control_panel, "database_url_or_none", lambda: None)

    data = app.test_client().get("/api/liquidation/borrow-pool").get_json()

    encoded = str(data)
    assert "scan_version" not in encoded
    assert "scan_runtime" not in encoded
    assert "scan_cycle_strategy" not in encoded
    assert "system_info" not in encoded
    assert "version" not in data
    assert "version" not in data.get("summary", {})
    assert "version" not in data.get("tiers", {})


def test_liquidation_daemon_status_falls_back_to_ui_runtime(monkeypatch):
    from web import control_panel

    class Config:
        mode = "auto"
        auto_execute = True
        manual_test_completed = True

    class Engine:
        config = Config()

    monkeypatch.setattr(control_panel, "read_daemon_status", lambda: {"state": "stale", "stale": True})
    monkeypatch.setattr(control_panel, "liquidation_engine_instance", Engine())
    monkeypatch.setattr(control_panel, "observer_supervisor_payload", lambda: {"healthy": True, "env_symbols": "AVAXUSDT", "display_symbols": "AVAXUSDT"})
    monkeypatch.setattr(control_panel, "quick_observer_running", lambda: True)
    monkeypatch.setattr(control_panel, "quick_observer_pid", lambda: 123)
    monkeypatch.setattr(control_panel, "displayed_symbols", lambda _running: ["AVAXUSDT"])
    monkeypatch.setattr(control_panel, "liquidation_market_price_snapshot", lambda: {"AVAXUSDT": 6.4})

    data = app.test_client().get("/api/liquidation/daemon/status").get_json()

    assert data["source"] == "ui_runtime"
    assert data["state"] == "running"
    assert data["engine"]["auto_execute"] is True
    assert data["running"] is True


def test_borrow_pool_route_passes_independent_pagination(monkeypatch):
    from web import control_panel

    captured = {}

    def fake_payload(**kwargs):
        captured.update(kwargs)
        return {
            "rows": [],
            "tiers": {"high_frequency_rows": [], "core_opportunity_rows": []},
            "summary": {},
            "pagination": {},
        }

    monkeypatch.setattr(control_panel, "liquidation_borrow_pool_payload", fake_payload)

    response = app.test_client().get(
        "/api/liquidation/borrow-pool?page_size=20&risk_page=2&high_page=3&core_page=4"
    )

    assert response.status_code == 200
    assert captured == {
        "page_size": 20,
        "risk_page": 2,
        "high_page": 3,
        "core_page": 4,
        "skip_schema": True,
    }


def test_borrow_pool_scan_route_passes_force_and_independent_pagination(monkeypatch):
    from web import control_panel

    captured = {}

    def fake_scan(**kwargs):
        captured.update(kwargs)
        return {"rows": [], "summary": {"scanned": True}, "tiers": {}, "pagination": {}}

    monkeypatch.setattr(control_panel, "liquidation_borrow_pool_scan_payload", fake_scan)

    response = app.test_client().post(
        "/api/liquidation/borrow-pool/scan?page_size=20&risk_page=2&high_page=3&core_page=4&force=1",
        json={},
    )

    assert response.status_code == 200
    assert captured == {
        "force": True,
        "page_size": 20,
        "risk_page": 2,
        "high_page": 3,
        "core_page": 4,
    }


def test_page_state_apis_return_consistent_shape(monkeypatch):
    from web import control_panel

    monkeypatch.setattr(control_panel, "quick_observer_running", lambda: False)
    monkeypatch.setattr(control_panel, "quick_observer_pid", lambda: None)
    monkeypatch.setattr(control_panel, "observer_starting", False)
    monkeypatch.setattr(control_panel, "observer_start_error", None)
    monkeypatch.setattr(control_panel, "latest_binance_extremes_file", lambda: {})
    control_panel.LIQUIDATION_SCAN_CACHE.update({"running": False, "stage": "idle", "last_result": {}})
    control_panel.LIQUIDATION_DISCOVERY_CACHE.update({"running": False, "stage": "idle", "last_result": {}})
    control_panel.LIQUIDATION_ACCOUNT_BACKFILL_CACHE.update({"running": False, "stage": "idle", "error": None})
    monkeypatch.setattr(control_panel, "database_url_or_none", lambda: "local_store.sqlite3")
    monkeypatch.setattr(control_panel, "load_liquidation_account_registry", lambda force=False: (["0x1"], "database"))
    monkeypatch.setattr(
        control_panel,
        "liquidation_account_registry_window",
        lambda: {"total_count": 1, "active_count": 1, "latest_scan_end_at": "2026-08-02T00:00:00+00:00"},
    )

    client = app.test_client()
    for path, page in [
        ("/api/debt-pool/state", "debt_pool"),
        ("/api/account-scan/state", "account_scan"),
        ("/api/market-observation/state", "market_observation"),
        ("/api/execution/state", "execution"),
    ]:
        response = client.get(path)
        data = response.get_json()

        assert response.status_code == 200
        assert data["page"] == page
        assert "status" in data
        assert "result" in data
        assert "message" in data
        assert "updated_at" in data
        assert "source_event_id" in data
        assert "last_error" in data
        assert "context" in data


def test_page_aliases_point_to_expected_work_surfaces():
    client = app.test_client()

    account_scan = client.get("/account-scan").get_data(as_text=True)
    account_scan += client.get("/static/liquidation_panel.js").get_data(as_text=True)
    execution = client.get("/execution").get_data(as_text=True)
    audit = client.get("/audit").get_data(as_text=True)

    assert "accountPoolScanBtn" in account_scan
    assert "auto_return=debt_pool" in account_scan
    assert "executeBtn" in execution
    assert "flashloanBtn" in execution
    assert "attemptBody" in audit


def test_workflow_context_distinguishes_liquidation_and_cow_dex():
    client = app.test_client()

    liquidation = client.get("/api/workflow-context?page_path=/liquidation").get_json()
    bot = client.get("/api/workflow-context?page_path=/bot/account-scan").get_json()
    dex = client.get(
        "/api/workflow-context?page_path=/dex/quotes&market_id=cow-dex-arbitrage&chain_id=0&execution_mode=cow&cow_networks=bnb,avalanche"
    ).get_json()
    analytics = client.get("/api/workflow-context?page_path=/analytics/exchange-matrix").get_json()

    assert liquidation["context"]["workflow"] == "liquidation"
    assert liquidation["context"]["execution_mode"] == "direct"
    assert liquidation["context"]["scope_type"] == "single_chain"
    assert bot["context"]["workflow"] == "liquidation"
    assert bot["context"]["execution_mode"] == "direct"
    assert dex["context"]["workflow"] == "dex_arbitrage"
    assert dex["context"]["execution_mode"] == "cow"
    assert dex["context"]["scope_type"] == "multi_chain"
    assert dex["context"]["selected_networks"] == ["bnb", "avalanche"]
    assert analytics["context"]["workflow"] == "chain_neutral"


def test_request_id_is_issued_and_echoed_back():
    response = app.test_client().get(
        "/api/workflow-context?page_path=/liquidation",
        headers={"X-Request-Id": "trace-123"},
    )

    assert response.status_code == 200
    assert response.headers["X-Request-Id"] == "trace-123"
    assert response.get_json()["request_id"] == "trace-123"


def test_workflow_context_rejects_cross_chain_direct_dex_scope():
    response = app.test_client().get(
        "/api/workflow-context?page_path=/dex-arbitrage&execution_mode=direct&scope=multi_chain"
    )

    assert response.status_code == 400
    assert response.get_json()["code"] == "multi_chain_requires_cow"


def test_api_rejects_explicitly_blocked_execution_mode():
    response = app.test_client().get("/api/liquidation-health?execution_mode=cow")

    assert response.status_code == 400
    assert response.get_json()["code"] == "workflow_execution_blocked"
    assert response.headers["X-Workflow"] == "liquidation"
    assert response.headers["X-Execution-Mode"] == "cow"


def test_dex_api_rejects_explicit_direct_mode_until_supported():
    response = app.test_client().get("/api/binance-market/state?execution_mode=direct")

    assert response.status_code == 400
    assert response.get_json()["code"] == "workflow_execution_blocked"
    assert response.headers["X-Workflow"] == "dex_arbitrage"
    assert response.headers["X-Execution-Mode"] == "direct"


def test_dex_strategy_api_exposes_registered_strategies():
    response = app.test_client().get("/api/dex/strategy?page_path=/dex")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["ok"] is True
    assert payload["selected_strategy"] == "closed_loop_routes"
    assert payload["dex_strategy"] == "closed_loop_routes"
    assert any(item["key"] == "binance_velocity" for item in payload["strategies"])


def test_chain_neutral_api_is_read_only():
    response = app.test_client().post("/api/opportunity-health", json={"attempt": "execute"})

    assert response.status_code == 400
    payload = response.get_json()
    assert payload["code"] == "workflow_execution_blocked"
    assert "chain_neutral_execution_blocked" in payload["workflow_context"]["blocked_reasons"]


def test_left_navigation_preserves_selected_dex_market_scope():
    client = app.test_client()
    body = client.get("/dex?market_id=ethereum-dex-arbitrage&chain_id=1").get_data(as_text=True)

    assert 'href="/dex?chain_id=1&market_id=dex-arbitrage-1-uniswap-v3"' in body
    assert 'href="/dex/overview?chain_id=1&market_id=dex-arbitrage-1-uniswap-v3"' in body
    assert 'data-shell-link="/dex/robinhood-shadow"' not in body
    assert 'data-shell-link="/bot"' not in body
    assert 'data-shell-link="/bot/account-scan"' not in body


def test_left_navigation_preserves_selected_dex_strategy_scope():
    client = app.test_client()
    body = client.get("/dex?market_id=ethereum-dex-arbitrage&chain_id=1&dex_strategy=closed_loop_routes").get_data(as_text=True)

    assert 'href="/dex?chain_id=1&market_id=dex-arbitrage-1-uniswap-v3&dex_strategy=closed_loop_routes"' in body
    assert 'href="/dex/overview?chain_id=1&market_id=dex-arbitrage-1-uniswap-v3&dex_strategy=closed_loop_routes"' in body


def test_left_navigation_preserves_selected_liquidation_market_scope():
    client = app.test_client()
    body = client.get("/bot?market_id=bnb-aave-v3&chain_id=56").get_data(as_text=True)

    assert f'href="/bot?chain_id=56&market_id={CANONICAL_BNB_AAVE}"' in body
    assert f'href="/bot/account-scan?chain_id=56&market_id={CANONICAL_BNB_AAVE}"' in body
    assert 'data-shell-link="/dex"' not in body
    assert 'data-shell-link="/dex/overview"' not in body
    assert "liquidationMarketSelect" not in body


def test_app_shell_owns_one_visible_global_chain_selector():
    client = app.test_client()

    bot_paths = ("/bot", "/bot/account-scan", "/account-scan")
    dex_paths = ("/dex", "/dex/candidates", "/flash-loan/pools")

    for path in bot_paths:
        body = client.get(path).get_data(as_text=True)
        assert body.count('data-global-chain-selector="true"') == 1
        assert body.count('data-chain-selector-component="true"') == 1
        assert body.count('data-dex-strategy-component="true"') == 0
        assert body.count('data-dex-strategy-meta') == 0
        assert body.count('data-feature-selector="true"') == 1
        assert body.count('data-feature-selector-select="true"') == 1
        assert body.count('/static/chain-selector.js') == 1
        assert body.count('/static/dex-strategy-selector.js') == 0
        assert 'class="app-shell-branding"' not in body
        assert 'class="app-shell-title"' not in body
        assert 'data-shell-link="/bot"' in body
        assert 'data-shell-link="/bot/account-scan"' in body
        assert 'data-shell-link="/dex"' not in body
        assert 'data-shell-link="/flash-loan/pools"' not in body
        assert 'class="app-shell-dex-strategy-control"' not in body
        assert 'class="app-shell-dex-strategy-meta"' not in body
        assert 'class="app-shell-protocols" data-chain-selection-options="true"' in body
        assert 'class="app-shell-protocol-control"' in body
        assert 'class="app-shell-protocols-meta"' in body
        assert 'class="app-shell-feature-control"' in body
        assert 'class="app-shell-chain-control"' in body
        assert 'class="app-shell-selector-meta"' in body
        assert "grid-template-columns: minmax(0, 20fr) minmax(0, 25fr) minmax(0, 55fr);" in body
        assert ".app-shell-feature-control { grid-column: 1; grid-row: 1; align-self: start; display: flex; flex-direction: column; align-items: flex-start; justify-content: flex-start; gap: 6px; width: 100%; min-width: 0; }" in body
        assert ".app-shell-chain-control { grid-column: 2; grid-row: 1; align-self: start; display: flex; flex-direction: column; align-items: flex-start; justify-content: flex-start; gap: 6px; width: 100%; min-width: 0; }" in body
        assert ".app-shell-protocol-control { grid-column: 3; grid-row: 1; align-self: start; display: flex; flex-direction: column; align-items: flex-start; justify-content: flex-start; gap: 6px; width: 100%; min-width: 0; overflow: hidden; }" in body
        assert ".app-shell-protocols { display: flex; flex-wrap: nowrap; justify-content: flex-start; align-items: center; align-content: flex-start; gap: 6px; width: 100%; min-width: 0; height: 42px; min-height: 42px; max-height: 42px; overflow-x: auto; overflow-y: hidden; scrollbar-width: none; }" in body
        assert ".app-shell-selector-meta { width: 100%; min-width: 0; min-height: 18px; display: flex; justify-content: flex-start; align-items: center; gap: 8px; color: #d0d5dd; font-size: 11px; line-height: 1.2; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }" in body
        assert ".app-shell-chain-control select { min-height: 38px; flex: 1 1 auto; width: auto; min-width: 0; box-sizing: border-box;" in body
        assert ".app-shell-protocols-meta { width: 100%; min-width: 0; min-height: 18px; color: #d0d5dd; font-size: 11px; line-height: 1.2; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }" in body
        assert ".app-shell-protocols .protocol-chip { display: inline-flex; flex: 0 0 auto; align-items: center; gap: 5px; height: 24px; min-height: 24px;" in body
        assert ".app-shell-protocols .protocol-chip input { flex: 0 0 auto; width: 13px; height: 13px; margin: 0;" in body
        assert ".app-shell-protocols .cow-network-chip { gap: 3px; height: 20px; min-height: 20px; padding: 0 5px; font-size: 9px;" in body
        assert ".app-shell-protocols .cow-network-chip input { width: 10px; height: 10px;" in body
        assert "background: #263244; color: #eef4ff; font-size: 10px;" in body
        assert 'data-shell-gate' in body

    for path in dex_paths:
        body = client.get(path).get_data(as_text=True)
        assert body.count('data-global-chain-selector="true"') == 1
        assert body.count('data-chain-selector-component="true"') == 1
        assert body.count('data-dex-strategy-component="true"') == 1
        assert body.count('data-dex-strategy-meta') == 1
        assert body.count('data-feature-selector="true"') == 1
        assert body.count('data-feature-selector-select="true"') == 1
        assert body.count('/static/chain-selector.js') == 1
        assert body.count('/static/dex-strategy-selector.js') == 1
        assert 'class="app-shell-branding"' not in body
        assert 'class="app-shell-title"' not in body
        assert 'data-shell-link="/bot"' not in body
        assert 'data-shell-link="/bot/account-scan"' not in body
        assert 'data-shell-link="/dex"' in body
        assert 'data-shell-link="/flash-loan/pools"' in body
        assert 'class="app-shell-dex-strategy-control"' in body
        assert 'class="app-shell-dex-strategy-meta"' in body
        assert 'class="app-shell-protocols" data-chain-selection-options="true"' in body
        assert 'class="app-shell-protocol-control"' in body
        assert 'class="app-shell-protocols-meta"' in body
        assert 'class="app-shell-feature-control"' in body
        assert 'class="app-shell-chain-control"' in body
        assert 'class="app-shell-selector-meta"' in body
        assert "grid-template-columns: minmax(0, 20fr) minmax(0, 25fr) minmax(0, 25fr) minmax(0, 30fr);" in body
        assert ".app-shell-feature-control { grid-column: 1; grid-row: 1; align-self: start; display: flex; flex-direction: column; align-items: flex-start; justify-content: flex-start; gap: 6px; width: 100%; min-width: 0; }" in body
        assert ".app-shell-dex-strategy-control { grid-column: 2; grid-row: 1; align-self: start; display: flex; flex-direction: column; align-items: flex-start; justify-content: flex-start; gap: 6px; width: 100%; min-width: 0; }" in body
        assert ".app-shell-chain-control { grid-column: 3; grid-row: 1; align-self: start; display: flex; flex-direction: column; align-items: flex-start; justify-content: flex-start; gap: 6px; width: 100%; min-width: 0; }" in body
        assert ".app-shell-protocol-control { grid-column: 4; grid-row: 1; align-self: start; display: flex; flex-direction: column; align-items: flex-start; justify-content: flex-start; gap: 6px; width: 100%; min-width: 0; overflow: hidden; }" in body
        assert ".app-shell-protocols { display: flex; flex-wrap: nowrap; justify-content: flex-start; align-items: center; align-content: flex-start; gap: 6px; width: 100%; min-width: 0; height: 42px; min-height: 42px; max-height: 42px; overflow-x: auto; overflow-y: hidden; scrollbar-width: none; }" in body
        assert ".app-shell-selector-meta { width: 100%; min-width: 0; min-height: 18px; display: flex; justify-content: flex-start; align-items: center; gap: 8px; color: #d0d5dd; font-size: 11px; line-height: 1.2; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }" in body
        assert ".app-shell-dex-strategy-control select { min-height: 38px; flex: 1 1 auto; width: auto; min-width: 0; box-sizing: border-box;" in body
        assert ".app-shell-chain-control select { min-height: 38px; flex: 1 1 auto; width: auto; min-width: 0; box-sizing: border-box;" in body
        assert ".app-shell-dex-strategy-meta { width: 100%; min-width: 0; min-height: 18px; display: flex; align-items: center; gap: 6px; color: #f2f4f7; font-size: 12px; line-height: 1.2; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }" in body
        assert ".app-shell-dex-strategy-desc { flex: 1 1 auto; min-width: 0; display: block; color: #d0d5dd; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }" in body
        assert ".app-shell-protocols-meta { width: 100%; min-width: 0; min-height: 18px; color: #d0d5dd; font-size: 11px; line-height: 1.2; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }" in body
        assert ".app-shell-protocols .protocol-chip { display: inline-flex; flex: 0 0 auto; align-items: center; gap: 5px; height: 24px; min-height: 24px;" in body
        assert ".app-shell-protocols .protocol-chip input { flex: 0 0 auto; width: 13px; height: 13px; margin: 0;" in body
        assert ".app-shell-protocols .cow-network-chip { gap: 3px; height: 20px; min-height: 20px; padding: 0 5px; font-size: 9px;" in body
        assert ".app-shell-protocols .cow-network-chip input { width: 10px; height: 10px;" in body
        assert "background: #263244; color: #eef4ff; font-size: 10px;" in body
        assert 'data-shell-gate' in body

    settings = client.get("/settings").get_data(as_text=True)
    analytics = client.get("/analytics/opportunity-health").get_data(as_text=True)
    for body in (settings, analytics):
        assert 'data-global-chain-selector="true"' in body
        assert 'data-feature-selector-select="true"' in body
        assert '<option value="dex_arbitrage" selected>DEX</option>' in body


