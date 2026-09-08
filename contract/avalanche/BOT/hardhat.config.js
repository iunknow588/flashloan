require("@nomicfoundation/hardhat-toolbox");
require("dotenv").config();

const PRIVATE_KEY = process.env.DEPLOYER_PRIVATE_KEY || "";
const FORK_RPC_URL = process.env.AVALANCHE_FORK_RPC_URL || "";

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
      url: process.env.AVALANCHE_RPC_URL || process.env.AVALANCHE_RPC || "https://api.avax.network/ext/bc/C/rpc",
      accounts: PRIVATE_KEY ? [PRIVATE_KEY] : [],
      chainId: 43114,
    },
  },
};
