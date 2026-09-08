(function () {
  "use strict";

  const PROTOCOL_EVENT = "chainselectionprotocolschanged";
  const COW_NETWORK_EVENT = "chainselectioncownetworkschanged";
  const SHELL_SELECTION_STORAGE_KEY = "flashloan:app-shell-selection";
  const DEFAULT_COW_NETWORKS = ["bnb"];
  const COW_NETWORK_ORDER = [
    "bnb",
    "avalanche",
    "polygon",
    "ethereum",
    "gnosis",
    "arbitrum_one",
    "base",
    "linea",
    "plasma",
    "ink",
  ];

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

  function normalizeProtocol(value) {
    return String(value || "").trim().toLowerCase();
  }

  function unique(values) {
    const seen = new Set();
    const items = [];
    for (const value of values || []) {
      const item = normalizeProtocol(value);
      if (!item || seen.has(item)) continue;
      seen.add(item);
      items.push(item);
    }
    return items;
  }

  function parseProtocolList(value) {
    return unique(String(value || "").replace(/\|/g, ",").split(","));
  }

  function protocolSignature(protocols) {
    return unique(protocols).sort().join(",");
  }

  function orderedCowNetworks(networks) {
    return unique(networks).sort(function (left, right) {
      const leftIndex = COW_NETWORK_ORDER.indexOf(left);
      const rightIndex = COW_NETWORK_ORDER.indexOf(right);
      const leftRank = leftIndex >= 0 ? leftIndex : COW_NETWORK_ORDER.length;
      const rightRank = rightIndex >= 0 ? rightIndex : COW_NETWORK_ORDER.length;
      if (leftRank !== rightRank) return leftRank - rightRank;
      return left.localeCompare(right);
    });
  }

  function currentQuery() {
    return new URLSearchParams(window.location.search);
  }

  function currentScope() {
    return window.__FLASHLOAN_MARKET_SCOPE__ || {};
  }

  function readStoredShellSelection(feature) {
    try {
      const key = shellSelectionStorageKey(feature);
      const raw = window.sessionStorage.getItem(key) ||
        (key === SHELL_SELECTION_STORAGE_KEY ? "" : window.sessionStorage.getItem(SHELL_SELECTION_STORAGE_KEY));
      const stored = raw ? JSON.parse(raw) : null;
      return stored && typeof stored === "object" ? stored : null;
    } catch (_) {
      return null;
    }
  }

  function writeStoredShellSelection(selection, feature) {
    if (!selection || typeof selection !== "object") return;
    const key = shellSelectionStorageKey(feature || selection.feature);
    const snapshot = {
      chain_key: selection.chain_key || selection.global_chain_id || "",
      chain_id: selection.chain_id === null || selection.chain_id === undefined ? null : selection.chain_id,
      market_id: selection.market_id || "",
      global_chain_id: selection.global_chain_id || selection.chain_key || "",
      feature: selection.feature || "",
      display_name: selection.display_name || "",
      network: selection.network || "",
      protocol: selection.protocol || "",
      protocol_summary: selection.protocol_summary || "",
      observation_only: Boolean(selection.observation_only),
      enabled: selection.enabled !== false,
      protocol_supported: selection.protocol_supported !== false,
      capabilities: selection.capabilities && typeof selection.capabilities === "object"
        ? selection.capabilities
        : {},
      protocol_options: Array.isArray(selection.protocol_options)
        ? selection.protocol_options
        : [],
      selected_protocols: unique(selection.selected_protocols || []),
      selected_cow_networks: orderedCowNetworks(selection.selected_cow_networks || []),
    };
    try {
      window.sessionStorage.setItem(key, JSON.stringify(snapshot));
      if (key !== SHELL_SELECTION_STORAGE_KEY) {
        window.sessionStorage.removeItem(SHELL_SELECTION_STORAGE_KEY);
      }
    } catch (_) {
      // Storage can be unavailable in private or embedded browser contexts.
    }
  }

  function hasExplicitScope(query) {
    return Boolean(
      query.get("market_id") ||
      query.get("chain_id") ||
      query.get("protocol_id") ||
      query.get("protocols") ||
      query.get("cow_network") ||
      query.get("cow_networks")
    );
  }

  function applyStoredShellScope(query, stored, targetFeature) {
    if (!stored || hasExplicitScope(query)) return query;
    const storedFeature = String(stored.feature || "").trim().toLowerCase();
    const requestedFeature = String(targetFeature || "").trim().toLowerCase();
    if (stored.chain_id !== null && stored.chain_id !== undefined && String(stored.chain_id)) {
      query.set("chain_id", String(stored.chain_id));
    } else if (stored.market_id && (!requestedFeature || storedFeature === requestedFeature)) {
      query.set("market_id", String(stored.market_id));
    }
    if (requestedFeature && storedFeature && requestedFeature !== storedFeature) return query;
    if (stored.selected_protocols && stored.selected_protocols.length) {
      query.set("protocols", protocolSignature(stored.selected_protocols));
    }
    if (stored.selected_cow_networks && stored.selected_cow_networks.length) {
      query.set("cow_networks", orderedCowNetworks(stored.selected_cow_networks).join(","));
    }
    return query;
  }

  function protocolOptions() {
    const scope = currentScope();
    return Array.isArray(scope.protocol_options) ? scope.protocol_options : [];
  }

  function selectedProtocols() {
    const scope = currentScope();
    const scoped = Array.isArray(scope.selected_protocols) ? unique(scope.selected_protocols) : [];
    if (scoped.length) return scoped;
    const requested = parseProtocolList(currentQuery().get("protocols"));
    if (requested.length) return requested;
    const available = protocolOptions().map(function (item) {
      return normalizeProtocol(item && item.protocol);
    }).filter(Boolean);
    return unique(available);
  }

  function selectionKey(selection) {
    if (!selection) return "";
    if (selection.chain_key) return String(selection.chain_key);
    if (selection.global_chain_id) return String(selection.global_chain_id);
    if (selection.chain_id !== null && selection.chain_id !== undefined) {
      return "chain:" + String(selection.chain_id);
    }
    if (selection.market_id) return "market:" + String(selection.market_id);
    return "";
  }

  function availableKey(available) {
    return (available || []).map(selectionKey).join("|");
  }

  function isCowExecutionRail(selection) {
    const marketId = String(selection && selection.market_id || "").toLowerCase();
    return !!selection && (
      String(selection.market_type || "").toLowerCase() === "execution_rail" ||
      marketId === "dex-arbitrage-0-cow-protocol" ||
      marketId === "cow-dex-arbitrage"
    );
  }

  function pathFeature(pathname) {
    const path = String(pathname || "/").split("?", 1)[0].replace(/\/$/, "") || "/";
    if (path === "/" || path === "/liquidation" || path === "/account-scan" || path === "/execution" || path === "/audit" || path.startsWith("/bot")) {
      return "liquidation";
    }
    if (path === "/binance-market" || path === "/dex-arbitrage" || path === "/dex-pools" || path === "/candidate-pools" || path === "/market-observation" || path === "/flash-loan" || path.startsWith("/flash-loan") || path.startsWith("/dex") || path.startsWith("/robinhood-dex")) {
      return "dex_arbitrage";
    }
    return "";
  }

  function isFlashLoanPoolPath(pathname) {
    const path = String(pathname || "/").split("?", 1)[0].replace(/\/$/, "") || "/";
    return path === "/flash-loan" || path === "/flash-loan/pools";
  }

  function clearMarketScope(url) {
    ["market_id", "feature", "protocol_id", "protocols", "cow_network", "cow_networks", "scope", "execution_mode"].forEach(function (key) {
      url.searchParams.delete(key);
    });
  }

  function applyChainOnlyToUrl(url, selection) {
    const next = url;
    next.searchParams.delete("market_id");
    next.searchParams.delete("protocols");
    next.searchParams.delete("protocol_id");
    next.searchParams.delete("cow_network");
    next.searchParams.delete("cow_networks");
    next.searchParams.delete("scope");
    next.searchParams.delete("execution_mode");
    next.searchParams.delete("feature");
    if (selection && selection.chain_id !== null && selection.chain_id !== undefined) {
      next.searchParams.set("chain_id", String(selection.chain_id));
    }
    return next;
  }

  function currentFeature(pathname) {
    return pathFeature(pathname);
  }

  function rootPathForFeature(feature) {
    const normalized = String(feature || "").toLowerCase();
    if (normalized === "dex_arbitrage") return "/dex";
    if (normalized === "liquidation") return "/bot";
    return window.location.pathname;
  }

  function queryFromSelection(selection) {
    const query = new URLSearchParams();
    if (selection && selection.chain_id !== null && selection.chain_id !== undefined) {
      query.set("chain_id", String(selection.chain_id));
    } else if (selection && selection.market_id) {
      query.set("market_id", selection.market_id);
    }
    if (isCowExecutionRail(selection)) {
      const cowNetworks = orderedCowNetworks(selection && selection.selected_cow_networks);
      if (cowNetworks.length) {
        query.set("cow_networks", cowNetworks.join(","));
        query.set("execution_mode", "cow");
        query.set("scope", cowNetworks.length > 1 ? "multi_chain" : "single_chain");
      }
      query.delete("protocols");
      query.delete("protocol_id");
    } else {
      const protocols = Array.isArray(selection && selection.selected_protocols)
        ? selection.selected_protocols
        : parseProtocolList(selection && selection.protocols);
      if (protocols.length) {
        query.set("protocols", protocolSignature(protocols));
        query.delete("protocol_id");
      }
    }
    return query;
  }

  function applySelectionToUrl(url, selection) {
    const next = url;
    const targetFeature = String(selection && selection.feature || "").toLowerCase();
    if (selection && selection.chain_id !== null && selection.chain_id !== undefined) {
      next.searchParams.delete("market_id");
      next.searchParams.set("chain_id", String(selection.chain_id));
    } else {
      next.searchParams.delete("chain_id");
      if (selection && selection.market_id) {
        next.searchParams.set("market_id", String(selection.market_id));
      } else {
        next.searchParams.delete("market_id");
      }
    }
    next.searchParams.delete("feature");
    next.searchParams.delete("protocol_id");
    if (isFlashLoanPoolPath(next.pathname)) {
      // Flash loan pool protocol navigation lives in the left sidebar.
      // Chain changes must preserve that sidebar-selected protocol.
    } else if (isCowExecutionRail(selection)) {
      next.searchParams.delete("protocols");
      next.searchParams.delete("protocol_id");
    } else {
      const protocols = Array.isArray(selection && selection.selected_protocols)
        ? selection.selected_protocols
        : parseProtocolList(selection && selection.protocols);
      if (protocols.length) {
        next.searchParams.set("protocols", protocolSignature(protocols));
        next.searchParams.delete("protocol_id");
      } else {
        next.searchParams.delete("protocols");
        next.searchParams.delete("protocol_id");
      }
    }
    if (targetFeature === "dex_arbitrage") {
      next.searchParams.delete("cow_networks");
      next.searchParams.delete("cow_network");
      next.searchParams.delete("scope");
      next.searchParams.set("execution_mode", isCowExecutionRail(selection) ? "cow" : "direct");
      if (isCowExecutionRail(selection)) {
        const cowNetworks = selectedCowNetworks();
        next.searchParams.set("scope", cowNetworks.length > 1 ? "multi_chain" : "single_chain");
        next.searchParams.set("cow_networks", cowNetworks.join(","));
      }
    } else {
      next.searchParams.delete("cow_networks");
      next.searchParams.delete("cow_network");
      next.searchParams.delete("scope");
      next.searchParams.delete("execution_mode");
    }
    const currentStrategy = currentQuery().get("dex_strategy");
    if (currentStrategy) {
      next.searchParams.set("dex_strategy", currentStrategy);
    }
    return next;
  }

  function clearScopeStorage(storage, feature) {
    if (!storage) return;
    const featureKey = shellSelectionStorageKey(feature);
    for (let index = storage.length - 1; index >= 0; index -= 1) {
      const key = storage.key(index);
      if (key && (key === SHELL_SELECTION_STORAGE_KEY || key === featureKey || (!feature && key.startsWith("flashloan:")))) {
        storage.removeItem(key);
      }
    }
  }

  function invalidateScopeCaches(previous, next) {
    if (selectionKey(previous) === selectionKey(next)) return false;
    const feature = shellStorageFeature(
      (next && next.feature) ||
      (previous && previous.feature) ||
      window.__FLASHLOAN_MARKET_SCOPE__ && window.__FLASHLOAN_MARKET_SCOPE__.feature
    );
    try {
      clearScopeStorage(window.sessionStorage, feature);
      clearScopeStorage(window.localStorage, feature);
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

  function applySelectionToCurrentUrl(selection) {
    const currentFeatureValue = currentFeature(window.location.pathname);
    const targetFeature = String(selection && selection.feature || "").toLowerCase();
    if (targetFeature && currentFeatureValue && targetFeature !== currentFeatureValue) {
      const target = new URL(rootPathForFeature(targetFeature), window.location.origin);
      return applySelectionToUrl(target, selection);
    }
    return applySelectionToUrl(new URL(window.location.href), selection);
  }

  function withSelection(path) {
    const url = new URL(path, window.location.origin);
    const targetFeature = pathFeature(url.pathname);
    const current = applyStoredShellScope(currentQuery(), readStoredShellSelection(targetFeature), targetFeature);
    const fallback = window.__CHAIN_SELECTOR_DEFAULT__ || {};
    const currentFeatureValue = pathFeature(window.location.pathname);
    if (targetFeature && currentFeatureValue && targetFeature !== currentFeatureValue) {
      const chainId = current.get("chain_id") || fallback.chain_id;
      clearMarketScope(url);
      if (chainId && !url.searchParams.get("chain_id")) {
        url.searchParams.set("chain_id", String(chainId));
      }
      return url.pathname + (url.search ? url.search : "");
    }
    if (!current.get("chain_id") && !fallback.chain_id && fallback.market_id && !url.searchParams.get("market_id")) {
      url.searchParams.set("market_id", String(fallback.market_id));
    }
    if (!current.get("chain_id") && fallback.chain_id && !url.searchParams.get("chain_id")) {
      url.searchParams.set("chain_id", String(fallback.chain_id));
    }
    ["chain_id", "workflow", "execution_mode", "scope", "protocols", "protocol_id"].forEach(function (key) {
      if (current.get(key) && !url.searchParams.get(key)) {
        url.searchParams.set(key, current.get(key));
      }
    });
    if (current.get("dex_strategy") && !url.searchParams.get("dex_strategy")) {
      url.searchParams.set("dex_strategy", current.get("dex_strategy"));
    }
    const currentSelection = currentScope();
    const currentMarketId = String(current.get("market_id") || "").toLowerCase();
    const currentIsCow = isCowExecutionRail(currentSelection) || currentMarketId === "dex-arbitrage-0-cow-protocol" || currentMarketId === "cow-dex-arbitrage";
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
    const currentStrategy = currentQuery().get("dex_strategy");
    if (currentStrategy) {
      selectedQuery.set("dex_strategy", currentStrategy);
    }
    document.querySelectorAll("a[href^='/']").forEach(function (link) {
      const href = link.getAttribute("href") || "";
      if (href.startsWith("/api/") || href.startsWith("/static/") || href === "#") return;
      if (link.hasAttribute("data-flash-loan-protocol-link")) return;
      const url = new URL(href, window.location.origin);
      const targetFeature = pathFeature(url.pathname);
      if (!targetFeature || !selection || !selection.feature) {
        return;
      }
      if (targetFeature !== String(selection.feature || "").toLowerCase()) {
        applyChainOnlyToUrl(url, selection);
        link.setAttribute("href", url.pathname + (url.search ? url.search : ""));
        return;
      }
      selectedQuery.forEach(function (value, key) {
        url.searchParams.set(key, value);
      });
      link.setAttribute("href", url.pathname + (url.search ? url.search : ""));
    });
  }

  function renderSelectOptions(select, options) {
    select.innerHTML = "";
    (options || []).forEach(function (item) {
      const option = document.createElement("option");
      const value = selectionKey(item);
      const label = [
        item.display_name || item.market_id,
        item.chain_id === null || item.chain_id === undefined ? "" : "ID " + item.chain_id,
      ].filter(Boolean).join(" / ");
      option.value = value;
      option.textContent = label;
      select.appendChild(option);
    });
  }

  function renderSelectionSummary(mount, selected) {
    selected = selected || {};
    const routeMeta = mount && mount.querySelector ? mount.querySelector("[data-chain-selection-meta]") : null;
    const optionsMeta = mount && mount.querySelector ? mount.querySelector("[data-chain-selection-options-meta]") : null;
    if (!routeMeta && !optionsMeta) return;
    const selectedList = unique(selected && selected.selected_protocols);
    const cowNetworkList = orderedCowNetworks(selected && selected.selected_cow_networks);
    const globalChainId = selected.global_chain_id || selected.chain_key || "";
    const routeText = [
      selected.display_name || selected.market_id || "Current route",
      globalChainId ? "Link " + globalChainId : "",
      selected.observation_only ? "Observation only" : "Execution capable",
    ].filter(Boolean).join(" | ");
    if (routeMeta) {
      routeMeta.textContent = routeText;
      routeMeta.title = routeText;
    }
    if (optionsMeta) {
      const protocolText = cowNetworkList.length
        ? "Networks " + cowNetworkList.join(", ")
        : (selectedList.length ? "Protocols " + selectedList.join(", ") : (selected.protocol_summary || selected.protocol || "Protocols"));
      optionsMeta.textContent = protocolText;
      optionsMeta.title = protocolText;
    }
  }

  async function fetchCowNetworkConfig() {
    try {
      const response = await fetch("/api/binance-market/cow-config", { cache: "no-cache" });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || ("HTTP " + response.status));
      return payload;
    } catch (error) {
      console.error("cow network config:", error);
      return {
        default_network: DEFAULT_COW_NETWORKS[0],
        networks: [
          { network: "bnb", label: "BNB", chain_id: 56, testnet: false },
        ],
      };
    }
  }

  function cowNetworkOptions(cowConfig) {
    const networks = Array.isArray(cowConfig && cowConfig.networks) ? cowConfig.networks : [];
    return networks
      .filter(function (item) { return item && item.network && !item.testnet; })
      .sort(function (left, right) {
        const leftNetwork = normalizeProtocol(left && left.network);
        const rightNetwork = normalizeProtocol(right && right.network);
        const leftIndex = COW_NETWORK_ORDER.indexOf(leftNetwork);
        const rightIndex = COW_NETWORK_ORDER.indexOf(rightNetwork);
        const leftRank = leftIndex >= 0 ? leftIndex : COW_NETWORK_ORDER.length;
        const rightRank = rightIndex >= 0 ? rightIndex : COW_NETWORK_ORDER.length;
        if (leftRank !== rightRank) return leftRank - rightRank;
        return leftNetwork.localeCompare(rightNetwork);
      });
  }

  function selectedCowNetworks(cowConfig) {
    const query = currentQuery();
    const requested = orderedCowNetworks(parseProtocolList(query.get("cow_networks") || query.get("cow_network")));
    if (requested.length) return requested;
    const scope = currentScope();
    const scoped = orderedCowNetworks(scope.selected_cow_networks || []);
    if (scoped.length) return scoped;
    const defaultNetwork = normalizeProtocol(cowConfig && cowConfig.default_network);
    return orderedCowNetworks(defaultNetwork ? [defaultNetwork] : DEFAULT_COW_NETWORKS);
  }

  function syncCowNetworkInputs(networks) {
    const selected = new Set(orderedCowNetworks(networks));
    document.querySelectorAll("input[name='cow_network']").forEach(function (input) {
      input.checked = selected.has(normalizeProtocol(input.value));
    });
  }

  function setSelectedCowNetworks(networks, options = {}) {
    const cowConfig = options.cowConfig || {};
    const allowed = new Set(cowNetworkOptions(cowConfig).map(function (item) {
      return normalizeProtocol(item && item.network);
    }).filter(Boolean));
    let normalized = orderedCowNetworks(networks);
    if (allowed.size) {
      normalized = normalized.filter(function (item) { return allowed.has(item); });
    }
    if (!normalized.length) {
      normalized = selectedCowNetworks(cowConfig);
    }
    const previous = currentScope();
    const nextScope = {
      ...previous,
      cow_network_options: cowNetworkOptions(cowConfig),
      selected_cow_networks: normalized,
    };
    const url = new URL(window.location.href);
    url.searchParams.set("workflow", "dex_arbitrage");
    url.searchParams.set("execution_mode", "cow");
    url.searchParams.set("scope", normalized.length > 1 ? "multi_chain" : "single_chain");
    url.searchParams.set("cow_networks", normalized.join(","));
    url.searchParams.delete("cow_network");
    url.searchParams.delete("protocols");
    if (options.updateUrl !== false && window.history && typeof window.history.replaceState === "function") {
      window.history.replaceState({}, "", url.pathname + (url.search ? url.search : ""));
    }
    window.__FLASHLOAN_MARKET_SCOPE__ = nextScope;
    writeStoredShellSelection(nextScope, nextScope.feature);
    syncCowNetworkInputs(normalized);
    const detail = {
      selected: nextScope,
      previous: previous,
      selected_cow_networks: normalized,
      cow_network_only: true,
    };
    window.dispatchEvent(new CustomEvent(COW_NETWORK_EVENT, { detail: detail }));
    window.dispatchEvent(new CustomEvent("chainselectionready", { detail: detail }));
    window.dispatchEvent(new CustomEvent("chainselectionchanged", { detail: detail }));
    preserveSelectionLinks(nextScope);
    return normalized;
  }

  function renderCowNetworkBadgeList(mount, cowConfig, selectedNetworksList) {
    const options = cowNetworkOptions(cowConfig);
    const selected = new Set(orderedCowNetworks(selectedNetworksList));
    mount.innerHTML = "";
    if (!options.length) {
      mount.hidden = false;
      mount.setAttribute("data-chain-selection-empty", "true");
      return [];
    }
    mount.hidden = false;
    mount.removeAttribute("data-chain-selection-empty");
    options.forEach(function (item) {
      const network = normalizeProtocol(item.network);
      const labelText = item.label || item.network;
      const ariaLabel = [
        item.label || item.network,
        item.chain_id === null || item.chain_id === undefined ? "" : "chain " + String(item.chain_id),
      ].filter(Boolean).join(" ");
      const chip = document.createElement("label");
      chip.className = "protocol-chip cow-network-chip" + (selected.has(network) ? " active" : "");
      const checkbox = document.createElement("input");
      checkbox.type = "checkbox";
      checkbox.value = network;
      checkbox.checked = selected.has(network);
      checkbox.setAttribute("data-cow-network-checkbox", "true");
      checkbox.setAttribute("aria-label", ariaLabel);
      checkbox.onchange = function () {
        const checked = Array.from(mount.querySelectorAll("[data-cow-network-checkbox]:checked"))
          .map(function (node) { return node.value; });
        const next = setSelectedCowNetworks(checked, { cowConfig: cowConfig });
        renderCowNetworkBadgeList(mount, cowConfig, next);
        const selectorMount = mount.closest ? mount.closest("[data-chain-selector]") : null;
        if (selectorMount) renderSelectionSummary(selectorMount, currentScope());
      };
      chip.appendChild(checkbox);
      chip.appendChild(document.createTextNode(labelText));
      mount.appendChild(chip);
    });
    return options.map(function (item) { return normalizeProtocol(item.network); });
  }

  function renderProtocolBadgeList(mount, protocolOptionsList, selectedProtocolsList) {
    const normalizedOptions = (protocolOptionsList || []).map(function (item) {
      return {
        protocol: normalizeProtocol(item && item.protocol),
        display_name: item && item.display_name ? String(item.display_name) : normalizeProtocol(item && item.protocol),
      };
    }).filter(function (item) { return item.protocol; });
    const selected = new Set(unique(selectedProtocolsList));
    mount.innerHTML = "";
    if (!normalizedOptions.length) {
      mount.hidden = false;
      mount.setAttribute("data-chain-selection-empty", "true");
      return [];
    }
    mount.hidden = false;
    mount.removeAttribute("data-chain-selection-empty");
    normalizedOptions.forEach(function (item) {
      const chip = document.createElement("label");
      chip.className = "protocol-chip" + (selected.has(item.protocol) ? " active" : "");
      const checkbox = document.createElement("input");
      checkbox.type = "checkbox";
      checkbox.value = item.protocol;
      checkbox.checked = selected.has(item.protocol);
      checkbox.setAttribute("aria-label", item.display_name);
      checkbox.onchange = function () {
        const checked = Array.from(mount.querySelectorAll("input[type='checkbox']:checked"))
          .map(function (node) { return node.value; });
        const next = setSelectedProtocols(checked);
        renderProtocolBadgeList(mount, protocolOptionsList, next);
        const selectorMount = mount.closest ? mount.closest("[data-chain-selector]") : null;
        if (selectorMount) renderSelectionSummary(selectorMount, currentScope());
      };
      chip.appendChild(checkbox);
      chip.appendChild(document.createTextNode(item.display_name));
      mount.appendChild(chip);
    });
    return normalizedOptions.map(function (item) { return item.protocol; });
  }

  function syncScopeFromSelection(selected, payload) {
    const protocolOptionsList = Array.isArray(payload && payload.protocol_options)
      ? payload.protocol_options
      : Array.isArray(selected && selected.protocol_options)
        ? selected.protocol_options
        : [];
    const selectedList = Array.isArray(payload && payload.selected_protocols)
      ? payload.selected_protocols
      : Array.isArray(selected && selected.selected_protocols)
        ? selected.selected_protocols
        : [];
    const availableProtocols = unique(protocolOptionsList.map(function (item) {
      return item && item.protocol;
    }));
    const normalizedProtocols = unique(selectedList.length ? selectedList : availableProtocols);
    window.__FLASHLOAN_MARKET_SCOPE__ = {
      ...selected,
      protocol_options: protocolOptionsList,
      selected_protocols: normalizedProtocols,
    };
    return window.__FLASHLOAN_MARKET_SCOPE__;
  }

  function setSelectedProtocols(protocols, options = {}) {
    const scope = currentScope();
    const protocolOptionsList = protocolOptions();
    const allowed = new Set(protocolOptionsList.map(function (item) {
      return normalizeProtocol(item && item.protocol);
    }).filter(Boolean));
    let normalized = unique(protocols);
    if (allowed.size) {
      normalized = normalized.filter(function (item) { return allowed.has(item); });
    }
    if (!normalized.length) {
      normalized = unique(protocolOptionsList.map(function (item) {
        return item && item.protocol;
      })).filter(Boolean);
    }
    const previous = unique(scope.selected_protocols || []);
    const nextScope = {
      ...scope,
      selected_protocols: normalized,
    };
    const url = new URL(window.location.href);
    if (normalized.length) {
      url.searchParams.set("protocols", protocolSignature(normalized));
      url.searchParams.delete("protocol_id");
    } else {
      url.searchParams.delete("protocols");
      url.searchParams.delete("protocol_id");
    }
    if (options.updateUrl !== false && window.history && typeof window.history.replaceState === "function") {
      window.history.replaceState({}, "", url.pathname + (url.search ? url.search : ""));
    }
    window.__FLASHLOAN_MARKET_SCOPE__ = nextScope;
    writeStoredShellSelection(nextScope, nextScope.feature);
    const detail = {
      selected: nextScope,
      previous: scope,
      selected_protocols: normalized,
      previous_protocols: previous,
      protocol_only: true,
    };
    window.dispatchEvent(new CustomEvent(PROTOCOL_EVENT, { detail: detail }));
    window.dispatchEvent(new CustomEvent("chainselectionready", { detail: detail }));
    preserveSelectionLinks(nextScope);
    return normalized;
  }

  function matchesProtocols(value, selection) {
    const requested = Array.isArray(selection) && selection.length
      ? unique(selection)
      : selectedProtocols();
    if (!requested.length) return true;
    const protocol = normalizeProtocol(value);
    if (!protocol) return true;
    return requested.includes(protocol);
  }

  async function init(options) {
    const config = options || {};
    const feature = Object.prototype.hasOwnProperty.call(config, "feature")
      ? String(config.feature || "").trim().toLowerCase()
      : String(document.body.dataset.chainFeature || "").trim().toLowerCase();
    const mount = config.mount || document.querySelector("[data-chain-selector]");
    if (!mount) return null;
    const originalQuery = currentQuery();
    const storedShellSelection = readStoredShellSelection(feature);
    const query = applyStoredShellScope(
      new URLSearchParams(originalQuery),
      storedShellSelection,
      feature
    );
    const requestQuery = new URLSearchParams();
    requestQuery.set("page_path", window.location.pathname);
    if (feature) requestQuery.set("feature", feature);
    if (config.defaultMarketId) requestQuery.set("default_market_id", config.defaultMarketId);
    if (config.includeChainConfig === false) requestQuery.set("include_chain_config", "false");
    if (query.get("market_id")) requestQuery.set("market_id", query.get("market_id"));
    if (query.get("chain_id")) requestQuery.set("chain_id", query.get("chain_id"));
    if (query.get("protocol_id")) requestQuery.set("protocol_id", query.get("protocol_id"));
    if (query.get("protocols")) requestQuery.set("protocols", query.get("protocols"));
    const response = await fetch("/api/chain-selection?" + requestQuery.toString(), { cache: "no-cache" });
    const payload = await response.json();
    if (!response.ok || !payload.ok) throw new Error(payload.error || ("HTTP " + response.status));
    const chainOptions = (payload.chain_options && payload.chain_options.length ? payload.chain_options : payload.available) || [];

    let select = mount.querySelector("[data-chain-selector-select]") || mount.querySelector(".app-shell-chain-control select");
    if (!select && mount.matches && mount.matches("select")) {
      select = mount;
    }
    if (!select) {
      select = mount.querySelector("select");
    }
    if (!select) {
      select = document.createElement("select");
      select.setAttribute("data-chain-selector-select", "true");
      select.setAttribute("aria-label", "选择链和市场");
      mount.appendChild(select);
    }
    const selectionMount = mount.querySelector("[data-chain-selection-options]") || (() => {
      const node = document.createElement("div");
      node.setAttribute("data-chain-selection-options", "true");
      mount.appendChild(node);
      return node;
    })();
    selectionMount.hidden = false;

    const selected = payload.selected || {};
    const nextKey = availableKey(chainOptions);
    const selectedKey = selectionKey(selected);
    if (select.dataset.chainSelectorSignature !== nextKey) {
      renderSelectOptions(select, chainOptions);
      select.dataset.chainSelectorSignature = nextKey;
    }
    if (select.dataset.chainSelectorKey !== selectedKey || select.value !== selectedKey) {
      select.value = selectedKey;
    }

    let scope = syncScopeFromSelection(selected, payload);
    const storedMatchesSelection = Boolean(
      storedShellSelection &&
      selectionKey(storedShellSelection) &&
      selectionKey(storedShellSelection) === selectionKey(scope)
    );
    if (!hasExplicitScope(originalQuery) && storedMatchesSelection) {
      scope = {
        ...scope,
        ...storedShellSelection,
        chain_key: storedShellSelection.chain_key || scope.chain_key,
        global_chain_id: storedShellSelection.global_chain_id || scope.global_chain_id,
        chain_id: storedShellSelection.chain_id ?? scope.chain_id,
        market_id: storedShellSelection.market_id || scope.market_id,
        protocol_options: Array.isArray(storedShellSelection.protocol_options) &&
          storedShellSelection.protocol_options.length
          ? storedShellSelection.protocol_options
          : scope.protocol_options,
        selected_protocols: Array.isArray(storedShellSelection.selected_protocols) &&
          storedShellSelection.selected_protocols.length
          ? storedShellSelection.selected_protocols
          : scope.selected_protocols,
      };
      window.__FLASHLOAN_MARKET_SCOPE__ = scope;
    }
    if (hasExplicitScope(originalQuery) || !storedShellSelection) {
      writeStoredShellSelection(scope, scope.feature || feature);
    }
    if (!hasExplicitScope(originalQuery) && storedShellSelection) {
      const restoredUrl = applySelectionToUrl(new URL(window.location.href), scope);
      if (window.history && typeof window.history.replaceState === "function") {
        window.history.replaceState(
          {},
          "",
          restoredUrl.pathname + (restoredUrl.search ? restoredUrl.search : "")
        );
      }
    }
    if (isCowExecutionRail(scope)) {
      const cowConfig = await fetchCowNetworkConfig();
      const cowNetworks = selectedCowNetworks(cowConfig);
      scope = {
        ...scope,
        cow_network_options: cowNetworkOptions(cowConfig),
        selected_cow_networks: cowNetworks,
      };
      window.__FLASHLOAN_MARKET_SCOPE__ = scope;
      renderCowNetworkBadgeList(selectionMount, cowConfig, cowNetworks);
    } else {
      const protocolOptionsList = scope.protocol_options || payload.protocol_options || [];
      const currentProtocols = scope.selected_protocols && scope.selected_protocols.length
        ? scope.selected_protocols
        : protocolOptionsList.map(function (item) { return item && item.protocol; }).filter(Boolean);
      renderProtocolBadgeList(
        selectionMount,
        protocolOptionsList,
        currentProtocols
      );
    }

    renderSelectionSummary(mount, scope);

    select.onchange = function () {
      const option = select.options[select.selectedIndex];
      const item = chainOptions.find(function (candidate) {
        return selectionKey(candidate) === option.value;
      });
      if (!item) return;
      const previous = currentScope();
      if (selectionKey(previous) === selectionKey(item)) return;
      const nextSelection = {
        ...item,
        protocol_options: item.protocol_options || [],
        selected_protocols: Array.isArray(item.protocol_options) && item.protocol_options.length
          ? item.protocol_options.map(function (option) { return option && option.protocol; }).filter(Boolean)
          : (Array.isArray(item.selected_protocols) ? item.selected_protocols : []),
      };
      const next = applySelectionToCurrentUrl(nextSelection);
      const target = next.pathname + (next.search ? next.search : "");
      invalidateScopeCaches(previous, item);
      window.__FLASHLOAN_MARKET_SCOPE__ = nextSelection;
      writeStoredShellSelection(nextSelection, nextSelection.feature);
      preserveSelectionLinks(nextSelection);
      if (window.AppShell && typeof window.AppShell.navigate === "function" && !window.AppShell.isEmbedded()) {
        window.AppShell.navigate(target);
      } else {
        window.location.assign(target);
      }
    };

    preserveSelectionLinks(scope);
    if (select.dataset) {
      select.dataset.chainSelectorKey = selectionKey(selected);
      select.dataset.chainSelectorFeature = feature;
      select.dataset.chainSelectorSelectedKey = selectedKey;
    }
    if (typeof config.onReady === "function") config.onReady(payload);
    window.dispatchEvent(new CustomEvent("chainselectionready", { detail: payload }));
    return payload;
  }

  window.ChainSelector = {
    init: init,
    query: queryFromSelection,
    currentQuery: currentQuery,
    withSelection: withSelection,
    invalidateScopeCaches: invalidateScopeCaches,
    selectedProtocols: selectedProtocols,
    protocolOptions: protocolOptions,
    setSelectedProtocols: setSelectedProtocols,
    selectedCowNetworks: selectedCowNetworks,
    setSelectedCowNetworks: setSelectedCowNetworks,
    matchesProtocols: matchesProtocols,
    protocolSignature: protocolSignature,
  };

  window.addEventListener("appshellnavigate", function (event) {
    const detail = event.detail || {};
    const target = new URL(detail.href || window.location.href, window.location.origin);
    const feature = pathFeature(target.pathname);
    if (!feature) return;
    init({ feature: feature, includeChainConfig: false }).catch(function (error) {
      console.error("chain selector:", error);
    });
  });
})();

