const hre = require("hardhat");
const fs = require("fs");
const path = require("path");
const {
  appendJsonl,
  EXPECTED_AVALANCHE_CHAIN_ID,
  EXPECTED_FUJI_CHAIN_ID,
  evidencePaths,
  networkContext,
  sanitizeError,
  writeJson,
} = require("./fuji-evidence");

function requireAnyEnv(...names) {
  for (const name of names) {
    const value = process.env[name];
    if (value && value.trim() && value !== "0x...") {
      return value.trim();
    }
  }
  throw new Error(`${names.join(" or ")} is required`);
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

function stableTokenFromEnv(...names) {
  for (const name of names) {
    const value = optionalEnv(name);
    if (value) return value;
  }
  const stableTokens = optionalEnv("DEX_TARGET_STABLE_TOKENS");
  if (stableTokens) {
    const entries = stableTokens.split(",").map((item) => item.trim()).filter(Boolean);
    for (const preferred of ["USDC", "USDC.e", "USDC.E"]) {
      const entry = entries.find((item) => item.toUpperCase().startsWith(`${preferred.toUpperCase()}:`));
      if (entry) {
        const [, address] = entry.split(":", 2);
        if (address && address.trim()) return address.trim();
      }
    }
    const fallback = entries.find((item) => item.includes(":"));
    if (fallback) {
      const [, address] = fallback.split(":", 2);
      if (address && address.trim()) return address.trim();
    }
  }
  return "";
}

function normalizeAddress(value) {
  const text = String(value || "").trim();
  if (hre.ethers.isAddress(text)) {
    return hre.ethers.getAddress(text);
  }
  if (/^0x[a-fA-F0-9]{40}$/.test(text)) {
    return hre.ethers.getAddress(text.toLowerCase());
  }
  return "";
}

function envBigInt(name, defaultValue) {
  const value = process.env[name];
  return value && value.trim() ? BigInt(value.trim()) : defaultValue;
}

function optionalBigInt(name) {
  const value = process.env[name];
  return value && value.trim() ? BigInt(value.trim()) : null;
}

function envBool(name, defaultValue) {
  const value = process.env[name];
  if (!value || !value.trim()) return defaultValue;
  return ["1", "true", "yes", "on"].includes(value.trim().toLowerCase());
}

function envEther(name, defaultEther) {
  const value = process.env[name];
  return hre.ethers.parseEther(value && value.trim() ? value.trim() : defaultEther);
}

function envBps(name, defaultValue) {
  const value = process.env[name];
  const parsed = value && value.trim() ? BigInt(value.trim()) : BigInt(defaultValue);
  if (parsed < 10_000n) throw new Error(`${name} must be at least 10000 bps`);
  return parsed;
}

async function requireExpectedChain(networkName) {
  const network = await hre.ethers.provider.getNetwork();
  const expected = String(networkName || "").toLowerCase() === "avalanche"
    ? EXPECTED_AVALANCHE_CHAIN_ID
    : EXPECTED_FUJI_CHAIN_ID;
  if (network.chainId !== expected) {
    throw new Error(`wrong chainId ${network.chainId}; expected ${expected} for ${networkName}`);
  }
  return network;
}

async function requireContractCode(label, address) {
  const code = await hre.ethers.provider.getCode(address);
  if (code === "0x") {
    throw new Error(`${label} has no contract code on ${hre.network.name}: ${address}`);
  }
}

async function preflightConfiguredContracts({ poolAddress, usdcAddress, v3Factory, v3Router, v3Quoter }) {
  await requireContractCode("TRIANGULAR_AAVE_POOL_ADDRESS", poolAddress);
  await requireContractCode("TRIANGULAR_USDC_ADDRESS", usdcAddress);
  await requireContractCode("TRIANGULAR_V3_FACTORY", v3Factory);
  await requireContractCode("TRIANGULAR_V3_ROUTER", v3Router);
  await requireContractCode("TRIANGULAR_V3_QUOTER", v3Quoter);

  const pool = await hre.ethers.getContractAt([
    "function FLASHLOAN_PREMIUM_TOTAL() view returns (uint128)",
  ], poolAddress);
  const token = await hre.ethers.getContractAt([
    "function decimals() view returns (uint8)",
  ], usdcAddress);
  const [premiumBps, decimals] = await Promise.all([
    pool.FLASHLOAN_PREMIUM_TOTAL(),
    token.decimals(),
  ]);
  if (Number(decimals) !== 6) {
    throw new Error(`TRIANGULAR_USDC_ADDRESS decimals must be 6, got ${decimals}`);
  }
  return {
    premiumBps: premiumBps.toString(),
    usdcDecimals: Number(decimals),
  };
}

async function maybeSet(contract, currentFn, setterFn, desiredValue, txLabel) {
  const current = await contract[currentFn]();
  if (current === desiredValue) {
    console.log(`${txLabel}=skipped_current_value`);
    return null;
  }
  const tx = await contract[setterFn](desiredValue);
  const receipt = await tx.wait();
  console.log(`${txLabel}=${receipt.hash}`);
  return receipt;
}

async function maybeSetRuntimeRiskConfig(controller, minPoolLiquidity, minTickDelta) {
  const current = await controller.runtimeRiskConfig();
  const currentMinPoolLiquidity = current.minPoolLiquidity ?? current[0];
  const currentMinTickDelta = current.minTickDelta ?? current[1];
  if (currentMinPoolLiquidity === minPoolLiquidity && currentMinTickDelta === minTickDelta) {
    console.log("setRuntimeRiskConfigTx=skipped_current_value");
    return null;
  }
  const tx = await controller.setRuntimeRiskConfig(minPoolLiquidity, minTickDelta);
  const receipt = await tx.wait();
  console.log(`setRuntimeRiskConfigTx=${receipt.hash}`);
  return receipt;
}

function receiptHash(receipt) {
  return receipt ? receipt.hash : null;
}

async function estimateGasLatest(tx, from) {
  const request = {
    from,
    ...(tx.to ? { to: tx.to } : {}),
    ...(tx.data ? { data: tx.data } : {}),
    ...(tx.value ? { value: hre.ethers.toQuantity(tx.value) } : {}),
  };
  const raw = await hre.ethers.provider.send("eth_estimateGas", [request, "latest"]);
  return BigInt(raw);
}

async function estimateDeploymentBudget({ Executor, Controller, poolAddress, usdcAddress, deployerAddress }) {
  const executorDeployTx = await Executor.getDeployTransaction(poolAddress, usdcAddress, deployerAddress);
  const controllerDeployTx = await Controller.getDeployTransaction(usdcAddress, deployerAddress, deployerAddress);
  const [executorGas, controllerGas, gasPriceRaw] = await Promise.all([
    estimateGasLatest(executorDeployTx, deployerAddress),
    estimateGasLatest(controllerDeployTx, deployerAddress),
    hre.ethers.provider.send("eth_gasPrice", []),
  ]);
  const configGas = envBigInt("TRIANGULAR_DEPLOY_CONFIG_GAS_UNITS", 800_000n);
  const safetyBps = envBps("TRIANGULAR_DEPLOY_GAS_SAFETY_BPS", 15_000);
  const networkGasPrice = BigInt(gasPriceRaw);
  const minGasPrice = envBigInt("TRIANGULAR_DEPLOY_MIN_GAS_PRICE_WEI", 25_000_000_000n);
  const gasPrice = networkGasPrice > minGasPrice ? networkGasPrice : minGasPrice;
  const estimatedGasUnits = executorGas + controllerGas + configGas;
  const estimatedBudgetWei = (estimatedGasUnits * gasPrice * safetyBps) / 10_000n;
  const historicalFloorWei = envEther("TRIANGULAR_DEPLOY_HISTORICAL_MIN_BUDGET_AVAX", "0.25");
  const budgetWei = estimatedBudgetWei > historicalFloorWei ? estimatedBudgetWei : historicalFloorWei;
  return {
    executorGas: executorGas.toString(),
    controllerGas: controllerGas.toString(),
    configGas: configGas.toString(),
    estimatedGasUnits: estimatedGasUnits.toString(),
    networkGasPriceWei: networkGasPrice.toString(),
    minGasPriceWei: minGasPrice.toString(),
    gasPriceWei: gasPrice.toString(),
    safetyBps: safetyBps.toString(),
    estimatedBudgetWei: estimatedBudgetWei.toString(),
    estimatedBudgetAvax: hre.ethers.formatEther(estimatedBudgetWei),
    historicalFloorWei: historicalFloorWei.toString(),
    historicalFloorAvax: hre.ethers.formatEther(historicalFloorWei),
    budgetWei: budgetWei.toString(),
    budgetAvax: hre.ethers.formatEther(budgetWei),
  };
}

async function main() {
  requireAnyEnv("FUJI_RPC_URL", "AVALANCHE_FUJI_RPC_URL", "AVALANCHE_RPC_URL", "AVALANCHE_RPC");
  requireAnyEnv("DEPLOYER_PRIVATE_KEY", "LIQUIDATION_EXECUTION_PRIVATE_KEY", "COW_ORDER_SIGNER_PRIVATE_KEY");

  const poolAddress = normalizeAddress(optionalEnv("TRIANGULAR_AAVE_POOL_ADDRESS", "AAVE_POOL_ADDRESS"));
  const usdcAddress = normalizeAddress(stableTokenFromEnv("TRIANGULAR_USDC_ADDRESS", "FUJI_USDC", "USDC_ADDRESS"));
  if (!poolAddress) throw new Error("TRIANGULAR_AAVE_POOL_ADDRESS or AAVE_POOL_ADDRESS is required");
  if (!usdcAddress) throw new Error("TRIANGULAR_USDC_ADDRESS or FUJI_USDC or USDC_ADDRESS is required");

  const adapterKind = 1n;
  const v3Factory = normalizeAddress(optionalEnv("TRIANGULAR_V3_FACTORY", "UNISWAP_V3_FACTORY"));
  const v3Router = normalizeAddress(optionalEnv("TRIANGULAR_V3_ROUTER", "UNISWAP_V3_ROUTER", "TRIANGULAR_DEX_ROUTER"));
  const v3Quoter = normalizeAddress(optionalEnv("TRIANGULAR_V3_QUOTER", "UNISWAP_V3_QUOTER"));
  if (!v3Factory) throw new Error("TRIANGULAR_V3_FACTORY or UNISWAP_V3_FACTORY is required");
  if (!v3Router) throw new Error("TRIANGULAR_V3_ROUTER or UNISWAP_V3_ROUTER or TRIANGULAR_DEX_ROUTER is required");
  if (!v3Quoter) throw new Error("TRIANGULAR_V3_QUOTER or UNISWAP_V3_QUOTER is required");
  const minPoolLiquidity = envBigInt("TRIANGULAR_MIN_POOL_LIQUIDITY", 1n);
  const minTickDelta = envBigInt("TRIANGULAR_MIN_TICK_DELTA", 1n);
  const profitSweepEnabled = envBool("TRIANGULAR_PROFIT_SWEEP_ENABLED", true);
  const profitSweepThreshold = optionalBigInt("TRIANGULAR_PROFIT_SWEEP_THRESHOLD_USDC");
  const profitReserve = optionalBigInt("TRIANGULAR_PROFIT_RESERVE_USDC");

  const networkName = hre.network.name || "unknown";
  await requireExpectedChain(networkName);
  const preflight = await preflightConfiguredContracts({ poolAddress, usdcAddress, v3Factory, v3Router, v3Quoter });
  const paths = evidencePaths({ strategy: `${networkName}-triangular-ab-deploy` });
  const [deployer] = await hre.ethers.getSigners();
  const deployerBalance = await hre.ethers.provider.getBalance(deployer.address);
  const minDeployBalance = envEther("TRIANGULAR_MIN_DEPLOYER_BALANCE_AVAX", "0.12");
  if (deployerBalance < minDeployBalance) {
    throw new Error(
      `deployer balance ${hre.ethers.formatEther(deployerBalance)} AVAX is below safety floor ${hre.ethers.formatEther(minDeployBalance)} AVAX`
    );
  }
  console.log(`deployer=${deployer.address}`);

  const Executor = await hre.ethers.getContractFactory("AaveTriangularExecutor");
  const Controller = await hre.ethers.getContractFactory("TriangularRouteController");
  const deployBudget = await estimateDeploymentBudget({
    Executor,
    Controller,
    poolAddress,
    usdcAddress,
    deployerAddress: deployer.address,
  });
  if (deployerBalance < BigInt(deployBudget.budgetWei)) {
    throw new Error(
      `deployer balance ${hre.ethers.formatEther(deployerBalance)} AVAX is below estimated/historical deployment budget ${deployBudget.budgetAvax} AVAX`
    );
  }

  const executor = await Executor.deploy(poolAddress, usdcAddress, deployer.address);
  await executor.waitForDeployment();
  const executorAddress = await executor.getAddress();
  console.log(`AAVE_TRIANGULAR_EXECUTOR_ADDRESS=${executorAddress}`);

  const controller = await Controller.deploy(usdcAddress, executorAddress, deployer.address);
  await controller.waitForDeployment();
  const controllerAddress = await controller.getAddress();
  console.log(`TRIANGULAR_ROUTE_CONTROLLER_ADDRESS=${controllerAddress}`);

  const adapterTx = await controller.setAdapterConfig(adapterKind, true, v3Factory, v3Router, v3Quoter);
  const adapterReceipt = await adapterTx.wait();
  console.log(`setAdapterConfigTx=${adapterReceipt.hash}`);

  const riskReceipt = await maybeSetRuntimeRiskConfig(controller, minPoolLiquidity, minTickDelta);

  const setControllerTx = await executor.setController(controllerAddress);
  const setControllerReceipt = await setControllerTx.wait();
  console.log(`setControllerTx=${setControllerReceipt.hash}`);

  const sweepEnabledReceipt = await maybeSet(
    executor,
    "profitSweepEnabled",
    "setProfitSweepEnabled",
    profitSweepEnabled,
    "setProfitSweepEnabledTx",
  );

  let sweepThresholdReceipt = null;
  if (profitSweepThreshold !== null) {
    sweepThresholdReceipt = await maybeSet(
      executor,
      "profitSweepThresholdUsdc",
      "setProfitSweepThresholdUsdc",
      profitSweepThreshold,
      "setProfitSweepThresholdTx",
    );
  }

  let reserveReceipt = null;
  if (profitReserve !== null) {
    reserveReceipt = await maybeSet(
      executor,
      "profitReserveUsdc",
      "setProfitReserveUsdc",
      profitReserve,
      "setProfitReserveTx",
    );
  }

  const outputDir = path.join(process.cwd(), "deployments");
  const deploymentPath = path.join(outputDir, `${networkName}-triangular-ab.json`);
  const output = {
    runId: paths.runId,
    network: networkName,
    chainId: Number((await hre.ethers.provider.getNetwork()).chainId),
    deployedAt: new Date().toISOString(),
    deployer: deployer.address,
    deployerBalanceAvaxBefore: hre.ethers.formatEther(deployerBalance),
    minDeployBalanceAvax: hre.ethers.formatEther(minDeployBalance),
    deployBudget,
    preflight,
    aavePoolAddress: poolAddress,
    usdcAddress,
    triangularRouteControllerAddress: controllerAddress,
    aaveTriangularExecutorAddress: executorAddress,
    runtimeRiskConfig: {
      minPoolLiquidity: minPoolLiquidity.toString(),
      minTickDelta: minTickDelta.toString(),
    },
    adapterConfig: {
      adapterKind: adapterKind.toString(),
      factory: v3Factory,
      router: v3Router,
      quoter: v3Quoter,
      setAdapterConfigTxHash: receiptHash(adapterReceipt),
    },
    profitSweep: {
      enabled: profitSweepEnabled,
      thresholdUsdc: profitSweepThreshold === null ? "contract-default" : profitSweepThreshold.toString(),
      reserveUsdc: profitReserve === null ? "contract-default" : profitReserve.toString(),
      setEnabledTxHash: receiptHash(sweepEnabledReceipt),
      setThresholdTxHash: receiptHash(sweepThresholdReceipt),
      setReserveTxHash: receiptHash(reserveReceipt),
    },
    setRuntimeRiskConfigTxHash: receiptHash(riskReceipt),
    setControllerTxHash: receiptHash(setControllerReceipt),
  };
  fs.mkdirSync(outputDir, { recursive: true });
  fs.writeFileSync(deploymentPath, `${JSON.stringify(output, null, 2)}\n`);

  const report = {
    ...output,
    context: await networkContext(hre, process.env),
    deploymentPath,
    reportPath: paths.reportPath,
  };
  writeJson(paths.reportPath, report);
  appendJsonl(paths.tradeLogPath, {
    runId: paths.runId,
    observedAt: report.deployedAt,
    network: hre.network.name,
    strategy: "triangular_ab_deploy",
    action: "deploy",
    success: true,
    deploymentPath,
    reportPath: paths.reportPath,
    triangularRouteControllerAddress: controllerAddress,
    aaveTriangularExecutorAddress: executorAddress,
  });
  console.log(`deploymentFile=${deploymentPath}`);
  console.log(`evidenceReport=${paths.reportPath}`);
}

if (require.main === module) {
  main().catch((error) => {
    console.error(JSON.stringify({ ok: false, error: sanitizeError(error) }, null, 2));
    process.exitCode = 1;
  });
}
