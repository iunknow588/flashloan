const fs = require("fs");
const path = require("path");
const dotenv = require("dotenv");
const { createPublicClient, http } = require("viem");
const viemChains = require("viem/chains");
const { setGlobalAdapter } = require("@cowprotocol/sdk-common");
const { ViemAdapter } = require("@cowprotocol/sdk-viem-adapter");
const { SupportedChainId } = require("@cowprotocol/sdk-config");
const { TradingSdk } = require("@cowprotocol/sdk-trading");
const {
  AAVE_ADAPTER_FACTORY,
  AAVE_HOOK_ADAPTER_PER_TYPE,
  AAVE_POOL_ADDRESS,
  AaveCollateralSwapSdk,
  AaveFlashLoanType,
} = require("@cowprotocol/sdk-flash-loans");

const CONTRACTS_ROOT = path.resolve(__dirname, "..");
const APP_RUNTIME_ROOT = path.resolve(CONTRACTS_ROOT, "../../flashloan/app_runtime");
const DEFAULT_OUTPUT_PATH = path.resolve(CONTRACTS_ROOT, "deployments/cow-flashloans-probe.json");
const DEFAULT_HISTORY_PATH = path.resolve(APP_RUNTIME_ROOT, "runtime/logs/cow_execution_attempts.jsonl");
const DEFAULT_LIVE_EXTREMES_PATH = path.resolve(APP_RUNTIME_ROOT, "runtime/state/latest_extremes.json");
const TEN = 10n;

dotenv.config({ path: path.resolve(CONTRACTS_ROOT, ".env") });
dotenv.config({ path: path.resolve(APP_RUNTIME_ROOT, ".env"), override: false });

const {
  NETWORKS,
  NETWORK_ALIASES,
  envFirst,
  envBool,
  normalizeNetwork,
  networkConfig,
} = require("./probe-cow-flashloans/config");

function loadJson(file) {
  return JSON.parse(fs.readFileSync(file, "utf8"));
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

const {
  tokenKey,
  stripUsdt,
  isAddress,
  registerToken,
  loadTokenOverrides,
  loadTokenRegistry,
  requireToken,
} = require("./probe-cow-flashloans/token-registry");
const {
  parseHumanUnits,
  formatUnits,
  numberOrNull,
} = require("./probe-cow-flashloans/decimal-math");

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

const {
  readJsonl,
  routeFromAttempt,
  loadHistoryRoutes,
} = require("./probe-cow-flashloans/route-loader");

const {
  ownStepAnalysis,
  legCostAnalysis,
  quoteLeg,
  classifyRoute,
  enrichLiveSignalWithQuote,
  routeProfit,
  routeSelection,
  routeCostAnalysis,
} = require("./probe-cow-flashloans/quote-analysis");

const {
  stablePrice,
  priceRowsFromRoute,
  tokenWindowPrice,
  binanceRateForWindow,
  classifyRateTiming,
  legWindowPriceAnalysis,
  routeWindowAnalysis,
  legSlippageControl,
} = require("./probe-cow-flashloans/window-analysis");

const {
  loadLivePureIntentSpec,
  manualPureIntentSpec,
} = require("./probe-cow-flashloans/pure-intent");

const {
  probePureIntent,
} = require("./probe-cow-flashloans/probe-pure-intent");

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
