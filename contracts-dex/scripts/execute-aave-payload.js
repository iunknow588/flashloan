const hre = require("hardhat");
const fs = require("fs");
const path = require("path");

function requireEnv(name) {
  const value = process.env[name];
  if (!value || !value.trim() || value === "0x...") {
    throw new Error(`${name} is required`);
  }
  return value.trim();
}

function payloadPath() {
  return path.resolve(process.cwd(), process.env.EXECUTION_PAYLOAD_FILE || "deployments/execution-payload.json");
}

async function main() {
  requireEnv("FUJI_RPC_URL");
  requireEnv("DEPLOYER_PRIVATE_KEY");
  const executorAddress = requireEnv("AAVE_EXECUTOR_ADDRESS");
  const payload = JSON.parse(fs.readFileSync(payloadPath(), "utf8"));
  const aavePayload = payload.contract && payload.contract.aaveSequentialFlashLoanExecutor;

  if (!aavePayload || !aavePayload.compatible || !aavePayload.plan) {
    const reason = aavePayload && aavePayload.reason ? aavePayload.reason : "missing Aave payload";
    throw new Error(`payload is not Aave-compatible: ${reason}`);
  }

  const executor = await hre.ethers.getContractAt("AaveSequentialFlashLoanExecutor", executorAddress);
  const latest = await hre.ethers.provider.getBlock("latest");
  const deadline = BigInt(latest.timestamp + Number(aavePayload.plan.deadlineSeconds || 600));

  const steps = aavePayload.plan.steps.map((step) => ({
    router: step.router,
    tokenIn: step.tokenIn,
    tokenOut: step.tokenOut,
    amountIn: BigInt(step.amountIn),
    amountOutMin: BigInt(step.amountOutMin),
    path: step.path,
  }));
  const plan = { steps, deadline };

  const tx = await executor.requestFlashLoan(
    aavePayload.borrowAsset,
    BigInt(aavePayload.borrowAmount),
    plan
  );
  const receipt = await tx.wait();
  console.log(`tx=${tx.hash}`);
  console.log(`gasUsed=${receipt.gasUsed}`);
  console.log(`borrowAsset=${aavePayload.borrowAsset}`);
  console.log(`borrowAmount=${aavePayload.borrowAmount}`);
  console.log(`steps=${steps.length}`);
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
