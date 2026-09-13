const { ethers } = require("./lib/hardhat");
const { readManifest, writeManifest, toToken } = require("./lib/local-v4-amm-common");

async function triangularMetric(amm, tz, ta, tb) {
  const tzAddress = await tz.getAddress();
  const taAddress = await ta.getAddress();
  const tbAddress = await tb.getAddress();
  const leg1 = await amm.getReserves(tzAddress, tbAddress);
  const leg2 = await amm.getReserves(taAddress, tbAddress);
  const leg3 = await amm.getReserves(taAddress, tzAddress);
  return (
    (Number(ethers.formatUnits(leg1[1], 18)) / Number(ethers.formatUnits(leg1[0], 18))) *
    (Number(ethers.formatUnits(leg2[0], 18)) / Number(ethers.formatUnits(leg2[1], 18))) *
    (Number(ethers.formatUnits(leg3[1], 18)) / Number(ethers.formatUnits(leg3[0], 18)))
  );
}

async function main() {
  const manifest = await readManifest();
  if (!manifest || !manifest.tz || !manifest.ta || !manifest.tb || !manifest.amm) {
    throw new Error("manifest missing tokens or pool manager, run create-tokens and inject-liquidity first");
  }

  const tz = await ethers.getContractAt("TestERC20", manifest.tz);
  const ta = await ethers.getContractAt("TestERC20", manifest.ta);
  const tb = await ethers.getContractAt("TestERC20", manifest.tb);
  const amm = await ethers.getContractAt("MockV4AmmPoolManager", manifest.amm);

  let arb;
  if (manifest.arb) {
    arb = await ethers.getContractAt("MockV4FlashAmmArb", manifest.arb);
  } else {
    const Arb = await ethers.getContractFactory("MockV4FlashAmmArb");
    arb = await Arb.deploy(manifest.amm);
    await arb.waitForDeployment();
    manifest.arb = await arb.getAddress();
    await writeManifest(manifest);
  }

  const tzAddress = await tz.getAddress();
  const taAddress = await ta.getAddress();
  const tbAddress = await tb.getAddress();
  const amountIn = toToken(1_000);
  const metricBefore = await triangularMetric(amm, tz, ta, tb);

  console.log("initial reserves:");
  const r1 = await amm.getReserves(tzAddress, taAddress);
  const r2 = await amm.getReserves(taAddress, tbAddress);
  const r3 = await amm.getReserves(tbAddress, tzAddress);
  console.log(`  tz/ta: ${ethers.formatUnits(r1[0], 18)} tz : ${ethers.formatUnits(r1[1], 18)} ta`);
  console.log(`  ta/tb: ${ethers.formatUnits(r2[0], 18)} ta : ${ethers.formatUnits(r2[1], 18)} tb`);
  console.log(`  tb/tz: ${ethers.formatUnits(r3[0], 18)} tb : ${ethers.formatUnits(r3[1], 18)} tz`);
  console.log(`  triangular price metric=${metricBefore.toFixed(6)}`);
  console.log("");

  let totalProfit = 0n;
  let lastProfit = 1n;
  let metricAfter = metricBefore;
  const stopProfit = toToken(1);
  const convergenceMetric = 1.01;
  for (let i = 0; i < 200 && lastProfit > 0n; i++) {
    let result;
    try {
      result = await arb.runArbitrage.staticCall(tzAddress, taAddress, tbAddress, amountIn, 1n);
    } catch {
      console.log("stopping: next round no longer profitable");
      break;
    }
    await arb.runArbitrage(tzAddress, taAddress, tbAddress, amountIn, 1n);
    const [outTb, outTa, outTz, profit] = result;
    lastProfit = profit;
    totalProfit += profit;
    metricAfter = await triangularMetric(amm, tz, ta, tb);
    console.log(`round ${i + 1}: outTb=${ethers.formatUnits(outTb, 18)} outTa=${ethers.formatUnits(outTa, 18)} outTz=${ethers.formatUnits(outTz, 18)} profit=${ethers.formatUnits(profit, 18)} metric=${metricAfter.toFixed(6)}`);
    if (lastProfit <= stopProfit) {
      console.log(`stopping: single-round profit no longer significant (<= ${ethers.formatUnits(stopProfit, 18)} tz)`);
      break;
    }
  }

  console.log("");
  console.log("final reserves:");
  const f1 = await amm.getReserves(tzAddress, taAddress);
  const f2 = await amm.getReserves(taAddress, tbAddress);
  const f3 = await amm.getReserves(tbAddress, tzAddress);
  console.log(`  tz/ta: ${ethers.formatUnits(f1[0], 18)} tz : ${ethers.formatUnits(f1[1], 18)} ta`);
  console.log(`  ta/tb: ${ethers.formatUnits(f2[0], 18)} ta : ${ethers.formatUnits(f2[1], 18)} tb`);
  console.log(`  tb/tz: ${ethers.formatUnits(f3[0], 18)} tb : ${ethers.formatUnits(f3[1], 18)} tz`);
  console.log(`  triangular price metric=${metricAfter.toFixed(6)}`);
  console.log(`  total tz profit in arb=${ethers.formatUnits(await tz.balanceOf(await arb.getAddress()), 18)}`);
  console.log("");

  if (totalProfit <= 0n && metricBefore > convergenceMetric) throw new Error("no arbitrage profit captured");
  if (metricAfter >= metricBefore && metricBefore > convergenceMetric) {
    throw new Error("price inconsistency did not decrease");
  }
  if (metricAfter <= convergenceMetric) {
    console.log(`converged: triangular price metric <= ${convergenceMetric}`);
  }
  console.log("result: OK. V4 flash loan arbitrage captured profit and moved pools toward balance.");
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
