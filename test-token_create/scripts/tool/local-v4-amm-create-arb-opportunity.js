const { ethers } = require("../lib/hardhat");
const {
  TOKEN_TOTAL,
  toToken,
  readManifest,
  writeManifest,
  deployOrGetContract,
  deployTokens,
  injectInitialLiquidity,
  injectArbitrageOpportunityLiquidity,
  triangularMetric,
} = require("../lib/local-v4-amm-common");

async function getOrBootstrapTokens(owner) {
  const existing = await readManifest();
  if (existing && existing.tz && existing.ta && existing.tb) {
    return {
      manifest: existing,
      tz: await ethers.getContractAt("TestERC20", existing.tz),
      ta: await ethers.getContractAt("TestERC20", existing.ta),
      tb: await ethers.getContractAt("TestERC20", existing.tb),
    };
  }

  console.log("manifest missing tokens; bootstrapping local token set first");
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
  return { manifest, tz, ta, tb };
}

async function printReserves(label, amm, tz, ta, tb) {
  const tzAddress = await tz.getAddress();
  const taAddress = await ta.getAddress();
  const tbAddress = await tb.getAddress();
  const r1 = await amm.getReserves(tzAddress, taAddress);
  const r2 = await amm.getReserves(taAddress, tbAddress);
  const r3 = await amm.getReserves(tbAddress, tzAddress);
  console.log(`${label}:`);
  console.log(`  tz/ta: ${ethers.formatUnits(r1[0], 18)} tz : ${ethers.formatUnits(r1[1], 18)} ta`);
  console.log(`  ta/tb: ${ethers.formatUnits(r2[0], 18)} ta : ${ethers.formatUnits(r2[1], 18)} tb`);
  console.log(`  tb/tz: ${ethers.formatUnits(r3[0], 18)} tb : ${ethers.formatUnits(r3[1], 18)} tz`);
}

async function printOwnerRetention(owner, tz, ta, tb) {
  for (const [name, token] of [
    ["tz", tz],
    ["ta", ta],
    ["tb", tb],
  ]) {
    const totalSupply = await token.totalSupply();
    const balance = await token.balanceOf(owner.address);
    const pct = (Number(balance) / Number(totalSupply)) * 100;
    console.log(`  owner ${name} balance=${ethers.formatUnits(balance, 18)} (${pct.toFixed(2)}% of total)`);
  }
}

async function main() {
  const [owner] = await ethers.getSigners();
  const { manifest, tz, ta, tb } = await getOrBootstrapTokens(owner);

  const amm = await deployOrGetContract("MockV4AmmPoolManager", manifest.amm, owner);
  if (!manifest.amm) {
    console.log("manifest missing pool manager; injecting base imbalanced liquidity first");
    await injectInitialLiquidity(owner, tz, ta, tb, amm);
    manifest.amm = await amm.getAddress();
    manifest.baseLiquidityInjected = true;
    await writeManifest(manifest);
  }

  const tzAddress = await tz.getAddress();
  const taAddress = await ta.getAddress();
  const tbAddress = await tb.getAddress();

  await printReserves("reserves before", amm, tz, ta, tb);
  const metricBefore = await triangularMetric(amm, tz, ta, tb);
  console.log(`  triangular price metric before=${metricBefore.toFixed(6)}`);
  await printOwnerRetention(owner, tz, ta, tb);

  const alreadyInjected = manifest.arbOpportunity && manifest.arbOpportunity.injected;
  const force = process.env.ROBINHOOD_LOCAL_ARB_OPP_FORCE === "true";
  if (alreadyInjected && !force) {
    console.log("arb opportunity already injected; set ROBINHOOD_LOCAL_ARB_OPP_FORCE=true to inject another layer");
    console.log(`recommended test arb amountIn=${ethers.formatUnits(toToken(100), 18)} tz`);
    return;
  }

  const added = await injectArbitrageOpportunityLiquidity(owner, tz, ta, tb, amm);
  manifest.arbOpportunity = {
    injected: true,
    amountIn: toToken(100).toString(),
    added: {
      tzTa: {
        tz: added.tzTa.a.toString(),
        ta: added.tzTa.b.toString(),
      },
      taTb: {
        ta: added.taTb.a.toString(),
        tb: added.taTb.b.toString(),
      },
      tbTz: {
        tb: added.tbTz.a.toString(),
        tz: added.tbTz.b.toString(),
      },
    },
  };
  await writeManifest(manifest);

  await printReserves("reserves after", amm, tz, ta, tb);
  const metricAfter = await triangularMetric(amm, tz, ta, tb);
  console.log(`  triangular price metric after=${metricAfter.toFixed(6)}`);
  console.log(`  expected direction: tz -> tb -> ta -> tz`);
  console.log(`  test amountIn=${ethers.formatUnits(toToken(100), 18)} tz`);
  await printOwnerRetention(owner, tz, ta, tb);

  console.log("result: arb opportunity injected. The 80% creation reserve is meant for later liquidity replenishment, not a post-injection floor.");
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
