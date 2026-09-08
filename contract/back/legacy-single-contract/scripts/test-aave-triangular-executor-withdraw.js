const fs = require("fs");
const path = require("path");
const hre = require("hardhat");
const {
  appendJsonl,
  evidencePaths,
  networkContext,
  receiptReport,
  sanitizeError,
  writeJson,
} = require("./fuji-evidence");

function requireEnv(name, defaultValue = "") {
  const value = process.env[name];
  return value && value.trim() ? value.trim() : defaultValue;
}

function normalizeAddress(value) {
  const text = String(value || "").trim();
  if (hre.ethers.isAddress(text)) return hre.ethers.getAddress(text);
  if (/^0x[a-fA-F0-9]{40}$/.test(text)) return hre.ethers.getAddress(text.toLowerCase());
  return "";
}

async function main() {
  const deploymentPath = requireEnv(
    "FUJI_TRIANGULAR_AB_MOCK_FIXTURE",
    path.resolve(process.cwd(), "deployments/fuji-triangular-ab-mock-fixture.json"),
  );
  const fixture = JSON.parse(fs.readFileSync(deploymentPath, "utf8"));
  const executorAddress = normalizeAddress(fixture.aaveTriangularExecutorAddress);
  const tokenAddress = normalizeAddress(fixture.usdcAddress);
  if (!executorAddress || !tokenAddress) throw new Error("deployment fixture is missing executor or token address");

  const [signer] = await hre.ethers.getSigners();
  const executor = await hre.ethers.getContractAt("AaveTriangularExecutor", executorAddress);
  const token = await hre.ethers.getContractAt("TestERC20", tokenAddress);
  const owner = await executor.owner();
  if (owner.toLowerCase() !== signer.address.toLowerCase()) {
    throw new Error(`signer ${signer.address} does not match executor owner ${owner}`);
  }

  const amount = BigInt(requireEnv("AAVE_TRIANGULAR_WITHDRAW_TEST_AMOUNT", "1234"));
  const beforeOwner = await token.balanceOf(signer.address);
  const beforeExecutor = await token.balanceOf(executorAddress);

  const mintTx = await token.mint(executorAddress, amount);
  const mintReceipt = await mintTx.wait();
  const withdrawTx = await executor.withdrawToken(tokenAddress, signer.address, amount);
  const withdrawReceipt = await withdrawTx.wait();

  const afterOwner = await token.balanceOf(signer.address);
  const afterExecutor = await token.balanceOf(executorAddress);
  const report = {
    network: hre.network.name || "unknown",
    chainId: Number((await hre.ethers.provider.getNetwork()).chainId),
    deploymentPath,
    executorAddress,
    tokenAddress,
    owner: signer.address,
    amount: amount.toString(),
    beforeOwner: beforeOwner.toString(),
    beforeExecutor: beforeExecutor.toString(),
    afterOwner: afterOwner.toString(),
    afterExecutor: afterExecutor.toString(),
    mintTxHash: mintReceipt.hash,
    withdrawTxHash: withdrawReceipt.hash,
    mintReceipt: receiptReport(mintReceipt),
    withdrawReceipt: receiptReport(withdrawReceipt),
    context: await networkContext(hre, process.env),
  };

  const paths = evidencePaths({ strategy: `${hre.network.name || "unknown"}-aave-triangular-executor-withdraw` });
  writeJson(paths.reportPath, report);
  appendJsonl(paths.tradeLogPath, {
    runId: paths.runId,
    observedAt: new Date().toISOString(),
    network: hre.network.name,
    strategy: "aave_triangular_executor_withdraw",
    action: "mint-and-withdraw",
    success: true,
    reportPath: paths.reportPath,
    deploymentPath,
    executorAddress,
    tokenAddress,
    withdrawTxHash: withdrawReceipt.hash,
  });

  console.log(JSON.stringify({
    ok: true,
    afterOwner: report.afterOwner,
    afterExecutor: report.afterExecutor,
    withdrawTxHash: report.withdrawTxHash,
    reportPath: paths.reportPath,
  }, null, 2));
}

if (require.main === module) {
  main().catch((error) => {
    console.error(JSON.stringify({ ok: false, error: sanitizeError(error) }, null, 2));
    process.exitCode = 1;
  });
}
