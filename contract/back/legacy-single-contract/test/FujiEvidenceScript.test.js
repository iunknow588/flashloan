const { expect } = require("chai");
const path = require("path");
const {
  buildBroadcastGate,
  evidencePaths,
  ownerMatchesSigner,
  receiptReport,
  runId,
  sanitizeError,
  toJsonValue,
} = require("../scripts/fuji-evidence");
const {
  buildSteps: buildAaveSteps,
  reportSummary: aaveReportSummary,
} = require("../scripts/execute-aave-payload");
const {
  buildSteps: buildMockFundedSteps,
  reportSummary: mockFundedReportSummary,
} = require("../scripts/execute-payload-mock-funded");

const ADDRESS_A = "0x0000000000000000000000000000000000000001";
const ADDRESS_B = "0x0000000000000000000000000000000000000002";
const ADDRESS_C = "0x0000000000000000000000000000000000000003";

function fakeHre(chainId = 43113n) {
  return {
    network: { name: "fuji" },
    ethers: {
      provider: {
        getNetwork: async () => ({ chainId }),
      },
    },
  };
}

function fakeHreWithSigner({ signer = ADDRESS_A, chainId = 43113n } = {}) {
  return {
    network: { name: "fuji" },
    ethers: {
      getAddress: (address) => address,
      getSigners: async () => [{ address: signer }],
      provider: {
        getNetwork: async () => ({ chainId }),
      },
    },
  };
}

describe("Fuji evidence helpers", function () {
  it("creates deterministic run IDs and evidence paths", function () {
    const id = runId("fuji mock funded", new Date("2026-08-02T12:34:56.789Z"));
    const paths = evidencePaths({
      env: {
        FUJI_RUN_ID: id,
        FUJI_EVIDENCE_DIR: "tmp/evidence",
        TESTNET_TRADE_LOG: "tmp/fuji-trades.jsonl",
      },
      strategy: "ignored",
    });

    expect(id).to.equal("20260802T123456Z_fuji-mock-funded");
    expect(paths.runId).to.equal(id);
    expect(paths.reportPath).to.equal(path.resolve(process.cwd(), "tmp/evidence", id, "report.json"));
    expect(paths.receiptPath).to.equal(path.resolve(process.cwd(), "tmp/evidence", id, "receipt.json"));
    expect(paths.tradeLogPath).to.equal(path.resolve(process.cwd(), "tmp/fuji-trades.jsonl"));
  });

  it("redacts sensitive errors and serializes bigint payloads", function () {
    const error = new Error(`failed https://example.com/rpc?token=secret 0x${"ab".repeat(32)}`);

    expect(sanitizeError(error)).to.equal("failed [redacted-url] [redacted-private-key]");
    expect(toJsonValue({ amount: 12n, nested: [3n] })).to.deep.equal({
      amount: "12",
      nested: ["3"],
    });
  });

  it("keeps receipt summaries next to raw receipt data", function () {
    const report = receiptReport({
      hash: "0xabc",
      blockNumber: 123,
      status: 1,
      gasUsed: 456n,
      toJSON: () => ({ hash: "0xabc", nestedGas: 456n }),
    });

    expect(report.summary).to.deep.equal({
      hash: "0xabc",
      blockNumber: 123,
      status: 1,
      gasUsed: "456",
    });
    expect(toJsonValue(report.raw)).to.deep.equal({ hash: "0xabc", nestedGas: "456" });
  });

  it("requires Fuji chain, explicit gates, static call, freshness, and profit checks", async function () {
    const gate = await buildBroadcastGate({
      hreLike: fakeHre(),
      env: {
        FUJI_EXECUTION_ENABLED: "true",
        FUJI_AAVE_PAYLOAD_BROADCAST_ENABLED: "true",
      },
      strategy: "small-amount",
      intent: ["AAVE_PAYLOAD_BROADCAST_ENABLED", "FUJI_AAVE_PAYLOAD_BROADCAST_ENABLED"],
      staticCallOk: true,
      payloadFresh: true,
      minProfitChecked: true,
    });

    expect(gate.ready).to.equal(true);
    expect(gate.checks.every((item) => item.ok)).to.equal(true);
  });

  it("blocks broadcast outside Fuji or without static-call evidence", async function () {
    const gate = await buildBroadcastGate({
      hreLike: fakeHre(43114n),
      env: {
        FUJI_EXECUTION_ENABLED: "true",
        MOCK_FUNDED_BROADCAST_ENABLED: "true",
      },
      strategy: "mock-funded",
      intent: "MOCK_FUNDED_BROADCAST_ENABLED",
      staticCallOk: false,
      payloadFresh: true,
      minProfitChecked: true,
    });

    expect(gate.ready).to.equal(false);
    expect(gate.checks.find((item) => item.name === "network.chainId").ok).to.equal(false);
    expect(gate.checks.find((item) => item.name === "staticCall.ok").ok).to.equal(false);
  });

  it("blocks broadcast when the executor owner does not match the signer", async function () {
    const gate = await buildBroadcastGate({
      hreLike: fakeHre(),
      env: {
        FUJI_EXECUTION_ENABLED: "true",
        DYNAMIC_SIGNAL_BROADCAST_ENABLED: "true",
      },
      strategy: "small-amount",
      intent: "DYNAMIC_SIGNAL_BROADCAST_ENABLED",
      ownerMatches: false,
      staticCallOk: true,
      payloadFresh: true,
      minProfitChecked: true,
    });

    expect(gate.ready).to.equal(false);
    expect(gate.checks.find((item) => item.name === "owner.matchesSigner").ok).to.equal(false);
  });

  it("compares contract owner with the active signer", async function () {
    const match = await ownerMatchesSigner(fakeHreWithSigner(), { owner: async () => ADDRESS_A });
    const mismatch = await ownerMatchesSigner(fakeHreWithSigner(), { owner: async () => ADDRESS_B });

    expect(match.matches).to.equal(true);
    expect(mismatch.matches).to.equal(false);
  });
});

describe("Fuji payload report summaries", function () {
  it("normalizes mock-funded steps for reports", function () {
    const plan = {
      profitToken: ADDRESS_A,
      minProfit: "1",
      deadlineSeconds: 60,
      steps: [{
        router: ADDRESS_C,
        tokenIn: ADDRESS_A,
        tokenOut: ADDRESS_B,
        amountIn: "100",
        amountOutMin: "90",
        path: [ADDRESS_A, ADDRESS_B],
      }],
    };

    const steps = buildMockFundedSteps(plan);
    const summary = mockFundedReportSummary({
      payloadFile: "payload.json",
      plan,
      steps,
      latest: { number: 10, timestamp: 1000 },
      deadline: 1060n,
      executorAddress: ADDRESS_C,
    });

    expect(summary.deadline).to.equal("1060");
    expect(summary.steps[0].amountIn).to.equal("100");
    expect(summary.steps[0].amountOutMin).to.equal("90");
  });

  it("normalizes Aave payload metadata for static-call reports", function () {
    const aavePayload = {
      borrowAsset: ADDRESS_A,
      borrowAmount: "1000",
      quoteAgeSeconds: 7,
      plan: {
        deadlineSeconds: 30,
        steps: [{
          router: ADDRESS_C,
          tokenIn: ADDRESS_A,
          tokenOut: ADDRESS_B,
          amountIn: "1000",
          amountOutMin: "990",
          path: [ADDRESS_A, ADDRESS_B],
        }],
      },
    };

    const steps = buildAaveSteps(aavePayload);
    const summary = aaveReportSummary({
      payloadFile: "payload.json",
      aavePayload,
      steps,
      latest: { number: 12, timestamp: 2000 },
      deadline: 2030n,
      executorAddress: ADDRESS_C,
    });

    expect(summary.borrowAmount).to.equal("1000");
    expect(summary.quoteAgeSeconds).to.equal(7);
    expect(summary.steps[0].amountOutMin).to.equal("990");
  });
});
