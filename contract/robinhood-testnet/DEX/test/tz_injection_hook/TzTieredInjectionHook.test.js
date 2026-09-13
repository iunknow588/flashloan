const { expect } = require("chai");
const { ethers } = require("hardhat");

const DECIMALS = 18n;
const MAX_UINT160 = (1n << 160n) - 1n;
const MASK_128 = (1n << 128n) - 1n;
const HOOK_MASK = (1n << 14n) - 1n;
const AFTER_SWAP_FLAG = 1n << 6n;

function toToken(value) {
  return BigInt(value) * 10n ** DECIMALS;
}

function packDelta(amount0, amount1) {
  const upper = amount0 & MASK_128;
  const lower = amount1 & MASK_128;
  let packed = (upper << 128n) | lower;
  if (packed & (1n << 255n)) packed -= 1n << 256n;
  return packed;
}

describe("TzTieredInjectionHook", function () {
  it("records swapped TZ for delayed replenishment without spending hook balance", async function () {
    const [owner] = await ethers.getSigners();
    const Token = await ethers.getContractFactory("TestERC20");
    const tz = await Token.deploy("Test Z", "tz", 18, owner.address);
    await tz.mint(owner.address, toToken(1_000_000));

    const Hook = await ethers.getContractFactory("TzTieredInjectionHook");
    const hook = await Hook.deploy(await tz.getAddress(), owner.address, owner.address);
    await hook.waitForDeployment();
    await hook.setInjectTarget(owner.address);
    await tz.approve(await hook.getAddress(), ethers.MaxUint256);
    await hook.fundTz(toToken(1_000));

    const permissions = await hook.getHookPermissions();
    expect(permissions.afterSwap).to.equal(true);
    expect(permissions.beforeSwap).to.equal(false);

    const key = [await tz.getAddress(), owner.address, 3000, 60, await hook.getAddress()];
    const params = [true, -toToken(120), MAX_UINT160];
    const result = await hook.afterSwap.staticCall(owner.address, key, params, packDelta(-toToken(120), 0n), "0x");
    const selector = result[0];
    expect(selector).to.be.a("string");
    expect(selector.length).to.be.greaterThan(2);
    expect(result[1]).to.equal(0n);

    await hook.afterSwap(owner.address, key, params, packDelta(-toToken(120), 0n), "0x");

    expect(await hook.lastSwappedAmount()).to.equal(toToken(120));
    expect(await hook.pendingReplenishAmount()).to.equal(toToken(120));
    expect(await hook.totalObservedSwappedAmount()).to.equal(toToken(120));
    expect(await tz.balanceOf(owner.address)).to.equal(toToken(1_000_000) - toToken(1_000));

    await hook.afterSwap(owner.address, key, params, packDelta(-toToken(50), 0n), "0x");

    expect(await hook.lastSwappedAmount()).to.equal(toToken(50));
    expect(await hook.pendingReplenishAmount()).to.equal(toToken(170));
    expect(await hook.totalObservedSwappedAmount()).to.equal(toToken(170));
    expect(await tz.balanceOf(owner.address)).to.equal(toToken(1_000_000) - toToken(1_000));
  });

  it("accumulates pending replenishment across afterSwap callbacks", async function () {
    const [owner] = await ethers.getSigners();
    const Token = await ethers.getContractFactory("TestERC20");
    const tz = await Token.deploy("Test Z", "tz", 18, owner.address);
    await tz.mint(owner.address, toToken(1_000_000));

    const Hook = await ethers.getContractFactory("TzTieredInjectionHook");
    const hook = await Hook.deploy(await tz.getAddress(), owner.address, owner.address);
    await hook.waitForDeployment();
    await hook.setInjectTarget(owner.address);
    await tz.approve(await hook.getAddress(), ethers.MaxUint256);
    await hook.fundTz(toToken(1_000));

    const key = [await tz.getAddress(), owner.address, 3000, 60, await hook.getAddress()];
    const params = [true, -toToken(100), MAX_UINT160];
    await hook.afterSwap(owner.address, key, params, packDelta(-toToken(100), 0n), "0x");

    expect(await hook.pendingReplenishAmount()).to.equal(toToken(100));
    expect(await hook.totalObservedSwappedAmount()).to.equal(toToken(100));
  });

  it("can be deployed via Create2Deployer with only afterSwap flag set", async function () {
    const [owner] = await ethers.getSigners();
    const Token = await ethers.getContractFactory("TestERC20");
    const tz = await Token.deploy("Test Z", "tz", 18, owner.address);
    await tz.mint(owner.address, toToken(1_000_000));

    const Hook = await ethers.getContractFactory("TzTieredInjectionHook");
    const Create2Deployer = await ethers.getContractFactory("Create2Deployer");
    const factory = await Create2Deployer.deploy();
    await factory.waitForDeployment();
    const factoryAddress = await factory.getAddress();

    const initCode = (await Hook.getDeployTransaction(await tz.getAddress(), owner.address, owner.address)).data;
    const initCodeHash = ethers.keccak256(initCode);
    let salt = "0x";
    let predicted = "0x";
    for (let i = 0; i < 100000; i++) {
      salt = ethers.zeroPadValue(ethers.toBeHex(BigInt(i)), 32);
      predicted = ethers.getCreate2Address(factoryAddress, salt, initCodeHash);
      if ((BigInt(predicted) & HOOK_MASK) === AFTER_SWAP_FLAG) break;
    }
    expect(BigInt(predicted) & HOOK_MASK, "failed to find afterSwap-only salt").to.equal(AFTER_SWAP_FLAG);

    const tx = await factory.deploy(initCode, salt);
    const receipt = await tx.wait();
    expect(receipt.status).to.equal(1);
    expect(await ethers.provider.getCode(predicted)).to.not.equal("0x");

    const hook = await Hook.attach(predicted);
    const permissions = await hook.getHookPermissions();
    expect(permissions.afterSwap).to.equal(true);
    expect(permissions.beforeSwap).to.equal(false);
  });
});
