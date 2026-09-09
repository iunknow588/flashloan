// Pure-intent route/spec helpers for the CoW flashloan probe CLI.

const fs = require("fs");
const path = require("path");

const {
  tokenKey,
  requireToken,
} = require("./token-registry");
const {
  decimalNumber,
  numberOrNull,
  parseHumanUnits,
} = require("./decimal-math");
const {
  envFirst,
} = require("./config");

const CONTRACTS_ROOT = path.resolve(__dirname, "..", "..");
const DEFAULT_LIVE_EXTREMES_PATH = path.resolve(
  CONTRACTS_ROOT,
  "../../flashloan/src/runtime/state/latest_extremes.json"
);

function baseSymbol(row) {
  return tokenKey(row?.base_symbol || String(row?.symbol || "").replace(/USDT$/i, ""));
}

function parseObservedAt(value) {
  const ms = Date.parse(String(value || ""));
  return Number.isFinite(ms) ? ms : null;
}

function loadJson(file) {
  return JSON.parse(fs.readFileSync(file, "utf8"));
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function pureIntentCandidateUniverse(extremes, registry, limit = 3) {
  const collect = (side, rows) =>
    (Array.isArray(rows) ? rows : []).slice(0, Math.max(1, limit)).map((row, index) => {
      const symbol = baseSymbol(row);
      const token = registry.get(tokenKey(symbol)) || null;
      return {
        side,
        rank: index + 1,
        symbol: row.symbol || `${symbol}USDT`,
        baseSymbol: symbol,
        address: token?.address || null,
        decimals: token?.decimals ?? null,
        cowSupported: Boolean(token),
        changePercent: numberOrNull(row.change_percent),
        startPrice: numberOrNull(row.start_price),
        currentPrice: numberOrNull(row.current_price ?? row.end_price),
        endPrice: numberOrNull(row.end_price ?? row.current_price),
        startMs: row.start_ms ?? null,
        endMs: row.end_ms ?? null,
      };
    });
  const gainers = collect("gainer", extremes?.top);
  const losers = collect("loser", extremes?.bottom);
  const bySymbol = new Map();
  for (const item of [...gainers, ...losers]) {
    if (item.baseSymbol && !bySymbol.has(item.baseSymbol)) bySymbol.set(item.baseSymbol, item);
  }
  return {
    limit,
    gainers,
    losers,
    tokens: Array.from(bySymbol.values()),
    supportedTokenCount: Array.from(bySymbol.values()).filter((item) => item.cowSupported).length,
    unsupportedTokenCount: Array.from(bySymbol.values()).filter((item) => !item.cowSupported).length,
    source: "binance_200ms_top_bottom",
  };
}

function pureIntentTokenScope(routeSpec) {
  const candidateUniverse = routeSpec?.candidateUniverse && typeof routeSpec.candidateUniverse === "object" ? routeSpec.candidateUniverse : {};
  const tokenRows = Array.isArray(candidateUniverse.tokens) ? candidateUniverse.tokens : [];
  const rawTokens = [];
  const route = Array.isArray(routeSpec?.route) ? routeSpec.route : [];
  for (const token of tokenRows) {
    if (!token || typeof token !== "object") continue;
    rawTokens.push(token.baseSymbol || token.symbol);
  }
  const scopeTokens = [];
  for (const symbol of [
    routeSpec?.pureIntent?.initialSymbol,
    routeSpec?.pureIntent?.finalSymbol,
    ...rawTokens,
  ]) {
    const key = tokenKey(symbol);
    if (key && !scopeTokens.includes(key)) scopeTokens.push(key);
  }
  return {
    input_symbol: tokenKey(routeSpec?.pureIntent?.initialSymbol || route[0] || "USDC"),
    output_symbol: tokenKey(routeSpec?.pureIntent?.finalSymbol || route[route.length - 1] || routeSpec?.pureIntent?.initialSymbol || "USDC"),
    tokens: scopeTokens,
    token_count: scopeTokens.length,
    scope_role: "solver_owned_token_universe_only",
  };
}

function effectiveRouteTradeFeePercent(tradeFeePercent, routeTradeFeeHops) {
  const feeRate = Math.max(0.0, Number(tradeFeePercent) || 0) / 100.0;
  const hops = Math.max(1, Number(routeTradeFeeHops) || 1);
  return (1.0 - (1.0 - feeRate) ** hops) * 100.0;
}

function pureIntentMinWindowSpreadPercent() {
  const tradeFeePercent = Number(envFirst("ARBITRAGE_TRADE_FEE_PERCENT") || "0.10");
  const flashloanFeePercent = Number(envFirst("ARBITRAGE_FLASHLOAN_FEE_PERCENT") || envFirst("COW_FLASHLOAN_FEE_PERCENT") || "0.05");
  const targetProfitPercent = Number(envFirst("ARBITRAGE_TARGET_PROFIT_PERCENT") || "0.618");
  const routeTradeFeeHops = Number(envFirst("ARBITRAGE_ROUTE_TRADE_FEE_HOPS") || "3");
  return (
    effectiveRouteTradeFeePercent(tradeFeePercent, routeTradeFeeHops) +
    Math.max(0, flashloanFeePercent) +
    Math.max(0, targetProfitPercent)
  );
}

function firstChangePercent(row) {
  return row == null ? null : decimalNumber(row.change_percent);
}

function pureIntentSpecFromExtremes({
  extremes,
  network,
  registry,
  amountHuman,
  minProfitPercentHuman,
  gasReserveHuman,
  otherKnownCostsHuman,
  candidateLimit,
}) {
  if (!extremes || typeof extremes !== "object") {
    return { routeSpec: null, reason: "latest_extremes_missing" };
  }
  const observedMs = parseObservedAt(extremes.observed_at);
  const freshnessSeconds = observedMs == null ? null : (Date.now() - observedMs) / 1000;
  const maxAgeSeconds = Number(envFirst("COW_FLASHLOAN_LIVE_MAX_AGE_SECONDS") || "30");
  if (freshnessSeconds == null) {
    return { routeSpec: null, reason: "latest_extremes_observed_at_missing" };
  }
  if (freshnessSeconds > maxAgeSeconds) {
    return {
      routeSpec: null,
      reason: "latest_extremes_stale",
      freshnessSeconds,
      maxAgeSeconds,
      observedAt: extremes.observed_at,
    };
  }
  const candidateUniverse = pureIntentCandidateUniverse(extremes, registry, candidateLimit);
  const gainer = candidateUniverse.gainers[0] || null;
  const loser = candidateUniverse.losers[0] || null;
  const gainerChangePercent = firstChangePercent(gainer);
  const loserChangePercent = firstChangePercent(loser);
  const windowSpreadPercent =
    gainerChangePercent != null && loserChangePercent != null ? gainerChangePercent - loserChangePercent : null;
  const minWindowSpreadPercent = pureIntentMinWindowSpreadPercent();
  const spreadOk =
    windowSpreadPercent != null &&
    gainerChangePercent != null &&
    loserChangePercent != null &&
    loserChangePercent < 0 &&
    windowSpreadPercent > minWindowSpreadPercent;
  const usdc = requireToken(registry, "USDC");
  if (!spreadOk) {
    return {
      routeSpec: null,
      reason: !gainer || !loser
        ? "latest_extremes_top_bottom_missing"
        : "latest_extremes_below_dynamic_profit_threshold",
      windowSpreadPercent,
      minWindowSpreadPercent,
      gainerChangePercent,
      loserChangePercent,
      candidateUniverse,
      observedAt: extremes.observed_at,
    };
  }
  return {
    routeSpec: {
      network,
      route: ["USDC", "USDC"],
      amountHuman,
      amountUnits: parseHumanUnits(amountHuman, usdc.decimals),
      pair: "USDC pure intent",
      pairRank: 1,
      routeDirection: "pure_intent_same_asset",
      priorityReason: "pure_intent_net_profit_floor",
      observedAt: extremes.observed_at,
      candidateUniverse,
      windowSpreadPercent,
      minWindowSpreadPercent,
      pureIntent: {
        initialSymbol: "USDC",
        finalSymbol: "USDC",
        initialAmountHuman: amountHuman,
        minProfitPercentHuman,
        gasReserveHuman,
        otherKnownCostsHuman,
        tokenScope: pureIntentTokenScope({ candidateUniverse, pureIntent: { initialSymbol: "USDC", finalSymbol: "USDC" }, route: ["USDC", "USDC"] }),
        windowSpreadPercent,
        minWindowSpreadPercent,
        semantics: "solver_owned_token_scope_only",
      },
      liveSignal: {
        freshnessSeconds,
        observedAt: extremes.observed_at,
        windowSeconds: extremes.window_seconds ?? null,
        windowSpreadPercent,
        minWindowSpreadPercent,
        spreadOk,
        gainerChangePercent,
        loserChangePercent,
        candidateUniverse,
      },
    },
    reason: "live_pure_intent_candidate_found",
    freshnessSeconds,
    candidateUniverse,
  };
}

async function loadLivePureIntentSpec({ network, registry }) {
  const livePath = envFirst("COW_FLASHLOAN_LIVE_EXTREMES_PATH") || DEFAULT_LIVE_EXTREMES_PATH;
  const waitSeconds = Math.max(0, Number(envFirst("COW_FLASHLOAN_LIVE_WAIT_SECONDS") || "0"));
  const pollSeconds = Math.max(0.2, Number(envFirst("COW_FLASHLOAN_LIVE_POLL_SECONDS") || "0.5"));
  const amountHuman = envFirst("COW_FLASHLOAN_PROBE_AMOUNT") || "1000";
  const minProfitPercentHuman = envFirst("COW_FLASHLOAN_PURE_INTENT_MIN_PROFIT_PERCENT") || "0.618";
  const gasReserveHuman = envFirst("COW_FLASHLOAN_PURE_INTENT_GAS_RESERVE_USDC") || "0";
  const otherKnownCostsHuman = envFirst("COW_FLASHLOAN_PURE_INTENT_OTHER_KNOWN_COSTS_USDC") || "0";
  const candidateLimit = Math.max(1, Number(envFirst("COW_FLASHLOAN_PURE_INTENT_CANDIDATE_LIMIT") || "3"));
  const started = Date.now();
  let last = { routeSpec: null, reason: "not_checked" };
  while (Date.now() - started <= waitSeconds * 1000) {
    const extremes = fs.existsSync(livePath) ? loadJson(livePath) : null;
    last = pureIntentSpecFromExtremes({
      extremes,
      network,
      registry,
      amountHuman,
      minProfitPercentHuman,
      gasReserveHuman,
      otherKnownCostsHuman,
      candidateLimit,
    });
    if (last.routeSpec) {
      return {
        livePath,
        status: last.reason,
        routes: [last.routeSpec],
        diagnostic: last,
      };
    }
    if (waitSeconds <= 0) break;
    await sleep(pollSeconds * 1000);
  }
  return { livePath, status: last.reason, routes: [], diagnostic: last };
}

function manualPureIntentSpec(network, registry) {
  const usdc = requireToken(registry, "USDC");
  const amountHuman = envFirst("COW_FLASHLOAN_PROBE_AMOUNT") || "1000";
  const minProfitPercentHuman = envFirst("COW_FLASHLOAN_PURE_INTENT_MIN_PROFIT_PERCENT") || "0.618";
  const gasReserveHuman = envFirst("COW_FLASHLOAN_PURE_INTENT_GAS_RESERVE_USDC") || "0";
  const otherKnownCostsHuman = envFirst("COW_FLASHLOAN_PURE_INTENT_OTHER_KNOWN_COSTS_USDC") || "0";
  return {
    network,
    route: ["USDC", "USDC"],
    amountHuman,
    amountUnits: parseHumanUnits(amountHuman, usdc.decimals),
    pair: "USDC pure intent",
    pairRank: 1,
    routeDirection: "pure_intent_same_asset",
    priorityReason: "pure_intent_net_profit_floor",
    observedAt: null,
    candidateUniverse: {
      limit: Math.max(1, Number(envFirst("COW_FLASHLOAN_PURE_INTENT_CANDIDATE_LIMIT") || "3")),
      gainers: [],
      losers: [],
      tokens: [],
      source: "manual",
    },
    pureIntent: {
      initialSymbol: "USDC",
      finalSymbol: "USDC",
      initialAmountHuman: amountHuman,
      minProfitPercentHuman,
      gasReserveHuman,
      otherKnownCostsHuman,
      tokenScope: pureIntentTokenScope({ pureIntent: { initialSymbol: "USDC", finalSymbol: "USDC" }, route: ["USDC", "USDC"] }),
      semantics: "solver_owned_token_scope_only",
    },
  };
}

function pureIntentAppData({ slippageBps, tokenScope, principalHuman, minFinalAmountHuman }) {
  return {
    metadata: {
      quote: { slippageBips: slippageBps },
      orderClass: { orderClass: "limit" },
      intent: {
        kind: "pure_profit",
        orderBounds: {
          inputAmount: String(principalHuman),
          minimumFinalAmount: String(minFinalAmountHuman),
        },
        tokenScope,
      },
    },
  };
}

function mergeFlashloanAppData(baseAppData, postingAppData) {
  return {
    metadata: {
      ...((baseAppData && baseAppData.metadata) || {}),
      ...((postingAppData && postingAppData.metadata) || {}),
    },
  };
}

module.exports = {
  pureIntentCandidateUniverse,
  pureIntentTokenScope,
  effectiveRouteTradeFeePercent,
  pureIntentMinWindowSpreadPercent,
  firstChangePercent,
  pureIntentSpecFromExtremes,
  loadLivePureIntentSpec,
  manualPureIntentSpec,
  pureIntentAppData,
  mergeFlashloanAppData,
};
