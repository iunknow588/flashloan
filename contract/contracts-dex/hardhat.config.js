require("@nomicfoundation/hardhat-toolbox");
const path = require("path");
const dotenv = require("dotenv");

dotenv.config();
dotenv.config({ path: path.resolve(__dirname, "../../flashloan/src_bot/.env"), override: false });
const testEnvPath = path.resolve(__dirname, "../../flashloan/src_bot/.env.test");
const testEnv = dotenv.config({ path: testEnvPath, processEnv: {} }).parsed || {};
const sensitiveEnvMarkers = ["PRIVATE_KEY", "SECRET", "PASSWORD", "DATABASE_URL", "JWT"];
for (const [key, value] of Object.entries(testEnv)) {
  const upperKey = key.toUpperCase();
  const sensitive = sensitiveEnvMarkers.some((marker) => upperKey.includes(marker));
  if (!sensitive && String(value || "").trim()) {
    process.env[key] = value;
  }
}

const FUJI_RPC_URL = process.env.FUJI_RPC_URL || process.env.AVALANCHE_FUJI_RPC_URL;
const AVALANCHE_RPC_URL = process.env.AVALANCHE_RPC_URL || process.env.AVALANCHE_RPC;
const AVALANCHE_FORK_BLOCK_NUMBER = process.env.AVALANCHE_FORK_BLOCK_NUMBER;
const AVALANCHE_FORK_RPC_URL =
  process.env.AVALANCHE_FORK_RPC_URL ||
  (AVALANCHE_FORK_BLOCK_NUMBER ? AVALANCHE_RPC_URL : "");
const PRIVATE_KEY = process.env.DEPLOYER_PRIVATE_KEY;
const FALLBACK_PRIVATE_KEY = process.env.LIQUIDATION_EXECUTION_PRIVATE_KEY || process.env.COW_ORDER_SIGNER_PRIVATE_KEY;
const DEPLOYER_KEYS = PRIVATE_KEY ? [PRIVATE_KEY] : (FALLBACK_PRIVATE_KEY ? [FALLBACK_PRIVATE_KEY] : []);

function hardhatNetworkConfig() {
  if (!AVALANCHE_FORK_RPC_URL) return {};
  const forking = { url: AVALANCHE_FORK_RPC_URL };
  if (AVALANCHE_FORK_BLOCK_NUMBER) {
    forking.blockNumber = Number(AVALANCHE_FORK_BLOCK_NUMBER);
  }
  return {
    chainId: Number(process.env.AVALANCHE_FORK_CHAIN_ID || "43114"),
    hardfork: process.env.AVALANCHE_FORK_HARDFORK || "cancun",
    blockGasLimit: Number(process.env.AVALANCHE_FORK_BLOCK_GAS_LIMIT || "40000000"),
    forking,
  };
}

module.exports = {
  paths: {
    sources: "src",
    tests: "test",
    cache: "cache",
    artifacts: "artifacts",
  },
  solidity: {
    version: "0.8.24",
    settings: {
      viaIR: true,
      optimizer: {
        enabled: true,
        runs: 1,
      },
    },
  },
  networks: {
    hardhat: hardhatNetworkConfig(),
    fuji: {
      url: FUJI_RPC_URL || "http://127.0.0.1:8545",
      accounts: DEPLOYER_KEYS,
      chainId: 43113,
    },
    avalanche: {
      url: AVALANCHE_RPC_URL || FUJI_RPC_URL || "http://127.0.0.1:8545",
      accounts: DEPLOYER_KEYS,
      chainId: 43114,
    },
  },
};
