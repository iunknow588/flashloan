const { expect } = require("chai");
const { ethers } = require("hardhat");

const DECIMALS = 18n;
const TOKEN_TOTAL = 10_000_000n * 10n ** DECIMALS;
const MILLION = 1_000_000n * 10n ** DECIMALS;

function toToken(value) {
  return BigInt(value) * 10n ** DECIMALS;
}

function computeOut(reserveIn, reserveOut, amountIn) {
  return (reserveOut * amountIn) / (reserveIn + amountIn);
}

async function deployFixture() {
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

  return { owner, trader, tz, ta, tb, amm };
}

async function approveTrader(amm, tz, ta, tb, trader) {
  await tz.connect(trader).approve(await amm.getAddress(), ethers.MaxUint256);
  await ta.connect(trader).approve(await amm.getAddress(), ethers.MaxUint256);
  await tb.connect(trader).approve(await amm.getAddress(), ethers.MaxUint256);
}

async function triangularPriceMetric(amm, tz, ta, tb) {
  const tzAddress = await tz.getAddress();
  const taAddress = await ta.getAddress();
  const tbAddress = await tb.getAddress();

  const leg1 = await amm.getReserves(tzAddress, tbAddress); // TZ -> TB
  const leg2 = await amm.getReserves(taAddress, tbAddress); // TB -> TA
  const leg3 = await amm.getReserves(taAddress, tzAddress); // TA -> TZ

  const rateTbPerTz = Number(ethers.formatUnits(leg1[1], 18)) / Number(ethers.formatUnits(leg1[0], 18));
  const rateTaPerTb = Number(ethers.formatUnits(leg2[0], 18)) / Number(ethers.formatUnits(leg2[1], 18));
  const rateTzPerTa = Number(ethers.formatUnits(leg3[1], 18)) / Number(ethers.formatUnits(leg3[0], 18));

  return rateTbPerTz * rateTaPerTb * rateTzPerTa;
}

describe("Robinhood imbalanced AMM triangle", function () {
  it("deploys tz/ta/tb with 10 million supply and imbalanced liquidity", async function () {
    const { tz, ta, tb, amm } = await deployFixture();

    expect(await tz.totalSupply()).to.equal(TOKEN_TOTAL);
    expect(await ta.totalSupply()).to.equal(TOKEN_TOTAL);
    expect(await tb.totalSupply()).to.equal(TOKEN_TOTAL);

    const tzAddress = await tz.getAddress();
    const taAddress = await ta.getAddress();
    const tbAddress = await tb.getAddress();

    const r1 = await amm.getReserves(tzAddress, taAddress);
    const r2 = await amm.getReserves(taAddress, tbAddress);
    const r3 = await amm.getReserves(tbAddress, tzAddress);

    expect(r1[0]).to.equal(MILLION);
    expect(r1[1]).to.equal(toToken(100_000));
    expect(r2[0]).to.equal(toToken(100_000));
    expect(r2[1]).to.equal(MILLION);
    expect(r3[0]).to.equal(MILLION);
    expect(r3[1]).to.equal(toToken(100_000));
  });

  it("executes a single x*y=k swap on an imbalanced pool", async function () {
    const { trader, tz, ta, tb, amm } = await deployFixture();
    await approveTrader(amm, tz, ta, tb, trader);

    const tzAddress = await tz.getAddress();
    const taAddress = await ta.getAddress();
    const [beforeIn, beforeOut] = await amm.getReserves(tzAddress, taAddress);
    const amountIn = toToken(10);

    await amm.connect(trader).swap(tzAddress, taAddress, amountIn, 0);

    const expectedOut = computeOut(beforeIn, beforeOut, amountIn);
    expect(await ta.balanceOf(trader.address)).to.equal(expectedOut);

    const [afterIn, afterOut] = await amm.getReserves(tzAddress, taAddress);
    expect(afterIn).to.equal(beforeIn + amountIn);
    expect(afterOut).to.equal(beforeOut - expectedOut);
  });

  it("estimates a profitable closed loop across the three imbalanced pools", async function () {
    const { tz, ta, tb, amm } = await deployFixture();
    const tzAddress = await tz.getAddress();
    const taAddress = await ta.getAddress();
    const tbAddress = await tb.getAddress();

    const amountIn = toToken(1);
    const leg1 = await amm.getReserves(tzAddress, tbAddress);
    const leg2 = await amm.getReserves(tbAddress, taAddress);
    const leg3 = await amm.getReserves(taAddress, tzAddress);

    const out1 = computeOut(leg1[0], leg1[1], amountIn);
    const out2 = computeOut(leg2[0], leg2[1], out1);
    const out3 = computeOut(leg3[0], leg3[1], out2);

    expect(out3).to.be.gt(amountIn);
  });

  it("executes TZ -> TB -> TA -> TZ and returns more TZ than it started with", async function () {
    const { trader, tz, ta, tb, amm } = await deployFixture();
    await approveTrader(amm, tz, ta, tb, trader);

    const tzAddress = await tz.getAddress();
    const taAddress = await ta.getAddress();
    const tbAddress = await tb.getAddress();
    const amountIn = toToken(10);
    const startTz = await tz.balanceOf(trader.address);

    const out1 = await amm.connect(trader).swap.staticCall(tzAddress, tbAddress, amountIn, 0);
    await amm.connect(trader).swap(tzAddress, tbAddress, amountIn, 0);

    const out2 = await amm.connect(trader).swap.staticCall(tbAddress, taAddress, out1, 0);
    await amm.connect(trader).swap(tbAddress, taAddress, out1, 0);

    const out3 = await amm.connect(trader).swap.staticCall(taAddress, tzAddress, out2, 0);
    await amm.connect(trader).swap(taAddress, tzAddress, out2, 0);

    const endTz = await tz.balanceOf(trader.address);
    expect(out3).to.be.gt(amountIn);
    expect(endTz).to.be.gt(startTz);
    expect(await ta.balanceOf(trader.address)).to.equal(0n);
    expect(await tb.balanceOf(trader.address)).to.equal(0n);
  });

  it("reduces the triangular price inconsistency after arbitrage", async function () {
    const { trader, tz, ta, tb, amm } = await deployFixture();
    await approveTrader(amm, tz, ta, tb, trader);

    const tzAddress = await tz.getAddress();
    const taAddress = await ta.getAddress();
    const tbAddress = await tb.getAddress();
    const metricBefore = await triangularPriceMetric(amm, tz, ta, tb);

    const amountIn = toToken(100);
    const out1 = await amm.connect(trader).swap.staticCall(tzAddress, tbAddress, amountIn, 0);
    await amm.connect(trader).swap(tzAddress, tbAddress, amountIn, 0);
    const out2 = await amm.connect(trader).swap.staticCall(tbAddress, taAddress, out1, 0);
    await amm.connect(trader).swap(tbAddress, taAddress, out1, 0);
    const out3 = await amm.connect(trader).swap.staticCall(taAddress, tzAddress, out2, 0);
    await amm.connect(trader).swap(taAddress, tzAddress, out2, 0);

    const metricAfter = await triangularPriceMetric(amm, tz, ta, tb);
    expect(metricAfter).to.be.lessThan(metricBefore);
    expect(out3).to.be.gt(amountIn);
  });
});
