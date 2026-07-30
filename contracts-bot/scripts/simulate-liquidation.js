const hre = require("hardhat");
const fs = require("fs");

function normalizeRequest(raw) {
  return {
    user: raw.user,
    collateralAsset: raw.collateralAsset,
    debtAsset: raw.debtAsset,
    debtToCover: BigInt(raw.debtToCover),
    minCollateralSwapOut: BigInt(raw.minCollateralSwapOut || 0),
    minProfitAmount: BigInt(raw.minProfitAmount || 0),
    deadline: BigInt(raw.deadline),
    swapPath: raw.swapPath || [],
  };
}

async function main() {
  const payloadPath = process.argv.includes("--payload")
    ? process.argv[process.argv.indexOf("--payload") + 1]
    : "";
  const payload = payloadPath ? JSON.parse(fs.readFileSync(payloadPath, "utf8")) : {};
  const executorAddress = payload.executor || process.env.LIQUIDATION_EXECUTOR_ADDRESS;
  const rawRequest = payload.request ? JSON.stringify(payload.request) : process.env.LIQUIDATION_REQUEST_JSON;
  if (!executorAddress) throw new Error("Missing LIQUIDATION_EXECUTOR_ADDRESS");
  if (!rawRequest) throw new Error("Missing LIQUIDATION_REQUEST_JSON");

  const request = normalizeRequest(JSON.parse(rawRequest));
  const executor = await hre.ethers.getContractAt("AaveV3LiquidationExecutor", executorAddress);
  await executor.requestLiquidation.staticCall(request);
  console.log("static liquidation simulation passed");
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
