from web.control_panel import app


def test_control_panel_navigation_chain_links_expected_pages():
    client = app.test_client()

    control_panel = client.get("/").get_data(as_text=True)
    liquidation_panel = client.get("/liquidation").get_data(as_text=True)
    account_scan_panel = client.get("/account-scan").get_data(as_text=True)
    execution_panel = client.get("/execution").get_data(as_text=True)
    exchange_matrix_panel = client.get("/exchange-matrix").get_data(as_text=True)
    opportunity_health_panel = client.get("/opportunity-health").get_data(as_text=True)
    binance_market_panel = client.get("/binance-market").get_data(as_text=True)
    dex_arbitrage_panel = client.get("/dex-arbitrage").get_data(as_text=True)

    assert 'data-app-shell="true"' in control_panel
    assert 'data-global-chain-selector="true"' in control_panel
    assert 'data-chain-selector-component="true"' in control_panel
    assert "FLASH LOAN" in control_panel
    assert 'class="app-shell-protocols" data-chain-protocols="true"' in control_panel
    assert 'class="app-shell-chain-control"' in control_panel
    assert 'class="app-shell-selector-meta"' in control_panel
    assert 'data-shell-link="/dex/robinhood-shadow"' not in control_panel

    for link in [
        "/bot",
        "/bot/borrow-pool",
        "/bot/account-scan",
        "/bot/execution",
        "/bot/audit",
        "/dex",
        "/dex/overview",
        "/dex/market-observation",
        "/dex/candidates",
        "/dex/submissions",
        "/ops",
        "/settings",
        "/analytics/exchange-matrix",
        "/analytics/opportunity-health",
    ]:
        assert link in control_panel

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
        assert token in binance_market_panel

    assert 'value="sepolia"' not in binance_market_panel
    assert "autoExecuteToggle" not in binance_market_panel
    assert 'data-route-action="auto-execute"' not in binance_market_panel
    assert 'data-app-shell="true"' in dex_arbitrage_panel
    assert 'data-app-shell="true"' in binance_market_panel
    assert 'data-dex-surface-template="quotes"' in dex_arbitrage_panel
    assert "cowNetworkChecks" not in dex_arbitrage_panel
    assert "cowNetworkChecks" not in binance_market_panel
    assert 'window.__CHAIN_SELECTOR_DEFAULT__={market_id:"avalanche-dex-arbitrage",chain_id:"43114"}' in binance_market_panel
    assert '/static/chain-selector.js' in binance_market_panel
    assert 'data-chain-selector-component="true"' in binance_market_panel


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
