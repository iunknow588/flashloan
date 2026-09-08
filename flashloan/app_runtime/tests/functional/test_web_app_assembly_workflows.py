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


def test_flash_loan_pool_page_uses_full_width_content_area_and_sidebar_protocol_nav(monkeypatch):
    from web import app_shell

    monkeypatch.setattr(
        app_shell,
        "protocol_options_for_chain",
        lambda chain_id=None: [{"protocol": "morpho_blue", "display_name": "Morpho Blue"}],
    )
    body = app.test_client().get("/flash-loan/pools?chain_id=4663&protocols=morpho_blue").get_data(as_text=True)

    assert '<main class="flash-loan-pool-page">' in body
    assert "main.flash-loan-pool-page { width: 100%; max-width: none; margin: 0; }" in body
    assert "body { margin: 0; padding: 24px; background: #f5f7fb;" in body
    assert "overflow-x: hidden;" in body
    assert ".table-wrap { width: 100%; max-width: 100%; overflow-x: auto;" in body
    assert ".table-wrap.hidden { display: none; }" in body
    assert "table { width: 100%; min-width: 980px;" in body
    assert 'id="stableSection"' in body
    assert 'id="nonStableSection"' in body
    assert 'id="targetSection"' in body
    assert 'id="statusPanel"' in body
    assert 'id="stableTableWrap"' in body
    assert 'id="targetTableWrap"' in body
    assert 'classList.toggle("hidden", unavailable)' in body
    assert 'classList.toggle("hidden", !rows.length)' in body
    assert 'data-flash-loan-protocol-link="morpho_blue"' in body
    assert 'href="/flash-loan/pools?chain_id=4663&protocols=morpho_blue"' in body
    assert 'data-flash-loan-protocol-link="balancer_v2"' not in body


def test_workflow_context_script_is_served_as_javascript():
    response = app.test_client().get("/static/workflow-context.js")

    assert response.status_code == 200
    assert "application/javascript" in response.content_type
    assert "workflowcontextready" in response.get_data(as_text=True)


def test_liquidation_page_is_served_as_utf8_html():
    client = app.test_client()
    response = client.get("/liquidation")
    body = response.get_data(as_text=True)
    body += client.get("/static/liquidation_panel.js").get_data(as_text=True)

    assert response.status_code == 200
    assert "text/html" in response.content_type
    assert "charset=utf-8" in response.content_type.lower()
    assert "system-info-strip" in body
    assert "scanStrategyText" in body
    assert 'force: "1"' in body
    assert "核心+高频" in body
    assert '<div class="card"><small>系统版本</small>' not in body


def test_liquidation_page_only_forces_manual_borrow_pool_scan():
    client = app.test_client()
    body = client.get("/liquidation").get_data(as_text=True)
    body += client.get("/static/liquidation_panel.js").get_data(as_text=True)
    refresh_start = body.index("async function refreshBorrowPool")
    scan_start = body.index("async function scanBorrowPool()")
    settings_start = body.index("async function refreshSettings()")

    refresh_block = body[refresh_start:scan_start]
    scan_block = body[scan_start:settings_start]

    assert 'force: "1"' not in refresh_block
    assert 'force: "1"' in scan_block


def test_liquidation_page_manual_refresh_scans_borrow_pool_health():
    client = app.test_client()
    body = client.get("/liquidation").get_data(as_text=True)
    body += client.get("/static/liquidation_panel.js").get_data(as_text=True)

    assert "刷新风险池健康度" in body
    assert "风险池健康度已重新链上扫描" in body
    assert '$("borrowPoolRefreshBtn").onclick = scanBorrowPool;' in body


def test_liquidation_page_explains_database_and_profit_review_states():
    client = app.test_client()
    body = client.get("/liquidation").get_data(as_text=True)
    body += client.get("/static/liquidation_panel.js").get_data(as_text=True)

    assert "databaseState" in body
    assert "databaseMeta" in body
    assert "数据库未配置" in body
    assert "数据库不可用" in body
    assert "数据库端点已禁用" in body
    assert "数据库连接被终止" in body
    assert "阻断/观察" in body
    assert "低于 1U，仅手工测试" in body
    assert "低收益测试项" in body


def test_account_pool_state_api_and_debt_pool_gate(monkeypatch):
    from web import control_panel

    monkeypatch.setattr(control_panel, "database_url_or_none", lambda: "postgresql://example")
    monkeypatch.setattr(control_panel, "latest_binance_extremes_file", lambda: {})
    monkeypatch.setattr(control_panel, "load_liquidation_account_registry", lambda force=False: ([], "database"))
    monkeypatch.setattr(
        control_panel,
        "liquidation_account_registry_window",
        lambda: {"total_count": 0, "active_count": 0, "latest_scan_end_at": None},
    )
    control_panel.LIQUIDATION_SCAN_CACHE.update({"running": False, "stage": "idle", "last_result": {}})

    client = app.test_client()
    account_pool = client.get("/api/account-pool/state").get_json()
    debt_pool = client.get("/api/debt-pool/state").get_json()

    assert account_pool["result"] == "ACCOUNT_POOL_EMPTY"
    assert account_pool["ready"] is False
    assert debt_pool["status"] == "NEED_ACCOUNT_POOL"
    assert debt_pool["result"] == "ACCOUNT_POOL_EMPTY"


def test_account_pool_gate_covers_missing_empty_incomplete_and_ready(monkeypatch):
    from web import control_panel

    cases = [
        {
            "database_url": None,
            "accounts": [],
            "source": "none",
            "window": {"total_count": 0, "active_count": 0, "latest_scan_end_at": None},
            "account_result": "ACCOUNT_POOL_MISSING",
            "debt_status": "NEED_ACCOUNT_POOL",
        },
        {
            "database_url": "postgresql://example",
            "accounts": [],
            "source": "database",
            "window": {"total_count": 0, "active_count": 0, "latest_scan_end_at": None},
            "account_result": "ACCOUNT_POOL_EMPTY",
            "debt_status": "NEED_ACCOUNT_POOL",
        },
        {
            "database_url": "postgresql://example",
            "accounts": ["0x1"],
            "source": "database",
            "window": {"total_count": 1, "active_count": 1, "latest_scan_end_at": None},
            "account_result": "ACCOUNT_POOL_INCOMPLETE",
            "debt_status": "NEED_ACCOUNT_POOL",
        },
        {
            "database_url": "postgresql://example",
            "accounts": ["0x1"],
            "source": "database",
            "window": {"total_count": 1, "active_count": 1, "latest_scan_end_at": "2026-08-02T00:00:00+00:00"},
            "account_result": "ACCOUNT_POOL_READY",
            "debt_status": "IDLE_FRESH",
        },
    ]
    client = app.test_client()
    monkeypatch.setattr(control_panel, "latest_binance_extremes_file", lambda: {})
    control_panel.LIQUIDATION_SCAN_CACHE.update({"running": False, "stage": "idle", "last_result": {}, "error": None})

    for case in cases:
        monkeypatch.setattr(control_panel, "database_url_or_none", lambda value=case["database_url"]: value)
        monkeypatch.setattr(control_panel, "load_liquidation_account_registry", lambda force=False, value=case: (value["accounts"], value["source"]))
        monkeypatch.setattr(control_panel, "liquidation_account_registry_window", lambda value=case: value["window"])

        account_pool = client.get("/api/account-pool/state").get_json()
        debt_pool = client.get("/api/debt-pool/state").get_json()

        assert account_pool["result"] == case["account_result"]
        assert account_pool["ready"] is (case["account_result"] == "ACCOUNT_POOL_READY")
        assert debt_pool["status"] == case["debt_status"]
        if case["debt_status"] == "NEED_ACCOUNT_POOL":
            assert debt_pool["context"]["account_pool"]["reason"]


def test_binance_market_state_route_emits_flat_and_market_state_contract(monkeypatch):
    from web.data_routes import binance_market as routes

    monkeypatch.setattr(routes, "safe_latest", lambda loader: {"observed_at": "2026-08-04T00:00:00+00:00", "window_seconds": 1})
    monkeypatch.setattr(
        routes,
        "build_market_state_payload",
        lambda *args, **kwargs: {
            "observed_at": "2026-08-04T00:00:00+00:00",
            "market_state_source": "test",
            "pairs": [],
            "cow_filter": {"network": "bnb"},
        },
    )

    response = app.test_client().get("/api/binance-market/state")

    assert response.status_code == 200, response.get_data(as_text=True)
    payload = response.get_json()
    assert payload["observed_at"] == "2026-08-04T00:00:00+00:00"
    assert payload["market_state"]["observed_at"] == "2026-08-04T00:00:00+00:00"


def test_cow_quotes_route_keeps_display_but_skips_recording_when_submission_paused(monkeypatch):
    from web.data_routes import binance_market as routes

    captured = {}

    monkeypatch.setattr(routes, "panel_call", lambda name, *args, **kwargs: None)
    monkeypatch.setattr(routes, "safe_latest", lambda loader: {"observed_at": "2026-08-04T00:00:00+00:00", "window_seconds": 1, "price_source": "test", "market_state_source": "test"})
    monkeypatch.setattr(routes, "select_binance_market_extremes", lambda extremes, side_limit=50: {"observed_at": "2026-08-04T00:00:00+00:00"})
    monkeypatch.setattr(
        routes,
        "request_cow_trade_thresholds",
        lambda amount=None: ({}, 50, {"adjusted_min_spread_percent": "0", "min_side_change_percent": "0", "min_token_price_usd": "0.01"}),
    )
    monkeypatch.setattr(
        routes,
        "load_cow_supported_token_registry",
        lambda **kwargs: {"network": "bnb", "chain_id": 56, "registry": {}, "source": "memory", "token_count": 0},
    )
    monkeypatch.setattr(
        routes,
        "build_binance_market_state",
        lambda *args, **kwargs: {
            "observed_at": "2026-08-04T00:00:00+00:00",
            "pairs": [{"rank": 1, "pair": "AAA / BBB", "x_base_symbol": "AAA", "y_base_symbol": "BBB"}],
            "cow_filter": {},
        },
    )
    monkeypatch.setattr(
        routes,
        "build_cow_quote_verification",
        lambda *args, **kwargs: {
            "observed_at": "2026-08-04T00:00:00+00:00",
            "amount": "1000",
            "owner": "0x" + "1" * 40,
            "owner_source": "request.owner",
            "cow_network": "bnb",
            "cow_chain_id": 56,
            "cow_testnet": False,
            "price_quality": "fast",
            "valid_for": 60,
            "selected_pair_count": 1,
            "route_count": 1,
            "supported_route_count": 1,
            "unsupported_route_count": 0,
            "viable_count": 1,
            "opportunity_count": 1,
            "precheck": {"routes": []},
            "best": {"pair": "AAA / BBB"},
            "best_opportunity": {"pair": "AAA / BBB"},
            "opportunities": [{"pair": "AAA / BBB"}],
            "ranking": [
                {
                    "pair": "AAA / BBB",
                    "pair_rank": 1,
                    "priority_reason": "reverse_check",
                    "execution_precheck": {"status": "limit_order_ready_to_submit", "checks_passed": True, "can_submit_order": True, "quote_rule_verified": True, "reasons": []},
                    "cow_sdk_result": {"status": "ready"},
                }
            ],
        },
    )
    monkeypatch.setattr(routes, "_cow_submission_pause_guard", lambda **kwargs: {"configured": True, "paused": True, "pause_reason": "manual_pause"})
    monkeypatch.setattr(
        routes,
        "record_cow_execution_attempts_safely",
        lambda *args, **kwargs: captured.setdefault("record_called", True),
    )

    client = app.test_client()
    response = client.get("/api/binance-market/cow-quotes?cow_network=bnb&quote_limit=1&amount=1000")
    data = response.get_json()

    assert response.status_code == 200
    assert captured == {}
    assert data["history_recording"]["source"] == "paused"
    assert data["history_recording"]["recorded"] == 0
    assert data["ranking"][0]["execution_precheck"]["status"] == "submission_paused"
    assert data["ranking"][0]["execution_precheck"]["can_submit_order"] is False
    assert data["ranking"][0]["cow_sdk_result"]["status"] == "submission_paused"


def test_cow_quotes_route_records_pool_evidence_for_candidate(monkeypatch):
    from web.data_routes import binance_market as routes

    captured = {}

    monkeypatch.setattr(routes, "panel_call", lambda name, *args, **kwargs: None)
    monkeypatch.setattr(routes, "safe_latest", lambda loader: {"observed_at": "2026-08-04T00:00:00+00:00"})
    monkeypatch.setattr(routes, "select_binance_market_extremes", lambda extremes, side_limit=50: {"observed_at": "2026-08-04T00:00:00+00:00"})
    monkeypatch.setattr(
        routes,
        "request_cow_trade_thresholds",
        lambda amount=None: ({}, 50, {"adjusted_min_spread_percent": "0", "min_side_change_percent": "0", "min_token_price_usd": "0.01"}),
    )
    monkeypatch.setattr(
        routes,
        "load_cow_supported_token_registry",
        lambda **kwargs: {"network": "avalanche", "chain_id": 43114, "registry": {}, "source": "memory", "token_count": 0},
    )
    monkeypatch.setattr(
        routes,
        "build_binance_market_state",
        lambda *args, **kwargs: {
            "observed_at": "2026-08-04T00:00:00+00:00",
            "pairs": [
                {
                    "rank": 1,
                    "pair": "AAA / BBB",
                    "x_base_symbol": "AAA",
                    "y_base_symbol": "BBB",
                    "route_results": [{"route": ["USDC", "AAA", "BBB", "USDC"], "priority_reason": "test"}],
                }
            ],
            "cow_filter": {"network": "avalanche"},
        },
    )
    monkeypatch.setattr(
        routes,
        "record_ui_signal_pool_evidence_safely",
        lambda market_state, extremes=None: captured.setdefault(
            "evidence",
            {"recorded": True, "output": "evidence.json", "candidateCount": len(market_state.get("pairs") or [])},
        ),
    )
    monkeypatch.setattr(
        routes,
        "build_cow_quote_verification",
        lambda *args, **kwargs: {
            "ranking": [],
            "cow_network": "avalanche",
            "cow_chain_id": 43114,
            "precheck": {"routes": []},
        },
    )
    monkeypatch.setattr(routes, "_cow_submission_pause_guard", lambda **kwargs: {"configured": True, "paused": True})

    data = app.test_client().get("/api/binance-market/cow-quotes?quote_limit=1&amount=1000").get_json()

    assert captured["evidence"]["recorded"] is True
    assert data["market_state"]["pool_evidence_recording"]["output"] == "evidence.json"


def test_cow_quotes_route_submits_ready_orders_when_unpaused(monkeypatch):
    from web.data_routes import binance_market as routes

    captured = {}

    monkeypatch.setattr(routes, "panel_call", lambda name, *args, **kwargs: None)
    monkeypatch.setattr(routes, "safe_latest", lambda loader: {"observed_at": "2026-08-04T00:00:00+00:00"})
    monkeypatch.setattr(routes, "select_binance_market_extremes", lambda extremes, side_limit=50: {"observed_at": "2026-08-04T00:00:00+00:00"})
    monkeypatch.setattr(
        routes,
        "request_cow_trade_thresholds",
        lambda amount=None: ({}, 50, {"adjusted_min_spread_percent": "0", "min_side_change_percent": "0", "min_token_price_usd": "0.01"}),
    )
    monkeypatch.setattr(
        routes,
        "load_cow_supported_token_registry",
        lambda **kwargs: {"network": "ethereum", "chain_id": 1, "registry": {}, "source": "memory", "token_count": 0},
    )
    monkeypatch.setattr(
        routes,
        "build_binance_market_state",
        lambda *args, **kwargs: {
            "observed_at": "2026-08-04T00:00:00+00:00",
            "pairs": [{"rank": 1, "pair": "AAA / BBB", "x_base_symbol": "AAA", "y_base_symbol": "BBB"}],
            "cow_filter": {},
        },
    )
    monkeypatch.setattr(
        routes,
        "build_cow_quote_verification",
        lambda *args, **kwargs: {
            "observed_at": "2026-08-04T00:00:00+00:00",
            "amount": "1000",
            "owner": "0x" + "1" * 40,
            "owner_source": "request.owner",
            "cow_network": "ethereum",
            "cow_chain_id": 1,
            "cow_testnet": False,
            "route_count": 1,
            "viable_count": 1,
            "opportunity_count": 1,
            "precheck": {"routes": []},
            "ranking": [
                {
                    "pair": "AAA / BBB",
                    "pair_rank": 1,
                    "priority_reason": "reverse_check",
                    "path": ["USDC", "BBB", "AAA", "USDC"],
                    "execution_precheck": {
                        "status": "limit_order_ready_to_submit",
                        "checks_passed": True,
                        "can_submit_order": True,
                        "quote_rule_verified": True,
                        "reasons": ["cow_flashloan_sdk_intent_ready"],
                    },
                    "cow_sdk_result": {"status": "ready"},
                }
            ],
        },
    )
    monkeypatch.setattr(routes, "_cow_submission_pause_guard", lambda **kwargs: {"configured": True, "paused": False})

    def fake_submit(**kwargs):
        captured["submission_kwargs"] = kwargs
        return {
            "status": "submitted_success",
            "submitted": True,
            "order_id": "0xorder",
            "tx_hash": None,
            "error": None,
            "blocked_reason": None,
        }

    def fake_record(payload, **kwargs):
        captured["recorded_state"] = payload["ranking"][0]["execution_precheck"]["status"]
        return {"recorded": 1, "source": "jsonl"}

    monkeypatch.setattr(routes, "submit_cow_intent_trade", fake_submit)
    monkeypatch.setattr(
        routes,
        "record_cow_execution_attempts_safely",
        fake_record,
    )

    client = app.test_client()
    response = client.get("/api/binance-market/cow-quotes?cow_network=ethereum&quote_limit=1&amount=1000")
    data = response.get_json()

    assert response.status_code == 200
    assert captured["submission_kwargs"]["quote_payload"]["cow_network"] == "ethereum"
    assert captured["recorded_state"] == "submitted_success"
    assert data["submission_summary"]["submitted"] == 1
    assert data["ranking"][0]["execution_precheck"]["status"] == "submitted_success"
    assert data["ranking"][0]["execution_precheck"]["submission_attempted"] is True
    assert data["ranking"][0]["execution_precheck"]["submission_order_id"] == "0xorder"
    assert data["ranking"][0]["cow_sdk_result"]["submission_status"] == "submitted_success"


def test_cow_candidate_queue_does_not_start_daemon_when_submission_paused(monkeypatch):
    from web.data_routes import binance_market_admin as routes

    captured = {}

    monkeypatch.setattr(routes, "_cow_submission_pause_guard", lambda **kwargs: {"configured": True, "paused": True, "pause_reason": "manual_pause"})
    monkeypatch.setattr(routes, "cow_quote_daemon_enabled", lambda: True)
    monkeypatch.setattr(
        routes,
        "ensure_cow_quote_daemon_running",
        lambda **kwargs: captured.setdefault("started", True),
    )
    monkeypatch.setattr(
        routes,
        "cow_candidate_queue_snapshot",
        lambda limit=100, networks=None: {
            "daemon": {"enabled": True, "running": False, "paused": True},
            "items": [],
            "networks": networks or [],
        },
    )

    client = app.test_client()
    response = client.get("/api/binance-market/cow-candidate-queue?limit=100")
    data = response.get_json()

    assert response.status_code == 200
    assert captured == {}
    assert data["daemon"]["paused"] is True
    assert data["items"] == []


def test_cow_candidate_queue_api_filters_selected_networks(monkeypatch):
    from web.data_routes import binance_market_admin as routes

    captured = {}

    monkeypatch.setattr(routes, "_cow_submission_pause_guard", lambda **kwargs: {"configured": True, "paused": True})
    monkeypatch.setattr(routes, "cow_quote_daemon_enabled", lambda: False)
    monkeypatch.setattr(
        routes,
        "cow_candidate_queue_snapshot",
        lambda limit=100, networks=None: captured.setdefault(
            "snapshot",
            {
                "daemon": {"enabled": True, "paused": True},
                "items": [],
                "networks": networks or [],
            },
        ),
    )

    response = app.test_client().get("/api/binance-market/cow-candidate-queue?limit=100&cow_networks=bnb,avalanche")
    data = response.get_json()

    assert response.status_code == 200
    assert data["networks"] == ["bnb", "avalanche"]


def test_cow_submission_switch_on_clears_stale_candidate_queue(monkeypatch):
    from web.data_routes import binance_market_admin as routes

    captured = {}
    monkeypatch.setattr(
        routes,
        "_set_cow_submission_pause_guard",
        lambda paused, reason=None, database_url=None: {"paused": paused, "reason": reason},
    )
    monkeypatch.setattr(routes, "cow_quote_daemon_enabled", lambda: True)
    monkeypatch.setattr(
        routes,
        "clear_cow_candidate_queue",
        lambda reason="": captured.setdefault(
            "cleanup",
            {"removed": 66, "reason": reason, "counts": {"ready_not_submitted": 40, "pending": 26}, "size": 0},
        ),
    )
    monkeypatch.setattr(
        routes,
        "ensure_cow_quote_daemon_running",
        lambda **kwargs: captured.setdefault("daemon_started", True),
    )

    response = app.test_client().post(
        "/api/binance-market/cow-submission-pause",
        json={"paused": False, "reason": None},
    )
    data = response.get_json()

    assert response.status_code == 200
    assert data["paused"] is False
    assert data["queue_cleanup"]["removed"] == 66
    assert data["queue_cleanup"]["reason"] == "submission_switch_enabled_clear_stale"
    assert data["queue_cleanup"]["counts"]["pending"] == 26
    assert data["queue_cleanup"]["size"] == 0
    assert captured["daemon_started"] is True


def test_account_scan_page_preserves_debt_pool_return_intent():
    client = app.test_client()

    body = client.get("/account-scan?from=debt_pool&reason=ACCOUNT_POOL_EMPTY&auto_return=debt_pool").get_data(as_text=True)
    body += client.get("/static/liquidation_panel.js").get_data(as_text=True)

    assert "autoReturnToDebtPoolIfReady" in body
    assert 'params.get("auto_return") !== "debt_pool"' in body
    assert 'location.href = "/liquidation?from=account_scan&account_pool_ready=1"' in body
    assert "/api/account-pool/state" in body


def test_debt_pool_decision_api_returns_layered_decision(monkeypatch):
    from web import control_panel

    monkeypatch.setattr(
        control_panel,
        "liquidation_borrow_pool_payload",
        lambda: {
            "debt_pool_decision": {
                "status": "CORE_LIQUIDATION_DECISION",
                "result": "CORE_POOL_LIQUIDATABLE",
                "route_intent": "execution",
            }
        },
    )

    response = app.test_client().get("/api/debt-pool/decision")
    data = response.get_json()

    assert response.status_code == 200
    assert data["result"] == "CORE_POOL_LIQUIDATABLE"
    assert data["route_intent"] == "execution"


def test_market_observation_page_exposes_observation_surface_controls():
    body = app.test_client().get("/dex/market-observation?market_id=polygon-dex-arbitrage&chain_id=137").get_data(as_text=True)

    assert 'data-page-surface="observation"' in body
    assert 'data-dex-surface-template="observation"' in body
    assert 'data-global-chain-selector="true"' in body
    assert 'id="borrowCount"' in body
    assert 'id="sequenceList"' in body


def test_dex_market_observation_uses_dex_surface_not_operations_surface():
    body = app.test_client().get("/dex/market-observation").get_data(as_text=True)

    assert 'data-dex-surface-template="observation"' in body
    assert 'data-global-chain-selector="true"' in body


def test_dex_page_variant_is_driven_by_market_selection():
    client = app.test_client()

    bnb = client.get("/dex/quotes?market_id=bnb-dex-arbitrage&chain_id=56")
    robinhood = client.get("/dex/quotes?market_id=robinhood-dex-shadow&chain_id=4663")

    assert bnb.status_code == 200
    bnb_body = bnb.get_data(as_text=True)
    assert 'data-dex-surface-template="quotes"' in bnb_body
    assert "cowNetworkChecks" not in bnb_body
    assert "复制本组" in bnb_body
    assert "data-copy-route" in bnb_body
    assert "initialProbe=" in bnb_body
    assert "hop ${step.hop" in bnb_body
    assert "referenceNetReturnBps=" in bnb_body
    assert robinhood.status_code == 200
    assert 'data-dex-surface-template="quotes"' in robinhood.get_data(as_text=True)


def test_dex_page_variants_follow_market_type():
    client = app.test_client()

    for market_id, chain_id in (
        ("cow-dex-arbitrage", 0),
        ("ethereum-dex-arbitrage", 1),
        ("polygon-dex-arbitrage", 137),
        ("robinhood-dex-shadow", 4663),
        ("robinhood-testnet-dex-paused", 46630),
    ):
        body = client.get(f"/dex?market_id={market_id}&chain_id={chain_id}").get_data(as_text=True)
        assert 'data-dex-surface-template="quotes"' in body
        assert "cowNetworkChecks" not in body


def test_robinhood_shadow_uses_the_shared_dex_surfaces():
    client = app.test_client()
    surfaces = {
        "/dex": "quotes",
        "/dex/overview": "overview",
        "/dex/market-observation": "observation",
        "/dex/candidates": "candidates",
        "/dex/quotes": "quotes",
        "/dex/submissions": "submissions",
    }

    for path, surface in surfaces.items():
        body = client.get(f"{path}?market_id=robinhood-dex-shadow&chain_id=4663").get_data(as_text=True)
        assert f'data-page-surface="{surface}"' in body
        assert 'data-chain-selector-component="true"' in body
        assert "data-surface-link" in body
        assert 'data-shell-link="/dex/robinhood-shadow"' not in body


def test_dex_overview_page_uses_explicit_page_scope_for_api_request():
    body = app.test_client().get(
        "/dex/overview?market_id=dex-arbitrage-46630-uniswap-v4&chain_id=46630&protocols=uniswap_v4"
    ).get_data(as_text=True)

    assert 'path === "/api/dex/overview"' in body
    assert 'pageUrl.searchParams.get(key)' in body


def test_dex_surface_payload_apis_are_distinct():
    client = app.test_client()

    overview = client.get("/api/dex/overview?market_id=bnb-dex-arbitrage&chain_id=56").get_json()
    observation = client.get("/api/dex/market-observation?market_id=bnb-dex-arbitrage&chain_id=56").get_json()
    robinhood_observation = client.get(
        "/api/dex/market-observation?market_id=robinhood-dex-shadow&chain_id=4663"
    ).get_json()
    robinhood_observation_with_single_protocol = client.get(
        f"/api/dex/market-observation?market_id={CANONICAL_ROBINHOOD_DEX_V2}&chain_id=4663&protocols=uniswap_v2"
    ).get_json()
    quotes = client.get("/api/dex/quote-routes?market_id=bnb-dex-arbitrage&chain_id=56").get_json()
    submissions = client.get("/api/dex/submissions?limit=5").get_json()
    robinhood_testnet_submissions = client.get(
        "/api/dex/submissions?market_id=robinhood-testnet-dex-paused&chain_id=46630&protocols=uniswap_v4&limit=5"
    ).get_json()
    robinhood_candidates = client.get(
        "/api/dex-pools/candidates?market_id=robinhood-dex-shadow&chain_id=4663&protocols=uniswap_v3"
    ).get_json()
    robinhood_candidates_with_lending_protocol = client.get(
        f"/api/dex-pools/candidates?market_id={CANONICAL_ROBINHOOD_DEX_MORPHO}&chain_id=4663&protocols=morpho_blue"
    ).get_json()
    robinhood_candidates_with_single_dex_protocol = client.get(
        f"/api/dex-pools/candidates?market_id={CANONICAL_ROBINHOOD_DEX_V2}&chain_id=4663&protocols=uniswap_v2"
    ).get_json()
    robinhood_candidates_page = client.get(
        "/dex/candidates?market_id=robinhood-dex-shadow&chain_id=4663"
    ).get_data(as_text=True)
    robinhood_testnet_observation_page = client.get(
        "/dex/market-observation?market_id=robinhood-testnet-dex-paused&chain_id=46630&protocols=uniswap_v4"
    ).get_data(as_text=True)
    robinhood_testnet_observation = client.get(
        "/api/dex/market-observation?market_id=robinhood-testnet-dex-paused&chain_id=46630&protocols=uniswap_v4"
    ).get_json()
    robinhood_testnet_overview = client.get(
        "/api/dex/overview?market_id=robinhood-testnet-dex-paused&chain_id=46630&protocols=uniswap_v4"
    ).get_json()
    robinhood_testnet_candidates = client.get(
        "/api/dex-pools/candidates?market_id=robinhood-testnet-dex-paused&chain_id=46630&protocols=uniswap_v4"
    ).get_json()
    robinhood_testnet_quotes = client.get(
        "/api/dex/quote-routes?market_id=robinhood-testnet-dex-paused&chain_id=46630&protocols=uniswap_v4"
    ).get_json()
    quote_pair = quote(str(robinhood_testnet_quotes["routes"][0]["symbolPair"]), safe="")
    quote_request = client.get(
        f"/api/dex/quote-request?market_id=robinhood-testnet-dex-paused&chain_id=46630&protocols=uniswap_v4&pair={quote_pair}&venue=uniswap_v4&amount=1000"
    ).get_json()

    assert overview["mode"] == "dex_overview"
    assert observation["mode"] == "dex_market_observation"
    assert robinhood_observation["mode"] == "dex_market_observation"
    assert robinhood_observation_with_single_protocol["selected_protocols"] == ["uniswap_v2"]
    assert quotes["mode"] == "dex_quote_routes"
    assert quote_request["mode"] == "dex_quote_request"
    assert quote_request["processing"]["flow_name"] == "dex_quote_request"
    assert quote_request["processing"]["network"] == "robinhood-testnet"
    assert quote_request["processing"]["handler"] == "intent_trade.chains.robinhood_testnet.dex_surfaces:dex_quote_request_flow"
    assert submissions["mode"] == "dex_submission_review"


    assert "routeFramework" in submissions
    assert robinhood_testnet_submissions["routeFramework"]["routeFrameworkSource"] in {"market_observation", "candidate_pool_fallback"}
    assert robinhood_testnet_submissions["routeFramework"]["marketObservationSourceStage"] in {"generated_live_snapshot", "refreshed_live_snapshot", "cached_snapshot", "computed"}
    assert robinhood_testnet_submissions["routeFramework"]["topRouteText"]
    assert "arbitragePaths" in overview
    assert overview["decisionBasis"] in {"market_observation_closed_loop", "borrow_anchor_to_non_anchor_candidate_path"}
    if overview["arbitragePaths"]:
        assert overview["arbitragePaths"][0]["overviewRoute"]["anchorBorrowSymbol"]
    assert "directPools" in observation
    assert "routes" in quotes
    assert "quoteRequests" in quote_request
    assert "attempts" in submissions
    assert robinhood_candidates["poolStaticTable"]["chainId"] == 4663
    assert robinhood_candidates["poolStaticTable"]["network"] == "robinhood"
    assert "pairTableCount" in robinhood_candidates["summary"]
    assert robinhood_candidates_with_lending_protocol.get("selected_protocols", []) == []
    assert "summary" in robinhood_candidates_with_lending_protocol or "error" in robinhood_candidates_with_lending_protocol
    assert robinhood_candidates_with_single_dex_protocol.get("selected_protocols") in (["uniswap_v2"], None)
    assert "summary" in robinhood_candidates_with_single_dex_protocol or "error" in robinhood_candidates_with_single_dex_protocol
    assert 'query.delete("protocols")' not in robinhood_candidates_page
    assert robinhood_testnet_candidates["selected_protocols"] == ["uniswap_v4"]
    assert robinhood_testnet_candidates["summary"]["pairTableCount"] != "0"
    assert robinhood_testnet_observation["selected_protocols"] == ["uniswap_v4"]
    assert robinhood_testnet_observation["summary"]["observationAnchorCount"] > 1
    assert robinhood_testnet_observation["summary"]["candidateObservationGroupCount"] > 0
    assert robinhood_testnet_observation["summary"]["observationSequenceCount"] == len(robinhood_testnet_observation["observationSequences"])
    assert robinhood_testnet_observation["summary"]["directPairCount"] > 0
    assert robinhood_testnet_observation["summary"]["closedLoopRouteCount"] == len(robinhood_testnet_observation["closedLoopRoutes"])
    assert len(robinhood_testnet_observation["observationAnchorSymbols"]) > 1
    assert robinhood_testnet_observation["marketObservationSource"] in {"generated_live_snapshot", "refreshed_live_snapshot", "cached_snapshot"}
    assert robinhood_testnet_observation["marketObservationSourceStage"] in {"generated_live_snapshot", "refreshed_live_snapshot", "cached_snapshot", "computed"}
    assert len(robinhood_testnet_observation["chainSpecificObservationPools"]) <= 80
    if robinhood_testnet_observation.get("marketObservationPreview"):
        assert robinhood_testnet_observation["marketObservationPreview"]["chainSpecificObservationPoolsTotal"] >= len(
            robinhood_testnet_observation["chainSpecificObservationPools"]
        )
    assert len(robinhood_testnet_observation["candidateObservationGroups"]) == robinhood_testnet_observation["summary"]["candidateObservationGroupCount"]


    first_candidate_group = robinhood_testnet_observation["candidateObservationGroups"][0]
    assert first_candidate_group["anchorSymbol"]
    assert int(first_candidate_group["ycTokenCount"]) >= 0
    assert int(first_candidate_group["zcTokenCount"]) >= 0
    assert int(first_candidate_group["observationSequenceCount"]) >= 0
    assert "observationState" in first_candidate_group
    assert "observationSequences" in robinhood_testnet_observation
    assert "marketObservationStaticTable" in robinhood_testnet_observation
    assert "candidateGroupList" in robinhood_testnet_observation_page
    assert "incrementalRefreshBtn" in robinhood_testnet_observation_page
    assert "fullRefreshBtn" in robinhood_testnet_observation_page
    assert "资金锚点可接入交易对" in robinhood_testnet_observation_page
    assert "关联非 ETH 交易对" not in robinhood_testnet_observation_page
    assert "观察状态" in robinhood_testnet_observation_page
    assert robinhood_testnet_overview["marketObservationSource"] in {"computed", "cached_framework_live_confirmation", "cached_snapshot"}
    assert robinhood_testnet_overview["marketObservationSourceStage"] in {"generated_live_snapshot", "refreshed_live_snapshot", "cached_snapshot", "computed"}
    assert robinhood_testnet_overview["summary"]["overviewProbeCompleteCount"] >= 0
    assert "overviewProfit" in robinhood_testnet_overview["arbitragePaths"][0]
    assert robinhood_testnet_quotes["selected_protocols"] == ["uniswap_v4"]
    assert robinhood_testnet_quotes["marketObservationSourceStage"] in {"generated_live_snapshot", "refreshed_live_snapshot", "cached_snapshot", "computed"}
    assert robinhood_testnet_quotes["summary"]["routeCount"] > 1
    assert robinhood_testnet_quotes["summary"]["pathSource"] == "market_observation"
    assert robinhood_testnet_quotes["summary"]["chainSpecificObservationPairCount"] == 2
    assert robinhood_testnet_quotes["summary"]["closedLoopAnchorSymbols"] == ["ETH"]
    assert robinhood_testnet_quotes["summary"]["completeStaticQuoteCount"] > 0
    assert {route["overviewRoute"]["anchorBorrowSymbol"] for route in robinhood_testnet_quotes["routes"]} == {"ETH"}
    assert "sizeAnalysis" in robinhood_testnet_quotes["routes"][0]
    assert robinhood_testnet_quotes["routes"][0]["sizeAnalysis"]["rows"]
    assert max(len(route["quoteAnalysis"]["steps"]) for route in robinhood_testnet_quotes["routes"]) > 1
    assert all(
        route["quoteAnalysis"]["referenceFinalOutputUsd"] is not None
        or route["quoteAnalysis"].get("trustedUsdReference") is False
        for route in robinhood_testnet_quotes["routes"]
    )


def test_dex_overview_response_contains_route_summary_only():
    payload = {
        "mode": "dex_overview",
        "arbitragePaths": [{
            "overviewRoute": {"pathText": "ETH -> USDC -> ETH"},
            "overviewProfit": {"complete": True, "steps": [{"amountOutRaw": "1"}], "observedNetProfitUsd": 2},
            "strictQualityPools": [{"pool": "full"}],
            "executionPools": [{"pool": "pool-1", "protocol": "uniswap_v4", "balances": {"tvlUsd": 10}}],
        }],
        "marketObservation": {
            "summary": {"observationSequenceCount": 1},
            "chainSpecificObservationPairs": [{"symbolPair": "ETH:USDC"}],
            "chainSpecificObservationPools": [{"pool": "full"}],
        },
    }

    response = _overview_response_payload(payload)
    path = response["arbitragePaths"][0]

    assert "strictQualityPools" not in path
    assert path["executionPools"] == [{"pool": "pool-1", "protocol": "uniswap_v4", "balances": {"tvlUsd": 10}}]
    assert "steps" not in path["overviewProfit"]
    assert response["marketObservation"] == {
        "summary": {"observationSequenceCount": 1},
        "chainSpecificObservationPairs": [{"symbolPair": "ETH:USDC"}],
    }


def test_dex_surface_payload_minimal_interfaces_are_consistent(monkeypatch):
    from web.data_routes import dex_surfaces as web_dex_surfaces

    monkeypatch.setattr(
        web_dex_surfaces,
        "selected_chain_id_from_request",
        lambda: ({"network": "robinhood-testnet", "chain_id": 46630}, 46630),
    )
    monkeypatch.setattr(web_dex_surfaces, "request_dex_protocols_arg", lambda: ["uniswap_v4"])
    monkeypatch.setattr(web_dex_surfaces, "_bootstrap_dex_surface_dependencies", lambda **kwargs: None)
    monkeypatch.setattr(web_dex_surfaces, "_dex_surface_payload_for_chain", lambda **kwargs: {
        "mode": kwargs["mode"],
        "chain_id": kwargs["chain_id"],
        "selected_protocols": kwargs["protocols"],
        "summary": {"routeCount": 1},
        "routeFrameworkSource": "market_observation",
        "marketObservationSourceStage": "cached_snapshot",
    })
    monkeypatch.setattr(
        web_dex_surfaces,
        "_finalize_market_observation_payload",
        lambda payload, **kwargs: {
            **payload,
            "marketObservationSource": "cached_snapshot",
            "marketObservationSourceStage": "cached_snapshot",
            "marketObservationStaticTable": {"path": "observation.json", "exists": True},
        },
    )
    monkeypatch.setattr(web_dex_surfaces, "_market_observation_page_payload", lambda payload, **kwargs: payload)
    monkeypatch.setattr(
        web_dex_surfaces,
        "_submission_review_framework_payload",
        lambda **kwargs: {
            "routeFrameworkLabel": "静态框架",
            "marketObservationSourceStageLabel": "缓存静态快照",
        },
    )
    monkeypatch.setattr(
        web_dex_surfaces,
        "dex_submission_review_payload",
        lambda **kwargs: {
            "mode": "dex_submission_review",
            "summary": {"attemptCount": len(kwargs.get("attempts") or [])},
            "attempts": kwargs.get("attempts") or [],
        },
    )
    monkeypatch.setattr(web_dex_surfaces.shared, "_cow_execution_attempt_groups", lambda *args, **kwargs: {"all": []})
    monkeypatch.setattr(web_dex_surfaces.shared, "_flatten_cow_execution_groups", lambda groups, limit: [])
    monkeypatch.setattr(web_dex_surfaces, "submission_networks_from_request", lambda chain_selection: ["robinhood-testnet"])

    client = app.test_client()
    overview = client.get("/api/dex/overview?market_id=robinhood-testnet-dex-paused&chain_id=46630&protocols=uniswap_v4").get_json()
    observation = client.get("/api/dex/market-observation?market_id=robinhood-testnet-dex-paused&chain_id=46630&protocols=uniswap_v4").get_json()
    quotes = client.get("/api/dex/quote-routes?market_id=robinhood-testnet-dex-paused&chain_id=46630&protocols=uniswap_v4").get_json()
    submissions = client.get("/api/dex/submissions?market_id=robinhood-testnet-dex-paused&chain_id=46630&protocols=uniswap_v4&limit=5").get_json()

    for payload, mode in (
        (overview, "dex_overview"),
        (observation, "dex_market_observation"),
        (quotes, "dex_quote_routes"),
        (submissions, "dex_submission_review"),
    ):
        assert payload["mode"] == mode
        if mode != "dex_submission_review":
            assert payload["chain_id"] == 46630
            assert payload["selected_protocols"] == ["uniswap_v4"]

    assert "summary" in overview
    assert "summary" in observation
    assert "summary" in quotes
    assert "routeFramework" in submissions
    assert "routeFrameworkLabel" in submissions["routeFramework"]
    assert "marketObservationSourceStageLabel" in submissions["routeFramework"]


