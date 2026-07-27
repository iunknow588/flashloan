const hre = require("hardhat");
const fs = require("fs");
const path = require("path");

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

  const [deployer] = await hre.ethers.getSigners();
  console.log(`deployer=${deployer.address}`);

  const Executor = await hre.ethers.getContractFactory("AaveSequentialFlashLoanExecutor");
  const executor = await Executor.deploy(poolAddress, deployer.address);
  await executor.waitForDeployment();

  const executorAddress = await executor.getAddress();
  console.log(`AaveSequentialFlashLoanExecutor=${executorAddress}`);

  const outputDir = path.join(process.cwd(), "deployments");
  fs.mkdirSync(outputDir, { recursive: true });
  fs.writeFileSync(
    path.join(outputDir, "fuji-aave-executor.json"),
    JSON.stringify(
      {
        network: hre.network.name,
        chainId: Number((await hre.ethers.provider.getNetwork()).chainId),
        deployedAt: new Date().toISOString(),
        deployer: deployer.address,
        aavePoolAddress: poolAddress,
        aaveExecutorAddress: executorAddress,
      },
      null,
      2
    )
  );
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
