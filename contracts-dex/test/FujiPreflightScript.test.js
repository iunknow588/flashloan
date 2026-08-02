const { expect } = require("chai");
const hardhat = require("hardhat");
const {
  addressCheck,
  buildFujiPreflightReport,
  sanitizeError,
  summarizeChecks,
} = require("../scripts/preflight-fuji");

const DEPLOYER = "0x0000000000000000000000000000000000000001";
const MOCK_EXECUTOR = "0x0000000000000000000000000000000000000002";
const AAVE_POOL = "0x0000000000000000000000000000000000000003";
const AAVE_EXECUTOR = "0x0000000000000000000000000000000000000004";
const DYNAMIC_EXECUTOR = "0x0000000000000000000000000000000000000005";
const ROUTER = "0x0000000000000000000000000000000000000006";
const USDC = "0x0000000000000000000000000000000000000007";
const TOKEN = "0x0000000000000000000000000000000000000008";

function fakeHre({ chainId = 43113n, owner = DEPLOYER, paused = false } = {}) {
  const codeAddresses = new Set([
    MOCK_EXECUTOR.toLowerCase(),
    AAVE_POOL.toLowerCase(),
    AAVE_EXECUTOR.toLowerCase(),
    DYNAMIC_EXECUTOR.toLowerCase(),
    ROUTER.toLowerCase(),
    USDC.toLowerCase(),
    TOKEN.toLowerCase(),
  ]);
  const provider = {
    getNetwork: async () => ({ chainId }),
    getBalance: async () => 1000000000000000000n,
    getTransactionCount: async () => 12,
    getCode: async (address) => (codeAddresses.has(String(address).toLowerCase()) ? "0x6000" : "0x"),
  };
  const fakeEthers = new Proxy(hardhat.ethers, {
    get(target, prop) {
      if (prop === "provider") return provider;
      if (prop === "getSigners") return async () => [{ address: DEPLOYER }];
      if (prop === "getContractAt") {
        return async (_abi, address) => ({
          owner: async () => owner,
          paused: async () => paused,
          pool: async () => AAVE_POOL,
          address,
        });
      }
      return target[prop];
    },
  });
  return {
    ethers: fakeEthers,
    artifacts: {
      readArtifact: async () => ({}),
    },
  };
}

function fakeHreWithoutSigner() {
  const base = fakeHre();
  const fakeEthers = new Proxy(base.ethers, {
    get(target, prop) {
      if (prop === "getSigners") return async () => [];
      return target[prop];
    },
  });
  return {
    ...base,
    ethers: fakeEthers,
  };
}

function env(overrides = {}) {
  return {
    FUJI_RPC_URL: "https://token.example/fuji/rpc?key=secret",
    DEPLOYER_PRIVATE_KEY: `0x${"11".repeat(32)}`,
    FUJI_EXECUTION_ENABLED: "true",
    MOCK_EXECUTOR_ADDRESS: MOCK_EXECUTOR,
    AAVE_POOL_ADDRESS: AAVE_POOL,
    AAVE_EXECUTOR_ADDRESS: AAVE_EXECUTOR,
    ONCHAIN_DYNAMIC_AAVE_EXECUTOR_ADDRESS: DYNAMIC_EXECUTOR,
    FUJI_DEX_ROUTER: ROUTER,
    FUJI_USDC: USDC,
    FUJI_ROUNDTRIP_TOKEN: TOKEN,
    ...overrides,
  };
}

describe("Fuji preflight script", function () {
  it("builds a redacted no-broadcast readiness report", async function () {
    const report = await buildFujiPreflightReport(fakeHre(), env());

    expect(report.network).to.equal("fuji");
    expect(report.rpcHost).to.equal("token.example");
    expect(report.deployer).to.equal(DEPLOYER);
    expect(report.summary.ok).to.equal(true);
    expect(report.summary.readyForBroadcast).to.equal(true);
    expect(report.checks.some((item) => item.name === "deployer.nonce" && item.pendingNonce === 12)).to.equal(true);
    expect(report.checks.some((item) => item.name === "contract.MOCK_EXECUTOR_ADDRESS" && item.ownerMatches)).to.equal(true);
    expect(JSON.stringify(report)).to.not.include("secret");
    expect(JSON.stringify(report)).to.not.include("1111111111111111111111111111111111111111111111111111111111111111");
  });

  it("marks broadcast readiness false when execution is not explicitly enabled", async function () {
    const report = await buildFujiPreflightReport(fakeHre(), env({ FUJI_EXECUTION_ENABLED: "false" }));

    expect(report.summary.ok).to.equal(true);
    expect(report.summary.readyForBroadcast).to.equal(false);
    expect(report.summary.warningCount).to.equal(1);
  });

  it("fails owner checks without leaking URLs or private keys", async function () {
    const report = await buildFujiPreflightReport(fakeHre({ owner: "0x0000000000000000000000000000000000000099" }), env());

    expect(report.summary.ok).to.equal(false);
    expect(report.summary.readyForBroadcast).to.equal(false);
    expect(report.checks.filter((item) => item.name.startsWith("contract.") && !item.ok)).to.have.length(3);
    expect(JSON.stringify(report)).to.not.include("key=secret");
  });

  it("returns structured errors when no deployer signer is available", async function () {
    const report = await buildFujiPreflightReport(fakeHreWithoutSigner(), env({ DEPLOYER_PRIVATE_KEY: "" }));

    expect(report.summary.ok).to.equal(false);
    expect(report.deployer).to.equal(null);
    expect(report.checks.some((item) => item.name === "deployer.signer" && item.level === "error")).to.equal(true);
    expect(report.checks.some((item) => item.name === "contract.ownerChecks" && item.level === "error")).to.equal(true);
  });

  it("keeps invalid optional addresses as hard errors", function () {
    const check = addressCheck(hardhat.ethers, { MOCK_EXECUTOR_ADDRESS: "not-an-address" }, "MOCK_EXECUTOR_ADDRESS");

    expect(check.ok).to.equal(false);
    expect(check.level).to.equal("error");
  });

  it("sanitizes error messages", function () {
    const message = sanitizeError(
      new Error(`failed https://token.example/rpc?key=secret 0x${"ab".repeat(32)}`)
    );

    expect(message).to.include("[redacted-url]");
    expect(message).to.include("[redacted-private-key]");
    expect(message).to.not.include("secret");
  });

  it("does not consider warnings broadcast ready", function () {
    const summary = summarizeChecks([{ ok: false, level: "warn" }], true);

    expect(summary.ok).to.equal(true);
    expect(summary.readyForBroadcast).to.equal(false);
  });
});
