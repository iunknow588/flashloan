const { expect } = require("chai");
const { ethers } = require("hardhat");

const STRATEGY = {
  adapterUniswapV3: 1n,
  executionKindUsdcTriangular: 1n,
  executionKindTokenCrossPool: 2n,
  routeUxyu: 1n,
  stepCheckedFailed: 1n,
  stepSelected: 2n,
  statusXyCrossPool: 3n,
  statusXyUsdcFallback: 4n,
  errNotEnoughPools: 1n,
  errNoPriceSpread: 2n,
  errBorrowAssetDisabled: 5n,
  errRouteLayoutInvalid: 6n,
  errNoProfitableRoute: 55555n,
};

function emptyRuntimePools() {
  return Array.from({ length: 5 }, () => ({ adapterKind: 0n, pool: ethers.ZeroAddress }));
}

function runtimePools(entries) {
  const pools = emptyRuntimePools();
  for (const [index, pool, adapterKind = 1n] of entries) {
    pools[index] = { adapterKind, pool };
  }
  return pools;
}

function runtimeTrade(tradeIndex, tokenX, tokenY, pools) {
  return { tradeIndex, tokenX, tokenY, pools };
}

async function deployV3Pool(ctx, token0, token1, tick, fee, liquidity = 1_000_000n) {
  const V3Pool = await ethers.getContractFactory("MockV3Pool");
  const sqrtPriceX96 = 1n << 96n;
  const pool = await V3Pool.deploy(ctx.factory.address, token0, token1, fee, liquidity, sqrtPriceX96, tick);
  return { pool, address: await pool.getAddress(), fee };
}

async function deployFixture() {
  const [owner, other, factory] = await ethers.getSigners();

  const Token = await ethers.getContractFactory("TestERC20");
  const usdc = await Token.deploy("USD Coin", "USDC", 6, owner.address);
  const x = await Token.deploy("Token X", "X", 18, owner.address);
  const y = await Token.deploy("Token Y", "Y", 18, owner.address);

  const AavePool = await ethers.getContractFactory("MockAavePool");
  const pool = await AavePool.deploy(5n);

  const Router = await ethers.getContractFactory("MockV3SwapRouter");
  const router = await Router.deploy(owner.address);

  const Executor = await ethers.getContractFactory("UnifiedFlashLoanMevExecutor");
  const executor = await Executor.deploy(await pool.getAddress(), await usdc.getAddress(), owner.address);

  await executor.setAdapterConfig(
    STRATEGY.adapterUniswapV3,
    true,
    factory.address,
    await router.getAddress(),
    await router.getAddress(),
  );

  const usdcAddress = await usdc.getAddress();
  const xAddress = await x.getAddress();
  const yAddress = await y.getAddress();
  const poolAddress = await pool.getAddress();
  const routerAddress = await router.getAddress();

  await usdc.mint(poolAddress, 10_000_000n);
  await x.mint(poolAddress, 10_000_000n);
  await usdc.mint(routerAddress, 10_000_000n);
  await x.mint(routerAddress, 10_000_000n);

  return {
    owner,
    other,
    factory,
    usdc,
    x,
    y,
    pool,
    router,
    executor,
    usdcAddress,
    xAddress,
    yAddress,
  };
}

async function buildTriangularTrades(ctx) {
  const uxLow = await deployV3Pool(ctx, ctx.usdcAddress, ctx.xAddress, -120, 500n);
  const uxHigh = await deployV3Pool(ctx, ctx.usdcAddress, ctx.xAddress, 220, 3000n);
  const uyLow = await deployV3Pool(ctx, ctx.usdcAddress, ctx.yAddress, -150, 500n);
  const uyHigh = await deployV3Pool(ctx, ctx.usdcAddress, ctx.yAddress, 180, 3000n);
  const xyLow = await deployV3Pool(ctx, ctx.xAddress, ctx.yAddress, -100, 500n);
  const xyHigh = await deployV3Pool(ctx, ctx.xAddress, ctx.yAddress, 250, 3000n);

  const trades = [
    runtimeTrade(101n, ctx.usdcAddress, ctx.xAddress, runtimePools([[0, uxLow.address], [1, uxHigh.address]])),
    runtimeTrade(102n, ctx.usdcAddress, ctx.yAddress, runtimePools([[0, uyLow.address], [1, uyHigh.address]])),
    runtimeTrade(103n, ctx.xAddress, ctx.yAddress, runtimePools([[0, xyLow.address], [1, xyHigh.address]])),
    runtimeTrade(104n, ctx.yAddress, ctx.xAddress, emptyRuntimePools()),
  ];

  return { trades, uxHigh, uyLow, xyHigh };
}

async function setStatus4Rates(ctx) {
  await ctx.router.setRate(ctx.usdcAddress, ctx.xAddress, 1001n, 1000n);
  await ctx.router.setRate(ctx.xAddress, ctx.usdcAddress, 990n, 1000n);
  await ctx.router.setRate(ctx.usdcAddress, ctx.yAddress, 990n, 1000n);
  await ctx.router.setRate(ctx.yAddress, ctx.usdcAddress, 1001n, 1000n);
  await ctx.router.setRate(ctx.xAddress, ctx.yAddress, 1001n, 1000n);
  await ctx.router.setRate(ctx.yAddress, ctx.xAddress, 990n, 1000n);
}

async function executionParams() {
  const deadline = BigInt((await ethers.provider.getBlock("latest")).timestamp + 600);
  return {
    usdcParams: {
      amount: 1_000_000n,
      deadline,
      amountOutMinUsdc: 1_000_000n,
      minProfitUsdc: 1n,
    },
    tokenBorrowParams: {
      amount: 1_000_000n,
      deadline,
      minFinalToken: 1_000_000n,
      minProfitToken: 1n,
    },
  };
}

describe("UnifiedFlashLoanMevExecutor ordered U-x-y-U strategy", function () {
  it("selects status 4 and returns the concrete U -> X -> Y -> U hop details", async function () {
    const ctx = await deployFixture();
    const { trades, uxHigh, uyLow, xyHigh } = await buildTriangularTrades(ctx);
    await setStatus4Rates(ctx);
    const { usdcParams, tokenBorrowParams } = await executionParams();

    const preview = await ctx.executor.previewOrderedRuntimeAutoExecution.staticCall(
      trades,
      usdcParams,
      tokenBorrowParams,
      false,
    );

    expect(preview.found).to.equal(true);
    expect(preview.strategyStatus).to.equal(STRATEGY.statusXyUsdcFallback);
    expect(preview.executionKind).to.equal(STRATEGY.executionKindUsdcTriangular);
    expect(preview.selectedTradeArrayIndex).to.equal(2n);
    expect(preview.executionPreview.borrowedAsset).to.equal(ctx.usdcAddress);
    expect(preview.executionPreview.profitAsset).to.equal(ctx.usdcAddress);
    expect(preview.executionPreview.routeDirection).to.equal(STRATEGY.routeUxyu);
    expect(preview.executionPreview.quotedFinal).to.equal(1_003_003n);
    expect(preview.executionPreview.premium).to.equal(500n);
    expect(preview.executionPreview.requiredFinal).to.equal(1_000_501n);
    expect(preview.executionPreview.protectedMinFinal).to.equal(1_000_501n);
    expect(preview.executionPreview.expectedProfit).to.equal(2_502n);

    expect(preview.triangularRoute.routeDirection).to.equal(STRATEGY.routeUxyu);
    expect(preview.triangularRoute.hops[0].pool).to.equal(uxHigh.address);
    expect(preview.triangularRoute.hops[0].tokenIn).to.equal(ctx.usdcAddress);
    expect(preview.triangularRoute.hops[0].tokenOut).to.equal(ctx.xAddress);
    expect(preview.triangularRoute.hops[0].fee).to.equal(uxHigh.fee);
    expect(preview.triangularRoute.hops[0].amountIn).to.equal(1_000_000n);
    expect(preview.triangularRoute.hops[0].quotedAmountOut).to.equal(1_001_000n);

    expect(preview.triangularRoute.hops[1].pool).to.equal(xyHigh.address);
    expect(preview.triangularRoute.hops[1].tokenIn).to.equal(ctx.xAddress);
    expect(preview.triangularRoute.hops[1].tokenOut).to.equal(ctx.yAddress);
    expect(preview.triangularRoute.hops[1].quotedAmountOut).to.equal(1_002_001n);

    expect(preview.triangularRoute.hops[2].pool).to.equal(uyLow.address);
    expect(preview.triangularRoute.hops[2].tokenIn).to.equal(ctx.yAddress);
    expect(preview.triangularRoute.hops[2].tokenOut).to.equal(ctx.usdcAddress);
    expect(preview.triangularRoute.hops[2].quotedAmountOut).to.equal(1_003_003n);
    expect(preview.triangularRoute.hops[2].amountOutMin).to.equal(1_000_501n);

    expect(preview.progress.finalResultCode).to.equal(1104n);
    expect(preview.progress.selectedStatus).to.equal(4);
    expect(preview.progress.attemptedStatusMask).to.equal(0b01111);
    expect(preview.progress.selectedStatusMask).to.equal(0b01000);
    expect(preview.progress.remainingStatusMask).to.equal(0b10000);
    expect(preview.progress.remainingStepCount).to.equal(1);
    expect(preview.progress.steps[0].phase).to.equal(STRATEGY.stepCheckedFailed);
    expect(preview.progress.steps[1].phase).to.equal(STRATEGY.stepCheckedFailed);
    expect(preview.progress.steps[2].resultCode).to.equal(3503n);
    expect(preview.progress.steps[3].phase).to.equal(STRATEGY.stepSelected);
  });

  it("executes the selected three-hop USDC route and sweeps the realized profit", async function () {
    const ctx = await deployFixture();
    const { trades, uxHigh } = await buildTriangularTrades(ctx);
    await setStatus4Rates(ctx);
    const { usdcParams, tokenBorrowParams } = await executionParams();

    const runPreview = await ctx.executor.runOrderedRuntimeTradesAndExecuteAuto.staticCall(
      trades,
      usdcParams,
      tokenBorrowParams,
      false,
    );
    expect(runPreview.resultCode).to.equal(1204n);
    expect(runPreview.strategyStatus).to.equal(4n);
    expect(runPreview.routeDirection).to.equal(STRATEGY.routeUxyu);
    expect(runPreview.profitAmount).to.equal(2_503n);
    expect(runPreview.profitSwept).to.equal(2_503n);

    await expect(ctx.executor.runOrderedRuntimeTradesAndExecuteAuto(trades, usdcParams, tokenBorrowParams, false))
      .to.emit(ctx.executor, "OrderedRuntimePreviewSelected")
      .withArgs(4n, STRATEGY.executionKindUsdcTriangular, 2n, 103n)
      .and.to.emit(ctx.executor, "RuntimeTriangularHopQuoted")
      .withArgs(
        STRATEGY.routeUxyu,
        1n,
        uxHigh.address,
        ctx.usdcAddress,
        ctx.xAddress,
        uxHigh.fee,
        1_000_000n,
        1_001_000n,
        0n,
      )
      .and.to.emit(ctx.executor, "FlashLoanRouteExecuted")
      .withArgs(4n, ctx.usdcAddress, 1_000_000n, 2_503n)
      .and.to.emit(ctx.executor, "ProfitSwept")
      .withArgs(ctx.owner.address, ctx.usdcAddress, 2_503n, 0n)
      .and.to.emit(ctx.executor, "RuntimeWorkflowFinished")
      .withArgs(1204n, 4n, 0b01111, 0b10000, 1n);

    expect(await ctx.usdc.balanceOf(ctx.owner.address)).to.equal(2_503n);
    expect(await ctx.usdc.balanceOf(await ctx.executor.getAddress())).to.equal(0n);
  });

  it("does not sweep a pre-existing USDC balance while collecting realized profit", async function () {
    const ctx = await deployFixture();
    const { trades } = await buildTriangularTrades(ctx);
    await setStatus4Rates(ctx);
    const { usdcParams, tokenBorrowParams } = await executionParams();
    const executorAddress = await ctx.executor.getAddress();

    await ctx.usdc.mint(executorAddress, 777_777n);
    await expect(ctx.executor.runOrderedRuntimeTradesAndExecuteAuto(trades, usdcParams, tokenBorrowParams, false))
      .to.emit(ctx.executor, "ProfitSwept")
      .withArgs(ctx.owner.address, ctx.usdcAddress, 2_503n, 0n);

    expect(await ctx.usdc.balanceOf(ctx.owner.address)).to.equal(2_503n);
    expect(await ctx.usdc.balanceOf(executorAddress)).to.equal(777_777n);
  });

  it("returns structured failure progress when the three-hop route is not profitable", async function () {
    const ctx = await deployFixture();
    const { trades } = await buildTriangularTrades(ctx);
    await setStatus4Rates(ctx);
    await ctx.router.setRate(ctx.yAddress, ctx.usdcAddress, 998n, 1000n);
    const { usdcParams, tokenBorrowParams } = await executionParams();

    const preview = await ctx.executor.previewOrderedRuntimeAutoExecution.staticCall(
      trades,
      usdcParams,
      tokenBorrowParams,
      false,
    );

    expect(preview.found).to.equal(false);
    expect(preview.progress.finalResultCode).to.equal(STRATEGY.errNoProfitableRoute);
    expect(preview.progress.steps[3].phase).to.equal(STRATEGY.stepCheckedFailed);
    expect(preview.progress.steps[3].resultCode).to.equal(3404n);
    expect(preview.progress.steps[3].expectedProfit).to.equal(-505n);
    expect(preview.progress.steps[3].quotedFinal).to.equal(999_996n);
    expect(preview.progress.steps[3].requiredFinal).to.equal(1_000_501n);

    await expect(ctx.executor.runOrderedRuntimeTradesAndExecuteAuto(trades, usdcParams, tokenBorrowParams, false))
      .to.be.revertedWithCustomError(ctx.executor, "OrderedRuntimeExecutionFailed")
      .withArgs(55555n, 5n, 3n, STRATEGY.errNotEnoughPools, 0n, 0n, 0n, 0b11111, 0n);
  });

  it("can choose direct token cross-pool status 3 when token borrowing is explicitly enabled", async function () {
    const ctx = await deployFixture();
    const { trades } = await buildTriangularTrades(ctx);
    await setStatus4Rates(ctx);
    await ctx.router.setRate(ctx.xAddress, ctx.yAddress, 1001n, 1000n);
    await ctx.router.setRate(ctx.yAddress, ctx.xAddress, 1002n, 1000n);
    await ctx.executor.setBorrowConfig(ctx.xAddress, true, 2_000_000n);
    const { usdcParams, tokenBorrowParams } = await executionParams();

    const preview = await ctx.executor.previewOrderedRuntimeAutoExecution.staticCall(
      trades,
      usdcParams,
      tokenBorrowParams,
      true,
    );

    expect(preview.found).to.equal(true);
    expect(preview.strategyStatus).to.equal(STRATEGY.statusXyCrossPool);
    expect(preview.executionKind).to.equal(STRATEGY.executionKindTokenCrossPool);
    expect(preview.selectedTradeArrayIndex).to.equal(2n);
    expect(preview.executionPreview.borrowedAsset).to.equal(ctx.xAddress);
    expect(preview.executionPreview.profitAsset).to.equal(ctx.xAddress);
    expect(preview.executionPreview.quotedFinal).to.equal(1_003_002n);
    expect(preview.progress.remainingStatusMask).to.equal(0b11000);
  });

  it("rejects a malformed triangular slot layout instead of quoting a mixed route", async function () {
    const ctx = await deployFixture();
    const { trades } = await buildTriangularTrades(ctx);
    await setStatus4Rates(ctx);

    // Keep the second trade viable but make it another U-X pair. The
    // contract must reject the resulting U-X-X route before quoting hops.
    trades[1] = runtimeTrade(
      102n,
      ctx.usdcAddress,
      ctx.xAddress,
      runtimePools([
        [0, (await deployV3Pool(ctx, ctx.usdcAddress, ctx.xAddress, -80, 500n)).address],
        [1, (await deployV3Pool(ctx, ctx.usdcAddress, ctx.xAddress, 160, 3000n)).address],
      ]),
    );

    const { usdcParams, tokenBorrowParams } = await executionParams();
    const preview = await ctx.executor.previewOrderedRuntimeAutoExecution.staticCall(
      trades,
      usdcParams,
      tokenBorrowParams,
      false,
    );

    expect(preview.found).to.equal(false);
    expect(preview.progress.steps[3].detailCode).to.equal(STRATEGY.errRouteLayoutInvalid);
    expect(preview.progress.steps[4].detailCode).to.equal(STRATEGY.errNotEnoughPools);
  });

  it("does not allow fallback triangular execution after USDC borrowing is disabled", async function () {
    const ctx = await deployFixture();
    const { trades } = await buildTriangularTrades(ctx);
    await setStatus4Rates(ctx);
    const yxLow = await deployV3Pool(ctx, ctx.yAddress, ctx.xAddress, -90, 500n);
    const yxHigh = await deployV3Pool(ctx, ctx.yAddress, ctx.xAddress, 210, 3000n);
    trades[3] = runtimeTrade(
      104n,
      ctx.yAddress,
      ctx.xAddress,
      runtimePools([[0, yxLow.address], [1, yxHigh.address]]),
    );
    await ctx.executor.setBorrowConfig(ctx.usdcAddress, false, 0n);

    const { usdcParams, tokenBorrowParams } = await executionParams();
    const preview = await ctx.executor.previewOrderedRuntimeAutoExecution.staticCall(
      trades,
      usdcParams,
      tokenBorrowParams,
      false,
    );

    expect(preview.found).to.equal(false);
    expect(preview.progress.steps[3].detailCode).to.equal(STRATEGY.errBorrowAssetDisabled);
    expect(preview.progress.steps[4].detailCode).to.equal(STRATEGY.errBorrowAssetDisabled);
  });

  it("allows a triangular route when each hop has one valid pool but keeps cross-pool checks strict", async function () {
    const ctx = await deployFixture();
    const ux = await deployV3Pool(ctx, ctx.usdcAddress, ctx.xAddress, 120, 3000n);
    const uy = await deployV3Pool(ctx, ctx.usdcAddress, ctx.yAddress, 180, 3000n);
    const xy = await deployV3Pool(ctx, ctx.xAddress, ctx.yAddress, 250, 3000n);
    const trades = [
      runtimeTrade(201n, ctx.usdcAddress, ctx.xAddress, runtimePools([[0, ux.address]])),
      runtimeTrade(202n, ctx.usdcAddress, ctx.yAddress, runtimePools([[0, uy.address]])),
      runtimeTrade(203n, ctx.xAddress, ctx.yAddress, runtimePools([[0, xy.address]])),
    ];
    await setStatus4Rates(ctx);
    const { usdcParams, tokenBorrowParams } = await executionParams();

    const preview = await ctx.executor.previewOrderedRuntimeAutoExecution.staticCall(
      trades,
      usdcParams,
      tokenBorrowParams,
      false,
    );

    expect(preview.found).to.equal(true);
    expect(preview.strategyStatus).to.equal(STRATEGY.statusXyUsdcFallback);
    expect(preview.executionPreview.routeDirection).to.equal(STRATEGY.routeUxyu);
    expect(preview.triangularRoute.hops[0].pool).to.equal(ux.address);
    expect(preview.triangularRoute.hops[1].pool).to.equal(xy.address);
    expect(preview.triangularRoute.hops[2].pool).to.equal(uy.address);
    expect(preview.progress.steps[0].detailCode).to.equal(STRATEGY.errNoPriceSpread);
    expect(preview.progress.steps[3].phase).to.equal(STRATEGY.stepSelected);
  });

  it("rejects a pool pair below minTickDelta before it reaches the quoter", async function () {
    const ctx = await deployFixture();
    const low = await deployV3Pool(ctx, ctx.usdcAddress, ctx.xAddress, 100, 500n);
    const high = await deployV3Pool(ctx, ctx.usdcAddress, ctx.xAddress, 110, 3000n);
    const trades = [
      runtimeTrade(
        301n,
        ctx.usdcAddress,
        ctx.xAddress,
        runtimePools([
          [0, low.address],
          [1, high.address],
        ]),
      ),
    ];
    await ctx.executor.setRuntimeRiskConfig(1n, 11n);
    const { usdcParams, tokenBorrowParams } = await executionParams();

    const preview = await ctx.executor.previewOrderedRuntimeAutoExecution.staticCall(
      trades,
      usdcParams,
      tokenBorrowParams,
      false,
    );

    expect(preview.found).to.equal(false);
    expect(preview.progress.steps[0].phase).to.equal(STRATEGY.stepCheckedFailed);
    expect(preview.progress.steps[0].resultCode).to.equal(3201n);
    expect(preview.progress.steps[0].detailCode).to.equal(STRATEGY.errNoPriceSpread);
  });

  it("rejects a fifth trade input because the current state machine consumes four route inputs", async function () {
    const ctx = await deployFixture();
    const { trades } = await buildTriangularTrades(ctx);
    const extra = runtimeTrade(105n, ctx.usdcAddress, ctx.xAddress, emptyRuntimePools());
    const { usdcParams, tokenBorrowParams } = await executionParams();

    await expect(
      ctx.executor.previewOrderedRuntimeAutoExecution.staticCall(
        [...trades, extra],
        usdcParams,
        tokenBorrowParams,
        false,
      ),
    ).to.be.revertedWithCustomError(ctx.executor, "InvalidRequest");
  });
});
