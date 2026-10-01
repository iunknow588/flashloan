const path = require("path");
const { createRequire } = require("module");

const dexProject = process.env.FLASHLOAN_CONTRACT_PROJECT
  ? path.resolve(process.env.FLASHLOAN_CONTRACT_PROJECT)
  : path.resolve(__dirname, "../../../contract/robinhood-testnet/DEX");
const dexPackageJson = path.join(dexProject, "package.json");
const requireFromDex = createRequire(dexPackageJson);

module.exports = requireFromDex("hardhat");
