const hre = require("hardhat");
const {
  appendJsonl,
  boolEnv,
  buildBroadcastGate,
  evidencePaths,
  networkContext,
  ownerMatchesSigner,
  receiptReport,
  sanitizeError,
  writeJson,
} = require("./fuji-evidence");

function requireEnv(name) {
  const value = process.env[name];
  if (!value || !value.trim() || value === "0x...") {
    throw new Error(`${name} is required`);
  }
  return value.trim();
}

function requireAnyEnv(...names) {
  for (const name of names) {
    const value = process.env[name];
    if (value && value.trim() && value !== "0x...") {
      return value.trim();
    }
  }
  throw new Error(`${names.join(" or ")} is required`);
}

function optionalEnv(...names) {
  for (const name of names) {
    const value = process.env[name];
    if (value && value.trim() && value !== "0x...") {
      return value.trim();
    }
  }
  return "";
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

function envBigInt(name, defaultValue) {
  const value = process.env[name];
  return value && value.trim() ? BigInt(value.trim()) : defaultValue;
}

function envNumber(name, defaultValue) {
  const value = process.env[name];
  return value && value.trim() ? Number(value.trim()) : defaultValue;
}

function parseRouteCandidates() {
  const value = process.env.TRIANGULAR_ROUTE_CANDIDATES_JSON;
  if (!value || !value.trim()) return [];
  const parsed = JSON.parse(value);
  if (!Array.isArray(parsed) || parsed.length === 0) {
    throw new Error("TRIANGULAR_ROUTE_CANDIDATES_JSON must be a non-empty JSON array");
  }
  return parsed.map((candidate, index) => {
    if (!candidate || typeof candidate !== "object") {
      throw new Error(`TRIANGULAR_ROUTE_CANDIDATES_JSON[${index}] must be an object`);
    }
    return {
      tokenX: String(candidate.tokenX || "").trim(),
      tokenY: String(candidate.tokenY || "").trim(),
      router: String(candidate.router || "").trim(),
    };
  });
}

function decisionReport(result) {
  return {
    ok: Boolean(result[0]),
    viable: Boolean(result[0]),
    reverse: Boolean(result[1]),
    quotedFinalUsdc: result[2].toString(),
    profitUsdc: result[3].toString(),
    path: result[4],
    edgeBps: result[5].toString(),
    requiredEdgeBps: result[6].toString(),
    directComparableAmount: result[7].toString(),
    viaComparableAmount: result[8].toString(),
  };
}

async function main() {
  requireAnyEnv("FUJI_RPC_URL", "AVALANCHE_FUJI_RPC_URL", "AVALANCHE_RPC_URL", "AVALANCHE_RPC");

  const controllerAddress = normalizeAddress(optionalEnv("TRIANGULAR_ROUTE_CONTROLLER_ADDRESS", "TRIANGULAR_CONTROLLER_ADDRESS"));
  if (!controllerAddress) throw new Error("TRIANGULAR_ROUTE_CONTROLLER_ADDRESS is required");

  const defaultRouter = normalizeAddress(optionalEnv("TRIANGULAR_DEX_ROUTER", "DEX_ROUTER_ADDRESS", "FUJI_DEX_ROUTER"));
  const routeCandidates = parseRouteCandidates();
  const singleTokenX = normalizeAddress(optionalEnv("TRIANGULAR_TOKEN_X"));
  const singleTokenY = normalizeAddress(optionalEnv("TRIANGULAR_TOKEN_Y"));
  if (routeCandidates.length === 0) {
    if (!defaultRouter) throw new Error("TRIANGULAR_DEX_ROUTER or FUJI_DEX_ROUTER is required");
    if (!singleTokenX) throw new Error("TRIANGULAR_TOKEN_X is required");
    if (!singleTokenY) throw new Error("TRIANGULAR_TOKEN_Y is required");
  }

  const latest = await hre.ethers.provider.getBlock("latest");
  const deadlineSeconds = envNumber("TRIANGULAR_DEADLINE_SECONDS", 60);
  const sharedRequest = {
    amount: envBigInt("TRIANGULAR_BORROW_AMOUNT_UNITS", 1_000_000n),
    premiumBps: envBigInt("TRIANGULAR_AAVE_PREMIUM_BPS", 5n),
    minProfitUsdc: envBigInt("TRIANGULAR_MIN_PROFIT_USDC_UNITS", 1n),
    deadline: BigInt(latest.timestamp + deadlineSeconds),
    slippageBps: envBigInt("TRIANGULAR_SLIPPAGE_BPS", 50n),
    allowReverse: !boolEnv(process.env, "TRIANGULAR_DISABLE_REVERSE"),
  };
  const requests = routeCandidates.length > 0
    ? routeCandidates.map((candidate, index) => {
      const router = normalizeAddress(candidate.router || defaultRouter);
      const tokenX = normalizeAddress(candidate.tokenX);
      const tokenY = normalizeAddress(candidate.tokenY);
      if (!router) throw new Error(`router is required for route candidate ${index}`);
      if (!tokenX) throw new Error(`tokenX is required for route candidate ${index}`);
      if (!tokenY) throw new Error(`tokenY is required for route candidate ${index}`);
      return { ...sharedRequest, tokenX, tokenY, router };
    })
    : [{ ...sharedRequest, tokenX: singleTokenX, tokenY: singleTokenY, router: defaultRouter }];
  const useBatch = requests.length > 1;
  const request = requests[0];
  const networkName = hre.network.name || "unknown";

  const controller = await hre.ethers.getContractAt("TriangularRouteController", controllerAddress);
  const ownerGate = await ownerMatchesSigner(hre, controller, process.env);
  const paths = evidencePaths({ strategy: `${networkName}-triangular-ab-execute` });

  let preview = { ok: false };
  try {
    if (useBatch) {
      const result = await controller.previewBestRouteFrom.staticCall(requests);
      preview = { ...decisionReport(result[1]), bestIndex: result[0].toString(), candidates: requests.length };
    } else {
      const result = await controller.previewBestRoute.staticCall(request);
      preview = decisionReport(result);
    }
  } catch (error) {
    preview = { ok: false, error: sanitizeError(error) };
  }

  let staticCall = { ok: false };
  try {
    if (useBatch) {
      await controller.runBest.staticCall(requests);
    } else {
      await controller.run.staticCall(request);
    }
    const gasEstimate = useBatch
      ? await controller.runBest.estimateGas(requests)
      : await controller.run.estimateGas(request);
    staticCall = { ok: true, gasEstimate: gasEstimate.toString() };
  } catch (error) {
    staticCall = { ok: false, error: sanitizeError(error) };
  }

  const gate = await buildBroadcastGate({
    hreLike: hre,
    env: process.env,
    strategy: "small-amount",
    intent: ["TRIANGULAR_AB_BROADCAST_ENABLED"],
    executionEnvNames: ["TRIANGULAR_AB_BROADCAST_ENABLED"],
    ownerMatches: ownerGate.matches,
    staticCallOk: staticCall.ok,
    payloadFresh: requests.every((item) => item.deadline > BigInt(latest.timestamp)),
    minProfitChecked: requests.every((item) => item.minProfitUsdc > 0n),
  });

  let receipt = null;
  let broadcast = { requested: boolEnv(process.env, "TRIANGULAR_AB_BROADCAST_ENABLED"), sent: false };
  if (gate.ready) {
    const tx = useBatch ? await controller.runBest(requests) : await controller.run(request);
    receipt = await tx.wait();
    broadcast = { ...broadcast, sent: true, hash: receipt.hash };
  }

  const report = {
    runId: paths.runId,
    strategy: "triangular_ab",
    mode: receipt ? "broadcast" : "static-call",
    startedAt: new Date().toISOString(),
    finishedAt: new Date().toISOString(),
    context: await networkContext(hre, process.env),
    network: networkName,
    owner: ownerGate,
    controllerAddress,
    blockNumber: latest.number,
    blockTimestamp: latest.timestamp,
    useBatch,
    requests: requests.map((item) => ({
      tokenX: item.tokenX,
      tokenY: item.tokenY,
      router: item.router,
      amount: item.amount.toString(),
      premiumBps: item.premiumBps.toString(),
      minProfitUsdc: item.minProfitUsdc.toString(),
      deadline: item.deadline.toString(),
      slippageBps: item.slippageBps.toString(),
      allowReverse: item.allowReverse,
    })),
    request: {
      tokenX: request.tokenX,
      tokenY: request.tokenY,
      router: request.router,
      amount: request.amount.toString(),
      premiumBps: request.premiumBps.toString(),
      minProfitUsdc: request.minProfitUsdc.toString(),
      deadline: request.deadline.toString(),
      slippageBps: request.slippageBps.toString(),
      allowReverse: request.allowReverse,
    },
    preview,
    staticCall,
    broadcast,
    gate,
    receipt: receiptReport(receipt),
  };
  writeJson(paths.reportPath, report);
  appendJsonl(paths.tradeLogPath, {
    runId: paths.runId,
    observedAt: report.finishedAt,
    network: hre.network.name,
    strategy: "triangular_ab",
    action: receipt ? "broadcast" : "static-call",
    success: staticCall.ok,
    controllerAddress,
    reportPath: paths.reportPath,
    txHash: receipt ? receipt.hash : null,
  });
  console.log(JSON.stringify({
    ok: staticCall.ok,
    mode: report.mode,
    preview,
    gateReady: gate.ready,
    txHash: receipt ? receipt.hash : null,
    reportPath: paths.reportPath,
  }, null, 2));
}

if (require.main === module) {
  main().catch((error) => {
    console.error(JSON.stringify({ ok: false, error: sanitizeError(error) }, null, 2));
    process.exitCode = 1;
  });
}
