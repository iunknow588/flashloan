    const zhError = value => {
      const text = String(value || "请求失败");
      if (text === "amount must be positive") return "金额必须大于 0";
      if (/^HTTP \d+$/.test(text)) return `请求失败（${text}）`;
      if (/[\u4e00-\u9fff]/.test(text)) return text;
      return `接口错误（${text}）`;
    };

    function routeBox(route, pair, network) {
      const reason = route.priority_reason === "buy_loser_then_gainer" ? "优先" : "逆向";
      const quote = route.cow_quote || null;
      const support = route.cow_support || quote?.cow_support || null;
      const plan = quote?.binance_execution_plan || route.binance_execution_plan || null;
      const pairRank = pair?.rank || route.pair_rank || "";
      const priorityReason = route.priority_reason || "";
      const pairName = pair?.pair || "";
      const quoteButtonDisabled = false;
      const checkButtonDisabled = !quote;
      const quoteBadge = quote
        ? `<span class="badge">${quote.viable ? "已报价未下单" : "报价失败"}</span>`
        : support
          ? `<span class="badge">${support.supported ? "支持" : "不支持"}</span>`
        : '<span class="badge">未报价</span>';
      const quoteMeta = quote
        ? `原始末跳 ${quote.final_amount || "-"} ${displaySymbol(quote.final_symbol || "")} | 预测关闭`
        : "等待查询 CoW 链上报价";
      const check = quote?.execution_precheck || null;
      const detail = quote
        ? `<div class="route-detail">
            <div>初始数量：${esc(quoteInitial(quote))}</div>
            <div>意图目标：${esc(planDelta(plan))}</div>
            <div>参考参数：${executionParams(plan)}</div>
            <div>CoW 链上实时报价流程：${quoteFlow(quote)}</div>
            <div>${esc(supportText(support))}</div>
            <div>CoW 实时报价比例：${esc(quoteRates(quote))}</div>
            <div>CoW 原始回执：${esc(quoteFinal(quote))}</div>
            <div>预测状态：上层盈亏预测关闭，不按本地差额阻断</div>
            <div>${precheckHtml(quote)}</div>
            ${costDetail(quote, plan)}
            ${quote.viable ? "" : `<div>报价失败原因：${esc(zhError(quote.error || "-"))}</div>`}
          </div>`
        : `<div class="route-detail">
            <div>初始数量：${esc(planInitial(plan))}</div>
            <div>意图目标：${esc(planDelta(plan))}</div>
            <div>参考参数：${executionParams(plan)}</div>
            <div>CoW 链上报价路径：${esc(routePath(route))}</div>
            <div>${esc(supportText(support))}</div>
            <div>CoW 实时报价比例：等待查询</div>
            <div>CoW 原始回执：等待查询</div>
            <div>预测状态：上层盈亏预测关闭</div>
            ${costDetail(null, plan)}
          </div>`;
      return `<div class="route-box">
        <div class="route-top"><span class="route-no">链路 ${esc(route.route_no || "-")} | ${reason}</span>${quoteBadge}</div>
        <div class="route-path">${routePathHtml(route)}</div>
        <div class="route-meta">起始 ${esc(displaySymbol(route.initial_symbol || "USDC"))}<br>${esc(route.route_summary || route.route_text || quoteMeta)}</div>
        ${detail}
        <div class="route-actions">
          <button data-route-action="quote" data-cow-network="${esc(network || "")}" data-pair-rank="${esc(pairRank)}" ${quoteButtonDisabled ? "disabled" : ""}>查询真实报价</button>
          <button data-route-action="execute-check" data-cow-network="${esc(network || "")}" data-pair-rank="${esc(pairRank)}" data-priority-reason="${esc(priorityReason)}" data-pair="${esc(pairName)}" ${checkButtonDisabled ? "disabled" : ""}>检查下单条件</button>
        </div>
      </div>`;
    }
    function routeGraph(routes, pair, network) {
      return `<div class="route-grid">${(routes || []).map(route => routeBox(route, pair, network)).join("")}</div>`;
    }
    function renderCowIdentity(config) {
      const selected = config || selectedCowConfig();
      $("cowQuoteContext").textContent = selected
        ? `CoW ${selected.network} | ${selected.owner_source || "-"} | ${shortAddress(selected.owner)}`
        : "CoW -";
      $("cowQuoteContext").title = selected?.owner || "";
    }
    function renderCowConfig(data) {
      cowConfig = data || null;
      const networks = Array.isArray(data?.networks) ? data.networks : [];
      const uiNetworks = networks.filter(item => !item.testnet).sort((left, right) => {
        const leftIndex = DEFAULT_SELECTED_NETWORKS.indexOf(left.network);
        const rightIndex = DEFAULT_SELECTED_NETWORKS.indexOf(right.network);
        const leftRank = leftIndex >= 0 ? leftIndex : DEFAULT_SELECTED_NETWORKS.length + networks.indexOf(left);
        const rightRank = rightIndex >= 0 ? rightIndex : DEFAULT_SELECTED_NETWORKS.length + networks.indexOf(right);
        return leftRank - rightRank;
      });
      const requestedSet = new Set(requestedNetworkIds());
      const selectedSet = new Set(
        (requestedSet.size ? requestedSet : DEFAULT_SELECTED_NETWORKS)
          .filter(name => uiNetworks.some(item => item.network === name))
      );
      if (!selectedSet.size && uiNetworks[0]?.network) selectedSet.add(uiNetworks[0].network);
      $("cowNetworkChecks").innerHTML = uiNetworks.map(item => {
        const label = `${item.label || item.network} (${item.chain_id})`;
        const checked = selectedSet.has(item.network) ? " checked" : "";
        return `<label class="network-check"><input type="checkbox" name="cow_network" value="${esc(item.network)}"${checked}>${esc(label)}</label>`;
      }).join("");
      $("refreshMeta").textContent = selectedSet.size ? `CoW 多链 ${Array.from(selectedSet).join(", ")}` : "CoW -";
      renderCowIdentity(selectedCowConfig(primaryNetwork()));
      syncCowScopeToUrl();
    }
    function quoteKey(pairRank, priorityReason, pair) {
      return `${pair || ""}:${pairRank || ""}:${priorityReason || ""}`;
    }
    function quoteMap(data) {
      const map = new Map();
      for (const row of data?.ranking || []) map.set(quoteKey(row.pair_rank, row.priority_reason, row.pair), row);
      return map;
    }
    function supportMap(data) {
      const map = new Map();
      for (const row of data?.routes || data?.precheck?.routes || []) map.set(quoteKey(row.pair_rank, row.priority_reason, row.pair), row);
      return map;
    }
    function copyText(text, label) {
      const value = String(text || "").trim();
      if (!value) {
        $("message").textContent = "没有可复制的内容";
        return Promise.resolve(false);
      }
      const finish = () => {
        $("message").textContent = `${label}已复制`;
        return true;
      };
      if (navigator.clipboard?.writeText) return navigator.clipboard.writeText(value).then(finish);
      const area = document.createElement("textarea");
      area.value = value;
      area.setAttribute("readonly", "readonly");
      area.style.position = "fixed";
      area.style.left = "-9999px";
      document.body.appendChild(area);
      area.select();
      const ok = document.execCommand("copy");
      document.body.removeChild(area);
      if (!ok) throw new Error("浏览器拒绝复制，请手动选择页面文本复制");
      return Promise.resolve(finish());
    }
    function routeText(route, pair) {
      const quote = route.cow_quote || null;
      const support = route.cow_support || quote?.cow_support || null;
      const plan = quote?.binance_execution_plan || route.binance_execution_plan || null;
      const reason = route.priority_reason === "buy_loser_then_gainer" ? "优先" : "逆向";
      return [
        `链路 ${route.route_no || "-"} | ${reason} | ${pair?.pair || "-"}`,
        `路径：${routePath(route)}`,
        `初始数量：${quote ? quoteInitial(quote) : planInitial(plan)}`,
        `意图目标：${planDelta(plan)}`,
        "参考参数：",
        executionParamsText(plan),
        `CoW 支持：${supportText(support)}`,
        `CoW 链上报价路径：${quote ? quotePath(quote) : routePath(route)}`,
        `CoW 实时报价比例：${quote ? quoteRates(quote) : "等待查询"}`,
        `CoW 原始回执：${quote ? quoteFinal(quote) : "等待查询"}`,
        precheckText(quote),
        costText(quote, plan),
        quote && quote.error ? `失败原因：${zhError(quote.error)}` : "",
      ].filter(Boolean).join("\n");
    }
    function pairText(pair) {
      return [
        `#${pair.rank || "-"} ${displaySymbol(pair.x_symbol)} / ${displaySymbol(pair.y_symbol)} | 价差 ${pair.window_spread_percent ?? "-"}`,
        ...((pair.route_results || []).map(route => routeText(route, pair))),
      ].join("\n");
    }
    function quoteRowText(row, index) {
      const plan = row.binance_execution_plan || null;
      return [
        `#${index + 1} ${row.pair || "-"} | ${row.viable ? "可报价" : "失败"}`,
        `路径：${quotePath(row)}`,
        `初始数量：${quoteInitial(row)}`,
        "CoW 链上报价流程：",
        quoteText(row),
        `CoW 实时报价比例：${quoteRates(row)}`,
        `CoW 原始回执：${quoteFinal(row)}`,
        `预测状态：上层盈亏预测关闭`,
        costText(row, plan),
        row.error ? `错误：${zhError(row.error)}` : "",
      ].filter(Boolean).join("\n");
    }
    function marketCopyText(includeQuotes) {
      const data = currentMarketState || {};
      const primaryPanel = networkPanels.get(primaryNetwork()) || {};
      const cowFilter = (primaryPanel.marketState || data).cow_filter || {};
      const lines = [
        "DEX 套利 | CoW SDK 页面信息",
        `复制时间：${new Date().toLocaleString([], {hour12: false})}`,
        `CoW 网络：${selectedNetworkIds().join(", ") || "-"}`,
        `Owner：${selectedCowConfig(primaryNetwork())?.owner_source || "-"} | ${selectedCowConfig(primaryNetwork())?.owner || "-"}`,
        `观察窗口：${Number.isFinite(Number(data.window_seconds)) ? `${Number(data.window_seconds).toFixed(3)}s` : "-"}`,
        `观察时间：${fmtTime(data.observed_at)}`,
        `样本数量：${data.sample_count ?? "-"} / universe ${data.observation_universe_size ?? "-"}`,
        `CoW token 缓存：${cowFilter.token_cache_count ?? "-"} (${cowFilter.token_cache_source || "-"})`,
        `Binance 命中：${cowFilter.supported_symbol_count ?? 0} / 未命中 ${cowFilter.unsupported_symbol_count ?? 0}`,
        `统计过滤：单边涨跌 > ${cowFilter.min_side_change_percent ?? "0.3"}%；价格 >= ${cowFilter.min_token_price_usd ?? "0.01"}U；过滤 ${cowFilter.market_excluded_symbol_count ?? 0} 个`,
        `动态交易门槛：${thresholdBreakdownText(cowFilter.threshold_detail || {})}`,
        `候选路径：${data.pair_count ?? 0}`,
        "",
        tableText(`Binance 原始涨幅前 ${data.top_limit || 50}`, data.raw_top || []),
        "",
        tableText(`Binance 原始跌幅前 ${data.bottom_limit || 50}`, data.raw_bottom || []),
        "",
        cowSupportedText(`所有 CoW 支持代币涨幅前 ${data.cow_supported_overview?.limit || 50}`, data.cow_supported_overview?.top || []),
        "",
        cowSupportedText(`所有 CoW 支持代币跌幅前 ${data.cow_supported_overview?.limit || 50}`, data.cow_supported_overview?.bottom || []),
        "",
        "选中 CoW 网络支持 Top/Bottom 5：",
        ...(selectedNetworkClaims(data.cow_network_claims || currentNetworkClaims()).map(networkClaimText)),
        "",
        "选中链候选路线：",
        ...activeNetworkPanels().map(panel => [
          `网络：${networkLabel(panel.network)} (${panel.network})`,
          ...((pairsWithQuotes(panel.marketState?.pairs || [], panel.quotes, panel.support)).map(pairText)),
        ].join("\n")),
      ];
      if (includeQuotes) {
        lines.push(
          "",
          "CoW 报价验证：",
          "",
          ...activeNetworkPanels().flatMap(panel => (panel.quotes?.ranking || []).map((row, index) => `${networkLabel(panel.network)}\n${quoteRowText(row, index)}`))
        );
      }
      return lines.join("\n");
    }
    function pairsWithQuotes(pairs, quotes, supportData = latestCowSupport) {
      const map = quoteMap(quotes);
      const precheck = supportMap(supportData);
      return (pairs || []).map(pair => ({
        ...pair,
        route_results: (pair.route_results || []).map(route => ({
          ...route,
          cow_support: precheck.get(quoteKey(pair.rank, route.priority_reason, pair.pair)) || null,
          cow_quote: map.get(quoteKey(pair.rank, route.priority_reason, pair.pair)) || null,
        })),
        quote_verified: Boolean(quotes) && (pair.rank || 0) <= Number(quotes.selected_pair_count || 0),
      }));
    }
    function renderPairs() {
      renderNetworkOpportunityCards();
    }
    function selectedPanelPairs(panel) {
      return pairsWithQuotes((panel?.marketState?.pairs || []).slice(0, 1), panel?.quotes || null, panel?.support || null);
    }
    function routeCountText(panel) {
      const state = panel?.marketState || {};
      return `${state.pair_count ?? 0} 组候选，当前展示 ${Math.min(1, Number(state.pair_count || 0))} 组`;
    }
    function networkOpportunityCard(panel) {
      const state = panel?.marketState || {};
      const filter = state.cow_filter || {};
      const claim = selectedNetworkClaimFor(panel.network);
      const pairs = selectedPanelPairs(panel);
      const pair = pairs[0] || null;
      const best = (panel?.quotes?.opportunities || panel?.quotes?.ranking || []).find(row => row?.execution_precheck?.checks_passed) || null;
      const quoteInfo = panel?.quotes
        ? `${panel.quotes.viable_count || 0}/${panel.quotes.route_count || 0} 已报价`
        : panel?.support
          ? `${panel.support.supported_route_count || 0}/${panel.support.route_count || 0} 支持`
          : "等待检查";
      const threshold = filter.threshold_detail || {};
      const highlightSymbols = claimSdkCandidateSymbols(claim);
      const strategyAnalysis = strategyAnalysisHtml(pair?.strategy_analysis || null, filter);
      return `<div class="chain-card" data-chain-card="${esc(panel.network || "")}">
        <div class="chain-card-head">
          <h3>${esc(networkLabel(panel.network))}</h3>
          <span class="context">${esc(panel.network || "-")} | ${esc(shortAddress(selectedCowConfig(panel.network)?.owner))}</span>
        </div>
        <div class="chain-summary">
          <span>最高涨幅：${tokenHtml(state.top?.[0]?.symbol || "-", highlightSymbols)} ${esc(pct(state.top?.[0]?.change_percent))}</span>
          <span>最高跌幅：${tokenHtml(state.bottom?.[0]?.symbol || "-", highlightSymbols)} ${esc(pct(state.bottom?.[0]?.change_percent))}</span>
          <span>${esc(routeCountText(panel))}</span>
          <span>${esc(quoteInfo)}</span>
          <span>SDK 意图 ${esc(best ? quoteSubmissionStatus(best) : "等待报价")}</span>
        </div>
        <div class="context">token ${esc(filter.token_cache_count ?? "-")} | 命中 ${esc(filter.supported_symbol_count ?? 0)} | 过滤 ${esc(filter.market_excluded_symbol_count ?? 0)} | 单边 ${esc(filter.min_side_change_percent ?? "0.3")}% | ${esc(thresholdBreakdownText(threshold))}</div>
        ${strategyAnalysis}
        <div class="chain-market-claim">
          <h4>实时 Top/Bottom 5</h4>
          ${claim ? `<div class="context">来源 ${esc(claim.token_cache_source || "-")} | token ${esc(claim.token_cache_count ?? "-")} | 命中 ${esc(claim.supported_symbol_count ?? 0)} | 候选 ${esc(claim.pair_count ?? 0)}</div>
          <div class="context">${highlightSymbols.size ? '<span class="sdk-candidate-note">蓝色粗体 = 将进入 CoW SDK 报价/交易候选</span>' : '<span class="muted">当前 Top/Bottom 5 未形成可入队候选</span>'}</div>
          <div class="mini-split">
            <div>${rowsTable(claim.top || [], "涨幅", {highlightSymbols})}</div>
            <div>${rowsTable(claim.bottom || [], "跌幅", {highlightSymbols})}</div>
          </div>` : '<div class="muted">等待该网络的 Top/Bottom 5 数据</div>'}
        </div>
        <div class="chain-routes">
          ${pair ? routeGraph(pair.route_results || [], pair, panel.network) : '<div class="muted">当前窗口没有同时满足涨幅、跌幅、价格、价差和 CoW 支持条件的一对代币。</div>'}
        </div>
      </div>`;
    }
    function renderNetworkOpportunityCards() {
      const selected = selectedNetworkIds();
      const panels = selected.map(network => networkPanels.get(network) || {network});
      $("networkOpportunityCards").innerHTML = panels.map(networkOpportunityCard).join("") || '<div class="muted">请选择至少一条 CoW 网络</div>';
    }
    async function quoteRouteGroup(network, pairRank) {
      $("message").textContent = `正在查询 ${networkLabel(network)} 第 ${pairRank || "1"} 组 CoW 链上真实报价`;
      await refreshCowQuotes(network);
    }
    function routeQuoteForButton(button) {
      const panel = networkPanels.get(button.dataset.cowNetwork || "");
      const latest = panel?.quotes || null;
      if (!latest) return null;
      const key = quoteKey(button.dataset.pairRank, button.dataset.priorityReason, button.dataset.pair);
      return quoteMap(latest).get(key) || null;
    }
    function executePrecheck(button) {
      const quote = routeQuoteForButton(button);
      if (!quote) {
        $("message").textContent = "请先点击“查询真实报价”，拿到 CoW 链上报价后再检查是否能进入 SDK 提交。";
        return;
      }
      const check = quote.execution_precheck || null;
      if (!check) {
        $("message").textContent = "缺少后端下单条件检查结果，请重新查询真实报价。";
        return;
      }
      const reasons = (check.reasons || []).join("；") || "-";
      $("message").textContent = `${precheckStatusLabel(check.status)}：${reasons}`;
    }
    function renderMarket(data, network) {
      currentMarketState = data || null;
      $("windowSeconds").textContent = Number.isFinite(Number(data.window_seconds)) ? `${Number(data.window_seconds).toFixed(3)}s` : "-";
      $("observedAt").textContent = fmtTime(data.observed_at);
      $("sampleCount").textContent = data.sample_count ?? "-";
      $("universeSize").textContent = `universe ${data.observation_universe_size ?? 0}`;
      const cowFilter = data.cow_filter || {};
      const resolvedNetwork = network || cowFilter.network || data.cow_network || primaryNetwork();
      const previousPanel = networkPanels.get(resolvedNetwork) || {};
      const previousObservedAt = previousPanel.marketState?.observed_at || null;
      const nextObservedAt = data.observed_at || null;
      const pairChanged = marketPairSignature(previousPanel.marketState) !== marketPairSignature(data);
      const quoteWindowChanged = previousObservedAt && nextObservedAt && previousObservedAt !== nextObservedAt;
      setNetworkPanel(resolvedNetwork, {
        marketState: data,
        loaded: true,
        ...(quoteWindowChanged ? {quotes: null} : {}),
        ...(pairChanged ? {support: null, quotes: null} : {}),
      });
      const panels = selectedNetworkIds().map(item => networkPanels.get(item)).filter(Boolean);
      const totalPairs = panels.reduce((sum, panel) => sum + Number(panel?.marketState?.pair_count || 0), 0);
      const tokenTotal = panels.reduce((sum, panel) => sum + Number(panel?.marketState?.cow_filter?.token_cache_count || 0), 0);
      const supportedTotal = panels.reduce((sum, panel) => sum + Number(panel?.marketState?.cow_filter?.supported_symbol_count || 0), 0);
      const unsupportedTotal = panels.reduce((sum, panel) => sum + Number(panel?.marketState?.cow_filter?.unsupported_symbol_count || 0), 0);
      const threshold = cowFilter.threshold_detail || {};
      const filterAmount = threshold.amount ?? ($("quoteAmountInput").value || "1000");
      $("cowTokenCount").textContent = tokenTotal || cowFilter.token_cache_count || "-";
      $("cowMatchCount").textContent = `Binance 命中 ${supportedTotal || cowFilter.supported_symbol_count || 0} / 未命中 ${unsupportedTotal || cowFilter.unsupported_symbol_count || 0}`;
      $("cowFilterMeta").textContent = `selected ${selectedNetworkIds().join(", ") || "-"} | side > ${cowFilter.min_side_change_percent ?? "0.3"}% | price >= ${cowFilter.min_token_price_usd ?? "0.01"}U | ${thresholdBreakdownText(threshold)} | amount ${filterAmount} | 复盘记录全部候选`;
      $("pairCount").textContent = totalPairs;
      $("bestPair").textContent = totalPairs ? "等待 CoW 报价" : "-";
      $("rawTopBody").innerHTML = (data.raw_top || []).map((row, index) => rowHtml(row, index + 1)).join("") || '<tr><td colspan="6">暂无原始涨幅数据</td></tr>';
      $("rawBottomBody").innerHTML = (data.raw_bottom || []).map((row, index) => rowHtml(row, index + 1)).join("") || '<tr><td colspan="6">暂无原始跌幅数据</td></tr>';
      $("rawTopTitle").textContent = `Binance 原始涨幅前 ${data.top_limit || 50}`;
      $("rawBottomTitle").textContent = `Binance 原始跌幅前 ${data.bottom_limit || 50}`;
      renderCowSupportedOverview(data.cow_supported_overview || null);
      renderNetworkClaims(data.cow_network_claims || []);
      $("pairTitle").textContent = "每条选中链仅展示 1 组 CoW 支持稳定币闭环套利候选路径";
      $("aaveBody").innerHTML = (data.aave_rows || []).map(row => `<tr>
        <td>${esc(displaySymbol(row.symbol))}</td><td class="${cls(row.change_percent)}">${pct(row.change_percent)}</td>
        <td>${num(row.start_price)}</td><td>${num(row.current_price)}</td><td>${row.window_ready ? "已就绪" : "等待中"}</td><td>${row.tracked ? "已跟踪" : "未跟踪"}</td>
      </tr>`).join("") || '<tr><td colspan="6">暂无 Aave 映射资产</td></tr>';
      renderNetworkOpportunityCards();
      renderCowIdentity();
      if (!Array.from(networkPanels.values()).some(panel => panel?.quotes)) {
        $("notionalUsd").textContent = "-";
        $("quoteState").textContent = "等待 CoW SDK 报价";
      }
      $("refreshMeta").textContent = `更新 ${fmtTime(data.observed_at)} | ${data.market_state_source || data.price_source || "-"}${data.fallback_reason ? ` | ${zhError(data.fallback_reason)}` : ""}`;
    }
    function renderCowQuoteTable() {
      const opportunities = activeNetworkPanels().flatMap(panel =>
        (panel?.quotes?.ranking || [])
          .map(row => ({...row, display_network: panel.network}))
      );
      const rows = opportunities.map((row, index) => `<tr>
        <td>${index + 1}</td><td>${esc(networkLabel(row.display_network))}<br>${esc(row.pair || "-")}</td><td>${esc(quoteInitial(row))}</td>
        <td>${quoteFlow(row)}</td><td>${esc(quoteRates(row))}</td><td>${esc(quoteFinal(row))}</td>
        <td>预测关闭</td><td>${esc(quoteSubmissionStatus(row))}</td>
      </tr>`).join("");
      $("cowQuoteBody").innerHTML = rows || '<tr><td colspan="8">暂无可展示的 CoW 报价；候选会按意图模式继续自动检查。</td></tr>';
      const best = opportunities[0] || null;
      $("notionalUsd").textContent = best ? "预测关闭" : "-";
      $("notionalUsd").title = best ? `${networkLabel(best.display_network)} | ${best.pair || "-"} | ${(best.path || []).join(" -> ")}` : "";
      const quotedRoutes = activeNetworkPanels().reduce((sum, panel) => sum + Number(panel?.quotes?.route_count || 0), 0);
      $("quoteState").innerHTML = `<span class="badge">CoW 多链</span><span class="muted">${esc(opportunities.length)} 个机会 | ${esc(quotedRoutes)} 条路由已报价</span>`;
    }
    const clientCowAttemptCategory = row => {
      if (row?.review_category) return row.review_category;
      const sdkStatus = historyCowSdkStatus(row);
      const sdkSubmitted = historyCowSdkSubmitted(row);
      const state = String(row?.state || "").toLowerCase();
      const phase = String(row?.execution_phase || "").toLowerCase();
      if (sdkSuccessStates.has(sdkStatus) || ["confirmed_success", "settled", "executed", "execution_success", "success"].includes(state) || ["confirmed_success", "settled", "execution_success"].includes(phase)) return "execution_success";
      if (sdkSubmitted && sdkFailureStates.has(sdkStatus)) return "execution_failed";
      return "not_executable";
    };
    const cowAttemptGroups = data => {
      const apiGroups = data?.groups || {};
      const groups = {
        not_executable: [...(apiGroups.not_executable?.attempts || [])],
        execution_failed: [...(apiGroups.execution_failed?.attempts || [])],
        execution_success: [...(apiGroups.execution_success?.attempts || [])],
      };
      if (!Object.values(groups).some(rows => rows.length)) {
        for (const row of data?.attempts || []) {
          const category = clientCowAttemptCategory(row);
          if (groups[category]) groups[category].push(row);
        }
      }
      return groups;
    };
    const cowAttemptReasonTop = rows => {
      const counts = new Map();
      for (const row of rows || []) {
        const reasons = historyReasonList(row);
        for (const reason of reasons.length ? reasons : [historyAttemptState(row)]) {
          counts.set(reason, (counts.get(reason) || 0) + 1);
        }
      }
      return Array.from(counts.entries()).sort((a, b) => b[1] - a[1])[0] || null;
    };
    const cowAttemptGroupNote = (category, rows) => {
      const meta = COW_ATTEMPT_GROUP_META[category];
      if (!rows.length) return `${meta.retention} | ${meta.empty}`;
      const topReason = cowAttemptReasonTop(rows);
      const latest = rows[0] || {};
      const latestLine = `${fmtTime(latest.created_at)} | ${networkLabel(latest.network) || latest.network || "-"} | ${latest.pair || "-"} | ${historyAttemptState(latest)} | ${historyQuoteDelta(latest)}`;
      if (category === "execution_success") return `${meta.retention} | 成功 ${rows.length} 条 | 最近一条 ${latestLine}`;
      if (category === "execution_failed") return `${meta.retention} | SDK 失败 ${rows.length} 条 | 主要失败项 ${topReason ? `${topReason[0]} (${topReason[1]})` : "-"} | 最近一条 ${latestLine}`;
      return `${meta.retention} | 当前等待 ${rows.length} 条 | 当前等待状态 ${topReason ? `${topReason[0]} (${topReason[1]})` : "-"} | 最近一条 ${latestLine}`;
    };
    const cowAttemptRowHtml = row => `<tr title="${esc(historyReviewText(row))}">
      <td>${esc(fmtTime(row.created_at))}</td>
      <td>${esc(row.network || "-")}</td>
      <td>${esc(row.pair || "-")}</td>
      <td>${esc(historyRoutePath(row))}</td>
      <td>${esc(historyAttemptState(row))}</td>
      <td>${historyMarketHtml(row)}</td>
      <td>${historyQuoteHtml(row)}</td>
      <td>${historyCostQuoteHtml(row)}</td>
      <td>${esc(historyReasons(row))}</td>
    </tr>`;
    function renderCowAttemptGroup(category, rows) {
      const meta = COW_ATTEMPT_GROUP_META[category];
      const total = rows.length;
      const pageCount = Math.max(1, Math.ceil(total / COW_ATTEMPT_PAGE_SIZE));
      cowAttemptHistoryPages[category] = Math.min(Math.max(0, cowAttemptHistoryPages[category] || 0), pageCount - 1);
      const page = cowAttemptHistoryPages[category];
      const pageRows = rows.slice(page * COW_ATTEMPT_PAGE_SIZE, (page + 1) * COW_ATTEMPT_PAGE_SIZE);
      $(meta.noteId).textContent = cowAttemptGroupNote(category, rows);
      $(meta.bodyId).innerHTML = pageRows.map(cowAttemptRowHtml).join("") || `<tr><td colspan="9">${esc(meta.empty)}</td></tr>`;
      $(meta.pagerId).innerHTML = `<button data-cow-attempt-page="${esc(category)}" data-page-delta="-1" ${page <= 0 ? "disabled" : ""}>上一页</button><span>${total ? page + 1 : 0}/${total ? pageCount : 0} | ${total} 条</span><button data-cow-attempt-page="${esc(category)}" data-page-delta="1" ${page >= pageCount - 1 || !total ? "disabled" : ""}>下一页</button>`;
    }
    function renderCowAttemptHistory(data) {
      latestCowAttemptHistoryPayload = data || {};
      const groups = cowAttemptGroups(data || {});
      const rows = Object.values(groups).flat().sort((left, right) => String(right.created_at || right.observed_at || "").localeCompare(String(left.created_at || left.observed_at || "")));
      const review = cowAttemptReview(rows);
      $("cowAttemptHistorySource").textContent = `来源 ${data?.source || "-"} | 共 ${rows.length} 条`;
      $("cowAttemptReviewConclusion").textContent = review.conclusion;
      $("cowAttemptReviewMetrics").innerHTML = [
        `等待/暂停 ${groups.not_executable.length}`,
        `SDK 失败 ${groups.execution_failed.length}`,
        `SDK 成功 ${groups.execution_success.length}`,
        ...(review.metrics || []),
      ].map(item => `<span>${esc(item)}</span>`).join("");
      renderCowAttemptGroup("not_executable", groups.not_executable);
      renderCowAttemptGroup("execution_failed", groups.execution_failed);
      renderCowAttemptGroup("execution_success", groups.execution_success);
      $("cowAttemptHistoryBody").innerHTML = rows.slice(0, COW_ATTEMPT_PAGE_SIZE).map(cowAttemptRowHtml).join("");
    }
    async function refreshCowAttemptHistory() {
      try {
        const networks = selectedNetworkIds();
        const suffix = networks.length ? `&cow_networks=${encodeURIComponent(networks.join(","))}` : "";
        renderCowAttemptHistory(await api(`/api/binance-market/cow-execution-attempts?limit=300${suffix}`));
      } catch (error) {
        $("cowAttemptHistoryBody").innerHTML = `<tr><td colspan="9">${esc(zhError(error.message))}</td></tr>`;
        for (const meta of Object.values(COW_ATTEMPT_GROUP_META)) {
          $(meta.bodyId).innerHTML = `<tr><td colspan="9">${esc(zhError(error.message))}</td></tr>`;
          $(meta.noteId).textContent = "加载失败";
          $(meta.pagerId).innerHTML = "";
        }
      }
    }
    const queueQuoteContainer = item => item?.result?.quote || null;
    const queueQuoteResult = item => {
      const container = queueQuoteContainer(item);
      if (container?.result && typeof container.result === "object") return container.result;
      if (container?.quote && typeof container.quote === "object") return container.quote;
      return container && typeof container === "object" ? container : {};
    };
    const queuePrecheck = item => {
      const container = queueQuoteContainer(item);
      const result = queueQuoteResult(item);
      const ranked = container?.payload?.ranking?.[0]?.execution_precheck;
      return result.execution_precheck || result.precheck || ranked || item?.candidate?.precheck || item?.candidate?.quote?.execution_precheck || {};
    };
    const queueReasonList = item => {
      const precheck = queuePrecheck(item);
      const result = queueQuoteResult(item);
      const reasons = [
        ...(Array.isArray(precheck.reasons) ? precheck.reasons : []),
        ...(Array.isArray(precheck.quote_probe_notes) ? precheck.quote_probe_notes : []),
        precheck.quote_error_display,
        result.error,
        item.last_error,
      ].filter(Boolean).map(value => /[\u4e00-\u9fff]/.test(String(value)) ? String(value) : `原因（${String(value)}）`);
      const causeCounts = precheck.blocking_cause_counts || {};
      for (const [cause, count] of Object.entries(causeCounts)) {
        if (cause) reasons.push(`${cause}${count ? ` x${count}` : ""}`);
      }
      return [...new Set(reasons)];
    };
    const queueStatusDetail = item => {
      const precheck = queuePrecheck(item);
      const status = String(precheck.status || "").trim();
      const statusText = status && status !== item.status ? `预检：${precheckStatusLabel(status)}` : "";
      const reasons = queueReasonList(item);
      return [statusText, ...reasons].filter(Boolean).join("；") || "-";
    };
    const queueDisplayStatus = item => {
      const precheck = queuePrecheck(item);
      const queueStatus = precheckStatusLabel(item.status);
      const precheckStatus = precheck.status ? precheckStatusLabel(precheck.status) : "";
      if (precheckStatus && precheckStatus !== queueStatus) return `${queueStatus} / ${precheckStatus}`;
      return queueStatus || "-";
    };
    function renderCowCandidateQueue(data) {
      const daemon = data?.daemon || {};
      const queue = daemon.queue || {};
      const counts = queue.counts || {};
      $("cowQueueStatus").textContent = [
        daemon.paused ? "交易关闭，仅显示队列" : (daemon.running ? "守护线程运行中" : (daemon.enabled ? "守护线程等待中" : "守护线程已关闭")),
        `队列 ${queue.size || 0}`,
        `待处理 ${counts.pending || 0}`,
        `处理中 ${counts.processing || 0}`,
        `重试等待 ${counts.retry_wait || 0}`,
        `已准备未提交 ${counts.ready_not_submitted || 0}`,
        `开关关闭 ${counts.order_submission_switch_off || counts.order_submission_disabled || 0}`,
        `缺 SDK ${counts.cow_flashloan_sdk_install_required || 0}`,
        `缺私钥 ${counts.order_submission_signer_not_ready || 0}`,
        `提交暂停 ${counts.submission_paused || 0}`,
        `可提交 ${counts.ready_to_submit || 0}`,
        `已提交 ${counts.submitted_success || 0}`,
        `提交失败 ${counts.submission_failed || 0}`,
        `已报价 ${counts.quoted || 0}`,
        `已阻断 ${counts.blocked || 0}`,
        `失败 ${counts.failed || 0}`,
      ].join(" | ");
      const rows = (data?.items || []).map((item, index) => `<tr>
        <td>${index + 1}</td>
        <td>${esc(item.network || "-")}</td>
        <td>${esc(item.pair || "-")}</td>
        <td>${(item.route_path || []).map(symbol => symbolKey(symbol) === "USDC" ? esc(displaySymbol(symbol)) : tokenHtml(symbol, new Set([symbolKey(symbol)]))).join(" -> ") || "-"}</td>
        <td>${esc(queueDisplayStatus(item))}</td>
        <td title="${esc(queueStatusDetail(item))}">${esc(queueStatusDetail(item))}</td>
        <td>${esc(item.priority_score || item.profit_hint || "-")}</td>
        <td>${esc(item.attempts || 0)}</td>
      </tr>`).join("");
      $("cowQueueBody").innerHTML = rows || '<tr><td colspan="8">暂无排队的 CoW 候选</td></tr>';
    }
    async function refreshCowCandidateQueue() {
      try {
        const networks = selectedNetworkIds();
        const suffix = networks.length ? `&cow_networks=${encodeURIComponent(networks.join(","))}` : "";
        renderCowCandidateQueue(await api(`/api/binance-market/cow-candidate-queue?limit=100${suffix}`));
      } catch (error) {
        $("cowQueueBody").innerHTML = `<tr><td colspan="8">${esc(zhError(error.message))}</td></tr>`;
      }
    }
    function renderCowSubmissionPause(data) {
      latestCowSubmissionPausePayload = data || null;
      const paused = data?.paused !== false;
      $("cowSubmissionEnabledCheckbox").checked = !paused;
      $("cowSubmissionEnabledCheckbox").disabled = false;
      $("cowTradingSwitchLabel").classList.toggle("enabled", !paused);
      $("cowTradingSwitchLabel").classList.toggle("disabled", paused);
      $("cowSubmissionPauseMessage").textContent = paused
        ? "自动交易关闭：报价和界面照常显示，不会提交交易，也不会写库"
        : "自动交易开启：允许提交前检查和交易提交";
      $("quoteBtn").disabled = false;
      renderNetworkOpportunityCards();
    }
    function cowAutomationPaused() {
      return latestCowSubmissionPausePayload?.paused !== false;
    }
    async function refreshCowSubmissionPause() {
      try {
        renderCowSubmissionPause(await api("/api/binance-market/cow-submission-pause"));
      } catch (error) {
        if (latestCowSubmissionPausePayload?.paused !== false) {
          latestCowSubmissionPausePayload = {paused: true, pause_reason: "pause_status_unavailable"};
          $("cowSubmissionEnabledCheckbox").checked = false;
          $("cowTradingSwitchLabel").classList.toggle("enabled", false);
          $("cowTradingSwitchLabel").classList.toggle("disabled", true);
        }
        $("quoteBtn").disabled = false;
        renderNetworkOpportunityCards();
        $("cowSubmissionPauseMessage").textContent = `开关状态读取失败，保留当前状态：${zhError(error.message)}`;
      }
    }
    async function setCowSubmissionPause(paused, reason = null) {
      const checkbox = $("cowSubmissionEnabledCheckbox");
      checkbox.disabled = true;
      try {
        renderCowSubmissionPause(await api("/api/binance-market/cow-submission-pause", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({paused, reason}),
        }));
      } catch (error) {
        $("cowSubmissionPauseMessage").textContent = zhError(error.message);
        await refreshCowSubmissionPause();
      } finally {
        checkbox.disabled = false;
      }
    }
    async function updateCowSubmissionFromCheckbox() {
      const enabled = Boolean($("cowSubmissionEnabledCheckbox").checked);
      return setCowSubmissionPause(!enabled, enabled ? null : "manual_automation_pause");
    }
    function renderCowQuotes(data, network) {
      latestCowQuotes = data || null;
      latestCowSupport = data?.precheck || latestCowSupport;
      if (data.market_state) currentMarketState = data.market_state;
      const resolvedNetwork = network || data.cow_network || primaryNetwork();
      setNetworkPanel(resolvedNetwork, {
        marketState: data.market_state || networkPanels.get(resolvedNetwork)?.marketState,
        quotes: data || null,
        support: data?.precheck || networkPanels.get(resolvedNetwork)?.support || null,
      });
      renderNetworkOpportunityCards();
      renderCowQuoteTable();
      refreshCowAttemptHistory();
      $("cowQuoteContext").textContent = `CoW ${selectedNetworkIds().join(", ") || "-"} | 默认账户`;
      $("cowQuoteContext").title = selectedNetworkIds().map(item => `${item}: ${selectedCowConfig(item)?.owner || "-"}`).join("\n");
    }
    async function loadCowConfig() {
      try {
        renderCowConfig(await api("/api/binance-market/cow-config"));
      } catch (error) {
        $("message").textContent = zhError(error.message);
      }
    }
    async function refresh(force = false) {
      if (marketRefreshInFlight && !force) return;
      const requestId = ++marketRefreshRequestId;
      marketRefreshInFlight = true;
      try {
        $("message").textContent = "";
        const sideLimit = $("sideLimitSelect").value || "50";
        const networks = selectedNetworkIds();
        const minSpread = minSpreadValue();
        const amount = $("quoteAmountInput").value || "1000";
        if (!networks.length) {
          renderNetworkOpportunityCards();
          throw new Error("请至少选择一条 CoW 网络");
        }
        if (!Number.isFinite(Number(amount)) || Number(amount) <= 0) throw new Error("金额必须大于 0");
        const data = await api(`/api/binance-market/states?side_limit=${encodeURIComponent(sideLimit)}&pair_side_limit=1&cow_networks=${encodeURIComponent(networks.join(","))}&min_spread_percent=${encodeURIComponent(minSpread)}&amount=${encodeURIComponent(amount)}`);
        if (requestId !== marketRefreshRequestId) return;
        for (const network of networks) {
          const state = data.states?.[network];
          if (state) renderMarket({...state, cow_network_claims: data.cow_network_claims || [], cow_supported_overview: data.cow_supported_overview || null}, network);
        }
        refreshCowAttemptHistory();
        refreshCowCandidateQueue();
        autoRefreshCowQuotes(data).catch(error => { $("message").textContent = zhError(error.message); });
      } catch (error) {
        if (requestId !== marketRefreshRequestId) return;
        $("message").textContent = zhError(error.message);
      } finally {
        if (requestId === marketRefreshRequestId) {
          marketRefreshInFlight = false;
        }
      }
    }
    async function refreshCowQuotes(network = null) {
      if (cowQuoteRefreshInFlight) return;
      cowQuoteRefreshInFlight = true;
      try {
        $("message").textContent = "";
        const sideLimit = $("sideLimitSelect").value || "50";
        const quoteLimit = quoteLimitValue();
        const networks = network ? [network] : selectedNetworkIds();
        const amount = $("quoteAmountInput").value || "1000";
        const minSpread = minSpreadValue();
        if (!networks.length) throw new Error("请至少选择一条 CoW 网络");
        if (!Number.isFinite(Number(amount)) || Number(amount) <= 0) throw new Error("金额必须大于 0");
        await Promise.all(networks.map(async cowNetwork => {
          const data = await api(`/api/binance-market/cow-quotes?side_limit=${encodeURIComponent(sideLimit)}&pair_side_limit=1&quote_limit=${encodeURIComponent(quoteLimit)}&amount=${encodeURIComponent(amount)}&cow_network=${encodeURIComponent(cowNetwork)}&min_spread_percent=${encodeURIComponent(minSpread)}&quote_timeout_seconds=8`);
          renderCowQuotes(data, cowNetwork);
        }));
      } catch (error) {
        $("message").textContent = zhError(error.message);
      } finally {
        cowQuoteRefreshInFlight = false;
      }
    }
    async function autoRefreshCowQuotes(data) {
      if (!hasQuoteCandidates(data)) return;
      const now = Date.now();
      const key = marketQuoteCandidateSignature(data);
      if (key === lastAutoCowQuoteKey) return;
      if (now - lastAutoCowQuoteAt < AUTO_COW_QUOTE_MIN_INTERVAL_MS) return;
      lastAutoCowQuoteKey = key;
      lastAutoCowQuoteAt = now;
      $("quoteState").innerHTML = `<span class="badge">CoW 自动报价</span><span class="muted">正在为显示的候选获取真实报价</span>`;
      await refreshCowQuotes();
    }
    async function refreshCowSupport(network = null) {
      try {
        $("message").textContent = "";
        const sideLimit = $("sideLimitSelect").value || "50";
        const quoteLimit = quoteLimitValue();
        const networks = network ? [network] : selectedNetworkIds();
        const amount = $("quoteAmountInput").value || "1000";
        const minSpread = minSpreadValue();
        if (!networks.length) throw new Error("请至少选择一条 CoW 网络");
        if (!Number.isFinite(Number(amount)) || Number(amount) <= 0) throw new Error("金额必须大于 0");
        await Promise.all(networks.map(async cowNetwork => {
          const data = await api(`/api/binance-market/cow-support?side_limit=${encodeURIComponent(sideLimit)}&pair_side_limit=1&quote_limit=${encodeURIComponent(quoteLimit)}&amount=${encodeURIComponent(amount)}&cow_network=${encodeURIComponent(cowNetwork)}&min_spread_percent=${encodeURIComponent(minSpread)}`);
          latestCowSupport = data || null;
          if (data.market_state) currentMarketState = data.market_state;
          setNetworkPanel(cowNetwork, {
            marketState: data.market_state || networkPanels.get(cowNetwork)?.marketState,
            support: data || null,
          });
        }));
        const supportedRoutes = activeNetworkPanels().reduce((sum, panel) => sum + Number(panel?.support?.supported_route_count || 0), 0);
        const routeCount = activeNetworkPanels().reduce((sum, panel) => sum + Number(panel?.support?.route_count || 0), 0);
        $("quoteState").innerHTML = `<span class="badge">CoW 多链</span><span class="muted">${esc(supportedRoutes)} / ${esc(routeCount)} 条路由支持</span>`;
      } catch (error) {
        $("message").textContent = zhError(error.message);
      }
    }
    async function refreshCowTokens() {
      try {
        const networks = (cowConfig?.networks || []).filter(item => !item.testnet).map(item => item.network);
        const targets = networks.length ? networks : selectedNetworkIds();
        $("message").textContent = `正在刷新 ${targets.length} 个 CoW 主网支持代币`;
        const refreshed = [];
        for (const network of targets) {
          const tokenPath = `/api/binance-market/cow-tokens/refresh?cow_network=${encodeURIComponent(network)}`;
          const response = await fetch(window.ChainSelector?.withSelection ? window.ChainSelector.withSelection(tokenPath) : tokenPath, {method: "POST", headers: {"Accept": "application/json"}});
          const data = await response.json();
          if (!response.ok) throw new Error(data.error || `请求失败（HTTP ${response.status}）`);
          refreshed.push(`${data.network}:${data.token_count || 0}`);
        }
        latestCowQuotes = null;
        latestCowSupport = null;
        $("message").textContent = `CoW 主网支持代币已刷新：${refreshed.join(" | ")}`;
        await refresh();
        await refreshCowSupport();
      } catch (error) {
        $("message").textContent = zhError(error.message);
      }
    }
    $("refreshBtn").onclick = refresh;
    $("refreshTokensBtn").onclick = refreshCowTokens;
    $("supportBtn").onclick = refreshCowSupport;
    $("quoteBtn").onclick = refreshCowQuotes;
    $("cowSubmissionEnabledCheckbox").onchange = updateCowSubmissionFromCheckbox;
    $("copySummaryBtn").onclick = () => copyText(marketCopyText(true), "页面信息").catch(error => { $("message").textContent = zhError(error.message); });
    $("copyQuotesBtn").onclick = () => copyText(activeNetworkPanels().some(panel => panel?.quotes) ? marketCopyText(true) : marketCopyText(false), activeNetworkPanels().some(panel => panel?.quotes) ? "CoW 报价" : "页面信息").catch(error => { $("message").textContent = zhError(error.message); });
    $("cowExecutionReviewSection").onclick = event => {
      const button = event.target.closest("button[data-cow-attempt-page]");
      if (!button) return;
      const category = button.dataset.cowAttemptPage;
      cowAttemptHistoryPages[category] = (cowAttemptHistoryPages[category] || 0) + Number(button.dataset.pageDelta || 0);
      renderCowAttemptHistory(latestCowAttemptHistoryPayload || {});
    };
    window.addEventListener("chainselectionchanged", event => {
      latestCowQuotes = null;
      latestCowSupport = null;
      lastAutoCowQuoteKey = "";
      lastAutoCowQuoteAt = 0;
      networkPanels.clear();
      refresh(true).then(() => refreshCowSupport()).catch(error => { $("message").textContent = zhError(error.message); });
    });
    $("networkOpportunityCards").onclick = event => {
      const button = event.target.closest("button[data-route-action]");
      if (!button) return;
      if (button.dataset.routeAction === "quote") {
        quoteRouteGroup(button.dataset.cowNetwork, button.dataset.pairRank).catch(error => { $("message").textContent = zhError(error.message); });
      } else if (button.dataset.routeAction === "execute-check") {
        executePrecheck(button);
      }
    };
    $("cowNetworkChecks").onchange = () => {
      latestCowQuotes = null;
      latestCowSupport = null;
      for (const network of Array.from(networkPanels.keys())) {
        if (!selectedNetworkIds().includes(network)) networkPanels.delete(network);
      }
      renderCowIdentity();
      renderNetworkOpportunityCards();
      renderNetworkClaims(currentNetworkClaims());
      syncCowScopeToUrl();
      refresh().then(refreshCowSupport);
    };
    $("sideLimitSelect").onchange = () => { latestCowQuotes = null; latestCowSupport = null; networkPanels.clear(); refresh().then(refreshCowSupport); };
    $("quoteLimitSelect").onchange = refreshCowSupport;
    $("quoteAmountInput").onchange = refreshCowSupport;
    $("minSpreadInput").onchange = () => { latestCowQuotes = null; latestCowSupport = null; networkPanels.clear(); refresh().then(refreshCowSupport); };
    async function bootDexArbitragePanel() {
      await refreshCowSubmissionPause();
      await loadCowConfig();
      await refresh();
      await refreshCowSupport();
    }
    bootDexArbitragePanel().catch(error => { $("message").textContent = zhError(error.message); });
    refreshCowAttemptHistory();
    refreshCowCandidateQueue();
    setInterval(refresh, 1000);
    setInterval(refreshCowCandidateQueue, 5000);
    setInterval(refreshCowSubmissionPause, 5000);
