const hre = require("hardhat");
const fs = require("fs");
const path = require("path");
const {
  appendJsonl,
  boolEnv,
  buildBroadcastGate,
  evidencePaths,
  networkContext,
  ownerMatchesSigner,
  receiptReport,
  sanitizeError,
  writeJson,
} = require("./fuji-evidence");

function requireEnv(name, env = process.env) {
  const value = env[name];
  if (!value || !value.trim() || value === "0x...") {
    throw new Error(`${name} is required`);
  }
  return value.trim();
}

function payloadPath(env = process.env) {
  return path.resolve(process.cwd(), env.EXECUTION_PAYLOAD_FILE || "deployments/execution-payload.json");
}

function buildSteps(aavePayload) {
  return aavePayload.plan.steps.map((step) => ({
    router: step.router,
    tokenIn: step.tokenIn,
    tokenOut: step.tokenOut,
    amountIn: BigInt(step.amountIn),
    amountOutMin: BigInt(step.amountOutMin),
    path: step.path,
  }));
}

function reportSummary({ payloadFile, aavePayload, steps, latest, deadline, executorAddress }) {
  return {
    payloadFile,
    executorAddress,
    blockNumber: latest.number,
    blockTimestamp: latest.timestamp,
    deadline: deadline.toString(),
    deadlineSeconds: Number(aavePayload.plan.deadlineSeconds || 600),
    profitToken: aavePayload.plan.profitToken || null,
    minProfitAmount: String(aavePayload.plan.minProfitAmount || "0"),
    borrowAsset: aavePayload.borrowAsset,
    borrowAmount: String(aavePayload.borrowAmount),
    quoteAgeSeconds: aavePayload.quoteAgeSeconds ?? aavePayload.quote_age_seconds ?? null,
    stepCount: steps.length,
    steps: steps.map((step) => ({
      router: step.router,
      tokenIn: step.tokenIn,
      tokenOut: step.tokenOut,
      amountIn: step.amountIn.toString(),
      amountOutMin: step.amountOutMin.toString(),
      path: step.path,
    })),
  };
}

async function run({ hreLike = hre, env = process.env } = {}) {
  requireEnv("FUJI_RPC_URL", env);
  const executorAddress = requireEnv("AAVE_EXECUTOR_ADDRESS", env);
  const payloadFile = payloadPath(env);
  const paths = evidencePaths({ env, strategy: "fuji-aave-payload-static-call" });
  const payload = JSON.parse(fs.readFileSync(payloadFile, "utf8"));
  const aavePayload = payload.contract && payload.contract.aaveSequentialFlashLoanExecutor;

  if (!aavePayload || !aavePayload.compatible || !aavePayload.plan) {
    const reason = aavePayload && aavePayload.reason ? aavePayload.reason : "missing Aave payload";
    throw new Error(`payload is not Aave-compatible: ${reason}`);
  }

  const executor = await hreLike.ethers.getContractAt("AaveSequentialFlashLoanExecutor", executorAddress);
  const latest = await hreLike.ethers.provider.getBlock("latest");
  const deadline = BigInt(latest.timestamp + Number(aavePayload.plan.deadlineSeconds || 600));
  const steps = buildSteps(aavePayload);
  const plan = {
    steps,
    deadline,
    profitToken: aavePayload.plan.profitToken || hreLike.ethers.ZeroAddress,
    minProfitAmount: BigInt(aavePayload.plan.minProfitAmount || 0),
  };
  const ownerGate = await ownerMatchesSigner(hreLike, executor, env);
  const startedAt = new Date().toISOString();

  let staticCall = { ok: false };
  try {
    await executor.requestFlashLoan.staticCall(
      aavePayload.borrowAsset,
      BigInt(aavePayload.borrowAmount),
      plan
    );
    const gasEstimate = await executor.requestFlashLoan.estimateGas(
      aavePayload.borrowAsset,
      BigInt(aavePayload.borrowAmount),
      plan
    );
    staticCall = { ok: true, gasEstimate: gasEstimate.toString() };
  } catch (error) {
    staticCall = {
      ok: false,
      error: sanitizeError(error),
      revertClassification: aavePayload.revertClassification || aavePayload.revert_classification || "unknown",
    };
  }

  const broadcastRequested = boolEnv(env, "AAVE_PAYLOAD_BROADCAST_ENABLED", "FUJI_AAVE_PAYLOAD_BROADCAST_ENABLED");
  const gate = await buildBroadcastGate({
    hreLike,
    env,
    strategy: "small-amount",
    intent: ["AAVE_PAYLOAD_BROADCAST_ENABLED", "FUJI_AAVE_PAYLOAD_BROADCAST_ENABLED"],
    ownerMatches: ownerGate.matches,
    staticCallOk: staticCall.ok,
    payloadFresh: deadline > BigInt(latest.timestamp),
    minProfitChecked: true,
  });

  const report = {
    runId: paths.runId,
    strategy: "aave_payload",
    mode: "static-call",
    startedAt,
    finishedAt: new Date().toISOString(),
    context: await networkContext(hreLike, env),
    owner: ownerGate,
    payload: reportSummary({ payloadFile, aavePayload, steps, latest, deadline, executorAddress }),
    staticCall,
    gate,
    broadcast: {
      requested: broadcastRequested,
      submitted: false,
      reason: broadcastRequested && !gate.ready ? "broadcast gate failed" : "static call evidence only",
    },
  };

  if (!broadcastRequested || !gate.ready) {
    writeJson(paths.reportPath, report);
    appendJsonl(paths.tradeLogPath, {
      runId: paths.runId,
      observedAt: report.finishedAt,
      network: "fuji",
      strategy: "aave_payload",
      action: "static_call",
      success: staticCall.ok,
      reportPath: paths.reportPath,
      error: staticCall.error,
    });
    console.log(JSON.stringify(report, null, 2));
    if (!staticCall.ok) process.exitCode = 1;
    return report;
  }

  const tx = await executor.requestFlashLoan(
    aavePayload.borrowAsset,
    BigInt(aavePayload.borrowAmount),
    plan
  );
  const receipt = await tx.wait();
  const completedReport = {
    ...report,
    finishedAt: new Date().toISOString(),
    broadcast: {
      requested: true,
      submitted: true,
      txHash: tx.hash,
      receiptStatus: receipt.status,
    },
  };
  writeJson(paths.reportPath, completedReport);
  writeJson(paths.receiptPath, receiptReport(receipt));
  appendJsonl(paths.tradeLogPath, {
    runId: paths.runId,
    observedAt: completedReport.finishedAt,
    network: "fuji",
    strategy: "aave_payload",
    action: "broadcast",
    success: receipt.status === 1,
    txHash: tx.hash,
    gasUsed: receipt.gasUsed.toString(),
    reportPath: paths.reportPath,
    receiptPath: paths.receiptPath,
  });
  console.log(JSON.stringify(completedReport, null, 2));
  return completedReport;
}

if (require.main === module) {
  run().catch((error) => {
    console.error(JSON.stringify({ ok: false, error: sanitizeError(error) }, null, 2));
    process.exitCode = 1;
  });
}

module.exports = {
  buildSteps,
  reportSummary,
  run,
};
