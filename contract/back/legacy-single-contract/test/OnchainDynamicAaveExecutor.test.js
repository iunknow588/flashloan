const { expect } = require("chai");
const { ethers } = require("hardhat");

const UNIT = 10n ** 18n;

async function futureDeadline(seconds = 600) {
  const block = await ethers.provider.getBlock("latest");
  return BigInt(block.timestamp + seconds);
}

async function deployDynamicFixture() {
  const [owner, other] = await ethers.getSigners();

  const TestERC20 = await ethers.getContractFactory("TestERC20");
  const x = await TestERC20.deploy("Token X", "X", 18, owner.address);
  const y = await TestERC20.deploy("Token Y", "Y", 18, owner.address);
  const usdc = await TestERC20.deploy("Mock USDC", "USDC", 18, owner.address);

  const MockAavePool = await ethers.getContractFactory("MockAavePool");
  const pool = await MockAavePool.deploy(5);

  const MockSwapRouter = await ethers.getContractFactory("MockSwapRouter");
  const router = await MockSwapRouter.deploy(owner.address);

  const Dynamic = await ethers.getContractFactory("OnchainDynamicAaveExecutor");
  const executor = await Dynamic.deploy(await pool.getAddress(), owner.address);

  const xAddress = await x.getAddress();
  const yAddress = await y.getAddress();
  const usdcAddress = await usdc.getAddress();
  const poolAddress = await pool.getAddress();
  const routerAddress = await router.getAddress();
  const executorAddress = await executor.getAddress();

  await x.mint(poolAddress, 1_000_000n * UNIT);
  await y.mint(poolAddress, 1_000_000n * UNIT);
  for (const token of [x, y, usdc]) {
    await token.mint(routerAddress, 1_000_000n * UNIT);
  }

  async function setRate(tokenIn, tokenOut, numerator, denominator = 1n) {
    await router.setRate(tokenIn, tokenOut, numerator, denominator);
  }

  await setRate(xAddress, usdcAddress, 2n);
  await setRate(usdcAddress, yAddress, 2n);
  await setRate(yAddress, xAddress, 3n, 10n);
  await setRate(xAddress, yAddress, 1n);
  await setRate(yAddress, usdcAddress, 8n, 10n);
  await setRate(usdcAddress, xAddress, 4n, 10n);

  return {
    owner,
    other,
    x,
    y,
    usdc,
    pool,
    router,
    executor,
    xAddress,
    yAddress,
    usdcAddress,
    poolAddress,
    routerAddress,
    executorAddress,
  };
}

function dynamicRequest(ctx, overrides = {}) {
  return {
    xToken: ctx.xAddress,
    yToken: ctx.yAddress,
    usdc: ctx.usdcAddress,
    router: ctx.routerAddress,
    amountX: 1_000n * UNIT,
    amountY: 1_000n * UNIT,
    premiumBps: 5n,
    minProfitValueUsdc: 1n,
    deadline: overrides.deadline,
    slippageBps: 0n,
    ...overrides,
  };
}

describe("OnchainDynamicAaveExecutor", function () {
  it("quotes four routes on-chain, chooses the best route, and repays Aave", async function () {
    const ctx = await deployDynamicFixture();
    const request = dynamicRequest(ctx, { deadline: await futureDeadline() });
    const premium = (request.amountX * request.premiumBps) / 10000n;

    const poolBefore = await ctx.x.balanceOf(ctx.poolAddress);
    await expect(ctx.executor.requestDynamicFlashLoan(request))
      .to.emit(ctx.executor, "DynamicFlashLoanExecuted")
      .withArgs(ctx.xAddress, 0, request.amountX, premium);
    const poolAfter = await ctx.x.balanceOf(ctx.poolAddress);

    expect(poolAfter - poolBefore).to.equal(premium);
    expect(await ctx.y.balanceOf(ctx.executorAddress)).to.be.gt(0n);
  });

  it("reverts before borrowing when no route can repay", async function () {
    const ctx = await deployDynamicFixture();
    await ctx.router.setRate(ctx.yAddress, ctx.xAddress, 1n, 100n);
    await ctx.router.setRate(ctx.yAddress, ctx.usdcAddress, 1n, 100n);
    const request = dynamicRequest(ctx, { deadline: await futureDeadline() });

    await expect(ctx.executor.requestDynamicFlashLoan(request))
      .to.be.revertedWithCustomError(ctx.executor, "NoViableRoute");
  });

  it("skips unquotable candidate routes and executes a viable route", async function () {
    const ctx = await deployDynamicFixture();
    const MockSwapRouter = await ethers.getContractFactory("MockSwapRouter");
    const sparseRouter = await MockSwapRouter.deploy(ctx.owner.address);
    const sparseRouterAddress = await sparseRouter.getAddress();

    for (const token of [ctx.x, ctx.y, ctx.usdc]) {
      await token.mint(sparseRouterAddress, 1_000_000n * UNIT);
    }
    await sparseRouter.setRate(ctx.xAddress, ctx.usdcAddress, 2n, 1n);
    await sparseRouter.setRate(ctx.usdcAddress, ctx.yAddress, 2n, 1n);
    await sparseRouter.setRate(ctx.yAddress, ctx.xAddress, 3n, 10n);

    const request = dynamicRequest(ctx, {
      router: sparseRouterAddress,
      deadline: await futureDeadline(),
    });
    await expect(ctx.executor.requestDynamicFlashLoan(request))
      .to.emit(ctx.executor, "DynamicFlashLoanExecuted");
  });

  it("chooses a smaller borrow amount before the flash loan when larger amounts are unquotable", async function () {
    const ctx = await deployDynamicFixture();
    const MockSwapRouter = await ethers.getContractFactory("MockSwapRouter");
    const cappedRouter = await MockSwapRouter.deploy(ctx.owner.address);
    const cappedRouterAddress = await cappedRouter.getAddress();

    for (const token of [ctx.x, ctx.y, ctx.usdc]) {
      await token.mint(cappedRouterAddress, 1_000_000n * UNIT);
    }
    await cappedRouter.setRate(ctx.xAddress, ctx.usdcAddress, 2n, 1n);
    await cappedRouter.setRate(ctx.usdcAddress, ctx.yAddress, 2n, 1n);
    await cappedRouter.setRate(ctx.yAddress, ctx.xAddress, 3n, 10n);
    await cappedRouter.setRate(ctx.yAddress, ctx.usdcAddress, 8n, 10n);
    await cappedRouter.setMaxAmountIn(ctx.yAddress, ctx.xAddress, 1_500n * UNIT);

    const request = dynamicRequest(ctx, {
      router: cappedRouterAddress,
      amountY: 0n,
      deadline: await futureDeadline(),
    });
    const selectedAmount = request.amountX / 4n;
    const premium = (selectedAmount * request.premiumBps) / 10000n;

    await expect(ctx.executor.requestDynamicFlashLoan(request))
      .to.emit(ctx.executor, "DynamicFlashLoanExecuted")
      .withArgs(ctx.xAddress, 0, selectedAmount, premium);
  });

  it("rejects non-owner requests", async function () {
    const ctx = await deployDynamicFixture();
    const request = dynamicRequest(ctx, { deadline: await futureDeadline() });

    await expect(ctx.executor.connect(ctx.other).requestDynamicFlashLoan(request))
      .to.be.revertedWithCustomError(ctx.executor, "NotOwner");
  });
});
