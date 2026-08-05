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
  if (!value || !value.trim()) {
    throw new Error(`${name} is required`);
  }
  return value.trim();
}

async function main() {
  requireEnv("FUJI_RPC_URL");
  requireEnv("DEPLOYER_PRIVATE_KEY");
  const paths = evidencePaths({ strategy: "fuji-mock-funded-executor-deploy" });

  const [deployer] = await hre.ethers.getSigners();
  console.log(`deployer=${deployer.address}`);

  const Executor = await hre.ethers.getContractFactory("MockFundedExecutor");
  const executor = await Executor.deploy(deployer.address);
  await executor.waitForDeployment();

  console.log(`MockFundedExecutor=${await executor.getAddress()}`);
  const outputDir = path.join(process.cwd(), "deployments");
  const deploymentPath = path.join(outputDir, "fuji-mock-funded-executor.json");
  const output = {
    runId: paths.runId,
    network: "fuji",
    chainId: 43113,
    deployedAt: new Date().toISOString(),
    deployer: deployer.address,
    mockExecutorAddress: await executor.getAddress(),
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
    strategy: "mock_funded_executor_deploy",
    action: "deploy",
    success: true,
    deploymentPath,
    reportPath: paths.reportPath,
    mockExecutorAddress: output.mockExecutorAddress,
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
