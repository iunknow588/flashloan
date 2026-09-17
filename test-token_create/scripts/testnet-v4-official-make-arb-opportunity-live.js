const path = require("path");
const fs = require("fs");
const { ethers } = require("./lib/hardhat");

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

async function deployOrGetMaker(owner, poolManagerAddress, allowBroadcast) {
  const Factory = await ethers.getContractFactory("OfficialV4ImbalanceMaker");
  const manifest = readManifest();
  const existing = manifest.officialV4 && manifest.officialV4.imbalanceMaker || "";
  if (existing) {
    const code = String(await ethers.provider.getCode(existing)).toLowerCase();
    if (code !== "0x") {
      return Factory.attach(existing);
    }
  }
  if (!allowBroadcast) {
    const known = existing || "none";
    throw new Error(
      `no compatible OfficialV4ImbalanceMaker available for read-only preflight (found=${known}); run once with --broadcast to deploy/upgrade the maker, then use read-only preflight`
    );
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

  const maker = await deployOrGetMaker(owner, poolManagerAddress, allowBroadcast);
  console.log(`  maker=${maker.target || maker.address}`);

  const makerAddress = maker.target || maker.address;
  const balance = await tzToken.balanceOf(makerAddress);
  if (!allowBroadcast && balance < amountIn) {
    throw new Error(
      `preflight cannot simulate because maker balance ${ethers.formatUnits(balance, 18)} TZ is less than requested ${ethers.formatUnits(amountIn, 18)} TZ; run once with --broadcast (or top up the maker contract) before using read-only preflight`
    );
  }
  if (balance < amountIn) {
    const fundTx = await tzToken.transfer(makerAddress, amountIn - balance);
    await fundTx.wait();
    console.log(`funded maker ${ethers.formatUnits(amountIn - balance, 18)} tz`);
  }

  let preflight;
  try {
    preflight = await maker.runImbalance.staticCall(tz, keys, directions, amountIn);
    const preflightSpent = BigInt(preflight.spent);
    console.log(`imbalance preflight OK outB=${ethers.formatUnits(preflight.outB, 18)} outC=${ethers.formatUnits(preflight.outC, 18)} outZ=${ethers.formatUnits(preflight.outZ, 18)} spent=${ethers.formatUnits(preflightSpent, 18)} tz`);
    if (preflightSpent <= 0n) {
      console.error("preflight did not create an imbalance gap: spent=0. The requested amount did not shift the route away from balance; choose a different amount or route.");
      process.exitCode = 1;
      return;
    }
  } catch (error) {
    console.error("imbalance preflight failed; no broadcast was attempted.");
    console.error(`  ${error.shortMessage || error.message || error}`);
    console.error("  The requested amount is too large for current pool depth, or the route cannot be executed.");
    const fallbackSizes = ["50", "10", "5", "1", "0.1", "0.01", "0.001", "0.0001"];
    let largestFeasible = null;
    for (const sizeText of fallbackSizes) {
      const candidate = ethers.parseUnits(sizeText, 18);
      if (candidate >= amountIn) continue;
      try {
        const probe = await maker.runImbalance.staticCall(tz, keys, directions, candidate);
        if (BigInt(probe.spent) > 0n) {
          largestFeasible = sizeText;
          break;
        }
      } catch (_) {
        // Try smaller size next.
      }
    }
    if (largestFeasible) {
      console.error(`  Suggested feasible amount that passes preflight and creates a gap: ${largestFeasible} TZ. Re-run with --amount ${largestFeasible}.`);
    } else {
      console.error("  Even 0.0001 TZ cannot pass preflight right now. Add liquidity to the triangle pools first, then retry this operation.");
    }
    process.exitCode = 1;
    return;
  }

  if (!allowBroadcast) {
    console.log("imbalance preflight OK; broadcast disabled, no on-chain transaction was sent.");
    return;
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
