const fs = require("fs");
const path = require("path");
const hre = require("hardhat");
const { AVALANCHE_V3_PROFILE } = require("./preflight-unified-flashloan");

const TARGET_CHAIN_ID = 43114n;
const ZERO = "0x0000000000000000000000000000000000000000";
const DEFAULT_AAVE_POOL = "0x794a61358D6845594F94dc1DB02A252b5b4814aD";
const DEFAULT_USDC = "0xB97EF9Ef8734C71904D8002F8b6Bc66Dd9c48a6E";
const FACTORY_ABI = ["function getPool(address,address,uint24) view returns (address)"];
const POOL_ABI = [
  "function factory() view returns (address)",
  "function token0() view returns (address)",
  "function token1() view returns (address)",
  "function fee() view returns (uint24)",
  "function liquidity() view returns (uint128)",
  "function slot0() view returns (uint160 sqrtPriceX96,int24 tick,uint16,uint16,uint16,uint8,bool)",
];
const TOKEN_ABI = [
  "function symbol() view returns (string)",
  "function decimals() view returns (uint8)",
];
const AAVE_ABI = ["function getReservesList() view returns (address[])"];
const QUOTER_ABI = [
  "function quoteExactInput(bytes path,uint256 amountIn) returns (uint256 amountOut,uint160[] sqrtPriceX96AfterList,uint32[] initializedTicksCrossedList,uint256 gasEstimate)",
];
const MULTICALL3_ADDRESS = "0xca11bde05977b3631167028862be2a173976ca11";
const MULTICALL3_ABI = [
  "function aggregate3((address target,bool allowFailure,bytes callData)[] calls) payable returns ((bool success,bytes returnData)[] returnData)",
];

function envAddress(...names) {
  for (const name of names) {
    const value = String(process.env[name] || "").trim();
    if (value && hre.ethers.isAddress(value)) return hre.ethers.getAddress(value);
  }
  return "";
}

function outputPath() {
  const configured =
    process.env.TRIANGULAR_RUNTIME_POOL_CACHE_FILE ||
    process.env.PINAX_POOL_DISCOVERY_CACHE_FILE ||
    "runtime/cache/avalanche_v3_pools.json";
  return path.isAbsolute(configured)
    ? configured
    : path.resolve(__dirname, "../../../flashloan/src_bot", configured);
}

function feeTiers() {
  return [...new Set(
    String(process.env.UNIFIED_V3_FEE_TIERS || "100,500,3000,10000")
      .split(",")
      .map((value) => Number(value.trim()))
      .filter((value) => Number.isInteger(value) && value > 0 && value <= 16_777_215),
  )];
}

function rpcHost() {
  try {
    return new URL(hre.network.config.url || "").host;
  } catch {
    return "";
  }
}

async function snapshotPool(poolAddress, factoryAddress, blockTag) {
  const code = await hre.ethers.provider.getCode(poolAddress, blockTag);
  if (code === "0x") return null;
  const pool = await hre.ethers.getContractAt(POOL_ABI, poolAddress);
  const [factory, token0, token1, fee, liquidity, slot0] = await Promise.all([
    pool.factory({ blockTag }),
    pool.token0({ blockTag }),
    pool.token1({ blockTag }),
    pool.fee({ blockTag }),
    pool.liquidity({ blockTag }),
    pool.slot0({ blockTag }),
  ]);
  if (factory.toLowerCase() !== factoryAddress.toLowerCase() || liquidity === 0n || slot0.sqrtPriceX96 === 0n) return null;
  const [token0Symbol, token1Symbol, token0Decimals, token1Decimals] = await Promise.all([
    hre.ethers.getContractAt(TOKEN_ABI, token0).then((token) => token.symbol({ blockTag })).catch(() => ""),
    hre.ethers.getContractAt(TOKEN_ABI, token1).then((token) => token.symbol({ blockTag })).catch(() => ""),
    hre.ethers.getContractAt(TOKEN_ABI, token0).then((token) => token.decimals({ blockTag })).catch(() => 0),
    hre.ethers.getContractAt(TOKEN_ABI, token1).then((token) => token.decimals({ blockTag })).catch(() => 0),
  ]);
  return {
    adapterKind: 1,
    pool: poolAddress,
    factory,
    token0,
    token1,
    token0_symbol: token0Symbol,
    token1_symbol: token1Symbol,
    token0_decimals: Number(token0Decimals),
    token1_decimals: Number(token1Decimals),
    fee: Number(fee),
    liquidity: liquidity.toString(),
    tick: Number(slot0.tick),
    code_hash: hre.ethers.keccak256(code),
  };
}

function batches(values, size) {
  const result = [];
  for (let start = 0; start < values.length; start += size) result.push(values.slice(start, start + size));
  return result;
}

async function getPools(factoryAddress, tokens, tiers, blockTag) {
  const multicallCode = await hre.ethers.provider.getCode(MULTICALL3_ADDRESS, blockTag);
  const factoryInterface = new hre.ethers.Interface(FACTORY_ABI);
  const jobs = [];
  for (let left = 0; left < tokens.length; left++) {
    for (let right = left + 1; right < tokens.length; right++) {
      for (const fee of tiers) {
        jobs.push({
          tokenA: tokens[left],
          tokenB: tokens[right],
          fee,
          callData: factoryInterface.encodeFunctionData("getPool", [tokens[left], tokens[right], fee]),
        });
      }
    }
  }
  if (multicallCode === "0x") {
    const factory = await hre.ethers.getContractAt(FACTORY_ABI, factoryAddress);
    const rows = [];
    for (const job of jobs) {
      const pool = await factory.getPool(job.tokenA, job.tokenB, job.fee, { blockTag });
      if (pool !== ZERO) rows.push({ ...job, pool });
    }
    return rows;
  }

  const multicall = await hre.ethers.getContractAt(MULTICALL3_ABI, MULTICALL3_ADDRESS);
  const rows = [];
  for (const chunk of batches(jobs, 200)) {
    const responses = await multicall.aggregate3.staticCall(
      chunk.map((job) => ({ target: factoryAddress, allowFailure: true, callData: job.callData })),
      { blockTag },
    );
    for (let index = 0; index < chunk.length; index++) {
      const response = responses[index];
      if (!response.success || response.returnData === "0x") continue;
      const [pool] = factoryInterface.decodeFunctionResult("getPool", response.returnData);
      if (pool !== ZERO) rows.push({ ...chunk[index], pool });
    }
  }
  return rows;
}

async function quoteProbe(quoter, snapshot, amountIn, blockTag) {
  const path = hre.ethers.solidityPacked(
    ["address", "uint24", "address"],
    [snapshot.token0, snapshot.fee, snapshot.token1],
  );
  try {
    const result = await quoter.quoteExactInput.staticCall(path, amountIn, { blockTag });
    return {
      pool: snapshot.pool,
      path,
      amount_in: amountIn.toString(),
      amount_out: result[0].toString(),
      gas_estimate: result[3].toString(),
    };
  } catch {
    return null;
  }
}

async function main() {
  const network = await hre.ethers.provider.getNetwork();
  if (network.chainId !== TARGET_CHAIN_ID) {
    throw new Error(`discovery requires Avalanche chainId ${TARGET_CHAIN_ID}, got ${network.chainId}`);
  }
  const factoryAddress = envAddress("UNIFIED_V3_FACTORY") || AVALANCHE_V3_PROFILE.factory;
  const routerAddress = envAddress("UNIFIED_V3_ROUTER") || AVALANCHE_V3_PROFILE.router;
  const quoterAddress = envAddress("UNIFIED_V3_QUOTER") || AVALANCHE_V3_PROFILE.quoter;
  const aavePoolAddress = envAddress("UNIFIED_AAVE_POOL_ADDRESS") || DEFAULT_AAVE_POOL;
  const usdcAddress = envAddress("UNIFIED_USDC_ADDRESS") || DEFAULT_USDC;
  const snapshotBlock = await hre.ethers.provider.getBlockNumber();
  for (const [label, address] of [["factory", factoryAddress], ["router", routerAddress], ["quoter", quoterAddress], ["aavePool", aavePoolAddress]]) {
    if ((await hre.ethers.provider.getCode(address, snapshotBlock)) === "0x") throw new Error(`${label} has no code: ${address}`);
  }

  const quoter = await hre.ethers.getContractAt(QUOTER_ABI, quoterAddress);
  const aave = await hre.ethers.getContractAt(AAVE_ABI, aavePoolAddress);
  const reserveAddresses = (await aave.getReservesList({ blockTag: snapshotBlock })).map((value) => hre.ethers.getAddress(value));
  const tokens = [...new Set([usdcAddress, ...reserveAddresses])];
  const pairs = new Map();
  const poolRows = await getPools(factoryAddress, tokens, feeTiers(), snapshotBlock);
  const snapshots = (await Promise.all(
    batches(poolRows, 12).map(async (chunk) => Promise.all(chunk.map((row) => snapshotPool(row.pool, factoryAddress, snapshotBlock)))),
  )).flat().filter(Boolean);
  for (const snapshot of snapshots) {
    const key = `${snapshot.token0.toLowerCase()}:${snapshot.token1.toLowerCase()}`;
    const entry = pairs.get(key) || {
      tokenX: snapshot.token0,
      tokenY: snapshot.token1,
      tokenX_symbol: snapshot.token0_symbol,
      tokenY_symbol: snapshot.token1_symbol,
      factory: factoryAddress,
      pools: [],
    };
    entry.pools.push(snapshot);
    pairs.set(key, entry);
  }
  if (!pairs.size) throw new Error("no non-empty V3 pools discovered; cache was not written");
  const probes = [];
  for (const entry of pairs.values()) {
    for (const snapshot of entry.pools) {
      const decimals = Math.max(0, Math.min(Number(snapshot.token0_decimals) || 0, 18));
      const probe = await quoteProbe(quoter, snapshot, 10n ** BigInt(decimals), snapshotBlock);
      if (probe) probes.push(probe);
    }
  }
  if (!probes.length) {
    throw new Error("no V3 pool returned a QuoterV2 quote; cache was not written");
  }

  const payload = {
    schema_version: 1,
    chain_id: Number(network.chainId),
    network: "avalanche",
    factory: factoryAddress,
    router: routerAddress,
    quoter: quoterAddress,
    aave_pool: aavePoolAddress,
    usdc: usdcAddress,
    rpc_host: rpcHost(),
    block_number: snapshotBlock,
    fetched_at: new Date().toISOString(),
    fee_tiers: feeTiers(),
    quoter_probes: probes,
    pools: [...pairs.values()],
  };
  const file = outputPath();
  fs.mkdirSync(path.dirname(file), { recursive: true });
  const temp = `${file}.tmp`;
  fs.writeFileSync(temp, `${JSON.stringify(payload, null, 2)}\n`, "utf8");
  fs.renameSync(temp, file);
  console.log(JSON.stringify({ output: file, pairCount: payload.pools.length, blockNumber: snapshotBlock, factory: factoryAddress }, null, 2));
}

main().catch((error) => {
  console.error(error.message || error);
  process.exitCode = 1;
});
