const { expect } = require("chai");
const { ethers, network } = require("hardhat");

const maybeDescribe = process.env.AVALANCHE_RPC_URL || process.env.AVALANCHE_RPC ? describe : describe.skip;

maybeDescribe("AaveV3LiquidationExecutor fork", function () {
  it("deploys against configured Avalanche Aave pool and exposes immutable addresses", async function () {
    const [owner] = await ethers.getSigners();
    const pool = process.env.AAVE_POOL_ADDRESS || "0x794a61358D6845594F94dc1db02a252b5b4814aD";
    const router = process.env.DEX_ROUTER_ADDRESS || "0x60aE616a2155Ee3d9A68541Ba4544862310933d4";

    const code = await ethers.provider.getCode(pool);
    expect(code).to.not.equal("0x");

    const Executor = await ethers.getContractFactory("AaveV3LiquidationExecutor");
    const executor = await Executor.deploy(pool, router, owner.address);
    await executor.waitForDeployment();

    expect(await executor.pool()).to.equal(pool);
    expect(await executor.router()).to.equal(router);
    expect(network.name).to.equal("hardhat");
  });
});
