require("@nomicfoundation/hardhat-toolbox");
const path = require("path");
const dotenv = require("dotenv");
const { loadChainConfig } = require(path.resolve(__dirname, "../../../scripts/tool/load-chain-config.js"));

dotenv.config();
dotenv.config({ path: path.resolve(__dirname, "../../../flashloan/app_runtime/.env"), override: false });
dotenv.config({ path: path.resolve(__dirname, ".env"), override: false });
const testEnvPath = path.resolve(__dirname, "../../../flashloan/app_runtime/.env.test");
const testEnv = dotenv.config({ path: testEnvPath, processEnv: {} }).parsed || {};
const sensitiveEnvMarkers = ["PRIVATE_KEY", "SECRET", "PASSWORD", "DATABASE_URL", "JWT"];
for (const [key, value] of Object.entries(testEnv)) {
  const upperKey = key.toUpperCase();
  const sensitive = sensitiveEnvMarkers.some((marker) => upperKey.includes(marker));
  if (!sensitive && String(value || "").trim()) {
    process.env[key] = value;
  }
}

const ROBINHOOD_CHAIN = loadChainConfig(4663);
const ROBINHOOD_TESTNET_CHAIN = loadChainConfig(46630);
const LOCAL_CHAIN = loadChainConfig(31337);
const ROBINHOOD_RPC_URL = ROBINHOOD_CHAIN.rpcUrl;
const ROBINHOOD_TESTNET_RPC_URL = ROBINHOOD_TESTNET_CHAIN.rpcUrl;
const PRIVATE_KEY = process.env.DEPLOYER_PRIVATE_KEY;
const FALLBACK_PRIVATE_KEY = process.env.LIQUIDATION_EXECUTION_PRIVATE_KEY || process.env.COW_ORDER_SIGNER_PRIVATE_KEY;
const DEPLOYER_KEYS = PRIVATE_KEY ? [PRIVATE_KEY] : (FALLBACK_PRIVATE_KEY ? [FALLBACK_PRIVATE_KEY] : []);

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
    hardhat: {},
    localhost: {
      url: LOCAL_CHAIN.rpcUrl,
      chainId: 31337,
    },
    robinhood: {
      url: ROBINHOOD_RPC_URL,
      accounts: DEPLOYER_KEYS,
      chainId: 4663,
    },
    robinhoodTestnet: {
      url: ROBINHOOD_TESTNET_RPC_URL,
      accounts: DEPLOYER_KEYS,
      chainId: 46630,
    },
  },
};
