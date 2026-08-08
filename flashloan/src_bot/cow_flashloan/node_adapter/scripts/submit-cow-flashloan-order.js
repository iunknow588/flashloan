const fs = require("fs");
const path = require("path");
const dotenv = require("dotenv");
const { createPublicClient, http } = require("viem");
const { privateKeyToAccount } = require("viem/accounts");
const viemChains = require("viem/chains");
const { setGlobalAdapter } = require("@cowprotocol/sdk-common");
const { ViemAdapter } = require("@cowprotocol/sdk-viem-adapter");
const { SupportedChainId } = require("@cowprotocol/sdk-config");
const { OrderKind } = require("@cowprotocol/sdk-order-book");
const { TradingSdk } = require("@cowprotocol/sdk-trading");
const {
  AaveCollateralSwapSdk,
  AaveFlashLoanType,
} = require("@cowprotocol/sdk-flash-loans");

const NODE_ADAPTER_ROOT = path.resolve(__dirname, "..");
const SRC_BOT_ROOT = path.resolve(NODE_ADAPTER_ROOT, "../..");
const DEFAULT_INPUT_PATH = process.env.COW_SUBMISSION_INPUT_PATH || "";
const TEN = 10n;

dotenv.config({ path: path.resolve(NODE_ADAPTER_ROOT, ".env") });
dotenv.config({ path: path.resolve(SRC_BOT_ROOT, ".env"), override: false });

const NETWORKS = {
  ethereum: {
    chainId: SupportedChainId.MAINNET,
    chain: viemChains.mainnet,
    envNames: ["ETHEREUM_RPC_URL", "MAINNET_RPC_URL", "RPC_URL"],
    ownerNames: ["COW_OWNER_ETHEREUM", "COW_OWNER_MAINNET"],
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
  polygon: {
    chainId: SupportedChainId.POLYGON,
    chain: viemChains.polygon,
    envNames: ["POLYGON_RPC_URL", "MATIC_RPC_URL", "RPC_URL"],
    ownerNames: ["COW_OWNER_POLYGON"],
    defaultRpc: "https://polygon-rpc.com",
  },
  base: {
    chainId: SupportedChainId.BASE,
    chain: viemChains.base,
    envNames: ["BASE_RPC_URL", "RPC_URL"],
    ownerNames: ["COW_OWNER_BASE"],
  },
};

const NETWORK_ALIASES = {
  mainnet: "ethereum",
  eth: "ethereum",
  avax: "avalanche",
  bsc: "bnb",
  binance: "bnb",
  matic: "polygon",
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

function normalizedPrivateKey(value) {
  const raw = String(value || "").trim();
  if (!raw) return "";
  const key = raw.startsWith("0x") ? raw : `0x${raw}`;
  return /^0x[0-9a-fA-F]{64}$/.test(key) ? key : "";
}

function loadJson(file) {
  return JSON.parse(fs.readFileSync(file, "utf8"));
}

function normalizeNetwork(value) {
  const key = String(value || "avalanche").trim().toLowerCase().replace(/-/g, "_");
  return NETWORK_ALIASES[key] || key;
}

function networkConfig(value) {
  const network = normalizeNetwork(value);
  const config = NETWORKS[network];
  if (!config) {
    throw new Error(`unsupported live CoW submission network: ${value || network}`);
  }
  return { network, ...config };
}

function tokenKey(symbol) {
  return String(symbol || "").trim().toUpperCase();
}

function stripUsdt(symbol) {
  return tokenKey(symbol).replace(/USDT$/, "");
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
  return registry;
}

function requireToken(registry, symbol) {
  const wanted = tokenKey(symbol);
  const token = registry.get(wanted);
  if (!token || !token.address) {
    throw new Error(`token not found in CoW/Aave cache for this network: ${symbol}`);
  }
  return token;
}

function bigintFrom(value) {
  if (typeof value === "bigint") return value;
  if (value == null || value === "") return 0n;
  return BigInt(String(value));
}

function parseHumanUnits(value, decimals) {
  const text = String(value ?? "").trim();
  if (!text) return 0n;
  const [wholeRaw, fractionRaw = ""] = text.split(".");
  const whole = wholeRaw ? BigInt(wholeRaw) : 0n;
  const paddedFraction = (fractionRaw + "0".repeat(Number(decimals))).slice(0, Number(decimals));
  const fraction = paddedFraction ? BigInt(paddedFraction) : 0n;
  return whole * TEN ** BigInt(decimals) + fraction;
}

function formatUnits(value, decimals) {
  const units = bigintFrom(value);
  const scale = TEN ** BigInt(decimals);
  const whole = units / scale;
  const fraction = units % scale;
  if (fraction === 0n) return whole.toString();
  const fractionText = fraction.toString().padStart(Number(decimals), "0").replace(/0+$/, "");
  return `${whole.toString()}.${fractionText}`;
}

function percentageOfUnits(units, percentHuman) {
  const percent = Number(percentHuman || 0);
  if (!Number.isFinite(percent) || percent <= 0) return 0n;
  return (BigInt(units) * BigInt(Math.round(percent * 10000))) / 1000000n;
}

function routeConstraintsFromPayload(intent, quotePayload, tokenRegistry) {
  const constraints = intent.route_hop_constraints || {};
  const plan = quotePayload.binance_execution_plan || {};
  const sourceHops = Array.isArray(constraints.hops)
    ? constraints.hops
    : Array.isArray(plan.steps)
      ? plan.steps
      : [];
  const hops = [];
  for (let index = 0; index < sourceHops.length; index += 1) {
    const item = sourceHops[index] || {};
    const sdk = item.cow_sdk_parameters || {};
    const sellSymbol = tokenKey(item.from_symbol || item.sell_symbol || sdk.sell_token_symbol);
    const buySymbol = tokenKey(item.to_symbol || item.buy_symbol || sdk.buy_token_symbol);
    const sellAmountHuman = String(
      item.sell_amount_before_fee ??
      sdk.sell_amount_before_fee ??
      item.query_sell_amount_before_fee ??
      item.input_amount ??
      ""
    );
    const minBuyAmountHuman = String(
      item.min_buy_amount_after_fee ??
      sdk.min_buy_amount_after_fee ??
      item.min_output_amount ??
      ""
    );
    if (!sellSymbol || !buySymbol || !sellAmountHuman || !minBuyAmountHuman) continue;
    const sellToken = requireToken(tokenRegistry, sellSymbol);
    const buyToken = requireToken(tokenRegistry, buySymbol);
    const sellAmountUnits = parseHumanUnits(sellAmountHuman, sellToken.decimals);
    const minBuyAmountUnits = parseHumanUnits(minBuyAmountHuman, buyToken.decimals);
    const targetBuyAmountHuman = item.target_buy_amount_after_fee ?? sdk.target_buy_amount_after_fee ?? item.target_output_amount ?? null;
    hops.push({
      hop: item.hop || item.step || index + 1,
      sellSymbol,
      buySymbol,
      sellToken,
      buyToken,
      sellAmountHuman,
      minBuyAmountHuman,
      targetBuyAmountHuman: targetBuyAmountHuman == null ? null : String(targetBuyAmountHuman),
      sellAmountUnits,
      minBuyAmountUnits,
      rule: item.rule || item.selection_rule || item.price_compare_rule || null,
      selectedTargetSource: item.selected_target_source || sdk.selected_target_source || null,
      selectedAcceptableSource: item.selected_acceptable_source || sdk.selected_acceptable_source || null,
    });
  }
  return {
    enabled: Boolean(constraints.enabled || hops.length),
    route: constraints.route || intent.route_path || plan.route || [],
    hops,
  };
}

function resolveOrderSigner(owner) {
  const privateKey = normalizedPrivateKey(
    envFirst(
      "COW_ORDER_SIGNER_PRIVATE_KEY",
      "COW_FLASHLOAN_PROBE_PRIVATE_KEY",
      "LIQUIDATION_EXECUTION_PRIVATE_KEY",
      "LIQUIDATION_SELF_FUNDED_PRIVATE_KEY"
    )
  );
  if (!privateKey) {
    return {
      available: false,
      reason: "signer_private_key_missing",
      signerAddress: null,
    };
  }
  const account = privateKeyToAccount(privateKey);
  const signerAddress = account.address;
  if (owner && signerAddress.toLowerCase() !== String(owner).toLowerCase()) {
    return {
      available: false,
      reason: "signer_owner_mismatch",
      signerAddress,
      owner,
    };
  }
  return {
    available: true,
    reason: "signer_ready",
    signerAddress,
    privateKey,
  };
}

function jsonFriendly(value) {
  return JSON.parse(
    JSON.stringify(value, (_key, item) => (typeof item === "bigint" ? item.toString() : item))
  );
}

function pureIntentAppData({ slippageBps }) {
  return {
    metadata: {
      quote: { slippageBips: slippageBps },
      orderClass: { orderClass: "limit" },
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

async function submitOne() {
  const inputPath = process.argv[2] || DEFAULT_INPUT_PATH;
  if (!inputPath) {
    throw new Error("submission input path is required");
  }
  const input = loadJson(inputPath);
  const quotePayload = input.quote_payload || {};
  const opportunity = input.opportunity || {};
  const intent = quotePayload.cow_flashloan_intent || {};
  const network = networkConfig(quotePayload.cow_network || opportunity.network || "avalanche");
  const rpc = envFirst(...network.envNames) || network.defaultRpc;
  if (!rpc) throw new Error(`${network.network} RPC env is required`);

  const signer = resolveOrderSigner(quotePayload.owner || opportunity.owner);
  if (!signer.available) {
    return {
      ok: false,
      submitted: false,
      status: "submission_failed",
      blockedReason: signer.reason,
      error: signer.reason,
      owner: quotePayload.owner || opportunity.owner || null,
      signerAddress: signer.signerAddress,
      network: network.network,
      chainId: network.chainId,
      quoteCall: null,
      postingCall: null,
      submitCall: null,
    };
  }

  const tokenRegistry = loadTokenRegistry(network.network);
  const usdc = requireToken(tokenRegistry, "USDC");
  const routeConstraints = routeConstraintsFromPayload(intent, quotePayload, tokenRegistry);
  const principalHuman = String(intent.initial_amount || "1000");
  const principalUnits = parseHumanUnits(principalHuman, usdc.decimals);
  const minFinalAmountHuman = String(intent.min_final_amount || intent.cow_sdk_order_intent?.minimum_final_buy_amount_after_all_costs || "0");
  const minFinalAmountUnits = parseHumanUnits(minFinalAmountHuman, usdc.decimals);
  const flashLoanFeePercent = Number(envFirst("COW_FLASHLOAN_FEE_PERCENT") || "0.05");
  const flashLoanFeeBps = Math.round(flashLoanFeePercent * 100);
  const slippageBps = Number(envFirst("COW_FLASHLOAN_PROBE_SLIPPAGE_BPS", "COW_FLASHLOAN_SLIPPAGE_BPS") || "50");
  const { flashLoanFeeAmount } = new AaveCollateralSwapSdk({ env: envFirst("COW_FLASHLOAN_PROBE_ENV", "COW_SDK_ENV") || "prod" }).calculateFlashLoanAmounts({
    sellAmount: principalUnits,
    flashLoanFeeBps,
  });
  const targetBuyUnits = minFinalAmountUnits || principalUnits;
  const validTo = Math.ceil(Date.now() / 1000) + 300;
  const quoteParams = {
    chainId: network.chainId,
    owner: signer.signerAddress,
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
  });
  if (routeConstraints.enabled) {
    customAppData.metadata.flashloanCompositeRoute = {
      route: routeConstraints.route,
      mode: "single_intent_reference_amounts",
      hopCount: routeConstraints.hops.length,
      hops: routeConstraints.hops.map((hop) => ({
        hop: hop.hop,
        path: [hop.sellSymbol, hop.buySymbol],
        sellAmountBeforeFee: hop.sellAmountHuman,
        minBuyAmountAfterFee: hop.minBuyAmountHuman,
        targetBuyAmountAfterFee: hop.targetBuyAmountHuman,
        selectedTargetSource: hop.selectedTargetSource,
        selectedAcceptableSource: hop.selectedAcceptableSource,
      })),
    };
  }
  const client = createPublicClient({ chain: network.chain, transport: http(rpc) });
  setGlobalAdapter(new ViemAdapter({ provider: client }));
  const sdkEnv = String(envFirst("COW_FLASHLOAN_PROBE_ENV", "COW_SDK_ENV") || "prod").toLowerCase();
  const tradingSdk = new TradingSdk({
    chainId: network.chainId,
    appCode: "flashloan-composite-arb-probe",
    env: sdkEnv,
  });
  const flashSdk = new AaveCollateralSwapSdk({ env: sdkEnv });

  const startedAt = new Date().toISOString();
  const quoteCall = {
    method: "tradingSdk.getQuote",
    startedAt,
    finishedAt: null,
    ok: false,
    error: null,
    input: {
      chainId: network.chainId,
      owner: signer.signerAddress,
      kind: quoteParams.kind,
      sellToken: quoteParams.sellToken,
      buyToken: quoteParams.buyToken,
      amount: quoteParams.amount,
      slippageBps: quoteParams.slippageBps,
      validTo: quoteParams.validTo,
      routeReferenceAmountMode: routeConstraints.enabled ? "signed_app_data" : "none",
    },
    routeReferenceAmounts: {
      enabled: routeConstraints.enabled,
      route: routeConstraints.route,
      hopCount: routeConstraints.hops.length,
      hops: routeConstraints.hops.map((hop) => ({
        hop: hop.hop,
        path: `${hop.sellSymbol} -> ${hop.buySymbol}`,
        sellAmountBeforeFee: hop.sellAmountHuman,
        minBuyAmountAfterFee: hop.minBuyAmountHuman,
        targetBuyAmountAfterFee: hop.targetBuyAmountHuman,
      })),
    },
    result: null,
  };
  let quoteAndPost;
  try {
    quoteAndPost = await tradingSdk.getQuote(
      {
        ...quoteParams,
        signer: signer.privateKey,
        owner: signer.signerAddress,
      },
      {
        appData: customAppData,
        allowIntermediateEqSellToken: true,
      }
    );
    quoteCall.ok = true;
    quoteCall.finishedAt = new Date().toISOString();
    quoteCall.result = {
      quoteResultKeys: Object.keys(quoteAndPost?.quoteResults || {}),
      hasPostSwapOrderFromQuote: typeof quoteAndPost?.postSwapOrderFromQuote === "function",
      quoteResults: jsonFriendly(quoteAndPost?.quoteResults || null),
    };
  } catch (error) {
    const message = error && error.message ? error.message : String(error);
    quoteCall.finishedAt = new Date().toISOString();
    quoteCall.error = message;
    return {
      ok: false,
      submitted: false,
      status: "quote_failed",
      blockedReason: "quote_failed",
      error: message,
      owner: signer.signerAddress,
      network: network.network,
      chainId: network.chainId,
      quoteCall,
      postingCall: null,
      submitCall: null,
    };
  }

  const quoteResults = quoteAndPost?.quoteResults || {};
  const orderToSign = quoteResults.orderToSign || null;
  const quoteAmounts = quoteResults.amountsAndCosts || {};
  const afterSlippageBuyUnits = bigintFrom(quoteAmounts.afterSlippage?.buyAmount ?? orderToSign?.buyAmount ?? 0n);
  const quotedNetworkFeeBuyUnits = bigintFrom(quoteAmounts.costs?.networkFee?.amountInBuyCurrency ?? 0n);
  const requiredSellUnits = bigintFrom(orderToSign?.sellAmount ?? 0n);
  const analysis = {
    principalUnits: String(principalUnits),
    afterSlippageBuyUnits: String(afterSlippageBuyUnits),
    quotedNetworkFeeBuyUnits: String(quotedNetworkFeeBuyUnits),
    flashLoanFeeUnits: String(flashLoanFeeAmount),
    requiredSellUnits: String(requiredSellUnits),
    minimumFinalAmountUnits: String(minFinalAmountUnits),
    sellBudgetPassed: requiredSellUnits <= principalUnits,
    profitBudgetMet: afterSlippageBuyUnits >= minFinalAmountUnits,
  };
  if (!analysis.sellBudgetPassed || !analysis.profitBudgetMet) {
    const blockedReason = !analysis.profitBudgetMet
      ? "intent_min_final_amount_not_met"
      : "required_sell_exceeds_principal";
    return {
      ok: false,
      submitted: false,
      status: "submission_blocked",
      blockedReason,
      error: blockedReason,
      owner: signer.signerAddress,
      network: network.network,
      chainId: network.chainId,
      quoteCall,
      postingCall: null,
      submitCall: null,
      analysis,
    };
  }
  if (!orderToSign) {
    return {
      ok: false,
      submitted: false,
      status: "submission_failed",
      blockedReason: "submission_quote_missing_order",
      error: "submission_quote_missing_order",
      owner: signer.signerAddress,
      network: network.network,
      chainId: network.chainId,
      quoteCall,
      postingCall: null,
      submitCall: null,
      analysis,
    };
  }

  let posting;
  try {
    posting = await flashSdk.getOrderPostingSettings(
      AaveFlashLoanType.CollateralSwap,
      quoteParams,
      {
        flashLoanAmount: principalUnits,
        orderToSign,
      }
    );
  } catch (error) {
    const message = error && error.message ? error.message : String(error);
    return {
      ok: false,
      submitted: false,
      status: "submission_failed",
      blockedReason: "posting_settings_failed",
      error: message,
      owner: signer.signerAddress,
      network: network.network,
      chainId: network.chainId,
      quoteCall,
      postingCall: null,
      submitCall: null,
      analysis,
    };
  }

  const postingCall = {
    method: "flashSdk.getOrderPostingSettings",
    startedAt,
    finishedAt: new Date().toISOString(),
    ok: true,
    error: null,
    input: {
      flashLoanAmount: String(principalUnits),
      chainId: network.chainId,
      owner: signer.signerAddress,
    },
    result: jsonFriendly(posting || null),
  };
  const swapSettings = {
    ...posting.swapSettings,
    appData: mergeFlashloanAppData(customAppData, posting.swapSettings?.appData),
  };
  if (envBool("COW_SUBMISSION_DRY_RUN", false)) {
    return {
      ok: true,
      submitted: false,
      status: "submission_dry_run",
      blockedReason: "dry_run_before_order_post",
      error: null,
      owner: signer.signerAddress,
      network: network.network,
      chainId: network.chainId,
      orderId: null,
      txHash: null,
      quoteCall,
      postingCall,
      submitCall: {
        method: "quoteAndPost.postSwapOrderFromQuote",
        skipped: true,
        reason: "COW_SUBMISSION_DRY_RUN",
      },
      analysis,
      finishedAt: new Date().toISOString(),
    };
  }
  const submitStartedAt = new Date().toISOString();
  let submitResult = null;
  let submitError = null;
  try {
    submitResult = await quoteAndPost.postSwapOrderFromQuote(swapSettings);
  } catch (error) {
    submitError = error;
  }
  const submitCall = {
    method: "quoteAndPost.postSwapOrderFromQuote",
    startedAt: submitStartedAt,
    finishedAt: new Date().toISOString(),
    ok: !submitError,
    error: submitError ? (submitError?.message || String(submitError)) : null,
    result: jsonFriendly(submitResult || null),
  };
  if (submitError || !submitResult?.orderId) {
    return {
      ok: false,
      submitted: false,
      status: "submission_failed",
      blockedReason: submitError ? "order_submission_failed" : "order_submission_returned_no_order_id",
      error: submitError ? (submitError?.message || String(submitError)) : "order_submission_returned_no_order_id",
      owner: signer.signerAddress,
      network: network.network,
      chainId: network.chainId,
      orderId: submitResult?.orderId || null,
      txHash: submitResult?.txHash || null,
      quoteCall,
      postingCall,
      submitCall,
      analysis,
    };
  }
  return {
    ok: true,
    submitted: true,
    status: "submitted_success",
    blockedReason: null,
    error: null,
    owner: signer.signerAddress,
    network: network.network,
    chainId: network.chainId,
    orderId: submitResult.orderId || null,
    txHash: submitResult.txHash || null,
    signingScheme: submitResult.signingScheme || null,
    quoteCall,
    postingCall,
    submitCall,
    analysis,
    finishedAt: new Date().toISOString(),
  };
}

async function main() {
  const result = await submitOne();
  process.stdout.write(`${JSON.stringify(result, null, 2)}\n`);
}

main().catch((error) => {
  const report = {
    ok: false,
    generatedAt: new Date().toISOString(),
    error: error && error.message ? error.message : String(error),
  };
  process.stdout.write(`${JSON.stringify(report, null, 2)}\n`);
  process.exitCode = 1;
});
