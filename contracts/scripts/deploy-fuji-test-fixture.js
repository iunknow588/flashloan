const hre = require("hardhat");
const fs = require("fs");
const path = require("path");

function requireEnv(name) {
  const value = process.env[name];
  if (!value || !value.trim()) {
    throw new Error(`${name} is required`);
  }
  return value.trim();
}

async function deploy(name, args) {
  const Factory = await hre.ethers.getContractFactory(name);
  const contract = await Factory.deploy(...args);
  await contract.waitForDeployment();
  console.log(`${name}=${await contract.getAddress()}`);
  return contract;
}

async function main() {
  requireEnv("FUJI_RPC_URL");
  requireEnv("DEPLOYER_PRIVATE_KEY");

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

  await (await usdc.mint(executorAddress, 1_000n * oneUsdc)).wait();
  await (await usdc.mint(routerAddress, 1_000_000n * oneUsdc)).wait();
  await (await token.mint(routerAddress, 1_000_000n * oneToken)).wait();

  // 1 tUSDC -> 1 tARB, then 1 tARB -> 1 tUSDC after decimal normalization.
  await (await router.setRate(usdcAddress, tokenAddress, oneToken, oneUsdc)).wait();
  await (await router.setRate(tokenAddress, usdcAddress, oneUsdc, oneToken)).wait();

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
  fs.writeFileSync(path.join(outputDir, "fuji-fixture.json"), JSON.stringify(output, null, 2));
  console.log(`deploymentFile=${path.join(outputDir, "fuji-fixture.json")}`);
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
