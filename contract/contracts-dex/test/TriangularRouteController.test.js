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
  const z = await Token.deploy("Token Z", "Z", 6, owner.address);
  const w = await Token.deploy("Token W", "W", 6, owner.address);

  const Pool = await ethers.getContractFactory("MockAavePool");
  const pool = await Pool.deploy(5);

  const Router = await ethers.getContractFactory("MockSwapRouter");
  const router = await Router.deploy(owner.address);

  const Executor = await ethers.getContractFactory("AaveTriangularExecutor");
  const executor = await Executor.deploy(await pool.getAddress(), await usdc.getAddress(), owner.address);

  const Controller = await ethers.getContractFactory("TriangularRouteController");
  const controller = await Controller.deploy(await usdc.getAddress(), await executor.getAddress(), owner.address);
  await executor.setController(await controller.getAddress());

  const poolAddress = await pool.getAddress();
  const routerAddress = await router.getAddress();
  const executorAddress = await executor.getAddress();
  const controllerAddress = await controller.getAddress();
  const usdcAddress = await usdc.getAddress();
  const xAddress = await x.getAddress();
  const yAddress = await y.getAddress();

  await usdc.mint(poolAddress, 10_000_000n * UNIT);
  for (const token of [usdc, x, y, z, w]) {
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
    controller,
    poolAddress,
    routerAddress,
    executorAddress,
    controllerAddress,
    usdcAddress,
    xAddress,
    yAddress,
    zAddress: await z.getAddress(),
    wAddress: await w.getAddress(),
  };
}

function routeRequest(ctx, overrides = {}) {
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

describe("TriangularRouteController + AaveTriangularExecutor", function () {
  it("lets A choose the route and B execute, repay Aave, and return profit to A", async function () {
    const ctx = await deployFixture();
    await setForwardProfit(ctx);
    const request = routeRequest(ctx, { deadline: await futureDeadline() });
    const premium = (request.amount * request.premiumBps) / 10000n;

    const poolBefore = await ctx.usdc.balanceOf(ctx.poolAddress);
    await expect(ctx.controller.run(request))
      .to.emit(ctx.executor, "RouteExecuted")
      .withArgs(ctx.controllerAddress, ctx.routerAddress, request.amount, premium, 1_200n * UNIT, 199_500_000n)
      .and.to.emit(ctx.controller, "RouteSubmitted")
      .withArgs(false, ctx.xAddress, ctx.yAddress, request.amount, 1_200n * UNIT, 199_500_000n);

    const poolAfter = await ctx.usdc.balanceOf(ctx.poolAddress);
    expect(poolAfter - poolBefore).to.equal(premium);
    expect(await ctx.usdc.balanceOf(ctx.controllerAddress)).to.equal(199_500_000n);
    expect(await ctx.usdc.balanceOf(ctx.executorAddress)).to.equal(0n);
  });

  it("lets A choose the reverse route when it quotes better", async function () {
    const ctx = await deployFixture();

    await ctx.router.setRate(ctx.usdcAddress, ctx.xAddress, 1n, 1n);
    await ctx.router.setRate(ctx.xAddress, ctx.yAddress, 9n, 10n);
    await ctx.router.setRate(ctx.yAddress, ctx.usdcAddress, 1n, 1n);

    await ctx.router.setRate(ctx.usdcAddress, ctx.yAddress, 2n, 1n);
    await ctx.router.setRate(ctx.yAddress, ctx.xAddress, 2n, 1n);
    await ctx.router.setRate(ctx.xAddress, ctx.usdcAddress, 3n, 10n);

    const request = routeRequest(ctx, { deadline: await futureDeadline() });
    await expect(ctx.controller.run(request))
      .to.emit(ctx.controller, "RouteSubmitted")
      .withArgs(true, ctx.xAddress, ctx.yAddress, request.amount, 1_200n * UNIT, 199_500_000n);
  });

  it("reports the relative edge and required P threshold before execution", async function () {
    const ctx = await deployFixture();
    await setForwardProfit(ctx);
    const request = routeRequest(ctx, { deadline: await futureDeadline() });

    const decision = await ctx.controller.previewBestRoute.staticCall(request);

    expect(decision[0]).to.equal(true);
    expect(decision[1]).to.equal(false);
    expect(decision[7]).to.equal(500n * UNIT);
    expect(decision[8]).to.equal(4_000n * UNIT);
    expect(decision[5]).to.equal(70_000n);
    expect(decision[6]).to.equal(1_055n);
  });

  it("chooses the best route from multiple candidate groups", async function () {
    const ctx = await deployFixture();
    await setForwardProfit(ctx);

    await ctx.router.setRate(ctx.usdcAddress, ctx.zAddress, 2n, 1n);
    await ctx.router.setRate(ctx.zAddress, ctx.wAddress, 2n, 1n);
    await ctx.router.setRate(ctx.wAddress, ctx.usdcAddress, 7n, 20n);

    await ctx.router.setRate(ctx.usdcAddress, ctx.wAddress, 1n, 2n);
    await ctx.router.setRate(ctx.wAddress, ctx.zAddress, 1n, 1n);
    await ctx.router.setRate(ctx.zAddress, ctx.usdcAddress, 1n, 1n);

    const deadline = await futureDeadline();
    const requests = [
      routeRequest(ctx, { deadline }),
      routeRequest(ctx, { tokenX: ctx.zAddress, tokenY: ctx.wAddress, deadline }),
    ];

    const preview = await ctx.controller.previewBestRouteFrom.staticCall(requests);
    expect(preview[0]).to.equal(1n);
    expect(preview[1].quotedFinalUsdc).to.equal(1_400n * UNIT);

    await expect(ctx.controller.runBest(requests))
      .to.emit(ctx.controller, "BatchRouteSubmitted")
      .withArgs(1n, false, ctx.zAddress, ctx.wAddress, 1_000n * UNIT, 1_400n * UNIT, 399_500_000n);
  });

  it("blocks direct B execution from non-controller callers", async function () {
    const ctx = await deployFixture();
    await setForwardProfit(ctx);
    const deadline = await futureDeadline();
    const executionRequest = {
      tokenX: ctx.xAddress,
      tokenY: ctx.yAddress,
      router: ctx.routerAddress,
      amount: 1_000n * UNIT,
      minProfitUsdc: 100n * UNIT,
      deadline,
      slippageBps: 50n,
    };

    await expect(ctx.executor.execute(executionRequest))
      .to.be.revertedWithCustomError(ctx.executor, "NotController");
  });

  it("blocks non-owner callers at A", async function () {
    const ctx = await deployFixture();
    await setForwardProfit(ctx);
    const request = routeRequest(ctx, { deadline: await futureDeadline() });

    await expect(ctx.controller.connect(ctx.other).run(request))
      .to.be.revertedWithCustomError(ctx.controller, "NotOwner");
  });

  it("reverts before B borrows when A cannot find a viable route", async function () {
    const ctx = await deployFixture();

    await ctx.router.setRate(ctx.usdcAddress, ctx.xAddress, 1n, 1n);
    await ctx.router.setRate(ctx.xAddress, ctx.yAddress, 1n, 1n);
    await ctx.router.setRate(ctx.yAddress, ctx.usdcAddress, 1n, 1n);
    await ctx.router.setRate(ctx.usdcAddress, ctx.yAddress, 1n, 1n);
    await ctx.router.setRate(ctx.yAddress, ctx.xAddress, 1n, 1n);
    await ctx.router.setRate(ctx.xAddress, ctx.usdcAddress, 1n, 1n);

    const request = routeRequest(ctx, { deadline: await futureDeadline(), minProfitUsdc: 1n });
    await expect(ctx.controller.run(request))
      .to.be.revertedWithCustomError(ctx.controller, "NoViableRoute");
  });
});
