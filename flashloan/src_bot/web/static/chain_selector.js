(function () {
  "use strict";

  function queryFromSelection(selection) {
    const query = new URLSearchParams();
    if (selection && selection.market_id) query.set("market_id", selection.market_id);
    if (selection && selection.chain_id !== null && selection.chain_id !== undefined) {
      query.set("chain_id", String(selection.chain_id));
    }
    return query;
  }

  function currentQuery() {
    const query = new URLSearchParams(window.location.search);
    return query;
  }

  function selectionKey(selection) {
    if (!selection) return "";
    return String(selection.market_id || "") + ":" + String(
      selection.chain_id === null || selection.chain_id === undefined ? "" : selection.chain_id
    );
  }

  function isRobinhoodShadowMarket(value) {
    return String(value || "").toLowerCase() === "robinhood-dex-shadow";
  }

  function isCowExecutionRail(selection) {
    return !!selection && (
      String(selection.market_type || "").toLowerCase() === "execution_rail" ||
      String(selection.market_id || "").toLowerCase() === "cow-dex-arbitrage"
    );
  }

  function isCowNetworkSelection(selection) {
    return isCowExecutionRail(selection);
  }

  function pathFeature(pathname) {
    const path = String(pathname || "/").split("?", 1)[0].replace(/\/$/, "") || "/";
    if (path === "/" || path === "/liquidation" || path === "/account-scan" || path === "/execution" || path === "/audit" || path.startsWith("/bot")) {
      return "liquidation";
    }
    if (path === "/binance-market" || path === "/dex-arbitrage" || path === "/dex-pools" || path === "/candidate-pools" || path === "/market-observation" || path.startsWith("/dex") || path.startsWith("/robinhood-dex")) {
      return "dex_arbitrage";
    }
    return "";
  }

  function clearMarketScope(url) {
    ["market_id", "chain_id", "feature", "cow_network", "cow_networks"].forEach(function (key) {
      url.searchParams.delete(key);
    });
  }

  function clearScopeStorage(storage) {
    if (!storage) return;
    for (let index = storage.length - 1; index >= 0; index -= 1) {
      const key = storage.key(index);
      if (key && (key.startsWith("flashloan:") || key.startsWith("flashloan-"))) {
        storage.removeItem(key);
      }
    }
  }

  function invalidateScopeCaches(previous, next) {
    if (selectionKey(previous) === selectionKey(next)) return false;
    try {
      clearScopeStorage(window.sessionStorage);
      clearScopeStorage(window.localStorage);
    } catch (_) {
      // Storage can be unavailable in private or embedded browser contexts.
    }
    if (window.caches && typeof window.caches.keys === "function") {
      window.caches.keys()
        .then(function (names) {
          return Promise.all(names
            .filter(function (name) { return name.startsWith("flashloan-"); })
            .map(function (name) { return window.caches.delete(name); }));
        })
        .catch(function () {});
    }
    window.__FLASHLOAN_SCOPE_CACHE__ = {};
    if (window.WorkflowContext) {
      window.WorkflowContext.ready = false;
      window.WorkflowContext.payload = null;
    }
    window.dispatchEvent(new CustomEvent("chainselectionchanging", {
      detail: { previous: previous || {}, next: next || {} }
    }));
    return true;
  }

  function withSelection(path) {
    const url = new URL(path, window.location.origin);
    const current = currentQuery();
    const fallback = window.__CHAIN_SELECTOR_DEFAULT__ || {};
    const targetFeature = pathFeature(url.pathname);
    const currentFeature = pathFeature(window.location.pathname);
    if (targetFeature && currentFeature && targetFeature !== currentFeature) {
      clearMarketScope(url);
      return url.pathname + (url.search ? url.search : "");
    }
    if (!current.get("market_id") && fallback.market_id && !url.searchParams.get("market_id")) {
      url.searchParams.set("market_id", String(fallback.market_id));
    }
    if (!current.get("chain_id") && fallback.chain_id && !url.searchParams.get("chain_id")) {
      url.searchParams.set("chain_id", String(fallback.chain_id));
    }
    ["market_id", "chain_id", "workflow", "execution_mode", "scope"].forEach(function (key) {
      if (current.get(key) && !url.searchParams.get(key)) {
        url.searchParams.set(key, current.get(key));
      }
    });
    const currentSelection = window.__FLASHLOAN_MARKET_SCOPE__ || {};
    const currentIsCow = isCowExecutionRail(currentSelection) || current.get("market_id") === "cow-dex-arbitrage";
    if (currentIsCow && current.get("cow_networks") && !url.searchParams.get("cow_networks")) {
      url.searchParams.set("cow_networks", current.get("cow_networks"));
    }
    if (currentIsCow && current.get("cow_network") && !url.searchParams.get("cow_network")) {
      url.searchParams.set("cow_network", current.get("cow_network"));
    }
    if (!currentIsCow) {
      url.searchParams.delete("cow_networks");
      url.searchParams.delete("cow_network");
      url.searchParams.delete("scope");
    }
    return url.pathname + (url.search ? url.search : "");
  }

  function preserveSelectionLinks(selection) {
    const selectedQuery = queryFromSelection(selection);
    document.querySelectorAll("a[href^='/']").forEach(function (link) {
      const href = link.getAttribute("href") || "";
      if (href.startsWith("/api/") || href.startsWith("/static/") || href === "#") return;
      const url = new URL(href, window.location.origin);
      const targetFeature = pathFeature(url.pathname);
      if (targetFeature && selection && selection.feature && targetFeature !== selection.feature) {
        clearMarketScope(url);
        link.setAttribute("href", url.pathname + (url.search ? url.search : ""));
        return;
      }
      selectedQuery.forEach(function (value, key) {
        url.searchParams.set(key, value);
      });
      link.setAttribute("href", url.pathname + (url.search ? url.search : ""));
    });
  }

  async function init(options) {
    const config = options || {};
    const feature = config.feature || document.body.dataset.chainFeature || "";
    let mount = config.mount || document.querySelector("[data-chain-selector]");
    if (!mount) mount = document.getElementById("liquidationMarketSelect");
    if (!mount) return null;
    const query = currentQuery();
    const requestQuery = new URLSearchParams();
    requestQuery.set("page_path", window.location.pathname);
    if (feature) requestQuery.set("feature", feature);
    if (config.defaultMarketId) requestQuery.set("default_market_id", config.defaultMarketId);
    if (query.get("market_id")) requestQuery.set("market_id", query.get("market_id"));
    if (query.get("chain_id")) requestQuery.set("chain_id", query.get("chain_id"));
    if (query.get("workflow")) requestQuery.set("workflow", query.get("workflow"));
    if (query.get("execution_mode")) requestQuery.set("execution_mode", query.get("execution_mode"));
    if (query.get("scope")) requestQuery.set("scope", query.get("scope"));
    const response = await fetch("/api/chain-selection?" + requestQuery.toString(), { cache: "no-cache" });
    const payload = await response.json();
    if (!response.ok || !payload.ok) throw new Error(payload.error || ("HTTP " + response.status));

    let select = mount.matches && mount.matches("select") ? mount : mount.querySelector("select");
    if (!select) {
      select = document.createElement("select");
      select.setAttribute("aria-label", "Select chain and market");
      mount.appendChild(select);
    }
    select.innerHTML = (payload.available || []).map(function (item) {
      const value = item.market_id + ":" + (item.chain_id === null ? "" : item.chain_id);
      const label = [item.display_name || item.market_id, item.network, item.chain_id === null ? "" : "chainId " + item.chain_id]
        .filter(Boolean).join(" / ");
      return '<option value="' + value.replace(/"/g, "&quot;") + '">' + label.replace(/[&<>"]/g, function (char) {
        return ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[char];
      }) + "</option>";
    }).join("");
    const selected = payload.selected || {};
    select.value = selected.market_id + ":" + (selected.chain_id === null ? "" : selected.chain_id);
    window.__FLASHLOAN_MARKET_SCOPE__ = selected;
    const meta = mount.querySelector ? mount.querySelector("[data-chain-selection-meta]") : null;
    if (meta) {
      const scopeLabel = feature === "dex_arbitrage" ? "DEX 链路" : feature === "liquidation" ? "BOT 市场" : "作用域";
      meta.textContent = [
        (payload.available || []).length + " 个 " + scopeLabel,
        selected.protocol,
        selected.observation_only ? "observation-only" : "execution-capable"
      ]
        .filter(Boolean).join(" | ");
    }
    select.onchange = function () {
      const option = select.options[select.selectedIndex];
      const item = (payload.available || []).find(function (candidate) {
        return candidate.market_id + ":" + (candidate.chain_id === null ? "" : candidate.chain_id) === option.value;
      });
      if (!item) return;
      const next = new URL(window.location.href);
      const nextQuery = queryFromSelection(item);
      nextQuery.forEach(function (value, key) { next.searchParams.set(key, value); });
      next.searchParams.delete("feature");
      if (item.feature === "dex_arbitrage") {
        next.searchParams.delete("cow_networks");
        next.searchParams.delete("cow_network");
        next.searchParams.delete("scope");
        next.searchParams.set("execution_mode", isCowNetworkSelection(item) ? "cow" : "direct");
        if (isCowNetworkSelection(item)) next.searchParams.set("scope", "single_chain");
      }
      invalidateScopeCaches(window.__FLASHLOAN_MARKET_SCOPE__, item);
      window.location.assign(next.pathname + (next.search ? next.search : ""));
    };
    preserveSelectionLinks(selected);
    if (typeof config.onReady === "function") config.onReady(payload);
    window.dispatchEvent(new CustomEvent("chainselectionready", {detail: payload}));
    return payload;
  }

  window.ChainSelector = {
    init: init,
    query: queryFromSelection,
    currentQuery: currentQuery,
    withSelection: withSelection,
    invalidateScopeCaches: invalidateScopeCaches,
  };
})();
