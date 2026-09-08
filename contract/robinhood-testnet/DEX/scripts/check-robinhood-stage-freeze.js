const fs = require("fs");
const path = require("path");
const hre = require("hardhat");
const {
  buildPausedRedeployPlan,
  REQUIRED_PAUSED_DEPLOY_MILESTONE,
  STAGE_FREEZE_PURPOSE,
  STAGE_FREEZE_RELEASE_CRITERIA,
} = require("./plan-robinhood-paused-redeploy");

function evidenceStamp() {
  const stamp = new Date().toISOString().replace(/[-:]/g, "").replace(/\.(\d+)Z$/, "$1Z");
  return `${stamp}-${process.pid}`;
}

function stringify(value) {
  if (typeof value === "bigint") return value.toString();
  if (Array.isArray(value)) return value.map(stringify);
  if (value && typeof value === "object") {
    return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, stringify(item)]));
  }
  return value;
}

function readJson(relativePath) {
  const filePath = path.resolve(__dirname, relativePath);
  return JSON.parse(fs.readFileSync(filePath, "utf8").replace(/^\uFEFF/, ""));
}

function normalizedEvidencePath(value) {
  return String(value || "").trim().replace(/\\/g, "/").toLowerCase();
}

function isRobinhoodTestnetEvidencePath(value, expectedTail) {
  const evidencePath = normalizedEvidencePath(value);
  return Boolean(
    evidencePath &&
      evidencePath.includes("/robinhood-testnet/") &&
      (!expectedTail || evidencePath.endsWith(expectedTail)),
  );
}

function writeEvidence(report) {
  const dir = path.resolve(
    __dirname,
    "../deployments/evidence",
    `${evidenceStamp()}_${hre.network.name}-robinhood-stage-freeze-predeploy`,
  );
  fs.mkdirSync(dir, { recursive: true });
  const reportPath = path.join(dir, "report.json");
  fs.writeFileSync(reportPath, `${JSON.stringify(report, null, 2)}\n`, "utf8");
  return reportPath;
}

function summarizeStageFreezeReadiness({ chainMetadata, contractsMetadata, redeployPlan }) {
  const blockingReasons = [];
  const chainPolicy = chainMetadata.onchainTestPolicy || {};
  const executorReadiness = chainMetadata.executorOffchainNormalReadiness || {};
  const testnetV4PoolEvidence = chainMetadata.testnetV4PoolEvidence || {};
  const lendingLiquidity = chainMetadata.lendingLiquidity || {};
  const redeployBudget = redeployPlan.budget || {};
  const chainTestPlanEvidenceScoped = isRobinhoodTestnetEvidencePath(
    chainMetadata.lastChainTestPlanEvidence,
    "/chain_test/latest/plan.json",
  );
  const predeployReadinessEvidenceScoped = isRobinhoodTestnetEvidencePath(
    chainMetadata.lastPredeployReadinessEvidence,
    "/predeploy_readiness/latest.json",
  );
  const lendingLiquidityEvidenceScoped = isRobinhoodTestnetEvidencePath(
    chainMetadata.lastLendingLiquidityEvidence,
    "/lending_liquidity/latest.json",
  );
  const candidatePoolSupplyEvidenceUnset = !String(chainMetadata.lastCandidatePoolSupplyEvidence || "").trim();

  if (chainMetadata.chainId !== 46630) blockingReasons.push("chain_metadata_not_robinhood_testnet_46630");
  if (chainMetadata.chainKey !== "chain:46630") blockingReasons.push("chain_key_not_robinhood_testnet");
  if (chainMetadata.defaultDexChainSelector !== false) {
    blockingReasons.push("robinhood_testnet_must_not_be_default_dex_chain_selector");
  }
  if (!chainTestPlanEvidenceScoped) blockingReasons.push("chain_test_plan_evidence_not_robinhood_testnet_scoped");
  if (!predeployReadinessEvidenceScoped) {
    blockingReasons.push("predeploy_readiness_evidence_not_robinhood_testnet_scoped");
  }
  if (!lendingLiquidityEvidenceScoped) {
    blockingReasons.push("lending_liquidity_evidence_not_robinhood_testnet_scoped");
  }
  if (!candidatePoolSupplyEvidenceUnset) {
    blockingReasons.push("candidate_pool_supply_evidence_must_be_null_for_testnet");
  }
  if (
    lendingLiquidity.supportsAaveOrMorphoFlashLoans === true &&
    lendingLiquidity.hasVerifiedBorrowableLiquidity !== true
  ) {
    blockingReasons.push("aave_or_morpho_flashloan_claim_without_verified_testnet_liquidity");
  }
  if (chainMetadata.broadcastEnabled !== false) blockingReasons.push("chain_metadata_broadcast_not_disabled");
  if (testnetV4PoolEvidence.supportsV4FlashAccountingTests !== true) {
    blockingReasons.push("testnet_v4_usable_pool_not_verified");
  }
  if (testnetV4PoolEvidence.executionReady !== true) {
    blockingReasons.push("testnet_v4_execution_not_ready");
  }
  if (chainPolicy.mode !== "normal_only") blockingReasons.push("onchain_test_mode_not_normal_only");
  if (chainPolicy.abnormalOnchainTestsEnabled !== false) blockingReasons.push("abnormal_onchain_tests_not_deferred");
  if (executorReadiness.readyForPausedRedeploy !== true) blockingReasons.push("executor_offchain_normal_not_ready");
  if (executorReadiness.readyForTestnetExecutorNormalProbe !== true) {
    blockingReasons.push("executor_testnet_normal_probe_not_ready");
  }
  if (contractsMetadata.broadcastEnabled !== false) blockingReasons.push("contracts_metadata_broadcast_not_disabled");
  if (contractsMetadata.matchesLocalArtifact !== false) blockingReasons.push("current_executor_mismatch_not_recorded");
  if (contractsMetadata.readyForLatestPausedExecutor !== false) blockingReasons.push("latest_executor_gate_not_closed");
  if (redeployPlan.technicalReadyForPausedRedeploy !== true) blockingReasons.push("paused_redeploy_technical_plan_not_ready");
  if (redeployPlan.broadcastAttempted !== false) blockingReasons.push("paused_redeploy_plan_attempted_broadcast");
  if (redeployPlan.deployedExecutor?.paused !== true) blockingReasons.push("current_executor_not_paused");
  if (redeployPlan.deployedExecutor?.matchesLocalArtifact !== false) {
    blockingReasons.push("current_executor_artifact_mismatch_not_confirmed");
  }
  if (redeployPlan.redeployNeeded !== true) blockingReasons.push("paused_redeploy_not_needed");
  if (redeployBudget.balanceCoversEstimatedBudget !== true) blockingReasons.push("redeploy_budget_not_covered");

  const stageFreezeApproved = redeployPlan.deployMilestone === REQUIRED_PAUSED_DEPLOY_MILESTONE;
  const readyForStageFreeze = blockingReasons.length === 0;

  return {
    schemaVersion: "robinhood-stage-freeze-predeploy-v1",
    runAt: new Date().toISOString(),
    network: hre.network.name,
    chainId: chainMetadata.chainId,
    chainKey: chainMetadata.chainKey,
    marketId: chainMetadata.marketId,
    requiredDeployMilestone: REQUIRED_PAUSED_DEPLOY_MILESTONE,
    deployMilestone: redeployPlan.deployMilestone || "",
    stageFreezeApproved,
    stageFreezeContext: {
      purpose: STAGE_FREEZE_PURPOSE,
      releaseCriteria: STAGE_FREEZE_RELEASE_CRITERIA,
      manualApprovalRequired: true,
    },
    readyForStageFreeze,
    deploymentDeferred: !stageFreezeApproved,
    broadcastAttempted: false,
    readyForIntentionalPausedRedeploy: Boolean(readyForStageFreeze && stageFreezeApproved),
    gates: {
      notDefaultDexChainSelector: chainMetadata.defaultDexChainSelector === false,
      chainTestPlanEvidenceScoped,
      predeployReadinessEvidenceScoped,
      lendingLiquidityEvidenceScoped,
      candidatePoolSupplyEvidenceUnset,
      aaveOrMorphoFlashLoansNotClaimedWithoutLiquidity:
        lendingLiquidity.supportsAaveOrMorphoFlashLoans !== true ||
        lendingLiquidity.hasVerifiedBorrowableLiquidity === true,
      testnetV4UsablePoolVerified: testnetV4PoolEvidence.supportsV4FlashAccountingTests === true,
      testnetV4ExecutionReady: testnetV4PoolEvidence.executionReady === true,
      broadcastDisabled: chainMetadata.broadcastEnabled === false && contractsMetadata.broadcastEnabled === false,
      normalOnlyOnchainTesting: chainPolicy.mode === "normal_only" && chainPolicy.abnormalOnchainTestsEnabled === false,
      executorOffchainNormalReady: Boolean(
        executorReadiness.readyForPausedRedeploy && executorReadiness.readyForTestnetExecutorNormalProbe,
      ),
      currentExecutorPaused: redeployPlan.deployedExecutor?.paused === true,
      currentExecutorNeedsRedeploy: Boolean(redeployPlan.redeployNeeded && !redeployPlan.deployedExecutor?.matchesLocalArtifact),
      pausedRedeployTechnicallyReady: redeployPlan.technicalReadyForPausedRedeploy === true,
      redeployBudgetCovered: redeployBudget.balanceCoversEstimatedBudget === true,
    },
    evidence: {
      readiness: chainMetadata.lastReadinessEvidence || null,
      executorOffchainNormal: chainMetadata.lastExecutorOffchainNormalEvidence || null,
      pausedRedeployPlan: redeployPlan.reportPath || chainMetadata.lastPausedRedeployPlanEvidence || null,
      normalTestnetProbe: chainMetadata.lastNormalTestnetProbeEvidence || null,
      chainTestPlan: chainMetadata.lastChainTestPlanEvidence || null,
      predeployReadiness: chainMetadata.lastPredeployReadinessEvidence || null,
      lendingLiquidity: chainMetadata.lastLendingLiquidityEvidence || null,
      candidatePoolSupply: chainMetadata.lastCandidatePoolSupplyEvidence || null,
      readback: chainMetadata.lastReadbackEvidence || null,
    },
    nextManualAction: stageFreezeApproved
      ? "run_paused_deploy_readback_when_intentionally_spending_testnet_eth"
      : "keep_deployment_deferred_until_stage_freeze_is_approved",
    blockingReasons,
  };
}

async function buildStageFreezeReadiness() {
  const chainMetadata = readJson("../config/chain.json");
  const contractsMetadata = readJson("../config/contracts.json");
  const redeployPlan = await buildPausedRedeployPlan();
  return summarizeStageFreezeReadiness({ chainMetadata, contractsMetadata, redeployPlan });
}

async function main() {
  const report = stringify(await buildStageFreezeReadiness());
  const reportPath = writeEvidence(report);
  console.log(JSON.stringify({ ...report, reportPath }, null, 2));
  if (!report.readyForStageFreeze) process.exitCode = 1;
}

if (require.main === module) {
  main().catch((error) => {
    console.error(error.message || error);
    process.exitCode = 1;
  });
}

module.exports = {
  buildStageFreezeReadiness,
  summarizeStageFreezeReadiness,
  writeEvidence,
};
