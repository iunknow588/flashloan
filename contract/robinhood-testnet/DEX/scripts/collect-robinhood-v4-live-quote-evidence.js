const fs = require("fs");
const path = require("path");
const hre = require("hardhat");

const REPO_ROOT = path.resolve(__dirname, "../../../..");
const DEFAULT_PLAN_PATH = path.join(
  REPO_ROOT,
  "flashloan",
  "src",
  "runtime",
  "evidence",
  "robinhood-testnet",
  "chain_test",
  "latest",
  "plan.json",
);
const DEFAULT_QUOTER_ADDRESS = "0x8Dc178eFB8111BB0973Dd9d722ebeFF267c98F94";
const DEFAULT_POOL_MANAGER_ADDRESS = "0x8366a39CC670B4001A1121B8F6A443A643e40951";

const QUOTER_ABI = [
  "function quoteExactInputSingle((tuple(address currency0,address currency1,uint24 fee,int24 tickSpacing,address hooks) poolKey,bool zeroForOne,uint128 exactAmount,bytes hookData) params) returns (uint256 amountOut,uint256 gasEstimate)",
];
const POOL_MANAGER_ABI = [
  "function extsload(bytes32 slot) view returns (bytes32)",
];
const V4_POOLS_SLOT = "0x0000000000000000000000000000000000000000000000000000000000000006";

function readJson(filePath) {
  return JSON.parse(fs.readFileSync(filePath, "utf8").replace(/^\uFEFF/, ""));
}

function writeJson(filePath, payload) {
  fs.mkdirSync(path.dirname(filePath), { recursive: true });
  fs.writeFileSync(filePath, `${JSON.stringify(payload, null, 2)}\n`, "utf8");
}

function stringify(value) {
  if (typeof value === "bigint") return value.toString();
  if (Array.isArray(value)) return value.map(stringify);
  if (value && typeof value === "object") {
    return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, stringify(item)]));
  }
  return value;
}

function normalizeAddress(value, { allowZero = false } = {}) {
  const raw = String(value || "").trim();
  if (!raw || !hre.ethers.isAddress(raw)) return "";
  const resolved = hre.ethers.getAddress(raw);
  if (!allowZero && resolved === hre.ethers.ZeroAddress) return "";
  return resolved;
}

function toBigInt(value) {
  if (typeof value === "bigint") return value;
  const text = String(value || "").trim();
  if (!text) return 0n;
  try {
    return BigInt(text);
  } catch {
    return 0n;
  }
}

function poolIdForKey(key) {
  return hre.ethers.keccak256(
    hre.ethers.AbiCoder.defaultAbiCoder().encode(
      ["tuple(address currency0,address currency1,uint24 fee,int24 tickSpacing,address hooks)"],
      [key],
    ),
  );
}

function stateSlotForPool(poolId) {
  const poolIdHex = hre.ethers.zeroPadValue(poolId, 32);
  return hre.ethers.keccak256(
    hre.ethers.concat([poolIdHex, V4_POOLS_SLOT]),
  );
}

function evidenceStamp() {
  const stamp = new Date().toISOString().replace(/[-:]/g, "").replace(/\.(\d+)Z$/, "$1Z");
  return `${stamp}-${process.pid}`;
}

function routeHops(plan) {
  const preflight = plan && typeof plan.preflightSpec === "object" ? plan.preflightSpec : {};
  return Array.isArray(preflight.routeHops) ? preflight.routeHops.filter((item) => item && typeof item === "object") : [];
}

function poolKeyAndDirection(hop) {
  const rawIn = normalizeAddress(hop.tokenIn, { allowZero: true });
  const rawOut = normalizeAddress(hop.tokenOut, { allowZero: true });
  const currency0 = BigInt(rawIn) < BigInt(rawOut) ? rawIn : rawOut;
  const currency1 = BigInt(rawIn) < BigInt(rawOut) ? rawOut : rawIn;
  const amountIn = toBigInt(hop.amountInRaw);
  const zeroForOne = rawIn === currency0;
  const blockers = [];
  if (!currency0 || !currency1) blockers.push("hop_token_missing");
  if (!rawIn || !rawOut) blockers.push("hop_token_missing");
  if (amountIn <= 0n) blockers.push("amount_in_missing_or_zero");
  if (!zeroForOne && !(rawIn === currency1 && rawOut === currency0)) blockers.push("hop_direction_does_not_match_pool_key");
  return {
    currency0,
    currency1,
    fee: Number(hop.fee || 3000),
    tickSpacing: Number(hop.tickSpacing || 60),
    hooks: normalizeAddress(hop.hooks || hre.ethers.ZeroAddress, { allowZero: true }) || hre.ethers.ZeroAddress,
    zeroForOne,
    tokenIn: rawIn,
    tokenOut: rawOut,
    amountIn,
    blockers,
  };
}

function encodeQuoteCalldata(hop) {
  const key = poolKeyAndDirection(hop);
  if (key.blockers.length) {
    return { ok: false, blockers: key.blockers, key: null, data: null };
  }
  const iface = new hre.ethers.Interface(QUOTER_ABI);
  const data = iface.encodeFunctionData("quoteExactInputSingle", [
    {
      poolKey: {
        currency0: key.currency0,
        currency1: key.currency1,
        fee: key.fee,
        tickSpacing: key.tickSpacing,
        hooks: key.hooks,
      },
      zeroForOne: key.zeroForOne,
      exactAmount: key.amountIn,
      hookData: "0x",
    },
  ]);
  return { ok: true, blockers: [], key, data };
}

function evidenceRows(plan) {
  return routeHops(plan).map((hop) => {
  const built = encodeQuoteCalldata(hop);
  const row = {
      hop: hop.hop,
      pool: hop.pool || null,
      tokenIn: hop.tokenIn || null,
      tokenOut: hop.tokenOut || null,
      inputDecimals: hop.inputDecimals ?? null,
      outputDecimals: hop.outputDecimals ?? null,
      quoteMethod: "robinhood_testnet_v4_quoter_quoteExactInputSingle_eth_call",
      adapterVersion: "robinhood-live-quote-evidence-v1",
      status: built.ok ? "pending" : "calldata_blocked",
      ok: built.ok ? null : false,
      blockers: built.blockers,
      key: built.key,
      poolId: built.key ? poolIdForKey({
        currency0: built.key.currency0,
        currency1: built.key.currency1,
        fee: built.key.fee,
        tickSpacing: built.key.tickSpacing,
        hooks: built.key.hooks,
      }) : null,
      calldata: built.data,
      amountInRaw: built.ok ? built.key.amountIn.toString() : null,
    };
    return row;
  });
}

function decodeError(error) {
  const message = error.shortMessage || error.reason || error.message || String(error);
  const data = error.data || error.info?.error?.data || error.error?.data || null;
  const known = new hre.ethers.Interface([
    "error PoolNotInitialized()",
    "error NotEnoughLiquidity()",
    "error InvalidPoolKey()",
    "error Paused()",
  ]);
  let decoded = null;
  if (data) {
    try {
      const parsed = known.parseError(data);
      decoded = {
        name: parsed.name,
        args: parsed.args.map((item) => typeof item === "bigint" ? item.toString() : item),
      };
    } catch {
      decoded = null;
    }
  }
  return { message, data, decoded };
}

function writeEvidence(report) {
  const dir = path.resolve(
    __dirname,
    "../deployments/evidence",
    `${evidenceStamp()}_${hre.network.name}-robinhood-v4-live-quote-evidence`,
  );
  const reportPath = path.join(dir, "report.json");
  writeJson(reportPath, stringify(report));
  return reportPath;
}

async function runLiveQuoteEvidence() {
  const planPath = path.resolve(process.env.ROBINHOOD_TESTNET_CHAIN_TEST_PLAN || DEFAULT_PLAN_PATH);
  const quoterAddress = normalizeAddress(
    process.env.ROBINHOOD_TESTNET_V4_QUOTER || DEFAULT_QUOTER_ADDRESS,
  );
  const poolManagerAddress = normalizeAddress(
    process.env.ROBINHOOD_TESTNET_V4_POOL_MANAGER || DEFAULT_POOL_MANAGER_ADDRESS,
  );
  const plan = readJson(planPath);
  const rows = evidenceRows(plan);
  const network = await hre.ethers.provider.getNetwork();
  const block = await hre.ethers.provider.getBlock("latest");
  const report = {
    schemaVersion: "robinhood-testnet-v4-live-quote-evidence-v1",
    runAt: new Date().toISOString(),
    network: hre.network.name,
    chainId: Number(network.chainId),
    blockNumber: block?.number ?? null,
    blockTimestamp: block?.timestamp ?? null,
    mode: "robinhood_testnet_v4_quoter_readonly_eth_call",
    readOnly: true,
    broadcastEnabled: false,
    executionEnabled: false,
    planPath,
    quoterAddress,
    poolManagerAddress,
    adapterVersion: "official_v4_quoter_quoteExactInputSingle_eth_call_v1",
    route: {
      pathText: plan?.route?.pathText || plan?.attempt?.pathText || "",
      hopCount: rows.length,
      triggerBlockNumber: plan?.attempt?.triggerBlockNumber || plan?.evidence?.triggerBlockNumber || null,
    },
    hops: [],
    blockingReasons: [],
  };

  if (!quoterAddress) report.blockingReasons.push("quoter_address_missing");
  if (!poolManagerAddress) report.blockingReasons.push("pool_manager_address_missing");
  if (!rows.length) report.blockingReasons.push("route_hops_missing");

  const quoter = quoterAddress
    ? new hre.ethers.Contract(quoterAddress, QUOTER_ABI, hre.ethers.provider)
    : null;
  const poolManager = poolManagerAddress
    ? new hre.ethers.Contract(poolManagerAddress, POOL_MANAGER_ABI, hre.ethers.provider)
    : null;

  for (const row of rows) {
    if (!row.ok && !row.calldata) {
      report.blockingReasons.push(`hop_${row.hop}_calldata_blocked`);
      report.hops.push(row);
      continue;
    }
    if (!quoter) {
      row.status = "quoter_missing";
      row.ok = false;
      report.hops.push(row);
      continue;
    }
    row.attempted = true;
    try {
      const [amountOut, gasEstimate] = await quoter.quoteExactInputSingle.staticCall(
        {
          poolKey: {
            currency0: row.key.currency0,
            currency1: row.key.currency1,
            fee: row.key.fee,
            tickSpacing: row.key.tickSpacing,
            hooks: row.key.hooks,
          },
          zeroForOne: row.key.zeroForOne,
          exactAmount: row.key.amountIn,
          hookData: "0x",
        },
        { gasLimit: 30000000 },
      );
      row.status = "ok";
      row.ok = true;
      row.amountOutRaw = amountOut.toString();
      row.gasEstimate = gasEstimate.toString();
      row.error = null;
    } catch (error) {
      row.status = "error";
      row.ok = false;
      row.error = decodeError(error);
      row.revertData = error?.data || error?.info?.error?.data || null;
    }

    let poolState = null;
    if (poolManager && row.poolId) {
      try {
        const stateSlot = stateSlotForPool(row.poolId);
        const slot0Raw = await poolManager.extsload(stateSlot);
        const liquiditySlot = hre.ethers.toBeHex(BigInt(stateSlot) + 3n, 32);
        const liquidityRaw = await poolManager.extsload(liquiditySlot);
        const slot0Value = BigInt(slot0Raw);
        let tick = Number((slot0Value >> 160n) & ((1n << 24n) - 1n));
        if (tick & (1 << 23)) tick -= 1 << 24;
        poolState = {
          source: "pool_manager_extsload_live_state",
          stateSlot,
          sqrtPriceX96: (slot0Value & ((1n << 160n) - 1n)).toString(),
          tick: tick.toString(),
          protocolFee: Number((slot0Value >> 184n) & 0xFFFFFFn).toString(),
          lpFee: Number((slot0Value >> 208n) & 0xFFFFFFn).toString(),
          liquidity: (BigInt(liquidityRaw) & ((1n << 128n) - 1n)).toString(),
        };
      } catch (error) {
        poolState = { error: String(error?.shortMessage || error?.message || error) };
      }
    }
    row.poolState = poolState;
    report.hops.push(row);
  }

  const failedHops = report.hops.filter((row) => row.ok !== true);
  if (failedHops.length) {
    report.blockingReasons.push(`one_or_more_hops_not_ok:${failedHops.map((row) => row.hop).join(",")}`);
  }
  const reportPath = writeEvidence(report);
  console.log(JSON.stringify(stringify({ ...report, reportPath }), null, 2));
  return { report, reportPath };
}

async function main() {
  const { report } = await runLiveQuoteEvidence();
  if (report.hops.some((row) => row.ok !== true)) process.exitCode = 1;
}

if (require.main === module) {
  main().catch((error) => {
    console.error(error.message || String(error));
    process.exitCode = 1;
  });
}

module.exports = {
  decodeError,
  encodeQuoteCalldata,
  evidenceRows,
  poolKeyAndDirection,
  routeHops,
  runLiveQuoteEvidence,
  stringify,
};
