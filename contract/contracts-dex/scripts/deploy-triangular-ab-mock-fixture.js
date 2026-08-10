const hre = require("hardhat");
const fs = require("fs");
const path = require("path");
const {
  appendJsonl,
  evidencePaths,
  networkContext,
  receiptReport,
  sanitizeError,
  writeJson,
} = require("./fuji-evidence");

const UNIT = 1_000_000n;

function decisionReport(result) {
  return {
    viable: Boolean(result[0]),
    reverse: Boolean(result[1]),
    quotedFinalUsdc: result[2].toString(),
    profitUsdc: result[3].toString(),
    path: result[4],
    edgeBps: result[5].toString(),
    requiredEdgeBps: result[6].toString(),
    directComparableAmount: result[7].toString(),
    viaComparableAmount: result[8].toString(),
    failureCode: result[9].toString(),
    requiredFinalUsdc: result[10].toString(),
    minAfterSlippageUsdc: result[11].toString(),
    amountOutMinUsdc: result[12].toString(),
    selectedAmount: result[13].toString(),
    routeMaxBorrow: result[14].toString(),
    probeAmount: result[15].toString(),
    probeProfitUsdc: result[16].toString(),
    fundingCostUsdc: result[17].toString(),
  };
}

async function deployContract(name, ...args) {
  const Factory = await hre.ethers.getContractFactory(name);
  const deployment = await Factory.getDeployTransaction(...args);
  const tx = await sendRaw({ ...deployment, gasLimit: 6_000_000n });
  const receipt = await tx.wait();
  if (!receipt.contractAddress) throw new Error(`failed to deploy ${name}`);
  return Factory.attach(receipt.contractAddress);
}

let rawSigner = null;
let nextNonce = null;
const GAS_PRICE = 30_000_000_000n;

async function sendRaw(tx) {
  if (!rawSigner) {
    [rawSigner] = await hre.ethers.getSigners();
    nextNonce = await hre.ethers.provider.getTransactionCount(rawSigner.address, "latest");
  }
  const sent = await rawSigner.sendTransaction({
    ...tx,
    nonce: nextNonce,
    gasPrice: GAS_PRICE,
  });
  nextNonce += 1;
  return sent;
}

async function sendCall(contract, functionName, args, gasLimit = 800_000n) {
  const data = contract.interface.encodeFunctionData(functionName, args);
  const tx = await sendRaw({
    to: await contract.getAddress(),
    data,
    gasLimit,
  });
  return tx.wait();
}

async function main() {
  const networkName = hre.network.name || "unknown";
  const [deployer] = await hre.ethers.getSigners();
  const paths = evidencePaths({ strategy: `${networkName}-triangular-ab-mock-fixture` });
  console.log(`deployer=${deployer.address}`);

  const usdc = await deployContract("TestERC20", "Mock USD Coin", "mUSDC", 6, deployer.address);
  const tokenX = await deployContract("TestERC20", "Mock Token X", "mX", 6, deployer.address);
  const tokenY = await deployContract("TestERC20", "Mock Token Y", "mY", 6, deployer.address);
  const pool = await deployContract("MockAavePool", 5);
  const router = await deployContract("MockSwapRouter", deployer.address);
  const executor = await deployContract("AaveTriangularExecutor", await pool.getAddress(), await usdc.getAddress(), deployer.address);
  const controller = await deployContract("TriangularRouteController", await usdc.getAddress(), await executor.getAddress(), deployer.address);

  const poolAddress = await pool.getAddress();
  const routerAddress = await router.getAddress();
  const controllerAddress = await controller.getAddress();
  const executorAddress = await executor.getAddress();
  const usdcAddress = await usdc.getAddress();
  const tokenXAddress = await tokenX.getAddress();
  const tokenYAddress = await tokenY.getAddress();

  const borrowAmount = 1_000n * UNIT;
  const minProfitUsdc = 100n * UNIT;
  const minBorrowAmount = 1_000n * UNIT;
  const maxBorrowAmount = 2_000n * UNIT;
  const amountSearchSteps = 6n;
  const slippageBps = 50n;
  const maxRouteSlippageBps = 5000n;
  const deadlineSeconds = 3600n;

  await sendCall(controller, "setExecutionConfig", [routerAddress, borrowAmount, minProfitUsdc, deadlineSeconds, slippageBps]);
  await sendCall(controller, "setAmountSearchConfig", [minBorrowAmount, maxBorrowAmount, amountSearchSteps, maxRouteSlippageBps]);
  await sendCall(executor, "setController", [controllerAddress]);

  const largeLiquidity = 10_000_000n * UNIT;
  await sendCall(usdc, "mint", [poolAddress, largeLiquidity]);
  for (const token of [usdc, tokenX, tokenY]) {
    await sendCall(token, "mint", [routerAddress, largeLiquidity]);
  }

  await sendCall(router, "setRate", [usdcAddress, tokenXAddress, 1n, 1n]);
  await sendCall(router, "setRate", [tokenXAddress, tokenYAddress, 1n, 1n]);
  await sendCall(router, "setRate", [tokenYAddress, usdcAddress, 2n, 1n]);
  await sendCall(router, "setImpactBpsPerUnit", [tokenYAddress, usdcAddress, 2n]);

  await sendCall(router, "setRate", [usdcAddress, tokenYAddress, 1n, 2n]);
  await sendCall(router, "setRate", [tokenYAddress, tokenXAddress, 1n, 1n]);
  await sendCall(router, "setRate", [tokenXAddress, usdcAddress, 1n, 1n]);

  const candidates = [tokenXAddress, tokenYAddress];
  const previewResult = await controller.previewBestRoute.staticCall(candidates);
  const preview = {
    bestPairIndex: previewResult[0].toString(),
    decision: decisionReport(previewResult[1]),
  };
  await controller.run.staticCall(candidates);
  const receipt = await sendCall(controller, "run", [candidates], 5_000_000n);

  const balances = {
    poolUsdc: (await usdc.balanceOf(poolAddress)).toString(),
    executorUsdc: (await usdc.balanceOf(executorAddress)).toString(),
    controllerUsdc: (await usdc.balanceOf(controllerAddress)).toString(),
    ownerUsdc: (await usdc.balanceOf(deployer.address)).toString(),
  };

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
    mockAavePoolAddress: poolAddress,
    mockRouterAddress: routerAddress,
    triangularRouteControllerAddress: controllerAddress,
    aaveTriangularExecutorAddress: executorAddress,
    dynamicAmountConfig: {
      minBorrowAmount: minBorrowAmount.toString(),
      maxBorrowAmount: maxBorrowAmount.toString(),
      amountSearchSteps: amountSearchSteps.toString(),
      maxRouteSlippageBps: maxRouteSlippageBps.toString(),
    },
    preview,
    txHash: receipt.hash,
    receipt: receiptReport(receipt),
    balances,
  };

  fs.mkdirSync(outputDir, { recursive: true });
  fs.writeFileSync(deploymentPath, `${JSON.stringify(output, null, 2)}\n`);
  writeJson(paths.reportPath, { ...output, context: await networkContext(hre, process.env), deploymentPath });
  appendJsonl(paths.tradeLogPath, {
    runId: paths.runId,
    observedAt: output.deployedAt,
    network: hre.network.name,
    strategy: "triangular_ab_mock_fixture",
    action: "deploy-and-run",
    success: true,
    deploymentPath,
    reportPath: paths.reportPath,
    txHash: receipt.hash,
  });

  console.log(JSON.stringify({
    ok: true,
    deploymentPath,
    reportPath: paths.reportPath,
    txHash: receipt.hash,
    preview,
    balances,
  }, null, 2));
}

if (require.main === module) {
  main().catch((error) => {
    console.error(JSON.stringify({ ok: false, error: sanitizeError(error) }, null, 2));
    process.exitCode = 1;
  });
}
