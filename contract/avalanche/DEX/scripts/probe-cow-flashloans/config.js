const { SupportedChainId } = require("@cowprotocol/sdk-config");
const viemChains = require("viem/chains");

const NETWORKS = {
  ethereum: {
    chainId: SupportedChainId.MAINNET,
    chain: viemChains.mainnet,
    envNames: ["ETHEREUM_RPC_URL", "MAINNET_RPC_URL", "RPC_URL"],
    ownerNames: ["COW_OWNER_ETHEREUM", "COW_OWNER_MAINNET"],
  },
  gnosis: {
    chainId: SupportedChainId.GNOSIS_CHAIN,
    chain: viemChains.gnosis,
    envNames: ["GNOSIS_RPC_URL", "XDAI_RPC_URL", "RPC_URL"],
    ownerNames: ["COW_OWNER_GNOSIS", "COW_OWNER_XDAI"],
  },
  arbitrum_one: {
    chainId: SupportedChainId.ARBITRUM_ONE,
    chain: viemChains.arbitrum,
    envNames: ["ARBITRUM_ONE_RPC_URL", "ARBITRUM_RPC_URL", "RPC_URL"],
    ownerNames: ["COW_OWNER_ARBITRUM_ONE", "COW_OWNER_ARBITRUM"],
  },
  base: {
    chainId: SupportedChainId.BASE,
    chain: viemChains.base,
    envNames: ["BASE_RPC_URL", "RPC_URL"],
    ownerNames: ["COW_OWNER_BASE"],
  },
  polygon: {
    chainId: SupportedChainId.POLYGON,
    chain: viemChains.polygon,
    envNames: ["POLYGON_RPC_URL", "MATIC_RPC_URL", "RPC_URL"],
    ownerNames: ["COW_OWNER_POLYGON"],
    defaultRpc: "https://polygon-rpc.com",
  },
  avalanche: {
    chainId: SupportedChainId.AVALANCHE,
    chain: viemChains.avalanche,
    envNames: ["AVALANCHE_RPC_URL", "AVALANCHE_RPC", "RPC_URL"],
    ownerNames: ["COW_OWNER_AVALANCHE"],
    defaultRpc: "https://api.avax.network/ext/bc/C/rpc",
  },
  bnb: {
    chainId: SupportedChainId.BNB,
    chain: viemChains.bsc,
    envNames: ["BNB_RPC_URL", "BSC_RPC_URL", "BINANCE_RPC_URL", "RPC_URL"],
    ownerNames: ["COW_OWNER_BNB", "COW_OWNER_BINANCE"],
    defaultRpc: "https://bsc-dataseed.binance.org",
  },
  linea: {
    chainId: SupportedChainId.LINEA,
    chain: viemChains.linea,
    envNames: ["LINEA_RPC_URL", "RPC_URL"],
    ownerNames: ["COW_OWNER_LINEA"],
  },
  sepolia: {
    chainId: SupportedChainId.SEPOLIA,
    chain: viemChains.sepolia,
    envNames: ["SEPOLIA_RPC_URL", "ETHEREUM_SEPOLIA_RPC_URL", "RPC_URL"],
    ownerNames: ["COW_OWNER_SEPOLIA"],
    defaultRpc: "https://sepolia.drpc.org",
  },
};

const NETWORK_ALIASES = {
  avax: "avalanche",
  "avalanche-c": "avalanche",
  bsc: "bnb",
  binance: "bnb",
  arbitrum: "arbitrum_one",
  arb: "arbitrum_one",
  matic: "polygon",
  mainnet: "ethereum",
  eth: "ethereum",
  ethereum_sepolia: "sepolia",
  testnet: "sepolia",
  xdai: "gnosis",
};

function envFirst(...names) {
  for (const name of names) {
    const value = process.env[name];
    if (value && value.trim() && value.trim() !== "0x...") return value.trim();
  }
  return "";
}

function envBool(name, fallback = false) {
  const raw = envFirst(name);
  if (!raw) return fallback;
  return ["1", "true", "yes", "on"].includes(raw.toLowerCase());
}

function normalizedPrivateKey(value) {
  const raw = String(value || "").trim();
  if (!raw) return "";
  const key = raw.startsWith("0x") ? raw : `0x${raw}`;
  return /^0x[0-9a-fA-F]{64}$/.test(key) ? key : "";
}

function normalizeNetwork(value) {
  const key = String(value || "avalanche").trim().toLowerCase().replace(/-/g, "_");
  return NETWORK_ALIASES[key] || key;
}

function networkConfig(value) {
  const network = normalizeNetwork(value);
  const config = NETWORKS[network];
  if (!config) {
    throw new Error(`unsupported SDK probe network: ${value || network}`);
  }
  return { network, ...config };
}


module.exports = {
  NETWORKS,
  NETWORK_ALIASES,
  envFirst,
  envBool,
  normalizedPrivateKey,
  normalizeNetwork,
  networkConfig,
};
