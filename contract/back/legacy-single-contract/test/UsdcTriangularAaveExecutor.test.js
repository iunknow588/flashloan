const { expect } = require("chai");
const { ethers } = require("hardhat");

const UNIT = 1_000_000n;

async function futureDeadline() {
  const block = await ethers.provider.getBlock("latest");
  return BigInt(block.timestamp + 3600);
}

async function deployFixture() {
  const [owner, other] = await ethers.getSigners();

  const Token = await ethers.getContractFactory("TestERC20");
  const usdc = await Token.deploy("USD Coin", "USDC", 6, owner.address);
  const x = await Token.deploy("Token X", "X", 6, owner.address);
  const y = await Token.deploy("Token Y", "Y", 6, owner.address);

  const Pool = await ethers.getContractFactory("MockAavePool");
  const pool = await Pool.deploy(5);

  const Router = await ethers.getContractFactory("MockSwapRouter");
  const router = await Router.deploy(owner.address);

  const Executor = await ethers.getContractFactory("UsdcTriangularAaveExecutor");
  const executor = await Executor.deploy(await pool.getAddress(), await usdc.getAddress(), owner.address);

  const poolAddress = await pool.getAddress();
  const routerAddress = await router.getAddress();
  const executorAddress = await executor.getAddress();
  const usdcAddress = await usdc.getAddress();
  const xAddress = await x.getAddress();
  const yAddress = await y.getAddress();

  await usdc.mint(poolAddress, 10_000_000n * UNIT);
  for (const token of [usdc, x, y]) {
    await token.mint(routerAddress, 10_000_000n * UNIT);
  }

  return {
    owner,
    other,
    usdc,
    x,
    y,
    pool,
    router,
    executor,
    poolAddress,
    routerAddress,
    executorAddress,
    usdcAddress,
    xAddress,
    yAddress,
  };
}

function request(ctx, overrides = {}) {
  return {
    tokenX: ctx.xAddress,
    tokenY: ctx.yAddress,
    router: ctx.routerAddress,
    amount: 1_000n * UNIT,
    premiumBps: 5n,
    minProfitUsdc: 100n * UNIT,
    deadline: overrides.deadline ?? 0n,
    slippageBps: 50n,
    allowReverse: true,
    ...overrides,
  };
}

async function setForwardProfit(ctx) {
  await ctx.router.setRate(ctx.usdcAddress, ctx.xAddress, 2n, 1n);
  await ctx.router.setRate(ctx.xAddress, ctx.yAddress, 2n, 1n);
  await ctx.router.setRate(ctx.yAddress, ctx.usdcAddress, 3n, 10n);

  await ctx.router.setRate(ctx.usdcAddress, ctx.yAddress, 1n, 2n);
  await ctx.router.setRate(ctx.yAddress, ctx.xAddress, 1n, 1n);
  await ctx.router.setRate(ctx.xAddress, ctx.usdcAddress, 1n, 1n);
}

describe("UsdcTriangularAaveExecutor", function () {
  it("borrows USDC, executes USDC-X-Y-USDC, and repays Aave", async function () {
    const ctx = await deployFixture();
    await setForwardProfit(ctx);
    const arb = request(ctx, { deadline: await futureDeadline() });
    const premium = (arb.amount * 5n) / 10000n;

    const poolBefore = await ctx.usdc.balanceOf(ctx.poolAddress);
    await expect(ctx.executor.requestArbitrage(arb))
      .to.emit(ctx.executor, "TriangularArbitrageExecuted")
      .withArgs(false, ctx.xAddress, ctx.yAddress, arb.amount, premium, 1_200n * UNIT, 199_500_000n);

    const poolAfter = await ctx.usdc.balanceOf(ctx.poolAddress);
    expect(poolAfter - poolBefore).to.equal(premium);
    expect(await ctx.usdc.balanceOf(ctx.executorAddress)).to.equal(199_500_000n);
  });

  it("chooses the reverse USDC-Y-X-USDC route when it is better", async function () {
    const ctx = await deployFixture();

    await ctx.router.setRate(ctx.usdcAddress, ctx.xAddress, 1n, 1n);
    await ctx.router.setRate(ctx.xAddress, ctx.yAddress, 9n, 10n);
    await ctx.router.setRate(ctx.yAddress, ctx.usdcAddress, 1n, 1n);

    await ctx.router.setRate(ctx.usdcAddress, ctx.yAddress, 2n, 1n);
    await ctx.router.setRate(ctx.yAddress, ctx.xAddress, 2n, 1n);
    await ctx.router.setRate(ctx.xAddress, ctx.usdcAddress, 3n, 10n);

    const arb = request(ctx, { deadline: await futureDeadline() });
    const premium = (arb.amount * 5n) / 10000n;

    await expect(ctx.executor.requestArbitrage(arb))
      .to.emit(ctx.executor, "TriangularArbitrageExecuted")
      .withArgs(true, ctx.xAddress, ctx.yAddress, arb.amount, premium, 1_200n * UNIT, 199_500_000n);
  });

  it("reverts before borrowing when neither route can meet the profit target", async function () {
    const ctx = await deployFixture();

    await ctx.router.setRate(ctx.usdcAddress, ctx.xAddress, 1n, 1n);
    await ctx.router.setRate(ctx.xAddress, ctx.yAddress, 1n, 1n);
    await ctx.router.setRate(ctx.yAddress, ctx.usdcAddress, 1n, 1n);
    await ctx.router.setRate(ctx.usdcAddress, ctx.yAddress, 1n, 1n);
    await ctx.router.setRate(ctx.yAddress, ctx.xAddress, 1n, 1n);
    await ctx.router.setRate(ctx.xAddress, ctx.usdcAddress, 1n, 1n);

    const arb = request(ctx, { deadline: await futureDeadline(), minProfitUsdc: 1n });
    await expect(ctx.executor.requestArbitrage(arb))
      .to.be.revertedWithCustomError(ctx.executor, "NoViableRoute");
  });

  it("rejects non-owner requests", async function () {
    const ctx = await deployFixture();
    await setForwardProfit(ctx);
    const arb = request(ctx, { deadline: await futureDeadline() });

    await expect(ctx.executor.connect(ctx.other).requestArbitrage(arb))
      .to.be.revertedWithCustomError(ctx.executor, "NotOwner");
  });

  it("rejects direct callbacks from non-pool callers", async function () {
    const ctx = await deployFixture();
    const plan = ethers.AbiCoder.defaultAbiCoder().encode(
      [
        "tuple(address tokenX,address tokenY,address router,uint256 minProfitUsdc,uint256 deadline,uint256 slippageBps,bool allowReverse,uint256 startingUsdcBalance)",
      ],
      [[ctx.xAddress, ctx.yAddress, ctx.routerAddress, 0n, await futureDeadline(), 0n, true, 0n]]
    );

    await expect(ctx.executor.executeOperation(ctx.usdcAddress, 1n, 0n, ctx.executorAddress, plan))
      .to.be.revertedWithCustomError(ctx.executor, "NotPool");
  });
});
