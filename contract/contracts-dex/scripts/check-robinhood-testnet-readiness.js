const hre = require("hardhat");
const {
  buildRobinhoodTestnetPreflight,
  writeEvidence,
} = require("./preflight-robinhood-testnet");
const {
  deploymentAddress,
  readbackRobinhoodPausedExecutor,
} = require("./readback-robinhood-paused-executor");

function summarize(report, reportPath, deployedExecutor = null) {
  const summary = {
    schemaVersion: "robinhood-testnet-readiness-v1",
    runAt: report.runAt,
    network: hre.network.name,
    expectedChainId: report.expectedChainId,
    chainId: report.rpc.chainId,
    routeRequirements: report.routeRequirements,
    onchainTestPolicy: report.onchainTestPolicy,
    rpcOk: report.rpc.ok,
    deployerConfigured: report.deployer.configured,
    deployerBalanceOk: report.deployer.balanceOk,
    deployerBalanceWei: report.deployer.balanceWei,
    deployerMinBalanceWei: report.deployer.minBalanceWei,
    requiredAdaptersOk: report.adapters
      .filter((adapter) => adapter.required)
      .every((adapter) => adapter.ok),
    optionalAdaptersOk: report.adapters
      .filter((adapter) => !adapter.required)
      .every((adapter) => adapter.ok),
    readyForPausedDeploy: report.readyForPausedDeploy,
    readyForBroadcast: false,
    flashAccountingLiquidity: report.flashAccountingLiquidity,
    blockingReasons: report.blockingReasons,
    reportPath,
  };
  if (deployedExecutor) {
    summary.deployedExecutor = deployedExecutor;
    summary.readyForLatestPausedExecutor = Boolean(deployedExecutor.matchesLocalArtifact);
  }
  return summary;
}

async function deployedExecutorSummary() {
  const address = deploymentAddress();
  if (!address) {
    return {
      configured: false,
      address: null,
      hasCode: false,
      matchesLocalArtifact: false,
      error: "deployment_address_missing",
    };
  }
  try {
    const readback = await readbackRobinhoodPausedExecutor(address);
    return {
      configured: true,
      address,
      hasCode: Boolean(readback.executor.hasCode),
      paused: Boolean(readback.config.paused),
      codeHash: readback.executor.codeHash,
      localArtifactCodeHash: readback.executor.localArtifact?.codeHash || null,
      matchesLocalArtifact: Boolean(readback.executor.matchesLocalArtifact),
    };
  } catch (error) {
    return {
      configured: true,
      address,
      hasCode: false,
      matchesLocalArtifact: false,
      error: error.message || String(error),
    };
  }
}

async function main() {
  const report = await buildRobinhoodTestnetPreflight();
  const reportPath = writeEvidence(report);
  const deployedExecutor = await deployedExecutorSummary();
  const summary = summarize(report, reportPath, deployedExecutor);
  console.log(JSON.stringify(summary, null, 2));
  if (!report.readyForPausedDeploy) {
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
  deployedExecutorSummary,
  summarize,
};
