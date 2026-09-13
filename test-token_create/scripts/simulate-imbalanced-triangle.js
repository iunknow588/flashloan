const { ethers } = require("./lib/hardhat");

const DECIMALS = 18n;
const TOKEN_TOTAL = 10_000_000n * 10n ** DECIMALS;
const MILLION = 1_000_000n * 10n ** DECIMALS;

function toToken(value) {
  return BigInt(value) * 10n ** DECIMALS;
}

function display(value, decimals = 18) {
  return ethers.formatUnits(value, decimals);
}

async function metric(amm, tz, ta, tb) {
  const tzAddress = await tz.getAddress();
  const taAddress = await ta.getAddress();
  const tbAddress = await tb.getAddress();
  const leg1 = await amm.getReserves(tzAddress, tbAddress);
  const leg2 = await amm.getReserves(taAddress, tbAddress);
  const leg3 = await amm.getReserves(taAddress, tzAddress);
  return (
    (Number(display(leg1[1])) / Number(display(leg1[0]))) *
    (Number(display(leg2[0])) / Number(display(leg2[1]))) *
    (Number(display(leg3[1])) / Number(display(leg3[0])))
  );
}

async function main() {
  const [owner, trader] = await ethers.getSigners();
  const Token = await ethers.getContractFactory("TestERC20");
  const tz = await Token.deploy("Test Z", "tz", 18, owner.address);
  const ta = await Token.deploy("Test A", "ta", 18, owner.address);
  const tb = await Token.deploy("Test B", "tb", 18, owner.address);

  await tz.mint(owner.address, TOKEN_TOTAL);
  await ta.mint(owner.address, TOKEN_TOTAL);
  await tb.mint(owner.address, TOKEN_TOTAL);

  const Amm = await ethers.getContractFactory("MockSimpleAmmPoolManager");
  const amm = await Amm.deploy();

  const tzAddress = await tz.getAddress();
  const taAddress = await ta.getAddress();
  const tbAddress = await tb.getAddress();
  const ammAddress = await amm.getAddress();

  await amm.createPool(tzAddress, taAddress);
  await amm.createPool(taAddress, tbAddress);
  await amm.createPool(tbAddress, tzAddress);

  await tz.approve(ammAddress, ethers.MaxUint256);
  await ta.approve(ammAddress, ethers.MaxUint256);
  await tb.approve(ammAddress, ethers.MaxUint256);

  await amm.addLiquidity(tzAddress, taAddress, MILLION, toToken(100_000));
  await amm.addLiquidity(taAddress, tbAddress, toToken(100_000), MILLION);
  await amm.addLiquidity(tbAddress, tzAddress, MILLION, toToken(100_000));

  await tz.transfer(trader.address, toToken(1_000_000));

  await tz.connect(trader).approve(ammAddress, ethers.MaxUint256);
  await ta.connect(trader).approve(ammAddress, ethers.MaxUint256);
  await tb.connect(trader).approve(ammAddress, ethers.MaxUint256);

  console.log("tokens:");
  console.log(`  tz=${tzAddress} ta=${taAddress} tb=${tbAddress}`);
  console.log(`  totalSupply each=${display(TOKEN_TOTAL)}`);
  console.log("");

  console.log("initial reserves:");
  const r1 = await amm.getReserves(tzAddress, taAddress);
  const r2 = await amm.getReserves(taAddress, tbAddress);
  const r3 = await amm.getReserves(tbAddress, tzAddress);
  console.log(`  tz/ta: ${display(r1[0])} tz : ${display(r1[1])} ta`);
  console.log(`  ta/tb: ${display(r2[0])} ta : ${display(r2[1])} tb`);
  console.log(`  tb/tz: ${display(r3[0])} tb : ${display(r3[1])} tz`);
  const metricBefore = await metric(amm, tz, ta, tb);
  console.log(`  triangular price metric=${metricBefore.toFixed(6)} (1.0 means no cross-pool arbitrage)`);
  console.log("");

  const amountIn = toToken(1_000);
  const startTz = await tz.balanceOf(trader.address);
  const out1 = await amm.connect(trader).swap.staticCall(tzAddress, tbAddress, amountIn, 0);
  await amm.connect(trader).swap(tzAddress, tbAddress, amountIn, 0);
  const out2 = await amm.connect(trader).swap.staticCall(tbAddress, taAddress, out1, 0);
  await amm.connect(trader).swap(tbAddress, taAddress, out1, 0);
  const out3 = await amm.connect(trader).swap.staticCall(taAddress, tzAddress, out2, 0);
  await amm.connect(trader).swap(taAddress, tzAddress, out2, 0);
  const endTz = await tz.balanceOf(trader.address);

  console.log("closed loop TZ -> TB -> TA -> TZ:");
  console.log(`  amountIn=${display(amountIn)} tz`);
  console.log(`  out1=${display(out1)} tb`);
  console.log(`  out2=${display(out2)} ta`);
  console.log(`  out3=${display(out3)} tz`);
  console.log(`  trader tz before=${display(startTz)} after=${display(endTz)}`);
  console.log(`  profit=${display(endTz - startTz)} tz`);
  console.log("");

  console.log("final reserves:");
  const f1 = await amm.getReserves(tzAddress, taAddress);
  const f2 = await amm.getReserves(taAddress, tbAddress);
  const f3 = await amm.getReserves(tbAddress, tzAddress);
  console.log(`  tz/ta: ${display(f1[0])} tz : ${display(f1[1])} ta`);
  console.log(`  ta/tb: ${display(f2[0])} ta : ${display(f2[1])} tb`);
  console.log(`  tb/tz: ${display(f3[0])} tb : ${display(f3[1])} tz`);
  const metricAfter = await metric(amm, tz, ta, tb);
  console.log(`  triangular price metric=${metricAfter.toFixed(6)}`);
  console.log("");

  if (!(out3 > amountIn)) {
    throw new Error("closed loop did not return profit");
  }
  if (metricAfter >= metricBefore) {
    throw new Error("triangular price inconsistency did not decrease");
  }

  console.log("result: OK. 不平衡池中执行闭环套利后利润为正，三角价格偏差降低，池子向平衡方向修正。");
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
