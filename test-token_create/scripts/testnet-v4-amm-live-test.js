const path = require("path");
const fs = require("fs");
const { ethers } = require("./lib/hardhat");
const {
  TOKEN_TOTAL,
  toToken,
  deployOrGetContract,
  deployTokens,
  injectInitialLiquidity,
} = require("./lib/local-v4-amm-common");

const TESTNET_MANIFEST_PATH = path.resolve(__dirname, "../config/testnet-v4-amm/manifest.json");

async function readTestnetManifest() {
  if (!fs.existsSync(TESTNET_MANIFEST_PATH)) return null;
  return JSON.parse(fs.readFileSync(TESTNET_MANIFEST_PATH, "utf8"));
}

async function writeTestnetManifest(manifest) {
  fs.mkdirSync(path.dirname(TESTNET_MANIFEST_PATH), { recursive: true });
  fs.writeFileSync(TESTNET_MANIFEST_PATH, JSON.stringify(manifest, null, 2) + "\n", "utf8");
}

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

async function assert80PercentRetained(owner, token, label) {
  const total = await token.totalSupply();
  const balance = await token.balanceOf(owner.address);
  const min = (total * 80n) / 100n;
  if (balance < min) {
    throw new Error(`${label} owner balance ${balance} below 80% of total ${total}`);
  }
}

async function main() {
  if (process.env.ROBINHOOD_TESTNET_ALLOW_V4_AMM_LIVE_TEST !== "true") {
    throw new Error("ROBINHOOD_TESTNET_ALLOW_V4_AMM_LIVE_TEST must be true");
  }

  const [owner] = await ethers.getSigners();
  const manifest = (await readTestnetManifest()) || {};

  let tz;
  let ta;
  let tb;
  if (manifest.tz && manifest.ta && manifest.tb) {
    tz = await ethers.getContractAt("TestERC20", manifest.tz);
    ta = await ethers.getContractAt("TestERC20", manifest.ta);
    tb = await ethers.getContractAt("TestERC20", manifest.tb);
    console.log("reusing testnet tokens from manifest");
  } else {
    const deployed = await deployTokens(owner);
    tz = deployed.tz;
    ta = deployed.ta;
    tb = deployed.tb;
    manifest.tz = await tz.getAddress();
    manifest.ta = await ta.getAddress();
    manifest.tb = await tb.getAddress();
    manifest.tokenTotal = TOKEN_TOTAL.toString();
    console.log("deployed testnet tokens");
  }

  const amm = await deployOrGetContract("MockV4AmmPoolManager", manifest.amm, owner);
  manifest.amm = await amm.getAddress();
  await writeTestnetManifest(manifest);

  await injectInitialLiquidity(owner, tz, ta, tb, amm);
  await assert80PercentRetained(owner, tz, "tz");
  await assert80PercentRetained(owner, ta, "ta");
  await assert80PercentRetained(owner, tb, "tb");

  let arb;
  if (manifest.arb) {
    arb = await ethers.getContractAt("MockV4FlashAmmArb", manifest.arb);
  } else {
    const Arb = await ethers.getContractFactory("MockV4FlashAmmArb");
    arb = await Arb.deploy(manifest.amm);
    await arb.waitForDeployment();
    manifest.arb = await arb.getAddress();
    await writeTestnetManifest(manifest);
  }

  const tzAddress = await tz.getAddress();
  const taAddress = await ta.getAddress();
  const tbAddress = await tb.getAddress();
  const amountIn = toToken(100);
  const metricBefore = await triangularMetric(amm, tz, ta, tb);

  console.log("testnet v4 amm live-test setup:");
  console.log(`  deployer=${owner.address}`);
  console.log(`  tz=${tzAddress}`);
  console.log(`  ta=${taAddress}`);
  console.log(`  tb=${tbAddress}`);
  console.log(`  amm=${manifest.amm}`);
  console.log(`  arb=${manifest.arb}`);
  console.log(`  metricBefore=${metricBefore.toFixed(6)}`);

  const result = await arb.runArbitrage.staticCall(tzAddress, taAddress, tbAddress, amountIn, 1n);
  const tx = await arb.runArbitrage(tzAddress, taAddress, tbAddress, amountIn, 1n);
  const receipt = await tx.wait();
  const [outTb, outTa, outTz, profit] = result;
  const metricAfter = await triangularMetric(amm, tz, ta, tb);
  const arbAddress = await arb.getAddress();

  const debtTz = await amm.currencyDebt(arbAddress, tzAddress);
  const debtTa = await amm.currencyDebt(arbAddress, taAddress);
  const debtTb = await amm.currencyDebt(arbAddress, tbAddress);

  console.log("live arbitrage tx:");
  console.log(`  txHash=${receipt.hash}`);
  console.log(`  gasUsed=${receipt.gasUsed.toString()}`);
  console.log(`  amountIn=${ethers.formatUnits(amountIn, 18)} tz`);
  console.log(`  outTb=${ethers.formatUnits(outTb, 18)}`);
  console.log(`  outTa=${ethers.formatUnits(outTa, 18)}`);
  console.log(`  outTz=${ethers.formatUnits(outTz, 18)}`);
  console.log(`  profit=${ethers.formatUnits(profit, 18)}`);
  console.log(`  metricAfter=${metricAfter.toFixed(6)}`);
  console.log(`  debts: tz=${debtTz} ta=${debtTa} tb=${debtTb}`);

  if (profit <= 0n) throw new Error("live test profit not positive");
  if (metricAfter >= metricBefore) throw new Error("live test did not reduce imbalance");
  if (debtTz !== 0n || debtTa !== 0n || debtTb !== 0n) throw new Error("flash loan debt not cleared");

  manifest.lastLiveTest = {
    txHash: receipt.hash,
    runAt: new Date().toISOString(),
    amountIn: amountIn.toString(),
    outTb: outTb.toString(),
    outTa: outTa.toString(),
    outTz: outTz.toString(),
    profit: profit.toString(),
    metricBefore: metricBefore.toString(),
    metricAfter: metricAfter.toString(),
  };
  await writeTestnetManifest(manifest);

  console.log("result: OK. testnet V4 flash loan arbitrage captured positive profit and cleared debt.");
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
