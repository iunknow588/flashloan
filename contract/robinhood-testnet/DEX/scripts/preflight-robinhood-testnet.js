const fs = require("fs");
const path = require("path");
const hre = require("hardhat");

const ROBINHOOD_TESTNET_CHAIN_JSON = path.resolve(__dirname, "../config/chain.json");
const V4_SWAP_TOPIC = hre.ethers.id("Swap(bytes32,address,int128,int128,uint160,uint128,int24,uint24)");

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
    if (address) {
      return { address, envName: name };
    }
  }
  return { address: "", envName: "" };
}

function envConfigured(...names) {
  return names.some((name) => String(process.env[name] || "").trim());
}

function rpcHost(url) {
  try {
    return new URL(url || "").host;
  } catch {
    return "";
  }
}

function configuredRpcSource(configuredUrl = hre.network?.config?.url || "") {
  const envNames = [
    "ROBINHOOD_TESTNET_RPC_URL",
    "ROBINHOOD_TESTNET_RPC",
    "ROBINHOOD_TESTNET_RPCS",
    "ROBINHOOD_TESTNET_RPC_URLS",
    "ROBINHOOD_TESTNET_ALCHEMY_RPC_URL",
    "ROBINHOOD_TESTNET_ALCHEMY_RPC",
  ];
  for (const name of envNames) {
    if (String(process.env[name] || "").trim()) {
      return { configured: true, source: "env", envName: name, host: rpcHost(process.env[name]) };
    }
  }
  if (rpcHost(configuredUrl) === "rpc.testnet.chain.robinhood.com") {
    return { configured: true, source: "default_public_rpc", envName: "", host: rpcHost(configuredUrl) };
  }
  return { configured: false, source: "missing", envName: "", host: rpcHost(configuredUrl) };
}

function envBool(name, fallback = false) {
  const value = String(process.env[name] || "").trim().toLowerCase();
  if (!value) return fallback;
  if (["1", "true", "yes", "on"].includes(value)) return true;
  if (["0", "false", "no", "off"].includes(value)) return false;
  return fallback;
}

function envNumber(name, fallback) {
  const value = String(process.env[name] || "").trim();
  if (!value) return fallback;
  if (!/^\d+$/.test(value)) return Number.NaN;
  return Number(value);
}

function envString(name, fallback = "") {
  const value = String(process.env[name] || "").trim();
  return value || fallback;
}

function readRobinhoodTestnetChainConfig() {
  try {
    const payload = JSON.parse(fs.readFileSync(ROBINHOOD_TESTNET_CHAIN_JSON, "utf8").replace(/^\uFEFF/, ""));
    return payload && typeof payload === "object" && !Array.isArray(payload) ? payload : {};
  } catch {
    return {};
  }
}

function normalizeRobinhoodTestnetDefaults() {
  const config = readRobinhoodTestnetChainConfig();
  return {
    chainJsonPath: ROBINHOOD_TESTNET_CHAIN_JSON,
    defaultFlashSourceKind: Number(config.defaultFlashSourceKind ?? 4),
    defaultFlashSource: normalizeAddress(config.defaultFlashSource || config.defaultV4PoolManager || ""),
    defaultPermit2: normalizeAddress(config.defaultPermit2 || ""),
    defaultV4PoolManager: normalizeAddress(config.defaultV4PoolManager || ""),
    defaultV4UniversalRouter: normalizeAddress(config.defaultV4UniversalRouter || ""),
    defaultPositionManager: normalizeAddress(config.defaultPositionManager || ""),
    defaultWrappedNative: normalizeAddress(config.defaultWrappedNative || ""),
    defaultMulticall3: normalizeAddress(config.defaultMulticall3 || ""),
  };
}

const ROBINHOOD_TESTNET_DEFAULTS = normalizeRobinhoodTestnetDefaults();

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

async function inspectV4FlashAccountingPools(addressInfo, options = {}) {
  const address = addressInfo.address || "";
  const lookbackBlocks = Number(options.lookbackBlocks ?? envNumber("ROBINHOOD_TESTNET_V4_POOL_LOOKBACK_BLOCKS", 20_000));
  const minUsablePools = Number(options.minUsablePools ?? envNumber("ROBINHOOD_TESTNET_MIN_USABLE_V4_POOL_COUNT", 1));
  const report = {
    attempted: false,
    address: address || null,
    lookbackBlocks,
    minUsablePools,
    fromBlock: null,
    toBlock: null,
    swapLogCount: 0,
    activePoolIds: [],
    usableFlashAccountingPoolCount: 0,
    nativeBalanceWei: null,
    ok: false,
    error: null,
  };
  if (!address) {
    report.error = "pool_manager_address_missing";
    return report;
  }
  report.attempted = true;
  try {
    const latestBlock = await hre.ethers.provider.getBlockNumber();
    const fromBlock = Math.max(0, latestBlock - Math.max(1, lookbackBlocks));
    const [logs, nativeBalance] = await Promise.all([
      hre.ethers.provider.getLogs({
        address,
        topics: [V4_SWAP_TOPIC],
        fromBlock,
        toBlock: latestBlock,
      }),
      hre.ethers.provider.getBalance(address),
    ]);
    const coder = hre.ethers.AbiCoder.defaultAbiCoder();
    const activePoolIds = new Set();
    for (const log of logs) {
      try {
        const decoded = coder.decode(["int128", "int128", "uint160", "uint128", "int24", "uint24"], log.data);
        if (decoded[3] > 0n && log.topics[1]) {
          activePoolIds.add(log.topics[1]);
        }
      } catch {
        // Ignore malformed logs; PoolManager swap logs from the expected topic should decode cleanly.
      }
    }
    report.fromBlock = fromBlock;
    report.toBlock = latestBlock;
    report.swapLogCount = logs.length;
    report.activePoolIds = Array.from(activePoolIds).sort().slice(0, 25);
    report.usableFlashAccountingPoolCount = activePoolIds.size;
    report.nativeBalanceWei = nativeBalance.toString();
    report.ok = activePoolIds.size >= minUsablePools;
    if (!report.ok) report.error = "testnet_usable_pool_missing";
  } catch (error) {
    report.error = error.shortMessage || error.message || String(error);
  }
  return report;
}

async function buildRobinhoodTestnetPreflight() {
  const rpcSource = configuredRpcSource();
  const requireV3Adapter = envBool("ROBINHOOD_TESTNET_REQUIRE_V3", false);
  const flashSourceKind = envNumber("ROBINHOOD_TESTNET_FLASH_SOURCE_KIND", ROBINHOOD_TESTNET_DEFAULTS.defaultFlashSourceKind);
  const minDeployerBalanceWei = BigInt(String(process.env.ROBINHOOD_TESTNET_MIN_DEPLOYER_BALANCE_WEI || "1").trim() || "1");
  const onchainTestMode = envString("ROBINHOOD_TESTNET_ONCHAIN_TEST_MODE", "normal_only");
  const abnormalOnchainTestsEnabled = envBool("ROBINHOOD_TESTNET_ENABLE_ABNORMAL_ONCHAIN_TESTS", false);
  const report = {
    schemaVersion: "robinhood-testnet-preflight-v1",
    runAt: new Date().toISOString(),
    network: hre.network.name,
    expectedChainId: 46630,
    routeRequirements: {
      requireV3Adapter,
      requireV4Adapter: true,
      flashSourceKind,
    },
    onchainTestPolicy: {
      mode: onchainTestMode,
      normalFunctionFirst: onchainTestMode === "normal_only",
      abnormalOnchainTestsEnabled,
      abnormalOnchainTestsDeferred: !abnormalOnchainTestsEnabled,
      tokenConservation: true,
    },
    rpc: {
      configured: rpcSource.configured,
      source: rpcSource.source,
      envName: rpcSource.envName,
      host: rpcSource.host,
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
      minBalanceWei: minDeployerBalanceWei.toString(),
      balanceOk: false,
      error: null,
    },
    flashAccountingLiquidity: {
      attempted: false,
      address: null,
      lookbackBlocks: envNumber("ROBINHOOD_TESTNET_V4_POOL_LOOKBACK_BLOCKS", 20_000),
      minUsablePools: envNumber("ROBINHOOD_TESTNET_MIN_USABLE_V4_POOL_COUNT", 1),
      fromBlock: null,
      toBlock: null,
      swapLogCount: 0,
      activePoolIds: [],
      usableFlashAccountingPoolCount: 0,
      nativeBalanceWei: null,
      ok: false,
      error: "not_checked",
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
    "ROBINHOOD_TESTNET_V4_POOL_MANAGER",
  );
  const v3Router = envAddress(
    "ROBINHOOD_TESTNET_V3_SWAP_ROUTER",
    "ROBINHOOD_TESTNET_V3_ROUTER",
  );
  const v4Router = envAddress(
    "ROBINHOOD_TESTNET_V4_UNIVERSAL_ROUTER",
  );
  if (!v4Router.address) {
    v4Router.address = ROBINHOOD_TESTNET_DEFAULTS.defaultV4UniversalRouter;
    v4Router.envName = "chain_json.defaultV4UniversalRouter";
  }
  const v4PoolManager = envAddress(
    "ROBINHOOD_TESTNET_V4_POOL_MANAGER",
  );
  if (!v4PoolManager.address) {
    v4PoolManager.address = ROBINHOOD_TESTNET_DEFAULTS.defaultV4PoolManager;
    v4PoolManager.envName = "chain_json.defaultV4PoolManager";
  }
  if (!flashSource.address) {
    flashSource.address = ROBINHOOD_TESTNET_DEFAULTS.defaultFlashSource || v4PoolManager.address;
    flashSource.envName = "chain_json.defaultFlashSource";
  }
  let permit2 = envAddress("ROBINHOOD_TESTNET_PERMIT2");
  if (!permit2.address) {
    permit2 = { address: ROBINHOOD_TESTNET_DEFAULTS.defaultPermit2, envName: "chain_json.defaultPermit2" };
  }

  if (report.rpc.ok) {
    report.adapters = await Promise.all([
      codeStatus("flashSource", flashSource, { required: true }),
      codeStatus("v3SwapRouter", v3Router, { required: requireV3Adapter }),
      codeStatus("v4UniversalRouter", v4Router, { required: true }),
      codeStatus("v4PoolManager", v4PoolManager, { required: true }),
      codeStatus("permit2", permit2, { required: true }),
    ]);
    report.flashAccountingLiquidity = await inspectV4FlashAccountingPools(v4PoolManager);
    try {
      const [deployer] = await hre.ethers.getSigners();
      if (deployer) {
        const balance = await hre.ethers.provider.getBalance(deployer.address);
        report.deployer = {
          configured: true,
          address: deployer.address,
          balanceWei: balance.toString(),
          minBalanceWei: minDeployerBalanceWei.toString(),
          balanceOk: balance >= minDeployerBalanceWei,
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
  if (flashSourceKind !== 4) report.blockingReasons.push("robinhood_testnet_requires_v4_poolmanager_flash_source_kind");
  if (abnormalOnchainTestsEnabled) report.blockingReasons.push("abnormal_onchain_tests_must_remain_disabled_until_token_reserve");
  if (flashSourceKind === 4 && !report.flashAccountingLiquidity.ok) {
    report.blockingReasons.push("testnet_usable_pool_missing");
  }
  for (const adapter of report.adapters) {
    if (adapter.required && !adapter.configured) report.blockingReasons.push(`${adapter.label}_address_missing`);
    else if (adapter.required && !adapter.hasCode) report.blockingReasons.push(`${adapter.label}_code_not_verified`);
    else if (!adapter.required && adapter.configured && !adapter.hasCode) {
      report.blockingReasons.push(`${adapter.label}_code_not_verified`);
    }
  }
  report.readyForPausedDeploy =
    report.rpc.ok
    && flashSourceKind === 4
    && report.flashAccountingLiquidity.ok
    && !abnormalOnchainTestsEnabled
    && report.adapters.filter((adapter) => adapter.required).every((adapter) => adapter.ok)
    && report.deployer.configured
    && report.deployer.balanceOk;
  if (!report.deployer.configured) report.blockingReasons.push("deployer_key_missing_or_unavailable");
  else if (!report.deployer.balanceOk) report.blockingReasons.push("deployer_balance_below_minimum");
  report.blockingReasons = Array.from(new Set(report.blockingReasons));
  return report;
}

async function main() {
  const report = await buildRobinhoodTestnetPreflight();
  const reportPath = writeEvidence(report);
  console.log(JSON.stringify({ ...report, reportPath }, null, 2));
  if (!report.readyForPausedDeploy && String(process.env.ROBINHOOD_TESTNET_PREFLIGHT_STRICT || "").trim() === "1") {
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
  inspectV4FlashAccountingPools,
  configuredRpcSource,
  ROBINHOOD_TESTNET_CHAIN_JSON,
  ROBINHOOD_TESTNET_DEFAULTS,
  envBool,
  envNumber,
  envString,
  normalizeAddress,
  readRobinhoodTestnetChainConfig,
  writeEvidence,
};
