const fs = require("fs");
const path = require("path");
const { ethers } = require("../lib/hardhat");

const BASE = path.resolve(__dirname, "../../..");
const INDEX = path.join(BASE, "flashloan", "src", "runtime", "evidence", "robinhood-testnet", "v4_pool_index", "latest.json");
const TOKENS = path.join(BASE, "flashloan", "src", "chains", "robinhood-testnet", "robinhood_stock_tokens.json");
const QUOTER = "0x8dc178efb8111bb0973dd9d722ebeff267c98f94";
const PM = "0x8366a39CC670B4001A1121B8F6A443A643e40951";
const ZERO = ethers.ZeroAddress;
const WETH = "0x7943e237c7F95DA44E0301572D358911207852Fa";
const QUOTER_ABI = [
  "function quoteExactInputSingle((tuple(address currency0,address currency1,uint24 fee,int24 tickSpacing,address hooks) poolKey,bool zeroForOne,uint128 exactAmount,bytes hookData) params) returns (uint256 amountOut,uint256 gasEstimate)",
];
const PM_ABI = ["function extsload(bytes32) view returns (bytes32)"];

const STOCKS = {};
for (const row of Object.values(JSON.parse(fs.readFileSync(TOKENS, "utf8")).tokens || {})) {
  if (row && row.symbol && row.address) STOCKS[String(row.symbol).toUpperCase()] = ethers.getAddress(row.address);
}

function json(value) {
  return JSON.stringify(value, (_, v) => (typeof v === "bigint" ? v.toString() : v), 2);
}

async function liveState(pm, id) {
  const slot = ethers.keccak256(ethers.concat([id, ethers.zeroPadValue("0x06", 32)]));
  const raw = await pm.extsload(slot);
  const liq = await pm.extsload(ethers.toBeHex(BigInt(slot) + 3n, 32));
  const word = BigInt(raw);
  return { sqrtPriceX96: word & ((1n << 160n) - 1n), tick: Number((word >> 160n) & ((1n << 24n) - 1n)), liquidity: BigInt(liq) & ((1n << 128n) - 1n) };
}

async function findQuotes(symbol, amountText) {
  const token = STOCKS[symbol];
  if (!token) throw new Error(`unknown stock symbol ${symbol}; use AMD, AMZN, NFLX, PLTR or TSLA`);
  const amountIn = ethers.parseUnits(amountText, 18);
  const index = JSON.parse(fs.readFileSync(INDEX, "utf8"));
  const rows = (index.poolKeys || []).filter((p) => {
    const a = String(p.currency0).toLowerCase();
    const b = String(p.currency1).toLowerCase();
    const t = token.toLowerCase();
    return (a === t || b === t) && (a === ZERO.toLowerCase() || b === ZERO.toLowerCase() || a === WETH.toLowerCase() || b === WETH.toLowerCase());
  });
  const quoter = new ethers.Contract(QUOTER, QUOTER_ABI, ethers.provider);
  const pm = new ethers.Contract(PM, PM_ABI, ethers.provider);
  const out = [];
  for (const p of rows) {
    const key = {
      currency0: ethers.getAddress(p.currency0),
      currency1: ethers.getAddress(p.currency1),
      fee: Number(p.fee),
      tickSpacing: Number(p.tickSpacing),
      hooks: ethers.getAddress(p.hooks || ZERO),
    };
    try {
      const state = await liveState(pm, p.poolId);
      if (state.liquidity === 0n) continue;
      const zeroForOne = token.toLowerCase() === key.currency0.toLowerCase();
      const [amountOut, gasEstimate] = await quoter.quoteExactInputSingle.staticCall(
        { poolKey: key, zeroForOne, exactAmount: amountIn, hookData: "0x" },
        { gasLimit: 30000000 }
      );
      if (amountOut > 0n) out.push({ poolId: p.poolId, key, zeroForOne, amountOut, gasEstimate, state });
    } catch (_) {}
  }
  out.sort((a, b) => (a.amountOut > b.amountOut ? -1 : 1));
  return { token, amountIn, quotes: out };
}

module.exports = { STOCKS, ZERO, WETH, findQuotes, json };
