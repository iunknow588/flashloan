const hre = require("hardhat");

function requiredAddress(name) {
  const raw = process.env[name];
  if (!raw) {
    throw new Error(`Missing ${name}`);
  }
  return hre.ethers.getAddress(raw.toLowerCase());
}

async function main() {
  const networkName = hre.network.name;
  const prefix = networkName === "fuji" ? "FUJI_" : "";
  const pool = requiredAddress(`${prefix}AAVE_POOL_ADDRESS`);
  const router = requiredAddress(`${prefix}DEX_ROUTER_ADDRESS`);
  const usdc = requiredAddress(`${prefix}USDC_ADDRESS`);

  const [deployer] = await hre.ethers.getSigners();
  const Executor = await hre.ethers.getContractFactory("AaveV3LiquidationExecutor");
  const executor = await Executor.deploy(pool, router, usdc, deployer.address, {
    gasLimit: 3_000_000,
  });
  await executor.waitForDeployment();

  console.log(`network=${networkName}`);
  console.log(`deployer=${deployer.address}`);
  console.log(`executor=${await executor.getAddress()}`);
  console.log(`pool=${pool}`);
  console.log(`router=${router}`);
  console.log(`usdc=${usdc}`);
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
