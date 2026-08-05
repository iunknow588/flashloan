require("@nomicfoundation/hardhat-toolbox");
require("dotenv").config();

const PRIVATE_KEY = process.env.DEPLOYER_PRIVATE_KEY || "";

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
      forking: process.env.AVALANCHE_RPC_URL || process.env.AVALANCHE_RPC
        ? {
            url: process.env.AVALANCHE_RPC_URL || process.env.AVALANCHE_RPC,
            blockNumber: process.env.AVALANCHE_FORK_BLOCK ? Number(process.env.AVALANCHE_FORK_BLOCK) : undefined,
          }
        : undefined,
    },
    avalanche: {
      url: process.env.AVALANCHE_RPC_URL || process.env.AVALANCHE_RPC || "https://api.avax.network/ext/bc/C/rpc",
      accounts: PRIVATE_KEY ? [PRIVATE_KEY] : [],
      chainId: 43114,
    },
    fuji: {
      url: process.env.FUJI_RPC_URL || "https://api.avax-test.network/ext/bc/C/rpc",
      accounts: PRIVATE_KEY ? [PRIVATE_KEY] : [],
      chainId: 43113,
    },
  },
};
