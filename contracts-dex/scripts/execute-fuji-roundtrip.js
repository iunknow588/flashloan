const hre = require("hardhat");
const fs = require("fs");
const path = require("path");

const USE_FULL_BALANCE = hre.ethers.MaxUint256;
const ERC20_ABI = [
  "function decimals() view returns (uint8)",
  "function balanceOf(address account) view returns (uint256)",
  "function transfer(address to, uint256 amount) returns (bool)",
];

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

function tradeLogPath() {
  return path.resolve(process.cwd(), process.env.TESTNET_TRADE_LOG || "deployments/fuji-trades.jsonl");
}

function appendTradeLog(entry) {
  const filePath = tradeLogPath();
  fs.mkdirSync(path.dirname(filePath), { recursive: true });
  fs.appendFileSync(filePath, `${JSON.stringify(entry)}\n`);
}

async function main() {
  requireEnv("FUJI_RPC_URL");
  requireEnv("DEPLOYER_PRIVATE_KEY");

  const executorAddress = requireEnv("MOCK_EXECUTOR_ADDRESS");
  const router = requireEnv("FUJI_DEX_ROUTER");
  const usdc = requireEnv("FUJI_USDC");
  const token = requireEnv("FUJI_ROUNDTRIP_TOKEN");
  const usdcAmountUnits = envBigInt("FUJI_USDC_AMOUNT_UNITS", 1000000n);
  const firstMinOut = envBigInt("FUJI_FIRST_MIN_OUT_UNITS", 1n);
  const finalMinOut = envBigInt("FUJI_FINAL_MIN_OUT_UNITS", 1n);
  const minProfit = envBigInt("FUJI_MIN_PROFIT_UNITS", 0n);
  const deadlineSeconds = Number(process.env.FUJI_DEADLINE_SECONDS || "600");

  const [signer] = await hre.ethers.getSigners();
  const usdcContract = new hre.ethers.Contract(usdc, ERC20_ABI, signer);
  const executor = await hre.ethers.getContractAt("MockFundedExecutor", executorAddress, signer);

  const before = await usdcContract.balanceOf(executorAddress);
  if (before < usdcAmountUnits) {
    const missing = usdcAmountUnits - before;
    console.log(`funding executor with ${missing} USDC base units`);
    const tx = await usdcContract.transfer(executorAddress, missing, { gasLimit: 120_000n });
    await tx.wait();
  }

  const latest = await hre.ethers.provider.getBlock("latest");
  const deadline = BigInt(latest.timestamp + deadlineSeconds);
  const steps = [
    {
      router,
      tokenIn: usdc,
      tokenOut: token,
      amountIn: usdcAmountUnits,
      amountOutMin: firstMinOut,
      path: [usdc, token],
    },
    {
      router,
      tokenIn: token,
      tokenOut: usdc,
      amountIn: USE_FULL_BALANCE,
      amountOutMin: finalMinOut,
      path: [token, usdc],
    },
  ];

  const tx = await executor.executePlan(steps, usdc, minProfit, deadline, { gasLimit: 1_500_000n });
  const receipt = await tx.wait();
  const after = await usdcContract.balanceOf(executorAddress);
  const profitUnits = after - before;

  console.log(`tx=${tx.hash}`);
  console.log(`gasUsed=${receipt.gasUsed}`);
  console.log(`executorUsdcBefore=${before}`);
  console.log(`executorUsdcAfter=${after}`);
  console.log(`profitUnits=${profitUnits}`);
  appendTradeLog({
    observedAt: new Date().toISOString(),
    network: "fuji",
    strategy: "mock_funded_roundtrip",
    success: true,
    txHash: tx.hash,
    gasUsed: receipt.gasUsed.toString(),
    executorAddress,
    router,
    profitToken: usdc,
    token,
    amountInUnits: usdcAmountUnits.toString(),
    profitUnits: profitUnits.toString(),
  });
}

main().catch((error) => {
  console.error(error);
  appendTradeLog({
    observedAt: new Date().toISOString(),
    network: "fuji",
    strategy: "mock_funded_roundtrip",
    success: false,
    error: error.shortMessage || error.reason || error.message || String(error),
  });
  process.exitCode = 1;
});
