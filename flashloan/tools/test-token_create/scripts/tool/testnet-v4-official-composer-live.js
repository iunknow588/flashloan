const path = require("path");
const fs = require("fs");
const { ethers, artifacts, network } = require("../lib/hardhat");
const poolKeyHelper = require("./v4-official-pool-keys");
const { matchesArtifactRuntime } = require("./official-v4-immutable-code");

const MANIFEST_PATH = process.env.FLASHLOAN_MANIFEST_PATH
  ? path.resolve(process.env.FLASHLOAN_MANIFEST_PATH)
  : path.resolve(__dirname, "../../config/testnet-v4-amm/manifest.json");
const ERC20_ABI = [
  "function totalSupply() view returns (uint256)",
  "function balanceOf(address) view returns (uint256)",
  "function mint(address to, uint256 amount)",
];

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

function safeKeys(keys) {
  return (keys || []).map((key) => ({
    currency0: key.currency0,
    currency1: key.currency1,
    fee: Number(key.fee),
    tickSpacing: Number(key.tickSpacing),
    hooks: key.hooks,
  }));
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

async function main() {
  assertExpectedChain(
    process.env.ROBINHOOD_TESTNET_CHAIN_ID || process.env.FLASHLOAN_CHAIN_ID || 46630
  );
  if (process.env.ROBINHOOD_TESTNET_ALLOW_V4_AMM_LIVE_TEST !== "true") {
    throw new Error("ROBINHOOD_TESTNET_ALLOW_V4_AMM_LIVE_TEST must be true");
  }

  const [owner] = await ethers.getSigners();
  const manifest = readManifest();
  const official = manifest.officialV4 || {};
  if (!official.poolManager || !official.arb) {
    throw new Error("officialV4 poolManager/arb missing; run testnet-v4-official-arb-live first");
  }

  const tz = manifest.tz;
  const ta = manifest.ta;
  const tb = manifest.tb;
  const fee = Number(official.fee);
  const tickSpacing = Number(official.tickSpacing);
  const hooks = official.hooks || ethers.ZeroAddress;
  const poolManagerAddress = official.poolManager;
  const executorAddress = official.arb;

  const routeText = readArg("route", process.env.ROBINHOOD_TESTNET_OFFICIAL_COMPOSER_ROUTE || "TZ -> TB -> TA -> TZ");
  const symbols = parseRouteSymbols(routeText);
  const symbolAddress = { TZ: tz, TA: ta, TB: tb };
  const routeAddresses = symbols.map((symbol) => {
    const address = symbolAddress[symbol];
    if (!address) {
      throw new Error(`official V4 composer route contains unsupported symbol ${symbol}; expected TA/TB/TZ`);
    }
    return address;
  });
  if (routeAddresses.length !== 4 || routeAddresses[0].toLowerCase() !== routeAddresses[3].toLowerCase()) {
    throw new Error("official V4 composer route must be a 4-symbol closed triangle");
  }

  const explicitPoolKeysJson = readArg(
    "pool-keys-json",
    process.env.ROBINHOOD_TESTNET_OFFICIAL_ARB_POOL_KEYS_JSON || ""
  );
  const buildKeys = (groupRouteAddresses) =>
    poolKeyHelper.resolveRoutePoolKeys(
      groupRouteAddresses,
      explicitPoolKeysJson,
      { fee, tickSpacing, hooks }
    );
  const directionsFor = (groupKeys, groupRouteAddresses) => [
    zeroForOne(groupKeys[0], groupRouteAddresses[0]),
    zeroForOne(groupKeys[1], groupRouteAddresses[1]),
    zeroForOne(groupKeys[2], groupRouteAddresses[2]),
  ];
  const keys = buildKeys(routeAddresses);
  const directions = directionsFor(keys, routeAddresses);
  const keysets = [keys];
  const directionsets = [directions];
  const reverseSymbols = [symbols[0], symbols[2], symbols[1], symbols[0]];
  const includeReverse = String(
    readArg("both", process.env.ROBINHOOD_TESTNET_COMPOSER_BOTH || "1")
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
    const reverseKeys = buildKeys(reverseAddresses);
    const reverseDirections = directionsFor(reverseKeys, reverseAddresses);
    keysets.push(reverseKeys);
    directionsets.push(reverseDirections);
  }

  console.log("official V4 composer route:");
  console.log(`  poolManager=${poolManagerAddress}`);
  console.log(`  executor=${executorAddress}`);
  console.log(`  route=${symbols.join(" -> ")}`);
  console.log(`  groups=${keysets.length} directions=${directionsets.map(group => group.join(",")).join(" | ")}`);

  const Token = (address) => new ethers.Contract(address, ERC20_ABI, owner);
  for (const [index, address] of routeAddresses.entries()) {
    const poolBalance = await Token(address).balanceOf(poolManagerAddress);
    console.log(`  token${index} PoolManager balance=${ethers.formatUnits(poolBalance, 18)}`);
  }

  const ComposerFactory = await ethers.getContractFactory("ArbitrageComposer");
  const composerArtifact = await artifacts.readArtifact("ArbitrageComposer");
  const expectedBytecode = String(composerArtifact.deployedBytecode || "").toLowerCase();
  const existingComposer = official.composer || "";
  const forceRedeploy =
    process.env.ROBINHOOD_TESTNET_OFFICIAL_COMPOSER_FORCE_REDEPLOY === "true" ||
    process.argv.includes("--force-redeploy");
  const deployOnly =
    process.env.ROBINHOOD_TESTNET_OFFICIAL_COMPOSER_DEPLOY_ONLY === "true" ||
    process.argv.includes("--deploy-only");
  let existingBytecode = "0x";
  if (existingComposer) {
    existingBytecode = String(await ethers.provider.getCode(existingComposer)).toLowerCase();
  }

  let composer = null;
  if (existingComposer && existingBytecode !== "0x" && !forceRedeploy) {
    composer = await ethers.getContractAt("ArbitrageComposer", existingComposer);
    if (!matchesArtifactRuntime(expectedBytecode, existingBytecode)) {
      console.warn("warning: existing ArbitrageComposer does not match local artifact; a redeploy is required");
      composer = null;
    } else {
      const actualExecutor = await composer.executor();
      if (String(actualExecutor).toLowerCase() !== executorAddress.toLowerCase()) {
        console.warn(
          `warning: existing ArbitrageComposer executor=${actualExecutor} does not match officialV4.arb=${executorAddress}`
        );
        composer = null;
      }
    }
  }
  if (composer === null) {
    if (process.env.ROBINHOOD_TESTNET_ALLOW_OFFICIAL_V4_COMPOSER_BROADCAST === "true" || process.argv.includes("--broadcast") || forceRedeploy) {
      composer = await ComposerFactory.deploy(poolManagerAddress, executorAddress);
      await composer.waitForDeployment();
      const deployedComposer = await composer.getAddress();
      const deployedCode = String(await ethers.provider.getCode(deployedComposer)).toLowerCase();
      manifest.officialV4 = manifest.officialV4 || {};
      manifest.officialV4.composer = deployedComposer;
      manifest.officialV4.composerCode = deployedCode;
      manifest.officialV4.composerAuthority = {
        address: deployedComposer,
        evidenceType: "official_v4_custom_contracts_code_check",
        source: "flashloan/src/runtime/evidence/robinhood-testnet/official_v4_custom_contracts/latest.json",
        checkedAt: new Date().toISOString(),
        codeBytes: (deployedCode.length - 2) / 2,
        status: "present",
      };
      fs.writeFileSync(MANIFEST_PATH, JSON.stringify(manifest, null, 2) + "\n", "utf8");
      console.log(`deployed ArbitrageComposer=${deployedComposer}`);
      if (deployOnly) {
        console.log(
          "\nOFFICIAL_V4_COMPOSER_RESULT_JSON " +
            JSON.stringify({
              ok: true,
              detail: "deploy_only_complete",
              composer: deployedComposer,
              executor: executorAddress,
              poolManager: poolManagerAddress,
              codeBytes: (deployedCode.length - 2) / 2,
              broadcastDisabled: true,
            })
        );
        return;
      }
    } else {
      throw new Error(
        "ArbitrageComposer is missing, stale, or points to a different executor; " +
        "run with ROBINHOOD_TESTNET_ALLOW_OFFICIAL_V4_COMPOSER_BROADCAST=true or --broadcast to redeploy"
      );
    }
  } else if (composer) {
    console.log(`using existing ArbitrageComposer=${existingComposer}`);
  } else {
    throw new Error("ArbitrageComposer not deployed; run with ROBINHOOD_TESTNET_ALLOW_OFFICIAL_V4_COMPOSER_BROADCAST=true or --broadcast first");
  }

  const amountText = readArg("amount", process.env.ROBINHOOD_TESTNET_OFFICIAL_COMPOSER_AMOUNT || "1");
  const amountIn = ethers.parseUnits(amountText, 18);
  const minProfit = BigInt(process.env.ROBINHOOD_TESTNET_OFFICIAL_COMPOSER_MIN_PROFIT || "1");
  const minDeviationBps = BigInt(readArg("min-deviation-bps", "0"));
  const mQuoteText = readArg("m-quote", process.env.ROBINHOOD_TESTNET_OFFICIAL_COMPOSER_M_QUOTE || "0");
  const xQuoteText = readArg("x-quote", process.env.ROBINHOOD_TESTNET_OFFICIAL_COMPOSER_X_QUOTE || "0");
  const yQuoteText = readArg("y-quote", process.env.ROBINHOOD_TESTNET_OFFICIAL_COMPOSER_Y_QUOTE || "0");
  const mQuote = mQuoteText ? ethers.parseUnits(mQuoteText, 18) : 0n;
  const xQuote = xQuoteText ? ethers.parseUnits(xQuoteText, 18) : 0n;
  const yQuote = yQuoteText ? ethers.parseUnits(yQuoteText, 18) : 0n;
  const candidate = {
    routePools: keysets,
    routeDirections: directionsets,
    deviationBps: minDeviationBps,
    mQuote,
    xQuote,
    yQuote,
  };

  let result;
  try {
    result = await composer.runBestDetailed.staticCall(routeAddresses[0], [candidate], amountIn, minProfit, minDeviationBps);
  } catch (error) {
    console.log(
      "\nOFFICIAL_V4_COMPOSER_RESULT_JSON " +
        JSON.stringify({
          ok: false,
          error: "static_composer_call_reverted",
          detail: error.shortMessage || error.reason || error.message || error,
          routeSymbols: symbols,
          amount: amountText,
          staticCall: "reverted",
          method: "runBestDetailed",
        })
    );
    console.error("official V4 composer staticCall reverted");
    console.error(`  shortMessage=${error.shortMessage || ""}`);
    console.error(`  reason=${error.reason || error.message || error}`);
    throw error;
  }

  const executed = Boolean(result[0]);
  const candidateIndex = Number(result[1]);
  const profit = result[2];
  const decoded = ethers.AbiCoder.defaultAbiCoder().decode(
    ["uint256", "uint256", "uint256", "uint256", "uint256"],
    result[3]
  );
  const routeIndex = Number(decoded[4]);
  const selectedSymbols = routeIndex === 0 ? symbols : reverseSymbols;
  const selectedKeys = result[4];
  const selectedDirections = result[5];

  console.log("official V4 composer staticCall result:");
  console.log(`  executed=${executed}`);
  console.log(`  candidateIndex=${candidateIndex}`);
  console.log(`  selected route=${selectedSymbols.join(" -> ")}`);
  console.log(`  outTb=${ethers.formatUnits(decoded[0], 18)}`);
  console.log(`  outTa=${ethers.formatUnits(decoded[1], 18)}`);
  console.log(`  out${symbols[0]}=${ethers.formatUnits(decoded[2], 18)}`);
  console.log(`  profit=${ethers.formatUnits(profit, 18)} ${symbols[0]}`);
  console.log(`  selectedKeys=${JSON.stringify(safeKeys(selectedKeys))}`);
  console.log(`  selectedDirections=${selectedDirections.map(String).join(",")}`);

  if (!executed || profit <= 0n) {
    process.exitCode = 1;
    return;
  }

  const broadcastAllowed = process.env.ROBINHOOD_TESTNET_ALLOW_OFFICIAL_V4_COMPOSER_BROADCAST === "true" || process.argv.includes("--broadcast");
  if (!broadcastAllowed) {
    console.log(
      "\nOFFICIAL_V4_COMPOSER_RESULT_JSON " +
        JSON.stringify({
          ok: true,
          detail: "broadcast disabled; static preflight passed",
          routeSymbols: symbols,
          selectedRouteSymbols: selectedSymbols,
          amount: amountText,
          staticCall: "ok",
          broadcastDisabled: true,
          method: "runBestDetailed",
          candidateIndex,
          routeIndex,
          selectedKeys: safeKeys(selectedKeys),
          selectedDirections,
          mQuote: mQuote.toString(),
          xQuote: xQuote.toString(),
          yQuote: yQuote.toString(),
          profit: profit.toString(),
          outAmount: decoded[2].toString(),
        })
    );
    return;
  }

  const tx = await composer.runBestDetailed(routeAddresses[0], [candidate], amountIn, minProfit, minDeviationBps);
  const receipt = await tx.wait();
  const txHash = receipt.hash;
  const record = {
    txHash,
    runAt: new Date().toISOString(),
    deployer: owner.address,
    composer: await composer.getAddress(),
    executor: executorAddress,
    poolManager: poolManagerAddress,
    amountIn: amountIn.toString(),
    outTb: decoded[0].toString(),
    outTa: decoded[1].toString(),
    outTz: decoded[2].toString(),
    profit: profit.toString(),
    gasUsed: receipt.gasUsed.toString(),
    route: { keys, directions },
    bestRoute: { keysets, directionsets, routeIndex },
    selectedRouteSymbols: selectedSymbols,
    selectedKeys: safeKeys(selectedKeys),
    selectedDirections,
    minDeviationBps: minDeviationBps.toString(),
  };
  manifest.officialV4.lastComposerLiveTest = record;
  fs.writeFileSync(MANIFEST_PATH, JSON.stringify(manifest, null, 2) + "\n", "utf8");

  console.log("official V4 composer live broadcast:");
  console.log(`  txHash=${txHash}`);
  console.log(`  gasUsed=${receipt.gasUsed}`);
  console.log(`  routeIndex=${routeIndex}`);
  console.log(`  selected route=${selectedSymbols.join(" -> ")}`);
  console.log(`  profit=${ethers.formatUnits(profit, 18)} ${symbols[0]}`);
  console.log(
    "\nOFFICIAL_V4_COMPOSER_RESULT_JSON " +
      JSON.stringify({
        ok: true,
        detail: "broadcast_complete",
        routeSymbols: symbols,
        selectedRouteSymbols: selectedSymbols,
        amount: amountText,
        broadcast: true,
        txHash,
        method: "runBestDetailed",
        candidateIndex,
        routeIndex,
        selectedKeys: safeKeys(selectedKeys),
        selectedDirections,
        mQuote: mQuote.toString(),
        xQuote: xQuote.toString(),
        yQuote: yQuote.toString(),
        profit: profit.toString(),
        outAmount: decoded[2].toString(),
      })
  );
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
