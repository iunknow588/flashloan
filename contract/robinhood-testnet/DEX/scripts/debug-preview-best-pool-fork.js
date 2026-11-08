"use strict";

// Read-only fork probe: verify OfficialV4FlashArb preview behavior against the
// real testnet PoolManager storage slots. Writes no files and sends no tx.
const hre = require("hardhat");

const ARB = "0x1A8Cc533EaBA93Ec7f68Ac6c428cDa948D7bB8DE";
const POOL_MANAGER = "0x8366a39CC670B4001A1121B8F6A443A643e40951";
const ETH = "0x0000000000000000000000000000000000000000";
const TA = "0x5884ad2f920c162cfbbacc88c9c51aa75ec09e02";
const TB = "0xc9f9c86933092bbbfff3ccb4b105a4a94bf3bd4e";

function poolIdForKey(key) {
  const encoded = hre.ethers.AbiCoder.defaultAbiCoder().encode(
    ["address", "address", "uint24", "int24", "address"],
    [key.currency0, key.currency1, key.fee, key.tickSpacing, key.hooks],
  );
  return hre.ethers.keccak256(encoded);
}

function stateSlot(poolId, index) {
  return hre.ethers.keccak256(
    hre.ethers.concat([poolId, hre.ethers.zeroPadValue(hre.ethers.toBeHex(index), 32)]),
  );
}

async function main() {
  const rpcUrl = process.env.ROBINHOOD_TESTNET_RPC_URL
    || "https://robinhood-sepolia-rpc.publicnode.com";
  await hre.network.provider.request({
    method: "hardhat_reset",
    params: [{ forking: { jsonRpcUrl: rpcUrl } }],
  });
  await hre.network.provider.request({ method: "evm_mine", params: [] });
  const latest = await hre.ethers.provider.getBlock("latest");
  console.log("fork block", latest && latest.number);

  const key = {
    currency0: "0x0000000000000000000000000000000000000000",
    currency1: TA,
    fee: 100,
    tickSpacing: 1,
    hooks: hre.ethers.ZeroAddress,
  };
  const pid = poolIdForKey(key);
  console.log("poolId", pid);
  const pm = await hre.ethers.getContractAt(
    [
      "function extsload(bytes32 slot) external view returns (bytes32)",
      "function extsload(bytes32[] calldata slots) external view returns (bytes32[])",
    ],
    POOL_MANAGER,
  );
  const abiCoder = hre.ethers.AbiCoder.defaultAbiCoder();
  for (const index of [0, 1, 4, 5, 6, 7]) {
    const slot = stateSlot(pid, index);
    try {
      const [value] = await hre.ethers.provider.call({
        to: POOL_MANAGER,
        data: "0x3d9c5c68" + abiCoder.encode(["bytes32"], [slot]).slice(2),
      });
      console.log("single slot", index, value);
    } catch (error) {
      console.log("single slot", index, "ERR", error.reason || error.message);
    }
  }
  try {
    const slots = [0, 1, 4, 5, 6, 7].map((i) => stateSlot(pid, i));
    const selector = "0x";
    const callData = selector + abiCoder.encode(["bytes32[]"], [slots]).slice(2);
    const result = await hre.ethers.provider.call({ to: POOL_MANAGER, data: callData });
    const values = abiCoder.decode(["bytes32[]"], result)[0];
    console.log("batch values", values.map((v) => v));
  } catch (error) {
    console.log("batch ERR", error.reason || error.message);
  }

  const keys = [
    [
      { currency0: ETH, currency1: TA, fee: 100, tickSpacing: 1, hooks: hre.ethers.ZeroAddress },
    ],
    [
      { currency0: TA, currency1: TB, fee: 500, tickSpacing: 5, hooks: hre.ethers.ZeroAddress },
    ],
    [
      { currency0: ETH, currency1: TB, fee: 500, tickSpacing: 10, hooks: hre.ethers.ZeroAddress },
    ],
  ];
  try {
    const IPreview = [
      "function previewBestPoolDetailed(address tokenZ, tuple(address currency0, address currency1, uint24 fee, int24 tickSpacing, address hooks)[] entryKeys, bool[] entryDirections, tuple(address currency0, address currency1, uint24 fee, int24 tickSpacing, address hooks)[] bridgeKeys, bool[] bridgeDirections, tuple(address currency0, address currency1, uint24 fee, int24 tickSpacing, address hooks)[] exitKeys, bool[] exitDirections) view returns (uint256 entryIndex, uint256 bridgeIndex, uint256 exitIndex, uint256 netProduct, int256 netProductDelta, bool profitable)",
    ];
    const arb = await hre.ethers.getContractAt(IPreview, ARB);
    const encoded = arb.interface.encodeFunctionData("previewBestPoolDetailed", [
      ETH,
      keys[0],
      [true],
      keys[1],
      [true],
      keys[2],
      [false],
    ]);
    console.log("encodedPreviewCalldata", encoded);
    const liveResponse = await fetch(rpcUrl, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        jsonrpc: "2.0",
        id: "live-eth-call",
        method: "eth_call",
        params: [{ to: ARB, data: encoded, gas: "0x2faf080" }, "latest"],
      }),
    });
    console.log("live rpc result", (await liveResponse.text()).slice(0, 500));
    const result = await arb.previewBestPoolDetailed.staticCall(
      ETH,
      keys[0],
      [true],
      keys[1],
      [true],
      keys[2],
      [false],
    );
    console.log("previewDetailed raw", result);
  } catch (error) {
    console.log("previewDetailed ERR", error.reason || error.shortMessage || error.message);
    console.log("data", error.data || error.info?.error?.data || error.error?.data || null);
  }
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
