    const $ = id => document.getElementById(id);
    const esc = value => String(value ?? "-").replace(/[&<>"']/g, ch => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));
    const zhError = value => {
      const text = String(value || "请求失败");
      if (/^HTTP \d+$/.test(text)) return `请求失败（${text}）`;
      if (text === "amount must be positive") return "金额必须大于 0";
      if (/[\u4e00-\u9fff]/.test(text)) return text;
      return `接口错误（${text}）`;
    };
    let cowConfig = null;
    let currentMarketState = null;
    let latestCowQuotes = null;
    let latestCowSupport = null;
    let marketRefreshInFlight = false;
    let marketRefreshRequestId = 0;
    let cowQuoteRefreshInFlight = false;
    let lastAutoCowQuoteKey = "";
    let lastAutoCowQuoteAt = 0;
    const AUTO_COW_QUOTE_MIN_INTERVAL_MS = 1000;
    const DEFAULT_SELECTED_NETWORKS = ["bnb"];
    const networkPanels = new Map();
    const COW_ATTEMPT_PAGE_SIZE = 10;
    const cowAttemptHistoryPages = {not_executable: 0, execution_failed: 0, execution_success: 0};
    let latestCowAttemptHistoryPayload = null;
    let latestCowSubmissionPausePayload = {paused: false, pause_reason: null};
    const COW_ATTEMPT_GROUP_META = {
      not_executable: {
        label: "报价等待 / 报价失败 / 提交暂停 / SDK 等待",
        bodyId: "cowAttemptNotExecutableBody",
        pagerId: "cowAttemptNotExecutablePager",
        noteId: "cowAttemptNotExecutableNote",
        empty: "暂无报价等待、报价失败、提交暂停或 SDK 等待记录",
        retention: "分析 1 天 | 存储当前与前一自然日",
      },
      execution_failed: {
        label: "SDK 执行失败",
        bodyId: "cowAttemptFailedBody",
        pagerId: "cowAttemptFailedPager",
        noteId: "cowAttemptFailedNote",
        empty: "暂无 SDK 确认失败记录",
        retention: "分析 7 天 | 存储当前与前一周",
      },
      execution_success: {
        label: "SDK 执行成功",
        bodyId: "cowAttemptSuccessBody",
        pagerId: "cowAttemptSuccessPager",
        noteId: "cowAttemptSuccessNote",
        empty: "暂无 SDK 确认成功记录",
        retention: "长期保留",
      },
    };

    const api = async (path, options = {}) => {
      const requestPath = window.ChainSelector?.withSelection ? window.ChainSelector.withSelection(path) : path;
      const response = await fetch(requestPath, {
        ...options,
        headers: {"Accept": "application/json", ...(options.headers || {})},
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || `请求失败（HTTP ${response.status}）`);
      return data;
    };
    const fmtTime = value => value ? new Date(value).toLocaleString([], {hour12: false}) : "-";
    const stripZeros = text => String(text).replace(/(\.\d*?[1-9])0+$/, "$1").replace(/\.0+$/, "");
    const priceDigits = value => {
      const num = Math.abs(Number(value));
      if (!Number.isFinite(num)) return 6;
      if (num === 0) return 6;
      if (num >= 1) return 6;
      if (num >= 0.01) return 8;
      if (num >= 0.0001) return 10;
      return Math.min(14, Math.max(8, Math.ceil(-Math.log10(num)) + 4));
    };
    const formatPrice = value => {
      const parsed = Number(value);
      if (!Number.isFinite(parsed)) return "-";
      return stripZeros(parsed.toFixed(priceDigits(parsed)));
    };
    const num = value => formatPrice(value);
    const pct = value => Number.isFinite(Number(value)) ? `${Number(value) >= 0 ? "+" : ""}${Number(value).toFixed(5)}%` : "-";
    const displaySymbol = symbol => {
      const value = String(symbol || "-").toUpperCase();
      if (value === "USDC" || value === "USDT") return value;
      return value.replace(/(USDT|USDC)$/i, "") || value;
    };
    const cls = value => Number.isFinite(Number(value)) ? (Number(value) >= 0 ? "positive" : "negative") : "";
    const routePath = route => (route.route || route.route_symbols || []).map(displaySymbol).join(" -> ") || "-";
    const selectedNetworkIds = () => Array.from(document.querySelectorAll("input[name='cow_network']:checked")).map(item => item.value);
    const requestedNetworkIds = () => {
      const query = new URLSearchParams(window.location.search);
      return (query.get("cow_networks") || query.get("cow_network") || "")
        .split(",")
        .map(item => item.trim().toLowerCase())
        .filter(Boolean);
    };
    const syncCowScopeToUrl = () => {
      const networks = selectedNetworkIds();
      const next = new URL(window.location.href);
      next.searchParams.set("workflow", "dex_arbitrage");
      next.searchParams.set("execution_mode", "cow");
      next.searchParams.set("scope", networks.length > 1 ? "multi_chain" : "single_chain");
      if (networks.length) next.searchParams.set("cow_networks", networks.join(","));
      else next.searchParams.delete("cow_networks");
      next.searchParams.delete("cow_network");
      window.history.replaceState({}, "", next.pathname + (next.search ? next.search : ""));
      window.WorkflowContext?.init?.().catch(error => { console.error("workflow context:", error); });
    };
    const activeNetworkPanels = () => selectedNetworkIds().map(network => networkPanels.get(network)).filter(Boolean);
    const primaryNetwork = () => selectedNetworkIds()[0] || cowConfig?.default_network || "bnb";
    const selectedNetworkClaims = claims => {
      const selected = new Set(selectedNetworkIds());
      return (claims || []).filter(claim => selected.has(claim.network));
    };
    const currentNetworkClaims = () => {
      if (Array.isArray(currentMarketState?.cow_network_claims)) return currentMarketState.cow_network_claims;
      for (const panel of activeNetworkPanels()) {
        if (Array.isArray(panel?.marketState?.cow_network_claims)) return panel.marketState.cow_network_claims;
      }
      return [];
    };
    const selectedNetworkClaimFor = network => selectedNetworkClaims(currentNetworkClaims()).find(claim => claim.network === network) || null;
    const quoteLimitValue = () => {
      const value = Number($("quoteLimitSelect")?.value || 1);
      return Number.isFinite(value) && value > 0 ? String(Math.min(9, Math.max(1, Math.floor(value)))) : "1";
    };
    const selectedCowConfig = network => cowConfig?.networks?.find(item => item.network === (network || primaryNetwork())) || null;
    const networkLabel = network => selectedCowConfig(network)?.label || network || "-";
    const setNetworkPanel = (network, patch) => {
      const previous = networkPanels.get(network) || {};
      networkPanels.set(network, {...previous, ...patch, network});
      renderNetworkOpportunityCards();
    };
    const marketPairSignature = state => (state?.pairs || []).slice(0, 1).map(pair =>
      `${pair.pair || ""}:${(pair.route_results || []).map(route => (route.route || []).join(">")).join("|")}`
    ).join(";");
    const marketQuoteCandidateSignature = data => {
      const networks = selectedNetworkIds();
      const states = data?.states || {};
      const claims = data?.cow_network_claims || [];
      const stateParts = networks.map(network => {
        const state = states[network] || networkPanels.get(network)?.marketState || {};
        return `${network}:${state.observed_at || data?.observed_at || ""}:${state.pair_count || 0}:${marketPairSignature(state)}`;
      });
      const claimParts = claims
        .filter(claim => networks.includes(claim.network))
        .map(claim => `${claim.network}:${claim.pair_count || 0}:${(claim.top || []).map(row => row.symbol).join(",")}:${(claim.bottom || []).map(row => row.symbol).join(",")}`);
      return [...stateParts, ...claimParts, `amount:${$("quoteAmountInput")?.value || "1000"}`, `limit:${quoteLimitValue()}`].join(";");
    };
    const hasQuoteCandidates = data => {
      const networks = selectedNetworkIds();
      const states = data?.states || {};
      if (networks.some(network => Number((states[network] || networkPanels.get(network)?.marketState || {}).pair_count || 0) > 0)) return true;
      return (data?.cow_network_claims || []).some(claim => networks.includes(claim.network) && Number(claim.pair_count || 0) > 0);
    };
    const shortAddress = value => {
      const text = String(value || "").trim();
      return text.startsWith("0x") && text.length === 42 ? `${text.slice(0, 6)}...${text.slice(-4)}` : (text || "-");
    };
    const quotePath = quote => (quote?.path || []).map(displaySymbol).join(" -> ") || "-";
    const quoteInitial = quote => quote ? `${quote.input_amount || "-"} ${displaySymbol(quote.input_symbol || "")}` : "-";
    const quoteFinal = quote => quote ? `${quote.final_amount || "-"} ${displaySymbol(quote.final_symbol || "")}（原始查询，不作盈亏预测）` : "-";
    const quoteVsPlan = (quote, plan) => {
      if (!quote || !plan?.available) return "-";
      const finalAmount = Number(quote.final_amount);
      const targetAmount = Number(plan.final_target_amount);
      if (!Number.isFinite(finalAmount) || !Number.isFinite(targetAmount) || targetAmount === 0) return "-";
      const diff = finalAmount - targetAmount;
      const pctDiff = diff / targetAmount * 100;
      return "上层预测已关闭";
    };
    const minSpreadValue = () => {
      const value = Number($("minSpreadInput").value || "0");
      return Number.isFinite(value) && value >= 0 ? String(value) : "0";
    };
    const usdcValueText = value => Number.isFinite(value) ? `${value.toFixed(6)} USDC` : "-";
    const hopValueTrace = (hop, costBasisUsdc) => {
      const basis = Number(costBasisUsdc);
      if (symbolKey(hop?.buy_symbol) !== "USDC") {
        return {
          text: ` | 链内成本基准 ${usdcValueText(basis)} | 未回到USDC，不做USDC折价`,
          value: basis,
        };
      }
      const realized = Number(hop?.buy_amount);
      const delta = Number.isFinite(realized) && Number.isFinite(basis) ? realized - basis : null;
      return {
        text: ` | 已回到USDC ${usdcValueText(realized)}${Number.isFinite(delta) ? ` | 已实现差额 ${delta >= 0 ? "+" : ""}${delta.toFixed(6)} USDC` : ""}`,
        value: Number.isFinite(realized) ? realized : basis,
      };
    };
    const hopLinesWithValueTrace = quote => {
      let previousUsdcValue = Number(quote?.input_amount);
      return (quote?.hops || []).map(hop => {
        const trace = hopValueTrace(hop, previousUsdcValue);
        previousUsdcValue = trace.value;
        return `${esc(hop.hop || "-")}. ${esc(hop.sell_amount || "-")} ${esc(displaySymbol(hop.sell_symbol))} -> ${esc(hop.buy_amount || "-")} ${esc(displaySymbol(hop.buy_symbol))} @ ${esc(hop.exchange_rate || "-")}${esc(trace.text)}`;
      });
    };
    const hopLine = hop => `${esc(hop.hop || "-")}. ${esc(hop.sell_amount || "-")} ${esc(displaySymbol(hop.sell_symbol))} -> ${esc(hop.buy_amount || "-")} ${esc(displaySymbol(hop.buy_symbol))} @ ${esc(hop.exchange_rate || "-")}`;
    const quoteRates = quote => (quote?.hops || []).map(hop => `${displaySymbol(hop.sell_symbol)}/${displaySymbol(hop.buy_symbol)} ${hop.exchange_rate || "-"}`).join(" | ") || "-";
    const quoteFlow = quote => hopLinesWithValueTrace(quote).join("<br>") || esc(quotePath(quote));
    const plain = value => String(value ?? "-");
    const plainPct = value => Number.isFinite(Number(value)) ? `${Number(value) >= 0 ? "+" : ""}${Number(value).toFixed(5)}%` : "-";
    const plainNum = value => formatPrice(value);
    const symbolKey = symbol => displaySymbol(symbol).toUpperCase();
    const tokenHtml = (symbol, highlightSymbols = null) => {
      const text = displaySymbol(symbol);
      const highlighted = highlightSymbols && highlightSymbols.has(symbolKey(symbol));
      return highlighted ? `<span class="sdk-candidate-token">${esc(text)}</span>` : esc(text);
    };
    const claimMinSpread = claim => {
      const threshold = claim?.threshold_detail || {};
      const value = Number(claim?.min_spread_percent ?? threshold.adjusted_min_spread_percent ?? threshold.min_window_spread_percent ?? 0);
      return Number.isFinite(value) ? Math.max(0, value) : 0;
    };
    const claimSdkCandidateSymbols = claim => {
      const symbols = new Set();
      const topRows = Array.isArray(claim?.top) ? claim.top : [];
      const bottomRows = Array.isArray(claim?.bottom) ? claim.bottom : [];
      const minSpread = claimMinSpread(claim);
      for (const top of topRows) {
        for (const bottom of bottomRows) {
          if (!top || !bottom || symbolKey(top.symbol) === symbolKey(bottom.symbol)) continue;
          const topChange = Number(top.change_percent);
          const bottomChange = Number(bottom.change_percent);
          if (!Number.isFinite(topChange) || !Number.isFinite(bottomChange)) continue;
          if (topChange - bottomChange > minSpread) {
            symbols.add(symbolKey(top.symbol));
            symbols.add(symbolKey(bottom.symbol));
          }
        }
      }
      return symbols;
    };
    const routePathHtml = route => (route.route || route.route_symbols || route.path || [])
      .map(symbol => symbolKey(symbol) === "USDC" ? esc(displaySymbol(symbol)) : tokenHtml(symbol, new Set([symbolKey(symbol)])))
      .join(" -> ") || "-";
    const tableText = (title, rows) => [
      title,
      ...((rows || []).map((row, index) => `${index + 1}. ${displaySymbol(row.symbol)} | ${plainPct(row.change_percent)} | ${plainNum(row.start_price)} -> ${plainNum(row.current_price)} | ${row.price_source || "-"}`)),
      ...(rows && rows.length ? [] : ["-"]),
    ].join("\n");
    const planText = plan => executionParamsText(plan);
    const quoteHopText = (hop, traceText = "") => `${hop.hop || "-"}. ${hop.sell_amount || "-"} ${displaySymbol(hop.sell_symbol)} -> ${hop.buy_amount || "-"} ${displaySymbol(hop.buy_symbol)} @ ${hop.exchange_rate || "-"}${traceText} | 费用 ${hop.fee_amount || "-"} ${displaySymbol(hop.sell_symbol)}`;
    const quoteText = quote => {
      let previousUsdcValue = Number(quote?.input_amount);
      const lines = (quote?.hops || []).map(hop => {
        const trace = hopValueTrace(hop, previousUsdcValue);
        previousUsdcValue = trace.value;
        return quoteHopText(hop, trace.text);
      });
      return lines.join("\n") || quotePath(quote);
    };
    const historyRoutePath = row => (row?.route_path || row?.quote?.path || []).map(displaySymbol).join(" -> ") || "-";
    /*
    const historyReasons = row => (row?.blocked_reasons || row?.precheck?.reasons || []).join("锛?) || row?.error || "-";
    */
    const cowErrorSummary = value => {
      const text = String(value || "").trim();
      const lower = text.toLowerCase();
      if (!text) return "";
      if (lower.includes("403 error") && (lower.includes("cloudfront") || lower.includes("request blocked"))) return "quote_api_http_403_cloudfront_request_blocked";
      if (lower.includes("timed out") || lower.includes("timeout")) return "quote_api_timeout";
      if (lower.includes("ssl")) return "quote_api_ssl_error";
      if (lower.includes("<html") || lower.includes("<!doctype") || lower.includes("http")) return "quote_api_http_error";
      return text.length > 240 ? `${text.slice(0, 240)}...` : text;
    };
    const nonFailureReason = reason => {
      const normalized = String(reason || "").trim().toLowerCase();
      if (new Set([
        "cow_intent_ready",
        "cow_flashloan_sdk_intent_ready",
        "limit_order_ready_to_submit",
        "ready_to_submit",
        "checks_passed",
      ]).has(normalized)) return true;
      return normalized.startsWith("diagnostic_cow_flashloan_sdk_plan_required:")
        || normalized.startsWith("diagnostic_cow_sdk_intent_route_required:")
        || normalized.startsWith("diagnostic_flashloanandsettle_required:");
    };
    const historyReasons = row => {
      const sdk = historyCowSdk(row);
      const submission = row?.cow_submission_result || {};
      const preflight = submission.preflight || sdk.submission_preflight || sdk.preflight || row?.preflight || {};
      const executionError = submission.execution_error || sdk.execution_error || {};
      const sdkStatus = historyCowSdkStatus(row);
      const reasons = (row?.blocked_reasons || row?.precheck?.reasons || []).map(cowErrorSummary).filter(Boolean);
      if (preflight.failureReason && preflight.failureReason !== "none") {
        reasons.unshift(`路线预检失败：${precheckStatusLabel(preflight.failureReason)}（code ${preflight.failureCode || "-"}）`);
      }
      if (executionError.failureReason) {
        reasons.unshift(`执行预检失败：${executionError.source ? precheckStatusLabel(executionError.source) : "未知"} / ${precheckStatusLabel(executionError.failureReason)}（code ${executionError.failureCode || "-"}）`);
      }
      const sdkReason = submission.error || submission.blocked_reason || sdk.submission_blocked_reason || sdk.blocked_reason || sdk.submission_error || sdk.error || "";
      if (sdkReason) reasons.unshift(cowErrorSummary(sdkReason));
      if (sdkStatus && historyCowSdkSubmitted(row)) reasons.unshift(`SDK 已提交：${precheckStatusLabel(sdkStatus)}`);
      if (sdkStatus && historySdkWaiting(row)) reasons.unshift(`SDK 等待中：${precheckStatusLabel(sdkStatus)}`);
      return reasons.filter(reason => !nonFailureReason(reason)).join("; ") || cowErrorSummary(row?.error) || "-";
    };
    const historyQuoteDelta = row => row?.profit_prediction_disabled || row?.precheck?.profit_prediction_disabled ? "预测关闭，等待 SDK/链上回执" : "预测关闭";
    const historyReview = row => row?.review_summary || {};
    const historyCowSdk = row => {
      const review = historyReview(row);
      return row?.cow_sdk_result || review.cow_sdk?.result || row?.quote?.cow_sdk_result || row?.precheck?.cow_sdk_result || {};
    };
    const historyCowSdkStatus = row => String(historyCowSdk(row)?.submission_status || historyCowSdk(row)?.status || "").trim().toLowerCase();
    const historyCowSdkSubmitted = row => Boolean(historyCowSdk(row)?.submission_attempted || historyCowSdk(row)?.submission_order_id || historyCowSdk(row)?.submission_tx_hash);
    const historyCowSdkHasResult = row => {
      const sdk = historyCowSdk(row);
      return Boolean(
        sdk?.submission_attempted
        || sdk?.submission_order_id
        || sdk?.submission_tx_hash
        || sdk?.submission_status
        || sdk?.status
        || sdk?.submission_error
        || sdk?.error
      );
    };
    const historyQuoteWaiting = row => {
      const state = String(row?.state || "").trim().toLowerCase();
      const sdkStatus = historyCowSdkStatus(row);
      return !historyCowSdkSubmitted(row) && (["quote_required", "quote_unavailable", "market_candidate"].includes(state) || ["quote_required", "quote_unavailable", "not_submitted", "ready_not_submitted"].includes(sdkStatus));
    };
    const historySdkWaiting = row => {
      const sdkStatus = historyCowSdkStatus(row);
      const state = String(row?.state || "").trim().toLowerCase();
      const pendingStates = new Set(["submission_pending", "submitted", "order_pending", "settlement_pending", "confirmation_pending", "pending"]);
      return historyCowSdkHasResult(row)
        && !historyCowSdkSubmitted(row)
        && (pendingStates.has(sdkStatus) || pendingStates.has(state));
    };
    const historyAttemptState = row => {
      const sdkStatus = historyCowSdkStatus(row);
      const state = String(row?.state || "").trim().toLowerCase();
       if (sdkStatus) return historyCowSdkSubmitted(row) ? `SDK 状态：${precheckStatusLabel(sdkStatus)}` : `等待状态：${precheckStatusLabel(sdkStatus)}`;
      if (["quote_required", "quote_unavailable", "market_candidate", "submission_paused"].includes(state)) return `等待状态：${precheckStatusLabel(state)}`;
      return precheckStatusLabel(state) || "-";
    };
    const sdkSuccessStates = new Set(["submitted_success", "confirmed_success", "settled", "settlement_success", "executed", "execution_success", "success"]);
    const sdkFailureStates = new Set(["submission_failed", "submit_failed", "order_failed", "execution_failed", "settlement_failed", "confirmation_failed", "confirmed_failed", "failed", "error"]);
    const historyThreshold = row => historyReview(row)?.threshold || {};
    const historyMarketHtml = row => {
      const review = historyReview(row);
      const prices = review.market_prices || [];
      const priceLines = prices.map(item =>
        `${displaySymbol(item.symbol || item.base_symbol || "")}: ${formatPrice(item.start_price)} -> ${formatPrice(item.current_price)} (${plainPct(item.change_percent)})`
      );
      const threshold = historyThreshold(row);
      const spread = review.window_spread_percent ?? row?.quote?.window_spread_percent;
      const thresholdLine = `价差 ${Number.isFinite(Number(spread)) ? Number(spread).toFixed(4) + "%" : "-"} / 最低 ${threshold.adjusted_min_spread_percent || threshold.min_window_spread_percent || "-"}%`;
      const sourceLine = `${review.price_source || row?.market_state?.price_source || "-"} | 窗口 ${review.window_seconds || row?.market_state?.window_seconds || "-"}s`;
      return [thresholdLine, sourceLine, ...priceLines].map(esc).join("<br>") || "-";
    };
    const historyQuoteHtml = row => {
      const review = historyReview(row);
      const quote = review.cow_quote || {};
      const timing = review.timing || {};
      const quoteTiming = timing.quote || {};
      const signalTiming = timing.signal || {};
      const windowTiming = timing.binance_window || {};
      const hops = quote.hops || [];
      if (!quote.quote_verified && !hops.length) return esc("等待 CoW 报价");
      const hopLines = hops.map(hop =>
        `${hop.hop || "-"}：${hop.sell_amount || "-"} ${displaySymbol(hop.sell_symbol || "")} -> ${hop.buy_amount || "-"} ${displaySymbol(hop.buy_symbol || "")} @ ${hop.exchange_rate || "-"}，费用 ${hop.fee_amount || "-"}`
      );
      return [
        `输入：${quote.input_amount || "-"} ${displaySymbol(quote.input_symbol || "")}`,
        `最终输出：${quote.final_amount || "-"} ${displaySymbol(quote.final_symbol || "")}`,
        `耗时：信号到报价 ${quoteTiming.signal_to_quote_start_ms ?? "-"}ms | 报价延迟 ${quoteTiming.quote_latency_ms ?? "-"}ms | 窗口年龄 ${quoteTiming.binance_window_age_at_quote_start_ms ?? "-"}ms`,
        `窗口结束 ${quoteTiming.binance_window_end_ms ?? windowTiming.x_end_ms ?? windowTiming.y_end_ms ?? signalTiming.binance_window_end_ms ?? "-"} | 信号时间 ${signalTiming.signal_detected_at || "-"}`,
        ...hopLines,
        quote.error ? `错误：${zhError(quote.error)}` : "",
      ].filter(Boolean).map(esc).join("<br>") || "-";
    };
    const slippageGuardText = step => {
      const actual = Number(step.query_output_amount || step.query_buy_amount_after_fee);
      const minimum = Number(step.min_output_amount);
      const ratio = Number.isFinite(actual) && Number.isFinite(minimum) && minimum > 0
        ? `${((actual / minimum - 1) * 10000).toFixed(2)}bps`
        : "-";
      return `已配置滑点 ${step.slippage_bps ?? "-"}bps | 实际输出与最低值差额 ${ratio}`;
    };
    const windowTimingText = timing => {
      const value = timing || {};
      return `接近 ${value.closer_to || "-"} | 相对当前窗口 ${value.timing_vs_current_window || "-"} | 相对上一窗口 ${value.query_vs_previous_percent || "-"}% | 相对当前窗口 ${value.query_vs_current_percent || "-"}%`;
    };
    const historyCostQuoteHtml = row => {
      const review = historyReview(row);
      const plan = review.plan || {};
      const costs = review.costs || {};
      const guard = review.profit_guard || {};
      const queryLines = (plan.steps || []).map(step => {
        const guardInfo = step.query_guard_analysis || {};
        const position = step.query_price_position || step.query_rate_position || {};
        const queryValue = step.query_price_usd_per_token || step.query_exchange_rate || "-";
        return `第 ${step.step || "-"} 跳 查询 ${queryValue} 排名 ${position.rank || "-"}/${position.total || "-"} ${position.position || "-"} 守卫 ${guardInfo.status || "-"} | ${windowTimingText(step.query_window_timing)} | ${slippageGuardText(step)}`;
      }).join(" | ") || "-";
      const fees = (costs.cow_fee_amounts || []).map(item =>
        `${item.fee_amount || "-"} ${displaySymbol(item.sell_symbol || "")}`
      ).join(" | ") || "-";
      return [
        `意图目标：${guard.min_final_amount || guard.auto_execute_min_profit_usd || "-"} ${displaySymbol(guard.final_symbol || row?.final_symbol || "USDC")}`,
        "上层预测：已关闭",
        `SDK 状态：${historyCowSdkStatus(row) ? precheckStatusLabel(historyCowSdkStatus(row)) : "-"}`,
        `报价结果门禁：${guard.quote_result_gate_enforced ? "开启" : "关闭"} | 上层报价差额门禁：已关闭 | 仅意图模式`,
        `报价探针说明：${(guard.quote_probe_notes || guard.local_profit_diagnostic_reasons || []).join("；") || "-"}`,
        `报价错误：${zhError(guard.quote_error_type || guard.quote_error_display || "-")}`,
        `原始查询位置：${queryLines}`,
        `阻断统计：${JSON.stringify(guard.blocking_cause_counts || {})}`,
        "报价差额抵消：已关闭",
        `意图最低值：${guard.auto_execute_min_profit_usd || "-"}U（${guard.auto_execute_min_profit_percent || "-"}%）`,
        `费用：${fees}`,
        `Gas 报价 ${costs.quote_api_gas_used ?? "-"} / 提交 ${costs.user_order_submission_gas_used ?? "-"} / 结算 ${costs.settlement_gas_payer || "-"}`,
      ].map(esc).join("<br>");
    };
    const historyReviewText = row => [
      `行情：${historyMarketHtml(row).replace(/<br>/g, " | ")}`,
      `报价：${historyQuoteHtml(row).replace(/<br>/g, " | ")}`,
      `费用/结果：${historyCostQuoteHtml(row).replace(/<br>/g, " | ")}`,
      `原因：${historyReasons(row)}`,
    ].join("\n");
    const historyReasonList = row => {
      const sdk = historyCowSdk(row);
      const sdkStatus = historyCowSdkStatus(row);
      const sdkReason = cowErrorSummary(sdk.submission_blocked_reason || sdk.blocked_reason || sdk.submission_error || sdk.error || "");
      if (sdkReason) return [sdkReason];
      if (historyCowSdkSubmitted(row) && sdkStatus) return [`SDK 状态：${precheckStatusLabel(sdkStatus)}`];
      if (historySdkWaiting(row) && sdkStatus) return [`SDK 待返回：${precheckStatusLabel(sdkStatus)}`];
      const reasons = row?.blocked_reasons || row?.precheck?.reasons || [];
      if (Array.isArray(reasons) && reasons.length) {
        return reasons
          .map(item => cowErrorSummary(item))
          .filter(reason => reason && !nonFailureReason(reason));
      }
      return row?.error ? [cowErrorSummary(row.error)] : [];
    };
    function cowAttemptReview(rows) {
      const items = Array.isArray(rows) ? rows : [];
      if (!items.length) {
        return {
          conclusion: "暂无 CoW 执行复盘记录。行情候选、报价检查、预检、暂停、SDK 失败和成功都会记录在这里。",
          metrics: ["记录 0", "报价等待 0", "SDK 等待 0", "SDK 失败 0", "SDK 成功 0"],
        };
      }
      const precheckPassed = items.filter(row => row?.checks_passed);
      const sdkSuccess = items.filter(row => sdkSuccessStates.has(historyCowSdkStatus(row)) && historyCowSdkSubmitted(row));
      const sdkFailed = items.filter(row => sdkFailureStates.has(historyCowSdkStatus(row)) && historyCowSdkSubmitted(row));
      const quoteWaiting = items.filter(historyQuoteWaiting);
      const sdkPending = items.filter(historySdkWaiting);
      const notExecutable = items.filter(row => clientCowAttemptCategory(row) === "not_executable");
      const topReasonRows = sdkFailed.length ? sdkFailed : (sdkPending.length ? sdkPending : quoteWaiting.length ? quoteWaiting : items);
      const topReason = cowAttemptReasonTop(topReasonRows);
      const latest = items[0] || {};
      const latestLine = `${fmtTime(latest.created_at)} | ${networkLabel(latest.network) || latest.network || "-"} | ${latest.pair || "-"} | ${historyAttemptState(latest)} | ${historyQuoteDelta(latest)}`;
      const conclusion = sdkSuccess.length
        ? `复盘结论：有 ${sdkSuccess.length} 条 SDK 确认成功记录。最近一条：${latestLine}`
        : sdkFailed.length
          ? `复盘结论：最近 ${items.length} 条中有 ${sdkFailed.length} 条 SDK 确认失败。主要失败项：${topReason ? `${topReason[0]} (${topReason[1]})` : "-"}。最近一条：${latestLine}`
          : quoteWaiting.length
            ? `复盘结论：最近 ${items.length} 条中有 ${quoteWaiting.length} 条仍在等待 CoW 报价，不属于 SDK pending。最近一条：${latestLine}`
            : `复盘结论：最近 ${items.length} 条中有 ${sdkPending.length} 条仍在等待 SDK 返回。最近一条：${latestLine}`;
      return {
        conclusion,
        metrics: [
          `记录 ${items.length}`,
          `SDK 成功 ${sdkSuccess.length}`,
          `SDK 失败 ${sdkFailed.length}`,
          `报价等待 ${quoteWaiting.length}`,
          `SDK 等待 ${sdkPending.length}`,
          `预检通过 ${precheckPassed.length}`,
          `未进入 SDK 终态 ${notExecutable.length}`,
          `主要状态 ${topReason ? `${topReason[0]} (${topReason[1]})` : "-"}`,
        ],
      };
    }
    const costText = (quote, plan) => {
      const costs = quote?.costs || {};
      return [
        `原生币余额：before ${costs.native_balance_before ?? "未检查"} / after ${costs.native_balance_after ?? "未检查"} (${costs.native_balance_source || "quote_stage"})`,
        `Gas：quote ${costs.quote_api_gas_used ?? 0}；下单 ${costs.user_order_submission_gas_used ?? 0}；结算 ${costs.settlement_gas_payer || "solver"}；授权 ${costs.approval_gas_status || "待检查"}`,
        `手续费：${feeLines(costs)}`,
        `上层盈亏预测：已关闭`,
        `提交控制：仅使用意图和 SDK/链上回执`,
        `原始报价：${quote ? quoteFinal(quote) : "等待 CoW SDK 报价"}`,
      ].join("\n");
    };
    const supportText = support => {
      if (!support) return "CoW 支持：未检查";
      return support.supported
        ? `CoW 支持：${support.cow_network} 已识别`
        : `CoW 支持：不支持 ${support.unsupported_tokens?.join(", ") || "-"}`;
    };
    const planInitial = plan => plan?.available ? `${plan.initial_amount || "-"} ${displaySymbol(plan.initial_symbol || "")}` : "-";
    const planFinal = plan => plan?.available ? `${plan.final_target_amount || "-"} ${displaySymbol(plan.final_symbol || "")}` : "-";
    const planDelta = plan => plan?.available ? `意图目标 ${plan.profit_amount || "-"} ${displaySymbol(plan.final_symbol || "")} (${plan.profit_percent || "-"}%)` : "-";
    const basisLabel = basis => ({
      current_binance_buy_low: "当前盘面买入",
      pre_change_binance_cross: "变化前价格换算",
      current_binance_sell_high: "当前盘面卖出",
      stable_reference: "稳定币参考",
    }[basis] || basis || "-");
    const planMarketPrices = plan => plan?.available ? (plan.market_prices || []).map(item =>
      `${displaySymbol(item.symbol)} | 起始价 ${item.start_price || "-"} | 当前价 ${item.current_price || "-"} | 涨跌 ${plainPct(item.change_percent)} | ${item.price_source || "-"}`
    ).join("<br>") : "-";
    const planMarketPricesText = plan => plan?.available ? (plan.market_prices || []).map(item =>
      `${displaySymbol(item.symbol)} | 起始价 ${item.start_price || "-"} | 当前价 ${item.current_price || "-"} | 涨跌 ${plainPct(item.change_percent)} | ${item.price_source || "-"}`
    ).join("\n") : "-";
    const sourceLabel = source => ({
      previous_window: "前期窗口",
      current_window: "当前窗口",
      query_quote: "CoW 查询报价",
    }[source] || source || "等待查询");
    const pricedToken = step => step.from_symbol === "USDC" ? step.to_symbol : step.from_symbol;
    const priceCandidateHtml = step => (step.price_candidates || []).length
      ? (step.price_candidates || []).map(item => `${esc(sourceLabel(item.source))} ${esc(item.price || "-")} USDC/${esc(displaySymbol(pricedToken(step)))}`).join(" | ")
      : "无价格候选";
    const priceCandidateText = step => (step.price_candidates || []).length
      ? (step.price_candidates || []).map(item => `${sourceLabel(item.source)} ${item.price || "-"} USDC/${displaySymbol(pricedToken(step))}`).join(" | ")
      : "无价格候选";
    const rateCandidateHtml = step => (step.rate_candidates || []).length
      ? (step.rate_candidates || []).map(item => `${esc(sourceLabel(item.source))} ${esc(item.rate || "-")} ${esc(displaySymbol(step.to_symbol))}/${esc(displaySymbol(step.from_symbol))}`).join(" | ")
      : "无比例候选";
    const rateCandidateText = step => (step.rate_candidates || []).length
      ? (step.rate_candidates || []).map(item => `${sourceLabel(item.source)} ${item.rate || "-"} ${displaySymbol(step.to_symbol)}/${displaySymbol(step.from_symbol)}`).join(" | ")
      : "无比例候选";
    const queryAnalysisText = step => {
      const guard = step.query_guard_analysis || {};
      const position = step.query_price_position || step.query_rate_position || {};
      const queryValue = step.query_price_usd_per_token || step.query_exchange_rate || "-";
      return `查询 ${queryValue} | 排名 ${position.rank || "-"}/${position.total || "-"} ${position.position || "-"} | ${windowTimingText(step.query_window_timing)} | 守卫 ${guard.status || "-"} | ${slippageGuardText(step)} | ${guard.reason || "-"}`;
    };
    const queryAnalysisHtml = step => esc(queryAnalysisText(step));
    const selectionHtml = step => {
      const usesPrice = (step.price_candidates || []).length > 0;
      const target = usesPrice
        ? `${step.selected_target_price_usd_per_token || step.target_price_usd_per_token || "-"} USDC/${displaySymbol(pricedToken(step))}`
        : `${step.selected_target_exchange_rate || step.target_exchange_rate || "-"} ${displaySymbol(step.to_symbol)}/${displaySymbol(step.from_symbol)}`;
      const acceptable = usesPrice
        ? `${step.acceptable_slippage_price_usd_per_token || step.acceptable_price_usd_per_token || "-"} USDC/${displaySymbol(pricedToken(step))}`
        : `${step.acceptable_slippage_exchange_rate || step.min_exchange_rate || "-"} ${displaySymbol(step.to_symbol)}/${displaySymbol(step.from_symbol)}`;
      return `目标：${esc(target)} (${esc(sourceLabel(step.selected_target_source))}) | 可接受滑点价格/比例：${esc(acceptable)} (${esc(sourceLabel(step.selected_acceptable_source))})`;
    };
    const selectionText = step => {
      const usesPrice = (step.price_candidates || []).length > 0;
      const target = usesPrice
        ? `${step.selected_target_price_usd_per_token || step.target_price_usd_per_token || "-"} USDC/${displaySymbol(pricedToken(step))}`
        : `${step.selected_target_exchange_rate || step.target_exchange_rate || "-"} ${displaySymbol(step.to_symbol)}/${displaySymbol(step.from_symbol)}`;
      const acceptable = usesPrice
        ? `${step.acceptable_slippage_price_usd_per_token || step.acceptable_price_usd_per_token || "-"} USDC/${displaySymbol(pricedToken(step))}`
        : `${step.acceptable_slippage_exchange_rate || step.min_exchange_rate || "-"} ${displaySymbol(step.to_symbol)}/${displaySymbol(step.from_symbol)}`;
      return `目标：${target} (${sourceLabel(step.selected_target_source)}) | 可接受滑点价格/比例：${acceptable} (${sourceLabel(step.selected_acceptable_source)})`;
    };
    const sdkParamsHtml = step => {
      const sdk = step.cow_sdk_parameters || {};
      return `传给 CoW SDK：kind=${esc(sdk.quote_kind || "sell")} | sellToken=${esc(displaySymbol(sdk.sell_token_symbol || step.from_symbol))} | buyToken=${esc(displaySymbol(sdk.buy_token_symbol || step.to_symbol))} | sellAmountBeforeFee=${esc(sdk.sell_amount_before_fee || "-")} | targetBuyAmountAfterFee=${esc(sdk.target_buy_amount_after_fee || "-")} | minBuyAmountAfterFee=${esc(sdk.min_buy_amount_after_fee || "-")}`;
    };
    const sdkParamsText = step => {
      const sdk = step.cow_sdk_parameters || {};
      return `传给 CoW SDK：kind=${sdk.quote_kind || "sell"} | sellToken=${displaySymbol(sdk.sell_token_symbol || step.from_symbol)} | buyToken=${displaySymbol(sdk.buy_token_symbol || step.to_symbol)} | sellAmountBeforeFee=${sdk.sell_amount_before_fee || "-"} | targetBuyAmountAfterFee=${sdk.target_buy_amount_after_fee || "-"} | minBuyAmountAfterFee=${sdk.min_buy_amount_after_fee || "-"}`;
    };
    const precheckStatusLabel = status => ({
      checks_passed_order_disabled: "意图已准备，真实下单未开放",
      limit_order_ready_not_submitted: "意图参数已准备，提交适配器未开放",
      limit_order_ready_to_submit: "意图参数已准备，可提交",
      ready_not_submitted: "意图已准备，未提交",
      ready_to_submit: "意图已准备，可提交",
      submission_paused: "提交已暂停",
      submitted_success: "已提交",
      submission_failed: "提交失败",
      adapter_unavailable: "提交适配器不可用",
      cow_flashloan_sdk_install_required: "需安装 CoW SDK",
      order_submission_switch_off: "交易开关关闭",
      order_submission_adapter_unavailable: "提交适配器不可用",
      order_submission_network_unsupported: "网络暂不支持提交",
      order_submission_signer_not_ready: "缺少签名私钥",
      order_submission_disabled: "真实下单未启用",
      unsupported: "代币不支持",
      quote_required: "等待 CoW 报价",
      quote_unavailable: "报价不可用",
      flashloan_payload_required: "缺少闪电贷执行参数",
      price_guard_failed: "旧逐跳保护已关闭",
      not_profitable: "旧本地预测已关闭",
      profit_below_threshold: "旧本地预测已关闭",
      blocked: "不能下单",
    }[status] || status || "未检查");
    const precheckLine = item => `原始查询 ${item.path || "-"} | 输出 ${item.query_buy_amount_after_fee || "-"} ${displaySymbol(item.buy_symbol || "")} | 上层不做逐跳保底判断`;
    const quoteSubmissionStatus = row => {
      const check = row?.execution_precheck || {};
      const sdk = row?.cow_sdk_result || {};
      const submission = row?.cow_submission_result || {};
      const submitted = Boolean(submission.submitted || sdk.submitted || check.submission_order_id || check.submission_tx_hash);
      if (submitted) return check.submission_order_id ? `已提交 ${shortAddress(check.submission_order_id)}` : "已提交";
      const submissionStatus = check.submission_status || sdk.submission_status || submission.status;
      if (submission?.preflight?.failureReason && submission.preflight.failureReason !== "none") {
        return `预检失败 ${zhError(submission.preflight.failureReason)}`;
      }
      if (submission?.execution_error?.failureReason) {
        return `执行预检失败 ${zhError(submission.execution_error.failureReason)}`;
      }
      if (check.submission_attempted || sdk.submission_attempted || submissionStatus) {
        return precheckStatusLabel(submissionStatus || check.status);
      }
      return check.can_submit_order ? "可提交，等待提交回执" : precheckStatusLabel(check.status);
    };
    const precheckHtml = quote => {
      const check = quote?.execution_precheck;
      if (!check) return "下单条件：等待查询真实报价";
      const hops = (check.hop_checks || []).map(item => esc(precheckLine(item))).join("<br>") || "-";
      const reasons = (check.reasons || []).map(item => esc(/[\u4e00-\u9fff]/.test(String(item)) ? String(item) : `原因（${String(item)}）`)).join("<br>") || "-";
      const autoLine = `上层盈亏预测已关闭 | 逐跳约束已关闭 | ${check.can_submit_order ? "可提交" : precheckStatusLabel(check.status)}`;
      return `下单条件：${esc(precheckStatusLabel(check.status))}<br>${esc(autoLine)}<br>原始逐跳查询：<br>${hops}<br>原因/限制：<br>${reasons}`;
    };
    const precheckText = quote => {
      const check = quote?.execution_precheck;
      if (!check) return "下单条件：等待查询真实报价";
      const hops = (check.hop_checks || []).map(precheckLine).join("\n") || "-";
      const reasons = (check.reasons || []).join("\n") || "-";
      const autoLine = `上层盈亏预测已关闭 | 逐跳约束已关闭 | ${check.can_submit_order ? "可提交" : precheckStatusLabel(check.status)}`;
      return `下单条件：${precheckStatusLabel(check.status)}\n${autoLine}\n原始逐跳查询：\n${hops}\n原因/限制：\n${reasons}`;
    };
    const executionStepLine = step => `${esc(step.step || "-")}. ${esc(displaySymbol(step.from_symbol))} -> ${esc(displaySymbol(step.to_symbol))} | ${esc(basisLabel(step.price_basis))}<br>` +
      `输入数量 ${esc(step.query_sell_amount_before_fee || step.input_amount || "-")} ${esc(displaySymbol(step.from_symbol))} | CoW 查询输出 ${esc(step.query_buy_amount_after_fee || "等待查询")} ${esc(displaySymbol(step.to_symbol))}<br>` +
      `候选价格：${priceCandidateHtml(step)}<br>候选兑换比例：${rateCandidateHtml(step)}<br>` +
      `${selectionHtml(step)} | 保底价动态生成；保底参数 ${esc(step.slippage_bps ?? "-")}bps (${esc(step.slippage_percent || "-")}%)<br>` +
      `目标数量 ${esc(step.target_output_amount || "-")} ${esc(displaySymbol(step.to_symbol))} | 最低接收 ${esc(step.min_output_amount || "-")} ${esc(displaySymbol(step.to_symbol))}<br>` +
      `${sdkParamsHtml(step)}<br>规则：${esc(step.selection_rule || step.price_compare_rule || "-")}`;
    const executionStepText = step => `${step.step || "-"}. ${displaySymbol(step.from_symbol)} -> ${displaySymbol(step.to_symbol)} | ${basisLabel(step.price_basis)}\n` +
      `输入数量 ${step.query_sell_amount_before_fee || step.input_amount || "-"} ${displaySymbol(step.from_symbol)} | CoW 查询输出 ${step.query_buy_amount_after_fee || "等待查询"} ${displaySymbol(step.to_symbol)}\n` +
      `候选价格：${priceCandidateText(step)}\n候选兑换比例：${rateCandidateText(step)}\n` +
      `${selectionText(step)} | 保底价动态生成；保底参数 ${step.slippage_bps ?? "-"}bps (${step.slippage_percent || "-"}%)\n` +
      `目标数量 ${step.target_output_amount || "-"} ${displaySymbol(step.to_symbol)} | 最低接收 ${step.min_output_amount || "-"} ${displaySymbol(step.to_symbol)}\n` +
      `${sdkParamsText(step)}\n规则：${step.selection_rule || step.price_compare_rule || "-"}`;
    const executionParams = plan => plan?.available ? `<div>本地候选参考价格：<br>${planMarketPrices(plan)}</div><div>本地参考参数（不控制 SDK 提交）：<br>${(plan.steps || []).map(executionStepLine).join("<br>")}</div><div>参考目标：${esc(planFinal(plan))} | slip ${esc(plan.slippage_bps ?? "-")}bps (${esc(plan.slippage_percent || "-")}%) | 上层预测关闭</div>` : `计划不可用：${esc(plan?.reason || "-")}`;
    const executionParamsText = plan => plan?.available ? [
      "本地候选参考价格：",
      planMarketPricesText(plan),
      "本地参考参数（不控制 SDK 提交）：",
      ...((plan.steps || []).map(executionStepText)),
      `参考目标：${planFinal(plan)} | slip ${plan.slippage_bps ?? "-"}bps (${plan.slippage_percent || "-"}%) | 上层预测关闭`,
    ].join("\n") : `计划不可用：${plan?.reason || "-"}`;
    const planStepLine = executionStepLine;
    const planFlow = plan => plan?.available ? (plan.steps || []).map(planStepLine).join("<br>") : `计划不可用：${esc(plan?.reason || "-")}`;
    const feeLines = costs => (costs?.cow_fee_amounts || []).map(item => `第 ${item.hop || "-"} 步 ${item.fee_amount || "-"} ${displaySymbol(item.sell_symbol || "")}`).join(" | ") || "-";
    const costDetail = (quote, plan) => {
      const costs = quote?.costs || {};
      return `<div>原生币余额：before ${esc(costs.native_balance_before ?? "未检查")} / after ${esc(costs.native_balance_after ?? "未检查")} (${esc(costs.native_balance_source || "quote_stage")})</div>
        <div>Gas：quote ${esc(costs.quote_api_gas_used ?? 0)}；下单 ${esc(costs.user_order_submission_gas_used ?? 0)}；结算 ${esc(costs.settlement_gas_payer || "solver")}；授权 ${esc(costs.approval_gas_status || "待检查")}</div>
        <div>手续费：${esc(feeLines(costs))}</div>
        <div>上层盈亏预测：已关闭</div>
        <div>提交控制：仅使用意图和 SDK/链上回执</div>
        <div>原始报价：${esc(quote ? quoteFinal(quote) : "等待 CoW SDK 报价")}</div>`;
    };

    const rowHtml = (row, index, options = {}) => {
      const highlightSymbols = options.highlightSymbols || null;
      const highlighted = highlightSymbols && highlightSymbols.has(symbolKey(row.symbol));
      return `<tr class="${highlighted ? "sdk-candidate-row" : ""}">
      <td>${index}</td><td>${tokenHtml(row.symbol, highlightSymbols)}</td><td class="${cls(row.change_percent)}">${pct(row.change_percent)}</td>
      <td>${num(row.start_price)}</td><td>${num(row.current_price)}</td><td>${esc(row.price_source || "-")}</td>
    </tr>`;
    };
    const chainList = row => (row?.cow_networks || []).join(", ") || "-";
    const cowSupportedRowHtml = (row, index) => `<tr>
      <td>${index}</td><td>${esc(displaySymbol(row.symbol))}</td><td class="${cls(row.change_percent)}">${pct(row.change_percent)}</td>
      <td>${num(row.start_price)}</td><td>${num(row.current_price)}</td><td>${esc(chainList(row))}</td><td>${esc(row.price_source || "-")}</td>
    </tr>`;
    const cowSupportedText = (title, rows) => [
      title,
      ...((rows || []).map((row, index) => `${index + 1}. ${displaySymbol(row.symbol)} | ${plainPct(row.change_percent)} | ${plainNum(row.start_price)} -> ${plainNum(row.current_price)} | 链 ${chainList(row)} | ${row.price_source || "-"}`)),
      ...(rows && rows.length ? [] : ["-"]),
    ].join("\n");
    const reasonCountsText = counts => Object.entries(counts || {}).map(([key, value]) => `${key}:${value}`).join(", ") || "-";
    const thresholdBreakdownText = threshold => {
      const t = threshold || {};
      const dynamic = t.min_window_spread_percent ?? t.adjusted_min_spread_percent ?? "-";
      const minProfit = t.min_profit_usd != null && t.amount != null && t.min_profit_percent != null ? `${t.min_profit_usd}U/${t.amount}U=${t.min_profit_percent}%` : "诊断值";
      return `动态诊断 ${dynamic}% = 双向手续费 ${t.route_trade_fee_percent ?? "-"}% + 闪电贷利息 ${t.flashloan_fee_percent ?? "-"}% + gas/费用预留 ${t.fee_reserve_percent ?? "0"}% + 本地报价诊断 ${minProfit}；真实执行由 CoW 意图和 SDK 结果控制`;
    };
    const cowSupportedSourceCount = overview => {
      const direct = Number(overview?.source_market_row_count);
      if (Number.isFinite(direct) && direct > 0) return direct;
      const eligible = Number(overview?.market_eligible_symbol_count || 0);
      const excluded = Number(overview?.market_excluded_symbol_count || 0);
      return eligible + excluded;
    };
    const cowSupportedOverviewMeta = (overview, sideLabel) => {
      const sideFilter = sideLabel === "涨幅" ? overview?.top_filter : overview?.bottom_filter;
      const stats = sideFilter || overview || {};
      const excluded = Number(stats.market_excluded_symbol_count || 0);
      const reasonText = excluded > 0 && reasonCountsText(stats.market_excluded_reason_counts) === "-" ? "旧接口未返回明细" : reasonCountsText(stats.market_excluded_reason_counts);
      return [
        `${sideLabel}要求：单边 ${sideLabel} > ${overview?.min_side_change_percent ?? "0.3"}%`,
        `币价 >= ${overview?.min_token_price_usd ?? "0.01"}U`,
        `来源 ${stats.source_market_row_count ?? cowSupportedSourceCount(overview)} 个`,
        `通过行情过滤 ${stats.market_eligible_symbol_count ?? 0} 个`,
        `CoW 命中 ${stats.supported_symbol_count ?? 0} / 未命中 ${stats.unsupported_symbol_count ?? 0}`,
        `行情过滤排除 ${stats.market_excluded_symbol_count ?? 0} 个 (${reasonText})`,
      ].join(" | ");
    };
    const renderCowSupportedOverview = overview => {
      const top = overview?.top || [];
      const bottom = overview?.bottom || [];
      const limit = overview?.limit || 50;
      $("cowSupportedTopTitle").textContent = `所有 CoW 支持代币涨幅前 ${limit}`;
      $("cowSupportedBottomTitle").textContent = `所有 CoW 支持代币跌幅前 ${limit}`;
      $("cowSupportedTopMeta").textContent = cowSupportedOverviewMeta(overview, "涨幅");
      $("cowSupportedBottomMeta").textContent = cowSupportedOverviewMeta(overview, "跌幅");
      $("cowSupportedTopBody").innerHTML = top.map((row, index) => cowSupportedRowHtml(row, index + 1)).join("") || '<tr><td colspan="7">暂无 CoW 支持代币涨幅数据</td></tr>';
      $("cowSupportedBottomBody").innerHTML = bottom.map((row, index) => cowSupportedRowHtml(row, index + 1)).join("") || '<tr><td colspan="7">暂无 CoW 支持代币跌幅数据</td></tr>';
    };
    const rowsTable = (rows, sideLabel, options = {}) => `<div class="table-wrap"><table>
      <thead><tr><th>#</th><th>代币</th><th>${esc(sideLabel)}</th><th>起始价</th><th>当前价</th><th>来源</th></tr></thead>
      <tbody>${(rows || []).map((row, index) => rowHtml(row, index + 1, options)).join("") || '<tr><td colspan="6">暂无数据</td></tr>'}</tbody>
    </table></div>`;
    const networkClaimHtml = claim => {
      const highlightSymbols = claimSdkCandidateSymbols(claim);
      const candidateNote = highlightSymbols.size
        ? `<span class="sdk-candidate-note">蓝色粗体 = 已进入 CoW SDK 候选链路</span>`
        : `<span class="muted">当前 Top/Bottom 5 未形成可入队候选</span>`;
      return `<div class="network-claim">
      <div class="network-claim-head">
        <h3>${esc(claim.label || claim.network || "-")} <span class="muted">(${esc(claim.chain_id || "-")})</span></h3>
        <span class="context">token ${esc(claim.token_cache_count ?? "-")} | 命中 ${esc(claim.supported_symbol_count ?? 0)} | 候选 ${esc(claim.pair_count ?? 0)}</span>
      </div>
      <div class="context">来源 ${esc(claim.token_cache_source || "-")} | 单边 &gt; ${esc(claim.min_side_change_percent ?? "0.3")}% | 价格 &gt;= ${esc(claim.min_token_price_usd ?? "0.01")}U | 已过滤 ${esc(claim.market_excluded_symbol_count ?? 0)} | ${esc(thresholdBreakdownText(claim.threshold_detail || {}))}</div>
      <div class="context">${candidateNote}</div>
      <div class="mini-split">
        <div>${rowsTable(claim.top || [], "涨幅", {highlightSymbols})}</div>
        <div>${rowsTable(claim.bottom || [], "跌幅", {highlightSymbols})}</div>
      </div>
    </div>`;
    };
    const renderNetworkClaims = claims => {
      const target = $("networkClaims");
      if (!target) return;
      const visibleClaims = selectedNetworkClaims(claims);
      target.innerHTML = visibleClaims.map(networkClaimHtml).join("") || '<div class="muted">请选择 CoW 网络，或等待选中网络的 Top/Bottom 5 数据</div>';
    };
    const networkClaimText = claim => [
      `${claim.label || claim.network || "-"} (${claim.chain_id || "-"}) | token ${claim.token_cache_count ?? "-"} | 命中 ${claim.supported_symbol_count ?? 0} | 候选 ${claim.pair_count ?? 0}`,
      tableText(`涨幅前 ${(claim.top || []).length || 5}`, claim.top || []),
      tableText(`跌幅前 ${(claim.bottom || []).length || 5}`, claim.bottom || []),
    ].join("\n");

    const hopOrdinalLabel = hop => ({
      1: "一跳",
      2: "二跳",
      3: "三跳",
      4: "四跳",
      5: "五跳",
    })[Number(hop)] || `${hop || "-"}跳`;

    const strategyAnalysisHtml = (analysis, filter) => {
      const source = filter?.pool_filter || {};
      const poolCandidates = currentMarketState?.pool_candidates || {};
      const poolStatic = poolCandidates?.poolStaticTable || {};
      const poolSummary = poolCandidates?.summary || {};
      const routes = Array.isArray(analysis?.route_rows) ? analysis.route_rows : [];
      const routeSummary = routes.length
        ? `${routes.length} 条闭环策略 | ${analysis.can_arbitrage ? "存在可套利路径" : "暂未出现可套利路径"} | 最佳 ${analysis?.best_strategy || "-"}`
        : "暂无策略分析";
      return `<div class="strategy-analysis">
        <div class="strategy-analysis-head">
          <div class="strategy-analysis-title">套利策略分析</div>
          <div class="strategy-analysis-meta">${esc(analysis?.x_symbol || "-")} / ${esc(analysis?.y_symbol || "-")} | ${esc(routeSummary)}</div>
        </div>
        <div class="strategy-metrics">
          <div class="strategy-metric"><small>X/U</small><strong>${esc(num(analysis?.x_u))}</strong></div>
          <div class="strategy-metric"><small>Y/U</small><strong>${esc(num(analysis?.y_u))}</strong></div>
          <div class="strategy-metric"><small>X/Y</small><strong>${esc(num(analysis?.x_y_ratio))}</strong></div>
          <div class="strategy-metric"><small>Y/X</small><strong>${esc(num(analysis?.y_x_ratio))}</strong></div>
          <div class="strategy-metric"><small>价差</small><strong class="${cls(analysis?.window_spread_percent)}">${esc(plainPct(analysis?.window_spread_percent))}</strong></div>
          <div class="strategy-metric"><small>是否可套利</small><strong class="${analysis?.can_arbitrage ? "positive" : "negative"}">${analysis?.can_arbitrage ? "可套利" : "暂不可"}</strong></div>
        </div>
        <div class="strategy-routes">
          ${(routes || []).map(route => `
            <div class="strategy-route">
              <div class="strategy-route-head">
                <div class="strategy-route-title">${esc(route.strategy || "-")} <span class="muted">${esc(route.route_depth_label || hopOrdinalLabel(route.hop_count || 0))}</span></div>
                <div class="strategy-route-meta">预计收益 ${esc(plainPct(route.profit_percent))} | ${esc(plainNum(route.profit_amount))} U</div>
              </div>
              <div class="strategy-route-meta">路径 ${esc(route.route_summary || (route.route_symbols || []).map(displaySymbol).join(" -> ") || "-")}</div>
              <div class="strategy-hop-list">
                ${(route.hops || []).map(hop => `
                  <div class="strategy-hop">${esc(hopOrdinalLabel(hop.hop))} ${esc(displaySymbol(hop.from_symbol))} -> ${esc(displaySymbol(hop.to_symbol))} | 输入 ${esc(num(hop.input_amount))} | 输出 ${esc(num(hop.output_amount))} | ${esc(num(hop.input_price_usd))} / ${esc(num(hop.output_price_usd))} | ${esc(hop.price_phase || "-")}</div>
                `).join("")}
              </div>
            </div>
          `).join("") || '<div class="muted">暂无策略分析结果</div>'}
        </div>
        <div class="strategy-pool-note">池子数据复用：${esc(source.loaded ? "已复用链路静态池表" : "等待池表加载")} | 支持对数 ${esc(source.pair_count ?? "-")} | 失败对数 ${esc(source.rejected_pair_count ?? "-")}</div>
        <div class="strategy-pool-note">池子页共享 payload：${esc(poolStatic.network || source.network || "-")} | 表 ${esc(poolSummary.pairTableCount || "-")} | 优选 ${esc(poolSummary.pairsWithPreferredPools || "-")} | 严格 ${esc(poolSummary.pairsWithStrictQualityPools || "-")}</div>
      </div>`;
    };

