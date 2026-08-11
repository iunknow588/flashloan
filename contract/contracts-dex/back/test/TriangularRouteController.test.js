const { expect } = require("chai");
const { ethers } = require("hardhat");

async function deployFixture() {
  const [owner, other, factory, router, quoter] = await ethers.getSigners();

  const Token = await ethers.getContractFactory("TestERC20");
  const usdc = await Token.deploy("USD Coin", "USDC", 6, owner.address);
  const x = await Token.deploy("Token X", "X", 18, owner.address);
  const y = await Token.deploy("Token Y", "Y", 18, owner.address);
  const z = await Token.deploy("Token Z", "Z", 18, owner.address);

  const Controller = await ethers.getContractFactory("TriangularRouteController");
  const controller = await Controller.deploy(await usdc.getAddress(), owner.address, owner.address);

  await controller.setAdapterConfig(
    await controller.ADAPTER_UNISWAP_V3(),
    true,
    factory.address,
    router.address,
    quoter.address,
  );

  return {
    owner,
    other,
    factory,
    router,
    quoter,
    usdc,
    x,
    y,
    z,
    controller,
    usdcAddress: await usdc.getAddress(),
    xAddress: await x.getAddress(),
    yAddress: await y.getAddress(),
    zAddress: await z.getAddress(),
  };
}

async function deployExecutionFixture() {
  const [owner, other, factory] = await ethers.getSigners();

  const Token = await ethers.getContractFactory("TestERC20");
  const usdc = await Token.deploy("USD Coin", "USDC", 6, owner.address);
  const x = await Token.deploy("Token X", "X", 18, owner.address);
  const y = await Token.deploy("Token Y", "Y", 18, owner.address);

  const AavePool = await ethers.getContractFactory("MockAavePool");
  const pool = await AavePool.deploy(5n);

  const Router = await ethers.getContractFactory("MockV3SwapRouter");
  const router = await Router.deploy(owner.address);

  const Executor = await ethers.getContractFactory("AaveTriangularExecutor");
  const executor = await Executor.deploy(await pool.getAddress(), await usdc.getAddress(), owner.address);

  const Controller = await ethers.getContractFactory("TriangularRouteController");
  const controller = await Controller.deploy(await usdc.getAddress(), await executor.getAddress(), owner.address);

  await executor.setController(await controller.getAddress());
  await controller.setAdapterConfig(
    await controller.ADAPTER_UNISWAP_V3(),
    true,
    factory.address,
    await router.getAddress(),
    await router.getAddress(),
  );

  const usdcAddress = await usdc.getAddress();
  const xAddress = await x.getAddress();
  const yAddress = await y.getAddress();
  await usdc.mint(await pool.getAddress(), 10_000_000n);
  await usdc.mint(await router.getAddress(), 10_000_000n);
  await router.setRate(usdcAddress, xAddress, 1001n, 1000n);
  await router.setRate(xAddress, yAddress, 1001n, 1000n);
  await router.setRate(yAddress, usdcAddress, 1001n, 1000n);
  await executor.setProfitSweepThresholdUsdc(1n);

  return {
    owner,
    other,
    factory,
    quoter: router,
    usdc,
    x,
    y,
    pool,
    router,
    executor,
    controller,
    usdcAddress,
    xAddress,
    yAddress,
  };
}

async function deployCrossPoolExecutionFixture() {
  const [owner, other, factory] = await ethers.getSigners();

  const Token = await ethers.getContractFactory("TestERC20");
  const usdc = await Token.deploy("USD Coin", "USDC", 6, owner.address);
  const x = await Token.deploy("Token X", "X", 18, owner.address);
  const y = await Token.deploy("Token Y", "Y", 18, owner.address);

  const AavePool = await ethers.getContractFactory("MockAavePool");
  const pool = await AavePool.deploy(5n);

  const Router = await ethers.getContractFactory("MockV3SwapRouter");
  const router = await Router.deploy(owner.address);

  const Executor = await ethers.getContractFactory("AaveTriangularExecutor");
  const executor = await Executor.deploy(await pool.getAddress(), await usdc.getAddress(), owner.address);

  const CrossPoolExecutor = await ethers.getContractFactory("AaveCrossPoolExecutor");
  const crossPoolExecutor = await CrossPoolExecutor.deploy(await pool.getAddress(), owner.address);

  const Controller = await ethers.getContractFactory("TriangularRouteController");
  const controller = await Controller.deploy(await usdc.getAddress(), await executor.getAddress(), owner.address);

  await executor.setController(await controller.getAddress());
  await crossPoolExecutor.setController(await controller.getAddress());
  await controller.setCrossPoolExecutor(await crossPoolExecutor.getAddress());
  await controller.setAdapterConfig(
    await controller.ADAPTER_UNISWAP_V3(),
    true,
    factory.address,
    await router.getAddress(),
    await router.getAddress(),
  );

  const usdcAddress = await usdc.getAddress();
  const xAddress = await x.getAddress();
  const yAddress = await y.getAddress();

  await usdc.mint(await pool.getAddress(), 10_000_000n);
  await x.mint(await pool.getAddress(), 10_000_000n);
  await y.mint(await pool.getAddress(), 10_000_000n);
  await usdc.mint(await router.getAddress(), 10_000_000n);
  await x.mint(await router.getAddress(), 10_000_000n);

  return {
    owner,
    other,
    factory,
    router,
    quoter: router,
    usdc,
    x,
    y,
    pool,
    executor,
    crossPoolExecutor,
    controller,
    usdcAddress,
    xAddress,
    yAddress,
  };
}

function emptyRuntimePools() {
  return Array.from({ length: 10 }, () => ({ adapterKind: 0n, pool: ethers.ZeroAddress }));
}

function runtimePools(entries) {
  const pools = emptyRuntimePools();
  for (const [index, pool, adapterKind = 1n] of entries) {
    pools[index] = { adapterKind, pool };
  }
  return pools;
}

function runtimeTrade(ctx, tradeIndex, pools, tokenX = ctx.xAddress, tokenY = ctx.yAddress) {
  return { tradeIndex, tokenX, tokenY, pools };
}

async function deployV3Pool(ctx, token0, token1, tick, liquidity = 1_000_000n, fee = 3000n, factory = ctx.factory.address) {
  const V3Pool = await ethers.getContractFactory("MockV3Pool");
  const sqrtPriceX96 = 1n << 96n;
  const pool = await V3Pool.deploy(factory, token0, token1, fee, liquidity, sqrtPriceX96, tick);
  return { pool, address: await pool.getAddress() };
}

async function setPoolRate(ctx, pool, tokenIn, tokenOut, numerator, denominator) {
  await pool.connect(ctx.factory).setRate(tokenIn, tokenOut, numerator, denominator);
}

describe("TriangularRouteController runtime pool candidates", function () {
  it("does not expose the old static pair-table ABI", async function () {
    const ctx = await deployFixture();

    expect(ctx.controller.interface.getFunction("pairCount")).to.equal(null);
    expect(ctx.controller.interface.getFunction("addPair")).to.equal(null);
    expect(ctx.controller.interface.getFunction("runPair")).to.equal(null);
    expect(ctx.controller.interface.getFunction("previewBestPair")).to.equal(null);
  });

  it("selects the lowest and highest V3 pool ticks in one bounded candidate set", async function () {
    const ctx = await deployFixture();
    const low = await deployV3Pool(ctx, ctx.xAddress, ctx.yAddress, -250);
    const middle = await deployV3Pool(ctx, ctx.xAddress, ctx.yAddress, 25);
    const highReversed = await deployV3Pool(ctx, ctx.yAddress, ctx.xAddress, -500);
    const unrelated = await deployV3Pool(ctx, ctx.xAddress, ctx.zAddress, 900);

    const trade = runtimeTrade(ctx, 7n, runtimePools([
      [0, middle.address],
      [1, unrelated.address],
      [3, highReversed.address],
      [9, low.address],
    ]));

    const decision = await ctx.controller.previewRuntimeTrade.staticCall(trade);

    expect(decision.viable).to.equal(true);
    expect(decision.tradeIndex).to.equal(7n);
    expect(decision.lowPool).to.equal(low.address);
    expect(decision.highPool).to.equal(highReversed.address);
    expect(decision.lowNormalizedTick).to.equal(-250n);
    expect(decision.highNormalizedTick).to.equal(500n);
    expect(decision.tickDelta).to.equal(750n);
    expect(decision.scannedPoolCount).to.equal(4n);
    expect(decision.validPoolCount).to.equal(3n);
  });

  it("selects only one best runtime trade from multiple calldata trades", async function () {
    const ctx = await deployFixture();
    const firstLow = await deployV3Pool(ctx, ctx.xAddress, ctx.yAddress, 10);
    const firstHigh = await deployV3Pool(ctx, ctx.xAddress, ctx.yAddress, 70);
    const secondLow = await deployV3Pool(ctx, ctx.xAddress, ctx.yAddress, -100);
    const secondHigh = await deployV3Pool(ctx, ctx.xAddress, ctx.yAddress, 350);

    const trades = [
      runtimeTrade(ctx, 100n, runtimePools([[0, firstLow.address], [1, firstHigh.address]])),
      runtimeTrade(ctx, 101n, runtimePools([[0, secondHigh.address], [1, secondLow.address]])),
    ];

    const [bestTradeArrayIndex, decision] = await ctx.controller.previewBestRuntimeTrades.staticCall(trades);

    expect(bestTradeArrayIndex).to.equal(1n);
    expect(decision.tradeIndex).to.equal(101n);
    expect(decision.lowPool).to.equal(secondLow.address);
    expect(decision.highPool).to.equal(secondHigh.address);
    expect(decision.tickDelta).to.equal(450n);

    await expect(ctx.controller.runBestRuntimeTrades(trades))
      .to.emit(ctx.controller, "RuntimeTradeSelected")
      .withArgs(1n, 101n, ctx.xAddress, ctx.yAddress, secondLow.address, secondHigh.address, 450n);
  });

  it("filters by V3 factory whitelist and minimum liquidity", async function () {
    const ctx = await deployFixture();
    const allowedLow = await deployV3Pool(ctx, ctx.xAddress, ctx.yAddress, -10, 1_000n);
    const allowedHigh = await deployV3Pool(ctx, ctx.xAddress, ctx.yAddress, 100, 1_000n);
    const wrongFactory = await deployV3Pool(ctx, ctx.xAddress, ctx.yAddress, 900, 1_000n, 3000n, ctx.other.address);
    const tooShallow = await deployV3Pool(ctx, ctx.xAddress, ctx.yAddress, -900, 1n);

    await ctx.controller.setRuntimeRiskConfig(100n, 1n);

    const trade = runtimeTrade(ctx, 1n, runtimePools([
      [0, wrongFactory.address],
      [1, tooShallow.address],
      [2, allowedHigh.address],
      [3, allowedLow.address],
    ]));
    const decision = await ctx.controller.previewRuntimeTrade.staticCall(trade);

    expect(decision.viable).to.equal(true);
    expect(decision.lowPool).to.equal(allowedLow.address);
    expect(decision.highPool).to.equal(allowedHigh.address);
    expect(decision.validPoolCount).to.equal(2n);
  });

  it("rejects unsupported adapter kinds and invalid runtime trades", async function () {
    const ctx = await deployFixture();
    const pool = await deployV3Pool(ctx, ctx.xAddress, ctx.yAddress, 1);

    await expect(ctx.controller.previewRuntimeTrade(runtimeTrade(ctx, 1n, runtimePools([[0, pool.address, 2n]]))))
      .to.be.revertedWithCustomError(ctx.controller, "UnsupportedAdapterKind")
      .withArgs(2n);

    const directUsdcPair = await ctx.controller.previewRuntimeTrade.staticCall(
      runtimeTrade(ctx, 1n, runtimePools([[0, pool.address]]), ctx.usdcAddress, ctx.yAddress),
    );
    expect(directUsdcPair.viable).to.equal(false);
    expect(directUsdcPair.failureCode).to.equal(await ctx.controller.FAIL_RUNTIME_NOT_ENOUGH_POOLS());

    const tooMany = Array.from({ length: 17 }, () => runtimeTrade(ctx, 1n, runtimePools([[0, pool.address]])));
    await expect(ctx.controller.previewBestRuntimeTrades(tooMany))
      .to.be.revertedWithCustomError(ctx.controller, "InvalidRequest");
  });

  it("requires an actual high-low spread before selecting a runtime opportunity", async function () {
    const ctx = await deployFixture();
    const flatA = await deployV3Pool(ctx, ctx.xAddress, ctx.yAddress, 42);
    const flatB = await deployV3Pool(ctx, ctx.xAddress, ctx.yAddress, 42);

    const trade = runtimeTrade(ctx, 1n, runtimePools([[0, flatA.address], [1, flatB.address]]));
    const decision = await ctx.controller.previewRuntimeTrade.staticCall(trade);

    expect(decision.viable).to.equal(false);
    expect(decision.failureCode).to.equal(await ctx.controller.FAIL_RUNTIME_NO_PRICE_SPREAD());
    await expect(ctx.controller.runBestRuntimeTrades([trade]))
      .to.be.revertedWithCustomError(ctx.controller, "NoRuntimeOpportunity")
      .withArgs(await ctx.controller.FAIL_RUNTIME_NO_PRICE_SPREAD());
  });

  it("executes the selected runtime trade through the configured executor", async function () {
    const ctx = await deployExecutionFixture();
    const low = await deployV3Pool(ctx, ctx.xAddress, ctx.yAddress, -150, 1_000_000n, 500n);
    const high = await deployV3Pool(ctx, ctx.xAddress, ctx.yAddress, 250, 1_000_000n, 3000n);
    const amount = 1_000_000n;
    const deadline = BigInt((await ethers.provider.getBlock("latest")).timestamp + 600);

    const trade = runtimeTrade(ctx, 42n, runtimePools([[0, high.address], [1, low.address]]));
    const params = {
      amount,
      deadline,
      amountOutMinUsdc: amount,
      minProfitUsdc: 1n,
      usdcToTokenXFee: 3000n,
      tokenYToUsdcFee: 3000n,
    };
    const [, , executionPreview] = await ctx.controller.previewBestRuntimeExecution.staticCall([trade], params);

    expect(executionPreview.quotedFinalUsdc).to.equal(1_003_003n);
    expect(executionPreview.premiumUsdc).to.equal(500n);
    expect(executionPreview.requiredFinalUsdc).to.equal(1_000_501n);
    expect(executionPreview.protectedAmountOutMinUsdc).to.equal(1_000_501n);

    await expect(ctx.controller.runBestRuntimeTradesAndExecute([trade], params))
      .to.emit(ctx.controller, "RuntimeTradeSelected")
      .withArgs(0n, 42n, ctx.xAddress, ctx.yAddress, low.address, high.address, 400n)
      .and.to.emit(ctx.controller, "RuntimeProfitChecked")
      .withArgs(await ctx.router.getAddress(), amount, 1_003_003n, 500n, 1n, 1_000_501n, 1_000_501n)
      .and.to.emit(ctx.executor, "FlashLoanRequested")
      .withArgs(await ctx.controller.getAddress(), amount)
      .and.to.emit(ctx.controller, "RuntimeTradeExecuted")
      .withArgs(0n, 42n, await ctx.router.getAddress(), amount, 1_000_501n, 2503n);

    expect(await ctx.usdc.balanceOf(ctx.owner.address)).to.equal(2503n);
  });

  it("uses a conservative rounded-up Aave premium in runtime execution previews", async function () {
    const ctx = await deployExecutionFixture();
    const low = await deployV3Pool(ctx, ctx.xAddress, ctx.yAddress, -150, 1_000_000n, 500n);
    const high = await deployV3Pool(ctx, ctx.xAddress, ctx.yAddress, 250, 1_000_000n, 3000n);
    const amount = 1_000n;
    const deadline = BigInt((await ethers.provider.getBlock("latest")).timestamp + 600);

    const trade = runtimeTrade(ctx, 42n, runtimePools([[0, high.address], [1, low.address]]));
    const [, , executionPreview] = await ctx.controller.previewBestRuntimeExecution.staticCall([trade], {
      amount,
      deadline,
      amountOutMinUsdc: amount,
      minProfitUsdc: 1n,
      usdcToTokenXFee: 3000n,
      tokenYToUsdcFee: 3000n,
    });

    expect(executionPreview.quotedFinalUsdc).to.equal(1_003n);
    expect(executionPreview.premiumUsdc).to.equal(1n);
    expect(executionPreview.requiredFinalUsdc).to.equal(1_002n);
    expect(executionPreview.protectedAmountOutMinUsdc).to.equal(1_002n);
  });

  it("sweeps all post-repayment USDC above the configured reserve", async function () {
    const ctx = await deployExecutionFixture();
    const low = await deployV3Pool(ctx, ctx.xAddress, ctx.yAddress, -150, 1_000_000n, 500n);
    const high = await deployV3Pool(ctx, ctx.xAddress, ctx.yAddress, 250, 1_000_000n, 3000n);
    const executorAddress = await ctx.executor.getAddress();
    const reserve = 100n;
    await ctx.executor.setProfitReserveUsdc(reserve);
    await ctx.usdc.mint(executorAddress, reserve);

    const amount = 1_000_000n;
    const deadline = BigInt((await ethers.provider.getBlock("latest")).timestamp + 600);
    const trade = runtimeTrade(ctx, 42n, runtimePools([[0, high.address], [1, low.address]]));
    const params = {
      amount,
      deadline,
      amountOutMinUsdc: amount,
      minProfitUsdc: 1n,
      usdcToTokenXFee: 3000n,
      tokenYToUsdcFee: 3000n,
    };

    await expect(ctx.controller.runBestRuntimeTradesAndExecute([trade], params))
      .to.emit(ctx.executor, "ProfitSwept")
      .withArgs(ctx.owner.address, 2503n, reserve);

    expect(await ctx.usdc.balanceOf(ctx.owner.address)).to.equal(2503n);
    expect(await ctx.usdc.balanceOf(executorAddress)).to.equal(reserve);
  });

  it("keeps profit in contract B when auto sweep is disabled", async function () {
    const ctx = await deployExecutionFixture();
    await ctx.executor.setProfitSweepEnabled(false);
    const low = await deployV3Pool(ctx, ctx.xAddress, ctx.yAddress, -150, 1_000_000n, 500n);
    const high = await deployV3Pool(ctx, ctx.xAddress, ctx.yAddress, 250, 1_000_000n, 3000n);
    const amount = 1_000_000n;
    const deadline = BigInt((await ethers.provider.getBlock("latest")).timestamp + 600);
    const trade = runtimeTrade(ctx, 42n, runtimePools([[0, high.address], [1, low.address]]));
    const params = {
      amount,
      deadline,
      amountOutMinUsdc: amount,
      minProfitUsdc: 1n,
      usdcToTokenXFee: 3000n,
      tokenYToUsdcFee: 3000n,
    };

    await expect(ctx.controller.runBestRuntimeTradesAndExecute([trade], params))
      .to.emit(ctx.controller, "RuntimeTradeExecuted")
      .withArgs(0n, 42n, await ctx.router.getAddress(), amount, 1_000_501n, 0n);

    expect(await ctx.usdc.balanceOf(ctx.owner.address)).to.equal(0n);
    expect(await ctx.usdc.balanceOf(await ctx.executor.getAddress())).to.equal(2503n);
  });

  it("rejects execution before calling the executor when the router quote is not profitable", async function () {
    const ctx = await deployExecutionFixture();
    const low = await deployV3Pool(ctx, ctx.xAddress, ctx.yAddress, -150, 1_000_000n, 500n);
    const high = await deployV3Pool(ctx, ctx.xAddress, ctx.yAddress, 250, 1_000_000n, 3000n);
    const amount = 1_000_000n;
    const deadline = BigInt((await ethers.provider.getBlock("latest")).timestamp + 600);
    await ctx.router.setRate(ctx.yAddress, ctx.usdcAddress, 998n, 1000n);

    const trade = runtimeTrade(ctx, 42n, runtimePools([[0, high.address], [1, low.address]]));
    const params = {
      amount,
      deadline,
      amountOutMinUsdc: amount,
      minProfitUsdc: 1n,
      usdcToTokenXFee: 3000n,
      tokenYToUsdcFee: 3000n,
    };

    await expect(ctx.controller.runBestRuntimeTradesAndExecute([trade], params))
      .to.be.revertedWithCustomError(ctx.controller, "RuntimeProfitCheckFailed")
      .withArgs(999_996n, 1_000_501n, 500n, 1n);
  });

  it("requires an execution router before calling the executor", async function () {
    const ctx = await deployFixture();
    await ctx.controller.setAdapterConfig(
      await ctx.controller.ADAPTER_UNISWAP_V3(),
      true,
      ctx.factory.address,
      ethers.ZeroAddress,
      await ctx.quoter.getAddress(),
    );
    const low = await deployV3Pool(ctx, ctx.xAddress, ctx.yAddress, -150, 1_000_000n, 500n);
    const high = await deployV3Pool(ctx, ctx.xAddress, ctx.yAddress, 250, 1_000_000n, 3000n);
    const deadline = BigInt((await ethers.provider.getBlock("latest")).timestamp + 600);
    const trade = runtimeTrade(ctx, 42n, runtimePools([[0, high.address], [1, low.address]]));

    await expect(ctx.controller.runBestRuntimeTradesAndExecute([trade], {
      amount: 1_000_000n,
      deadline,
      amountOutMinUsdc: 1_000_000n,
      minProfitUsdc: 1n,
      usdcToTokenXFee: 3000n,
      tokenYToUsdcFee: 3000n,
    }))
      .to.be.revertedWithCustomError(ctx.controller, "ExecutionRouterMissing")
      .withArgs(await ctx.controller.ADAPTER_UNISWAP_V3());
  });

  it("skips an unprofitable earlier runtime trade and executes the first profitable trade in calldata order", async function () {
    const ctx = await deployExecutionFixture();
    const firstLow = await deployV3Pool(ctx, ctx.xAddress, ctx.yAddress, -150);
    const firstHigh = await deployV3Pool(ctx, ctx.xAddress, ctx.yAddress, 250);
    const secondLow = await deployV3Pool(ctx, ctx.yAddress, ctx.xAddress, -450);
    const secondHigh = await deployV3Pool(ctx, ctx.yAddress, ctx.xAddress, 450);
    const amount = 1_000_000n;
    const deadline = BigInt((await ethers.provider.getBlock("latest")).timestamp + 600);

    await ctx.router.setRate(ctx.usdcAddress, ctx.xAddress, 1000n, 1000n);
    await ctx.router.setRate(ctx.xAddress, ctx.yAddress, 1000n, 1000n);
    await ctx.router.setRate(ctx.yAddress, ctx.usdcAddress, 999n, 1000n);
    await ctx.router.setRate(ctx.usdcAddress, ctx.yAddress, 1002n, 1000n);
    await ctx.router.setRate(ctx.yAddress, ctx.xAddress, 1002n, 1000n);
    await ctx.router.setRate(ctx.xAddress, ctx.usdcAddress, 1002n, 1000n);

    const trades = [
      runtimeTrade(ctx, 101n, runtimePools([[0, firstLow.address], [1, firstHigh.address]])),
      runtimeTrade(ctx, 202n, runtimePools([[0, secondHigh.address], [1, secondLow.address]]), ctx.yAddress, ctx.xAddress),
    ];
    const params = {
      amount,
      deadline,
      amountOutMinUsdc: amount,
      minProfitUsdc: 1n,
      usdcToTokenXFee: 3000n,
      tokenYToUsdcFee: 3000n,
    };

    const [found, selectedTradeArrayIndex, decision, executionPreview] =
      await ctx.controller.previewFirstProfitableRuntimeExecution.staticCall(trades, params);
    expect(found).to.equal(true);
    expect(selectedTradeArrayIndex).to.equal(1n);
    expect(decision.tradeIndex).to.equal(202n);
    expect(decision.lowPool).to.equal(secondLow.address);
    expect(decision.highPool).to.equal(secondHigh.address);
    expect(executionPreview.quotedFinalUsdc).to.equal(1_006_012n);

    await expect(ctx.controller.runFirstProfitableRuntimeTradesAndExecute(trades, params))
      .to.emit(ctx.controller, "RuntimeTradeSelected")
      .withArgs(1n, 202n, ctx.yAddress, ctx.xAddress, secondLow.address, secondHigh.address, 900n)
      .and.to.emit(ctx.executor, "FlashLoanRequested")
      .withArgs(await ctx.controller.getAddress(), amount)
      .and.to.emit(ctx.controller, "RuntimeTradeExecuted")
      .withArgs(1n, 202n, await ctx.router.getAddress(), amount, 1_000_501n, 5512n);

    expect(await ctx.usdc.balanceOf(ctx.owner.address)).to.equal(5512n);
  });

  it("keeps calldata order when more than one runtime trade is profitable", async function () {
    const ctx = await deployExecutionFixture();
    const firstLow = await deployV3Pool(ctx, ctx.xAddress, ctx.yAddress, -150);
    const firstHigh = await deployV3Pool(ctx, ctx.xAddress, ctx.yAddress, 250);
    const secondLow = await deployV3Pool(ctx, ctx.yAddress, ctx.xAddress, -450);
    const secondHigh = await deployV3Pool(ctx, ctx.yAddress, ctx.xAddress, 450);
    const amount = 1_000_000n;
    const deadline = BigInt((await ethers.provider.getBlock("latest")).timestamp + 600);

    await ctx.router.setRate(ctx.usdcAddress, ctx.yAddress, 1002n, 1000n);
    await ctx.router.setRate(ctx.yAddress, ctx.xAddress, 1002n, 1000n);
    await ctx.router.setRate(ctx.xAddress, ctx.usdcAddress, 1002n, 1000n);

    const trades = [
      runtimeTrade(ctx, 101n, runtimePools([[0, firstLow.address], [1, firstHigh.address]])),
      runtimeTrade(ctx, 202n, runtimePools([[0, secondHigh.address], [1, secondLow.address]]), ctx.yAddress, ctx.xAddress),
    ];
    const params = {
      amount,
      deadline,
      amountOutMinUsdc: amount,
      minProfitUsdc: 1n,
      usdcToTokenXFee: 3000n,
      tokenYToUsdcFee: 3000n,
    };

    const [found, selectedTradeArrayIndex, decision] =
      await ctx.controller.previewFirstProfitableRuntimeExecution.staticCall(trades, params);
    expect(found).to.equal(true);
    expect(selectedTradeArrayIndex).to.equal(0n);
    expect(decision.tradeIndex).to.equal(101n);
    expect(decision.tickDelta).to.equal(400n);
  });

  it("does not call contract B when no runtime trade is profitable", async function () {
    const ctx = await deployExecutionFixture();
    const firstLow = await deployV3Pool(ctx, ctx.xAddress, ctx.yAddress, -150);
    const firstHigh = await deployV3Pool(ctx, ctx.xAddress, ctx.yAddress, 250);
    const secondLow = await deployV3Pool(ctx, ctx.yAddress, ctx.xAddress, -450);
    const secondHigh = await deployV3Pool(ctx, ctx.yAddress, ctx.xAddress, 450);
    const amount = 1_000_000n;
    const deadline = BigInt((await ethers.provider.getBlock("latest")).timestamp + 600);

    await ctx.router.setRate(ctx.usdcAddress, ctx.xAddress, 1000n, 1000n);
    await ctx.router.setRate(ctx.xAddress, ctx.yAddress, 1000n, 1000n);
    await ctx.router.setRate(ctx.yAddress, ctx.usdcAddress, 999n, 1000n);
    await ctx.router.setRate(ctx.usdcAddress, ctx.yAddress, 1000n, 1000n);
    await ctx.router.setRate(ctx.yAddress, ctx.xAddress, 1000n, 1000n);
    await ctx.router.setRate(ctx.xAddress, ctx.usdcAddress, 999n, 1000n);

    const trades = [
      runtimeTrade(ctx, 101n, runtimePools([[0, firstLow.address], [1, firstHigh.address]])),
      runtimeTrade(ctx, 202n, runtimePools([[0, secondHigh.address], [1, secondLow.address]]), ctx.yAddress, ctx.xAddress),
    ];
    const params = {
      amount,
      deadline,
      amountOutMinUsdc: amount,
      minProfitUsdc: 1n,
      usdcToTokenXFee: 3000n,
      tokenYToUsdcFee: 3000n,
    };

    const [found] = await ctx.controller.previewFirstProfitableRuntimeExecution.staticCall(trades, params);
    expect(found).to.equal(false);

    await expect(ctx.controller.runFirstProfitableRuntimeTradesAndExecute(trades, params))
      .to.be.revertedWithCustomError(ctx.controller, "NoProfitableRuntimeExecution")
      .withArgs(2n);
    expect(await ctx.usdc.balanceOf(ctx.owner.address)).to.equal(0n);
  });
});

describe("TriangularRouteController cross-pool runtime arbitrage", function () {
  it("previews and executes one profitable cross-pool trade through contract C", async function () {
    const ctx = await deployCrossPoolExecutionFixture();
    const low = await deployV3Pool(ctx, ctx.xAddress, ctx.yAddress, -150, 1_000_000n, 500n);
    const high = await deployV3Pool(ctx, ctx.xAddress, ctx.yAddress, 250, 1_000_000n, 3000n);

    await ctx.router.setRate(ctx.xAddress, ctx.yAddress, 1001n, 1000n);
    await ctx.router.setRate(ctx.yAddress, ctx.xAddress, 1002n, 1000n);

    const amount = 1_000_000n;
    const deadline = BigInt((await ethers.provider.getBlock("latest")).timestamp + 600);

    const trade = runtimeTrade(ctx, 7n, runtimePools([[0, low.address], [1, high.address]]));
    const params = {
      amount,
      deadline,
      minFinalTokenX: amount,
      minProfitTokenX: 1n,
    };

    const [, , executionPreview] = await ctx.controller.previewBestRuntimeCrossPoolExecution.staticCall([trade], params);
    expect(executionPreview.quotedFinalUsdc).to.equal(1_003_002n);
    expect(executionPreview.premiumUsdc).to.equal(500n);
    expect(executionPreview.requiredFinalUsdc).to.equal(1_000_501n);
    expect(executionPreview.protectedAmountOutMinUsdc).to.equal(1_000_501n);

    await expect(ctx.controller.runBestRuntimeTradesAndExecuteCrossPool([trade], params))
      .to.emit(ctx.controller, "RuntimeTradeSelected")
      .withArgs(0n, 7n, ctx.xAddress, ctx.yAddress, low.address, high.address, 400n)
      .and.to.emit(ctx.controller, "RuntimeCrossPoolProfitChecked")
      .withArgs(high.address, low.address, amount, 1_003_002n, 500n, 1n, 1_000_501n, 1_000_501n)
      .and.to.emit(ctx.crossPoolExecutor, "FlashLoanRequested")
      .withArgs(await ctx.controller.getAddress(), ctx.xAddress, amount)
      .and.to.emit(ctx.crossPoolExecutor, "CrossPoolRouteExecuted")
      .withArgs(await ctx.controller.getAddress(), high.address, low.address, amount, 500n, 1_003_002n, 2502n)
      .and.to.emit(ctx.controller, "RuntimeCrossPoolExecuted")
      .withArgs(0n, 7n, high.address, low.address, amount, 1_000_501n, 2502n);

    expect(await ctx.x.balanceOf(ctx.owner.address)).to.equal(2502n);
  });

  it("rejects cross-pool execution before calling contract C when the profit floor is not met", async function () {
    const ctx = await deployCrossPoolExecutionFixture();
    const low = await deployV3Pool(ctx, ctx.xAddress, ctx.yAddress, -150, 1_000_000n, 500n);
    const high = await deployV3Pool(ctx, ctx.xAddress, ctx.yAddress, 250, 1_000_000n, 3000n);

    await ctx.router.setRate(ctx.xAddress, ctx.yAddress, 1000n, 1000n);
    await ctx.router.setRate(ctx.yAddress, ctx.xAddress, 1000n, 1000n);

    const amount = 1_000_000n;
    const deadline = BigInt((await ethers.provider.getBlock("latest")).timestamp + 600);
    const trade = runtimeTrade(ctx, 7n, runtimePools([[0, low.address], [1, high.address]]));

    await expect(ctx.controller.runBestRuntimeTradesAndExecuteCrossPool([trade], {
      amount,
      deadline,
      minFinalTokenX: amount,
      minProfitTokenX: 1n,
    }))
      .to.be.revertedWithCustomError(ctx.controller, "RuntimeCrossPoolProfitCheckFailed")
      .withArgs(1_000_000n, 1_000_501n, 500n, 1n);
  });

  it("requires contract C to be configured before cross-pool execution", async function () {
    const ctx = await deployCrossPoolExecutionFixture();
    const low = await deployV3Pool(ctx, ctx.xAddress, ctx.yAddress, -150, 1_000_000n, 500n);
    const high = await deployV3Pool(ctx, ctx.xAddress, ctx.yAddress, 250, 1_000_000n, 3000n);

    await ctx.router.setRate(ctx.xAddress, ctx.yAddress, 1001n, 1000n);
    await ctx.router.setRate(ctx.yAddress, ctx.xAddress, 1002n, 1000n);

    const Controller = await ethers.getContractFactory("TriangularRouteController");
    const controllerNoC = await Controller.deploy(await ctx.usdc.getAddress(), await ctx.executor.getAddress(), ctx.owner.address);
    await ctx.executor.setController(await controllerNoC.getAddress());
    await controllerNoC.setAdapterConfig(
      await controllerNoC.ADAPTER_UNISWAP_V3(),
      true,
      ctx.factory.address,
      await ctx.router.getAddress(),
      await ctx.quoter.getAddress(),
    );

    const trade = runtimeTrade(ctx, 7n, runtimePools([[0, low.address], [1, high.address]]));
    const deadline = BigInt((await ethers.provider.getBlock("latest")).timestamp + 600);

    await expect(controllerNoC.runBestRuntimeTradesAndExecuteCrossPool([trade], {
      amount: 1_000_000n,
      deadline,
      minFinalTokenX: 1_000_000n,
      minProfitTokenX: 1n,
    }))
      .to.be.revertedWithCustomError(controllerNoC, "CrossPoolExecutorMissing");
  });

  it("auto execution chooses contract C before contract B when the direct two-pool route is profitable", async function () {
    const ctx = await deployCrossPoolExecutionFixture();
    const low = await deployV3Pool(ctx, ctx.xAddress, ctx.yAddress, -150, 1_000_000n, 500n);
    const high = await deployV3Pool(ctx, ctx.xAddress, ctx.yAddress, 250, 1_000_000n, 3000n);
    const amount = 1_000_000n;
    const deadline = BigInt((await ethers.provider.getBlock("latest")).timestamp + 600);

    await ctx.router.setRate(ctx.xAddress, ctx.yAddress, 1001n, 1000n);
    await ctx.router.setRate(ctx.yAddress, ctx.xAddress, 1002n, 1000n);
    await ctx.router.setRate(ctx.usdcAddress, ctx.xAddress, 1001n, 1000n);
    await ctx.router.setRate(ctx.yAddress, ctx.usdcAddress, 1001n, 1000n);

    const trade = runtimeTrade(ctx, 77n, runtimePools([[0, low.address], [1, high.address]]));
    const triangularParams = {
      amount,
      deadline,
      amountOutMinUsdc: amount,
      minProfitUsdc: 1n,
      usdcToTokenXFee: 3000n,
      tokenYToUsdcFee: 3000n,
    };
    const crossPoolParams = {
      amount,
      deadline,
      minFinalTokenX: amount,
      minProfitTokenX: 1n,
    };

    const [found, executionKind, selectedTradeArrayIndex, decision] =
      await ctx.controller.previewFirstProfitableRuntimeAutoExecution.staticCall([trade], triangularParams, crossPoolParams);
    expect(found).to.equal(true);
    expect(executionKind).to.equal(await ctx.controller.EXECUTION_KIND_CROSS_POOL());
    expect(selectedTradeArrayIndex).to.equal(0n);
    expect(decision.tradeIndex).to.equal(77n);

    await expect(ctx.controller.runFirstProfitableRuntimeTradesAndExecuteAuto([trade], triangularParams, crossPoolParams))
      .to.emit(ctx.controller, "RuntimeAutoExecutionSelected")
      .withArgs(await ctx.controller.EXECUTION_KIND_CROSS_POOL(), 0n, 77n)
      .and.to.emit(ctx.controller, "RuntimeCrossPoolExecuted")
      .withArgs(0n, 77n, high.address, low.address, amount, 1_000_501n, 2502n);

    expect(await ctx.x.balanceOf(ctx.owner.address)).to.equal(2502n);
    expect(await ctx.usdc.balanceOf(ctx.owner.address)).to.equal(0n);
  });

  it("auto execution falls back to contract B when the two-pool route is not profitable", async function () {
    const ctx = await deployCrossPoolExecutionFixture();
    const low = await deployV3Pool(ctx, ctx.xAddress, ctx.yAddress, -150, 1_000_000n, 500n);
    const high = await deployV3Pool(ctx, ctx.xAddress, ctx.yAddress, 250, 1_000_000n, 3000n);
    const amount = 1_000_000n;
    const deadline = BigInt((await ethers.provider.getBlock("latest")).timestamp + 600);

    await ctx.router.setRate(ctx.xAddress, ctx.yAddress, 1000n, 1000n);
    await ctx.router.setRate(ctx.yAddress, ctx.xAddress, 1000n, 1000n);
    await ctx.router.setRate(ctx.usdcAddress, ctx.xAddress, 1002n, 1000n);
    await ctx.router.setRate(ctx.yAddress, ctx.usdcAddress, 1002n, 1000n);

    const trade = runtimeTrade(ctx, 88n, runtimePools([[0, low.address], [1, high.address]]));
    const triangularParams = {
      amount,
      deadline,
      amountOutMinUsdc: amount,
      minProfitUsdc: 1n,
      usdcToTokenXFee: 3000n,
      tokenYToUsdcFee: 3000n,
    };
    const crossPoolParams = {
      amount,
      deadline,
      minFinalTokenX: amount,
      minProfitTokenX: 1n,
    };

    const [found, executionKind] =
      await ctx.controller.previewFirstProfitableRuntimeAutoExecution.staticCall([trade], triangularParams, crossPoolParams);
    expect(found).to.equal(true);
    expect(executionKind).to.equal(await ctx.controller.EXECUTION_KIND_TRIANGULAR());

    await expect(ctx.controller.runFirstProfitableRuntimeTradesAndExecuteAuto([trade], triangularParams, crossPoolParams))
      .to.emit(ctx.controller, "RuntimeAutoExecutionSelected")
      .withArgs(await ctx.controller.EXECUTION_KIND_TRIANGULAR(), 0n, 88n)
      .and.to.emit(ctx.controller, "RuntimeTradeExecuted")
      .withArgs(0n, 88n, await ctx.router.getAddress(), amount, 1_000_501n, 3504n);

    expect(await ctx.usdc.balanceOf(ctx.owner.address)).to.equal(3504n);
    expect(await ctx.x.balanceOf(ctx.owner.address)).to.equal(0n);
  });

  it("auto execution supports a direct X-USDC two-pool route through contract C", async function () {
    const ctx = await deployCrossPoolExecutionFixture();
    const low = await deployV3Pool(ctx, ctx.xAddress, ctx.usdcAddress, -150, 1_000_000n, 500n);
    const high = await deployV3Pool(ctx, ctx.xAddress, ctx.usdcAddress, 250, 1_000_000n, 3000n);
    const amount = 1_000_000n;
    const deadline = BigInt((await ethers.provider.getBlock("latest")).timestamp + 600);

    await ctx.router.setRate(ctx.xAddress, ctx.usdcAddress, 1001n, 1000n);
    await ctx.router.setRate(ctx.usdcAddress, ctx.xAddress, 1002n, 1000n);

    const trade = runtimeTrade(ctx, 99n, runtimePools([[0, low.address], [1, high.address]]), ctx.xAddress, ctx.usdcAddress);
    const triangularParams = {
      amount,
      deadline,
      amountOutMinUsdc: amount,
      minProfitUsdc: 1n,
      usdcToTokenXFee: 3000n,
      tokenYToUsdcFee: 3000n,
    };
    const crossPoolParams = {
      amount,
      deadline,
      minFinalTokenX: amount,
      minProfitTokenX: 1n,
    };

    const [found, executionKind, selectedTradeArrayIndex, decision] =
      await ctx.controller.previewFirstProfitableRuntimeAutoExecution.staticCall([trade], triangularParams, crossPoolParams);
    expect(found).to.equal(true);
    expect(executionKind).to.equal(await ctx.controller.EXECUTION_KIND_CROSS_POOL());
    expect(selectedTradeArrayIndex).to.equal(0n);
    expect(decision.tokenY).to.equal(ctx.usdcAddress);

    await expect(ctx.controller.runFirstProfitableRuntimeTradesAndExecuteAuto([trade], triangularParams, crossPoolParams))
      .to.emit(ctx.controller, "RuntimeCrossPoolExecuted")
      .withArgs(0n, 99n, high.address, low.address, amount, 1_000_501n, 2502n);

    expect(await ctx.x.balanceOf(ctx.owner.address)).to.equal(2502n);
  });

  it("auto execution also supports a direct USDC-X two-pool route through contract C", async function () {
    const ctx = await deployCrossPoolExecutionFixture();
    const low = await deployV3Pool(ctx, ctx.usdcAddress, ctx.xAddress, -150, 1_000_000n, 500n);
    const high = await deployV3Pool(ctx, ctx.usdcAddress, ctx.xAddress, 250, 1_000_000n, 3000n);
    const amount = 1_000_000n;
    const deadline = BigInt((await ethers.provider.getBlock("latest")).timestamp + 600);

    await ctx.router.setRate(ctx.usdcAddress, ctx.xAddress, 1001n, 1000n);
    await ctx.router.setRate(ctx.xAddress, ctx.usdcAddress, 1002n, 1000n);

    const trade = runtimeTrade(ctx, 100n, runtimePools([[0, low.address], [1, high.address]]), ctx.usdcAddress, ctx.xAddress);
    const triangularParams = {
      amount,
      deadline,
      amountOutMinUsdc: amount,
      minProfitUsdc: 1n,
      usdcToTokenXFee: 3000n,
      tokenYToUsdcFee: 3000n,
    };
    const crossPoolParams = {
      amount,
      deadline,
      minFinalTokenX: amount,
      minProfitTokenX: 1n,
    };

    const preview = await ctx.controller.previewFirstProfitableRuntimeAutoExecution.staticCall(
      [trade],
      triangularParams,
      crossPoolParams
    );
    const runPreview = await ctx.controller.runFirstProfitableRuntimeTradesAndExecuteAuto.staticCall(
      [trade],
      triangularParams,
      crossPoolParams
    );

    expect(preview[0]).to.equal(true);
    expect(preview[1]).to.equal(await ctx.controller.EXECUTION_KIND_CROSS_POOL());
    expect(preview[2]).to.equal(0n);
    expect(preview[3].tokenX).to.equal(ctx.usdcAddress);
    expect(preview[3].tokenY).to.equal(ctx.xAddress);
    expect(runPreview[0]).to.equal(await ctx.controller.EXECUTION_KIND_CROSS_POOL());

    await expect(ctx.controller.runFirstProfitableRuntimeTradesAndExecuteAuto([trade], triangularParams, crossPoolParams))
      .to.emit(ctx.controller, "RuntimeCrossPoolExecuted")
      .withArgs(0n, 100n, high.address, low.address, amount, preview[4].protectedAmountOutMinUsdc, runPreview[3]);

    expect(await ctx.usdc.balanceOf(ctx.owner.address)).to.equal(runPreview[3]);
    expect(await ctx.x.balanceOf(ctx.owner.address)).to.equal(0n);
  });

  it("ordered auto execution returns status 1 for trade[0] U-X cross-pool", async function () {
    const ctx = await deployCrossPoolExecutionFixture();
    const low = await deployV3Pool(ctx, ctx.usdcAddress, ctx.xAddress, -150, 1_000_000n, 500n);
    const high = await deployV3Pool(ctx, ctx.usdcAddress, ctx.xAddress, 250, 1_000_000n, 3000n);
    const amount = 1_000_000n;
    const deadline = BigInt((await ethers.provider.getBlock("latest")).timestamp + 600);

    await ctx.router.setRate(ctx.usdcAddress, ctx.xAddress, 1001n, 1000n);
    await ctx.router.setRate(ctx.xAddress, ctx.usdcAddress, 1002n, 1000n);

    const trade = runtimeTrade(ctx, 101n, runtimePools([[0, low.address], [1, high.address]]), ctx.usdcAddress, ctx.xAddress);
    const triangularParams = {
      amount,
      deadline,
      amountOutMinUsdc: amount,
      minProfitUsdc: 1n,
      usdcToTokenXFee: 3000n,
      tokenYToUsdcFee: 3000n,
    };
    const crossPoolParams = {
      amount,
      deadline,
      minFinalTokenX: amount,
      minProfitTokenX: 1n,
    };

    const preview = await ctx.controller.previewOrderedRuntimeAutoExecution.staticCall(
      [trade],
      triangularParams,
      crossPoolParams,
      false
    );
    expect(preview[0]).to.equal(true);
    expect(preview[1]).to.equal(await ctx.controller.STRATEGY_STATUS_UX_CROSS_POOL());
    expect(preview[2]).to.equal(await ctx.controller.EXECUTION_KIND_CROSS_POOL());
    expect(preview[3]).to.equal(0n);

    await expect(ctx.controller.runOrderedRuntimeTradesAndExecuteAuto([trade], triangularParams, crossPoolParams, false))
      .to.emit(ctx.controller, "RuntimeOrderedAutoExecutionSelected")
      .withArgs(
        await ctx.controller.STRATEGY_STATUS_UX_CROSS_POOL(),
        await ctx.controller.EXECUTION_KIND_CROSS_POOL(),
        0n,
        101n
      );
  });

  it("ordered auto maps trade[2] X-Y to status 4 unless non-USDC cross-pool is enabled", async function () {
    const ctx = await deployCrossPoolExecutionFixture();
    const low = await deployV3Pool(ctx, ctx.xAddress, ctx.yAddress, -150, 1_000_000n, 500n);
    const high = await deployV3Pool(ctx, ctx.xAddress, ctx.yAddress, 250, 1_000_000n, 3000n);
    const amount = 1_000_000n;
    const deadline = BigInt((await ethers.provider.getBlock("latest")).timestamp + 600);

    await ctx.router.setRate(ctx.xAddress, ctx.yAddress, 1002n, 1000n);
    await ctx.router.setRate(ctx.yAddress, ctx.xAddress, 1002n, 1000n);
    await ctx.router.setRate(ctx.usdcAddress, ctx.xAddress, 1002n, 1000n);
    await ctx.router.setRate(ctx.yAddress, ctx.usdcAddress, 1002n, 1000n);

    const trades = [
      runtimeTrade(ctx, 301n, emptyRuntimePools(), ctx.usdcAddress, ctx.xAddress),
      runtimeTrade(ctx, 302n, emptyRuntimePools(), ctx.usdcAddress, ctx.yAddress),
      runtimeTrade(ctx, 303n, runtimePools([[0, low.address], [1, high.address]]), ctx.xAddress, ctx.yAddress),
    ];
    const triangularParams = {
      amount,
      deadline,
      amountOutMinUsdc: amount,
      minProfitUsdc: 1n,
      usdcToTokenXFee: 3000n,
      tokenYToUsdcFee: 3000n,
    };
    const crossPoolParams = {
      amount,
      deadline,
      minFinalTokenX: amount,
      minProfitTokenX: 1n,
    };

    const disabledPreview = await ctx.controller.previewOrderedRuntimeAutoExecution.staticCall(
      trades,
      triangularParams,
      crossPoolParams,
      false
    );
    expect(disabledPreview[0]).to.equal(true);
    expect(disabledPreview[1]).to.equal(await ctx.controller.STRATEGY_STATUS_XY_USDC_FALLBACK());
    expect(disabledPreview[2]).to.equal(await ctx.controller.EXECUTION_KIND_TRIANGULAR());
    expect(disabledPreview[3]).to.equal(2n);

    const enabledPreview = await ctx.controller.previewOrderedRuntimeAutoExecution.staticCall(
      trades,
      triangularParams,
      crossPoolParams,
      true
    );
    expect(enabledPreview[0]).to.equal(true);
    expect(enabledPreview[1]).to.equal(await ctx.controller.STRATEGY_STATUS_XY_CROSS_POOL());
    expect(enabledPreview[2]).to.equal(await ctx.controller.EXECUTION_KIND_CROSS_POOL());
    expect(enabledPreview[3]).to.equal(2n);

    await expect(ctx.controller.runOrderedRuntimeTradesAndExecuteAuto(trades, triangularParams, crossPoolParams, false))
      .to.emit(ctx.controller, "RuntimeOrderedAutoExecutionSelected")
      .withArgs(
        await ctx.controller.STRATEGY_STATUS_XY_USDC_FALLBACK(),
        await ctx.controller.EXECUTION_KIND_TRIANGULAR(),
        2n,
        303n
      );
  });

  it("ordered auto scans only the first five pools in each runtime trade", async function () {
    const ctx = await deployCrossPoolExecutionFixture();
    const low = await deployV3Pool(ctx, ctx.usdcAddress, ctx.xAddress, -150, 1_000_000n, 500n);
    const high = await deployV3Pool(ctx, ctx.usdcAddress, ctx.xAddress, 250, 1_000_000n, 3000n);
    const amount = 1_000_000n;
    const deadline = BigInt((await ethers.provider.getBlock("latest")).timestamp + 600);

    await ctx.router.setRate(ctx.usdcAddress, ctx.xAddress, 1001n, 1000n);
    await ctx.router.setRate(ctx.xAddress, ctx.usdcAddress, 1002n, 1000n);

    const latePoolsTrade = runtimeTrade(
      ctx,
      401n,
      runtimePools([[5, low.address], [6, high.address]]),
      ctx.usdcAddress,
      ctx.xAddress
    );
    const triangularParams = {
      amount,
      deadline,
      amountOutMinUsdc: amount,
      minProfitUsdc: 1n,
      usdcToTokenXFee: 3000n,
      tokenYToUsdcFee: 3000n,
    };
    const crossPoolParams = {
      amount,
      deadline,
      minFinalTokenX: amount,
      minProfitTokenX: 1n,
    };

    const oldPreview = await ctx.controller.previewFirstProfitableRuntimeAutoExecution.staticCall(
      [latePoolsTrade],
      triangularParams,
      crossPoolParams
    );
    expect(oldPreview[0]).to.equal(true);

    const orderedPreview = await ctx.controller.previewOrderedRuntimeAutoExecution.staticCall(
      [latePoolsTrade],
      triangularParams,
      crossPoolParams,
      false
    );
    expect(orderedPreview[0]).to.equal(false);
    expect(orderedPreview[1]).to.equal(await ctx.controller.STRATEGY_STATUS_NO_PROFITABLE_ROUTE());
  });
});
