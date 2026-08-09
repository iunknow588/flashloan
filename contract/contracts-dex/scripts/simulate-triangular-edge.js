const hre = require("hardhat");

const ROUTER_ABI = [
  "function getAmountsOut(uint256 amountIn, address[] calldata path) external view returns (uint256[] memory amounts)",
];

function optionalEnv(...names) {
  for (const name of names) {
    const value = process.env[name];
    if (value && value.trim() && value !== "0x...") {
      return value.trim();
    }
  }
  return "";
}

function requireAnyEnv(...names) {
  const value = optionalEnv(...names);
  if (!value) throw new Error(`${names.join(" or ")} is required`);
  return value;
}

function envBigInt(name, defaultValue) {
  const value = process.env[name];
  return value && value.trim() ? BigInt(value.trim()) : defaultValue;
}

function parseRouteCandidates(defaultRouter) {
  const value = process.env.TRIANGULAR_ROUTE_CANDIDATES_JSON;
  if (!value || !value.trim()) {
    return [{
      router: defaultRouter,
      tokenX: requireAnyEnv("TRIANGULAR_TOKEN_X"),
      tokenY: requireAnyEnv("TRIANGULAR_TOKEN_Y"),
    }];
  }

  const parsed = JSON.parse(value);
  if (!Array.isArray(parsed) || parsed.length === 0) {
    throw new Error("TRIANGULAR_ROUTE_CANDIDATES_JSON must be a non-empty JSON array");
  }
  return parsed.map((candidate, index) => {
    const router = String(candidate.router || defaultRouter || "").trim();
    const tokenX = String(candidate.tokenX || "").trim();
    const tokenY = String(candidate.tokenY || "").trim();
    if (!router) throw new Error(`router is required for route candidate ${index}`);
    if (!tokenX) throw new Error(`tokenX is required for route candidate ${index}`);
    if (!tokenY) throw new Error(`tokenY is required for route candidate ${index}`);
    return { router, tokenX, tokenY };
  });
}

function ceilDiv(value, divisor) {
  return value === 0n ? 0n : ((value - 1n) / divisor) + 1n;
}

function edgeBps(viaAmount, directAmount) {
  if (directAmount === 0n || viaAmount <= directAmount) return 0n;
  return ((viaAmount - directAmount) * 10000n) / directAmount;
}

async function quote(router, amount, path) {
  try {
    const amounts = await router.getAmountsOut(amount, path);
    return {
      ok: true,
      amountOut: amounts[amounts.length - 1],
      amounts: amounts.map((item) => item.toString()),
    };
  } catch (error) {
    return { ok: false, error: error.shortMessage || error.message };
  }
}

async function evaluateCandidate({ router: routerAddress, tokenX, tokenY }, params) {
  const router = await hre.ethers.getContractAt(ROUTER_ABI, routerAddress);
  const usdc = params.usdc;
  const amount = params.amount;
  const requiredFinal = amount + ((amount * params.premiumBps) / 10000n) + params.minProfitUsdc;

  const usdcToX = await quote(router, amount, [usdc, tokenX]);
  const usdcToY = await quote(router, amount, [usdc, tokenY]);

  let forward = { ok: false };
  if (usdcToX.ok && usdcToY.ok) {
    const xToY = await quote(router, usdcToX.amountOut, [tokenX, tokenY]);
    const full = await quote(router, amount, [usdc, tokenX, tokenY, usdc]);
    const edge = xToY.ok ? edgeBps(xToY.amountOut, usdcToY.amountOut) : 0n;
    const minAfterSlippage = full.ok ? (full.amountOut * (10000n - params.slippageBps)) / 10000n : 0n;
    forward = {
      ok: xToY.ok && full.ok,
      path: [usdc, tokenX, tokenY, usdc],
      directComparableAmount: usdcToY.amountOut,
      viaComparableAmount: xToY.amountOut || 0n,
      edgeBps: edge,
      requiredEdgeBps: params.requiredEdgeBps,
      quotedFinalUsdc: full.amountOut || 0n,
      minAfterSlippage,
      viable: xToY.ok && full.ok && edge >= params.requiredEdgeBps && minAfterSlippage >= requiredFinal,
    };
  }

  let reverse = { ok: false };
  if (usdcToX.ok && usdcToY.ok) {
    const yToX = await quote(router, usdcToY.amountOut, [tokenY, tokenX]);
    const full = await quote(router, amount, [usdc, tokenY, tokenX, usdc]);
    const edge = yToX.ok ? edgeBps(yToX.amountOut, usdcToX.amountOut) : 0n;
    const minAfterSlippage = full.ok ? (full.amountOut * (10000n - params.slippageBps)) / 10000n : 0n;
    reverse = {
      ok: yToX.ok && full.ok,
      path: [usdc, tokenY, tokenX, usdc],
      directComparableAmount: usdcToX.amountOut,
      viaComparableAmount: yToX.amountOut || 0n,
      edgeBps: edge,
      requiredEdgeBps: params.requiredEdgeBps,
      quotedFinalUsdc: full.amountOut || 0n,
      minAfterSlippage,
      viable: yToX.ok && full.ok && edge >= params.requiredEdgeBps && minAfterSlippage >= requiredFinal,
    };
  }

  return { router: routerAddress, tokenX, tokenY, forward, reverse };
}

function bigintReplacer(_key, value) {
  return typeof value === "bigint" ? value.toString() : value;
}

async function main() {
  requireAnyEnv("FUJI_RPC_URL", "AVALANCHE_FUJI_RPC_URL");

  const usdc = requireAnyEnv("TRIANGULAR_USDC_ADDRESS", "FUJI_USDC", "USDC_ADDRESS");
  const defaultRouter = optionalEnv("TRIANGULAR_DEX_ROUTER", "FUJI_DEX_ROUTER");
  const amount = envBigInt("TRIANGULAR_BORROW_AMOUNT_UNITS", 1_000_000n);
  const premiumBps = envBigInt("TRIANGULAR_AAVE_PREMIUM_BPS", 5n);
  const minProfitUsdc = envBigInt("TRIANGULAR_MIN_PROFIT_USDC_UNITS", 1n);
  const slippageBps = envBigInt("TRIANGULAR_SLIPPAGE_BPS", 50n);
  const requiredEdgeBps = premiumBps + slippageBps + ceilDiv(minProfitUsdc * 10000n, amount);
  const candidates = parseRouteCandidates(defaultRouter);

  const params = { usdc, amount, premiumBps, minProfitUsdc, slippageBps, requiredEdgeBps };
  const evaluations = [];
  for (const candidate of candidates) {
    evaluations.push(await evaluateCandidate(candidate, params));
  }

  const directions = evaluations.flatMap((item, index) => [
    { index, reverse: false, ...item.forward },
    { index, reverse: true, ...item.reverse },
  ]).filter((item) => item.ok);
  const viable = directions.filter((item) => item.viable);
  viable.sort((a, b) => (a.quotedFinalUsdc === b.quotedFinalUsdc ? 0 : a.quotedFinalUsdc > b.quotedFinalUsdc ? -1 : 1));

  console.log(JSON.stringify({
    ok: true,
    network: hre.network.name,
    chainId: Number((await hre.ethers.provider.getNetwork()).chainId),
    params,
    candidates: evaluations,
    best: viable[0] || null,
  }, bigintReplacer, 2));
}

if (require.main === module) {
  main().catch((error) => {
    console.error(JSON.stringify({ ok: false, error: error.shortMessage || error.message }, null, 2));
    process.exitCode = 1;
  });
}
