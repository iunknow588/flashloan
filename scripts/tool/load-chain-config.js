const fs = require("fs");
const path = require("path");

const ROOT = path.resolve(__dirname, "../..");
const CHAINS_ROOT = path.join(ROOT, "flashloan", "src", "chains");

function readJson(filePath) {
  return JSON.parse(fs.readFileSync(filePath, "utf8"));
}

const LEGACY_RPC_ENV_BY_CHAIN = {
  4663: [
    "ROBINHOOD_MAINNET_RPC_URL",
    "ROBINHOOD_RPC_URL",
    "ROBINHOOD_RPC",
    "ROBINHOOD_RPCS",
    "ROBINHOOD_RPC_URLS",
    "ROBINHOOD_MAINNET_RPC",
  ],
  46630: [
    "ROBINHOOD_TESTNET_RPC_URL",
    "ROBINHOOD_TESTNET_RPC",
    "ROBINHOOD_TESTNET_RPCS",
    "ROBINHOOD_TESTNET_RPC_URLS",
    "ROBINHOOD_TESTNET_ALCHEMY_RPC_URL",
    "ROBINHOOD_TESTNET_ALCHEMY_RPC",
  ],
  43114: ["AVALANCHE_RPC_URL", "AVALANCHE_RPC"],
  43113: ["FUJI_RPC_URL", "AVALANCHE_FUJI_RPC_URL"],
  56: ["BNB_RPC_URL", "BSC_RPC_URL"],
  137: ["POLYGON_RPC_URL"],
  1: ["ETHEREUM_RPC_URL", "MAINNET_RPC_URL"],
};

function envRpc(chainId) {
  if (process.env.FLASHLOAN_RPC_URL) return process.env.FLASHLOAN_RPC_URL;
  for (const name of LEGACY_RPC_ENV_BY_CHAIN[chainId] || []) {
    if (process.env[name]) return process.env[name];
  }
  return "";
}

function loadChainConfig(chainId) {
  const registry = readJson(path.join(CHAINS_ROOT, "registry.json"));
  const entry = (registry.chains || {})[String(chainId)] || {};
  const directory = entry.directory || String(chainId);
  const configPath = path.join(CHAINS_ROOT, directory, "chain.json");
  const config = readJson(configPath);
  const rpc = config.rpc || {};
  const rpcUrl = envRpc(chainId) || rpc.primary || config.rpc_url || config.officialRpc;
  if (!rpcUrl) {
    throw new Error(`No RPC configured for chain ${chainId} in ${configPath}`);
  }
  return {
    ...config,
    configPath,
    rpcUrl,
    rpcUrls: [rpcUrl, ...(rpc.fallbacks || config.rpc_urls || config.officialRpcUrls || [])]
      .filter(Boolean)
      .filter((value, index, values) => values.indexOf(value) === index),
  };
}

module.exports = { ROOT, CHAINS_ROOT, loadChainConfig };
