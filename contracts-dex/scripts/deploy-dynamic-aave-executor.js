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

async function main() {
  requireEnv("FUJI_RPC_URL");
  requireEnv("DEPLOYER_PRIVATE_KEY");
  const poolAddress = requireEnv("AAVE_POOL_ADDRESS");
  const paths = evidencePaths({ strategy: "fuji-dynamic-aave-executor-deploy" });

  const [deployer] = await hre.ethers.getSigners();
  console.log(`deployer=${deployer.address}`);

  const Executor = await hre.ethers.getContractFactory("OnchainDynamicAaveExecutor");
  const executor = await Executor.deploy(poolAddress, deployer.address);
  await executor.waitForDeployment();

  const executorAddress = await executor.getAddress();
  console.log(`ONCHAIN_DYNAMIC_AAVE_EXECUTOR_ADDRESS=${executorAddress}`);

  const outputDir = path.join(process.cwd(), "deployments");
  const deploymentPath = path.join(outputDir, "fuji-dynamic-aave-executor.json");
  const output = {
    runId: paths.runId,
    network: hre.network.name,
    chainId: Number((await hre.ethers.provider.getNetwork()).chainId),
    deployedAt: new Date().toISOString(),
    deployer: deployer.address,
    aavePoolAddress: poolAddress,
    dynamicAaveExecutorAddress: executorAddress,
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
    network: "fuji",
    strategy: "dynamic_aave_executor_deploy",
    action: "deploy",
    success: true,
    deploymentPath,
    reportPath: paths.reportPath,
    dynamicAaveExecutorAddress: executorAddress,
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
