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
CANONICAL_ROBINHOOD_DEX = "dex-arbitrage-4663-uniswap-v4"
CANONICAL_ROBINHOOD_DEX_V2 = "dex-arbitrage-4663-uniswap-v2"
CANONICAL_ROBINHOOD_DEX_V4 = "dex-arbitrage-4663-uniswap-v4"
CANONICAL_ROBINHOOD_DEX_MORPHO = "dex-arbitrage-4663-morpho-blue"
CANONICAL_ROBINHOOD_TESTNET_DEX = "dex-arbitrage-46630-uniswap-v4"
CANONICAL_HYPERLIQUID_DEX = "dex-arbitrage-hyperliquid-hyperliquid-native"


def test_control_panel_routes_are_assembled_by_app_factory():
    paths = {rule.rule for rule in app.url_map.iter_rules()}

    assert "/healthz" in paths
    assert "/api/status" in paths
    assert "/api/system-info" in paths
    assert "/api/chain-selection" in paths
    assert "/api/markets" in paths
    assert "/api/workflow-context" in paths
    assert "/api/flash-loan/pools" in paths
    assert "/api/flash-loan/pools/refresh" in paths
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
    assert "/api/dex/estimate-fixed-pnl" in paths
    assert "/api/dex/opportunity-scanner/start" in paths
    assert "/api/dex/opportunity-scanner/snapshot" in paths
    assert "/api/dex/opportunity-scanner/scan-now" in paths
    assert "/api/dex/opportunity-scanner/auto-execute" in paths
    assert "/api/dex/closed-loop-groups/<group_key>/refresh" in paths
    assert "/api/dex/quote-request" in paths
    assert "/api/dex/submissions" in paths
    assert "/api/dex/submissions/clear" in paths
    assert "/static/dex-strategy-selector.js" in paths
    assert "/api/execution/state" in paths
    assert "/robinhood-dex" in paths
    assert "/api/robinhood-dex/observation" in paths
    assert "/api/robinhood-dex/readiness" in paths
    assert "/api/liquidation-health" in paths
    assert "/api/debt-pool/decision" in paths
    assert "/api/liquidation/account/<account>/static-call-and-save" in paths


def test_estimate_fixed_pnl_route_requires_route_symbols():
    client = app.test_client()
    response = client.post(
        "/api/dex/estimate-fixed-pnl",
        json={"chain_id": 46630, "amount": 8.0},
    )
    data = response.get_json()
    assert response.status_code == 400
    assert data["error"] == "route_symbols_required"


def test_estimate_fixed_pnl_route_returns_payload(monkeypatch):
    import market.binance_market.dex_route_test as route_test_module

    def fake_estimate_fixed_amount_pnl(**kwargs):
        return {
            "ok": True,
            "mode": "dex_route_fixed_amount_pnl",
            "routeSymbols": kwargs.get("route_symbols"),
            "amount": kwargs.get("amount"),
            "bestAmountMissing": True,
            "direction": " -> ".join(kwargs.get("route_symbols") or []),
            "quoteStatus": "static_reference_pending_live_review",
        }

    monkeypatch.setattr(route_test_module, "estimate_fixed_amount_pnl", fake_estimate_fixed_amount_pnl)
    client = app.test_client()
    response = client.post(
        "/api/dex/estimate-fixed-pnl",
        json={
            "chain_id": 46630,
            "routeSymbols": ["TZ", "TB", "TA", "TZ"],
            "amount": 8.0,
        },
    )
    data = response.get_json()
    assert response.status_code == 200
    assert data["mode"] == "dex_route_fixed_amount_pnl"
    assert data["amount"] == 8.0
    assert data["bestAmountMissing"] is True
    assert data["routeSymbols"] == ["TZ", "TB", "TA", "TZ"]


def test_dex_pages_label_static_framework_as_static_not_history():
    overview = (TEMPLATE_DIR / "dex_overview.html").read_text(encoding="utf-8")
    quotes = (TEMPLATE_DIR / "dex_quotes.html").read_text(encoding="utf-8")
    submissions = (TEMPLATE_DIR / "dex_submissions.html").read_text(encoding="utf-8")
    client = app.test_client()
    overview_body = client.get("/dex/overview").get_data(as_text=True)
    quotes_body = client.get("/dex/quotes").get_data(as_text=True)
    submissions_body = client.get("/dex/submissions").get_data(as_text=True)
    embed_body = client.get("/dex/overview?embed=1").get_data(as_text=True)

    assert "历史路由框架" not in overview
    assert "历史路由框架" not in quotes
    assert "function frameworkLabel(source)" not in overview
    assert "const frameworkLabel = source => {" not in quotes
    assert "function frameworkLabel(source)" not in submissions
    assert "静态框架" in overview_body
    assert "静态框架" in quotes_body
    assert "/static/dex-provenance.js" in overview_body
    assert "/static/dex-provenance.js" in quotes_body
    assert "/static/dex-provenance.js" in submissions_body
    assert "/static/dex-provenance.js" in embed_body
    assert "window.__DEX_PROVENANCE__" in embed_body


def test_dex_provenance_script_exports_shared_labels():
    script = (STATIC_DIR / "dex-provenance.js").read_text(encoding="utf-8")

    assert "function frameworkLabel(source)" in script
    assert "function priceEvidenceLabel(source)" in script
    assert "function priceEvidenceModeLabel(mode)" in script
    assert "function marketObservationSourceStageLabel(source)" in script
    assert "window.priceEvidenceLabel = priceEvidenceLabel" in script
    assert "window.marketObservationSourceStageLabel = marketObservationSourceStageLabel" in script

def test_dex_pages_expose_shared_chain_selector_and_scope_defaults():
    client = app.test_client()

    for path in ("/binance-market", "/dex/market-observation"):
        body = client.get(path).get_data(as_text=True)
        assert "data-chain-selector" in body
        assert 'data-chain-selector-component="true"' in body
        assert 'data-dex-strategy-selector="true"' in body
        assert 'data-chain-selector-select="true"' in body
        assert 'data-dex-strategy-select="true"' in body
        assert 'class="app-shell-protocols" data-chain-selection-options="true"' in body
        assert 'class="app-shell-dex-strategy-control"' in body
        assert 'class="app-shell-control-line"' in body
        assert 'data-dex-strategy-meta' in body
        assert 'id="app-shell-dex-strategy-summary"' in body
        assert 'class="app-shell-chain-control"' in body
        assert 'data-chain-selector-signature="' in body
        assert 'option value="chain:4663"' in body
        assert "padding: 10px 36px 12px;" in body
        assert "padding-left: 18px; padding-right: 18px;" in body
        assert "/static/chain-selector.js" in body
        assert "/static/dex-strategy-selector.js" in body
        assert "/static/dex-provenance.js" in body
        assert "window.__DEX_PROVENANCE__" in body
        assert f'window.__CHAIN_SELECTOR_DEFAULT__={{market_id:"{CANONICAL_ROBINHOOD_DEX}",chain_id:"4663"}}' in body
        assert "window.__DEX_STRATEGY_DEFAULT__" in body
        assert "Binance 速度策略" in body
        assert "闭环扫描策略" in body
        assert "每 200ms 以币安行情涨跌幅筛选差异最大的代币池。" in body
        assert "直接扫描所有闭环链路，实时复核可套利机会。" in body
        assert "includeChainConfig: false" in body
        assert "当前 BOT 市场" not in body
        assert "当前 DEX 链路" not in body

    scoped_body = client.get(
        "/dex/candidates?market_id=robinhood-testnet-dex-paused&chain_id=46630&protocols=uniswap_v4"
    ).get_data(as_text=True)
    assert '/ops?chain_id=46630' in scoped_body
    assert '/settings?chain_id=46630' in scoped_body
    assert '/ops?market_id=robinhood-testnet-dex-paused' not in scoped_body

    quotes_body = client.get("/dex/quotes").get_data(as_text=True)
    quotes_body += client.get("/static/dex_quotes/quotes_app.js").get_data(as_text=True)
    quotes_body += client.get("/static/dex_quotes/quotes_render.js").get_data(as_text=True)
    assert "/api/dex/opportunity-scanner/start" in quotes_body
    assert "/api/dex/opportunity-scanner/snapshot" in quotes_body
    assert "/api/dex/opportunity-scanner/scan-now" in quotes_body
    assert "scanIntervalMs = 3000" in quotes_body
    assert "costs.bestAmountMissing === true" in quotes_body
    assert "activeExecution" in quotes_body
    assert "routeExecutionKey" in quotes_body
    assert "static_reference_pending_live_review" in quotes_body

    submissions_body = client.get("/dex/submissions").get_data(as_text=True)
    assert "/api/dex/submissions/clear" in submissions_body
    assert 'id="routeFrameworkPanel"' in submissions_body
    assert "盈亏/复盘" in submissions_body
    assert "清空当前链路记录" in submissions_body
    assert "未通过链上复核" in submissions_body
    assert "第三方参考价" in submissions_body
    assert "复制记录" in submissions_body
    assert "data-copy-record" in submissions_body
    assert "最佳量=未计算" in submissions_body
    assert '最佳交易量：${reviewBestAmount === null ? "未计算"' in submissions_body
    assert "初始测试量=未记录" in submissions_body
    assert '初始测试量：${reviewInitialAmount === null ? "未记录"' in submissions_body
    assert "const recordTimeValue = row => {" in submissions_body
    assert "recordTimeValue(b) - recordTimeValue(a)" in submissions_body
    assert "function submissionInitialAmount(row)" in submissions_body
    assert "function submissionBestAmount(row)" in submissions_body
    assert "function submissionInputSymbol(row)" in submissions_body
    assert "探测记录=未计算；原始探测记录字段缺失" in submissions_body

    for path in ("/dex-pools", "/candidate-pools"):
        body = client.get(path).get_data(as_text=True)
        assert "data-chain-selector" in body
        assert 'data-chain-selector-component="true"' in body
        assert 'class="app-shell-protocols" data-chain-selection-options="true"' in body
        assert 'class="app-shell-chain-control"' in body
        assert "/static/chain-selector.js" in body
        assert f'window.__CHAIN_SELECTOR_DEFAULT__={{market_id:"{CANONICAL_ROBINHOOD_DEX}",chain_id:"4663"}}' in body


def test_system_pages_keep_feature_selection_and_scope():
    client = app.test_client()

    dex_ops = client.get("/ops?chain_id=46630&feature=dex_arbitrage").get_data(as_text=True)
    bot_ops = client.get("/ops?chain_id=46630&feature=liquidation").get_data(as_text=True)
    dex_body = client.get("/dex?chain_id=46630").get_data(as_text=True)

    assert 'data-feature-selector-select="true"' in dex_ops
    assert '<option value="dex_arbitrage" selected>DEX</option>' in dex_ops
    assert 'option value="chain:46630" selected' in dex_ops
    assert 'data-feature-selector-select="true"' in bot_ops
    assert '<option value="liquidation" selected>BOT</option>' in bot_ops
    assert '/ops?chain_id=46630&feature=dex_arbitrage' in dex_body


def test_chain_selection_api_infers_feature_from_page_path():
    client = app.test_client()

    bot_response = client.get("/api/chain-selection?page_path=/bot")
    dex_response = client.get("/api/chain-selection?page_path=/dex")
    unscoped_response = client.get("/api/chain-selection")

    assert bot_response.status_code == 200
    bot_payload = bot_response.get_json()
    assert bot_payload["selected"]["feature"] == "liquidation"
    assert {row["feature"] for row in bot_payload["available"]} == {"liquidation"}
    assert [row["display_name"] for row in bot_payload["chain_options"]] == EXPECTED_LIQUIDATION_CHAIN_ORDER

    assert dex_response.status_code == 200
    dex_payload = dex_response.get_json()
    assert dex_payload["selected"]["feature"] == "dex_arbitrage"
    assert {row["feature"] for row in dex_payload["available"]} == {"dex_arbitrage"}
    assert [row["display_name"] for row in dex_payload["chain_options"]] == EXPECTED_DEX_CHAIN_ORDER

    assert unscoped_response.status_code == 200
    unscoped_payload = unscoped_response.get_json()
    assert len({row["market_id"] for row in unscoped_payload["available"]}) == 28
    assert [row["display_name"] for row in unscoped_payload["chain_options"]] == EXPECTED_DEX_CHAIN_ORDER


def test_chain_selection_api_exposes_flash_loan_protocols_per_chain():
    client = app.test_client()

    mainnet = client.get("/api/chain-selection?feature=dex_arbitrage&chain_id=4663&include_chain_config=false").get_json()
    testnet = client.get("/api/chain-selection?feature=dex_arbitrage&chain_id=46630&include_chain_config=false").get_json()

    assert mainnet["ok"] is True
    assert mainnet["selected"]["chain_id"] == 4663
    assert [row["protocol"] for row in mainnet["selected"]["flash_loan_protocols"]] == [
        "uniswap_v4",
    ]
    assert mainnet["selected"]["flash_loan_protocol_count"] == 1

    assert testnet["ok"] is True
    assert testnet["selected"]["chain_id"] == 46630
    assert testnet["selected"]["flash_loan_protocols"] == [
        {"protocol": "uniswap_v4", "protocol_id": "uniswap_v4", "display_name": "Uniswap V4"}
    ]
    assert testnet["selected"]["flash_loan_protocol_count"] == 1


def test_flash_loan_pool_page_is_independent_shared_capability_view(monkeypatch):
    from web import app_shell

    monkeypatch.setattr(
        app_shell,
        "protocol_options_for_chain",
        lambda chain_id=None: [
            {"protocol": "uniswap_v4", "display_name": "Uniswap V4"},
        ],
    )
    client = app.test_client()

    body = client.get("/flash-loan/pools").get_data(as_text=True)
    payload = client.get("/api/flash-loan/pools").get_json()
    scoped_payload = client.get("/api/flash-loan/pools?chain_id=4663&protocols=uniswap_v4").get_json()
    bnb_payload = client.get("/api/flash-loan/pools?chain_id=56").get_json()
    venus_payload = client.get("/api/flash-loan/pools?chain_id=56&protocols=venus_core").get_json()

    assert "<title>闪电贷可用资金池</title>" in body
    assert 'id="incrementalRefreshBtn"' in body
    assert 'id="fullRefreshBtn"' in body
    assert "PoolManager" in body
    assert "/api/flash-loan/pools" in body
    assert 'data-chain-selector-component="true"' in body
    assert 'class="app-shell-sidebar"' in body
    assert 'class="protocol-rail"' not in body
    assert 'data-flash-loan-protocol-nav="true"' in body
    assert 'data-flash-loan-protocol-link="uniswap_v4"' in body
    assert 'href="/flash-loan/pools?chain_id=46630&protocols=uniswap_v4"' in body
    assert 'data-flash-loan-protocol-link="venus_core"' not in body
    assert 'data-flash-loan-protocol-link="morpho_blue"' not in body
    assert 'data-flash-loan-protocol-link="pancakeswap_v3"' not in body
    assert 'data-flash-loan-protocol-link="balancer_v2"' not in body
    assert 'data-shell-link="/flash-loan/pools"' in body
    assert payload["ok"] is True
    assert payload["scope"]["chain_id"] == 46630
    assert payload["summary"]["provider_count"] >= 1
    assert payload["summary"]["chain_count"] == 1
    assert payload["summary"]["dex_consumable_count"] > 0
    assert any(row["provider_kind"] == "dex_pool" for row in payload["providers"])
    assert scoped_payload["summary"]["provider_count"] == 1
    assert scoped_payload["summary"]["bot_consumable_count"] == 0
    assert scoped_payload["summary"]["dex_consumable_count"] > 0
    assert scoped_payload["scope"]["chain_id"] == 4663
    assert scoped_payload["scope"]["protocols"] == ["uniswap_v4"]
    scoped_view = scoped_payload["protocol_views"][0]
    assert scoped_view["protocol"] == "uniswap_v4"
    assert scoped_view["processing"]["network"] == "robinhood"
    assert scoped_view["processing"]["chain_id"] == 4663
    assert "borrow_target_universe" in scoped_view
    assert "depth_counts" in scoped_view["summary"]
    assert bnb_payload["scope"]["chain_id"] == 56
    assert {row["chain_id"] for row in bnb_payload["providers"]} == {56}
    assert venus_payload["protocol_views"][0]["protocol"] == "venus_core"
    assert venus_payload["protocol_views"][0]["summary"]["stable_borrow_pool_count"] > 0
    assert "status" not in venus_payload["protocol_views"][0]


def test_flash_loan_pool_page_keeps_robinhood_testnet_on_its_own_scope():
    client = app.test_client()

    response = client.get("/flash-loan/pools?chain_id=46630&protocols=uniswap_v4")
    payload = client.get("/api/flash-loan/pools?chain_id=46630&protocols=uniswap_v4").get_json()

    assert response.status_code == 200
    assert response.location is None
    body = response.get_data(as_text=True)
    assert 'data-flash-loan-protocol-link="uniswap_v4"' in body
    assert payload["ok"] is True
    assert payload["scope"]["chain_id"] == 46630
    assert [provider["chain_id"] for provider in payload["providers"]] == [46630]
    assert payload["protocol_views"][0]["cache"]["source"] == "chains/robinhood-testnet/chain.json"
    assert payload["protocol_views"][0]["cache"]["pool_address"] == ""
    assert payload["protocol_views"][0]["cache"]["pool_manager_address"] == "0x8366a39CC670B4001A1121B8F6A443A643e40951"
    assert payload["protocol_views"][0]["cache"]["identities"][0]["kind"] == "pool_manager"
    assert payload["protocol_views"][0]["cache"]["identities"][0]["value"] == "0x8366a39CC670B4001A1121B8F6A443A643e40951"
    assert payload["protocol_views"][0]["summary"]["borrow_asset_count"] > 0
    assert payload["protocol_views"][0]["summary"]["verified_lending_borrow_asset_count"] == 0
    assert payload["protocol_views"][0]["summary"]["flash_borrowable_asset_count"] > 0
    assert payload["protocol_views"][0]["summary"]["eth_call_verified_flash_asset_count"] == 0
    assert payload["protocol_views"][0]["chainIsolation"]["usesAaveOrMorphoPool"] is False
    assert "status" not in payload["protocol_views"][0]
    assert payload["protocol_views"][0]["cache"]["asset_count"] == 0
    assert payload["protocol_views"][0]["cache"]["flash_accounting_asset_count"] > 0
    assert payload["protocol_views"][0]["cache"]["observable_pool_key_count"] == 25
    assert payload["protocol_views"][0]["cache"]["pool_key_count"] == 25
    assert payload["protocol_views"][0]["summary"]["v4_pool_liquidity_pool_count"] == 25
    assert payload["protocol_views"][0]["summary"]["v4_pool_key_count"] == 25
    assert payload["protocol_views"][0]["summary"]["v4_decoded_pool_key_count"] == 25
    assert payload["protocol_views"][0]["summary"]["v4_token_address_count"] >= 25
    assert payload["protocol_views"][0]["summary"]["depth_counts"] == {"observable": 25}
    assert payload["protocol_views"][0]["chainIsolation"]["network"] == "robinhood-testnet"
    assert payload["protocol_views"][0]["chainIsolation"]["usesMainnetRobinhoodCache"] is False


def test_flash_loan_pool_page_prepares_robinhood_mainnet_v4_scope():
    client = app.test_client()

    response = client.get("/flash-loan/pools?chain_id=4663&protocols=uniswap_v4")
    payload = client.get("/api/flash-loan/pools?chain_id=4663&protocols=uniswap_v4").get_json()
    view = payload["protocol_views"][0]

    assert response.status_code == 200
    assert response.location is None
    assert payload["ok"] is True
    assert payload["scope"]["chain_id"] == 4663
    assert payload["scope"]["protocols"] == ["uniswap_v4"]
    assert payload["providers"][0]["network"] == "robinhood"
    assert payload["providers"][0]["chain_id"] == 4663
    assert payload["providers"][0]["protocol"] == "uniswap_v4"
    assert view["protocol"] == "uniswap_v4"
    assert view.get("status") != "chain_protocol_not_supported"
    assert view["processing"]["network"] == "robinhood"
    assert view["processing"]["chain_id"] == 4663
    assert view["processing"]["handler"] == "intent_trade.chains.robinhood.flash_loan_pools:flash_loan_pool_protocol_view"
    assert "uniswap_v4" in view["processing"]["allowed_protocols"]
    assert "status" not in view
    assert view["summary"]["observable_pool_count"] > 0
    assert view["summary"]["observed_borrow_token_count"] == 1
    assert view["non_stable_borrow_pools"][0]["token_symbol"] == "ETH"
    assert view["non_stable_borrow_pools"][0]["pool_count"] == view["summary"]["v4_pool_key_count"]
    assert view["non_stable_borrow_pools"][0]["paired_symbol_count"] > 0
    assert view["cache"]["identities"][0]["kind"] == "pool_manager"
    assert view["cache"]["identities"][0]["value"] == "0x8366a39CC670B4001A1121B8F6A443A643e40951"
    assert "v4_pool_index" in str(view["cache"].get("source") or "").replace("\\", "/")
    assert "robinhood-testnet" not in str(view.get("cache", {}).get("source") or "").replace("\\", "/")


def test_robinhood_mainnet_dex_surface_apis_use_chain_flows():
    client = app.test_client()

    overview = client.get(
        f"/api/dex/overview?market_id={CANONICAL_ROBINHOOD_DEX_V4}&chain_id=4663&protocols=uniswap_v4"
    ).get_json()
    observation = client.get(
        f"/api/dex/market-observation?market_id={CANONICAL_ROBINHOOD_DEX_V4}&chain_id=4663&protocols=uniswap_v4"
    ).get_json()
    routes = client.get(
        f"/api/dex/quote-routes?market_id={CANONICAL_ROBINHOOD_DEX_V4}&chain_id=4663&protocols=uniswap_v4"
    ).get_json()

    for payload, flow_name in (
        (overview, "dex_overview"),
        (observation, "market_observation"),
        (routes, "dex_quote_routes"),
    ):
        assert payload["chain_id"] == 4663
        assert payload["selected_protocols"] == ["uniswap_v4"]
        assert payload["processing"]["flow_name"] == flow_name
        assert payload["processing"]["network"] == "robinhood"
        assert payload["processing"]["handler"].startswith("intent_trade.chains.robinhood.dex_surfaces:")
        assert "robinhood-testnet" not in str(payload.get("poolStaticTable") or "").lower()


def test_flash_loan_pool_refresh_route_persists_static_store(monkeypatch):
    from web import control_panel_page_routes as page_routes

    calls = {}
    monkeypatch.setattr(
        page_routes,
        "_refresh_flash_loan_pool_sources",
        lambda *, chain_id, protocols, full, panel: {
            "ok": True,
            "requested": "full" if full else "incremental",
            "source": "test",
            "steps": [],
            "errors": [],
            "message": "ok",
        },
    )
    monkeypatch.setattr(
        page_routes,
        "protocol_options_for_chain",
        lambda chain_id=None: [{"protocol": "uniswap_v4", "display_name": "Uniswap V4"}],
    )

    def fake_payload(**kwargs):
        calls.update(kwargs)
        return {
            "ok": True,
            "scope": {"chain_id": kwargs["chain_id"], "protocols": kwargs["protocols"]},
            "summary": {},
            "providers": [],
            "protocol_views": [],
            "reserve_cache": {},
            "borrow_target_universe": None,
            "static_pool_store": [{"protocol": "uniswap_v4", "exists": True}],
        }

    monkeypatch.setattr(page_routes, "flash_loan_pools_payload", fake_payload)

    response = app.test_client().post(
        "/api/flash-loan/pools/refresh?chain_id=46630&protocols=uniswap_v4&full=1"
    )
    data = response.get_json()

    assert response.status_code == 200
    assert calls["chain_id"] == 46630
    assert calls["protocols"] == ["uniswap_v4"]
    assert calls["persist_static"] is True
    assert calls["refresh_mode"] == "full"
    assert data["refresh_state"]["staticPoolStore"][0]["exists"] is True


def test_chain_selection_api_exposes_chain_level_options():
    client = app.test_client()

    bot = client.get("/api/chain-selection?feature=liquidation&page_path=/bot").get_json()
    dex = client.get("/api/chain-selection?feature=dex_arbitrage&page_path=/dex").get_json()

    bot_options = {row["chain_id"]: row for row in bot["chain_options"]}
    dex_options = {row["chain_id"]: row for row in dex["chain_options"]}

    assert [row["display_name"] for row in bot["chain_options"]] == EXPECTED_LIQUIDATION_CHAIN_ORDER
    assert [row["display_name"] for row in dex["chain_options"]] == EXPECTED_DEX_CHAIN_ORDER
    assert bot_options[56]["display_name"] == "BNB Chain"
    assert bot_options[56]["chain_key"] == "chain:56"
    assert bot_options[56]["global_chain_id"] == "chain:56"
    assert bot_options[56]["chain_config"]["features"]["liquidation"]["default_market_id"] == CANONICAL_BNB_VENUS
    assert bot_options[56]["chain_config"]["features"]["liquidation"]["legacy_default_market_id"] == "bnb-venus-core"
    assert bot_options[56]["default_market_id"] in {CANONICAL_BNB_AAVE, CANONICAL_BNB_VENUS}
    assert bot_options[56]["representative_market_id"] == bot_options[56]["default_market_id"]
    assert bot_options[56]["protocol_summary"] == "aave_v3, venus_core"
    assert bot_options[56]["market_count"] == 2
    assert bot_options[4663]["display_name"] == "Robinhood Chain"
    assert bot_options[4663]["chain_key"] == "chain:4663"
    assert bot_options[4663]["protocol_summary"] == "aave_v3, morpho_blue"
    assert bot_options[4663]["market_count"] == 2
    assert bot_options[46630]["display_name"] == "Robinhood Testnet"
    assert bot_options[46630]["chain_key"] == "chain:46630"
    assert bot_options[46630]["global_chain_id"] == "chain:46630"
    assert bot_options[46630]["default_market_id"] == CANONICAL_ROBINHOOD_TESTNET_BOT
    assert bot_options[46630]["chain_config"]["features"]["liquidation"]["default_market_id"] == CANONICAL_ROBINHOOD_TESTNET_BOT
    assert (
        bot_options[46630]["chain_config"]["features"]["liquidation"]["legacy_default_market_id"]
        == "robinhood-testnet-liquidation-shadow"
    )
    assert bot_options[46630]["protocol_summary"] == "morpho_blue"
    assert bot_options[46630]["market_count"] == 1
    assert bot_options[137]["protocol_summary"] == "aave_v3"
    assert bot_options[1]["protocol_summary"] == "aave_v3"
    assert bot_options[0]["protocol_summary"] == "cow_protocol"
    assert dex_options[56]["display_name"] == "BNB Chain"
    assert dex_options[56]["chain_key"] == "chain:56"
    assert dex_options[56]["default_market_id"] == CANONICAL_BNB_DEX
    assert dex_options[56]["representative_market_id"] == CANONICAL_BNB_DEX
    assert dex_options[56]["protocol_summary"] == "biswap_v2, pancakeswap_v2, pancakeswap_v3"
    assert dex_options[56]["market_count"] == 3
    assert dex_options[4663]["chain_key"] == "chain:4663"
    assert dex_options[4663]["protocol_summary"] == "uniswap_v4"
    assert dex_options[4663]["market_count"] == 4
    assert dex_options[46630]["display_name"] == "Robinhood Testnet"
    assert dex_options[46630]["chain_key"] == "chain:46630"
    assert dex_options[46630]["global_chain_id"] == "chain:46630"
    assert dex_options[46630]["chain_config"]["contracts"]["v4_pool_manager"] == "0x8366a39CC670B4001A1121B8F6A443A643e40951"
    assert dex_options[46630]["chain_config"]["data_independence"]["isolation"]["forbidden_networks_complete"] is True
    assert dex_options[46630]["chain_config"]["processing_independence"]["all_flows_require_scope_match"] is True
    assert dex_options[46630]["default_market_id"] == CANONICAL_ROBINHOOD_TESTNET_DEX
    assert dex_options[46630]["protocol_summary"] == "uniswap_v4"
    assert dex_options[46630]["market_count"] == 1
    hyperliquid = next(row for row in dex["chain_options"] if row["chain_key"] == "network:hyperliquid")
    assert hyperliquid["protocol_summary"] == "链路"
    assert hyperliquid["protocol_options"] == []


def test_chain_selection_api_can_omit_chain_config_for_lightweight_ui():
    client = app.test_client()

    payload = client.get(
        "/api/chain-selection?feature=dex_arbitrage&page_path=/dex&chain_id=46630&include_chain_config=false"
    ).get_json()

    assert payload["selected_chain_config"] == {}
    assert all(option["chain_config"] == {} for option in payload["chain_options"])
    assert payload["selected"]["chain_config"] == {}


def test_chain_selection_api_defaults_protocol_filter_to_all_chain_protocols():
    client = app.test_client()

    payload = client.get(
        "/api/chain-selection?feature=liquidation&page_path=/bot&chain_id=56"
    ).get_json()

    assert payload["selected"]["chain_id"] == 56
    assert payload["selected"]["chain_key"] == "chain:56"
    assert payload["selected"]["global_chain_id"] == "chain:56"
    assert payload["selected"]["chain_config"]["network"] == "bnb"
    assert payload["selected"]["chain_config"]["data_independence"]["all_flow_data_files_declared"] is True
    assert payload["selected"]["chain_config"]["processing_independence"]["all_flows_have_handlers"] is True
    assert payload["market_scope"]["global_chain_id"] == "chain:56"
    assert payload["protocol_options"] == [
        {
            "protocol": "aave_v3",
            "protocol_id": "aave_v3",
            "display_name": "Aave V3",
            "market_ids": [CANONICAL_BNB_AAVE],
            "market_count": 1,
            "is_default": False,
            "market_id": CANONICAL_BNB_AAVE,
            "canonical_market_id": CANONICAL_BNB_AAVE,
            "legacy_market_id": "bnb-aave-v3",
        },
        {
            "protocol": "venus_core",
            "protocol_id": "venus_core",
            "display_name": "Venus Core",
            "market_ids": [CANONICAL_BNB_VENUS],
            "market_count": 1,
            "is_default": False,
            "market_id": CANONICAL_BNB_VENUS,
            "canonical_market_id": CANONICAL_BNB_VENUS,
            "legacy_market_id": "bnb-venus-core",
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

    assert payload["selected"]["market_id"] == CANONICAL_BNB_DEX
    assert payload["selected"]["chain_key"] == "chain:56"
    assert payload["selected"]["default_market_id"] == CANONICAL_BNB_DEX
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
        "uniswap_v4",
    ]
    assert len(robinhood["protocol_options"]) == 1
    assert robinhood["selected_protocols"] == ["uniswap_v4"]


def test_robinhood_page_rejects_cow_network_scope():
    response = app.test_client().get(
        "/dex/quotes?market_id=robinhood-dex-shadow&chain_id=4663&workflow=dex_arbitrage&execution_mode=cow&scope=single_chain&cow_networks=bnb"
    )

    assert response.status_code == 400
    assert response.get_json()["code"] in {"execution_scope_mismatch", "market_network_mismatch"}


def test_dex_page_rejects_liquidation_market_as_feature_mismatch():
    response = app.test_client().get("/dex?market_id=avalanche-aave-v3&chain_id=43114")

    assert response.status_code == 400
    payload = response.get_json()
    assert payload["code"] == "feature_mismatch"
    assert "liquidation" in payload["error"]


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
    assert "function clearMarketScope(url)" in source
    assert "function applyChainOnlyToUrl(url, selection)" in source
    assert "applyChainOnlyToUrl(url, selection);" in source
    assert 'link.hasAttribute("data-flash-loan-protocol-link")' in source
    assert "function isFlashLoanPoolPath(pathname)" in source
    assert 'data-chain-selection-empty' in source
    assert 'data-chain-selector-select' in source


def test_dex_strategy_selector_is_registered():
    response = app.test_client().get("/static/dex-strategy-selector.js")
    source = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "function currentStrategyKey()" in source
    assert "function renderStrategyMeta(mount, definition)" in source
    assert 'const STATE_EVENT = "dexstrategychanged";' in source
    assert 'data-dex-strategy-select' in source


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
    assert aave.get_json()["selected"]["market_id"] == CANONICAL_BNB_AAVE
    assert aave.get_json()["selected"]["legacy_market_id"] == "bnb-aave-v3"
    assert aave.get_json()["selected"]["protocol"] == "aave_v3"
    assert venus.status_code == 200
    assert venus.get_json()["selected"]["market_id"] == CANONICAL_BNB_VENUS
    assert venus.get_json()["selected"]["legacy_market_id"] == "bnb-venus-core"
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
    assert len(dex_markets) == 19
    assert CANONICAL_ROBINHOOD_DEX in dex_market_ids
    assert CANONICAL_ROBINHOOD_DEX_V2 in dex_market_ids
    assert CANONICAL_ROBINHOOD_DEX_V4 in dex_market_ids
    assert CANONICAL_ROBINHOOD_DEX_MORPHO in dex_market_ids
    assert CANONICAL_ROBINHOOD_TESTNET_DEX in dex_market_ids
    assert CANONICAL_HYPERLIQUID_DEX in dex_market_ids
    assert next(row for row in dex_markets if row["market_id"] == CANONICAL_ROBINHOOD_DEX)["legacy_market_id"] == "robinhood-dex-shadow"
    assert next(row for row in dex_markets if row["market_id"] == CANONICAL_ROBINHOOD_DEX)["market_type"] == "shadow_chain"
    assert next(row for row in dex_markets if row["market_id"] == CANONICAL_ROBINHOOD_DEX_V4)["protocol"] == "uniswap_v4"
    assert next(row for row in dex_markets if row["market_id"] == CANONICAL_ROBINHOOD_TESTNET_DEX)["chain_id"] == 46630

    assert bot.status_code == 200
    bot_markets = bot.get_json()["available"]
    bot_market_ids = {row["market_id"] for row in bot_markets}
    assert len(bot_markets) == 9
    assert CANONICAL_ROBINHOOD_BOT in bot_market_ids
    assert CANONICAL_ROBINHOOD_MORPHO in bot_market_ids
    assert "liquidation-137-aave-v3" in bot_market_ids
    assert "liquidation-1-aave-v3" in bot_market_ids
    assert "liquidation-0-cow-protocol" in bot_market_ids
    assert CANONICAL_ROBINHOOD_DEX not in bot_market_ids


def test_chain_selection_api_falls_back_fast_when_database_endpoint_is_disabled(monkeypatch):
    from core.db import storage_chain_markets
    from web import control_panel

    database_url = "local_store_endpoint_disabled.sqlite3"
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
    assert aave.get_json()["selected"]["market_id"] == CANONICAL_BNB_AAVE
    assert venus.status_code == 200
    assert venus.get_json()["selected"]["market_id"] == CANONICAL_BNB_VENUS
    assert wrong_chain.status_code == 400
    assert wrong_chain.get_json()["code"] == "chain_mismatch"
    assert len(calls) == 3


