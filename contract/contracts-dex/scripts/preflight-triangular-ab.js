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

function stableTokenFromEnv(...names) {
  for (const name of names) {
    if (configured(name)) return envValue(name);
  }
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

function addressCheck(name, value) {
  const address = normalizeAddress(value);
  if (!value) {
    return check(`address.${name}`, false, { error: `${name} is required` });
  }
  if (!address) {
    return check(`address.${name}`, false, { error: `${name} is not a valid address` });
  }
  return check(`address.${name}`, true, { address });
}

async function codeCheck(name, addressResult) {
  if (!addressResult.ok) return addressResult;
  try {
    const code = await hre.ethers.provider.getCode(addressResult.address);
    const hasCode = code !== "0x";
    return check(`code.${name}`, hasCode, {
      address: addressResult.address,
      error: hasCode ? undefined : `${name} has no contract code`,
    });
  } catch (error) {
    return check(`code.${name}`, false, { error: sanitizeError(error) });
  }
}

async function main() {
  const checks = [];
  const networkName = (hre.network.name || "fuji").toLowerCase();
  const expectedChainId = EXPECTED_CHAIN_IDS[networkName] || EXPECTED_CHAIN_IDS.fuji;
  const rpc = networkName === "avalanche"
    ? configuredAddress("AVALANCHE_RPC_URL", "AVALANCHE_RPC", "FUJI_RPC_URL", "AVALANCHE_FUJI_RPC_URL")
    : configuredAddress("FUJI_RPC_URL", "AVALANCHE_FUJI_RPC_URL", "AVALANCHE_RPC_URL", "AVALANCHE_RPC");
  const pool = configuredAddress("TRIANGULAR_AAVE_POOL_ADDRESS", "AAVE_POOL_ADDRESS");
  const usdc = configuredAddress("TRIANGULAR_USDC_ADDRESS", "FUJI_USDC", "USDC_ADDRESS");
  const derivedUsdc = stableTokenFromEnv("TRIANGULAR_USDC_ADDRESS", "FUJI_USDC", "USDC_ADDRESS");
  const router = configuredAddress("TRIANGULAR_DEX_ROUTER", "DEX_ROUTER_ADDRESS", "FUJI_DEX_ROUTER");

  checks.push(check("env.rpc", Boolean(rpc.value), {
    rpcHost: rpc.value ? rpcHost(rpc.value) : null,
    error: rpc.value ? undefined : "FUJI_RPC_URL or AVALANCHE_FUJI_RPC_URL or AVALANCHE_RPC_URL is required",
  }));
  checks.push(check("env.DEPLOYER_PRIVATE_KEY", configured("DEPLOYER_PRIVATE_KEY") || configured("LIQUIDATION_EXECUTION_PRIVATE_KEY") || configured("COW_ORDER_SIGNER_PRIVATE_KEY"), {
    redacted: configured("DEPLOYER_PRIVATE_KEY") || configured("LIQUIDATION_EXECUTION_PRIVATE_KEY") || configured("COW_ORDER_SIGNER_PRIVATE_KEY"),
    error: (configured("DEPLOYER_PRIVATE_KEY") || configured("LIQUIDATION_EXECUTION_PRIVATE_KEY") || configured("COW_ORDER_SIGNER_PRIVATE_KEY")) ? undefined : "DEPLOYER_PRIVATE_KEY or LIQUIDATION_EXECUTION_PRIVATE_KEY is required",
  }));

  let network = null;
  try {
    network = await hre.ethers.provider.getNetwork();
    checks.push(check("network.chainId", network.chainId === expectedChainId, {
      chainId: Number(network.chainId),
      expectedChainId: Number(expectedChainId),
    }));
  } catch (error) {
    checks.push(check("network.chainId", false, { error: sanitizeError(error) }));
  }

  let deployer = null;
  try {
    const [signer] = await hre.ethers.getSigners();
    if (!signer) {
      checks.push(check("deployer.signer", false, { error: "no deployer signer is available" }));
    } else {
      deployer = signer.address;
      const balance = await hre.ethers.provider.getBalance(deployer);
      checks.push(check("deployer.balance", balance > 0n, {
        address: deployer,
        balanceAvax: hre.ethers.formatEther(balance),
        error: balance > 0n ? undefined : "deployer has no Fuji AVAX",
      }));
    }
  } catch (error) {
    checks.push(check("deployer.signer", false, { error: sanitizeError(error) }));
  }

  const poolAddress = addressCheck(pool.name, pool.value);
  const usdcAddress = addressCheck(usdc.name, usdc.value || derivedUsdc);
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

  if (router.value) {
    const routerAddress = addressCheck(router.name, router.value);
    checks.push(routerAddress, await codeCheck(router.name, routerAddress));
  } else {
    checks.push(check("address.router", false, {
      level: "warn",
      error: "router is not configured; deployment can proceed but execution cannot",
    }));
  }

  for (const name of ["TRIANGULAR_TOKEN_X", "TRIANGULAR_TOKEN_Y"]) {
    if (!configured(name)) {
      checks.push(check(`address.${name}`, false, {
        level: "warn",
        error: `${name} is not configured; execution cannot run`,
      }));
      continue;
    }
    const tokenAddress = addressCheck(name, envValue(name));
    checks.push(tokenAddress, await codeCheck(name, tokenAddress));
  }

  const errors = checks.filter((item) => !item.ok && item.level === "error");
  const warnings = checks.filter((item) => item.level === "warn");
  const report = {
    network: networkName,
    deployer,
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
