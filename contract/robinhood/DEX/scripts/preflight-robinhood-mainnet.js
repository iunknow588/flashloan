const fs = require("fs");
const path = require("path");
const hre = require("hardhat");

const MAINNET_CHAIN_JSON = path.resolve(__dirname, "../config/chain.json");
const CONTRACTS_JSON = path.resolve(__dirname, "../config/contracts.json");
const DEFAULT_MAINNET_RPC = "https://rpc.mainnet.chain.robinhood.com";
const EXPECTED_CHAIN_ID = 4663;

function readJson(filePath) {
  return JSON.parse(fs.readFileSync(filePath, "utf8").replace(/^\uFEFF/, ""));
}

function normalizeAddress(value) {
  const raw = String(value || "").trim();
  if (!raw || raw === "0x...") return "";
  if (hre.ethers.isAddress(raw)) {
    const address = hre.ethers.getAddress(raw);
    return address === hre.ethers.ZeroAddress ? "" : address;
  }
  if (/^0x[0-9a-fA-F]{40}$/.test(raw)) {
    const address = hre.ethers.getAddress(raw.toLowerCase());
    return address === hre.ethers.ZeroAddress ? "" : address;
  }
  return "";
}

function envAddress(...names) {
  for (const name of names) {
    const address = normalizeAddress(process.env[name]);
    if (address) return { address, envName: name };
  }
  return { address: "", envName: "" };
}

function envBool(name, fallback = false) {
  const value = String(process.env[name] || "").trim().toLowerCase();
  if (!value) return fallback;
  return ["1", "true", "yes", "on"].includes(value);
}

function evidenceStamp() {
  const stamp = new Date().toISOString().replace(/[-:]/g, "").replace(/\.(\d+)Z$/, "$1Z");
  return `${stamp}-${process.pid}`;
}

function writeEvidence(report) {
  const dir = path.resolve(
    __dirname,
    "../deployments/evidence",
    `${evidenceStamp()}_${hre.network.name}-robinhood-mainnet-preflight`,
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
    base.ok = base.hasCode;
    base.error = base.hasCode ? null : "address_has_no_code";
  } catch (error) {
    base.ok = false;
    base.error = error.message || String(error);
  }
  return base;
}

async function buildRobinhoodMainnetPreflight() {
  const chainConfig = readJson(MAINNET_CHAIN_JSON);
  const contractsConfig = readJson(CONTRACTS_JSON);
  const requireV3Adapter = envBool("ROBINHOOD_MAINNET_REQUIRE_V3", false);
  const flashSourceKind = Number(process.env.ROBINHOOD_MAINNET_FLASH_SOURCE_KIND || chainConfig.defaultFlashSourceKind || contractsConfig.defaultFlashSourceKind || 4);
  const report = {
    schemaVersion: "robinhood-mainnet-preflight-v1",
    runAt: new Date().toISOString(),
    network: hre.network.name,
    expectedChainId: EXPECTED_CHAIN_ID,
    routeRequirements: {
      requireV3Adapter,
      requireV4Adapter: true,
      flashSourceKind,
    },
    rpc: {
      configured: Boolean(hre.network.config.url),
      host: "",
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
      ok: false,
      error: null,
    },
    broadcastEnabled: false,
    readyForPausedDeploy: false,
    blockingReasons: [],
  };
  try {
    const url = new URL(hre.network.config.url || "");
    report.rpc.host = url.host;
  } catch {
    report.rpc.host = "";
  }
  try {
    const [network, blockNumber] = await Promise.all([
      hre.ethers.provider.getNetwork(),
      hre.ethers.provider.getBlockNumber(),
    ]);
    report.rpc.chainId = Number(network.chainId);
    report.rpc.blockNumber = blockNumber;
    report.rpc.ok = report.rpc.chainId === EXPECTED_CHAIN_ID;
    if (!report.rpc.ok) report.rpc.error = `unexpected_chain_id_${report.rpc.chainId}`;
  } catch (error) {
    report.rpc.error = error.message || String(error);
  }

  const flashSource = envAddress("ROBINHOOD_MAINNET_FLASH_SOURCE", "ROBINHOOD_MAINNET_V4_POOL_MANAGER");
  const v3Router = envAddress("ROBINHOOD_MAINNET_V3_SWAP_ROUTER", "ROBINHOOD_MAINNET_V3_ROUTER");
  const v4Router = envAddress("ROBINHOOD_MAINNET_V4_UNIVERSAL_ROUTER");
  const v4PoolManager = envAddress("ROBINHOOD_MAINNET_V4_POOL_MANAGER");
  let permit2 = envAddress("ROBINHOOD_MAINNET_PERMIT2");
  if (!flashSource.address) {
    flashSource.address = normalizeAddress(chainConfig.defaultV4PoolManager || contractsConfig.defaultV4PoolManager);
    flashSource.envName = "chain_json.defaultV4PoolManager";
  }
  if (!v4PoolManager.address) {
    v4PoolManager.address = normalizeAddress(chainConfig.defaultV4PoolManager || contractsConfig.defaultV4PoolManager);
    v4PoolManager.envName = "chain_json.defaultV4PoolManager";
  }
  if (!v4Router.address) {
    v4Router.address = normalizeAddress(chainConfig.defaultV4UniversalRouter || contractsConfig.defaultV4UniversalRouter);
    v4Router.envName = "chain_json.defaultV4UniversalRouter";
  }
  if (!permit2.address) {
    permit2 = { address: normalizeAddress(chainConfig.defaultPermit2 || contractsConfig.defaultPermit2), envName: "chain_json.defaultPermit2" };
  }

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
          minBalanceWei: "0",
          ok: balance > 0n,
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

  if (!report.rpc.configured) report.blockingReasons.push("robinhood_mainnet_rpc_env_missing");
  if (!report.rpc.ok) report.blockingReasons.push("robinhood_mainnet_rpc_chain_id_not_verified");
  if (flashSourceKind !== 4) report.blockingReasons.push("robinhood_mainnet_requires_v4_poolmanager_flash_source_kind");
  for (const adapter of report.adapters) {
    if (adapter.required && !adapter.configured) report.blockingReasons.push(`${adapter.label}_address_missing`);
    else if (adapter.required && !adapter.hasCode) report.blockingReasons.push(`${adapter.label}_code_not_verified`);
  }
  report.blockingReasons.push("mainnet_broadcast_gate_closed");
  if (report.deployer.ok === false) {
    report.blockingReasons.push("deployer_balance_insufficient");
  }
  report.readyForPausedDeploy = false;
  report.blockingReasons = Array.from(new Set(report.blockingReasons));
  return report;
}

async function main() {
  const report = await buildRobinhoodMainnetPreflight();
  const reportPath = writeEvidence(report);
  console.log(JSON.stringify({ ...report, reportPath }, null, 2));
  if (report.blockingReasons.length > 0) process.exitCode = 1;
}

if (require.main === module) {
  main().catch((error) => {
    console.error(error.message || error);
    process.exitCode = 1;
  });
}

module.exports = {
  buildRobinhoodMainnetPreflight,
  EXPECTED_CHAIN_ID,
  MAINNET_CHAIN_JSON,
  CONTRACTS_JSON,
  DEFAULT_MAINNET_RPC,
  envBool,
  normalizeAddress,
  writeEvidence,
};
