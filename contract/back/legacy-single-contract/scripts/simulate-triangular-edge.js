const hre = require("hardhat");
const { sanitizeError, toJsonValue } = require("./fuji-evidence");

function optionalEnv(...names) {
  for (const name of names) {
    const value = process.env[name];
    if (value && value.trim() && value !== "0x...") {
      return value.trim();
    }
  }
  return "";
}

function normalizeAddress(value) {
  const text = String(value || "").trim();
  if (hre.ethers.isAddress(text)) {
    return hre.ethers.getAddress(text);
  }
  if (/^0x[a-fA-F0-9]{40}$/.test(text)) {
    return hre.ethers.getAddress(text.toLowerCase());
  }
  return "";
}

function normalizeRuntimeTrade(trade, tradeArrayIndex) {
  if (!trade || typeof trade !== "object") {
    throw new Error(`runtimeTrades[${tradeArrayIndex}] must be an object`);
  }
  const tokenX = normalizeAddress(trade.tokenX || trade.token_x);
  const tokenY = normalizeAddress(trade.tokenY || trade.token_y);
  if (!tokenX || !tokenY) {
    throw new Error(`runtimeTrades[${tradeArrayIndex}] tokenX/tokenY must be valid addresses`);
  }
  const inputPools = trade.pools || trade.candidatePools || trade.candidate_pools;
  if (!Array.isArray(inputPools) || inputPools.length === 0 || inputPools.length > 10) {
    throw new Error(`runtimeTrades[${tradeArrayIndex}] pools must include 1 to 10 items`);
  }
  const pools = Array.from({ length: 10 }, () => ({ adapterKind: 0n, pool: hre.ethers.ZeroAddress }));
  inputPools.forEach((pool, poolIndex) => {
    const poolAddress = normalizeAddress(pool && pool.pool);
    if (!poolAddress) {
      throw new Error(`runtimeTrades[${tradeArrayIndex}].pools[${poolIndex}].pool must be a valid address`);
    }
    pools[poolIndex] = {
      adapterKind: BigInt(pool.adapterKind ?? pool.adapter_kind ?? 1),
      pool: poolAddress,
    };
  });
  return {
    tradeIndex: BigInt(trade.tradeIndex ?? trade.trade_index ?? tradeArrayIndex),
    tokenX,
    tokenY,
    pools,
  };
}

function runtimeTradesFromEnv() {
  const value = optionalEnv("TRIANGULAR_RUNTIME_TRADES_JSON");
  if (!value) throw new Error("TRIANGULAR_RUNTIME_TRADES_JSON is required");
  const parsed = JSON.parse(value);
  if (!Array.isArray(parsed) || parsed.length === 0 || parsed.length > 16) {
    throw new Error("TRIANGULAR_RUNTIME_TRADES_JSON must include 1 to 16 trades");
  }
  return parsed.map(normalizeRuntimeTrade);
}

function runtimeFailureReason(code) {
  return ({
    0: "none",
    101: "not_enough_valid_pools",
    102: "no_price_spread",
  })[Number(code)] || `unknown_failure_${code}`;
}

function decisionReport(result) {
  const failureCode = result[16] ? Number(result[16]) : 0;
  return {
    ok: Boolean(result[0]),
    viable: Boolean(result[0]),
    tradeIndex: result[1].toString(),
    tokenX: result[2],
    tokenY: result[3],
    lowPool: result[4],
    highPool: result[5],
    adapterKind: result[6].toString(),
    lowFee: result[7].toString(),
    highFee: result[8].toString(),
    lowLiquidity: result[9].toString(),
    highLiquidity: result[10].toString(),
    lowNormalizedTick: result[11].toString(),
    highNormalizedTick: result[12].toString(),
    tickDelta: result[13].toString(),
    scannedPoolCount: result[14].toString(),
    validPoolCount: result[15].toString(),
    failureCode: failureCode.toString(),
    failureReason: runtimeFailureReason(failureCode),
  };
}

async function main() {
  const controllerAddress = normalizeAddress(optionalEnv("TRIANGULAR_ROUTE_CONTROLLER_ADDRESS", "TRIANGULAR_CONTROLLER_ADDRESS"));
  if (!controllerAddress) throw new Error("TRIANGULAR_ROUTE_CONTROLLER_ADDRESS is required");
  const runtimeTrades = runtimeTradesFromEnv();
  const controller = await hre.ethers.getContractAt("TriangularRouteController", controllerAddress);
  const result = await controller.previewBestRuntimeTrades.staticCall(runtimeTrades);

  console.log(JSON.stringify(toJsonValue({
    ok: true,
    network: hre.network.name,
    chainId: Number((await hre.ethers.provider.getNetwork()).chainId),
    controllerAddress,
    runtimeTrades,
    preview: {
      bestTradeArrayIndex: result[0].toString(),
      decision: decisionReport(result[1]),
    },
  }), null, 2));
}

if (require.main === module) {
  main().catch((error) => {
    console.error(JSON.stringify({ ok: false, error: sanitizeError(error) }, null, 2));
    process.exitCode = 1;
  });
}
