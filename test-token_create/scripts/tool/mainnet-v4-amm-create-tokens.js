const path = require("path");
const fs = require("fs");
const { ethers } = require("../lib/hardhat");

const DECIMALS = 18n;
const TOKEN_TOTAL = 10_000_000n * 10n ** DECIMALS;
const MANIFEST_PATH = process.env.FLASHLOAN_MAINNET_MANIFEST_PATH
  ? path.resolve(process.env.FLASHLOAN_MAINNET_MANIFEST_PATH)
  : path.resolve(__dirname, "../../config/mainnet-v4-amm/manifest.json");

function readManifest() {
  try {
    return JSON.parse(fs.readFileSync(MANIFEST_PATH, "utf8"));
  } catch (error) {
    return null;
  }
}

function writeManifest(manifest) {
  fs.mkdirSync(path.dirname(MANIFEST_PATH), { recursive: true });
  fs.writeFileSync(MANIFEST_PATH, JSON.stringify(manifest, null, 2) + "\n", "utf8");
}

function readArg(name, fallback) {
  const idx = process.argv.indexOf(`--${name}`);
  if (idx >= 0) return process.argv[idx + 1];
  return fallback;
}

function readBoolArg(name, fallback) {
  const value = readArg(name, "");
  if (value) return value === "true" || value === "1" || value === "yes";
  return fallback;
}

async function main() {
  const allowBroadcast = readBoolArg(
    "broadcast",
    process.env.ROBINHOOD_MAINNET_ALLOW_V4_TEST_TOKEN_DEPLOY === "true"
  );
  const [owner] = await ethers.getSigners();
  const existing = readManifest();
  if (existing && existing.tz && existing.ta && existing.tb) {
    console.log("tokens already exist in mainnet manifest, reusing:");
    console.log(`  tz=${existing.tz}`);
    console.log(`  ta=${existing.ta}`);
    console.log(`  tb=${existing.tb}`);
    return;
  }

  console.log("Robinhood mainnet TestERC20 deployment plan");
  console.log(`  deployer=${owner.address}`);
  console.log(`  manifest=${MANIFEST_PATH}`);
  console.log(`  totalSupply each=${ethers.formatUnits(TOKEN_TOTAL, 18)}`);

  if (!allowBroadcast) {
    console.log("broadcast disabled; run with --broadcast or set ROBINHOOD_MAINNET_ALLOW_V4_TEST_TOKEN_DEPLOY=true to deploy");
    return;
  }

  const Token = await ethers.getContractFactory("TestERC20");
  const tz = await Token.deploy("Test Z", "tz", 18, owner.address);
  const ta = await Token.deploy("Test A", "ta", 18, owner.address);
  const tb = await Token.deploy("Test B", "tb", 18, owner.address);

  await tz.waitForDeployment();
  await ta.waitForDeployment();
  await tb.waitForDeployment();

  await (await tz.mint(owner.address, TOKEN_TOTAL)).wait();
  await (await ta.mint(owner.address, TOKEN_TOTAL)).wait();
  await (await tb.mint(owner.address, TOKEN_TOTAL)).wait();

  const manifest = {
    network: "robinhood",
    chainId: (await ethers.provider.getNetwork()).chainId.toString(),
    tz: await tz.getAddress(),
    ta: await ta.getAddress(),
    tb: await tb.getAddress(),
    tokenTotal: TOKEN_TOTAL.toString(),
    deployedAt: new Date().toISOString(),
  };
  writeManifest(manifest);

  console.log("created tokens:");
  console.log(`  tz=${manifest.tz}`);
  console.log(`  ta=${manifest.ta}`);
  console.log(`  tb=${manifest.tb}`);
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
