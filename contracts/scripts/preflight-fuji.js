const hre = require("hardhat");

function requireEnv(name) {
  const value = process.env[name];
  if (!value || !value.trim() || value.includes("your_") || value === "0x...") {
    throw new Error(`${name} is required`);
  }
  return value.trim();
}

async function main() {
  const rpcUrl = requireEnv("FUJI_RPC_URL");
  requireEnv("DEPLOYER_PRIVATE_KEY");

  const [deployer] = await hre.ethers.getSigners();
  const network = await hre.ethers.provider.getNetwork();
  const balance = await hre.ethers.provider.getBalance(deployer.address);

  if (network.chainId !== 43113n) {
    throw new Error(`wrong chainId: expected 43113, got ${network.chainId}`);
  }
  if (balance === 0n) {
    throw new Error(`deployer has no Fuji AVAX: ${deployer.address}`);
  }

  await hre.artifacts.readArtifact("MockFundedExecutor");
  await hre.artifacts.readArtifact("AaveSequentialFlashLoanExecutor");

  console.log("fujiPreflight=ok");
  console.log(`chainId=${network.chainId}`);
  console.log(`deployer=${deployer.address}`);
  console.log(`balanceAvax=${hre.ethers.formatEther(balance)}`);
  console.log(`rpcHost=${new URL(rpcUrl).host}`);
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
