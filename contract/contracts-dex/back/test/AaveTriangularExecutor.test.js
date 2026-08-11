const { expect } = require("chai");
const { ethers } = require("hardhat");

async function deployFixture() {
  const [owner, other] = await ethers.getSigners();

  const Token = await ethers.getContractFactory("TestERC20");
  const usdc = await Token.deploy("USD Coin", "USDC", 6, owner.address);

  const AavePool = await ethers.getContractFactory("MockAavePool");
  const pool = await AavePool.deploy(5n);

  const Executor = await ethers.getContractFactory("AaveTriangularExecutor");
  const executor = await Executor.deploy(await pool.getAddress(), await usdc.getAddress(), owner.address);

  return {
    owner,
    other,
    usdc,
    pool,
    executor,
  };
}

describe("AaveTriangularExecutor withdrawToken", function () {
  it("withdraws token balance to the owner", async function () {
    const ctx = await deployFixture();
    const amount = 12_345n;
    const executorAddress = await ctx.executor.getAddress();

    await ctx.usdc.mint(executorAddress, amount);

    await expect(ctx.executor.withdrawToken(await ctx.usdc.getAddress(), ctx.owner.address, amount))
      .to.emit(ctx.executor, "TokenWithdrawn")
      .withArgs(await ctx.usdc.getAddress(), ctx.owner.address, amount);

    expect(await ctx.usdc.balanceOf(executorAddress)).to.equal(0n);
    expect(await ctx.usdc.balanceOf(ctx.owner.address)).to.equal(amount);
  });

  it("rejects withdrawToken calls from non-owners", async function () {
    const ctx = await deployFixture();
    const amount = 1_000n;
    const executorAddress = await ctx.executor.getAddress();

    await ctx.usdc.mint(executorAddress, amount);

    await expect(ctx.executor.connect(ctx.other).withdrawToken(await ctx.usdc.getAddress(), ctx.other.address, amount))
      .to.be.revertedWithCustomError(ctx.executor, "NotOwner");
  });

  it("lets the owner toggle automatic profit sweeping", async function () {
    const ctx = await deployFixture();

    expect(await ctx.executor.profitSweepEnabled()).to.equal(true);
    expect(await ctx.executor.profitReserveUsdc()).to.equal(0n);

    await expect(ctx.executor.setProfitSweepEnabled(false))
      .to.emit(ctx.executor, "ProfitSweepEnabledSet")
      .withArgs(true, false);
    expect(await ctx.executor.profitSweepEnabled()).to.equal(false);

    await expect(ctx.executor.connect(ctx.other).setProfitSweepEnabled(true))
      .to.be.revertedWithCustomError(ctx.executor, "NotOwner");
  });

  it("lets the owner configure the retained USDC reserve", async function () {
    const ctx = await deployFixture();

    await expect(ctx.executor.setProfitReserveUsdc(100_000_000n))
      .to.emit(ctx.executor, "ProfitReserveSet")
      .withArgs(0n, 100_000_000n);
    expect(await ctx.executor.profitReserveUsdc()).to.equal(100_000_000n);

    await expect(ctx.executor.connect(ctx.other).setProfitReserveUsdc(0n))
      .to.be.revertedWithCustomError(ctx.executor, "NotOwner");
  });
});
