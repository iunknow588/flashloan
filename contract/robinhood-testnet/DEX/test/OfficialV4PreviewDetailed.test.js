const { expect } = require("chai");
const { ethers } = require("hardhat");

const DECIMALS = 18n;
const TOKEN_TOTAL = 10_000_000n * 10n ** DECIMALS;
const MILLION = 1_000_000n * 10n ** DECIMALS;

function toToken(value) {
  return BigInt(value) * 10n ** DECIMALS;
}

function makeBridgePoolKey(currency0, currency1, fee = 3000) {
  return {
    currency0,
    currency1,
    fee,
    tickSpacing: 60,
    hooks: ethers.ZeroAddress,
  };
}

async function deployBridgeTriFixture() {
  const [owner] = await ethers.getSigners();
  const Token = await ethers.getContractFactory("TestERC20");
  const tz = await Token.deploy("Test Z", "tz", 18, owner.address);
  const ta = await Token.deploy("Test A", "ta", 18, owner.address);
  const tb = await Token.deploy("Test B", "tb", 18, owner.address);

  await tz.mint(owner.address, TOKEN_TOTAL);
  await ta.mint(owner.address, TOKEN_TOTAL);
  await tb.mint(owner.address, TOKEN_TOTAL);

  const PoolManager = await ethers.getContractFactory("MockOfficialV4PoolManager");
  const poolManager = await PoolManager.deploy();

  const tzAddress = await tz.getAddress();
  const taAddress = await ta.getAddress();
  const tbAddress = await tb.getAddress();
  const poolManagerAddress = await poolManager.getAddress();

  await poolManager.createPool(tbAddress, tzAddress);
  await poolManager.createPool(taAddress, tbAddress);
  await poolManager.createPool(tzAddress, taAddress);

  await tz.approve(poolManagerAddress, ethers.MaxUint256);
  await ta.approve(poolManagerAddress, ethers.MaxUint256);
  await tb.approve(poolManagerAddress, ethers.MaxUint256);

  await poolManager.addLiquidity(tbAddress, tzAddress, MILLION, toToken(100_000));
  await poolManager.addLiquidity(taAddress, tbAddress, toToken(100_000), MILLION);
  await poolManager.addLiquidity(tzAddress, taAddress, MILLION, toToken(100_000));

  const Arb = await ethers.getContractFactory("OfficialV4FlashArb");
  const arb = await Arb.deploy(poolManagerAddress);

  return { tz, ta, tb, poolManager, arb };
}

describe("OfficialV4PreviewDetailed", function () {
  it("returns concrete netProductDelta for a low-profit direction", async function () {
    const { tz, ta, tb, poolManager, arb } = await deployBridgeTriFixture();
    const tzAddress = await tz.getAddress();
    const taAddress = await ta.getAddress();
    const tbAddress = await tb.getAddress();

    // Reverse of the profitable orientation: tz -> ta, ta -> tb, tb -> tz.
    // The route stays anchored and has no positive fee-adjusted product, so
    // previewBestPool would revert but previewBestPoolDetailed must still
    // supply concrete indices and netProductDelta for on-chain review.
    const reverseEntryKey = makeBridgePoolKey(tzAddress, taAddress);
    const reverseBridgeKey = makeBridgePoolKey(taAddress, tbAddress);
    const reverseExitKey = makeBridgePoolKey(tbAddress, tzAddress);

    const result = await arb.previewBestPoolDetailed.staticCall(
      tzAddress,
      [reverseEntryKey],
      [true],
      [reverseBridgeKey],
      [true],
      [reverseExitKey],
      [true]
    );

    expect(result.entryIndex).to.equal(0n);
    expect(result.bridgeIndex).to.equal(0n);
    expect(result.exitIndex).to.equal(0n);
    expect(result.profitable).to.equal(false);
    expect(result.netProduct).to.be.lte(10n ** 18n);
    expect(result.netProductDelta).to.equal(BigInt(result.netProduct) - 10n ** 18n);
  });
});
