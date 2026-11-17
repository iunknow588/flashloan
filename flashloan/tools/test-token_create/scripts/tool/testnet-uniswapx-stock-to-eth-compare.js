const fs = require("fs");
const path = require("path");
const { ethers } = require("../lib/hardhat");
const { findQuotes, json } = require("./v4-stock-quote-common");

const TRADE_API = "https://trade-api.gateway.uniswap.org/v1/quote";
const DOC_DEPLOYMENTS = "https://developers.uniswap.org/docs/liquidity/uniswapx/deployments";

function arg(name, fallback) {
  const i = process.argv.indexOf(`--${name}`);
  return i >= 0 ? process.argv[i + 1] : fallback;
}
function flag(name) { return process.argv.includes(`--${name}`); }

async function directV4Quote(symbol, amountText) {
  const { token, amountIn, quotes } = await findQuotes(symbol, amountText);
  if (!quotes.length) throw new Error(`${symbol}: no direct V4 pool quote`);
  return { token, amountIn, best: quotes[0] };
}

async function uniswapxExactOutputQuote(payload, headers) {
  try {
    const res = await fetch(TRADE_API, {
      method: "POST",
      headers: { "content-type": "application/json", accept: "application/json", ...headers },
      body: JSON.stringify(payload),
      signal: AbortSignal.timeout(15000),
    });
    const raw = await res.text();
    let body;
    try { body = JSON.parse(raw); } catch (_) { body = { raw }; }
    return { status: res.status, ok: res.ok, body };
  } catch (error) {
    return { status: 0, ok: false, body: { error: error.name, message: error.message } };
  }
}

async function main() {
  const symbol = String(arg("symbol", "")).toUpperCase();
  const amountText = arg("amount", "10");
  const targetBps = Number(arg("target-bps", "1000"));
  const apiKey = arg("api-key", process.env.UNISWAP_X_API_KEY || "");
  if (!symbol) throw new Error("--symbol is required (AMD, AMZN, NFLX, PLTR, TSLA)");
  const { token, amountIn, best } = await directV4Quote(symbol, amountText);
  const targetOut = (best.amountOut * BigInt(10000 + targetBps)) / 10000n;
  const payload = {
    tokenIn: token,
    tokenOut: "0x0000000000000000000000000000000000000000",
    tokenInChainId: 46630,
    tokenOutChainId: 46630,
    amount: targetOut.toString(),
    type: "EXACT_OUTPUT",
    protocols: ["UNISWAPX_V3"],
    routingPreference: "BEST_PRICE",
    recipient: "0xF9C5525792dF4D70C287b872BeA21C2E44df87Ac",
  };
  const headers = {};
  if (apiKey) headers["x-api-key"] = apiKey;
  const response = await uniswapxExactOutputQuote(payload, headers);
  const result = {
    symbol,
    amountIn,
    directV4: {
      bestPoolId: best.poolId,
      poolKey: best.key,
      directOutputWei: best.amountOut.toString(),
      directOutputETH: ethers.formatEther(best.amountOut),
      liquidity: best.state.liquidity.toString(),
    },
    uniswapxTest: {
      api: TRADE_API,
      type: "EXACT_OUTPUT",
      targetOutputWei: targetOut.toString(),
      targetOutputETH: ethers.formatEther(targetOut),
      targetBpsAboveDirect: targetBps,
      requiresApiKey: apiKey ? false : true,
      officialDeployDocs: DOC_DEPLOYMENTS,
      supportedInDocumentation: false,
    },
    httpStatus: response.status,
    httpOk: response.ok,
    response: response.body,
  };
  console.log(json(result));
  if (!response.ok) {
    console.error(`UniswapX EXACT_OUTPUT +10% test failed for ${symbol}: HTTP ${response.status} ${response.body?.errorCode || response.body?.detail || JSON.stringify(response.body || {}).slice(0, 800)}`);
    process.exitCode = 2;
  }
}

main().catch((e) => { console.error(e.message || e); process.exitCode = 1; });
