const { expect } = require("chai");
const { envBool } = require("../scripts/preflight-robinhood-testnet");
const { summarize } = require("../scripts/check-robinhood-testnet-readiness");

function report(overrides = {}) {
  return {
    runAt: "2026-08-25T00:00:00.000Z",
    expectedChainId: 46630,
    routeRequirements: {
      requireV3Adapter: false,
      requireV4Adapter: true,
    },
    rpc: {
      chainId: 46630,
      ok: true,
    },
    deployer: {
      configured: true,
    },
    adapters: [
      { label: "flashSource", required: true, ok: true },
      { label: "v3SwapRouter", required: false, ok: true },
      { label: "v4UniversalRouter", required: true, ok: true },
      { label: "v4PoolManager", required: true, ok: true },
      { label: "permit2", required: true, ok: true },
    ],
    readyForPausedDeploy: true,
    blockingReasons: [],
    ...overrides,
  };
}

describe("Robinhood testnet readiness helpers", function () {
  it("parses boolean env flags conservatively", function () {
    process.env.ROBINHOOD_TEST_FLAG = "1";
    expect(envBool("ROBINHOOD_TEST_FLAG")).to.equal(true);
    process.env.ROBINHOOD_TEST_FLAG = "false";
    expect(envBool("ROBINHOOD_TEST_FLAG", true)).to.equal(false);
    process.env.ROBINHOOD_TEST_FLAG = "maybe";
    expect(envBool("ROBINHOOD_TEST_FLAG", true)).to.equal(true);
    delete process.env.ROBINHOOD_TEST_FLAG;
  });

  it("summarizes required V3 route gating", function () {
    const summary = summarize(report({
      routeRequirements: {
        requireV3Adapter: true,
        requireV4Adapter: true,
      },
      adapters: [
        { label: "flashSource", required: true, ok: true },
        { label: "v3SwapRouter", required: true, ok: false },
        { label: "v4UniversalRouter", required: true, ok: true },
        { label: "v4PoolManager", required: true, ok: true },
        { label: "permit2", required: true, ok: true },
      ],
      readyForPausedDeploy: false,
      blockingReasons: ["v3SwapRouter_address_missing"],
    }), "report.json");

    expect(summary.routeRequirements.requireV3Adapter).to.equal(true);
    expect(summary.requiredAdaptersOk).to.equal(false);
    expect(summary.readyForPausedDeploy).to.equal(false);
    expect(summary.blockingReasons).to.deep.equal(["v3SwapRouter_address_missing"]);
  });
});
