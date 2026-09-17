const { expect } = require("chai");
const { ethers } = require("hardhat");

async function deployFixture() {
  const [owner] = await ethers.getSigners();

  const MockExecutor = await ethers.getContractFactory("MockComposerExecutor");
  const executor = await MockExecutor.deploy();

  const Composer = await ethers.getContractFactory("ArbitrageComposer");
  const composer = await Composer.deploy(owner.address, await executor.getAddress());

  return { owner, executor, composer };
}

async function deployOnchainFixture() {
  const [owner] = await ethers.getSigners();
  const MockExecutor = await ethers.getContractFactory("MockComposerExecutor");
  const executor = await MockExecutor.deploy();
  const MockPoolManager = await ethers.getContractFactory("MockPoolManagerExtsload");
  const poolManager = await MockPoolManager.deploy();
  const Composer = await ethers.getContractFactory("ArbitrageComposer");
  const composer = await Composer.deploy(await poolManager.getAddress(), await executor.getAddress());
  return { owner, executor, composer, poolManager };
}

async function slotForPool(poolKey) {
  const poolId = ethers.keccak256(
    ethers.AbiCoder.defaultAbiCoder().encode(
      ["tuple(address currency0,address currency1,uint24 fee,int24 tickSpacing,address hooks)"],
      [poolKey]
    )
  );
  return ethers.keccak256(ethers.concat([poolId, ethers.zeroPadValue("0x06", 32)]));
}

async function setPoolPrice(poolManager, poolKey, sqrtPriceX96) {
  await poolManager.setSlot(await slotForPool(poolKey), ethers.zeroPadValue(ethers.toBeHex(sqrtPriceX96), 32));
}

function poolKey(currency0, currency1) {
  return {
    currency0,
    currency1,
    fee: 3000,
    tickSpacing: 60,
    hooks: ethers.ZeroAddress,
  };
}

async function makeCandidate(
  deviationBps,
  firstKey0 = "0x1111111111111111111111111111111111111111",
  mQuote = 0n,
  xQuote = 0n,
  yQuote = 0n
) {
  const zero = ethers.ZeroAddress;
  const poolKey = (currency0, currency1) => ({
    currency0,
    currency1,
    fee: 3000,
    tickSpacing: 60,
    hooks: zero,
  });

  // Two opposite directions for the same TZ/TB/TA triangle.
  const keysets = [
    [poolKey(firstKey0, "0x2222222222222222222222222222222222222222"),
      poolKey("0x2222222222222222222222222222222222222222", "0x3333333333333333333333333333333333333333"),
      poolKey("0x1111111111111111111111111111111111111111", "0x3333333333333333333333333333333333333333")],
    [poolKey("0x1111111111111111111111111111111111111111", "0x2222222222222222222222222222222222222222"),
      poolKey("0x2222222222222222222222222222222222222222", "0x3333333333333333333333333333333333333333"),
      poolKey("0x1111111111111111111111111111111111111111", "0x3333333333333333333333333333333333333333")],
  ];
  const directionsets = [
    [true, true, false],
    [false, false, true],
  ];

  return {
    routePools: keysets,
    routeDirections: directionsets,
    deviationBps: deviationBps,
    mQuote,
    xQuote,
    yQuote,
  };
}

describe("ArbitrageComposer", function () {
  it("skips candidates below the deviation threshold", async function () {
    const { composer, executor } = await deployFixture();
    const low = await makeCandidate(10);
    const high = await makeCandidate(300);

    await composer.runBest(
      "0x1111111111111111111111111111111111111111",
      [low, high],
      ethers.parseEther("1000"),
      ethers.parseEther("1"),
      200n
    );

    expect(await executor.calls()).to.equal(1n);
    expect(await executor.shouldRevert()).to.equal(false);
  });

  it("continues to the next candidate when the first executor call reverts", async function () {
    const { composer, executor } = await deployFixture();
    const marker = "0xEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEE";
    const alpha = await makeCandidate(300, marker);
    const beta = await makeCandidate(500);

    await executor.setRevertMarkerCurrency0(marker);
    const preflight = await composer.runBest.staticCall(
      "0x1111111111111111111111111111111111111111",
      [alpha, beta],
      ethers.parseEther("1000"),
      ethers.parseEther("1"),
      250n
    );
    expect(preflight.candidateIndex).to.equal(1n);
    await composer.runBest(
      "0x1111111111111111111111111111111111111111",
      [alpha, beta],
      ethers.parseEther("1000"),
      ethers.parseEther("1"),
      250n
    );

    // The failed inner call rolls back its own storage writes; the successful
    // second candidate is the only persisted executor call.
    expect(await executor.calls()).to.equal(1n);
  });

  it("uses on-chain deviation from m/x/y quotes instead of trusting candidate deviation", async function () {
    const { composer, executor } = await deployFixture();
    const one = 1_000_000_000_000_000_000n;
    const low = await makeCandidate(0, "0x1111111111111111111111111111111111111111", one, one, one);
    const high = await makeCandidate(0, "0x1111111111111111111111111111111111111111", 2n * one, one, one);

    await composer.runBest(
      "0x1111111111111111111111111111111111111111",
      [low, high],
      ethers.parseEther("1000"),
      ethers.parseEther("1"),
      5000n
    );

    expect(await executor.calls()).to.equal(1n);
  });

  it("does not treat an uncomputable quote deviation as infinite", async function () {
    const { composer } = await deployFixture();
    const one = 1_000_000_000_000_000_000n;
    const candidate = await makeCandidate(10, "0x1111111111111111111111111111111111111111", one, 1n, one * 1_000_000n);

    await expect(
      composer.runBest(
        "0x1111111111111111111111111111111111111111",
        [candidate],
        ethers.parseEther("1000"),
        ethers.parseEther("1"),
        250n
      )
    ).to.be.revertedWithCustomError(composer, "NoProfitableCandidate");
  });

  it("reads PoolManager storage and computes deviation on-chain", async function () {
    const { composer, executor, poolManager } = await deployOnchainFixture();
    const candidate = await makeCandidate(0);
    const basePrice = 1n << 96n;
    const highPrice = (1n << 96n) * 3n;
    await setPoolPrice(poolManager, candidate.routePools[0][0], highPrice);
    await setPoolPrice(poolManager, candidate.routePools[0][1], basePrice);
    await setPoolPrice(poolManager, candidate.routePools[0][2], basePrice);

    await composer.runBest(
      "0x1111111111111111111111111111111111111111",
      [candidate],
      ethers.parseEther("1000"),
      ethers.parseEther("1"),
      10000n
    );

    expect(await executor.calls()).to.equal(1n);
  });

  it("uses the best route deviation when a candidate has multiple routes", async function () {
    const { composer, executor, poolManager } = await deployOnchainFixture();
    const basePrice = 1n << 96n;
    const highPrice = basePrice * 3n;
    const tokenZ = "0x1111111111111111111111111111111111111111";
    const tokenB = "0x4444444444444444444444444444444444444444";
    const tokenA = "0x5555555555555555555555555555555555555555";
    const candidate = await makeCandidate(0);

    for (let j = 0; j < 3; j++) {
      await setPoolPrice(poolManager, candidate.routePools[0][j], basePrice);
    }

    candidate.routePools[1] = [
      poolKey(tokenZ, tokenB),
      poolKey(tokenB, tokenA),
      poolKey(tokenZ, tokenA),
    ];
    candidate.routeDirections[1] = [true, true, false];
    await setPoolPrice(poolManager, candidate.routePools[1][0], highPrice);
    await setPoolPrice(poolManager, candidate.routePools[1][1], basePrice);
    await setPoolPrice(poolManager, candidate.routePools[1][2], basePrice);

    await composer.runBest(
      tokenZ,
      [candidate],
      ethers.parseEther("1000"),
      ethers.parseEther("1"),
      15000n
    );

    expect(await executor.calls()).to.equal(1n);
  });

  it("maps the sorted route back to the original candidate route index", async function () {
    const { composer, poolManager } = await deployOnchainFixture();
    const basePrice = 1n << 96n;
    const highPrice = basePrice * 3n;
    const tokenZ = "0x1111111111111111111111111111111111111111";
    const tokenX = "0x7777777777777777777777777777777777777777";
    const tokenY = "0x8888888888888888888888888888888888888888";
    const candidate = await makeCandidate(0);

    for (let j = 0; j < 3; j++) {
      await setPoolPrice(poolManager, candidate.routePools[0][j], basePrice);
    }

    candidate.routePools[1] = [
      poolKey(tokenZ, tokenX),
      poolKey(tokenX, tokenY),
      poolKey(tokenZ, tokenY),
    ];
    candidate.routeDirections[1] = [true, true, false];
    await setPoolPrice(poolManager, candidate.routePools[1][0], highPrice);
    await setPoolPrice(poolManager, candidate.routePools[1][1], basePrice);
    await setPoolPrice(poolManager, candidate.routePools[1][2], basePrice);

    const preflight = await composer.runBestDetailed.staticCall(
      tokenZ,
      [candidate],
      ethers.parseEther("1000"),
      ethers.parseEther("1"),
      15000n
    );

    expect(preflight.candidateIndex).to.equal(0n);
    expect(preflight.selectedKeys[0].currency1).to.equal(tokenX);
  });

  it("ignores non-anchored routes when scoring a candidate on-chain", async function () {
    const { composer, poolManager } = await deployOnchainFixture();
    const basePrice = 1n << 96n;
    const highPrice = basePrice * 3n;
    const tokenZ = "0x1111111111111111111111111111111111111111";
    const tokenB = "0x4444444444444444444444444444444444444444";
    const tokenA = "0x5555555555555555555555555555555555555555";
    const tokenC = "0x6666666666666666666666666666666666666666";
    const candidate = await makeCandidate(0);

    for (let j = 0; j < 3; j++) {
      await setPoolPrice(poolManager, candidate.routePools[0][j], basePrice);
    }

    candidate.routePools[1] = [
      poolKey(tokenB, tokenA),
      poolKey(tokenA, tokenC),
      poolKey(tokenB, tokenC),
    ];
    candidate.routeDirections[1] = [true, true, false];
    await setPoolPrice(poolManager, candidate.routePools[1][0], highPrice);
    await setPoolPrice(poolManager, candidate.routePools[1][1], basePrice);
    await setPoolPrice(poolManager, candidate.routePools[1][2], basePrice);

    await expect(
      composer.runBest(
        tokenZ,
        [candidate],
        ethers.parseEther("1000"),
        ethers.parseEther("1"),
        15000n
      )
    ).to.be.revertedWithCustomError(composer, "NoProfitableCandidate");
  });

  it("uses fee-adjusted deviation when deciding eligibility", async function () {
    const { composer, poolManager } = await deployOnchainFixture();
    const basePrice = 1n << 96n;
    const highPrice = basePrice * 2n;
    const tokenZ = "0x1111111111111111111111111111111111111111";
    const candidate = await makeCandidate(0);

    for (let j = 0; j < 3; j++) {
      candidate.routePools[0][j].fee = 360000;
    }
    await setPoolPrice(poolManager, candidate.routePools[0][0], highPrice);
    await setPoolPrice(poolManager, candidate.routePools[0][1], basePrice);
    await setPoolPrice(poolManager, candidate.routePools[0][2], basePrice);

    await expect(
      composer.runBest(
        tokenZ,
        [candidate],
        ethers.parseEther("1000"),
        ethers.parseEther("1"),
        600n
      )
    ).to.be.revertedWithCustomError(composer, "NoProfitableCandidate");
  });

  it("does not rank a fee-adjusted losing route as a profitable deviation", async function () {
    const { composer, poolManager } = await deployOnchainFixture();
    const basePrice = 1n << 96n;
    const highPrice = basePrice * 2n;
    const tokenZ = "0x1111111111111111111111111111111111111111";
    const candidate = await makeCandidate(0);

    for (let j = 0; j < 3; j++) {
      candidate.routePools[0][j].fee = 500000;
    }
    await setPoolPrice(poolManager, candidate.routePools[0][0], highPrice);
    await setPoolPrice(poolManager, candidate.routePools[0][1], basePrice);
    await setPoolPrice(poolManager, candidate.routePools[0][2], basePrice);

    await expect(
      composer.runBest(
        tokenZ,
        [candidate],
        ethers.parseEther("1000"),
        ethers.parseEther("1"),
        100n
      )
    ).to.be.revertedWithCustomError(composer, "NoProfitableCandidate");
  });

  it("rejects a candidate with discontinuous middle hops", async function () {
    const { composer } = await deployFixture();
    const candidate = await makeCandidate(500);
    candidate.routePools[0][1].currency0 = "0x9999999999999999999999999999999999999999";

    await expect(
      composer.runBest(
        "0x1111111111111111111111111111111111111111",
        [candidate],
        ethers.parseEther("1000"),
        ethers.parseEther("1"),
        250n
      )
    ).to.be.revertedWithCustomError(composer, "DexError").withArgs(40010401n);
  });

  it("reverts when no candidate is executable", async function () {
    const { composer, executor } = await deployFixture();
    const low = await makeCandidate(10);
    const high = await makeCandidate(20);

    await expect(
      composer.runBest(
        "0x1111111111111111111111111111111111111111",
        [low, high],
        ethers.parseEther("1000"),
        ethers.parseEther("1"),
        250n
      )
    ).to.be.revertedWithCustomError(composer, "NoProfitableCandidate");
  });

  it("reverts with CONTRACT.DEX route invalid when no candidate is anchored to the borrow token", async function () {
    const { composer, executor } = await deployFixture();
    const wrongTokenCandidate = await makeCandidate(500, "0x9999999999999999999999999999999999999999");

    await expect(
      composer.runBest(
        "0x1111111111111111111111111111111111111111",
        [wrongTokenCandidate],
        ethers.parseEther("1000"),
        ethers.parseEther("1"),
        250n
      )
    ).to.be.revertedWithCustomError(composer, "DexError").withArgs(40010401n);
  });

  it("does not let an invalid candidate mask a real no-profit result", async function () {
    const { composer } = await deployFixture();
    const validButBelowThreshold = await makeCandidate(10);
    const invalid = await makeCandidate(500, "0x9999999999999999999999999999999999999999");

    await expect(
      composer.runBest(
        "0x1111111111111111111111111111111111111111",
        [validButBelowThreshold, invalid],
        ethers.parseEther("1000"),
        ethers.parseEther("1"),
        250n
      )
    ).to.be.revertedWithCustomError(composer, "NoProfitableCandidate");
  });

  it("rejects a candidate when route pools and directions have different lengths", async function () {
    const { composer } = await deployFixture();
    const candidate = await makeCandidate(500);
    candidate.routeDirections = [candidate.routeDirections[0]];

    await expect(
      composer.runBest(
        "0x1111111111111111111111111111111111111111",
        [candidate],
        ethers.parseEther("1000"),
        ethers.parseEther("1"),
        250n
      )
    ).to.be.revertedWithCustomError(composer, "DexError").withArgs(40010401n);
  });

  it("reverts with CONTRACT.DEX route invalid when official best-route keysets mismatch directions", async function () {
    const { poolManager } = await deployOnchainFixture();
    const OfficialV4FlashArb = await ethers.getContractFactory("OfficialV4FlashArb");
    const arb = await OfficialV4FlashArb.deploy(await poolManager.getAddress());
    const zero = ethers.ZeroAddress;
    const key = {
      currency0: "0x1111111111111111111111111111111111111111",
      currency1: "0x2222222222222222222222222222222222222222",
      fee: 3000,
      tickSpacing: 60,
      hooks: zero,
    };

    await expect(
      arb.runArbitrageBest(
        "0x1111111111111111111111111111111111111111",
        [[key, key, key], [key, key, key]],
        [[true, false, true]],
        ethers.parseEther("1000"),
        1n
      )
    ).to.be.revertedWithCustomError(arb, "DexError").withArgs(40010401n);
  });
  it("executes the candidate with the highest deviation rather than the first one listed", async function () {
    const { composer, executor } = await deployFixture();
    const low = await makeCandidate(300);
    const high = await makeCandidate(500);

    const preflight = await composer.runBest.staticCall(
      "0x1111111111111111111111111111111111111111",
      [low, high],
      ethers.parseEther("1000"),
      ethers.parseEther("1"),
      200n
    );
    expect(preflight.candidateIndex).to.equal(1n);

    await composer.runBest(
      "0x1111111111111111111111111111111111111111",
      [low, high],
      ethers.parseEther("1000"),
      ethers.parseEther("1"),
      200n
    );
    // Only the highest-deviation candidate is sent to the executor.
    expect(await executor.calls()).to.equal(1n);
  });

  it("falls back to the next highest deviation candidate when the top one fails", async function () {
    const { composer, executor } = await deployFixture();
    const marker = "0xEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEE";
    const top = await makeCandidate(800);
    top.routePools[0][1].currency1 = marker;
    top.routePools[0][2].currency1 = marker;
    const mid = await makeCandidate(500);
    const low = await makeCandidate(300);

    await executor.setFailMarkerCurrency0(marker);
    const preflight = await composer.runBest.staticCall(
      "0x1111111111111111111111111111111111111111",
      [low, mid, top],
      ethers.parseEther("1000"),
      ethers.parseEther("1"),
      200n
    );
    expect(preflight.candidateIndex).to.equal(1n);

    await composer.runBest(
      "0x1111111111111111111111111111111111111111",
      [low, mid, top],
      ethers.parseEther("1000"),
      ethers.parseEther("1"),
      200n
    );
    // The top candidate (marker) is rejected by the executor; mid is tried next.
    expect(await executor.calls()).to.equal(2n);
    expect(await executor.lastFailMarkerTried()).to.equal(ethers.getAddress(marker));
  });

  it("runBestDetailed follows the same highest-deviation ordering", async function () {
    const { composer, executor } = await deployFixture();
    const low = await makeCandidate(300);
    const high = await makeCandidate(500);

    const preflight = await composer.runBestDetailed.staticCall(
      "0x1111111111111111111111111111111111111111",
      [low, high],
      ethers.parseEther("1000"),
      ethers.parseEther("1"),
      200n
    );
    expect(preflight.candidateIndex).to.equal(1n);

    await composer.runBestDetailed(
      "0x1111111111111111111111111111111111111111",
      [low, high],
      ethers.parseEther("1000"),
      ethers.parseEther("1"),
      200n
    );
    expect(await executor.calls()).to.equal(1n);
  });

});
