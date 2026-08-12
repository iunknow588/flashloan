const fs = require("fs");
const path = require("path");
const hre = require("hardhat");
const { validateDeploymentPrerequisites } = require("./preflight-unified-flashloan");

function envAddress(...names) {
  for (const name of names) {
    const value = String(process.env[name] || "").trim();
    if (value && value !== "0x..." && value !== "0xyour_private_key") return value;
  }
  return "";
}

function requiredAddress(...names) {
  const value = envAddress(...names);
  if (!value) {
    throw new Error(`Missing address env: ${names.join(" or ")}`);
  }
  if (!hre.ethers.isAddress(value)) {
    throw new Error(`Invalid address env: ${names.join(" or ")}=${value}`);
  }
  return value;
}

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
  if (BigInt(preflight.balanceWei) < BigInt(preflight.estimatedBudgetWei)) {
    throw new Error(
      `insufficient deployer balance: have ${preflight.balanceWei} wei, need approximately ${preflight.estimatedBudgetWei} wei`,
    );
  }
  const poolAddress = requiredAddress("UNIFIED_AAVE_POOL_ADDRESS", "TRIANGULAR_AAVE_POOL_ADDRESS");
  const usdcAddress = requiredAddress("UNIFIED_USDC_ADDRESS", "TRIANGULAR_USDC_ADDRESS");

  const Executor = await hre.ethers.getContractFactory("UnifiedFlashLoanMevExecutor");
  const factoryAddress = envAddress("UNIFIED_V3_FACTORY", "TRIANGULAR_V3_FACTORY");
  const routerAddress = envAddress("UNIFIED_V3_ROUTER", "TRIANGULAR_V3_ROUTER");
  const quoterAddress = envAddress("UNIFIED_V3_QUOTER", "TRIANGULAR_V3_QUOTER");
  if (Boolean(factoryAddress) || Boolean(routerAddress) || Boolean(quoterAddress)) {
    if (!factoryAddress || !routerAddress || !quoterAddress) {
      throw new Error("UNIFIED_V3_FACTORY, UNIFIED_V3_ROUTER and UNIFIED_V3_QUOTER must be set together");
    }
    for (const [label, address] of [
      ["UNIFIED_V3_FACTORY", factoryAddress],
      ["UNIFIED_V3_ROUTER", routerAddress],
      ["UNIFIED_V3_QUOTER", quoterAddress],
    ]) {
      if (!hre.ethers.isAddress(address)) throw new Error(`Invalid address env: ${label}=${address}`);
      if ((await hre.ethers.provider.getCode(address)) === "0x") {
        throw new Error(`${label} has no contract code on ${hre.network.name}: ${address}`);
      }
    }
  } else {
    throw new Error("V3 factory/router/quoter are required before deployment");
  }
  if ((await hre.ethers.provider.getCode(poolAddress)) === "0x") {
    throw new Error(`Aave pool has no contract code on ${hre.network.name}: ${poolAddress}`);
  }
  if ((await hre.ethers.provider.getCode(usdcAddress)) === "0x") {
    throw new Error(`USDC has no contract code on ${hre.network.name}: ${usdcAddress}`);
  }

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
