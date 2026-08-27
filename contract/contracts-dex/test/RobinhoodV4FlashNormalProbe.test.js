const { expect } = require("chai");
const { ethers } = require("hardhat");

describe("RobinhoodV4FlashNormalProbe", function () {
  it("runs a token-conservative normal V4 flash-accounting cycle", async function () {
    const [owner] = await ethers.getSigners();
    const Token = await ethers.getContractFactory("TestERC20");
    const token = await Token.deploy("Test USDG", "tUSDG", 6, owner.address);
    const PoolManager = await ethers.getContractFactory("MockV4PoolManager");
    const poolManager = await PoolManager.deploy();
    const Probe = await ethers.getContractFactory("RobinhoodV4FlashNormalProbe");
    const probe = await Probe.deploy(await poolManager.getAddress());
    const amount = 1_000n;

    await token.mint(await poolManager.getAddress(), amount);

    const tokenAddress = await token.getAddress();
    await expect(probe.runNormalFlash(tokenAddress, amount))
      .to.emit(probe, "NormalV4FlashBorrowed")
      .withArgs(tokenAddress, amount, amount)
      .and.to.emit(probe, "NormalV4FlashSettled")
      .withArgs(tokenAddress, amount, amount)
      .and.to.emit(probe, "NormalV4FlashCompleted")
      .withArgs(tokenAddress, amount);

    expect(await token.balanceOf(await poolManager.getAddress())).to.equal(amount);
    expect(await token.balanceOf(await probe.getAddress())).to.equal(0n);
    expect(await poolManager.currencyDebt(await probe.getAddress(), tokenAddress)).to.equal(0n);
  });
});
