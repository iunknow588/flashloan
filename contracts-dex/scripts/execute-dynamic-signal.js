const hre = require("hardhat");
const fs = require("fs");
const path = require("path");
const {
  boolEnv,
  buildBroadcastGate,
  evidencePaths,
  networkContext,
  ownerMatchesSigner,
  receiptReport,
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

function tokenAddressFor(symbol) {
  const address = optionalEnv(...tokenEnvNames(symbol));
  if (!address) {
    const cached = cachedTokenAddressFor(symbol);
    if (cached) {
      return cached;
    }
    throw new Error(`missing token address env/cache for ${symbol}: ${tokenEnvNames(symbol).join(" or ")}`);
  }
  return address;
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

function readSignal() {
  const file = signalPath();
  const payload = JSON.parse(fs.readFileSync(file, "utf8"));
  const signal = payload.signal || payload;
  if (!signal.trigger_signal && !signal.signal) {
    throw new Error("latest trigger signal is false");
  }
  if (
    signal.executable_signal !== true ||
    signal.dex_quote_verified !== true ||
    signal.net_profit_verified !== true
  ) {
    throw new Error(
      "signal is not executable: dex quote, net profit, and executable flags must be verified"
    );
  }
  if (!signal.x_symbol || !signal.y_symbol) {
    throw new Error("signal must contain x_symbol and y_symbol");
  }
  return signal;
}

function signalId(signal) {
  return signal.signal_id || signal.signalId || signal.id || signal.observed_at || null;
}

function signalSummary(signal) {
  return {
    signalId: signalId(signal),
    observedAt: signal.observed_at || signal.observedAt || null,
    xSymbol: signal.x_symbol,
    ySymbol: signal.y_symbol,
    xChangePercent: signal.x_change_percent ?? signal.a_change_percent ?? null,
    yChangePercent: signal.y_change_percent ?? signal.b_change_percent ?? null,
    executableSignal: signal.executable_signal ?? null,
    dexQuoteVerified: signal.dex_quote_verified ?? null,
    netProfitVerified: signal.net_profit_verified ?? null,
    minProfit: signal.min_profit ?? signal.minProfit ?? null,
  };
}

function tradeLogPath() {
  return path.resolve(process.cwd(), process.env.TESTNET_TRADE_LOG || "deployments/fuji-trades.jsonl");
}

function appendTradeLog(row) {
  const logFile = tradeLogPath();
  fs.mkdirSync(path.dirname(logFile), { recursive: true });
  fs.appendFileSync(logFile, `${JSON.stringify(row)}\n`);
}

async function main() {
  requireEnv("FUJI_RPC_URL");

  const signal = readSignal();
  const executorAddress = requireEnv("ONCHAIN_DYNAMIC_AAVE_EXECUTOR_ADDRESS");
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
  const paths = evidencePaths({ strategy: "fuji-dynamic-signal" });
  const request = {
    xToken: tokenAddressFor(signal.x_symbol),
    yToken: tokenAddressFor(signal.y_symbol),
    usdc,
    router,
    amountX,
    amountY,
    premiumBps,
    minProfitValueUsdc,
    deadline: BigInt(latest.timestamp + deadlineSeconds),
    slippageBps,
  };

  const executor = await hre.ethers.getContractAt("OnchainDynamicAaveExecutor", executorAddress);
  const ownerGate = await ownerMatchesSigner(hre, executor, process.env);
  const requestSummary = {
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
  };
  let staticCall = { ok: false };
  try {
    await executor.requestDynamicFlashLoan.staticCall(request);
    const gasEstimate = await executor.requestDynamicFlashLoan.estimateGas(request);
    staticCall = { ok: true, gasEstimate: gasEstimate.toString() };
  } catch (error) {
    staticCall = { ok: false, error: sanitizeError(error) };
  }

  const broadcastRequested = boolEnv(process.env, "DYNAMIC_SIGNAL_BROADCAST_ENABLED", "FUJI_DYNAMIC_SIGNAL_BROADCAST_ENABLED");
  const gate = await buildBroadcastGate({
    hreLike: hre,
    env: process.env,
    strategy: "small-amount",
    intent: ["DYNAMIC_SIGNAL_BROADCAST_ENABLED", "FUJI_DYNAMIC_SIGNAL_BROADCAST_ENABLED"],
    ownerMatches: ownerGate.matches,
    staticCallOk: staticCall.ok,
    payloadFresh: request.deadline > BigInt(latest.timestamp),
    minProfitChecked: signal.net_profit_verified === true && signal.dex_quote_verified === true,
  });

  const report = {
    runId: paths.runId,
    strategy: "dynamic_signal",
    mode: "static-call",
    startedAt: new Date().toISOString(),
    finishedAt: new Date().toISOString(),
    context: await networkContext(hre, process.env),
    owner: ownerGate,
    executorAddress,
    blockNumber: latest.number,
    blockTimestamp: latest.timestamp,
    signal: signalSummary(signal),
    request: requestSummary,
    staticCall,
    gate,
    broadcast: {
      requested: broadcastRequested,
      submitted: false,
      reason: broadcastRequested && !gate.ready ? "broadcast gate failed" : "static call evidence only",
    },
  };

  if (!broadcastRequested || !gate.ready) {
    writeJson(paths.reportPath, report);
    appendTradeLog({
      runId: paths.runId,
      observedAt: report.finishedAt,
      network: "fuji",
      strategy: "dynamic_signal",
      action: "static_call",
      signalId: report.signal.signalId,
      success: staticCall.ok,
      reportPath: paths.reportPath,
      error: staticCall.error,
    });
    console.log(JSON.stringify(report, null, 2));
    if (!staticCall.ok) process.exitCode = 1;
    return;
  }

  requireEnv("DEPLOYER_PRIVATE_KEY");
  try {
    const tx = await executor.requestDynamicFlashLoan(request);
    const receipt = await tx.wait();
    const completedReport = {
      ...report,
      finishedAt: new Date().toISOString(),
      broadcast: {
        requested: true,
        submitted: true,
        txHash: tx.hash,
        receiptStatus: receipt.status,
      },
    };
    writeJson(paths.reportPath, completedReport);
    writeJson(paths.receiptPath, receiptReport(receipt));
    appendTradeLog({
      runId: paths.runId,
      observedAt: completedReport.finishedAt,
      network: "fuji",
      strategy: "dynamic_signal",
      action: "broadcast",
      signalId: completedReport.signal.signalId,
      success: true,
      txHash: tx.hash,
      gasUsed: receipt.gasUsed.toString(),
      profitUnits: "0",
      reportPath: paths.reportPath,
      receiptPath: paths.receiptPath,
    });
    console.log(JSON.stringify(completedReport, null, 2));
  } catch (error) {
    appendTradeLog({
      runId: paths.runId,
      observedAt: new Date().toISOString(),
      network: "fuji",
      strategy: "dynamic_signal",
      action: "broadcast",
      signalId: report.signal.signalId,
      success: false,
      reportPath: paths.reportPath,
      error: sanitizeError(error),
    });
    throw error;
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
