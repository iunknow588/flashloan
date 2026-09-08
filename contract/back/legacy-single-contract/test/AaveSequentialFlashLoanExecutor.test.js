const { expect } = require("chai");
const { ethers } = require("hardhat");

const ONE_USDC = 1_000_000n;

async function deployAaveFixture() {
  const [owner, other] = await ethers.getSigners();

  const TestERC20 = await ethers.getContractFactory("TestERC20");
  const usdc = await TestERC20.deploy("Test USDC", "tUSDC", 6, owner.address);
  const token = await TestERC20.deploy("Test Token", "tTOK", 18, owner.address);

  const MockAavePool = await ethers.getContractFactory("MockAavePool");
  const pool = await MockAavePool.deploy(5); // 5 bps

  const AaveExecutor = await ethers.getContractFactory("AaveSequentialFlashLoanExecutor");
  const executor = await AaveExecutor.deploy(await pool.getAddress(), owner.address);

  const usdcAddress = await usdc.getAddress();
  const tokenAddress = await token.getAddress();
  const poolAddress = await pool.getAddress();
  const executorAddress = await executor.getAddress();

  await usdc.mint(poolAddress, 1_000_000n * ONE_USDC);
  await usdc.mint(executorAddress, 100n * ONE_USDC);
  await token.mint(poolAddress, 1_000_000n * 10n ** 18n);
  await token.mint(executorAddress, 100n * 10n ** 18n);

  return { owner, other, usdc, token, pool, executor, usdcAddress, tokenAddress, poolAddress, executorAddress };
}

async function futureDeadline(seconds = 600) {
  const block = await ethers.provider.getBlock("latest");
  return BigInt(block.timestamp + seconds);
}

function emptyPlan(deadline) {
  return {
    steps: [],
    deadline,
    profitToken: ethers.ZeroAddress,
    minProfitAmount: 0,
  };
}

describe("AaveSequentialFlashLoanExecutor", function () {
  it("requests flashLoanSimple and repays amount plus premium", async function () {
    const ctx = await deployAaveFixture();
    const amount = 1_000n * ONE_USDC;
    const premium = (amount * 5n) / 10000n;
    const deadline = await futureDeadline();
    const plan = emptyPlan(deadline);

    const poolBefore = await ctx.usdc.balanceOf(ctx.poolAddress);
    await expect(ctx.executor.requestFlashLoan(ctx.usdcAddress, amount, plan))
      .to.emit(ctx.executor, "FlashLoanExecuted")
      .withArgs(ctx.usdcAddress, amount, premium);
    const poolAfter = await ctx.usdc.balanceOf(ctx.poolAddress);

    expect(poolAfter - poolBefore).to.equal(premium);
  });

  it("requests pair flashLoanSimple with borrow token and swap token", async function () {
    const ctx = await deployAaveFixture();
    const amount = 1_000n * ONE_USDC;
    const premium = (amount * 5n) / 10000n;
    const deadline = await futureDeadline();
    const plan = emptyPlan(deadline);

    const poolBefore = await ctx.usdc.balanceOf(ctx.poolAddress);
    await expect(ctx.executor.requestPairFlashLoan(ctx.usdcAddress, ctx.tokenAddress, amount, plan))
      .to.emit(ctx.executor, "FlashLoanRequested")
      .withArgs(ctx.usdcAddress, ctx.tokenAddress, amount);
    const poolAfter = await ctx.usdc.balanceOf(ctx.poolAddress);

    expect(poolAfter - poolBefore).to.equal(premium);
  });

  it("requests batch flashLoan with k borrow tokens and k swap tokens", async function () {
    const ctx = await deployAaveFixture();
    const usdcAmount = 1_000n * ONE_USDC;
    const tokenAmount = 10n * 10n ** 18n;
    const usdcPremium = (usdcAmount * 5n) / 10000n;
    const tokenPremium = (tokenAmount * 5n) / 10000n;
    const deadline = await futureDeadline();
    const plan = emptyPlan(deadline);

    const poolUsdcBefore = await ctx.usdc.balanceOf(ctx.poolAddress);
    const poolTokenBefore = await ctx.token.balanceOf(ctx.poolAddress);
    await expect(
      ctx.executor.requestBatchFlashLoan(
        [ctx.usdcAddress, ctx.tokenAddress],
        [ctx.tokenAddress, ctx.usdcAddress],
        [usdcAmount, tokenAmount],
        plan
      )
    ).to.emit(ctx.executor, "BatchFlashLoanRequested").withArgs(2);
    const poolUsdcAfter = await ctx.usdc.balanceOf(ctx.poolAddress);
    const poolTokenAfter = await ctx.token.balanceOf(ctx.poolAddress);

    expect(poolUsdcAfter - poolUsdcBefore).to.equal(usdcPremium);
    expect(poolTokenAfter - poolTokenBefore).to.equal(tokenPremium);
  });

  it("lets the pool revert when repayment is not available", async function () {
    const ctx = await deployAaveFixture();
    const executorBalance = await ctx.usdc.balanceOf(ctx.executorAddress);
    await ctx.executor.withdrawToken(ctx.usdcAddress, ctx.owner.address, executorBalance);
    const amount = 1_000n * ONE_USDC;
    const deadline = await futureDeadline();
    const plan = emptyPlan(deadline);

    await expect(ctx.executor.requestFlashLoan(ctx.usdcAddress, amount, plan))
      .to.be.revertedWithCustomError(ctx.usdc, "InsufficientBalance");
  });

  it("enforces minimum profit after reserving flashloan repayment", async function () {
    const ctx = await deployAaveFixture();
    const amount = 1_000n * ONE_USDC;
    const premium = (amount * 5n) / 10000n;
    const deadline = await futureDeadline();
    const plan = {
      ...emptyPlan(deadline),
      profitToken: ctx.usdcAddress,
      minProfitAmount: 101n * ONE_USDC,
    };

    await expect(ctx.executor.requestFlashLoan(ctx.usdcAddress, amount, plan))
      .to.be.revertedWithCustomError(ctx.executor, "MinProfitNotMet");

    const passPlan = {
      ...emptyPlan(deadline),
      profitToken: ctx.usdcAddress,
      minProfitAmount: 99n * ONE_USDC,
    };
    await expect(ctx.executor.requestFlashLoan(ctx.usdcAddress, amount, passPlan))
      .to.emit(ctx.executor, "FlashLoanExecuted")
      .withArgs(ctx.usdcAddress, amount, premium);
  });

  it("rejects non-owner requests", async function () {
    const ctx = await deployAaveFixture();
    const deadline = await futureDeadline();
    const plan = emptyPlan(deadline);

    await expect(ctx.executor.connect(ctx.other).requestFlashLoan(ctx.usdcAddress, ONE_USDC, plan))
      .to.be.revertedWithCustomError(ctx.executor, "NotOwner");
  });

  it("rejects direct executeOperation calls from non-pool", async function () {
    const ctx = await deployAaveFixture();
    const deadline = await futureDeadline();
    const encodedPlan = ethers.AbiCoder.defaultAbiCoder().encode(
      [
        "tuple(tuple(address router,address tokenIn,address tokenOut,uint256 amountIn,uint256 amountOutMin,address[] path)[] steps,uint256 deadline,address profitToken,uint256 minProfitAmount)",
      ],
      [emptyPlan(deadline)]
    );

    await expect(
      ctx.executor["executeOperation(address,uint256,uint256,address,bytes)"](
        ctx.usdcAddress,
        ONE_USDC,
        0,
        ctx.executorAddress,
        encodedPlan
      )
    )
      .to.be.revertedWithCustomError(ctx.executor, "NotPool");
  });

  it("rejects expired plans before requesting the loan", async function () {
    const ctx = await deployAaveFixture();
    const block = await ethers.provider.getBlock("latest");
    const plan = emptyPlan(BigInt(block.timestamp - 1));

    await expect(ctx.executor.requestFlashLoan(ctx.usdcAddress, ONE_USDC, plan))
      .to.be.revertedWithCustomError(ctx.executor, "InvalidPlan");
  });
});
