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

    await expect(ctx.controller.previewRuntimeTrade(runtimeTrade(ctx, 1n, runtimePools([[0, pool.address]]), ctx.usdcAddress, ctx.yAddress)))
      .to.be.revertedWithCustomError(ctx.controller, "InvalidRequest");

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
});
