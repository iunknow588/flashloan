const hre = require("hardhat");
const { rpcHost, sanitizeError } = require("./fuji-evidence");

const EXPECTED_CHAIN_IDS = {
  fuji: 43113n,
  avalanche: 43114n,
};
const PLACEHOLDERS = new Set(["", "0x...", "0xyour_private_key"]);

const POOL_ABI = [
  "function FLASHLOAN_PREMIUM_TOTAL() view returns (uint128)",
];

const ERC20_ABI = [
  "function decimals() view returns (uint8)",
  "function balanceOf(address) view returns (uint256)",
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
      level: hasCode || required ? "error" : "warn",
      address: addressResult.address,
      error: hasCode ? undefined : `${name} has no contract code`,
    });
  } catch (error) {
    return check(`code.${name}`, false, { error: sanitizeError(error) });
  }
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
      checks.push(check("deployer.balance", balance > 0n, {
        address: signer.address,
        balanceAvax: hre.ethers.formatEther(balance),
        error: balance > 0n ? undefined : "deployer has no AVAX",
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
      const [decimals, poolBalance] = await Promise.all([
        token.decimals(),
        token.balanceOf(poolCode && poolCode.ok ? poolCode.address : hre.ethers.ZeroAddress),
      ]);
      checks.push(check("usdc.metadata", true, {
        decimals: Number(decimals),
        poolBalance: poolBalance.toString(),
      }));
    } catch (error) {
      checks.push(check("usdc.metadata", false, { error: sanitizeError(error) }));
    }
  }

  for (const [envName, required] of [
    ["TRIANGULAR_V3_FACTORY", false],
    ["TRIANGULAR_V3_ROUTER", false],
    ["TRIANGULAR_V3_QUOTER", false],
  ]) {
    const value = optionalEnv(envName, envName.replace("TRIANGULAR_", "UNISWAP_"));
    if (!value) {
      checks.push(check(`env.${envName}`, false, {
        level: "warn",
        error: `${envName} is not configured; adapter will use zero address for this field`,
      }));
      continue;
    }
    const addressResult = addressCheck(envName, value, { required });
    checks.push(addressResult);
    if (envName !== "TRIANGULAR_V3_FACTORY") {
      checks.push(await codeCheck(envName, addressResult, { required: false }));
    }
  }

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
      checks.push(await codeCheck(`runtimeTrades[${tradeIndex}].pools[${poolIndex}].pool`, poolResult));
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
