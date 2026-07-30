const hre = require("hardhat");

const USE_FULL_BALANCE = hre.ethers.MaxUint256;

function requireEnv(name) {
  const value = process.env[name];
  if (!value || !value.trim()) {
    throw new Error(`${name} is required`);
  }
  return value.trim();
}

function envBigInt(name, fallback) {
  const value = process.env[name];
  return value && value.trim() ? BigInt(value.trim()) : fallback;
}

async function expectRevert(label, fn) {
  try {
    await fn();
    throw new Error(`${label} unexpectedly succeeded`);
  } catch (error) {
    const message = error.shortMessage || error.reason || error.message || String(error);
    console.log(`${label}=reverted`);
    console.log(`${label}.reason=${message.split("\n")[0]}`);
  }
}

async function main() {
  requireEnv("FUJI_RPC_URL");
  requireEnv("DEPLOYER_PRIVATE_KEY");

  const executorAddress = requireEnv("MOCK_EXECUTOR_ADDRESS");
  const router = requireEnv("FUJI_DEX_ROUTER");
  const usdc = requireEnv("FUJI_USDC");
  const token = requireEnv("FUJI_ROUNDTRIP_TOKEN");
  const usdcAmountUnits = envBigInt("FUJI_USDC_AMOUNT_UNITS", 1000000n);

  const executor = await hre.ethers.getContractAt("MockFundedExecutor", executorAddress);
  const latest = await hre.ethers.provider.getBlock("latest");
  const validDeadline = BigInt(latest.timestamp + 600);
  const expiredDeadline = BigInt(latest.timestamp - 1);

  const baseSteps = [
    {
      router,
      tokenIn: usdc,
      tokenOut: token,
      amountIn: usdcAmountUnits,
      amountOutMin: 1n,
      path: [usdc, token],
    },
    {
      router,
      tokenIn: token,
      tokenOut: usdc,
      amountIn: USE_FULL_BALANCE,
      amountOutMin: 1n,
      path: [token, usdc],
    },
  ];

  const highMinOutSteps = [
    { ...baseSteps[0], amountOutMin: hre.ethers.MaxUint256 },
    baseSteps[1],
  ];

  await expectRevert("guard.amountOutMin", () =>
    executor.executePlan.staticCall(highMinOutSteps, usdc, 0, validDeadline)
  );
  await expectRevert("guard.deadline", () =>
    executor.executePlan.staticCall(baseSteps, usdc, 0, expiredDeadline)
  );
  await expectRevert("guard.minProfit", () =>
    executor.executePlan.staticCall(baseSteps, usdc, 1, validDeadline)
  );
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
