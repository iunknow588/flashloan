const { expect } = require("chai");
const { ethers } = require("hardhat");

async function latestDeadline(seconds = 3600) {
  const block = await ethers.provider.getBlock("latest");
  return BigInt(block.timestamp + seconds);
}

describe("AaveV3LiquidationExecutor", function () {
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
    const executor = await Executor.deploy(await pool.getAddress(), await router.getAddress(), owner.address);

    await debt.mint(await pool.getAddress(), ethers.parseUnits("1000000", 6));
    await collateral.mint(await pool.getAddress(), ethers.parseEther("1000"));
    await debt.mint(await router.getAddress(), ethers.parseUnits("1000000", 6));

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

    await executor.withdrawToken(await debt.getAddress(), owner.address, profit);
    expect(await debt.balanceOf(await executor.getAddress())).to.equal(0n);
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
