const path = require("path");
const fs = require("fs");
const { ethers } = require("../lib/hardhat");
const poolKeyHelper = require("./v4-official-pool-keys");

const MANIFEST_PATH = process.env.FLASHLOAN_MANIFEST_PATH
  ? path.resolve(process.env.FLASHLOAN_MANIFEST_PATH)
  : path.resolve(__dirname, "../../config/testnet-v4-amm/manifest.json");

function readManifest() {
  return JSON.parse(fs.readFileSync(MANIFEST_PATH, "utf8"));
}

function sortPair(a, b) {
  return BigInt(a.toLowerCase()) < BigInt(b.toLowerCase()) ? [a, b] : [b, a];
}

function makeKey(a, b, fee, tickSpacing, hooks) {
  const [currency0, currency1] = sortPair(a, b);
  return { currency0, currency1, fee, tickSpacing, hooks };
}

function poolId(key) {
  return ethers.keccak256(
    ethers.AbiCoder.defaultAbiCoder().encode(
      ["tuple(address currency0,address currency1,uint24 fee,int24 tickSpacing,address hooks)"],
      [key]
    )
  );
}

function stateSlot(poolIdHex) {
  return ethers.keccak256(ethers.concat([poolIdHex, ethers.zeroPadValue("0x06", 32)]));
}

async function main() {
  const manifest = readManifest();
  const official = manifest.officialV4 || {};
  if (!official.poolManager) {
    throw new Error("officialV4.poolManager missing");
  }
  const [owner] = await ethers.getSigners();
  const poolManager = await ethers.getContractAt(
    ["function extsload(bytes32 slot) external view returns (bytes32)"],
    official.poolManager,
    owner
  );

  const routeAddresses = [manifest.tz, manifest.tb, manifest.ta, manifest.tz];
  const explicitPoolKeysJson = process.env.ROBINHOOD_TESTNET_OFFICIAL_ARB_POOL_KEYS_JSON || "";
  const keys = poolKeyHelper.resolveRoutePoolKeys(
    routeAddresses,
    explicitPoolKeysJson,
    {
      fee: Number(official.fee),
      tickSpacing: Number(official.tickSpacing),
      hooks: official.hooks || ethers.ZeroAddress,
    }
  );

  for (const [index, key] of keys.entries()) {
    const id = poolId(key);
    const slot = stateSlot(id);
    const slot0 = await poolManager.extsload(slot);
    const slot3 = await poolManager.extsload(ethers.toBeHex(BigInt(slot) + 3n));
    const sqrtPriceX96 = BigInt(slot0) & ((1n << 160n) - 1n);
    const liquidity = BigInt(slot3) & ((1n << 128n) - 1n);
    console.log(`pool${index + 1} id=${id}`);
    console.log(`  stateSlot=${slot}`);
    console.log(`  slot0=${slot0}`);
    console.log(`  slot3=${slot3}`);
    console.log(`  sqrtPriceX96=${sqrtPriceX96}`);
    console.log(`  liquidity=${liquidity}`);
  }
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
