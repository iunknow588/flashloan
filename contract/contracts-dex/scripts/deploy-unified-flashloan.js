const fs = require("fs");
const path = require("path");
const hre = require("hardhat");

function envAddress(...names) {
  for (const name of names) {
    const value = String(process.env[name] || "").trim();
    if (value) return value;
  }
  return "";
}

function requiredAddress(...names) {
  const value = envAddress(...names);
  if (!value) {
    throw new Error(`Missing address env: ${names.join(" or ")}`);
  }
  return value;
}

async function main() {
  const [deployer] = await hre.ethers.getSigners();
  const poolAddress = requiredAddress("UNIFIED_AAVE_POOL_ADDRESS", "TRIANGULAR_AAVE_POOL_ADDRESS");
  const usdcAddress = requiredAddress("UNIFIED_USDC_ADDRESS", "TRIANGULAR_USDC_ADDRESS");

  const Executor = await hre.ethers.getContractFactory("UnifiedFlashLoanMevExecutor");
  const executor = await Executor.deploy(poolAddress, usdcAddress, deployer.address);
  await executor.waitForDeployment();

  const adapterKind = await executor.ADAPTER_UNISWAP_V3();
  const factoryAddress = envAddress("UNIFIED_V3_FACTORY", "TRIANGULAR_V3_FACTORY");
  const routerAddress = envAddress("UNIFIED_V3_ROUTER", "TRIANGULAR_V3_ROUTER");
  const quoterAddress = envAddress("UNIFIED_V3_QUOTER", "TRIANGULAR_V3_QUOTER");
  if (factoryAddress || routerAddress || quoterAddress) {
    if (!factoryAddress || !routerAddress || !quoterAddress) {
      throw new Error("UNIFIED_V3_FACTORY, UNIFIED_V3_ROUTER and UNIFIED_V3_QUOTER must be set together");
    }
    await (
      await executor.setAdapterConfig(adapterKind, true, factoryAddress, routerAddress, quoterAddress)
    ).wait();
  }

  const minLiquidity = String(process.env.UNIFIED_MIN_POOL_LIQUIDITY || "").trim();
  const minTickDelta = String(process.env.UNIFIED_MIN_TICK_DELTA || "").trim();
  if (minLiquidity || minTickDelta) {
    if (!minLiquidity || !minTickDelta) {
      throw new Error("UNIFIED_MIN_POOL_LIQUIDITY and UNIFIED_MIN_TICK_DELTA must be set together");
    }
    await (await executor.setRuntimeRiskConfig(BigInt(minLiquidity), BigInt(minTickDelta))).wait();
  }

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
