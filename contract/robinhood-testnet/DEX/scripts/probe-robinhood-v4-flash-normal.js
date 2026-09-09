const fs = require("fs");
const path = require("path");
const hre = require("hardhat");
const {
  ROBINHOOD_TESTNET_DEFAULTS,
  envBool,
  normalizeAddress,
} = require("./preflight-robinhood-testnet");

const REPO_ROOT = path.resolve(__dirname, "../../../..");
const APP_RUNTIME_ROOT = path.join(REPO_ROOT, "flashloan", "app_runtime");
const DEFAULT_CHAIN_TEST_PLAN = path.join(
  APP_RUNTIME_ROOT,
  "runtime",
  "evidence",
  "robinhood-testnet",
  "chain_test",
  "latest",
  "plan.json",
);
const DEFAULT_V4_POOL_LIQUIDITY = path.join(
  APP_RUNTIME_ROOT,
  "runtime",
  "evidence",
  "robinhood-testnet",
  "v4_pool_liquidity",
  "latest.json",
);
const DEFAULT_NORMAL_PROBE_EVIDENCE = path.join(
  APP_RUNTIME_ROOT,
  "runtime",
  "evidence",
  "robinhood-testnet",
  "normal_testnet_probe",
  "latest.json",
);
const DEFAULT_PROBE_DEPLOYMENT = path.join(
  REPO_ROOT,
  "contract",
  "robinhood-testnet",
  "DEX",
  "deployments",
  "robinhood-v4-flash-normal-probe-robinhoodTestnet.json",
);

function envAddress(...names) {
  for (const name of names) {
    const address = normalizeAddress(process.env[name]);
    if (address) return { address, envName: name };
  }
  return { address: "", envName: "" };
}

function envCurrencyAddress(...names) {
  for (const name of names) {
    const address = normalizeCurrencyAddress(process.env[name]);
    if (address) return { address, envName: name };
  }
  return { address: "", envName: "" };
}

function envAmount(...names) {
  for (const name of names) {
    const value = String(process.env[name] || "").trim();
    if (value && /^\d+$/.test(value)) return { amount: BigInt(value), envName: name };
  }
  return { amount: 0n, envName: "" };
}

function normalizeCurrencyAddress(value) {
  const raw = String(value || "").trim();
  if (!raw || raw === "0x...") return "";
  if (!hre.ethers.isAddress(raw) && !/^0x[0-9a-fA-F]{40}$/.test(raw)) return "";
  return hre.ethers.getAddress(raw);
}

function readJsonIfExists(filePath) {
  if (!filePath || !fs.existsSync(filePath)) return {};
  try {
    const payload = JSON.parse(fs.readFileSync(filePath, "utf8").replace(/^\uFEFF/, ""));
    return payload && typeof payload === "object" && !Array.isArray(payload) ? payload : {};
  } catch {
    return {};
  }
}

function toBigInt(value) {
  if (typeof value === "bigint") return value;
  const text = String(value || "").trim();
  if (!text) return 0n;
  try {
    return BigInt(text);
  } catch {
    return 0n;
  }
}

function firstDefined(...values) {
  for (const value of values) {
    if (value !== undefined && value !== null && String(value).trim() !== "") return value;
  }
  return "";
}

function planRouteHops(plan) {
  const preflight = plan && typeof plan.preflightSpec === "object" ? plan.preflightSpec : {};
  return Array.isArray(preflight.routeHops) ? preflight.routeHops.filter((item) => item && typeof item === "object") : [];
}

function readProbeDeployment() {
  const deployment = readJsonIfExists(DEFAULT_PROBE_DEPLOYMENT);
  return {
    address: normalizeAddress(firstDefined(deployment.probeAddress, deployment.address, deployment.probe?.address)),
    source: deployment.probeAddress || deployment.address || deployment.probe?.address ? "deployment_file" : "",
    deploymentFile: DEFAULT_PROBE_DEPLOYMENT,
  };
}

function resolveNormalFlashProbeInputs() {
  const evidence = readJsonIfExists(DEFAULT_NORMAL_PROBE_EVIDENCE);
  const plan = readJsonIfExists(DEFAULT_CHAIN_TEST_PLAN);
  const liquidity = readJsonIfExists(DEFAULT_V4_POOL_LIQUIDITY);
  const routeHops = planRouteHops(plan);
  const preflight = plan && typeof plan.preflightSpec === "object" ? plan.preflightSpec : {};
  const repayment = preflight && typeof preflight.repaymentConstraint === "object" ? preflight.repaymentConstraint : {};
  const firstHop = routeHops[0] && typeof routeHops[0] === "object" ? routeHops[0] : {};
  const evidenceProbe = evidence.probe && typeof evidence.probe === "object" ? evidence.probe : {};
  const evidenceToken = evidence.token && typeof evidence.token === "object" ? evidence.token : {};
  const evidenceAmount = evidence.amount && typeof evidence.amount === "object" ? evidence.amount : {};
  const tokenAddress = normalizeCurrencyAddress(
    firstDefined(
      repayment.borrowToken,
      firstHop.tokenIn,
      evidenceToken.address,
    ),
  );
  const evidenceTokenAddress = normalizeCurrencyAddress(evidenceToken.address);
  const evidenceTokenMatches = Boolean(evidenceTokenAddress && tokenAddress && evidenceTokenAddress === tokenAddress);
  const tokenSymbol = String(firstDefined(repayment.borrowSymbol, firstHop.fromSymbol, evidenceToken.symbol)).trim();
  const amountValue = firstDefined(
    repayment.borrowAmountRaw,
    firstHop.amountInRaw,
    evidenceAmount.amount,
  );
  const amount = toBigInt(amountValue);
  const decimals = Number(
    firstDefined(
      firstHop.inputDecimals,
      firstHop.outputDecimals,
      evidenceAmount.decimals,
      18,
    ),
  );
  const humanAmount = amount > 0n ? (Number(amount) / Number(10n ** BigInt(Math.max(0, decimals)))).toString() : "0";
  const deployment = readProbeDeployment();
  const probeAddress = normalizeAddress(
    firstDefined(
      normalizeAddress(process.env.ROBINHOOD_TESTNET_V4_FLASH_PROBE),
      evidenceProbe.address,
      deployment.address,
    ),
  );
  const probeSource = probeAddress
    ? firstDefined(evidenceProbe.source, deployment.source, "internal_evidence")
    : "probe_address_missing";
  return {
    schemaVersion: "robinhood-normal-test-probe-config-v1",
    network: plan.network || "robinhood-testnet",
    chainId: Number(plan.chainId || 46630),
    poolManager: normalizeAddress(
      firstDefined(
        liquidity.poolManager,
        evidence.poolManager,
        ROBINHOOD_TESTNET_DEFAULTS.defaultV4PoolManager,
      ),
    ),
    probe: {
      address: probeAddress,
      source: probeSource,
      deploymentFile: deployment.deploymentFile,
    },
    token: {
      address: tokenAddress,
      symbol: tokenSymbol,
      decimals,
      source: repayment.borrowToken
        ? "chain_test_plan.preflightSpec.repaymentConstraint"
        : firstDefined(evidenceToken.source, "chain_test_plan.preflightSpec.repaymentConstraint"),
      poolManagerBalanceRaw: evidenceTokenMatches
        ? firstDefined(evidenceToken.poolManagerBalanceRaw, evidenceToken.poolmanager_balance_raw)
        : "",
      poolManagerBalance: evidenceTokenMatches
        ? firstDefined(evidenceToken.poolManagerBalance, evidenceToken.poolmanager_balance)
        : "",
    },
    amount: {
      amount: amount.toString(),
      human: humanAmount,
      decimals,
      source: repayment.borrowAmountRaw
        ? "chain_test_plan.preflightSpec.repaymentConstraint.borrowAmountRaw"
        : firstDefined(evidenceAmount.source, "chain_test_plan.preflightSpec.repaymentConstraint.borrowAmountRaw"),
    },
    route: {
      pathText: firstDefined(plan?.route?.pathText, preflight?.pathText),
      closed: Boolean(plan?.route?.closed),
      hopCount: routeHops.length,
    },
    measurementReady: Boolean(tokenAddress && amount > 0n),
    readyForNormalBroadcast: Boolean(probeAddress && tokenAddress && amount > 0n),
    blockingReasons: [
      ...(probeAddress ? [] : ["probe_address_missing"]),
      ...(tokenAddress ? [] : ["token_address_missing"]),
      ...(amount > 0n ? [] : ["normal_flash_amount_missing_or_zero"]),
    ],
    sourceFiles: {
      chainTestPlan: DEFAULT_CHAIN_TEST_PLAN,
      v4PoolLiquidity: DEFAULT_V4_POOL_LIQUIDITY,
      normalProbeEvidence: DEFAULT_NORMAL_PROBE_EVIDENCE,
      probeDeployment: DEFAULT_PROBE_DEPLOYMENT,
    },
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
    `${evidenceStamp()}_${hre.network.name}-robinhood-v4-flash-normal-probe`,
  );
  fs.mkdirSync(dir, { recursive: true });
  const reportPath = path.join(dir, "report.json");
  fs.writeFileSync(reportPath, `${JSON.stringify(report, null, 2)}\n`, "utf8");
  return reportPath;
}

function stringify(value) {
  if (typeof value === "bigint") return value.toString();
  if (Array.isArray(value)) return value.map(stringify);
  if (value && typeof value === "object") {
    return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, stringify(item)]));
  }
  return value;
}

async function localArtifactCodeStatus(contractName) {
  const artifact = await hre.artifacts.readArtifact(contractName);
  const deployedBytecode = String(artifact.deployedBytecode || "");
  return {
    contract: contractName,
    available: deployedBytecode !== "" && deployedBytecode !== "0x",
    codeBytes: deployedBytecode === "0x" ? 0 : (deployedBytecode.length - 2) / 2,
    codeHash: deployedBytecode === "0x" ? null : hre.ethers.keccak256(deployedBytecode),
  };
}

async function deployedCodeStatus(address, contractName) {
  const artifact = await localArtifactCodeStatus(contractName);
  const code = address ? await hre.ethers.provider.getCode(address) : "0x";
  const codeBytes = code === "0x" ? 0 : (code.length - 2) / 2;
  return {
    contract: contractName,
    address,
    hasCode: code !== "0x",
    codeBytes,
    codeHash: code === "0x" ? null : hre.ethers.keccak256(code),
    localArtifact: artifact,
    matchesLocalCodeSize: artifact.available && codeBytes === artifact.codeBytes,
  };
}

async function main() {
  const [signer] = await hre.ethers.getSigners();
  const network = await hre.ethers.provider.getNetwork();
  const resolved = resolveNormalFlashProbeInputs();
  const probeOverride = envAddress("ROBINHOOD_TESTNET_V4_FLASH_PROBE");
  const tokenOverride = envCurrencyAddress("ROBINHOOD_TESTNET_NORMAL_FLASH_TOKEN");
  const amountOverride = envAmount("ROBINHOOD_TESTNET_NORMAL_FLASH_AMOUNT", "ROBINHOOD_TESTNET_MAX_NORMAL_FLASH_AMOUNT");
  const probe = probeOverride.address
    ? { address: probeOverride.address, source: "env", envName: probeOverride.envName }
    : resolved.probe;
  const token = tokenOverride.address
    ? { address: tokenOverride.address, source: "env", envName: tokenOverride.envName }
    : resolved.token;
  const amount = amountOverride.amount > 0n
    ? { amount: amountOverride.amount, source: "env", envName: amountOverride.envName }
    : { amount: toBigInt(resolved.amount.amount), source: resolved.amount.source, envName: "" };
  const allowBroadcast = envBool("ROBINHOOD_TESTNET_ALLOW_NORMAL_FLASH_BROADCAST", false);
  const report = {
    schemaVersion: "robinhood-v4-flash-normal-probe-v1",
    runAt: new Date().toISOString(),
    network: hre.network.name,
    chainId: Number(network.chainId),
    expectedChainId: 46630,
    policy: {
      mode: "normal_only",
      tokenConservation: true,
      abnormalOnchainTestsEnabled: false,
      broadcastRequiresExplicitEnv: "ROBINHOOD_TESTNET_ALLOW_NORMAL_FLASH_BROADCAST=true",
      broadcastEnabled: allowBroadcast,
    },
    defaults: {
      poolManager: ROBINHOOD_TESTNET_DEFAULTS.defaultV4PoolManager,
    },
    signer: signer ? signer.address : null,
    resolvedInputs: resolved,
    probe,
    token,
    amount,
    staticCall: {
      attempted: false,
      ok: false,
      result: null,
      error: null,
    },
    estimateGas: {
      attempted: false,
      ok: false,
      gas: null,
      error: null,
    },
    broadcast: {
      attempted: false,
      ok: false,
      hash: null,
      error: null,
    },
    probeCode: null,
    readyForNormalBroadcast: false,
    blockingReasons: [],
  };

  if (report.chainId !== report.expectedChainId) report.blockingReasons.push("unexpected_chain_id");
  if (!probe.address) report.blockingReasons.push("probe_address_missing");
  if (!token.address) report.blockingReasons.push("token_address_missing");
  if (amount.amount <= 0n) report.blockingReasons.push("normal_flash_amount_missing_or_zero");
  if (!signer) report.blockingReasons.push("signer_missing");
  if (probe.address) {
    report.probeCode = await deployedCodeStatus(probe.address, "RobinhoodV4FlashNormalProbe");
    if (!report.probeCode.hasCode) {
      report.blockingReasons.push("probe_code_missing");
    } else if (!report.probeCode.matchesLocalCodeSize) {
      report.blockingReasons.push("probe_runtime_size_mismatch");
    }
  }

  if (report.blockingReasons.length === 0) {
    const contract = await hre.ethers.getContractAt("RobinhoodV4FlashNormalProbe", probe.address, signer);
    report.staticCall.attempted = true;
    try {
      report.staticCall.result = await contract.runNormalFlash.staticCall(token.address, amount.amount);
      report.staticCall.ok = true;
    } catch (error) {
      report.staticCall.error = error.shortMessage || error.message || String(error);
      report.blockingReasons.push("static_call_failed");
    }

    if (report.staticCall.ok) {
      report.estimateGas.attempted = true;
      try {
        report.estimateGas.gas = await contract.runNormalFlash.estimateGas(token.address, amount.amount);
        report.estimateGas.ok = true;
      } catch (error) {
        report.estimateGas.error = error.shortMessage || error.message || String(error);
        report.blockingReasons.push("estimate_gas_failed");
      }
    }

    report.readyForNormalBroadcast = report.staticCall.ok && report.estimateGas.ok && allowBroadcast;
    if (report.readyForNormalBroadcast) {
      report.broadcast.attempted = true;
      try {
        const tx = await contract.runNormalFlash(token.address, amount.amount);
        const receipt = await tx.wait();
        report.broadcast.ok = true;
        report.broadcast.hash = receipt.hash;
      } catch (error) {
        report.broadcast.error = error.shortMessage || error.message || String(error);
        report.blockingReasons.push("normal_broadcast_failed");
      }
    }
  }

  const clean = stringify(report);
  const reportPath = writeEvidence(clean);
  console.log(JSON.stringify({ ...clean, reportPath }, null, 2));
  if (report.blockingReasons.length > 0) process.exitCode = 1;
}

if (require.main === module) {
  main().catch((error) => {
    console.error(error.message || error);
    process.exitCode = 1;
  });
}
