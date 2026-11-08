const { assert } = require("chai");
const { ethers, artifacts } = require("hardhat");
const { matchesArtifactRuntime } = require("../../../../test-token_create/scripts/tool/official-v4-immutable-code.js");

describe("OfficialV4ImmutableCode", function () {
  it("accepts deployed runtime code that only differs in immutable fields", async function () {
    const Composer = await ethers.getContractFactory("ArbitrageComposer");
    const composer = await Composer.deploy(
      "0x8366a39CC670B4001A1121B8F6A443A643e40951",
      "0x9b4FEB127297a4f827522D57c2a9B3D01229d0C0"
    );
    await composer.waitForDeployment();
    const artifact = await artifacts.readArtifact("ArbitrageComposer");
    const onchainCode = await ethers.provider.getCode(await composer.getAddress());
    assert.isTrue(matchesArtifactRuntime(artifact.deployedBytecode, onchainCode));
  });

  it("rejects runtime code with a real byte change", async function () {
    const Composer = await ethers.getContractFactory("ArbitrageComposer");
    const composer = await Composer.deploy(
      "0x8366a39CC670B4001A1121B8F6A443A643e40951",
      "0x9b4FEB127297a4f827522D57c2a9B3D01229d0C0"
    );
    await composer.waitForDeployment();
    const artifact = await artifacts.readArtifact("ArbitrageComposer");
    const onchainCode = await ethers.provider.getCode(await composer.getAddress());
    const tampered = onchainCode.slice(0, 10) + (onchainCode.slice(10, 12) === "00" ? "11" : "00") + onchainCode.slice(12);
    assert.isFalse(matchesArtifactRuntime(artifact.deployedBytecode, tampered));
  });
});
