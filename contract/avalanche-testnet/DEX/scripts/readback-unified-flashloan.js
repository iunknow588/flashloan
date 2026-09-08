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

function deploymentPath() {
  const configured = String(process.env.UNIFIED_EXECUTOR_DEPLOYMENT_FILE || "").trim();
  if (configured) return path.isAbsolute(configured) ? configured : path.resolve(process.cwd(), configured);
  return path.resolve(__dirname, "../deployments", `unified-flashloan-${hre.network.name}.json`);
}

function deploymentAddress() {
  const configured = envAddress("UNIFIED_EXECUTOR_ADDRESS", "TRIANGULAR_UNIFIED_EXECUTOR_ADDRESS");
  if (configured) return configured;
  try {
    const payload = JSON.parse(fs.readFileSync(deploymentPath(), "utf8"));
    const address = payload.unifiedFlashLoanMevExecutorAddress || payload.executorAddress || "";
    return hre.ethers.isAddress(address) ? hre.ethers.getAddress(address) : "";
  } catch {
    return "";
  }
}

function normalizeConfig(value) {
  if (Array.isArray(value)) return value.map(normalizeConfig);
  if (typeof value === "bigint") return value.toString();
  if (value && typeof value === "object") {
    const result = {};
    for (const [key, item] of Object.entries(value)) {
      if (/^\d+$/.test(key)) continue;
      result[key] = normalizeConfig(item);
    }
    return result;
  }
  return value;
}

async function readbackUnifiedExecutor(executorAddress = "") {
  const address = executorAddress || deploymentAddress();
  if (!address) throw new Error("UNIFIED_EXECUTOR_ADDRESS or deployment file address is required");
  const executor = await hre.ethers.getContractAt("UnifiedFlashLoanMevExecutor", address);
  const [network, blockNumber, code, owner, aavePool, usdc, paused, profitSweepEnabled, profitReserveUsdc, profitSweepThreshold, risk, adapter, borrow] =
    await Promise.all([
      hre.ethers.provider.getNetwork(),
      hre.ethers.provider.getBlockNumber(),
      hre.ethers.provider.getCode(address),
      executor.owner(),
      executor.aavePool(),
      executor.usdc(),
      executor.paused(),
      executor.profitSweepEnabled(),
      executor.profitReserveUsdc(),
      executor.profitSweepThreshold(),
      executor.runtimeRiskConfig(),
      executor.adapterConfigs(1),
      executor.borrowConfigs(await executor.usdc()),
    ]);
  return {
    runAt: new Date().toISOString(),
    network: hre.network.name,
    chainId: Number(network.chainId),
    blockNumber,
    executor: {
      address,
      hasCode: code !== "0x",
      codeBytes: (code.length - 2) / 2,
      codeHash: code === "0x" ? null : hre.ethers.keccak256(code),
    },
    config: {
      owner,
      aavePool,
      usdc,
      paused,
      adapterKind1: normalizeConfig(adapter),
      runtimeRiskConfig: normalizeConfig(risk),
      usdcBorrowConfig: normalizeConfig(borrow),
      profitConfig: {
        sweepEnabled: profitSweepEnabled,
        reserveUsdc: profitReserveUsdc.toString(),
        sweepThreshold: profitSweepThreshold.toString(),
      },
    },
    readyForBroadcast: false,
  };
}

function evidenceStamp() {
  const stamp = new Date().toISOString().replace(/[-:]/g, "").replace(/\.(\d+)Z$/, "$1Z");
  return `${stamp}-${process.pid}`;
}

function writeEvidence(report) {
  const dir = path.resolve(__dirname, "../deployments/evidence", `${evidenceStamp()}_${hre.network.name}-unified-readback`);
  fs.mkdirSync(dir, { recursive: true });
  const reportPath = path.join(dir, "report.json");
  fs.writeFileSync(reportPath, `${JSON.stringify(report, null, 2)}\n`, "utf8");
  return reportPath;
}

async function main() {
  const report = await readbackUnifiedExecutor();
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
  readbackUnifiedExecutor,
  deploymentAddress,
};
