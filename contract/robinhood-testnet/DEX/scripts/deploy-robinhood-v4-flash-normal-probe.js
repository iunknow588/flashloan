const fs = require("fs");
const path = require("path");
const hre = require("hardhat");
const {
  buildRobinhoodTestnetPreflight,
  ROBINHOOD_TESTNET_DEFAULTS,
  envBool,
  normalizeAddress,
} = require("./preflight-robinhood-testnet");

function evidenceStamp() {
  const stamp = new Date().toISOString().replace(/[-:]/g, "").replace(/\.(\d+)Z$/, "$1Z");
  return `${stamp}-${process.pid}`;
}

function writeEvidence(report) {
  const dir = path.resolve(
    __dirname,
    "../deployments/evidence",
    `${evidenceStamp()}_${hre.network.name}-robinhood-v4-flash-normal-probe-deploy`,
  );
  fs.mkdirSync(dir, { recursive: true });
  const reportPath = path.join(dir, "report.json");
  fs.writeFileSync(reportPath, `${JSON.stringify(report, null, 2)}\n`, "utf8");
  return reportPath;
}

function repoRelative(filePath) {
  return path.relative(path.resolve(__dirname, "../../.."), filePath).replace(/\\/g, "/");
}

function robinhoodChainPath() {
  return path.resolve(__dirname, "../config/chain.json");
}

function robinhoodContractsPath() {
  return path.resolve(__dirname, "../config/contracts.json");
}

function updateRobinhoodNormalProbeMetadata(report, outputPath, evidencePath) {
  const normalTestnetProbe = {
    address: report.probeAddress,
    deploymentFile: repoRelative(outputPath),
    deploymentAddress: report.probeAddress,
    readyForNormalBroadcast: report.readyForNormalProbeDeploy,
    broadcastAttempted: false,
    measurementReady: true,
    blockingReasons: report.blockingReasons,
  };

  for (const filePath of [robinhoodChainPath(), robinhoodContractsPath()]) {
    let previous = {};
    try {
      previous = JSON.parse(fs.readFileSync(filePath, "utf8").replace(/^\uFEFF/, ""));
    } catch {
      previous = {};
    }
    const previousTestnetV4PoolEvidence = previous.testnetV4PoolEvidence || {};
    const updatedBlockingReasons = Array.isArray(previous.blockingReasons)
      ? previous.blockingReasons.filter((reason) => reason !== "normal_testnet_probe_address_missing")
      : previous.blockingReasons;
    const updatedTestnetV4PoolEvidence = {
      ...previousTestnetV4PoolEvidence,
      executionReady: Boolean(report.probeAddress) && previousTestnetV4PoolEvidence.supportsV4FlashAccountingTests !== false,
    };
    const next = {
      ...previous,
      blockingReasons: updatedBlockingReasons,
      lastNormalTestnetProbeEvidence: repoRelative(evidencePath),
      normalTestnetProbe,
      normal_testnet_probe_address: report.probeAddress,
      normal_testnet_probe_deployment_file: repoRelative(outputPath),
      testnetV4PoolEvidence: updatedTestnetV4PoolEvidence,
    };
    fs.mkdirSync(path.dirname(filePath), { recursive: true });
    fs.writeFileSync(filePath, `${JSON.stringify(next, null, 2)}\n`, "utf8");
  }
}

async function main() {
  const allowDeploy = envBool("ROBINHOOD_TESTNET_ALLOW_NORMAL_PROBE_DEPLOY", false);
  const preflight = await buildRobinhoodTestnetPreflight();
  const network = await hre.ethers.provider.getNetwork();
  const chainId = Number(network.chainId);
  const poolManager = normalizeAddress(
    process.env.ROBINHOOD_TESTNET_V4_POOL_MANAGER || ROBINHOOD_TESTNET_DEFAULTS.defaultV4PoolManager,
  );
  const report = {
    schemaVersion: "robinhood-v4-flash-normal-probe-deploy-v1",
    runAt: new Date().toISOString(),
    network: hre.network.name,
    chainId,
    expectedChainId: 46630,
    policy: {
      mode: "normal_only",
      tokenConservation: true,
      deployRequiresExplicitEnv: "ROBINHOOD_TESTNET_ALLOW_NORMAL_PROBE_DEPLOY=true",
      deployEnabled: allowDeploy,
    },
    poolManager,
    deployer: null,
    probeAddress: null,
    outputPath: null,
    readyForNormalProbeDeploy: false,
    blockingReasons: [],
  };
  let reportPath = "";

  if (chainId !== 46630) report.blockingReasons.push("unexpected_chain_id");
  if (!preflight.readyForPausedDeploy) report.blockingReasons.push("robinhood_testnet_preflight_not_ready");
  if (!poolManager) report.blockingReasons.push("pool_manager_missing");
  if (!allowDeploy) report.blockingReasons.push("normal_probe_deploy_explicit_env_missing");

  const [deployer] = await hre.ethers.getSigners();
  report.deployer = deployer ? deployer.address : null;
  if (!deployer) report.blockingReasons.push("deployer_missing");

  report.readyForNormalProbeDeploy = report.blockingReasons.length === 0;
  if (report.readyForNormalProbeDeploy) {
    const Probe = await hre.ethers.getContractFactory("RobinhoodV4FlashNormalProbe");
    const probe = await Probe.deploy(poolManager);
    await probe.waitForDeployment();
    report.probeAddress = await probe.getAddress();
    const output = {
      schemaVersion: "robinhood-v4-flash-normal-probe-deploy-v1",
      network: hre.network.name,
      chainId,
      poolManager,
      deployer: report.deployer,
      probeAddress: report.probeAddress,
      broadcastEnabled: false,
      normalOnly: true,
    };
    const outputPath = path.resolve(__dirname, "../deployments", `robinhood-v4-flash-normal-probe-${hre.network.name}.json`);
    fs.writeFileSync(outputPath, `${JSON.stringify(output, null, 2)}\n`, "utf8");
    report.outputPath = path.relative(path.resolve(__dirname, "../../.."), outputPath).replace(/\\/g, "/");
    reportPath = writeEvidence(report);
    updateRobinhoodNormalProbeMetadata(report, outputPath, reportPath);
  }

  if (!reportPath) reportPath = writeEvidence(report);
  console.log(JSON.stringify({ ...report, reportPath }, null, 2));
  if (!report.readyForNormalProbeDeploy) process.exitCode = 1;
}

if (require.main === module) {
  main().catch((error) => {
    console.error(error.message || error);
    process.exitCode = 1;
  });
}
