const fs = require("fs");
const path = require("path");
const { ethers } = require("../lib/hardhat");

const BASE = path.resolve(__dirname, "../../..");
const INDEX = path.join(BASE, "flashloan", "src", "runtime", "evidence", "robinhood-testnet", "v4_pool_index", "latest.json");
const TOKENS = path.join(BASE, "flashloan", "src", "chains", "robinhood-testnet", "robinhood_stock_tokens.json");
const ROUTER = "0x8876789976dEcBfCbBbe364623C63652db8C0904";
const QUOTER = "0x8dc178efb8111bb0973dd9d722ebeff267c98f94";
const PERMIT2 = "0x000000000022D473030F116dDEE9F6B43aC78BA3";
const ZERO = ethers.ZeroAddress;
const WETH = "0x7943e237c7F95DA44E0301572D358911207852Fa";
const PM = "0x8366a39CC670B4001A1121B8F6A443A643e40951";
const ERC20 = [
  "function balanceOf(address) view returns (uint256)",
  "function decimals() view returns (uint8)",
  "function allowance(address,address) view returns (uint256)",
  "function approve(address,uint256) returns (bool)",
];
const PERMIT2_ABI = ["function approve(address,address,uint160,uint48)"];
const QUOTER_ABI = [
  "function quoteExactInputSingle((tuple(address currency0,address currency1,uint24 fee,int24 tickSpacing,address hooks) poolKey,bool zeroForOne,uint128 exactAmount,bytes hookData) params) returns (uint256 amountOut,uint256 gasEstimate)",
];
const PM_ABI = ["function extsload(bytes32) view returns (bytes32)"];
const ROUTER_ABI = ["function execute(bytes commands,bytes[] inputs,uint256 deadline) payable"];

const STOCKS = {};
for (const row of Object.values(JSON.parse(fs.readFileSync(TOKENS, "utf8")).tokens || {})) {
  if (row && row.symbol && row.address) STOCKS[String(row.symbol).toUpperCase()] = ethers.getAddress(row.address);
}

function arg(name, fallback) {
  const i = process.argv.indexOf(`--${name}`);
  return i >= 0 ? process.argv[i + 1] : fallback;
}
function flag(name) { return process.argv.includes(`--${name}`); }
function sortPair(a, b) { return BigInt(a.toLowerCase()) < BigInt(b.toLowerCase()) ? [a, b] : [b, a]; }
function poolId(key) { return ethers.keccak256(ethers.AbiCoder.defaultAbiCoder().encode(["tuple(address,address,uint24,int24,address)"], [[key.currency0, key.currency1, key.fee, key.tickSpacing, key.hooks]])); }
function json(value) { return JSON.stringify(value, (_, v) => typeof v === "bigint" ? v.toString() : v, 2); }

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
    const a = String(p.currency0).toLowerCase(); const b = String(p.currency1).toLowerCase(); const t = token.toLowerCase();
    return (a === t || b === t) && (a === ZERO.toLowerCase() || b === ZERO.toLowerCase() || a === WETH.toLowerCase() || b === WETH.toLowerCase());
  });
  const quoter = new ethers.Contract(QUOTER, QUOTER_ABI, ethers.provider);
  const pm = new ethers.Contract(PM, PM_ABI, ethers.provider);
  const out = [];
  for (const p of rows) {
    const key = { currency0: ethers.getAddress(p.currency0), currency1: ethers.getAddress(p.currency1), fee: Number(p.fee), tickSpacing: Number(p.tickSpacing), hooks: ethers.getAddress(p.hooks || ZERO) };
    try {
      const state = await liveState(pm, p.poolId);
      if (state.liquidity === 0n) continue;
      const zeroForOne = token.toLowerCase() === key.currency0.toLowerCase();
      const [amountOut, gasEstimate] = await quoter.quoteExactInputSingle.staticCall({ poolKey: key, zeroForOne, exactAmount: amountIn, hookData: "0x" }, { gasLimit: 30000000 });
      if (amountOut > 0n) out.push({ poolId: p.poolId, key, zeroForOne, amountOut, gasEstimate, state });
    } catch (_) {}
  }
  out.sort((a, b) => a.amountOut > b.amountOut ? -1 : 1);
  return { token, amountIn, quotes: out };
}

async function main() {
  const symbol = String(arg("symbol", "")).toUpperCase();
  const amountText = arg("amount", "10");
  if (!symbol) throw new Error("--symbol is required");
  const [wallet] = await ethers.getSigners();
  const { token, amountIn, quotes } = await findQuotes(symbol, amountText);
  if (!quotes.length) throw new Error(`${symbol}: no live ETH/WETH V4 pool returned a quote`);
  const best = quotes[0];
  console.log(json({ network: "Robinhood Testnet", chainId: 46630, wallet: wallet.address, symbol, token, amountIn, quoteCount: quotes.length, quotes: quotes.map((q) => ({ poolId: q.poolId, key: q.key, zeroForOne: q.zeroForOne, amountOutWei: q.amountOut, amountOutETH: ethers.formatEther(q.amountOut), gasEstimate: q.gasEstimate, liquidity: q.state.liquidity, tick: q.state.tick })) }));
  if (flag("quote-only")) return;

  const tokenContract = new ethers.Contract(token, ERC20, wallet);
  const beforeToken = await tokenContract.balanceOf(wallet.address);
  const beforeEth = await ethers.provider.getBalance(wallet.address);
  if (beforeToken < amountIn) throw new Error(`${symbol} balance ${ethers.formatUnits(beforeToken, 18)} is below ${amountText}`);
  const minOut = best.amountOut * BigInt(10000 - Number(arg("slippage-bps", "100"))) / 10000n;
  const permit = new ethers.Contract(PERMIT2, PERMIT2_ABI, wallet);
  const max160 = (1n << 160n) - 1n;
  const max48 = (1n << 48n) - 1n;
  const allowance = await tokenContract.allowance(wallet.address, PERMIT2);
  if (allowance < amountIn) {
    const tx = await tokenContract.approve(PERMIT2, ethers.MaxUint256);
    console.log(`approve token->Permit2 ${tx.hash}`);
    await tx.wait();
  }
  const ptx = await permit.approve(token, ROUTER, max160, max48); console.log(`approve Permit2->Router ${ptx.hash}`); await ptx.wait();

  const deadline = BigInt(Math.floor(Date.now() / 1000) + 900);
  const swap = { poolKey: best.key, zeroForOne: best.zeroForOne, amountIn, amountOutMinimum: minOut, hookData: "0x" };
  const coder = ethers.AbiCoder.defaultAbiCoder();
  const target = ROUTER;
  const actions = "0x060c0f";
  const tokenIn = best.zeroForOne ? best.key.currency0 : best.key.currency1;
  const tokenOut = best.zeroForOne ? best.key.currency1 : best.key.currency0;
  const struct = coder.encode(
    ["tuple(tuple(address currency0,address currency1,uint24 fee,int24 tickSpacing,address hooks) poolKey,bool zeroForOne,uint128 amountIn,uint128 amountOutMinimum,bytes hookData)"],
    [swap]
  );
  const params = [struct, coder.encode(["address", "uint128"], [tokenIn, amountIn]), coder.encode(["address", "uint128"], [tokenOut, minOut])];
  const commands = "0x10";
  const inputs = [coder.encode(["bytes", "bytes[]"], [actions, params])];
  const routerIface = new ethers.Interface(ROUTER_ABI);
  const data = routerIface.encodeFunctionData("execute", [commands, inputs, deadline]);
  const router = new ethers.Contract(ROUTER, ROUTER_ABI, wallet);
  await router.execute.staticCall(commands, inputs, deadline, { from: wallet.address });
  const tx = await wallet.sendTransaction({ to: target, data, value: 0 });
  console.log(`swap submitted ${tx.hash}`);
  const receipt = await tx.wait();
  const afterToken = await tokenContract.balanceOf(wallet.address);
  const afterEth = await ethers.provider.getBalance(wallet.address);
  console.log(json({ status: receipt.status === 1 ? "success" : "failed", txHash: receipt.hash, gasUsed: receipt.gasUsed, selectedPool: best, tokenSpent: beforeToken - afterToken, ethDeltaBeforeGas: afterEth - beforeEth, beforeToken, afterToken, beforeEth, afterEth }));
}
main().catch((e) => { console.error(e.shortMessage || e.message || e); process.exitCode = 1; });
