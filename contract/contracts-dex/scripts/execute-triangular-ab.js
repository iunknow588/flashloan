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

function envRuntimeTrades() {
  const value = optionalEnv("TRIANGULAR_RUNTIME_TRADES_JSON");
  if (!value) {
    throw new Error("TRIANGULAR_RUNTIME_TRADES_JSON is required");
  }
  const parsed = JSON.parse(value);
  if (!Array.isArray(parsed)) {
    throw new Error("TRIANGULAR_RUNTIME_TRADES_JSON must be a JSON array");
  }
  if (parsed.length === 0 || parsed.length > 16) {
    throw new Error("TRIANGULAR_RUNTIME_TRADES_JSON must include 1 to 16 trades");
  }
  return parsed.map(normalizeRuntimeTrade);
}

function normalizeRuntimeTrade(trade, tradeArrayIndex) {
  if (!trade || typeof trade !== "object") {
    throw new Error(`runtimeTrades[${tradeArrayIndex}] must be an object`);
  }
  const tokenX = normalizeAddress(trade.tokenX || trade.token_x);
  const tokenY = normalizeAddress(trade.tokenY || trade.token_y);
  if (!tokenX || !tokenY) {
    throw new Error(`runtimeTrades[${tradeArrayIndex}] tokenX/tokenY must be valid addresses`);
  }
  const inputPools = trade.pools || trade.candidatePools || trade.candidate_pools;
  if (!Array.isArray(inputPools) || inputPools.length === 0 || inputPools.length > 10) {
    throw new Error(`runtimeTrades[${tradeArrayIndex}] pools must include 1 to 10 items`);
  }
  const pools = Array.from({ length: 10 }, () => ({ adapterKind: 0n, pool: hre.ethers.ZeroAddress }));
  inputPools.forEach((pool, poolIndex) => {
    if (!pool || typeof pool !== "object") {
      throw new Error(`runtimeTrades[${tradeArrayIndex}].pools[${poolIndex}] must be an object`);
    }
    const poolAddress = normalizeAddress(pool.pool);
    if (!poolAddress) {
      throw new Error(`runtimeTrades[${tradeArrayIndex}].pools[${poolIndex}].pool must be a valid address`);
    }
    pools[poolIndex] = {
      adapterKind: BigInt(pool.adapterKind ?? pool.adapter_kind ?? 1),
      pool: poolAddress,
    };
  });
  return {
    tradeIndex: BigInt(trade.tradeIndex ?? trade.trade_index ?? tradeArrayIndex),
    tokenX,
    tokenY,
    pools,
  };
}

function decisionReport(result) {
  const failureCode = result[16] ? Number(result[16]) : 0;
  return {
    ok: Boolean(result[0]),
    viable: Boolean(result[0]),
    tradeIndex: result[1].toString(),
    tokenX: result[2],
    tokenY: result[3],
    lowPool: result[4],
    highPool: result[5],
    adapterKind: result[6].toString(),
    lowFee: result[7].toString(),
    highFee: result[8].toString(),
    lowLiquidity: result[9].toString(),
    highLiquidity: result[10].toString(),
    lowNormalizedTick: result[11].toString(),
    highNormalizedTick: result[12].toString(),
    tickDelta: result[13].toString(),
    scannedPoolCount: result[14].toString(),
    validPoolCount: result[15].toString(),
    failureCode: failureCode.toString(),
    failureReason: runtimeFailureReason(failureCode),
  };
}

function runtimeFailureReason(code) {
  return ({
    0: "none",
    101: "not_enough_valid_pools",
    102: "no_price_spread",
  })[Number(code)] || `unknown_failure_${code}`;
}

async function staticCallLatest(contract, functionName, args, from) {
  const data = contract.interface.encodeFunctionData(functionName, args);
  const raw = await hre.ethers.provider.send("eth_call", [{
    ...(from ? { from } : {}),
    to: await contract.getAddress(),
    data,
  }, "latest"]);
  return contract.interface.decodeFunctionResult(functionName, raw);
}

async function estimateGasLatest(contract, functionName, args, from) {
  const data = contract.interface.encodeFunctionData(functionName, args);
  const raw = await hre.ethers.provider.send("eth_estimateGas", [{
    ...(from ? { from } : {}),
    to: await contract.getAddress(),
    data,
  }]);
  return BigInt(raw);
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function normalizeReceipt(receipt) {
  if (!receipt) return null;
  return {
    ...receipt,
    hash: receipt.transactionHash,
    blockNumber: receipt.blockNumber ? Number(BigInt(receipt.blockNumber)) : null,
    status: receipt.status ? Number(BigInt(receipt.status)) : null,
    gasUsed: receipt.gasUsed ? BigInt(receipt.gasUsed).toString() : null,
  };
}

async function waitForReceipt(hash, timeoutMs = 180_000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const receipt = await hre.ethers.provider.send("eth_getTransactionReceipt", [hash]);
    if (receipt) return normalizeReceipt(receipt);
    await sleep(2_000);
  }
  throw new Error(`timed out waiting for receipt ${hash}`);
}

async function sendRawControllerCall(contract, functionName, args, gasLimit) {
  const privateKey = optionalEnv("DEPLOYER_PRIVATE_KEY", "LIQUIDATION_EXECUTION_PRIVATE_KEY", "COW_ORDER_SIGNER_PRIVATE_KEY");
  if (!privateKey) throw new Error("DEPLOYER_PRIVATE_KEY or fallback signer key is required");
  const wallet = new hre.ethers.Wallet(privateKey, hre.ethers.provider);
  const network = await hre.ethers.provider.getNetwork();
  const nonce = await hre.ethers.provider.getTransactionCount(wallet.address, "latest");
  const signed = await wallet.signTransaction({
    to: await contract.getAddress(),
    data: contract.interface.encodeFunctionData(functionName, args),
    nonce,
    gasPrice: 30_000_000_000n,
    gasLimit,
    chainId: network.chainId,
  });
  const hash = await hre.ethers.provider.send("eth_sendRawTransaction", [signed]);
  return waitForReceipt(hash);
}

async function main() {
  requireAnyEnv("FUJI_RPC_URL", "AVALANCHE_FUJI_RPC_URL", "AVALANCHE_RPC_URL", "AVALANCHE_RPC");

  const controllerAddress = normalizeAddress(optionalEnv("TRIANGULAR_ROUTE_CONTROLLER_ADDRESS", "TRIANGULAR_CONTROLLER_ADDRESS"));
  if (!controllerAddress) throw new Error("TRIANGULAR_ROUTE_CONTROLLER_ADDRESS is required");
  const runtimeTrades = envRuntimeTrades();

  const latest = await hre.ethers.provider.getBlock("latest");
  const networkName = hre.network.name || "unknown";

  const controller = await hre.ethers.getContractAt("TriangularRouteController", controllerAddress);
  const ownerGate = await ownerMatchesSigner(hre, controller, process.env);
  const paths = evidencePaths({ strategy: `${networkName}-triangular-ab-execute` });

  let preview = { ok: false };
  try {
    const result = await staticCallLatest(controller, "previewBestRuntimeTrades", [runtimeTrades], ownerGate.signer);
    preview = { bestTradeArrayIndex: result[0].toString(), decision: decisionReport(result[1]) };
  } catch (error) {
    preview = { ok: false, error: sanitizeError(error) };
  }

  let staticCall = { ok: false };
  try {
    await staticCallLatest(controller, "runBestRuntimeTrades", [runtimeTrades], ownerGate.signer);
    const gasEstimate = await estimateGasLatest(controller, "runBestRuntimeTrades", [runtimeTrades], ownerGate.signer);
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
    const gasLimit = staticCall.gasEstimate ? BigInt(staticCall.gasEstimate) + 50_000n : 3_000_000n;
    receipt = await sendRawControllerCall(controller, "runBestRuntimeTrades", [runtimeTrades], gasLimit);
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
    runtimeTrades,
    blockNumber: latest.number,
    blockTimestamp: latest.timestamp,
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
    runtimeTrades,
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
