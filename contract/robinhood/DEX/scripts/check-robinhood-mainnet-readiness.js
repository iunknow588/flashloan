const hre = require("hardhat");
const {
  buildRobinhoodMainnetPreflight,
  writeEvidence,
} = require("./preflight-robinhood-mainnet");

function summarize(report, reportPath) {
  return {
    schemaVersion: "robinhood-mainnet-readiness-v1",
    runAt: report.runAt,
    network: hre.network.name,
    expectedChainId: report.expectedChainId,
    chainId: report.rpc.chainId,
    rpcOk: report.rpc.ok,
    deployerConfigured: report.deployer.configured,
    requiredAdaptersOk: report.adapters
      .filter((adapter) => adapter.required)
      .every((adapter) => adapter.ok),
    readyForPausedDeploy: report.readyForPausedDeploy,
    readyForBroadcast: false,
    broadcastEnabled: false,
    blockingReasons: report.blockingReasons,
    reportPath,
  };
}

async function main() {
  const report = await buildRobinhoodMainnetPreflight();
  const reportPath = writeEvidence(report);
  const summary = summarize(report, reportPath);
  console.log(JSON.stringify(summary, null, 2));
  if (report.blockingReasons.length > 0) process.exitCode = 1;
}

if (require.main === module) {
  main().catch((error) => {
    console.error(error.message || error);
    process.exitCode = 1;
  });
}

module.exports = { summarize };
