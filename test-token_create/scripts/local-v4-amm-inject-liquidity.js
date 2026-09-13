const { ethers } = require("./lib/hardhat");
const { readManifest, writeManifest, deployOrGetContract, injectInitialLiquidity } = require("./lib/local-v4-amm-common");

async function main() {
  const [owner] = await ethers.getSigners();
  const manifest = await readManifest();
  if (!manifest || !manifest.tz || !manifest.ta || !manifest.tb) {
    throw new Error("manifest missing token addresses, run local:v4-amm:create-tokens first");
  }

  const tz = await ethers.getContractAt("TestERC20", manifest.tz);
  const ta = await ethers.getContractAt("TestERC20", manifest.ta);
  const tb = await ethers.getContractAt("TestERC20", manifest.tb);
  const amm = await deployOrGetContract("MockV4AmmPoolManager", manifest.amm, owner);

  await injectInitialLiquidity(owner, tz, ta, tb, amm);
  manifest.amm = await amm.getAddress();
  await writeManifest(manifest);

  console.log("v4 amm pool manager:", manifest.amm);
  console.log("imbalanced liquidity injected:");
  const tzAddress = await tz.getAddress();
  const taAddress = await ta.getAddress();
  const tbAddress = await tb.getAddress();
  const r1 = await amm.getReserves(tzAddress, taAddress);
  const r2 = await amm.getReserves(taAddress, tbAddress);
  const r3 = await amm.getReserves(tbAddress, tzAddress);
  console.log(`  tz/ta: ${ethers.formatUnits(r1[0], 18)} tz : ${ethers.formatUnits(r1[1], 18)} ta`);
  console.log(`  ta/tb: ${ethers.formatUnits(r2[0], 18)} ta : ${ethers.formatUnits(r2[1], 18)} tb`);
  console.log(`  tb/tz: ${ethers.formatUnits(r3[0], 18)} tb : ${ethers.formatUnits(r3[1], 18)} tz`);
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
