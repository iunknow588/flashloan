const path = require("path");
const fs = require("fs");
const { ethers } = require("./hardhat");

const DECIMALS = 18n;
const TOKEN_TOTAL = 10_000_000n * 10n ** DECIMALS;
const MILLION = 1_000_000n * 10n ** DECIMALS;
const MANIFEST_PATH = process.env.FLASHLOAN_MANIFEST_PATH
  ? path.resolve(process.env.FLASHLOAN_MANIFEST_PATH)
  : path.resolve(__dirname, "../../config/local-v4-amm/manifest.json");

function toToken(value) {
  return BigInt(value) * 10n ** DECIMALS;
}

async function readManifest() {
  if (!fs.existsSync(MANIFEST_PATH)) return null;
  return JSON.parse(fs.readFileSync(MANIFEST_PATH, "utf8"));
}

async function writeManifest(manifest) {
  fs.mkdirSync(path.dirname(MANIFEST_PATH), { recursive: true });
  fs.writeFileSync(MANIFEST_PATH, JSON.stringify(manifest, null, 2) + "\n", "utf8");
}

async function deployOrGetContract(contractName, address, owner) {
  if (address) {
    const factory = await ethers.getContractFactory(contractName);
    return factory.attach(address);
  }
  const factory = await ethers.getContractFactory(contractName);
  const contract = await factory.connect(owner).deploy();
  await contract.waitForDeployment();
  return contract;
}

async function deployTokens(owner) {
  const Token = await ethers.getContractFactory("TestERC20");
  const tz = await Token.deploy("Test Z", "tz", 18, owner.address);
  const ta = await Token.deploy("Test A", "ta", 18, owner.address);
  const tb = await Token.deploy("Test B", "tb", 18, owner.address);
  await tz.waitForDeployment();
  await ta.waitForDeployment();
  await tb.waitForDeployment();
  await tz.mint(owner.address, TOKEN_TOTAL);
  await ta.mint(owner.address, TOKEN_TOTAL);
  await tb.mint(owner.address, TOKEN_TOTAL);
  return { tz, ta, tb };
}

async function ensurePool(amm, tokenA, tokenB) {
  try {
    await amm.getReserves(await tokenA.getAddress(), await tokenB.getAddress());
  } catch {
    await amm.createPool(await tokenA.getAddress(), await tokenB.getAddress());
  }
}

async function injectInitialLiquidity(owner, tz, ta, tb, amm) {
  const ammAddress = await amm.getAddress();
  await tz.connect(owner).approve(ammAddress, ethers.MaxUint256);
  await ta.connect(owner).approve(ammAddress, ethers.MaxUint256);
  await tb.connect(owner).approve(ammAddress, ethers.MaxUint256);

  await ensurePool(amm, tz, ta);
  await ensurePool(amm, ta, tb);
  await ensurePool(amm, tb, tz);

  await ensureOwnerHasEnough(owner, tz, MILLION);
  await ensureOwnerHasEnough(owner, ta, toToken(100_000));
  await amm.addLiquidity(await tz.getAddress(), await ta.getAddress(), MILLION, toToken(100_000));

  await ensureOwnerHasEnough(owner, ta, toToken(100_000));
  await ensureOwnerHasEnough(owner, tb, MILLION);
  await amm.addLiquidity(await ta.getAddress(), await tb.getAddress(), toToken(100_000), MILLION);

  await ensureOwnerHasEnough(owner, tb, MILLION);
  await ensureOwnerHasEnough(owner, tz, toToken(100_000));
  await amm.addLiquidity(await tb.getAddress(), await tz.getAddress(), MILLION, toToken(100_000));
}

async function injectArbitrageOpportunityLiquidity(owner, tz, ta, tb, amm) {
  const ammAddress = await amm.getAddress();
  await tz.connect(owner).approve(ammAddress, ethers.MaxUint256);
  await ta.connect(owner).approve(ammAddress, ethers.MaxUint256);
  await tb.connect(owner).approve(ammAddress, ethers.MaxUint256);

  await ensurePool(amm, tz, ta);
  await ensurePool(amm, ta, tb);
  await ensurePool(amm, tb, tz);

  const tzTaA = toToken(100_000);
  const tzTaB = toToken(10_000);
  const taTbA = toToken(10_000);
  const taTbB = toToken(100_000);
  const tbTzA = toToken(100_000);
  const tbTzB = toToken(10_000);

  const requiredTz = tzTaA + tbTzB;
  const requiredTa = tzTaB + taTbA;
  const requiredTb = taTbB + tbTzA;

  for (const [token, amount] of [
    [tz, requiredTz],
    [ta, requiredTa],
    [tb, requiredTb],
  ]) {
    await ensureOwnerHasEnough(owner, token, amount);
  }

  await amm.addLiquidity(await tz.getAddress(), await ta.getAddress(), tzTaA, tzTaB);
  await amm.addLiquidity(await ta.getAddress(), await tb.getAddress(), taTbA, taTbB);
  await amm.addLiquidity(await tb.getAddress(), await tz.getAddress(), tbTzA, tbTzB);

  return {
    tzTa: { a: tzTaA, b: tzTaB },
    taTb: { a: taTbA, b: taTbB },
    tbTz: { a: tbTzA, b: tbTzB },
  };
}

async function ownerHasEnough(owner, token, amount) {
  const balance = await token.balanceOf(owner.address);
  return balance >= amount;
}

async function ensureOwnerHasEnough(owner, token, amount) {
  const balance = await token.balanceOf(owner.address);
  if (balance >= amount) return;
  const totalSupply = await token.totalSupply();
  const symbol = await token.symbol();
  throw new Error(
    `${symbol} owner balance ${ethers.formatUnits(balance, 18)} is below required ${ethers.formatUnits(amount, 18)} (total ${ethers.formatUnits(totalSupply, 18)}); the 80% creation reserve has been spent and should be replenished before more liquidity injection`
  );
}

async function triangularMetric(amm, tz, ta, tb) {
  const tzAddress = await tz.getAddress();
  const taAddress = await ta.getAddress();
  const tbAddress = await tb.getAddress();
  const leg1 = await amm.getReserves(tzAddress, tbAddress);
  const leg2 = await amm.getReserves(taAddress, tbAddress);
  const leg3 = await amm.getReserves(taAddress, tzAddress);
  return (
    (Number(ethers.formatUnits(leg1[1], 18)) / Number(ethers.formatUnits(leg1[0], 18))) *
    (Number(ethers.formatUnits(leg2[0], 18)) / Number(ethers.formatUnits(leg2[1], 18))) *
    (Number(ethers.formatUnits(leg3[1], 18)) / Number(ethers.formatUnits(leg3[0], 18)))
  );
}

module.exports = {
  DECIMALS,
  TOKEN_TOTAL,
  MILLION,
  MANIFEST_PATH,
  toToken,
  readManifest,
  writeManifest,
  deployOrGetContract,
  deployTokens,
  ensurePool,
  injectInitialLiquidity,
  injectArbitrageOpportunityLiquidity,
  ownerHasEnough,
  ensureOwnerHasEnough,
  triangularMetric,
};
