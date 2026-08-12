const fs = require("fs");
const path = require("path");
const hre = require("hardhat");
const { AVALANCHE_V3_PROFILE } = require("./preflight-unified-flashloan");
const { readbackUnifiedExecutor } = require("./readback-unified-flashloan");
const executorArtifact = require("../artifacts/src/UnifiedFlashLoanMevExecutor.sol/UnifiedFlashLoanMevExecutor.json");

const DEFAULT_AAVE_POOL = "0x794a61358D6845594F94dc1DB02A252b5b4814aD";
const DEFAULT_USDC = "0xB97EF9Ef8734C71904D8002F8b6Bc66Dd9c48a6E";

function envAddress(...names) {
  for (const name of names) {
    const value = String(process.env[name] || "").trim();
    if (value && value !== "0x..." && hre.ethers.isAddress(value)) return hre.ethers.getAddress(value);
  }
  return "";
}

function envBigInt(name, fallback) {
  const value = String(process.env[name] || "").trim();
  return value ? BigInt(value) : BigInt(fallback);
}

function envBool(name, fallback) {
  const value = String(process.env[name] || "").trim().toLowerCase();
  if (!value) return fallback;
  return !["0", "false", "no", "off"].includes(value);
}

function runtimeTradesPath() {
  const configured = String(process.env.UNIFIED_FORK_RUNTIME_TRADES_FILE || "").trim();
  if (!configured) return "";
  return path.isAbsolute(configured) ? configured : path.resolve(process.cwd(), configured);
}

function normalizePool(pool) {
  return {
    adapterKind: Number(pool?.adapterKind ?? pool?.adapter_kind ?? 0),
    pool: hre.ethers.isAddress(pool?.pool || "") ? hre.ethers.getAddress(pool.pool) : hre.ethers.ZeroAddress,
  };
}

function normalizeTrade(trade, index) {
  const pools = Array.isArray(trade?.pools) ? trade.pools.slice(0, 5).map(normalizePool) : [];
  while (pools.length < 5) pools.push({ adapterKind: 0, pool: hre.ethers.ZeroAddress });
  return {
    tradeIndex: BigInt(trade?.tradeIndex ?? trade?.trade_index ?? index),
    tokenX: hre.ethers.getAddress(trade.tokenX || trade.token_x),
    tokenY: hre.ethers.getAddress(trade.tokenY || trade.token_y),
    pools,
  };
}

function loadRuntimeTrades() {
  const raw = String(process.env.UNIFIED_FORK_RUNTIME_TRADES_JSON || "").trim();
  const file = runtimeTradesPath();
  let payload = null;
  if (raw) payload = JSON.parse(raw);
  if (!payload && file) payload = JSON.parse(fs.readFileSync(file, "utf8"));
  const source = Array.isArray(payload?.runtimeTrades)
    ? payload.runtimeTrades
    : Array.isArray(payload?.trades)
      ? payload.trades
      : Array.isArray(payload)
        ? payload
        : [];
  return source.map(normalizeTrade);
}

function executionParams() {
  return {
    amount: envBigInt("UNIFIED_FORK_USDC_AMOUNT", process.env.UNIFIED_BORROW_AMOUNT_UNITS || "100000000"),
    deadline: BigInt(Math.floor(Date.now() / 1000) + Number(process.env.UNIFIED_FORK_DEADLINE_SECONDS || "300")),
    amountOutMinUsdc: envBigInt("UNIFIED_FORK_AMOUNT_OUT_MIN_USDC", "0"),
    minProfitUsdc: envBigInt("UNIFIED_FORK_MIN_PROFIT_USDC", "1"),
  };
}

function tokenBorrowParams(usdcParams) {
  return {
    amount: envBigInt("UNIFIED_FORK_TOKEN_AMOUNT", usdcParams.amount.toString()),
    deadline: usdcParams.deadline,
    minFinalToken: envBigInt("UNIFIED_FORK_MIN_FINAL_TOKEN", "0"),
    minProfitToken: envBigInt("UNIFIED_FORK_MIN_PROFIT_TOKEN", "1"),
  };
}

function resultError(error) {
  return {
    message: error?.shortMessage || error?.message || String(error),
    data: error?.data || error?.info?.error?.data || error?.error?.data || null,
  };
}

function jsonReplacer(_key, value) {
  return typeof value === "bigint" ? value.toString() : value;
}

function stringifyReport(report) {
  return JSON.stringify(report, jsonReplacer, 2);
}

function forkCallOverrides() {
  return {
    gasLimit: envBigInt("UNIFIED_FORK_CALL_GAS_LIMIT", "39000000"),
  };
}

async function deployConfiguredExecutor() {
  if (hre.network.name !== "hardhat" && !envBool("UNIFIED_FORK_ALLOW_NON_HARDHAT", false)) {
    throw new Error("fork rehearsal must run on --network hardhat unless UNIFIED_FORK_ALLOW_NON_HARDHAT=true");
  }
  const [deployer] = await hre.ethers.getSigners();
  const aavePool = envAddress("UNIFIED_AAVE_POOL_ADDRESS") || DEFAULT_AAVE_POOL;
  const usdc = envAddress("UNIFIED_USDC_ADDRESS") || DEFAULT_USDC;
  const factory = envAddress("UNIFIED_V3_FACTORY") || AVALANCHE_V3_PROFILE.factory;
  const router = envAddress("UNIFIED_V3_ROUTER") || AVALANCHE_V3_PROFILE.router;
  const quoter = envAddress("UNIFIED_V3_QUOTER") || AVALANCHE_V3_PROFILE.quoter;

  for (const [label, address] of Object.entries({ aavePool, usdc, factory, router, quoter })) {
    const code = await hre.ethers.provider.getCode(address);
    if (code === "0x") throw new Error(`${label} has no code; run this script against an Avalanche fork`);
  }

  const Executor = await hre.ethers.getContractFactory("UnifiedFlashLoanMevExecutor");
  const executor = await Executor.deploy(aavePool, usdc, deployer.address);
  await executor.waitForDeployment();
  await (await executor.setAdapterConfig(1, true, factory, router, quoter)).wait();
  return { deployer, executor, aavePool, usdc, factory, router, quoter };
}

async function runCandidate(executor, trades, usdcParams, tokenParams, enableNonUsdcCrossPool, label) {
  const report = { label, tradeCount: trades.length };
  try {
    const preview = await executor.previewOrderedRuntimeAutoExecution.staticCall(
      trades,
      usdcParams,
      tokenParams,
      enableNonUsdcCrossPool,
      forkCallOverrides(),
    );
    report.preview = {
      found: preview.found,
      strategyStatus: preview.strategyStatus.toString(),
      executionKind: Number(preview.executionKind),
      selectedTradeArrayIndex: preview.selectedTradeArrayIndex.toString(),
      expectedProfit: preview.executionPreview.expectedProfit.toString(),
      quotedFinal: preview.executionPreview.quotedFinal.toString(),
      requiredFinal: preview.executionPreview.requiredFinal.toString(),
    };
  } catch (error) {
    report.previewError = resultError(error);
  }

  try {
    const runResult = await executor.runOrderedRuntimeTradesAndExecuteAuto.staticCall(
      trades,
      usdcParams,
      tokenParams,
      enableNonUsdcCrossPool,
      forkCallOverrides(),
    );
    report.staticCall = {
      ok: true,
      resultCode: runResult.resultCode.toString(),
      strategyStatus: runResult.strategyStatus.toString(),
      executionKind: Number(runResult.executionKind),
      profitAmount: runResult.profitAmount.toString(),
      profitSwept: runResult.profitSwept.toString(),
    };
  } catch (error) {
    report.staticCall = { ok: false, error: resultError(error) };
  }

  try {
    const gas = await executor.runOrderedRuntimeTradesAndExecuteAuto.estimateGas(
      trades,
      usdcParams,
      tokenParams,
      enableNonUsdcCrossPool,
    );
    report.estimateGas = gas.toString();
  } catch (error) {
    report.estimateGasError = resultError(error);
  }
  return report;
}

async function executeCandidateTx(executor, trades, usdcParams, tokenParams, enableNonUsdcCrossPool) {
  if (!envBool("UNIFIED_FORK_SEND_SUCCESS_TX", true)) {
    return { skipped: true, reason: "UNIFIED_FORK_SEND_SUCCESS_TX=false" };
  }
  try {
    const tx = await executor.runOrderedRuntimeTradesAndExecuteAuto(
      trades,
      usdcParams,
      tokenParams,
      enableNonUsdcCrossPool,
      forkCallOverrides(),
    );
    const receipt = await tx.wait();
    return {
      skipped: false,
      ok: receipt.status === 1,
      transactionHash: receipt.hash || receipt.transactionHash,
      blockNumber: receipt.blockNumber,
      gasUsed: receipt.gasUsed.toString(),
      logCount: receipt.logs.length,
    };
  } catch (error) {
    return { skipped: false, ok: false, error: resultError(error) };
  }
}

async function prefixDiagnostics(executor, trades, usdcParams, tokenParams, enableNonUsdcCrossPool) {
  const rows = [];
  for (let count = 1; count <= trades.length; count++) {
    const prefix = trades.slice(0, count);
    const row = { tradeCount: count };
    try {
      const preview = await executor.previewOrderedRuntimeAutoExecution.staticCall(
        prefix,
        usdcParams,
        tokenParams,
        enableNonUsdcCrossPool,
        forkCallOverrides(),
      );
      row.preview = {
        found: preview.found,
        strategyStatus: preview.strategyStatus.toString(),
        executionKind: Number(preview.executionKind),
        expectedProfit: preview.executionPreview.expectedProfit.toString(),
        quotedFinal: preview.executionPreview.quotedFinal.toString(),
        requiredFinal: preview.executionPreview.requiredFinal.toString(),
      };
    } catch (error) {
      row.previewError = resultError(error);
    }
    try {
      const gas = await executor.runOrderedRuntimeTradesAndExecuteAuto.estimateGas(
        prefix,
        usdcParams,
        tokenParams,
        enableNonUsdcCrossPool,
        forkCallOverrides(),
      );
      row.estimateGas = gas.toString();
    } catch (error) {
      row.estimateGasError = resultError(error);
    }
    rows.push(row);
  }
  return rows;
}

function writeEvidence(report) {
  const stamp = new Date().toISOString().replace(/[-:]/g, "").replace(/\..+/, "Z");
  const dir = path.resolve(__dirname, "../deployments/evidence", `${stamp}_${hre.network.name}-unified-fork`);
  fs.mkdirSync(dir, { recursive: true });
  const reportPath = path.join(dir, "report.json");
  fs.writeFileSync(reportPath, `${stringifyReport(report)}\n`, "utf8");
  return reportPath;
}

async function main() {
  const network = await hre.ethers.provider.getNetwork();
  const trades = loadRuntimeTrades();
  const baseReport = {
    runAt: new Date().toISOString(),
    network: hre.network.name,
    chainId: Number(network.chainId),
    blockNumber: await hre.ethers.provider.getBlockNumber(),
    artifact: {
      contractName: "UnifiedFlashLoanMevExecutor",
      deployedBytecodeBytes: (executorArtifact.deployedBytecode.length - 2) / 2,
      deployedBytecodeHash: hre.ethers.keccak256(executorArtifact.deployedBytecode),
    },
    forkExecutionConfig: {
      hardfork: hre.network.config.hardfork || null,
      blockGasLimit: String(hre.network.config.blockGasLimit || ""),
      callGasLimit: forkCallOverrides().gasLimit.toString(),
    },
    candidateSource: runtimeTradesPath() || (process.env.UNIFIED_FORK_RUNTIME_TRADES_JSON ? "env" : ""),
    candidateTradeCount: trades.length,
    runtimeTrades: trades,
    ready: false,
  };
  if (!trades.length) {
    const report = { ...baseReport, status: "candidate_missing", error: "UNIFIED_FORK_RUNTIME_TRADES_FILE or UNIFIED_FORK_RUNTIME_TRADES_JSON is required" };
    report.reportPath = writeEvidence(report);
    console.log(stringifyReport(report));
    process.exitCode = 1;
    return;
  }

  const { executor } = await deployConfiguredExecutor();
  const executorAddress = await executor.getAddress();
  const usdcParams = executionParams();
  const tokenParams = tokenBorrowParams(usdcParams);
  const enableNonUsdcCrossPool = String(process.env.UNIFIED_FORK_ENABLE_NON_USDC_CROSS_POOL || "").toLowerCase() === "true";
  const failureParams = { ...usdcParams, minProfitUsdc: envBigInt("UNIFIED_FORK_FAILURE_MIN_PROFIT_USDC", "999999999999999999999999") };
  const report = {
    ...baseReport,
    executorAddress,
    readback: await readbackUnifiedExecutor(executorAddress),
    executionParams: Object.fromEntries(Object.entries(usdcParams).map(([key, value]) => [key, value.toString()])),
    tokenBorrowParams: Object.fromEntries(Object.entries(tokenParams).map(([key, value]) => [key, value.toString()])),
    enableNonUsdcCrossPool,
    prefixDiagnostics: await prefixDiagnostics(
      executor,
      trades,
      usdcParams,
      tokenParams,
      enableNonUsdcCrossPool,
    ),
    successCandidate: await runCandidate(executor, trades, usdcParams, tokenParams, enableNonUsdcCrossPool, "success_candidate"),
    failureCandidate: await runCandidate(executor, trades, failureParams, tokenParams, enableNonUsdcCrossPool, "failure_candidate"),
  };
  report.successTransaction = report.successCandidate.staticCall?.ok
    ? await executeCandidateTx(executor, trades, usdcParams, tokenParams, enableNonUsdcCrossPool)
    : { skipped: true, reason: "success_candidate_static_call_failed" };
  report.ready = Boolean(
    report.successCandidate.staticCall?.ok &&
    report.successTransaction.ok &&
    report.failureCandidate.staticCall?.ok === false,
  );
  report.status = report.ready ? "fork_preflight_passed" : "fork_preflight_failed";
  report.reportPath = writeEvidence(report);
  console.log(stringifyReport(report));
  if (!report.ready) process.exitCode = 1;
}

if (require.main === module) {
  main().catch((error) => {
    console.error(error);
    process.exitCode = 1;
  });
}

module.exports = {
  loadRuntimeTrades,
  normalizeTrade,
  runCandidate,
};
