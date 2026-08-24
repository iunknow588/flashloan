(function () {
  "use strict";

  const PROTOCOL_EVENT = "chainselectionprotocolschanged";
  const COW_NETWORK_EVENT = "chainselectioncownetworkschanged";
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
    if (selection.chain_id !== null && selection.chain_id !== undefined) {
      return "chain:" + String(selection.chain_id);
    }
    if (selection.market_id) return "market:" + String(selection.market_id);
    return "";
  }

  function availableKey(available) {
    return (available || []).map(selectionKey).join("|");
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

  function isFlashLoanPoolPath(pathname) {
    const path = String(pathname || "/").split("?", 1)[0].replace(/\/$/, "") || "/";
    return path === "/flash-loan" || path === "/flash-loan/pools";
  }

  function clearMarketScope(url) {
    ["market_id", "chain_id", "feature", "cow_network", "cow_networks", "protocols"].forEach(function (key) {
      url.searchParams.delete(key);
    });
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
    } else {
      const protocols = Array.isArray(selection && selection.selected_protocols)
        ? selection.selected_protocols
        : parseProtocolList(selection && selection.protocols);
      if (protocols.length) {
        query.set("protocols", protocolSignature(protocols));
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
    if (isFlashLoanPoolPath(next.pathname)) {
      // Flash loan pool protocol navigation lives in the left sidebar.
      // Chain changes must preserve that sidebar-selected protocol.
    } else if (isCowExecutionRail(selection)) {
      next.searchParams.delete("protocols");
    } else {
      const protocols = Array.isArray(selection && selection.selected_protocols)
        ? selection.selected_protocols
        : parseProtocolList(selection && selection.protocols);
      if (protocols.length) {
        next.searchParams.set("protocols", protocolSignature(protocols));
      } else {
        next.searchParams.delete("protocols");
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
    return next;
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
    const current = currentQuery();
    const fallback = window.__CHAIN_SELECTOR_DEFAULT__ || {};
    const targetFeature = pathFeature(url.pathname);
    const currentFeatureValue = pathFeature(window.location.pathname);
    if (targetFeature && currentFeatureValue && targetFeature !== currentFeatureValue) {
      clearMarketScope(url);
      return url.pathname + (url.search ? url.search : "");
    }
    if (!current.get("chain_id") && !fallback.chain_id && fallback.market_id && !url.searchParams.get("market_id")) {
      url.searchParams.set("market_id", String(fallback.market_id));
    }
    if (!current.get("chain_id") && fallback.chain_id && !url.searchParams.get("chain_id")) {
      url.searchParams.set("chain_id", String(fallback.chain_id));
    }
    ["chain_id", "workflow", "execution_mode", "scope", "protocols"].forEach(function (key) {
      if (current.get(key) && !url.searchParams.get(key)) {
        url.searchParams.set(key, current.get(key));
      }
    });
    const currentSelection = currentScope();
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
      if (link.hasAttribute("data-flash-loan-protocol-link")) return;
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

  function renderSelectionMeta(mount, selected) {
    selected = selected || {};
    const meta = mount && mount.querySelector ? mount.querySelector("[data-chain-selection-meta]") : null;
    if (!meta) return;
    const selectedList = unique(selected && selected.selected_protocols);
    const cowNetworkList = orderedCowNetworks(selected && selected.selected_cow_networks);
    meta.textContent = [
      selected.display_name || selected.market_id || "Current route",
      cowNetworkList.length ? "Networks " + cowNetworkList.join(", ") : (selected.protocol_summary || selected.protocol),
      !cowNetworkList.length && selectedList.length ? "Protocols " + selectedList.join(", ") : "",
      selected.observation_only ? "Observation only" : "Execution capable",
    ].filter(Boolean).join(" | ");
    return;
    meta.textContent = [
      selected.display_name || selected.market_id || "当前链路",
      selected.protocol_summary || selected.protocol,
      selectedList.length ? "协议 " + selectedList.join(", ") : "",
      selected.observation_only ? "仅观察" : "可执行",
    ].filter(Boolean).join(" | ");
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
      mount.setAttribute("data-chain-protocols-empty", "true");
      return [];
    }
    mount.hidden = false;
    mount.removeAttribute("data-chain-protocols-empty");
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
        if (selectorMount) renderSelectionMeta(selectorMount, currentScope());
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
      mount.setAttribute("data-chain-protocols-empty", "true");
      return [];
    }
    mount.hidden = false;
    mount.removeAttribute("data-chain-protocols-empty");
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
        if (selectorMount) renderSelectionMeta(selectorMount, currentScope());
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
    } else {
      url.searchParams.delete("protocols");
    }
    if (options.updateUrl !== false && window.history && typeof window.history.replaceState === "function") {
      window.history.replaceState({}, "", url.pathname + (url.search ? url.search : ""));
    }
    window.__FLASHLOAN_MARKET_SCOPE__ = nextScope;
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
    const query = currentQuery();
    const requestQuery = new URLSearchParams();
    requestQuery.set("page_path", window.location.pathname);
    if (feature) requestQuery.set("feature", feature);
    if (config.defaultMarketId) requestQuery.set("default_market_id", config.defaultMarketId);
    if (query.get("market_id")) requestQuery.set("market_id", query.get("market_id"));
    if (query.get("chain_id")) requestQuery.set("chain_id", query.get("chain_id"));
    if (query.get("protocols")) requestQuery.set("protocols", query.get("protocols"));
    const response = await fetch("/api/chain-selection?" + requestQuery.toString(), { cache: "no-cache" });
    const payload = await response.json();
    if (!response.ok || !payload.ok) throw new Error(payload.error || ("HTTP " + response.status));
    const chainOptions = (payload.chain_options && payload.chain_options.length ? payload.chain_options : payload.available) || [];

    let select = mount.matches && mount.matches("select") ? mount : mount.querySelector("select");
    if (!select) {
      select = document.createElement("select");
      select.setAttribute("aria-label", "选择链和市场");
      mount.appendChild(select);
    }
    const protocolMount = mount.querySelector("[data-chain-protocols]") || (() => {
      const node = document.createElement("div");
      node.setAttribute("data-chain-protocols", "true");
      mount.appendChild(node);
      return node;
    })();

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
    let protocolList = [];
    if (isFlashLoanPoolPath(window.location.pathname)) {
      protocolMount.hidden = true;
      protocolMount.setAttribute("data-chain-protocols-flashloan-disabled", "true");
    } else if (isCowExecutionRail(scope)) {
      const cowConfig = await fetchCowNetworkConfig();
      const cowNetworks = selectedCowNetworks(cowConfig);
      scope = {
        ...scope,
        cow_network_options: cowNetworkOptions(cowConfig),
        selected_cow_networks: cowNetworks,
      };
      window.__FLASHLOAN_MARKET_SCOPE__ = scope;
      protocolList = renderCowNetworkBadgeList(protocolMount, cowConfig, cowNetworks);
    } else {
      const protocolOptionsList = scope.protocol_options || payload.protocol_options || [];
      const currentProtocols = scope.selected_protocols && scope.selected_protocols.length
        ? scope.selected_protocols
        : protocolOptionsList.map(function (item) { return item && item.protocol; }).filter(Boolean);
      protocolList = renderProtocolBadgeList(
        protocolMount,
        protocolOptionsList,
        currentProtocols
      );
    }

    renderSelectionMeta(mount, scope);

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
      preserveSelectionLinks(nextSelection);
      window.location.assign(target);
    };

    if (protocolMount && !protocolList.length) protocolMount.hidden = true;

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
})();
