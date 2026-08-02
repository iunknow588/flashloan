from web.control_panel import app


def test_control_panel_navigation_chain_links_expected_pages():
    client = app.test_client()

    control_panel = client.get("/").get_data(as_text=True)
    liquidation_panel = client.get("/liquidation").get_data(as_text=True)
    account_scan_panel = client.get("/account-scan").get_data(as_text=True)
    execution_panel = client.get("/execution").get_data(as_text=True)
    exchange_matrix_panel = client.get("/exchange-matrix").get_data(as_text=True)
    opportunity_health_panel = client.get("/opportunity-health").get_data(as_text=True)

    for link in ["/", "/opportunity-health", "/exchange-matrix", "/liquidation"]:
        assert link in control_panel

    for path in ["/account-scan", "/market-observation", "/config", "/audit", "/execution"]:
        assert client.get(path).status_code == 200

    assert "enforceDebtPoolPrereq" in liquidation_panel
    assert "autoReturnToDebtPoolIfReady" in liquidation_panel
    assert '/account-scan?from=debt_pool' in liquidation_panel
    assert 'location.href="/liquidation?from=account_scan&account_pool_ready=1"' in liquidation_panel
    assert "/liquidation/account?account=" in liquidation_panel

    assert "enforceDebtPoolPrereq" in account_scan_panel
    assert "autoReturnToDebtPoolIfReady" in account_scan_panel
    assert "from=account_scan" in account_scan_panel
    assert "accountPoolScanBtn" in account_scan_panel

    assert "document.body.classList.add(\"embedded\")" in execution_panel
    assert "accountInput" in execution_panel
    assert "executeBtn" in execution_panel
    assert "flashloanBtn" in execution_panel
    assert "/liquidation" in execution_panel

    assert "/opportunity-health" in exchange_matrix_panel
    assert "/liquidation" in exchange_matrix_panel
    assert "/exchange-matrix" in opportunity_health_panel
    assert "/liquidation" in opportunity_health_panel


def test_liquidation_account_page_supports_embedded_execution_detail():
    client = app.test_client()

    body = client.get("/liquidation/account?account=0x0000000000000000000000000000000000000001&embed=1").get_data(as_text=True)

    assert "body.embedded header { display: none; }" in body
    assert "document.body.classList.add(\"embedded\")" in body
    assert "refreshAccount(accountParam)" in body
    assert "/liquidation" in body
    assert "force_allowed" in body
