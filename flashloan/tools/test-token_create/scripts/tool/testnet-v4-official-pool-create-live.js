const path = require("path");
const fs = require("fs");
const { ethers } = require("../lib/hardhat");
const v4Liquidity = require("../lib/v4-liquidity-common");

const POOL_MANAGER_ADDRESS = ethers.getAddress(
  process.env.FLASHLOAN_V4_POOL_MANAGER_ADDRESS || "0x8366a39cc670b4001a1121b8f6a443a643e40951"
);
const POSITION_MANAGER_ADDRESS = ethers.getAddress(
  process.env.FLASHLOAN_POSITION_MANAGER_ADDRESS || "0x58daec3116aae6d93017baaea7749052e8a04fa7"
);
const PERMIT2_ADDRESS = ethers.getAddress(
  process.env.FLASHLOAN_PERMIT2_ADDRESS || "0x000000000022d473030f116ddee9f6b43ac78ba3"
);

const MANIFEST_PATH = process.env.FLASHLOAN_MANIFEST_PATH
  ? path.resolve(process.env.FLASHLOAN_MANIFEST_PATH)
  : path.resolve(__dirname, "../../config/testnet-v4-amm/manifest.json");

const POOL_MANAGER_ABI = [
  "function initialize((address,address,uint24,int24,address) key, uint160 sqrtPriceX96) returns (int24 tick)",
  "function unlock(bytes data) returns (bytes)",
];

const POSITION_MANAGER_ABI = [
  "function modifyLiquidities(bytes unlockData, uint256 deadline)",
];

const PERMIT2_ABI = [
  "function approve(address token, address spender, uint160 amount, uint48 expiration)",
];

function toToken(value) {
  return BigInt(value) * 10n ** 18n;
}

function sqrt(value) {
  if (value < 2n) return value;
  let x0 = 1n << BigInt(Math.ceil(value.toString(2).length / 2));
  let x1 = (x0 + value / x0) >> 1n;
  while (x1 < x0) {
    x0 = x1;
    x1 = (x0 + value / x0) >> 1n;
  }
  return x0;
}

function sqrtPriceX96ForRatio(amountA, amountB) {
  return (sqrt(amountB) * (1n << 96n)) / sqrt(amountA);
}

async function readManifest() {
  return JSON.parse(fs.readFileSync(MANIFEST_PATH, "utf8"));
}

function poolKey(currency0, currency1, fee, tickSpacing, hooks) {
  return [currency0, currency1, fee, tickSpacing, hooks];
}

async function main() {
  if (process.env.ROBINHOOD_TESTNET_ALLOW_V4_AMM_LIVE_TEST !== "true") {
    throw new Error("ROBINHOOD_TESTNET_ALLOW_V4_AMM_LIVE_TEST must be true");
  }

  const [owner] = await ethers.getSigners();
  const manifest = await readManifest();
  const tz = await ethers.getContractAt("TestERC20", manifest.tz);
  const ta = await ethers.getContractAt("TestERC20", manifest.ta);
  const tb = await ethers.getContractAt("TestERC20", manifest.tb);
  const poolManager = await ethers.getContractAt(POOL_MANAGER_ABI, POOL_MANAGER_ADDRESS);
  const positionManager = await ethers.getContractAt(POSITION_MANAGER_ABI, POSITION_MANAGER_ADDRESS);
  const permit2 = await ethers.getContractAt(PERMIT2_ABI, PERMIT2_ADDRESS);

  const tokens = [tz, ta, tb];
  const maxUint = ethers.MaxUint256;
  const maxUint160 = (1n << 160n) - 1n;
  const maxUint48 = (1n << 48n) - 1n;
  for (const token of tokens) {
    await token.approve(PERMIT2_ADDRESS, maxUint);
    await token.approve(POSITION_MANAGER_ADDRESS, maxUint);
    await permit2.approve(await token.getAddress(), POSITION_MANAGER_ADDRESS, maxUint160, maxUint48);
  }

  const hooks = ethers.ZeroAddress;

  const pairs = [
    {
      name: "tz-ta",
      tokenA: tz,
      tokenB: ta,
      reserveA: toToken(1_000_000),
      reserveB: toToken(100_000),
      fallbackTick: -23028,
    },
    {
      name: "ta-tb",
      tokenA: ta,
      tokenB: tb,
      reserveA: toToken(100_000),
      reserveB: toToken(1_000_000),
      fallbackTick: -23028,
    },
    {
      name: "tb-tz",
      tokenA: tb,
      tokenB: tz,
      reserveA: toToken(1_000_000),
      reserveB: toToken(100_000),
      fallbackTick: 23027,
    },
  ];

  const fee = 3000;
  const tickSpacing = 60;
  const [fullLower, fullUpper] = v4Liquidity.fullRangeTicks(tickSpacing);
  const rangeForTick = (tick) => {
    const aligned = Math.floor(Number(tick) / tickSpacing) * tickSpacing;
    return [aligned - tickSpacing, aligned + tickSpacing];
  };
  const actions = [];
  const actionParams = [];

  for (const pair of pairs) {
    const addressA = await pair.tokenA.getAddress();
    const addressB = await pair.tokenB.getAddress();
    let currency0;
    let currency1;
    let sqrtPriceX96;
    if (BigInt(addressA) < BigInt(addressB)) {
      currency0 = addressA;
      currency1 = addressB;
      sqrtPriceX96 = sqrtPriceX96ForRatio(pair.reserveA, pair.reserveB);
    } else {
      currency0 = addressB;
      currency1 = addressA;
      sqrtPriceX96 = sqrtPriceX96ForRatio(pair.reserveB, pair.reserveA);
    }

    const key = poolKey(currency0, currency1, fee, tickSpacing, hooks);
    const amount0 = currency0 === addressA ? pair.reserveA : pair.reserveB;
    const amount1 = currency0 === addressA ? pair.reserveB : pair.reserveA;
    const liquidity = v4Liquidity.fullRangeLiquidityForAmounts(sqrtPriceX96, amount0, amount1);
    try {
      const tick = await poolManager.initialize.staticCall(key, sqrtPriceX96);
      const tx = await poolManager.initialize(key, sqrtPriceX96);
      const receipt = await tx.wait();
      console.log(`${pair.name} initialized: tick=${tick} tx=${receipt.hash} liquidity≈${ethers.formatUnits(liquidity, 0)}`);
      const lower = fullLower;
      const upper = fullUpper;
      actions.push(0x02);
      actionParams.push(mintParams(key, lower, upper, liquidity, owner.address));
      actions.push(0x0d);
      actionParams.push(ethers.AbiCoder.defaultAbiCoder().encode(["address", "address"], [currency0, currency1]));
    } catch (error) {
      const message = String((error && error.message) || error);
      console.warn(`${pair.name} initialize skipped (${message.slice(0, 80)}), assuming initialized and adding liquidity`);
      console.log(`${pair.name} adding full-range liquidity≈${ethers.formatUnits(liquidity, 0)}`);
      const lower = fullLower;
      const upper = fullUpper;
      actions.push(0x02);
      actionParams.push(mintParams(key, lower, upper, liquidity, owner.address));
      actions.push(0x0d);
      actionParams.push(ethers.AbiCoder.defaultAbiCoder().encode(["address", "address"], [currency0, currency1]));
    }
  }

  if (actions.length === 0) {
    throw new Error("no official V4 pools initialized");
  }

  const unlockData = ethers.AbiCoder.defaultAbiCoder().encode(
    ["bytes", "bytes[]"],
    [ethers.hexlify(new Uint8Array(actions)), actionParams]
  );

  const deadline = (await ethers.provider.getBlock("latest")).timestamp + 600;
  try {
    await positionManager.modifyLiquidities.estimateGas(unlockData, deadline);
  } catch (error) {
    console.warn("modifyLiquidities estimate failed: " + String((error && error.message) || error).slice(0, 500));
    throw error;
  }
  const tx = await positionManager.modifyLiquidities(unlockData, deadline);
  const receipt = await tx.wait();
  console.log("official V4 liquidity added:");
  console.log(`  txHash=${receipt.hash}`);
  console.log(`  gasUsed=${receipt.gasUsed.toString()}`);

  manifest.officialV4 = {
    ...(manifest.officialV4 || {}),
    poolManager: POOL_MANAGER_ADDRESS,
    positionManager: POSITION_MANAGER_ADDRESS,
    permit2: PERMIT2_ADDRESS,
    fee,
    tickSpacing,
    hooks,
    txHash: receipt.hash,
    runAt: new Date().toISOString(),
  };
  fs.writeFileSync(MANIFEST_PATH, JSON.stringify(manifest, null, 2) + "\n", "utf8");
  console.log("result: OK. test tokens initialized and funded official Robinhood V4 pools.");
}

function mintParams(key, tickLower, tickUpper, liquidity, owner) {
  const abiCoder = ethers.AbiCoder.defaultAbiCoder();
  return abiCoder.encode(
    ["tuple(address,address,uint24,int24,address)", "int24", "int24", "uint256", "uint128", "uint128", "address", "bytes"],
    [key, tickLower, tickUpper, liquidity, (1n << 128n) - 1n, (1n << 128n) - 1n, owner, "0x"]
  );
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
