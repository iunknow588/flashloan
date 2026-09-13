const path = require("path");
const fs = require("fs");
const { ethers, artifacts } = require("./lib/hardhat");

const MANIFEST_PATH = path.resolve(__dirname, "../config/testnet-v4-amm/manifest.json");

const ERC20_ABI = [
  "function balanceOf(address account) view returns (uint256)",
  "function transfer(address to, uint256 value) returns (bool)",
];

function readArg(name, fallback) {
  const idx = process.argv.indexOf(`--${name}`);
  if (idx >= 0) return process.argv[idx + 1];
  return fallback;
}

function readBoolArg(name, fallback) {
  const value = readArg(name, "");
  if (value) return value === "true" || value === "1" || value === "yes";
  return fallback;
}

function readManifest() {
  return JSON.parse(fs.readFileSync(MANIFEST_PATH, "utf8"));
}

function writeManifest(manifest) {
  fs.writeFileSync(MANIFEST_PATH, JSON.stringify(manifest, null, 2) + "\n", "utf8");
}

function sortPair(a, b) {
  return BigInt(a.toLowerCase()) < BigInt(b.toLowerCase()) ? [a, b] : [b, a];
}

function withinRange(addr) {
  return addr.toLowerCase();
}

async function deployOrGetMaker(owner, poolManagerAddress) {
  const Factory = await ethers.getContractFactory("OfficialV4ImbalanceMaker");
  const artifact = await artifacts.readArtifact("OfficialV4ImbalanceMaker");
  const expectedBytecode = String(artifact.deployedBytecode || "").toLowerCase();
  const manifest = readManifest();
  const existing = manifest.officialV4 && manifest.officialV4.imbalanceMaker || "";
  if (existing) {
    const code = String(await ethers.provider.getCode(existing)).toLowerCase();
    if (code !== "0x" && code === expectedBytecode) {
      return Factory.attach(existing);
    }
  }
  const maker = await Factory.deploy(poolManagerAddress);
  await maker.waitForDeployment();
  manifest.officialV4 = manifest.officialV4 || {};
  manifest.officialV4.imbalanceMaker = await maker.getAddress();
  writeManifest(manifest);
  console.log(`deployed OfficialV4ImbalanceMaker=${await maker.getAddress()}`);
  return maker;
}

async function main() {
  const allowBroadcast = readBoolArg(
    "broadcast",
    process.env.ROBINHOOD_TESTNET_ALLOW_OFFICIAL_V4_MAKE_ARB_BROADCAST === "true"
  );
  const amountIn = ethers.parseUnits(readArg("amount", process.env.ROBINHOOD_TESTNET_MAKE_ARB_AMOUNT || "0.001"), 18);

  const manifest = readManifest();
  if (!manifest.officialV4 || !manifest.officialV4.poolManager) {
    throw new Error("officialV4.poolManager missing; run testnet-v4-official-pool-create-live first");
  }

  const [owner] = await ethers.getSigners();
  const tz = manifest.tz;
  const ta = manifest.ta;
  const tb = manifest.tb;
  const fee = Number(manifest.officialV4.fee);
  const tickSpacing = Number(manifest.officialV4.tickSpacing);
  const hooks = manifest.officialV4.hooks || ethers.ZeroAddress;
  const poolManagerAddress = manifest.officialV4.poolManager;

  const erc = (address) => new ethers.Contract(address, ERC20_ABI, owner);
  const tzToken = erc(tz);

  console.log("official V4 arbitrage opportunity maker");
  console.log(`  poolManager=${poolManagerAddress}`);
  console.log(`  amountIn=${ethers.formatUnits(amountIn, 18)} tz`);

  const key0 = (() => {
    const [c0, c1] = sortPair(tz, ta);
    return { currency0: withinRange(c0), currency1: withinRange(c1), fee, tickSpacing, hooks };
  })();
  const key1 = (() => {
    const [c0, c1] = sortPair(ta, tb);
    return { currency0: withinRange(c0), currency1: withinRange(c1), fee, tickSpacing, hooks };
  })();
  const key2 = (() => {
    const [c0, c1] = sortPair(tb, tz);
    return { currency0: withinRange(c0), currency1: withinRange(c1), fee, tickSpacing, hooks };
  })();

  const keys = [key0, key1, key2];
  const directions = [true, false, false];
  console.log("reverse circular route (TZ -> TA -> TB -> TZ):");
  console.log(`  keys=${JSON.stringify(keys)}`);
  console.log(`  directions=${directions.join(",")}`);

  if (!allowBroadcast) {
    console.log("broadcast disabled; imbalance maker action prepared for review (not broadcast)");
    return;
  }

  const maker = await deployOrGetMaker(owner, poolManagerAddress);
  console.log(`  maker=${maker.target || maker.address}`);

  const makerAddress = maker.target || maker.address;
  const balance = await tzToken.balanceOf(makerAddress);
  if (balance < amountIn) {
    const fundTx = await tzToken.transfer(makerAddress, amountIn - balance);
    await fundTx.wait();
    console.log(`funded maker ${ethers.formatUnits(amountIn - balance, 18)} tz`);
  }

  const tx = await maker.runImbalance(tz, keys, directions, amountIn);
  const receipt = await tx.wait();
  console.log(`imbalance maker tx=${receipt.hash} gasUsed=${receipt.gasUsed}`);

  manifest.officialV4.lastMakeArb = {
    txHash: receipt.hash,
    gasUsed: receipt.gasUsed.toString(),
    runAt: new Date().toISOString(),
    amountIn: amountIn.toString(),
    route: { keys, directions },
  };
  writeManifest(manifest);

  console.log("result: OK. arbitrage opportunity created with no token burn.");
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
