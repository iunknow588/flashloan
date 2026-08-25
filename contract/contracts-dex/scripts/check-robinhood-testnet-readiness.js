const hre = require("hardhat");
const {
  buildRobinhoodTestnetPreflight,
  writeEvidence,
} = require("./preflight-robinhood-testnet");

function summarize(report, reportPath) {
  return {
    schemaVersion: "robinhood-testnet-readiness-v1",
    runAt: report.runAt,
    network: hre.network.name,
    expectedChainId: report.expectedChainId,
    chainId: report.rpc.chainId,
    routeRequirements: report.routeRequirements,
    rpcOk: report.rpc.ok,
    deployerConfigured: report.deployer.configured,
    requiredAdaptersOk: report.adapters
      .filter((adapter) => adapter.required)
      .every((adapter) => adapter.ok),
    optionalAdaptersOk: report.adapters
      .filter((adapter) => !adapter.required)
      .every((adapter) => adapter.ok),
    readyForPausedDeploy: report.readyForPausedDeploy,
    readyForBroadcast: false,
    blockingReasons: report.blockingReasons,
    reportPath,
  };
}

async function main() {
  const report = await buildRobinhoodTestnetPreflight();
  const reportPath = writeEvidence(report);
  const summary = summarize(report, reportPath);
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
  summarize,
};
