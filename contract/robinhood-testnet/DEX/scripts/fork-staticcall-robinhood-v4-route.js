const fs = require("fs");
const path = require("path");
const hre = require("hardhat");
const { loadChainConfig } = require("../../../../scripts/tool/load-chain-config.js");

const REPO_ROOT = path.resolve(__dirname, "../../../..");
const DEFAULT_PLAN_PATH = path.join(
  REPO_ROOT,
  "flashloan",
  "src",
  "runtime",
  "evidence",
  "robinhood-testnet",
  "chain_test",
  "latest",
  "plan.json",
);
const EXECUTOR_ADDRESS = "0x69d9e4ac531d48E388c54EA19B11AABeCE1d59bB";

function readJson(filePath) {
  return JSON.parse(fs.readFileSync(filePath, "utf8").replace(/^\uFEFF/, ""));
}

function writeJson(filePath, payload) {
  fs.mkdirSync(path.dirname(filePath), { recursive: true });
  fs.writeFileSync(filePath, `${JSON.stringify(payload, null, 2)}\n`, "utf8");
}

function stringify(value) {
  if (typeof value === "bigint") return value.toString();
  if (Array.isArray(value)) return value.map(stringify);
  if (value && typeof value === "object") {
    return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, stringify(item)]));
  }
  return value;
}

function normalizeAddress(value, { allowZero = false } = {}) {
  const raw = String(value || "").trim();
  if (!raw || !hre.ethers.isAddress(raw)) return "";
  const resolved = hre.ethers.getAddress(raw);
  if (!allowZero && resolved === hre.ethers.ZeroAddress) return "";
  return resolved;
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

function decodeError(error) {
  const message = error.shortMessage || error.reason || error.message || String(error);
  const data = error.data || error.info?.error?.data || error.error?.data || null;
  return { message, data };
}

function poolKeyAndDirection(hop) {
  const rawIn = normalizeAddress(hop.tokenIn, { allowZero: true });
  const rawOut = normalizeAddress(hop.tokenOut, { allowZero: true });
  const currency0 = BigInt(rawIn) < BigInt(rawOut) ? rawIn : rawOut;
  const currency1 = BigInt(rawIn) < BigInt(rawOut) ? rawOut : rawIn;
  return {
    currency0,
    currency1,
    fee: Number(hop.fee || 3000),
    tickSpacing: Number(hop.tickSpacing || 60),
    hooks: normalizeAddress(hop.hooks || hre.ethers.ZeroAddress, { allowZero: true }) || hre.ethers.ZeroAddress,
    zeroForOne: rawIn === currency0,
    tokenIn: rawIn,
    tokenOut: rawOut,
    amountIn: toBigInt(hop.amountInRaw),
    amountOutMinimum: toBigInt(hop.minAmountOutRaw || hop.amountOutMinimumRaw || 1),
  };
}

function poolIdForKey(key) {
  const encoded = hre.ethers.AbiCoder.defaultAbiCoder().encode(
    ["address", "address", "uint24", "int24", "address"],
    [key.currency0, key.currency1, key.fee, key.tickSpacing, key.hooks],
  );
  return hre.ethers.keccak256(encoded);
}

function expectedExecutorContractAddress(plan) {
  const metadata = plan?.contractMetadata || {};
  return normalizeAddress(
    metadata.deploymentAddress
      || metadata.executorAddress
      || plan.preflightSpec?.executionRequest?.executorContractAddress,
  ) || EXECUTOR_ADDRESS;
}

function buildForkSwaps(plan) {
  const hops = Array.isArray(plan?.preflightSpec?.routeHops) ? plan.preflightSpec.routeHops : [];
  const swaps = [];
  const audit = [];
  const blockers = [];
  for (const hop of hops) {
    const key = poolKeyAndDirection(hop);
    const computedId = poolIdForKey(key);
    const poolMatch = computedId.toLowerCase() === String(hop.pool || "").toLowerCase();
    const hopBlockers = [];
    if (!key.currency0 || !key.currency1) hopBlockers.push("hop_token_missing");
    if (!key.amountIn || key.amountIn <= 0n) hopBlockers.push("amount_in_missing_or_zero");
    if (!key.amountOutMinimum || key.amountOutMinimum <= 0n) hopBlockers.push("min_amount_out_missing_or_zero");
    if (!poolMatch) hopBlockers.push("pool_key_does_not_match_plan");
    if (hopBlockers.length) blockers.push(`hop_${hop.hop}_${hopBlockers.join("_and_")}`);
    swaps.push({
      poolKey: {
        currency0: key.currency0,
        currency1: key.currency1,
        fee: key.fee,
        tickSpacing: key.tickSpacing,
        hooks: key.hooks,
      },
      zeroForOne: key.zeroForOne,
      amountIn: key.amountIn,
      amountOutMinimum: key.amountOutMinimum,
      hookData: "0x",
    });
    audit.push({
      hop: hop.hop,
      pool: hop.pool,
      computedPoolId: computedId,
      poolMatch,
      tokenIn: key.tokenIn,
      tokenOut: key.tokenOut,
      zeroForOne: key.zeroForOne,
      amountInRaw: key.amountIn.toString(),
      minAmountOutRaw: key.amountOutMinimum.toString(),
      blockers: hopBlockers,
    });
  }
  return { swaps, audit, blockers };
}

function buildV4FlashRequest(plan, targets, calldatas) {
  const repayment = plan?.preflightSpec?.repaymentConstraint || {};
  const executionRequest = plan?.preflightSpec?.executionRequest || {};
  const borrowToken = normalizeAddress(repayment.borrowToken || executionRequest.token, { allowZero: true });
  return {
    token: borrowToken,
    amount: toBigInt(repayment.borrowAmountRaw || executionRequest.amount),
    swapTargets: Array.from(targets || []),
    swapCalldatas: Array.from(calldatas || []),
    finalToken: borrowToken,
    minProfit: toBigInt(repayment.minProfitRaw || executionRequest.minProfit || 1),
    gasReserve: toBigInt(repayment.gasReserveRaw || executionRequest.gasReserve || 0),
  };
}

function attachForkEvidenceToPlan(plan, forkEvidence) {
  const next = {
    ...plan,
    forkEvidence,
    simulationEvidence: forkEvidence,
  };
  const doneIds = new Set(["calldata_builder", "risk_guard", "fork_staticcall"]);
  next.checklist = (next.checklist || []).map((item) => {
    if (doneIds.has(item.id)) {
      return { ...item, status: "done", done: true };
    }
    return item;
  });
  return next;
}

function evidenceStamp() {
  const stamp = new Date().toISOString().replace(/[-:]/g, "").replace(/\.(\d+)Z$/, "$1Z");
  return `${stamp}-${process.pid}`;
}

function writeEvidence(report) {
  const dir = path.resolve(
    __dirname,
    "../deployments/evidence",
    `${evidenceStamp()}_${hre.network.name}-robinhood-v4-fork-staticcall`,
  );
  return path.join(dir, "report.json");
}

async function runForkStaticcall() {
  const planPath = path.resolve(process.env.ROBINHOOD_TESTNET_CHAIN_TEST_PLAN || DEFAULT_PLAN_PATH);
  const plan = readJson(planPath);
  const chainConfig = loadChainConfig(46630);
  const forkBlockParam = process.env.ROBINHOOD_TESTNET_FORK_BLOCK;
  const forkBlockNumber = forkBlockParam
    ? Number(forkBlockParam)
    : Number(plan?.attempt?.triggerBlockNumber || plan?.evidence?.triggerBlockNumber || 0) || null;
  const executorAddress = expectedExecutorContractAddress(plan);
  const forkTimeoutMs = Number(process.env.ROBINHOOD_TESTNET_FORK_TIMEOUT_MS || "180000");
  const deadlineSeconds = Number(process.env.ROBINHOOD_TESTNET_FORK_DEADLINE_SECONDS || "3600");
  const report = {
    schemaVersion: "robinhood-testnet-v4-fork-staticcall-v1",
    runAt: new Date().toISOString(),
    network: "robinhood-testnet",
    chainId: 46630,
    mode: "hardhat_fork_readonly_staticcall",
    readOnly: true,
    broadcastEnabled: false,
    planPath,
    rpcSource: chainConfig.configPath,
    fork: {
      requestedBlockNumber: forkBlockNumber,
      planReferenceBlockNumber: Number(plan?.attempt?.triggerBlockNumber || plan?.evidence?.triggerBlockNumber || 0) || null,
      resolvedBlockNumber: null,
      resetOk: false,
      blockTimestamp: null,
    },
    readback: {},
    calldataBuild: { attempted: false, ok: false },
    poolAudit: { attempted: false, ok: false, items: [], blockers: [] },
    request: null,
    pausedGateBefore: { attempted: false, revertedAsPaused: false, error: null },
    forkStaticCall: { attempted: false, ok: false, result: null, error: null },
    forkEstimateGas: { attempted: false, ok: false, gas: null, error: null },
    pausedGateAfter: { attempted: false, revertedAsPaused: false, error: null },
    blockingReasons: [],
    status: "pending",
    passed: false,
  };

  const resetTimer = setTimeout(() => {
    report.blockingReasons.push("fork_rpc_timeout");
  }, forkTimeoutMs);
  resetTimer.unref();

  const resetParams = [{ forking: { jsonRpcUrl: chainConfig.rpcUrl } }];
  if (forkBlockNumber) {
    resetParams[0].forking.blockNumber = forkBlockNumber;
  }
  await hre.network.provider.request({ method: "hardhat_reset", params: resetParams });
  report.fork.resetOk = true;
  if (forkBlockNumber) {
    // EDR requires a local block after the fork point to use the configured hardfork.
    await hre.network.provider.request({ method: "evm_mine", params: [] });
  }
  const latest = await hre.ethers.provider.getBlock("latest");
  report.fork.resolvedBlockNumber = latest?.number || null;
  report.fork.blockTimestamp = latest?.timestamp || null;
  report.fork.executionBlockNumber = latest?.number || null;
  report.fork.forkedFromPlanBlockNumber = report.fork.planReferenceBlockNumber || forkBlockNumber || null;

  const executor = await hre.ethers.getContractAt("RobinhoodPausedFlashLoanExecutor", executorAddress);
  const [code, owner, paused] = await Promise.all([
    hre.ethers.provider.getCode(executorAddress),
    executor.owner(),
    executor.paused(),
  ]);
  report.readback = {
    executorAddress,
    hasCode: code !== "0x",
    codeBytes: code === "0x" ? 0 : (code.length - 2) / 2,
    owner,
    pausedBefore: paused,
  };
  if (!report.readback.hasCode) report.blockingReasons.push("executor_code_missing_on_fork");
  if (report.readback.pausedBefore !== true) report.blockingReasons.push("executor_not_paused_on_fork");

  const built = buildForkSwaps(plan);
  report.poolAudit.attempted = true;
  report.poolAudit.items = built.audit;
  report.poolAudit.blockers = built.blockers;
  report.poolAudit.ok = built.blockers.length === 0;
  if (built.blockers.length) report.blockingReasons.push(...built.blockers);

  if (report.blockingReasons.length || built.swaps.length === 0) {
    report.status = "blocked";
    const reportPath = writeEvidence(report);
    writeJson(reportPath, stringify(report));
    return { report, reportPath, planUpdated: false };
  }

  const deadline = BigInt((report.fork.blockTimestamp || 0) + deadlineSeconds);
  report.calldataBuild.attempted = true;
  const [targets, calldatas, initialToken, finalToken] =
    await executor.buildV4PoolManagerExactInputRouteCalldata.staticCall(built.swaps, deadline);
  report.calldataBuild.ok = true;
  report.calldataBuild.targetCount = targets.length;
  report.calldataBuild.calldataCount = calldatas.length;
  report.calldataBuild.initialToken = initialToken;
  report.calldataBuild.finalToken = finalToken;

  const request = buildV4FlashRequest(plan, targets, calldatas);
  report.request = stringify(request);
  const borrowToken = normalizeAddress(plan.preflightSpec.repaymentConstraint?.borrowToken, { allowZero: true });
  if (initialToken !== borrowToken || finalToken !== borrowToken) {
    report.blockingReasons.push("calldata_token_chain_does_not_match_repayment");
  }
  if (report.calldataBuild.targetCount !== built.swaps.length) {
    report.blockingReasons.push("calldata_hop_count_mismatch");
  }
  if (report.blockingReasons.length) {
    report.status = "blocked";
    const reportPath = writeEvidence(report);
    writeJson(reportPath, stringify(report));
    return { report, reportPath, planUpdated: false };
  }

  await hre.network.provider.request({ method: "hardhat_impersonateAccount", params: [owner] });
  await hre.network.provider.request({
    method: "hardhat_setBalance",
    params: [owner, "0x0DE0B6B3A7640000"],
  });
  const ownerSigner = await hre.ethers.getSigner(owner);
  const ownedExecutor = executor.connect(ownerSigner);

  report.pausedGateBefore.attempted = true;
  try {
    await ownedExecutor.requestV4FlashLoan.staticCall(request);
    report.pausedGateBefore.revertedAsPaused = false;
    report.blockingReasons.push("paused_gate_did_not_block_fork_staticcall");
  } catch (error) {
    report.pausedGateBefore.revertedAsPaused = true;
    report.pausedGateBefore.error = decodeError(error);
  }

  if (report.pausedGateBefore.revertedAsPaused) {
    await (await ownedExecutor.setPaused(false)).wait();
    report.readback.pausedAfterUnpause = await executor.paused();
  }
  if (report.readback.pausedAfterUnpause !== false) {
    report.blockingReasons.push("fork_unpause_failed");
  }
  if (report.blockingReasons.length) {
    report.status = "blocked";
    const reportPath = writeEvidence(report);
    writeJson(reportPath, stringify(report));
    return { report, reportPath, planUpdated: false };
  }

  report.forkStaticCall.attempted = true;
  try {
    report.forkStaticCall.result = await ownedExecutor.requestV4FlashLoan.staticCall(request);
    report.forkStaticCall.ok = true;
  } catch (error) {
    report.forkStaticCall.error = decodeError(error);
    report.blockingReasons.push("fork_staticcall_failed");
  }
  if (report.forkStaticCall.ok) {
    report.forkEstimateGas.attempted = true;
    try {
      report.forkEstimateGas.gas = await ownedExecutor.requestV4FlashLoan.estimateGas(request);
      report.forkEstimateGas.ok = true;
    } catch (error) {
      report.forkEstimateGas.error = decodeError(error);
      report.blockingReasons.push("fork_estimate_gas_failed");
    }
  }

  if (report.forkStaticCall.ok) {
    await (await ownedExecutor.setPaused(true)).wait();
    report.readback.pausedAfter = await executor.paused();
    report.pausedGateAfter.attempted = true;
    try {
      await ownedExecutor.requestV4FlashLoan.staticCall(request);
      report.pausedGateAfter.revertedAsPaused = false;
      report.blockingReasons.push("paused_gate_not_restored_on_fork");
    } catch (error) {
      report.pausedGateAfter.revertedAsPaused = true;
      report.pausedGateAfter.error = decodeError(error);
    }
  }

  report.passed = report.forkStaticCall.ok && report.forkEstimateGas.ok && report.blockingReasons.length === 0;
  report.status = report.passed ? "fork_staticcall_passed" : "fork_staticcall_failed";
  const reportPath = writeEvidence(report);
  writeJson(reportPath, stringify(report));

  let planUpdated = false;
  if (report.passed) {
    const forkEvidence = {
      schemaVersion: report.schemaVersion,
      status: "fork_staticcall_passed",
      passed: true,
      chainId: 46630,
      network: "robinhood-testnet",
      mode: report.mode,
      forkedBlockNumber: report.fork.resolvedBlockNumber,
      runAt: report.runAt,
      readback: report.readback,
      poolAudit: report.poolAudit,
      calldataBuild: {
        ok: report.calldataBuild.ok,
        targetCount: report.calldataBuild.targetCount,
        calldataCount: report.calldataBuild.calldataCount,
        initialToken: report.calldataBuild.initialToken,
        finalToken: report.calldataBuild.finalToken,
      },
      request: report.request,
      forkStaticCall: {
        ok: report.forkStaticCall.ok,
        resultLength: report.forkStaticCall.result ? report.forkStaticCall.result.length : 0,
      },
      forkEstimateGas: {
        ok: report.forkEstimateGas.ok,
        gas: report.forkEstimateGas.gas ? report.forkEstimateGas.gas.toString() : null,
      },
      pausedGate: {
        beforeClosed: report.pausedGateBefore.revertedAsPaused,
        afterRestoredClosed: report.pausedGateAfter.revertedAsPaused,
      },
      evidencePath: reportPath,
      broadcastEnabled: false,
    };
    const updatedPlan = attachForkEvidenceToPlan(plan, forkEvidence);
    writeJson(planPath, updatedPlan);
    planUpdated = true;
  }

  return { report, reportPath, planUpdated };
}

async function main() {
  const result = await runForkStaticcall();
  console.log(JSON.stringify(stringify({ ...result.report, reportPath: result.reportPath, planUpdated: result.planUpdated }), null, 2));
  if (!result.report.passed) process.exitCode = 1;
}

if (require.main === module) {
  main().catch((error) => {
    console.error(error.message || String(error));
    process.exitCode = 1;
  });
}

module.exports = {
  attachForkEvidenceToPlan,
  buildForkSwaps,
  buildV4FlashRequest,
  poolIdForKey,
  runForkStaticcall,
};
