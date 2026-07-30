const { expect } = require("chai");
const { ethers } = require("hardhat");

async function latestDeadline(seconds = 3600) {
  const block = await ethers.provider.getBlock("latest");
  return BigInt(block.timestamp + seconds);
}

describe("AaveV3LiquidationExecutor", function () {
  this.timeout(120000);

  async function deployFixture() {
    const [owner, other, user] = await ethers.getSigners();
    const Token = await ethers.getContractFactory("TestERC20");
    const debt = await Token.deploy("Mock USDC", "USDC", 6, owner.address);
    const collateral = await Token.deploy("Mock WETH", "WETH", 18, owner.address);
    const Pool = await ethers.getContractFactory("MockAaveLiquidationPool");
    const pool = await Pool.deploy(5);
    const Router = await ethers.getContractFactory("MockSwapRouter");
    const router = await Router.deploy();
    const Executor = await ethers.getContractFactory("AaveV3LiquidationExecutor");
    const executor = await Executor.deploy(
      await pool.getAddress(),
      await router.getAddress(),
      await debt.getAddress(),
      owner.address
    );

    await debt.mint(await pool.getAddress(), ethers.parseUnits("1000000", 6));
    await collateral.mint(await pool.getAddress(), ethers.parseEther("1000"));
    await debt.mint(await router.getAddress(), ethers.parseUnits("1000000", 6));
    await owner.sendTransaction({ to: await executor.getAddress(), value: ethers.parseEther("1.0") });

    await router.setRate(await collateral.getAddress(), await debt.getAddress(), ethers.parseUnits("2200", 6), ethers.parseEther("1"));
    await pool.setLiquidationQuote(
      user.address,
      await collateral.getAddress(),
      await debt.getAddress(),
      ethers.parseEther("1")
    );

    return { owner, other, user, debt, collateral, pool, router, executor };
  }

  it("executes a profitable flash-loan liquidation and leaves profit", async function () {
    const { owner, user, debt, collateral, pool, executor } = await deployFixture();
    const request = {
      user: user.address,
      collateralAsset: await collateral.getAddress(),
      debtAsset: await debt.getAddress(),
      debtToCover: ethers.parseUnits("1000", 6),
      minCollateralSwapOut: ethers.parseUnits("1100", 6),
      minProfitAmount: ethers.parseUnits("50", 6),
      deadline: await latestDeadline(),
      swapPath: [await collateral.getAddress(), await debt.getAddress()],
    };

    await expect(executor.requestLiquidation(request))
      .to.emit(executor, "LiquidationExecuted")
      .and.to.emit(pool, "LiquidationCall");

    const profit = await debt.balanceOf(await executor.getAddress());
    expect(profit).to.equal(ethers.parseUnits("1199.5", 6));

    await executor.withdrawUSDC(owner.address, profit);
    expect(await debt.balanceOf(await executor.getAddress())).to.equal(0n);
  });

  it("allows owner to sweep token profits into USDC and withdraw native balance", async function () {
    const { owner, other, collateral, debt, executor } = await deployFixture();
    const extraCollateral = ethers.parseEther("2");
    await collateral.mint(await executor.getAddress(), extraCollateral);

    await expect(
      executor.sweepAllTokenToUSDC(
        await collateral.getAddress(),
        0,
        [await collateral.getAddress(), await debt.getAddress()]
      )
    ).to.emit(executor, "TokenSweptToUSDC");
    expect(await collateral.balanceOf(await executor.getAddress())).to.equal(0n);
    expect(await debt.balanceOf(await executor.getAddress())).to.be.greaterThan(0n);

    const usdcBalance = await debt.balanceOf(await executor.getAddress());
    await expect(executor.withdrawUSDC(other.address, usdcBalance)).to.emit(executor, "TokenWithdrawn");
    expect(await debt.balanceOf(await executor.getAddress())).to.equal(0n);

    const contractNative = await ethers.provider.getBalance(await executor.getAddress());
    const otherBefore = await ethers.provider.getBalance(other.address);
    await expect(executor.withdrawNative(other.address, contractNative)).to.emit(executor, "NativeWithdrawn");
    const otherAfter = await ethers.provider.getBalance(other.address);
    expect(otherAfter - otherBefore).to.equal(contractNative);
    expect(await ethers.provider.getBalance(await executor.getAddress())).to.equal(0n);
  });

  it("allows USDC sweep as a no-op only when minOut is satisfied", async function () {
    const { debt, executor } = await deployFixture();
    const amount = ethers.parseUnits("10", 6);
    await debt.mint(await executor.getAddress(), amount);

    await expect(executor.sweepTokenToUSDC(await debt.getAddress(), amount, amount, []))
      .to.emit(executor, "TokenSweptToUSDC")
      .withArgs(await debt.getAddress(), amount, amount);

    await expect(
      executor.sweepTokenToUSDC(await debt.getAddress(), amount, amount + 1n, [])
    ).to.be.revertedWithCustomError(executor, "ProfitTooLow");
  });

  it("allows same-asset requests without a swap path through executor validation", async function () {
    const { user, debt, executor, pool } = await deployFixture();
    const request = {
      user: user.address,
      collateralAsset: await debt.getAddress(),
      debtAsset: await debt.getAddress(),
      debtToCover: ethers.parseUnits("1000", 6),
      minCollateralSwapOut: 0,
      minProfitAmount: ethers.parseUnits("50", 6),
      deadline: await latestDeadline(),
      swapPath: [],
    };

    await expect(executor.requestLiquidation(request)).to.be.revertedWithCustomError(pool, "LiquidationNotConfigured");
  });

  it("pauses liquidation entry points and callbacks", async function () {
    const { other, user, debt, collateral, executor } = await deployFixture();
    const request = {
      user: user.address,
      collateralAsset: await collateral.getAddress(),
      debtAsset: await debt.getAddress(),
      debtToCover: ethers.parseUnits("1000", 6),
      minCollateralSwapOut: 0,
      minProfitAmount: 0,
      deadline: await latestDeadline(),
      swapPath: [await collateral.getAddress(), await debt.getAddress()],
    };

    await executor.setPaused(true);
    await expect(executor.requestLiquidation(request)).to.be.revertedWithCustomError(executor, "Paused");

    await expect(
      executor.connect(other).executeOperation(
        await debt.getAddress(),
        ethers.parseUnits("1000", 6),
        0,
        await executor.getAddress(),
        ethers.AbiCoder.defaultAbiCoder().encode(
          [
            "tuple(address user,address collateralAsset,address debtAsset,uint256 debtToCover,uint256 minCollateralSwapOut,uint256 minProfitAmount,uint256 deadline,address[] swapPath)",
          ],
          [request]
        )
      )
    ).to.be.revertedWithCustomError(executor, "Paused");
  });

  it("rejects non-owner callers", async function () {
    const { other, user, debt, collateral, executor } = await deployFixture();
    const request = {
      user: user.address,
      collateralAsset: await collateral.getAddress(),
      debtAsset: await debt.getAddress(),
      debtToCover: ethers.parseUnits("1000", 6),
      minCollateralSwapOut: 0,
      minProfitAmount: 0,
      deadline: await latestDeadline(),
      swapPath: [await collateral.getAddress(), await debt.getAddress()],
    };

    await expect(executor.connect(other).requestLiquidation(request)).to.be.revertedWithCustomError(executor, "NotOwner");
    await expect(executor.connect(other).withdrawUSDC(other.address, 1)).to.be.revertedWithCustomError(executor, "NotOwner");
  });

  it("reverts when profit is below the required minimum", async function () {
    const { user, debt, collateral, executor } = await deployFixture();
    const request = {
      user: user.address,
      collateralAsset: await collateral.getAddress(),
      debtAsset: await debt.getAddress(),
      debtToCover: ethers.parseUnits("1000", 6),
      minCollateralSwapOut: ethers.parseUnits("1100", 6),
      minProfitAmount: ethers.parseUnits("1300", 6),
      deadline: await latestDeadline(),
      swapPath: [await collateral.getAddress(), await debt.getAddress()],
    };

    await expect(executor.requestLiquidation(request)).to.be.revertedWithCustomError(executor, "ProfitTooLow");
  });
});
