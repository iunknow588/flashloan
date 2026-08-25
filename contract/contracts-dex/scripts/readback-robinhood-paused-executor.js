const fs = require("fs");
const path = require("path");
const hre = require("hardhat");

function envAddress(...names) {
  for (const name of names) {
    const value = String(process.env[name] || "").trim();
    if (value && value !== "0x..." && hre.ethers.isAddress(value)) return hre.ethers.getAddress(value);
  }
  return "";
}

function normalize(value) {
  if (Array.isArray(value)) return value.map(normalize);
  if (typeof value === "bigint") return value.toString();
  if (value && typeof value === "object") {
    const result = {};
    for (const [key, item] of Object.entries(value)) {
      if (/^\d+$/.test(key)) continue;
      result[key] = normalize(item);
    }
    return result;
  }
  return value;
}

async function codeStatus(label, address) {
  const value = String(address || "").trim();
  const result = {
    label,
    configured: Boolean(value && hre.ethers.isAddress(value)),
    address: value && hre.ethers.isAddress(value) ? hre.ethers.getAddress(value) : null,
    hasCode: false,
    codeBytes: 0,
    codeHash: null,
  };
  if (!result.address) return result;
  const code = await hre.ethers.provider.getCode(result.address);
  result.hasCode = code !== "0x";
  result.codeBytes = code === "0x" ? 0 : (code.length - 2) / 2;
  result.codeHash = code === "0x" ? null : hre.ethers.keccak256(code);
  return result;
}

function robinhoodContractsPath() {
  return path.resolve(__dirname, "../../robinhood/testnet/contracts.json");
}

function deploymentPath() {
  const configured = String(process.env.ROBINHOOD_EXECUTOR_DEPLOYMENT_FILE || "").trim();
  if (configured) return path.isAbsolute(configured) ? configured : path.resolve(process.cwd(), configured);
  return path.resolve(__dirname, "../deployments", `robinhood-paused-flashloan-${hre.network.name}.json`);
}

function deploymentAddress() {
  const configured = envAddress("ROBINHOOD_EXECUTOR_ADDRESS", "ROBINHOOD_PAUSED_EXECUTOR_ADDRESS");
  if (configured) return configured;
  for (const filePath of [deploymentPath(), robinhoodContractsPath()]) {
    try {
      const payload = JSON.parse(fs.readFileSync(filePath, "utf8"));
      const address = payload.robinhoodPausedFlashLoanExecutorAddress
        || payload.deploymentAddress
        || payload.executorAddress
        || "";
      if (hre.ethers.isAddress(address)) return hre.ethers.getAddress(address);
    } catch {
      // Continue to the next source.
    }
  }
  return "";
}

async function readbackRobinhoodPausedExecutor(executorAddress = "") {
  const address = executorAddress || deploymentAddress();
  if (!address) throw new Error("ROBINHOOD_EXECUTOR_ADDRESS or Robinhood deployment file address is required");
  const executor = await hre.ethers.getContractAt("RobinhoodPausedFlashLoanExecutor", address);
  const [network, blockNumber, code, owner, paused, flashSource, v3Adapter, v4Adapter, repaymentGuard] = await Promise.all([
    hre.ethers.provider.getNetwork(),
    hre.ethers.provider.getBlockNumber(),
    hre.ethers.provider.getCode(address),
    executor.owner(),
    executor.paused(),
    executor.flashSourceAdapter(),
    executor.uniswapV3Adapter(),
    executor.uniswapV4Adapter(),
      executor.repaymentGuard(),
  ]);
  const normalizedFlashSource = normalize(flashSource);
  const normalizedV3Adapter = normalize(v3Adapter);
  const normalizedV4Adapter = normalize(v4Adapter);
  const adapterCode = await Promise.all([
    codeStatus("flashSource", normalizedFlashSource.source),
    codeStatus("v3SwapRouter", normalizedV3Adapter.swapRouter),
    codeStatus("v3Factory", normalizedV3Adapter.factory),
    codeStatus("v3Quoter", normalizedV3Adapter.quoter),
    codeStatus("v4UniversalRouter", normalizedV4Adapter.universalRouter),
    codeStatus("v4PoolManager", normalizedV4Adapter.poolManager),
    codeStatus("permit2", normalizedV4Adapter.permit2),
  ]);

  return {
    schemaVersion: "robinhood-paused-executor-readback-v1",
    runAt: new Date().toISOString(),
    network: hre.network.name,
    chainId: Number(network.chainId),
    blockNumber,
    executor: {
      address,
      contract: "RobinhoodPausedFlashLoanExecutor",
      hasCode: code !== "0x",
      codeBytes: code === "0x" ? 0 : (code.length - 2) / 2,
      codeHash: code === "0x" ? null : hre.ethers.keccak256(code),
    },
    config: {
      owner,
      paused,
      flashSourceAdapter: normalizedFlashSource,
      uniswapV3Adapter: normalizedV3Adapter,
      uniswapV4Adapter: normalizedV4Adapter,
      repaymentGuard: normalize(repaymentGuard),
    },
    adapterCode,
    broadcastEnabled: false,
    readyForBroadcast: false,
    executionGate: paused ? "paused_testnet_rehearsal_only" : "unexpected_unpaused_state",
  };
}

function evidenceStamp() {
  const stamp = new Date().toISOString().replace(/[-:]/g, "").replace(/\.(\d+)Z$/, "$1Z");
  return `${stamp}-${process.pid}`;
}

function writeEvidence(report) {
  const dir = path.resolve(
    __dirname,
    "../deployments/evidence",
    `${evidenceStamp()}_${hre.network.name}-robinhood-paused-readback`,
  );
  fs.mkdirSync(dir, { recursive: true });
  const reportPath = path.join(dir, "report.json");
  fs.writeFileSync(reportPath, `${JSON.stringify(report, null, 2)}\n`, "utf8");
  return reportPath;
}

async function main() {
  const report = await readbackRobinhoodPausedExecutor();
  const reportPath = writeEvidence(report);
  console.log(JSON.stringify({ ...report, reportPath }, null, 2));
}

if (require.main === module) {
  main().catch((error) => {
    console.error(error.message || error);
    process.exitCode = 1;
  });
}

module.exports = {
  deploymentAddress,
  readbackRobinhoodPausedExecutor,
  robinhoodContractsPath,
  writeEvidence,
};
