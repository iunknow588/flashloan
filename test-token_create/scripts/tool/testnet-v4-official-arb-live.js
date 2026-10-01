const path = require("path");
const fs = require("fs");
const { ethers, artifacts } = require("../lib/hardhat");

const MANIFEST_PATH = process.env.FLASHLOAN_MANIFEST_PATH
  ? path.resolve(process.env.FLASHLOAN_MANIFEST_PATH)
  : path.resolve(__dirname, "../../config/testnet-v4-amm/manifest.json");
const ERC20_ABI = [
  "function totalSupply() view returns (uint256)",
  "function balanceOf(address) view returns (uint256)",
  "function mint(address to, uint256 amount)",
];

const MIN_SQRT_PRICE = 4295128740n;
const MAX_SQRT_PRICE = 1461446703485210103287273052203988822378723970341n;

function readManifest() {
  return JSON.parse(fs.readFileSync(MANIFEST_PATH, "utf8"));
}

function writeManifest(manifest) {
  fs.mkdirSync(path.dirname(MANIFEST_PATH), { recursive: true });
  const text = JSON.stringify(manifest, null, 2) + "\n";
  let lastError = null;
  for (let attempt = 0; attempt < 5; attempt += 1) {
    try {
      fs.writeFileSync(MANIFEST_PATH, text, "utf8");
      return true;
    } catch (error) {
      lastError = error;
      if (attempt < 4) {
        const waitUntil = Date.now() + 300;
        while (Date.now() < waitUntil) {}
      }
    }
  }
  console.error(`warning: failed to persist ${MANIFEST_PATH}: ${lastError && lastError.message}`);
  return false;
}

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

function loadExplicitPoolKeysJson() {
  const jsonText = readArg(
    "pool-keys-json",
    process.env.ROBINHOOD_TESTNET_OFFICIAL_ARB_POOL_KEYS_JSON || ""
  );
  if (!jsonText) return null;
  const parsed = JSON.parse(jsonText);
  if (!Array.isArray(parsed) || parsed.length !== 3) {
    throw new Error("official V4 arb pool keys json must be a 3-element array");
  }
  return parsed.map((key) => ({
    currency0: String(key.currency0 || ""),
    currency1: String(key.currency1 || ""),
    fee: Number(key.fee),
    tickSpacing: Number(key.tickSpacing),
    hooks: String(key.hooks || ""),
  }));
}

function sortPair(a, b) {
  return BigInt(a.toLowerCase()) < BigInt(b.toLowerCase()) ? [a, b] : [b, a];
}

function decodeRevertData(rawData) {
  const data = String(rawData || "");
  if (!data.startsWith("0x") || data.length < 10) {
    return { data, selector: data, errorDetail: data || "no_revert_data" };
  }
  const selector = data.slice(0, 10).toLowerCase();
  const knownSelectors = {
    "0x16301234": { name: "ArbitrageNotProfitable(received, required)", decode: true },
    "0xb20c2204": { name: "SwapNotResolved()" },
    "0x90b8ec18": { name: "TransferFailed()" },
    "0xa87726a1": { name: "BadPoolManagerCallback()" },
    "0x41abc801": { name: "InvalidRequest()" },
    "0xab143c06": { name: "Reentrancy()" },
    "0x4f422847": { name: "NoProfitableRoute()" },
    "0x5f9d6142": { name: "DexError(uint256)" },
  };
  const known = knownSelectors[selector];
  if (!known) {
    return { data, selector, errorDetail: `revert selector ${selector}` };
  }
  if (known.decode && data.length >= 10 + 64) {
    try {
      const [received, required] = ethers.AbiCoder.defaultAbiCoder().decode(
        ["uint256", "uint256"],
        "0x" + data.slice(10)
      );
      return {
        data,
        selector,
        errorDetail: `${known.name} received=${ethers.formatUnits(received, 18)} required=${ethers.formatUnits(required, 18)}`,
      };
    } catch (error) {
      return { data, selector, errorDetail: known.name };
    }
  }
  return { data, selector, errorDetail: known.name };
}

function makeKey(a, b, fee, tickSpacing, hooks) {
  const [currency0, currency1] = sortPair(a, b);
  return { currency0, currency1, fee, tickSpacing, hooks };
}

function zeroForOne(key, input) {
  return input.toLowerCase() === key.currency0.toLowerCase();
}

function doubleBigInt(value) {
  return value > BigInt("0x7fffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff")
    ? 0n
    : value * 2n;
}

function poolId(key) {
  return ethers.keccak256(ethers.AbiCoder.defaultAbiCoder().encode(
    ["tuple(address currency0,address currency1,uint24 fee,int24 tickSpacing,address hooks)"],
    [key]
  ));
}

async function logOwnerBalance(owner, token, label) {
  try {
    const total = await token.totalSupply();
    const balance = await token.balanceOf(owner.address);
    const pct = (Number(balance) / Number(total)) * 100;
    console.log(`  ${label} owner balance=${ethers.formatUnits(balance, 18)} (${pct.toFixed(2)}% of total; 80% creation reserve is meant for later liquidity, not a post-run floor)`);
  } catch (error) {
    console.warn(`  warning: ${label} owner balance unavailable (${error.shortMessage || error.message || error})`);
  }
}

async function main() {
  if (process.env.ROBINHOOD_TESTNET_ALLOW_V4_AMM_LIVE_TEST !== "true") {
    throw new Error("ROBINHOOD_TESTNET_ALLOW_V4_AMM_LIVE_TEST must be true");
  }

  const [owner] = await ethers.getSigners();
  const manifest = readManifest();
  if (!manifest.officialV4 || !manifest.officialV4.poolManager) {
    throw new Error("officialV4 pool manifest missing; run testnet-v4-official-pool-create-live first");
  }

  const tz = manifest.tz;
  const ta = manifest.ta;
  const tb = manifest.tb;
  const fee = Number(manifest.officialV4.fee);
  const tickSpacing = Number(manifest.officialV4.tickSpacing);
  const hooks = manifest.officialV4.hooks || ethers.ZeroAddress;
  const poolManagerAddress = manifest.officialV4.poolManager;

  const routeText = readArg("route", process.env.ROBINHOOD_TESTNET_OFFICIAL_ARB_ROUTE || "TZ -> TB -> TA -> TZ");
  const symbols = parseRouteSymbols(routeText);
  const symbolAddress = { TZ: tz, TA: ta, TB: tb };
  if (symbols.length !== 4 || symbols[0] !== symbols[3] || new Set(symbols).size !== 3) {
    throw new Error("official V4 arb route must be a 4-symbol closed triangle made of 3 unique tokens, e.g. A -> B -> C -> A");
  }
  const envAddresses = parseRouteAddresses(process.env.ROBINHOOD_TESTNET_OFFICIAL_ARB_ADDRESSES || "");
  const routeAddresses = envAddresses.length > 0
    ? envAddresses
    : symbols.map((symbol) => {
        const address = symbolAddress[symbol];
        if (!address) {
          throw new Error(`official V4 arb route contains unsupported symbol ${symbol}; pass ROBINHOOD_TESTNET_OFFICIAL_ARB_ADDRESSES as 4 comma-separated token addresses`);
        }
        return address;
      });
  if (routeAddresses.length !== symbols.length) {
    throw new Error("official V4 arb route address list length must match route symbols length");
  }
  if (routeAddresses[0].toLowerCase() !== routeAddresses[3].toLowerCase()) {
    throw new Error("official V4 arb route must close: first and last token addresses must match");
  }
  for (const address of routeAddresses) {
    if (!ethers.isAddress(address)) {
      throw new Error(`official V4 arb route contains invalid token address: ${address}`);
    }
  }
  const explicitPoolKeys = loadExplicitPoolKeysJson();
  const inferredPoolParams = explicitPoolKeys
    ? explicitPoolKeys.map((key) => ({
        fee: key.fee,
        tickSpacing: key.tickSpacing,
        hooks: key.hooks,
      }))
    : [
        { fee, tickSpacing, hooks },
        { fee, tickSpacing, hooks },
        { fee, tickSpacing, hooks },
      ];
  const keys = [
    makeKey(routeAddresses[0], routeAddresses[1], fee, tickSpacing, hooks),
    makeKey(routeAddresses[1], routeAddresses[2], fee, tickSpacing, hooks),
    makeKey(routeAddresses[2], routeAddresses[3], fee, tickSpacing, hooks),
  ].map((key, index) => {
    const poolParams = inferredPoolParams[index];
    if (poolParams) {
      return makeKey(
        key.currency0,
        key.currency1,
        poolParams.fee || key.fee,
        poolParams.tickSpacing || key.tickSpacing,
        poolParams.hooks || key.hooks
      );
    }
    return key;
  });
  const directions = [
    zeroForOne(keys[0], routeAddresses[0]),
    zeroForOne(keys[1], routeAddresses[1]),
    zeroForOne(keys[2], routeAddresses[2]),
  ];

  const poolManager = new ethers.Contract(poolManagerAddress, [], owner);
  console.log("official V4 route:");
  console.log(`  poolManager=${poolManagerAddress}`);
  console.log(`  tz=${tz}`);
  console.log(`  ta=${ta}`);
  console.log(`  tb=${tb}`);
  console.log(`  route=${symbols.join(" -> ")}`);
  console.log(`  directions=${directions.join(",")}`);

  const Token = (address) => new ethers.Contract(address, ERC20_ABI, owner);
  const uniqueRouteTokens = Array.from(new Set(routeAddresses.map((address) => address.toLowerCase()))).map(
    (address) => routeAddresses.find((item) => item.toLowerCase() === address)
  );
  for (const [index, address] of uniqueRouteTokens.entries()) {
    try {
      const poolBalance = await Token(address).balanceOf(poolManagerAddress);
      console.log(`  token${index} PoolManager balance=${ethers.formatUnits(poolBalance, 18)}`);
      if (poolBalance === 0n) {
        console.warn(`  warning: token${index} PoolManager balance is zero`);
      }
    } catch (error) {
      console.warn(`  warning: token${index} balanceOf failed (${error.shortMessage || error.message || error})`);
    }
  }
  for (const [index, key] of keys.entries()) {
    console.log(`  pool${index + 1} id=${poolId(key)}`);
  }

  await logOwnerBalance(owner, Token(tz), "tz");
  await logOwnerBalance(owner, Token(ta), "ta");
  await logOwnerBalance(owner, Token(tb), "tb");

  let arb;
  const Factory = await ethers.getContractFactory("OfficialV4FlashArb");
  const artifact = await artifacts.readArtifact("OfficialV4FlashArb");
  const expectedBytecode = String(artifact.deployedBytecode || "").toLowerCase();
  const arbCandidates = [
    manifest.officialV4.lastArbLiveTest?.arb,
    manifest.officialV4.arb,
  ]
    .filter(Boolean)
    .filter((value, index, all) => (
      all.findIndex((item) => item.toLowerCase() === String(value).toLowerCase()) === index
    ));
  let selectedArb = "";
  for (const candidate of arbCandidates) {
    try {
      const code = String(await ethers.provider.getCode(candidate)).toLowerCase();
      const persistedCode = String(
        candidate.toLowerCase() === String(manifest.officialV4.arb || "").toLowerCase()
          ? (manifest.officialV4.arbBytecode || "")
          : ""
      ).toLowerCase();
      if (code && code !== "0x" && (code === expectedBytecode || (persistedCode && persistedCode === code))) {
        selectedArb = candidate;
        break;
      }
    } catch {
      // Try the next known deployment address.
    }
  }
  if (selectedArb) {
    arb = await ethers.getContractAt("OfficialV4FlashArb", selectedArb);
    if (String(manifest.officialV4.arb || "").toLowerCase() !== selectedArb.toLowerCase()) {
      manifest.officialV4.arb = selectedArb;
      writeManifest(manifest);
    }
    if (!manifest.officialV4.arbBytecode) {
      manifest.officialV4.arbBytecode = String(await ethers.provider.getCode(selectedArb)).toLowerCase();
      writeManifest(manifest);
    }
    console.log(`using OfficialV4FlashArb=${selectedArb}`);
  } else {
    arb = await Factory.deploy(poolManagerAddress);
    await arb.waitForDeployment();
    const deployedArb = await arb.getAddress();
    manifest.officialV4.arb = deployedArb;
    manifest.officialV4.arbBytecode = String(await ethers.provider.getCode(deployedArb)).toLowerCase();
    writeManifest(manifest);
    console.log(`deployed OfficialV4FlashArb=${deployedArb}`);
  }

  const amountText = readArg("amount", process.env.ROBINHOOD_TESTNET_OFFICIAL_ARB_AMOUNT || "1");
  const amountIn = ethers.parseUnits(expandNumericString(amountText), 18);
  // The adaptive search must start from the caller-provided amount; do not
  // replace it with a fixed 1 TZ origin.
  const searchStartAmount = amountIn;
  const searchStartText = amountText;
  const minProfit = BigInt(process.env.ROBINHOOD_TESTNET_OFFICIAL_ARB_MIN_PROFIT || "1");
  const reverseKeys = [keys[2], keys[1], keys[0]];
  const reverseDirections = [!directions[2], !directions[1], !directions[0]];
  const keysets = [keys, reverseKeys];
  const directionsets = [directions, reverseDirections];
  const amountAlreadyBest = String(
    readArg("amount-is-best", process.env.ROBINHOOD_TESTNET_OFFICIAL_ARB_AMOUNT_IS_BEST || "false")
  ).toLowerCase() === "true"
    && String(process.env.ROBINHOOD_TESTNET_OFFICIAL_ARB_REUSE_AMOUNT || "false").toLowerCase() === "true";
  const reportedInitialAmount = amountAlreadyBest ? amountText : searchStartText;
  let preview = null;
  let selectedAmountBig;
  let selectedAmountText;
  if (amountAlreadyBest) {
    selectedAmountBig = amountIn;
    selectedAmountText = amountText;
  } else {
    try {
      // Probe each amount as a separate eth_call. Running the on-chain adaptive
      // preview inside one outer call mutates the simulated pool state between
      // probes, so later amounts appear worse than they really are.
      const probe = async (amount) => {
        try {
          const result = await arb.previewArbitrageBestAmount.staticCall(
            routeAddresses[0],
            keysets,
            directionsets,
            amount,
            minProfit,
            true
          );
          const profit = result.selectedProfit ?? result[2] ?? 0n;
          return {
            ok: profit > 0n,
            amount,
            profit,
            routeIndex: Number(result.selectedRouteIndex ?? result[1] ?? 0n),
            attempted: Number(result.attemptedCount ?? result[3] ?? 1n),
          };
        } catch (error) {
          const revertInfo = decodeRevertData(error.data || error.info?.error?.data || "");
          if (revertInfo.selector === "0x4f422847") {
            // With isBestAmount=true the contract reverts when this exact
            // amount is not profitable; treat that as a miss and keep lowering.
            return {
              ok: false,
              amount,
              profit: 0n,
              routeIndex: 0,
              attempted: 1,
            };
          }
          throw error;
        }
      };

      let selectedAmountLocal = 0n;
      let selectedRouteLocal = 0;
      let selectedProfitLocal = 0n;
      let attemptedCount = 0;
      const MAX_STEPS = 24;
      let baseAmount = 0n;
      let lowerAmount = 0n;
      let upperAmount = 0n;
      let hasUpperAmount = false;

      const initialProbe = await probe(searchStartAmount);
      attemptedCount += initialProbe.attempted;
      if (initialProbe.ok) {
        selectedAmountLocal = searchStartAmount;
        selectedRouteLocal = initialProbe.routeIndex;
        selectedProfitLocal = initialProbe.profit;
        baseAmount = searchStartAmount;
        lowerAmount = searchStartAmount / 2n;
        let candidate = doubleBigInt(baseAmount);
        for (let step = 0; step < MAX_STEPS && candidate > 0n; step += 1) {
          const p = await probe(candidate);
          attemptedCount += p.attempted;
          if (p.ok && p.profit > selectedProfitLocal) {
            selectedAmountLocal = candidate;
            selectedRouteLocal = p.routeIndex;
            selectedProfitLocal = p.profit;
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
        upperAmount = searchStartAmount;
        let candidate = searchStartAmount / 2n;
        for (let step = 0; step < MAX_STEPS && candidate > 0n; step += 1) {
          const p = await probe(candidate);
          attemptedCount += p.attempted;
          if (p.ok) {
            selectedAmountLocal = candidate;
            selectedRouteLocal = p.routeIndex;
            selectedProfitLocal = p.profit;
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

          const left = await probe(leftMid);
          const right = await probe(rightMid);
          attemptedCount += left.attempted + right.attempted;
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

      preview = {
        selectedAmount: selectedAmountLocal,
        selectedRouteIndex: selectedRouteLocal,
        selectedProfit: selectedProfitLocal,
        attemptedCount,
      };
      if (!preview.selectedAmount || preview.selectedAmount === 0n) throw new Error("NoProfitableRoute");
      selectedAmountBig = preview.selectedAmount;
      selectedAmountText = ethers.formatUnits(selectedAmountBig, 18);
    } catch (error) {
      const revertInfo = decodeRevertData(error.data || error.info?.error?.data || "");
      console.log(
        "\nOFFICIAL_V4_ARB_RESULT_JSON " +
          JSON.stringify({
            ok: false,
            error: "static_arb_call_reverted",
            detail: revertInfo.errorDetail || error.shortMessage || error.reason || error.message || error,
            errorData: revertInfo.data,
            errorSelector: revertInfo.selector,
            errorDetail: revertInfo.errorDetail,
            routeSymbols: symbols,
            amount: amountText,
            initialAmount: reportedInitialAmount,
            staticCall: "reverted",
            method: "previewArbitrageBestAmount",
            bestAmountMethod: "onchain_preview_adaptive_best_amount",
          })
      );
      console.error("official V4 on-chain previewArbitrageBestAmount reverted");
      console.error(`  shortMessage=${error.shortMessage || error.message || ""}`);
      console.error(`  data=${revertInfo.data}`);
      console.error(`  errorDetail=${revertInfo.errorDetail}`);
      throw error;
    }
    console.log(`official V4 on-chain previewArbitrageBestAmount selected=${selectedAmountText} ${symbols[0]} routeIndex=${preview.selectedRouteIndex} attempted=${preview.attemptedCount}`);
    if (!amountAlreadyBest) {
      console.log(`  initialSearchOrigin=${searchStartText} ${symbols[0]}`);
    }
  }
  const bestAmountMethod = amountAlreadyBest
    ? "preselected_best_amount"
    : "onchain_preview_adaptive_best_amount";
  const bestAmountAttemptedCount = preview ? String(preview.attemptedCount || "1") : "1";
  let result;
  try {
    const previewRouteIndex = Number(preview?.selectedRouteIndex ?? 0);
    result = await arb.runArbitrage.staticCall(
      routeAddresses[0],
      keysets[previewRouteIndex],
      directionsets[previewRouteIndex],
      selectedAmountBig,
      minProfit
    );
  } catch (error) {
    const revertInfo = decodeRevertData(error.data || error.info?.error?.data || "");
    console.log(
      "\nOFFICIAL_V4_ARB_RESULT_JSON " +
        JSON.stringify({
          ok: false,
          error: "static_arb_call_reverted",
          detail: revertInfo.errorDetail || error.shortMessage || error.reason || error.message || error,
          errorData: revertInfo.data,
          errorSelector: revertInfo.selector,
          errorDetail: revertInfo.errorDetail,
          routeSymbols: symbols,
          amount: amountText,
          initialAmount: reportedInitialAmount,
          bestAmount: selectedAmountText,
          bestAmountRaw: selectedAmountBig.toString(),
          bestAmountMethod,
          bestAmountAttemptedCount,
          previewMethod: "previewArbitrageBestAmount",
          amountIsBest: amountAlreadyBest,
          staticCall: "reverted",
          method: "runArbitrage",
        })
    );
    console.error("official V4 static fixed-route arbitrage reverted after best amount preview");
    console.error(`  shortMessage=${error.shortMessage || ""}`);
    console.error(`  data=${revertInfo.data}`);
    console.error(`  errorDetail=${revertInfo.errorDetail}`);
    console.error(`  reason=${error.reason || error.message || error}`);
    throw error;
  }
  const routeIndex = Number(preview?.selectedRouteIndex ?? 0);
  const selectedSymbols = routeIndex === 0 ? symbols : [symbols[0], symbols[2], symbols[1], symbols[0]];
  console.log(`official V4 ${amountAlreadyBest ? "preselected amount" : "adaptive best amount preview"} + static arbitrageBest result:`);
  console.log(`  initialAmount=${reportedInitialAmount}`);
  console.log(`  bestAmount=${selectedAmountText} ${symbols[0]}`);
  console.log(`  attemptedCount=${bestAmountAttemptedCount}`);
  console.log(`  selected routeIndex=${routeIndex}`);
  console.log(`  selected route=${selectedSymbols.join(" -> ")}`);
  console.log(`  outTb=${ethers.formatUnits(result[0], 18)}`);
  console.log(`  outTa=${ethers.formatUnits(result[1], 18)}`);
  console.log(`  out${symbols[0]}=${ethers.formatUnits(result[2], 18)}`);
  console.log(`  profit=${ethers.formatUnits(result[3], 18)} ${symbols[0]}`);
  const staticOk = result[3] > 0n;
  const profitHuman = ethers.formatUnits(result[3], 18);
  const outAmountHuman = ethers.formatUnits(result[2], 18);
  console.log(
    "\nOFFICIAL_V4_ARB_RESULT_JSON " +
      JSON.stringify({
        ok: staticOk,
        error: staticOk ? undefined : "static_arb_not_profitable",
        detail: staticOk ? "static_call_ok" : "static arbitrage is not profitable",
        routeSymbols: symbols,
        selectedRouteSymbols: selectedSymbols,
        amount: amountText,
        initialAmount: reportedInitialAmount,
        bestAmount: selectedAmountText,
        bestAmountRaw: selectedAmountBig.toString(),
        bestAmountMethod,
        bestAmountAttemptedCount,
        previewMethod: "previewArbitrageBestAmount",
        amountIsBest: amountAlreadyBest,
        staticCall: staticOk ? "ok" : "not_profitable",
        method: "runArbitrage",
        routeIndex,
        profit: result[3].toString(),
        profitHuman,
        profitRaw: result[3].toString(),
        outAmount: result[2].toString(),
        outAmountHuman,
      })
  );
  if (!staticOk) {
    process.exitCode = 1;
    return;
  }

  if (process.env.ROBINHOOD_TESTNET_ALLOW_OFFICIAL_V4_ARB_BROADCAST !== "true") {
    console.log(
      "\nOFFICIAL_V4_ARB_RESULT_JSON " +
        JSON.stringify({
          ok: true,
          detail: "broadcast disabled; static preflight passed",
          routeSymbols: symbols,
          selectedRouteSymbols: selectedSymbols,
          amount: amountText,
          initialAmount: reportedInitialAmount,
          bestAmount: selectedAmountText,
          bestAmountRaw: selectedAmountBig.toString(),
          bestAmountMethod,
          bestAmountAttemptedCount,
          previewMethod: "previewArbitrageBestAmount",
          amountIsBest: amountAlreadyBest,
          staticCall: "ok",
          broadcastDisabled: true,
          method: "runArbitrage",
          routeIndex,
          profit: result[3].toString(),
          profitHuman,
          profitRaw: result[3].toString(),
          outAmount: result[2].toString(),
          outAmountHuman,
        })
    );
    return;
  }

  const tx = await arb.runArbitrageBestWithSelectedAmount(
    routeAddresses[0],
    keysets,
    directionsets,
    selectedAmountBig,
    minProfit,
    true,
    routeIndex
  );
  const txHash = tx.hash;
  let receipt = null;
  let waitError = null;
  try {
    receipt = await tx.wait();
  } catch (error) {
    waitError = error;
  }
  const record = {
    txHash,
    runAt: new Date().toISOString(),
    deployer: owner.address,
    arb: await arb.getAddress(),
    poolManager: poolManagerAddress,
    amountIn: amountIn.toString(),
    initialAmountIn: amountIn.toString(),
    selectedAmountIn: selectedAmountBig.toString(),
    outTb: result[0].toString(),
    outTa: result[1].toString(),
    outTz: result[2].toString(),
    profit: result[3].toString(),
    profitHuman,
    profitRaw: result[3].toString(),
    selectedAmount: selectedAmountText,
    selectedAmountMethod: bestAmountMethod,
    selectedAmountAttemptedCount: bestAmountAttemptedCount,
    selectedAmountPreviewedOnChain: true,
    amountIsBest: true,
    gasUsed: receipt ? receipt.gasUsed.toString() : "0",
    waitError: waitError ? (waitError.shortMessage || waitError.reason || waitError.message || String(waitError)).slice(0, 200) : null,
    route: { keys, directions },
    bestRoute: { keysets, directionsets, routeIndex },
    selectedRouteSymbols: selectedSymbols,
    reservePolicy: "80_percent_creation_reserve_is_for_later_liquidity_not_a_post_run_floor",
  };
  manifest.officialV4.lastArbLiveTest = record;
  writeManifest(manifest);
  console.log("official V4 live arbitrageBest broadcast:");
  console.log(`  txHash=${txHash}`);
  console.log(`  gasUsed=${receipt ? receipt.gasUsed : "pending"}`);
  console.log(`  routeIndex=${routeIndex}`);
  console.log(`  selected route=${selectedSymbols.join(" -> ")}`);
  console.log(`  profit=${ethers.formatUnits(result[3], 18)} ${symbols[0]}`);
  if (waitError) {
    const revertInfo = decodeRevertData(waitError.data || waitError.info?.error?.data || "");
    console.log(
      "\nOFFICIAL_V4_ARB_RESULT_JSON " +
        JSON.stringify({
          ok: false,
          error: "broadcast_receipt_wait_failed",
          detail: waitError.shortMessage || waitError.reason || waitError.message || "broadcast receipt wait failed",
          errorData: revertInfo.data,
          errorSelector: revertInfo.selector,
          errorDetail: revertInfo.errorDetail || revertInfo.data,
          routeSymbols: symbols,
          selectedRouteSymbols: selectedSymbols,
          amount: amountText,
          initialAmount: reportedInitialAmount,
          bestAmount: selectedAmountText,
          bestAmountRaw: selectedAmountBig.toString(),
          bestAmountMethod,
          bestAmountAttemptedCount,
          previewMethod: "previewArbitrageBestAmount",
          amountIsBest: true,
          broadcast: true,
          txHash,
          method: "runArbitrage",
          routeIndex,
          profit: result[3].toString(),
          profitHuman,
          profitRaw: result[3].toString(),
          outAmount: result[2].toString(),
          outAmountHuman,
        })
    );
    console.error("official V4 broadcast receipt wait failed; transaction was submitted but confirmation is unknown");
    console.error(`  txHash=${txHash}`);
    console.error(`  waitError=${waitError.shortMessage || waitError.reason || waitError.message || waitError}`);
    process.exitCode = 1;
    return;
  }
  console.log(
    "\nOFFICIAL_V4_ARB_RESULT_JSON " +
      JSON.stringify({
        ok: true,
        detail: "broadcast_complete",
        routeSymbols: symbols,
        selectedRouteSymbols: selectedSymbols,
        amount: amountText,
        initialAmount: reportedInitialAmount,
        bestAmount: selectedAmountText,
        bestAmountRaw: selectedAmountBig.toString(),
        bestAmountMethod,
        bestAmountAttemptedCount,
        previewMethod: "previewArbitrageBestAmount",
        amountIsBest: true,
        broadcast: true,
        txHash,
        method: "runArbitrage",
        routeIndex,
        profit: result[3].toString(),
        profitHuman,
        profitRaw: result[3].toString(),
        outAmount: result[2].toString(),
        outAmountHuman,
      })
  );
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
