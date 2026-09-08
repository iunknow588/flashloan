const hre = require("hardhat");
const { rpcHost, sanitizeError } = require("./fuji-evidence");

const EXPECTED_CHAIN_IDS = {
  fuji: 43113n,
  avalanche: 43114n,
};
const PLACEHOLDERS = new Set(["", "0x...", "0xyour_private_key"]);

const POOL_ABI = [
  "function FLASHLOAN_PREMIUM_TOTAL() view returns (uint128)",
  "function getReserveData(address asset) view returns (tuple(uint256 configuration,uint128 liquidityIndex,uint128 currentLiquidityRate,uint128 variableBorrowIndex,uint128 currentVariableBorrowRate,uint128 currentStableBorrowRate,uint40 lastUpdateTimestamp,uint16 id,address aTokenAddress,address stableDebtTokenAddress,address variableDebtTokenAddress,address interestRateStrategyAddress,uint128 accruedToTreasury,uint128 unbacked,uint128 isolationModeTotalDebt))",
];

const ERC20_ABI = [
  "function decimals() view returns (uint8)",
  "function balanceOf(address) view returns (uint256)",
];

const V3_POOL_ABI = [
  "function factory() view returns (address)",
  "function token0() view returns (address)",
  "function token1() view returns (address)",
  "function fee() view returns (uint24)",
  "function liquidity() view returns (uint128)",
  "function slot0() view returns (uint160 sqrtPriceX96,int24 tick,uint16 observationIndex,uint16 observationCardinality,uint16 observationCardinalityNext,uint8 feeProtocol,bool unlocked)",
];

function envValue(name) {
  return String(process.env[name] || "").trim();
}

function configured(name) {
  return !PLACEHOLDERS.has(envValue(name));
}

function configuredAddress(...names) {
  for (const name of names) {
    if (configured(name)) return { name, value: envValue(name) };
  }
  return { name: names.join(" or "), value: "" };
}

function optionalEnv(...names) {
  for (const name of names) {
    if (configured(name)) return envValue(name);
  }
  return "";
}

function positiveUint24Env(name) {
  const value = envValue(name);
  if (!value) {
    return check(`env.${name}`, false, { error: `${name} is required for V3 exactInput path construction` });
  }
  try {
    const parsed = BigInt(value);
    return check(`env.${name}`, parsed > 0n && parsed <= 16_777_215n, {
      value,
      error: parsed > 0n && parsed <= 16_777_215n ? undefined : `${name} must be between 1 and 16777215`,
    });
  } catch (error) {
    return check(`env.${name}`, false, { value, error: sanitizeError(error) });
  }
}

function bigintEnv(name, defaultValue) {
  const value = envValue(name);
  if (!value) return defaultValue;
  return BigInt(value);
}

function etherEnv(name, defaultEther) {
  const value = envValue(name) || defaultEther;
  return hre.ethers.parseEther(value);
}

function bpsEnv(name, defaultValue) {
  const value = envValue(name);
  const parsed = value ? BigInt(value) : BigInt(defaultValue);
  if (parsed < 10_000n) {
    throw new Error(`${name} must be at least 10000 bps`);
  }
  return parsed;
}

function stableTokenFromEnv(...names) {
  const direct = optionalEnv(...names);
  if (direct) return direct;
  const stableTokens = envValue("DEX_TARGET_STABLE_TOKENS");
  if (stableTokens) {
    const entries = stableTokens.split(",").map((item) => item.trim()).filter(Boolean);
    for (const preferred of ["USDC", "USDC.e", "USDC.E"]) {
      const entry = entries.find((item) => item.toUpperCase().startsWith(`${preferred.toUpperCase()}:`));
      if (entry) {
        const [, address] = entry.split(":", 2);
        if (address && address.trim()) return address.trim();
      }
    }
    const fallback = entries.find((item) => item.includes(":"));
    if (fallback) {
      const [, address] = fallback.split(":", 2);
      if (address && address.trim()) return address.trim();
    }
  }
  return "";
}

function check(name, ok, details = {}) {
  return {
    name,
    ok: Boolean(ok),
    level: ok ? "ok" : (details.level || "error"),
    ...details,
  };
}

function normalizeAddress(value) {
  const text = String(value || "").trim();
  if (hre.ethers.isAddress(text)) {
    return hre.ethers.getAddress(text);
  }
  if (/^0x[a-fA-F0-9]{40}$/.test(text)) {
    return hre.ethers.getAddress(text.toLowerCase());
  }
  return "";
}

function addressCheck(name, value, { required = true } = {}) {
  const address = normalizeAddress(value);
  if (!value) {
    return check(`address.${name}`, false, {
      level: required ? "error" : "warn",
      error: required ? `${name} is required` : `${name} is not configured`,
    });
  }
  if (!address) {
    return check(`address.${name}`, false, { error: `${name} is not a valid address` });
  }
  return check(`address.${name}`, true, { address });
}

async function codeCheck(name, addressResult, { required = true } = {}) {
  if (!addressResult.ok) return addressResult;
  if (addressResult.address === hre.ethers.ZeroAddress) {
    return check(`code.${name}`, false, {
      level: required ? "error" : "warn",
      address: addressResult.address,
      error: `${name} is zero address`,
    });
  }
  try {
    const code = await hre.ethers.provider.getCode(addressResult.address);
    const hasCode = code !== "0x";
    return check(`code.${name}`, hasCode, {
      level: hasCode ? "ok" : (required ? "error" : "warn"),
      address: addressResult.address,
      error: hasCode ? undefined : `${name} has no contract code`,
    });
  } catch (error) {
    return check(`code.${name}`, false, { error: sanitizeError(error) });
  }
}

async function v3PoolSemanticChecks(name, poolAddress, trade, expectedFactory) {
  const checks = [];
  try {
    const pool = await hre.ethers.getContractAt(V3_POOL_ABI, poolAddress);
    const [factory, token0, token1, fee, liquidity, slot0] = await Promise.all([
      pool.factory(),
      pool.token0(),
      pool.token1(),
      pool.fee(),
      pool.liquidity(),
      pool.slot0(),
    ]);
    const normalizedFactory = hre.ethers.getAddress(factory);
    const normalizedToken0 = hre.ethers.getAddress(token0);
    const normalizedToken1 = hre.ethers.getAddress(token1);
    const tokenX = hre.ethers.getAddress(trade.tokenX);
    const tokenY = hre.ethers.getAddress(trade.tokenY);
    const containsPair =
      (normalizedToken0 === tokenX && normalizedToken1 === tokenY)
        || (normalizedToken0 === tokenY && normalizedToken1 === tokenX);
    checks.push(check(`${name}.factory`, !expectedFactory || normalizedFactory === expectedFactory, {
      factory: normalizedFactory,
      expectedFactory: expectedFactory || null,
      error: !expectedFactory || normalizedFactory === expectedFactory ? undefined : "pool factory does not match TRIANGULAR_V3_FACTORY",
    }));
    checks.push(check(`${name}.tokens`, containsPair, {
      token0: normalizedToken0,
      token1: normalizedToken1,
      tokenX,
      tokenY,
      error: containsPair ? undefined : "pool token0/token1 does not match trade tokenX/tokenY",
    }));
    checks.push(check(`${name}.liquidity`, liquidity > 0n, {
      fee: fee.toString(),
      liquidity: liquidity.toString(),
      tick: slot0[1].toString(),
      error: liquidity > 0n ? undefined : "pool has no active liquidity",
    }));
  } catch (error) {
    checks.push(check(`${name}.semantic`, false, { error: sanitizeError(error) }));
  }
  return checks;
}

async function estimateGasLatest(tx, from) {
  const request = {
    from,
    ...(tx.to ? { to: tx.to } : {}),
    ...(tx.data ? { data: tx.data } : {}),
    ...(tx.value ? { value: hre.ethers.toQuantity(tx.value) } : {}),
  };
  const raw = await hre.ethers.provider.send("eth_estimateGas", [request, "latest"]);
  return BigInt(raw);
}

async function deploymentBudgetEstimate({ poolAddress, usdcAddress, deployerAddress }) {
  const Executor = await hre.ethers.getContractFactory("AaveTriangularExecutor");
  const Controller = await hre.ethers.getContractFactory("TriangularRouteController");
  const executorDeployTx = await Executor.getDeployTransaction(poolAddress, usdcAddress, deployerAddress);
  const controllerDeployTx = await Controller.getDeployTransaction(usdcAddress, deployerAddress, deployerAddress);
  const [executorGas, controllerGas, gasPriceRaw] = await Promise.all([
    estimateGasLatest(executorDeployTx, deployerAddress),
    estimateGasLatest(controllerDeployTx, deployerAddress),
    hre.ethers.provider.send("eth_gasPrice", []),
  ]);
  const configGas = bigintEnv("TRIANGULAR_DEPLOY_CONFIG_GAS_UNITS", 800_000n);
  const safetyBps = bpsEnv("TRIANGULAR_DEPLOY_GAS_SAFETY_BPS", 15_000);
  const networkGasPrice = BigInt(gasPriceRaw);
  const minGasPrice = bigintEnv("TRIANGULAR_DEPLOY_MIN_GAS_PRICE_WEI", 25_000_000_000n);
  const gasPrice = networkGasPrice > minGasPrice ? networkGasPrice : minGasPrice;
  const estimatedGasUnits = executorGas + controllerGas + configGas;
  const estimatedBudgetWei = (estimatedGasUnits * gasPrice * safetyBps) / 10_000n;
  const historicalFloorWei = etherEnv("TRIANGULAR_DEPLOY_HISTORICAL_MIN_BUDGET_AVAX", "0.25");
  const budgetWei = estimatedBudgetWei > historicalFloorWei ? estimatedBudgetWei : historicalFloorWei;
  return {
    executorGas: executorGas.toString(),
    controllerGas: controllerGas.toString(),
    configGas: configGas.toString(),
    estimatedGasUnits: estimatedGasUnits.toString(),
    networkGasPriceWei: networkGasPrice.toString(),
    minGasPriceWei: minGasPrice.toString(),
    gasPriceWei: gasPrice.toString(),
    safetyBps: safetyBps.toString(),
    estimatedBudgetWei: estimatedBudgetWei.toString(),
    estimatedBudgetAvax: hre.ethers.formatEther(estimatedBudgetWei),
    historicalFloorWei: historicalFloorWei.toString(),
    historicalFloorAvax: hre.ethers.formatEther(historicalFloorWei),
    budgetWei: budgetWei.toString(),
    budgetAvax: hre.ethers.formatEther(budgetWei),
  };
}

function normalizeRuntimePool(pool, tradeIndex, poolIndex) {
  if (!pool || typeof pool !== "object") {
    throw new Error(`runtimeTrades[${tradeIndex}].pools[${poolIndex}] must be an object`);
  }
  const adapterKind = Number(pool.adapterKind ?? pool.adapter_kind ?? 0);
  const poolAddress = normalizeAddress(pool.pool);
  return { adapterKind, pool: poolAddress };
}

function parseRuntimeTrades() {
  const value = envValue("TRIANGULAR_RUNTIME_TRADES_JSON");
  if (!value) return [];
  const parsed = JSON.parse(value);
  if (!Array.isArray(parsed)) {
    throw new Error("TRIANGULAR_RUNTIME_TRADES_JSON must be a JSON array");
  }
  if (parsed.length > 16) {
    throw new Error("TRIANGULAR_RUNTIME_TRADES_JSON must include at most 16 trades");
  }
  return parsed.map((trade, tradeIndex) => {
    if (!trade || typeof trade !== "object") {
      throw new Error(`runtimeTrades[${tradeIndex}] must be an object`);
    }
    const tokenX = normalizeAddress(trade.tokenX || trade.token_x);
    const tokenY = normalizeAddress(trade.tokenY || trade.token_y);
    if (!tokenX || !tokenY) {
      throw new Error(`runtimeTrades[${tradeIndex}] tokenX/tokenY must be valid addresses`);
    }
    if (tokenX.toLowerCase() === tokenY.toLowerCase()) {
      throw new Error(`runtimeTrades[${tradeIndex}] tokenX and tokenY must be different`);
    }
    const pools = trade.pools || trade.candidatePools || trade.candidate_pools;
    if (!Array.isArray(pools) || pools.length === 0) {
      throw new Error(`runtimeTrades[${tradeIndex}] pools must be a non-empty array`);
    }
    if (pools.length > 10) {
      throw new Error(`runtimeTrades[${tradeIndex}] pools must include at most 10 items`);
    }
    return {
      tradeIndex: Number(trade.tradeIndex ?? trade.trade_index ?? tradeIndex),
      tokenX,
      tokenY,
      pools: pools.map((pool, poolIndex) => normalizeRuntimePool(pool, tradeIndex, poolIndex)),
    };
  });
}

async function main() {
  const checks = [];
  let deployerAddress = "";
  let deployerBalance = 0n;
  const networkName = (hre.network.name || "fuji").toLowerCase();
  const expectedChainId = EXPECTED_CHAIN_IDS[networkName] || EXPECTED_CHAIN_IDS.fuji;
  const rpc = networkName === "avalanche"
    ? configuredAddress("AVALANCHE_RPC_URL", "AVALANCHE_RPC", "FUJI_RPC_URL", "AVALANCHE_FUJI_RPC_URL")
    : configuredAddress("FUJI_RPC_URL", "AVALANCHE_FUJI_RPC_URL", "AVALANCHE_RPC_URL", "AVALANCHE_RPC");
  const pool = configuredAddress("TRIANGULAR_AAVE_POOL_ADDRESS", "AAVE_POOL_ADDRESS");
  const usdc = {
    name: "TRIANGULAR_USDC_ADDRESS or FUJI_USDC or USDC_ADDRESS",
    value: stableTokenFromEnv("TRIANGULAR_USDC_ADDRESS", "FUJI_USDC", "USDC_ADDRESS"),
  };
  const controller = configuredAddress("TRIANGULAR_ROUTE_CONTROLLER_ADDRESS", "TRIANGULAR_CONTROLLER_ADDRESS");

  checks.push(check("env.rpc", Boolean(rpc.value), {
    rpcHost: rpc.value ? rpcHost(rpc.value) : null,
    error: rpc.value ? undefined : "FUJI_RPC_URL or AVALANCHE_FUJI_RPC_URL or AVALANCHE_RPC_URL is required",
  }));
  const hasKey = configured("DEPLOYER_PRIVATE_KEY") || configured("LIQUIDATION_EXECUTION_PRIVATE_KEY") || configured("COW_ORDER_SIGNER_PRIVATE_KEY");
  checks.push(check("env.signerPrivateKey", hasKey, {
    redacted: hasKey,
    error: hasKey ? undefined : "DEPLOYER_PRIVATE_KEY or LIQUIDATION_EXECUTION_PRIVATE_KEY is required",
  }));

  try {
    const network = await hre.ethers.provider.getNetwork();
    checks.push(check("network.chainId", network.chainId === expectedChainId, {
      chainId: Number(network.chainId),
      expectedChainId: Number(expectedChainId),
    }));
  } catch (error) {
    checks.push(check("network.chainId", false, { error: sanitizeError(error) }));
  }

  try {
    const [signer] = await hre.ethers.getSigners();
    if (!signer) {
      checks.push(check("deployer.signer", false, { error: "no deployer signer is available" }));
    } else {
      const balance = await hre.ethers.provider.getBalance(signer.address);
      deployerAddress = signer.address;
      deployerBalance = balance;
      const minDeployBalance = etherEnv("TRIANGULAR_MIN_DEPLOYER_BALANCE_AVAX", "0.12");
      checks.push(check("deployer.balance", balance > 0n, {
        address: signer.address,
        balanceAvax: hre.ethers.formatEther(balance),
        error: balance > 0n ? undefined : "deployer has no AVAX",
      }));
      checks.push(check("deployer.balanceForDeployment", balance >= minDeployBalance, {
        address: signer.address,
        balanceAvax: hre.ethers.formatEther(balance),
        minDeployBalanceAvax: hre.ethers.formatEther(minDeployBalance),
        error: balance >= minDeployBalance
          ? undefined
          : "deployer balance is below the configured deployment safety floor",
      }));
    }
  } catch (error) {
    checks.push(check("deployer.signer", false, { error: sanitizeError(error) }));
  }

  const poolAddress = addressCheck(pool.name, pool.value);
  const usdcAddress = addressCheck(usdc.name, usdc.value);
  checks.push(poolAddress, usdcAddress);

  let poolCode = null;
  if (poolAddress.ok) {
    poolCode = await codeCheck(pool.name, poolAddress);
    checks.push(poolCode);
  }

  let usdcCode = null;
  if (usdcAddress.ok) {
    usdcCode = await codeCheck(usdc.name, usdcAddress);
    checks.push(usdcCode);
  }

  if (poolAddress.ok && usdcAddress.ok && deployerAddress) {
    try {
      const budget = await deploymentBudgetEstimate({
        poolAddress: poolAddress.address,
        usdcAddress: usdcAddress.address,
        deployerAddress,
      });
      const budgetWei = BigInt(budget.budgetWei);
      checks.push(check("deployer.estimatedDeploymentBudget", deployerBalance >= budgetWei, {
        ...budget,
        balanceAvax: hre.ethers.formatEther(deployerBalance),
        error: deployerBalance >= budgetWei
          ? undefined
          : "deployer balance is below estimated/historical deployment budget",
      }));
    } catch (error) {
      checks.push(check("deployer.estimatedDeploymentBudget", false, { error: sanitizeError(error) }));
    }
  }

  if (poolCode && poolCode.ok) {
    try {
      const aavePool = await hre.ethers.getContractAt(POOL_ABI, poolCode.address);
      const premiumBps = await aavePool.FLASHLOAN_PREMIUM_TOTAL();
      checks.push(check("aave.flashLoanPremiumBps", true, { premiumBps: premiumBps.toString() }));
    } catch (error) {
      checks.push(check("aave.flashLoanPremiumBps", false, { error: sanitizeError(error) }));
    }
  }

  if (usdcCode && usdcCode.ok) {
    try {
      const token = await hre.ethers.getContractAt(ERC20_ABI, usdcCode.address);
      const aavePool = poolCode && poolCode.ok ? await hre.ethers.getContractAt(POOL_ABI, poolCode.address) : null;
      const reserveData = aavePool ? await aavePool.getReserveData(usdcCode.address) : null;
      const aTokenAddress = reserveData ? reserveData.aTokenAddress : hre.ethers.ZeroAddress;
      const [decimals, poolBalance, aTokenUnderlyingLiquidity] = await Promise.all([
        token.decimals(),
        token.balanceOf(poolCode && poolCode.ok ? poolCode.address : hre.ethers.ZeroAddress),
        token.balanceOf(aTokenAddress),
      ]);
      checks.push(check("usdc.metadata", true, {
        decimals: Number(decimals),
        poolBalance: poolBalance.toString(),
        aTokenAddress,
        aTokenUnderlyingLiquidity: aTokenUnderlyingLiquidity.toString(),
      }));
      const configuredBorrowAmount = bigintEnv(
        "TRIANGULAR_BORROW_AMOUNT_UNITS",
        bigintEnv("TRIANGULAR_EXECUTION_AMOUNT_USDC_BASE_UNITS", 100_000_000n),
      );
      checks.push(check("aave.usdcLiquidityForBorrow", configuredBorrowAmount > 0n && configuredBorrowAmount <= aTokenUnderlyingLiquidity, {
        borrowAmount: configuredBorrowAmount.toString(),
        aTokenUnderlyingLiquidity: aTokenUnderlyingLiquidity.toString(),
        error: configuredBorrowAmount <= aTokenUnderlyingLiquidity
          ? undefined
          : "configured borrow amount exceeds current Aave USDC liquidity",
      }));
    } catch (error) {
      checks.push(check("usdc.metadata", false, { error: sanitizeError(error) }));
    }
  }

  let configuredV3Factory = "";
  for (const [envName, required] of [
    ["TRIANGULAR_V3_FACTORY", true],
    ["TRIANGULAR_V3_ROUTER", true],
    ["TRIANGULAR_V3_QUOTER", true],
  ]) {
    const value = optionalEnv(envName, envName.replace("TRIANGULAR_", "UNISWAP_"));
    if (!value) {
      checks.push(check(`env.${envName}`, false, {
        level: required ? "error" : "warn",
        error: `${envName} is required for V3 execution`,
      }));
      continue;
    }
    const addressResult = addressCheck(envName, value, { required });
    checks.push(addressResult);
    checks.push(await codeCheck(envName, addressResult, { required }));
    if (envName === "TRIANGULAR_V3_FACTORY" && addressResult.ok) {
      configuredV3Factory = addressResult.address;
    }
  }
  checks.push(positiveUint24Env("TRIANGULAR_USDC_TO_TOKEN_X_FEE"));
  checks.push(positiveUint24Env("TRIANGULAR_TOKEN_Y_TO_USDC_FEE"));

  let runtimeTrades = [];
  try {
    runtimeTrades = parseRuntimeTrades();
    checks.push(check("env.TRIANGULAR_RUNTIME_TRADES_JSON", runtimeTrades.length > 0, {
      level: runtimeTrades.length > 0 ? "ok" : "warn",
      tradeCount: runtimeTrades.length,
      error: runtimeTrades.length > 0 ? undefined : "runtime trades are required for execute:*-triangular-ab",
    }));
  } catch (error) {
    checks.push(check("env.TRIANGULAR_RUNTIME_TRADES_JSON", false, { error: sanitizeError(error) }));
  }

  for (const [tradeIndex, trade] of runtimeTrades.entries()) {
    checks.push(addressCheck(`runtimeTrades[${tradeIndex}].tokenX`, trade.tokenX));
    checks.push(addressCheck(`runtimeTrades[${tradeIndex}].tokenY`, trade.tokenY));
    for (const [poolIndex, poolCandidate] of trade.pools.entries()) {
      checks.push(check(`runtimeTrades[${tradeIndex}].pools[${poolIndex}].adapterKind`, poolCandidate.adapterKind === 1, {
        adapterKind: poolCandidate.adapterKind,
        error: poolCandidate.adapterKind === 1 ? undefined : "only ADAPTER_UNISWAP_V3=1 is supported",
      }));
      const poolResult = addressCheck(`runtimeTrades[${tradeIndex}].pools[${poolIndex}].pool`, poolCandidate.pool);
      checks.push(poolResult);
      const poolCodeResult = await codeCheck(`runtimeTrades[${tradeIndex}].pools[${poolIndex}].pool`, poolResult);
      checks.push(poolCodeResult);
      if (poolCandidate.adapterKind === 1 && poolCodeResult.ok) {
        checks.push(...await v3PoolSemanticChecks(
          `runtimeTrades[${tradeIndex}].pools[${poolIndex}]`,
          poolResult.address,
          trade,
          configuredV3Factory,
        ));
      }
    }
  }

  if (controller.value) {
    const controllerAddress = addressCheck(controller.name, controller.value);
    checks.push(controllerAddress, await codeCheck(controller.name, controllerAddress));
  } else {
    checks.push(check("address.controller", false, {
      level: "warn",
      error: "TRIANGULAR_ROUTE_CONTROLLER_ADDRESS is not configured; deployment can proceed but execution cannot",
    }));
  }

  const errors = checks.filter((item) => !item.ok && item.level === "error");
  const warnings = checks.filter((item) => item.level === "warn");
  const report = {
    network: networkName,
    summary: {
      ok: errors.length === 0,
      deploymentReady: errors.length === 0,
      executionConfigured: errors.length === 0 && warnings.length === 0,
      errorCount: errors.length,
      warningCount: warnings.length,
    },
    checks,
  };

  console.log(JSON.stringify(report, null, 2));
  if (!report.summary.ok) process.exitCode = 1;
}

if (require.main === module) {
  main().catch((error) => {
    console.error(JSON.stringify({ ok: false, error: sanitizeError(error) }, null, 2));
    process.exitCode = 1;
  });
}
