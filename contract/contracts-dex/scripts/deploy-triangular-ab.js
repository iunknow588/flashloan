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

function requireAnyEnv(...names) {
  for (const name of names) {
    const value = process.env[name];
    if (value && value.trim() && value !== "0x...") {
      return value.trim();
    }
  }
  throw new Error(`${names.join(" or ")} is required`);
}

function requireAnyPrivateKey(...names) {
  return requireAnyEnv(...names);
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
    const value = process.env[name];
    if (value && value.trim() && value !== "0x...") {
      return value.trim();
    }
  }
  const stableTokens = optionalEnv("DEX_TARGET_STABLE_TOKENS");
  if (stableTokens) {
    const entries = stableTokens.split(",").map((item) => item.trim()).filter(Boolean);
    for (const preferred of ["USDC", "USDC.e", "USDC.E"]) {
      const entry = entries.find((item) => item.toUpperCase().startsWith(`${preferred.toUpperCase()}:`));
      if (entry) {
        const [, address] = entry.split(":", 2);
        if (address && address.trim()) {
          return address.trim();
        }
      }
    }
    const fallback = entries.find((item) => item.includes(":"));
    if (fallback) {
      const [, address] = fallback.split(":", 2);
      if (address && address.trim()) {
        return address.trim();
      }
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

async function main() {
  requireAnyEnv("FUJI_RPC_URL", "AVALANCHE_FUJI_RPC_URL", "AVALANCHE_RPC_URL", "AVALANCHE_RPC");
  requireAnyPrivateKey("DEPLOYER_PRIVATE_KEY", "LIQUIDATION_EXECUTION_PRIVATE_KEY", "COW_ORDER_SIGNER_PRIVATE_KEY");

  const poolAddress = normalizeAddress(optionalEnv("TRIANGULAR_AAVE_POOL_ADDRESS", "AAVE_POOL_ADDRESS"));
  const usdcAddress = normalizeAddress(stableTokenFromEnv("TRIANGULAR_USDC_ADDRESS", "FUJI_USDC", "USDC_ADDRESS"));
  const routerAddress = normalizeAddress(optionalEnv("TRIANGULAR_DEX_ROUTER", "DEX_ROUTER_ADDRESS", "FUJI_DEX_ROUTER"));
  if (!poolAddress) throw new Error("TRIANGULAR_AAVE_POOL_ADDRESS or AAVE_POOL_ADDRESS is required");
  if (!usdcAddress) throw new Error("TRIANGULAR_USDC_ADDRESS or FUJI_USDC or USDC_ADDRESS is required");
  if (!routerAddress) throw new Error("TRIANGULAR_DEX_ROUTER or DEX_ROUTER_ADDRESS or FUJI_DEX_ROUTER is required");
  const borrowAmount = BigInt(optionalEnv("TRIANGULAR_BORROW_AMOUNT_UNITS") || "1000000");
  const minProfitUsdc = BigInt(optionalEnv("TRIANGULAR_MIN_PROFIT_USDC_UNITS") || "0");
  const deadlineSeconds = BigInt(optionalEnv("TRIANGULAR_DEADLINE_SECONDS") || "60");
  const slippageBps = BigInt(optionalEnv("TRIANGULAR_SLIPPAGE_BPS") || "50");
  const minBorrowAmount = BigInt(optionalEnv("TRIANGULAR_MIN_BORROW_AMOUNT_UNITS") || borrowAmount.toString());
  const maxBorrowAmount = BigInt(optionalEnv("TRIANGULAR_MAX_BORROW_AMOUNT_UNITS") || borrowAmount.toString());
  const amountSearchSteps = BigInt(optionalEnv("TRIANGULAR_AMOUNT_SEARCH_STEPS") || "1");
  const maxRouteSlippageBps = BigInt(optionalEnv("TRIANGULAR_MAX_ROUTE_SLIPPAGE_BPS") || slippageBps.toString());

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

  const configTx = await controller.setExecutionConfig(routerAddress, borrowAmount, minProfitUsdc, deadlineSeconds, slippageBps);
  const configReceipt = await configTx.wait();
  console.log(`setExecutionConfigTx=${configReceipt.hash}`);

  const amountSearchTx = await controller.setAmountSearchConfig(
    minBorrowAmount,
    maxBorrowAmount,
    amountSearchSteps,
    maxRouteSlippageBps,
  );
  const amountSearchReceipt = await amountSearchTx.wait();
  console.log(`setAmountSearchConfigTx=${amountSearchReceipt.hash}`);

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
    routerAddress,
    borrowAmount: borrowAmount.toString(),
    minProfitUsdc: minProfitUsdc.toString(),
    deadlineSeconds: deadlineSeconds.toString(),
    slippageBps: slippageBps.toString(),
    minBorrowAmount: minBorrowAmount.toString(),
    maxBorrowAmount: maxBorrowAmount.toString(),
    amountSearchSteps: amountSearchSteps.toString(),
    maxRouteSlippageBps: maxRouteSlippageBps.toString(),
    triangularRouteControllerAddress: controllerAddress,
    aaveTriangularExecutorAddress: executorAddress,
    setExecutionConfigTxHash: configReceipt.hash,
    setAmountSearchConfigTxHash: amountSearchReceipt.hash,
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
