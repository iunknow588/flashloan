const fs = require("fs");
const path = require("path");
const hre = require("hardhat");
const {
  buildRobinhoodTestnetPreflight,
  writeEvidence: writePreflightEvidence,
} = require("./preflight-robinhood-testnet");
const {
  readbackRobinhoodPausedExecutor,
  robinhoodContractsPath,
  writeEvidence,
} = require("./readback-robinhood-paused-executor");

function envBool(name, fallback) {
  const value = String(process.env[name] || "").trim().toLowerCase();
  if (!value) return fallback;
  return !["0", "false", "no", "off"].includes(value);
}

function envUint(name, fallback) {
  const value = String(process.env[name] || "").trim();
  return value ? BigInt(value) : BigInt(fallback);
}

function envAddress(...names) {
  for (const name of names) {
    const value = String(process.env[name] || "").trim();
    if (value && value !== "0x..." && hre.ethers.isAddress(value)) return hre.ethers.getAddress(value);
  }
  return "";
}

function metadataHash(label, payload) {
  const configured = String(process.env[label] || "").trim();
  if (/^0x[0-9a-fA-F]{64}$/.test(configured)) return configured;
  return hre.ethers.keccak256(hre.ethers.toUtf8Bytes(JSON.stringify(payload)));
}

function requireAddress(value, label, missing) {
  if (!value) missing.push(label);
}

async function assertPausedDeployPreflight() {
  const report = await buildRobinhoodTestnetPreflight();
  const evidencePath = writePreflightEvidence(report);
  if (report.readyForPausedDeploy) {
    return { report, evidencePath };
  }
  const reasons = report.blockingReasons.length ? report.blockingReasons.join(", ") : "unknown_blocker";
  throw new Error(`Robinhood paused deploy preflight failed: ${reasons}; evidence=${evidencePath}`);
}

async function assertRobinhoodTestnetScope() {
  const network = await hre.ethers.provider.getNetwork();
  const chainId = Number(network.chainId);
  const allowMainnet = envBool("ROBINHOOD_ALLOW_MAINNET_PAUSED_DEPLOY", false);
  if (chainId === 46630) return chainId;
  if (allowMainnet && chainId === 4663) return chainId;
  throw new Error(`Robinhood paused deploy is restricted to chainId 46630; got ${chainId}`);
}

function updateRobinhoodContractsJson(report, deploymentOutputPath, evidencePath) {
  const filePath = robinhoodContractsPath();
  let previous = {};
  try {
    previous = JSON.parse(fs.readFileSync(filePath, "utf8"));
  } catch {
    previous = {};
  }
  const next = {
    ...previous,
    broadcastEnabled: false,
    chainId: report.chainId,
    deploymentAddress: report.executor.address,
    deploymentFile: deploymentOutputPath,
    deploymentGate: "paused_until_robinhood_flashloan_v4_repayment_and_fork_staticcall_pass",
    executorContract: "RobinhoodPausedFlashLoanExecutor",
    lastReadbackEvidence: evidencePath,
    mode: "testnet_deploy_readback_only",
    network: "robinhood-testnet",
    readbackCodeHash: report.executor.codeHash,
    status: report.config.paused ? "deployed_paused" : "deployed_unexpected_unpaused",
  };
  fs.mkdirSync(path.dirname(filePath), { recursive: true });
  fs.writeFileSync(filePath, `${JSON.stringify(next, null, 2)}\n`, "utf8");
  return filePath;
}

async function main() {
  const preflight = await assertPausedDeployPreflight();
  const chainId = await assertRobinhoodTestnetScope();
  const [deployer] = await hre.ethers.getSigners();
  if (!deployer) throw new Error("DEPLOYER_PRIVATE_KEY is required for Robinhood paused deploy");

  const flashSource = envAddress(
    "ROBINHOOD_TESTNET_MORPHO",
    "ROBINHOOD_TESTNET_FLASH_SOURCE",
    "ROBINHOOD_FLASH_SOURCE",
    "ROBINHOOD_MORPHO",
  );
  const universalRouter = envAddress(
    "ROBINHOOD_TESTNET_V4_UNIVERSAL_ROUTER",
    "ROBINHOOD_V4_UNIVERSAL_ROUTER",
    "ROBINHOOD_UNIVERSAL_ROUTER",
  );
  const v3Router = envAddress(
    "ROBINHOOD_TESTNET_V3_SWAP_ROUTER",
    "ROBINHOOD_TESTNET_V3_ROUTER",
    "ROBINHOOD_V3_SWAP_ROUTER",
    "ROBINHOOD_V3_ROUTER",
  );
  const v3Factory = envAddress("ROBINHOOD_TESTNET_V3_FACTORY", "ROBINHOOD_V3_FACTORY");
  const v3Quoter = envAddress("ROBINHOOD_TESTNET_V3_QUOTER", "ROBINHOOD_V3_QUOTER");
  const poolManager = envAddress(
    "ROBINHOOD_TESTNET_V4_POOL_MANAGER",
    "ROBINHOOD_V4_POOL_MANAGER",
    "ROBINHOOD_POOL_MANAGER",
  );
  const permit2 = envAddress("ROBINHOOD_TESTNET_PERMIT2", "ROBINHOOD_PERMIT2");

  const missing = [];
  requireAddress(flashSource, "ROBINHOOD_TESTNET_FLASH_SOURCE or ROBINHOOD_TESTNET_MORPHO", missing);
  requireAddress(universalRouter, "ROBINHOOD_TESTNET_V4_UNIVERSAL_ROUTER", missing);
  requireAddress(poolManager, "ROBINHOOD_TESTNET_V4_POOL_MANAGER", missing);
  requireAddress(permit2, "ROBINHOOD_TESTNET_PERMIT2", missing);
  if (missing.length) {
    throw new Error(`missing Robinhood adapter addresses: ${missing.join(", ")}`);
  }

  const Executor = await hre.ethers.getContractFactory("RobinhoodPausedFlashLoanExecutor");
  const deploymentRequest = await Executor.getDeployTransaction(deployer.address);
  const estimatedDeploymentGas = await hre.ethers.provider.estimateGas({
    ...deploymentRequest,
    from: deployer.address,
  });
  const feeData = await hre.ethers.provider.getFeeData();
  const gasPrice = feeData.maxFeePerGas || feeData.gasPrice || 0n;
  if (gasPrice <= 0n) throw new Error("Unable to determine a non-zero deployment gas price");
  const safetyBps = BigInt(process.env.ROBINHOOD_PAUSED_DEPLOY_GAS_SAFETY_BPS || "15000");
  const configGasUnits = BigInt(process.env.ROBINHOOD_PAUSED_DEPLOY_CONFIG_GAS_UNITS || "700000");
  const estimatedBudget = ((estimatedDeploymentGas + configGasUnits) * gasPrice * safetyBps) / 10_000n;
  const balance = await hre.ethers.provider.getBalance(deployer.address);
  if (balance < estimatedBudget) {
    throw new Error(`insufficient deployer balance: have ${balance} wei, need approximately ${estimatedBudget} wei`);
  }

  const executor = await Executor.deploy(deployer.address);
  await executor.waitForDeployment();
  const executorAddress = await executor.getAddress();

  const flashFeeBps = Number(envUint("ROBINHOOD_FLASH_SOURCE_FEE_BPS", 0));
  const flashSourceHash = metadataHash("ROBINHOOD_FLASH_SOURCE_METADATA_HASH", {
    chainId,
    adapter: "morpho_blue_flashLoan",
    source: flashSource,
    feeBps: flashFeeBps,
  });
  const v4MetadataHash = metadataHash("ROBINHOOD_V4_ADAPTER_METADATA_HASH", {
    chainId,
    adapter: "uniswap_v4_universal_router_exact_input_single",
    universalRouter,
    poolManager,
    permit2,
  });

  if (flashSource) {
    await (await executor.setFlashSourceAdapter(1, true, flashSource, flashFeeBps, flashSourceHash)).wait();
  }
  if (v3Router) {
    const v3MetadataHash = metadataHash("ROBINHOOD_V3_ADAPTER_METADATA_HASH", {
      chainId,
      adapter: "uniswap_v3_exact_input_single",
      router: v3Router,
      factory: v3Factory,
      quoter: v3Quoter,
    });
    await (await executor.setUniswapV3Adapter(true, v3Router, v3Factory, v3Quoter, v3MetadataHash)).wait();
  }
  if (universalRouter && poolManager && permit2) {
    await (await executor.setUniswapV4Adapter(true, universalRouter, poolManager, permit2, v4MetadataHash)).wait();
  }

  const minProfit = envUint("ROBINHOOD_REPAYMENT_MIN_PROFIT", 0);
  const gasReserve = envUint("ROBINHOOD_REPAYMENT_GAS_RESERVE", 0);
  const maxFlashFeeBps = Number(envUint("ROBINHOOD_MAX_FLASH_FEE_BPS", flashFeeBps));
  await (await executor.setRepaymentGuard(minProfit, gasReserve, maxFlashFeeBps, true)).wait();
  await (await executor.setPaused(true)).wait();

  const readback = await readbackRobinhoodPausedExecutor(executorAddress);
  if (!readback.config.paused) throw new Error("Robinhood executor readback is not paused");

  const output = {
    schemaVersion: "robinhood-paused-executor-deploy-v1",
    network: hre.network.name,
    chainId,
    deployer: deployer.address,
    owner: deployer.address,
    robinhoodPausedFlashLoanExecutorAddress: executorAddress,
    broadcastEnabled: false,
    readyForBroadcast: false,
    adaptersConfigured: {
      flashSource: Boolean(flashSource),
      uniswapV3: Boolean(v3Router),
      uniswapV4: Boolean(universalRouter && poolManager && permit2),
    },
    preflightEvidence: preflight.evidencePath,
    readback,
  };

  const outputDir = path.resolve(__dirname, "../deployments");
  fs.mkdirSync(outputDir, { recursive: true });
  const outputPath = path.join(outputDir, `robinhood-paused-flashloan-${hre.network.name}.json`);
  fs.writeFileSync(outputPath, `${JSON.stringify(output, null, 2)}\n`, "utf8");
  const evidencePath = writeEvidence(readback);
  const robinhoodContracts = updateRobinhoodContractsJson(readback, outputPath, evidencePath);
  console.log(JSON.stringify({ ...output, outputPath, evidencePath, robinhoodContracts }, null, 2));
}

if (require.main === module) {
  main().catch((error) => {
    console.error(error.message || error);
    process.exitCode = 1;
  });
}
