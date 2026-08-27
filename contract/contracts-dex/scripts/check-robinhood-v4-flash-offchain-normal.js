const fs = require("fs");
const path = require("path");
const hre = require("hardhat");

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
    `${evidenceStamp()}_${hre.network.name}-robinhood-v4-flash-offchain-normal`,
  );
  fs.mkdirSync(dir, { recursive: true });
  const reportPath = path.join(dir, "report.json");
  fs.writeFileSync(reportPath, `${JSON.stringify(report, null, 2)}\n`, "utf8");
  return reportPath;
}

async function runOffchainNormalReadiness() {
  const [owner] = await hre.ethers.getSigners();
  const Token = await hre.ethers.getContractFactory("TestERC20");
  const token = await Token.deploy("Test USDG", "tUSDG", 6, owner.address);
  const PoolManager = await hre.ethers.getContractFactory("MockV4PoolManager");
  const poolManager = await PoolManager.deploy();
  const Probe = await hre.ethers.getContractFactory("RobinhoodV4FlashNormalProbe");
  const probe = await Probe.deploy(await poolManager.getAddress());
  const amount = BigInt(process.env.ROBINHOOD_OFFCHAIN_NORMAL_FLASH_AMOUNT || "1000");
  const tokenAddress = await token.getAddress();
  const poolManagerAddress = await poolManager.getAddress();
  const probeAddress = await probe.getAddress();

  await token.mint(poolManagerAddress, amount);
  const tx = await probe.runNormalFlash(tokenAddress, amount);
  const receipt = await tx.wait();

  const poolBalance = await token.balanceOf(poolManagerAddress);
  const probeBalance = await token.balanceOf(probeAddress);
  const debt = await poolManager.currencyDebt(probeAddress, tokenAddress);
  const ok = poolBalance === amount && probeBalance === 0n && debt === 0n;

  return {
    schemaVersion: "robinhood-v4-flash-offchain-normal-v1",
    runAt: new Date().toISOString(),
    network: hre.network.name,
    policy: {
      mode: "normal_only",
      tokenConservation: true,
      abnormalCases: "deferred",
    },
    contracts: {
      token: tokenAddress,
      poolManager: poolManagerAddress,
      probe: probeAddress,
    },
    amount,
    receipt: {
      hash: receipt.hash,
      gasUsed: receipt.gasUsed,
    },
    balances: {
      poolManager: poolBalance,
      probe: probeBalance,
    },
    debt,
    readyForTestnetNormalProbe: ok,
    blockingReasons: ok ? [] : ["offchain_normal_flash_accounting_not_settled"],
  };
}

async function main() {
  const report = stringify(await runOffchainNormalReadiness());
  const reportPath = writeEvidence(report);
  console.log(JSON.stringify({ ...report, reportPath }, null, 2));
  if (!report.readyForTestnetNormalProbe) process.exitCode = 1;
}

if (require.main === module) {
  main().catch((error) => {
    console.error(error.message || error);
    process.exitCode = 1;
  });
}

module.exports = {
  runOffchainNormalReadiness,
  writeEvidence,
};
