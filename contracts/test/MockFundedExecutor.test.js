const { expect } = require("chai");
const { ethers } = require("hardhat");

const ONE_USDC = 1_000_000n;
const ONE_TOKEN = 1_000_000_000_000_000_000n;
const USE_FULL_BALANCE = ethers.MaxUint256;

async function deployFixture() {
  const [owner, other] = await ethers.getSigners();

  const TestERC20 = await ethers.getContractFactory("TestERC20");
  const usdc = await TestERC20.deploy("Test USDC", "tUSDC", 6, owner.address);
  const token = await TestERC20.deploy("Test Arb Token", "tARB", 18, owner.address);

  const MockSwapRouter = await ethers.getContractFactory("MockSwapRouter");
  const router = await MockSwapRouter.deploy(owner.address);

  const MockFundedExecutor = await ethers.getContractFactory("MockFundedExecutor");
  const executor = await MockFundedExecutor.deploy(owner.address);

  const usdcAddress = await usdc.getAddress();
  const tokenAddress = await token.getAddress();
  const routerAddress = await router.getAddress();
  const executorAddress = await executor.getAddress();

  await usdc.mint(executorAddress, 1_000n * ONE_USDC);
  await usdc.mint(routerAddress, 1_000_000n * ONE_USDC);
  await token.mint(routerAddress, 1_000_000n * ONE_TOKEN);

  await router.setRate(usdcAddress, tokenAddress, ONE_TOKEN, ONE_USDC);
  await router.setRate(tokenAddress, usdcAddress, ONE_USDC, ONE_TOKEN);

  return { owner, other, usdc, token, router, executor, usdcAddress, tokenAddress, routerAddress, executorAddress };
}

async function buildSteps(ctx, overrides = {}) {
  return [
    {
      router: ctx.routerAddress,
      tokenIn: ctx.usdcAddress,
      tokenOut: ctx.tokenAddress,
      amountIn: overrides.amountIn ?? ONE_USDC,
      amountOutMin: overrides.firstMinOut ?? 1n,
      path: [ctx.usdcAddress, ctx.tokenAddress],
    },
    {
      router: ctx.routerAddress,
      tokenIn: ctx.tokenAddress,
      tokenOut: ctx.usdcAddress,
      amountIn: USE_FULL_BALANCE,
      amountOutMin: overrides.finalMinOut ?? 1n,
      path: [ctx.tokenAddress, ctx.usdcAddress],
    },
  ];
}

async function futureDeadline(seconds = 600) {
  const block = await ethers.provider.getBlock("latest");
  return BigInt(block.timestamp + seconds);
}

describe("MockFundedExecutor", function () {
  it("executes a funded roundtrip", async function () {
    const ctx = await deployFixture();
    const steps = await buildSteps(ctx);
    const deadline = await futureDeadline();

    const before = await ctx.usdc.balanceOf(ctx.executorAddress);
    await expect(ctx.executor.executePlan(steps, ctx.usdcAddress, 0, deadline))
      .to.emit(ctx.executor, "PlanExecuted");
    const after = await ctx.usdc.balanceOf(ctx.executorAddress);

    expect(after).to.equal(before);
  });

  it("reverts when amountOutMin is too high", async function () {
    const ctx = await deployFixture();
    const steps = await buildSteps(ctx, { firstMinOut: 2n * ONE_TOKEN });
    const deadline = await futureDeadline();

    await expect(ctx.executor.executePlan(steps, ctx.usdcAddress, 0, deadline))
      .to.be.revertedWithCustomError(ctx.router, "AmountOutTooLow");
  });

  it("reverts when deadline has expired", async function () {
    const ctx = await deployFixture();
    const steps = await buildSteps(ctx);
    const block = await ethers.provider.getBlock("latest");
    const expiredDeadline = BigInt(block.timestamp - 1);

    await expect(ctx.executor.executePlan(steps, ctx.usdcAddress, 0, expiredDeadline))
      .to.be.revertedWithCustomError(ctx.executor, "DeadlineExpired");
  });

  it("reverts when minProfit is not met", async function () {
    const ctx = await deployFixture();
    const steps = await buildSteps(ctx);
    const deadline = await futureDeadline();

    await expect(ctx.executor.executePlan(steps, ctx.usdcAddress, 1, deadline))
      .to.be.revertedWithCustomError(ctx.executor, "ProfitTooLow");
  });

  it("rejects non-owner execution", async function () {
    const ctx = await deployFixture();
    const steps = await buildSteps(ctx);
    const deadline = await futureDeadline();

    await expect(ctx.executor.connect(ctx.other).executePlan(steps, ctx.usdcAddress, 0, deadline))
      .to.be.revertedWithCustomError(ctx.executor, "NotOwner");
  });
});
