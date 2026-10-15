const path = require("path");
const fs = require("fs");
const { ethers, artifacts } = require("../lib/hardhat");
const v4Liquidity = require("../lib/v4-liquidity-common");
const poolKeyHelper = require("./v4-official-pool-keys");

const MANIFEST_PATH = process.env.FLASHLOAN_MANIFEST_PATH
  ? path.resolve(process.env.FLASHLOAN_MANIFEST_PATH)
  : path.resolve(__dirname, "../../config/testnet-v4-amm/manifest.json");
const STOCK_TOKEN_REGISTRY_PATH = process.env.FLASHLOAN_TESTNET_STOCK_TOKEN_REGISTRY
  ? path.resolve(process.env.FLASHLOAN_TESTNET_STOCK_TOKEN_REGISTRY)
  : path.resolve(
      __dirname,
      "../../../flashloan/src/chains/robinhood-testnet/robinhood_stock_tokens.json"
    );
const V4_POOL_LIQUIDITY_EVIDENCE_PATH = process.env.FLASHLOAN_TESTNET_V4_POOL_LIQUIDITY_EVIDENCE
  ? path.resolve(process.env.FLASHLOAN_TESTNET_V4_POOL_LIQUIDITY_EVIDENCE)
  : path.resolve(
      __dirname,
      "../../../flashloan/src/runtime/evidence/robinhood-testnet/v4_pool_liquidity/latest.json"
    );

const POSITION_MANAGER_ADDRESS = ethers.getAddress(
  process.env.FLASHLOAN_POSITION_MANAGER_ADDRESS || "0x58daec3116aae6d93017baaea7749052e8a04fa7"
);
const PERMIT2_ADDRESS = ethers.getAddress(
  process.env.FLASHLOAN_PERMIT2_ADDRESS || "0x000000000022d473030f116ddee9f6b43ac78ba3"
);

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
  "function decimals() view returns (uint8)",
  "function symbol() view returns (string)",
];

const ZERO_ADDRESS = "0x0000000000000000000000000000000000000000";
const STOCK_SYMBOLS = ["AMD", "AMZN", "NFLX", "PLTR", "TSLA"];
const MAX_STOCK_POOL_FIXED_FEE = 3000;

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

function readJson(pathname) {
  return JSON.parse(fs.readFileSync(pathname, "utf8"));
}

function normalizeAddress(value) {
  return String(value || "").trim().toLowerCase();
}

function stockTokenAddress(symbol) {
  const payload = readJson(STOCK_TOKEN_REGISTRY_PATH);
  const tokens = payload.tokens || {};
  for (const item of Object.values(tokens)) {
    if (!item || typeof item !== "object") continue;
    if (String(item.symbol || "").toUpperCase() === symbol.toUpperCase()) {
      return String(item.address || "").trim();
    }
  }
  throw new Error(`stock symbol ${symbol} not found in ${STOCK_TOKEN_REGISTRY_PATH}`);
}

function loadStockEthCandidatePools(stockAddress) {
  const evidence = readJson(V4_POOL_LIQUIDITY_EVIDENCE_PATH);
  const pools = evidence.pools || [];
  const rows = [];
  for (const raw of pools) {
    if (!raw || typeof raw !== "object") continue;
    const pk = raw.poolKey && typeof raw.poolKey === "object" ? raw.poolKey : raw;
    const currency0 = String(pk.currency0 || raw.token0 || "").trim();
    const currency1 = String(pk.currency1 || raw.token1 || "").trim();
    const metadata0 = pk.currency0Metadata && typeof pk.currency0Metadata === "object"
      ? pk.currency0Metadata
      : {};
    const metadata1 = pk.currency1Metadata && typeof pk.currency1Metadata === "object"
      ? pk.currency1Metadata
      : {};
    const symbol0 = String(metadata0.symbol || pk.token0Symbol || "").trim().toUpperCase();
    const symbol1 = String(metadata1.symbol || pk.token1Symbol || "").trim().toUpperCase();
    const hasNative = normalizeAddress(currency0) === normalizeAddress(ZERO_ADDRESS);
    const hasStock =
      normalizeAddress(currency0) === normalizeAddress(stockAddress) ||
      normalizeAddress(currency1) === normalizeAddress(stockAddress);
    if (!hasNative || !hasStock) continue;
    if (symbol0 !== "ETH" && symbol1 !== "ETH") continue;
    const feeRaw = pk.fee !== undefined ? pk.fee : raw.fee;
    const tickSpacingRaw =
      pk.tickSpacing !== undefined ? pk.tickSpacing : raw.tickSpacing;
    const hooks = String(pk.hooks || raw.hooks || ZERO_ADDRESS).trim();
    let fee;
    try {
      fee = Number(feeRaw);
    } catch (error) {
      continue;
    }
    if (
      !Number.isInteger(fee) ||
      fee <= 0 ||
      fee > MAX_STOCK_POOL_FIXED_FEE ||
      fee & 8388608
    ) {
      continue;
    }
    if (normalizeAddress(hooks) !== normalizeAddress(ZERO_ADDRESS)) continue;
    let tickSpacing;
    try {
      tickSpacing = Number(tickSpacingRaw);
    } catch (error) {
      continue;
    }
    if (!Number.isInteger(tickSpacing) || tickSpacing <= 0) continue;
    const poolIdValue = String(raw.poolId || raw.pool || raw.pool_id || "").trim();
    const sqrtPriceX96 = String(
      raw.sqrtPriceX96 || pk.sqrtPriceX96 || ""
    ).trim();
    rows.push({
      currency0,
      currency1,
      fee,
      tickSpacing,
      hooks,
      poolId: poolIdValue,
      sqrtPriceX96,
    });
  }
  if (!rows.length) {
    throw new Error(
      `no ETH-stock candidate pools found for ${stockAddress} in ${V4_POOL_LIQUIDITY_EVIDENCE_PATH}`
    );
  }
  rows.sort(
    (left, right) =>
      left.fee - right.fee ||
      left.tickSpacing - right.tickSpacing ||
      String(left.poolId).localeCompare(String(right.poolId))
  );
  return rows;
}

function sqrtPriceOrDefault(value) {
  if (!value) return v4Liquidity.Q96;
  try {
    const parsed = BigInt(value);
    return parsed > 0n ? parsed : v4Liquidity.Q96;
  } catch (error) {
    return v4Liquidity.Q96;
  }
}

function stockEthAmounts(pool, amountEth, sqrtPriceX96) {
  if (normalizeAddress(pool.currency0) === normalizeAddress(ZERO_ADDRESS)) {
    return {
      amount0: amountEth,
      amount1: (amountEth * sqrtPriceX96) / v4Liquidity.Q96,
    };
  }
  return {
    amount0: (amountEth * v4Liquidity.Q96) / sqrtPriceX96,
    amount1: amountEth,
  };
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
  const stockSymbol = String(
    readArg("stock", process.env.ROBINHOOD_TESTNET_STOCK_LIQUIDITY_SYMBOL || "")
  )
    .trim()
    .toUpperCase();
  if (stockSymbol && !STOCK_SYMBOLS.includes(stockSymbol)) {
    throw new Error(`stock symbol must be one of ${STOCK_SYMBOLS.join(", ")}`);
  }

  const manifest = readManifest();
  if (!manifest.officialV4 || !manifest.officialV4.poolManager) {
    throw new Error("officialV4.poolManager missing; run testnet-v4-official-pool-create-live first");
  }

  const [owner] = await ethers.getSigners();
  const positionManager = await ethers.getContractAt(POSITION_MANAGER_ABI, POSITION_MANAGER_ADDRESS, owner);
  const permit2 = await ethers.getContractAt(PERMIT2_ABI, PERMIT2_ADDRESS, owner);
  const actions = [];
  const actionParams = [];
  const ranges = [];
  const requiredByToken = new Map();

  if (stockSymbol) {
    const stockAddress = stockTokenAddress(stockSymbol);
    const stock = await ethers.getContractAt(ERC20_ABI, ethers.getAddress(stockAddress), owner);
    const stockPools = loadStockEthCandidatePools(stockAddress);
    console.log(`stock pool liquidity replenisher for ${stockSymbol}`);
    console.log(`  stockAddress=${stockAddress}`);
    console.log(`  requestedEthPerPool=${ethers.formatUnits(anchorTokenAmount, 18)}`);
    for (const pool of stockPools) {
      const key = poolKey(pool.currency0, pool.currency1, pool.fee, pool.tickSpacing, pool.hooks);
      const sqrtPriceX96 = sqrtPriceOrDefault(pool.sqrtPriceX96);
      const { amount0, amount1 } = stockEthAmounts(pool, anchorTokenAmount, sqrtPriceX96);
      const [fullLower, fullUpper] = v4Liquidity.fullRangeTicks(pool.tickSpacing);
      const liquidity = v4Liquidity.fullRangeLiquidityForAmounts(
        sqrtPriceX96,
        amount0,
        amount1
      );
      ranges.push({
        name: `${stockSymbol}-eth-fee${pool.fee}`,
        key,
        lower: fullLower,
        upper: fullUpper,
        targetA: amount0,
        targetB: amount1,
        liquidity,
        sqrtPriceX96,
        poolId: pool.poolId || poolKeyHelper.poolId(key),
      });
      actions.push(0x02);
      actionParams.push(mintParams(key, fullLower, fullUpper, liquidity, owner.address));
      actions.push(0x0d);
      actionParams.push(
        ethers.AbiCoder.defaultAbiCoder().encode(
          ["address", "address"],
          [key.currency0, key.currency1]
        )
      );
      requiredByToken.set(
        normalizeAddress(key.currency0),
        (requiredByToken.get(normalizeAddress(key.currency0)) || 0n) + amount0
      );
      requiredByToken.set(
        normalizeAddress(key.currency1),
        (requiredByToken.get(normalizeAddress(key.currency1)) || 0n) + amount1
      );
    }
    console.log("would add stock liquidity to ETH pools:");
    for (const r of ranges) {
      console.log(
        `  ${r.name} add=${ethers.formatUnits(r.targetA, 18)}+${ethers.formatUnits(r.targetB, 18)} ` +
          `liquidity=${ethers.formatUnits(r.liquidity, 0)} range=[${r.lower},${r.upper}] ` +
          `key=[${r.key.currency0},${r.key.currency1},${r.key.fee},${r.key.tickSpacing},${r.key.hooks}] ` +
          `poolId=${r.poolId}`
      );
    }
    const requiredStock = requiredByToken.get(stockAddress.toLowerCase()) || 0n;
    const requiredEth = requiredByToken.get(normalizeAddress(ZERO_ADDRESS)) || 0n;
    const ethBalance = await ethers.provider.getBalance(owner.address);
    if (ethBalance < requiredEth) {
      throw new Error(
        `ETH owner balance ${ethers.formatUnits(
          ethBalance,
          18
        )} is below required ${ethers.formatUnits(requiredEth, 18)}`
      );
    }
    const balance = await stock.balanceOf(owner.address);
    if (balance < requiredStock) {
      throw new Error(
        `${stockSymbol} owner balance ${ethers.formatUnits(balance, 18)} is below required ${ethers.formatUnits(requiredStock, 18)}`
      );
    }
    if (!allowBroadcast) {
      console.log("broadcast disabled; stock pool liquidity action prepared for review (not broadcast)");
      return;
    }
    const maxUint = ethers.MaxUint256;
    const maxUint160 = (1n << 160n) - 1n;
    const maxUint48 = (1n << 48n) - 1n;
    await stock.approve(PERMIT2_ADDRESS, maxUint);
    await stock.approve(POSITION_MANAGER_ADDRESS, maxUint);
    await permit2.approve(stockAddress, POSITION_MANAGER_ADDRESS, maxUint160, maxUint48);
    const unlockData = ethers.AbiCoder.defaultAbiCoder().encode(
      ["bytes", "bytes[]"],
      [ethers.hexlify(new Uint8Array(actions)), actionParams]
    );
    const deadline = (await ethers.provider.getBlock("latest")).timestamp + 600;
    const tx = await positionManager.modifyLiquidities(unlockData, deadline, {
      value: requiredByToken.get(normalizeAddress(ZERO_ADDRESS)) || 0n,
    });
    const receipt = await tx.wait();
    console.log(`stock pool replenish liquidity tx=${receipt.hash} gasUsed=${receipt.gasUsed}`);
    manifest.officialV4.lastStockReplenish = {
      stockSymbol,
      stockAddress,
      txHash: receipt.hash,
      gasUsed: receipt.gasUsed.toString(),
      runAt: new Date().toISOString(),
      ethPerPool: anchorTokenAmount.toString(),
      pools: ranges.map((r) => ({
        name: r.name,
        poolId: r.poolId,
        liquidity: r.liquidity.toString(),
        amount0: r.targetA.toString(),
        amount1: r.targetB.toString(),
      })),
    };
    writeManifest(manifest);
    console.log("result: OK. stock pool liquidity injected.");
    return;
  }

  const tz = await ethers.getContractAt("TestERC20", manifest.tz);
  const ta = await ethers.getContractAt("TestERC20", manifest.ta);
  const tb = await ethers.getContractAt("TestERC20", manifest.tb);
  const tokens = [tz, ta, tb];

  const fee = Number(manifest.officialV4.fee);
  const tickSpacing = Number(manifest.officialV4.tickSpacing);
  const hooks = manifest.officialV4.hooks || ethers.ZeroAddress;
  const [fullLower, fullUpper] = v4Liquidity.fullRangeTicks(tickSpacing);
  const tzAddress = await tz.getAddress();
  const taAddress = await ta.getAddress();
  const tbAddress = await tb.getAddress();
  const explicitPoolKeysJson = readArg(
    "pool-keys-json",
    process.env.ROBINHOOD_TESTNET_OFFICIAL_ARB_POOL_KEYS_JSON || ""
  );
  const keysByPair = poolKeyHelper.resolveRoutePoolKeys(
    [tzAddress, taAddress, tbAddress, tzAddress],
    explicitPoolKeysJson,
    { fee, tickSpacing, hooks }
  );

  console.log("official V4 manual liquidity replenisher");
  console.log(`  poolManager=${manifest.officialV4.poolManager}`);
  console.log(`  hook=${hooks}`);
  console.log(`  requestedAnchorTokenPerPair=${ethers.formatUnits(anchorTokenAmount, 18)}`);

  for (const [index, pair] of PAIRS.entries()) {
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
    const key = keysByPair[index];
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
