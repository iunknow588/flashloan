const hre = require("hardhat");
const {
  appendJsonl,
  evidencePaths,
  networkContext,
  sanitizeError,
  writeJson,
} = require("./fuji-evidence");

const USE_FULL_BALANCE = hre.ethers.MaxUint256;

function requireEnv(name) {
  const value = process.env[name];
  if (!value || !value.trim()) {
    throw new Error(`${name} is required`);
  }
  return value.trim();
}

function envBigInt(name, fallback) {
  const value = process.env[name];
  return value && value.trim() ? BigInt(value.trim()) : fallback;
}

async function expectRevert(label, fn) {
  try {
    await fn();
    const message = `${label} unexpectedly succeeded`;
    console.log(`${label}=unexpected_success`);
    return {
      label,
      reverted: false,
      reason: message,
    };
  } catch (error) {
    const message = sanitizeError(error);
    console.log(`${label}=reverted`);
    console.log(`${label}.reason=${message.split("\n")[0]}`);
    return {
      label,
      reverted: true,
      reason: message.split("\n")[0],
    };
  }
}

async function main() {
  requireEnv("FUJI_RPC_URL");

  const executorAddress = requireEnv("MOCK_EXECUTOR_ADDRESS");
  const router = requireEnv("FUJI_DEX_ROUTER");
  const usdc = requireEnv("FUJI_USDC");
  const token = requireEnv("FUJI_ROUNDTRIP_TOKEN");
  const usdcAmountUnits = envBigInt("FUJI_USDC_AMOUNT_UNITS", 1000000n);

  const executor = await hre.ethers.getContractAt("MockFundedExecutor", executorAddress);
  const latest = await hre.ethers.provider.getBlock("latest");
  const validDeadline = BigInt(latest.timestamp + 600);
  const expiredDeadline = BigInt(latest.timestamp - 1);

  const baseSteps = [
    {
      router,
      tokenIn: usdc,
      tokenOut: token,
      amountIn: usdcAmountUnits,
      amountOutMin: 1n,
      path: [usdc, token],
    },
    {
      router,
      tokenIn: token,
      tokenOut: usdc,
      amountIn: USE_FULL_BALANCE,
      amountOutMin: 1n,
      path: [token, usdc],
    },
  ];

  const highMinOutSteps = [
    { ...baseSteps[0], amountOutMin: hre.ethers.MaxUint256 },
    baseSteps[1],
  ];

  const paths = evidencePaths({ strategy: "fuji-guards" });
  const checks = [];
  checks.push(await expectRevert("guard.amountOutMin", () =>
    executor.executePlan.staticCall(highMinOutSteps, usdc, 0, validDeadline)
  ));
  checks.push(await expectRevert("guard.deadline", () =>
    executor.executePlan.staticCall(baseSteps, usdc, 0, expiredDeadline)
  ));
  checks.push(await expectRevert("guard.minProfit", () =>
    executor.executePlan.staticCall(baseSteps, usdc, 1, validDeadline)
  ));
  const report = {
    runId: paths.runId,
    strategy: "mock_funded_guard_revert",
    mode: "static-call",
    startedAt: new Date().toISOString(),
    finishedAt: new Date().toISOString(),
    context: await networkContext(hre, process.env),
    executorAddress,
    blockTimestamp: latest.timestamp,
    checks,
    summary: {
      ok: checks.every((item) => item.reverted),
      checkCount: checks.length,
    },
  };
  writeJson(paths.reportPath, report);
  appendJsonl(paths.tradeLogPath, {
    runId: paths.runId,
    observedAt: report.finishedAt,
    network: "fuji",
    strategy: "mock_funded_guard_revert",
    action: "static_call",
    success: report.summary.ok,
    reportPath: paths.reportPath,
  });
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
  expectRevert,
};
