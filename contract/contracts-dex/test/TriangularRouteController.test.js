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
  const zAddress = await z.getAddress();
  const wAddress = await w.getAddress();

  await controller.setExecutionConfig(routerAddress, 1_000n * UNIT, 100n * UNIT, 3600n, 50n);

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
    zAddress,
    wAddress,
  };
}

function candidatePair(ctx) {
  return [ctx.xAddress, ctx.yAddress];
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
  it("lets A choose the route and B execute, repay Aave, and sweep profit to B owner above threshold", async function () {
    const ctx = await deployFixture();
    await setForwardProfit(ctx);
    const premium = (1_000n * UNIT * await ctx.executor.flashLoanPremiumBps()) / 10000n;
    expect(await ctx.executor.profitSweepThresholdUsdc()).to.equal(100n * UNIT);

    const poolBefore = await ctx.usdc.balanceOf(ctx.poolAddress);
    const ownerBefore = await ctx.usdc.balanceOf(ctx.owner.address);
    await expect(ctx.controller.run(candidatePair(ctx)))
      .to.emit(ctx.executor, "RouteExecuted")
      .withArgs(ctx.controllerAddress, ctx.routerAddress, 1_000n * UNIT, premium, 1_200n * UNIT, 199_500_000n)
      .and.to.emit(ctx.executor, "ProfitSwept")
      .withArgs(ctx.owner.address, 199_500_000n, 100n * UNIT)
      .and.to.emit(ctx.controller, "RouteSubmitted")
      .withArgs(false, ctx.xAddress, ctx.yAddress, 1_000n * UNIT, 1_200n * UNIT, 199_500_000n);

    const poolAfter = await ctx.usdc.balanceOf(ctx.poolAddress);
    expect(poolAfter - poolBefore).to.equal(premium);
    expect(await ctx.usdc.balanceOf(ctx.owner.address) - ownerBefore).to.equal(199_500_000n);
    expect(await ctx.usdc.balanceOf(ctx.controllerAddress)).to.equal(0n);
    expect(await ctx.usdc.balanceOf(ctx.executorAddress)).to.equal(0n);
  });

  it("accumulates B USDC below the sweep threshold and sweeps all balance once exceeded", async function () {
    const ctx = await deployFixture();
    await setForwardProfit(ctx);
    await ctx.executor.setProfitSweepThresholdUsdc(250n * UNIT);

    const ownerBefore = await ctx.usdc.balanceOf(ctx.owner.address);
    await ctx.controller.run(candidatePair(ctx));

    expect(await ctx.usdc.balanceOf(ctx.owner.address)).to.equal(ownerBefore);
    expect(await ctx.usdc.balanceOf(ctx.controllerAddress)).to.equal(0n);
    expect(await ctx.usdc.balanceOf(ctx.executorAddress)).to.equal(199_500_000n);

    await expect(ctx.controller.run(candidatePair(ctx)))
      .to.emit(ctx.executor, "ProfitSwept")
      .withArgs(ctx.owner.address, 399_000_000n, 250n * UNIT);

    expect(await ctx.usdc.balanceOf(ctx.owner.address) - ownerBefore).to.equal(399_000_000n);
    expect(await ctx.usdc.balanceOf(ctx.executorAddress)).to.equal(0n);
  });

  it("restricts profit sweep threshold updates to B owner", async function () {
    const ctx = await deployFixture();

    await expect(ctx.executor.connect(ctx.other).setProfitSweepThresholdUsdc(1n))
      .to.be.revertedWithCustomError(ctx.executor, "NotOwner");

    await expect(ctx.executor.setProfitSweepThresholdUsdc(42n))
      .to.emit(ctx.executor, "ProfitSweepThresholdSet")
      .withArgs(100n * UNIT, 42n);
    expect(await ctx.executor.profitSweepThresholdUsdc()).to.equal(42n);
  });

  it("lets A choose the reverse route when it quotes better", async function () {
    const ctx = await deployFixture();

    await ctx.router.setRate(ctx.usdcAddress, ctx.xAddress, 1n, 1n);
    await ctx.router.setRate(ctx.xAddress, ctx.yAddress, 9n, 10n);
    await ctx.router.setRate(ctx.yAddress, ctx.usdcAddress, 1n, 1n);

    await ctx.router.setRate(ctx.usdcAddress, ctx.yAddress, 2n, 1n);
    await ctx.router.setRate(ctx.yAddress, ctx.xAddress, 2n, 1n);
    await ctx.router.setRate(ctx.xAddress, ctx.usdcAddress, 3n, 10n);

    await expect(ctx.controller.run(candidatePair(ctx)))
      .to.emit(ctx.controller, "RouteSubmitted")
      .withArgs(true, ctx.yAddress, ctx.xAddress, 1_000n * UNIT, 1_200n * UNIT, 199_500_000n);
  });

  it("reports the relative edge and required P threshold before execution", async function () {
    const ctx = await deployFixture();
    await setForwardProfit(ctx);

    const preview = await ctx.controller.previewBestRoute.staticCall(candidatePair(ctx));
    const decision = preview[1];

    expect(decision[0]).to.equal(true);
    expect(decision[1]).to.equal(false);
    expect(decision[7]).to.equal(500n * UNIT);
    expect(decision[8]).to.equal(4_000n * UNIT);
    expect(decision[5]).to.equal(70_000n);
    expect(decision[6]).to.equal(1_055n);
    expect(decision[12]).to.equal(1_194n * UNIT);

    await ctx.pool.setPremiumBps(17);
    const updatedPreview = await ctx.controller.previewBestRoute.staticCall(candidatePair(ctx));
    const updatedDecision = updatedPreview[1];
    expect(updatedDecision[6]).to.equal(1_067n);
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

    const tokens = [ctx.xAddress, ctx.yAddress, ctx.zAddress, ctx.wAddress];
    const preview = await ctx.controller.previewBestRoute.staticCall(tokens);
    expect(preview[0]).to.equal(5n);
    expect(preview[1].quotedFinalUsdc).to.equal(1_400n * UNIT);

    await expect(ctx.controller.run(tokens))
      .to.emit(ctx.controller, "RouteSubmitted")
      .withArgs(false, ctx.zAddress, ctx.wAddress, 1_000n * UNIT, 1_400n * UNIT, 399_500_000n);
  });

  it("doubles from the configured borrow amount and refines after profit declines", async function () {
    const ctx = await deployFixture();

    await ctx.router.setRate(ctx.usdcAddress, ctx.xAddress, 1n, 1n);
    await ctx.router.setRate(ctx.xAddress, ctx.yAddress, 1n, 1n);
    await ctx.router.setRate(ctx.yAddress, ctx.usdcAddress, 2n, 1n);
    await ctx.router.setImpactBpsPerUnit(ctx.yAddress, ctx.usdcAddress, 2n);

    await ctx.router.setRate(ctx.usdcAddress, ctx.yAddress, 1n, 2n);
    await ctx.router.setRate(ctx.yAddress, ctx.xAddress, 1n, 1n);
    await ctx.router.setRate(ctx.xAddress, ctx.usdcAddress, 1n, 1n);

    await ctx.controller.setExecutionConfig(ctx.routerAddress, 1_000n * UNIT, 100n * UNIT, 3600n, 50n);
    await ctx.controller.setAmountSearchConfig(1_000n * UNIT, 2_000n * UNIT, 6n, 5000n);

    const preview = await ctx.controller.previewBestRoute.staticCall(candidatePair(ctx));
    const decision = preview[1];

    expect(decision.viable).to.equal(true);
    expect(decision.probeAmount).to.equal(1_000n * UNIT);
    expect(decision.probeProfitUsdc).to.equal(599_500_000n);
    expect(decision.selectedAmount).to.equal(1_250n * UNIT);
    expect(decision.routeMaxBorrow).to.equal(2_000n * UNIT);
    expect(decision.quotedFinalUsdc).to.equal(1_875n * UNIT);
    expect(decision.profitUsdc).to.equal(624_375_000n);
    expect(decision.fundingCostUsdc).to.equal(625_000n);

    await expect(ctx.controller.run(candidatePair(ctx)))
      .to.emit(ctx.controller, "RouteSubmitted")
      .withArgs(false, ctx.xAddress, ctx.yAddress, 1_250n * UNIT, 1_875n * UNIT, 624_375_000n);
  });

  it("uses the configured borrow amount as a profit probe before optimizing selectedAmount", async function () {
    const ctx = await deployFixture();

    await ctx.router.setRate(ctx.usdcAddress, ctx.xAddress, 1n, 1n);
    await ctx.router.setRate(ctx.xAddress, ctx.yAddress, 1n, 1n);
    await ctx.router.setRate(ctx.yAddress, ctx.usdcAddress, 1n, 1n);

    await ctx.router.setRate(ctx.usdcAddress, ctx.yAddress, 1n, 2n);
    await ctx.router.setRate(ctx.yAddress, ctx.xAddress, 1n, 1n);
    await ctx.router.setRate(ctx.xAddress, ctx.usdcAddress, 1n, 1n);

    await ctx.controller.setExecutionConfig(ctx.routerAddress, 1_000n * UNIT, 100n * UNIT, 3600n, 50n);
    await ctx.controller.setAmountSearchConfig(1_000n * UNIT, 2_000n * UNIT, 6n, 5000n);

    const preview = await ctx.controller.previewBestRoute.staticCall(candidatePair(ctx));
    const decision = preview[1];

    expect(decision.viable).to.equal(false);
    expect(decision.selectedAmount).to.equal(1_000n * UNIT);
    expect(decision.probeAmount).to.equal(1_000n * UNIT);
    expect(decision.probeProfitUsdc).to.equal(0n);
    expect(decision.fundingCostUsdc).to.equal(500_000n);
    expect(decision.failureCode).to.equal(await ctx.controller.FAIL_FINAL_BELOW_REQUIRED());
  });

  it("rejects invalid amount search configs", async function () {
    const ctx = await deployFixture();

    await expect(ctx.controller.setAmountSearchConfig(0n, 1n, 1n, 0n))
      .to.be.revertedWithCustomError(ctx.controller, "InvalidRequest");
    await expect(ctx.controller.setAmountSearchConfig(2n, 1n, 1n, 0n))
      .to.be.revertedWithCustomError(ctx.controller, "InvalidRequest");
    await expect(ctx.controller.setAmountSearchConfig(1n, 2n, 17n, 0n))
      .to.be.revertedWithCustomError(ctx.controller, "InvalidRequest");
    await expect(ctx.controller.setAmountSearchConfig(1n, 999n * UNIT, 1n, 0n))
      .to.be.revertedWithCustomError(ctx.controller, "InvalidRequest");
    await expect(ctx.controller.connect(ctx.other).setAmountSearchConfig(1n, 2n, 1n, 0n))
      .to.be.revertedWithCustomError(ctx.controller, "NotOwner");
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
      deadline,
      amountOutMinUsdc: 1_194n * UNIT,
    };

    await expect(ctx.executor.execute(executionRequest))
      .to.be.revertedWithCustomError(ctx.executor, "NotController");
  });

  it("blocks non-owner callers at A", async function () {
    const ctx = await deployFixture();
    await setForwardProfit(ctx);

    await expect(ctx.controller.connect(ctx.other).run(candidatePair(ctx)))
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

    await ctx.controller.setExecutionConfig(ctx.routerAddress, 1_000n * UNIT, 1n, 3600n, 50n);
    const preview = await ctx.controller.previewBestRoute.staticCall(candidatePair(ctx));
    const decision = preview[1];
    expect(decision.failureCode).to.equal(await ctx.controller.FAIL_EDGE_BELOW_REQUIRED());
    expect(decision.requiredFinalUsdc).to.equal(1_000_500_001n);
    await expect(ctx.controller.run(candidatePair(ctx)))
      .to.be.revertedWithCustomError(ctx.controller, "NoViableRoute")
      .withArgs(
        await ctx.controller.FAIL_EDGE_BELOW_REQUIRED(),
        0n,
        56n,
        0n,
        1_000_500_001n,
        0n,
      );
  });

  it("does not treat break-even after Aave premium as a viable route", async function () {
    const ctx = await deployFixture();

    await ctx.router.setRate(ctx.usdcAddress, ctx.xAddress, 1n, 1n);
    await ctx.router.setRate(ctx.xAddress, ctx.yAddress, 1n, 1n);
    await ctx.router.setRate(ctx.yAddress, ctx.usdcAddress, 10005n, 10000n);
    await ctx.router.setRate(ctx.usdcAddress, ctx.yAddress, 9n, 10n);

    await ctx.controller.setExecutionConfig(ctx.routerAddress, 1_000n * UNIT, 0n, 3600n, 0n);
    const preview = await ctx.controller.previewBestRoute.staticCall(candidatePair(ctx));
    const decision = preview[1];

    expect(decision.viable).to.equal(false);
    expect(decision.failureCode).to.equal(await ctx.controller.FAIL_FINAL_BELOW_REQUIRED());
    expect(decision.quotedFinalUsdc).to.equal(1_000_500_000n);
    expect(decision.requiredFinalUsdc).to.equal(1_000_500_001n);
  });

  it("requires B's actual post-swap balance to cover the real Aave repayment", async function () {
    const ctx = await deployFixture();
    await ctx.executor.setController(ctx.owner.address);
    await ctx.router.setRate(ctx.usdcAddress, ctx.xAddress, 1n, 1n);
    await ctx.router.setRate(ctx.xAddress, ctx.yAddress, 1n, 1n);
    await ctx.router.setRate(ctx.yAddress, ctx.usdcAddress, 1n, 1n);
    const amount = 1_000n * UNIT;
    const premium = (amount * await ctx.executor.flashLoanPremiumBps()) / 10000n;
    const owed = amount + premium;
    const executionRequest = {
      tokenX: ctx.xAddress,
      tokenY: ctx.yAddress,
      router: ctx.routerAddress,
      amount,
      deadline: await futureDeadline(),
      amountOutMinUsdc: 1n,
    };

    await expect(ctx.executor.execute(executionRequest))
      .to.be.revertedWithCustomError(ctx.executor, "ExecutionConstraintFailed")
      .withArgs(
        await ctx.executor.FAIL_POST_SWAP_BALANCE_BELOW_REPAYMENT(),
        1n,
        owed,
        amount,
        amount,
        owed,
      );
  });

  it("reports the exact missing middle-hop quote before execution", async function () {
    const ctx = await deployFixture();

    await ctx.router.setRate(ctx.usdcAddress, ctx.xAddress, 2n, 1n);
    await ctx.router.setRate(ctx.usdcAddress, ctx.yAddress, 1n, 1n);

    const preview = await ctx.controller.previewBestRoute.staticCall(candidatePair(ctx));
    const decision = preview[1];

    expect(decision.viable).to.equal(false);
    expect(decision.failureCode).to.equal(await ctx.controller.FAIL_MIDDLE_HOP_QUOTE());
    expect(decision.path).to.deep.equal([ctx.usdcAddress, ctx.xAddress, ctx.yAddress, ctx.usdcAddress]);
  });

  it("keeps the most useful batch failure when no candidate is viable", async function () {
    const ctx = await deployFixture();
    await ctx.controller.setExecutionConfig(ctx.routerAddress, 1_000n * UNIT, 1n, 3600n, 50n);

    await ctx.router.setRate(ctx.usdcAddress, ctx.xAddress, 2n, 1n);
    await ctx.router.setRate(ctx.usdcAddress, ctx.yAddress, 1n, 1n);

    await ctx.router.setRate(ctx.usdcAddress, ctx.zAddress, 1n, 1n);
    await ctx.router.setRate(ctx.zAddress, ctx.wAddress, 1n, 1n);
    await ctx.router.setRate(ctx.wAddress, ctx.usdcAddress, 1n, 1n);
    await ctx.router.setRate(ctx.usdcAddress, ctx.wAddress, 1n, 1n);
    await ctx.router.setRate(ctx.wAddress, ctx.zAddress, 1n, 1n);
    await ctx.router.setRate(ctx.zAddress, ctx.usdcAddress, 1n, 1n);

    const tokens = [ctx.xAddress, ctx.yAddress, ctx.zAddress, ctx.wAddress];
    const preview = await ctx.controller.previewBestRoute.staticCall(tokens);
    expect(preview[0]).to.equal(5n);
    expect(preview[1].failureCode).to.equal(await ctx.controller.FAIL_EDGE_BELOW_REQUIRED());

    await expect(ctx.controller.run(tokens))
      .to.be.revertedWithCustomError(ctx.controller, "NoViableRoute")
      .withArgs(
        await ctx.controller.FAIL_EDGE_BELOW_REQUIRED(),
        0n,
        56n,
        0n,
        1_000_500_001n,
        0n,
      );
  });
});
