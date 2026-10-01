require("@nomicfoundation/hardhat-toolbox");
require("dotenv").config();
const path = require("path");
const { loadChainConfig } = require(path.resolve(__dirname, "../../../scripts/tool/load-chain-config.js"));

const PRIVATE_KEY = process.env.DEPLOYER_PRIVATE_KEY || "";
const FORK_RPC_URL = process.env.AVALANCHE_FORK_RPC_URL || "";
const AVALANCHE_CHAIN = loadChainConfig(43114);

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
        runs: 200,
      },
    },
  },
  networks: {
    hardhat: {
      forking: FORK_RPC_URL
        ? {
            url: FORK_RPC_URL,
            blockNumber: process.env.AVALANCHE_FORK_BLOCK ? Number(process.env.AVALANCHE_FORK_BLOCK) : undefined,
          }
        : undefined,
    },
    avalanche: {
      url: AVALANCHE_CHAIN.rpcUrl,
      accounts: PRIVATE_KEY ? [PRIVATE_KEY] : [],
      chainId: 43114,
    },
  },
};
