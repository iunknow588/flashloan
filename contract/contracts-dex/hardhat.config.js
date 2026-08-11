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
