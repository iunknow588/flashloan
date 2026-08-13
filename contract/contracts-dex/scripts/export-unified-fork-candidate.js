const crypto = require("crypto");
const fs = require("fs");
const path = require("path");

const ZERO_ADDRESS = "0x0000000000000000000000000000000000000000";
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

function outputPath(cache, tokenXSymbol, tokenYSymbol, includeReverse, mode) {
  const configured = String(process.env.UNIFIED_FORK_RUNTIME_TRADES_OUTPUT || "").trim();
  if (configured) return path.isAbsolute(configured) ? configured : path.resolve(process.cwd(), configured);
  const pair = `${tokenXSymbol}${tokenYSymbol ? `-${tokenYSymbol}` : ""}`.replace(/[^A-Za-z0-9_.-]+/g, "-").toLowerCase();
  const suffix = mode === "single_pair" ? "-single-pair" : includeReverse ? "" : "-forward-only";
  return path.resolve(
    __dirname,
    "../deployments/evidence/candidates",
    `avalanche-runtime-trades-block-${cache.block_number}-${pair}${suffix}.json`,
  );
}

function normalizedSymbol(value) {
  return String(value || "").trim().toUpperCase();
}

function isStableSymbol(value) {
  return STABLE_SYMBOLS.has(normalizedSymbol(value));
}

function allowStableTargets() {
  return String(process.env.UNIFIED_FORK_ALLOW_STABLE_TARGETS || "").trim().toLowerCase() === "true";
}

function evidenceSemantics() {
  return {
    historicalForkOnly: true,
    purpose: "runtime trade template replay and execution-path rehearsal",
    doesNotProveLiveProfitOpportunity: true,
    liveProfitOpportunityMustBeCapturedFromFreshRuntimeSignal: true,
  };
}

function rowSymbols(row) {
  return [normalizedSymbol(row?.tokenX_symbol), normalizedSymbol(row?.tokenY_symbol)];
}

function findPair(cache, leftSymbol, rightSymbol) {
  const left = normalizedSymbol(leftSymbol);
  const right = normalizedSymbol(rightSymbol);
  return (cache.pools || []).find((row) => {
    const symbols = rowSymbols(row);
    return symbols.includes(left) && symbols.includes(right);
  });
}

function tokenAddress(row, symbol) {
  const wanted = normalizedSymbol(symbol);
  if (normalizedSymbol(row?.tokenX_symbol) === wanted) return row.tokenX;
  if (normalizedSymbol(row?.tokenY_symbol) === wanted) return row.tokenY;
  throw new Error(`pair row does not contain symbol ${symbol}`);
}

function normalizedPools(row) {
  const pools = (row?.pools || [])
    .filter((pool) => pool && Number(pool.adapterKind || 0) === 1 && /^0x[0-9a-fA-F]{40}$/.test(pool.pool || ""))
    .slice(0, 5)
    .map((pool) => ({
      adapterKind: 1,
      pool: pool.pool,
    }));
  while (pools.length < 5) pools.push({ adapterKind: 0, pool: ZERO_ADDRESS });
  return pools;
}

function runtimeTrade(tradeIndex, row, tokenXSymbol, tokenYSymbol) {
  return {
    tradeIndex,
    tokenX: tokenAddress(row, tokenXSymbol),
    tokenY: tokenAddress(row, tokenYSymbol),
    pools: normalizedPools(row),
  };
}

function buildCandidate(cache, rawCache, tokenXSymbol, tokenYSymbol) {
  if (Number(cache.chain_id) !== 43114) throw new Error(`cache chain_id must be 43114, got ${cache.chain_id}`);
  if (!Number.isInteger(Number(cache.block_number)) || Number(cache.block_number) <= 0) {
    throw new Error("cache block_number is required");
  }
  const usdcSymbol = String(process.env.UNIFIED_FORK_USDC_SYMBOL || "USDC").trim();
  const ux = findPair(cache, usdcSymbol, tokenXSymbol);
  const mode = String(process.env.UNIFIED_FORK_CANDIDATE_MODE || "triangular").trim().toLowerCase();
  if (mode === "single_pair") {
    if (String(process.env.UNIFIED_FORK_ALLOW_SINGLE_PAIR_DIAGNOSTIC || "").trim().toLowerCase() !== "true") {
      throw new Error(
        "single_pair is diagnostic-only; set UNIFIED_FORK_ALLOW_SINGLE_PAIR_DIAGNOSTIC=true to export it explicitly",
      );
    }
    if (isStableSymbol(tokenXSymbol) && !allowStableTargets()) {
      throw new Error(`stable token ${tokenXSymbol} is not allowed as an exercise target`);
    }
    if (!ux) throw new Error(`missing USDC/${tokenXSymbol} pair in pool cache`);
    if ((ux.pools || []).length < 2) throw new Error(`USDC/${tokenXSymbol} pair must have at least 2 pools`);
    return {
      schemaVersion: 1,
      generatedAt: new Date().toISOString(),
      network: "avalanche",
      chainId: 43114,
      forkBlockNumber: Number(cache.block_number),
      evidenceSemantics: evidenceSemantics(),
      sourceCache: {
        file: cachePath(),
        sha256: crypto.createHash("sha256").update(rawCache).digest("hex"),
        fetchedAt: cache.fetched_at || null,
        blockNumber: Number(cache.block_number),
        factory: cache.factory || null,
        router: cache.router || null,
        quoter: cache.quoter || null,
        aavePool: cache.aave_pool || null,
        usdc: cache.usdc || null,
      },
      route: {
        mode,
        usdcSymbol,
        tokenXSymbol,
        tokenYSymbol: "",
        tradeOrder: ["U-X"],
        includeReverseTrade: false,
        exerciseTargetPolicy: "single-pair-diagnostic",
        containsStableExerciseTarget: isStableSymbol(tokenXSymbol),
      },
      runtimeTrades: [
        runtimeTrade(0, ux, usdcSymbol, tokenXSymbol),
      ],
    };
  }
  if ((isStableSymbol(tokenXSymbol) || isStableSymbol(tokenYSymbol)) && !allowStableTargets()) {
    throw new Error(
      `exercise targets must both be non-stable assets; got ${tokenXSymbol}/${tokenYSymbol}. ` +
      "Set UNIFIED_FORK_ALLOW_STABLE_TARGETS=true only for an explicitly labeled diagnostic export.",
    );
  }
  const uy = findPair(cache, usdcSymbol, tokenYSymbol);
  const xy = findPair(cache, tokenXSymbol, tokenYSymbol);
  for (const [label, row] of Object.entries({ ux, uy, xy })) {
    if (!row) throw new Error(`missing ${label} pair in pool cache`);
    if (!(row.pools || []).length) throw new Error(`${label} pair has no pools`);
  }
  const includeReverse = String(process.env.UNIFIED_FORK_INCLUDE_REVERSE_TRADE || "true").trim().toLowerCase() !== "false";
  const runtimeTrades = [
    runtimeTrade(0, ux, usdcSymbol, tokenXSymbol),
    runtimeTrade(1, uy, usdcSymbol, tokenYSymbol),
    runtimeTrade(2, xy, tokenXSymbol, tokenYSymbol),
  ];
  if (includeReverse) runtimeTrades.push(runtimeTrade(3, xy, tokenYSymbol, tokenXSymbol));
  return {
    schemaVersion: 1,
    generatedAt: new Date().toISOString(),
    network: "avalanche",
    chainId: 43114,
    forkBlockNumber: Number(cache.block_number),
    evidenceSemantics: evidenceSemantics(),
    sourceCache: {
      file: cachePath(),
      sha256: crypto.createHash("sha256").update(rawCache).digest("hex"),
      fetchedAt: cache.fetched_at || null,
      blockNumber: Number(cache.block_number),
      factory: cache.factory || null,
      router: cache.router || null,
      quoter: cache.quoter || null,
      aavePool: cache.aave_pool || null,
      usdc: cache.usdc || null,
    },
    route: {
      mode,
      usdcSymbol,
      tokenXSymbol,
      tokenYSymbol,
      tradeOrder: includeReverse ? ["U-X", "U-Y", "X-Y", "Y-X"] : ["U-X", "U-Y", "X-Y"],
      includeReverseTrade: includeReverse,
      exerciseTargetPolicy: "non-stable-pair",
      containsStableExerciseTarget: isStableSymbol(tokenXSymbol) || isStableSymbol(tokenYSymbol),
    },
    runtimeTrades,
  };
}

function main() {
  const file = cachePath();
  const rawCache = fs.readFileSync(file, "utf8");
  const cache = JSON.parse(rawCache);
  const tokenXSymbol = String(process.env.UNIFIED_FORK_TOKEN_X_SYMBOL || "BTC.b").trim();
  const tokenYSymbol = String(process.env.UNIFIED_FORK_TOKEN_Y_SYMBOL || "WAVAX").trim();
  const candidate = buildCandidate(cache, rawCache, tokenXSymbol, tokenYSymbol);
  const output = outputPath(cache, tokenXSymbol, candidate.route.tokenYSymbol, candidate.route.includeReverseTrade, candidate.route.mode);
  fs.mkdirSync(path.dirname(output), { recursive: true });
  fs.writeFileSync(output, `${JSON.stringify(candidate, null, 2)}\n`, "utf8");
  console.log(JSON.stringify({
    ok: true,
    output,
    forkBlockNumber: candidate.forkBlockNumber,
    route: candidate.route,
    tradeCount: candidate.runtimeTrades.length,
    sourceCacheSha256: candidate.sourceCache.sha256,
  }, null, 2));
}

if (require.main === module) {
  try {
    main();
  } catch (error) {
    console.error(error.message || error);
    process.exitCode = 1;
  }
}

module.exports = {
  buildCandidate,
  evidenceSemantics,
  findPair,
  isStableSymbol,
  runtimeTrade,
};
