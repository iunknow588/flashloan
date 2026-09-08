const hre = require("hardhat");
const fs = require("fs");
const path = require("path");
const {
  appendJsonl,
  evidencePaths,
  networkContext,
  sanitizeError,
  writeJson,
} = require("./fuji-evidence");

function requireEnv(name) {
  const value = process.env[name];
  if (!value || !value.trim() || value === "0x...") {
    throw new Error(`${name} is required`);
  }
  return value.trim();
}

function optionalEnv(...names) {
  for (const name of names) {
    const value = process.env[name];
    if (value && value.trim() && value !== "0x...") {
      return value.trim();
    }
  }
  return "";
}

function envBigInt(name, defaultValue) {
  const value = process.env[name];
  return value && value.trim() ? BigInt(value.trim()) : defaultValue;
}

function envNumber(name, defaultValue) {
  const value = process.env[name];
  return value && value.trim() ? Number(value.trim()) : defaultValue;
}

function signalPath() {
  return path.resolve(
    process.cwd(),
    process.env.DYNAMIC_SIGNAL_FILE || "../flashloan/srcs_dex/runtime/state/latest_executable_signal.json"
  );
}

function tokenEnvNames(symbol) {
  const normalized = String(symbol).trim().toUpperCase().replace(/[^A-Z0-9]/g, "_");
  const base = normalized.endsWith("USDT") ? normalized.slice(0, -4) : normalized;
  return [
    `DYNAMIC_TOKEN_${normalized}`,
    `DYNAMIC_${normalized}_TOKEN`,
    `TOKEN_${normalized}_ADDRESS`,
    `DYNAMIC_TOKEN_${base}`,
    `DYNAMIC_${base}_TOKEN`,
    `TOKEN_${base}_ADDRESS`,
  ];
}

function cachedTokenAddressFor(symbol) {
  const cacheFile = path.resolve(
    process.cwd(),
    process.env.AAVE_RESERVE_CACHE_FILE || "../flashloan/srcs_dex/runtime/cache/aave_reserve_assets.json"
  );
  if (!fs.existsSync(cacheFile)) {
    return "";
  }
  const cache = JSON.parse(fs.readFileSync(cacheFile, "utf8"));
  const target = String(symbol).trim().toUpperCase();
  for (const asset of cache.assets || []) {
    if (String(asset.binance_symbol || "").toUpperCase() === target && asset.token_address) {
      return asset.token_address;
    }
  }
  return "";
}

function tokenAddressFor(symbol) {
  const address = optionalEnv(...tokenEnvNames(symbol));
  if (address) {
    return address;
  }
  const cached = cachedTokenAddressFor(symbol);
  if (cached) {
    return cached;
  }
  throw new Error(`missing token address env/cache for ${symbol}: ${tokenEnvNames(symbol).join(" or ")}`);
}

function readCandidate() {
  const file = signalPath();
  const payload = JSON.parse(fs.readFileSync(file, "utf8"));
  const candidate = payload.signal || payload.quoted_candidate || payload.candidate || payload;
  if (!candidate.x_symbol || !candidate.y_symbol) {
    throw new Error("candidate must contain x_symbol and y_symbol");
  }
  return candidate;
}

function signalId(candidate) {
  return candidate.signal_id || candidate.signalId || candidate.id || candidate.observed_at || null;
}

function signalSummary(candidate) {
  return {
    signalId: signalId(candidate),
    observedAt: candidate.observed_at || candidate.observedAt || null,
    xSymbol: candidate.x_symbol,
    ySymbol: candidate.y_symbol,
    xChangePercent: candidate.x_change_percent ?? candidate.a_change_percent ?? null,
    yChangePercent: candidate.y_change_percent ?? candidate.b_change_percent ?? null,
    executableSignal: candidate.executable_signal ?? null,
    dexQuoteVerified: candidate.dex_quote_verified ?? null,
    netProfitVerified: candidate.net_profit_verified ?? null,
    minProfit: candidate.min_profit ?? candidate.minProfit ?? null,
  };
}

async function buildRequest(candidate) {
  const router = optionalEnv("DYNAMIC_DEX_ROUTER", "FUJI_DEX_ROUTER") || requireEnv("FUJI_DEX_ROUTER");
  const usdc = optionalEnv("DYNAMIC_USDC", "FUJI_USDC") || requireEnv("FUJI_USDC");
  const defaultAmount = envBigInt("DYNAMIC_BORROW_AMOUNT_UNITS", 1000000000000000000n);
  const amountX = envBigInt("DYNAMIC_AMOUNT_X_UNITS", defaultAmount);
  const amountY = envBigInt("DYNAMIC_AMOUNT_Y_UNITS", defaultAmount);
  const premiumBps = envBigInt("DYNAMIC_AAVE_PREMIUM_BPS", 5n);
  const minProfitValueUsdc = envBigInt("DYNAMIC_MIN_PROFIT_USDC_UNITS", 1n);
  const slippageBps = envBigInt("DYNAMIC_SLIPPAGE_BPS", 50n);
  const deadlineSeconds = envNumber("DYNAMIC_DEADLINE_SECONDS", 60);
  const latest = await hre.ethers.provider.getBlock("latest");

  return {
    xToken: tokenAddressFor(candidate.x_symbol),
    yToken: tokenAddressFor(candidate.y_symbol),
    usdc,
    router,
    amountX,
    amountY,
    premiumBps,
    minProfitValueUsdc,
    deadline: BigInt(latest.timestamp + deadlineSeconds),
    slippageBps,
  };
}

async function main() {
  requireEnv("FUJI_RPC_URL");
  const candidate = readCandidate();
  const executorAddress = requireEnv("ONCHAIN_DYNAMIC_AAVE_EXECUTOR_ADDRESS");
  const request = await buildRequest(candidate);
  const executor = await hre.ethers.getContractAt("OnchainDynamicAaveExecutor", executorAddress);
  const latest = await hre.ethers.provider.getBlock("latest");
  const paths = evidencePaths({ strategy: "fuji-dynamic-signal-simulate" });

  const result = {
    runId: paths.runId,
    simulatedAt: new Date().toISOString(),
    context: await networkContext(hre, process.env),
    network: hre.network.name,
    executorAddress,
    blockNumber: latest.number,
    blockTimestamp: latest.timestamp,
    signal: signalSummary(candidate),
    request: {
      xToken: request.xToken,
      yToken: request.yToken,
      usdc: request.usdc,
      router: request.router,
      amountX: request.amountX.toString(),
      amountY: request.amountY.toString(),
      premiumBps: request.premiumBps.toString(),
      minProfitValueUsdc: request.minProfitValueUsdc.toString(),
      deadline: request.deadline.toString(),
      slippageBps: request.slippageBps.toString(),
    },
    staticCall: { ok: false },
  };

  try {
    await executor.requestDynamicFlashLoan.staticCall(request);
    const gasEstimate = await executor.requestDynamicFlashLoan.estimateGas(request);
    result.staticCall = { ok: true, gasEstimate: gasEstimate.toString() };
  } catch (error) {
    result.staticCall = { ok: false, error: sanitizeError(error) };
  }

  result.success = result.staticCall.ok;
  result.finishedAt = new Date().toISOString();
  writeJson(paths.reportPath, result);
  appendJsonl(paths.tradeLogPath, {
    runId: paths.runId,
    observedAt: result.finishedAt,
    network: "fuji",
    strategy: "dynamic_signal",
    action: "simulate_static_call",
    signalId: result.signal.signalId,
    success: result.success,
    reportPath: paths.reportPath,
    error: result.staticCall.error,
  });
  console.log(JSON.stringify(result, null, 2));
  if (!result.success) {
    process.exitCode = 1;
  }
}

if (require.main === module) {
  main().catch((error) => {
    console.error(JSON.stringify({ ok: false, error: sanitizeError(error) }, null, 2));
    process.exitCode = 1;
  });
}

module.exports = {
  signalId,
  signalSummary,
};
