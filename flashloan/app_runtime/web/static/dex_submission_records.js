(function () {
  "use strict";

  const PAGE_SIZE = 20;
  const PAGE_BY_KIND = window.DEX_SUBMISSION_RECORD_PAGE_CONFIG || {};
  const model = window.DexSubmissionRecordModel;
  const renderers = window.DexSubmissionRecordRender;
  if (!model || !renderers) throw new Error("DEX submission record modules are not loaded");

  const $ = id => document.getElementById(id);

    let state = {
      kind: null,
      page: 1,
      totalPages: 1,
      rows: [],
      summary: {},
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
        if (fallbackChainId && fallbackChainId === chainId) normalized.set("market_id", fallbackMarketId);
      }
      return normalized;
    };
    const apiPath = path => {
      const scoped = window.ChainSelector && window.ChainSelector.withSelection
        ? window.ChainSelector.withSelection(path)
        : path;
      const url = new URL(scoped, window.location.origin);
      if (!(window.ChainSelector && window.ChainSelector.withSelection)) {
        url.search = normalizeScopeQuery(new URLSearchParams(window.location.search)).toString();
      } else if (window.ChainSelector && window.ChainSelector.normalizeScopeQuery) {
        url.search = window.ChainSelector.normalizeScopeQuery(url.searchParams).toString();
      }
      url.searchParams.delete("protocols");
      return url.pathname + (url.search ? url.search : "");
    };
    async function fetchJson(path) {
      const response = await fetch(apiPath(path), {cache: "no-cache", headers: {"Accept": "application/json"}});
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || `请求失败（HTTP ${response.status}）`);
      return data;
    }

  const {
    renderSummary,
    renderHeaders,
    renderRows,
    copyRecord,
    renderPagination,
  } = renderers.create(state, PAGE_BY_KIND);

    document.addEventListener("click", event => {
      const button = event.target.closest("[data-copy-record]");
      if (!button) return;
      if (!state.kind || state.kind === "overview") return;
      const row = state.rows[Number(button.dataset.copyRecord)];
      if (row) copyRecord(row, button);
    });

    const load = async () => {
      const config = PAGE_BY_KIND[state.kind];
      $("message").textContent = "正在刷新记录...";
      try {
        const payload = await fetchJson(`${config.endpoint}?limit=300&page=${state.page}&page_size=${PAGE_SIZE}`);
        state.rows = Array.isArray(payload.attempts) ? payload.attempts : [];
        state.summary = payload.summary || {};
        renderHeaders();
        renderSummary(payload);
        renderRows(state.rows);
        renderPagination(payload.pagination);
        $("message").textContent = `已加载 ${state.rows.length} 条记录`;
      } catch (error) {
        $("message").textContent = error && error.message ? `${error.message}` : "请求失败";
        renderRows([]);
      }
    };

    let tabsBound = false;
    const setActiveTab = kind => {
      document.querySelectorAll("[data-record-kind]").forEach(tab => {
        tab.classList.toggle("active", tab.getAttribute("data-record-kind") === kind);
      });
    };
    const switchKind = kind => {
      if (!PAGE_BY_KIND[kind] || state.kind === kind) return;
      state.kind = kind;
      state.page = 1;
      state.totalPages = 1;
      const config = PAGE_BY_KIND[kind];
      document.title = config.title;
      const heading = document.querySelector("h1");
      if (heading) heading.textContent = config.title;
      document.body.setAttribute("data-dex-surface-template", config.surface);
      setActiveTab(kind);
      renderHeaders();
      if (kind === "overview") {
        const frameworkPanel = document.getElementById("routeFrameworkPanel");
        const auditPanel = document.getElementById("broadcastFailureAuditPanel");
        const clearBtn = document.getElementById("clearBtn");
        if (frameworkPanel) frameworkPanel.style.display = "";
        if (auditPanel) auditPanel.style.display = "";
        if (clearBtn) clearBtn.style.display = "";
      }
      load();
    };

    window.DexSubmissionRecordPage = {
      init(kind) {
        const config = PAGE_BY_KIND[kind];
        if (!config) return;
        state.kind = kind;
        setActiveTab(kind);
        document.title = config.title;
        const heading = document.querySelector("h1");
        if (heading) heading.textContent = config.title;
        document.body.setAttribute("data-dex-surface-template", config.surface);
        const message = $("message");
        if (message) message.textContent = "等待记录";
        renderHeaders();
        if (!tabsBound) {
          tabsBound = true;
          document.querySelectorAll("[data-record-kind]").forEach(tab => {
            tab.addEventListener("click", event => {
              event.preventDefault();
              switchKind(tab.getAttribute("data-record-kind"));
            });
          });
          const refreshBtn = $("refreshBtn");
          if (refreshBtn) refreshBtn.addEventListener("click", () => load());
          const prevPageBtn = $("prevPageBtn");
          if (prevPageBtn) prevPageBtn.addEventListener("click", () => {
            if (state.page > 1) { state.page -= 1; load(); }
          });
          const nextPageBtn = $("nextPageBtn");
          if (nextPageBtn) nextPageBtn.addEventListener("click", () => {
            if (state.page < state.totalPages) { state.page += 1; load(); }
          });
        }
        load();
      },
      switchKind,
      refresh() {
        if (state.kind && PAGE_BY_KIND[state.kind]) load();
      },
      previousPage() {
        if (state.kind && PAGE_BY_KIND[state.kind] && state.page > 1) {
          state.page -= 1;
          load();
        }
      },
      nextPage() {
        if (state.kind && PAGE_BY_KIND[state.kind] && state.page < state.totalPages) {
          state.page += 1;
          load();
        }
      },
      reset(kind) {
        state.kind = kind || null;
        state.page = 1;
        state.totalPages = 1;
        state.rows = [];
        state.summary = {};
      },
    };
  })();
