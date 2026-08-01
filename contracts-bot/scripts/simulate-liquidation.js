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
    gasLimit: BigInt(raw.gasLimit || 0),
    swapPath: (raw.swapPath || []).map((item) => hre.ethers.getAddress(item)),
  };
}

function readPayload(payloadPath) {
  if (!payloadPath) return {};
  const absolutePath = path.resolve(process.cwd(), payloadPath);
  return JSON.parse(fs.readFileSync(absolutePath, "utf8").replace(/^\uFEFF/, ""));
}

function requireAddressEnv(name) {
  const value = process.env[name];
  if (!value) throw new Error(`Missing ${name}`);
  return hre.ethers.getAddress(value.toLowerCase());
}

async function signerForExecutor(executor) {
  const owner = await executor.owner();
  await hre.network.provider.request({
    method: "hardhat_impersonateAccount",
    params: [owner],
  });
  await hre.network.provider.send("hardhat_setBalance", [
    owner,
    "0x56BC75E2D63100000",
  ]);
  return hre.ethers.getSigner(owner);
}

async function resolveExecutor(payload) {
  const useConfiguredExecutor = String(process.env.SIMULATE_USE_CONFIGURED_EXECUTOR || "").toLowerCase() === "true";
  if (useConfiguredExecutor && (payload.executor || process.env.LIQUIDATION_EXECUTOR_ADDRESS)) {
    const executorAddress = payload.executor || process.env.LIQUIDATION_EXECUTOR_ADDRESS;
    const executor = await hre.ethers.getContractAt("AaveV3LiquidationExecutor", executorAddress);
    return {
      executor,
      signer: await signerForExecutor(executor),
      source: "configured",
    };
  }

  const [deployer] = await hre.ethers.getSigners();
  const Executor = await hre.ethers.getContractFactory("AaveV3LiquidationExecutor");
  const executor = await Executor.deploy(
    requireAddressEnv("AAVE_POOL_ADDRESS"),
    requireAddressEnv("DEX_ROUTER_ADDRESS"),
    requireAddressEnv("USDC_ADDRESS"),
    deployer.address,
    { gasLimit: 3_000_000 }
  );
  await executor.waitForDeployment();
  return { executor, signer: deployer, source: "temporary-deploy" };
}

async function main() {
  if (hre.network.name !== "hardhat") {
    throw new Error("simulate-liquidation must run on the hardhat fork network; refusing to simulate requestLiquidation with staticCall on a live network");
  }

  const payloadPath = process.argv.includes("--payload")
    ? process.argv[process.argv.indexOf("--payload") + 1]
    : process.env.LIQUIDATION_PAYLOAD_PATH || "";
  const payload = readPayload(payloadPath);
  const rawRequest = payload.request || (payload.user ? payload : undefined) || process.env.LIQUIDATION_REQUEST_JSON;
  if (!rawRequest) throw new Error("Missing LIQUIDATION_REQUEST_JSON");

  const request = normalizeRequest(typeof rawRequest === "string" ? JSON.parse(rawRequest) : rawRequest);
  const { executor, signer, source } = await resolveExecutor(payload);
  console.log(`network=${hre.network.name}`);
  console.log(`mode=fork-transaction`);
  console.log(`executorSource=${source}`);
  console.log(`executor=${await executor.getAddress()}`);
  console.log(`sender=${await signer.getAddress()}`);
  console.log(`user=${request.user}`);
  console.log(`debtAsset=${request.debtAsset}`);
  console.log(`debtToCover=${request.debtToCover}`);
  const tx = await executor.connect(signer).requestLiquidation(request, { gasLimit: 3_000_000 });
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
