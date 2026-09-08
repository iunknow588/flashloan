(function () {
  "use strict";

  const FRAME_ID = "app-shell-content-frame";
  const NAVIGATE_MESSAGE = "flashloan:navigate";
  const SHELL_SELECTION_STORAGE_KEY = "flashloan:app-shell-selection";

  function isEmbedded() {
    return window.parent !== window || document.body && document.body.classList.contains("embedded");
  }

  function shellStorageFeature(feature) {
    const normalized = String(feature || "").trim().toLowerCase();
    if (normalized === "liquidation" || normalized === "dex_arbitrage") {
      return normalized;
    }
    return pathFeature(window.location.pathname) || "";
  }

  function shellSelectionStorageKey(feature) {
    const normalized = shellStorageFeature(feature);
    return normalized ? SHELL_SELECTION_STORAGE_KEY + ":" + normalized : SHELL_SELECTION_STORAGE_KEY;
  }

  function targetLink(event) {
    if (!event || event.defaultPrevented || event.button !== 0) return null;
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return null;
    const node = event.target && event.target.closest
      ? event.target.closest("a[data-shell-link], a[data-flash-loan-protocol-link], a.app-shell-brand")
      : null;
    if (!node || node.target && node.target !== "_self") return null;
    return node;
  }

  function normalizedPath(pathname) {
    return String(pathname || "/").replace(/\/+$/, "") || "/";
  }

  function pathFeature(pathname) {
    const path = normalizedPath(pathname);
    if (path === "/" || path === "/liquidation" || path === "/account-scan" || path === "/execution" || path === "/audit" || path.startsWith("/bot")) {
      return "liquidation";
    }
    if (path === "/binance-market" || path === "/dex-arbitrage" || path === "/dex-pools" || path === "/candidate-pools" || path === "/market-observation" || path === "/flash-loan" || path.startsWith("/flash-loan") || path.startsWith("/dex") || path.startsWith("/robinhood-dex")) {
      return "dex_arbitrage";
    }
    return "";
  }

  function readStoredSelection() {
    try {
      const key = shellSelectionStorageKey();
      const raw = window.sessionStorage.getItem(key) ||
        (key === SHELL_SELECTION_STORAGE_KEY ? "" : window.sessionStorage.getItem(SHELL_SELECTION_STORAGE_KEY));
      const stored = raw ? JSON.parse(raw) : null;
      return stored && typeof stored === "object" ? stored : null;
    } catch (_) {
      return null;
    }
  }

  function currentChainId() {
    const query = new URLSearchParams(window.location.search);
    const scoped = window.__FLASHLOAN_MARKET_SCOPE__ || {};
    const stored = readStoredSelection() || {};
    return query.get("chain_id") ||
      (scoped.chain_id === null || scoped.chain_id === undefined ? "" : String(scoped.chain_id)) ||
      (stored.chain_id === null || stored.chain_id === undefined ? "" : String(stored.chain_id));
  }

  function rootPathForFeature(feature) {
    const normalized = String(feature || "").trim().toLowerCase();
    if (normalized === "dex_arbitrage") return "/dex";
    return "/bot";
  }

  function setFeatureState(feature) {
    const normalized = String(feature || "liquidation").trim().toLowerCase();
    document.querySelectorAll("[data-feature-selector-select]").forEach(function (select) {
      select.value = normalized === "dex_arbitrage" ? "dex_arbitrage" : "liquidation";
    });
    document.querySelectorAll("[data-feature-selection-meta]").forEach(function (node) {
      node.textContent = "当前功能域 " + (normalized === "dex_arbitrage" ? "DEX" : "BOT");
    });
    if (document.body) {
      document.body.dataset.appShellFeature = normalized;
    }
  }

  function sanitizeNavigationUrl(url) {
    const currentFeature = pathFeature(window.location.pathname);
    const targetFeature = pathFeature(url.pathname);
    if (!currentFeature || !targetFeature || currentFeature === targetFeature) {
      return url;
    }
    url.searchParams.delete("market_id");
    url.searchParams.delete("protocols");
    url.searchParams.delete("protocol_id");
    url.searchParams.delete("cow_networks");
    url.searchParams.delete("cow_network");
    url.searchParams.delete("scope");
    url.searchParams.delete("execution_mode");
    if (targetFeature !== "dex_arbitrage") {
      url.searchParams.delete("dex_strategy");
    }
    return url;
  }

  function embeddedUrl(href) {
    const url = new URL(href, window.location.origin);
    url.searchParams.set("embed", "1");
    return url;
  }

  function displayUrl(href) {
    const url = new URL(href, window.location.origin);
    url.searchParams.delete("embed");
    return url;
  }

  function ensureFrame() {
    let frame = document.getElementById(FRAME_ID);
    if (frame) return frame;
    frame = document.createElement("iframe");
    frame.id = FRAME_ID;
    frame.name = FRAME_ID;
    frame.className = "app-shell-content-frame";
    frame.title = "闪电贷内容";
    frame.setAttribute("data-app-shell-content-frame", "true");
    document.body.appendChild(frame);
    document.body.classList.add("app-shell-framed");
    return frame;
  }

  function setActiveLink(url) {
    const target = normalizedPath(url.pathname);
    document.querySelectorAll("[data-shell-link], [data-flash-loan-protocol-link]").forEach(function (link) {
      const href = link.getAttribute("href") || "";
      const linkUrl = new URL(href, window.location.origin);
      const active = normalizedPath(linkUrl.pathname) === target &&
        (!linkUrl.searchParams.get("protocols") || linkUrl.searchParams.get("protocols") === url.searchParams.get("protocols"));
      link.classList.toggle("active", active);
    });
    const nextFeature = pathFeature(url.pathname);
    if (nextFeature) setFeatureState(nextFeature);
  }

  function navigate(href, options) {
    const config = options || {};
    const shown = sanitizeNavigationUrl(displayUrl(href));
    const frameTarget = embeddedUrl(shown.href);
    const frame = ensureFrame();
    frame.dataset.appShellTarget = shown.pathname + shown.search + shown.hash;
    frame.src = frameTarget.pathname + frameTarget.search + frameTarget.hash;
    setActiveLink(shown);
    if (window.history && typeof window.history[config.replace ? "replaceState" : "pushState"] === "function") {
      window.history[config.replace ? "replaceState" : "pushState"](
        { appShellFrame: true },
        "",
        shown.pathname + shown.search + shown.hash
      );
    }
    window.dispatchEvent(new CustomEvent("appshellnavigate", {
      detail: {
        href: shown.pathname + shown.search + shown.hash,
        frame: frame,
      },
    }));
    return frame;
  }

  function handleClick(event) {
    const link = targetLink(event);
    if (!link) return;
    const href = link.getAttribute("href") || "";
    const url = new URL(href, window.location.origin);
    if (url.origin !== window.location.origin) return;
    event.preventDefault();
    navigate(url.pathname + url.search + url.hash);
  }

  function handleChange(event) {
    const select = event.target && event.target.closest
      ? event.target.closest("[data-feature-selector-select]")
      : null;
    if (!select) return;
    const url = new URL(rootPathForFeature(select.value), window.location.origin);
    const chainId = currentChainId();
    if (chainId) url.searchParams.set("chain_id", chainId);
    // A feature switch changes the shell's selector, sidebar, and content contract.
    // Rebuild the top-level document instead of retaining a shell from a system iframe.
    window.location.assign(url.pathname + url.search);
  }

  function handleMessage(event) {
    if (event.origin !== window.location.origin) return;
    const data = event.data || {};
    if (!data || data.type !== NAVIGATE_MESSAGE || !data.href) return;
    navigate(data.href);
  }

  function handlePopState() {
    if (!document.body || !document.body.classList.contains("app-shell-framed")) return;
    navigate(window.location.pathname + window.location.search + window.location.hash, { replace: true });
  }

  document.addEventListener("click", handleClick);
  document.addEventListener("change", handleChange);
  window.addEventListener("message", handleMessage);
  window.addEventListener("popstate", handlePopState);
  setFeatureState(pathFeature(window.location.pathname) || "liquidation");

  window.AppShell = {
    navigate: navigate,
    isEmbedded: isEmbedded,
    contentFrame: function () {
      return document.getElementById(FRAME_ID);
    },
  };
})();
