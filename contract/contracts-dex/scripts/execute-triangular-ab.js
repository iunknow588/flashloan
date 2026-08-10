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
    };
  });
}

function decisionReport(result) {
  const failureCode = result[9] ? Number(result[9]) : 0;
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
    failureCode: failureCode.toString(),
    failureReason: routeFailureReason(failureCode),
    requiredFinalUsdc: result[10] ? result[10].toString() : "0",
    minAfterSlippageUsdc: result[11] ? result[11].toString() : "0",
    amountOutMinUsdc: result[12] ? result[12].toString() : "0",
    selectedAmount: result[13] ? result[13].toString() : "0",
    routeMaxBorrow: result[14] ? result[14].toString() : "0",
    probeAmount: result[15] ? result[15].toString() : "0",
    probeProfitUsdc: result[16] ? result[16].toString() : "0",
    fundingCostUsdc: result[17] ? result[17].toString() : "0",
  };
}

function routeFailureReason(code) {
  return ({
    0: "none",
    1: "first_hop_quote_failed",
    2: "direct_comparison_quote_failed",
    3: "middle_hop_quote_failed",
    4: "edge_below_required",
    5: "full_route_quote_failed",
    6: "quoted_final_below_required",
    7: "slippage_adjusted_final_below_required",
  })[Number(code)] || `unknown_failure_${code}`;
}

async function main() {
  requireAnyEnv("FUJI_RPC_URL", "AVALANCHE_FUJI_RPC_URL", "AVALANCHE_RPC_URL", "AVALANCHE_RPC");

  const controllerAddress = normalizeAddress(optionalEnv("TRIANGULAR_ROUTE_CONTROLLER_ADDRESS", "TRIANGULAR_CONTROLLER_ADDRESS"));
  if (!controllerAddress) throw new Error("TRIANGULAR_ROUTE_CONTROLLER_ADDRESS is required");

  const routeCandidates = parseRouteCandidates();
  const singleTokenX = normalizeAddress(optionalEnv("TRIANGULAR_TOKEN_X"));
  const singleTokenY = normalizeAddress(optionalEnv("TRIANGULAR_TOKEN_Y"));
  if (routeCandidates.length === 0) {
    if (!singleTokenX) throw new Error("TRIANGULAR_TOKEN_X is required");
    if (!singleTokenY) throw new Error("TRIANGULAR_TOKEN_Y is required");
  }

  const latest = await hre.ethers.provider.getBlock("latest");
  const candidateTokens = routeCandidates.length > 0
    ? routeCandidates.flatMap((candidate, index) => {
      const tokenX = normalizeAddress(candidate.tokenX);
      const tokenY = normalizeAddress(candidate.tokenY);
      if (!tokenX) throw new Error(`tokenX is required for route candidate ${index}`);
      if (!tokenY) throw new Error(`tokenY is required for route candidate ${index}`);
      return [tokenX, tokenY];
    })
    : [singleTokenX, singleTokenY];
  const uniqueCandidateTokens = [...new Set(candidateTokens.map((item) => hre.ethers.getAddress(item)))];
  const networkName = hre.network.name || "unknown";

  const controller = await hre.ethers.getContractAt("TriangularRouteController", controllerAddress);
  const ownerGate = await ownerMatchesSigner(hre, controller, process.env);
  const paths = evidencePaths({ strategy: `${networkName}-triangular-ab-execute` });

  let preview = { ok: false };
  try {
    const result = await controller.previewBestRoute.staticCall(uniqueCandidateTokens);
    preview = { ...decisionReport(result[1]), bestPairIndex: result[0].toString(), candidateTokens: uniqueCandidateTokens.length };
  } catch (error) {
    preview = { ok: false, error: sanitizeError(error) };
  }

  let staticCall = { ok: false };
  try {
    await controller.run.staticCall(uniqueCandidateTokens);
    const gasEstimate = await controller.run.estimateGas(uniqueCandidateTokens);
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
    payloadFresh: latest.timestamp > 0,
    minProfitChecked: true,
  });

  let receipt = null;
  let broadcast = { requested: boolEnv(process.env, "TRIANGULAR_AB_BROADCAST_ENABLED"), sent: false };
  if (gate.ready) {
    const tx = await controller.run(uniqueCandidateTokens);
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
    candidateTokens: uniqueCandidateTokens,
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
