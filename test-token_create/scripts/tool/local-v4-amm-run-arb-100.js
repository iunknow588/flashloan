const { ethers } = require("../lib/hardhat");
const {
  toToken,
  readManifest,
  writeManifest,
  triangularMetric,
} = require("../lib/local-v4-amm-common");

async function printReserves(label, amm, tz, ta, tb) {
  const tzAddress = await tz.getAddress();
  const taAddress = await ta.getAddress();
  const tbAddress = await tb.getAddress();
  const r1 = await amm.getReserves(tzAddress, taAddress);
  const r2 = await amm.getReserves(taAddress, tbAddress);
  const r3 = await amm.getReserves(tbAddress, tzAddress);
  console.log(`${label}:`);
  console.log(`  tz/ta: ${ethers.formatUnits(r1[0], 18)} tz : ${ethers.formatUnits(r1[1], 18)} ta`);
  console.log(`  ta/tb: ${ethers.formatUnits(r2[0], 18)} ta : ${ethers.formatUnits(r2[1], 18)} tb`);
  console.log(`  tb/tz: ${ethers.formatUnits(r3[0], 18)} tb : ${ethers.formatUnits(r3[1], 18)} tz`);
}

async function assertNoDebt(amm, arbAddress, tokens) {
  for (const token of tokens) {
    const debt = await amm.currencyDebt(arbAddress, await token.getAddress());
    if (debt !== 0n) throw new Error(`currency debt not cleared: ${await token.getAddress()} = ${debt}`);
  }
}

async function main() {
  const manifest = await readManifest();
  if (!manifest || !manifest.tz || !manifest.ta || !manifest.tb || !manifest.amm) {
    throw new Error("manifest missing tokens or pool manager, run create-tokens/inject-liquidity first");
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
  const arbAddress = await arb.getAddress();
  const amountIn = toToken(100);
  const convergenceMetric = 1.01;

  await printReserves("reserves before", amm, tz, ta, tb);
  const metricBefore = await triangularMetric(amm, tz, ta, tb);
  console.log(`  triangular price metric before=${metricBefore.toFixed(6)}`);
  console.log(`  flash amountIn=${ethers.formatUnits(amountIn, 18)} tz`);
  console.log(`  route: tz -> tb -> ta -> tz`);
  console.log("");

  let totalProfit = 0n;
  let metricAfter = metricBefore;
  let executedRounds = 0;
  const stopProfit = toToken(1);

  for (let i = 0; i < 200; i++) {
    let result;
    try {
      result = await arb.runArbitrage.staticCall(tzAddress, taAddress, tbAddress, amountIn, 1n);
    } catch {
      console.log(`stopping: round ${i + 1} staticCall no longer profitable`);
      break;
    }

    const tx = await arb.runArbitrage(tzAddress, taAddress, tbAddress, amountIn, 1n);
    const receipt = await tx.wait();
    const [outTb, outTa, outTz, profit] = result;
    totalProfit += profit;
    executedRounds += 1;
    metricAfter = await triangularMetric(amm, tz, ta, tb);
    await assertNoDebt(amm, arbAddress, [tz, ta, tb]);

    console.log(
      `round ${i + 1}: tx=${receipt.hash} gas=${receipt.gasUsed} outTb=${ethers.formatUnits(outTb, 18)} ` +
        `outTa=${ethers.formatUnits(outTa, 18)} outTz=${ethers.formatUnits(outTz, 18)} ` +
        `profit=${ethers.formatUnits(profit, 18)} metric=${metricAfter.toFixed(6)}`
    );

    const arbTz = await tz.balanceOf(arbAddress);
    console.log(`  arb contract tz balance=${ethers.formatUnits(arbTz, 18)}`);

    if (profit <= stopProfit) {
      console.log(`stopping: single-round profit no longer significant (<= ${ethers.formatUnits(stopProfit, 18)} tz)`);
      break;
    }
    if (metricAfter <= convergenceMetric) {
      console.log(`stopping: triangular price metric converged to <= ${convergenceMetric}`);
      break;
    }
  }

  console.log("");
  await printReserves("reserves after", amm, tz, ta, tb);
  console.log(`  triangular price metric after=${metricAfter.toFixed(6)}`);
  const finalArbTz = await tz.balanceOf(arbAddress);
  console.log(`  total executed arbitrage rounds=${executedRounds}`);
  console.log(`  total tz profit retained in arb=${ethers.formatUnits(totalProfit, 18)}`);
  console.log(`  arb contract final tz balance=${ethers.formatUnits(finalArbTz, 18)}`);
  await assertNoDebt(amm, arbAddress, [tz, ta, tb]);
  console.log("  all currencyDebt entries are zero");

  if (executedRounds === 0) throw new Error("no arbitrage transaction was executed");
  if (totalProfit <= 0n) throw new Error("no arbitrage profit captured");
  if (metricBefore > convergenceMetric && metricAfter >= metricBefore) {
    throw new Error("price inconsistency did not decrease");
  }

  if (metricAfter <= convergenceMetric) {
    console.log(`result: OK. V4 flash loan arbitrage ran ${executedRounds} round(s) and moved pools toward balance.`);
  } else {
    console.log(`result: OK. V4 flash loan arbitrage ran ${executedRounds} round(s) and reduced imbalance metric.`);
  }
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
