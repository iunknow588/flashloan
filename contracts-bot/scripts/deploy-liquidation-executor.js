const hre = require("hardhat");

function normalizeAddress(value, fallback) {
  const raw = value || fallback;
  return raw ? hre.ethers.getAddress(raw.toLowerCase()) : undefined;
}

async function main() {
  const networkName = hre.network.name;
  const pool = normalizeAddress(
    networkName === "fuji" ? process.env.FUJI_AAVE_POOL_ADDRESS : process.env.AAVE_POOL_ADDRESS
  );
  const router = normalizeAddress(
    networkName === "fuji" ? process.env.FUJI_DEX_ROUTER_ADDRESS : process.env.DEX_ROUTER_ADDRESS
  );
  const usdc = normalizeAddress(
    networkName === "fuji"
      ? process.env.FUJI_USDC_ADDRESS || process.env.USDC_ADDRESS
      : process.env.USDC_ADDRESS,
    "0xB97EF9Ef8734C71904D8002F8b6Bc66Dd9c48a6E"
  );

  if (!pool) throw new Error("Missing AAVE_POOL_ADDRESS");
  if (!router) throw new Error("Missing DEX_ROUTER_ADDRESS");
  if (!usdc) throw new Error("Missing USDC_ADDRESS");

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
