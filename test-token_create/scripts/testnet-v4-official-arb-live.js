const path = require("path");
const fs = require("fs");
const { ethers, artifacts } = require("./lib/hardhat");

const MANIFEST_PATH = path.resolve(__dirname, "../config/testnet-v4-amm/manifest.json");
const ERC20_ABI = [
  "function totalSupply() view returns (uint256)",
  "function balanceOf(address) view returns (uint256)",
  "function mint(address to, uint256 amount)",
];

const MIN_SQRT_PRICE = 4295128740n;
const MAX_SQRT_PRICE = 1461446703485210103287273052203988822378723970341n;

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
  return ethers.keccak256(ethers.AbiCoder.defaultAbiCoder().encode(
    ["tuple(address currency0,address currency1,uint24 fee,int24 tickSpacing,address hooks)"],
    [key]
  ));
}

async function logOwnerBalance(owner, token, label) {
  const total = await token.totalSupply();
  const balance = await token.balanceOf(owner.address);
  const pct = (Number(balance) / Number(total)) * 100;
  console.log(`  ${label} owner balance=${ethers.formatUnits(balance, 18)} (${pct.toFixed(2)}% of total; 80% creation reserve is meant for later liquidity, not a post-run floor)`);
}

async function main() {
  if (process.env.ROBINHOOD_TESTNET_ALLOW_V4_AMM_LIVE_TEST !== "true") {
    throw new Error("ROBINHOOD_TESTNET_ALLOW_V4_AMM_LIVE_TEST must be true");
  }

  const [owner] = await ethers.getSigners();
  const manifest = readManifest();
  if (!manifest.officialV4 || !manifest.officialV4.poolManager) {
    throw new Error("officialV4 pool manifest missing; run testnet-v4-official-pool-create-live first");
  }

  const tz = manifest.tz;
  const ta = manifest.ta;
  const tb = manifest.tb;
  const fee = Number(manifest.officialV4.fee);
  const tickSpacing = Number(manifest.officialV4.tickSpacing);
  const hooks = manifest.officialV4.hooks || ethers.ZeroAddress;
  const poolManagerAddress = manifest.officialV4.poolManager;

  const keys = [
    makeKey(tz, tb, fee, tickSpacing, hooks),
    makeKey(tb, ta, fee, tickSpacing, hooks),
    makeKey(ta, tz, fee, tickSpacing, hooks),
  ];
  const directions = [zeroForOne(keys[0], tz), zeroForOne(keys[1], tb), zeroForOne(keys[2], ta)];

  const poolManager = new ethers.Contract(poolManagerAddress, [], owner);
  console.log("official V4 route:");
  console.log(`  poolManager=${poolManagerAddress}`);
  console.log(`  tz=${tz}`);
  console.log(`  ta=${ta}`);
  console.log(`  tb=${tb}`);
  console.log(`  directions=${directions.join(",")}`);

  const Token = (address) => new ethers.Contract(address, ERC20_ABI, owner);
  for (const [label, address] of [["tz", tz], ["ta", ta], ["tb", tb]]) {
    const poolBalance = await Token(address).balanceOf(poolManagerAddress);
    console.log(`  ${label} PoolManager balance=${ethers.formatUnits(poolBalance, 18)}`);
    if (poolBalance === 0n) throw new Error(`${label} PoolManager balance is zero`);
  }
  for (const [index, key] of keys.entries()) {
    console.log(`  pool${index + 1} id=${poolId(key)}`);
  }

  await logOwnerBalance(owner, Token(tz), "tz");
  await logOwnerBalance(owner, Token(ta), "ta");
  await logOwnerBalance(owner, Token(tb), "tb");

  let arb;
  const Factory = await ethers.getContractFactory("OfficialV4FlashArb");
  const artifact = await artifacts.readArtifact("OfficialV4FlashArb");
  const expectedBytecode = String(artifact.deployedBytecode || "").toLowerCase();
  const existingArb = manifest.officialV4.arb || "";
  let existingBytecode = "0x";
  if (existingArb) {
    existingBytecode = String(await ethers.provider.getCode(existingArb)).toLowerCase();
  }
  if (existingArb && existingBytecode !== "0x" && existingBytecode === expectedBytecode) {
    arb = await ethers.getContractAt("OfficialV4FlashArb", existingArb);
  } else {
    arb = await Factory.deploy(poolManagerAddress);
    await arb.waitForDeployment();
    manifest.officialV4.arb = await arb.getAddress();
    fs.writeFileSync(MANIFEST_PATH, JSON.stringify(manifest, null, 2) + "\n", "utf8");
    console.log(`deployed OfficialV4FlashArb=${manifest.officialV4.arb}`);
  }

  const amountIn = BigInt(process.env.ROBINHOOD_TESTNET_OFFICIAL_ARB_AMOUNT || "100000000000000");
  const minProfit = BigInt(process.env.ROBINHOOD_TESTNET_OFFICIAL_ARB_MIN_PROFIT || "1");
  let result;
  try {
    result = await arb.runArbitrage.staticCall(tz, keys, directions, amountIn, minProfit);
  } catch (error) {
    console.error("official V4 static arbitrage reverted");
    console.error(`  shortMessage=${error.shortMessage || ""}`);
    console.error(`  data=${error.data || error.info?.error?.data || ""}`);
    console.error(`  reason=${error.reason || error.message || error}`);
    throw error;
  }
  console.log("official V4 static arbitrage result:");
  console.log(`  outTb=${ethers.formatUnits(result[0], 18)}`);
  console.log(`  outTa=${ethers.formatUnits(result[1], 18)}`);
  console.log(`  outTz=${ethers.formatUnits(result[2], 18)}`);
  console.log(`  profit=${ethers.formatUnits(result[3], 18)} tz`);
  if (result[3] <= 0n) throw new Error("official V4 static arbitrage is not profitable");

  if (process.env.ROBINHOOD_TESTNET_ALLOW_OFFICIAL_V4_ARB_BROADCAST !== "true") {
    console.log("broadcast disabled; static preflight passed");
    return;
  }

  const tx = await arb.runArbitrage(tz, keys, directions, amountIn, minProfit);
  const receipt = await tx.wait();
  const txHash = receipt.hash;
  const record = {
    txHash,
    runAt: new Date().toISOString(),
    deployer: owner.address,
    arb: await arb.getAddress(),
    poolManager: poolManagerAddress,
    amountIn: amountIn.toString(),
    outTb: result[0].toString(),
    outTa: result[1].toString(),
    outTz: result[2].toString(),
    profit: result[3].toString(),
    gasUsed: receipt.gasUsed.toString(),
    route: { keys, directions },
    reservePolicy: "80_percent_creation_reserve_is_for_later_liquidity_not_a_post_run_floor",
  };
  manifest.officialV4.lastArbLiveTest = record;
  fs.writeFileSync(MANIFEST_PATH, JSON.stringify(manifest, null, 2) + "\n", "utf8");
  console.log("official V4 live arbitrage broadcast:");
  console.log(`  txHash=${txHash}`);
  console.log(`  gasUsed=${receipt.gasUsed}`);
  console.log(`  profit=${ethers.formatUnits(result[3], 18)} tz`);
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
