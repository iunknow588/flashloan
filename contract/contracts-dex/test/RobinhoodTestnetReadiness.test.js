const { expect } = require("chai");
const fs = require("fs");
const path = require("path");
const { configuredRpcSource, envBool, envNumber, normalizeAddress } = require("../scripts/preflight-robinhood-testnet");
const { summarize } = require("../scripts/check-robinhood-testnet-readiness");
const { codeStatus, localArtifactCodeStatus } = require("../scripts/readback-robinhood-paused-executor");
const {
  assertPausedDeployExplicitConsent,
  assertPausedDeployStageFreeze,
  REQUIRED_PAUSED_DEPLOY_MILESTONE,
} = require("../scripts/deploy-robinhood-paused-executor");
const { summarizePausedRedeployPlan } = require("../scripts/plan-robinhood-paused-redeploy");
const { summarizeStageFreezeReadiness } = require("../scripts/check-robinhood-stage-freeze");

function report(overrides = {}) {
  return {
    runAt: "2026-08-25T00:00:00.000Z",
    expectedChainId: 46630,
    routeRequirements: {
      requireV3Adapter: false,
      requireV4Adapter: true,
    },
    onchainTestPolicy: {
      mode: "normal_only",
      normalFunctionFirst: true,
      abnormalOnchainTestsEnabled: false,
      abnormalOnchainTestsDeferred: true,
      tokenConservation: true,
    },
    rpc: {
      chainId: 46630,
      ok: true,
    },
    deployer: {
      configured: true,
      balanceWei: "1",
      minBalanceWei: "1",
      balanceOk: true,
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
  it("keeps testnet scripts from reading mainnet-scoped Robinhood env names", function () {
    const scriptNames = [
      "preflight-robinhood-testnet.js",
      "deploy-robinhood-paused-executor.js",
      "probe-robinhood-v4-flash-normal.js",
      "readback-robinhood-paused-executor.js",
      "plan-robinhood-paused-redeploy.js",
    ];
    const forbidden = [
      "ROBINHOOD_FLASH_SOURCE_KIND",
      "ROBINHOOD_REQUIRE_V3",
      "ROBINHOOD_FLASH_SOURCE",
      "ROBINHOOD_V3_SWAP_ROUTER",
      "ROBINHOOD_V4_UNIVERSAL_ROUTER",
      "ROBINHOOD_UNIVERSAL_ROUTER",
      "ROBINHOOD_V4_POOL_MANAGER",
      "ROBINHOOD_POOL_MANAGER",
      "ROBINHOOD_PERMIT2",
      "ROBINHOOD_EXECUTOR_ADDRESS",
      "ROBINHOOD_PAUSED_EXECUTOR_ADDRESS",
      "ROBINHOOD_EXECUTOR_DEPLOYMENT_FILE",
      "ROBINHOOD_ALLOW_MAINNET_PAUSED_DEPLOY",
      "ROBINHOOD_PREFLIGHT_STRICT",
    ];
    const sources = scriptNames.map((name) => fs.readFileSync(path.join(__dirname, "..", "scripts", name), "utf8"));

    for (const source of sources) {
      for (const name of forbidden) {
        expect(source).not.to.include(`"${name}"`);
        expect(source).not.to.include(`'${name}'`);
      }
    }
  });

  it("parses boolean env flags conservatively", function () {
    process.env.ROBINHOOD_TEST_FLAG = "1";
    expect(envBool("ROBINHOOD_TEST_FLAG")).to.equal(true);
    process.env.ROBINHOOD_TEST_FLAG = "false";
    expect(envBool("ROBINHOOD_TEST_FLAG", true)).to.equal(false);
    process.env.ROBINHOOD_TEST_FLAG = "maybe";
    expect(envBool("ROBINHOOD_TEST_FLAG", true)).to.equal(true);
    delete process.env.ROBINHOOD_TEST_FLAG;
  });

  it("normalizes non-checksummed mixed-case EVM addresses", function () {
    expect(normalizeAddress("0x8876789976DecbFCbBbe364623C63652db8c0904")).to.equal(
      "0x8876789976dEcBfCbBbe364623C63652db8C0904",
    );
  });

  it("treats zero addresses as missing adapter addresses", function () {
    expect(normalizeAddress("0x0000000000000000000000000000000000000000")).to.equal("");
  });

  it("defaults Robinhood testnet flash source kind to V4 PoolManager kind", function () {
    delete process.env.ROBINHOOD_TESTNET_FLASH_SOURCE_KIND;
    expect(envNumber("ROBINHOOD_TESTNET_FLASH_SOURCE_KIND", 4)).to.equal(4);
    process.env.ROBINHOOD_TESTNET_FLASH_SOURCE_KIND = "1";
    expect(envNumber("ROBINHOOD_TESTNET_FLASH_SOURCE_KIND", 4)).to.equal(1);
    delete process.env.ROBINHOOD_TESTNET_FLASH_SOURCE_KIND;
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

  it("summarizes token-conservative on-chain test policy", function () {
    const summary = summarize(report(), "report.json", {
      configured: true,
      address: "0x1D3Af86E2C5a1aA85429885ADc1BB8e09f3dC17E",
      hasCode: true,
      paused: true,
      matchesLocalArtifact: false,
    });

    expect(summary.onchainTestPolicy.mode).to.equal("normal_only");
    expect(summary.onchainTestPolicy.abnormalOnchainTestsEnabled).to.equal(false);
    expect(summary.onchainTestPolicy.tokenConservation).to.equal(true);
    expect(summary.readyForLatestPausedExecutor).to.equal(false);
    expect(summary.deployedExecutor.matchesLocalArtifact).to.equal(false);
  });

  it("recognizes the official default Robinhood testnet RPC", function () {
    const source = configuredRpcSource("https://rpc.testnet.chain.robinhood.com");
    expect(source.configured).to.equal(true);
    expect(source.host).to.equal("rpc.testnet.chain.robinhood.com");
  });

  it("summarizes deployer balance readiness", function () {
    const summary = summarize(report({
      deployer: {
        configured: true,
        balanceWei: "0",
        minBalanceWei: "1",
        balanceOk: false,
      },
      readyForPausedDeploy: false,
      blockingReasons: ["deployer_balance_below_minimum"],
    }), "report.json");

    expect(summary.deployerConfigured).to.equal(true);
    expect(summary.deployerBalanceOk).to.equal(false);
    expect(summary.deployerMinBalanceWei).to.equal("1");
    expect(summary.blockingReasons).to.deep.equal(["deployer_balance_below_minimum"]);
  });

  it("treats zero adapter addresses as unconfigured in readback reports", async function () {
    const status = await codeStatus("v3SwapRouter", "0x0000000000000000000000000000000000000000");

    expect(status.configured).to.equal(false);
    expect(status.address).to.equal(null);
    expect(status.hasCode).to.equal(false);
    expect(status.codeBytes).to.equal(0);
    expect(status.codeHash).to.equal(null);
  });

  it("loads local executor artifact code status for readback comparison", async function () {
    const status = await localArtifactCodeStatus("RobinhoodPausedFlashLoanExecutor");

    expect(status.available).to.equal(true);
    expect(status.contract).to.equal("RobinhoodPausedFlashLoanExecutor");
    expect(status.codeBytes).to.be.greaterThan(0);
    expect(status.codeHash).to.match(/^0x[0-9a-f]{64}$/);
  });

  it("requires an explicit env flag before Robinhood testnet paused deploy", function () {
    delete process.env.ROBINHOOD_TESTNET_ALLOW_PAUSED_DEPLOY;
    expect(() => assertPausedDeployExplicitConsent(46630)).to.throw("ROBINHOOD_TESTNET_ALLOW_PAUSED_DEPLOY=true");

    process.env.ROBINHOOD_TESTNET_ALLOW_PAUSED_DEPLOY = "true";
    expect(() => assertPausedDeployExplicitConsent(46630)).to.not.throw();
    delete process.env.ROBINHOOD_TESTNET_ALLOW_PAUSED_DEPLOY;
  });

  it("requires a frozen milestone before Robinhood testnet paused deploy", function () {
    delete process.env.ROBINHOOD_TESTNET_DEPLOY_MILESTONE;
    expect(() => assertPausedDeployStageFreeze(46630)).to.throw("ROBINHOOD_TESTNET_DEPLOY_MILESTONE");

    process.env.ROBINHOOD_TESTNET_DEPLOY_MILESTONE = REQUIRED_PAUSED_DEPLOY_MILESTONE;
    expect(() => assertPausedDeployStageFreeze(46630)).to.not.throw();
    delete process.env.ROBINHOOD_TESTNET_DEPLOY_MILESTONE;
  });

  it("summarizes a non-broadcast paused redeploy plan", function () {
    delete process.env.ROBINHOOD_TESTNET_ALLOW_PAUSED_DEPLOY;
    delete process.env.ROBINHOOD_TESTNET_DEPLOY_MILESTONE;
    const plan = summarizePausedRedeployPlan({
      preflight: report(),
      deployedExecutor: {
        configured: true,
        address: "0x1D3Af86E2C5a1aA85429885ADc1BB8e09f3dC17E",
        hasCode: true,
        paused: true,
        matchesLocalArtifact: false,
        redeployNeeded: true,
      },
      budget: {
        deployerConfigured: true,
        balanceCoversEstimatedBudget: true,
      },
    });

    expect(plan.broadcastAttempted).to.equal(false);
    expect(plan.explicitDeployAllowed).to.equal(false);
    expect(plan.stageFreezeApproved).to.equal(false);
    expect(plan.deploymentDeferred).to.equal(true);
    expect(plan.redeployNeeded).to.equal(true);
    expect(plan.technicalReadyForPausedRedeploy).to.equal(true);
    expect(plan.readyForIntentionalPausedRedeploy).to.equal(false);
    expect(plan.deployRequiresExplicitEnv).to.equal("ROBINHOOD_TESTNET_ALLOW_PAUSED_DEPLOY=true");
    expect(plan.stageFreezeContext.purpose).to.include("Freeze the current paused executor");
    expect(plan.stageFreezeContext.releaseCriteria).to.include("paused_redeploy_plan_remains_technically_ready");
  });

  it("summarizes predeploy stage-freeze readiness without authorizing redeploy", function () {
    const redeployPlan = summarizePausedRedeployPlan({
      preflight: report(),
      deployedExecutor: {
        configured: true,
        address: "0x1D3Af86E2C5a1aA85429885ADc1BB8e09f3dC17E",
        hasCode: true,
        paused: true,
        matchesLocalArtifact: false,
        redeployNeeded: true,
      },
      budget: {
        deployerConfigured: true,
        balanceCoversEstimatedBudget: true,
      },
    });
    const stage = summarizeStageFreezeReadiness({
      chainMetadata: {
        chainId: 46630,
        chainKey: "chain:46630",
        marketId: "robinhood-testnet-dex-paused",
        defaultDexChainSelector: false,
        broadcastEnabled: false,
        lastChainTestPlanEvidence: "flashloan/src_bot/runtime/evidence/robinhood-testnet/chain_test/latest/plan.json",
        lastPredeployReadinessEvidence: "flashloan/src_bot/runtime/evidence/robinhood-testnet/predeploy_readiness/latest.json",
        lastLendingLiquidityEvidence: "flashloan/src_bot/runtime/evidence/robinhood-testnet/lending_liquidity/latest.json",
        lastCandidatePoolSupplyEvidence: null,
        onchainTestPolicy: {
          mode: "normal_only",
          abnormalOnchainTestsEnabled: false,
        },
        testnetV4PoolEvidence: {
          supportsV4FlashAccountingTests: true,
          executionReady: true,
        },
        lendingLiquidity: {
          hasVerifiedBorrowableLiquidity: false,
          supportsAaveOrMorphoFlashLoans: false,
        },
        executorOffchainNormalReadiness: {
          readyForPausedRedeploy: true,
          readyForTestnetExecutorNormalProbe: true,
        },
      },
      contractsMetadata: {
        broadcastEnabled: false,
        matchesLocalArtifact: false,
        readyForLatestPausedExecutor: false,
      },
      redeployPlan,
    });

    expect(stage.readyForStageFreeze).to.equal(true);
    expect(stage.deploymentDeferred).to.equal(true);
    expect(stage.readyForIntentionalPausedRedeploy).to.equal(false);
    expect(stage.gates.currentExecutorNeedsRedeploy).to.equal(true);
    expect(stage.gates.notDefaultDexChainSelector).to.equal(true);
    expect(stage.gates.chainTestPlanEvidenceScoped).to.equal(true);
    expect(stage.gates.predeployReadinessEvidenceScoped).to.equal(true);
    expect(stage.gates.lendingLiquidityEvidenceScoped).to.equal(true);
    expect(stage.gates.candidatePoolSupplyEvidenceUnset).to.equal(true);
    expect(stage.gates.aaveOrMorphoFlashLoansNotClaimedWithoutLiquidity).to.equal(true);
    expect(stage.gates.testnetV4UsablePoolVerified).to.equal(true);
    expect(stage.gates.testnetV4ExecutionReady).to.equal(true);
    expect(stage.stageFreezeContext.purpose).to.include("Freeze the current paused executor");
    expect(stage.stageFreezeContext.manualApprovalRequired).to.equal(true);
    expect(stage.blockingReasons).to.deep.equal([]);
  });

  it("blocks stage freeze when Robinhood testnet V4 pool evidence is not execution-ready", function () {
    const redeployPlan = summarizePausedRedeployPlan({
      preflight: report(),
      deployedExecutor: {
        configured: true,
        address: "0x1D3Af86E2C5a1aA85429885ADc1BB8e09f3dC17E",
        hasCode: true,
        paused: true,
        matchesLocalArtifact: false,
        redeployNeeded: true,
      },
      budget: {
        deployerConfigured: true,
        balanceCoversEstimatedBudget: true,
      },
    });
    const stage = summarizeStageFreezeReadiness({
      chainMetadata: {
        chainId: 46630,
        chainKey: "chain:46630",
        marketId: "robinhood-testnet-dex-paused",
        defaultDexChainSelector: false,
        broadcastEnabled: false,
        lastChainTestPlanEvidence: "flashloan/src_bot/runtime/evidence/robinhood-testnet/chain_test/latest/plan.json",
        lastPredeployReadinessEvidence: "flashloan/src_bot/runtime/evidence/robinhood-testnet/predeploy_readiness/latest.json",
        lastLendingLiquidityEvidence: "flashloan/src_bot/runtime/evidence/robinhood-testnet/lending_liquidity/latest.json",
        lastCandidatePoolSupplyEvidence: null,
        onchainTestPolicy: {
          mode: "normal_only",
          abnormalOnchainTestsEnabled: false,
        },
        testnetV4PoolEvidence: {
          supportsV4FlashAccountingTests: false,
          executionReady: false,
        },
        lendingLiquidity: {
          hasVerifiedBorrowableLiquidity: false,
          supportsAaveOrMorphoFlashLoans: false,
        },
        executorOffchainNormalReadiness: {
          readyForPausedRedeploy: true,
          readyForTestnetExecutorNormalProbe: true,
        },
      },
      contractsMetadata: {
        broadcastEnabled: false,
        matchesLocalArtifact: false,
        readyForLatestPausedExecutor: false,
      },
      redeployPlan,
    });

    expect(stage.readyForStageFreeze).to.equal(false);
    expect(stage.gates.testnetV4UsablePoolVerified).to.equal(false);
    expect(stage.gates.testnetV4ExecutionReady).to.equal(false);
    expect(stage.blockingReasons).to.include("testnet_v4_usable_pool_not_verified");
    expect(stage.blockingReasons).to.include("testnet_v4_execution_not_ready");
  });

  it("blocks stage freeze when Robinhood testnet metadata points at mainnet pool evidence", function () {
    const redeployPlan = summarizePausedRedeployPlan({
      preflight: report(),
      deployedExecutor: {
        configured: true,
        address: "0x1D3Af86E2C5a1aA85429885ADc1BB8e09f3dC17E",
        hasCode: true,
        paused: true,
        matchesLocalArtifact: false,
        redeployNeeded: true,
      },
      budget: {
        deployerConfigured: true,
        balanceCoversEstimatedBudget: true,
      },
    });
    const stage = summarizeStageFreezeReadiness({
      chainMetadata: {
        chainId: 46630,
        chainKey: "chain:46630",
        marketId: "robinhood-testnet-dex-paused",
        defaultDexChainSelector: false,
        broadcastEnabled: false,
        lastChainTestPlanEvidence: "flashloan/src_bot/runtime/evidence/robinhood/chain_test/latest/plan.json",
        lastPredeployReadinessEvidence: "flashloan/src_bot/runtime/evidence/robinhood/predeploy_readiness/latest.json",
        lastLendingLiquidityEvidence: "flashloan/src_bot/runtime/evidence/robinhood/lending_liquidity/latest.json",
        lastCandidatePoolSupplyEvidence:
          "flashloan/src_bot/runtime/evidence/robinhood/candidate_pool_supply/latest/report.json",
        onchainTestPolicy: {
          mode: "normal_only",
          abnormalOnchainTestsEnabled: false,
        },
        testnetV4PoolEvidence: {
          supportsV4FlashAccountingTests: true,
          executionReady: true,
        },
        lendingLiquidity: {
          hasVerifiedBorrowableLiquidity: false,
          supportsAaveOrMorphoFlashLoans: false,
        },
        executorOffchainNormalReadiness: {
          readyForPausedRedeploy: true,
          readyForTestnetExecutorNormalProbe: true,
        },
      },
      contractsMetadata: {
        broadcastEnabled: false,
        matchesLocalArtifact: false,
        readyForLatestPausedExecutor: false,
      },
      redeployPlan,
    });

    expect(stage.readyForStageFreeze).to.equal(false);
    expect(stage.gates.chainTestPlanEvidenceScoped).to.equal(false);
    expect(stage.gates.predeployReadinessEvidenceScoped).to.equal(false);
    expect(stage.gates.lendingLiquidityEvidenceScoped).to.equal(false);
    expect(stage.gates.candidatePoolSupplyEvidenceUnset).to.equal(false);
    expect(stage.blockingReasons).to.include("chain_test_plan_evidence_not_robinhood_testnet_scoped");
    expect(stage.blockingReasons).to.include("predeploy_readiness_evidence_not_robinhood_testnet_scoped");
    expect(stage.blockingReasons).to.include("lending_liquidity_evidence_not_robinhood_testnet_scoped");
    expect(stage.blockingReasons).to.include("candidate_pool_supply_evidence_must_be_null_for_testnet");
  });

  it("blocks stage freeze when Aave or Morpho flash loans are claimed without verified testnet liquidity", function () {
    const redeployPlan = summarizePausedRedeployPlan({
      preflight: report(),
      deployedExecutor: {
        configured: true,
        address: "0x1D3Af86E2C5a1aA85429885ADc1BB8e09f3dC17E",
        hasCode: true,
        paused: true,
        matchesLocalArtifact: false,
        redeployNeeded: true,
      },
      budget: {
        deployerConfigured: true,
        balanceCoversEstimatedBudget: true,
      },
    });
    const stage = summarizeStageFreezeReadiness({
      chainMetadata: {
        chainId: 46630,
        chainKey: "chain:46630",
        marketId: "robinhood-testnet-dex-paused",
        defaultDexChainSelector: false,
        broadcastEnabled: false,
        lastChainTestPlanEvidence: "flashloan/src_bot/runtime/evidence/robinhood-testnet/chain_test/latest/plan.json",
        lastPredeployReadinessEvidence: "flashloan/src_bot/runtime/evidence/robinhood-testnet/predeploy_readiness/latest.json",
        lastLendingLiquidityEvidence: "flashloan/src_bot/runtime/evidence/robinhood-testnet/lending_liquidity/latest.json",
        lastCandidatePoolSupplyEvidence: null,
        onchainTestPolicy: {
          mode: "normal_only",
          abnormalOnchainTestsEnabled: false,
        },
        testnetV4PoolEvidence: {
          supportsV4FlashAccountingTests: true,
          executionReady: true,
        },
        lendingLiquidity: {
          hasVerifiedBorrowableLiquidity: false,
          supportsAaveOrMorphoFlashLoans: true,
        },
        executorOffchainNormalReadiness: {
          readyForPausedRedeploy: true,
          readyForTestnetExecutorNormalProbe: true,
        },
      },
      contractsMetadata: {
        broadcastEnabled: false,
        matchesLocalArtifact: false,
        readyForLatestPausedExecutor: false,
      },
      redeployPlan,
    });

    expect(stage.readyForStageFreeze).to.equal(false);
    expect(stage.gates.aaveOrMorphoFlashLoansNotClaimedWithoutLiquidity).to.equal(false);
    expect(stage.blockingReasons).to.include(
      "aave_or_morpho_flashloan_claim_without_verified_testnet_liquidity",
    );
  });
});
