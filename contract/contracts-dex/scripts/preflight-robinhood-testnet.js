const fs = require("fs");
const path = require("path");
const hre = require("hardhat");

function envAddress(...names) {
  for (const name of names) {
    const value = String(process.env[name] || "").trim();
    if (value && value !== "0x..." && hre.ethers.isAddress(value)) {
      return { address: hre.ethers.getAddress(value), envName: name };
    }
  }
  return { address: "", envName: "" };
}

function envConfigured(...names) {
  return names.some((name) => String(process.env[name] || "").trim());
}

function envBool(name, fallback = false) {
  const value = String(process.env[name] || "").trim().toLowerCase();
  if (!value) return fallback;
  if (["1", "true", "yes", "on"].includes(value)) return true;
  if (["0", "false", "no", "off"].includes(value)) return false;
  return fallback;
}

function evidenceStamp() {
  const stamp = new Date().toISOString().replace(/[-:]/g, "").replace(/\.(\d+)Z$/, "$1Z");
  return `${stamp}-${process.pid}`;
}

function writeEvidence(report) {
  const dir = path.resolve(
    __dirname,
    "../deployments/evidence",
    `${evidenceStamp()}_${hre.network.name}-robinhood-testnet-preflight`,
  );
  fs.mkdirSync(dir, { recursive: true });
  const reportPath = path.join(dir, "report.json");
  fs.writeFileSync(reportPath, `${JSON.stringify(report, null, 2)}\n`, "utf8");
  return reportPath;
}

async function codeStatus(label, addressInfo, { required = false } = {}) {
  const address = addressInfo.address || "";
  const base = {
    label,
    required,
    configured: Boolean(address),
    envName: addressInfo.envName || "",
    address: address || null,
    hasCode: false,
    codeBytes: 0,
    codeHash: null,
    ok: !required,
    error: null,
  };
  if (!address) {
    base.ok = !required;
    base.error = required ? "missing_address" : null;
    return base;
  }
  try {
    const code = await hre.ethers.provider.getCode(address);
    base.hasCode = code !== "0x";
    base.codeBytes = code === "0x" ? 0 : (code.length - 2) / 2;
    base.codeHash = code === "0x" ? null : hre.ethers.keccak256(code);
    base.ok = base.hasCode;
    base.error = base.hasCode ? null : "address_has_no_code";
  } catch (error) {
    base.ok = false;
    base.error = error.message || String(error);
  }
  return base;
}

async function buildRobinhoodTestnetPreflight() {
  const rpcEnvConfigured = envConfigured(
    "ROBINHOOD_TESTNET_RPC_URL",
    "ROBINHOOD_TESTNET_RPC",
    "ROBINHOOD_TESTNET_ALCHEMY_RPC_URL",
    "ROBINHOOD_TESTNET_ALCHEMY_RPC",
  );
  const requireV3Adapter = envBool("ROBINHOOD_TESTNET_REQUIRE_V3", false) || envBool("ROBINHOOD_REQUIRE_V3", false);
  const report = {
    schemaVersion: "robinhood-testnet-preflight-v1",
    runAt: new Date().toISOString(),
    network: hre.network.name,
    expectedChainId: 46630,
    routeRequirements: {
      requireV3Adapter,
      requireV4Adapter: true,
    },
    rpc: {
      configured: rpcEnvConfigured,
      chainId: null,
      blockNumber: null,
      ok: false,
      error: null,
    },
    adapters: [],
    deployer: {
      configured: false,
      address: null,
      balanceWei: null,
      error: null,
    },
    broadcastEnabled: false,
    readyForPausedDeploy: false,
    readyForBroadcast: false,
    blockingReasons: [],
  };

  try {
    const [network, blockNumber] = await Promise.all([
      hre.ethers.provider.getNetwork(),
      hre.ethers.provider.getBlockNumber(),
    ]);
    report.rpc.chainId = Number(network.chainId);
    report.rpc.blockNumber = blockNumber;
    report.rpc.ok = Number(network.chainId) === report.expectedChainId;
    if (!report.rpc.ok) report.rpc.error = `unexpected_chain_id_${Number(network.chainId)}`;
  } catch (error) {
    report.rpc.error = error.message || String(error);
  }

  const flashSource = envAddress(
    "ROBINHOOD_TESTNET_FLASH_SOURCE",
    "ROBINHOOD_TESTNET_MORPHO",
    "ROBINHOOD_FLASH_SOURCE",
    "ROBINHOOD_MORPHO",
  );
  const v3Router = envAddress(
    "ROBINHOOD_TESTNET_V3_SWAP_ROUTER",
    "ROBINHOOD_TESTNET_V3_ROUTER",
    "ROBINHOOD_V3_SWAP_ROUTER",
    "ROBINHOOD_V3_ROUTER",
  );
  const v4Router = envAddress(
    "ROBINHOOD_TESTNET_V4_UNIVERSAL_ROUTER",
    "ROBINHOOD_V4_UNIVERSAL_ROUTER",
    "ROBINHOOD_UNIVERSAL_ROUTER",
  );
  const v4PoolManager = envAddress(
    "ROBINHOOD_TESTNET_V4_POOL_MANAGER",
    "ROBINHOOD_V4_POOL_MANAGER",
    "ROBINHOOD_POOL_MANAGER",
  );
  const permit2 = envAddress("ROBINHOOD_TESTNET_PERMIT2", "ROBINHOOD_PERMIT2");

  if (report.rpc.ok) {
    report.adapters = await Promise.all([
      codeStatus("flashSource", flashSource, { required: true }),
      codeStatus("v3SwapRouter", v3Router, { required: requireV3Adapter }),
      codeStatus("v4UniversalRouter", v4Router, { required: true }),
      codeStatus("v4PoolManager", v4PoolManager, { required: true }),
      codeStatus("permit2", permit2, { required: true }),
    ]);
    try {
      const [deployer] = await hre.ethers.getSigners();
      if (deployer) {
        const balance = await hre.ethers.provider.getBalance(deployer.address);
        report.deployer = {
          configured: true,
          address: deployer.address,
          balanceWei: balance.toString(),
          error: null,
        };
      }
    } catch (error) {
      report.deployer.error = error.message || String(error);
    }
  } else {
    report.adapters = [
      { label: "flashSource", required: true, configured: Boolean(flashSource.address), envName: flashSource.envName, address: flashSource.address || null, ok: false, error: "rpc_unavailable_or_wrong_chain" },
      { label: "v3SwapRouter", required: requireV3Adapter, configured: Boolean(v3Router.address), envName: v3Router.envName, address: v3Router.address || null, ok: !requireV3Adapter && !v3Router.address, error: requireV3Adapter || v3Router.address ? "rpc_unavailable_or_wrong_chain" : null },
      { label: "v4UniversalRouter", required: true, configured: Boolean(v4Router.address), envName: v4Router.envName, address: v4Router.address || null, ok: false, error: "rpc_unavailable_or_wrong_chain" },
      { label: "v4PoolManager", required: true, configured: Boolean(v4PoolManager.address), envName: v4PoolManager.envName, address: v4PoolManager.address || null, ok: false, error: "rpc_unavailable_or_wrong_chain" },
      { label: "permit2", required: true, configured: Boolean(permit2.address), envName: permit2.envName, address: permit2.address || null, ok: false, error: "rpc_unavailable_or_wrong_chain" },
    ];
  }

  if (!report.rpc.configured) report.blockingReasons.push("robinhood_testnet_rpc_env_missing");
  if (!report.rpc.ok) report.blockingReasons.push("robinhood_testnet_rpc_chain_id_not_verified");
  for (const adapter of report.adapters) {
    if (adapter.required && !adapter.configured) report.blockingReasons.push(`${adapter.label}_address_missing`);
    else if (adapter.required && !adapter.hasCode) report.blockingReasons.push(`${adapter.label}_code_not_verified`);
    else if (!adapter.required && adapter.configured && !adapter.hasCode) {
      report.blockingReasons.push(`${adapter.label}_code_not_verified`);
    }
  }
  report.readyForPausedDeploy =
    report.rpc.ok
    && report.adapters.filter((adapter) => adapter.required).every((adapter) => adapter.ok)
    && report.deployer.configured;
  if (!report.deployer.configured) report.blockingReasons.push("deployer_key_missing_or_unavailable");
  report.blockingReasons = Array.from(new Set(report.blockingReasons));
  return report;
}

async function main() {
  const report = await buildRobinhoodTestnetPreflight();
  const reportPath = writeEvidence(report);
  console.log(JSON.stringify({ ...report, reportPath }, null, 2));
  if (!report.readyForPausedDeploy && String(process.env.ROBINHOOD_PREFLIGHT_STRICT || "").trim() === "1") {
    process.exitCode = 1;
  }
}

if (require.main === module) {
  main().catch((error) => {
    console.error(error.message || error);
    process.exitCode = 1;
  });
}

module.exports = {
  buildRobinhoodTestnetPreflight,
  envBool,
  writeEvidence,
};
