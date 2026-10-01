require("@nomicfoundation/hardhat-toolbox");
const path = require("path");
const dotenv = require("dotenv");
const { loadChainConfig } = require(path.resolve(__dirname, "../../../scripts/tool/load-chain-config.js"));

dotenv.config({ path: path.resolve(__dirname, ".env"), override: false });
dotenv.config({ path: path.resolve(__dirname, "../../../flashloan/app_runtime/.env"), override: false });

const PRIVATE_KEY = process.env.DEPLOYER_PRIVATE_KEY || process.env.LIQUIDATION_EXECUTION_PRIVATE_KEY || "";
const FUJI_CHAIN = loadChainConfig(43113);
// Forking is an explicit diagnostic mode. The normal test suite uses local mocks
// and must not depend on archive data from a public Fuji RPC endpoint.
const FORK_RPC_URL = process.env.FUJI_FORK_RPC_URL || process.env.AVALANCHE_FORK_RPC_URL;

module.exports = {
  paths: {
    sources: "src",
    tests: "test",
    cache: "cache",
    artifacts: "artifacts",
  },
  solidity: {
    version: "0.8.24",
    settings: { viaIR: true, optimizer: { enabled: true, runs: 1 } },
  },
  networks: {
    hardhat: FORK_RPC_URL ? {
      chainId: 43113,
      forking: {
        url: FORK_RPC_URL,
        ...(process.env.FUJI_FORK_BLOCK_NUMBER ? { blockNumber: Number(process.env.FUJI_FORK_BLOCK_NUMBER) } : {}),
      },
    } : {},
    fuji: {
      url: FUJI_CHAIN.rpcUrl,
      accounts: PRIVATE_KEY ? [PRIVATE_KEY] : [],
      chainId: 43113,
    },
  },
};
