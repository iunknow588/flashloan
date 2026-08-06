const fs = require("fs");
const path = require("path");
const dotenv = require("dotenv");
const { createPublicClient, http } = require("viem");
const viemChains = require("viem/chains");
const { setGlobalAdapter } = require("@cowprotocol/sdk-common");
const { ViemAdapter } = require("@cowprotocol/sdk-viem-adapter");
const { SupportedChainId } = require("@cowprotocol/sdk-config");
const { OrderKind } = require("@cowprotocol/sdk-order-book");
const { TradingSdk } = require("@cowprotocol/sdk-trading");
const {
  AAVE_ADAPTER_FACTORY,
  AAVE_HOOK_ADAPTER_PER_TYPE,
  AAVE_POOL_ADDRESS,
  AaveCollateralSwapSdk,
  AaveFlashLoanType,
} = require("@cowprotocol/sdk-flash-loans");

const CONTRACTS_ROOT = path.resolve(__dirname, "..");
const SRC_BOT_ROOT = path.resolve(CONTRACTS_ROOT, "../../flashloan/src_bot");
const DEFAULT_OUTPUT_PATH = path.resolve(CONTRACTS_ROOT, "deployments/cow-flashloans-probe.json");
const DEFAULT_HISTORY_PATH = path.resolve(SRC_BOT_ROOT, "runtime/logs/cow_execution_attempts.jsonl");
const DEFAULT_LIVE_EXTREMES_PATH = path.resolve(SRC_BOT_ROOT, "runtime/state/latest_extremes.json");
const TEN = 10n;

dotenv.config({ path: path.resolve(CONTRACTS_ROOT, ".env") });
dotenv.config({ path: path.resolve(SRC_BOT_ROOT, ".env"), override: false });

const NETWORKS = {
  ethereum: {
    chainId: SupportedChainId.MAINNET,
    chain: viemChains.mainnet,
    envNames: ["ETHEREUM_RPC_URL", "MAINNET_RPC_URL", "RPC_URL"],
    ownerNames: ["COW_OWNER_ETHEREUM", "COW_OWNER_MAINNET"],
  },
  gnosis: {
    chainId: SupportedChainId.GNOSIS_CHAIN,
    chain: viemChains.gnosis,
    envNames: ["GNOSIS_RPC_URL", "XDAI_RPC_URL", "RPC_URL"],
    ownerNames: ["COW_OWNER_GNOSIS", "COW_OWNER_XDAI"],
  },
  arbitrum_one: {
    chainId: SupportedChainId.ARBITRUM_ONE,
    chain: viemChains.arbitrum,
    envNames: ["ARBITRUM_ONE_RPC_URL", "ARBITRUM_RPC_URL", "RPC_URL"],
    ownerNames: ["COW_OWNER_ARBITRUM_ONE", "COW_OWNER_ARBITRUM"],
  },
  base: {
    chainId: SupportedChainId.BASE,
    chain: viemChains.base,
    envNames: ["BASE_RPC_URL", "RPC_URL"],
    ownerNames: ["COW_OWNER_BASE"],
  },
  polygon: {
    chainId: SupportedChainId.POLYGON,
    chain: viemChains.polygon,
    envNames: ["POLYGON_RPC_URL", "MATIC_RPC_URL", "RPC_URL"],
    ownerNames: ["COW_OWNER_POLYGON"],
    defaultRpc: "https://polygon-rpc.com",
  },
  avalanche: {
    chainId: SupportedChainId.AVALANCHE,
    chain: viemChains.avalanche,
    envNames: ["AVALANCHE_RPC_URL", "AVALANCHE_RPC", "RPC_URL"],
    ownerNames: ["COW_OWNER_AVALANCHE"],
    defaultRpc: "https://api.avax.network/ext/bc/C/rpc",
  },
  bnb: {
    chainId: SupportedChainId.BNB,
    chain: viemChains.bsc,
    envNames: ["BNB_RPC_URL", "BSC_RPC_URL", "BINANCE_RPC_URL", "RPC_URL"],
    ownerNames: ["COW_OWNER_BNB", "COW_OWNER_BINANCE"],
    defaultRpc: "https://bsc-dataseed.binance.org",
  },
  linea: {
    chainId: SupportedChainId.LINEA,
    chain: viemChains.linea,
    envNames: ["LINEA_RPC_URL", "RPC_URL"],
    ownerNames: ["COW_OWNER_LINEA"],
  },
  sepolia: {
    chainId: SupportedChainId.SEPOLIA,
    chain: viemChains.sepolia,
    envNames: ["SEPOLIA_RPC_URL", "ETHEREUM_SEPOLIA_RPC_URL", "RPC_URL"],
    ownerNames: ["COW_OWNER_SEPOLIA"],
    defaultRpc: "https://sepolia.drpc.org",
  },
};

const NETWORK_ALIASES = {
  avax: "avalanche",
  "avalanche-c": "avalanche",
  bsc: "bnb",
  binance: "bnb",
  arbitrum: "arbitrum_one",
  arb: "arbitrum_one",
  matic: "polygon",
  mainnet: "ethereum",
  eth: "ethereum",
  ethereum_sepolia: "sepolia",
  testnet: "sepolia",
  xdai: "gnosis",
};

function envFirst(...names) {
  for (const name of names) {
    const value = process.env[name];
    if (value && value.trim() && value.trim() !== "0x...") return value.trim();
  }
  return "";
}

function envBool(name, fallback = false) {
  const raw = envFirst(name);
  if (!raw) return fallback;
  return ["1", "true", "yes", "on"].includes(raw.toLowerCase());
}

function loadJson(file) {
  return JSON.parse(fs.readFileSync(file, "utf8"));
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function normalizeNetwork(value) {
  const key = String(value || "avalanche").trim().toLowerCase().replace(/-/g, "_");
  return NETWORK_ALIASES[key] || key;
}

function networkConfig(value) {
  const network = normalizeNetwork(value);
  const config = NETWORKS[network];
  if (!config) {
    throw new Error(`unsupported SDK probe network: ${value || network}`);
  }
  return { network, ...config };
}

function tokenKey(symbol) {
  return String(symbol || "").trim().toUpperCase();
}

function stripUsdt(symbol) {
  return tokenKey(symbol).replace(/USDT$/, "");
}

function isAddress(value) {
  const text = String(value || "").trim();
  return text.startsWith("0x") && text.length === 42;
}

function registerToken(registry, token) {
  if (!token || !token.address || token.decimals == null) return;
  const symbol = tokenKey(token.symbol);
  const normalized = {
    ...token,
    symbol,
    address: String(token.address),
    decimals: Number(token.decimals),
  };
  if (symbol) registry.set(symbol, { ...(registry.get(symbol) || {}), ...normalized });
  registry.set(normalized.address.toLowerCase(), normalized);
}

function loadTokenOverrides(registry) {
  const raw = envFirst("COW_FLASHLOAN_PROBE_TOKEN_OVERRIDES_JSON", "COW_TOKEN_OVERRIDES_JSON");
  if (!raw) return;
  const payload = JSON.parse(raw);
  const rows = Array.isArray(payload)
    ? payload
    : Object.entries(payload).map(([symbol, value]) => ({ symbol, ...(value || {}) }));
  for (const item of rows) registerToken(registry, item);
}

function loadTokenRegistry(network) {
  const aavePath = path.resolve(SRC_BOT_ROOT, "runtime/cache/aave_reserve_assets.json");
  const cowPath = path.resolve(SRC_BOT_ROOT, "runtime/cache/cow_supported_tokens.json");
  const aave = fs.existsSync(aavePath) ? loadJson(aavePath).assets || [] : [];
  const cow = (((fs.existsSync(cowPath) ? loadJson(cowPath) : {}).networks || {})[network] || {}).tokens || [];
  const registry = new Map();

  for (const item of cow) {
    registerToken(registry, {
      symbol: item.symbol,
      address: item.address,
      decimals: item.decimals,
      cowSource: item.source,
      cowSupported: true,
    });
  }
  if (network === "avalanche") {
    for (const item of aave) {
      const aliases = [tokenKey(item.token_symbol), stripUsdt(item.binance_symbol)];
      for (const alias of aliases) {
        if (!alias) continue;
        const existing = registry.get(alias) || {};
        registerToken(registry, {
          ...existing,
          symbol: alias,
          address: existing.address || item.token_address,
          decimals: Number(existing.decimals ?? item.decimals),
          aaveTokenSymbol: item.token_symbol,
          binanceSymbol: item.binance_symbol,
          aaveAvailableLiquidity: item.available_liquidity,
          aaveReserveLiquidity: item.reserve_data_liquidity,
          aaveDepthScoreUsd: item.depth_score_usd,
          aaveSource: "aave_reserve_assets",
        });
      }
    }
  }
  loadTokenOverrides(registry);
  return registry;
}

function requireToken(registry, symbol) {
  const text = String(symbol || "").trim();
  const token = registry.get(isAddress(text) ? text.toLowerCase() : tokenKey(text));
  if (!token || !token.address || token.decimals == null) {
    throw new Error(`token not found in CoW/Aave cache for this network: ${symbol}`);
  }
  return token;
}

function pow10(decimals) {
  return TEN ** BigInt(Number(decimals));
}

function parseHumanUnits(amount, decimals) {
  const text = String(amount || "0").trim();
  if (!text || text.startsWith("-")) throw new Error(`invalid positive amount: ${amount}`);
  const [whole, fractional = ""] = text.split(".");
  const scale = Number(decimals);
  const padded = `${fractional}${"0".repeat(scale)}`.slice(0, scale);
  return (BigInt(whole || "0") * pow10(decimals) + BigInt(padded || "0")).toString();
}

function parseDecimalScaled(value, scale = 1000000n) {
  const text = String(value ?? "0").trim();
  if (!/^(?:\d+)(?:\.\d+)?$/.test(text)) {
    throw new Error(`invalid non-negative decimal: ${value}`);
  }
  const [whole, fractional = ""] = text.split(".");
  const scaleDigits = String(scale).length - 1;
  const fractionText = `${fractional}${"0".repeat(scaleDigits)}`.slice(0, scaleDigits);
  return BigInt(whole) * scale + BigInt(fractionText || "0");
}

function percentageOfUnits(amountUnits, percentHuman) {
  const scale = 1000000n;
  const percentScaled = parseDecimalScaled(percentHuman, scale);
  const denominator = 100n * scale;
  if (percentScaled === 0n || amountUnits === 0n) return 0n;
  // Round up so the configured pure-profit floor can never be underfunded.
  return (amountUnits * percentScaled + denominator - 1n) / denominator;
}

function formatUnits(amount, decimals) {
  if (amount == null || amount === "") return null;
  const value = BigInt(String(amount));
  const scale = pow10(decimals);
  const whole = value / scale;
  const fraction = value % scale;
  if (fraction === 0n) return whole.toString();
  return `${whole}.${fraction.toString().padStart(Number(decimals), "0").replace(/0+$/, "")}`;
}

function decimalNumber(value) {
  if (value == null || value === "") return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function compareHuman(actual, expected) {
  const a = decimalNumber(actual);
  const b = decimalNumber(expected);
  if (a == null || b == null) return null;
  if (a >= b) return "met";
  return "below";
}

function decimalDifference(a, b) {
  const left = decimalNumber(a);
  const right = decimalNumber(b);
  if (left == null || right == null) return null;
  return left - right;
}

function decimalPercent(numerator, denominator) {
  const top = decimalNumber(numerator);
  const bottom = decimalNumber(denominator);
  if (top == null || bottom == null || bottom === 0) return null;
  return (top / bottom) * 100;
}

function decimalPercentDelta(actual, expected) {
  const left = decimalNumber(actual);
  const right = decimalNumber(expected);
  if (left == null || right == null || right === 0) return null;
  return ((left - right) / right) * 100;
}

function bigintText(value) {
  return typeof value === "bigint" ? value.toString() : String(value ?? "");
}

function jsonFriendly(value) {
  if (value === undefined) return undefined;
  return JSON.parse(
    JSON.stringify(value, (_key, item) => {
      if (typeof item === "bigint") return item.toString();
      return item;
    })
  );
}

function orderToSignSummary(order) {
  if (!order || typeof order !== "object") return null;
  return {
    sellToken: order.sellToken,
    buyToken: order.buyToken,
    receiver: order.receiver,
    sellAmount: order.sellAmount,
    buyAmount: order.buyAmount,
    validTo: order.validTo,
    appData: order.appData,
    feeAmount: order.feeAmount,
    kind: order.kind,
    partiallyFillable: order.partiallyFillable,
    sellTokenBalance: order.sellTokenBalance,
    buyTokenBalance: order.buyTokenBalance,
  };
}

function quoteResponseSummary(response) {
  if (!response || typeof response !== "object") return null;
  const quote = response.quote && typeof response.quote === "object" ? response.quote : {};
  return {
    id: response.id ?? response.quoteId ?? null,
    verified: response.verified ?? quote.verified ?? null,
    solver: response.solver ?? quote.solver ?? null,
    quote: {
      sellAmount: quote.sellAmount,
      buyAmount: quote.buyAmount,
      feeAmount: quote.feeAmount,
      gasAmount: quote.gasAmount,
      validTo: quote.validTo,
      appData: quote.appData,
      kind: quote.kind,
      partiallyFillable: quote.partiallyFillable,
    },
  };
}

function appDataSummary(appDataInfo, metadata) {
  const doc = appDataInfo?.doc && typeof appDataInfo.doc === "object" ? appDataInfo.doc : {};
  const docMetadata = doc.metadata && typeof doc.metadata === "object" ? doc.metadata : {};
  return {
    appDataKeccak256: appDataInfo?.appDataKeccak256 || null,
    fullAppDataPresent: Boolean(appDataInfo?.fullAppData),
    docVersion: doc.version || null,
    orderClass: docMetadata.orderClass?.orderClass || docMetadata.orderClass || null,
    metadataKeys: Object.keys(docMetadata),
    generatedMetadataKeys: Object.keys(metadata || {}),
  };
}

function hooksSummary(hooks) {
  const result = {};
  for (const key of ["pre", "post"]) {
    const rows = Array.isArray(hooks?.[key]) ? hooks[key] : [];
    result[key] = rows.map((hook) => ({
      target: hook.target,
      gasLimit: hook.gasLimit,
      callDataBytes: typeof hook.callData === "string" ? Math.max(0, (hook.callData.length - 2) / 2) : null,
    }));
  }
  return result;
}

function sdkDeploymentSummary(config) {
  return {
    aavePool: AAVE_POOL_ADDRESS[config.chainId] || "",
    adapterFactory: AAVE_ADAPTER_FACTORY[config.chainId] || "",
    collateralAdapter: AAVE_HOOK_ADAPTER_PER_TYPE[AaveFlashLoanType.CollateralSwap][config.chainId] || "",
    debtAdapter: AAVE_HOOK_ADAPTER_PER_TYPE[AaveFlashLoanType.DebtSwap][config.chainId] || "",
    repayAdapter: AAVE_HOOK_ADAPTER_PER_TYPE[AaveFlashLoanType.RepayCollateral][config.chainId] || "",
  };
}

function sdkDeploymentGaps(summary) {
  return Object.entries(summary)
    .filter(([_key, value]) => !isAddress(value))
    .map(([key]) => key);
}

function buildSingleSolverSettlementIntent(routeSpec, tokens, legs) {
  const route = Array.isArray(routeSpec?.route) ? routeSpec.route.map(tokenKey) : [];
  const hopCount = Math.max(0, route.length - 1);
  const closedCycle = route.length >= 2 && route[0] === route[route.length - 1];
  const threeHopRoute = hopCount >= 3;
  const supported = threeHopRoute && closedCycle;
  const singleStartingAsset = new Set([route[0]].filter(Boolean)).size === 1;
  const borrowedAndRepaidSameAsset = closedCycle && route[0] === route[route.length - 1];
  return {
    model: "single_flashloan_router_call_with_single_cow_solver_settlement",
    requestedSemantics: "one_starting_asset_one_flashloan_one_cow_solver_settlement",
    testedSemantics: "sequential_quote_only_per_hop_sdk_probe",
    borrowedAsset: tokens[0]?.symbol || route[0] || null,
    repaidAsset: tokens[tokens.length - 1]?.symbol || route[route.length - 1] || null,
    route,
    hopCount,
    requiredMinimumHopCount: 3,
    threeHopRoute,
    closedCycle,
    startingAssetCount: singleStartingAsset ? 1 : 0,
    singleStartingAsset,
    borrowedAndRepaidSameAsset,
    flashLoanCount: supported ? 1 : 0,
    solverOrderCount: supported ? 1 : 0,
    settlementTransactionCount: supported ? 1 : 0,
    independentPerHopOrderCount: 0,
    diagnosticQuoteLegCount: legs.length,
    submissionMode: supported
      ? "one_flashLoanAndSettle_call"
      : "blocked_requires_closed_three_hop_solver_path",
    proofStatus: "not_proven_quote_only",
    proofGap:
      "current probe quotes each hop separately; proof requires a submitted single order and one settlement tx carrying the whole cycle",
    requiredEvidence: [
      "single_order_uid",
      "single_settlement_tx_hash",
      "flashloan_metadata_in_app_data",
      "solver_interactions_cover_three_hop_cycle_or_better",
      "flashloan_principal_plus_fee_repaid_in_final_settlement",
    ],
  };
}

function readJsonl(pathname) {
  if (!fs.existsSync(pathname)) return [];
  return fs
    .readFileSync(pathname, "utf8")
    .split(/\r?\n/)
    .filter(Boolean)
    .map((line) => {
      try {
        return JSON.parse(line);
      } catch (_) {
        return null;
      }
    })
    .filter(Boolean);
}

function routeFromAttempt(row) {
  const quote = row.quote && typeof row.quote === "object" ? row.quote : {};
  const route = Array.isArray(row.route_path) && row.route_path.length ? row.route_path : quote.path || quote.route;
  if (!Array.isArray(route) || route.length < 2) return null;
  return {
    network: normalizeNetwork(row.network || (row.market_state || {}).cow_filter?.network || "avalanche"),
    route: route.map((item) => String(item).trim()).filter(Boolean),
    amountHuman: String(quote.input_amount || row.amount || process.env.COW_FLASHLOAN_PROBE_AMOUNT || "1"),
    pair: row.pair || quote.pair || "",
    pairRank: row.pair_rank || quote.pair_rank || null,
    priorityReason: row.priority_reason || quote.priority_reason || "",
    observedAt: row.observed_at || row.created_at || null,
    ownPlan: quote.binance_execution_plan || (quote.route || {}).binance_execution_plan || null,
  };
}

function loadHistoryRoutes({ network, limit, onlyTop1 }) {
  const historyPath = envFirst("COW_FLASHLOAN_PROBE_HISTORY_PATH") || DEFAULT_HISTORY_PATH;
  const wantedNetwork = normalizeNetwork(network);
  const seen = new Set();
  const routes = [];
  for (const row of readJsonl(historyPath).reverse()) {
    const item = routeFromAttempt(row);
    if (!item || item.network !== wantedNetwork) continue;
    if (onlyTop1 && item.pairRank != null && Number(item.pairRank) !== 1) continue;
    const key = `${item.network}|${item.pair}|${item.route.join(">")}`;
    if (seen.has(key)) continue;
    seen.add(key);
    routes.push(item);
    if (routes.length >= limit) break;
  }
  return { historyPath, routes };
}

function manualRouteSpec(network, registry) {
  const route = (envFirst("COW_FLASHLOAN_PROBE_ROUTE") || "USDC,WAVAX,WETH.E,USDC")
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
  const first = requireToken(registry, route[0]);
  const amountUnits = envFirst("COW_FLASHLOAN_PROBE_AMOUNT_UNITS");
  const amountHuman = envFirst("COW_FLASHLOAN_PROBE_AMOUNT") || "1";
  return {
    network,
    route,
    amountHuman,
    amountUnits: amountUnits || parseHumanUnits(amountHuman, first.decimals),
    pair: "manual",
    pairRank: null,
    priorityReason: "manual_route",
    observedAt: null,
    ownPlan: null,
  };
}

function withUnits(routeSpec, registry) {
  if (routeSpec.amountUnits) return routeSpec;
  const first = requireToken(registry, routeSpec.route[0]);
  return {
    ...routeSpec,
    amountUnits: parseHumanUnits(routeSpec.amountHuman || "1", first.decimals),
  };
}

function baseSymbol(row) {
  return tokenKey(row?.base_symbol || String(row?.symbol || "").replace(/USDT$/i, ""));
}

function symbolSupported(registry, symbol) {
  return Boolean(registry.get(tokenKey(symbol)));
}

function numberOrNull(value) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function parseObservedAt(value) {
  const ms = Date.parse(String(value || ""));
  return Number.isFinite(ms) ? ms : null;
}

function buildLiveRouteFromExtremes({ extremes, network, registry, amountHuman, minSideChangePercent, minSpreadPercent }) {
  if (!extremes || typeof extremes !== "object") return { routeSpec: null, reason: "latest_extremes_missing" };
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

  const rows = Array.isArray(extremes.basket) ? extremes.basket : [];
  const eligible = rows
    .map((row) => ({
      ...row,
      base_symbol: baseSymbol(row),
      change_percent: numberOrNull(row.change_percent),
      current_price: numberOrNull(row.current_price ?? row.end_price),
      start_price: numberOrNull(row.start_price),
    }))
    .filter((row) => {
      if (!row.base_symbol || row.base_symbol === "USDC" || row.base_symbol === "USDT") return false;
      if (row.change_percent == null) return false;
      if (Math.abs(row.change_percent) < minSideChangePercent) return false;
      if (!symbolSupported(registry, row.base_symbol)) return false;
      return true;
    });
  const positiveCandidates = eligible.filter((row) => row.change_percent > 0).sort((a, b) => b.change_percent - a.change_percent);
  const negativeCandidates = eligible.filter((row) => row.change_percent < 0).sort((a, b) => a.change_percent - b.change_percent);
  const top = positiveCandidates[0];
  const bottom = negativeCandidates[0];
  const routeLimit = Math.max(1, Number(envFirst("COW_FLASHLOAN_LIVE_ROUTE_LIMIT") || "5"));
  const candidateSummary = (items) =>
    items.slice(0, 10).map((row) => ({
      symbol: row.symbol,
      baseSymbol: row.base_symbol,
      changePercent: row.change_percent,
      currentPrice: row.current_price,
      startPrice: row.start_price,
    }));
  if (!top || !bottom) {
    return {
      routeSpec: null,
      reason: "live_top_bottom_not_available",
      eligibleCount: eligible.length,
      supportedPositiveCount: positiveCandidates.length,
      supportedNegativeCount: negativeCandidates.length,
      topCandidates: candidateSummary(positiveCandidates),
      bottomCandidates: candidateSummary(negativeCandidates),
      minSideChangePercent,
      minSpreadPercent,
      observedAt: extremes.observed_at,
      freshnessSeconds,
    };
  }
  const spread = top.change_percent - bottom.change_percent;
  const pairSpecs = [];
  for (const loser of negativeCandidates) {
    for (const gainer of positiveCandidates) {
      if (loser.base_symbol === gainer.base_symbol) continue;
      const candidateSpread = gainer.change_percent - loser.change_percent;
      if (candidateSpread <= minSpreadPercent) continue;
      pairSpecs.push({
        top: gainer,
        bottom: loser,
        spreadPercent: candidateSpread,
      });
    }
  }
  pairSpecs.sort((a, b) => b.spreadPercent - a.spreadPercent);
  if (spread <= minSpreadPercent) {
    return {
      routeSpec: null,
      reason: "live_spread_below_min",
      spreadPercent: spread,
      minSpreadPercent,
      observedAt: extremes.observed_at,
      freshnessSeconds,
      supportedPositiveCount: positiveCandidates.length,
      supportedNegativeCount: negativeCandidates.length,
      topCandidates: candidateSummary(positiveCandidates),
      bottomCandidates: candidateSummary(negativeCandidates),
      top,
      bottom,
    };
  }
  return {
    routeSpec: {
      network,
      route: ["USDC", bottom.base_symbol, top.base_symbol, "USDC"],
      amountHuman,
      pair: `${top.symbol || top.base_symbol} / ${bottom.symbol || bottom.base_symbol}`,
      pairRank: 1,
      routeDirection: "forward_buy_loser_then_gainer",
      priorityReason: "buy_loser_then_gainer_live_top1",
      observedAt: extremes.observed_at,
      ownPlan: {
        available: true,
        initial_amount: amountHuman,
        initial_symbol: "USDC",
        final_symbol: "USDC",
        route: ["USDC", bottom.base_symbol, top.base_symbol, "USDC"],
        market_prices: [
          {
            symbol: top.base_symbol,
            start_price: top.start_price,
            current_price: top.current_price,
            change_percent: top.change_percent,
            role: "gainer_sell_high",
          },
          {
            symbol: bottom.base_symbol,
            start_price: bottom.start_price,
            current_price: bottom.current_price,
            change_percent: bottom.change_percent,
            role: "loser_buy_low",
          },
        ],
        steps: [],
      },
      liveSignal: {
        spreadPercent: spread,
        minSpreadPercent,
        minSideChangePercent,
        freshnessSeconds,
        top,
        bottom,
      },
    },
    routes: pairSpecs.slice(0, routeLimit).flatMap((item, index) => {
      const directions = [
        {
          route: ["USDC", item.bottom.base_symbol, item.top.base_symbol, "USDC"],
          routeDirection: "forward_buy_loser_then_gainer",
          priorityReason: "buy_loser_then_gainer_live_supported_queue",
        },
        {
          route: ["USDC", item.top.base_symbol, item.bottom.base_symbol, "USDC"],
          routeDirection: "reverse_buy_gainer_then_loser",
          priorityReason: "reverse_check_live_supported_queue",
        },
      ];
      return directions.map((direction) => ({
        network,
        route: direction.route,
        amountHuman,
        pair: `${item.top.symbol || item.top.base_symbol} / ${item.bottom.symbol || item.bottom.base_symbol}`,
        pairRank: index + 1,
        routeDirection: direction.routeDirection,
        priorityReason: direction.priorityReason,
        observedAt: extremes.observed_at,
        ownPlan: {
          available: true,
          initial_amount: amountHuman,
          initial_symbol: "USDC",
          final_symbol: "USDC",
          route: direction.route,
          market_prices: [
            {
              symbol: item.top.base_symbol,
              start_price: item.top.start_price,
              current_price: item.top.current_price,
              change_percent: item.top.change_percent,
              role: "gainer_sell_high",
            },
            {
              symbol: item.bottom.base_symbol,
              start_price: item.bottom.start_price,
              current_price: item.bottom.current_price,
              change_percent: item.bottom.change_percent,
              role: "loser_buy_low",
            },
          ],
          steps: [],
        },
        liveSignal: {
          spreadPercent: item.spreadPercent,
          minSpreadPercent,
          minSideChangePercent,
          freshnessSeconds,
          top: item.top,
          bottom: item.bottom,
          routeDirection: direction.routeDirection,
        },
      }));
    }),
    candidatePairCount: pairSpecs.length,
    candidateRouteCount: pairSpecs.length * 2,
    reason: "live_top1_candidate_found",
  };
}

async function loadLiveRouteSpecs({ network, registry }) {
  const livePath = envFirst("COW_FLASHLOAN_LIVE_EXTREMES_PATH") || DEFAULT_LIVE_EXTREMES_PATH;
  const waitSeconds = Math.max(0, Number(envFirst("COW_FLASHLOAN_LIVE_WAIT_SECONDS") || "0"));
  const pollSeconds = Math.max(1, Number(envFirst("COW_FLASHLOAN_LIVE_POLL_SECONDS") || "2"));
  const amountHuman = envFirst("COW_FLASHLOAN_PROBE_AMOUNT") || "1000";
  const minSideChangePercent = Math.max(0, Number(envFirst("COW_FLASHLOAN_MIN_SIDE_CHANGE_PERCENT") || "0.3"));
  const minSpreadPercent = Math.max(0, Number(envFirst("COW_FLASHLOAN_MIN_SPREAD_PERCENT") || "0.968"));
  const started = Date.now();
  let last = { routeSpec: null, reason: "not_checked" };
  while (Date.now() - started <= waitSeconds * 1000) {
    const extremes = fs.existsSync(livePath) ? loadJson(livePath) : null;
    last = buildLiveRouteFromExtremes({
      extremes,
      network,
      registry,
      amountHuman,
      minSideChangePercent,
      minSpreadPercent,
    });
    if (Array.isArray(last.routes) && last.routes.length) {
      return { livePath, status: last.reason, routes: last.routes.map((item) => withUnits(item, registry)), diagnostic: last };
    }
    if (last.routeSpec) return { livePath, status: last.reason, routes: [withUnits(last.routeSpec, registry)], diagnostic: last };
    if (waitSeconds <= 0) break;
    await sleep(pollSeconds * 1000);
  }
  return { livePath, status: last.reason, routes: [], diagnostic: last };
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
        candidateUniverse,
        windowSpreadPercent,
        minWindowSpreadPercent,
        semantics:
          "buy_at_least_initial_plus_flashloan_fee_plus_gas_reserve_plus_other_known_costs_plus_initial_amount_percentage_profit",
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
      candidateUniverse: null,
      semantics:
        "buy_at_least_initial_plus_flashloan_fee_plus_gas_reserve_plus_other_known_costs_plus_initial_amount_percentage_profit",
    },
  };
}

function pureIntentAppData({
  slippageBps,
  candidateUniverse,
  minProfitPercentHuman,
  gasReserveHuman,
  otherKnownCostsHuman,
}) {
  return {
    metadata: {
      quote: { slippageBips: slippageBps },
      orderClass: { orderClass: "limit" },
      intent: {
        kind: "pure_profit",
        minProfitPercent: String(minProfitPercentHuman),
        gasReserveUsdc: String(gasReserveHuman),
        otherKnownCostsUsdc: String(otherKnownCostsHuman),
        candidateUniverse,
      },
    },
  };
}

async function probePureIntent({
  routeSpec,
  registry,
  tradingSdk,
  flashSdk,
  chainId,
  owner,
  flashLoanFeePercent,
  slippageBps,
}) {
  const usdc = requireToken(registry, "USDC");
  const principalUnits = BigInt(routeSpec.amountUnits);
  const flashLoanFeeBps = Math.round(flashLoanFeePercent * 100);
  const { flashLoanFeeAmount } = flashSdk.calculateFlashLoanAmounts({
    sellAmount: principalUnits,
    flashLoanFeeBps,
  });
  const minProfitUnits = percentageOfUnits(
    principalUnits,
    routeSpec.pureIntent.minProfitPercentHuman
  );
  const gasReserveUnits = BigInt(parseHumanUnits(routeSpec.pureIntent.gasReserveHuman, usdc.decimals));
  const otherKnownCostsUnits = BigInt(
    parseHumanUnits(routeSpec.pureIntent.otherKnownCostsHuman, usdc.decimals)
  );
  const knownCostsUnits = flashLoanFeeAmount + gasReserveUnits + otherKnownCostsUnits;
  const targetBuyUnits = principalUnits + knownCostsUnits + minProfitUnits;
  const validTo = Math.ceil(Date.now() / 1000) + 300;
  const quoteParams = {
    chainId,
    owner,
    kind: OrderKind.BUY,
    sellToken: usdc.address,
    sellTokenDecimals: usdc.decimals,
    buyToken: usdc.address,
    buyTokenDecimals: usdc.decimals,
    amount: targetBuyUnits.toString(),
    validTo,
    slippageBps,
    flashLoanFeeAmount,
  };
  const customAppData = pureIntentAppData({
    slippageBps,
    candidateUniverse: routeSpec.candidateUniverse,
    minProfitPercentHuman: routeSpec.pureIntent.minProfitPercentHuman,
    gasReserveHuman: routeSpec.pureIntent.gasReserveHuman,
    otherKnownCostsHuman: routeSpec.pureIntent.otherKnownCostsHuman,
  });
  let quote = null;
  let appDataAttempt = "custom_intent";
  let customAppDataError = null;
  let error = null;
  try {
    quote = await tradingSdk.getQuoteOnly(quoteParams, {
      appData: customAppData,
      allowIntermediateEqSellToken: true,
    });
  } catch (firstError) {
    customAppDataError = firstError?.message || String(firstError);
    appDataAttempt = "standard_fallback";
    try {
      quote = await tradingSdk.getQuoteOnly(quoteParams, {
        allowIntermediateEqSellToken: true,
      });
    } catch (secondError) {
      error = `custom_app_data: ${customAppDataError}; standard_fallback: ${secondError?.message || String(secondError)}`;
    }
  }
  if (error || !quote?.orderToSign) {
    return {
      ok: false,
      network: routeSpec.network,
      pair: routeSpec.pair,
      pairRank: routeSpec.pairRank,
      routeDirection: routeSpec.routeDirection,
      priorityReason: routeSpec.priorityReason,
      observedAt: routeSpec.observedAt,
      route: routeSpec.route,
      amountHuman: routeSpec.amountHuman,
      classification: "pure_intent_quote_failed",
      error: error || "pure intent quote returned no order",
      intent: {
        ...routeSpec.pureIntent,
        targetBuyAmountHuman: formatUnits(targetBuyUnits, usdc.decimals),
        minPureProfitHuman: formatUnits(minProfitUnits, usdc.decimals),
        flashLoanFeeHuman: formatUnits(flashLoanFeeAmount, usdc.decimals),
        gasReserveHuman: formatUnits(gasReserveUnits, usdc.decimals),
        otherKnownCostsHuman: formatUnits(otherKnownCostsUnits, usdc.decimals),
        knownCostsHuman: formatUnits(knownCostsUnits, usdc.decimals),
        appDataAttempt,
        customAppDataAccepted: false,
        customAppDataError,
      },
      candidateUniverse: routeSpec.candidateUniverse,
      legs: [],
      profit: null,
      netProfit: null,
      costAnalysis: {
        pureIntent: true,
        minProfitPercent: routeSpec.pureIntent.minProfitPercentHuman,
        requiredMinPureProfitHuman: formatUnits(minProfitUnits, usdc.decimals),
        knownCostsHuman: formatUnits(knownCostsUnits, usdc.decimals),
        error,
      },
      singleSolverSettlement: {
        model: "pure_intent_single_solver_settlement",
        testedSemantics: "quote_only",
        proofStatus: "not_proven_quote_only",
      },
    };
  }
  let posting = null;
  try {
    posting = await flashSdk.getOrderPostingSettings(
      AaveFlashLoanType.CollateralSwap,
      quoteParams,
      {
        flashLoanAmount: principalUnits,
        orderToSign: quote.orderToSign,
      }
    );
  } catch (postingError) {
    error = postingError?.message || String(postingError);
  }
  const order = quote.orderToSign;
  const protectedBuyUnits = BigInt(order.buyAmount);
  const requiredSellUnits = BigInt(order.sellAmount);
  const maxSellUnits = principalUnits > knownCostsUnits ? principalUnits - knownCostsUnits : 0n;
  const grossDeltaUnits = protectedBuyUnits - principalUnits;
  const netDeltaUnits = grossDeltaUnits - knownCostsUnits;
  const sellBudgetPassed = requiredSellUnits <= maxSellUnits;
  const sellBudgetExcessUnits = requiredSellUnits > maxSellUnits ? requiredSellUnits - maxSellUnits : 0n;
  const profitBudgetMet = netDeltaUnits >= minProfitUnits;
  const feasible = !error && Boolean(quote?.orderToSign);
  const protectedBuyHuman = formatUnits(protectedBuyUnits, usdc.decimals);
  const netProfitHuman = formatUnits(netDeltaUnits, usdc.decimals);
  const postingMetadata = posting?.swapSettings?.appData?.metadata || {};
  const quoteAppData = appDataSummary(quote.appDataInfo, postingMetadata);
  const quoteReport = {
    tradeParameters: {
      kind: "buy",
      sellToken: usdc.symbol,
      buyToken: usdc.symbol,
      targetBuyAmountHuman: formatUnits(targetBuyUnits, usdc.decimals),
      requiredSellAmountHuman: formatUnits(requiredSellUnits, usdc.decimals),
      maxSellAmountHuman: formatUnits(maxSellUnits, usdc.decimals),
      sellBudgetExcessHuman: formatUnits(sellBudgetExcessUnits, usdc.decimals),
    },
    orderToSign: orderToSignSummary(order),
    quoteResponse: quoteResponseSummary(quote.quoteResponse),
    amountsAndCosts: jsonFriendly(quote.amountsAndCosts || null),
    appData: quoteAppData,
    postingAppDataMetadataKeys: Object.keys(postingMetadata),
    customAppDataAccepted: appDataAttempt === "custom_intent" && !customAppDataError,
  };
  return {
    ok: feasible,
    network: routeSpec.network,
    pair: routeSpec.pair,
    pairRank: routeSpec.pairRank,
    routeDirection: routeSpec.routeDirection,
    priorityReason: routeSpec.priorityReason,
    observedAt: routeSpec.observedAt,
    liveSignal: routeSpec.liveSignal || null,
    route: routeSpec.route,
    amountHuman: routeSpec.amountHuman,
    amountUnits: routeSpec.amountUnits,
    classification: feasible ? "pure_intent_quote_returned" : "pure_intent_quote_failed",
    error,
    candidateUniverse: routeSpec.candidateUniverse,
    intent: {
      ...routeSpec.pureIntent,
      targetBuyAmountHuman: formatUnits(targetBuyUnits, usdc.decimals),
      minPureProfitHuman: formatUnits(minProfitUnits, usdc.decimals),
      protectedBuyAmountHuman: protectedBuyHuman,
      requiredSellAmountHuman: formatUnits(requiredSellUnits, usdc.decimals),
      maxSellAmountHuman: formatUnits(maxSellUnits, usdc.decimals),
      flashLoanFeeHuman: formatUnits(flashLoanFeeAmount, usdc.decimals),
      gasReserveHuman: formatUnits(gasReserveUnits, usdc.decimals),
      otherKnownCostsHuman: formatUnits(otherKnownCostsUnits, usdc.decimals),
      knownCostsHuman: formatUnits(knownCostsUnits, usdc.decimals),
      sellBudgetCapHuman: formatUnits(maxSellUnits, usdc.decimals),
      sellBudgetExcessHuman: formatUnits(sellBudgetExcessUnits, usdc.decimals),
      sellBudgetPassed,
      profitBudgetMet,
      appDataAttempt,
      customAppDataAccepted: appDataAttempt === "custom_intent" && !customAppDataError,
      customAppDataError,
    },
    quote: quoteReport,
    profit: {
      inputAmount: routeSpec.amountHuman,
      finalAmount: protectedBuyHuman,
      deltaAmount: formatUnits(grossDeltaUnits, usdc.decimals),
      deltaPercent: String(Number(grossDeltaUnits) / Number(principalUnits) * 100),
      symbol: usdc.symbol,
    },
    netProfit: {
      inputAmount: routeSpec.amountHuman,
      finalAmount: netProfitHuman,
      deltaAmount: netProfitHuman,
      deltaPercent: String(Number(netDeltaUnits) / Number(principalUnits) * 100),
      symbol: usdc.symbol,
    },
    costAnalysis: {
      pureIntent: true,
      grossProfitBeforeFlashLoanAndGas: formatUnits(grossDeltaUnits, usdc.decimals),
      netProfitAfterAllCosts: {
        inputAmount: routeSpec.amountHuman,
        finalAmount: netProfitHuman,
        deltaAmount: netProfitHuman,
        deltaPercent: String(Number(netDeltaUnits) / Number(principalUnits) * 100),
        symbol: usdc.symbol,
      },
      minProfitPercent: routeSpec.pureIntent.minProfitPercentHuman,
      requiredMinPureProfitHuman: formatUnits(minProfitUnits, usdc.decimals),
      flashLoanFeeHuman: formatUnits(flashLoanFeeAmount, usdc.decimals),
      gasReserveHuman: formatUnits(gasReserveUnits, usdc.decimals),
      otherKnownCostsHuman: formatUnits(otherKnownCostsUnits, usdc.decimals),
      knownCostsHuman: formatUnits(knownCostsUnits, usdc.decimals),
      sellBudgetCapHuman: formatUnits(maxSellUnits, usdc.decimals),
      sellBudgetExcessHuman: formatUnits(sellBudgetExcessUnits, usdc.decimals),
      protectedBuyAmountHuman: protectedBuyHuman,
      requiredSellAmountHuman: formatUnits(requiredSellUnits, usdc.decimals),
      maxSellAmountHuman: formatUnits(maxSellUnits, usdc.decimals),
      sellBudgetPassed,
      profitBudgetMet,
      appData: quoteReport.appData,
    },
    legs: [],
    singleSolverSettlement: {
      model: "pure_intent_single_solver_settlement",
      requestedSemantics: "one_starting_asset_one_flashloan_one_cow_solver_settlement",
      testedSemantics: "single_intent_quote_only",
      borrowedAsset: usdc.symbol,
      repaidAsset: usdc.symbol,
      route: routeSpec.route,
      hopCount: 0,
      singleStartingAsset: true,
      borrowedAndRepaidSameAsset: true,
      flashLoanCount: 1,
      solverOrderCount: 1,
      settlementTransactionCount: 1,
      proofStatus: "not_proven_quote_only",
    },
  };
}

function ownStepAnalysis(step, leg) {
  if (!step) return null;
  const actual = leg.buyAmountHuman;
  return {
    minOutputAmount: step.min_output_amount ?? step.cow_sdk_parameters?.min_buy_amount_after_fee ?? null,
    targetOutputAmount: step.target_output_amount ?? step.cow_sdk_parameters?.target_buy_amount_after_fee ?? null,
    selectedTargetSource: step.selected_target_source ?? null,
    selectedAcceptableSource: step.selected_acceptable_source ?? null,
    rule: step.selection_rule || step.rule || null,
    actualVsMin: leg.ok
      ? compareHuman(actual, step.min_output_amount ?? step.cow_sdk_parameters?.min_buy_amount_after_fee)
      : "not_quoted",
    actualVsTarget: leg.ok
      ? compareHuman(actual, step.target_output_amount ?? step.cow_sdk_parameters?.target_buy_amount_after_fee)
      : "not_quoted",
  };
}

function legCostAnalysis({ leg, sell, buy, quoteParams, quote }) {
  const costs = quote.amountsAndCosts || {};
  const beforeAll = costs.beforeAllFees || {};
  const afterNetwork = costs.afterNetworkCosts || {};
  const afterSlippage = costs.afterSlippage || {};
  const quoteBuyHuman = formatUnits(quote.quoteResponse?.quote?.buyAmount, buy.decimals);
  const beforeAllBuyHuman = formatUnits(beforeAll.buyAmount, buy.decimals);
  const afterNetworkBuyHuman = formatUnits(afterNetwork.buyAmount, buy.decimals);
  const afterSlippageBuyHuman = formatUnits(afterSlippage.buyAmount, buy.decimals);
  const networkFeeSellHuman = formatUnits(costs.costs?.networkFee?.amountInSellCurrency, sell.decimals);
  const protocolFeeBuyHuman = formatUnits(costs.costs?.protocolFee?.amount, buy.decimals);
  const slippageLossBuy = decimalDifference(afterNetworkBuyHuman, afterSlippageBuyHuman);
  return {
    configuredSlippageBps: leg.configuredSlippageBps,
    suggestedSlippageBps: quote.suggestedSlippageBps ?? null,
    signedSellAfterFlashLoanFeeHuman: formatUnits(quoteParams.amount, sell.decimals),
    flashLoanFeeHuman: formatUnits(quoteParams.flashLoanFeeAmount, sell.decimals),
    quoteBuyBeforeSlippageHuman: quoteBuyHuman,
    beforeAllFeesBuyHuman: beforeAllBuyHuman,
    afterNetworkCostsBuyHuman: afterNetworkBuyHuman,
    afterSlippageBuyHuman: afterSlippageBuyHuman,
    networkFeeSellHuman,
    protocolFeeBuyHuman,
    slippageLossBuyHuman: slippageLossBuy == null ? null : String(slippageLossBuy),
    slippageLossPercentOfAfterNetwork: decimalPercent(slippageLossBuy, afterNetworkBuyHuman),
    networkFeePercentOfSell: decimalPercent(networkFeeSellHuman, formatUnits(quoteParams.amount, sell.decimals)),
    protocolFeePercentOfBuy: decimalPercent(protocolFeeBuyHuman, beforeAllBuyHuman),
  };
}

async function quoteLeg({ tradingSdk, flashSdk, chainId, owner, sell, buy, amount, flashLoanFeePercent, slippageBps, ownStep }) {
  const tradeParameters = {
    kind: OrderKind.SELL,
    owner,
    sellToken: sell.address,
    sellTokenDecimals: sell.decimals,
    buyToken: buy.address,
    buyTokenDecimals: buy.decimals,
    amount: String(amount),
    validFor: 300,
    slippageBps,
  };
  const params = {
    chainId,
    tradeParameters,
    collateralToken: sell.address,
    flashLoanFeePercent,
    settings: {
      preventApproval: true,
    },
  };
  const quoteParams = await flashSdk.getSwapQuoteParams(params);
  const quote = await tradingSdk.getQuoteOnly(quoteParams);
  const posting = await flashSdk.getOrderPostingSettings(
    AaveFlashLoanType.CollateralSwap,
    quoteParams,
    {
      flashLoanAmount: BigInt(amount),
      orderToSign: quote.orderToSign,
    }
  );
  const metadata = posting.swapSettings?.appData?.metadata || {};
  const hooks = metadata.hooks || {};
  const flashloanMetadata = metadata.flashloan || metadata.flashLoan || null;
  const leg = {
    sellSymbol: sell.symbol,
    buySymbol: buy.symbol,
    sellToken: sell.address,
    buyToken: buy.address,
    sellDecimals: sell.decimals,
    buyDecimals: buy.decimals,
    inputAmount: String(amount),
    inputAmountHuman: formatUnits(amount, sell.decimals),
    configuredSlippageBps: slippageBps,
    signedSellAmountAfterFlashLoanFee: quoteParams.amount,
    signedSellAmountAfterFlashLoanFeeHuman: formatUnits(quoteParams.amount, sell.decimals),
    flashLoanFeeAmount: bigintText(quoteParams.flashLoanFeeAmount),
    flashLoanFeeAmountHuman: formatUnits(quoteParams.flashLoanFeeAmount, sell.decimals),
    buyAmount: quote.orderToSign.buyAmount,
    buyAmountHuman: formatUnits(quote.orderToSign.buyAmount, buy.decimals),
    quoteBuyAmount: quote.quoteResponse?.quote?.buyAmount,
    quoteBuyAmountHuman: formatUnits(quote.quoteResponse?.quote?.buyAmount, buy.decimals),
    sellAmount: quote.orderToSign.sellAmount,
    validTo: quote.orderToSign.validTo,
    orderToSign: orderToSignSummary(quote.orderToSign),
    quoteResponse: quoteResponseSummary(quote.quoteResponse),
    amountsAndCosts: jsonFriendly(quote.amountsAndCosts || null),
    appData: appDataSummary(quote.appDataInfo, metadata),
    instanceAddress: posting.instanceAddress,
    hasFlashloanMetadata: Boolean(flashloanMetadata),
    flashloanMetadata: jsonFriendly(flashloanMetadata),
    hasHooksMetadata: Boolean(metadata.hooks),
    hookKeys: Object.keys(hooks || {}),
    hooks: hooksSummary(hooks),
    aaveLiquidityHint: {
      sellTokenDepthScoreUsd: sell.aaveDepthScoreUsd ?? null,
      sellTokenAvailableLiquidity: sell.aaveAvailableLiquidity ?? null,
      sellTokenReserveLiquidity: sell.aaveReserveLiquidity ?? null,
    },
  };
  leg.costAnalysis = legCostAnalysis({ leg, sell, buy, quoteParams, quote });
  return {
    ...leg,
    ownGuard: ownStepAnalysis(ownStep, leg),
  };
}

function classifyRoute(routeSpec, tokens, legs, error) {
  if (error) return "quote_or_sdk_failed";
  if (!legs.length || legs.some((leg) => !leg.ok)) return "quote_or_sdk_failed";
  if (!legs.every((leg) => leg.hasFlashloanMetadata && leg.hasHooksMetadata)) return "hooks_missing";
  const ownGuardFailures = legs.filter((leg) => leg.ownGuard?.actualVsMin === "below").length;
  if (ownGuardFailures) return "query_below_own_guard";
  const first = tokens[0];
  const last = tokens[tokens.length - 1];
  if (first.address.toLowerCase() === last.address.toLowerCase()) {
    const input = decimalNumber(formatUnits(routeSpec.amountUnits, first.decimals));
    const output = decimalNumber(legs[legs.length - 1].buyAmountHuman);
    if (input != null && output != null && output <= input) return "not_profitable_after_sequential_quotes";
    if (input != null && output != null && output > input) return "profitable_after_sequential_quotes";
  }
  return "quote_hooks_ok_profit_unknown";
}

function enrichLiveSignalWithQuote(routeSpec, tokens, legs) {
  if (!routeSpec?.liveSignal || !legs.length) return routeSpec?.liveSignal || null;
  const expected = routeProfit(routeSpec, tokens, legs, "quoteBuyAmountHuman");
  const protectedFloor = routeProfit(routeSpec, tokens, legs, "buyAmountHuman");
  return {
    ...routeSpec.liveSignal,
    quoteComparison: {
      binanceWindowSpreadPercent: routeSpec.liveSignal.spreadPercent,
      expectedQuoteDeltaPercent: expected?.deltaPercent ?? null,
      protectedFloorDeltaPercent: protectedFloor?.deltaPercent ?? null,
      verdict:
        decimalNumber(expected?.deltaAmount) != null && decimalNumber(expected.deltaAmount) < 0
          ? "solver_price_not_matching_binance_window_edge"
          : "solver_quote_preserves_or_improves_binance_edge",
    },
  };
}

function routeProfit(routeSpec, tokens, legs, outputKey = "buyAmountHuman") {
  if (!legs.length) return null;
  const first = tokens[0];
  const last = tokens[tokens.length - 1];
  if (first.address.toLowerCase() !== last.address.toLowerCase()) return null;
  const input = decimalNumber(formatUnits(routeSpec.amountUnits, first.decimals));
  const output = decimalNumber(legs[legs.length - 1][outputKey]);
  if (input == null || output == null || input === 0) return null;
  return {
    inputAmount: String(input),
    finalAmount: String(output),
    deltaAmount: String(output - input),
    deltaPercent: String(((output - input) / input) * 100),
    symbol: first.symbol,
  };
}

function routeSelection(routes) {
  const pureIntentEnabled = envBool("COW_FLASHLOAN_PURE_INTENT_ENABLED", true);
  const pureIntentMinProfitPercent =
    decimalNumber(envFirst("COW_FLASHLOAN_PURE_INTENT_MIN_PROFIT_PERCENT")) ?? 0.618;
  const absoluteMinProfit =
    decimalNumber(envFirst("COW_FLASHLOAN_PROBE_MIN_PROFIT_USDC", "COW_AUTO_EXECUTE_MIN_PROFIT_USD")) ?? 0;
  const ranking = routes
    .map((route, index) => {
      const protectedProfit = route.profit || route.costAnalysis?.protectedProfitAfterSlippageFloor || null;
      const netProfit = route.netProfit || route.costAnalysis?.netProfitAfterAllCosts || null;
      const expectedProfit = route.costAnalysis?.expectedProfitBeforeSlippageFloor || null;
      const finalAmount = decimalNumber(protectedProfit?.finalAmount);
      const deltaAmount = decimalNumber(netProfit?.deltaAmount ?? protectedProfit?.deltaAmount);
      const expectedFinalAmount = decimalNumber(expectedProfit?.finalAmount);
      const expectedDeltaAmount = decimalNumber(expectedProfit?.deltaAmount);
      const inputAmount = decimalNumber(route.amountHuman ?? protectedProfit?.inputAmount);
      const minProfit =
        pureIntentEnabled
          ? decimalNumber(
              route.intent?.minPureProfitHuman ??
                route.costAnalysis?.requiredMinPureProfitHuman
            ) ??
            (inputAmount == null ? null : inputAmount * pureIntentMinProfitPercent / 100)
          : absoluteMinProfit;
      const quoteAvailable = Boolean(finalAmount != null && (route.ok || route.quote));
      const profitFloorMet = quoteAvailable && deltaAmount != null && minProfit != null && deltaAmount >= minProfit;
      const sellBudgetPassed =
        route.intent?.sellBudgetPassed ?? route.costAnalysis?.sellBudgetPassed ?? null;
      const executionBudgetMet = Boolean(route.ok && profitFloorMet);
      return {
        sourceIndex: index + 1,
        ok: Boolean(route.ok),
        quoteAvailable,
        pair: route.pair,
        pairRank: route.pairRank,
        routeDirection: route.routeDirection || null,
        priorityReason: route.priorityReason,
        route: route.route,
        classification: route.classification,
        finalAmount: protectedProfit?.finalAmount ?? null,
        deltaAmount: netProfit?.deltaAmount ?? protectedProfit?.deltaAmount ?? null,
        deltaPercent: netProfit?.deltaPercent ?? protectedProfit?.deltaPercent ?? null,
        grossDeltaAmount: protectedProfit?.deltaAmount ?? null,
        netProfitAfterAllCosts: netProfit?.deltaAmount ?? null,
        expectedFinalAmountBeforeSlippageFloor: expectedProfit?.finalAmount ?? null,
        expectedDeltaAmountBeforeSlippageFloor: expectedProfit?.deltaAmount ?? null,
        expectedDeltaPercentBeforeSlippageFloor: expectedProfit?.deltaPercent ?? null,
        profitFloorMet,
        sellBudgetPassed,
        profitBudgetMet: executionBudgetMet,
        blockingReason:
          !quoteAvailable
            ? "quote_unavailable"
            : !profitFloorMet
              ? "net_profit_below_percentage_floor"
              : !route.ok
                ? "route_not_feasible"
                : null,
        budgetWarning: sellBudgetPassed === false ? "required_sell_exceeds_available_budget" : null,
        minProfitHuman: minProfit == null ? null : String(minProfit),
        minProfitPercent: pureIntentEnabled ? String(pureIntentMinProfitPercent) : null,
        slippageRecommendation: route.costAnalysis?.slippageRecommendation || null,
        finalLossDominantCause: route.costAnalysis?.finalLossDominantCause || null,
        binanceWindowVerdict: route.binanceWindowAnalysis?.threeHopVerdict || null,
        quoteCloserToWindow: route.binanceWindowAnalysis?.quoteCloserToWindow || null,
        protectedCloserToWindow: route.binanceWindowAnalysis?.protectedCloserToWindow || null,
        threeHopRatios: (route.legs || []).map((leg) => ({
          index: leg.index,
          sellSymbol: leg.sellSymbol,
          buySymbol: leg.buySymbol,
          quoteActualBuyPerSell:
            leg.binanceWindowPriceAnalysis?.quoteBeforeSlippageFloor?.actualBuyPerSell ?? null,
          protectedActualBuyPerSell:
            leg.binanceWindowPriceAnalysis?.protectedAfterSlippageFloor?.actualBuyPerSell ?? null,
          binancePreviousWindowBuyPerSell:
            leg.binanceWindowPriceAnalysis?.quoteBeforeSlippageFloor?.binancePreviousWindowBuyPerSell ?? null,
          binanceNextWindowBuyPerSell:
            leg.binanceWindowPriceAnalysis?.quoteBeforeSlippageFloor?.binanceNextWindowBuyPerSell ?? null,
          quoteVsPreviousWindowPercent:
            leg.binanceWindowPriceAnalysis?.quoteBeforeSlippageFloor?.quoteVsPreviousWindowPercent ?? null,
          quoteVsNextWindowPercent:
            leg.binanceWindowPriceAnalysis?.quoteBeforeSlippageFloor?.quoteVsNextWindowPercent ?? null,
          closerToWindow:
            leg.binanceWindowPriceAnalysis?.quoteBeforeSlippageFloor?.closerToWindow ?? null,
          timingVsBinanceNextWindow:
            leg.binanceWindowPriceAnalysis?.quoteBeforeSlippageFloor?.timingVsBinanceNextWindow ?? null,
        })),
        _sortFinalAmount: quoteAvailable ? finalAmount : Number.NEGATIVE_INFINITY,
        _sortExpectedFinalAmount:
          route.ok && expectedFinalAmount != null ? expectedFinalAmount : Number.NEGATIVE_INFINITY,
        _sortDeltaAmount:
          route.ok && deltaAmount != null ? deltaAmount : Number.NEGATIVE_INFINITY,
        _sortExpectedDeltaAmount:
          route.ok && expectedDeltaAmount != null ? expectedDeltaAmount : Number.NEGATIVE_INFINITY,
      };
    })
    .sort((a, b) => {
      if (a.quoteAvailable !== b.quoteAvailable) return a.quoteAvailable ? -1 : 1;
      if (a.profitBudgetMet !== b.profitBudgetMet) return a.profitBudgetMet ? -1 : 1;
      if (a._sortFinalAmount !== b._sortFinalAmount) return b._sortFinalAmount - a._sortFinalAmount;
      if (a._sortExpectedFinalAmount !== b._sortExpectedFinalAmount) {
        return b._sortExpectedFinalAmount - a._sortExpectedFinalAmount;
      }
      return b._sortDeltaAmount - a._sortDeltaAmount;
    })
    .map(({ _sortFinalAmount, _sortExpectedFinalAmount, _sortDeltaAmount, _sortExpectedDeltaAmount, ...item }, rank) => ({
      rank: rank + 1,
      ...item,
    }));
  const best = ranking[0] || null;
  return {
    minProfitHuman: best?.minProfitHuman ?? null,
    minProfitPercent: pureIntentEnabled ? String(pureIntentMinProfitPercent) : null,
    minProfitRule: pureIntentEnabled ? "input_amount_times_percent" : "absolute_usdc",
    minProfitSymbol: "USDC",
    candidateCount: routes.length,
    quotedCandidateCount: routes.filter((route) => route.ok).length,
    selectedRoute:
      best && best.profitBudgetMet
        ? {
            rank: best.rank,
            sourceIndex: best.sourceIndex,
            pair: best.pair,
            routeDirection: best.routeDirection,
            priorityReason: best.priorityReason,
            route: best.route,
            finalAmount: best.finalAmount,
            deltaAmount: best.deltaAmount,
            deltaPercent: best.deltaPercent,
          }
        : null,
    status: best?.profitBudgetMet ? "selected_profit_budget_passed" : "no_route_above_profit_budget",
    bestRouteEvenIfBlocked: best,
    ranking,
  };
}

function routeCostAnalysis(routeSpec, tokens, legs) {
  const protectedProfit = routeProfit(routeSpec, tokens, legs, "buyAmountHuman");
  const expectedProfit = routeProfit(routeSpec, tokens, legs, "quoteBuyAmountHuman");
  const totalFlashFees = legs
    .map((leg) => decimalNumber(leg.flashLoanFeeAmountHuman))
    .filter((value) => value != null)
    .reduce((sum, value) => sum + value, 0);
  const totalNetworkFeeInLegSellAssets = legs
    .map((leg) => decimalNumber(leg.costAnalysis?.networkFeeSellHuman))
    .filter((value) => value != null)
    .reduce((sum, value) => sum + value, 0);
  const finalDelta = decimalNumber(protectedProfit?.deltaAmount);
  const expectedDelta = decimalNumber(expectedProfit?.deltaAmount);
  const expectedFinal = decimalNumber(expectedProfit?.finalAmount);
  const protectedFinal = decimalNumber(protectedProfit?.finalAmount);
  const slippageFloorLoss =
    expectedFinal != null && protectedFinal != null ? expectedFinal - protectedFinal : null;
  const slippageRecommendation =
    expectedDelta != null && expectedDelta < 0
      ? "do_not_widen_slippage; solver_price_edge_is_already_negative"
      : expectedDelta != null && expectedDelta > 0 && finalDelta != null && finalDelta <= 0
        ? "keep_or_reduce_slippage; protected_floor_removes_expected_profit"
        : "slippage_within_profitability_budget";
  return {
    finalProfit: protectedProfit,
    expectedProfitBeforeSlippageFloor: expectedProfit,
    protectedProfitAfterSlippageFloor: protectedProfit,
    slippageFloorLossInFinalAsset: slippageFloorLoss == null ? null : String(slippageFloorLoss),
    slippageFloorLossPercentOfExpected: decimalPercent(slippageFloorLoss, expectedFinal),
    slippageRecommendation,
    finalLossDominantCause:
      finalDelta != null && finalDelta < 0
        ? "solver_quote_prices_plus_network_protocol_fees_exceed_binance_window_edge"
        : expectedDelta != null && expectedDelta > 0 && finalDelta != null && finalDelta <= 0
          ? "slippage_floor_turns_expected_profit_into_unprofitable_protected_floor"
          : expectedDelta != null && expectedDelta > 0
            ? "profitable_before_slippage_floor"
            : "unknown",
    flashLoanFeePercentConfigured: null,
    totalFlashLoanFeesInNativeLegUnits: String(totalFlashFees),
    totalNetworkFeesInMixedSellAssetUnits: String(totalNetworkFeeInLegSellAssets),
    slippageBpsConsistent: legs.every((leg) => leg.configuredSlippageBps === legs[0]?.configuredSlippageBps),
    configuredSlippageBps: legs[0]?.configuredSlippageBps ?? null,
    legCount: legs.length,
  };
}

function stablePrice(symbol) {
  return ["USDC", "USDT", "DAI"].includes(tokenKey(symbol)) ? 1 : null;
}

function priceRowsFromRoute(routeSpec) {
  const rows = [];
  const addRow = (row, source) => {
    if (!row || typeof row !== "object") return;
    const symbol = tokenKey(row.base_symbol || row.symbol);
    if (!symbol) return;
    rows.push({
      symbol: symbol.replace(/USDT$/, ""),
      startPrice: numberOrNull(row.start_price),
      endPrice: numberOrNull(row.end_price ?? row.current_price),
      currentPrice: numberOrNull(row.current_price ?? row.end_price),
      changePercent: numberOrNull(row.change_percent),
      role: row.role || null,
      source,
    });
  };
  for (const row of routeSpec?.ownPlan?.market_prices || []) addRow(row, "ownPlan.market_prices");
  addRow(routeSpec?.liveSignal?.top, "liveSignal.top");
  addRow(routeSpec?.liveSignal?.bottom, "liveSignal.bottom");
  return rows;
}

function tokenWindowPrice(symbol, routeSpec) {
  const stable = stablePrice(symbol);
  if (stable != null) {
    return {
      symbol: tokenKey(symbol),
      startPrice: stable,
      endPrice: stable,
      currentPrice: stable,
      changePercent: 0,
      role: "stable_reference",
      source: "stable_reference",
    };
  }
  const wanted = tokenKey(symbol);
  const row = priceRowsFromRoute(routeSpec).find((item) => item.symbol === wanted);
  if (!row || row.startPrice == null || row.endPrice == null || row.startPrice <= 0 || row.endPrice <= 0) {
    return null;
  }
  return row;
}

function binanceRateForWindow(sellPrice, buyPrice) {
  if (sellPrice == null || buyPrice == null || buyPrice === 0) return null;
  return sellPrice / buyPrice;
}

function classifyRateTiming(actualRate, previousRate, nextRate) {
  if (actualRate == null || previousRate == null || nextRate == null) return null;
  const previousDistance = Math.abs(actualRate - previousRate);
  const nextDistance = Math.abs(actualRate - nextRate);
  const closerTo = previousDistance <= nextDistance ? "previous_window" : "next_window";
  const movement = nextRate - previousRate;
  let timing = "flat_window";
  if (Math.abs(movement) > Math.max(Math.abs(previousRate), Math.abs(nextRate), 1) * 1e-12) {
    if (movement > 0) {
      timing = actualRate < nextRate ? "lagging_vs_next_window" : "leading_vs_next_window";
    } else {
      timing = actualRate > nextRate ? "lagging_vs_next_window" : "leading_vs_next_window";
    }
  }
  return {
    closerTo,
    timing,
    previousDistancePercent: decimalPercentDelta(actualRate, previousRate),
    nextDistancePercent: decimalPercentDelta(actualRate, nextRate),
  };
}

function legWindowPriceAnalysis(routeSpec, leg, outputKey) {
  if (!leg?.ok) return null;
  const sell = tokenWindowPrice(leg.sellSymbol, routeSpec);
  const buy = tokenWindowPrice(leg.buySymbol, routeSpec);
  const sellAmount = decimalNumber(leg.signedSellAmountAfterFlashLoanFeeHuman || leg.inputAmountHuman);
  const buyAmount = decimalNumber(leg[outputKey]);
  if (!sell || !buy || sellAmount == null || buyAmount == null || sellAmount <= 0) {
    return {
      available: false,
      reason: "binance_window_price_missing_or_invalid",
      sellSymbol: leg.sellSymbol,
      buySymbol: leg.buySymbol,
      sellPrice: sell,
      buyPrice: buy,
    };
  }
  const actualRate = buyAmount / sellAmount;
  const previousRate = binanceRateForWindow(sell.startPrice, buy.startPrice);
  const nextRate = binanceRateForWindow(sell.endPrice, buy.endPrice);
  const timing = classifyRateTiming(actualRate, previousRate, nextRate);
  const impliedBuyPriceAtNextSell = actualRate > 0 ? sell.endPrice / actualRate : null;
  return {
    available: true,
    outputBasis: outputKey === "quoteBuyAmountHuman" ? "quote_before_slippage_floor" : "protected_min_buy_after_slippage_floor",
    sellSymbol: leg.sellSymbol,
    buySymbol: leg.buySymbol,
    sellAmountHuman: String(sellAmount),
    buyAmountHuman: String(buyAmount),
    actualBuyPerSell: String(actualRate),
    binancePreviousWindowBuyPerSell: previousRate == null ? null : String(previousRate),
    binanceNextWindowBuyPerSell: nextRate == null ? null : String(nextRate),
    actualOutputAtBinancePreviousWindow: previousRate == null ? null : String(sellAmount * previousRate),
    actualOutputAtBinanceNextWindow: nextRate == null ? null : String(sellAmount * nextRate),
    quoteVsPreviousWindowPercent: decimalPercentDelta(actualRate, previousRate),
    quoteVsNextWindowPercent: decimalPercentDelta(actualRate, nextRate),
    closerToWindow: timing?.closerTo || null,
    timingVsBinanceNextWindow: timing?.timing || null,
    impliedBuyTokenUsdtPriceUsingNextSellPrice:
      impliedBuyPriceAtNextSell == null ? null : String(impliedBuyPriceAtNextSell),
    sellPriceWindow: sell,
    buyPriceWindow: buy,
  };
}

function routeWindowAnalysis(routeSpec, tokens, legs) {
  if (!legs.length) return null;
  const previous = legs.map((leg) => legWindowPriceAnalysis(routeSpec, leg, "quoteBuyAmountHuman"));
  const protectedFloor = legs.map((leg) => legWindowPriceAnalysis(routeSpec, leg, "buyAmountHuman"));
  const routeQuote = routeProfit(routeSpec, tokens, legs, "quoteBuyAmountHuman");
  const routeProtected = routeProfit(routeSpec, tokens, legs, "buyAmountHuman");
  const input = decimalNumber(routeQuote?.inputAmount || routeProtected?.inputAmount);
  const spread = decimalNumber(routeSpec?.liveSignal?.spreadPercent);
  const quoteFinal = decimalNumber(routeQuote?.finalAmount);
  const protectedFinal = decimalNumber(routeProtected?.finalAmount);
  const binanceWindowEdgeFinal = input != null && spread != null ? input * (1 + spread / 100) : null;
  const routeRateQuote = input && quoteFinal != null ? quoteFinal / input : null;
  const routeRateProtected = input && protectedFinal != null ? protectedFinal / input : null;
  const edgeRate = spread != null ? 1 + spread / 100 : null;
  const quoteTiming = classifyRateTiming(routeRateQuote, 1, edgeRate);
  const protectedTiming = classifyRateTiming(routeRateProtected, 1, edgeRate);
  return {
    available: previous.every((item) => item?.available),
    binanceExpectedModel:
      "previous_window_roundtrip_rate_is_1; next_window_edge_rate_uses_top_change_minus_bottom_change_as the optimistic trigger edge",
    binanceWindowSpreadPercent: spread == null ? null : String(spread),
    binanceWindowEdgeExpectedFinalAmount: binanceWindowEdgeFinal == null ? null : String(binanceWindowEdgeFinal),
    quoteFinalAmountBeforeSlippageFloor: routeQuote?.finalAmount ?? null,
    protectedFinalAmountAfterSlippageFloor: routeProtected?.finalAmount ?? null,
    quoteGapVsBinanceWindowEdgePercent: decimalPercentDelta(quoteFinal, binanceWindowEdgeFinal),
    protectedGapVsBinanceWindowEdgePercent: decimalPercentDelta(protectedFinal, binanceWindowEdgeFinal),
    quoteCloserToWindow: quoteTiming?.closerTo || null,
    protectedCloserToWindow: protectedTiming?.closerTo || null,
    quoteTimingVsBinanceEdge: quoteTiming?.timing || null,
    protectedTimingVsBinanceEdge: protectedTiming?.timing || null,
    threeHopVerdict:
      protectedFinal != null && input != null && protectedFinal <= input
        ? "three_hop_cow_quote_lags_expected_binance_edge_and_is_unprofitable"
        : quoteFinal != null && binanceWindowEdgeFinal != null && quoteFinal < binanceWindowEdgeFinal
          ? "three_hop_cow_quote_lags_expected_binance_edge"
          : "three_hop_cow_quote_matches_or_leads_expected_binance_edge",
    legs: previous.map((item, index) => ({
      index: legs[index]?.index ?? index + 1,
      quoteBeforeSlippageFloor: item,
      protectedAfterSlippageFloor: protectedFloor[index],
    })),
  };
}

function legSlippageControl(leg) {
  const afterNetwork = decimalNumber(leg.costAnalysis?.afterNetworkCostsBuyHuman);
  const afterSlippage = decimalNumber(leg.costAnalysis?.afterSlippageBuyHuman);
  const configured = decimalNumber(leg.configuredSlippageBps);
  if (afterNetwork == null || afterSlippage == null || afterNetwork <= 0 || configured == null) {
    return { available: false, reason: "slippage_amounts_missing" };
  }
  const observedBps = ((afterNetwork - afterSlippage) / afterNetwork) * 10000;
  const expectedFloor = afterNetwork * (1 - configured / 10000);
  const floorDiffBps = observedBps - configured;
  return {
    available: true,
    configuredSlippageBps: configured,
    suggestedSlippageBps: leg.costAnalysis?.suggestedSlippageBps ?? null,
    observedSlippageBps: String(observedBps),
    expectedMinBuyFromConfiguredBps: String(expectedFloor),
    actualMinBuyAfterSlippage: String(afterSlippage),
    floorDifferenceBps: String(floorDiffBps),
    verdict:
      Math.abs(floorDiffBps) <= 0.05
        ? "matches_configured_slippage_bps"
        : floorDiffBps > 0
          ? "protected_floor_is_wider_than_configured_slippage"
          : "protected_floor_is_tighter_than_configured_slippage",
  };
}

async function probeRoute({ routeSpec, registry, tradingSdk, flashSdk, chainId, owner, flashLoanFeePercent, slippageBps }) {
  const tokens = routeSpec.route.map((symbol) => requireToken(registry, symbol));
  const ownSteps = Array.isArray(routeSpec.ownPlan?.steps) ? routeSpec.ownPlan.steps : [];
  const legs = [];
  let amount = routeSpec.amountUnits;
  let error = null;
  for (let index = 0; index < tokens.length - 1; index += 1) {
    try {
      const result = await quoteLeg({
        tradingSdk,
        flashSdk,
        chainId,
        owner,
        sell: tokens[index],
        buy: tokens[index + 1],
        amount,
        flashLoanFeePercent,
        slippageBps,
        ownStep: ownSteps[index],
      });
      legs.push({ index: index + 1, ok: true, ...result });
      amount = result.buyAmount;
    } catch (exc) {
      const message = exc && exc.message ? exc.message : String(exc);
      legs.push({
        index: index + 1,
        ok: false,
        sellSymbol: tokens[index].symbol,
        buySymbol: tokens[index + 1].symbol,
        inputAmount: String(amount),
        inputAmountHuman: formatUnits(amount, tokens[index].decimals),
        error: message,
        ownGuard: ownStepAnalysis(ownSteps[index], { ok: false }),
      });
      error = message;
      break;
    }
  }
  const windowAnalysis = routeWindowAnalysis(routeSpec, tokens, legs);
  const analyzedLegs = legs.map((leg, index) => ({
    ...leg,
    slippageControl: leg.ok ? legSlippageControl(leg) : null,
    binanceWindowPriceAnalysis: windowAnalysis?.legs?.[index] || null,
  }));
  return {
    ok: !error,
    network: routeSpec.network,
    pair: routeSpec.pair,
    pairRank: routeSpec.pairRank,
    routeDirection: routeSpec.routeDirection || null,
    priorityReason: routeSpec.priorityReason,
    observedAt: routeSpec.observedAt,
    liveSignal: enrichLiveSignalWithQuote(routeSpec, tokens, analyzedLegs),
    route: routeSpec.route,
    amountHuman: routeSpec.amountHuman,
    amountUnits: routeSpec.amountUnits,
    ownPlanAvailable: Boolean(routeSpec.ownPlan),
    classification: classifyRoute(routeSpec, tokens, legs, error),
    error,
    profit: routeProfit(routeSpec, tokens, analyzedLegs),
    costAnalysis: routeCostAnalysis(routeSpec, tokens, analyzedLegs),
    binanceWindowAnalysis: windowAnalysis,
    singleSolverSettlement: buildSingleSolverSettlementIntent(routeSpec, tokens, analyzedLegs),
    eachLegCanGenerateFlashLoanHooks: analyzedLegs.every((leg) => leg.ok && leg.hasFlashloanMetadata && leg.hasHooksMetadata),
    ownGuardFailures: analyzedLegs.filter((leg) => leg.ownGuard?.actualVsMin === "below").length,
    legs: analyzedLegs,
  };
}

async function codeStatus(client, address) {
  if (!address) return "not_configured";
  try {
    const code = await client.getCode({ address });
    return code && code !== "0x" ? "deployed" : "empty_code";
  } catch (exc) {
    return `check_failed:${exc && exc.shortMessage ? exc.shortMessage : exc.message || String(exc)}`;
  }
}

async function main() {
  const selectedNetwork = normalizeNetwork(envFirst("COW_FLASHLOAN_PROBE_NETWORK") || "avalanche");
  const config = networkConfig(selectedNetwork);
  const owner = envFirst("COW_FLASHLOAN_PROBE_OWNER", ...config.ownerNames, "LIQUIDATION_EXECUTOR_OWNER_ADDRESS");
  if (!owner) throw new Error(`COW_FLASHLOAN_PROBE_OWNER or a ${config.network} owner env is required`);
  const rpc = envFirst(...config.envNames) || config.defaultRpc;
  if (!rpc) throw new Error(`${config.network} RPC env is required`);

  const registry = loadTokenRegistry(config.network);
  const sourceMode = tokenKey(envFirst("COW_FLASHLOAN_PROBE_SOURCE") || "manual").toLowerCase();
  const pureIntentEnabled = envBool("COW_FLASHLOAN_PURE_INTENT_ENABLED", true);
  const fromHistory = envBool("COW_FLASHLOAN_PROBE_FROM_HISTORY", false) || sourceMode === "history";
  if (fromHistory && !envBool("COW_FLASHLOAN_PROBE_ALLOW_HISTORY", false)) {
    throw new Error("history candidates are disabled for live execution tests; set COW_FLASHLOAN_PROBE_SOURCE=live and wait for a fresh signal");
  }
  const limit = Math.max(1, Number(envFirst("COW_FLASHLOAN_PROBE_LIMIT") || "1"));
  const onlyTop1 = envBool("COW_FLASHLOAN_PROBE_ONLY_TOP1", true);
  const history = fromHistory ? loadHistoryRoutes({ network: config.network, limit, onlyTop1 }) : null;
  const live =
    sourceMode === "live"
      ? pureIntentEnabled
        ? await loadLivePureIntentSpec({ network: config.network, registry })
        : await loadLiveRouteSpecs({ network: config.network, registry })
      : null;
  let routeSpecs = [];
  if (live) {
    routeSpecs = live.routes;
  } else if (history) {
    routeSpecs = pureIntentEnabled
      ? [manualPureIntentSpec(config.network, registry)]
      : history.routes.map((item) => withUnits(item, registry));
  } else {
    routeSpecs = [pureIntentEnabled ? manualPureIntentSpec(config.network, registry) : manualRouteSpec(config.network, registry)];
  }

  const client = createPublicClient({ chain: config.chain, transport: http(rpc) });
  setGlobalAdapter(new ViemAdapter({ provider: client }));
  const sdkEnv = tokenKey(envFirst("COW_FLASHLOAN_PROBE_ENV", "COW_SDK_ENV") || "prod").toLowerCase();
  if (!["prod", "staging"].includes(sdkEnv)) {
    throw new Error(`unsupported CoW SDK env: ${sdkEnv}`);
  }
  const tradingSdk = new TradingSdk({
    chainId: config.chainId,
    appCode: "flashloan-composite-arb-probe",
    env: sdkEnv,
  });
  const flashSdk = new AaveCollateralSwapSdk({ env: sdkEnv });
  const flashLoanFeePercent = Number(envFirst("COW_FLASHLOAN_FEE_PERCENT") || "0.05");
  const slippageBps = Number(
    envFirst("COW_FLASHLOAN_PROBE_SLIPPAGE_BPS", "COW_FLASHLOAN_SLIPPAGE_BPS") || "50"
  );
  if (!Number.isInteger(slippageBps) || slippageBps < 0 || slippageBps > 5000) {
    throw new Error(`slippageBps must be an integer between 0 and 5000, got ${slippageBps}`);
  }
  const deployments = sdkDeploymentSummary(config);
  const deploymentGaps = sdkDeploymentGaps(deployments);

  const routes = [];
  for (const routeSpec of routeSpecs) {
    const route = pureIntentEnabled
      ? await probePureIntent({
          routeSpec,
          registry,
          tradingSdk,
          flashSdk,
          chainId: config.chainId,
          owner,
          flashLoanFeePercent,
          slippageBps,
        })
      : await probeRoute({
          routeSpec,
          registry,
          tradingSdk,
          flashSdk,
          chainId: config.chainId,
          owner,
          flashLoanFeePercent,
          slippageBps,
        });
    routes.push(route);
    if (sourceMode === "live" && route.ok && envBool("COW_FLASHLOAN_STOP_AFTER_FIRST_QUOTED_ROUTE", false)) {
      break;
    }
  }
  const quotedRoutes = routes.filter((route) => route.ok);
  const selection = routeSelection(routes);

  const outputPath = path.resolve(envFirst("COW_FLASHLOAN_PROBE_OUTPUT") || DEFAULT_OUTPUT_PATH);
  const report = {
    ok: quotedRoutes.length > 0 && (sourceMode === "live" || routes.every((route) => route.ok)),
    generatedAt: new Date().toISOString(),
    outputPath,
    executionMode: "quote_only",
    orderSubmissionAttempted: false,
    strategyMode: pureIntentEnabled ? "pure_intent" : "three_hop_route_probe",
    pureIntentEnabled,
    pureIntentMinProfitPercent: envFirst("COW_FLASHLOAN_PURE_INTENT_MIN_PROFIT_PERCENT") || "0.618",
    pureIntentGasReserveUsdc: envFirst("COW_FLASHLOAN_PURE_INTENT_GAS_RESERVE_USDC") || "0",
    pureIntentOtherKnownCostsUsdc:
      envFirst("COW_FLASHLOAN_PURE_INTENT_OTHER_KNOWN_COSTS_USDC") || "0",
    source: live ? "live_latest_extremes" : fromHistory ? "history_jsonl_for_forensics_only" : "manual_route",
    historyPath: history?.historyPath || null,
    livePath: live?.livePath || null,
    liveStatus: live?.status || null,
    liveDiagnostic: live?.diagnostic || null,
    chainId: config.chainId,
    network: config.network,
    sdkEnv,
    owner,
    rpcSource: "configured",
    slippageBps,
    flashLoanFeePercent,
    stopAfterFirstQuotedRoute: envBool("COW_FLASHLOAN_STOP_AFTER_FIRST_QUOTED_ROUTE", false),
    routeCount: routes.length,
    quotedRouteCount: quotedRoutes.length,
    failedRouteCount: routes.length - quotedRoutes.length,
    routeSelection: selection,
    sdkDeployments: deployments,
    sdkDeploymentGaps: deploymentGaps,
    sdkDeploymentCode: {
      aavePool: await codeStatus(client, deployments.aavePool),
      adapterFactory: await codeStatus(client, deployments.adapterFactory),
      collateralAdapter: await codeStatus(client, deployments.collateralAdapter),
    },
    probeReliability: {
      perHopProbeOnly: !pureIntentEnabled,
      atomicityProof: false,
      mustUseSingleCollateralSwapOrder: true,
      expectedAtomicEvidence: [
        "single_order_uid",
        "single_settlement_tx_hash",
        "flashloan_metadata_in_app_data",
        "solver_interactions_cover_three_hop_cycle_or_better",
        "flashloan_principal_plus_fee_repaid_in_final_settlement",
      ],
      unsafeInterpretation:
        pureIntentEnabled
          ? "A pure intent quote proves only that the SDK/orderbook accepted the single intent and generated flash-loan hooks; it does not prove a solver will settle the intended hidden path atomically."
          : "Sequential getQuoteOnly/getOrderPostingSettings results prove hook generation per leg only; they do not prove X->Y->Z->X settles atomically.",
    },
    modelConclusion: {
      eachLegCanGenerateFlashLoanHooks: pureIntentEnabled
        ? routes.every((route) => route.ok && route.quote?.appData)
        : routes.every((route) => route.eachLegCanGenerateFlashLoanHooks),
      singleSolverSettlementPlanned: routes.every(
        (route) => route.singleSolverSettlement?.solverOrderCount === 1
      ),
      testedAsOneAtomicSettlement: false,
      sdkDefaultDeploymentsComplete: deploymentGaps.length === 0,
      deploymentGaps,
      reason:
        pureIntentEnabled
          ? "This probe verifies a single pure-profit intent quote and flash-loan posting settings. It does not submit or prove a solver settlement."
          : "This probe verifies quote/settings generation per SDK collateral-swap intent. It does not submit one GPv2Settlement.settle(...) containing a multi-trade settlement, so it cannot prove all hops settle atomically.",
      importantBoundary:
        "If three conversions are posted as three ordinary CoW orders, they can fill independently. Atomic all-or-none behavior requires the trades/interactions to be included in the same settlement transaction.",
    },
    routes,
  };
  fs.mkdirSync(path.dirname(outputPath), { recursive: true });
  fs.writeFileSync(outputPath, `${JSON.stringify(report, null, 2)}\n`);
  console.log(JSON.stringify(report, null, 2));
}

main().catch((error) => {
  const report = {
    ok: false,
    generatedAt: new Date().toISOString(),
    error: error && error.message ? error.message : String(error),
  };
  const outputPath = path.resolve(envFirst("COW_FLASHLOAN_PROBE_OUTPUT") || DEFAULT_OUTPUT_PATH);
  fs.mkdirSync(path.dirname(outputPath), { recursive: true });
  fs.writeFileSync(outputPath, `${JSON.stringify(report, null, 2)}\n`);
  console.error(JSON.stringify(report, null, 2));
  process.exitCode = 1;
});
