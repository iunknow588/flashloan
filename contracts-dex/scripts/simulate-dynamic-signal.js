const hre = require("hardhat");
const fs = require("fs");
const path = require("path");

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

  const result = {
    simulatedAt: new Date().toISOString(),
    network: hre.network.name,
    xSymbol: candidate.x_symbol,
    ySymbol: candidate.y_symbol,
    success: false,
  };

  try {
    await executor.requestDynamicFlashLoan.staticCall(request);
    const gasEstimate = await executor.requestDynamicFlashLoan.estimateGas(request);
    result.success = true;
    result.gasEstimate = gasEstimate.toString();
  } catch (error) {
    result.error = error.shortMessage || error.reason || error.message;
  }

  console.log(JSON.stringify(result, null, 2));
  if (!result.success) {
    process.exitCode = 1;
  }
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
