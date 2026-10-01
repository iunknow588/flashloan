require("@nomicfoundation/hardhat-toolbox");
require("dotenv").config();
const path = require("path");
const { loadChainConfig } = require(path.resolve(__dirname, "../../../scripts/tool/load-chain-config.js"));

const PRIVATE_KEY = process.env.DEPLOYER_PRIVATE_KEY || "";
const FUJI_CHAIN = loadChainConfig(43113);

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
      optimizer: { enabled: true, runs: 200 },
    },
  },
  networks: {
    hardhat: {},
    fuji: {
      url: FUJI_CHAIN.rpcUrl,
      accounts: PRIVATE_KEY ? [PRIVATE_KEY] : [],
      chainId: 43113,
    },
  },
};
