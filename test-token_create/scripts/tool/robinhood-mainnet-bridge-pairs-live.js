const path = require("path");
const fs = require("fs");
const { ethers } = require("../lib/hardhat");

const CHAIN_PATH = process.env.FLASHLOAN_CHAIN_JSON
  ? path.resolve(process.env.FLASHLOAN_CHAIN_JSON)
  : path.resolve(__dirname, "../../../flashloan/src/chains/robinhood/chain.json");
const CONTRACTS_PATH = process.env.FLASHLOAN_CONTRACTS_JSON
  ? path.resolve(process.env.FLASHLOAN_CONTRACTS_JSON)
  : path.resolve(__dirname, "../../../contract/robinhood/DEX/config/contracts.json");
const POOL_INDEX_PATH = process.env.FLASHLOAN_V4_POOL_INDEX
  ? path.resolve(process.env.FLASHLOAN_V4_POOL_INDEX)
  : path.resolve(__dirname, "../../../flashloan/src/runtime/evidence/robinhood/v4_pool_index/latest.json");
const EVIDENCE_ROOT = process.env.FLASHLOAN_MAINNET_EVIDENCE_ROOT
  ? path.resolve(process.env.FLASHLOAN_MAINNET_EVIDENCE_ROOT)
  : path.resolve(__dirname, "../../../contract/robinhood/DEX/deployments/evidence");

function persistReport(report) {
  const stamp = new Date().toISOString().replace(/[:.]/g, "-").replace(/\.\d+Z$/, "Z");
  const dir = path.join(EVIDENCE_ROOT, `${stamp}_robinhood-mainnet-bridge-pairs`);
  fs.mkdirSync(dir, { recursive: true });
  const reportPath = path.join(dir, "report.json");
  fs.writeFileSync(reportPath, `${JSON.stringify(report, null, 2)}\n`);
  return reportPath;
}

function writeReport(report) {
  const reportPath = persistReport(report);
  report.reportPath = reportPath;
  return reportPath;
}

function readJsonOr(filePath, fallback) {
  try {
    return JSON.parse(fs.readFileSync(filePath, "utf8"));
  } catch (error) {
    return fallback;
  }
}

function readArg(name, fallback) {
  const idx = process.argv.indexOf(`--${name}`);
  if (idx >= 0) return process.argv[idx + 1];
  return fallback;
}

function parseSymbols(value) {
  return String(value || "")
    .toUpperCase()
    .split(/\s*->\s*|\s*,\s*|[\s;]+/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function parseAddresses(value) {
  return String(value || "")
    .trim()
    .split(/,|->|\s+/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function sortPair(a, b) {
  return BigInt(a.toLowerCase()) < BigInt(b.toLowerCase()) ? [a, b] : [b, a];
}

function makeKey(a, b, fee, tickSpacing, hooks) {
  const [currency0, currency1] = sortPair(a, b);
  return { currency0, currency1, fee, tickSpacing, hooks };
}

function zeroForOne(key, input) {
  return input.toLowerCase() === key.currency0.toLowerCase();
}

function normalizeAddress(value) {
  const raw = String(value || "").trim();
  if (!raw || raw === "0x...") return "";
  try {
    return ethers.getAddress(raw);
  } catch (error) {
    return "";
  }
}

function poolIndexPools() {
  const index = readJsonOr(POOL_INDEX_PATH, {});
  return Array.isArray(index.pools) ? index.pools : [];
}

function findPoolByPair(pools, a, b) {
  const want0 = String(a || "").toLowerCase();
  const want1 = String(b || "").toLowerCase();
  for (const pool of pools) {
    const pk = pool.poolKey;
    if (!pk) continue;
    const cur0 = String(pk.currency0 || "").toLowerCase();
    const cur1 = String(pk.currency1 || "").toLowerCase();
    const ok = (cur0 === want0 && cur1 === want1) || (cur0 === want1 && cur1 === want0);
    if (ok) {
      return {
        fee: Number(pk.fee),
        tickSpacing: Number(pk.tickSpacing),
        hooks: pk.hooks || ethers.ZeroAddress,
      };
    }
  }
  return null;
}

function defaultRoute() {
  const chain = readJsonOr(CHAIN_PATH, {});
  const contracts = readJsonOr(CONTRACTS_PATH, {});
  const chainContracts = chain.contracts || {};
  const weth =
    process.env.ROBINHOOD_MAINNET_WETH_ADDRESS ||
    chainContracts.wrapped_native ||
    contracts.defaultV4WrappedNative ||
    contracts.defaultWrappedNative ||
    "";
  if (!weth) throw new Error("mainnet wrapped_native is missing from chain.json/contracts.json");
  const usdg =
    process.env.ROBINHOOD_MAINNET_USDG_ADDRESS ||
    chainContracts.usdg ||
    contracts.defaultUsdg ||
    contracts.usdg ||
    "0x5fc5360D0400a0Fd4f2af552ADD042D716F1d168";
  const tokenA =
    process.env.ROBINHOOD_MAINNET_ARB_TOKEN_ADDRESS ||
    "0x7195e2088f3DFCca56e6A71D129051963Ed9657A";
  const pools = poolIndexPools();
  if (!findPoolByPair(pools, tokenA, weth) || !findPoolByPair(pools, weth, usdg) || !findPoolByPair(pools, usdg, tokenA)) {
    throw new Error(
      `missing_route_pool_key: default route requires V4 pools for ${tokenA}-WETH, WETH-USDG, USDG-${tokenA}; ` +
      "refresh flashloan/src/runtime/evidence/robinhood/v4_pool_index/latest.json or set ROBINHOOD_MAINNET_OFFICIAL_ARB_BRIDGE_ROUTE"
    );
  }
  return {
    symbols: ["0X7195E208", "WETH", "USDG", "0X7195E208"],
    addresses: [tokenA, weth, usdg, tokenA],
    fee0: Number(process.env.ROBINHOOD_MAINNET_ENTRY_FEE || "0"),
    tick0: Number(process.env.ROBINHOOD_MAINNET_ENTRY_TICK_SPACING || "0"),
    fee1: Number(process.env.ROBINHOOD_MAINNET_BRIDGE_FEE || "0"),
    tick1: Number(process.env.ROBINHOOD_MAINNET_BRIDGE_TICK_SPACING || "0"),
    fee2: Number(process.env.ROBINHOOD_MAINNET_EXIT_FEE || "0"),
    tick2: Number(process.env.ROBINHOOD_MAINNET_EXIT_TICK_SPACING || "0"),
    inferFromPoolIndex: true,
  };
}

function resolveRoute() {
  const routeText = readArg("route", process.env.ROBINHOOD_MAINNET_OFFICIAL_ARB_BRIDGE_ROUTE || "");
  if (routeText) {
    const symbols = parseSymbols(routeText);
    const envAddresses = parseAddresses(process.env.ROBINHOOD_MAINNET_OFFICIAL_ARB_BRIDGE_ADDRESSES || "");
    if (symbols.length !== 4 || envAddresses.length !== 4) {
      throw new Error(
        "bridge-pair arb route must be a 4-symbol closed loop and require ROBINHOOD_MAINNET_OFFICIAL_ARB_BRIDGE_ADDRESSES"
      );
    }
    if (symbols[0].toLowerCase() !== symbols[3].toLowerCase()) {
      throw new Error("bridge-pair arb route must close: first and last symbols must match");
    }
    if (envAddresses[0].toLowerCase() !== envAddresses[3].toLowerCase()) {
      throw new Error("bridge-pair arb route must close: first and last addresses must match");
    }
    return {
      symbols,
      addresses: envAddresses,
      fee0: Number(process.env.ROBINHOOD_MAINNET_ENTRY_FEE || "3000"),
      tick0: Number(process.env.ROBINHOOD_MAINNET_ENTRY_TICK_SPACING || "60"),
      fee1: Number(process.env.ROBINHOOD_MAINNET_BRIDGE_FEE || "3000"),
      tick1: Number(process.env.ROBINHOOD_MAINNET_BRIDGE_TICK_SPACING || "60"),
      fee2: Number(process.env.ROBINHOOD_MAINNET_EXIT_FEE || "3000"),
      tick2: Number(process.env.ROBINHOOD_MAINNET_EXIT_TICK_SPACING || "60"),
      inferFromPoolIndex: true,
    };
  }
  return defaultRoute();
}

function inferPoolParamsFromIndex(route) {
  const index = readJsonOr(POOL_INDEX_PATH, {});
  const pools = index.pools || [];
  const pairs = [
    [route.addresses[0], route.addresses[1]],
    [route.addresses[1], route.addresses[2]],
    [route.addresses[2], route.addresses[3]],
  ];
  const out = [];
  for (const [a, b] of pairs) {
    out.push(findPoolByPair(pools, a, b));
  }
  return out;
}

async function main() {
  const chain = readJsonOr(CHAIN_PATH, {});
  const contracts = readJsonOr(CONTRACTS_PATH, {});
  const chainContracts = chain.contracts || {};
  const quoter = normalizeAddress(
    process.env.ROBINHOOD_MAINNET_V4_QUOTER ||
      chainContracts.v4_quoter ||
      contracts.defaultV4Quoter
  );
  const poolManager = normalizeAddress(
    process.env.ROBINHOOD_MAINNET_V4_POOL_MANAGER ||
      chainContracts.v4_pool_manager ||
      contracts.defaultV4PoolManager
  );
  if (!quoter || !poolManager) {
    throw new Error("mainnet v4_quoter and v4_pool_manager are missing from chain.json/contracts.json");
  }

  const route = resolveRoute();
  const inferred = route.inferFromPoolIndex ? inferPoolParamsFromIndex(route) : [];
  if (route.inferFromPoolIndex && inferred.some((item) => !item)) {
    const missing = inferred.map((item, i) => (item ? null : `${i + 1}`)).filter(Boolean).join(",");
    const report = {
      schemaVersion: "robinhood-mainnet-bridge-pairs-readonly-v1",
      chainId: Number((await ethers.provider.getNetwork()).chainId),
      route: route.symbols,
      addresses: route.addresses,
      profitable: false,
      broadcast: false,
      ok: false,
      detail: "missing_route_pool_key",
      missingHops: missing ? `hops ${missing}` : "all",
      note: "no V4 Quoter call attempted because one or more hops are absent from the mainnet v4_pool_index evidence",
    };
    writeReport(report);
    console.log(JSON.stringify(report, null, 2));
    console.error("mainnet bridge-pair read-only route cannot resolve all pool keys from v4_pool_index");
    process.exitCode = 1;
    return;
  }
  const fee0 = inferred[0] ? inferred[0].fee : route.fee0;
  const tick0 = inferred[0] ? inferred[0].tickSpacing : route.tick0;
  const fee1 = inferred[1] ? inferred[1].fee : route.fee1;
  const tick1 = inferred[1] ? inferred[1].tickSpacing : route.tick1;
  const fee2 = inferred[2] ? inferred[2].fee : route.fee2;
  const tick2 = inferred[2] ? inferred[2].tickSpacing : route.tick2;
  const hooks0 = inferred[0] ? inferred[0].hooks : ethers.ZeroAddress;
  const hooks1 = inferred[1] ? inferred[1].hooks : ethers.ZeroAddress;
  const hooks2 = inferred[2] ? inferred[2].hooks : ethers.ZeroAddress;

  const entryKey = makeKey(route.addresses[0], route.addresses[1], fee0, tick0, hooks0);
  const bridgeKey = makeKey(route.addresses[1], route.addresses[2], fee1, tick1, hooks1);
  const exitKey = makeKey(route.addresses[2], route.addresses[3], fee2, tick2, hooks2);
  const keys = [entryKey, bridgeKey, exitKey];
  const zeroForOneDirections = [
    zeroForOne(entryKey, route.addresses[0]),
    zeroForOne(bridgeKey, route.addresses[1]),
    zeroForOne(exitKey, route.addresses[2]),
  ];

  const amountText = readArg("amount", process.env.ROBINHOOD_MAINNET_OFFICIAL_ARB_BRIDGE_AMOUNT || "0.0001");
  const amountIn = ethers.parseUnits(amountText, 18);
  const quoterAbi = [
    {
      inputs: [
        {
          components: [
            {
              components: [
                { internalType: "Currency", name: "currency0", type: "address" },
                { internalType: "Currency", name: "currency1", type: "address" },
                { internalType: "uint24", name: "fee", type: "uint24" },
                { internalType: "int24", name: "tickSpacing", type: "int24" },
                { internalType: "address", name: "hooks", type: "address" },
              ],
              internalType: "struct PoolKey",
              name: "poolKey",
              type: "tuple",
            },
            { internalType: "bool", name: "zeroForOne", type: "bool" },
            { internalType: "uint128", name: "exactAmount", type: "uint128" },
            { internalType: "bytes", name: "hookData", type: "bytes" },
          ],
          internalType: "struct IV4Quoter.QuoteExactSingleParams",
          name: "params",
          type: "tuple",
        }
      ],
      name: "quoteExactInputSingle",
      outputs: [
        { internalType: "uint256", name: "amountOut", type: "uint256" },
        { internalType: "uint256", name: "gasEstimate", type: "uint256" },
      ],
      stateMutability: "nonpayable",
      type: "function",
    },
  ];

  const quoterContract = new ethers.Contract(quoter, quoterAbi, ethers.provider);
  let input = amountIn;
  const steps = [];
  console.log("Robinhood mainnet bridge-pair read-only route:");
  console.log(`  route=${route.symbols.join(" -> ")}`);
  console.log(`  addresses=${route.addresses.map((a) => ethers.getAddress(a)).join(" -> ")}`);
  for (let i = 0; i < keys.length; i += 1) {
    const poolKey = keys[i];
    const zeroForOneFlag = zeroForOneDirections[i];
    let quote;
    try {
      quote = await quoterContract.quoteExactInputSingle.staticCall(
        [poolKey, zeroForOneFlag, input, "0x"]
      );
    } catch (error) {
      const report = {
        schemaVersion: "robinhood-mainnet-bridge-pairs-readonly-v1",
        chainId: Number((await ethers.provider.getNetwork()).chainId),
        route: route.symbols,
        addresses: route.addresses,
        amountInRaw: input.toString(),
        profitable: false,
        broadcast: false,
        ok: false,
        detail: "quote_reverted",
        errorMessage: error.shortMessage || error.reason || error.message || String(error),
        hop: i + 1,
        poolKey,
        zeroForOne: zeroForOneFlag,
        note: "read-only V4 Quoter preflight; no broadcast attempted",
      };
      writeReport(report);
      console.log(JSON.stringify(report, null, 2));
      console.error(`mainnet bridge-pair read-only quote reverted at hop ${i + 1}`);
      console.error(`  error=${error.shortMessage || error.reason || error.message || error}`);
      process.exitCode = 1;
      return;
    }
    const [amountOut, gasEstimate] = quote;
    steps.push({
      hop: i + 1,
      amountInRaw: input.toString(),
      amountOutRaw: amountOut.toString(),
      gasEstimateRaw: gasEstimate.toString(),
      poolKey,
      zeroForOne: zeroForOneFlag,
    });
    console.log(
      `  hop ${i + 1}: ${ethers.formatUnits(input, 18)} -> ${ethers.formatUnits(amountOut, 18)}`
    );
    input = amountOut;
  }

  const finalAmount = input;
  const profit = finalAmount - amountIn;
  const report = {
    schemaVersion: "robinhood-mainnet-bridge-pairs-readonly-v1",
    chainId: Number((await ethers.provider.getNetwork()).chainId),
    route: route.symbols,
    addresses: route.addresses,
    amountInRaw: amountIn.toString(),
    finalAmountRaw: finalAmount.toString(),
    profitRaw: profit.toString(),
    profitable: profit > 0n,
    steps,
    broadcast: false,
    note: "read-only V4 Quoter preflight; no broadcast attempted",
  };
  writeReport(report);
  console.log(JSON.stringify(report, null, 2));
}

main().catch((error) => {
  console.error(error.message || error);
  process.exitCode = 1;
});
