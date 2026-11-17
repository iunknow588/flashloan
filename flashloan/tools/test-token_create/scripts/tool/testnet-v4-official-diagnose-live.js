const path = require("path");
const fs = require("fs");
const { ethers, network } = require("../lib/hardhat");
const poolKeyHelper = require("./v4-official-pool-keys");

const MANIFEST_PATH = process.env.FLASHLOAN_MANIFEST_PATH
  ? path.resolve(process.env.FLASHLOAN_MANIFEST_PATH)
  : path.resolve(__dirname, "../../config/testnet-v4-amm/manifest.json");

const ERC20_ABI = [
  "function balanceOf(address account) view returns (uint256)",
  "function totalSupply() view returns (uint256)",
];

function readArg(name, fallback) {
  const idx = process.argv.indexOf(`--${name}`);
  if (idx >= 0) return process.argv[idx + 1];
  return fallback;
}

function expandNumericString(value) {
  const text = String(value).trim();
  const match = /^([+-]?)(\d+)(?:\.(\d+))?[eE]([+-]?\d+)$/.exec(text);
  if (!match) return text;
  const [, sign, intPart, fracPartRaw, exponentText] = match;
  const fracPart = fracPartRaw || "";
  const exponent = Number(exponentText);
  const digits = intPart + fracPart;
  const decimalPos = intPart.length + exponent;
  let expanded;
  if (decimalPos <= 0) {
    expanded = "0." + "0".repeat(-decimalPos) + digits;
  } else if (decimalPos >= digits.length) {
    expanded = digits + "0".repeat(decimalPos - digits.length);
  } else {
    expanded = digits.slice(0, decimalPos) + "." + digits.slice(decimalPos);
  }
  return sign + expanded;
}

function readManifest() {
  return JSON.parse(fs.readFileSync(MANIFEST_PATH, "utf8"));
}

function parseRouteSymbols(value) {
  return String(value || "")
    .toUpperCase()
    .split(/\s*->\s*|\s*,\s*|[\s;]+/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function parseRouteAddresses(value) {
  return String(value || "")
    .trim()
    .split(/\s*,\s*|\s*->\s*|\s+/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function addressLabels(manifest) {
  const labels = {};
  for (const [label, address] of Object.entries(manifest || {})) {
    if (typeof address === "string" && ethers.isAddress(address)) {
      labels[address.toLowerCase()] = label.toUpperCase();
    }
  }
  return labels;
}

function assertExpectedChain(expectedChainId) {
  const actual = Number(network.config?.chainId || network.name || 0);
  if (actual !== Number(expectedChainId)) {
    throw new Error(
      `network guard failed: current chainId=${actual}, expected=${expectedChainId}; ` +
      "run under the correct hardhat --network"
    );
  }
}

function routeAddressesFromImbalanceRoute(route) {
  if (!route || !Array.isArray(route.keys) || route.keys.length !== 3 || !Array.isArray(route.directions) || route.directions.length !== 3) {
    return null;
  }
  const addresses = [];
  const firstKey = route.keys[0];
  addresses.push(route.directions[0] ? firstKey.currency0 : firstKey.currency1);
  for (const [index, key] of route.keys.entries()) {
    const input = addresses[addresses.length - 1].toLowerCase();
    const zeroForOne = route.directions[index] === true;
    const expected = zeroForOne ? key.currency0.toLowerCase() : key.currency1.toLowerCase();
    if (input !== expected) return null;
    addresses.push(zeroForOne ? key.currency1 : key.currency0);
  }
  if (addresses.length !== 4 || addresses[0].toLowerCase() !== addresses[3].toLowerCase()) {
    return null;
  }
  return addresses;
}

function sortPair(a, b) {
  return BigInt(a.toLowerCase()) < BigInt(b.toLowerCase()) ? [a, b] : [b, a];
}

function makeKey(a, b, fee, tickSpacing, hooks) {
  const [currency0, currency1] = sortPair(a, b);
  return { currency0, currency1, fee, tickSpacing, hooks };
}

function zeroForOne(key, input) {
  return input.toLowerCase() === key.currency0.toLowerCase();
}

function poolId(key) {
  return ethers.keccak256(
    ethers.AbiCoder.defaultAbiCoder().encode(
      ["tuple(address currency0,address currency1,uint24 fee,int24 tickSpacing,address hooks)"],
      [key]
    )
  );
}

function symbolForAddress(address, labels) {
  return labels[String(address).toLowerCase()] || address;
}

async function printKnownTokensAndPools(owner, manifest, keys, routeAddresses, labels) {
  const poolManagerAddress = manifest.officialV4.poolManager;
  console.log("known manifest tokens:");
  const uniqueRouteAddresses = Array.from(
    new Set(routeAddresses.map((address) => address.toLowerCase()))
  ).map((address) => routeAddresses.find((item) => item.toLowerCase() === address));
  for (const address of uniqueRouteAddresses) {
    const label = symbolForAddress(address, labels);
    const token = new ethers.Contract(address, ERC20_ABI, owner);
    let totalSupply = "read-error";
    let poolManagerBalance = "read-error";
    try {
      totalSupply = ethers.formatUnits(await token.totalSupply(), 18);
    } catch (error) {
      console.log(`  ${label} address=${address} total=read-error (${error.shortMessage || error.message || error})`);
      continue;
    }
    try {
      poolManagerBalance = ethers.formatUnits(await token.balanceOf(poolManagerAddress), 18);
    } catch (error) {
      console.log(`  ${label} address=${address} total=${totalSupply} poolManager=read-error (${error.shortMessage || error.message || error})`);
      continue;
    }
    console.log(`  ${label} address=${address} total=${totalSupply} poolManager=${poolManagerBalance}`);
  }

  console.log("known manifest pools:");
  for (const [index, key] of keys.entries()) {
    const c0 = symbolForAddress(key.currency0, labels);
    const c1 = symbolForAddress(key.currency1, labels);
    console.log(`  pool ${index + 1} ${c0}:${c1} fee=${key.fee} tickSpacing=${key.tickSpacing} hooks=${key.hooks} id=${poolId(key)}`);
  }
}

async function printPoolDepth(owner, manifest, routeAddresses, keys, directions, labels) {
  const poolManagerAddress = manifest.officialV4.poolManager;
  const tokenZ = routeAddresses[0];
  const tokenLabel = symbolForAddress(tokenZ, labels);
  const uniqueRouteAddresses = Array.from(
    new Set(routeAddresses.map((address) => address.toLowerCase()))
  ).map((address) => routeAddresses.find((item) => item.toLowerCase() === address));
  for (const address of uniqueRouteAddresses) {
    const label = symbolForAddress(address, labels);
    const token = new ethers.Contract(address, ERC20_ABI, owner);
    const poolManagerBalance = await token.balanceOf(poolManagerAddress);
    const totalSupply = await token.totalSupply();
    const pct = totalSupply === 0n ? 0 : (Number(poolManagerBalance) / Number(totalSupply)) * 100;
    console.log(`${label} PoolManager balance=${ethers.formatUnits(poolManagerBalance, 18)} (${pct.toFixed(2)}% of total)`);
  }

  const makerAddress = manifest.officialV4 && manifest.officialV4.imbalanceMaker;
  if (!makerAddress) {
    console.log("OfficialV4ImbalanceMaker not deployed; run testnet-v4-official-make-arb-opportunity-live first");
    return;
  }
  const maker = await ethers.getContractAt("OfficialV4ImbalanceMaker", makerAddress);
  const depthSizes = process.env.ROBINHOOD_TESTNET_DIAGNOSE_DEPTH_SIZES || "0.001,1,100";
  console.log("imbalance maker staticCall depth probe (read-only):");
  for (const sizeText of depthSizes.split(",")) {
    const size = sizeText.trim();
    if (!size) continue;
    const amountIn = ethers.parseUnits(size, 18);
    try {
      await maker.runImbalance.staticCall(tokenZ, keys, directions, amountIn);
      console.log(`  ${size} ${tokenLabel}: staticCall OK`);
    } catch (error) {
      console.log(`  ${size} ${tokenLabel}: staticCall reverted`);
      console.log(`    ${error.shortMessage || error.message || error}`);
    }
  }
}

async function printTriangularBalance(owner, manifest, routeGroups) {
  const arbAddress = manifest.officialV4 && manifest.officialV4.arb;
  if (!arbAddress) {
    console.log("OfficialV4FlashArb not deployed; run testnet-v4-official-arb-live first");
    return;
  }
  const tokenZ = routeGroups[0].tokenZ;
  const routeSymbols = routeGroups[0].routeSymbols;
  const arb = await ethers.getContractAt("OfficialV4FlashArb", arbAddress);
  const searchStart = ethers.parseUnits(
    expandNumericString(process.env.ROBINHOOD_TESTNET_DIAGNOSE_AMOUNT || "1"),
    18
  );
  const minProfit = BigInt(process.env.ROBINHOOD_TESTNET_DIAGNOSE_MIN_PROFIT || "1");
  const probe = async (amount, keysets, directionsets) => {
    try {
      const preview = await arb.previewArbitrageBestAmount.staticCall(
        tokenZ,
        keysets,
        directionsets,
        amount,
        minProfit,
        true
      );
      const profit = BigInt(preview.selectedProfit ?? preview[2] ?? 0n);
      return {
        ok: profit > 0n,
        amount,
        routeIndex: Number(preview.selectedRouteIndex ?? preview[1] ?? 0n),
        profit,
        attempts: Number(preview.attemptedCount ?? preview[3] ?? 1n),
      };
    } catch (error) {
      return {
        ok: false,
        amount,
        routeIndex: 0,
        profit: 0n,
        attempts: 1,
        error: error.shortMessage || error.message || String(error),
      };
    }
  };
  const doubleBigInt = (value) => {
    const max = (1n << 255n) - 1n;
    return value > max ? 0n : value * 2n;
  };
  const MAX_STEPS = 24;

  const adaptiveBestAmount = async (keysets, directionsets) => {
    let selectedAmountLocal = 0n;
    let selectedRouteLocal = 0;
    let selectedProfitLocal = 0n;
    let attemptedCount = 0;
    let revertCount = 0;
    const probeCandidates = [];
    let baseAmount = 0n;
    let lowerAmount = 0n;
    let upperAmount = 0n;
    let hasUpperAmount = false;

    const probeOnce = async (amount) => {
      if (amount <= 0n) return { ok: false, amount: 0n, routeIndex: 0, profit: 0n, attempts: 0 };
      const result = await probe(amount, keysets, directionsets);
      probeCandidates.push(ethers.formatUnits(amount, 18));
      if (!result.ok) {
        revertCount += 1;
      }
      return result;
    };

    const initialProbe = await probeOnce(searchStart);
    attemptedCount += initialProbe.attempts;
    if (initialProbe.ok) {
      selectedAmountLocal = searchStart;
      selectedRouteLocal = initialProbe.routeIndex;
      selectedProfitLocal = initialProbe.profit;
      baseAmount = searchStart;
      lowerAmount = searchStart / 2n;
      let candidate = doubleBigInt(baseAmount);
      for (let step = 0; step < MAX_STEPS && candidate > 0n; step += 1) {
        const result = await probeOnce(candidate);
        attemptedCount += result.attempts;
        if (result.ok && result.profit > selectedProfitLocal) {
          selectedAmountLocal = candidate;
          selectedRouteLocal = result.routeIndex;
          selectedProfitLocal = result.profit;
          baseAmount = candidate;
          lowerAmount = candidate / 2n;
        } else {
          upperAmount = candidate;
          hasUpperAmount = true;
          break;
        }
        candidate = doubleBigInt(candidate);
      }
      if (!hasUpperAmount && selectedAmountLocal > 0n) {
        upperAmount = doubleBigInt(selectedAmountLocal);
        baseAmount = selectedAmountLocal;
        lowerAmount = selectedAmountLocal / 2n;
        hasUpperAmount = upperAmount > 0n;
      }
    } else {
      upperAmount = searchStart;
      let candidate = searchStart / 2n;
      for (let step = 0; step < MAX_STEPS && candidate > 0n; step += 1) {
        const result = await probeOnce(candidate);
        attemptedCount += result.attempts;
        if (result.ok) {
          selectedAmountLocal = candidate;
          selectedRouteLocal = result.routeIndex;
          selectedProfitLocal = result.profit;
          baseAmount = candidate;
          lowerAmount = candidate / 2n;
          hasUpperAmount = true;
          break;
        }
        upperAmount = candidate;
        candidate /= 2n;
      }
    }

    if (hasUpperAmount && selectedAmountLocal > 0n) {
      for (let step = 0; step < MAX_STEPS; step += 1) {
        if (upperAmount <= baseAmount + 1n || lowerAmount >= baseAmount) break;
        if (upperAmount <= lowerAmount) break;
        const leftMid = lowerAmount + (baseAmount - lowerAmount) / 2n;
        const rightMid = baseAmount + (upperAmount - baseAmount) / 2n;
        if (leftMid <= lowerAmount || leftMid >= baseAmount || rightMid <= baseAmount || rightMid >= upperAmount) break;

        const left = await probeOnce(leftMid);
        const right = await probeOnce(rightMid);
        attemptedCount += left.attempts + right.attempts;
        if (!left.ok && !right.ok) break;
        const leftImproves = left.ok && left.profit > selectedProfitLocal;
        const rightImproves = right.ok && right.profit > selectedProfitLocal;
        if (!leftImproves && !rightImproves) break;

        if (left.ok && right.ok) {
          if (left.profit >= right.profit) {
            if (leftImproves) {
              selectedAmountLocal = leftMid;
              selectedRouteLocal = left.routeIndex;
              selectedProfitLocal = left.profit;
            }
          } else if (rightImproves) {
            selectedAmountLocal = rightMid;
            selectedRouteLocal = right.routeIndex;
            selectedProfitLocal = right.profit;
          }
        } else if (left.ok && leftImproves) {
          selectedAmountLocal = leftMid;
          selectedRouteLocal = left.routeIndex;
          selectedProfitLocal = left.profit;
        } else if (right.ok && rightImproves) {
          selectedAmountLocal = rightMid;
          selectedRouteLocal = right.routeIndex;
          selectedProfitLocal = right.profit;
        }

        const oldBase = baseAmount;
        if (left.ok && right.ok) {
          if (left.profit >= right.profit) {
            upperAmount = oldBase;
            baseAmount = leftMid;
          } else {
            lowerAmount = oldBase;
            baseAmount = rightMid;
          }
        } else if (left.ok) {
          upperAmount = oldBase;
          baseAmount = leftMid;
        } else {
          lowerAmount = oldBase;
          baseAmount = rightMid;
        }
      }
    }
    const best = selectedAmountLocal > 0n
      ? { ok: true, amount: selectedAmountLocal, routeIndex: selectedRouteLocal, profit: selectedProfitLocal, attempts: attemptedCount }
      : { ok: false, amount: 0n, routeIndex: 0, profit: 0n, attempts: attemptedCount };
    return { best, probeCandidates, revertCount };
  };

  console.log(`adaptive best amount staticCall origin=${ethers.formatUnits(searchStart, 18)} ${routeSymbols[0]}, minProfit=${minProfit}`);
  console.log(`  groups=${routeGroups.length} ${routeGroups.map(group => group.routeSymbols.join(" -> ")).join(" | ")}`);
  const perDirection = [];
  for (const group of routeGroups) {
    const result = await adaptiveBestAmount([group.keys], [group.directions]);
    perDirection.push({ group, ...result });
    const symbolText = group.routeSymbols.join(" -> ");
    console.log(`  [${symbolText}] probes=${result.probeCandidates.length} reverts=${result.revertCount} attempts=${result.best.attempts}`);
    if (result.best.ok) {
      console.log(`    bestAmount=${ethers.formatUnits(result.best.amount, 18)} ${routeSymbols[0]} profit=${ethers.formatUnits(result.best.profit, 18)} ${routeSymbols[0]}`);
    } else {
      console.log("    verdict: no executable static profit across the adaptive amount ladder");
    }
  }

  const profitable = perDirection.filter((entry) => entry.best.ok);
  if (profitable.length === 0) {
    console.log("verdict: no direction currently profitable in staticCall");
    return;
  }
  for (const entry of profitable) {
    try {
      const result = await arb.runArbitrage.staticCall(
        tokenZ,
        entry.group.keys,
        entry.group.directions,
        entry.best.amount,
        minProfit
      );
      const profit = result[3];
      console.log(`  verified ${entry.group.routeSymbols.join(" -> ")}: outMid1=${ethers.formatUnits(result[0], 18)} outMid2=${ethers.formatUnits(result[1], 18)} out${routeSymbols[0]}=${ethers.formatUnits(result[2], 18)} profit=${ethers.formatUnits(profit, 18)} ${routeSymbols[0]}`);
      console.log(profit > 0n ? "verdict: route is currently imbalanced / profitable in staticCall" : "verdict: route is currently balanced / no static profit");
    } catch (error) {
      console.log("staticCall reverted after best amount preview; depth may have changed between probes");
      console.log(`  ${error.shortMessage || error.message || error}`);
    }
  }
}

async function main() {
  assertExpectedChain(
    process.env.ROBINHOOD_TESTNET_CHAIN_ID || process.env.FLASHLOAN_CHAIN_ID || 46630
  );
  const mode = readArg("mode", process.env.ROBINHOOD_TESTNET_DIAGNOSE_MODE || "all");
  const manifest = readManifest();
  if (!manifest.officialV4 || !manifest.officialV4.poolManager) {
    throw new Error("officialV4 pool manifest missing; run testnet-v4-official-pool-create-live first");
  }

  const [owner] = await ethers.getSigners();
  const labels = addressLabels(manifest);
  const symbolToAddress = {};
  for (const [address, label] of Object.entries(labels)) {
    symbolToAddress[label] = address;
  }
  const fee = Number(manifest.officialV4.fee);
  const tickSpacing = Number(manifest.officialV4.tickSpacing);
  const hooks = manifest.officialV4.hooks || ethers.ZeroAddress;
  const poolManagerAddress = manifest.officialV4.poolManager;

  const routeText = readArg("route", process.env.ROBINHOOD_TESTNET_OFFICIAL_ARB_ROUTE || "");
  let routeSymbols = routeText ? parseRouteSymbols(routeText) : [];
  let routeAddresses = routeSymbols.map((symbol) => symbolToAddress[symbol] || symbol);
  if (routeAddresses.length !== 4) {
    routeAddresses = routeAddressesFromImbalanceRoute(manifest.officialV4.lastMakeArb?.route);
    if (routeAddresses) {
      routeSymbols = routeAddresses.map((address) => symbolForAddress(address, labels));
    }
  }
  if (routeAddresses.length !== 4 || routeAddresses[0].toLowerCase() !== routeAddresses[3].toLowerCase()) {
    throw new Error(
      "official V4 diagnostic requires a closed 4-address triangle route; " +
      "pass --route 'A -> B -> C -> A' or run the imbalance maker first"
    );
  }
  for (const address of routeAddresses) {
    if (!ethers.isAddress(address)) {
      throw new Error(`invalid route address: ${address}`);
    }
  }
  const explicitPoolKeysJson = readArg(
    "pool-keys-json",
    process.env.ROBINHOOD_TESTNET_OFFICIAL_ARB_POOL_KEYS_JSON || ""
  );
  const buildGroup = (groupRouteAddresses, groupRouteSymbols) => {
    const groupKeys = poolKeyHelper.resolveRoutePoolKeys(
      groupRouteAddresses,
      explicitPoolKeysJson,
      { fee, tickSpacing, hooks }
    );
    return {
      tokenZ: groupRouteAddresses[0],
      routeSymbols: groupRouteSymbols,
      routeAddresses: groupRouteAddresses,
      keys: groupKeys,
      directions: [
        zeroForOne(groupKeys[0], groupRouteAddresses[0]),
        zeroForOne(groupKeys[1], groupRouteAddresses[1]),
        zeroForOne(groupKeys[2], groupRouteAddresses[2]),
      ],
    };
  };
  const forwardGroup = buildGroup(routeAddresses, routeSymbols);
  const routeGroups = [forwardGroup];
  const includeReverse = String(
    readArg("both", process.env.ROBINHOOD_TESTNET_DIAGNOSE_BOTH || "1")
  ).trim().toLowerCase();
  if (
    includeReverse !== "0"
    && includeReverse !== "false"
    && includeReverse !== "no"
    && !explicitPoolKeysJson
  ) {
    const reverseAddresses = [
      routeAddresses[0],
      routeAddresses[2],
      routeAddresses[1],
      routeAddresses[0],
    ];
    const reverseSymbols = [
      routeSymbols[0],
      routeSymbols[2],
      routeSymbols[1],
      routeSymbols[0],
    ];
    routeGroups.push(buildGroup(reverseAddresses, reverseSymbols));
  }

  console.log("official V4 diagnostic (read-only):");
  console.log(`  mode=${mode}`);
  console.log(`  poolManager=${poolManagerAddress}`);
  console.log(`  route=${routeSymbols.join(" -> ")}`);
  console.log(`  groups=${routeGroups.length} directions=${routeGroups.map(group => group.directions.join(",")).join(" | ")}`);

  if (mode === "depth" || mode === "all") {
    console.log("\n[depth]");
    await printKnownTokensAndPools(owner, manifest, forwardGroup.keys, routeAddresses, labels);
    await printPoolDepth(owner, manifest, routeAddresses, forwardGroup.keys, forwardGroup.directions, labels);
  }
  if (mode === "balance" || mode === "all") {
    console.log("\n[balance]");
    await printKnownTokensAndPools(owner, manifest, forwardGroup.keys, routeAddresses, labels);
    await printTriangularBalance(owner, manifest, routeGroups);
  }
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
