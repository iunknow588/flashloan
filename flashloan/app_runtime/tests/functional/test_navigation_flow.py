from web.control_panel import app


def test_control_panel_navigation_chain_links_expected_pages():
    client = app.test_client()

    root_response = client.get("/")
    assert root_response.status_code == 302
    assert root_response.headers["Location"] == "/flash-loan/pools"

    control_panel = client.get("/bot").get_data(as_text=True)
    pools_panel = client.get("/flash-loan/pools").get_data(as_text=True)
    liquidation_panel = client.get("/liquidation").get_data(as_text=True)
    account_scan_panel = client.get("/account-scan").get_data(as_text=True)
    execution_panel = client.get("/execution").get_data(as_text=True)
    exchange_matrix_panel = client.get("/exchange-matrix").get_data(as_text=True)
    opportunity_health_panel = client.get("/opportunity-health").get_data(as_text=True)
    binance_market_panel = client.get("/binance-market").get_data(as_text=True)
    dex_arbitrage_panel = client.get("/dex-arbitrage").get_data(as_text=True)
    binance_market_html = binance_market_panel
    liquidation_panel += client.get("/static/liquidation_panel.js").get_data(as_text=True)
    account_scan_panel += client.get("/static/liquidation_panel.js").get_data(as_text=True)
    binance_market_panel += client.get("/static/binance_market_helpers.js").get_data(as_text=True)
    binance_market_panel += client.get("/static/binance_market_views.js").get_data(as_text=True)
    dex_arbitrage_panel += client.get("/static/dex_quotes/quotes_app.js").get_data(as_text=True)

    assert 'data-app-shell="true"' in control_panel
    assert 'data-global-chain-selector="true"' in control_panel
    assert 'data-chain-selector-component="true"' in control_panel
    assert 'class="app-shell-branding"' not in control_panel
    assert 'class="app-shell-protocols" data-chain-selection-options="true"' in control_panel
    assert 'class="app-shell-protocol-control"' in control_panel
    assert 'class="app-shell-chain-control"' in control_panel
    assert 'class="app-shell-selector-meta"' in control_panel
    assert 'data-shell-link="/dex/robinhood-shadow"' not in control_panel
    assert 'data-dex-strategy-component="true"' not in control_panel
    assert 'data-shell-link="/bot"' in control_panel
    assert 'data-shell-link="/bot/account-scan"' in control_panel
    assert 'data-shell-link="/dex"' not in control_panel
    assert 'data-shell-link="/flash-loan/pools"' not in control_panel
    assert 'data-app-shell="true"' in pools_panel
    assert 'data-shell-link="/dex"' in pools_panel
    assert 'data-shell-link="/flash-loan/pools"' in pools_panel
    assert 'data-shell-link="/bot"' not in pools_panel
    assert 'data-shell-link="/bot/account-scan"' not in pools_panel

    for link in [
        "/bot",
        "/bot/borrow-pool",
        "/bot/account-scan",
        "/bot/execution",
        "/bot/audit",
        "/ops",
        "/settings",
        "/analytics/exchange-matrix",
        "/analytics/opportunity-health",
    ]:
        assert link in control_panel

    for link in [
        "/dex",
        "/dex/overview",
        "/dex/market-observation",
        "/dex/candidates",
        "/dex/submissions",
        "/flash-loan/pools",
    ]:
        assert f'data-shell-link="{link}"' not in control_panel

    for path in [
        "/account-scan",
        "/market-observation",
        "/dex-arbitrage",
        "/config",
        "/audit",
        "/execution",
        "/bot",
        "/bot/account-scan",
        "/bot/execution",
        "/bot/audit",
        "/dex",
        "/dex/overview",
        "/dex/market-observation",
        "/dex/candidates",
        "/dex/quotes",
        "/dex/submissions",
        "/settings",
        "/analytics/exchange-matrix",
        "/analytics/opportunity-health",
    ]:
        assert client.get(path).status_code == 200

    assert "enforceDebtPoolPrereq" in liquidation_panel
    assert "autoReturnToDebtPoolIfReady" in liquidation_panel
    assert '/account-scan?from=debt_pool' in liquidation_panel
    assert "/liquidation/account?account=" in liquidation_panel

    assert "enforceDebtPoolPrereq" in account_scan_panel
    assert "autoReturnToDebtPoolIfReady" in account_scan_panel
    assert "from=account_scan" in account_scan_panel
    assert "accountPoolScanBtn" in account_scan_panel
    assert 'data-shell-link="/bot/account-scan"' in account_scan_panel
    assert 'data-shell-link="/dex"' not in account_scan_panel

    assert "document.body.classList.add(\"embedded\")" in execution_panel
    assert "accountInput" in execution_panel
    assert "executeBtn" in execution_panel
    assert "flashloanBtn" in execution_panel

    assert "/liquidation" in exchange_matrix_panel
    assert "/liquidation" in opportunity_health_panel
    assert "/account-scan" in exchange_matrix_panel
    assert "/execution" in opportunity_health_panel

    for token in [
        'data-dex-surface-template="quotes"',
        "/api/dex/opportunity-scanner/start",
        "/api/dex/opportunity-scanner/snapshot",
        "/api/dex/opportunity-scanner/scan-now",
        'id="routeList"',
        'id="routeCount"',
        "报价与路由",
        "DEX 总览",
        "/dex/overview",
    ]:
        assert token in dex_arbitrage_panel

    assert 'value="sepolia"' not in binance_market_html
    assert "autoExecuteToggle" not in binance_market_html
    assert 'data-route-action="auto-execute"' not in binance_market_html
    assert 'data-app-shell="true"' in dex_arbitrage_panel
    assert 'data-app-shell="true"' in binance_market_html
    assert 'data-dex-strategy-component="true"' in dex_arbitrage_panel
    assert 'data-dex-strategy-component="true"' in binance_market_html
    assert 'data-dex-surface-template="quotes"' in dex_arbitrage_panel
    assert "cowNetworkChecks" not in dex_arbitrage_panel
    assert "cowNetworkChecks" not in binance_market_html
    assert 'data-shell-link="/bot"' not in dex_arbitrage_panel
    assert 'data-shell-link="/bot"' not in binance_market_html
    assert 'data-shell-link="/bot/account-scan"' not in dex_arbitrage_panel
    assert 'data-shell-link="/bot/account-scan"' not in binance_market_html
    assert 'data-shell-link="/dex"' in dex_arbitrage_panel
    assert 'data-shell-link="/dex"' in binance_market_html
    assert 'data-shell-link="/flash-loan/pools"' in dex_arbitrage_panel
    assert 'data-shell-link="/flash-loan/pools"' in binance_market_html
    assert 'window.__CHAIN_SELECTOR_DEFAULT__={market_id:"dex-arbitrage-46630-uniswap-v4",chain_id:"46630"}' in binance_market_html
    assert '/static/chain-selector.js' in binance_market_html
    assert 'data-chain-selector-component="true"' in binance_market_html


def test_html_pages_disable_browser_cache():
    client = app.test_client()

    account_scan_response = client.get("/account-scan")
    binance_market_response = client.get("/binance-market")

    for response in (account_scan_response, binance_market_response):
        assert response.headers.get("Cache-Control") == "no-store, no-cache, must-revalidate, max-age=0"
        assert response.headers.get("Pragma") == "no-cache"
        assert response.headers.get("Expires") == "0"


def test_liquidation_account_page_supports_embedded_execution_detail():
    client = app.test_client()

    body = client.get(
        "/liquidation/account?account=0x0000000000000000000000000000000000000001&embed=1"
    ).get_data(as_text=True)

    assert 'data-app-shell="true"' not in body
    assert "body.embedded header { display: none; }" in body
    assert "document.body.classList.add(\"embedded\")" in body
    assert "refreshAccount(accountParam)" in body
    assert "/liquidation" in body
    assert "force_allowed" in body
    assert "database_health_scan_history" in body
    assert "flatMap(row" in body
