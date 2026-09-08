(function () {
  "use strict";

  const STATE_EVENT = "dexstrategychanged";
  const DEFAULT_BOOTSTRAP = window.__DEX_STRATEGY_DEFAULT__ || {};
  const DEFAULT_STATE = window.__DEX_STRATEGY_STATE__ || {};

  function normalizeStrategy(value) {
    return String(value || "").trim().toLowerCase();
  }

  function currentQuery() {
    return new URLSearchParams(window.location.search);
  }

  function currentStrategyKey() {
    return normalizeStrategy(
      currentQuery().get("dex_strategy") ||
      DEFAULT_BOOTSTRAP.strategyKey ||
      DEFAULT_BOOTSTRAP.currentStrategyKey ||
      DEFAULT_STATE.active_strategy
    );
  }

  function strategyDefinitions() {
    return Array.isArray(DEFAULT_STATE.strategies) ? DEFAULT_STATE.strategies : [];
  }

  function strategyDefinition(key, definitions) {
    const normalized = normalizeStrategy(key);
    const list = Array.isArray(definitions) ? definitions : strategyDefinitions();
    return list.find(function (item) {
      return normalizeStrategy(item && item.key) === normalized;
    }) || null;
  }

  function renderStrategyMeta(mount, definition) {
    const meta = mount && mount.querySelector ? mount.querySelector("[data-dex-strategy-meta]") : null;
    if (!meta) return;
    const nameNode = meta.querySelector("[data-dex-strategy-name]");
    const descriptionNode = meta.querySelector("[data-dex-strategy-description]");
    const displayName = String(definition && definition.display_name || "").trim();
    const description = String(definition && definition.description || "").trim();
    if (nameNode) {
      nameNode.textContent = displayName || "策略信息加载中";
    }
    if (descriptionNode) {
      descriptionNode.textContent = description;
    }
    if (definition && (definition.key || definition.default_path)) {
      meta.title = [
        String(definition.key || "").trim(),
        String(definition.default_path || "").trim(),
      ].filter(Boolean).join(" | ");
    }
  }

  function strategyPathForKey(key, definitions) {
    const definition = strategyDefinition(key, definitions);
    if (definition && definition.default_path) {
      return String(definition.default_path);
    }
    if (normalizeStrategy(key) === normalizeStrategy(DEFAULT_BOOTSTRAP.currentStrategyKey)) {
      return String(DEFAULT_BOOTSTRAP.currentStrategyPath || DEFAULT_BOOTSTRAP.strategyPath || window.location.pathname);
    }
    return String(DEFAULT_BOOTSTRAP.strategyPath || window.location.pathname);
  }

  function selectMount() {
    return document.querySelector("[data-dex-strategy-selector]");
  }

  function ensureSelect(mount) {
    if (!mount) return null;
    if (mount.matches && mount.matches("select")) {
      return mount;
    }
    return mount.querySelector("[data-dex-strategy-select]") || mount.querySelector("select") || (() => {
      const select = document.createElement("select");
      select.setAttribute("data-dex-strategy-select", "true");
      select.setAttribute("aria-label", "选择 DEX 策略");
      mount.appendChild(select);
      return select;
    })();
  }

  function renderOptions(select, definitions, selectedKey) {
    const signature = (definitions || []).map(function (item) {
      return [
        normalizeStrategy(item && item.key),
        String(item && item.display_name || ""),
        String(item && item.default_path || ""),
      ].join(":");
    }).join("|");
    if (select.dataset.dexStrategySignature === signature) {
      select.value = normalizeStrategy(selectedKey);
      return;
    }
    select.innerHTML = "";
    (definitions || []).forEach(function (item) {
      const option = document.createElement("option");
      option.value = normalizeStrategy(item && item.key);
      option.textContent = String(item && item.display_name || item && item.key || "");
      if (item && item.description) {
        option.title = [String(item.description), String(item.default_path || "")].filter(Boolean).join(" | ");
      }
      select.appendChild(option);
    });
    select.dataset.dexStrategySignature = signature;
    select.value = normalizeStrategy(selectedKey);
  }

  function updateUrlForStrategy(strategyKey, definitions) {
    const normalized = normalizeStrategy(strategyKey);
    const definition = strategyDefinition(normalized, definitions);
    const targetPath = definition && definition.default_path ? String(definition.default_path) : strategyPathForKey(normalized, definitions);
    const scoped = window.ChainSelector && typeof window.ChainSelector.withSelection === "function"
      ? window.ChainSelector.withSelection(targetPath)
      : targetPath;
    const url = new URL(scoped, window.location.origin);
    url.searchParams.set("dex_strategy", normalized);
    return url;
  }

  async function persistStrategy(strategyKey) {
    try {
      const response = await fetch("/api/dex/strategy", {
        method: "POST",
        cache: "no-cache",
        headers: {
          "Accept": "application/json",
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ active_strategy: normalizeStrategy(strategyKey) }),
      });
      const payload = await response.json().catch(function () { return {}; });
      if (!response.ok) {
        throw new Error(payload.error || ("请求失败（HTTP " + response.status + "）"));
      }
      window.__DEX_STRATEGY_STATE__ = payload;
      return payload;
    } catch (error) {
      console.error("dex strategy persist:", error);
      return null;
    }
  }

  function applyBootstrapStrategy(selectedKey, definitions) {
    const normalized = normalizeStrategy(selectedKey) || normalizeStrategy(DEFAULT_BOOTSTRAP.currentStrategyKey);
    if (!normalized) return normalized;
    const current = normalizeStrategy(DEFAULT_BOOTSTRAP.currentStrategyKey || currentStrategyKey());
    if (current && normalized !== current) {
      const url = updateUrlForStrategy(normalized, definitions);
      const target = url.pathname + (url.search ? url.search : "");
      if (window.AppShell && typeof window.AppShell.navigate === "function" && !window.AppShell.isEmbedded()) {
        window.AppShell.navigate(target, { replace: true });
      } else {
        window.location.replace(target);
      }
      return normalized;
    }
    const url = new URL(window.location.href);
    if (url.searchParams.get("dex_strategy") !== normalized) {
      url.searchParams.set("dex_strategy", normalized);
      if (window.history && typeof window.history.replaceState === "function") {
        window.history.replaceState({}, "", url.pathname + (url.search ? url.search : ""));
      }
    }
    return normalized;
  }

  async function init(options) {
    const config = options || {};
    const mount = config.mount || selectMount();
    if (!mount) return null;

    const bootstrapKey = normalizeStrategy(
      DEFAULT_BOOTSTRAP.strategyKey ||
      config.selectedStrategyKey ||
      currentStrategyKey()
    );
    const bootstrapCurrentKey = normalizeStrategy(
      DEFAULT_BOOTSTRAP.currentStrategyKey ||
      currentStrategyKey()
    );
    if (bootstrapKey && bootstrapCurrentKey && bootstrapKey !== bootstrapCurrentKey) {
      const immediate = updateUrlForStrategy(bootstrapKey, strategyDefinitions());
      window.location.replace(immediate.pathname + (immediate.search ? immediate.search : ""));
      return null;
    }
    if (bootstrapKey) {
      const immediate = new URL(window.location.href);
      if (immediate.searchParams.get("dex_strategy") !== bootstrapKey) {
        immediate.searchParams.set("dex_strategy", bootstrapKey);
        if (window.history && typeof window.history.replaceState === "function") {
          window.history.replaceState({}, "", immediate.pathname + (immediate.search ? immediate.search : ""));
        }
      }
    }

    const requestUrl = new URL("/api/dex/strategy", window.location.origin);
    requestUrl.searchParams.set("page_path", window.location.pathname);
    const currentDexStrategy = currentQuery().get("dex_strategy");
    if (currentDexStrategy) {
      requestUrl.searchParams.set("dex_strategy", currentDexStrategy);
    }
    const response = await fetch(requestUrl.toString(), { cache: "no-cache", headers: { Accept: "application/json" } });
    const payload = await response.json();
    if (!response.ok || !payload.ok) {
      throw new Error(payload.error || ("请求失败（HTTP " + response.status + "）"));
    }

    window.__DEX_STRATEGY_STATE__ = payload;
    const definitions = Array.isArray(payload.strategies) && payload.strategies.length ? payload.strategies : strategyDefinitions();
    const rawQueryKey = normalizeStrategy(currentQuery().get("dex_strategy"));
    const queryKey = strategyDefinition(rawQueryKey, definitions) ? rawQueryKey : "";
    const strategyKey = normalizeStrategy(
      DEFAULT_BOOTSTRAP.strategyKey ||
      config.selectedStrategyKey ||
      payload.active_strategy ||
      payload.config && payload.config.active_strategy
    );
    const selectedKey = applyBootstrapStrategy(
      queryKey || strategyKey || currentStrategyKey() || definitions[0] && definitions[0].key,
      definitions
    );

    const select = ensureSelect(mount);
    if (!select) return payload;
    renderOptions(select, definitions, selectedKey);
    renderStrategyMeta(mount, strategyDefinition(selectedKey, definitions) || payload.selected_strategy_definition || null);

    select.onchange = async function () {
      const nextKey = normalizeStrategy(select.value) || selectedKey;
      if (!nextKey || nextKey === normalizeStrategy(currentQuery().get("dex_strategy"))) {
        return;
      }
      renderStrategyMeta(mount, strategyDefinition(nextKey, definitions));
      await persistStrategy(nextKey);
      const url = updateUrlForStrategy(nextKey, definitions);
      if (window.location.pathname === url.pathname && window.location.search === url.search) {
        return;
      }
      const target = url.pathname + (url.search ? url.search : "");
      if (window.AppShell && typeof window.AppShell.navigate === "function" && !window.AppShell.isEmbedded()) {
        window.AppShell.navigate(target);
      } else {
        window.location.assign(target);
      }
    };

    window.DexStrategySelector = window.DexStrategySelector || {};
    window.DexStrategySelector.state = payload;
    window.DexStrategySelector.currentStrategyKey = function () {
      return normalizeStrategy(currentQuery().get("dex_strategy") || selectedKey);
    };
    window.DexStrategySelector.strategyDefinitions = function () {
      return definitions.slice();
    };
    window.DexStrategySelector.strategyPathForKey = function (key) {
      return strategyPathForKey(key, definitions);
    };
    window.DexStrategySelector.updateUrlForStrategy = function (key) {
      return updateUrlForStrategy(key, definitions);
    };
    window.DexStrategySelector.selectStrategy = function (key, options) {
      const normalized = normalizeStrategy(key);
      const next = updateUrlForStrategy(normalized, definitions);
      if (window.history && typeof window.history.replaceState === "function" && !(options && options.skipHistory)) {
        window.history.replaceState({}, "", next.pathname + (next.search ? next.search : ""));
      }
      persistStrategy(normalized);
      return next;
    };
    window.dispatchEvent(new CustomEvent(STATE_EVENT, {
      detail: {
        selected_strategy: selectedKey,
        strategy_state: payload,
      },
    }));
    return payload;
  }

  window.DexStrategySelector = window.DexStrategySelector || {
    init: init,
    currentStrategyKey: currentStrategyKey,
    strategyDefinitions: strategyDefinitions,
    strategyPathForKey: function (key) {
      return strategyPathForKey(key, strategyDefinitions());
    },
    updateUrlForStrategy: function (key) {
      return updateUrlForStrategy(key, strategyDefinitions());
    },
  };
})();
