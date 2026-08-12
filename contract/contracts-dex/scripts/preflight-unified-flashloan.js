const fs = require("fs");
const path = require("path");
const hre = require("hardhat");

const ZERO = "0x0000000000000000000000000000000000000000";
const ADAPTER_UNISWAP_V3 = 1;
const DEFAULT_FUJI_V2_ROUTER = "0xd7f655E3376cE2D7A2b08fF01Eb3B1023191A901";

function envAddress(...names) {
  for (const name of names) {
    const value = String(process.env[name] || "").trim();
    if (value && value !== "0x..." && value !== "0xyour_private_key") return value;
  }
  return "";
}

function requiredAddress(...names) {
  const value = envAddress(...names);
  if (!value || !hre.ethers.isAddress(value)) {
    throw new Error(`Missing or invalid address env: ${names.join(" or ")}`);
  }
  return value;
}

function envBigInt(name, fallback) {
  const value = String(process.env[name] || "").trim();
  return value ? BigInt(value) : BigInt(fallback);
}

function envBool(name, fallback) {
  const value = String(process.env[name] || "").trim().toLowerCase();
  if (!value) return fallback;
  return !["0", "false", "no", "off"].includes(value);
}

function cachePath() {
  const configured =
    process.env.TRIANGULAR_RUNTIME_POOL_CACHE_FILE ||
    process.env.PINAX_POOL_DISCOVERY_CACHE_FILE ||
    "runtime/cache/fuji_v3_pools.json";
  return path.isAbsolute(configured)
    ? configured
    : path.resolve(__dirname, "../../flashloan/src_bot", configured);
}

function asAddress(value) {
  return value && hre.ethers.isAddress(value) ? value : ZERO;
}

async function hasCode(address) {
  return (await hre.ethers.provider.getCode(address)) !== "0x";
}

function reserveConfigFlags(configuration) {
  const value = BigInt(configuration);
  return {
    active: Boolean((value >> 56n) & 1n),
    frozen: Boolean((value >> 57n) & 1n),
    borrowingEnabled: Boolean((value >> 58n) & 1n),
    paused: Boolean((value >> 60n) & 1n),
    flashloanEnabled: Boolean((value >> 63n) & 1n),
  };
}

async function readAaveState(poolAddress, usdcAddress, borrowAmount) {
  const pool = await hre.ethers.getContractAt(
    [
      "function FLASHLOAN_PREMIUM_TOTAL() view returns (uint128)",
      "function getReservesList() view returns (address[])",
      "function getReserveData(address) view returns (uint256 configuration,uint128 liquidityIndex,uint128 currentLiquidityRate,uint128 variableBorrowIndex,uint128 currentVariableBorrowRate,uint128 currentStableBorrowRate,uint40 lastUpdateTimestamp,uint16 id,address aTokenAddress,address stableDebtTokenAddress,address variableDebtTokenAddress,address interestRateStrategyAddress,uint128 accruedToTreasury,uint128 unbacked,uint128 isolationModeTotalDebt)",
    ],
    poolAddress,
  );
  const erc20 = ["function symbol() view returns (string)", "function decimals() view returns (uint8)", "function balanceOf(address) view returns (uint256)"];
  const reserves = await pool.getReservesList();
  const rows = [];
  for (const asset of reserves) {
    const token = await hre.ethers.getContractAt(erc20, asset);
    const data = await pool.getReserveData(asset);
    const flags = reserveConfigFlags(data.configuration);
    const [symbol, decimals, liquidity] = await Promise.all([
      token.symbol().catch(() => ""),
      token.decimals().catch(() => 0),
      token.balanceOf(data.aTokenAddress).catch(() => 0n),
    ]);
    rows.push({
      asset,
      symbol,
      decimals: Number(decimals),
      aToken: data.aTokenAddress,
      availableLiquidity: liquidity.toString(),
      borrowAmount: borrowAmount.toString(),
      enoughLiquidity: liquidity >= borrowAmount,
      ...flags,
      usableForFlashloan: flags.active && !flags.paused && flags.flashloanEnabled && liquidity >= borrowAmount,
    });
  }
  const usdc = rows.find((row) => row.asset.toLowerCase() === usdcAddress.toLowerCase());
  return {
    premiumBps: (await pool.FLASHLOAN_PREMIUM_TOTAL()).toString(),
    reserveCount: rows.length,
    reserves: rows,
    usdc,
  };
}

async function readV2Pairs(routerAddress, usdcAddress, reserveRows) {
  const router = await hre.ethers.getContractAt(
    [
      "function factory() view returns (address)",
      "function WAVAX() view returns (address)",
    ],
    routerAddress,
  );
  const factoryAddress = await router.factory();
  const factory = await hre.ethers.getContractAt(
    ["function getPair(address,address) view returns (address)"],
    factoryAddress,
  );
  const pairs = [];
  for (const reserve of reserveRows) {
    if (reserve.asset.toLowerCase() === usdcAddress.toLowerCase()) continue;
    const pair = await factory.getPair(usdcAddress, reserve.asset).catch(() => ZERO);
    pairs.push({
      token: reserve.asset,
      symbol: reserve.symbol,
      pair,
      exists: pair !== ZERO,
    });
  }
  return { router: routerAddress, factory: factoryAddress, pairs };
}

async function readV3Pools(factoryAddress, usdcAddress, reserveRows) {
  const factory = await hre.ethers.getContractAt(
    ["function getPool(address,address,uint24) view returns (address)"],
    factoryAddress,
  );
  const configuredFees = [
    process.env.TRIANGULAR_USDC_TO_TOKEN_X_FEE || "500",
    process.env.TRIANGULAR_TOKEN_Y_TO_USDC_FEE || "3000",
    "10000",
  ].map((value) => Number(value));
  const pools = [];
  for (const reserve of reserveRows) {
    if (reserve.asset.toLowerCase() === usdcAddress.toLowerCase()) continue;
    for (const fee of [...new Set(configuredFees)]) {
      const pool = await factory.getPool(usdcAddress, reserve.asset, fee).catch(() => ZERO);
      pools.push({
        token: reserve.asset,
        symbol: reserve.symbol,
        fee,
        pool,
        exists: pool !== ZERO && (await hasCode(pool)),
      });
    }
  }
  return pools;
}

function readPoolCache() {
  const file = cachePath();
  try {
    const payload = JSON.parse(fs.readFileSync(file, "utf8"));
    const pools = Array.isArray(payload.pools) ? payload.pools : [];
    return { file, exists: true, poolCount: pools.length, payload };
  } catch {
    return { file, exists: false, poolCount: 0, payload: null };
  }
}

async function validateDeploymentPrerequisites() {
  const network = await hre.ethers.provider.getNetwork();
  if (network.chainId !== 43113n) {
    throw new Error(`preflight requires Fuji chainId 43113, got ${network.chainId}`);
  }

  const [deployer] = await hre.ethers.getSigners();
  const poolAddress = requiredAddress("UNIFIED_AAVE_POOL_ADDRESS", "TRIANGULAR_AAVE_POOL_ADDRESS");
  const usdcAddress = requiredAddress("UNIFIED_USDC_ADDRESS", "TRIANGULAR_USDC_ADDRESS");
  const factoryAddress = requiredAddress("UNIFIED_V3_FACTORY", "TRIANGULAR_V3_FACTORY");
  const routerAddress = requiredAddress("UNIFIED_V3_ROUTER", "TRIANGULAR_V3_ROUTER");
  const quoterAddress = requiredAddress("UNIFIED_V3_QUOTER", "TRIANGULAR_V3_QUOTER");

  for (const [label, address] of [
    ["aavePool", poolAddress],
    ["usdc", usdcAddress],
    ["v3Factory", factoryAddress],
    ["v3Router", routerAddress],
    ["v3Quoter", quoterAddress],
  ]) {
    if (!(await hasCode(address))) throw new Error(`${label} has no contract code on Fuji: ${address}`);
  }

  const borrowAmount = envBigInt(
    "UNIFIED_BORROW_AMOUNT_UNITS",
    process.env.TRIANGULAR_BORROW_AMOUNT_UNITS || "100000000",
  );
  const aave = await readAaveState(poolAddress, usdcAddress, borrowAmount);
  if (!aave.usdc) throw new Error("configured USDC is not an Aave Fuji reserve");
  if (aave.usdc.decimals !== 6) throw new Error(`configured USDC decimals must be 6, got ${aave.usdc.decimals}`);
  if (!aave.usdc.usableForFlashloan) {
    throw new Error(`configured USDC is not flashloan-usable at amount ${borrowAmount}`);
  }

  const nonUsdcUsable = aave.reserves.filter(
    (row) => row.asset.toLowerCase() !== usdcAddress.toLowerCase() && row.usableForFlashloan,
  );
  if (nonUsdcUsable.length === 0) {
    throw new Error("Fuji Aave has no non-USDC reserve with active flashloan liquidity");
  }

  const v3Pools = await readV3Pools(factoryAddress, usdcAddress, nonUsdcUsable);
  const v3PoolCount = v3Pools.filter((row) => row.exists).length;
  const cache = readPoolCache();
  if (!cache.exists || cache.poolCount === 0) {
    throw new Error(`runtime V3 pool cache is missing or empty: ${cache.file}`);
  }
  if (v3PoolCount === 0) {
    throw new Error("configured Fuji V3 factory has no USDC pool for any flashloan-usable Aave reserve");
  }

  const v2RouterAddress = asAddress(process.env.FUJI_V2_ROUTER_ADDRESS || DEFAULT_FUJI_V2_ROUTER);
  const v2 = await readV2Pairs(v2RouterAddress, usdcAddress, aave.reserves);
  const deploymentRequest = await (await hre.ethers.getContractFactory("UnifiedFlashLoanMevExecutor")).getDeployTransaction(
    poolAddress,
    usdcAddress,
    deployer.address,
  );
  const estimatedDeploymentGas = await hre.ethers.provider.estimateGas({
    ...deploymentRequest,
    from: deployer.address,
  });
  const configuredGasPrice = envBigInt(
    "TRIANGULAR_DEPLOY_MIN_GAS_PRICE_WEI",
    (await hre.ethers.provider.getFeeData()).maxFeePerGas || 1n,
  );
  const safetyBps = envBigInt("TRIANGULAR_DEPLOY_GAS_SAFETY_BPS", "15000");
  const configGasUnits = envBigInt("TRIANGULAR_DEPLOY_CONFIG_GAS_UNITS", "800000");
  const estimatedBudget = ((estimatedDeploymentGas + configGasUnits) * configuredGasPrice * safetyBps) / 10000n;
  const balance = await hre.ethers.provider.getBalance(deployer.address);

  return {
    network: hre.network.name,
    chainId: Number(network.chainId),
    deployer: deployer.address,
    balanceWei: balance.toString(),
    estimatedDeploymentGas: estimatedDeploymentGas.toString(),
    gasPriceWei: configuredGasPrice.toString(),
    estimatedBudgetWei: estimatedBudget.toString(),
    aavePool: poolAddress,
    usdc: usdcAddress,
    aave,
    v3: {
      factory: factoryAddress,
      router: routerAddress,
      quoter: quoterAddress,
      pools: v3Pools,
      existingPoolCount: v3PoolCount,
      cache,
    },
    fujiV2: v2,
    profitConfig: {
      sweepEnabled: envBool("TRIANGULAR_PROFIT_SWEEP_ENABLED", true),
      reserveUsdc: envBigInt("TRIANGULAR_PROFIT_RESERVE_USDC", "0").toString(),
      sweepThreshold: envBigInt("TRIANGULAR_PROFIT_SWEEP_THRESHOLD_USDC", "0").toString(),
    },
    adapterConfigured: true,
    broadcast: false,
  };
}

async function main() {
  try {
    const report = await validateDeploymentPrerequisites();
    console.log(JSON.stringify(report, null, 2));
    if (BigInt(report.balanceWei) < BigInt(report.estimatedBudgetWei)) {
      throw new Error(`insufficient deployer balance: have ${report.balanceWei} wei, need ${report.estimatedBudgetWei} wei`);
    }
  } catch (error) {
    console.error(error.message || error);
    process.exitCode = 1;
  }
}

if (require.main === module) {
  main();
}

module.exports = { validateDeploymentPrerequisites, reserveConfigFlags };
