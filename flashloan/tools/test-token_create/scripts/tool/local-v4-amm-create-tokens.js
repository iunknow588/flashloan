const { ethers } = require("../lib/hardhat");
const {
  TOKEN_TOTAL,
  readManifest,
  writeManifest,
  deployTokens,
} = require("../lib/local-v4-amm-common");

async function main() {
  const [owner] = await ethers.getSigners();
  const existing = await readManifest();
  if (existing && existing.tz && existing.ta && existing.tb) {
    console.log("tokens already exist, reusing manifest:");
    console.log(`  tz=${existing.tz}`);
    console.log(`  ta=${existing.ta}`);
    console.log(`  tb=${existing.tb}`);
    return;
  }

  const { tz, ta, tb } = await deployTokens(owner);
  const manifest = {
    network: "localhost",
    chainId: (await ethers.provider.getNetwork()).chainId.toString(),
    tz: await tz.getAddress(),
    ta: await ta.getAddress(),
    tb: await tb.getAddress(),
    tokenTotal: TOKEN_TOTAL.toString(),
  };
  await writeManifest(manifest);

  console.log("created tokens:");
  console.log(`  tz=${manifest.tz}`);
  console.log(`  ta=${manifest.ta}`);
  console.log(`  tb=${manifest.tb}`);
  console.log(`  totalSupply each=${ethers.formatUnits(TOKEN_TOTAL, 18)}`);
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
