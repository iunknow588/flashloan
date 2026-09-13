const { expect } = require("chai");
const { ethers } = require("hardhat");

const DECIMALS = 18n;
const TOKEN_TOTAL = 10_000_000n * 10n ** DECIMALS;
const MILLION = 1_000_000n * 10n ** DECIMALS;

function toToken(value) {
  return BigInt(value) * 10n ** DECIMALS;
}

async function deployFixture() {
  const [owner] = await ethers.getSigners();
  const Token = await ethers.getContractFactory("TestERC20");
  const tz = await Token.deploy("Test Z", "tz", 18, owner.address);
  const ta = await Token.deploy("Test A", "ta", 18, owner.address);
  const tb = await Token.deploy("Test B", "tb", 18, owner.address);

  await tz.mint(owner.address, TOKEN_TOTAL);
  await ta.mint(owner.address, TOKEN_TOTAL);
  await tb.mint(owner.address, TOKEN_TOTAL);

  const PoolManager = await ethers.getContractFactory("MockV4AmmPoolManager");
  const poolManager = await PoolManager.deploy();

  const tzAddress = await tz.getAddress();
  const taAddress = await ta.getAddress();
  const tbAddress = await tb.getAddress();
  const poolManagerAddress = await poolManager.getAddress();

  await poolManager.createPool(tzAddress, taAddress);
  await poolManager.createPool(taAddress, tbAddress);
  await poolManager.createPool(tbAddress, tzAddress);

  await tz.approve(poolManagerAddress, ethers.MaxUint256);
  await ta.approve(poolManagerAddress, ethers.MaxUint256);
  await tb.approve(poolManagerAddress, ethers.MaxUint256);

  await poolManager.addLiquidity(tzAddress, taAddress, MILLION, toToken(100_000));
  await poolManager.addLiquidity(taAddress, tbAddress, toToken(100_000), MILLION);
  await poolManager.addLiquidity(tbAddress, tzAddress, MILLION, toToken(100_000));

  const Arb = await ethers.getContractFactory("MockV4FlashAmmArb");
  const arb = await Arb.deploy(poolManagerAddress);

  return { owner, tz, ta, tb, poolManager, arb };
}

async function triangularMetric(poolManager, tz, ta, tb) {
  const tzAddress = await tz.getAddress();
  const taAddress = await ta.getAddress();
  const tbAddress = await tb.getAddress();
  const leg1 = await poolManager.getReserves(tzAddress, tbAddress);
  const leg2 = await poolManager.getReserves(taAddress, tbAddress);
  const leg3 = await poolManager.getReserves(taAddress, tzAddress);
  return (
    (Number(ethers.formatUnits(leg1[1], 18)) / Number(ethers.formatUnits(leg1[0], 18))) *
    (Number(ethers.formatUnits(leg2[0], 18)) / Number(ethers.formatUnits(leg2[1], 18))) *
    (Number(ethers.formatUnits(leg3[1], 18)) / Number(ethers.formatUnits(leg3[0], 18)))
  );
}

describe("Robinhood V4 flash loan imbalanced AMM arbitrage", function () {
  it("captures a profitable TZ -> TB -> TA -> TZ flash-loan arbitrage and clears pool debt", async function () {
    const { tz, ta, tb, poolManager, arb } = await deployFixture();
    const tzAddress = await tz.getAddress();
    const taAddress = await ta.getAddress();
    const tbAddress = await tb.getAddress();
    const arbAddress = await arb.getAddress();
    const amountIn = toToken(1_000);
    const metricBefore = await triangularMetric(poolManager, tz, ta, tb);

    const result = await arb.runArbitrage.staticCall(tzAddress, taAddress, tbAddress, amountIn, 1n);
    await arb.runArbitrage(tzAddress, taAddress, tbAddress, amountIn, 1n);
    const [outTb, outTa, outTz, profit] = result;

    expect(outTb).to.be.gt(0n);
    expect(outTa).to.be.gt(0n);
    expect(outTz).to.be.gt(amountIn);
    expect(profit).to.be.gt(0n);
    expect(await tz.balanceOf(arbAddress)).to.equal(profit);

    expect(await poolManager.currencyDebt(arbAddress, tzAddress)).to.equal(0n);
    expect(await poolManager.currencyDebt(arbAddress, taAddress)).to.equal(0n);
    expect(await poolManager.currencyDebt(arbAddress, tbAddress)).to.equal(0n);

    const metricAfter = await triangularMetric(poolManager, tz, ta, tb);
    expect(metricAfter).to.be.lessThan(metricBefore);
  });

  it("keeps x*y conserved across the three AMM pools after flash arbitrage", async function () {
    const { tz, ta, tb, poolManager, arb } = await deployFixture();
    const tzAddress = await tz.getAddress();
    const taAddress = await ta.getAddress();
    const tbAddress = await tb.getAddress();

    const beforePairs = [
      await poolManager.getReserves(tzAddress, taAddress),
      await poolManager.getReserves(taAddress, tbAddress),
      await poolManager.getReserves(tbAddress, tzAddress),
    ];

    await arb.runArbitrage(tzAddress, taAddress, tbAddress, toToken(1_000), 1n);

    const afterPairs = [
      await poolManager.getReserves(tzAddress, taAddress),
      await poolManager.getReserves(taAddress, tbAddress),
      await poolManager.getReserves(tbAddress, tzAddress),
    ];

    for (let i = 0; i < beforePairs.length; i++) {
      const beforeK = Number(ethers.formatUnits(beforePairs[i][0] * beforePairs[i][1], 36));
      const afterK = Number(ethers.formatUnits(afterPairs[i][0] * afterPairs[i][1], 36));
      expect(Math.abs(afterK - beforeK) / beforeK).to.be.lessThan(0.001);
    }
  });

  it("runs multiple automatic flash-loan rounds and keeps reducing the imbalance metric", async function () {
    const { tz, ta, tb, poolManager, arb } = await deployFixture();
    const tzAddress = await tz.getAddress();
    const taAddress = await ta.getAddress();
    const tbAddress = await tb.getAddress();
    const arbAddress = await arb.getAddress();

    let metric = await triangularMetric(poolManager, tz, ta, tb);
    let arbBalanceBefore = 0n;
    for (let round = 0; round < 3; round++) {
      const result = await arb.runArbitrage.staticCall(tzAddress, taAddress, tbAddress, toToken(1_000), 1n);
      await arb.runArbitrage(tzAddress, taAddress, tbAddress, toToken(1_000), 1n);
      const profit = result[3];
      expect(profit).to.be.gt(0n);
      const nextMetric = await triangularMetric(poolManager, tz, ta, tb);
      expect(nextMetric).to.be.lessThan(metric);
      metric = nextMetric;
      expect(await tz.balanceOf(arbAddress)).to.be.gt(arbBalanceBefore);
      arbBalanceBefore = await tz.balanceOf(arbAddress);
    }

    expect(await poolManager.currencyDebt(arbAddress, tzAddress)).to.equal(0n);
    expect(await poolManager.currencyDebt(arbAddress, taAddress)).to.equal(0n);
    expect(await poolManager.currencyDebt(arbAddress, tbAddress)).to.equal(0n);
  });
});
