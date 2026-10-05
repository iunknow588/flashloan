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

const MANIFEST_PATH = process.env.FLASHLOAN_MAINNET_MANIFEST_PATH
  ? path.resolve(process.env.FLASHLOAN_MAINNET_MANIFEST_PATH)
  : path.resolve(__dirname, "../../config/mainnet-v4-amm/manifest.json");

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

function readManifest() {
  const payload = JSON.parse(fs.readFileSync(MANIFEST_PATH, "utf8"));
  if (!payload.tz || !payload.ta || !payload.tb) {
    throw new Error("mainnet manifest missing tz/ta/tb; run mainnet-v4-amm-create-tokens first");
  }
  return payload;
}

function writeManifest(manifest) {
  fs.mkdirSync(path.dirname(MANIFEST_PATH), { recursive: true });
  fs.writeFileSync(MANIFEST_PATH, JSON.stringify(manifest, null, 2) + "\n", "utf8");
}

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

function poolKey(currency0, currency1, fee, tickSpacing, hooks) {
  return [currency0, currency1, fee, tickSpacing, hooks];
}

function poolIdFromKey(key) {
  return ethers.keccak256(
    ethers.AbiCoder.defaultAbiCoder().encode(
      ["tuple(address currency0,address currency1,uint24 fee,int24 tickSpacing,address hooks)"],
      [key]
    )
  );
}

function mintParams(key, tickLower, tickUpper, liquidity, owner) {
  const abiCoder = ethers.AbiCoder.defaultAbiCoder();
  return abiCoder.encode(
    ["tuple(address,address,uint24,int24,address)", "int24", "int24", "uint256", "uint128", "uint128", "address", "bytes"],
    [key, tickLower, tickUpper, liquidity, (1n << 128n) - 1n, (1n << 128n) - 1n, owner, "0x"]
  );
}

async function main() {
  const allowBroadcast = readBoolArg(
    "broadcast",
    process.env.ROBINHOOD_MAINNET_ALLOW_V4_POOL_CREATE === "true"
  );
  const [owner] = await ethers.getSigners();
  const manifest = readManifest();
  const tz = await ethers.getContractAt("TestERC20", manifest.tz);
  const ta = await ethers.getContractAt("TestERC20", manifest.ta);
  const tb = await ethers.getContractAt("TestERC20", manifest.tb);
  const poolManager = await ethers.getContractAt(POOL_MANAGER_ABI, POOL_MANAGER_ADDRESS);
  const positionManager = await ethers.getContractAt(POSITION_MANAGER_ABI, POSITION_MANAGER_ADDRESS);

  const pairs = [
    {
      name: "tz-ta",
      tokenA: tz,
      tokenB: ta,
      reserveA: toToken(1_000_000),
      reserveB: toToken(100_000),
    },
    {
      name: "ta-tb",
      tokenA: ta,
      tokenB: tb,
      reserveA: toToken(100_000),
      reserveB: toToken(1_000_000),
    },
    {
      name: "tb-tz",
      tokenA: tb,
      tokenB: tz,
      reserveA: toToken(1_000_000),
      reserveB: toToken(100_000),
    },
  ];

  const fee = 3000;
  const tickSpacing = 60;
  const hooks = ethers.ZeroAddress;
  const [fullLower, fullUpper] = v4Liquidity.fullRangeTicks(tickSpacing);

  const plans = [];
  for (const pair of pairs) {
    const addressA = await pair.tokenA.getAddress();
    const addressB = await pair.tokenB.getAddress();
    let currency0;
    let currency1;
    let sqrtPriceX96;
    let amount0;
    let amount1;
    if (BigInt(addressA) < BigInt(addressB)) {
      currency0 = addressA;
      currency1 = addressB;
      sqrtPriceX96 = sqrtPriceX96ForRatio(pair.reserveA, pair.reserveB);
      amount0 = pair.reserveA;
      amount1 = pair.reserveB;
    } else {
      currency0 = addressB;
      currency1 = addressA;
      sqrtPriceX96 = sqrtPriceX96ForRatio(pair.reserveB, pair.reserveA);
      amount0 = pair.reserveB;
      amount1 = pair.reserveA;
    }
    const liquidity = v4Liquidity.fullRangeLiquidityForAmounts(sqrtPriceX96, amount0, amount1);
    plans.push({
      name: pair.name,
      key: poolKey(currency0, currency1, fee, tickSpacing, hooks),
      sqrtPriceX96,
      liquidity,
      amount0,
      amount1,
      lower: fullLower,
      upper: fullUpper,
    });
    console.log(`${pair.name} would initialize key=[${currency0},${currency1},${fee},${tickSpacing},${hooks}]`);
    console.log(`  poolId=${poolIdFromKey(plans[plans.length - 1].key)}`);
    console.log(`  sqrtPriceX96=${sqrtPriceX96} liquidity≈${ethers.formatUnits(liquidity, 0)}`);
    console.log(`  reserve0=${ethers.formatUnits(amount0, 18)} reserve1=${ethers.formatUnits(amount1, 18)}`);
  }

  if (!allowBroadcast) {
    console.log("broadcast disabled; no token approvals or on-chain transactions were sent");
    console.log("run with --broadcast or set ROBINHOOD_MAINNET_ALLOW_V4_POOL_CREATE=true to execute");
    return;
  }

  const permit2 = await ethers.getContractAt(PERMIT2_ABI, PERMIT2_ADDRESS);
  const tokens = [tz, ta, tb];
  const maxUint = ethers.MaxUint256;
  const maxUint160 = (1n << 160n) - 1n;
  const maxUint48 = (1n << 48n) - 1n;
  for (const token of tokens) {
    await (await token.approve(PERMIT2_ADDRESS, maxUint)).wait();
    await (await token.approve(POSITION_MANAGER_ADDRESS, maxUint)).wait();
    await permit2.approve(await token.getAddress(), POSITION_MANAGER_ADDRESS, maxUint160, maxUint48).then((tx) => tx.wait());
  }

  const actions = [];
  const actionParams = [];
  for (const plan of plans) {
    try {
      await poolManager.initialize.staticCall(plan.key, plan.sqrtPriceX96);
      const tx = await poolManager.initialize(plan.key, plan.sqrtPriceX96);
      const receipt = await tx.wait();
      console.log(`${plan.name} initialized tx=${receipt.hash}`);
    } catch (error) {
      const message = String((error && error.message) || error);
      console.log(`${plan.name} initialize skipped (${message.slice(0, 100)}), assuming already initialized`);
    }
    actions.push(0x02);
    actionParams.push(mintParams(plan.key, plan.lower, plan.upper, plan.liquidity, owner.address));
    actions.push(0x0d);
    actionParams.push(ethers.AbiCoder.defaultAbiCoder().encode(["address", "address"], [plan.key[0], plan.key[1]]));
  }

  const unlockData = ethers.AbiCoder.defaultAbiCoder().encode(
    ["bytes", "bytes[]"],
    [ethers.hexlify(new Uint8Array(actions)), actionParams]
  );
  const deadline = (await ethers.provider.getBlock("latest")).timestamp + 600;
  const tx = await positionManager.modifyLiquidities(unlockData, deadline);
  const receipt = await tx.wait();
  console.log("official V4 liquidity added on mainnet:");
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
  writeManifest(manifest);
  console.log("result: OK. Ta/Tb/Tz tokens initialized and funded official Robinhood V4 pools.");
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
