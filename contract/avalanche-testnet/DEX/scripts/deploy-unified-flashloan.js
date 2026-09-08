const fs = require("fs");
const path = require("path");
const hre = require("hardhat");
const { validateDeploymentPrerequisites } = require("./preflight-unified-flashloan");
const { readbackUnifiedExecutor } = require("./readback-unified-flashloan");

function envBool(name, fallback) {
  const value = String(process.env[name] || "").trim().toLowerCase();
  if (!value) return fallback;
  return !["0", "false", "no", "off"].includes(value);
}

function envUint(name, fallback) {
  const value = String(process.env[name] || "").trim();
  return value ? BigInt(value) : BigInt(fallback);
}

async function main() {
  const [deployer] = await hre.ethers.getSigners();
  const preflight = await validateDeploymentPrerequisites();
  if (!preflight.ready || !preflight.deployment) {
    const failed = preflight.checks.filter((item) => !item.ok).map((item) => item.name).join(", ");
    throw new Error(`deployment preflight failed: ${failed || "unknown prerequisite"}`);
  }
  if (BigInt(preflight.deployment.balanceWei) < BigInt(preflight.deployment.estimatedBudgetWei)) {
    throw new Error(
      `insufficient deployer balance: have ${preflight.deployment.balanceWei} wei, need approximately ${preflight.deployment.estimatedBudgetWei} wei`,
    );
  }
  const poolAddress = preflight.configured.aavePool;
  const usdcAddress = preflight.configured.usdc;

  const Executor = await hre.ethers.getContractFactory("UnifiedFlashLoanMevExecutor");
  const factoryAddress = preflight.configured.factory;
  const routerAddress = preflight.configured.router;
  const quoterAddress = preflight.configured.quoter;

  const deploymentRequest = await Executor.getDeployTransaction(poolAddress, usdcAddress, deployer.address);
  const estimatedDeploymentGas = await hre.ethers.provider.estimateGas({
    ...deploymentRequest,
    from: deployer.address,
  });
  const feeData = await hre.ethers.provider.getFeeData();
  const configuredGasPrice = String(process.env.TRIANGULAR_DEPLOY_MIN_GAS_PRICE_WEI || "").trim();
  const gasPrice = configuredGasPrice
    ? BigInt(configuredGasPrice)
    : (feeData.maxFeePerGas || feeData.gasPrice || 0n);
  if (gasPrice <= 0n) throw new Error("Unable to determine a non-zero deployment gas price");
  const safetyBps = BigInt(process.env.TRIANGULAR_DEPLOY_GAS_SAFETY_BPS || "15000");
  const configGasUnits = BigInt(process.env.TRIANGULAR_DEPLOY_CONFIG_GAS_UNITS || "800000");
  const estimatedBudget = ((estimatedDeploymentGas + configGasUnits) * gasPrice * safetyBps) / 10_000n;
  const balance = await hre.ethers.provider.getBalance(deployer.address);
  if (balance < estimatedBudget) {
    throw new Error(
      `insufficient deployer balance: have ${balance} wei, need approximately ${estimatedBudget} wei`,
    );
  }

  const executor = await Executor.deploy(poolAddress, usdcAddress, deployer.address);
  await executor.waitForDeployment();

  const adapterKind = 1;
  await (await executor.setAdapterConfig(adapterKind, true, factoryAddress, routerAddress, quoterAddress)).wait();

  const minLiquidity = String(process.env.UNIFIED_MIN_POOL_LIQUIDITY || "").trim();
  const minTickDelta = String(process.env.UNIFIED_MIN_TICK_DELTA || "").trim();
  if (minLiquidity || minTickDelta) {
    if (!minLiquidity || !minTickDelta) {
      throw new Error("UNIFIED_MIN_POOL_LIQUIDITY and UNIFIED_MIN_TICK_DELTA must be set together");
    }
    await (await executor.setRuntimeRiskConfig(BigInt(minLiquidity), BigInt(minTickDelta))).wait();
  }

  const profitSweepEnabled = envBool("TRIANGULAR_PROFIT_SWEEP_ENABLED", true);
  const profitReserveUsdc = envUint("TRIANGULAR_PROFIT_RESERVE_USDC", 0);
  const profitSweepThreshold = envUint("TRIANGULAR_PROFIT_SWEEP_THRESHOLD_USDC", 0);
  await (
    await executor.setProfitConfig(
      profitSweepEnabled,
      profitReserveUsdc,
      profitSweepThreshold,
    )
  ).wait();

  const output = {
    network: hre.network.name,
    chainId: Number((await hre.ethers.provider.getNetwork()).chainId),
    deployer: deployer.address,
    owner: deployer.address,
    unifiedFlashLoanMevExecutorAddress: await executor.getAddress(),
    aavePoolAddress: poolAddress,
    usdcAddress,
    adapterKind: Number(adapterKind),
    adapterConfigured: Boolean(factoryAddress && routerAddress && quoterAddress),
    profitConfig: {
      sweepEnabled: profitSweepEnabled,
      reserveUsdc: profitReserveUsdc.toString(),
      sweepThreshold: profitSweepThreshold.toString(),
    },
    readback: await readbackUnifiedExecutor(await executor.getAddress()),
  };

  const outputDir = path.resolve(__dirname, "../deployments");
  fs.mkdirSync(outputDir, { recursive: true });
  const outputPath = path.join(outputDir, `unified-flashloan-${hre.network.name}.json`);
  fs.writeFileSync(outputPath, `${JSON.stringify(output, null, 2)}\n`, "utf8");
  console.log(JSON.stringify({ ...output, outputPath }, null, 2));
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
