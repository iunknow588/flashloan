const hre = require("hardhat");
const fs = require("fs");

function loadPayload() {
  const payloadPath = process.env.LIQUIDATION_PAYLOAD_PATH || "deployments/liquidation-staticcall-eb5e-dust.json";
  return JSON.parse(fs.readFileSync(payloadPath, "utf8"));
}

function normalizeRequest(raw) {
  return {
    user: hre.ethers.getAddress(raw.user),
    collateralAsset: hre.ethers.getAddress(raw.collateralAsset),
    debtAsset: hre.ethers.getAddress(raw.debtAsset),
    debtToCover: BigInt(raw.debtToCover),
    minCollateralSwapOut: BigInt(raw.minCollateralSwapOut || 0),
    minProfitAmount: BigInt(raw.minProfitAmount || 0),
    deadline: BigInt(raw.deadline),
    gasLimit: BigInt(raw.gasLimit || 0),
    swapPath: (raw.swapPath || []).map((item) => hre.ethers.getAddress(item)),
  };
}

async function main() {
  if (hre.network.name !== "hardhat") {
    throw new Error("debug-liquidation-flow must run on the hardhat fork network");
  }

  const payload = loadPayload();
  const request = normalizeRequest(payload.request);
  const [owner] = await hre.ethers.getSigners();
  const pool = hre.ethers.getAddress((process.env.AAVE_POOL_ADDRESS || "0x794a61358D6845594F94dc1db02a252b5b4814aD").toLowerCase());
  const router = hre.ethers.getAddress((process.env.DEX_ROUTER_ADDRESS || "0x60aE616a2155Ee3d9A68541Ba4544862310933d4").toLowerCase());
  const usdc = hre.ethers.getAddress((process.env.USDC_ADDRESS || "0xB97EF9Ef8734C71904D8002F8b6Bc66Dd9c48a6E").toLowerCase());

  const Executor = await hre.ethers.getContractFactory("AaveV3LiquidationExecutor");
  const executor = await Executor.deploy(pool, router, usdc, owner.address, { gasLimit: 3_000_000 });
  await executor.waitForDeployment();

  console.log(`forkExecutor=${await executor.getAddress()}`);
  console.log(`user=${request.user}`);
  console.log(`collateralAsset=${request.collateralAsset}`);
  console.log(`debtAsset=${request.debtAsset}`);
  console.log(`debtToCover=${request.debtToCover}`);

  const tx = await executor.requestLiquidation(request, { gasLimit: 3_000_000 });
  const receipt = await tx.wait();
  console.log(`tx=${receipt.hash}`);
  console.log(`status=${receipt.status}`);
  console.log(`gasUsed=${receipt.gasUsed}`);
  console.log("fork liquidation transaction simulation passed");
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
