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
  const executorAddress = requireEnv("MOCK_EXECUTOR_ADDRESS");
  const payload = JSON.parse(fs.readFileSync(payloadPath(), "utf8"));
  const plan = payload.contract.mockFundedExecutor;
  const executor = await hre.ethers.getContractAt("MockFundedExecutor", executorAddress);
  const latest = await hre.ethers.provider.getBlock("latest");
  const deadline = BigInt(latest.timestamp + Number(plan.deadlineSeconds || 600));

  const steps = plan.steps.map((step) => ({
    router: step.router,
    tokenIn: step.tokenIn,
    tokenOut: step.tokenOut,
    amountIn: BigInt(step.amountIn),
    amountOutMin: BigInt(step.amountOutMin),
    path: step.path,
  }));

  const tx = await executor.executePlan(steps, plan.profitToken, BigInt(plan.minProfit || "0"), deadline);
  const receipt = await tx.wait();
  console.log(`tx=${tx.hash}`);
  console.log(`gasUsed=${receipt.gasUsed}`);
  console.log(`steps=${steps.length}`);
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
