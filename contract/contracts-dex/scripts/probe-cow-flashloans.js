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
  return {
    model: "single_flashloan_router_call_with_single_cow_solver_settlement",
    borrowedAsset: tokens[0]?.symbol || route[0] || null,
    repaidAsset: tokens[tokens.length - 1]?.symbol || route[route.length - 1] || null,
    route,
    hopCount,
    requiredMinimumHopCount: 3,
    threeHopRoute,
    closedCycle,
    flashLoanCount: supported ? 1 : 0,
    solverOrderCount: supported ? 1 : 0,
    settlementTransactionCount: supported ? 1 : 0,
    independentPerHopOrderCount: 0,
    diagnosticQuoteLegCount: legs.length,
    submissionMode: supported
      ? "one_flashLoanAndSettle_call"
      : "blocked_requires_closed_three_hop_solver_path",
    proofStatus: "not_proven_quote_only",
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
  const top = eligible.filter((row) => row.change_percent > 0).sort((a, b) => b.change_percent - a.change_percent)[0];
  const bottom = eligible.filter((row) => row.change_percent < 0).sort((a, b) => a.change_percent - b.change_percent)[0];
  if (!top || !bottom) {
    return {
      routeSpec: null,
      reason: "live_top_bottom_not_available",
      eligibleCount: eligible.length,
      observedAt: extremes.observed_at,
      freshnessSeconds,
    };
  }
  const spread = top.change_percent - bottom.change_percent;
  if (spread <= minSpreadPercent) {
    return {
      routeSpec: null,
      reason: "live_spread_below_min",
      spreadPercent: spread,
      minSpreadPercent,
      observedAt: extremes.observed_at,
      freshnessSeconds,
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
    if (last.routeSpec) return { livePath, status: last.reason, routes: [withUnits(last.routeSpec, registry)] };
    if (waitSeconds <= 0) break;
    await sleep(pollSeconds * 1000);
  }
  return { livePath, status: last.reason, routes: [], diagnostic: last };
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

async function quoteLeg({ tradingSdk, flashSdk, chainId, owner, sell, buy, amount, flashLoanFeePercent, ownStep }) {
  const tradeParameters = {
    kind: OrderKind.SELL,
    owner,
    sellToken: sell.address,
    sellTokenDecimals: sell.decimals,
    buyToken: buy.address,
    buyTokenDecimals: buy.decimals,
    amount: String(amount),
    validFor: 300,
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
    signedSellAmountAfterFlashLoanFee: quoteParams.amount,
    signedSellAmountAfterFlashLoanFeeHuman: formatUnits(quoteParams.amount, sell.decimals),
    flashLoanFeeAmount: bigintText(quoteParams.flashLoanFeeAmount),
    flashLoanFeeAmountHuman: formatUnits(quoteParams.flashLoanFeeAmount, sell.decimals),
    buyAmount: quote.orderToSign.buyAmount,
    buyAmountHuman: formatUnits(quote.orderToSign.buyAmount, buy.decimals),
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

function routeProfit(routeSpec, tokens, legs) {
  if (!legs.length) return null;
  const first = tokens[0];
  const last = tokens[tokens.length - 1];
  if (first.address.toLowerCase() !== last.address.toLowerCase()) return null;
  const input = decimalNumber(formatUnits(routeSpec.amountUnits, first.decimals));
  const output = decimalNumber(legs[legs.length - 1].buyAmountHuman);
  if (input == null || output == null || input === 0) return null;
  return {
    inputAmount: String(input),
    finalAmount: String(output),
    deltaAmount: String(output - input),
    deltaPercent: String(((output - input) / input) * 100),
    symbol: first.symbol,
  };
}

async function probeRoute({ routeSpec, registry, tradingSdk, flashSdk, chainId, owner, flashLoanFeePercent }) {
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
  return {
    ok: !error,
    network: routeSpec.network,
    pair: routeSpec.pair,
    pairRank: routeSpec.pairRank,
    priorityReason: routeSpec.priorityReason,
    observedAt: routeSpec.observedAt,
    route: routeSpec.route,
    amountHuman: routeSpec.amountHuman,
    amountUnits: routeSpec.amountUnits,
    ownPlanAvailable: Boolean(routeSpec.ownPlan),
    classification: classifyRoute(routeSpec, tokens, legs, error),
    error,
    profit: routeProfit(routeSpec, tokens, legs),
    singleSolverSettlement: buildSingleSolverSettlementIntent(routeSpec, tokens, legs),
    eachLegCanGenerateFlashLoanHooks: legs.every((leg) => leg.ok && leg.hasFlashloanMetadata && leg.hasHooksMetadata),
    ownGuardFailures: legs.filter((leg) => leg.ownGuard?.actualVsMin === "below").length,
    legs,
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
  const fromHistory = envBool("COW_FLASHLOAN_PROBE_FROM_HISTORY", false) || sourceMode === "history";
  if (fromHistory && !envBool("COW_FLASHLOAN_PROBE_ALLOW_HISTORY", false)) {
    throw new Error("history candidates are disabled for live execution tests; set COW_FLASHLOAN_PROBE_SOURCE=live and wait for a fresh signal");
  }
  const limit = Math.max(1, Number(envFirst("COW_FLASHLOAN_PROBE_LIMIT") || "1"));
  const onlyTop1 = envBool("COW_FLASHLOAN_PROBE_ONLY_TOP1", true);
  const history = fromHistory ? loadHistoryRoutes({ network: config.network, limit, onlyTop1 }) : null;
  const live = sourceMode === "live" ? await loadLiveRouteSpecs({ network: config.network, registry }) : null;
  let routeSpecs = [];
  if (live) {
    routeSpecs = live.routes;
  } else if (history) {
    routeSpecs = history.routes.map((item) => withUnits(item, registry));
  } else {
    routeSpecs = [manualRouteSpec(config.network, registry)];
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
  const deployments = sdkDeploymentSummary(config);
  const deploymentGaps = sdkDeploymentGaps(deployments);

  const routes = [];
  for (const routeSpec of routeSpecs) {
    routes.push(
      await probeRoute({
        routeSpec,
        registry,
        tradingSdk,
        flashSdk,
        chainId: config.chainId,
        owner,
        flashLoanFeePercent,
      })
    );
  }

  const report = {
    ok: routes.length > 0 && routes.every((route) => route.ok),
    generatedAt: new Date().toISOString(),
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
    routeCount: routes.length,
    sdkDeployments: deployments,
    sdkDeploymentGaps: deploymentGaps,
    sdkDeploymentCode: {
      aavePool: await codeStatus(client, deployments.aavePool),
      adapterFactory: await codeStatus(client, deployments.adapterFactory),
      collateralAdapter: await codeStatus(client, deployments.collateralAdapter),
    },
    probeReliability: {
      perHopProbeOnly: true,
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
        "Sequential getQuoteOnly/getOrderPostingSettings results prove hook generation per leg only; they do not prove X->Y->Z->X settles atomically.",
    },
    modelConclusion: {
      eachLegCanGenerateFlashLoanHooks: routes.every((route) => route.eachLegCanGenerateFlashLoanHooks),
      singleSolverSettlementPlanned: routes.every(
        (route) => route.singleSolverSettlement?.solverOrderCount === 1
      ),
      testedAsOneAtomicSettlement: false,
      sdkDefaultDeploymentsComplete: deploymentGaps.length === 0,
      deploymentGaps,
      reason:
        "This probe verifies quote/settings generation per SDK collateral-swap intent. It does not submit one GPv2Settlement.settle(...) containing a multi-trade settlement, so it cannot prove all hops settle atomically.",
      importantBoundary:
        "If three conversions are posted as three ordinary CoW orders, they can fill independently. Atomic all-or-none behavior requires the trades/interactions to be included in the same settlement transaction.",
    },
    routes,
  };
  const outputPath = path.resolve(envFirst("COW_FLASHLOAN_PROBE_OUTPUT") || DEFAULT_OUTPUT_PATH);
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
