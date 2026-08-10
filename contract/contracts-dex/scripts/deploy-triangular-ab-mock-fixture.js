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
const GAS_PRICE = 30_000_000_000n;

function optionalEnv(...names) {
  for (const name of names) {
    const value = process.env[name];
    if (value && value.trim() && value !== "0x...") {
      return value.trim();
    }
  }
  return "";
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
  const request = {
    ...tx,
    nonce: nextNonce,
    gasPrice: GAS_PRICE,
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

async function main() {
  const networkName = hre.network.name || "unknown";
  const [deployer] = await hre.ethers.getSigners();
  const paths = evidencePaths({ strategy: `${networkName}-triangular-ab-runtime-mock-fixture` });
  console.log(`deployer=${deployer.address}`);

  const usdc = await deployContract("TestERC20", "Mock USD Coin", "mUSDC", 6, deployer.address);
  const tokenX = await deployContract("TestERC20", "Mock Token X", "mX", 18, deployer.address);
  const tokenY = await deployContract("TestERC20", "Mock Token Y", "mY", 18, deployer.address);
  const pool = await deployContract("MockAavePool", 5);
  const executor = await deployContract("AaveTriangularExecutor", await pool.getAddress(), await usdc.getAddress(), deployer.address);
  const controller = await deployContract("TriangularRouteController", await usdc.getAddress(), await executor.getAddress(), deployer.address);

  const controllerAddress = await controller.getAddress();
  const executorAddress = await executor.getAddress();
  const usdcAddress = await usdc.getAddress();
  const tokenXAddress = await tokenX.getAddress();
  const tokenYAddress = await tokenY.getAddress();
  const mockAavePoolAddress = await pool.getAddress();
  const mockFactoryAddress = deployer.address;

  await sendCall(controller, "setAdapterConfig", [1n, true, mockFactoryAddress, hre.ethers.ZeroAddress, hre.ethers.ZeroAddress]);
  await sendCall(controller, "setRuntimeRiskConfig", [100n, 100n]);
  await sendCall(executor, "setController", [controllerAddress]);

  const low = await deployV3Pool({
    factory: mockFactoryAddress,
    token0: tokenXAddress,
    token1: tokenYAddress,
    tick: -250n,
    liquidity: 2_000_000n,
  });
  const middle = await deployV3Pool({
    factory: mockFactoryAddress,
    token0: tokenXAddress,
    token1: tokenYAddress,
    tick: 25n,
    liquidity: 3_000_000n,
  });
  const highReversed = await deployV3Pool({
    factory: mockFactoryAddress,
    token0: tokenYAddress,
    token1: tokenXAddress,
    tick: -500n,
    liquidity: 4_000_000n,
  });
  const tooShallow = await deployV3Pool({
    factory: mockFactoryAddress,
    token0: tokenXAddress,
    token1: tokenYAddress,
    tick: -900n,
    liquidity: 1n,
  });

  const runtimeTrades = [{
    tradeIndex: 0n,
    tokenX: tokenXAddress,
    tokenY: tokenYAddress,
    pools: runtimePools([
      [0, middle.address],
      [1, tooShallow.address],
      [3, highReversed.address],
      [9, low.address],
    ]),
  }];

  const previewResult = await staticCallLatest(controller, "previewBestRuntimeTrades", [runtimeTrades]);
  const preview = {
    bestTradeArrayIndex: previewResult[0].toString(),
    decision: decisionReport(previewResult[1]),
  };
  await staticCallLatest(controller, "runBestRuntimeTrades", [runtimeTrades]);
  const gasEstimate = await estimateGasLatest(controller, "runBestRuntimeTrades", [runtimeTrades]);
  const receipt = await sendCall(controller, "runBestRuntimeTrades", [runtimeTrades], 3_000_000n);

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
    mockAavePoolAddress,
    mockFactoryAddress,
    triangularRouteControllerAddress: controllerAddress,
    aaveTriangularExecutorAddress: executorAddress,
    adapterConfig: {
      adapterKind: "1",
      factory: mockFactoryAddress,
      router: hre.ethers.ZeroAddress,
      quoter: hre.ethers.ZeroAddress,
    },
    runtimeRiskConfig: {
      minPoolLiquidity: "100",
      minTickDelta: "100",
    },
    mockPools: [low, middle, highReversed, tooShallow].map(({ contract, ...item }) => item),
    runtimeTrades,
    preview,
    staticCall: {
      ok: true,
      gasEstimate: gasEstimate.toString(),
    },
    txHash: receipt.hash,
    receipt: receiptReport(receipt),
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
    txHash: receipt.hash,
    controllerAddress,
  });

  console.log(JSON.stringify({
    ok: true,
    deploymentPath,
    reportPath: paths.reportPath,
    txHash: receipt.hash,
    preview,
    staticCall: output.staticCall,
  }, null, 2));
}

if (require.main === module) {
  main().catch((error) => {
    console.error(JSON.stringify({ ok: false, error: sanitizeError(error) }, null, 2));
    process.exitCode = 1;
  });
}
