from web.control_panel import app


EXPECTED_UNIFIED_CHAIN_ORDER = [
    "BNB Chain",
    "AVALANCHE Chain",
    "POLYGON Chain",
    "Robinhood Chain",
    "ETHEREUM Chain",
    "HYPERLIQUID Chain",
    "CoW Rail",
]


def test_control_panel_routes_are_assembled_by_app_factory():
    paths = {rule.rule for rule in app.url_map.iter_rules()}

    assert "/healthz" in paths
    assert "/api/status" in paths
    assert "/api/system-info" in paths
    assert "/api/chain-selection" in paths
    assert "/api/markets" in paths
    assert "/api/workflow-context" in paths
    assert "/api/flash-loan/pools" in paths
    assert "/api/flash-loan/pool-discovery" in paths
    assert "/api/flash-loan/pool-discovery/refresh" in paths
    assert "/static/chain-selector.js" in paths
    assert "/static/workflow-context.js" in paths
    assert "/account-scan" in paths
    assert "/bot" in paths
    assert "/bot/borrow-pool" in paths
    assert "/bot/account-scan" in paths
    assert "/bot/execution" in paths
    assert "/bot/audit" in paths
    assert "/flash-loan" in paths
    assert "/flash-loan/pools" in paths
    assert "/market-observation" in paths
    assert "/binance-market" in paths
    assert "/dex-arbitrage" in paths
    assert "/dex" in paths
    assert "/dex/overview" in paths
    assert "/dex/market-observation" in paths
    assert "/dex/candidates" in paths
    assert "/dex/quotes" in paths
    assert "/dex/submissions" in paths
    assert "/dex/robinhood-shadow" in paths
    assert "/execution" in paths
    assert "/audit" in paths
    assert "/config" in paths
    assert "/settings" in paths
    assert "/ops" in paths
    assert "/analytics/exchange-matrix" in paths
    assert "/analytics/opportunity-health" in paths
    assert "/api/debt-pool/state" in paths
    assert "/api/account-pool/state" in paths
    assert "/api/account-scan/state" in paths
    assert "/api/market-observation/state" in paths
    assert "/api/binance-market/state" in paths
    assert "/api/binance-market/states" in paths
    assert "/api/binance-market/cow-config" in paths
    assert "/api/binance-market/cow-tokens" in paths
    assert "/api/binance-market/cow-tokens/refresh" in paths
    assert "/api/binance-market/cow-support" in paths
    assert "/api/binance-market/cow-quotes" in paths
    assert "/api/binance-market/cow-execution-attempts" in paths
    assert "/api/binance-market/cow-candidate-queue" in paths
    assert "/api/dex/overview" in paths
    assert "/api/dex/market-observation" in paths
    assert "/api/dex/quote-routes" in paths
    assert "/api/dex/quote-request" in paths
    assert "/api/dex/submissions" in paths
    assert "/api/execution/state" in paths
    assert "/robinhood-dex" in paths
    assert "/api/robinhood-dex/observation" in paths
    assert "/api/robinhood-dex/readiness" in paths
    assert "/api/liquidation-health" in paths
    assert "/api/debt-pool/decision" in paths
    assert "/api/liquidation/account/<account>/static-call-and-save" in paths


def test_dex_pages_expose_shared_chain_selector_and_scope_defaults():
    client = app.test_client()

    for path in ("/binance-market", "/dex/market-observation"):
        body = client.get(path).get_data(as_text=True)
        assert "data-chain-selector" in body
        assert 'data-chain-selector-component="true"' in body
        assert 'class="app-shell-protocols" data-chain-protocols="true"' in body
        assert 'class="app-shell-chain-control"' in body
        assert "padding: 10px 36px 12px;" in body
        assert "padding-left: 18px; padding-right: 18px;" in body
        assert "/static/chain-selector.js" in body
        assert 'window.__CHAIN_SELECTOR_DEFAULT__={market_id:"cow-dex-arbitrage",chain_id:"0"}' in body
        assert "当前 BOT 市场" not in body
        assert "当前 DEX 链路" not in body

    for path in ("/dex-pools", "/candidate-pools"):
        body = client.get(path).get_data(as_text=True)
        assert "data-chain-selector" in body
        assert 'data-chain-selector-component="true"' in body
        assert 'class="app-shell-protocols" data-chain-protocols="true"' in body
        assert 'class="app-shell-chain-control"' in body
        assert "/static/chain-selector.js" in body
        assert 'window.__CHAIN_SELECTOR_DEFAULT__={market_id:"bnb-dex-arbitrage",chain_id:"56"}' in body


def test_chain_selection_api_infers_feature_from_page_path():
    client = app.test_client()

    bot_response = client.get("/api/chain-selection?page_path=/bot")
    dex_response = client.get("/api/chain-selection?page_path=/dex")
    unscoped_response = client.get("/api/chain-selection")

    assert bot_response.status_code == 200
    bot_payload = bot_response.get_json()
    assert bot_payload["selected"]["feature"] == "liquidation"
    assert {row["feature"] for row in bot_payload["available"]} == {"liquidation"}
    assert [row["display_name"] for row in bot_payload["chain_options"]] == EXPECTED_UNIFIED_CHAIN_ORDER

    assert dex_response.status_code == 200
    dex_payload = dex_response.get_json()
    assert dex_payload["selected"]["feature"] == "dex_arbitrage"
    assert {row["feature"] for row in dex_payload["available"]} == {"dex_arbitrage"}
    assert [row["display_name"] for row in dex_payload["chain_options"]] == EXPECTED_UNIFIED_CHAIN_ORDER

    assert unscoped_response.status_code == 200
    unscoped_payload = unscoped_response.get_json()
    assert len({row["market_id"] for row in unscoped_payload["available"]}) == 26
    assert [row["display_name"] for row in unscoped_payload["chain_options"]] == EXPECTED_UNIFIED_CHAIN_ORDER


def test_flash_loan_pool_page_is_independent_shared_capability_view(monkeypatch):
    from web import app_shell

    monkeypatch.setattr(
        app_shell,
        "protocol_options_for_chain",
        lambda chain_id=None: [
            {"protocol": "venus_core", "display_name": "Venus Core"},
            {"protocol": "morpho_blue", "display_name": "Morpho Blue"},
            {"protocol": "pancakeswap_v3", "display_name": "PancakeSwap V3"},
        ],
    )
    client = app.test_client()

    body = client.get("/flash-loan/pools").get_data(as_text=True)
    payload = client.get("/api/flash-loan/pools").get_json()
    bnb_payload = client.get("/api/flash-loan/pools?chain_id=56").get_json()
    venus_payload = client.get("/api/flash-loan/pools?protocols=venus_core").get_json()

    assert "闪电贷资金池" in body
    assert "协议目录" not in body
    assert "稳定币借贷池" in body
    assert "非稳定币借贷池" in body
    assert "可借资产与目标资产" in body
    assert "/api/flash-loan/pools" in body
    assert 'data-chain-selector-component="true"' in body
    assert 'class="app-shell-sidebar"' in body
    assert 'class="protocol-rail"' not in body
    assert 'data-flash-loan-protocol-nav="true"' in body
    assert 'data-flash-loan-protocol-link="venus_core"' in body
    assert 'data-flash-loan-protocol-link="morpho_blue"' in body
    assert 'data-flash-loan-protocol-link="pancakeswap_v3"' in body
    assert 'data-flash-loan-protocol-link="balancer_v2"' not in body
    assert "BOT 清算" in body
    assert "DEX 套利" in body
    assert "闪电贷" in body
    assert "系统与分析" in body
    assert 'data-shell-link="/flash-loan/pools"' in body
    assert payload["ok"] is True
    assert payload["summary"]["provider_count"] >= 20
    assert payload["summary"]["bot_consumable_count"] > 0
    assert payload["summary"]["dex_consumable_count"] > 0
    assert any(row["provider_kind"] == "lending_pool" for row in payload["providers"])
    assert any(row["provider_kind"] == "dex_pool" for row in payload["providers"])
    assert payload["protocol_views"][0]["protocol"] == "aave_v3"
    assert "stable_borrow_pools" in payload["protocol_views"][0]
    assert "non_stable_borrow_pools" in payload["protocol_views"][0]
    assert "borrow_target_universe" in payload["protocol_views"][0]
    assert "depth_counts" in payload["protocol_views"][0]["summary"]
    assert bnb_payload["scope"]["chain_id"] == 56
    assert {row["chain_id"] for row in bnb_payload["providers"]} == {56}
    assert venus_payload["protocol_views"][0]["protocol"] == "venus_core"
    assert venus_payload["protocol_views"][0]["summary"]["stable_borrow_pool_count"] > 0
    assert "status" not in venus_payload["protocol_views"][0]


def test_chain_selection_api_exposes_chain_level_options():
    client = app.test_client()

    bot = client.get("/api/chain-selection?feature=liquidation&page_path=/bot").get_json()
    dex = client.get("/api/chain-selection?feature=dex_arbitrage&page_path=/dex").get_json()

    bot_options = {row["chain_id"]: row for row in bot["chain_options"]}
    dex_options = {row["chain_id"]: row for row in dex["chain_options"]}

    assert [row["display_name"] for row in bot["chain_options"]] == EXPECTED_UNIFIED_CHAIN_ORDER
    assert [row["display_name"] for row in dex["chain_options"]] == EXPECTED_UNIFIED_CHAIN_ORDER
    assert bot_options[56]["display_name"] == "BNB Chain"
    assert bot_options[56]["chain_key"] == "chain:56"
    assert bot_options[56]["default_market_id"] in {"bnb-aave-v3", "bnb-venus-core"}
    assert bot_options[56]["representative_market_id"] == bot_options[56]["default_market_id"]
    assert bot_options[56]["protocol_summary"] == "aave_v3, venus_core"
    assert bot_options[56]["market_count"] == 2
    assert bot_options[4663]["display_name"] == "Robinhood Chain"
    assert bot_options[4663]["chain_key"] == "chain:4663"
    assert bot_options[4663]["protocol_summary"] == "aave_v3, morpho_blue"
    assert bot_options[4663]["market_count"] == 2
    assert bot_options[137]["protocol_summary"] == "aave_v3"
    assert bot_options[1]["protocol_summary"] == "aave_v3"
    assert bot_options[0]["protocol_summary"] == "cow_protocol"
    assert dex_options[56]["display_name"] == "BNB Chain"
    assert dex_options[56]["chain_key"] == "chain:56"
    assert dex_options[56]["default_market_id"] == "bnb-dex-arbitrage"
    assert dex_options[56]["representative_market_id"] == "bnb-dex-arbitrage"
    assert dex_options[56]["protocol_summary"] == "biswap_v2, pancakeswap_v2, pancakeswap_v3"
    assert dex_options[56]["market_count"] == 3
    assert dex_options[4663]["chain_key"] == "chain:4663"
    assert dex_options[4663]["protocol_summary"] == "morpho_blue, uniswap_v2, uniswap_v3"
    assert dex_options[4663]["market_count"] == 3
    hyperliquid = next(row for row in dex["chain_options"] if row["chain_key"] == "network:hyperliquid")
    assert hyperliquid["protocol_summary"] == "链路"
    assert hyperliquid["protocol_options"] == []


def test_chain_selection_api_defaults_protocol_filter_to_all_chain_protocols():
    client = app.test_client()

    payload = client.get(
        "/api/chain-selection?feature=liquidation&page_path=/bot&chain_id=56"
    ).get_json()

    assert payload["selected"]["chain_id"] == 56
    assert payload["selected"]["chain_key"] == "chain:56"
    assert payload["protocol_options"] == [
        {
            "protocol": "aave_v3",
            "display_name": "Aave V3",
            "market_ids": ["bnb-aave-v3"],
            "market_count": 1,
            "is_default": False,
            "market_id": "bnb-aave-v3",
        },
        {
            "protocol": "venus_core",
            "display_name": "Venus Core",
            "market_ids": ["bnb-venus-core"],
            "market_count": 1,
            "is_default": False,
            "market_id": "bnb-venus-core",
        },
    ]
    assert payload["selected_protocols"] == ["aave_v3", "venus_core"]
    assert payload["selected"]["selected_protocols"] == ["aave_v3", "venus_core"]
    assert payload["query"]["protocols"] == "aave_v3,venus_core"
    assert payload["selector_query"] == {
        "chain_id": 56,
        "feature": "liquidation",
        "protocols": "aave_v3,venus_core",
    }
    assert "market_id" not in payload["selector_query"]


def test_chain_selection_api_exposes_multi_protocol_dex_chain_filters():
    client = app.test_client()

    payload = client.get(
        "/api/chain-selection?feature=dex_arbitrage&page_path=/dex&chain_id=56"
    ).get_json()
    robinhood = client.get(
        "/api/chain-selection?feature=dex_arbitrage&page_path=/dex&chain_id=4663"
    ).get_json()

    assert payload["selected"]["market_id"] == "bnb-dex-arbitrage"
    assert payload["selected"]["chain_key"] == "chain:56"
    assert payload["selected"]["default_market_id"] == "bnb-dex-arbitrage"
    assert payload["selected"]["protocol"] == "pancakeswap_v3"
    assert [row["protocol"] for row in payload["protocol_options"]] == [
        "biswap_v2",
        "pancakeswap_v2",
        "pancakeswap_v3",
    ]
    assert payload["selected_protocols"] == [
        "biswap_v2",
        "pancakeswap_v2",
        "pancakeswap_v3",
    ]
    assert len(payload["protocol_options"]) > 1
    assert payload["selector_query"] == {
        "chain_id": 56,
        "feature": "dex_arbitrage",
        "protocols": "biswap_v2,pancakeswap_v2,pancakeswap_v3",
    }
    assert "market_id" not in payload["selector_query"]
    assert [row["protocol"] for row in robinhood["protocol_options"]] == [
        "morpho_blue",
        "uniswap_v2",
        "uniswap_v3",
    ]
    assert len(robinhood["protocol_options"]) > 1
    assert robinhood["selected_protocols"] == ["morpho_blue", "uniswap_v2", "uniswap_v3"]


def test_dex_market_observation_uses_dex_surface_not_operations_surface():
    body = app.test_client().get("/dex/market-observation").get_data(as_text=True)

    assert "DEX 套利 | CoW SDK" in body
    assert "启动机会观察" not in body
    assert 'data-global-chain-selector="true"' in body


def test_dex_page_variant_is_driven_by_market_selection():
    client = app.test_client()

    bnb = client.get("/dex/quotes?market_id=bnb-dex-arbitrage&chain_id=56")
    robinhood = client.get("/dex/quotes?market_id=robinhood-dex-shadow&chain_id=4663")

    assert bnb.status_code == 200
    assert "单链 DEX 链路" in bnb.get_data(as_text=True)
    assert "cowNetworkChecks" not in bnb.get_data(as_text=True)
    assert robinhood.status_code == 200
    assert "Robinhood Shadow" in robinhood.get_data(as_text=True)
    assert "DEX 套利 | CoW SDK" not in robinhood.get_data(as_text=True)


def test_robinhood_page_rejects_cow_network_scope():
    response = app.test_client().get(
        "/dex/quotes?market_id=robinhood-dex-shadow&chain_id=4663&workflow=dex_arbitrage&execution_mode=cow&scope=single_chain&cow_networks=bnb"
    )

    assert response.status_code == 400
    assert response.get_json()["code"] == "market_network_mismatch"


def test_dex_page_rejects_liquidation_market_as_feature_mismatch():
    response = app.test_client().get("/dex?market_id=avalanche-aave-v3&chain_id=43114")

    assert response.status_code == 400
    payload = response.get_json()
    assert payload["code"] == "feature_mismatch"
    assert "liquidation" in payload["error"]


def test_dex_page_variants_follow_market_type():
    client = app.test_client()

    cow = client.get("/dex/overview?market_id=cow-dex-arbitrage&chain_id=0").get_data(as_text=True)
    ethereum = client.get("/dex/overview?market_id=ethereum-dex-arbitrage&chain_id=1").get_data(as_text=True)
    polygon = client.get("/dex/overview?market_id=polygon-dex-arbitrage&chain_id=137").get_data(as_text=True)
    robinhood = client.get("/dex/overview?market_id=robinhood-dex-shadow&chain_id=4663").get_data(as_text=True)

    assert "cowNetworkChecks" in cow
    assert "cowNetworkChecks" not in ethereum
    assert "单链 DEX 链路" in ethereum
    assert "cowNetworkChecks" not in polygon
    assert "单链 DEX 链路" in polygon
    assert "cowNetworkChecks" not in robinhood
    assert "Robinhood Shadow" in robinhood


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
        assert "Robinhood Shadow" in body
        assert "data-surface-link" in body
        assert 'data-shell-link="/dex/robinhood-shadow"' not in body


def test_chain_selector_invalidates_scope_bound_browser_state_before_navigation():
    response = app.test_client().get("/static/chain-selector.js")
    source = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "function invalidateScopeCaches(previous, next)" in source
    assert 'key.startsWith("flashloan:")' in source
    assert 'new CustomEvent("chainselectionchanging"' in source
    assert "invalidateScopeCaches(previous, item);" in source
    assert 'requestQuery.set("page_path", window.location.pathname);' in source
    assert 'requestQuery.set("execution_mode", query.get("execution_mode"));' not in source
    assert "function pathFeature(pathname)" in source
    assert "clearMarketScope(url);" in source
    assert 'link.hasAttribute("data-flash-loan-protocol-link")' in source
    assert "function isFlashLoanPoolPath(pathname)" in source
    assert 'data-chain-protocols-flashloan-disabled' in source


def test_chain_selection_api_keeps_same_chain_markets_distinct(monkeypatch):
    from web import control_panel

    monkeypatch.setattr(control_panel, "database_url_or_none", lambda: None)
    client = app.test_client()

    aave = client.get(
        "/api/chain-selection?feature=liquidation&market_id=bnb-aave-v3&chain_id=56"
    )
    venus = client.get(
        "/api/chain-selection?feature=liquidation&market_id=bnb-venus-core&chain_id=56"
    )
    wrong_chain = client.get(
        "/api/chain-selection?feature=liquidation&market_id=bnb-venus-core&chain_id=43114"
    )

    assert aave.status_code == 200
    assert aave.get_json()["selected"]["market_id"] == "bnb-aave-v3"
    assert aave.get_json()["selected"]["protocol"] == "aave_v3"
    assert venus.status_code == 200
    assert venus.get_json()["selected"]["market_id"] == "bnb-venus-core"
    assert venus.get_json()["selected"]["observation_only"] is True
    assert wrong_chain.status_code == 400
    assert wrong_chain.get_json()["code"] == "chain_mismatch"


def test_chain_selection_api_exposes_robinhood_in_bot_and_dex_scopes():
    client = app.test_client()

    dex = client.get(
        "/api/chain-selection?feature=dex_arbitrage&page_path=/dex"
    )
    bot = client.get(
        "/api/chain-selection?feature=liquidation&page_path=/bot"
    )

    assert dex.status_code == 200
    dex_markets = dex.get_json()["available"]
    dex_market_ids = {row["market_id"] for row in dex_markets}
    assert len(dex_markets) == 17
    assert "robinhood-dex-shadow" in dex_market_ids
    assert "robinhood-dex-v2-shadow" in dex_market_ids
    assert "robinhood-dex-morpho-blue-flash" in dex_market_ids
    assert "hyperliquid-dex-shadow" in dex_market_ids
    assert next(row for row in dex_markets if row["market_id"] == "robinhood-dex-shadow")["market_type"] == "shadow_chain"

    assert bot.status_code == 200
    bot_markets = bot.get_json()["available"]
    bot_market_ids = {row["market_id"] for row in bot_markets}
    assert len(bot_markets) == 9
    assert "robinhood-liquidation-shadow" in bot_market_ids
    assert "robinhood-morpho-blue-flash" in bot_market_ids
    assert "polygon-aave-v3-shadow" in bot_market_ids
    assert "ethereum-aave-v3-shadow" in bot_market_ids
    assert "cow-liquidation-shadow" in bot_market_ids
    assert "robinhood-dex-shadow" not in bot_market_ids


def test_chain_selection_api_falls_back_fast_when_database_endpoint_is_disabled(monkeypatch):
    from db import storage_chain_markets
    from db import storage_common
    from web import control_panel

    database_url = "postgresql://live-chain-selection-endpoint-disabled"
    storage_common._DATABASE_UNAVAILABLE_UNTIL.clear()
    calls = []

    def fail(database_url):
        calls.append(database_url)
        raise RuntimeError("the endpoint has been disabled; enable it using the API and retry")

    monkeypatch.setattr(control_panel, "database_url_or_none", lambda: database_url)
    monkeypatch.setattr(storage_chain_markets, "load_chain_market_summaries", fail)
    client = app.test_client()

    aave = client.get(
        "/api/chain-selection?feature=liquidation&market_id=bnb-aave-v3&chain_id=56"
    )
    venus = client.get(
        "/api/chain-selection?feature=liquidation&market_id=bnb-venus-core&chain_id=56"
    )
    wrong_chain = client.get(
        "/api/chain-selection?feature=liquidation&market_id=bnb-venus-core&chain_id=43114"
    )

    assert aave.status_code == 200
    assert aave.get_json()["selected"]["market_id"] == "bnb-aave-v3"
    assert venus.status_code == 200
    assert venus.get_json()["selected"]["market_id"] == "bnb-venus-core"
    assert wrong_chain.status_code == 400
    assert wrong_chain.get_json()["code"] == "chain_mismatch"
    assert len(calls) == 1


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

    from web.data_routes import dex_pools
    payload = dex_pools.dex_pool_candidates_payload()

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
    assert data["build"]["app"] == "flashloan-src-bot"
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
    assert data["system_info"]["build"]["app"] == "flashloan-src-bot"
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
    monkeypatch.setattr(control_panel, "database_url_or_none", lambda: "postgresql://example")
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
        "/api/workflow-context?page_path=/dex/quotes&execution_mode=cow&cow_networks=bnb,avalanche"
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


def test_chain_neutral_api_is_read_only():
    response = app.test_client().post("/api/opportunity-health", json={"attempt": "execute"})

    assert response.status_code == 400
    payload = response.get_json()
    assert payload["code"] == "workflow_execution_blocked"
    assert "chain_neutral_execution_blocked" in payload["workflow_context"]["blocked_reasons"]


def test_dex_page_mounts_shared_market_selector():
    body = app.test_client().get("/dex-arbitrage").get_data(as_text=True)

    assert "cowNetworkChecks" in body
    assert "#cowNetworkChecks { display: none !important; }" in body
    assert "data-chain-selector" in body
    assert 'data-app-shell="true"' in body
    assert 'data-global-chain-selector="true"' in body
    assert "liquidationMarketSelect" not in body
    assert "/static/workflow-context.js" in body
    assert "requestedNetworkIds" in body
    assert "syncCowScopeToUrl" in body
    assert 'next.searchParams.set("execution_mode", "cow")' in body


def test_left_navigation_preserves_selected_dex_market_scope():
    client = app.test_client()
    body = client.get("/dex?market_id=ethereum-dex-arbitrage&chain_id=1").get_data(as_text=True)

    assert 'href="/dex?market_id=ethereum-dex-arbitrage&chain_id=1"' in body
    assert 'href="/dex/overview?market_id=ethereum-dex-arbitrage&chain_id=1"' in body
    assert 'data-shell-link="/dex/robinhood-shadow"' not in body
    assert 'href="/bot"' in body


def test_left_navigation_preserves_selected_liquidation_market_scope():
    client = app.test_client()
    body = client.get("/bot?market_id=bnb-aave-v3&chain_id=56").get_data(as_text=True)

    assert 'href="/bot?market_id=bnb-aave-v3&chain_id=56"' in body
    assert 'href="/bot/account-scan?market_id=bnb-aave-v3&chain_id=56"' in body
    assert 'href="/dex"' in body
    assert "liquidationMarketSelect" not in body


def test_app_shell_owns_one_visible_global_chain_selector():
    client = app.test_client()

    for path in ("/bot", "/bot/account-scan", "/dex", "/dex/candidates", "/flash-loan/pools"):
        body = client.get(path).get_data(as_text=True)
        assert body.count('data-global-chain-selector="true"') == 1
        assert body.count('data-chain-selector-component="true"') == 1
        assert body.count('/static/chain-selector.js') == 1
        assert '<a class="app-shell-brand" href="/bot">FLASH LOAN</a>' in body
        assert "BOT 清算" in body
        assert "DEX 套利" in body
        assert "闪电贷" in body
        assert "系统与分析" in body
        assert 'class="app-shell-protocols" data-chain-protocols="true"' in body
        assert 'class="app-shell-chain-control"' in body
        assert 'class="app-shell-selector-meta"' in body
        assert "grid-template-columns: minmax(160px, 3fr) minmax(260px, 4fr) minmax(0, 5fr);" in body
        assert ".app-shell-chain-control { grid-column: 2; grid-row: 1; display: flex; align-items: center; justify-content: flex-start;" in body
        assert ".app-shell-protocols { grid-column: 3; grid-row: 1; display: flex; flex-wrap: wrap; justify-content: flex-start;" in body
        assert ".app-shell-selector-meta { grid-column: 2 / 4; grid-row: 2; display: flex; justify-content: flex-start;" in body
        assert "justify-content: flex-start; align-items: center; align-content: flex-start; gap: 6px; width: 100%; min-width: 0; height: 42px; min-height: 42px; max-height: 42px; overflow: hidden;" in body
        assert ".app-shell-protocols .protocol-chip { display: inline-flex; flex: 0 0 auto; align-items: center; gap: 5px; min-height: 22px;" in body
        assert ".app-shell-protocols .protocol-chip input { flex: 0 0 auto; width: 13px; height: 13px; margin: 0;" in body
        assert ".app-shell-protocols .cow-network-chip { gap: 3px; min-height: 17px; padding: 1px 5px; font-size: 9px;" in body
        assert ".app-shell-protocols .cow-network-chip input { width: 10px; height: 10px;" in body
        assert "background: #263244; color: #eef4ff; font-size: 10px;" in body
        assert 'data-shell-gate' in body

    settings = client.get("/settings").get_data(as_text=True)
    analytics = client.get("/analytics/opportunity-health").get_data(as_text=True)
    assert 'data-global-chain-selector="true"' not in settings
    assert 'data-global-chain-selector="true"' not in analytics


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
    response = app.test_client().get("/liquidation")
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "text/html" in response.content_type
    assert "charset=utf-8" in response.content_type.lower()
    assert "核心机会库" in body
    assert "清算价值" in body
    assert "执行状态" in body
    assert "system-info-strip" in body
    assert "scanStrategyText" in body
    assert 'force: "1"' in body
    assert "核心+高频" in body
    assert '<div class="card"><small>系统版本</small>' not in body


def test_liquidation_page_only_forces_manual_borrow_pool_scan():
    body = app.test_client().get("/liquidation").get_data(as_text=True)
    refresh_start = body.index("async function refreshBorrowPool")
    scan_start = body.index("async function scanBorrowPool()")
    settings_start = body.index("async function refreshSettings()")

    refresh_block = body[refresh_start:scan_start]
    scan_block = body[scan_start:settings_start]

    assert 'force: "1"' not in refresh_block
    assert 'force: "1"' in scan_block


def test_liquidation_page_manual_refresh_scans_borrow_pool_health():
    body = app.test_client().get("/liquidation").get_data(as_text=True)

    assert "刷新风险池健康度" in body
    assert "风险池健康度已重新链上扫描" in body
    assert '$("borrowPoolRefreshBtn").onclick = scanBorrowPool;' in body


def test_liquidation_page_explains_database_and_profit_review_states():
    body = app.test_client().get("/liquidation").get_data(as_text=True)

    assert "数据库状态" in body
    assert "databaseState" in body
    assert "databaseMeta" in body
    assert "数据库未配置" in body
    assert "数据库不可用" in body
    assert "数据库端点已禁用" in body
    assert "数据库连接被终止" in body
    assert "已连接" in body
    assert "等待数据库" in body
    assert "自动候选" in body
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
                    "execution_precheck": {"status": "limit_order_ready_to_submit", "checks_passed": True, "can_submit_order": True, "reasons": []},
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
    assert 'id="observationStatus"' in body
    assert 'id="marketRows"' in body


def test_dex_market_observation_uses_dex_surface_not_operations_surface():
    body = app.test_client().get("/dex/market-observation").get_data(as_text=True)

    assert 'data-dex-surface-template="observation"' in body
    assert 'data-global-chain-selector="true"' in body


def test_dex_page_variant_is_driven_by_market_selection():
    client = app.test_client()

    bnb = client.get("/dex/quotes?market_id=bnb-dex-arbitrage&chain_id=56")
    robinhood = client.get("/dex/quotes?market_id=robinhood-dex-shadow&chain_id=4663")

    assert bnb.status_code == 200
    assert 'data-dex-surface-template="quotes"' in bnb.get_data(as_text=True)
    assert "cowNetworkChecks" not in bnb.get_data(as_text=True)
    assert robinhood.status_code == 200
    assert 'data-dex-surface-template="quotes"' in robinhood.get_data(as_text=True)


def test_dex_page_variants_follow_market_type():
    client = app.test_client()

    for market_id, chain_id in (
        ("cow-dex-arbitrage", 0),
        ("ethereum-dex-arbitrage", 1),
        ("polygon-dex-arbitrage", 137),
        ("robinhood-dex-shadow", 4663),
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


def test_market_observation_page_exposes_observation_surface_controls():
    body = app.test_client().get("/dex/market-observation?market_id=polygon-dex-arbitrage&chain_id=137").get_data(as_text=True)

    assert 'data-page-surface="observation"' in body
    assert 'data-dex-surface-template="observation"' in body
    assert 'id="directList"' in body
    assert 'id="indirectList"' in body


def test_dex_surface_payload_apis_are_distinct():
    client = app.test_client()

    overview = client.get("/api/dex/overview?market_id=bnb-dex-arbitrage&chain_id=56").get_json()
    observation = client.get("/api/dex/market-observation?market_id=bnb-dex-arbitrage&chain_id=56").get_json()
    quotes = client.get("/api/dex/quote-routes?market_id=bnb-dex-arbitrage&chain_id=56").get_json()
    quote_request = client.get("/api/dex/quote-request?market_id=bnb-dex-arbitrage&chain_id=56&pair=USDC:USDT&venue=uniswapx&amount=1000").get_json()
    submissions = client.get("/api/dex/submissions?limit=5").get_json()
    robinhood_candidates = client.get(
        "/api/dex-pools/candidates?market_id=robinhood-dex-shadow&chain_id=4663&protocols=uniswap_v3"
    ).get_json()
    robinhood_candidates_with_lending_protocol = client.get(
        "/api/dex-pools/candidates?market_id=robinhood-dex-shadow&chain_id=4663&protocols=morpho_blue"
    ).get_json()
    robinhood_candidates_page = client.get(
        "/dex/candidates?market_id=robinhood-dex-shadow&chain_id=4663"
    ).get_data(as_text=True)

    assert overview["mode"] == "dex_overview"
    assert observation["mode"] == "dex_market_observation"
    assert quotes["mode"] == "dex_quote_routes"
    assert quote_request["mode"] == "dex_quote_request"
    assert submissions["mode"] == "dex_submission_review"
    assert "arbitragePaths" in overview
    assert "directPools" in observation
    assert "routes" in quotes
    assert "quoteRequests" in quote_request
    assert "attempts" in submissions
    assert robinhood_candidates["poolStaticTable"]["chainId"] == 4663
    assert robinhood_candidates["poolStaticTable"]["network"] == "robinhood"
    assert robinhood_candidates["summary"]["pairTableCount"] != "0"
    assert robinhood_candidates_with_lending_protocol["summary"]["pairTableCount"] != "0"
    assert robinhood_candidates_with_lending_protocol["pairs"][0]["symbolPair"] == "USDG:WETH"
    assert "本页展示 Avalanche L1 静态池表" not in robinhood_candidates_page


def test_dex_page_mounts_shared_market_selector():
    body = app.test_client().get("/dex-arbitrage").get_data(as_text=True)

    assert 'data-dex-surface-template="quotes"' in body
    assert 'data-chain-selector-component="true"' in body
    assert "data-surface-link" in body

