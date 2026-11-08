const { expect } = require("chai");
const { ethers } = require("hardhat");
const {
  attachForkEvidenceToPlan,
  buildForkSwaps,
  buildV4FlashRequest,
} = require("../scripts/fork-staticcall-robinhood-v4-route");

const TZ = "0x05a31b34a1bf8edb08cbfa2e9d860e6d0939313e";
const TB = "0x28cc9a0b94ad34a3a0cb2734c8d58ad95862c6c4";
const TA = "0xb169fe96425783d7ba7a9f905fe8de5f80e3fdc8";

function planFixture() {
  return {
    preflightSpec: {
      routeHops: [
        {
          hop: 1,
          tokenIn: TZ,
          tokenOut: TB,
          amountInRaw: "100000000000000",
          minAmountOutRaw: "992013436199234",
          fee: 3000,
          pool: "0x56f5e256ddc14dcc08a5f059cd126cc1d50724fae30ce4a7dfbf553c1f29ef66",
        },
        {
          hop: 2,
          tokenIn: TB,
          tokenOut: TA,
          amountInRaw: "996998428340939",
          minAmountOutRaw: "98903584145185",
          fee: 3000,
          pool: "0xb396fc25e38939d34d68e4ddbbc5b2f1d962a6753a316df25c96bf665e1d27ea",
        },
        {
          hop: 3,
          tokenIn: TA,
          tokenOut: TZ,
          amountInRaw: "99400587080588",
          minAmountOutRaw: "986067188815472",
          fee: 3000,
          pool: "0x47dbe57e46c680c947def5a11bd9c0a6d6b124475dea35e61729fd63118e8157",
        },
      ],
      repaymentConstraint: {
        borrowToken: TZ,
        borrowAmountRaw: "100000000000000",
        finalToken: TZ,
        minProfitRaw: "1",
        gasReserveRaw: "0",
      },
      executionRequest: {
        token: TZ,
        amount: "100000000000000",
        minProfit: "1",
        gasReserve: "0",
      },
    },
    checklist: [
      { id: "calldata_builder", status: "pending", done: false },
      { id: "risk_guard", status: "pending", done: false },
      { id: "fork_staticcall", status: "pending", done: false },
      { id: "broadcast_gate", status: "blocked", done: false },
    ],
  };
}

describe("Robinhood V4 fork static-call evidence helpers", function () {
  it("derives pool ids that match the pinned chain-test plan", function () {
    const built = buildForkSwaps(planFixture());
    expect(built.blockers).to.deep.equal([]);
    expect(built.audit.every((item) => item.poolMatch)).to.equal(true);
    expect(built.swaps).to.have.length(3);
    expect(built.swaps.every((item) => item.amountOutMinimum > 0n)).to.equal(true);
  });

  it("builds a V4 flash accounting request with plan repayment guards", function () {
    const built = buildForkSwaps(planFixture());
    const request = buildV4FlashRequest(
      planFixture(),
      [ethers.ZeroAddress, ethers.ZeroAddress, ethers.ZeroAddress],
      ["0x01", "0x02", "0x03"],
    );
    expect(request.token).to.equal(ethers.getAddress(TZ));
    expect(request.amount).to.equal(100000000000000n);
    expect(request.finalToken).to.equal(ethers.getAddress(TZ));
    expect(request.minProfit).to.equal(1n);
    expect(request.gasReserve).to.equal(0n);
    expect(request.swapTargets).to.have.length(built.swaps.length);
    expect(request.swapCalldatas).to.have.length(built.swaps.length);
  });

  it("attaches passing fork evidence and marks only completed checklist items done", function () {
    const plan = planFixture();
    const updated = attachForkEvidenceToPlan(plan, {
      status: "fork_staticcall_passed",
      passed: true,
      forkedBlockNumber: 118022639,
    });
    expect(updated.forkEvidence.status).to.equal("fork_staticcall_passed");
    expect(updated.simulationEvidence.passed).to.equal(true);
    const byId = Object.fromEntries(updated.checklist.map((item) => [item.id, item]));
    expect(byId.calldata_builder.status).to.equal("done");
    expect(byId.risk_guard.status).to.equal("done");
    expect(byId.fork_staticcall.status).to.equal("done");
    expect(byId.broadcast_gate.status).to.equal("blocked");
    expect(byId.broadcast_gate.done).to.equal(false);
  });
});
