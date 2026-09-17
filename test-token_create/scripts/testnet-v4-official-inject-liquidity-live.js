const path = require("path");
const fs = require("fs");
const { ethers, artifacts } = require("./lib/hardhat");
const v4Liquidity = require("./lib/v4-liquidity-common");

const MANIFEST_PATH = path.resolve(__dirname, "../config/testnet-v4-amm/manifest.json");

const POSITION_MANAGER_ADDRESS = ethers.getAddress("0x58daec3116aae6d93017baaea7749052e8a04fa7");
const PERMIT2_ADDRESS = ethers.getAddress("0x000000000022d473030f116ddee9f6b43ac78ba3");

const POSITION_MANAGER_ABI = [
  "function modifyLiquidities(bytes unlockData, uint256 deadline)",
];

const PERMIT2_ABI = [
  "function approve(address token, address spender, uint160 amount, uint48 expiration)",
];

const ERC20_ABI = [
  "function approve(address spender, uint256 amount) returns (bool)",
  "function balanceOf(address account) view returns (uint256)",
  "function totalSupply() view returns (uint256)",
];

function readArg(name, fallback) {
  const idx = process.argv.indexOf(`--${name}`);
  if (idx >= 0) return process.argv[idx + 1];
  return fallback;
}

function readBoolArg(name, fallback) {
  const value = readArg(name, "");
  if (value) return value === "true" || value === "1" || value === "yes";
  return fallback;
}

const PAIRS = [
  {
    name: "tz-ta",
    tokenA: null,
    tokenB: null,
    reserveA: toToken(1_000_000),
    reserveB: toToken(100_000),
    fallbackTick: -23028,
  },
  {
    name: "ta-tb",
    tokenA: null,
    tokenB: null,
    reserveA: toToken(100_000),
    reserveB: toToken(1_000_000),
    fallbackTick: -23028,
  },
  {
    name: "tb-tz",
    tokenA: null,
    tokenB: null,
    reserveA: toToken(1_000_000),
    reserveB: toToken(100_000),
    fallbackTick: 23027,
  },
];

function toToken(value) {
  return BigInt(value) * 10n ** 18n;
}

function readManifest() {
  return JSON.parse(fs.readFileSync(MANIFEST_PATH, "utf8"));
}

function writeManifest(manifest) {
  fs.writeFileSync(MANIFEST_PATH, JSON.stringify(manifest, null, 2) + "\n", "utf8");
}

function sortPair(a, b) {
  return BigInt(a.toLowerCase()) < BigInt(b.toLowerCase()) ? [a, b] : [b, a];
}

function poolKey(currency0, currency1, fee, tickSpacing, hooks) {
  return {
    currency0,
    currency1,
    fee,
    tickSpacing,
    hooks,
  };
}

function mintParams(key, tickLower, tickUpper, liquidity, owner) {
  const abiCoder = ethers.AbiCoder.defaultAbiCoder();
  const keyTuple = [key.currency0, key.currency1, key.fee, key.tickSpacing, key.hooks];
  return abiCoder.encode(
    ["tuple(address,address,uint24,int24,address)", "int24", "int24", "uint256", "uint128", "uint128", "address", "bytes"],
    [keyTuple, tickLower, tickUpper, liquidity, (1n << 128n) - 1n, (1n << 128n) - 1n, owner, "0x"]
  );
}

async function logOwnerBalance(owner, token, label) {
  const total = await token.totalSupply();
  const balance = await token.balanceOf(owner.address);
  const pct = (Number(balance) / Number(total)) * 100;
  console.log(`  ${label} owner balance=${ethers.formatUnits(balance, 18)} (${pct.toFixed(2)}% of total; 80% creation reserve is meant to be spent on later liquidity, not enforced after replenishment)`);
}

async function main() {
  const allowBroadcast = readBoolArg(
    "broadcast",
    process.env.ROBINHOOD_TESTNET_ALLOW_OFFICIAL_V4_REPLENISH_BROADCAST === "true"
  );
  const anchorTokenAmount = ethers.parseUnits(readArg("amount", process.env.ROBINHOOD_TESTNET_REPLENISH_LIQUIDITY || "2000"), 18);

  const manifest = readManifest();
  if (!manifest.officialV4 || !manifest.officialV4.poolManager) {
    throw new Error("officialV4.poolManager missing; run testnet-v4-official-pool-create-live first");
  }

  const [owner] = await ethers.getSigners();
  const tz = await ethers.getContractAt("TestERC20", manifest.tz);
  const ta = await ethers.getContractAt("TestERC20", manifest.ta);
  const tb = await ethers.getContractAt("TestERC20", manifest.tb);
  const positionManager = await ethers.getContractAt(POSITION_MANAGER_ABI, POSITION_MANAGER_ADDRESS, owner);
  const permit2 = await ethers.getContractAt(PERMIT2_ABI, PERMIT2_ADDRESS, owner);
  const tokens = [tz, ta, tb];

  const fee = Number(manifest.officialV4.fee);
  const tickSpacing = Number(manifest.officialV4.tickSpacing);
  const hooks = manifest.officialV4.hooks || ethers.ZeroAddress;
  const [fullLower, fullUpper] = v4Liquidity.fullRangeTicks(tickSpacing);

  console.log("official V4 manual liquidity replenisher");
  console.log(`  poolManager=${manifest.officialV4.poolManager}`);
  console.log(`  hook=${hooks}`);
  console.log(`  requestedAnchorTokenPerPair=${ethers.formatUnits(anchorTokenAmount, 18)}`);

  const actions = [];
  const actionParams = [];
  const ranges = [];
  const requiredByToken = new Map();
  for (const pair of PAIRS) {
    const tokenA = pair.name === "tz-ta" ? tz : pair.name === "ta-tb" ? ta : tb;
    const tokenB = pair.name === "tz-ta" ? ta : pair.name === "ta-tb" ? tb : tz;
    const addressA = await tokenA.getAddress();
    const addressB = await tokenB.getAddress();
    let amountA;
    let amountB;
    if (pair.reserveA >= pair.reserveB) {
      amountA = anchorTokenAmount;
      amountB = (anchorTokenAmount * pair.reserveB) / pair.reserveA;
    } else {
      amountA = (anchorTokenAmount * pair.reserveA) / pair.reserveB;
      amountB = anchorTokenAmount;
    }
    const [currency0, currency1] = sortPair(addressA, addressB);
    const reserve0 = currency0 === addressA ? amountA : amountB;
    const reserve1 = currency0 === addressA ? amountB : amountA;
    const sqrtPriceX96 = v4Liquidity.sqrtPriceX96ForRatio(reserve0, reserve1);
    const liquidity = v4Liquidity.fullRangeLiquidityForAmounts(sqrtPriceX96, reserve0, reserve1);
    const key = poolKey(currency0, currency1, fee, tickSpacing, hooks);
    const lower = fullLower;
    const upper = fullUpper;
    ranges.push({ name: pair.name, key, lower, upper, targetA: amountA, targetB: amountB, liquidity });
    actions.push(0x02);
    actionParams.push(mintParams(key, lower, upper, liquidity, owner.address));
    actions.push(0x0d);
    actionParams.push(ethers.AbiCoder.defaultAbiCoder().encode(["address", "address"], [currency0, currency1]));
    requiredByToken.set(addressA.toLowerCase(), (requiredByToken.get(addressA.toLowerCase()) || 0n) + amountA);
    requiredByToken.set(addressB.toLowerCase(), (requiredByToken.get(addressB.toLowerCase()) || 0n) + amountB);
  }

  console.log("would add liquidity to official V4 pools:");
  for (const r of ranges) {
    console.log(`  ${r.name} add=${ethers.formatUnits(r.targetA, 18)}+${ethers.formatUnits(r.targetB, 18)} liquidity≈${ethers.formatUnits(r.liquidity, 0)} range=[${r.lower},${r.upper}] key=[${r.key.currency0},${r.key.currency1},${r.key.fee},${r.key.tickSpacing},${r.key.hooks}]`);
  }

  for (const token of tokens) {
    const address = await token.getAddress();
    const required = requiredByToken.get(address.toLowerCase()) || 0n;
    const balance = await token.balanceOf(owner.address);
    if (balance < required) {
      throw new Error(`${await token.symbol()} owner balance ${ethers.formatUnits(balance, 18)} is below required ${ethers.formatUnits(required, 18)}`);
    }
  }

  if (!allowBroadcast) {
    console.log("broadcast disabled; replenish action prepared for review (not broadcast)");
    return;
  }

  const maxUint = ethers.MaxUint256;
  const maxUint160 = (1n << 160n) - 1n;
  const maxUint48 = (1n << 48n) - 1n;
  for (const token of tokens) {
    await token.approve(PERMIT2_ADDRESS, maxUint);
    await token.approve(POSITION_MANAGER_ADDRESS, maxUint);
    await permit2.approve(await token.getAddress(), POSITION_MANAGER_ADDRESS, maxUint160, maxUint48);
  }

  await logOwnerBalance(owner, tz, "tz");
  await logOwnerBalance(owner, ta, "ta");
  await logOwnerBalance(owner, tb, "tb");

  const unlockData = ethers.AbiCoder.defaultAbiCoder().encode(
    ["bytes", "bytes[]"],
    [ethers.hexlify(new Uint8Array(actions)), actionParams]
  );
  const deadline = (await ethers.provider.getBlock("latest")).timestamp + 600;
  const tx = await positionManager.modifyLiquidities(unlockData, deadline);
  const receipt = await tx.wait();
  console.log(`official V4 replenish liquidity tx=${receipt.hash} gasUsed=${receipt.gasUsed}`);

  manifest.officialV4.lastReplenish = {
    txHash: receipt.hash,
    gasUsed: receipt.gasUsed.toString(),
    runAt: new Date().toISOString(),
    liquidityToAdd: anchorTokenAmount.toString(),
  };
  writeManifest(manifest);

  console.log("result: OK. manual official V4 liquidity injected without burning tokens.");
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
