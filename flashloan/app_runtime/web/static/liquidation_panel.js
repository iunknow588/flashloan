    const $ = id => document.getElementById(id);
    const parseApiResponse = async (r, p) => {
      const ct = r.headers.get("content-type") || "";
      if (ct.includes("application/json")) return await r.json();
      const text = await r.text();
      const title = (text.match(/<title>(.*?)<\/title>/i) || [])[1];
      throw new Error(`${p} 返回了非 JSON 响应：HTTP ${r.status}${title ? ` ${title}` : ""}`);
    };
    const api = async (p, o = {}) => {
      const r = await fetch(p, { headers: { "Content-Type": "application/json" }, ...o });
      const d = await parseApiResponse(r, p);
      if (!r.ok) throw new Error(d.error || `HTTP ${r.status}`);
      return d;
    };
    let refreshingAccounts = false;
    let scanningAccounts = false;
    let refreshingBorrow = false;
    let scanningBorrow = false;
    let scanningGapFill = false;
    const PAGE_SIZE = 20;
    const DEFAULT_MARKET = { market_id: "", chain_id: "" };
    const initialMarketParams = new URLSearchParams(location.search);
    const selectedMarket = {
      market_id: initialMarketParams.get("market_id") || "",
      chain_id: initialMarketParams.get("chain_id") || "",
    };
    let latestChainSelectionPayload = null;
    let latestAccountsPayload = null;
    let latestBorrowPayload = null;
    const accountPageState = { page: 1, pageCount: 1 };
    const borrowPageState = { risk: 1, high: 1, core: 1 };
    const accountDetailBasePath = "/liquidation/account?account=";
    const isAccountScanPage = () => ["/account-scan", "/bot/account-scan"].includes(location.pathname);
    const fmtTime = v => v ? new Date(v).toLocaleString([], { hour12: false }) : "-";
    const moneyText = v => Number.isFinite(Number(v)) ? Number(v).toLocaleString(undefined, { maximumFractionDigits: 4 }) : "-";
    const statusCls = v => ({ healthy: "status-healthy", liquidatable: "status-liquidatable", candidate: "status-candidate", warning: "status-candidate" }[String(v || "").toLowerCase()] || "status-watching");
    const poolTierText = v => ({ core: "核心", high_frequency: "高频", borrow_health: "普通", healthy: "健康", pending: "待扫", unknown: "未知" }[String(v || "").toLowerCase()] || String(v || "-"));
    const poolTierCls = v => ({ core: "hf-red", high_frequency: "hf-orange", borrow_health: "hf-yellow", healthy: "hf-green", pending: "status-watching", unknown: "status-watching" }[String(v || "").toLowerCase()] || "status-watching");
    const hfCls = v => ({ green: "hf-green", beige: "hf-beige", yellow: "hf-yellow", orange: "hf-orange", red: "hf-red" }[String(v || "").toLowerCase()] || "hf-red");
    const marketQueryParams = () => {
      const params = {};
      if (selectedMarket.market_id) params.market_id = selectedMarket.market_id;
      if (selectedMarket.chain_id) params.chain_id = selectedMarket.chain_id;
      const protocols = currentSelectedProtocols();
      if (protocols.length) params.protocols = protocols.join(",");
      return params;
    };
    const appendMarketParams = query => {
      Object.entries(marketQueryParams()).forEach(([key, value]) => query.set(key, String(value)));
      return query;
    };
    const normalizeProtocol = value => String(value || "").trim().toLowerCase();
    const currentSelectedProtocols = () => {
      const selectorProtocols = window.ChainSelector && typeof window.ChainSelector.selectedProtocols === "function"
        ? window.ChainSelector.selectedProtocols()
        : [];
      if (Array.isArray(selectorProtocols) && selectorProtocols.length) {
        return selectorProtocols.map(normalizeProtocol).filter(Boolean);
      }
      const query = new URLSearchParams(location.search);
      return String(query.get("protocols") || "")
        .split(",")
        .map(normalizeProtocol)
        .filter(Boolean);
    };
    const rowProtocol = row => normalizeProtocol(row && row.protocol);
    const protocolVisible = row => {
      const selected = currentSelectedProtocols();
      if (!selected.length) return true;
      const protocol = rowProtocol(row);
      return !protocol || selected.includes(protocol);
    };
    const filterRowsByProtocol = rows => (Array.isArray(rows) ? rows.filter(protocolVisible) : []);
    function renderProtocolFilter() {
      const protocols = currentSelectedProtocols();
      const label = protocols.length ? protocols.join(", ") : "全部";
      if ($("message") && latestChainSelectionPayload) {
        $("message").textContent = `当前协议过滤：${label}`;
      }
    }
    const isBnbVenusMarket = () => selectedMarket.market_id === "liquidation-56-venus-core" || selectedMarket.market_id === "bnb-venus-core";
    const marketLabel = row => {
      const network = row && (row.network || row.market_id) ? (row.network || row.market_id) : "-";
      const chain = row && row.chain_id ? row.chain_id : "-";
      return `${network} / ${chain}`;
    };
    const protocolLabel = row => row && (row.protocol || row.market_id) ? (row.protocol || row.market_id) : "-";
    const healthText = row => {
      if (!row) return "-";
      if (row.health_display) return row.health_display;
      if (row.last_health_display) return row.last_health_display;
      const hf = Number(row.health_factor ?? row.last_health_factor);
      return Number.isFinite(hf) ? hf.toFixed(3) : "-";
    };
    const healthBand = row => row && (row.health_factor_band || row.last_health_factor_band);
    const detailButton = value => {
      const row = value && typeof value === "object" ? value : { account: value };
      const account = row.account || "";
      if (!account) return "-";
      const market = row.market_id ? ` data-market-id="${row.market_id}"` : "";
      const chain = Number.isFinite(Number(row.chain_id)) ? ` data-chain-id="${Number(row.chain_id)}"` : "";
      return `<button class="compact" data-account="${account}"${market}${chain}>打开</button>`;
    };
    const borrowPoolBlockReasonText = reason => ({
      database_unconfigured: "数据库未配置",
      database_endpoint_disabled: "数据库端点已禁用",
      database_connection_terminated: "数据库连接被终止",
      database_unavailable: "数据库不可用",
    }[String(reason || "")] || String(reason || "扫描暂停"));
    const scanStrategyText = strategy => ({
      core_opportunity_refresh: "核心库",
      high_frequency_refresh: "核心+高频",
      borrow_health_full: "全量普通库",
      fallback_high_frequency: "降级高频",
      fallback_borrow_health: "降级普通",
      empty_account_pool: "账户池为空",
      cooldown: "等待周期",
    }[String(strategy || "")] || String(strategy || "-"));
    function coreProfitAssessment(row) {
      const assessment = (row && row.profit_assessment) || (row && row.metadata && row.metadata.profit_assessment) || {};
      const profit = Number(row && row.estimated_operator_net_profit_usd);
      if (!Number.isFinite(profit)) return "-";
      if (assessment.label === "over_1u_candidate" || assessment.above_auto_profit_threshold === true || profit > 1) {
        return "超过 1u";
      }
      return "低收益测试项";
    }
    const coreValueText = {
      worth_executing: "值得自动执行",
      manual_test_under_1u: "低于 1U，仅手工测试",
      not_liquidatable: "当前不可清算",
      no_executable_candidate: "无可执行候选",
    };
    const coreExecutionStatusText = {
      blocked: "已阻断",
      no_candidate: "缺少资产对",
      waiting_quote: "等待报价",
      waiting_preflight: "等待预演",
      preflight_passed: "预演通过",
      preflight_failed: "预演失败",
    };
    const coreExecutionResultText = {
      not_submitted: "尚未提交",
      submission_blocked: "提交阻断",
      submission_failed: "提交失败",
      static_call_failed: "预演失败",
      confirmed_failed: "确认失败",
      confirmed_success: "清算成功",
    };
    const coreReasonText = {
      account_not_liquidatable: "HF 未低于 1",
      no_liquidation_candidate: "没有可执行资产对",
      profit_below_minimum: "预估净收益低于 1U",
      quote_failed: "报价失败",
      static_call_required: "等待静态预演",
      missing_executor: "缺少执行器",
      auto_pause_active: "自动暂停保护",
    };
    function coreExecutionSummary(row) {
      const execution = row && row.execution ? row.execution : {};
      const reasons = Array.isArray(execution.blocked_reasons) ? execution.blocked_reasons : (row.blocked_reasons || []);
      const latest = execution.latest_attempt || {};
      const resultCode = execution.execution_result || latest.state || "not_submitted";
      const resultText = coreExecutionResultText[resultCode] || resultCode;
      const historical = latest && latest.stale && latest.state
        ? `历史记录（本轮未提交）：${coreExecutionResultText[latest.state] || latest.state}`
        : "";
      const lowValueNoCandidate = execution.value_state === "manual_test_under_1u" && reasons.includes("no_liquidation_candidate");
      const detail = lowValueNoCandidate
        ? "预计净收益低于 1U，且没有可执行资产对；本轮不提交，仅保留手工测试标记。"
        : historical || reasons.map(reason => coreReasonText[reason] || reason).join("、") || "暂无执行记录";
      return {
        value: coreValueText[execution.value_state] || coreProfitAssessment(row),
        status: coreExecutionStatusText[execution.execution_status] || "待判定",
        result: resultText,
        reason: detail,
      };
    }
    function coreExecutionCounts(rows) {
      return rows.reduce((counts, row) => {
        const state = (row.execution || {}).value_state;
        if (state === "worth_executing") counts.auto += 1;
        else if (state === "manual_test_under_1u") counts.manual += 1;
        else counts.blocked += 1;
        return counts;
      }, { auto: 0, manual: 0, blocked: 0 });
    }
    function renderPager(id, meta, onPage) {
      const page = Math.max(1, Number(meta && meta.page || 1));
      const pageCount = Math.max(1, Number(meta && meta.page_count || 1));
      const total = Math.max(0, Number(meta && meta.total_count || 0));
      const node = $(id);
      if (!node) return;
      node.innerHTML = `<button class="compact" data-page-action="prev" ${page <= 1 ? "disabled" : ""}>上一页</button><span>第 ${page}/${pageCount} 页 · 共 ${total} 条</span><button class="compact" data-page-action="next" ${page >= pageCount ? "disabled" : ""}>下一页</button>`;
      node.querySelector('[data-page-action="prev"]').onclick = () => page > 1 && onPage(page - 1);
      node.querySelector('[data-page-action="next"]').onclick = () => page < pageCount && onPage(page + 1);
    }
    const accountScanStatusText = status => ({
      ENTER: "进入账户扫描",
      LOADING_ACCOUNT_POOL: "读取账户池",
      SHOWING_ACCOUNT_POOL: "显示账户池",
      PREPARING: "准备扫描",
      RESOLVING_WINDOW: "计算扫描窗口",
      RESOLVING_BLOCKS: "解析区块范围",
      SCANNING_EVENTS: "扫描借贷事件",
      SAVING_ACCOUNTS: "保存账户",
      PREPARING_BACKFILL: "准备一年查漏",
      RESOLVING_BACKFILL_RANGE: "解析查漏范围",
      SCANNING_HISTORICAL_EVENTS: "扫描历史事件",
      VERIFYING_ACCOUNT_POOL: "校验账户池",
      AUTO_COMPLETED: "自动扫描完成",
      MANUAL_COMPLETED: "手工扫描完成",
      COMPLETED: "扫描完成",
      SKIPPED: "扫描跳过",
      ERROR: "扫描异常",
    }[String(status || "")] || String(status || "未知状态"));
    const discoveryStageText = stage => ({
      idle: "空闲",
      window: "计算扫描窗口",
      borrowers: "扫描借贷账户",
      configuration: "检查配置",
    }[String(stage || "")] || String(stage || "空闲"));
    const accountPoolResultText = result => ({
      ACCOUNT_POOL_MISSING: "缺少账户池",
      ACCOUNT_POOL_EMPTY: "账户池为空",
      ACCOUNT_POOL_INCOMPLETE: "账户池未完整",
      ACCOUNT_POOL_READY: "账户池就绪",
    }[String(result || "")] || String(result || "-"));
    const scanReasonText = text => ({
      "discovery interval not reached": "扫描间隔未到，系统按配置暂缓本次发现扫描。",
      "historical backfill complete": "历史查漏窗口已完成，系统跳过本次查漏。",
      "account pool is empty": "账户池为空，需要先扫描真实借贷账户。",
      "no active liquidation accounts": "当前没有活跃借贷账户，需要继续账户发现扫描。",
      "account pool has accounts but no completed scan window": "账户池已有账户，但缺少已完成的扫描窗口。",
    }[String(text || "")] || text || "");

    async function enforceDebtPoolPrereq() {
      const params = new URLSearchParams(location.search);
      if (!["/", "/liquidation"].includes(location.pathname) || params.get("account_pool_ready") === "1") return;
      try {
        const s = await api("/api/debt-pool/state");
        const marketAlert = s.status === "MARKET_ALERT_RECEIVED" && !(s.context && s.context.account_pool && s.context.account_pool.ready);
        if (s.status === "NEED_ACCOUNT_POOL" || marketAlert) {
          const reason = s.status === "MARKET_ALERT_RECEIVED" ? (s.context && s.context.market_event && s.context.market_event.trigger_reason) || s.result || "market_volatility_alert" : s.result || "missing_or_empty_or_incomplete_account_pool";
          const eventId = (s.context && s.context.market_event && s.context.market_event.event_id) || s.source_event_id || "";
          location.href = `/account-scan?from=debt_pool&reason=${encodeURIComponent(reason)}${eventId ? `&event_id=${encodeURIComponent(eventId)}` : ""}&auto_return=debt_pool`;
        }
      } catch (e) {}
    }

    async function autoReturnToDebtPoolIfReady() {
      const params = new URLSearchParams(location.search);
      if (location.pathname !== "/account-scan" || params.get("auto_return") !== "debt_pool") return;
      try {
        const s = await api("/api/account-pool/state");
        if (s.ready) {
          location.href = "/liquidation?from=account_scan&account_pool_ready=1";
        } else {
          $("message").textContent = s.reason || "账户池尚未就绪";
        }
      } catch (e) {
        $("message").textContent = e.message;
      }
    }

    function openAccountDetail(account, marketId = "", chainId = "") {
      if (!account) return;
      $("accountDetailTitle").textContent = `账户详情 ${account}`;
      const query = new URLSearchParams({ account, embed: "1" });
      if (marketId) query.set("market_id", marketId);
      if (chainId) query.set("chain_id", chainId);
      $("accountDetailFrame").src = `/liquidation/account?${query}`;
      $("accountDetailDialog").showModal();
    }

    function bindAccountDetailButtons(root) {
      (root || document).querySelectorAll("button[data-account]").forEach(btn => {
        btn.onclick = () => openAccountDetail(btn.dataset.account, btn.dataset.marketId || "", btn.dataset.chainId || "");
      });
    }

    function renderSystemInfo(info) {
      const data = info || {};
      const policy = data.scan_policy || {};
      $("systemVersion").textContent = data.version || "-";
      $("systemVersionMeta").textContent = `核心 ${policy.core_opportunity_refresh_seconds ?? 1}s | 高频 ${Math.round((policy.high_frequency_refresh_seconds ?? 300) / 60)}m | 普通 ${Math.round((policy.borrow_health_refresh_seconds ?? 1800) / 60)}m`;
    }

    function renderDaemonStatus(data) {
      const daemon = data || {};
      const observer = daemon.observer || {};
      const engine = daemon.engine || {};
      const market = daemon.market || {};
      const accountScan = daemon.account_scan || {};
      const state = String(daemon.state || "unknown").toLowerCase();
      const stateText = {
        starting: "启动中",
        running: "运行中",
        degraded: "降级运行",
        stopped: "已停止",
        error: "错误",
      }[state] || (daemon.state || "未知");
      const freshText = market.fresh ? "新鲜" : "过期";
      const missingText = Array.isArray(market.missing_snapshot_symbols) && market.missing_snapshot_symbols.length
        ? `缺失 ${market.missing_snapshot_symbols.join(", ")}`
        : "快照完整";
      $("daemonState").textContent = stateText;
      $("daemonState").className = state === "running" ? "positive" : (state === "error" ? "negative" : "status-candidate");
      $("daemonStateReason").textContent = daemon.last_error ? String(daemon.last_error) : "守护进程负责接收行情快照、驱动清算引擎并同步账户扫描状态，Aave 仍是最终判断源";
      $("daemonObserverState").textContent = observer.state || (observer.enabled ? "启用" : "未启用");
      $("daemonObserverMeta").textContent = [observer.env_symbols || "-", observer.display_symbols ? `显示 ${observer.display_symbols}` : "", observer.heartbeat_age_seconds ? `心跳 ${Number(observer.heartbeat_age_seconds).toFixed(1)}s` : ""].filter(Boolean).join(" | ");
      $("daemonEngineState").textContent = engine.started ? (engine.mode === "auto" ? "自动模式" : "观察模式") : "未启动";
      $("daemonEngineMeta").textContent = [engine.auto_execute ? "满足条件将自动提交" : "不会自动下单", engine.manual_test_completed ? "手工测试完成" : "手工测试未完成"].join(" | ");
      $("daemonMarketState").textContent = `${Number(market.snapshot_count || 0)} 快照`;
      $("daemonMarketMeta").textContent = [freshText, market.display_symbols ? `显示 ${market.display_symbols.join(", ")}` : "显示 -", missingText].join(" | ");
      $("daemonScanState").textContent = accountScan.state || "unknown";
      $("daemonScanMeta").textContent = accountScan.stage || "等待扫描";
    }

    async function refreshSystemInfo() {
      try {
        renderSystemInfo(await api("/api/system-info"));
      } catch (e) {
        $("systemVersionMeta").textContent = e.message;
      }
    }

    async function refreshMarketScope(forceRender = false) {
      try {
        const query = appendMarketParams(new URLSearchParams());
        query.set("include_chain_config", "false");
        const d = await api(`/api/chain-selection?feature=liquidation${query.toString() ? `&${query}` : ""}`);
        latestChainSelectionPayload = d;
        const selected = d.selected || {};
        selectedMarket.market_id = selected.market_id || selectedMarket.market_id || "";
        selectedMarket.chain_id = selected.chain_id || selectedMarket.chain_id || "";
        renderProtocolFilter();
        return selected;
      } catch (e) {
        $("message").textContent = e.message;
        return null;
      }
    }

    async function refreshDaemonStatus() {
      try {
        renderDaemonStatus(await api("/api/liquidation/daemon/status"));
      } catch (e) {
        $("daemonState").textContent = "错误";
        $("daemonState").className = "negative";
        $("daemonStateReason").textContent = e.message;
      }
    }

    function renderAccountScanState(state, statusPayload) {
      const panel = $("accountScanStatusPanel");
      if (!panel || !isAccountScanPage()) return;
      panel.hidden = false;
      const ctx = state && state.context ? state.context : {};
      const pool = ctx.account_pool || {};
      const discovery = ctx.discovery || {};
      const backfill = ctx.backfill || {};
      const bg = statusPayload && statusPayload.background_activity ? statusPayload.background_activity : {};
      const discoveryBg = bg.liquidation_discovery || {};
      const backfillBg = bg.liquidation_account_backfill || {};
      const running = Boolean(discovery.running || backfill.running || discoveryBg.running || backfillBg.running);
      const error = discovery.error || backfill.error || state.last_error || discoveryBg.detail || backfillBg.detail || "";
      const reason = scanReasonText(discovery.reason || state.result || state.message || pool.reason || "");
      const headline = accountScanStatusText(state.status);
      $("accountScanState").textContent = running ? `${headline}中` : headline;
      $("accountScanState").className = error ? "negative" : (running ? "status-candidate" : "positive");
      panel.className = `card state-panel ${error ? "error" : (state.status === "SKIPPED" || !pool.ready ? "warning" : "")}`;
      if (error) {
        $("accountScanReason").textContent = `账户扫描没有继续推进：${error}`;
      } else if (reason) {
        $("accountScanReason").textContent = reason;
      } else if (running) {
        $("accountScanReason").textContent = "账户扫描正在运行，按钮会暂时禁用以避免重复任务。";
      } else if (pool.ready) {
        $("accountScanReason").textContent = "账户池已就绪，可以返回债务池继续健康度扫描。";
      } else {
        $("accountScanReason").textContent = "账户池尚未就绪，请继续扫描账户池或启动一年查漏补缺。";
      }
      $("accountScanPoolState").textContent = accountPoolResultText(pool.result);
      $("accountScanPoolState").className = pool.ready ? "positive" : "status-candidate";
      $("accountScanPoolMeta").textContent = `${pool.account_count ?? 0} 个账户 | ${scanReasonText(pool.reason) || "-"}`;
      $("accountScanDiscoveryState").textContent = discovery.running || discoveryBg.running ? "运行中" : discoveryStageText(discovery.stage || discoveryBg.stage);
      $("accountScanDiscoveryState").className = discovery.error || discoveryBg.detail ? "negative" : (discovery.running || discoveryBg.running ? "status-candidate" : "positive");
      $("accountScanDiscoveryMeta").textContent = discoveryBg.feedback || `${discoveryStageText(discovery.stage)} | 开始 ${fmtTime(discovery.started_at)} | 结束 ${fmtTime(discovery.finished_at)}`;
      $("accountScanBackfillState").textContent = backfill.running || backfillBg.running ? "运行中" : discoveryStageText(backfill.stage || backfillBg.stage);
      $("accountScanBackfillState").className = backfill.error || backfillBg.detail ? "negative" : (backfill.running || backfillBg.running ? "status-candidate" : "positive");
      $("accountScanBackfillMeta").textContent = backfillBg.feedback || `${discoveryStageText(backfill.stage)} | 开始 ${fmtTime(backfill.started_at)} | 结束 ${fmtTime(backfill.finished_at)}`;
      $("accountScanBackgroundState").textContent = bg.active ? "后台忙" : "空闲";
      $("accountScanBackgroundState").className = bg.active ? "status-candidate" : "positive";
      $("accountScanBackgroundMeta").textContent = bg.summary || "无后台账户扫描任务";
    }

    async function refreshAccountScanState() {
      const panel = $("accountScanStatusPanel");
      if (!panel || !isAccountScanPage()) return;
      const results = await Promise.allSettled([api("/api/account-scan/state"), api("/api/status")]);
      const state = results[0].status === "fulfilled" ? results[0].value : { status: "ERROR", message: results[0].reason.message || String(results[0].reason), context: {} };
      const statusPayload = results[1].status === "fulfilled" ? results[1].value : {};
      renderAccountScanState(state, statusPayload);
    }

    function renderGapFillStatus(d) {
      const running = Boolean(d && d.running);
      const progress = d && d.progress ? d.progress : {};
      const result = d && d.last_result ? d.last_result : null;
      const count = Number.isFinite(progress.discovered_count) ? progress.discovered_count : (result && Number.isFinite(result.count) ? result.count : 0);
      const percent = Number.isFinite(progress.percent) ? `${Number(progress.percent).toFixed(2)}%` : "-";
      $("accountPoolGapFillBtn").disabled = running || scanningGapFill;
      $("accountPoolGapStopBtn").disabled = !running;
      $("accountPoolGapFillBtn").textContent = running ? "查漏中" : "一年查漏补缺";
      $("accountPoolWindow").textContent = running ? `查漏 ${d.stage || "running"} | ${percent} | 已发现 ${count}` : $("accountPoolWindow").textContent;
    }

    function renderAccounts(d) {
      const rows = d.accounts || [];
      const visibleRows = filterRowsByProtocol(rows);
      latestAccountsPayload = d;
      const w = d.registry_window || {};
      const pagination = d.pagination || { page: 1, page_count: 1, total_count: d.count || rows.length };
      accountPageState.page = Number(pagination.page || 1);
      accountPageState.pageCount = Number(pagination.page_count || 1);
      $("accountPoolCount").textContent = visibleRows.length;
      const fileBackedAccountPool = d.source === "bnb_venus_current_health_pool";
      const poolTierCounts = w.pool_tier_counts || {};
      const accountPoolScanMeta = fileBackedAccountPool
        ? `当前池 ${w.active_count ?? visibleRows.length}/${w.total_count ?? visibleRows.length} | 已扫 ${w.health_report_count ?? "-"} | 待扫 ${w.pending_health_count ?? "-"} | 核心 ${poolTierCounts.core ?? 0} | 高频 ${poolTierCounts.high_frequency ?? 0} | 普通 ${poolTierCounts.borrow_health ?? 0}`
        : `来源 ${d.source || "-"} | 市场 ${w.market_id || "-"} | 链 ${w.chain_id || "-"} | 协议 ${currentSelectedProtocols().join(", ") || "全部"} | 每页 ${pagination.page_size || PAGE_SIZE}`;
      $("accountPoolMeta").textContent = accountPoolScanMeta;
      $("accountPoolWindow").textContent = `窗口 ${fmtTime(w.earliest_scan_start_at)} ~ ${fmtTime(w.latest_scan_end_at)} | 长期保留`;
      $("accountPoolBody").innerHTML = visibleRows.map(row => {
        const hf = Number(row.last_health_factor);
        return `<tr><td>${row.chain_id || "-"}</td><td>${row.market_id || row.network || "-"}</td><td>${row.account || "-"}</td><td>${row.source || d.source || "-"}</td><td class="${hfCls(row.last_health_factor_band)}">${row.last_health_display || (Number.isFinite(hf) ? hf.toFixed(3) : "-")}</td><td class="${poolTierCls(row.pool_tier)}">${poolTierText(row.pool_tier)}</td><td class="${statusCls(row.last_status)}">${row.last_status || "-"}</td><td>${detailButton(row)}</td></tr>`;
      }).join("") || '<tr><td colspan="8">暂无账户池记录</td></tr>';
      bindAccountDetailButtons($("accountPoolBody"));
      renderPager("accountPoolPager", pagination, page => {
        accountPageState.page = page;
        refreshAccounts();
      });
    }


    function renderBorrowPool(d) {
      const rows = Array.isArray(d && d.rows) ? d.rows : [];
      const visibleRows = filterRowsByProtocol(rows);
      latestBorrowPayload = d;
      const s = d && d.summary ? d.summary : {};
      const tiers = d && d.tiers ? d.tiers : {};
      const accountTiers = s.account_tiers || {};
      const batch = d.latest_batch || s.latest_batch || {};
      const running = Boolean(s.scan_running) || scanningBorrow;
      const blocked = Boolean(s.scan_blocked) || s.configured === false || s.db_ready === false;
      const fileBacked = s.scan_response_source === "file_observation";
      const responseSourceText = { chain_scan: "链上扫描", database_display: "数据库显示", cached_scan_suppressed: "缓存抑制", file_observation: "文件观察" }[s.scan_response_source] || "-";
      $("borrowPoolCount").textContent = visibleRows.length;
      $("borrowPoolMeta").textContent = blocked
        ? (s.error || borrowPoolBlockReasonText(s.scan_blocked_reason))
        : (fileBacked
          ? `普通 ${s.ordinary_account_count ?? visibleRows.length} | 已扫 ${s.scanned_account_count ?? "-"}/${s.current_pool_account_count ?? s.registry_account_count ?? "-"} | 待扫 ${s.pending_account_count ?? "-"}`
          : (Number.isFinite(s.watch_health_factor) ? `HF < ${Number(s.watch_health_factor).toFixed(2)} | 每页 ${d.pagination?.borrow_health?.page_size || PAGE_SIZE}` : "等待中"));
      $("borrowPoolWorstHF").textContent = Number.isFinite(s.worst_health_factor) ? Number(s.worst_health_factor).toFixed(3) : "-";
      $("borrowPoolWorstMeta").textContent = s.worst_account || "暂无风险账户";
      $("borrowPoolScanState").textContent = blocked ? borrowPoolBlockReasonText(s.scan_blocked_reason) : (running ? "扫描中" : "已就绪");
      $("borrowPoolScanState").className = blocked ? "negative" : (running ? "status-candidate" : "positive");
      $("borrowPoolScanMeta").textContent = blocked ? (s.next_action || s.error || "等待数据库恢复") : (running && s.scan_finished_at ? `本轮扫描中 | 上轮完成 ${fmtTime(s.scan_finished_at)}` : (s.scan_finished_at ? `完成 ${fmtTime(s.scan_finished_at)}` : (s.stage ? `阶段 ${s.stage}` : (s.error || "等待扫描"))));
      $("databaseState").textContent = fileBacked ? "文件已就绪" : (s.db_ready === true ? "已连接" : borrowPoolBlockReasonText(s.scan_blocked_reason));
      $("databaseState").className = fileBacked || s.db_ready === true ? "positive" : "negative";
      $("databaseMeta").textContent = fileBacked ? "读取 BNB 当前健康池" : (s.db_ready === true ? "可读取扫描数据" : (s.next_action || s.error || "等待数据库配置"));
      $("borrowPoolHealthMeta").textContent = blocked ? (s.next_action || s.error || "数据库未就绪，扫描暂停") : (s.error || `响应 ${responseSourceText} | 阶段 ${s.stage || "idle"} | 策略 ${scanStrategyText(s.scan_strategy)} | 来源 ${s.source_account_count ?? "-"} | 选中 ${s.selected_account_count ?? "-"} | 已扫 ${s.scanned_account_count ?? "-"} | block ${s.block_number ?? "-"} | 进入 ${s.entered_count ?? "-"} 退出 ${s.exited_count ?? "-"}`);
      $("accountTierCounts").textContent = `${accountTiers.hot_count ?? 0}/${accountTiers.warm_count ?? 0}/${accountTiers.cold_count ?? 0}`;
      $("accountTierMeta").textContent = `注册热/温/冷 | 转化率 ${Number.isFinite(s.risk_pool_conversion_rate) ? (s.risk_pool_conversion_rate * 100).toFixed(2) + "%" : "-"}`;
      $("corePoolCount").textContent = tiers.core_opportunity_count ?? 0;
      const coreRows = tiers.core_opportunity_rows || [];
      const coreCounts = coreExecutionCounts(coreRows);
      $("corePoolMeta").textContent = blocked ? "等待数据库" : `自动候选 ${coreCounts.auto} | 手工测试 ${coreCounts.manual} | 阻断/观察 ${coreCounts.blocked}`;
      $("highFrequencyPoolCount").textContent = tiers.high_frequency_count ?? 0;
      $("highFrequencyPoolMeta").textContent = "HF 1.0 至 1.1";
      $("latestBatchState").textContent = fileBacked ? "当前健康池" : (batch.status || "-");
      $("latestBatchMeta").textContent = fileBacked
        ? `批次仅过程记录 | 本轮 ${batch.scanned_count || s.selected_account_count || 0} | 已扫 ${s.scanned_account_count ?? "-"}/${s.current_pool_account_count ?? s.registry_account_count ?? "-"}`
        : (batch.id ? `#${batch.id} | ${batch.scanned_count || 0}/${batch.account_count || 0} | 错误 ${batch.error_count || 0}` : (s.error || "暂无批次"));
      $("borrowPoolScanBtn").disabled = running || blocked;
      $("borrowPoolScanBtn").textContent = blocked ? "等待数据库" : (running ? "扫描中" : "实时扫描借贷池");
      $("borrowPoolMeta").textContent = `${$("borrowPoolMeta").textContent} | 协议 ${currentSelectedProtocols().join(", ") || "全部"}`;
      $("borrowPoolBody").innerHTML = visibleRows.map(row => `<tr><td>${marketLabel(row)}</td><td>${protocolLabel(row)}</td><td>${row.account || "-"}</td><td class="${hfCls(healthBand(row))}">${healthText(row)}</td><td class="${statusCls(row.status)}">${row.status || "-"}</td><td>${moneyText(row.total_collateral_base)}</td><td>${moneyText(row.total_debt_base)}</td><td>${Number.isFinite(row.candidate_count) ? row.candidate_count : "-"}</td><td>${fmtTime(row.last_scanned_at)}</td><td>${detailButton(row)}</td></tr>`).join("") || '<tr><td colspan="10">暂无普通借贷账户</td></tr>';
      $("highFrequencyPoolBody").innerHTML = filterRowsByProtocol(tiers.high_frequency_rows || []).map(row => `<tr><td>${marketLabel(row)}</td><td>${protocolLabel(row)}</td><td>${row.account || "-"}</td><td class="${hfCls(healthBand(row))}">${healthText(row)}</td><td>${moneyText(row.priority_score)}</td><td>${moneyText(row.total_debt_base)}</td><td>${Number.isFinite(row.candidate_count) ? row.candidate_count : "-"}</td><td>${fmtTime(row.last_scanned_at)}</td><td>${detailButton(row)}</td></tr>`).join("") || '<tr><td colspan="9">暂无高频更新账户</td></tr>';
      $("coreOpportunityBody").innerHTML = filterRowsByProtocol(coreRows).map(row => {
        const execution = coreExecutionSummary(row);
        return `<tr><td>${marketLabel(row)}</td><td>${protocolLabel(row)}</td><td>${row.account || "-"}</td><td class="${hfCls(healthBand(row))}">${healthText(row)}</td><td>${moneyText(row.priority_score)}</td><td>${row.best_debt_asset || "-"}</td><td>${row.best_collateral_asset || "-"}</td><td>${row.debt_to_cover_units || "-"}</td><td>${moneyText(row.estimated_operator_net_profit_usd)}</td><td>${execution.value}</td><td>${execution.status}</td><td title="${execution.reason}">${execution.result}</td><td title="${execution.reason}">${execution.reason}</td><td>${row.quote_viable ? "viable" : "pending"}</td><td>${row.static_call_status || "-"}</td><td>${detailButton(row)}</td></tr>`;
      }).join("") || '<tr><td colspan="16">暂无核心机会</td></tr>';
      bindAccountDetailButtons($("borrowPoolBody"));
      bindAccountDetailButtons($("highFrequencyPoolBody"));
      bindAccountDetailButtons($("coreOpportunityBody"));
      const pagination = d.pagination || {};
      renderPager("borrowPoolPager", pagination.borrow_health, page => {
        borrowPageState.risk = page;
        refreshBorrowPool();
      });
      renderPager("highFrequencyPoolPager", pagination.high_frequency, page => {
        borrowPageState.high = page;
        refreshBorrowPool();
      });
      renderPager("coreOpportunityPager", pagination.core_opportunity, page => {
        borrowPageState.core = page;
        refreshBorrowPool();
      });
    }


    async function refreshAccounts(force = false) {
      if (refreshingAccounts) return;
      refreshingAccounts = true;
      try {
        const query = appendMarketParams(new URLSearchParams({ page: String(accountPageState.page), page_size: String(PAGE_SIZE) }));
        if (force) query.set("force", "1");
        renderAccounts(await api(`/api/liquidation/accounts?${query}`));
      } catch (e) {
        $("message").textContent = e.message;
      } finally {
        refreshingAccounts = false;
      }
    }

    async function refreshGapFillStatus() {
      try { renderGapFillStatus(await api("/api/liquidation/account-backfill")); } catch (e) { $("message").textContent = e.message; }
    }

    async function refreshBorrowPool(options = {}) {
      if (refreshingBorrow) return;
      refreshingBorrow = true;
      try {
        const query = appendMarketParams(new URLSearchParams({
          page_size: String(PAGE_SIZE),
          risk_page: String(borrowPageState.risk),
          high_page: String(borrowPageState.high),
          core_page: String(borrowPageState.core),
        }));
        renderBorrowPool(await api(`/api/liquidation/borrow-pool?${query}`));
        if (options.showMessage) $("message").textContent = `风险池显示已刷新：${new Date().toLocaleString([], {hour12:false})}`;
      } catch (e) {
        $("message").textContent = e.message;
      } finally {
        refreshingBorrow = false;
      }
    }

    function renderPauseGuard(d) {
      const level = Number(d && d.circuit_breaker_level || 0);
      const paused = Boolean(d && d.paused);
      $("circuitBreakerLevel").textContent = level;
      $("circuitBreakerLevel").className = level >= 3 ? "negative" : (level > 0 ? "status-candidate" : "positive");
      $("circuitBreakerLevelMeta").textContent = d && d.configured ? "已配置" : "未配置";
      $("circuitBreakerPaused").textContent = paused ? "是" : "否";
      $("circuitBreakerPaused").className = paused ? "negative" : "positive";
      $("circuitBreakerPauseMeta").textContent = (d && d.pause_reason) || "未暂停";
      $("circuitBreakerFailures").textContent = d && Number.isFinite(Number(d.consecutive_failure_count)) ? Number(d.consecutive_failure_count) : 0;
      $("circuitBreakerFailureMeta").textContent = `最近失败 ${fmtTime(d && d.last_failure_at)}`;
      $("circuitBreakerCooldown").textContent = `x${Number(d && d.cooldown_multiplier || 1).toFixed(1)}`;
      $("circuitBreakerUpdatedAt").textContent = `更新 ${fmtTime(d && d.updated_at)}`;
      $("pauseGuardMessage").textContent = paused ? ((d && d.pause_reason) || "已暂停") : "就绪";
    }

    function renderStaticCallPanel(attemptRows, sampleRows) {
      const staticRows = (attemptRows || []).filter(row => row.mode === "static_call" || String(row.state || "").startsWith("static_call"));
      const passed = staticRows.filter(row => row.state === "static_call_passed").length;
      const failed = staticRows.filter(row => row.state === "static_call_failed").length;
      const reasons = {};
      (sampleRows || []).filter(row => String(row.failure_type || "").includes("static_call")).forEach(row => {
        const reason = row.failure_reason || row.failure_type || "unknown";
        reasons[reason] = (reasons[reason] || 0) + 1;
      });
      const top = Object.entries(reasons).sort((a, b) => b[1] - a[1])[0];
      $("staticCallPassedCount").textContent = passed;
      $("staticCallPassedMeta").textContent = `${staticRows.length} 条最近静态调用`;
      $("staticCallFailedCount").textContent = failed;
      $("staticCallFailedCount").className = failed ? "negative" : "positive";
      $("staticCallFailedMeta").textContent = failed ? "检查失败原因" : "近期无失败";
      $("staticCallTopReason").textContent = top ? top[0] : "-";
      $("staticCallTopReasonMeta").textContent = top ? `${top[1]} 个样本` : "无失败样本";
      $("staticCallBody").innerHTML = staticRows.slice(0, 20).map(row => `<tr><td>${row.id}</td><td>${row.account || "-"}</td><td>${row.state || "-"}</td><td>${row.error || (row.blocked_reasons || []).join(", ") || "-"}</td><td>${fmtTime(row.created_at)}</td></tr>`).join("") || '<tr><td colspan="5">暂无静态模拟记录</td></tr>';
    }

    function renderLiquidationStats(attemptRows, sampleRows, stats = {}) {
      const rows = attemptRows || [];
      const success = Number(stats.confirmed_success ?? rows.filter(row => row.state === "confirmed_success").length);
      const failed = Number(stats.confirmed_failed ?? rows.filter(row => row.state === "confirmed_failed").length);
      const confirmed = success + failed;
      const profits = [];
      rows.forEach(row => {
        const candidates = [row.profit_usdc, row.profitUsdc, row.net_profit_usdc, row.netProfitUsdc, row.receipt && row.receipt.profit_usdc, row.request_payload && row.request_payload.amounts && row.request_payload.amounts.profit && row.request_payload.amounts.profit.operator_net_profit_estimate_usd];
        const value = candidates.find(item => Number.isFinite(Number(item)));
        if (value !== undefined) profits.push(Number(value));
      });
      const reasonCounts = {};
      (sampleRows || []).forEach(row => {
        const reason = row.failure_reason || row.failure_type || "unknown";
        reasonCounts[reason] = (reasonCounts[reason] || 0) + 1;
      });
      const reasons = Object.entries(reasonCounts).sort((a, b) => b[1] - a[1]);
      const top = reasons[0];
      const average = profits.length ? profits.reduce((sum, value) => sum + value, 0) / profits.length : null;
      $("liquidationStatsDailyCount").textContent = rows.length;
      $("liquidationStatsDailyMeta").textContent = `${success} 成功 / ${failed} 失败`;
      $("liquidationStatsSuccessRate").textContent = confirmed ? `${((success / confirmed) * 100).toFixed(1)}%` : "-";
      $("liquidationStatsSuccessMeta").textContent = confirmed ? `${confirmed} 条已确认结果` : "暂无已确认结果";
      $("liquidationStatsAvgProfit").textContent = average === null ? "-" : moneyText(average);
      $("liquidationStatsAvgProfitMeta").textContent = profits.length ? `${profits.length} 条利润记录` : "未记录";
      $("liquidationStatsTopFailure").textContent = top ? top[0] : "-";
      $("liquidationStatsTopFailureMeta").textContent = top ? `${top[1]} 个样本` : "无失败样本";
      $("liquidationStatsFailureBody").innerHTML = reasons.map(([reason, count]) => `<tr><td>${reason}</td><td>${count}</td></tr>`).join("") || '<tr><td colspan="2">无失败样本</td></tr>';
    }

    async function refreshPauseGuard() {
      if (isBnbVenusMarket()) {
        $("circuitBreakerLevel").textContent = "OBS";
        $("circuitBreakerLevelMeta").textContent = "BNB Venus observation-only";
        $("circuitBreakerPaused").textContent = "No";
        $("circuitBreakerPauseMeta").textContent = "No execution submission";
        $("circuitBreakerFailures").textContent = "0";
        $("circuitBreakerFailureMeta").textContent = "File-backed observation";
        $("circuitBreakerCooldown").textContent = "-";
        $("circuitBreakerUpdatedAt").textContent = "No database guard needed";
        $("pauseGuardMessage").textContent = "BNB Venus observation-only";
        return;
      }
      try { renderPauseGuard(await api("/api/liquidation/pause-guard")); } catch (e) { $("pauseGuardMessage").textContent = e.message; }
    }

    async function clearPauseGuard() {
      const b = $("pauseGuardClearBtn");
      const old = b.textContent;
      b.disabled = true;
      b.textContent = "清除中";
      try {
        renderPauseGuard(await api("/api/liquidation/pause-guard/clear", { method: "POST", body: "{}" }));
        await refreshAudit();
      } catch (e) {
        $("pauseGuardMessage").textContent = e.message;
      } finally {
        b.disabled = false;
        b.textContent = old;
      }
    }

    async function refreshAudit() {
      if (isBnbVenusMarket()) {
        $("attemptBody").innerHTML = '<tr><td colspan="8">BNB Venus observation-only, no execution attempts</td></tr>';
        $("circuitBreakerHistoryBody").innerHTML = '<tr><td colspan="7">BNB Venus observation-only, no guard history</td></tr>';
        $("failureSampleBody").innerHTML = '<tr><td colspan="11">BNB Venus observation-only, no failure samples</td></tr>';
        renderStaticCallPanel([], []);
        renderLiquidationStats([], [], {});
        return;
      }
      try {
        const auditQuery = appendMarketParams(new URLSearchParams({ limit: "20" }));
        const attempts = await api(`/api/liquidation/execution-attempts?${auditQuery}`);
        const attemptRows = attempts.attempts || [];
        $("attemptBody").innerHTML = attemptRows.map(row => `<tr><td>${row.id}</td><td>${row.account || "-"}</td><td>${row.mode || "-"}</td><td>${row.state || "-"}</td><td>${row.execution_phase || "-"}</td><td>${(row.blocked_reasons || []).join(", ") || "-"}</td><td>${row.error || "-"}</td><td>${fmtTime(row.created_at)}</td></tr>`).join("") || '<tr><td colspan="8">暂无执行尝试</td></tr>';
        const breakerStates = new Set(["submission_blocked", "submission_failed", "static_call_failed", "confirmed_failed", "confirmed_success"]);
        $("circuitBreakerHistoryBody").innerHTML = attemptRows.filter(row => breakerStates.has(row.state)).map(row => `<tr><td>${row.id}</td><td>${row.account || "-"}</td><td>${row.mode || "-"}</td><td>${row.state || "-"}</td><td>${row.execution_phase || "-"}</td><td>${(row.blocked_reasons || []).join(", ") || row.error || "-"}</td><td>${fmtTime(row.created_at)}</td></tr>`).join("") || '<tr><td colspan="7">暂无保护历史</td></tr>';
        const samples = await api(`/api/liquidation/failure-samples?${auditQuery}`);
        const sampleRows = samples.samples || [];
        renderStaticCallPanel(attemptRows, sampleRows);
        renderLiquidationStats(attemptRows, sampleRows, attempts.stats || {});
        $("failureSampleBody").innerHTML = sampleRows.map(row => {
          const p = row.payload || {};
          const phase = p.execution_phase || row.execution_phase || "-";
          const tx = p.tx_hash || row.tx_hash || "-";
          const receipt = p.receipt_status ?? (p.receipt && p.receipt.status) ?? "-";
          const retry = p.retryable === undefined ? "-" : (p.retryable ? "是" : "否");
          return `<tr><td>${row.id}</td><td>${row.account || "-"}</td><td>${row.failure_type || "-"}</td><td>${phase}</td><td>${tx}</td><td>${receipt}</td><td>${retry}</td><td>${row.failure_reason || "-"}</td><td>${row.debt_asset || "-"}</td><td>${row.collateral_asset || "-"}</td><td>${fmtTime(row.created_at)}</td></tr>`;
        }).join("") || '<tr><td colspan="11">无失败样本</td></tr>';
      } catch (e) {
        $("message").textContent = e.message;
      }
    }

    async function scanBorrowPool() {
      if (scanningBorrow) return;
      scanningBorrow = true;
      const refreshButton = $("borrowPoolRefreshBtn");
      const scanButton = $("borrowPoolScanBtn");
      const refreshText = refreshButton.textContent;
      const scanText = scanButton.textContent;
      refreshButton.disabled = true;
      scanButton.disabled = true;
      refreshButton.textContent = "扫描中";
      scanButton.textContent = "扫描中";
      renderBorrowPool({ rows: [], summary: { scan_running: true } });
      try {
        const query = appendMarketParams(new URLSearchParams({
          page_size: String(PAGE_SIZE),
          risk_page: String(borrowPageState.risk),
          high_page: String(borrowPageState.high),
          core_page: String(borrowPageState.core),
          force: "1",
        }));
        renderBorrowPool(await api(`/api/liquidation/borrow-pool/scan?${query}`, { method: "POST", body: "{}" }));
        $("message").textContent = `风险池健康度已重新链上扫描：${new Date().toLocaleString([], {hour12:false})}`;
      } catch (e) {
        $("message").textContent = e.message;
      } finally {
        scanningBorrow = false;
        refreshButton.disabled = false;
        scanButton.disabled = false;
        refreshButton.textContent = refreshText;
        scanButton.textContent = scanText;
        refreshBorrowPool();
      }
    }

    async function refreshSettings() {
      try {
        const d = await api("/api/liquidation-settings");
        $("liquidationScanIntervalInput").value = Number.isFinite(d.scan_interval_seconds) ? Math.round(d.scan_interval_seconds) : 300;
        $("liquidationDiscoveryIntervalInput").value = Number.isFinite(d.discovery_interval_seconds) ? Math.round(d.discovery_interval_seconds) : 3600;
      } catch (e) {
        $("message").textContent = e.message;
      }
    }

    async function saveSettings() {
      const b = $("liquidationSettingsSaveBtn");
      const old = b.textContent;
      b.disabled = true;
      b.textContent = "保存中";
      try {
        await api("/api/liquidation-settings", { method: "POST", body: JSON.stringify({ scan_interval_seconds: $("liquidationScanIntervalInput").value, discovery_interval_seconds: $("liquidationDiscoveryIntervalInput").value }) });
        $("message").textContent = "清算参数已保存";
      } catch (e) {
        $("message").textContent = e.message;
      } finally {
        b.disabled = false;
        b.textContent = old;
      }
    }

    async function scanAccounts(full = false) {
      if (scanningAccounts) return;
      scanningAccounts = true;
      const b = full ? $("accountPoolRestartBtn") : $("accountPoolScanBtn");
      const old = b.textContent;
      b.disabled = true;
      b.textContent = full ? "重扫中" : "扫描中";
      try {
        const d = await api(`/api/liquidation-discovery${full ? "?full=1" : ""}`, { method: "POST", body: JSON.stringify({ full }) });
        $("message").textContent = d.skipped ? `账户池扫描跳过：${d.reason || "间隔未到"}` : `账户池新增或更新 ${d.count || 0} 个账户`;
        refreshAccounts(true);
        refreshAccountScanState();
        await autoReturnToDebtPoolIfReady();
      } catch (e) {
        $("message").textContent = e.message;
      } finally {
        scanningAccounts = false;
        b.disabled = false;
        b.textContent = old;
      }
    }

    async function startGapFill() {
      if (scanningGapFill) return;
      scanningGapFill = true;
      try {
        const d = await api("/api/liquidation/account-backfill/start", { method: "POST", body: "{}" });
        $("message").textContent = d.started ? "一年查漏补缺已启动" : "查漏补缺已在运行";
        refreshGapFillStatus();
      } catch (e) {
        $("message").textContent = e.message;
      } finally {
        scanningGapFill = false;
      }
    }

    async function stopGapFill() {
      try {
        await api("/api/liquidation/account-backfill/stop", { method: "POST", body: "{}" });
        $("message").textContent = "已请求停止账户查漏补缺";
        refreshGapFillStatus();
      } catch (e) {
        $("message").textContent = e.message;
      }
    }

    $("accountDetailCloseBtn").onclick = () => { $("accountDetailDialog").close(); $("accountDetailFrame").src = ""; };
    $("accountPoolRefreshBtn").onclick = () => refreshAccounts(true);
    $("accountPoolScanBtn").onclick = () => scanAccounts(false);
    $("accountPoolRestartBtn").onclick = () => scanAccounts(true);
    $("accountPoolGapFillBtn").onclick = startGapFill;
    $("accountPoolGapStopBtn").onclick = stopGapFill;
    $("borrowPoolRefreshBtn").onclick = scanBorrowPool;
    $("borrowPoolScanBtn").onclick = scanBorrowPool;
    $("liquidationSettingsSaveBtn").onclick = saveSettings;
    $("pauseGuardRefreshBtn").onclick = refreshPauseGuard;
    $("pauseGuardClearBtn").onclick = clearPauseGuard;
    function rerenderProtocolFilteredViews() {
      renderProtocolFilter();
      if (latestAccountsPayload) renderAccounts(latestAccountsPayload);
      if (latestBorrowPayload) renderBorrowPool(latestBorrowPayload);
    }

    window.addEventListener("chainselectionchanged", event => {
      const detail = event?.detail || {};
      const selected = detail.selected || {};
      if (detail.protocol_only) {
        rerenderProtocolFilteredViews();
        return;
      }
      if (selected.feature && String(selected.feature).toLowerCase() !== "liquidation") return;
      selectedMarket.market_id = selected.market_id || selectedMarket.market_id || "";
      selectedMarket.chain_id = selected.chain_id || selectedMarket.chain_id || "";
      accountPageState.page = 1;
      borrowPageState.risk = 1;
      borrowPageState.high = 1;
      borrowPageState.core = 1;
      refreshMarketScope()
        .then(() => {
          refreshAccounts(true);
          refreshAccountScanState();
          refreshGapFillStatus();
          refreshBorrowPool({ showMessage: true });
          refreshAudit();
        })
        .catch(error => { $("message").textContent = error.message; });
    });

    window.addEventListener("chainselectionprotocolschanged", () => {
      rerenderProtocolFilteredViews();
    });
    async function bootstrapLiquidationPanel() {
      enforceDebtPoolPrereq();
      await refreshMarketScope();
      refreshSystemInfo();
      refreshDaemonStatus();
      refreshSettings();
      refreshAccounts();
      refreshAccountScanState();
      refreshGapFillStatus();
      refreshBorrowPool();
      refreshPauseGuard();
      refreshAudit();
      autoReturnToDebtPoolIfReady();
      setInterval(refreshSystemInfo, 60000);
      setInterval(refreshDaemonStatus, 5000);
      setInterval(refreshBorrowPool, 5000);
      setInterval(refreshAccountScanState, 3000);
      setInterval(refreshGapFillStatus, 2000);
      setInterval(refreshPauseGuard, 5000);
      setInterval(refreshAudit, 15000);
    }
    bootstrapLiquidationPanel();
