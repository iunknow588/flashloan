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

async function main() {
  requireEnv("FUJI_RPC_URL");
  requireEnv("DEPLOYER_PRIVATE_KEY");

  const [deployer] = await hre.ethers.getSigners();
  console.log(`deployer=${deployer.address}`);

  const Executor = await hre.ethers.getContractFactory("MockFundedExecutor");
  const executor = await Executor.deploy(deployer.address);
  await executor.waitForDeployment();

  console.log(`MockFundedExecutor=${await executor.getAddress()}`);
  const outputDir = path.join(process.cwd(), "deployments");
  fs.mkdirSync(outputDir, { recursive: true });
  fs.writeFileSync(
    path.join(outputDir, "fuji-mock-funded-executor.json"),
    JSON.stringify(
      {
        network: "fuji",
        chainId: 43113,
        deployedAt: new Date().toISOString(),
        deployer: deployer.address,
        mockExecutorAddress: await executor.getAddress(),
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
