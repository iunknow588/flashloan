const path = require("path");
const fs = require("fs");
const { ethers, artifacts } = require("./lib/hardhat");

const MANIFEST_PATH = path.resolve(__dirname, "../config/testnet-v4-amm/manifest.json");
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

function readArg(name, fallback) {
  const idx = process.argv.indexOf(`--${name}`);
  if (idx >= 0) return process.argv[idx + 1];
  return fallback;
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

function poolId(key) {
  return ethers.keccak256(ethers.AbiCoder.defaultAbiCoder().encode(
    ["tuple(address currency0,address currency1,uint24 fee,int24 tickSpacing,address hooks)"],
    [key]
  ));
}

async function logOwnerBalance(owner, token, label) {
  const total = await token.totalSupply();
  const balance = await token.balanceOf(owner.address);
  const pct = (Number(balance) / Number(total)) * 100;
  console.log(`  ${label} owner balance=${ethers.formatUnits(balance, 18)} (${pct.toFixed(2)}% of total; 80% creation reserve is meant for later liquidity, not a post-run floor)`);
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
  const keys = [
    makeKey(routeAddresses[0], routeAddresses[1], fee, tickSpacing, hooks),
    makeKey(routeAddresses[1], routeAddresses[2], fee, tickSpacing, hooks),
    makeKey(routeAddresses[2], routeAddresses[3], fee, tickSpacing, hooks),
  ];
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
    const poolBalance = await Token(address).balanceOf(poolManagerAddress);
    console.log(`  token${index} PoolManager balance=${ethers.formatUnits(poolBalance, 18)}`);
    if (poolBalance === 0n) throw new Error(`token${index} PoolManager balance is zero`);
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
  const existingArb = manifest.officialV4.arb || "";
  let existingBytecode = "0x";
  if (existingArb) {
    existingBytecode = String(await ethers.provider.getCode(existingArb)).toLowerCase();
  }
  if (existingArb && existingBytecode !== "0x" && existingBytecode === expectedBytecode) {
    arb = await ethers.getContractAt("OfficialV4FlashArb", existingArb);
  } else {
    arb = await Factory.deploy(poolManagerAddress);
    await arb.waitForDeployment();
    manifest.officialV4.arb = await arb.getAddress();
    fs.writeFileSync(MANIFEST_PATH, JSON.stringify(manifest, null, 2) + "\n", "utf8");
    console.log(`deployed OfficialV4FlashArb=${manifest.officialV4.arb}`);
  }

  const amountText = readArg("amount", process.env.ROBINHOOD_TESTNET_OFFICIAL_ARB_AMOUNT || "0.0001");
  const amountIn = ethers.parseUnits(amountText, 18);
  const minProfit = BigInt(process.env.ROBINHOOD_TESTNET_OFFICIAL_ARB_MIN_PROFIT || "1");
  const reverseKeys = [keys[2], keys[1], keys[0]];
  const reverseDirections = [!directions[2], !directions[1], !directions[0]];
  const keysets = [keys, reverseKeys];
  const directionsets = [directions, reverseDirections];
  const amountAlreadyBest = String(
    readArg("amount-is-best", process.env.ROBINHOOD_TESTNET_OFFICIAL_ARB_AMOUNT_IS_BEST || "false")
  ).toLowerCase() === "true"
    && String(process.env.ROBINHOOD_TESTNET_OFFICIAL_ARB_REUSE_AMOUNT || "false").toLowerCase() === "true";
  let preview = null;
  let selectedAmountBig;
  let selectedAmountText;
  if (amountAlreadyBest) {
    selectedAmountBig = amountIn;
    selectedAmountText = amountText;
  } else {
    try {
      preview = await arb.previewArbitrageBestAmount.staticCall(
        routeAddresses[0],
        keysets,
        directionsets,
        amountIn,
        minProfit,
        false
      );
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
            initialAmount: amountText,
            staticCall: "reverted",
            method: "previewArbitrageBestAmount",
          })
      );
      console.error("official V4 previewArbitrageBestAmount reverted");
      console.error(`  shortMessage=${error.shortMessage || ""}`);
      console.error(`  data=${revertInfo.data}`);
      console.error(`  errorDetail=${revertInfo.errorDetail}`);
      throw error;
    }
  }
  const bestAmountMethod = amountAlreadyBest
    ? "preselected_best_amount"
    : "onchain_preview_adaptive_best_amount";
  const bestAmountAttemptedCount = preview ? preview.attemptedCount.toString() : "1";
  let result;
  try {
    result = await arb.runArbitrageBest.staticCall(routeAddresses[0], keysets, directionsets, selectedAmountBig, minProfit);
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
          initialAmount: amountText,
          bestAmount: selectedAmountText,
          bestAmountRaw: selectedAmountBig.toString(),
          bestAmountMethod,
          bestAmountAttemptedCount,
          amountIsBest: amountAlreadyBest,
          staticCall: "reverted",
          method: "runArbitrageBest",
        })
    );
    console.error("official V4 static arbitrageBest reverted after best amount preview");
    console.error(`  shortMessage=${error.shortMessage || ""}`);
    console.error(`  data=${revertInfo.data}`);
    console.error(`  errorDetail=${revertInfo.errorDetail}`);
    console.error(`  reason=${error.reason || error.message || error}`);
    throw error;
  }
  const routeIndex = Number(result[4] ?? Number(preview?.selectedRouteIndex ?? 0));
  const selectedSymbols = routeIndex === 0 ? symbols : [symbols[0], symbols[2], symbols[1], symbols[0]];
  console.log(`official V4 ${amountAlreadyBest ? "preselected amount" : "adaptive best amount preview"} + static arbitrageBest result:`);
  console.log(`  initialAmount=${amountText}`);
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
        initialAmount: amountText,
        bestAmount: selectedAmountText,
        bestAmountRaw: selectedAmountBig.toString(),
        bestAmountMethod,
        bestAmountAttemptedCount,
        amountIsBest: amountAlreadyBest,
        staticCall: staticOk ? "ok" : "not_profitable",
        method: "runArbitrageBest",
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
          initialAmount: amountText,
          bestAmount: selectedAmountText,
          bestAmountRaw: selectedAmountBig.toString(),
          bestAmountMethod,
          bestAmountAttemptedCount,
          amountIsBest: amountAlreadyBest,
          staticCall: "ok",
          broadcastDisabled: true,
          method: "runArbitrageBest",
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
    amountIsBest: true,
    gasUsed: receipt ? receipt.gasUsed.toString() : "0",
    waitError: waitError ? (waitError.shortMessage || waitError.reason || waitError.message || String(waitError)).slice(0, 200) : null,
    route: { keys, directions },
    bestRoute: { keysets, directionsets, routeIndex },
    selectedRouteSymbols: selectedSymbols,
    reservePolicy: "80_percent_creation_reserve_is_for_later_liquidity_not_a_post_run_floor",
  };
  manifest.officialV4.lastArbLiveTest = record;
  fs.writeFileSync(MANIFEST_PATH, JSON.stringify(manifest, null, 2) + "\n", "utf8");
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
          initialAmount: amountText,
          bestAmount: selectedAmountText,
          bestAmountRaw: selectedAmountBig.toString(),
          bestAmountMethod,
          bestAmountAttemptedCount,
          amountIsBest: true,
          broadcast: true,
          txHash,
          method: "runArbitrageBest",
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
        initialAmount: amountText,
        bestAmount: selectedAmountText,
        bestAmountRaw: selectedAmountBig.toString(),
        bestAmountMethod,
        bestAmountAttemptedCount,
        amountIsBest: true,
        broadcast: true,
        txHash,
        method: "runArbitrageBest",
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
