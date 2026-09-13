const path = require("path");
const { createRequire } = require("module");

const dexPackageJson = path.resolve(__dirname, "../../../contract/robinhood-testnet/DEX/package.json");
const requireFromDex = createRequire(dexPackageJson);

module.exports = requireFromDex("hardhat");
