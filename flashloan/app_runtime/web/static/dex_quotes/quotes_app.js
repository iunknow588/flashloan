const refreshIntervalKey = "dex-quotes-refresh-interval-seconds";
const autoExecuteEnabledKey = "dex-quotes-auto-execute-enabled";
    const scanIntervalMs = 3000;
    const defaultRefreshIntervalSeconds = 180;
    let refreshTimer = null;
    let refreshInFlight = false;
    let snapshotRefreshTimers = [];
    let scanNowInFlight = false;
    let latestRoutes = [];
    let latestSubmissionRows = [];
    let snapshotRefreshing = false;
    let autoChainReviewStarted = false;
    let autoReviewRunning = false;
    let autoReviewQueued = false;
    let staticRoutesLoaded = false;
    let autoReviewToken = 0;
    let selectionGeneration = 0;
    let activeSelectionSignature = "";
    let activeRouteKey = "";
    let pageState = "init";
    const PAGE_STATE = Object.freeze({
      INIT: "init",
      STATIC_READY: "static_ready",
      SCAN: "scan",
      REVIEW: "review",
      EXECUTION: "execution",
    });
    const pageStateIs = (...states) => states.includes(pageState);
    const setPageState = (nextState, message) => {
      pageState = nextState;
      if (message) $("message").textContent = message;
    };
    const canRefreshSnapshot = () => !refreshInFlight && !snapshotRefreshing && !pageStateIs(PAGE_STATE.REVIEW, PAGE_STATE.EXECUTION);
    const selectionSignature = () => {
      const query = window.ChainSelector?.currentQuery ? window.ChainSelector.currentQuery() : new URLSearchParams(window.location.search);
      return ["chain_id", "market_id", "protocols", "protocol_id", "dex_strategy"].map(key => query.get(key) || "").join("|").toLowerCase();
    };
    const beginSelectionRefresh = message => {
      selectionGeneration += 1; autoReviewToken += 1; autoChainReviewStarted = false; staticRoutesLoaded = false; activeSelectionSignature = selectionSignature(); latestRoutes = [];
      activeRouteKey = ""; pageState = PAGE_STATE.INIT;
      activeExecution = { routeKey: "", state: "", txHash: "", startedAt: 0 };
      $("routeList").innerHTML = `<div class="empty">${esc(message)}</div>`;
      ["routeCount", "completeCount", "readyCount", "autoExecutableCount"].forEach(id => $(id).textContent = "0");
    };
    const focusedRouteKey = () => {
      if (activeRouteKey && latestRoutes.some(route => routeKey(route) === activeRouteKey)) return activeRouteKey;
      return latestRoutes.length ? routeKey(latestRoutes[0]) : "";
    };
    const marketIdChainComponent = marketId => {
      const normalized = String(marketId || "").trim().toLowerCase();
      const match = normalized.match(/^(liquidation|dex-arbitrage)-(\d+)-[a-z0-9_-]+$/);
      return match ? match[2] : "";
    };
    const normalizeScopeQuery = query => {
      const normalized = new URLSearchParams(query);
      const chainId = String(normalized.get("chain_id") || "").trim();
      const marketId = String(normalized.get("market_id") || "").trim();
      const marketChainId = marketIdChainComponent(marketId);
      if (chainId && marketChainId && marketChainId !== chainId) {
        normalized.delete("market_id");
        normalized.delete("protocols");
        normalized.delete("protocol_id");
      }
      if (chainId && !normalized.get("market_id")) {
        const fallback = window.__CHAIN_SELECTOR_DEFAULT__ || {};
        const fallbackMarketId = String(fallback.market_id || "").trim();
        const fallbackChainId = marketIdChainComponent(fallbackMarketId);
        if (fallbackChainId && fallbackChainId === chainId) {
          normalized.set("market_id", fallbackMarketId);
        }
      }
      return normalized;
    };
    const apiPath = path => {
      const scoped = window.ChainSelector?.withSelection ? window.ChainSelector.withSelection(path) : path;
      const url = new URL(scoped, window.location.origin);
      if (!window.ChainSelector?.withSelection) {
        url.search = normalizeScopeQuery(new URLSearchParams(window.location.search)).toString();
      } else if (window.ChainSelector?.normalizeScopeQuery) {
        url.search = window.ChainSelector.normalizeScopeQuery(url.searchParams).toString();
      }
      url.searchParams.set("v", APP_VERSION);
      return url.pathname + url.search;
    };
    async function fetchJson(path) {
      const response = await fetch(apiPath(path), {cache: "no-cache", headers: {Accept: "application/json"}});
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || `请求失败（HTTP ${response.status}）`);
      return data;
    }
    async function fetchJsonWithTimeout(path, timeoutMs = 20000) {
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), timeoutMs);
      try {
        const response = await fetch(apiPath(path), {cache: "no-cache", signal: controller.signal, headers: {Accept: "application/json"}});
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || `请求失败（HTTP ${response.status}）`);
        return data;
      } catch (error) {
        if (error && error.name === "AbortError") throw new Error("请求超时，已停止等待数据");
        throw error;
      } finally {
        clearTimeout(timer);
      }
    }
    function renderScannerResponse(response, quoteRoutesPayload) {
      const responseChainId = response?.chain_id ?? response?.snapshot?.chain_id ?? "";
      const activeChainId = new URLSearchParams(window.location.search).get("chain_id") || "";
      if (responseChainId && activeChainId && String(responseChainId) !== activeChainId) return;
      if (pageStateIs(PAGE_STATE.REVIEW, PAGE_STATE.EXECUTION)) return;
      const scanner = response.scanner || {}; const snapshot = response.snapshot || {}; const data = snapshot.payload || {}; const summary = data.summary || snapshot.summary || {};
      const autoExecuteToggle = document.getElementById("autoExecuteToggle");
      if (autoExecuteToggle) {
        try {
          const backendAutoExecute = Boolean(scanner.auto_execute_enabled);
          autoExecuteToggle.checked = backendAutoExecute;
          if (backendAutoExecute) {
            window.localStorage.setItem(autoExecuteEnabledKey, "true");
          }
        } catch (_) {
          autoExecuteToggle.checked = Boolean(scanner.auto_execute_enabled);
        }
      }
      $("routeCount").textContent = summary.routeCount ?? summary.route_count ?? 0;
      $("completeCount").textContent = summary.completeStaticQuoteCount ?? summary.complete_static_quote_count ?? 0;
      $("readyCount").textContent = summary.executableCandidateCount ?? summary.executable_candidate_count ?? 0;
      const autoExecuteOn = autoExecuteToggle ? Boolean(autoExecuteToggle.checked) : Boolean(scanner.auto_execute_enabled);
      const autoExecutable = summary.autoExecutableClosedLoopCount ?? summary.auto_executable_closed_loop_count ?? 0;
      $("autoExecutableCount").textContent = `${autoExecutable}`;
      $("autoExecutableMeta").textContent = autoExecuteOn && scanner.auto_execute_callback_configured
        ? `${autoExecutable} 条自动扫描复核并链上广播已开启`
        : autoExecuteOn
          ? `${autoExecutable} 条自动扫描复核并链上广播未授权`
          : `${autoExecutable} 条自动扫描复核并链上广播未开启`;
      const reviewedCount = summary.onchainEvidenceReviewedRouteCount ?? 0;
      $("reviewedCount").textContent = `${reviewedCount}`;
      $("reviewedMeta").textContent = `已复核 ${reviewedCount} 条 / 总 ${summary.routeCount ?? 0} 条`;
      const profitableCount = summary.liveProfitableRouteCount ?? summary.live_profitable_route_count ?? 0;
      $("profitableCount").textContent = `${profitableCount}`;
      $("profitableMeta").textContent = `${profitableCount} 条链上复核为正收益`;
      const sessionStats = scanner.session_stats || {};
      const sessionExecuted = sessionStats.executed_count ?? 0;
      const sessionAutoExecuted = sessionStats.auto_executed_count ?? sessionStats.executed_count ?? 0;
      const sessionProfit = sessionStats.positive_profit_amount ?? 0;
      const autoExecutedCount = sessionAutoExecuted;
      const closedLoopScanCount = scanner.closed_loop_scan_count ?? 0;
      const closedLoopReviewCount = scanner.closed_loop_review_count ?? 0;
      const autoBroadcastCount = scanner.auto_broadcast_count ?? scanner.auto_execute_count ?? 0;
      const lastRecordingReviewCount = scanner.last_recording?.review_count ?? 0;
      $("closedLoopScanCount").textContent = `${closedLoopScanCount}`;
      $("closedLoopScanMeta").textContent = lastRecordingReviewCount > 0
        ? `上次闭环 ${lastRecordingReviewCount} 条 | 累计闭环 ${closedLoopScanCount} 次`
        : `累计已完成闭环 ${closedLoopScanCount} 次`;
      $("closedLoopReviewCount").textContent = `${closedLoopReviewCount}`;
      $("closedLoopReviewMeta").textContent = lastRecordingReviewCount > 0
        ? `上次复核 ${lastRecordingReviewCount} 条 | 累计复核 ${closedLoopReviewCount} 条`
        : `累计链上复核尝试 ${closedLoopReviewCount} 条`;
      $("autoExecutedCount").textContent = `${autoExecutedCount}`;
      $("autoExecutedMeta").textContent = sessionStats.reset_at
        ? `统计自 ${new Date(sessionStats.reset_at).toLocaleString("zh-CN", {hour12: false})} | 已执行 ${sessionExecuted} 条 | 自动 ${autoExecutedCount} 条 | 累计广播 ${autoBroadcastCount} 条`
        : autoBroadcastCount > 0
          ? `累计上链广播 ${autoBroadcastCount} 条`
          : autoExecutedCount > 0
            ? `累计自动执行 ${autoExecutedCount} 条`
            : "暂未自动执行";
      $("autoBroadcastCount").textContent = `${autoBroadcastCount}`;
      $("autoBroadcastMeta").textContent = autoBroadcastCount > 0
        ? `累计链上广播 ${autoBroadcastCount} 条`
        : "暂未链上广播";
      $("profitableMeta").textContent = sessionStats.reset_at
        ? `最近清零后正利润 ${numeric(sessionProfit, 6)}U / ${sessionStats.positive_profit_count ?? 0} 条`
        : `${profitableCount} 条链上复核为正收益`;
      const queueCount = (scanner.closed_loop_review_queue?.queued ?? 0) + (scanner.queued_scan_count ?? 0);
      $("queueCount").textContent = `${queueCount}`;
      $("queueMeta").textContent = `复核队列 ${scanner.closed_loop_review_queue?.queued ?? 0} | 扫描排队 ${scanner.queued_scan_count ?? 0}`;
      $("routeSourceMeta").textContent = `${summary.pathSource || "market_observation"} | 观察路线 ${summary.marketObservationRouteCount ?? 0}`;
      const reviewed = summary.onchainEvidenceReviewedRouteCount ?? 0;
      const deferred = summary.onchainEvidencePendingRouteCount ?? summary.liveQuoterRequiredCount ?? 0;
      const failed = summary.onchainEvidenceFailedRouteCount ?? 0;
      $("probeMeta").textContent = `探测 ${numeric(summary.probeNotionalUsd ?? 1000, 2)}U | 已链上复核 ${reviewed} | 待处理 ${deferred}`;
      $("profitMeta").textContent = `链上复核候选 ${summary.chainSubmissionCandidateCount ?? 0} | 报价失败 ${failed} | 记录 ${snapshot.recording?.source === "disabled" ? "关闭" : "开启"}`;
      const uniswapx = data.dexSettlement?.uniswapx || {};
      $("settlement").textContent = uniswapx.supported ? "UniswapX 可用" : "优先 AMM";
      $("settlementMeta").textContent = uniswapx.supported ? "意图报价通道已开启" : "当前链未启用 UniswapX 结算";
      const scannerRoutes = Array.isArray(data.routes) ? data.routes : [];
      const quoteRoutesList = Array.isArray(quoteRoutesPayload?.routes)
        ? quoteRoutesPayload.routes
        : Array.isArray(quoteRoutesPayload?.snapshot?.payload?.routes)
          ? quoteRoutesPayload.snapshot.payload.routes
          : [];
      const hasIncomingRoutes = scannerRoutes.length > 0 || quoteRoutesList.length > 0;
      const previousRoutes = latestRoutes.slice();
      const previousReviewedByKey = new Map(
        previousRoutes
          .filter(route => route && (
            route.__offchainReviewed
            || uiStateOf(route) === ROUTE_UI_STATE.OFFCHAIN_ESTIMATE
            || shouldPreserveRouteState(route)
          ))
          .map(route => [routeKey(route), route])
      );
      if (hasIncomingRoutes || !latestRoutes.length) {
        latestRoutes = routesToRender(scannerRoutes, quoteRoutesPayload).map(route => {
          const reviewed = previousReviewedByKey.get(routeKey(route));
          return reviewed || route;
        });
      } else if (latestRoutes.length) {
        $("routeCount").textContent = latestRoutes.length;
      }
      latestRoutes.forEach(route => {
        if (!route) return;
        if (shouldPreserveRouteState(route)) {
          setRouteUiState(route, uiStateOf(route), {quoteStatus: route.quoteStatus || ""});
        } else if (routeHasCompletedOffchainReview(route)) {
          setRouteUiState(route, inferUiState(route), {quoteStatus: route.quoteStatus || ""});
        } else if (!route.__offchainReviewed) {
          setRouteUiState(route, ROUTE_UI_STATE.OFFCHAIN_PENDING, {quoteStatus: route.quoteStatus || ""});
        }
      });
      if (latestRoutes.length > scannerRoutes.length) {
        const quoteSummary = (quoteRoutesPayload && (quoteRoutesPayload.summary || quoteRoutesPayload.snapshot?.payload?.summary)) || {};
        $("routeCount").textContent = latestRoutes.length;
        $("routeSourceMeta").textContent = `${quoteSummary.pathSource || summary.pathSource || "market_observation"} | 观察路线 ${quoteSummary.marketObservationRouteCount ?? summary.marketObservationRouteCount ?? 0}`;
      }
      $("routeList").innerHTML = renderPoolDirectories(latestRoutes);
      if (latestRoutes.length) scheduleReviewActiveRoute();
      if (quoteRoutesPayload && Array.isArray(quoteRoutesPayload.closedLoopGroups)) {
        renderClosedLoopGroups({groups: quoteRoutesPayload.closedLoopGroups});
      }
      const duration = scanner.last_duration_ms == null ? "-" : `${scanner.last_duration_ms} ms`;
      const scannerState = quoteRoutesPayload?.scannerState || {};
      const dataFreshnessLabel = scannerState.status === "stale_refresh_queued" ? " | 数据源待实时刷新" : (scannerState.status === "fresh" ? " | 数据源新鲜" : "");
      const snapshotUpdatedLabel = scannerState.snapshotUpdatedAt ? ` | 快照 ${esc(scannerState.snapshotUpdatedAt)}` : "";
      $("message").textContent = `${scanner.running ? "扫描器运行中" : "扫描器已停止"} | ${duration} | 扫描次数 ${scanner.scan_count ?? 0}${snapshot.error ? ` | ${snapshot.error}` : ""}${dataFreshnessLabel}${snapshotUpdatedLabel}`;
    }
    function renderStaticRoutes(payload) {
      const generation = selectionGeneration;
      const signature = activeSelectionSignature;
      if (generation !== selectionGeneration || signature !== activeSelectionSignature) return;
      if (pageStateIs(PAGE_STATE.REVIEW, PAGE_STATE.EXECUTION)) return;
      const routes = Array.isArray(payload.routes) ? payload.routes : [];
      latestRoutes = routes.filter(Boolean).map(route => ({...route, reviewMode: "pending_offchain_review", __uiState: ROUTE_UI_STATE.OFFCHAIN_PENDING, __offchainReviewed: false}));
      $("routeCount").textContent = latestRoutes.length;
      $("routeSourceMeta").textContent = "市场观察 | 静态闭环";
      $("completeCount").textContent = "0";
      $("readyCount").textContent = "0";
      $("routeList").innerHTML = renderPoolDirectories(latestRoutes);
      if (Array.isArray(payload.closedLoopGroups) && payload.closedLoopGroups.length) {
        renderClosedLoopGroups({groups: payload.closedLoopGroups});
      }
      setPageState(PAGE_STATE.STATIC_READY, "静态闭环已读取，进入扫描状态");
    }
    function renderClosedLoopGroups(payload) {
      const groups = Array.isArray(payload?.groups) ? payload.groups : [];
      if (!groups.length) {
        $("groupList").innerHTML = "";
        return;
      }
      const groupHtml = groups.slice().sort((a, b) => {
        const symbolA = String(a.flashLoanToken?.symbol || a.startToken?.symbol || "").toUpperCase();
        const symbolB = String(b.flashLoanToken?.symbol || b.startToken?.symbol || "").toUpperCase();
        const borrowDiff = (b.borrowable ? 0 : 1) - (a.borrowable ? 0 : 1);
        return borrowDiff || symbolA.localeCompare(symbolB);
      }).map(group => {
        const token = group.flashLoanToken || group.startToken || {};
        const address = token.address ? `<small>${esc(token.address)}</small>` : "";
        const borrowable = group.borrowable ? "" : " <small>(not borrowable)</small>";
        return `<div class="group-card"><h3>${esc(token.symbol || "-")}${borrowable}</h3>${address}<strong>${esc(group.routeCount || 0)} routes / ${esc(group.poolDependencyCount || 0)} pools</strong></div>`;
      }).join("");
      $("groupList").innerHTML = groupHtml;
    }
    async function refreshClosedLoopGroups() {
      try {
        const payload = await fetchJsonWithTimeout("/api/dex/closed-loop-groups", 20000);
        renderClosedLoopGroups(payload);
      } catch (error) {
        if (!document.querySelector("#groupList .group-card")) {
          $("groupList").innerHTML = `<div class="group-card"><h3>闭环分组暂不可用</h3><small>${esc(uiError(error.message))}</small></div>`;
        }
      }
    }
    async function refreshSnapshot() {
      if (!canRefreshSnapshot()) return; snapshotRefreshing = true; refreshInFlight = true;
      const generation = selectionGeneration; const signature = activeSelectionSignature;
      let snapshotHasRoutes = false;
      try {
        refreshClosedLoopGroups();
        loadLatestSubmissions().catch(() => {});
        const staticPromise = (!staticRoutesLoaded || latestRoutes.length === 0)
          ? fetchJsonWithTimeout("/api/dex/quote-routes/static", 25000)
              .then(staticPayload => {
                if (generation !== selectionGeneration || signature !== activeSelectionSignature) return;
                if (!staticPayload?.routes?.length) return;
                staticRoutesLoaded = true;
                if (!snapshotHasRoutes) renderStaticRoutes(staticPayload);
              })
              .catch(() => ({}))
          : Promise.resolve({});
        const snapshotPromise = staticPromise
          .then(() => {
            if (generation === selectionGeneration && signature === activeSelectionSignature) setPageState(PAGE_STATE.SCAN, "静态闭环已读取，开始扫描实时快照");
            return fetchJsonWithTimeout("/api/dex/opportunity-scanner/snapshot", 45000);
          })
          .then(snapshotPayload => {
            if (generation !== selectionGeneration || signature !== activeSelectionSignature) return;
            snapshotHasRoutes = Array.isArray(snapshotPayload?.snapshot?.payload?.routes) && snapshotPayload.snapshot.payload.routes.length > 0;
            renderScannerResponse(snapshotPayload);
            (async () => {
              let quoteRoutesPayload = null;
              try { quoteRoutesPayload = await fetchJsonWithTimeout("/api/dex/quote-routes"); } catch (_) {}
              if (generation === selectionGeneration && signature === activeSelectionSignature && snapshotPayload && quoteRoutesPayload) renderScannerResponse(snapshotPayload, quoteRoutesPayload);
            })();
          })
          .catch(snapshotError => {
            if (generation === selectionGeneration && signature === activeSelectionSignature) {
              $("message").textContent = `实时扫描快照暂不可用（${uiError(snapshotError.message)}）；静态闭环已展示，稍后自动重试`;
              if (latestRoutes.length) scheduleReviewActiveRoute();
            }
          });
        await snapshotPromise.catch(() => {});
      }
      catch (error) { if (generation === selectionGeneration && signature === activeSelectionSignature) $("message").textContent = uiError(error.message); }
      finally { snapshotRefreshing = false; refreshInFlight = false; }
    }
    function scheduleSnapshotRefreshBackoff() {
      const delays = [1200, 4000, 10000, 25000];
      let index = 0;
      const scheduleNext = () => {
        if (index >= delays.length) return;
        const delay = delays[index++];
        const timer = setTimeout(async () => {
          snapshotRefreshTimers = snapshotRefreshTimers.filter(item => item !== timer);
          try { await refreshSnapshot(); } catch (_) {}
          scheduleNext();
        }, delay);
        snapshotRefreshTimers.push(timer);
      };
      scheduleNext();
    }
    async function startScanner() {
      const generation = selectionGeneration; const signature = activeSelectionSignature;
      try {
        await fetchJsonWithTimeout("/api/dex/opportunity-scanner/start", 20000);
        const response = await fetchJsonWithTimeout("/api/dex/opportunity-scanner/snapshot", 45000);
        if (generation === selectionGeneration && signature === activeSelectionSignature) renderScannerResponse(response);
        if (!scanNowInFlight) {
          scanNowInFlight = true;
          (async () => {
            const rendered = snapshotAgeSeconds(response) <= 600
              ? response
              : await fetchJsonWithTimeout("/api/dex/opportunity-scanner/scan-now?wait=1", 240000);
            let quoteRoutesPayload = null;
            try { quoteRoutesPayload = await fetchJsonWithTimeout("/api/dex/quote-routes"); } catch (_) {}
            if (generation === selectionGeneration && signature === activeSelectionSignature) renderScannerResponse(rendered, quoteRoutesPayload);
          })()
            .catch(() => {})
            .finally(() => { scanNowInFlight = false; });
        }
      }
      catch (error) { if (generation === selectionGeneration && signature === activeSelectionSignature) $("message").textContent = uiError(error.message); }
    }
    async function refresh(forceScan = false) {
      const generation = selectionGeneration; const signature = activeSelectionSignature; setPageState(PAGE_STATE.INIT, "正在加载静态闭环...");
      let snapshot = null;
      let snapshotHasRoutes = false;
      let quoteRoutesPayloadPromise = null;
      let reviewScheduled = false;
      try {
        refreshClosedLoopGroups();
        loadLatestSubmissions().catch(() => {});
        const staticPromise = (!staticRoutesLoaded || latestRoutes.length === 0)
          ? fetchJsonWithTimeout("/api/dex/quote-routes/static", 25000)
              .then(staticPayload => {
                if (generation !== selectionGeneration || signature !== activeSelectionSignature) return;
                if (!staticPayload?.routes?.length) return;
                staticRoutesLoaded = true;
                if (!snapshotHasRoutes) renderStaticRoutes(staticPayload);
              })
              .catch(() => ({}))
          : Promise.resolve({});
        const snapshotPromise = staticPromise
          .then(() => {
            if (generation === selectionGeneration && signature === activeSelectionSignature) setPageState(PAGE_STATE.SCAN, "静态闭环已读取，开始扫描实时快照");
            return fetchJsonWithTimeout("/api/dex/opportunity-scanner/snapshot", 45000);
          })
          .then(snapshotPayload => {
            if (generation !== selectionGeneration || signature !== activeSelectionSignature) return;
            snapshot = snapshotPayload;
            snapshotHasRoutes = Array.isArray(snapshotPayload?.snapshot?.payload?.routes) && snapshotPayload.snapshot.payload.routes.length > 0;
            renderScannerResponse(snapshotPayload);
            reviewScheduled = true;
            quoteRoutesPayloadPromise = fetchJsonWithTimeout("/api/dex/quote-routes").catch(() => null);
            quoteRoutesPayloadPromise.then(quoteRoutesPayload => {
              if (generation === selectionGeneration && signature === activeSelectionSignature && snapshotPayload && quoteRoutesPayload) renderScannerResponse(snapshotPayload, quoteRoutesPayload);
            });
          })
          .catch(snapshotError => {
            if (generation === selectionGeneration && signature === activeSelectionSignature) {
              $("message").textContent = `实时扫描快照暂不可用（${uiError(snapshotError.message)}）；静态闭环已展示，稍后自动重试`;
              if (latestRoutes.length) scheduleReviewActiveRoute();
              reviewScheduled = true;
            }
          });
        const staticSettled = staticPromise.then(() => {}, () => {});
        const snapshotSettled = snapshotPromise.then(() => {}, () => {});
        await staticSettled;
        snapshotSettled.finally(() => {
          if (generation !== selectionGeneration || signature !== activeSelectionSignature) return;
          if (latestRoutes.length && !reviewScheduled) {
            scheduleReviewActiveRoute();
            reviewScheduled = true;
          }
          if (scanNowInFlight) return;
          if (!forceScan && snapshot && snapshotAgeSeconds(snapshot) <= 600) {
            $("message").textContent = "当前扫描快照未过期，直接展示实时复核结果";
            return;
          }
          if (!snapshot) return;
          scanNowInFlight = true;
          (async () => {
            const rendered = await fetchJsonWithTimeout("/api/dex/opportunity-scanner/scan-now?wait=1", 240000);
            if (generation === selectionGeneration && signature === activeSelectionSignature) {
              renderScannerResponse(rendered);
              if (latestRoutes.length) scheduleReviewActiveRoute();
            }
            let refreshedQuoteRoutesPayload = null;
            if (quoteRoutesPayloadPromise) {
              try { refreshedQuoteRoutesPayload = await quoteRoutesPayloadPromise; } catch (_) {}
            }
            if (!refreshedQuoteRoutesPayload) {
              try { refreshedQuoteRoutesPayload = await fetchJsonWithTimeout("/api/dex/quote-routes"); } catch (_) {}
            }
            if (generation === selectionGeneration && signature === activeSelectionSignature && refreshedQuoteRoutesPayload) renderScannerResponse(rendered, refreshedQuoteRoutesPayload);
          })()
            .catch(error => {
              if (generation === selectionGeneration && signature === activeSelectionSignature) $("message").textContent = uiError(error.message);
            })
            .finally(() => { scanNowInFlight = false; scheduleSnapshotRefreshBackoff(); });
        });
      }
      catch (error) { if (generation === selectionGeneration && signature === activeSelectionSignature) $("message").textContent = uiError(error.message); }
    }
    async function loadLatestSubmissions() {
      latestSubmissionRows = [];
      try {
        const data = await fetchJsonWithTimeout("/api/dex/submissions?limit=120", 6000);
        latestSubmissionRows = Array.isArray(data?.attempts) ? data.attempts : [];
      } catch (_) {
        latestSubmissionRows = [];
      }
    }
    async function copyRoute(rawRoute, button) {
      const route = normalizeRouteForRender(rawRoute);
      const activeExecutionRoute = Boolean(activeExecution?.routeKey && routeExecutionKey(route) === activeExecution.routeKey);
      const latestExecution = activeExecutionRoute
        ? {
            text: activeExecution?.broadcastDisabled || route?.__broadcastDisabled
              ? "链上执行未提交：自动扫描复核并链上广播未开启，本次未发起链上交易。"
              : activeExecution?.state === ROUTE_UI_STATE.EXECUTION_PENDING
              ? "链上执行提交中，请稍候查看本次链上结果。"
              : activeExecution?.state === ROUTE_UI_STATE.EXECUTION_FAILED
                ? (route?.__broadcastError ? `链上执行失败/未提交：${route.__broadcastError}` : "链上执行失败/未提交；请检查网络、RPC 或开关后重试。")
                : "链上执行已提交；请到提交记录或区块浏览器确认交易哈希与链上验证结果。",
            txHash: activeExecution?.txHash || "",
            txHashes: activeExecution?.txHash ? [activeExecution.txHash] : [],
          }
        : latestBroadcastAdvice(route);
      const reviewStage = normalizedReviewStage(route, latestExecution, activeExecution);
      const execPending = reviewStage === "execution_pending";
      const execFailed = reviewStage === "execution_failed";
      const notSubmitted = activeExecution?.broadcastDisabled || route?.__broadcastDisabled;
      const executionSubmission = latestExecution?.txHash ? latestBroadcastSubmissionForRoute(route) : null;
      const executionQuote = (executionSubmission && executionSubmission.quote) || {};
      const executionAnalysis = executionQuote.quote_analysis || executionQuote.analysis || {};
      const executionSteps = Array.isArray(executionQuote.hops) ? executionQuote.hops.filter(Boolean) : Array.isArray(executionQuote.steps) ? executionQuote.steps.filter(Boolean) : Array.isArray(executionAnalysis.steps) ? executionAnalysis.steps.filter(Boolean) : [];
      const evidence = routeEvidence(route); const analysis = routeAnalysis(route);
      const sourceAnalysis = analysis;
      const size = route?.sizeAnalysis || {};
      const costs = evidence.costModel || {};
      const validation = evidence.validation || {};
      const pendingCopy = uiStateOf(route) === ROUTE_UI_STATE.OFFCHAIN_PENDING && !routeHasCompletedOffchainReview(route);
      const display = value => value === null || value === undefined || value === "" ? "-" : String(value);
      const evidenceFirst = (...values) => {
        for (const value of values) {
          if (value !== null && value !== undefined && value !== "" && String(value).trim() !== "") return value;
        }
        return null;
      };
      const copySteps = executionSteps.length
        ? executionSteps
        : (Array.isArray(sourceAnalysis.steps) ? sourceAnalysis.steps.filter(Boolean) : []);
      const firstCopyStep = copySteps[0] || null;
      const lastCopyStep = copySteps[copySteps.length - 1] || null;
      const copyDirection = pathFromSteps(copySteps, (route.routeSymbols || []).join(" -> "));
      const firstHopInputNumber = firstCopyStep ? Number(firstCopyStep.inputAmount ?? firstCopyStep.amountIn) : NaN;
      const lastHopOutputNumber = lastCopyStep ? Number(lastCopyStep.outputAmount ?? lastCopyStep.referenceOutputAmount ?? lastCopyStep.amountOut) : NaN;
      const derivedNetProfitAmount = Number.isFinite(firstHopInputNumber) && Number.isFinite(lastHopOutputNumber)
        ? lastHopOutputNumber - firstHopInputNumber
        : null;
      const referenceInputAmount = pendingCopy ? null : evidenceFirst(
        executionSubmission?.best_amount,
        executionQuote.best_amount,
        executionAnalysis.bestAmount,
        executionAnalysis.best_amount,
        executionQuote.input_amount,
        validation.borrowInputAmount,
        costs.selectedBorrowInputAmount,
        costs.borrowInputAmount,
        costs.requestedBorrowInputAmount,
        sourceAnalysis.referenceInputAmount,
        Number.isFinite(firstHopInputNumber) ? firstHopInputNumber : null
      );
      const rawBestAmountCopy = pendingCopy ? null : evidenceFirst(
        executionSubmission?.best_amount,
        executionQuote.best_amount,
        executionAnalysis.bestAmount,
        executionAnalysis.best_amount,
        validation.borrowInputAmount,
        costs.selectedBorrowInputAmount,
        costs.borrowInputAmount,
        sourceAnalysis.bestAmount,
        sourceAnalysis.best_amount,
        size.recommendedBorrowInputAmount,
        sourceAnalysis.bestAmountCalculation?.bestAmount,
        sourceAnalysis.bestAmountCalculation?.recommendedBorrowInputAmount,
        route.bestAmountCalculation?.bestAmount,
        route.bestAmountCalculation?.recommendedBorrowInputAmount
      );
      const confirmedCopyBestAmount = evidenceFirst(
        executionSubmission?.best_amount,
        executionQuote.best_amount,
        executionAnalysis.bestAmount,
        executionAnalysis.best_amount,
        validation.borrowInputAmount,
        costs.selectedBorrowInputAmount,
        costs.borrowInputAmount
      );
      const finalRawBestAmountCopy = pendingCopy
        ? null
        : (costs.bestAmountMissing === true || sourceAnalysis.bestAmountMissing === true || route.bestAmountMissing === true
            ? null
            : rawBestAmountCopy);
      const bestAmountCopy = pendingCopy ? null : (reviewStage === "estimate"
        ? (offchainProbeAmount(route, evidence) ?? sourceAnalysis.requestedAmount ?? sourceAnalysis.initialProbeAmount ?? finalRawBestAmountCopy ?? 1)
        : finalRawBestAmountCopy);
      const evidenceStatusLower = String(evidence.status || "").toLowerCase();
      const evidenceTrustLower = String(evidence.executionTrust || "").toLowerCase();
      const staticReview = routeNeedsLiveReview(route) || evidenceStatusLower === "static_reference_pending_live_review" || evidenceTrustLower === "static_reference_pending_live_review";
      const estimateFailed = terminalEvidenceStatus(evidence.status);
      const liveTrust = ["live_v4_adapter", "live_quoter_verified_readonly", "live_verified"].includes(evidenceTrustLower);
      const liveStatus = ["live_verified", "route_unprofitable", "slippage_limit_exceeded", "pool_insufficient_for_profitable_execution"].includes(evidenceStatusLower);
      const hardEvidenceFailure = [
        "rpc_verification_failed",
        "rpc_verification_timeout",
        "adapter_required",
        "missing_trigger_block",
        "live_quote_failed",
        "quote_failed",
        "pool_not_initialized",
        "not_enough_liquidity",
        "missing_effective_pool",
      ].includes(evidenceStatusLower);
      const live = Boolean(!hardEvidenceFailure && (analysis.livePriceVerified || analysis.priceEvidenceMode === "live_verified" || liveStatus || liveTrust));
      const gateBlocked = Object.keys(evidence).length > 0
        && live
        && ["route_unprofitable", "pool_insufficient_for_profitable_execution"].includes(String(evidence.status || "").toLowerCase());
      const hasLiveEvidence = Object.keys(evidence).length > 0 && !["live_evidence_deferred", "not_reviewed_this_scan"].includes(String(evidence.status || "").toLowerCase());
      const staleWarningCodes = [
        "hop_1_reference_quote_requires_live_quoter",
        "hop_2_reference_quote_requires_live_quoter",
        "hop_3_reference_quote_requires_live_quoter",
        "hop_4_reference_quote_requires_live_quoter",
        "hop_1_requires_live_quoter",
        "hop_2_requires_live_quoter",
        "hop_3_requires_live_quoter",
        "hop_4_requires_live_quoter",
        "reference_price_step_requires_live_quoter",
        "reference_usd_price_missing_or_untrusted",
        "static_depth_evidence_unavailable",
        "robinhood_live_price_only_enforced",
        "v4_token_decimals_missing_or_assumed",
      ];
      const copiedWarnings = (sourceAnalysis.warnings || []).filter(warning => !hasLiveEvidence || !staleWarningCodes.some(code => String(warning).startsWith(code))).map(warningText);
      const failedHops = (Array.isArray(evidence.hops) ? evidence.hops : analysis.steps || []).filter(hop => hop?.liveQuoteError || ["live_quote_failed", "quote_failed", "pool_not_initialized", "not_enough_liquidity"].includes(String(hop?.status || "").toLowerCase()));
      const failedHop = failedHops[0] || null;
      const failedStep = failedHop ? (analysis.steps || []).find(step => Number(step?.hop) === Number(failedHop.hop)) : null;
      const focusOnError = Boolean(failedHop);
      const failedIsLiquidity = focusOnError && String(failedHop.status || "").toLowerCase() === "not_enough_liquidity";
      const failedIsDeferred = focusOnError && ["live_evidence_deferred", "not_reviewed_this_scan"].includes(String(failedHop.status || "").toLowerCase());
      const stackTop = failedHop?.errorStack?.[0] || route.errorStack?.[0] || evidence.errorStack?.[0] || null;
      const focusErrorText = failedHop?.liveQuoteError ? blockerText(failedHop.liveQuoteError) : (stackTop?.message ? blockerText(stackTop.message) : "链上单跳报价失败");
      if (focusOnError) {
        const probe = evidence.executionSizeProbe || null;
        const probeDecimals = Number(probe?.decimals ?? evidence.costModel?.borrowInputDecimals ?? 18);
        const probeAmount = raw => {
          if (raw === null || raw === undefined || raw === "") return null;
          const parsed = Number(raw);
          return Number.isFinite(parsed) ? parsed / (10 ** probeDecimals) : null;
        };
        const probeLines = Array.isArray(probe?.attempts) ? probe.attempts.filter(Boolean).map(row => {
          const amountText = numeric(probeAmount(row.amountRaw), 8);
          const reasonText = row.failureReason ? ` (${row.failureReason})` : "";
          return `探测 ${amountText} ${display(evidence.costModel?.borrowInputSymbol || "-")} -> ${display(row.status || "-")}${reasonText}`;
        }) : [];
        const probeHeader = probe ? [`执行规模探测=${display(probe.searchStatus || probe.status || "-")}${probe.selectedProfitableAmountRaw != null ? `，可用 ${numeric(probeAmount(probe.selectedProfitableAmountRaw), 8)} ${display(evidence.costModel?.borrowInputSymbol || "")}` : ""}`] : [];
        const text = [
          `路线=${display(route.symbolPair || (route.routeSymbols || []).join(" -> "))}`,
          `路径=${display((route.routeSymbols || []).join(" -> "))}`,
          `报价状态=${display(label(route.quoteStatus))}`,
          `状态=${display(evidenceStatusEx(evidence.status))}`,
          `区块=${display(evidence.blockNumber || evidence.triggerBlockNumber)}`,
          `依据=${display(evidenceTrustText(evidence, sourceAnalysis))}`,
          `失败输入=${failedIsLiquidity ? `${display(failedHop.amountIn ?? failedHop.inputAmount ?? failedStep?.inputAmount)} ${display(failedHop.fromSymbol)}` : "-"}`,
          `错误定位=第 ${display(failedHop.hop)} 步 ${display(failedHop.fromSymbol)} -> ${display(failedHop.toSymbol)}`,
          `链上错误=${display(focusErrorText)}`,
          `错误分层=\n${display(errorChainText(failedHop.liveQuoteError || focusErrorText))}`,
          `建议=${failedIsLiquidity ? "流动性不足；降低借贷金额后重试，或标记当前金额不可执行" : failedIsDeferred ? "链上复核延后；后续自动复核，不判定为路线失败" : "链上报价失败，阻止链上执行"}`,
          ...probeHeader,
          ...probeLines,
        ].filter(Boolean).join("\n");
        try { await navigator.clipboard.writeText(text); const old = button.textContent; button.textContent = "已复制"; setTimeout(() => button.textContent = old, 1200); } catch (error) { $("message").textContent = uiError(error.message); }
        return;
      }
      const referenceInputSymbol = evidenceFirst(
        executionQuote.input_symbol,
        executionAnalysis.referenceInputSymbol,
        validation.borrowInputSymbol,
        costs.borrowInputSymbol,
        sourceAnalysis.referenceInputSymbol
      );
      const firstCopyStepInput = firstCopyStep ? Number(firstCopyStep.inputAmount ?? firstCopyStep.amountIn ?? firstCopyStep.referenceInputAmount) : NaN;
      const copyAmountMismatch = Number.isFinite(Number(bestAmountCopy))
        && Number.isFinite(firstCopyStepInput)
        && firstCopyStepInput > 0
        && Math.abs(Number(bestAmountCopy) - firstCopyStepInput) > Math.max(Math.abs(Number(bestAmountCopy)), Math.abs(firstCopyStepInput), 1) * 1e-9;
      const copyAmountMismatchLine = copyAmountMismatch
        ? `步骤金额提示=复制步骤首跳金额 ${display(firstCopyStepInput)} ${display(referenceInputSymbol)} 与当前最佳量 ${display(bestAmountCopy)} ${display(referenceInputSymbol)} 不一致；请以链上复核确认金额为准。`
        : "";
      const referenceOutputAmount = evidenceFirst(
        executionSubmission?.final_amount,
        executionQuote.final_amount,
        executionAnalysis.referenceFinalOutputAmount,
        validation.finalOutputAmount,
        costs.finalOutputAmount,
        sourceAnalysis.referenceFinalOutputAmount,
        Number.isFinite(lastHopOutputNumber) ? lastHopOutputNumber : null
      );
      const referenceOutputSymbol = evidenceFirst(
        executionQuote.final_symbol,
        executionAnalysis.referenceFinalOutputSymbol,
        validation.finalOutputSymbol,
        costs.finalOutputSymbol,
        sourceAnalysis.referenceFinalOutputSymbol,
        lastCopyStep?.toSymbol || null
      );
      const referenceNetProfitAmount = humanTokenValue(evidenceFirst(
        executionSubmission?.profit_amount,
        executionQuote.profit_amount,
        executionAnalysis.referenceNetProfitAmount,
        validation.netProfitAmount,
        validation.submissionNetProfitAmount,
        costs.netProfitAmount,
        costs.submissionNetProfitAmount,
        sourceAnalysis.referenceNetProfitAmount,
        derivedNetProfitAmount
      ));
      const referenceNetReturnBps = evidenceFirst(
        executionAnalysis.referenceNetReturnBps,
        validation.netProfitAmountBps,
        costs.netProfitAmountBps,
        sourceAnalysis.referenceNetReturnBps
      );
      const netProfit = hardEvidenceFailure ? null : ((staticReview && !live && !latestExecution?.txHash) ? null : humanTokenValue(evidenceFirst(
        executionSubmission?.profit_amount,
        executionQuote.profit_amount,
        analysis.netProfitUsd,
        sourceAnalysis.netProfitUsd,
        costs.netProfitUsd,
        validation.netProfitAmount,
        validation.submissionNetProfitAmount,
        costs.netProfitAmount,
        costs.submissionNetProfitAmount,
        sourceAnalysis.referenceNetProfitAmount,
        derivedNetProfitAmount
      )));
      const netProfitSymbol = analysis.netProfitUsd != null || sourceAnalysis.netProfitUsd != null || costs.netProfitUsd != null ? "USD" : (executionAnalysis.referenceInputSymbol || validation.netProfitAmountSymbol || costs.netProfitAmountSymbol || sourceAnalysis.referenceInputSymbol || "token");
      const staticReferenceNotice = staticReview && reviewStage === "estimate"
        ? "静态参考提示=当前净利为静态参考估算，尚未经链上逐 hop 复核；实际产出可能因流动性和滑点显著低于参考值"
        : "";
      const offchainProbe = offchainProbeAmount(route, evidence);
      const requestedAmountValue = pendingCopy ? autoRouteAmount(route) : (reviewStage === "execution"
        ? evidenceFirst(
            executionSubmission?.initial_probe_amount,
            executionQuote.initial_probe_amount,
            executionAnalysis.requestedAmount,
            executionAnalysis.initialProbeAmount,
            executionAnalysis.amountOptimizedFrom,
            executionQuote.input_amount,
            costs.requestedBorrowInputAmount,
            sourceAnalysis.requestedAmount,
            sourceAnalysis.amountOptimizedFrom,
            referenceInputAmount,
            offchainProbe,
            referenceInputAmount
          )
        : (costs.requestedBorrowInputAmount ?? sourceAnalysis.requestedAmount ?? sourceAnalysis.amountOptimizedFrom ?? referenceInputAmount ?? offchainProbe ?? referenceInputAmount));
      const initialProbeValue = pendingCopy ? null : (reviewStage === "execution"
        ? evidenceFirst(
            executionSubmission?.initial_probe_amount,
            executionQuote.initial_probe_amount,
            executionAnalysis.initialProbeAmount,
            executionAnalysis.requestedAmount,
            executionAnalysis.amountOptimizedFrom,
            costs.requestedBorrowInputAmount,
            costs.selectedBorrowInputAmount,
            sourceAnalysis.requestedAmount,
            sourceAnalysis.amountOptimizedFrom,
            offchainProbe,
            referenceInputAmount
          )
        : (costs.requestedBorrowInputAmount ?? offchainProbe ?? costs.selectedBorrowInputAmount ?? sourceAnalysis.requestedAmount ?? sourceAnalysis.amountOptimizedFrom ?? referenceInputAmount ?? size.recommendedBorrowInputAmount ?? size.tokenAmountSteps?.[0]));
      const initialProbeSymbol = pendingCopy ? autoRouteAmountSymbol(route) : (executionQuote.initial_probe_symbol || executionAnalysis.referenceInputSymbol || costs.borrowInputSymbol || sourceAnalysis.referenceInputSymbol || size.recommendedBorrowInputAmountBasis || "token");
      const amountScaleHint = Number.isFinite(Number(bestAmountCopy))
        && Number.isFinite(Number(initialProbeValue))
        && Number(initialProbeValue) > 0
        ? (Math.abs(Number(bestAmountCopy) - Number(initialProbeValue)) / Number(initialProbeValue) > 0.01
            ? bestAmountHint({
                evidence,
                analysis: sourceAnalysis,
                amount: display(bestAmountCopy),
                symbol: display(referenceInputSymbol),
                initialAmount: display(initialProbeValue),
                initialSymbol: display(initialProbeSymbol),
                executionCount: latestExecution?.txHashes?.length || 0,
                stage: reviewStage,
              })
            : "")
        : "";
      const backendAmountHint = evidenceFirst(
        analysis.amountOptimizationHint,
        sourceAnalysis.amountOptimizationHint
      );
      const rows = (Array.isArray(executionSubmission?.probe_records) && executionSubmission.probe_records.length) ? executionSubmission.probe_records : (Array.isArray(executionSubmission?.probeRecords) && executionSubmission.probeRecords.length) ? executionSubmission.probeRecords : (Array.isArray(executionAnalysis.probe_records) && executionAnalysis.probe_records.length) ? executionAnalysis.probe_records : (Array.isArray(executionAnalysis.probeRecords) && executionAnalysis.probeRecords.length) ? executionAnalysis.probeRecords : (Array.isArray(size.rows) && size.rows.length) ? size.rows : Array.isArray(sourceAnalysis.probeRecords) ? sourceAnalysis.probeRecords : Array.isArray(sourceAnalysis.probe_records) ? sourceAnalysis.probe_records : [];
      const liveDepthImpacts = (copySteps.length ? copySteps : sourceAnalysis.steps || [])
        .map(step => Number(step.priceImpactBps))
        .filter(value => Number.isFinite(value));
      const liveMaxDepthImpactBps = liveDepthImpacts.length ? Math.max(...liveDepthImpacts) : null;
      const hopLines = copySteps.map((step, index) => {
        const output = step.outputAmount ?? step.referenceOutputAmount;
        const rate = step.effectiveRate ?? step.referenceEffectiveRate;
        let continuity = "";
        if (index > 0) {
          const previous = copySteps[index - 1];
          const previousOutput = Number(previous.outputAmount ?? previous.referenceOutputAmount);
          const currentInput = Number(step.inputAmount ?? step.amountIn);
          if (Number.isFinite(previousOutput) && Number.isFinite(currentInput) && Math.abs(previousOutput - currentInput) > Math.max(Math.abs(previousOutput), Math.abs(currentInput), 1) * 1e-9) {
            continuity = " [continuity mismatch]";
          } else if (String(previous?.toSymbol || "").toUpperCase() !== String(step.fromSymbol || "").toUpperCase()) {
            continuity = " [continuity mismatch]";
          }
        }
        return `第 ${step.hop ?? index + 1} 步: ${display(step.fromSymbol)} -> ${display(step.toSymbol)} | 输入=${display(step.inputAmount)} ${display(step.fromSymbol)} | 输出=${display(output)} ${display(step.toSymbol)} | 价格=${display(rate)} | 费用 BPS=${display(step.feeBps)} | 滑点 BPS=${display(step.priceImpactBps)}${continuity}`;
      });
      const ladderLines = rows.map(row => `探测 ${display(row.borrowAmount)} ${display(row.borrowSymbol || row.borrowBasis)} | 输出=${display(row.referenceFinalOutputAmount)} ${display(row.referenceFinalOutputSymbol)} | 净利=${display(row.referenceNetProfitAmount)} | 净利 BPS=${display(row.referenceNetReturnBps)} | 深度=${display(row.staticDepthStatus)} | 可执行=${display(row.executableCandidate)}`);
      const recommendationText = (() => {
        if (pendingCopy) return "链下估算完成：默认初始量已生成；链上复核可继续确认当前最佳量";
        if (!Object.keys(evidence).length) return "链上报价未完成";
        if (focusOnError) return "链上复核失败/流动性不足；阻止链上执行";
        if (execFailed) return route.__broadcastError ? `链上执行失败/未提交：${route.__broadcastError}` : "链上执行失败/未提交；请检查网络、RPC 或开关后重试。";
        if (execPending) return "链上执行正在提交；请稍候查看本次链上结果。";
        if (reviewStage === "execution") return "链上执行已提交；请到提交记录或区块浏览器确认交易哈希与链上验证结果";
        if (reviewStage === "preflight_failed") return "链上复核失败：RPC 或链上报价未返回有效结果，请稍后重试或检查 RPC 配置。";
        if (staticReview && reviewStage === "estimate") return "当前为静态参考，尚未通过链上逐 hop 复核；不能作为已确认最佳量，不建议直接链上执行。";
        const status = String(evidence.status || "").toLowerCase();
        if (status === "route_unprofitable") return "链上报价已验证，但净收益未转正；禁止链上执行";
        if (status === "slippage_limit_exceeded") return "链上已验证，但至少一个单跳价格影响超过 10 bps 或无法确认；禁止链上执行";
        if (estimateFailed) return terminalFailureAdvice(evidence.status);
        if (reviewStage === "preflight" && bestAmountMissingOf(route, evidence, sourceAnalysis)) return "链上复核未通过：最佳量未计算成功，不能作为已确认最佳量；请重新链上复核并完成最佳量计算后再执行。";
        if (reviewStage === "preflight") return "链上复核完成；尚未执行链上交易。当前最佳交易量已通过，若要执行请点击链上执行。";
        if (live && (evidence.readyForTransactionPreflight || analysis.readyForTransactionPreflight || (sourceAnalysis.recommendation || {}).readyForTransactionPreflight)) return "链下估算完成：当前初始量已生成；最佳量与可执行性需点击链上复核确认。";
        if (live) return "链上复核已验证，但当前状态不满足交易提交条件；禁止链上执行";
        return "链上报价未完成";
      })();
      const domTrustedForCopy = !estimateFailed && !focusOnError && !execFailed && !execPending && reviewStage !== "preflight_failed" && !pendingCopy;
      const routeTestResultElement = button ? document.querySelector(`[data-route-test-result="${button.dataset.copyRoute}"]`) : null;
      const domRouteTestResultLine = routeTestResultElement && routeTestResultElement.textContent
        && !["", "处理中..."].includes(String(routeTestResultElement.textContent).trim())
        ? `本次结果=${String(routeTestResultElement.textContent).trim()}`
        : null;
      const latestExecutionResultLine = latestExecution?.txHash
        ? `本次结果=链上执行已提交：${latestExecution.txHash}`
        : null;
      const routeTestResultLine = latestExecutionResultLine || (domTrustedForCopy ? domRouteTestResultLine : null);
      const routeRecommendationElement = button ? document.querySelector(`[data-route-recommendation="${button.dataset.copyRoute}"]`) : null;
      const domRouteRecommendationText = routeRecommendationElement && routeRecommendationElement.textContent
        ? String(routeRecommendationElement.textContent).trim().replace(/^建议：/, "")
        : "";
      const routeRecommendationText = latestExecution?.text || (domTrustedForCopy ? domRouteRecommendationText : recommendationText);
      const bestAmountLabel = bestAmountDisplayLabel({evidence, analysis: sourceAnalysis, stage: reviewStage});
      const bestAmountMissingCopy = pendingCopy
        || estimateFailed
        || (reviewStage !== "estimate" && (costs.bestAmountMissing === true || sourceAnalysis.bestAmountMissing === true || route.bestAmountMissing === true))
        || (bestAmountCopy === null || bestAmountCopy === undefined || bestAmountCopy === "");
      const bestAmountCopyText = bestAmountMissingCopy
        ? "未计算"
        : `${display(bestAmountCopy)} ${display(referenceInputSymbol)}`;
      const actionRequested = Number(route.__requestedAmount);
      const hasActionRequest = Number.isFinite(actionRequested) && actionRequested > 0;
      const requestedNumber = Number(requestedAmountValue);
      const probeNumber = Number(initialProbeValue);
      const initialAmountsSame = Number.isFinite(requestedNumber)
        && Number.isFinite(probeNumber)
        && probeNumber > 0
        && Math.abs(requestedNumber - probeNumber) <= Math.max(Math.abs(requestedNumber), Math.abs(probeNumber)) * 1e-9;
      const initialProbeDisplay = display(initialProbeValue);
      const referenceOutputAmountDisplay = display(referenceOutputAmount);
      const referenceNetProfitAmountDisplay = display(referenceNetProfitAmount);
      const text = [
        `路线=${display(copyDirection || route.symbolPair || (route.routeSymbols || []).join(" -> "))}`,
        `路径=${display(copyDirection || (route.routeSymbols || []).join(" -> "))}`,
        `方向=${display(copyDirection)}`,
        `${bestAmountLabel}=${bestAmountCopyText}`,
        `报价状态=${pendingCopy ? "链下估算完成" : notSubmitted ? "链上执行未提交" : execFailed ? "链上执行失败" : execPending ? "链上执行提交中" : reviewStage === "execution" ? "链上执行" : reviewStage === "preflight_failed" ? "链上复核失败" : estimateFailed || focusOnError ? display(evidenceStatusEx(evidence.status)) : staticReview ? display(evidenceStatusEx(evidence.status)) : reviewStage === "preflight" ? "链上复核" : "链下估算完成"}`,
        `状态=${pendingCopy ? "链下估算完成" : notSubmitted ? "链上执行未提交（自动扫描复核并链上广播未开启）" : execFailed ? "链上执行失败/未提交" : execPending ? "链上执行提交中" : reviewStage === "execution" ? "链上执行已提交，等待链上确认" : reviewStage === "preflight_failed" ? "链上复核失败" : estimateFailed || focusOnError ? display(evidenceStatusEx(evidence.status)) : staticReview ? display(evidenceStatusEx(evidence.status)) : reviewStage === "preflight" ? "链上复核完成" : "链下估算完成"}`,
        `区块=${display(evidence.blockNumber || evidence.triggerBlockNumber)}`,
        `依据=${display(evidenceTrustText(evidence, sourceAnalysis))}`,
        [`${reviewStage === "preflight" ? "初始测试量" : "初始探测"}=${initialProbeDisplay}${initialProbeDisplay !== "-" ? ` ${display(initialProbeSymbol)}` : ""}`],
        `初始探测依据=${display(evidenceProbeReasonText(evidence.offchainInitialProbeReason, evidence))}`,
        ...(amountScaleHint ? [amountScaleHint] : []),
        ...(backendAmountHint ? [`优化提示=${display(backendAmountHint)}`] : []),
        ...(copyAmountMismatchLine ? [copyAmountMismatchLine] : []),
        `盈亏=${netProfit !== null && netProfit !== undefined && netProfit !== "" ? `${display(netProfit)} ${netProfitSymbol}` : (referenceNetProfitAmount != null ? `${display(referenceNetProfitAmount)} ${display(referenceInputSymbol)}` : "-")}`,
        `参考输出=${referenceOutputAmountDisplay}${referenceOutputAmountDisplay !== "-" ? ` ${display(referenceOutputSymbol)}` : ""}`,
        `参考净利=${referenceNetProfitAmountDisplay}${referenceNetProfitAmountDisplay !== "-" ? ` ${display(referenceInputSymbol)}` : ""}`,
        `参考净利 BPS=${display(referenceNetReturnBps)}`,
        ...(staticReferenceNotice ? [staticReferenceNotice] : []),
        `净利=${netProfit !== null && netProfit !== undefined && netProfit !== "" ? `${display(netProfit)} ${netProfitSymbol}` : "-"}`,
        `深度状态=${display(live ? "链上实时深度已验证" : sourceAnalysis.staticDepthStatus)}`,
        `最大静态深度影响 BPS=${display(live ? liveMaxDepthImpactBps : sourceAnalysis.maxStaticDepthImpactBps)}`,
        `深度影响上限 BPS=${display(live ? "不适用：已使用链上实时报价" : (sourceAnalysis.staticDepthImpactLimitBps || size.depthImpactLimitBps || 10))}`,
        `建议=${routeRecommendationText || display(recommendationText)}`,
        ...hopLines,
        ...ladderLines,
        ...(routeTestResultLine ? [routeTestResultLine] : []),
        ...(lines(evidence.blockers).map(item => errorChainText(item) || blockerText(item))),
        ...copiedWarnings,
      ].filter(Boolean).join("\n");
      try { await navigator.clipboard.writeText(text); const old = button.textContent; button.textContent = "已复制"; setTimeout(() => button.textContent = old, 1200); } catch (error) { $("message").textContent = uiError(error.message); }
    }
    $("refreshBtn").onclick = () => refresh(true);
    async function postJson(path, payload) {
      const response = await fetch(apiPath(path), {
        method: "POST",
        cache: "no-cache",
        headers: {"Content-Type": "application/json", Accept: "application/json"},
        body: JSON.stringify(payload),
      });
      const data = await response.json();
      const broadcastDisabledByBackend = Boolean(
        data?.broadcastDisabled
        || data?.error === "official_arb_broadcast_disabled"
        || String(data?.detail || data?.error || "").toLowerCase().includes("broadcast disabled")
      );
      if (!response.ok && data?.ok !== true && !broadcastDisabledByBackend) {
        const detail = data?.detail || data?.error || `请求失败（HTTP ${response.status}）`;
        throw new Error(detail);
      }
      return data;
    }
    async function refreshClosedLoopGroup(groupKey, button) {
      const signature = activeSelectionSignature;
      const old = button ? button.textContent : "";
      if (button) {
        button.disabled = true;
        button.textContent = "刷新中";
      }
      $("message").textContent = `正在定点刷新闭环 ${groupKey} ...`;
      try {
        const data = await postJson(`/api/dex/closed-loop-groups/${encodeURIComponent(groupKey)}/refresh?wait=1`, {mode: "deep"});
        if (signature !== activeSelectionSignature) return;
        const routes = Array.isArray(data?.task?.result?.routes) ? data.task.result.routes : [];
        if (routes.length) {
          const incoming = new Map(routes.map(route => [routeKey(route), route]));
          latestRoutes = latestRoutes.map(route => {
            const replacement = incoming.get(routeKey(route));
            return replacement || route;
          });
          $("routeList").innerHTML = renderPoolDirectories(latestRoutes);
          $("message").textContent = `闭环 ${groupKey} 已定点刷新`;
          scheduleSnapshotRefreshBackoff();
        } else {
          $("message").textContent = data?.task?.result?.message || `闭环 ${groupKey} 无可刷新路线`;
        }
      } catch (error) {
        if (signature === activeSelectionSignature) $("message").textContent = uiError(error.message);
      } finally {
        if (button) {
          button.textContent = old;
          button.disabled = false;
        }
      }
    }
    function routeTestSymbols(route) {
      if (route?.routeSymbols?.length) return route.routeSymbols;
      if (route?.quoteAnalysis?.routeSymbols?.length) return route.quoteAnalysis.routeSymbols;
      const steps = (route?.quoteAnalysis?.steps || []).filter(step => step && step.fromSymbol);
      if (!steps.length) return [];
      const symbols = steps.map(step => step.fromSymbol);
      const last = steps[steps.length - 1]?.toSymbol;
      if (last && (symbols.length === 0 || last === symbols[0])) symbols.push(last);
      return symbols;
    }
    function routeTestSummary(result, mode) {
      if (result?.txHash) {
        return mode === "broadcast"
          ? executionRouteTestSummary(result)
          : mode === "static_call"
            ? preflightRouteTestSummary(result)
            : estimateRouteTestSummary(result);
      }
      return mode === "broadcast"
        ? executionRouteTestSummary(result)
        : mode === "static_call"
          ? preflightRouteTestSummary(result)
          : estimateRouteTestSummary(result);
    }
    function mergeRouteActionPayload(route, payload, stage) {
      if (!route || typeof route !== "object" || !payload || typeof payload !== "object") return route;
      const incomingAnalysis = payload.analysis && typeof payload.analysis === "object" ? payload.analysis : {};
      const requestedAmount = payload.requestedAmount ?? incomingAnalysis.requestedAmount ?? payload.initialProbeAmount ?? payload.amount ?? null;
      const bestAmount = payload.bestAmount ?? payload.bestAmountCalculation?.bestAmount ?? incomingAnalysis.bestAmount ?? incomingAnalysis.best_amount ?? requestedAmount;
      const symbol = payload.amountSymbol || payload.initialProbeSymbol || incomingAnalysis.referenceInputSymbol || (Array.isArray(route.routeSymbols) ? route.routeSymbols[0] : "");
      const probeRecords = payload.probeRecords || payload.probe_records || payload.bestAmountCalculation?.probeRecords || payload.bestAmountCalculation?.probe_records || [];
      const oldSteps = Array.isArray(route.quoteAnalysis?.steps) ? route.quoteAnalysis.steps : Array.isArray(incomingAnalysis.steps) ? incomingAnalysis.steps : [];
      const scaledSteps = Array.isArray(payload.steps)
        ? payload.steps
        : Array.isArray(incomingAnalysis.steps)
          ? incomingAnalysis.steps
          : oldSteps;
      const finalOutputValue = payload.outAmountHuman ?? (Number.isFinite(Number(payload.outAmount)) ? Number(payload.outAmount) : null);
      const netProfitValue = payload.profitHuman ?? (Number.isFinite(Number(payload.profit)) ? Number(payload.profit) : null);
      const computedNetProfitBps = Number.isFinite(Number(bestAmount)) && Number(bestAmount) > 0 && Number.isFinite(Number(netProfitValue))
        ? Number(netProfitValue) / Number(bestAmount) * 10000
        : null;
      const netProfitBps = computedNetProfitBps ?? payload.referenceNetReturnBps ?? incomingAnalysis.referenceNetReturnBps ?? payload.bestAmountCalculation?.referenceNetReturnBps ?? null;
      const lastScaledOutput = scaledSteps.length ? Number(scaledSteps[scaledSteps.length - 1]?.outputAmount ?? scaledSteps[scaledSteps.length - 1]?.referenceOutputAmount) : NaN;
      const referenceFinalOutputAmount = Number.isFinite(Number(finalOutputValue)) ? Number(finalOutputValue) : Number.isFinite(lastScaledOutput) ? lastScaledOutput : incomingAnalysis.referenceFinalOutputAmount ?? null;
      const referenceNetProfitAmount = Number.isFinite(Number(netProfitValue)) ? Number(netProfitValue) : incomingAnalysis.referenceNetProfitAmount ?? null;
      const routeEvidenceBlockers = Array.isArray(payload.evidence?.blockers)
        ? payload.evidence.blockers
        : Array.isArray(route.live_onchain_evidence?.blockers)
          ? route.live_onchain_evidence.blockers
          : [];
      const rawMergedQuoteStatus = payload.quoteStatus
        || route.live_onchain_evidence?.status
        || route.quoteStatus
        || route.quoteAnalysis?.onchainEvidenceStatus
        || (route.quoteAnalysis?.livePriceVerified ? "live_verified" : "static_reference_pending_live_review");
      const rawMergedLiveVerified = String(rawMergedQuoteStatus || "").toLowerCase() === "live_verified";
      const preflightPassed = payload.ok !== false
        && (payload.staticCallPassed || payload.readyForTransactionPreflight || payload.bestAmountConfirmed === true || rawMergedLiveVerified)
        && !payload.bestAmountMissing
        && !terminalEvidenceStatus(payload.quoteStatus || payload.evidenceStatus || "");
      const routeEvidenceExisting = route.live_onchain_evidence || {};
      const mergedQuoteStatus = payload.quoteStatus
        || routeEvidenceExisting.status
        || route.quoteStatus
        || route.quoteAnalysis?.onchainEvidenceStatus
        || (route.quoteAnalysis?.livePriceVerified ? "live_verified" : "static_reference_pending_live_review");
      const mergedExecutionTrust = payload.executionTrust
        || routeEvidenceExisting.executionTrust
        || route.quoteAnalysis?.executionTrust
        || (route.quoteAnalysis?.livePriceVerified ? "live_quoter_verified_readonly" : "static_reference_pending_live_review");
      const mergedLiveVerifiedStatus = String(mergedQuoteStatus || "").toLowerCase() === "live_verified";
      const blockers = preflightPassed
        ? routeEvidenceBlockers
        : (routeEvidenceBlockers.length
            ? routeEvidenceBlockers
            : payload.detail ? [payload.detail] : []);
      const payloadBestAmountConfirmed = payload.bestAmountConfirmed === true
        || incomingAnalysis.bestAmountConfirmed === true
        || payload.bestAmountCalculation?.bestAmountConfirmed === true;
      const explicitBestAmountMissing = payload.bestAmountMissing === true
        || incomingAnalysis.bestAmountMissing === true
        || payload.bestAmountCalculation?.bestAmountMissing === true;
      const mergedAnalysis = {
        ...(route.quoteAnalysis && typeof route.quoteAnalysis === "object" ? route.quoteAnalysis : {}),
        ...incomingAnalysis,
        steps: scaledSteps.length ? scaledSteps : incomingAnalysis.steps,
        bestAmount,
        bestAmountMissing: explicitBestAmountMissing || (!mergedLiveVerifiedStatus && !payloadBestAmountConfirmed),
        bestAmountConfirmed: mergedLiveVerifiedStatus || payloadBestAmountConfirmed,
        bestAmountSelectionBasis: payload.bestAmountSelectionBasis
          || incomingAnalysis.bestAmountSelectionBasis
          || payload.bestAmountCalculation?.bestAmountSelectionBasis
          || (["preselected_best_amount", "onchain_preview_adaptive_best_amount", "onchain_preview_arbitrage_best_amount"].includes(payload.bestAmountMethod) ? payload.bestAmountMethod : "initial_probe_unconfirmed"),
        bestAmountConfirmationReason: payload.bestAmountConfirmationReason
          || incomingAnalysis.bestAmountConfirmationReason
          || payload.bestAmountCalculation?.bestAmountConfirmationReason
          || "no confirmed best amount",
        amountOptimizationMethod: payload.amountOptimizationMethod
          || incomingAnalysis.amountOptimizationMethod
          || payload.bestAmountMethod
          || "route_action",
        amountIsBest: payload.amountIsBest === true || incomingAnalysis.amountIsBest === true,
        requestedAmount,
        initialProbeAmount: payload.initialProbeAmount ?? incomingAnalysis.initialProbeAmount ?? requestedAmount,
        initialProbeSymbol: payload.initialProbeSymbol || incomingAnalysis.initialProbeSymbol || symbol,
        referenceInputAmount: bestAmount,
        referenceInputSymbol: symbol,
        referenceFinalOutputAmount,
        referenceFinalOutputSymbol: incomingAnalysis.referenceFinalOutputSymbol || symbol,
        referenceNetProfitAmount,
        referenceNetReturnBps: netProfitBps,
        amountOptimizedFrom: payload.requestedAmount ?? incomingAnalysis.amountOptimizedFrom ?? requestedAmount,
        amountOptimized: Number.isFinite(Number(bestAmount)) && Number.isFinite(Number(requestedAmount)) && Math.abs(Number(bestAmount) - Number(requestedAmount)) > 1e-12,
        probeRecords,
        probe_records: probeRecords,
        bestAmountCalculation: payload.bestAmountCalculation || {} ,
      };
      route.quoteAnalysis = mergedAnalysis;
      route.live_onchain_evidence = {
        ...routeEvidenceExisting,
        status: mergedQuoteStatus,
        executionTrust: mergedExecutionTrust,
        offchainInitialProbeReason: (
          payload.evidence?.offchainInitialProbeReason
          || (["preflight", "preflight_failed", "execution", "execution_pending", "execution_failed"].includes(stage)
            ? (mergedLiveVerifiedStatus || payloadBestAmountConfirmed
              ? "onchain_review_best_amount_reused"
              : "requested_borrow_amount_fallback")
            : routeEvidenceExisting.offchainInitialProbeReason || "")
        ),
        costModel: {
          ...(routeEvidenceExisting.costModel || {}),
          borrowInputAmount: bestAmount,
          borrowInputSymbol: symbol,
          selectedBorrowInputAmount: bestAmount,
          requestedBorrowInputAmount: requestedAmount,
          amountIsBest: payload.amountIsBest === true || incomingAnalysis.amountIsBest === true,
          bestAmountMissing: explicitBestAmountMissing || (!mergedLiveVerifiedStatus && !payloadBestAmountConfirmed),
          bestAmountConfirmed: mergedLiveVerifiedStatus || payloadBestAmountConfirmed,
          bestAmountSelectionBasis: payload.bestAmountSelectionBasis
            || incomingAnalysis.bestAmountSelectionBasis
            || payload.bestAmountCalculation?.bestAmountSelectionBasis
            || "initial_probe_unconfirmed",
          bestAmountConfirmationReason: payload.bestAmountConfirmationReason
            || incomingAnalysis.bestAmountConfirmationReason
            || payload.bestAmountCalculation?.bestAmountConfirmationReason
            || "no confirmed best amount",
          netProfitAmount: referenceNetProfitAmount ?? payload.profitHuman ?? humanTokenValue(payload.analysis?.referenceNetProfitAmount ?? payload.profit) ?? routeEvidenceExisting.costModel?.netProfitAmount,
          netProfitAmountBps: netProfitBps ?? routeEvidenceExisting.costModel?.netProfitAmountBps,
          finalOutputAmount: referenceFinalOutputAmount ?? routeEvidenceExisting.costModel?.finalOutputAmount,
          finalOutputSymbol: incomingAnalysis.referenceFinalOutputSymbol || symbol,
        },
        blockers,
      };
      const mergedBestAmountUnconfirmed = mergedAnalysis.bestAmountMissing === true;
      route.bestAmountMissing = mergedAnalysis.bestAmountMissing === true;
      route.bestAmountConfirmed = mergedAnalysis.bestAmountConfirmed === true;
      const resolvedStage = stage === "preflight" && mergedBestAmountUnconfirmed ? "estimate" : (stage || "estimate");
      setRouteUiState(route, uiStateFromStage(resolvedStage), {
        quoteStatus: String(mergedQuoteStatus),
      });
      return route;
    }
    function routeTestAdvice(result, mode) {
      if (result?.txHash) {
        return mode === "broadcast"
          ? executionRouteTestAdvice(result)
          : mode === "static_call"
            ? preflightRouteTestAdvice(result)
            : estimateRouteTestAdvice(result);
      }
      return mode === "broadcast"
        ? executionRouteTestAdvice(result)
        : mode === "static_call"
          ? preflightRouteTestAdvice(result)
          : estimateRouteTestAdvice(result);
    }
    async function runRouteTest(route, button, mode, resultElement) {
      const sizeAnalysis = route?.sizeAnalysis || {};
      const calcBest = route?.quoteAnalysis?.bestAmountCalculation || route?.bestAmountCalculation || {};
      const costModel = route?.live_onchain_evidence?.costModel || {};
      const bestConfirmed = route?.quoteAnalysis?.bestAmountConfirmed === true
        || route?.quoteAnalysis?.best_amount_confirmed === true
        || costModel?.bestAmountConfirmed === true
        || route?.live_onchain_evidence?.bestAmountConfirmed === true;
      const confirmedHistorical = bestConfirmed
        ? Number(route?.quoteAnalysis?.bestAmount ?? route?.quoteAnalysis?.best_amount ?? costModel?.selectedBorrowInputAmount ?? NaN)
        : NaN;
      const tokenSteps = Array.isArray(sizeAnalysis?.tokenAmountSteps) ? sizeAnalysis.tokenAmountSteps : [];
      const recommendedInitial = Number(
        calcBest?.recommendedBorrowInputAmount
        ?? sizeAnalysis?.recommendedBorrowInputAmount
        ?? (tokenSteps.length ? tokenSteps[tokenSteps.length - 1] : null)
        ?? null
      );
      const currentBestAmount = Number.isFinite(confirmedHistorical) && confirmedHistorical > 0
        ? confirmedHistorical
        : (Number.isFinite(recommendedInitial) && recommendedInitial > 0 ? recommendedInitial : NaN);
      const actionRequestedAmount = Number(route.__requestedAmount);
      const previousBestAmount = Number.isFinite(currentBestAmount) && currentBestAmount > 0
        ? currentBestAmount
        : (Number.isFinite(actionRequestedAmount) && actionRequestedAmount > 0
            ? actionRequestedAmount
            : autoRouteAmount(route));
      const hasHistoricalReview = Number.isFinite(currentBestAmount) && currentBestAmount > 0;
      const initialAmountLabel = hasHistoricalReview ? "历史复核金额" : "初始测试金额";
      const amount = previousBestAmount;
      const symbols = routeTestSymbols(route);
      const routeIndex = Number(resultElement?.dataset.routeTestResult ?? button?.dataset.routeIndex ?? button?.dataset.routeLiveEstimate ?? button?.dataset.routeStaticCall ?? button?.dataset.routeBroadcast);
      const rerenderRouteList = () => {
        $("routeList").innerHTML = renderPoolDirectories(latestRoutes.slice());
        resultElement = Number.isFinite(routeIndex)
          ? document.querySelector(`[data-route-test-result="${routeIndex}"]`)
          : null;
      };
      const broadcast = mode === "broadcast";
      if (broadcast && !window.confirm(`确认执行这笔链上交易？执行前会把${initialAmountLabel}作为初始量，重新链上复核当前最佳量后再提交链上交易。`)) return;
      const requestMode = mode === "estimate" ? "estimate" : (mode === "static_call" ? "static_call" : "auto");
      const old = button ? button.textContent : `${mode === "estimate" ? "链下估算" : "链上复核"}中`;
      if (button) button.disabled = true;
      if (mode !== "broadcast") setPageState(PAGE_STATE.REVIEW);
      if (mode === "broadcast") {
        setPageState(PAGE_STATE.EXECUTION);
        activeExecution = {
          routeKey: routeExecutionKey(route),
          state: ROUTE_UI_STATE.EXECUTION_PENDING,
          txHash: "",
          startedAt: Date.now(),
        };
      }
      setRouteUiState(route, mode === "broadcast" ? ROUTE_UI_STATE.EXECUTION_PENDING : (mode === "static_call" ? ROUTE_UI_STATE.PREFLIGHT_RUNNING : ROUTE_UI_STATE.OFFCHAIN_PENDING), {
        quoteStatus: route.quoteStatus || "",
      });
      route.__broadcastError = "";
      route.__requestedAmount = amount;
      route.__requestedSymbol = symbols[0] || autoRouteAmountSymbol(route);
      const oldMessage = $("message").textContent;
      $("message").textContent = `正在${mode === "broadcast" ? "发起链上执行" : mode === "static_call" ? "执行链上复核" : "执行链下估算"}...`;
      if (resultElement) resultElement.textContent = "处理中...";
      try {
        const payload = await postJson("/api/dex/quote-route/test", {
          routeSymbols: symbols,
          routeAddresses: route?.routeAddresses || [],
          amount,
          amountIsBest: false,
          mode: requestMode,
          broadcast,
        });
        const broadcastDisabledPayload = mode === "broadcast" && Boolean(
          payload?.broadcastDisabled
          || payload?.error === "official_arb_broadcast_disabled"
          || String(payload?.detail || payload?.error || "").toLowerCase().includes("broadcast disabled")
        );
        if (mode === "estimate") {
          if (payload?.ok) {
            mergeRouteTestPayload(route, payload);
            rerenderRouteList();
          }
        } else if (mode === "broadcast" || payload?.ok || payload?.txHash || payload?.bestAmount || payload?.bestAmountCalculation) {
          mergeRouteActionPayload(
            route,
            payload,
            mode === "broadcast"
              ? (payload?.txHash ? "execution" : broadcastDisabledPayload ? "preflight" : "execution_failed")
              : mode === "static_call"
                ? "preflight"
                : (uiStageFromUiState(uiStateOf(route)) || "estimate")
          );
          const mergedKey = routeKey(route);
          const currentIndex = latestRoutes.findIndex(candidate => routeKey(candidate) === mergedKey);
          if (currentIndex >= 0) latestRoutes[currentIndex] = route;
          rerenderRouteList();
        }
        if (mode === "static_call" && !payload?.ok && !payload?.txHash && !payload?.bestAmount && !payload?.bestAmountCalculation) {
          markPreflightFailed(
            route,
            String(payload?.detail || payload?.error || "链上复核失败"),
            String(payload?.quoteStatus || "rpc_verification_failed")
          );
          rerenderRouteList();
        }
        const summary = routeTestSummary(payload, mode);
        const advice = routeTestAdvice(payload, mode);
        if (mode === "broadcast" && payload?.txHash) {
          setRouteUiState(route, ROUTE_UI_STATE.EXECUTION_SUBMITTED, {quoteStatus: "broadcast_submitted"});
          activeExecution = {
            routeKey: routeExecutionKey(route),
            state: ROUTE_UI_STATE.EXECUTION_SUBMITTED,
            txHash: payload.txHash,
            startedAt: Date.now(),
          };
          route.__broadcastError = "";
          route.__broadcastDisabled = false;
        } else if (mode === "broadcast") {
          if (broadcastDisabledPayload) {
            route.__broadcastDisabled = true;
            route.__broadcastError = "";
            activeExecution = {
              routeKey: routeExecutionKey(route),
              state: ROUTE_UI_STATE.PREFLIGHT_DONE,
              broadcastDisabled: true,
              txHash: "",
              startedAt: Date.now(),
            };
            setRouteUiState(route, ROUTE_UI_STATE.PREFLIGHT_DONE, {quoteStatus: "broadcast_disabled"});
          } else {
            setRouteUiState(route, ROUTE_UI_STATE.EXECUTION_FAILED, {quoteStatus: "broadcast_failed"});
            activeExecution.state = ROUTE_UI_STATE.EXECUTION_FAILED;
            route.__broadcastError = advice ? String(advice).replace(/^建议：/, "") : "链上执行未提交";
          }
          rerenderRouteList();
        }
        if (broadcast && payload?.txHash) {
          await loadLatestSubmissions().catch(() => {});
        }
        if (resultElement) resultElement.textContent = advice ? `${summary} | ${advice}` : summary;
        $("message").textContent = advice ? `${summary} | ${advice}` : summary;
        if (Number.isFinite(routeIndex)) {
          const cleanAdvice = advice ? String(advice).replace(/^建议：/, "") : "已更新本次测试结果，请结合链上状态决定下一步";
          document.querySelectorAll(`[data-route-recommendation="${routeIndex}"]`).forEach(el => {
            el.innerHTML = `<strong>建议：${esc(cleanAdvice)}</strong>`;
          });
        }
      } catch (error) {
        if (mode === "broadcast") {
          setRouteUiState(route, ROUTE_UI_STATE.EXECUTION_FAILED, {quoteStatus: "broadcast_failed"});
          activeExecution.state = ROUTE_UI_STATE.EXECUTION_FAILED;
          route.__broadcastError = uiError(error.message) || "请检查网络或稍后重试；仍失败可重启服务";
        } else if (mode === "static_call") {
          markPreflightFailed(route, `链上复核失败：${uiError(error.message)}`, "rpc_verification_failed");
        } else {
          setRouteUiState(route, ROUTE_UI_STATE.OFFCHAIN_PENDING, {quoteStatus: ""});
          route.__broadcastError = "";
        }
        const symbol = route.__requestedSymbol || routeTestSymbols(route)[0] || autoRouteAmountSymbol(route);
        const amount = route.__requestedAmount ?? autoRouteAmount(route);
        if (route.quoteAnalysis && typeof route.quoteAnalysis === "object") {
          route.quoteAnalysis.requestedAmount = amount;
          route.quoteAnalysis.initialProbeAmount = amount;
          route.quoteAnalysis.initialProbeSymbol = symbol;
          route.quoteAnalysis.referenceInputAmount = amount;
          route.quoteAnalysis.referenceInputSymbol = symbol;
        }
        if (route.live_onchain_evidence && typeof route.live_onchain_evidence === "object") {
          route.live_onchain_evidence.costModel = {
            ...(route.live_onchain_evidence.costModel || {}),
            requestedBorrowInputAmount: amount,
            borrowInputAmount: amount,
            selectedBorrowInputAmount: amount,
            borrowInputSymbol: symbol,
          };
        }
        rerenderRouteList();
        if (resultElement) resultElement.textContent = uiError(error.message);
        $("message").textContent = `${uiError(error.message)} | 建议：请检查网络或稍后重试；仍失败可重启服务。`;
        if (Number.isFinite(routeIndex)) {
          document.querySelectorAll(`[data-route-recommendation="${routeIndex}"]`).forEach(el => {
            el.innerHTML = `<strong>建议：${esc(uiError(error.message) || "请检查网络或稍后重试；仍失败可重启服务")}</strong>`;
          });
        }
      } finally {
        if (button) {
          button.textContent = old;
          button.disabled = false;
        }
        if (pageStateIs(PAGE_STATE.REVIEW, PAGE_STATE.EXECUTION)) setPageState(PAGE_STATE.SCAN);
        if (!resultElement?.textContent) $("message").textContent = oldMessage;
      }
    }
    function scheduleReviewActiveRoute() {
      if (pageStateIs(PAGE_STATE.EXECUTION)) return;
      if (autoReviewRunning) {
        autoReviewQueued = true;
        return;
      }
      autoReviewRunning = true;
      autoReviewQueued = false;
      setPageState(PAGE_STATE.REVIEW);
      autoReviewActiveRoute()
        .catch(() => {})
        .finally(() => {
          autoReviewRunning = false;
          setPageState(PAGE_STATE.SCAN);
          if (autoReviewQueued) {
            autoReviewQueued = false;
            scheduleReviewActiveRoute();
          }
        });
    }
    async function autoReviewActiveRoute() {
      const generation = ++autoReviewToken;
      const targetKey = focusedRouteKey();
      const targetIndex = targetKey ? latestRoutes.findIndex(route => routeKey(route) === targetKey) : -1;
      const routes = targetIndex >= 0 ? latestRoutes.slice(targetIndex, targetIndex + 1) : latestRoutes.slice(0, 1);
      for (let index = 0; index < routes.length; index += 1) {
        if (generation !== autoReviewToken) return;
        const initialRoute = routes[index];
        if (!initialRoute) continue;
        if (!canAutoEstimate(initialRoute) || shouldPreserveRouteState(initialRoute)) continue;
        const key = routeKey(initialRoute);
        let currentIndex = latestRoutes.findIndex(candidate => routeKey(candidate) === key);
        if (currentIndex < 0) {
          latestRoutes.push(initialRoute);
          currentIndex = latestRoutes.length - 1;
        }
        const route = latestRoutes[currentIndex];
        const resultElement = document.querySelector(`[data-route-test-result="${currentIndex}"]`);
        await runRouteTest(route, null, "estimate", resultElement);
        await new Promise(resolve => setTimeout(resolve, 180));
      }
    }
    $("routeList").addEventListener("click", event => {
      const refreshButton = event.target.closest("[data-closed-loop-refresh]");
      if (refreshButton) {
        refreshClosedLoopGroup(refreshButton.dataset.closedLoopRefresh, refreshButton);
        return;
      }
      const copyButton = event.target.closest("[data-copy-route]");
      if (copyButton) {
        const copyIndex = Number(copyButton.dataset.copyRoute);
        if (Number.isFinite(copyIndex) && latestRoutes[copyIndex]) activeRouteKey = routeKey(latestRoutes[copyIndex]);
        copyRoute(latestRoutes[copyIndex], copyButton);
      }
      const button = event.target.closest("[data-route-static-call], [data-route-broadcast]");
      if (!button) return;
      const index = Number(button.dataset.routeIndex ?? button.dataset.routeLiveEstimate ?? button.dataset.routeStaticCall ?? button.dataset.routeBroadcast);
      if (Number.isFinite(index) && latestRoutes[index]) activeRouteKey = routeKey(latestRoutes[index]);
      const mode = button.hasAttribute("data-route-broadcast") ? "broadcast" : button.hasAttribute("data-route-static-call") ? "static_call" : "estimate";
      const resultElement = document.querySelector(`[data-route-test-result="${index}"]`);
      return runRouteTest(latestRoutes[index], button, mode, resultElement);
    });
    window.addEventListener("chainselectionchanged", event => {
      if (String(event?.detail?.selected?.feature || "").toLowerCase() !== "dex_arbitrage") return;
      if (selectionSignature() === activeSelectionSignature && latestRoutes.length) return;
      beginSelectionRefresh("正在加载选中的链...");
      refresh();
    });
    window.addEventListener("chainselectionprotocolschanged", () => {
      if (selectionSignature() === activeSelectionSignature && latestRoutes.length) return;
      beginSelectionRefresh("正在加载选中的 DEX...");
      refresh();
    });
    beginSelectionRefresh("正在加载当前选择...");
    refresh();
    function normalizeRefreshInterval(value) {
      const parsed = Number(value);
      return Number.isFinite(parsed) ? Math.min(3600, Math.max(10, Math.floor(parsed))) : defaultRefreshIntervalSeconds;
    }
    function scheduleRefresh() {
      const seconds = normalizeRefreshInterval($("refreshInterval").value);
      $("refreshInterval").value = String(seconds);
      try { window.localStorage.setItem(refreshIntervalKey, String(seconds)); } catch (_) {}
      if (refreshTimer) clearInterval(refreshTimer);
      refreshTimer = setInterval(refreshSnapshot, seconds * 1000);
    }
    try {
      $("refreshInterval").value = normalizeRefreshInterval(window.localStorage.getItem(refreshIntervalKey) || defaultRefreshIntervalSeconds);
    } catch (_) {}
    function setupAutoExecuteToggle() {
      const toggle = document.getElementById("autoExecuteToggle");
      if (!toggle) return;
      toggle.addEventListener("change", async () => {
        const next = Boolean(toggle.checked);
        try {
          await postJson("/api/dex/opportunity-scanner/auto-execute", {enabled: next});
          try { window.localStorage.setItem(autoExecuteEnabledKey, next ? "true" : "false"); } catch (_) {}
          $("message").textContent = next ? "自动扫描复核并链上广播已开启" : "自动扫描复核并链上广播未开启";
          scheduleSnapshotRefreshBackoff();
        } catch (error) {
          toggle.checked = !next;
          $("message").textContent = uiError(error.message);
        }
      });
    }
    setupAutoExecuteToggle();
    const resetStatsBtn = document.getElementById("resetStatsBtn");
    if (resetStatsBtn) {
      resetStatsBtn.addEventListener("click", async () => {
        try {
          await postJson("/api/dex/opportunity-scanner/stats/reset", {});
          $("message").textContent = "统计已清零";
          await refresh();
        } catch (error) {
          $("message").textContent = uiError(error.message);
        }
      });
    }
    $("refreshInterval").addEventListener("change", scheduleRefresh);
    scheduleRefresh();
