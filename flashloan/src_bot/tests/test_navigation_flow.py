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

    for link in ["/liquidation", "/account-scan", "/market-observation", "/dex-arbitrage", "/execution", "/audit", "/config"]:
        assert link in control_panel

    assert "债务池" in control_panel
    assert "账户扫描" in control_panel
    assert "市场观察" in control_panel
    assert "DEX 套利" in control_panel
    assert "清算执行" in control_panel
    assert "执行审计" in control_panel
    assert "系统配置" in control_panel
    assert "机会诊断" not in control_panel
    assert "兑换矩阵" not in control_panel

    for path in ["/account-scan", "/market-observation", "/dex-arbitrage", "/config", "/audit", "/execution"]:
        assert client.get(path).status_code == 200

    assert "enforceDebtPoolPrereq" in liquidation_panel
    assert "autoReturnToDebtPoolIfReady" in liquidation_panel
    assert '/account-scan?from=debt_pool' in liquidation_panel
    assert 'location.href = "/liquidation?from=account_scan&account_pool_ready=1"' in liquidation_panel
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

    assert "/liquidation" in exchange_matrix_panel
    assert "/liquidation" in opportunity_health_panel
    assert "/account-scan" in exchange_matrix_panel
    assert "/execution" in opportunity_health_panel
    assert "/api/binance-market/state" in binance_market_panel
    assert "/api/binance-market/states" in binance_market_panel
    assert "/api/binance-market/cow-config" in binance_market_panel
    assert "/api/binance-market/cow-tokens/refresh" in binance_market_panel
    assert "/api/binance-market/cow-support" in binance_market_panel
    assert "/api/binance-market/cow-execution-attempts" in binance_market_panel
    assert "/api/binance-market/cow-candidate-queue" in binance_market_panel
    assert "cowNetworkChecks" in binance_market_panel
    assert "networkOpportunityCards" in binance_market_panel
    assert 'name="cow_network"' in binance_market_panel
    assert "quoteAmountInput" in binance_market_panel
    assert 'value="sepolia"' not in binance_market_panel
    assert 'DEFAULT_SELECTED_NETWORKS = ["avalanche", "polygon", "base", "bnb"]' in binance_market_panel
    assert "amount=${encodeURIComponent(amount)}" in binance_market_panel
    assert "cow_networks=${encodeURIComponent(networks.join(\",\"))}" in binance_market_panel
    assert "cow_network=${encodeURIComponent(cowNetwork)}" in binance_market_panel
    assert "DEX 套利 | CoW SDK" in binance_market_panel
    assert "CoW SDK 报价验证" in binance_market_panel
    assert "CoW 执行复盘历史" in binance_market_panel
    assert "记录满足候选条件后的报价、预检、阻断原因和最终状态" in binance_market_panel
    assert "cowExecutionReviewSection" in binance_market_panel
    assert "cowAttemptReviewConclusion" in binance_market_panel
    assert "cowAttemptReviewMetrics" in binance_market_panel
    assert "cowAttemptHistoryBody" in binance_market_panel
    assert "cowAttemptNotExecutableBody" in binance_market_panel
    assert "cowAttemptFailedBody" in binance_market_panel
    assert "cowAttemptSuccessBody" in binance_market_panel
    assert "COW_ATTEMPT_PAGE_SIZE = 10" in binance_market_panel
    assert "cowAttemptReview" in binance_market_panel
    assert "refreshCowAttemptHistory" in binance_market_panel
    assert "复制摘要" in binance_market_panel
    assert "复制报价" in binance_market_panel
    assert "navigator.clipboard" in binance_market_panel
    assert "marketCopyText" in binance_market_panel
    assert "查询真实报价" in binance_market_panel
    assert "检查下单条件" in binance_market_panel
    assert "quoteRouteGroup" in binance_market_panel
    assert "executePrecheck" in binance_market_panel
    assert "刷新支持代币" in binance_market_panel
    assert "CoW 支持检查" in binance_market_panel
    assert "CoW 支持：未检查" in binance_market_panel
    assert "Binance 原始涨幅前" in binance_market_panel
    assert "Binance 原始跌幅前" in binance_market_panel
    assert "所有 CoW 支持代币涨幅前 50" in binance_market_panel
    assert "所有 CoW 支持代币跌幅前 50" in binance_market_panel
    assert "cowSupportedTopBody" in binance_market_panel
    assert "cowSupportedBottomBody" in binance_market_panel
    assert "renderCowSupportedOverview" in binance_market_panel
    assert "cow_supported_overview" in binance_market_panel
    assert '<button id="quoteBtn">CoW SDK 报价验证</button>' in binance_market_panel
    assert "cowSubmissionEnabledCheckbox" in binance_market_panel
    assert "允许 CoW SDK 交易" in binance_market_panel
    assert "updateCowSubmissionFromCheckbox" in binance_market_panel
    assert 'latestCowSubmissionPausePayload = {paused: true, pause_reason: "ui_initial_fail_closed"}' in binance_market_panel
    assert "latestCowSubmissionPausePayload?.paused !== false" in binance_market_panel
    assert "async function bootDexArbitragePanel()" in binance_market_panel
    assert "await refreshCowSubmissionPause();\n      await loadCowConfig();" in binance_market_panel
    assert "const quoteButtonDisabled = false;" in binance_market_panel
    assert "const checkButtonDisabled = !quote;" in binance_market_panel
    assert "cowSubmissionPauseToggleBtn" not in binance_market_panel
    assert "cowSubmissionPauseClearBtn" not in binance_market_panel
    assert "cowSubmissionPauseRefreshBtn" not in binance_market_panel
    assert "选中 CoW 网络候选链路" in binance_market_panel
    assert "每条链从自己的 CoW 支持代币里独立认领最高涨幅和最高跌幅" in binance_market_panel
    assert "各 CoW 网络支持 Top/Bottom 5" in binance_market_panel
    assert "networkClaims" in binance_market_panel
    assert 'value="50" selected' in binance_market_panel
    assert "前 50 名" in binance_market_panel
    assert "每链 1 组" in binance_market_panel
    assert "pair_side_limit=1" in binance_market_panel
    assert 'value="1" selected' in binance_market_panel
    assert "const quoteLimit = quoteLimitValue()" in binance_market_panel
    assert "autoRefreshCowQuotes" in binance_market_panel
    assert "cowAutomationPaused()" in binance_market_panel
    assert "CoW SDK 交易关闭：报价和界面照常显示，不会提交交易" in binance_market_panel
    assert "AUTO_COW_QUOTE_MIN_INTERVAL_MS" in binance_market_panel
    assert "autoExecuteToggle" not in binance_market_panel
    assert 'data-route-action="auto-execute"' not in binance_market_panel
    assert "自动执行检查" not in binance_market_panel
    assert "rawTopBody" in binance_market_panel
    assert "rawBottomBody" in binance_market_panel
    assert "formatPrice" in binance_market_panel
    assert "priceDigits" in binance_market_panel
    assert "unsupported" in binance_market_panel
    assert "行情触发资产" in binance_market_panel
    assert "初始数量" in binance_market_panel
    assert "CoW 报价流程" in binance_market_panel
    assert "CoW 报价比例" in binance_market_panel
    assert "CoW 预计结果" in binance_market_panel
    assert "原生币余额" in binance_market_panel
    assert "Gas" in binance_market_panel
    assert "手续费" in binance_market_panel
    assert "盘面盈利" in binance_market_panel
    assert "CoW 相对 Binance 估算" in binance_market_panel
    assert "CoW 盈利" in binance_market_panel
    assert "当前盘面买入" in binance_market_panel
    assert "变化前价格换算" in binance_market_panel
    assert "当前盘面卖出" in binance_market_panel
    assert "slip" in binance_market_panel
    assert "currentMarketState = data || null;\n      latestCowQuotes = null" not in binance_market_panel
    assert dex_arbitrage_panel == binance_market_panel


def test_liquidation_account_page_supports_embedded_execution_detail():
    client = app.test_client()

    body = client.get("/liquidation/account?account=0x0000000000000000000000000000000000000001&embed=1").get_data(as_text=True)

    assert "body.embedded header { display: none; }" in body
    assert "document.body.classList.add(\"embedded\")" in body
    assert "refreshAccount(accountParam)" in body
    assert "/liquidation" in body
    assert "force_allowed" in body
    assert "database_health_scan_history" in body
    assert "Aave 历史完整快照" in body
    assert "持仓更新" in body
    assert "flatMap(row" in body
    assert "抵押物" in body
    assert "债务资产" in body
    assert "抵押+借款" not in body
