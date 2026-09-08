const hre = require("hardhat");

const EXPECTED_FUJI_CHAIN_ID = 43113n;
const PLACEHOLDER_VALUES = new Set(["0x...", "0xyour_private_key"]);
const OWNER_ABI = ["function owner() view returns (address)", "function paused() view returns (bool)"];
const AAVE_EXECUTOR_ABI = [
  "function owner() view returns (address)",
  "function paused() view returns (bool)",
  "function pool() view returns (address)",
];

function isPlaceholder(value) {
  const text = String(value || "").trim();
  return !text || PLACEHOLDER_VALUES.has(text) || text.includes("your_");
}

function envValue(env, name) {
  const value = env[name];
  return value && value.trim() ? value.trim() : "";
}

function boolEnv(env, ...names) {
  for (const name of names) {
    const value = envValue(env, name).toLowerCase();
    if (["1", "true", "yes", "on"].includes(value)) return true;
    if (["0", "false", "no", "off"].includes(value)) return false;
  }
  return false;
}

function rpcHost(value) {
  try {
    return new URL(value).host;
  } catch (_) {
    return null;
  }
}

function sanitizeError(error) {
  const firstLine = String(error && (error.shortMessage || error.reason || error.message || error) || "unknown")
    .split("\n")[0]
    .trim();
  return firstLine
    .replace(/https?:\/\/[^\s"'<>]+/gi, "[redacted-url]")
    .replace(/0x[a-fA-F0-9]{64}/g, "[redacted-private-key]");
}

function result(name, ok, details = {}) {
  return {
    name,
    ok: Boolean(ok),
    level: ok ? "ok" : (details.level || "error"),
    ...details,
  };
}

function envCheck(env, name, { required = false, secret = false } = {}) {
  const value = envValue(env, name);
  const configured = !isPlaceholder(value);
  const ok = required ? configured : true;
  const details = {
    configured,
    required,
  };
  if (!secret && configured) {
    details.value = name.endsWith("_RPC_URL") ? rpcHost(value) : value;
  }
  if (secret && configured) {
    details.redacted = true;
  }
  if (!ok) {
    details.level = "error";
    details.error = `${name} is required`;
  }
  return result(`env.${name}`, ok, details);
}

function addressCheck(ethers, env, name, { required = false } = {}) {
  const value = envValue(env, name);
  if (isPlaceholder(value)) {
    return result(`address.${name}`, !required, {
      configured: false,
      required,
      level: required ? "error" : "warn",
      error: required ? `${name} is required` : `${name} is not configured`,
    });
  }
  if (!ethers.isAddress(value)) {
    return result(`address.${name}`, false, {
      configured: true,
      required,
      level: "error",
      error: `${name} is not a valid address`,
    });
  }
  return result(`address.${name}`, true, {
    configured: true,
    required,
    address: ethers.getAddress(value),
  });
}

async function codeCheck(provider, ethers, env, name, { required = false } = {}) {
  const base = addressCheck(ethers, env, name, { required });
  if (!base.ok || !base.configured) return base;
  try {
    const code = await provider.getCode(base.address);
    const hasCode = Boolean(code && code !== "0x");
    return result(`code.${name}`, hasCode, {
      configured: true,
      required,
      address: base.address,
      hasCode,
      ...(hasCode ? {} : {
        level: required ? "error" : "warn",
        error: `${name} has no contract code`,
      }),
    });
  } catch (error) {
    return result(`code.${name}`, false, {
      configured: true,
      required,
      address: base.address,
      level: "error",
      error: sanitizeError(error),
    });
  }
}

async function ownerCheck(hreLike, env, name, abi, expectedOwner, { poolEnvName = null } = {}) {
  const ethers = hreLike.ethers;
  const addressResult = addressCheck(ethers, env, name);
  if (!addressResult.ok || !addressResult.configured) return addressResult;
  try {
    const contract = await ethers.getContractAt(abi, addressResult.address);
    const owner = ethers.getAddress(await contract.owner());
    const paused = Boolean(await contract.paused());
    const ownerMatches = owner.toLowerCase() === expectedOwner.toLowerCase();
    const details = {
      configured: true,
      address: addressResult.address,
      owner,
      ownerMatches,
      paused,
    };
    if (poolEnvName) {
      const expectedPool = addressCheck(ethers, env, poolEnvName);
      const actualPool = ethers.getAddress(await contract.pool());
      details.pool = actualPool;
      details.poolMatches = expectedPool.ok && expectedPool.configured
        ? actualPool.toLowerCase() === expectedPool.address.toLowerCase()
        : false;
      details.expectedPoolConfigured = Boolean(expectedPool.configured);
    }
    const ok = ownerMatches && !paused && (poolEnvName ? details.poolMatches : true);
    return result(`contract.${name}`, ok, {
      ...details,
      level: ok ? "ok" : "error",
      error: ok ? undefined : `${name} owner/pool/paused check failed`,
    });
  } catch (error) {
    return result(`contract.${name}`, false, {
      configured: true,
      address: addressResult.address,
      level: "error",
      error: sanitizeError(error),
    });
  }
}

async function artifactCheck(artifacts, name) {
  try {
    await artifacts.readArtifact(name);
    return result(`artifact.${name}`, true);
  } catch (error) {
    return result(`artifact.${name}`, false, { error: sanitizeError(error) });
  }
}

function summarizeChecks(checks, executionEnabled) {
  const errors = checks.filter((item) => item.level === "error" && !item.ok);
  const warnings = checks.filter((item) => item.level === "warn");
  return {
    ok: errors.length === 0,
    readyForBroadcast: errors.length === 0 && warnings.length === 0 && executionEnabled,
    errorCount: errors.length,
    warningCount: warnings.length,
  };
}

async function buildFujiPreflightReport(hreLike = hre, env = process.env) {
  const ethers = hreLike.ethers;
  const checks = [];
  const startedAt = new Date().toISOString();
  const rpcUrl = envValue(env, "FUJI_RPC_URL");
  const executionEnabled = boolEnv(env, "FUJI_EXECUTION_ENABLED", "LIQUIDATION_EXECUTION_ENABLED");

  checks.push(envCheck(env, "FUJI_RPC_URL", { required: true }));
  checks.push(envCheck(env, "DEPLOYER_PRIVATE_KEY", { required: true, secret: true }));

  const network = await ethers.provider.getNetwork();
  let deployerAddress = null;
  try {
    const signers = await ethers.getSigners();
    const deployer = signers[0];
    if (!deployer || !deployer.address) {
      checks.push(result("deployer.signer", false, {
        level: "error",
        error: "no deployer signer is available",
      }));
    } else {
      deployerAddress = ethers.getAddress(deployer.address);
      const balance = await ethers.provider.getBalance(deployerAddress);
      const nonce = await ethers.provider.getTransactionCount(deployerAddress, "pending");
      checks.push(result("deployer.balance", balance > 0n, {
        address: deployerAddress,
        balanceAvax: ethers.formatEther(balance),
        error: balance > 0n ? undefined : "deployer has no Fuji AVAX",
      }));
      checks.push(result("deployer.nonce", true, { address: deployerAddress, pendingNonce: nonce }));
    }
  } catch (error) {
    checks.push(result("deployer.signer", false, {
      level: "error",
      error: sanitizeError(error),
    }));
  }

  checks.push(result("network.chainId", network.chainId === EXPECTED_FUJI_CHAIN_ID, {
    chainId: Number(network.chainId),
    expectedChainId: Number(EXPECTED_FUJI_CHAIN_ID),
    error: network.chainId === EXPECTED_FUJI_CHAIN_ID ? undefined : `wrong chainId: expected 43113, got ${network.chainId}`,
  }));
  checks.push(result("execution.enabled", executionEnabled, {
    level: executionEnabled ? "ok" : "warn",
    enabled: executionEnabled,
    error: executionEnabled ? undefined : "execution switch is not explicitly enabled",
  }));

  checks.push(await artifactCheck(hreLike.artifacts, "MockFundedExecutor"));
  checks.push(await artifactCheck(hreLike.artifacts, "AaveSequentialFlashLoanExecutor"));
  checks.push(await artifactCheck(hreLike.artifacts, "OnchainDynamicAaveExecutor"));

  for (const name of ["MOCK_EXECUTOR_ADDRESS", "AAVE_POOL_ADDRESS", "AAVE_EXECUTOR_ADDRESS", "ONCHAIN_DYNAMIC_AAVE_EXECUTOR_ADDRESS", "FUJI_DEX_ROUTER", "FUJI_USDC", "FUJI_ROUNDTRIP_TOKEN"]) {
    checks.push(await codeCheck(ethers.provider, ethers, env, name));
  }

  if (deployerAddress) {
    checks.push(await ownerCheck(hreLike, env, "MOCK_EXECUTOR_ADDRESS", OWNER_ABI, deployerAddress));
    checks.push(await ownerCheck(hreLike, env, "AAVE_EXECUTOR_ADDRESS", AAVE_EXECUTOR_ABI, deployerAddress, { poolEnvName: "AAVE_POOL_ADDRESS" }));
    checks.push(await ownerCheck(hreLike, env, "ONCHAIN_DYNAMIC_AAVE_EXECUTOR_ADDRESS", AAVE_EXECUTOR_ABI, deployerAddress, { poolEnvName: "AAVE_POOL_ADDRESS" }));
  } else {
    checks.push(result("contract.ownerChecks", false, {
      level: "error",
      error: "owner checks require a deployer signer",
    }));
  }

  const summary = summarizeChecks(checks, executionEnabled);
  return {
    network: "fuji",
    rpcHost: rpcHost(rpcUrl),
    startedAt,
    finishedAt: new Date().toISOString(),
    deployer: deployerAddress,
    summary,
    checks,
  };
}

async function main() {
  const report = await buildFujiPreflightReport();
  console.log(JSON.stringify(report, null, 2));
  if (!report.summary.ok) {
    process.exitCode = 1;
  }
}

if (require.main === module) {
  main().catch((error) => {
    console.error(JSON.stringify({ ok: false, error: sanitizeError(error) }, null, 2));
    process.exitCode = 1;
  });
}

module.exports = {
  addressCheck,
  buildFujiPreflightReport,
  boolEnv,
  envCheck,
  isPlaceholder,
  rpcHost,
  sanitizeError,
  summarizeChecks,
};
