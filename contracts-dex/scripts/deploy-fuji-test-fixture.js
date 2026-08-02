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

async function deploy(name, args) {
  const Factory = await hre.ethers.getContractFactory(name);
  const contract = await Factory.deploy(...args, { gasLimit: 6_000_000n });
  await contract.waitForDeployment();
  console.log(`${name}=${await contract.getAddress()}`);
  return contract;
}

async function main() {
  requireEnv("FUJI_RPC_URL");
  requireEnv("DEPLOYER_PRIVATE_KEY");
  const paths = evidencePaths({ strategy: "fuji-fixture-deploy" });

  const [deployer] = await hre.ethers.getSigners();
  console.log(`deployer=${deployer.address}`);

  const usdc = await deploy("TestERC20", ["Test USDC", "tUSDC", 6, deployer.address]);
  const token = await deploy("TestERC20", ["Test Arb Token", "tARB", 18, deployer.address]);
  const router = await deploy("MockSwapRouter", [deployer.address]);
  const executor = await deploy("MockFundedExecutor", [deployer.address]);

  const usdcAddress = await usdc.getAddress();
  const tokenAddress = await token.getAddress();
  const routerAddress = await router.getAddress();
  const executorAddress = await executor.getAddress();

  const oneUsdc = 1_000_000n;
  const oneToken = 1_000_000_000_000_000_000n;

  await (await usdc.mint(executorAddress, 1_000n * oneUsdc, { gasLimit: 300_000n })).wait();
  await (await usdc.mint(routerAddress, 1_000_000n * oneUsdc, { gasLimit: 300_000n })).wait();
  await (await token.mint(routerAddress, 1_000_000n * oneToken, { gasLimit: 300_000n })).wait();

  // 1 tUSDC -> 1 tARB, then 1 tARB -> 1 tUSDC after decimal normalization.
  await (await router.setRate(usdcAddress, tokenAddress, oneToken, oneUsdc, { gasLimit: 250_000n })).wait();
  await (await router.setRate(tokenAddress, usdcAddress, oneUsdc, oneToken, { gasLimit: 250_000n })).wait();

  console.log("");
  console.log("Add these values to .env for mock fixture execution:");
  console.log(`MOCK_EXECUTOR_ADDRESS=${executorAddress}`);
  console.log(`FUJI_DEX_ROUTER=${routerAddress}`);
  console.log(`FUJI_USDC=${usdcAddress}`);
  console.log(`FUJI_ROUNDTRIP_TOKEN=${tokenAddress}`);
  console.log("FUJI_USDC_AMOUNT_UNITS=1000000");
  console.log("FUJI_FIRST_MIN_OUT_UNITS=1");
  console.log("FUJI_FINAL_MIN_OUT_UNITS=1");
  console.log("FUJI_MIN_PROFIT_UNITS=0");

  const output = {
    runId: paths.runId,
    network: "fuji",
    chainId: 43113,
    deployedAt: new Date().toISOString(),
    deployer: deployer.address,
    mockExecutorAddress: executorAddress,
    routerAddress,
    usdcAddress,
    roundtripTokenAddress: tokenAddress,
  };
  const outputDir = path.join(process.cwd(), "deployments");
  fs.mkdirSync(outputDir, { recursive: true });
  const fixturePath = path.join(outputDir, "fuji-fixture.json");
  fs.writeFileSync(fixturePath, `${JSON.stringify(output, null, 2)}\n`);
  const report = {
    ...output,
    context: await networkContext(hre, process.env),
    fixturePath,
    reportPath: paths.reportPath,
  };
  writeJson(paths.reportPath, report);
  appendJsonl(paths.tradeLogPath, {
    runId: paths.runId,
    observedAt: report.deployedAt,
    network: "fuji",
    strategy: "fuji_fixture_deploy",
    action: "deploy",
    success: true,
    fixturePath,
    reportPath: paths.reportPath,
    mockExecutorAddress: executorAddress,
  });
  console.log(`deploymentFile=${fixturePath}`);
  console.log(`evidenceReport=${paths.reportPath}`);
}

if (require.main === module) {
  main().catch((error) => {
    console.error(JSON.stringify({ ok: false, error: sanitizeError(error) }, null, 2));
    process.exitCode = 1;
  });
}
