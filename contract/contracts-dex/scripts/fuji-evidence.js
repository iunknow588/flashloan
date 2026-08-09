const fs = require("fs");
const path = require("path");

const EXPECTED_FUJI_CHAIN_ID = 43113n;
const EXPECTED_AVALANCHE_CHAIN_ID = 43114n;

function envValue(env, name) {
  const value = env[name];
  return value && String(value).trim() ? String(value).trim() : "";
}

function boolEnv(env, ...names) {
  for (const name of names) {
    const value = envValue(env, name).toLowerCase();
    if (["1", "true", "yes", "on"].includes(value)) return true;
    if (["0", "false", "no", "off"].includes(value)) return false;
  }
  return false;
}

function sanitizeError(error) {
  const text = String(error && (error.shortMessage || error.reason || error.message || error) || "unknown")
    .split("\n")[0]
    .trim();
  return text
    .replace(/https?:\/\/[^\s"'<>]+/gi, "[redacted-url]")
    .replace(/0x[a-fA-F0-9]{64}/g, "[redacted-private-key]");
}

function rpcHost(value) {
  try {
    return new URL(value).host;
  } catch (_) {
    return null;
  }
}

function runId(prefix, date = new Date()) {
  const stamp = date.toISOString().replace(/[-:]/g, "").replace(/\.\d{3}Z$/, "Z");
  const safePrefix = String(prefix || "fuji-run").replace(/[^A-Za-z0-9_-]+/g, "-");
  return `${stamp}_${safePrefix}`;
}

function evidencePaths({ env = process.env, id, strategy }) {
  const selectedRunId = id || envValue(env, "FUJI_RUN_ID") || runId(strategy || "fuji-run");
  const root = path.resolve(process.cwd(), envValue(env, "FUJI_EVIDENCE_DIR") || "deployments/evidence");
  const dir = path.join(root, selectedRunId);
  return {
    runId: selectedRunId,
    dir,
    reportPath: path.join(dir, "report.json"),
    receiptPath: path.join(dir, "receipt.json"),
    tradeLogPath: path.resolve(process.cwd(), envValue(env, "TESTNET_TRADE_LOG") || "deployments/fuji-trades.jsonl"),
  };
}

function toJsonValue(value) {
  if (typeof value === "bigint") return value.toString();
  if (Array.isArray(value)) return value.map(toJsonValue);
  if (value && typeof value === "object") {
    const output = {};
    for (const [key, item] of Object.entries(value)) {
      output[key] = toJsonValue(item);
    }
    return output;
  }
  return value;
}

function writeJson(filePath, payload) {
  fs.mkdirSync(path.dirname(filePath), { recursive: true });
  fs.writeFileSync(filePath, `${JSON.stringify(toJsonValue(payload), null, 2)}\n`);
}

function appendJsonl(filePath, payload) {
  fs.mkdirSync(path.dirname(filePath), { recursive: true });
  fs.appendFileSync(filePath, `${JSON.stringify(toJsonValue(payload))}\n`);
}

function gateCheck(name, ok, details = {}) {
  return {
    name,
    ok: Boolean(ok),
    level: ok ? "ok" : "error",
    ...details,
  };
}

async function buildBroadcastGate({
  hreLike,
  env = process.env,
  strategy,
  intent,
  ownerMatches = null,
  staticCallOk,
  payloadFresh = true,
  minProfitChecked = true,
  expectedChainId = null,
  executionEnvNames = null,
}) {
  const network = await hreLike.ethers.provider.getNetwork();
  const resolvedExpectedChainId = expectedChainId
    ? BigInt(expectedChainId)
    : (String(hreLike.network && hreLike.network.name || "").toLowerCase() === "avalanche"
      ? EXPECTED_AVALANCHE_CHAIN_ID
      : EXPECTED_FUJI_CHAIN_ID);
  const chainIdOk = network.chainId === resolvedExpectedChainId;
  const explicitExecution = executionEnvNames && executionEnvNames.length
    ? boolEnv(env, ...executionEnvNames)
    : boolEnv(env, "FUJI_EXECUTION_ENABLED", "LIQUIDATION_EXECUTION_ENABLED");
  const intentNames = Array.isArray(intent) ? intent : (intent ? [intent] : []);
  const intentEnabled = intentNames.length ? boolEnv(env, ...intentNames) : true;
  const strategyAllowed = ["mock-funded", "small-amount"].includes(strategy);
  const checks = [
    gateCheck("network.chainId", chainIdOk, {
      chainId: Number(network.chainId),
      expectedChainId: Number(resolvedExpectedChainId),
    }),
    gateCheck("execution.enabled", explicitExecution, {
      env: "FUJI_EXECUTION_ENABLED",
    }),
    gateCheck("execution.intent", intentEnabled, {
      env: intentNames,
    }),
    gateCheck("execution.strategy", strategyAllowed, {
      strategy,
    }),
    ...(ownerMatches === null ? [] : [gateCheck("owner.matchesSigner", ownerMatches, {
      ownerMatches: Boolean(ownerMatches),
    })]),
    gateCheck("staticCall.ok", staticCallOk, {
      staticCallOk: Boolean(staticCallOk),
    }),
    gateCheck("payload.fresh", payloadFresh, {
      payloadFresh: Boolean(payloadFresh),
    }),
    gateCheck("profit.checked", minProfitChecked, {
      minProfitChecked: Boolean(minProfitChecked),
    }),
  ];
  return {
    ready: checks.every((item) => item.ok),
    checks,
  };
}

async function networkContext(hreLike, env = process.env) {
  const network = await hreLike.ethers.provider.getNetwork();
  let signer = null;
  try {
    const signers = await hreLike.ethers.getSigners();
    signer = signers[0] && signers[0].address ? hreLike.ethers.getAddress(signers[0].address) : null;
  } catch (_) {
    signer = null;
  }
  return {
    network: hreLike.network && hreLike.network.name ? hreLike.network.name : "unknown",
    chainId: Number(network.chainId),
    rpcHost: rpcHost(envValue(env, "FUJI_RPC_URL")),
    signer,
  };
}

async function ownerMatchesSigner(hreLike, contract, env = process.env) {
  const context = await networkContext(hreLike, env);
  if (!context.signer || typeof contract.owner !== "function") {
    return {
      owner: null,
      signer: context.signer,
      matches: false,
    };
  }
  const owner = hreLike.ethers.getAddress(await contract.owner());
  return {
    owner,
    signer: context.signer,
    matches: owner.toLowerCase() === context.signer.toLowerCase(),
  };
}

function receiptSummary(receipt) {
  if (!receipt) return null;
  return {
    hash: receipt.hash || receipt.transactionHash || null,
    blockNumber: receipt.blockNumber || null,
    status: receipt.status,
    gasUsed: receipt.gasUsed ? receipt.gasUsed.toString() : null,
  };
}

function receiptReport(receipt) {
  if (!receipt) return null;
  let raw = receipt;
  if (typeof receipt.toJSON === "function") {
    raw = receipt.toJSON();
  }
  return {
    summary: receiptSummary(receipt),
    raw,
  };
}

module.exports = {
  EXPECTED_FUJI_CHAIN_ID,
  EXPECTED_AVALANCHE_CHAIN_ID,
  appendJsonl,
  boolEnv,
  buildBroadcastGate,
  envValue,
  evidencePaths,
  networkContext,
  ownerMatchesSigner,
  receiptReport,
  receiptSummary,
  rpcHost,
  runId,
  sanitizeError,
  toJsonValue,
  writeJson,
};
