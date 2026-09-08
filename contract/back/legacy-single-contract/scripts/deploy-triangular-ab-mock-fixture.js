const hre = require("hardhat");
const fs = require("fs");
const path = require("path");
const {
  appendJsonl,
  evidencePaths,
  networkContext,
  receiptReport,
  sanitizeError,
  toJsonValue,
  writeJson,
} = require("./fuji-evidence");

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
    failureReason: ({
      0: "none",
      101: "not_enough_valid_pools",
      102: "no_price_spread",
    })[failureCode] || `unknown_failure_${failureCode}`,
  };
}

function emptyRuntimePools() {
  return Array.from({ length: 10 }, () => ({
    adapterKind: 0n,
    pool: hre.ethers.ZeroAddress,
  }));
}

function runtimePools(entries) {
  const pools = emptyRuntimePools();
  for (const [index, pool, adapterKind = 1n] of entries) {
    pools[index] = { adapterKind, pool };
  }
  return pools;
}

async function deployContract(name, ...args) {
  console.log(`deploying ${name}...`);
  const Factory = await hre.ethers.getContractFactory(name);
  const deployment = await Factory.getDeployTransaction(...args);
  const tx = await sendRaw({ ...deployment, gasLimit: 6_000_000n });
  console.log(`${name} tx=${tx.hash}`);
  const receipt = await waitForReceipt(tx.hash);
  if (!receipt.contractAddress) throw new Error(`failed to deploy ${name}`);
  console.log(`${name} address=${receipt.contractAddress}`);
  return Factory.attach(receipt.contractAddress);
}

let rawSigner = null;
let nextNonce = null;
let rawChainId = null;
const GAS_PRICE_CAP = 30_000_000_000n;

function optionalEnv(...names) {
  for (const name of names) {
    const value = process.env[name];
    if (value && value.trim() && value !== "0x...") {
      return value.trim();
    }
  }
  return "";
}

function envBigInt(name, defaultValue) {
  const value = optionalEnv(name);
  return value ? BigInt(value) : defaultValue;
}

function envBool(name, defaultValue) {
  const value = optionalEnv(name);
  if (!value) return defaultValue;
  return ["1", "true", "yes", "on"].includes(value.trim().toLowerCase());
}

async function sendRaw(tx) {
  if (!rawSigner) {
    const privateKey = optionalEnv("DEPLOYER_PRIVATE_KEY", "LIQUIDATION_EXECUTION_PRIVATE_KEY", "COW_ORDER_SIGNER_PRIVATE_KEY");
    if (!privateKey) throw new Error("DEPLOYER_PRIVATE_KEY or fallback signer key is required");
    rawSigner = new hre.ethers.Wallet(privateKey, hre.ethers.provider);
    rawChainId = (await hre.ethers.provider.getNetwork()).chainId;
    nextNonce = await hre.ethers.provider.getTransactionCount(rawSigner.address, "latest");
    console.log(`rawSender=${rawSigner.address} nonce=${nextNonce} chainId=${rawChainId}`);
  }
  const networkGasPriceRaw = await hre.ethers.provider.send("eth_gasPrice", []);
  const networkGasPrice = BigInt(networkGasPriceRaw);
  const request = {
    ...tx,
    nonce: nextNonce,
    gasPrice: networkGasPrice < GAS_PRICE_CAP ? networkGasPrice : GAS_PRICE_CAP,
    chainId: rawChainId,
  };
  delete request.from;
  const signed = await rawSigner.signTransaction(request);
  const hash = await hre.ethers.provider.send("eth_sendRawTransaction", [signed]);
  nextNonce += 1;
  return { hash };
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

async function sendCall(contract, functionName, args, gasLimit = 800_000n) {
  const data = contract.interface.encodeFunctionData(functionName, args);
  const tx = await sendRaw({
    to: await contract.getAddress(),
    data,
    gasLimit,
  });
  return waitForReceipt(tx.hash);
}

async function staticCallLatest(contract, functionName, args) {
  const data = contract.interface.encodeFunctionData(functionName, args);
  const raw = await hre.ethers.provider.send("eth_call", [{
    from: rawSigner ? rawSigner.address : undefined,
    to: await contract.getAddress(),
    data,
  }, "latest"]);
  return contract.interface.decodeFunctionResult(functionName, raw);
}

async function estimateGasLatest(contract, functionName, args) {
  const data = contract.interface.encodeFunctionData(functionName, args);
  const raw = await hre.ethers.provider.send("eth_estimateGas", [{
    from: rawSigner ? rawSigner.address : undefined,
    to: await contract.getAddress(),
    data,
  }]);
  return BigInt(raw);
}

async function deployV3Pool({ factory, token0, token1, tick, liquidity = 1_000_000n, fee = 3000n }) {
  const sqrtPriceX96 = 1n << 96n;
  const pool = await deployContract("MockV3Pool", factory, token0, token1, fee, liquidity, sqrtPriceX96, tick);
  return {
    contract: pool,
    address: await pool.getAddress(),
    factory,
    token0,
    token1,
    fee: fee.toString(),
    liquidity: liquidity.toString(),
    tick: tick.toString(),
  };
}

function pairPlan(index) {
  if (index === 0) {
    return {
      label: "pair-1",
      tokenXName: "Mock Token X A",
      tokenXSymbol: "mXA",
      tokenYName: "Mock Token Y A",
      tokenYSymbol: "mYA",
      ticks: { low: -20n, middle: -5n, high: -15n, shallow: -900n },
      liquidity: { low: 2_000_000n, middle: 2_000_000n, high: 3_000_000n, shallow: 1n },
    };
  }
  return {
    label: `pair-${index + 1}`,
    tokenXName: `Mock Token X ${index + 1}`,
    tokenXSymbol: `mX${index + 1}`,
    tokenYName: `Mock Token Y ${index + 1}`,
    tokenYSymbol: `mY${index + 1}`,
    ticks: { low: -250n, middle: 25n, high: -500n, shallow: -900n },
    liquidity: { low: 2_000_000n, middle: 3_000_000n, high: 4_000_000n, shallow: 1n },
  };
}

async function main() {
  const networkName = hre.network.name || "unknown";
  const [deployer] = await hre.ethers.getSigners();
  const paths = evidencePaths({ strategy: `${networkName}-triangular-ab-runtime-mock-fixture` });
  console.log(`deployer=${deployer.address}`);

  const usdc = await deployContract("TestERC20", "Mock USD Coin", "mUSDC", 6, deployer.address);
  const pool = await deployContract("MockAavePool", 5);
  const router = await deployContract("MockV3SwapRouter", deployer.address);
  const executor = await deployContract("AaveTriangularExecutor", await pool.getAddress(), await usdc.getAddress(), deployer.address);
  const controller = await deployContract("TriangularRouteController", await usdc.getAddress(), await executor.getAddress(), deployer.address);
  const pairCount = Math.max(1, Math.min(Number(process.env.TRIANGULAR_MOCK_PAIR_COUNT || "1"), 5));

  const controllerAddress = await controller.getAddress();
  const executorAddress = await executor.getAddress();
  const usdcAddress = await usdc.getAddress();
  const mockAavePoolAddress = await pool.getAddress();
  const mockRouterAddress = await router.getAddress();
  const mockFactoryAddress = deployer.address;

  await sendCall(controller, "setAdapterConfig", [1n, true, mockFactoryAddress, mockRouterAddress, mockRouterAddress]);
  await sendCall(controller, "setRuntimeRiskConfig", [100n, 100n]);
  await sendCall(executor, "setController", [controllerAddress]);
  const profitSweepEnabled = envBool("TRIANGULAR_PROFIT_SWEEP_ENABLED", true);
  const profitSweepThreshold = envBigInt("TRIANGULAR_PROFIT_SWEEP_THRESHOLD_USDC", 1n);
  const profitReserve = envBigInt("TRIANGULAR_PROFIT_RESERVE_USDC", 0n);
  await sendCall(executor, "setProfitSweepEnabled", [profitSweepEnabled]);
  await sendCall(executor, "setProfitSweepThresholdUsdc", [profitSweepThreshold]);
  await sendCall(executor, "setProfitReserveUsdc", [profitReserve]);
  await sendCall(usdc, "mint", [mockAavePoolAddress, 10_000_000n]);
  await sendCall(usdc, "mint", [mockRouterAddress, 10_000_000n]);
  const pairFixtures = [];
  for (let index = 0; index < pairCount; index += 1) {
    const plan = pairPlan(index);
    const tokenX = await deployContract("TestERC20", plan.tokenXName, plan.tokenXSymbol, 18, deployer.address);
    const tokenY = await deployContract("TestERC20", plan.tokenYName, plan.tokenYSymbol, 18, deployer.address);
    const tokenXAddress = await tokenX.getAddress();
    const tokenYAddress = await tokenY.getAddress();

    await sendCall(router, "setRate", [usdcAddress, tokenXAddress, 1001n, 1000n]);
    await sendCall(router, "setRate", [tokenXAddress, tokenYAddress, 1001n, 1000n]);
    await sendCall(router, "setRate", [tokenYAddress, usdcAddress, 1001n, 1000n]);

    const low = await deployV3Pool({
      factory: mockFactoryAddress,
      token0: tokenXAddress,
      token1: tokenYAddress,
      tick: plan.ticks.low,
      liquidity: plan.liquidity.low,
    });
    const middle = await deployV3Pool({
      factory: mockFactoryAddress,
      token0: tokenXAddress,
      token1: tokenYAddress,
      tick: plan.ticks.middle,
      liquidity: plan.liquidity.middle,
    });
    const highReversed = await deployV3Pool({
      factory: mockFactoryAddress,
      token0: tokenYAddress,
      token1: tokenXAddress,
      tick: plan.ticks.high,
      liquidity: plan.liquidity.high,
    });
    const tooShallow = await deployV3Pool({
      factory: mockFactoryAddress,
      token0: tokenXAddress,
      token1: tokenYAddress,
      tick: plan.ticks.shallow,
      liquidity: plan.liquidity.shallow,
    });

    const runtimeTrade = {
      tradeIndex: BigInt(index),
      tokenX: tokenXAddress,
      tokenY: tokenYAddress,
      pools: runtimePools([
        [0, middle.address],
        [1, tooShallow.address],
        [3, highReversed.address],
        [9, low.address],
      ]),
    };
    const individualPreviewResult = await staticCallLatest(controller, "previewFirstProfitableRuntimeExecution", [[runtimeTrade], {
      amount: 1_000_000n,
      deadline: BigInt(Math.floor(Date.now() / 1000) + 600),
      amountOutMinUsdc: 1_000_000n,
      minProfitUsdc: 1n,
      usdcToTokenXFee: 3000n,
      tokenYToUsdcFee: 3000n,
    }]);
    pairFixtures.push({
      index,
      label: plan.label,
      tokenXAddress,
      tokenYAddress,
      low,
      middle,
      highReversed,
      tooShallow,
      runtimeTrade,
      individualPreview: {
        found: Boolean(individualPreviewResult[0]),
        selectedTradeArrayIndex: individualPreviewResult[1].toString(),
        decision: decisionReport(individualPreviewResult[2]),
        executionPreview: {
          router: individualPreviewResult[3].router,
          quotedFinalUsdc: individualPreviewResult[3].quotedFinalUsdc.toString(),
          premiumUsdc: individualPreviewResult[3].premiumUsdc.toString(),
          requiredFinalUsdc: individualPreviewResult[3].requiredFinalUsdc.toString(),
          protectedAmountOutMinUsdc: individualPreviewResult[3].protectedAmountOutMinUsdc.toString(),
          minProfitUsdc: individualPreviewResult[3].minProfitUsdc.toString(),
        },
      },
    });
  }

  const runtimeTrades = pairFixtures.map((fixture) => fixture.runtimeTrade);
  const tokenXAddress = pairFixtures[0].tokenXAddress;
  const tokenYAddress = pairFixtures[0].tokenYAddress;
  const low = pairFixtures[0].low;
  const middle = pairFixtures[0].middle;
  const highReversed = pairFixtures[0].highReversed;
  const tooShallow = pairFixtures[0].tooShallow;

  const previewParams = {
    amount: 1_000_000n,
    deadline: BigInt(Math.floor(Date.now() / 1000) + 600),
    amountOutMinUsdc: 1_000_000n,
    minProfitUsdc: 1n,
    usdcToTokenXFee: 3000n,
    tokenYToUsdcFee: 3000n,
  };
  const previewResult = await staticCallLatest(controller, "previewFirstProfitableRuntimeExecution", [runtimeTrades, previewParams]);
  const preview = {
    found: Boolean(previewResult[0]),
    selectedTradeArrayIndex: previewResult[1].toString(),
    decision: decisionReport(previewResult[2]),
    executionPreview: {
      router: previewResult[3].router,
      quotedFinalUsdc: previewResult[3].quotedFinalUsdc.toString(),
      premiumUsdc: previewResult[3].premiumUsdc.toString(),
      requiredFinalUsdc: previewResult[3].requiredFinalUsdc.toString(),
      protectedAmountOutMinUsdc: previewResult[3].protectedAmountOutMinUsdc.toString(),
      minProfitUsdc: previewResult[3].minProfitUsdc.toString(),
    },
  };
  await staticCallLatest(controller, "runFirstProfitableRuntimeTradesAndExecute", [runtimeTrades, previewParams]);
  const selectionGasEstimate = await estimateGasLatest(controller, "runFirstProfitableRuntimeTradesAndExecute", [runtimeTrades, previewParams]);

  const executionParams = {
    amount: 1_000_000n,
    deadline: BigInt(Math.floor(Date.now() / 1000) + 600),
    amountOutMinUsdc: 1_000_000n,
    minProfitUsdc: 1n,
    usdcToTokenXFee: 3000n,
    tokenYToUsdcFee: 3000n,
  };
  const executionStaticResult = await staticCallLatest(
    controller,
    "runFirstProfitableRuntimeTradesAndExecute",
    [runtimeTrades, executionParams],
  );
  const executionGasEstimate = await estimateGasLatest(
    controller,
    "runFirstProfitableRuntimeTradesAndExecute",
    [runtimeTrades, executionParams],
  );
  const executionReceipt = await sendCall(
    controller,
    "runFirstProfitableRuntimeTradesAndExecute",
    [runtimeTrades, executionParams],
    executionGasEstimate + 100_000n,
  );

  const outputDir = path.join(process.cwd(), "deployments");
  const deploymentPath = path.join(outputDir, `${networkName}-triangular-ab-mock-fixture.json`);
  const output = {
    runId: paths.runId,
    network: networkName,
    chainId: Number((await hre.ethers.provider.getNetwork()).chainId),
    deployedAt: new Date().toISOString(),
    deployer: deployer.address,
    usdcAddress,
    tokenXAddress,
    tokenYAddress,
    pairCount,
    mockAavePoolAddress,
    mockRouterAddress,
    mockFactoryAddress,
    triangularRouteControllerAddress: controllerAddress,
    aaveTriangularExecutorAddress: executorAddress,
    adapterConfig: {
      adapterKind: "1",
      factory: mockFactoryAddress,
      router: mockRouterAddress,
      quoter: mockRouterAddress,
    },
    runtimeRiskConfig: {
      minPoolLiquidity: "100",
      minTickDelta: "100",
    },
    profitSweep: {
      enabled: profitSweepEnabled,
      thresholdUsdc: profitSweepThreshold.toString(),
      reserveUsdc: profitReserve.toString(),
      recipient: deployer.address,
    },
    mockPairs: pairFixtures.map((fixture) => ({
      index: fixture.index,
      label: fixture.label,
      tokenXAddress: fixture.tokenXAddress,
      tokenYAddress: fixture.tokenYAddress,
      preview: fixture.individualPreview,
      mockPools: [fixture.low, fixture.middle, fixture.highReversed, fixture.tooShallow].map(({ contract, ...item }) => item),
    })),
    mockPools: [low, middle, highReversed, tooShallow].map(({ contract, ...item }) => item),
    runtimeTrades,
    pairPreviews: pairFixtures.map((fixture) => fixture.individualPreview),
    preview,
    staticCall: {
      ok: true,
      gasEstimate: selectionGasEstimate.toString(),
    },
    txHash: executionReceipt.hash,
    receipt: receiptReport(executionReceipt),
    execution: {
      params: {
      amount: executionParams.amount.toString(),
      deadline: executionParams.deadline.toString(),
      amountOutMinUsdc: executionParams.amountOutMinUsdc.toString(),
      minProfitUsdc: executionParams.minProfitUsdc.toString(),
      usdcToTokenXFee: executionParams.usdcToTokenXFee.toString(),
      tokenYToUsdcFee: executionParams.tokenYToUsdcFee.toString(),
    },
    staticCall: {
      ok: true,
      selectedTradeArrayIndex: executionStaticResult[0].toString(),
      decision: decisionReport(executionStaticResult[1]),
      profitSwept: executionStaticResult[2].toString(),
    },
      gasEstimate: executionGasEstimate.toString(),
      txHash: executionReceipt.hash,
      receipt: receiptReport(executionReceipt),
    },
  };

  fs.mkdirSync(outputDir, { recursive: true });
  fs.writeFileSync(deploymentPath, `${JSON.stringify(toJsonValue(output), null, 2)}\n`);
  writeJson(paths.reportPath, { ...output, context: await networkContext(hre, process.env), deploymentPath });
  appendJsonl(paths.tradeLogPath, {
    runId: paths.runId,
    observedAt: output.deployedAt,
    network: hre.network.name,
    strategy: "triangular_ab_runtime_mock_fixture",
    action: "deploy-preview-static-broadcast",
    success: true,
    deploymentPath,
    reportPath: paths.reportPath,
    txHash: executionReceipt.hash,
    controllerAddress,
  });

  console.log(JSON.stringify({
    ok: true,
    deploymentPath,
    reportPath: paths.reportPath,
    txHash: executionReceipt.hash,
    preview,
    staticCall: output.staticCall,
    execution: output.execution,
  }, null, 2));
}

if (require.main === module) {
  main().catch((error) => {
    console.error(JSON.stringify({ ok: false, error: sanitizeError(error) }, null, 2));
    process.exitCode = 1;
  });
}
