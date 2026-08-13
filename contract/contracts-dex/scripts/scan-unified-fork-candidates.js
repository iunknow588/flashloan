const fs = require("fs");
const path = require("path");
const hre = require("hardhat");
const { AVALANCHE_V3_PROFILE } = require("./preflight-unified-flashloan");
const { resultError } = require("./unified-error-decoder");

const DEFAULT_AAVE_POOL = "0x794a61358D6845594F94dc1DB02A252b5b4814aD";
const DEFAULT_USDC = "0xB97EF9Ef8734C71904D8002F8b6Bc66Dd9c48a6E";
const ZERO_ADDRESS = hre.ethers.ZeroAddress;
const STABLE_SYMBOLS = new Set([
  "USDC",
  "USDC.E",
  "USDT",
  "USDT.E",
  "USDT0",
  "USDT0.E",
  "DAI",
  "DAI.E",
  "FRAX",
  "EURC",
  "USDE",
  "SUSDE",
]);

function envAddress(...names) {
  for (const name of names) {
    const value = String(process.env[name] || "").trim();
    if (value && hre.ethers.isAddress(value)) return hre.ethers.getAddress(value);
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

function jsonReplacer(_key, value) {
  return typeof value === "bigint" ? value.toString() : value;
}

function stringifyReport(value) {
  return JSON.stringify(value, jsonReplacer, 2);
}

function evidenceStamp() {
  const stamp = new Date().toISOString().replace(/[-:]/g, "").replace(/\.(\d+)Z$/, "$1Z");
  return `${stamp}-${process.pid}`;
}

function evidenceSemantics() {
  return {
    historicalForkOnly: true,
    purpose: "enumerate runtime trade templates against a pinned pool-cache snapshot",
    doesNotProveLiveProfitOpportunity: true,
    positiveProfitWindowsCanBeSubBlockAndMustBeCapturedFromFreshRuntimeSignal: true,
  };
}

function cachePath() {
  const configured =
    process.env.UNIFIED_FORK_POOL_CACHE_FILE ||
    process.env.TRIANGULAR_RUNTIME_POOL_CACHE_FILE ||
    process.env.PINAX_POOL_DISCOVERY_CACHE_FILE ||
    "runtime/cache/avalanche_v3_pools.json";
  return path.isAbsolute(configured)
    ? configured
    : path.resolve(__dirname, "../../../flashloan/src_bot", configured);
}

function loadCache() {
  const file = cachePath();
  const raw = fs.readFileSync(file, "utf8");
  return { file, raw, payload: JSON.parse(raw) };
}

function pairKey(left, right) {
  return [left.toLowerCase(), right.toLowerCase()].sort().join(":");
}

function pairRows(cache) {
  const result = new Map();
  for (const row of cache.pools || []) {
    if (!row?.tokenX || !row?.tokenY || !Array.isArray(row.pools)) continue;
    const pools = row.pools
      .filter((pool) => Number(pool?.adapterKind || 0) === 1 && hre.ethers.isAddress(pool?.pool || ""))
      .slice(0, 5)
      .map((pool) => ({ adapterKind: 1, pool: hre.ethers.getAddress(pool.pool) }));
    if (!pools.length) continue;
    result.set(pairKey(row.tokenX, row.tokenY), {
      tokenX: hre.ethers.getAddress(row.tokenX),
      tokenY: hre.ethers.getAddress(row.tokenY),
      tokenXSymbol: row.tokenX_symbol || "",
      tokenYSymbol: row.tokenY_symbol || "",
      pools,
    });
  }
  return result;
}

function findPair(rows, left, right) {
  return rows.get(pairKey(left, right)) || null;
}

function tokenForSymbol(row, symbol) {
  if (String(row.tokenXSymbol).toLowerCase() === String(symbol).toLowerCase()) return row.tokenX;
  if (String(row.tokenYSymbol).toLowerCase() === String(symbol).toLowerCase()) return row.tokenY;
  return "";
}

function isStableSymbol(symbol) {
  return STABLE_SYMBOLS.has(String(symbol || "").trim().toUpperCase());
}

function exerciseTargetConfig() {
  return {
    includeStable: envBool("UNIFIED_FORK_SCAN_INCLUDE_STABLE", false),
    allowSinglePairDiagnostic: envBool("UNIFIED_FORK_ALLOW_SINGLE_PAIR_DIAGNOSTIC", false),
  };
}

function buildTrade(index, left, right, row) {
  const pools = row.pools.slice(0, 5);
  while (pools.length < 5) pools.push({ adapterKind: 0, pool: ZERO_ADDRESS });
  return {
    tradeIndex: BigInt(index),
    tokenX: hre.ethers.getAddress(left),
    tokenY: hre.ethers.getAddress(right),
    pools,
  };
}

function candidateGroups(cache) {
  const usdc = envAddress("UNIFIED_USDC_ADDRESS") || DEFAULT_USDC;
  const rows = pairRows(cache);
  const mode = String(process.env.UNIFIED_FORK_SCAN_MODE || "triangular").trim().toLowerCase();
  if (!["triangular", "single_pair", "all"].includes(mode)) {
    throw new Error(`UNIFIED_FORK_SCAN_MODE must be triangular, single_pair, or all; got ${mode}`);
  }
  const { includeStable, allowSinglePairDiagnostic } = exerciseTargetConfig();
  const tokens = new Map();
  for (const row of rows.values()) {
    for (const [address, symbol] of [
      [row.tokenX, row.tokenXSymbol],
      [row.tokenY, row.tokenYSymbol],
    ]) {
      if (address.toLowerCase() !== usdc.toLowerCase()) tokens.set(address.toLowerCase(), { address, symbol });
    }
  }
  const result = [];
  const entries = [...tokens.values()];
  if (mode === "single_pair" || mode === "all") {
    if (!allowSinglePairDiagnostic) {
      if (mode === "single_pair") {
        throw new Error(
          "single_pair scan is diagnostic-only; set UNIFIED_FORK_ALLOW_SINGLE_PAIR_DIAGNOSTIC=true explicitly",
        );
      }
    } else {
      for (const x of entries) {
        if (!includeStable && isStableSymbol(x.symbol)) continue;
        const ux = findPair(rows, usdc, x.address);
        if (!ux || ux.pools.length < 2) continue;
        result.push({
          route: `USDC/${x.symbol}`,
          mode: "single_pair",
          exerciseTargetPolicy: "single-pair-diagnostic",
          usdc,
          tokenX: x.address,
          tokenY: "",
          tokenXSymbol: x.symbol,
          tokenYSymbol: "",
          trades: [
            buildTrade(0, usdc, x.address, ux),
          ],
        });
      }
    }
  }
  if (mode === "single_pair") return result;
  for (const x of entries) {
    if (!includeStable && isStableSymbol(x.symbol)) continue;
    for (const y of entries) {
      if (x.address.toLowerCase() === y.address.toLowerCase()) continue;
      if (!includeStable && isStableSymbol(y.symbol)) continue;
      const ux = findPair(rows, usdc, x.address);
      const uy = findPair(rows, usdc, y.address);
      const xy = findPair(rows, x.address, y.address);
      if (!ux || !uy || !xy) continue;
      result.push({
        route: `USDC/${x.symbol}/${y.symbol}`,
        mode: "triangular",
        exerciseTargetPolicy: isStableSymbol(x.symbol) || isStableSymbol(y.symbol)
          ? "stable-target-diagnostic"
          : "non-stable-pair",
        usdc,
        tokenX: x.address,
        tokenY: y.address,
        tokenXSymbol: x.symbol,
        tokenYSymbol: y.symbol,
        trades: [
          buildTrade(0, usdc, x.address, ux),
          buildTrade(1, usdc, y.address, uy),
          buildTrade(2, x.address, y.address, xy),
        ],
      });
    }
  }
  return result;
}

function amounts() {
  const raw = String(process.env.UNIFIED_FORK_SCAN_AMOUNTS || "1000000,10000000,100000000,500000000,1000000000");
  return raw
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean)
    .map((item) => BigInt(item));
}

function params(amount, minProfit) {
  const deadline = BigInt(Math.floor(Date.now() / 1000) + Number(process.env.UNIFIED_FORK_DEADLINE_SECONDS || "300"));
  return {
    usdc: { amount, deadline, amountOutMinUsdc: 0n, minProfitUsdc: minProfit },
    token: { amount, deadline, minFinalToken: 0n, minProfitToken: 1n },
  };
}

async function deployExecutor() {
  const [deployer] = await hre.ethers.getSigners();
  const aavePool = envAddress("UNIFIED_AAVE_POOL_ADDRESS") || DEFAULT_AAVE_POOL;
  const usdc = envAddress("UNIFIED_USDC_ADDRESS") || DEFAULT_USDC;
  const factory = envAddress("UNIFIED_V3_FACTORY") || AVALANCHE_V3_PROFILE.factory;
  const router = envAddress("UNIFIED_V3_ROUTER") || AVALANCHE_V3_PROFILE.router;
  const quoter = envAddress("UNIFIED_V3_QUOTER") || AVALANCHE_V3_PROFILE.quoter;
  for (const [label, address] of Object.entries({ aavePool, usdc, factory, router, quoter })) {
    if ((await hre.ethers.provider.getCode(address)) === "0x") {
      throw new Error(`${label} has no code; fork RPC is not Avalanche at the requested block`);
    }
  }
  const Executor = await hre.ethers.getContractFactory("UnifiedFlashLoanMevExecutor");
  const executor = await Executor.deploy(aavePool, usdc, deployer.address);
  await executor.waitForDeployment();
  await (await executor.setAdapterConfig(1, true, factory, router, quoter)).wait();
  return { executor, deployer, aavePool, usdc, factory, router, quoter };
}

function callOverrides() {
  return { gasLimit: envBigInt("UNIFIED_FORK_CALL_GAS_LIMIT", "39000000") };
}

async function scanCandidate(executor, candidate, amount, minProfit, enableNonUsdcCrossPool) {
  const execution = params(amount, minProfit);
  const report = {
    route: candidate.route,
    mode: candidate.mode,
    amount: amount.toString(),
    minProfit: minProfit.toString(),
    tradeCount: candidate.trades.length,
  };
  try {
    const preview = await executor.previewOrderedRuntimeAutoExecution.staticCall(
      candidate.trades,
      execution.usdc,
      execution.token,
      enableNonUsdcCrossPool,
      callOverrides(),
    );
    report.preview = {
      found: preview.found,
      strategyStatus: preview.strategyStatus.toString(),
      executionKind: Number(preview.executionKind),
      expectedProfit: preview.executionPreview.expectedProfit.toString(),
      quotedFinal: preview.executionPreview.quotedFinal.toString(),
      requiredFinal: preview.executionPreview.requiredFinal.toString(),
    };
  } catch (error) {
    report.previewError = resultError(error);
  }
  return report;
}

function writeReport(report) {
  const dir = path.resolve(__dirname, "../deployments/evidence", `${evidenceStamp()}_hardhat-unified-scan`);
  fs.mkdirSync(dir, { recursive: true });
  const output = path.join(dir, "report.json");
  fs.writeFileSync(output, `${stringifyReport(report)}\n`, "utf8");
  return output;
}

async function main() {
  if (hre.network.name !== "hardhat" && !envBool("UNIFIED_FORK_ALLOW_NON_HARDHAT", false)) {
    throw new Error("fork scan must run on --network hardhat");
  }
  const network = await hre.ethers.provider.getNetwork();
  if (network.chainId !== 43114n) throw new Error(`expected Avalanche fork chainId 43114, got ${network.chainId}`);
  const { payload: cache, file } = loadCache();
  const { includeStable, allowSinglePairDiagnostic } = exerciseTargetConfig();
  const candidates = candidateGroups(cache);
  const scanAmounts = amounts();
  const scanMinProfit = envBigInt("UNIFIED_FORK_SCAN_MIN_PROFIT_USDC", "1");
  const enableNonUsdcCrossPool = envBool("UNIFIED_FORK_ENABLE_NON_USDC_CROSS_POOL", false);
  const { executor } = await deployExecutor();
  const rows = [];
  for (const candidate of candidates) {
    for (const amount of scanAmounts) {
      rows.push(await scanCandidate(executor, candidate, amount, scanMinProfit, enableNonUsdcCrossPool));
    }
  }
  const successes = rows.filter((row) => row.preview?.found);
  const report = {
    runAt: new Date().toISOString(),
    network: hre.network.name,
    chainId: Number(network.chainId),
    blockNumber: await hre.ethers.provider.getBlockNumber(),
    cacheFile: file,
    cacheBlockNumber: cache.block_number,
    evidenceSemantics: evidenceSemantics(),
    exerciseTargetPolicy: includeStable ? "stable-target-diagnostic-enabled" : "non-stable-pair-only",
    includeStableExerciseTargets: includeStable,
    singlePairDiagnosticEnabled: allowSinglePairDiagnostic,
    candidateCount: candidates.length,
    amountCandidates: scanAmounts,
    minProfit: scanMinProfit,
    enableNonUsdcCrossPool,
    executorAddress: await executor.getAddress(),
    successCount: successes.length,
    successes: successes.slice(0, 25),
    failures: rows,
  };
  report.status = successes.length ? "candidates_found" : "no_candidate_found";
  report.reportPath = writeReport(report);
  console.log(stringifyReport(report));
  if (!successes.length) process.exitCode = 1;
}

if (require.main === module) {
  main().catch((error) => {
    console.error(error);
    process.exitCode = 1;
  });
}

module.exports = {
  candidateGroups,
  evidenceSemantics,
  exerciseTargetConfig,
  isStableSymbol,
  loadCache,
  resultError,
  scanCandidate,
};
