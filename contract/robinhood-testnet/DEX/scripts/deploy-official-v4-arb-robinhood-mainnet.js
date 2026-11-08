"use strict";

// Minimal Robinhood mainnet deployment for the latest OfficialV4FlashArb.
// The script is intentionally read-only unless the explicit deploy guard is
// set, so dry-run / preflight checks never send a transaction.
const fs = require("fs");
const path = require("path");
const hre = require("hardhat");
const { matchesArtifactRuntime } = require("../../../../test-token_create/scripts/tool/official-v4-immutable-code");

const MAINNET_CHAIN_ID = 4663;
const DEFAULT_POOL_MANAGER = "0x8366a39CC670B4001A1121B8F6A443A643e40951";
const OUTPUT_PATH = path.resolve(
  __dirname,
  "../../../robinhood/DEX/deployments/official_v4_flash_arb_latest.json",
);

function assertMainnet() {
  const chainId = Number(hre.network.config.chainId || 0);
  if (chainId !== MAINNET_CHAIN_ID) {
    throw new Error(
      `network guard failed: current chainId=${chainId}, expected=${MAINNET_CHAIN_ID}; ` +
        "run under --network robinhood",
    );
  }
}

function writeJsonAtomic(target, obj) {
  fs.mkdirSync(path.dirname(target), { recursive: true });
  const text = JSON.stringify(obj, null, 2) + "\n";
  const tmp = `${target}.tmp`;
  fs.writeFileSync(tmp, text, "utf8");
  fs.renameSync(tmp, target);
}

async function main() {
  assertMainnet();
  const allowDeploy = process.env.ROBINHOOD_MAINNET_ALLOW_OFFICIAL_V4_ARB_DEPLOY === "true";
  const dryRun = process.argv.includes("--dry-run") || !allowDeploy;
  const [signer] = await hre.ethers.getSigners();
  const provider = hre.ethers.provider;
  const latest = await provider.getBlock("latest");
  const balance = await provider.getBalance(signer.address);
  const chain = await provider.getNetwork();
  const artifact = await hre.artifacts.readArtifact("OfficialV4FlashArb");
  const Factory = await hre.ethers.getContractFactory("OfficialV4FlashArb");
  const poolManager = (
    process.env.ROBINHOOD_MAINNET_V4_POOL_MANAGER || DEFAULT_POOL_MANAGER
  ).trim();
  const existing = (process.env.ROBINHOOD_MAINNET_OFFICIAL_V4_ARB_ADDRESS || "").trim();

  console.log("chainId", chain.chainId.toString());
  console.log("block", latest && latest.number);
  console.log("deployer", signer.address);
  console.log("deployerBalanceWei", balance.toString());
  console.log("poolManager", poolManager);
  console.log("artifactDeployedBytes", Math.floor(String(artifact.deployedBytecode || "").length / 2));
  console.log("dryRun", dryRun);

  if (existing) {
    const existingCode = await provider.getCode(existing);
    const existingMatches = matchesArtifactRuntime(
      String(artifact.deployedBytecode || ""),
      String(existingCode || "0x"),
    );
    console.log("existingOfficialV4Arb", existing);
    console.log("existingCodeBytes", existingCode && existingCode !== "0x" ? Math.floor(existingCode.length / 2) : 0);
    console.log("existingMatchesArtifact", existingMatches);
  }

  if (dryRun) {
    try {
      const deployTx = await Factory.getDeployTransaction(poolManager);
      const estimatedGas = await provider.estimateGas(deployTx);
      const feeData = await provider.getFeeData();
      const gasPrice = feeData.maxFeePerGas || feeData.gasPrice || 0n;
      const estimatedWei = estimatedGas * gasPrice;
      console.log("estimatedDeployGas", estimatedGas.toString());
      console.log("gasPriceWei", gasPrice.toString());
      console.log("estimatedDeployCostWei", estimatedWei.toString());
    } catch (error) {
      console.warn("deploy gas estimation skipped:", error.shortMessage || error.message || error);
    }
    console.log("dry-run complete; set ROBINHOOD_MAINNET_ALLOW_OFFICIAL_V4_ARB_DEPLOY=true to deploy");
    return;
  }

  const arb = await Factory.deploy(poolManager);
  await arb.waitForDeployment();
  const arbAddress = await arb.getAddress();
  const deployTx = arb.deploymentTransaction();
  const receipt = deployTx ? await deployTx.wait(1) : null;
  const code = await provider.getCode(arbAddress);
  const matches = matchesArtifactRuntime(String(artifact.deployedBytecode || ""), String(code || "0x"));
  const record = {
    chainId: MAINNET_CHAIN_ID,
    network: "robinhood",
    contract: "OfficialV4FlashArb",
    arb: arbAddress,
    poolManager,
    txHash: deployTx ? deployTx.hash : "",
    blockNumber: receipt ? receipt.blockNumber : null,
    deployedAt: new Date().toISOString(),
    codeBytes: code && code !== "0x" ? Math.floor(code.length / 2) : 0,
    matchesArtifact: matches,
  };
  writeJsonAtomic(OUTPUT_PATH, record);
  console.log("deployedOfficialV4Arb", arbAddress);
  console.log("txHash", record.txHash);
  console.log("blockNumber", record.blockNumber);
  console.log("codeBytes", record.codeBytes);
  console.log("matchesArtifact", matches);
  console.log("record", OUTPUT_PATH);
  if (!matches) {
    throw new Error("deployed code does not match the local OfficialV4FlashArb artifact; refusing to update configs");
  }
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
