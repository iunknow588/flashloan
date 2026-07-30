const hre = require("hardhat");
const fs = require("fs");
const path = require("path");

function requireField(raw, field) {
  if (raw[field] === undefined || raw[field] === null || raw[field] === "") {
    throw new Error(`Missing request.${field}`);
  }
  return raw[field];
}

function normalizeRequest(raw) {
  if (!raw || typeof raw !== "object") {
    throw new Error("Missing liquidation request");
  }

  return {
    user: hre.ethers.getAddress(requireField(raw, "user")),
    collateralAsset: hre.ethers.getAddress(requireField(raw, "collateralAsset")),
    debtAsset: hre.ethers.getAddress(requireField(raw, "debtAsset")),
    debtToCover: BigInt(requireField(raw, "debtToCover")),
    minCollateralSwapOut: BigInt(raw.minCollateralSwapOut || 0),
    minProfitAmount: BigInt(raw.minProfitAmount || 0),
    deadline: BigInt(requireField(raw, "deadline")),
    swapPath: (raw.swapPath || []).map((item) => hre.ethers.getAddress(item)),
  };
}

function readPayload(payloadPath) {
  if (!payloadPath) return {};
  const absolutePath = path.resolve(process.cwd(), payloadPath);
  return JSON.parse(fs.readFileSync(absolutePath, "utf8"));
}

async function main() {
  const payloadPath = process.argv.includes("--payload")
    ? process.argv[process.argv.indexOf("--payload") + 1]
    : "";
  const payload = readPayload(payloadPath);
  const executorAddress = payload.executor || process.env.LIQUIDATION_EXECUTOR_ADDRESS;
  const rawRequest = payload.request || (payload.user ? payload : undefined) || process.env.LIQUIDATION_REQUEST_JSON;
  if (!executorAddress) throw new Error("Missing LIQUIDATION_EXECUTOR_ADDRESS");
  if (!rawRequest) throw new Error("Missing LIQUIDATION_REQUEST_JSON");

  const request = normalizeRequest(typeof rawRequest === "string" ? JSON.parse(rawRequest) : rawRequest);
  const executor = await hre.ethers.getContractAt("AaveV3LiquidationExecutor", executorAddress);
  console.log(`network=${hre.network.name}`);
  console.log(`executor=${await executor.getAddress()}`);
  console.log(`user=${request.user}`);
  console.log(`debtAsset=${request.debtAsset}`);
  console.log(`debtToCover=${request.debtToCover}`);
  await executor.requestLiquidation.staticCall(request);
  console.log("static liquidation simulation passed");
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
