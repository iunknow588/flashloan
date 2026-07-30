const hre = require("hardhat");

async function main() {
  const networkName = hre.network.name;
  const pool =
    networkName === "fuji"
      ? process.env.FUJI_AAVE_POOL_ADDRESS || process.env.AAVE_POOL_ADDRESS
      : process.env.AAVE_POOL_ADDRESS;
  const router =
    networkName === "fuji"
      ? process.env.FUJI_DEX_ROUTER_ADDRESS || process.env.DEX_ROUTER_ADDRESS
      : process.env.DEX_ROUTER_ADDRESS;

  if (!pool) throw new Error("Missing AAVE_POOL_ADDRESS");
  if (!router) throw new Error("Missing DEX_ROUTER_ADDRESS");

  const [deployer] = await hre.ethers.getSigners();
  const Executor = await hre.ethers.getContractFactory("AaveV3LiquidationExecutor");
  const executor = await Executor.deploy(pool, router, deployer.address);
  await executor.waitForDeployment();

  console.log(`network=${networkName}`);
  console.log(`deployer=${deployer.address}`);
  console.log(`executor=${await executor.getAddress()}`);
  console.log(`pool=${pool}`);
  console.log(`router=${router}`);
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
