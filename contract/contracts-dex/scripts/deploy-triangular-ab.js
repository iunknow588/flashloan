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

function normalizeOptionalAddress(value, label) {
  const address = normalizeAddress(value);
  if (!value || value === "0x...") return hre.ethers.ZeroAddress;
  if (!address) throw new Error(`${label} must be a valid address`);
  return address;
}

function envBigInt(name, defaultValue) {
  const value = process.env[name];
  return value && value.trim() ? BigInt(value.trim()) : defaultValue;
}

async function main() {
  requireAnyEnv("FUJI_RPC_URL", "AVALANCHE_FUJI_RPC_URL", "AVALANCHE_RPC_URL", "AVALANCHE_RPC");
  requireAnyEnv("DEPLOYER_PRIVATE_KEY", "LIQUIDATION_EXECUTION_PRIVATE_KEY", "COW_ORDER_SIGNER_PRIVATE_KEY");

  const poolAddress = normalizeAddress(optionalEnv("TRIANGULAR_AAVE_POOL_ADDRESS", "AAVE_POOL_ADDRESS"));
  const usdcAddress = normalizeAddress(stableTokenFromEnv("TRIANGULAR_USDC_ADDRESS", "FUJI_USDC", "USDC_ADDRESS"));
  if (!poolAddress) throw new Error("TRIANGULAR_AAVE_POOL_ADDRESS or AAVE_POOL_ADDRESS is required");
  if (!usdcAddress) throw new Error("TRIANGULAR_USDC_ADDRESS or FUJI_USDC or USDC_ADDRESS is required");

  const adapterKind = 1n;
  const v3Factory = normalizeOptionalAddress(optionalEnv("TRIANGULAR_V3_FACTORY", "UNISWAP_V3_FACTORY"), "TRIANGULAR_V3_FACTORY");
  const v3Router = normalizeOptionalAddress(optionalEnv("TRIANGULAR_V3_ROUTER", "UNISWAP_V3_ROUTER", "TRIANGULAR_DEX_ROUTER"), "TRIANGULAR_V3_ROUTER");
  const v3Quoter = normalizeOptionalAddress(optionalEnv("TRIANGULAR_V3_QUOTER", "UNISWAP_V3_QUOTER"), "TRIANGULAR_V3_QUOTER");
  const minPoolLiquidity = envBigInt("TRIANGULAR_MIN_POOL_LIQUIDITY", 1n);
  const minTickDelta = envBigInt("TRIANGULAR_MIN_TICK_DELTA", 1n);

  const networkName = hre.network.name || "unknown";
  const paths = evidencePaths({ strategy: `${networkName}-triangular-ab-deploy` });
  const [deployer] = await hre.ethers.getSigners();
  console.log(`deployer=${deployer.address}`);

  const Executor = await hre.ethers.getContractFactory("AaveTriangularExecutor");
  const executor = await Executor.deploy(poolAddress, usdcAddress, deployer.address);
  await executor.waitForDeployment();
  const executorAddress = await executor.getAddress();
  console.log(`AAVE_TRIANGULAR_EXECUTOR_ADDRESS=${executorAddress}`);

  const Controller = await hre.ethers.getContractFactory("TriangularRouteController");
  const controller = await Controller.deploy(usdcAddress, executorAddress, deployer.address);
  await controller.waitForDeployment();
  const controllerAddress = await controller.getAddress();
  console.log(`TRIANGULAR_ROUTE_CONTROLLER_ADDRESS=${controllerAddress}`);

  const adapterTx = await controller.setAdapterConfig(adapterKind, true, v3Factory, v3Router, v3Quoter);
  const adapterReceipt = await adapterTx.wait();
  console.log(`setAdapterConfigTx=${adapterReceipt.hash}`);

  const riskTx = await controller.setRuntimeRiskConfig(minPoolLiquidity, minTickDelta);
  const riskReceipt = await riskTx.wait();
  console.log(`setRuntimeRiskConfigTx=${riskReceipt.hash}`);

  const setControllerTx = await executor.setController(controllerAddress);
  const setControllerReceipt = await setControllerTx.wait();
  console.log(`setControllerTx=${setControllerReceipt.hash}`);

  const outputDir = path.join(process.cwd(), "deployments");
  const deploymentPath = path.join(outputDir, `${networkName}-triangular-ab.json`);
  const output = {
    runId: paths.runId,
    network: networkName,
    chainId: Number((await hre.ethers.provider.getNetwork()).chainId),
    deployedAt: new Date().toISOString(),
    deployer: deployer.address,
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
      setAdapterConfigTxHash: adapterReceipt.hash,
    },
    setRuntimeRiskConfigTxHash: riskReceipt.hash,
    setControllerTxHash: setControllerReceipt.hash,
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
