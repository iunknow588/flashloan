const path = require("path");
const fs = require("fs");
const { ethers } = require("../lib/hardhat");

const MANIFEST_PATH = process.env.FLASHLOAN_MANIFEST_PATH
  ? path.resolve(process.env.FLASHLOAN_MANIFEST_PATH)
  : path.resolve(__dirname, "../../config/testnet-v4-amm/manifest.json");
const CHAIN_PATH = process.env.FLASHLOAN_CHAIN_JSON
  ? path.resolve(process.env.FLASHLOAN_CHAIN_JSON)
  : path.resolve(__dirname, "../../../flashloan/src/chains/robinhood-testnet/chain.json");
const POOL_INDEX_PATH = process.env.FLASHLOAN_V4_POOL_INDEX
  ? path.resolve(process.env.FLASHLOAN_V4_POOL_INDEX)
  : path.resolve(__dirname, "../../../flashloan/src/runtime/evidence/robinhood-testnet/v4_pool_index/latest.json");

function readManifest() {
  return JSON.parse(fs.readFileSync(MANIFEST_PATH, "utf8"));
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

function loadExplicitPoolKeysJson() {
  const jsonText = readArg(
    "pool-keys-json",
    process.env.ROBINHOOD_TESTNET_OFFICIAL_ARB_POOL_KEYS_JSON || ""
  );
  if (!jsonText) return null;
  const parsed = JSON.parse(jsonText);
  if (!Array.isArray(parsed) || parsed.length !== 3) {
    throw new Error("official V4 bridge-pair pool keys json must be a 3-element array");
  }
  return parsed.map((key) => ({
    currency0: String(key.currency0 || ""),
    currency1: String(key.currency1 || ""),
    fee: Number(key.fee),
    tickSpacing: Number(key.tickSpacing),
    hooks: String(key.hooks || ""),
  }));
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

function poolId(key) {
  return ethers.keccak256(
    ethers.AbiCoder.defaultAbiCoder().encode(
      ["tuple(address currency0,address currency1,uint24 fee,int24 tickSpacing,address hooks)"],
      [key]
    )
  );
}

function stateSlot(poolIdHex) {
  return ethers.keccak256(ethers.concat([poolIdHex, ethers.zeroPadValue("0x06", 32)]));
}

function decodeRevertData(rawData) {
  const data = String(rawData || "");
  if (!data.startsWith("0x") || data.length < 10) {
    return { data, selector: data, errorDetail: data || "no_revert_data" };
  }
  const selector = data.slice(0, 10).toLowerCase();
  const knownSelectors = {
    "0x4f422847": { name: "NoProfitableRoute()" },
    "0x16301234": { name: "ArbitrageNotProfitable(received, required)" },
    "0x90b8ec18": { name: "TransferFailed()" },
    "0xa87726a1": { name: "BadPoolManagerCallback()" },
    "0x41abc801": { name: "InvalidRequest()" },
  };
  const known = knownSelectors[selector];
  if (!known) return { data, selector, errorDetail: `revert selector ${selector}` };
  return { data, selector, errorDetail: known.name };
}

function defaultRoute() {
  const chain = readJsonOr(CHAIN_PATH, {});
  const manifest = readManifest();
  const weth =
    process.env.ROBINHOOD_TESTNET_WETH_ADDRESS ||
    (chain && chain.contracts && chain.contracts.wrapped_native) ||
    "0x7943e237c7F95DA44E0301572D358911207852Fa";
  const c9 = process.env.ROBINHOOD_TESTNET_C9F9C8_ADDRESS || "0xC9f9c86933092BbbfFF3CCb4b105A4A94bf3Bd4E";
  const zero = ethers.ZeroAddress;
  return {
    symbols: ["WETH", "ETH", "0XC9F9C8", "WETH"],
    addresses: [weth, zero, c9, weth],
    fee0: Number(process.env.ROBINHOOD_TESTNET_ENTRY_FEE || "500"),
    tick0: Number(process.env.ROBINHOOD_TESTNET_ENTRY_TICK_SPACING || "10"),
    fee1: Number(process.env.ROBINHOOD_TESTNET_BRIDGE_FEE || "500"),
    tick1: Number(process.env.ROBINHOOD_TESTNET_BRIDGE_TICK_SPACING || "10"),
    fee2: Number(process.env.ROBINHOOD_TESTNET_EXIT_FEE || "3000"),
    tick2: Number(process.env.ROBINHOOD_TESTNET_EXIT_TICK_SPACING || "60"),
  };
}

async function resolveRoute() {
  const manifest = readManifest();
  const routeText = readArg("route", process.env.ROBINHOOD_TESTNET_OFFICIAL_ARB_BRIDGE_ROUTE || "");
  if (routeText) {
    const symbols = parseSymbols(routeText);
    const envAddresses = parseAddresses(process.env.ROBINHOOD_TESTNET_OFFICIAL_ARB_BRIDGE_ADDRESSES || "");
    const map = {
      TZ: manifest.tz,
      TA: manifest.ta,
      TB: manifest.tb,
    };
    const addresses = envAddresses.length > 0
      ? envAddresses
      : symbols.map((symbol) => {
          const address = map[symbol];
          if (!address) throw new Error(`route symbol ${symbol} has no address; pass ROBINHOOD_TESTNET_OFFICIAL_ARB_BRIDGE_ADDRESSES`);
          return address;
        });
    if (symbols.length !== 4 || addresses.length !== 4 || symbols[0].toLowerCase() !== symbols[3].toLowerCase()) {
      throw new Error("bridge-pair arb route must be a 4-symbol closed loop, e.g. WETH -> ETH -> 0XC9F9C8 -> WETH");
    }
    if (addresses[0].toLowerCase() !== addresses[3].toLowerCase()) {
      throw new Error("bridge-pair arb route must close: first and last addresses must match");
    }
    const fallback = defaultRoute();
    return {
      symbols,
      addresses,
      fee0: Number(process.env.ROBINHOOD_TESTNET_ENTRY_FEE || "3000"),
      tick0: Number(process.env.ROBINHOOD_TESTNET_ENTRY_TICK_SPACING || "60"),
      fee1: Number(process.env.ROBINHOOD_TESTNET_BRIDGE_FEE || "3000"),
      tick1: Number(process.env.ROBINHOOD_TESTNET_BRIDGE_TICK_SPACING || "60"),
      fee2: Number(process.env.ROBINHOOD_TESTNET_EXIT_FEE || "3000"),
      tick2: Number(process.env.ROBINHOOD_TESTNET_EXIT_TICK_SPACING || "60"),
      // Try to infer exact fee/tickSpacing from the V4 pool index when provided.
      inferFromPoolIndex: true,
      fallback,
    };
  }
  return defaultRoute();
}

function inferPoolParamsFromIndex(route) {
  const explicitPoolKeys = loadExplicitPoolKeysJson();
  if (explicitPoolKeys) return explicitPoolKeys;
  const index = readJsonOr(POOL_INDEX_PATH, {});
  const pools = [...(index.poolKeys || []), ...(index.pools || [])];
  const pairs = [
    [route.addresses[0], route.addresses[1]],
    [route.addresses[1], route.addresses[2]],
    [route.addresses[2], route.addresses[3]],
  ];
  const out = [];
  for (const [a, b] of pairs) {
    const matches = [];
    for (const pool of pools) {
      const pk = pool.poolKey;
      if (!pk) continue;
      const cur0 = String(pk.currency0 || "").toLowerCase();
      const cur1 = String(pk.currency1 || "").toLowerCase();
      const want0 = String(a || "").toLowerCase();
      const want1 = String(b || "").toLowerCase();
      const ok = (cur0 === want0 && cur1 === want1) || (cur0 === want1 && cur1 === want0);
      if (ok) {
        matches.push({
          fee: Number(pk.fee),
          tickSpacing: Number(pk.tickSpacing),
          hooks: String(pk.hooks || ethers.ZeroAddress),
          initializeBlockNumber: Number(pool.initializeBlockNumber || pk.initializeBlockNumber || 0),
        });
      }
    }
    if (!matches.length) {
      out.push(null);
      continue;
    }
    matches.sort((left, right) => right.initializeBlockNumber - left.initializeBlockNumber);
    out.push({
      fee: matches[0].fee,
      tickSpacing: matches[0].tickSpacing,
      hooks: matches[0].hooks,
    });
  }
  return out;
}

async function main() {
  const manifest = readManifest();
  const official = manifest.officialV4 || {};
  if (!official.poolManager || !official.arb) {
    throw new Error("officialV4.poolManager and officialV4.arb missing; run testnet-v4-official-arb-live first");
  }

  const [owner] = await ethers.getSigners();
  const route = await resolveRoute();
  const inferred = route.inferFromPoolIndex ? inferPoolParamsFromIndex(route) : [];
  const fee0 = inferred[0] ? inferred[0].fee : route.fee0;
  const tick0 = inferred[0] ? inferred[0].tickSpacing : route.tick0;
  const fee1 = inferred[1] ? inferred[1].fee : route.fee1;
  const tick1 = inferred[1] ? inferred[1].tickSpacing : route.tick1;
  const fee2 = inferred[2] ? inferred[2].fee : route.fee2;
  const tick2 = inferred[2] ? inferred[2].tickSpacing : route.tick2;
  const hooks = official.hooks || ethers.ZeroAddress;

  const entryHooks = inferred[0] ? inferred[0].hooks : hooks;
  const bridgeHooks = inferred[1] ? inferred[1].hooks : hooks;
  const exitHooks = inferred[2] ? inferred[2].hooks : hooks;

  const entryKey = makeKey(route.addresses[0], route.addresses[1], fee0, tick0, entryHooks);
  const bridgeKey = makeKey(route.addresses[1], route.addresses[2], fee1, tick1, bridgeHooks);
  const exitKey = makeKey(route.addresses[2], route.addresses[3], fee2, tick2, exitHooks);

  const entryZeroForOne = zeroForOne(entryKey, route.addresses[0]);
  const bridgeZeroForOne = zeroForOne(bridgeKey, route.addresses[1]);
  const exitZeroForOne = zeroForOne(exitKey, route.addresses[2]);

  console.log("official V4 bridge-pair route:");
  console.log(`  route=${route.symbols.join(" -> ")}`);
  console.log(`  addresses=${route.addresses.map((a) => ethers.getAddress(a)).join(" -> ")}`);
  console.log(`  direction=${[entryZeroForOne, bridgeZeroForOne, exitZeroForOne].join(",")}`);
  console.log(`  entryKey=[${entryKey.currency0},${entryKey.currency1},${entryKey.fee},${entryKey.tickSpacing},${entryKey.hooks}]`);
  console.log(`  bridgeKey=[${bridgeKey.currency0},${bridgeKey.currency1},${bridgeKey.fee},${bridgeKey.tickSpacing},${bridgeKey.hooks}]`);
  console.log(`  exitKey=[${exitKey.currency0},${exitKey.currency1},${exitKey.fee},${exitKey.tickSpacing},${exitKey.hooks}]`);

  const poolManager = await ethers.getContractAt(
    ["function extsload(bytes32 slot) external view returns (bytes32)"],
    official.poolManager,
    owner
  );
  for (const [name, key] of [["entry", entryKey], ["bridge", bridgeKey], ["exit", exitKey]]) {
    const id = poolId(key);
    const slot = stateSlot(id);
    const slot0 = await poolManager.extsload(slot);
    const slot3 = await poolManager.extsload(ethers.toBeHex(BigInt(slot) + 3n));
    const sqrtPriceX96 = BigInt(slot0) & ((1n << 160n) - 1n);
    const liquidity = BigInt(slot3) & ((1n << 128n) - 1n);
    console.log(`  ${name} pool id=${id}`);
    console.log(`    sqrtPriceX96=${sqrtPriceX96}`);
    console.log(`    liquidity=${liquidity}`);
  }

  const amountText = readArg("amount", process.env.ROBINHOOD_TESTNET_OFFICIAL_ARB_BRIDGE_AMOUNT || "1");
  const amountIn = ethers.parseUnits(amountText, 18);
  const minProfit = BigInt(process.env.ROBINHOOD_TESTNET_OFFICIAL_ARB_BRIDGE_MIN_PROFIT || "1");
  const arb = await ethers.getContractAt("OfficialV4FlashArb", official.arb, owner);

  let staticResult;
  try {
    staticResult = await arb.runArbitrageBestBridgePairs.staticCall(
      route.addresses[0],
      entryKey,
      exitKey,
      entryZeroForOne,
      exitZeroForOne,
      [bridgeKey],
      [bridgeZeroForOne],
      amountIn,
      minProfit
    );
  } catch (error) {
    const revertInfo = decodeRevertData(error.data || error.info?.error?.data || "");
    console.log(
      "\nOFFICIAL_V4_BRIDGE_PAIRS_RESULT_JSON " +
        JSON.stringify({
          ok: false,
          error: "static_call_reverted",
          detail: revertInfo.errorDetail || error.shortMessage || error.reason || error.message || error,
          errorData: revertInfo.data,
          errorSelector: revertInfo.selector,
          routeSymbols: route.symbols,
          amount: amountText,
          staticCall: "reverted",
          method: "runArbitrageBestBridgePairs",
        })
    );
    console.error("official V4 bridge-pair staticCall reverted");
    console.error(`  shortMessage=${error.shortMessage || ""}`);
    console.error(`  errorDetail=${revertInfo.errorDetail}`);
    console.error(`  data=${revertInfo.data}`);
    return;
  }

  const profit = staticResult[3];
  const ok = profit > 0n;
  console.log("official V4 bridge-pair static result:");
  console.log(`  outY=${ethers.formatUnits(staticResult[0], 18)}`);
  console.log(`  outZ=${ethers.formatUnits(staticResult[1], 18)}`);
  console.log(`  outTokenZ=${ethers.formatUnits(staticResult[2], 18)}`);
  console.log(`  profit=${ethers.formatUnits(profit, 18)}`);
  console.log(`  routeIndex=${staticResult[4].toString()}`);

  if (!ok) {
    console.log(
      "\nOFFICIAL_V4_BRIDGE_PAIRS_RESULT_JSON " +
        JSON.stringify({
          ok: false,
          detail: "static_call_not_profitable",
          routeSymbols: route.symbols,
          amount: amountText,
          bestAmount: amountText,
          staticCall: "not_profitable",
          method: "runArbitrageBestBridgePairs",
          profit: profit.toString(),
        })
    );
    return;
  }

  const broadcastEnabled =
    process.env.ROBINHOOD_TESTNET_ALLOW_V4_AMM_LIVE_TEST === "true" &&
    process.env.ROBINHOOD_TESTNET_ALLOW_OFFICIAL_V4_BRIDGE_PAIRS_BROADCAST === "true";

  console.log(
    "\nOFFICIAL_V4_BRIDGE_PAIRS_RESULT_JSON " +
      JSON.stringify({
        ok: true,
        detail: broadcastEnabled ? "broadcast_enabled_static_ok" : "broadcast_disabled_static_ok",
        routeSymbols: route.symbols,
        amount: amountText,
        bestAmount: amountText,
        staticCall: "ok",
        broadcastEnabled,
        method: "runArbitrageBestBridgePairs",
        profit: profit.toString(),
      })
  );

  if (!broadcastEnabled) {
    console.log("broadcast disabled; not submitting tx. Set both ROBINHOOD_TESTNET_ALLOW_V4_AMM_LIVE_TEST and ROBINHOOD_TESTNET_ALLOW_OFFICIAL_V4_BRIDGE_PAIRS_BROADCAST=true to broadcast");
    return;
  }

  const tx = await arb.runArbitrageBestBridgePairs(
    route.addresses[0],
    entryKey,
    exitKey,
    entryZeroForOne,
    exitZeroForOne,
    [bridgeKey],
    [bridgeZeroForOne],
    amountIn,
    minProfit
  );
  const receipt = await tx.wait();
  console.log("official V4 bridge-pair broadcast:");
  console.log(`  txHash=${receipt.hash}`);
  console.log(`  gasUsed=${receipt.gasUsed.toString()}`);
  console.log(
    "\nOFFICIAL_V4_BRIDGE_PAIRS_RESULT_JSON " +
      JSON.stringify({
        ok: true,
        detail: "broadcast_complete",
        routeSymbols: route.symbols,
        amount: amountText,
        bestAmount: amountText,
        broadcast: true,
        txHash: receipt.hash,
        method: "runArbitrageBestBridgePairs",
        profit: profit.toString(),
      })
  );
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
