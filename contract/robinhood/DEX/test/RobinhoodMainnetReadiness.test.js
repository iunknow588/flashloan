const fs = require("fs");
const path = require("path");
const { expect } = require("chai");
const { normalizeAddress } = require("../scripts/preflight-robinhood-mainnet");

describe("Robinhood mainnet contract package readiness", function () {
  const chainJson = JSON.parse(
    fs.readFileSync(path.resolve(__dirname, "../config/chain.json"), "utf8").replace(/^\uFEFF/, ""),
  );
  const contractsJson = JSON.parse(
    fs.readFileSync(path.resolve(__dirname, "../config/contracts.json"), "utf8").replace(/^\uFEFF/, ""),
  );

  it("keeps mainnet broadcast and deployment gates closed", function () {
    expect(chainJson.broadcastEnabled).to.equal(false);
    expect(contractsJson.broadcastEnabled).to.equal(false);
    expect(contractsJson.deploymentGate).to.contain("blocked");
  });

  it("marks mainnet V4 addresses as missing placeholders", function () {
    expect(String(chainJson.defaultV4PoolManager || "").trim()).to.equal("");
    expect(String(chainJson.defaultV4Quoter || "").trim()).to.equal("");
    expect(String(contractsJson.v4UniversalRouter || "").trim()).to.equal("");
    expect(String(contractsJson.permit2 || "").trim()).to.equal("");
  });

  it("normalizes empty and valid addresses without throwing", function () {
    expect(normalizeAddress("")).to.equal("");
    expect(normalizeAddress(chainJson.defaultWrappedNative)).to.not.equal("");
  });
});
