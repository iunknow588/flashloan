const fs = require("fs");
const path = require("path");
const hre = require("hardhat");

const FLASH_SOURCE_UNISWAP_V4_POOL_MANAGER = 4n;

function evidenceStamp() {
  const stamp = new Date().toISOString().replace(/[-:]/g, "").replace(/\.(\d+)Z$/, "$1Z");
  return `${stamp}-${process.pid}`;
}

function stringify(value) {
  if (typeof value === "bigint") return value.toString();
  if (Array.isArray(value)) return value.map(stringify);
  if (value && typeof value === "object") {
    return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, stringify(item)]));
  }
  return value;
}

function writeEvidence(report) {
  const dir = path.resolve(
    __dirname,
    "../deployments/evidence",
    `${evidenceStamp()}_${hre.network.name}-robinhood-executor-v4-flash-offchain-normal`,
  );
  fs.mkdirSync(dir, { recursive: true });
  const reportPath = path.join(dir, "report.json");
  fs.writeFileSync(reportPath, `${JSON.stringify(report, null, 2)}\n`, "utf8");
  return reportPath;
}

async function deployExecutorV4Fixture(owner) {
  const PoolManager = await hre.ethers.getContractFactory("MockV4PoolManager");
  const poolManager = await PoolManager.deploy();
  const Router = await hre.ethers.getContractFactory("MockRobinhoodUniversalRouter");
  const router = await Router.deploy();
  const Executor = await hre.ethers.getContractFactory("RobinhoodPausedFlashLoanExecutor");
  const executor = await Executor.deploy(owner.address);

  await executor.setFlashSourceAdapter(
    FLASH_SOURCE_UNISWAP_V4_POOL_MANAGER,
    true,
    await poolManager.getAddress(),
    0,
    hre.ethers.ZeroHash,
  );
  await executor.setUniswapV4Adapter(
    true,
    await router.getAddress(),
    await poolManager.getAddress(),
    hre.ethers.getAddress("0x000000000022D473030F116dDEE9F6B43aC78BA3"),
    hre.ethers.ZeroHash,
  );
  await executor.setRepaymentGuard(0, 0, 0, true);
  await executor.setPaused(false);

  return { poolManager, router, executor };
}

function v4RouterRouteCall(routerInterface, deadline) {
  return routerInterface.encodeFunctionData("execute", ["0x10", ["0x"], deadline]);
}

async function localArtifactCodeStatus(contractName) {
  const artifact = await hre.artifacts.readArtifact(contractName);
  const deployedBytecode = String(artifact.deployedBytecode || "");
  return {
    contract: contractName,
    available: deployedBytecode !== "" && deployedBytecode !== "0x",
    codeBytes: deployedBytecode === "0x" ? 0 : (deployedBytecode.length - 2) / 2,
    codeHash: deployedBytecode === "0x" ? null : hre.ethers.keccak256(deployedBytecode),
  };
}

async function runExecutorOffchainNormalReadiness() {
  const [owner] = await hre.ethers.getSigners();
  const Token = await hre.ethers.getContractFactory("TestERC20");

  const amount = BigInt(process.env.ROBINHOOD_TESTNET_EXECUTOR_OFFCHAIN_NORMAL_FLASH_AMOUNT || "1000");
  const artifact = await localArtifactCodeStatus("RobinhoodPausedFlashLoanExecutor");

  const routerInterface = new hre.ethers.Interface(["function execute(bytes commands, bytes[] inputs, uint256 deadline)"]);
  const block = await hre.ethers.provider.getBlock("latest");
  const deadline = BigInt(block.timestamp + 600);
  const routeCall = v4RouterRouteCall(routerInterface, deadline);

  const token = await Token.deploy("Test USDG", "tUSDG", 6, owner.address);
  const tokenAddress = await token.getAddress();
  const tokenFixture = await deployExecutorV4Fixture(owner);
  const poolManagerAddress = await tokenFixture.poolManager.getAddress();
  const routerAddress = await tokenFixture.router.getAddress();
  const executorAddress = await tokenFixture.executor.getAddress();
  await token.mint(poolManagerAddress, amount);
  const request = {
    token: tokenAddress,
    amount,
    swapTargets: [routerAddress],
    swapCalldatas: [routeCall],
    finalToken: tokenAddress,
    minProfit: 0n,
    gasReserve: 0n,
  };

  const tx = await tokenFixture.executor.requestV4FlashLoan(request);
  const receipt = await tx.wait();

  const nativeAmount = BigInt(process.env.ROBINHOOD_TESTNET_EXECUTOR_OFFCHAIN_NORMAL_NATIVE_AMOUNT || amount.toString());
  const nativeMinProfit = BigInt(process.env.ROBINHOOD_TESTNET_EXECUTOR_OFFCHAIN_NORMAL_NATIVE_MIN_PROFIT || "1");
  const nativeFixture = await deployExecutorV4Fixture(owner);
  const nativePoolManagerAddress = await nativeFixture.poolManager.getAddress();
  const nativeRouterAddress = await nativeFixture.router.getAddress();
  const nativeExecutorAddress = await nativeFixture.executor.getAddress();
  await owner.sendTransaction({ to: nativePoolManagerAddress, value: nativeAmount });
  await owner.sendTransaction({ to: nativeRouterAddress, value: nativeMinProfit });
  await nativeFixture.router.setNativeTransferOnNextExecute(nativeAmount + nativeMinProfit);
  const nativeRequest = {
    token: hre.ethers.ZeroAddress,
    amount: nativeAmount,
    swapTargets: [nativeRouterAddress],
    swapCalldatas: [routeCall],
    swapValues: [nativeAmount],
    finalToken: hre.ethers.ZeroAddress,
    minProfit: nativeMinProfit,
    gasReserve: 0n,
  };
  const nativeTx = await nativeFixture.executor.requestV4FlashLoanWithValues(nativeRequest);
  const nativeReceipt = await nativeTx.wait();

  const poolBalance = await token.balanceOf(poolManagerAddress);
  const executorBalance = await token.balanceOf(executorAddress);
  const debt = await tokenFixture.poolManager.currencyDebt(executorAddress, tokenAddress);
  const nativeExecutorBalance = await hre.ethers.provider.getBalance(nativeExecutorAddress);
  const nativeDebt = await nativeFixture.poolManager.currencyDebt(nativeExecutorAddress, hre.ethers.ZeroAddress);
  const tokenRouterEvents = await tokenFixture.router.queryFilter(tokenFixture.router.filters.Executed());
  const nativeRouterEvents = await nativeFixture.router.queryFilter(nativeFixture.router.filters.Executed());
  const routerEvents = [...tokenRouterEvents, ...nativeRouterEvents];
  const ok = poolBalance === amount
    && executorBalance === 0n
    && debt === 0n
    && nativeExecutorBalance === nativeMinProfit
    && nativeDebt === 0n
    && routerEvents.length === 2;

  return {
    schemaVersion: "robinhood-executor-v4-flash-offchain-normal-v1",
    runAt: new Date().toISOString(),
    network: hre.network.name,
    policy: {
      mode: "normal_only",
      tokenConservation: true,
      abnormalCases: "deferred",
    },
    localArtifact: artifact,
    contracts: {
      token: tokenAddress,
      poolManager: poolManagerAddress,
      router: routerAddress,
      executor: executorAddress,
    },
    amount,
    nativeAmount,
    nativeMinProfit,
    receipt: {
      hash: receipt.hash,
      gasUsed: receipt.gasUsed,
    },
    nativeReceipt: {
      hash: nativeReceipt.hash,
      gasUsed: nativeReceipt.gasUsed,
    },
    route: {
      hopCount: 1,
      routerEventCount: routerEvents.length,
      nativeExecutionFunction: "requestV4FlashLoanWithValues",
      nativeSwapValues: [nativeAmount.toString()],
      nativePoolManagerAddress,
      nativeRouterAddress,
      nativeExecutorAddress,
    },
    balances: {
      poolManager: poolBalance,
      executor: executorBalance,
      nativeExecutor: nativeExecutorBalance,
    },
    debt,
    nativeDebt,
    readyForPausedRedeploy: ok,
    readyForTestnetExecutorNormalProbe: ok,
    blockingReasons: ok ? [] : ["executor_v4_flash_accounting_not_settled_or_native_path_not_supported"],
  };
}

async function main() {
  const report = stringify(await runExecutorOffchainNormalReadiness());
  const reportPath = writeEvidence(report);
  console.log(JSON.stringify({ ...report, reportPath }, null, 2));
  if (!report.readyForPausedRedeploy) process.exitCode = 1;
}

if (require.main === module) {
  main().catch((error) => {
    console.error(error.message || error);
    process.exitCode = 1;
  });
}

module.exports = {
  runExecutorOffchainNormalReadiness,
  writeEvidence,
};
