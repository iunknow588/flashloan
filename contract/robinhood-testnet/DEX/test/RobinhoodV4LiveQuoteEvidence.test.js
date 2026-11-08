const { expect } = require("chai");
const { ethers } = require("hardhat");
const {
  encodeQuoteCalldata,
  evidenceRows,
  poolKeyAndDirection,
} = require("../scripts/collect-robinhood-v4-live-quote-evidence");

const TZ = "0x05a31b34a1bf8edb08cbfa2e9d860e6d0939313e";
const TB = "0x28cc9a0b94ad34a3a0cb2734c8d58ad95862c6c4";
const TA = "0xb169fe96425783d7ba7a9f905fe8de5f80e3fdc8";

function planFixture() {
  return {
    route: {
      pathText: "TZ -> TB -> TA -> TZ",
    },
    attempt: {
      triggerBlockNumber: 118022639,
    },
    preflightSpec: {
      routeHops: [
        {
          hop: 1,
          tokenIn: TZ,
          tokenOut: TB,
          amountInRaw: "100000000000000",
          minAmountOutRaw: "992013436199234",
          fee: 3000,
          tickSpacing: 60,
          hooks: ethers.ZeroAddress,
          pool: "0x56f5e256ddc14dcc08a5f059cd126cc1d50724fae30ce4a7dfbf553c1f29ef66",
          inputDecimals: 18,
          outputDecimals: 18,
        },
        {
          hop: 2,
          tokenIn: TB,
          tokenOut: TA,
          amountInRaw: "996998428340939",
          minAmountOutRaw: "98903584145185",
          fee: 3000,
          tickSpacing: 60,
          hooks: ethers.ZeroAddress,
          pool: "0xb396fc25e38939d34d68e4ddbbc5b2f1d962a6753a316df25c96bf665e1d27ea",
          inputDecimals: 18,
          outputDecimals: 18,
        },
        {
          hop: 3,
          tokenIn: TA,
          tokenOut: TZ,
          amountInRaw: "99400587080588",
          minAmountOutRaw: "986067188815472",
          fee: 3000,
          tickSpacing: 60,
          hooks: ethers.ZeroAddress,
          pool: "0x47dbe57e46c680c947def5a11bd9c0a6d6b124475dea35e61729fd63118e8157",
          inputDecimals: 18,
          outputDecimals: 18,
        },
      ],
    },
  };
}

describe("Robinhood V4 live quote evidence helpers", function () {
  it("builds quoter calldata for every hop with ordered currency keys", function () {
    const rows = evidenceRows(planFixture());
    expect(rows).to.have.length(3);
    for (const row of rows) {
      expect(row.calldata).to.match(/^0x[0-9a-f]{8}/);
      expect(row.key.currency0).to.be.a("string");
      expect(row.key.currency1).to.be.a("string");
      expect(row.amountInRaw).to.match(/^\d+$/);
      expect(row.inputDecimals).to.equal(18);
      expect(row.outputDecimals).to.equal(18);
    }
  });

  it("keeps the hop direction consistent with the pool key", function () {
    const hops = planFixture().preflightSpec.routeHops;
    const first = poolKeyAndDirection(hops[0]);
    const last = poolKeyAndDirection(hops[2]);
    expect(first.zeroForOne).to.equal(true);
    expect(last.zeroForOne).to.equal(false);
  });

  it("marks missing token hops as calldata blocked", function () {
    const plan = planFixture();
    plan.preflightSpec.routeHops[0].tokenIn = "";
    const rows = evidenceRows(plan);
    expect(rows[0].status).to.equal("calldata_blocked");
    expect(rows[0].ok).to.equal(false);
  });
});
