const fs = require("fs");
const path = require("path");
const hre = require("hardhat");
const { buildRobinhoodTestnetPreflight } = require("./preflight-robinhood-testnet");
const { deploymentAddress, readbackRobinhoodPausedExecutor } = require("./readback-robinhood-paused-executor");

const REQUIRED_PAUSED_DEPLOY_MILESTONE = "paused_executor_v4_accounting_freeze";
const STAGE_FREEZE_PURPOSE =
  "Freeze the current paused executor ABI/runtime and evidence baseline before any intentional redeploy.";
const STAGE_FREEZE_RELEASE_CRITERIA = [
  "no_additional_executor_abi_changes_planned_for_this_stage",
  "paused_redeploy_plan_remains_technically_ready",
  "readback_and_predeploy_evidence_remain_testnet_scoped",
];

function envBool(name, fallback = false) {
  const value = String(process.env[name] || "").trim().toLowerCase();
  if (!value) return fallback;
  if (["1", "true", "yes", "on"].includes(value)) return true;
  if (["0", "false", "no", "off"].includes(value)) return false;
  return fallback;
}

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

function writeEvidence(report) {
  const dir = path.resolve(
    __dirname,
    "../deployments/evidence",
    `${evidenceStamp()}_${hre.network.name}-robinhood-paused-redeploy-plan`,
  );
  fs.mkdirSync(dir, { recursive: true });
  const reportPath = path.join(dir, "report.json");
  fs.writeFileSync(reportPath, `${JSON.stringify(report, null, 2)}\n`, "utf8");
  return reportPath;
}

async function deployedExecutorPlanSummary() {
  const address = deploymentAddress();
  if (!address) {
    return {
      configured: false,
      address: null,
      hasCode: false,
      paused: null,
      matchesLocalArtifact: false,
      redeployNeeded: true,
      error: "deployment_address_missing",
    };
  }
  try {
    const readback = await readbackRobinhoodPausedExecutor(address);
    const matchesLocalArtifact = Boolean(readback.executor.matchesLocalArtifact);
    return {
      configured: true,
      address,
      hasCode: Boolean(readback.executor.hasCode),
      paused: Boolean(readback.config.paused),
      codeHash: readback.executor.codeHash,
      localArtifactCodeHash: readback.executor.localArtifact?.codeHash || null,
      matchesLocalArtifact,
      redeployNeeded: !matchesLocalArtifact,
    };
  } catch (error) {
    return {
      configured: true,
      address,
      hasCode: false,
      paused: null,
      matchesLocalArtifact: false,
      redeployNeeded: true,
      error: error.message || String(error),
    };
  }
}

async function estimatePausedRedeployBudget() {
  const [deployer] = await hre.ethers.getSigners();
  if (!deployer) {
    return {
      deployerConfigured: false,
      deployer: null,
      balanceWei: null,
      estimatedDeploymentGas: null,
      configGasUnits: null,
      estimatedTotalGasUnits: null,
      gasPriceWei: null,
      safetyBps: null,
      estimatedBudgetWei: null,
      balanceCoversEstimatedBudget: false,
      error: "deployer_key_missing_or_unavailable",
    };
  }

  const Executor = await hre.ethers.getContractFactory("RobinhoodPausedFlashLoanExecutor");
  const deploymentRequest = await Executor.getDeployTransaction(deployer.address);
  const estimatedDeploymentGas = await hre.ethers.provider.estimateGas({
    ...deploymentRequest,
    from: deployer.address,
  });
  const feeData = await hre.ethers.provider.getFeeData();
  const gasPrice = feeData.maxFeePerGas || feeData.gasPrice || 0n;
  const safetyBps = BigInt(process.env.ROBINHOOD_TESTNET_PAUSED_DEPLOY_GAS_SAFETY_BPS || "15000");
  const configGasUnits = BigInt(process.env.ROBINHOOD_TESTNET_PAUSED_DEPLOY_CONFIG_GAS_UNITS || "700000");
  const estimatedTotalGasUnits = estimatedDeploymentGas + configGasUnits;
  const estimatedBudget = (estimatedTotalGasUnits * gasPrice * safetyBps) / 10_000n;
  const balance = await hre.ethers.provider.getBalance(deployer.address);

  return {
    deployerConfigured: true,
    deployer: deployer.address,
    balanceWei: balance,
    estimatedDeploymentGas,
    configGasUnits,
    estimatedTotalGasUnits,
    gasPriceWei: gasPrice,
    safetyBps,
    estimatedBudgetWei: estimatedBudget,
    balanceCoversEstimatedBudget: gasPrice > 0n && balance >= estimatedBudget,
    error: gasPrice > 0n ? null : "gas_price_unavailable",
  };
}

function summarizePausedRedeployPlan({ preflight, deployedExecutor, budget }) {
  const explicitDeployAllowed = envBool("ROBINHOOD_TESTNET_ALLOW_PAUSED_DEPLOY", false);
  const deployMilestone = String(process.env.ROBINHOOD_TESTNET_DEPLOY_MILESTONE || "").trim();
  const stageFreezeApproved = deployMilestone === REQUIRED_PAUSED_DEPLOY_MILESTONE;
  const blockingReasons = [];
  if (!preflight.readyForPausedDeploy) blockingReasons.push("preflight_not_ready_for_paused_deploy");
  if (!deployedExecutor.redeployNeeded) blockingReasons.push("latest_executor_already_deployed");
  if (deployedExecutor.error) blockingReasons.push(deployedExecutor.error);
  if (!budget.deployerConfigured) blockingReasons.push("deployer_key_missing_or_unavailable");
  if (!budget.balanceCoversEstimatedBudget) blockingReasons.push("deployer_balance_below_estimated_redeploy_budget");
  const technicalReadyForPausedRedeploy = Boolean(
    preflight.readyForPausedDeploy
      && deployedExecutor.redeployNeeded
      && budget.deployerConfigured
      && budget.balanceCoversEstimatedBudget
      && blockingReasons.length === 0
  );

  return {
    schemaVersion: "robinhood-paused-redeploy-plan-v1",
    runAt: new Date().toISOString(),
    network: hre.network.name,
    expectedChainId: preflight.expectedChainId,
    chainId: preflight.rpc.chainId,
    broadcastAttempted: false,
    deployRequiresExplicitEnv: "ROBINHOOD_TESTNET_ALLOW_PAUSED_DEPLOY=true",
    explicitDeployAllowed,
    deployMilestone,
    requiredDeployMilestone: REQUIRED_PAUSED_DEPLOY_MILESTONE,
    stageFreezeApproved,
    stageFreezeContext: {
      purpose: STAGE_FREEZE_PURPOSE,
      releaseCriteria: STAGE_FREEZE_RELEASE_CRITERIA,
      manualApprovalRequired: true,
    },
    deploymentDeferred: !stageFreezeApproved,
    preflight: {
      readyForPausedDeploy: Boolean(preflight.readyForPausedDeploy),
      blockingReasons: preflight.blockingReasons,
    },
    deployedExecutor,
    budget,
    redeployNeeded: Boolean(deployedExecutor.redeployNeeded),
    technicalReadyForPausedRedeploy,
    readyForIntentionalPausedRedeploy: Boolean(technicalReadyForPausedRedeploy && stageFreezeApproved),
    blockingReasons: Array.from(new Set(blockingReasons)),
  };
}

async function buildPausedRedeployPlan() {
  const preflight = await buildRobinhoodTestnetPreflight();
  const deployedExecutor = await deployedExecutorPlanSummary();
  const budget = await estimatePausedRedeployBudget();
  return summarizePausedRedeployPlan({ preflight, deployedExecutor, budget });
}

async function main() {
  const report = stringify(await buildPausedRedeployPlan());
  const reportPath = writeEvidence(report);
  console.log(JSON.stringify({ ...report, reportPath }, null, 2));
  if (!report.technicalReadyForPausedRedeploy) {
    process.exitCode = 1;
  }
}

if (require.main === module) {
  main().catch((error) => {
    console.error(error.message || error);
    process.exitCode = 1;
  });
}

module.exports = {
  buildPausedRedeployPlan,
  deployedExecutorPlanSummary,
  estimatePausedRedeployBudget,
  REQUIRED_PAUSED_DEPLOY_MILESTONE,
  STAGE_FREEZE_PURPOSE,
  STAGE_FREEZE_RELEASE_CRITERIA,
  summarizePausedRedeployPlan,
  writeEvidence,
};
