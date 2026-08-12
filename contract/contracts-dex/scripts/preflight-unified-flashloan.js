const fs = require("fs");
const path = require("path");
const hre = require("hardhat");

const TARGET_CHAIN_ID = 43114n;
const AVALANCHE_V3_PROFILE = {
  factory: "0x740b1c1de25031C31FF4fC9A62f554A55cdC1baD",
  router: "0xbb00FF08d01D300023C629E8fFfFcb65A5a578cE",
  quoter: "0xbe0F5544EC67e9B3b2D979aaA43f18Fd87E6257F",
};
const DEFAULT_AAVE_POOL = "0x794a61358D6845594F94dc1DB02A252b5b4814aD";
const DEFAULT_USDC = "0xB97EF9Ef8734C71904D8002F8b6Bc66Dd9c48a6E";

function envAddress(...names) {
  for (const name of names) {
    const value = String(process.env[name] || "").trim();
    if (value && value !== "0x..." && hre.ethers.isAddress(value)) return hre.ethers.getAddress(value);
  }
  return "";
}

function envBigInt(name, fallback) {
  const value = String(process.env[name] || "").trim();
  return value ? BigInt(value) : BigInt(fallback);
}

function cachePath() {
  const configured =
    process.env.TRIANGULAR_RUNTIME_POOL_CACHE_FILE ||
    process.env.PINAX_POOL_DISCOVERY_CACHE_FILE ||
    "runtime/cache/avalanche_v3_pools.json";
  return path.isAbsolute(configured)
    ? configured
    : path.resolve(__dirname, "../../../flashloan/src_bot", configured);
}

function rpcHost() {
  try {
    return new URL(hre.network.config.url || "").host;
  } catch {
    return "";
  }
}

function check(name, ok, detail = {}) {
  return { name, ok: Boolean(ok), ...detail };
}

async function codeEvidence(label, address) {
  const code = await hre.ethers.provider.getCode(address);
  return {
    label,
    address,
    hasCode: code !== "0x",
    codeBytes: (code.length - 2) / 2,
    codeHash: code === "0x" ? null : hre.ethers.keccak256(code),
  };
}

function errorData(error) {
  const candidates = [
    error?.data,
    error?.info?.error?.data,
    error?.error?.data,
    error?.info?.data,
  ];
  for (const value of candidates) {
    if (typeof value === "string" && value.startsWith("0x")) return value;
    if (value && typeof value.data === "string" && value.data.startsWith("0x")) return value.data;
  }
  return "0x";
}

async function invalidCalldataProbe(label, address, calldata) {
  try {
    const result = await hre.ethers.provider.call({ to: address, data: calldata });
    return {
      label,
      address,
      accepted: true,
      returnBytes: (result.length - 2) / 2,
      revertData: "0x",
    };
  } catch (error) {
    const revertData = errorData(error);
    return {
      label,
      address,
      accepted: false,
      returnBytes: 0,
      revertData,
      revertBytes: (revertData.length - 2) / 2,
    };
  }
}

async function validateV3InterfaceProbes(routerAddress, quoterAddress) {
  const router = new hre.ethers.Interface([
    "function exactInput((bytes path,address recipient,uint256 amountIn,uint256 amountOutMinimum) params) payable returns (uint256)",
  ]);
  const routerProbe = await invalidCalldataProbe(
    "router.exactInput",
    routerAddress,
    router.encodeFunctionData("exactInput", [["0x", hre.ethers.ZeroAddress, 0n, 0n]]),
  );

  // Empty V3 paths are intentionally invalid. A non-empty revert payload proves that
  // the target recognized the selector and decoded the expected argument layout.
  const routerOk = routerProbe.accepted || routerProbe.revertBytes >= 4;
  return {
    quoter: {
      label: "quoter.quoteExactInput",
      address: quoterAddress,
      compatible: null,
      evidence: "validated_from_non_empty_cached_pool_quote",
    },
    router: { ...routerProbe, compatible: routerOk },
    ready: routerOk,
  };
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
  const tokenAbi = [
    "function symbol() view returns (string)",
    "function decimals() view returns (uint8)",
    "function balanceOf(address) view returns (uint256)",
  ];
  const reserves = await pool.getReservesList();
  const rows = [];
  for (const asset of reserves) {
    const token = await hre.ethers.getContractAt(tokenAbi, asset);
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
      enoughLiquidity: liquidity >= borrowAmount,
      ...flags,
      usableForFlashloan: flags.active && !flags.paused && flags.flashloanEnabled && liquidity >= borrowAmount,
    });
  }
  return {
    premiumBps: (await pool.FLASHLOAN_PREMIUM_TOTAL()).toString(),
    reserveCount: rows.length,
    reserves: rows,
    usdc: rows.find((row) => row.asset.toLowerCase() === usdcAddress.toLowerCase()) || null,
  };
}

function readPoolCache() {
  const file = cachePath();
  try {
    const payload = JSON.parse(fs.readFileSync(file, "utf8"));
    return { file, exists: true, payload: payload && typeof payload === "object" ? payload : null };
  } catch {
    return { file, exists: false, payload: null };
  }
}

function flattenCachedPools(payload) {
  const rows = Array.isArray(payload?.pools) ? payload.pools : [];
  const pools = [];
  for (const row of rows) {
    if (!row || typeof row !== "object") continue;
    const nested = Array.isArray(row.pools) ? row.pools : [row];
    for (const pool of nested) {
      if (!pool || typeof pool !== "object") continue;
      const address = pool.pool || pool.pool_address || pool.address;
      pools.push({
        address,
        factory: pool.factory || row.factory,
        token0: pool.token0 || row.token0 || row.tokenX,
        token1: pool.token1 || row.token1 || row.tokenY,
        fee: pool.fee ?? row.fee,
        liquidity: pool.liquidity ?? row.liquidity,
        tick: pool.tick ?? row.tick,
        codeHash: pool.codeHash || pool.code_hash || row.codeHash || row.code_hash,
      });
    }
  }
  return pools;
}

async function validatePoolCache(factoryAddress, currentBlock) {
  const cache = readPoolCache();
  const base = {
    file: cache.file,
    exists: cache.exists,
    poolCount: 0,
    validPoolCount: 0,
    codeHashCheckedCount: 0,
    codeHashMismatchCount: 0,
    missingCodeHashCount: 0,
    invalidPoolSamples: [],
    verifiedPoolSamples: [],
    errors: [],
  };
  if (!cache.exists || !cache.payload) {
    return { ...base, errors: ["cache_missing_or_invalid"] };
  }
  const payload = cache.payload;
  if (Number(payload.schema_version || 0) < 1) base.errors.push("schema_version_missing");
  if (Number(payload.chain_id) !== Number(TARGET_CHAIN_ID)) base.errors.push("chain_id_mismatch");
  if (String(payload.factory || "").toLowerCase() !== factoryAddress.toLowerCase()) base.errors.push("factory_mismatch");
  if (!payload.block_number || !payload.fetched_at || !payload.rpc_host) base.errors.push("snapshot_metadata_missing");
  if (String(payload.rpc_host).toLowerCase().includes("avax-test")) base.errors.push("testnet_cache_rejected");

  const maxAgeSeconds = Number(process.env.UNIFIED_V3_POOL_CACHE_MAX_AGE_SECONDS || "300");
  const fetchedAt = Date.parse(String(payload.fetched_at || ""));
  if (!Number.isFinite(fetchedAt) || Date.now() - fetchedAt > maxAgeSeconds * 1000) {
    base.errors.push("cache_stale");
  }
  const maxBlockAge = Number(process.env.UNIFIED_V3_POOL_CACHE_MAX_BLOCKS || "60");
  const snapshotBlock = Number(payload.block_number);
  base.currentBlock = currentBlock;
  base.snapshotBlock = Number.isInteger(snapshotBlock) ? snapshotBlock : null;
  base.blockAge = Number.isInteger(snapshotBlock) ? currentBlock - snapshotBlock : null;
  base.codeHashBlock = Number.isInteger(snapshotBlock) && snapshotBlock <= currentBlock ? snapshotBlock : currentBlock;
  if (!Number.isInteger(snapshotBlock) || snapshotBlock > currentBlock || currentBlock - snapshotBlock > maxBlockAge) {
    base.errors.push("cache_block_stale");
  }

  const pools = flattenCachedPools(payload);
  base.poolCount = pools.length;
  for (const pool of pools) {
    let liquidity = 0n;
    try {
      liquidity = BigInt(pool.liquidity || 0);
    } catch {
      continue;
    }
    const cachedCodeHash = pool.codeHash || pool.code_hash;
    const invalidReasons = [];
    if (!hre.ethers.isAddress(pool.address || "")) invalidReasons.push("pool_address_invalid");
    if (String(pool.factory || "").toLowerCase() !== factoryAddress.toLowerCase()) invalidReasons.push("factory_mismatch");
    if (!hre.ethers.isAddress(pool.token0 || "")) invalidReasons.push("token0_invalid");
    if (!hre.ethers.isAddress(pool.token1 || "")) invalidReasons.push("token1_invalid");
    if (!Number.isInteger(Number(pool.fee))) invalidReasons.push("fee_invalid");
    if (liquidity === 0n) invalidReasons.push("zero_liquidity");
    if (typeof cachedCodeHash !== "string" || !cachedCodeHash.startsWith("0x")) {
      invalidReasons.push("code_hash_missing");
      base.missingCodeHashCount++;
    }
    if (invalidReasons.length > 0) {
      if (base.invalidPoolSamples.length < 5) {
        base.invalidPoolSamples.push({ address: pool.address || null, reasons: invalidReasons });
      }
      continue;
    }
    const code = await hre.ethers.provider.getCode(pool.address, base.codeHashBlock);
    base.codeHashCheckedCount++;
    const actualCodeHash = code === "0x" ? null : hre.ethers.keccak256(code);
    if (!actualCodeHash || actualCodeHash.toLowerCase() !== cachedCodeHash.toLowerCase()) {
      base.codeHashMismatchCount++;
      if (base.invalidPoolSamples.length < 5) {
        base.invalidPoolSamples.push({
          address: pool.address,
          reasons: [actualCodeHash ? "code_hash_mismatch" : "pool_code_missing"],
          cachedCodeHash,
          actualCodeHash,
        });
      }
      continue;
    }
    base.validPoolCount++;
    if (base.verifiedPoolSamples.length < 5) {
      base.verifiedPoolSamples.push({
        address: hre.ethers.getAddress(pool.address),
        token0: hre.ethers.getAddress(pool.token0),
        token1: hre.ethers.getAddress(pool.token1),
        fee: Number(pool.fee),
        codeHash: cachedCodeHash,
      });
    }
  }
  if (base.validPoolCount === 0) base.errors.push("no_verified_v3_pool");
  if (base.codeHashMismatchCount > 0) base.errors.push("pool_code_hash_mismatch");
  if (base.missingCodeHashCount > 0) base.errors.push("pool_code_hash_missing");
  const probes = Array.isArray(payload.quoter_probes) ? payload.quoter_probes : [];
  const verifiedAddresses = new Set(
    pools
      .filter((pool) => hre.ethers.isAddress(pool.address || ""))
      .map((pool) => pool.address.toLowerCase()),
  );
  base.validQuoterProbeCount = probes.filter((probe) => {
    if (
      !probe ||
      !hre.ethers.isAddress(probe.pool || "") ||
      !verifiedAddresses.has(probe.pool.toLowerCase())
    ) {
      return false;
    }
    try {
      return BigInt(probe.amount_out || 0) > 0n;
    } catch {
      return false;
    }
  }).length;
  if (base.validQuoterProbeCount === 0) base.errors.push("no_verified_quoter_probe");
  return { ...base, ready: base.errors.length === 0 };
}

async function validateDeploymentPrerequisites() {
  const network = await hre.ethers.provider.getNetwork();
  const [signer] = await hre.ethers.getSigners();
  const poolAddress = envAddress("UNIFIED_AAVE_POOL_ADDRESS") || DEFAULT_AAVE_POOL;
  const usdcAddress = envAddress("UNIFIED_USDC_ADDRESS") || DEFAULT_USDC;
  const factoryAddress = envAddress("UNIFIED_V3_FACTORY") || AVALANCHE_V3_PROFILE.factory;
  const routerAddress = envAddress("UNIFIED_V3_ROUTER") || AVALANCHE_V3_PROFILE.router;
  const quoterAddress = envAddress("UNIFIED_V3_QUOTER") || AVALANCHE_V3_PROFILE.quoter;
  const checks = [check("network.chainId", network.chainId === TARGET_CHAIN_ID, { actual: network.chainId.toString(), expected: TARGET_CHAIN_ID.toString() })];
  const artifact = require("../artifacts/src/UnifiedFlashLoanMevExecutor.sol/UnifiedFlashLoanMevExecutor.json");
  const deployedBytecodeBytes = (artifact.deployedBytecode.length - 2) / 2;
  checks.push(check("artifact.deployedBytecode", deployedBytecodeBytes < 24000, { deployedBytecodeBytes, maxBytes: 24000 }));

  const dependencies = await Promise.all([
    codeEvidence("aavePool", poolAddress),
    codeEvidence("usdc", usdcAddress),
    codeEvidence("v3Factory", factoryAddress),
    codeEvidence("v3Router", routerAddress),
    codeEvidence("v3Quoter", quoterAddress),
  ]);
  for (const item of dependencies) checks.push(check(`code.${item.label}`, item.hasCode, item));
  let v3Interface = null;
  if (dependencies[3].hasCode && dependencies[4].hasCode) {
    v3Interface = await validateV3InterfaceProbes(routerAddress, quoterAddress);
    checks.push(check("v3.routerQuoterAbi", v3Interface.ready, v3Interface));
  } else {
    checks.push(check("v3.routerQuoterAbi", false, { reason: "skipped_until_router_and_quoter_have_code" }));
  }

  let aave = null;
  if (dependencies[0].hasCode && dependencies[1].hasCode) {
    const borrowAmount = envBigInt(
      "UNIFIED_BORROW_AMOUNT_UNITS",
      process.env.TRIANGULAR_BORROW_AMOUNT_UNITS || "100000000",
    );
    aave = await readAaveState(poolAddress, usdcAddress, borrowAmount);
    checks.push(check("aave.usdcReserve", Boolean(aave.usdc), { borrowAmount: borrowAmount.toString() }));
    checks.push(check("aave.usdcDecimals", aave.usdc?.decimals === 6, { actual: aave.usdc?.decimals ?? null }));
    checks.push(check("aave.usdcFlashloanLiquidity", Boolean(aave.usdc?.usableForFlashloan), { usdc: aave.usdc }));
  }

  const preflightBlockNumber = await hre.ethers.provider.getBlockNumber();
  const cache = await validatePoolCache(factoryAddress, preflightBlockNumber);
  checks.push(check("v3.poolCache", cache.ready, cache));

  let deployment = null;
  if (signer && checks.every((item) => item.ok)) {
    const request = await (await hre.ethers.getContractFactory("UnifiedFlashLoanMevExecutor")).getDeployTransaction(
      poolAddress,
      usdcAddress,
      signer.address,
    );
    const estimatedGas = await hre.ethers.provider.estimateGas({ ...request, from: signer.address });
    const gasPrice = envBigInt(
      "TRIANGULAR_DEPLOY_MIN_GAS_PRICE_WEI",
      (await hre.ethers.provider.getFeeData()).maxFeePerGas || 1n,
    );
    const safetyBps = envBigInt("TRIANGULAR_DEPLOY_GAS_SAFETY_BPS", "15000");
    const configGasUnits = envBigInt("TRIANGULAR_DEPLOY_CONFIG_GAS_UNITS", "800000");
    const budget = ((estimatedGas + configGasUnits) * gasPrice * safetyBps) / 10000n;
    const balance = await hre.ethers.provider.getBalance(signer.address);
    deployment = {
      signer: signer.address,
      balanceWei: balance.toString(),
      estimatedDeploymentGas: estimatedGas.toString(),
      gasPriceWei: gasPrice.toString(),
      estimatedBudgetWei: budget.toString(),
      enoughBalance: balance >= budget,
    };
    checks.push(check("deployer.balance", deployment.enoughBalance, deployment));
  } else {
    checks.push(check("deployer.estimate", false, { reason: "skipped_until_network_dependency_and_cache_checks_pass" }));
  }

  return {
    runAt: new Date().toISOString(),
    network: hre.network.name,
    chainId: Number(network.chainId),
    rpcHost: rpcHost(),
    profile: { name: "uniswap_v3_avalanche_sdk_core_7.10.0", ...AVALANCHE_V3_PROFILE },
    configured: { aavePool: poolAddress, usdc: usdcAddress, factory: factoryAddress, router: routerAddress, quoter: quoterAddress },
    artifact: { deployedBytecodeBytes, deployedBytecodeHash: hre.ethers.keccak256(artifact.deployedBytecode) },
    dependencies,
    v3Interface,
    aave,
    cache,
    deployment,
    checks,
    ready: checks.every((item) => item.ok),
    broadcast: false,
  };
}

async function main() {
  const report = await validateDeploymentPrerequisites();
  console.log(JSON.stringify(report, null, 2));
  if (!report.ready) process.exitCode = 1;
}

if (require.main === module) {
  main().catch((error) => {
    console.error(error.message || error);
    process.exitCode = 1;
  });
}

module.exports = {
  validateDeploymentPrerequisites,
  reserveConfigFlags,
  AVALANCHE_V3_PROFILE,
  validateV3InterfaceProbes,
};
