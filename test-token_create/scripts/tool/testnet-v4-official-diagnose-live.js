const path = require("path");
const fs = require("fs");
const { ethers } = require("../lib/hardhat");
const poolKeyHelper = require("./v4-official-pool-keys");

const MANIFEST_PATH = process.env.FLASHLOAN_MANIFEST_PATH
  ? path.resolve(process.env.FLASHLOAN_MANIFEST_PATH)
  : path.resolve(__dirname, "../../config/testnet-v4-amm/manifest.json");

const ERC20_ABI = [
  "function balanceOf(address account) view returns (uint256)",
  "function totalSupply() view returns (uint256)",
];

function readArg(name, fallback) {
  const idx = process.argv.indexOf(`--${name}`);
  if (idx >= 0) return process.argv[idx + 1];
  return fallback;
}

function readManifest() {
  return JSON.parse(fs.readFileSync(MANIFEST_PATH, "utf8"));
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

function symbolForAddress(address, tz, ta, tb) {
  const low = address.toLowerCase();
  if (low === tz.toLowerCase()) return "TZ";
  if (low === ta.toLowerCase()) return "TA";
  if (low === tb.toLowerCase()) return "TB";
  return address;
}

async function printKnownTokensAndPools(owner, manifest, keys) {
  const poolManagerAddress = manifest.officialV4.poolManager;
  console.log("known manifest tokens:");
  for (const [label, address] of [["TZ", manifest.tz], ["TA", manifest.ta], ["TB", manifest.tb]]) {
    const token = new ethers.Contract(address, ERC20_ABI, owner);
    const totalSupply = await token.totalSupply();
    const poolManagerBalance = await token.balanceOf(poolManagerAddress);
    console.log(`  ${label} address=${address} total=${ethers.formatUnits(totalSupply, 18)} poolManager=${ethers.formatUnits(poolManagerBalance, 18)}`);
  }

  console.log("known manifest pools:");
  for (const [index, key] of keys.entries()) {
    const c0 = symbolForAddress(key.currency0, manifest.tz, manifest.ta, manifest.tb);
    const c1 = symbolForAddress(key.currency1, manifest.tz, manifest.ta, manifest.tb);
    console.log(`  pool ${index + 1} ${c0}:${c1} fee=${key.fee} tickSpacing=${key.tickSpacing} hooks=${key.hooks} id=${poolId(key)}`);
  }
}

async function printPoolDepth(owner, manifest, tz, keys, directions) {
  const poolManagerAddress = manifest.officialV4.poolManager;
  for (const [label, address] of [["TZ", manifest.tz], ["TA", manifest.ta], ["TB", manifest.tb]]) {
    const token = new ethers.Contract(address, ERC20_ABI, owner);
    const poolManagerBalance = await token.balanceOf(poolManagerAddress);
    const totalSupply = await token.totalSupply();
    const pct = totalSupply === 0n ? 0 : (Number(poolManagerBalance) / Number(totalSupply)) * 100;
    console.log(`${label} PoolManager balance=${ethers.formatUnits(poolManagerBalance, 18)} (${pct.toFixed(2)}% of total)`);
  }

  const makerAddress = manifest.officialV4 && manifest.officialV4.imbalanceMaker;
  if (!makerAddress) {
    console.log("OfficialV4ImbalanceMaker not deployed; run testnet-v4-official-make-arb-opportunity-live first");
    return;
  }
  const maker = await ethers.getContractAt("OfficialV4ImbalanceMaker", makerAddress);
  const depthSizes = process.env.ROBINHOOD_TESTNET_DIAGNOSE_DEPTH_SIZES || "0.001,1,100";
  console.log("imbalance maker staticCall depth probe (read-only):");
  for (const sizeText of depthSizes.split(",")) {
    const size = sizeText.trim();
    if (!size) continue;
    const amountIn = ethers.parseUnits(size, 18);
    try {
      await maker.runImbalance.staticCall(tz, keys, directions, amountIn);
      console.log(`  ${size} TZ: staticCall OK`);
    } catch (error) {
      console.log(`  ${size} TZ: staticCall reverted`);
      console.log(`    ${error.shortMessage || error.message || error}`);
    }
  }
}

async function printTriangularBalance(owner, manifest, tz, keys, directions) {
  const arbAddress = manifest.officialV4 && manifest.officialV4.arb;
  if (!arbAddress) {
    console.log("OfficialV4FlashArb not deployed; run testnet-v4-official-arb-live first");
    return;
  }
  const arb = await ethers.getContractAt("OfficialV4FlashArb", arbAddress);
  const amountIn = BigInt(process.env.ROBINHOOD_TESTNET_DIAGNOSE_AMOUNT || "100000000000000");
  const minProfit = BigInt(process.env.ROBINHOOD_TESTNET_DIAGNOSE_MIN_PROFIT || "1");
  console.log(`staticCall amountIn=${ethers.formatUnits(amountIn, 18)} tz, minProfit=${minProfit}`);
  try {
    const result = await arb.runArbitrage.staticCall(tz, keys, directions, amountIn, minProfit);
    const profit = result[3];
    console.log(`  outTb=${ethers.formatUnits(result[0], 18)}`);
    console.log(`  outTa=${ethers.formatUnits(result[1], 18)}`);
    console.log(`  outTz=${ethers.formatUnits(result[2], 18)}`);
    console.log(`  profit=${ethers.formatUnits(profit, 18)} tz`);
    console.log(profit > 0n ? "verdict: route is currently imbalanced / profitable in staticCall" : "verdict: route is currently balanced / no static profit");
  } catch (error) {
    console.log("staticCall reverted; likely no current executable imbalance or depth too thin for this probe");
    console.log(`  ${error.shortMessage || error.message || error}`);
  }
}

async function main() {
  const mode = readArg("mode", process.env.ROBINHOOD_TESTNET_DIAGNOSE_MODE || "all");
  const manifest = readManifest();
  if (!manifest.officialV4 || !manifest.officialV4.poolManager) {
    throw new Error("officialV4 pool manifest missing; run testnet-v4-official-pool-create-live first");
  }

  const [owner] = await ethers.getSigners();
  const tz = manifest.tz;
  const ta = manifest.ta;
  const tb = manifest.tb;
  const fee = Number(manifest.officialV4.fee);
  const tickSpacing = Number(manifest.officialV4.tickSpacing);
  const hooks = manifest.officialV4.hooks || ethers.ZeroAddress;
  const poolManagerAddress = manifest.officialV4.poolManager;

  const routeAddresses = [tz, tb, ta, tz];
  const explicitPoolKeysJson = readArg(
    "pool-keys-json",
    process.env.ROBINHOOD_TESTNET_OFFICIAL_ARB_POOL_KEYS_JSON || ""
  );
  const keys = poolKeyHelper.resolveRoutePoolKeys(
    routeAddresses,
    explicitPoolKeysJson,
    { fee, tickSpacing, hooks }
  );
  const directions = [zeroForOne(keys[0], tz), zeroForOne(keys[1], tb), zeroForOne(keys[2], ta)];

  console.log("official V4 diagnostic (read-only):");
  console.log(`  mode=${mode}`);
  console.log(`  poolManager=${poolManagerAddress}`);
  console.log(`  route=TZ -> TB -> TA -> TZ`);
  console.log(`  directions=${directions.join(",")}`);

  if (mode === "depth" || mode === "all") {
    console.log("\n[depth]");
    await printKnownTokensAndPools(owner, manifest, keys);
    await printPoolDepth(owner, manifest, tz, keys, directions);
  }
  if (mode === "balance" || mode === "all") {
    console.log("\n[balance]");
    await printKnownTokensAndPools(owner, manifest, keys);
    await printTriangularBalance(owner, manifest, tz, keys, directions);
  }
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
