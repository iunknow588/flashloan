const fs = require("fs");
const path = require("path");
const hre = require("hardhat");
const { deploymentAddress } = require("./readback-robinhood-paused-executor");

const REPO_ROOT = path.resolve(__dirname, "../../..");
const SRC_BOT_ROOT = path.join(REPO_ROOT, "flashloan", "src_bot");
const DEFAULT_CHAIN_TEST_PLAN = path.join(
  SRC_BOT_ROOT,
  "runtime",
  "evidence",
  "robinhood-testnet",
  "chain_test",
  "latest",
  "plan.json",
);
const DEFAULT_V4_POOL_INDEX = path.join(
  SRC_BOT_ROOT,
  "runtime",
  "evidence",
  "robinhood-testnet",
  "v4_pool_index",
  "latest.json",
);

function readJson(filePath) {
  return JSON.parse(fs.readFileSync(filePath, "utf8"));
}

function maybeReadJson(filePath) {
  try {
    return readJson(filePath);
  } catch {
    return {};
  }
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
    `${evidenceStamp()}_${hre.network.name}-robinhood-dynamic-v4-route-preflight`,
  );
  fs.mkdirSync(dir, { recursive: true });
  const reportPath = path.join(dir, "report.json");
  fs.writeFileSync(reportPath, `${JSON.stringify(report, null, 2)}\n`, "utf8");
  return reportPath;
}

function envBool(name, fallback = false) {
  const value = String(process.env[name] || "").trim().toLowerCase();
  if (!value) return fallback;
  if (["1", "true", "yes", "on"].includes(value)) return true;
  if (["0", "false", "no", "off"].includes(value)) return false;
  return fallback;
}

function decodeError(error) {
  const message = error.shortMessage || error.reason || error.message || String(error);
  const data = error.data || error.info?.error?.data || error.error?.data || null;
  const known = new hre.ethers.Interface([
    "error NotOwner()",
    "error Paused()",
    "error InvalidRequest()",
    "error UnsupportedFlashSourceKind(uint8 kind)",
    "error FlashSourceMissing()",
    "error V4AdapterMissing()",
    "error BadFlashCallback()",
    "error BadFlashRepayment(uint256 balance,uint256 requiredBalance)",
    "error CurrencyNotSettled()",
    "error ApproveFailed()",
    "error TransferFailed()",
    "error RouterCallFailed(bytes4 selector)",
    "error Reentrancy()",
    "error ERC20InsufficientBalance(address sender,uint256 balance,uint256 needed)",
    "error ERC20InsufficientAllowance(address spender,uint256 allowance,uint256 needed)",
  ]);
  let decoded = null;
  if (data) {
    try {
      const parsed = known.parseError(data);
      decoded = {
        name: parsed.name,
        args: parsed.args.map((item) => typeof item === "bigint" ? item.toString() : item),
      };
    } catch {
      decoded = null;
    }
  }
  return { message, data, decoded };
}

function normalizeAddress(value, { allowZero = false } = {}) {
  const raw = String(value || "").trim();
  if (!raw || !hre.ethers.isAddress(raw)) return "";
  const address = hre.ethers.getAddress(raw);
  if (!allowZero && address === hre.ethers.ZeroAddress) return "";
  return address;
}

function toBigInt(value) {
  if (typeof value === "bigint") return value;
  const text = String(value || "").trim();
  if (!text) return 0n;
  try {
    return BigInt(text);
  } catch {
    return 0n;
  }
}

function envBigInt(name, fallback = 0n) {
  const value = String(process.env[name] || "").trim();
  if (!value) return fallback;
  try {
    return BigInt(value);
  } catch {
    return fallback;
  }
}

function poolIndexById(poolIndex) {
  const rows = Array.isArray(poolIndex.poolKeys) ? poolIndex.poolKeys : [];
  const result = new Map();
  for (const row of rows) {
    const poolId = String(row.poolId || row.pool || "").trim().toLowerCase();
    if (poolId) result.set(poolId, row);
  }
  return result;
}

function routeHops(plan) {
  const preflight = plan && typeof plan.preflightSpec === "object" ? plan.preflightSpec : {};
  return Array.isArray(preflight.routeHops) ? preflight.routeHops.filter((item) => item && typeof item === "object") : [];
}

function buildSwap(hop, poolKey) {
  const relaxedMinOut = envBool("ROBINHOOD_TESTNET_DYNAMIC_ROUTE_RELAXED_MIN_OUT", true);
  const currency0 = normalizeAddress(poolKey.currency0 || poolKey.token0, { allowZero: true });
  const currency1 = normalizeAddress(poolKey.currency1 || poolKey.token1, { allowZero: true });
  const tokenIn = normalizeAddress(hop.tokenIn, { allowZero: true });
  const tokenOut = normalizeAddress(hop.tokenOut, { allowZero: true });
  const amountIn = toBigInt(hop.amountInRaw);
  const amountOutMinimum = relaxedMinOut ? 1n : toBigInt(hop.minAmountOutRaw || hop.amountOutMinimumRaw);
  const fee = Number(poolKey.fee ?? hop.fee ?? 0);
  const tickSpacing = Number(poolKey.tickSpacing ?? poolKey.tick_spacing ?? 0);
  const hooks = normalizeAddress(poolKey.hooks || hre.ethers.ZeroAddress, { allowZero: true }) || hre.ethers.ZeroAddress;
  const zeroForOne = tokenIn === currency0 && tokenOut === currency1;
  const oneForZero = tokenIn === currency1 && tokenOut === currency0;
  const blockers = [];
  if (!currency0 || !currency1) blockers.push("pool_key_currency_missing");
  if (!tokenIn || !tokenOut) blockers.push("hop_token_missing");
  if (!zeroForOne && !oneForZero) blockers.push("hop_direction_does_not_match_pool_key");
  if (amountIn <= 0n) blockers.push("amount_in_missing_or_zero");
  if (!relaxedMinOut && amountOutMinimum <= 0n) blockers.push("min_amount_out_missing_or_zero");
  if (!Number.isFinite(fee) || fee < 0) blockers.push("invalid_fee");
  if (!Number.isFinite(tickSpacing) || tickSpacing <= 0) blockers.push("invalid_tick_spacing");
  return {
    swap: {
      poolKey: { currency0, currency1, fee, tickSpacing, hooks },
      zeroForOne,
      amountIn,
      amountOutMinimum,
      hookData: "0x",
    },
    audit: {
      hop: hop.hop,
      pool: hop.pool,
      tokenIn,
      tokenOut,
      currency0,
      currency1,
      zeroForOne,
      amountIn,
      amountOutMinimum,
      relaxedMinOut,
      blockers,
    },
  };
}

function nativeSwapValue(hop) {
  return normalizeAddress(hop.tokenIn, { allowZero: true }) === hre.ethers.ZeroAddress ? toBigInt(hop.amountInRaw) : 0n;
}

async function runDynamicRoutePreflight() {
  const planPath = path.resolve(process.env.ROBINHOOD_TESTNET_CHAIN_TEST_PLAN || DEFAULT_CHAIN_TEST_PLAN);
  const poolIndexPath = path.resolve(process.env.ROBINHOOD_TESTNET_V4_POOL_INDEX || DEFAULT_V4_POOL_INDEX);
  const plan = readJson(planPath);
  const poolIndex = readJson(poolIndexPath);
  const executorAddress = normalizeAddress(
    process.env.ROBINHOOD_TESTNET_EXECUTOR_ADDRESS
      || process.env.ROBINHOOD_TESTNET_PAUSED_EXECUTOR_ADDRESS
      || deploymentAddress(),
  );
  const [signer] = await hre.ethers.getSigners();
  const network = await hre.ethers.provider.getNetwork();
  const block = await hre.ethers.provider.getBlock("latest");
  const deadline = BigInt(block.timestamp + Number(process.env.ROBINHOOD_TESTNET_DYNAMIC_ROUTE_DEADLINE_SECONDS || "600"));
  const hops = routeHops(plan);
  const poolMap = poolIndexById(poolIndex);
  const built = hops.map((hop) => buildSwap(hop, poolMap.get(String(hop.pool || "").toLowerCase()) || {}));
  const allBlockers = built.flatMap((item) => item.audit.blockers.map((reason) => `hop_${item.audit.hop}_${reason}`));
  const repayment = plan?.preflightSpec?.repaymentConstraint || {};
  const minProfitOverride = envBigInt(
    "ROBINHOOD_TESTNET_REPAYMENT_MIN_PROFIT",
    envBigInt("ROBINHOOD_TESTNET_DYNAMIC_ROUTE_MIN_PROFIT_RAW", toBigInt(repayment.minProfitRaw)),
  );
  const allowBroadcast = envBool("ROBINHOOD_TESTNET_ALLOW_DYNAMIC_ROUTE_BROADCAST", false);
  const allowUnpause = envBool("ROBINHOOD_TESTNET_ALLOW_DYNAMIC_ROUTE_UNPAUSE", false);
  const allowApprovals = envBool("ROBINHOOD_TESTNET_ALLOW_DYNAMIC_ROUTE_APPROVALS", false);
  const allowRePause = envBool("ROBINHOOD_TESTNET_DYNAMIC_ROUTE_REPAUSE_AFTER_TEST", true);
  const referenceQuotedRoute = hops.some((hop) => String(hop.quoteMethod || "").toLowerCase().includes("reference_quote"));
  const report = {
    schemaVersion: "robinhood-dynamic-v4-route-preflight-v1",
    runAt: new Date().toISOString(),
    network: hre.network.name,
    chainId: Number(network.chainId),
    expectedChainId: 46630,
    signer: signer ? signer.address : null,
    sourceFiles: { planPath, poolIndexPath },
    executor: { address: executorAddress || null, hasCode: false, pausedBefore: null, pausedAfter: null },
    route: {
      pathText: plan?.route?.pathText || plan?.attempt?.pathText || "",
      hopCount: hops.length,
      triggerBlockNumber: plan?.attempt?.triggerBlockNumber || plan?.evidence?.triggerBlockNumber || null,
    },
    policy: {
      mode: "dynamic_route_staticcall_first",
      broadcastRequiresExplicitEnv: "ROBINHOOD_TESTNET_ALLOW_DYNAMIC_ROUTE_BROADCAST=true",
      unpauseRequiresExplicitEnv: "ROBINHOOD_TESTNET_ALLOW_DYNAMIC_ROUTE_UNPAUSE=true",
      broadcastEnabled: allowBroadcast,
      unpauseEnabled: allowUnpause,
      approvalEnabled: allowApprovals,
      rePauseAfterTest: allowRePause,
    },
    calldataBuild: { attempted: false, ok: false, targetCount: 0, calldataCount: 0, initialToken: null, finalToken: null, error: null },
    approvals: { attempted: false, ok: false, tokens: [], errors: [] },
    nativeValueRoute: { requiresForwarding: false, validationMode: "static_call_first" },
    routeVerification: {
      referenceQuoteDetected: referenceQuotedRoute,
      individualHopChecksOk: null,
      amountChainVerified: false,
    },
    staticCall: { attempted: false, ok: false, result: null, error: null },
    estimateGas: { attempted: false, ok: false, gas: null, error: null },
    hopExecutionChecks: [],
    broadcast: { attempted: false, ok: false, hash: null, error: null },
    operationError: null,
    repaymentConstraint: repayment,
    hopAudit: built.map((item) => item.audit),
    blockingReasons: Array.from(new Set(allBlockers)),
    readyForDynamicBroadcast: false,
  };

  if (report.chainId !== report.expectedChainId) report.blockingReasons.push("unexpected_chain_id");
  if (!executorAddress) report.blockingReasons.push("executor_address_missing");
  if (!signer) report.blockingReasons.push("signer_missing");
  if (!hops.length) report.blockingReasons.push("route_hops_missing");

  let executor = null;
  let poolManagerAddress = "";
  if (executorAddress) {
    const code = await hre.ethers.provider.getCode(executorAddress);
    report.executor.hasCode = code !== "0x";
    if (!report.executor.hasCode) report.blockingReasons.push("executor_code_missing");
    executor = await hre.ethers.getContractAt("RobinhoodPausedFlashLoanExecutor", executorAddress, signer);
    const v4Adapter = await executor.uniswapV4Adapter();
    poolManagerAddress = normalizeAddress(v4Adapter.poolManager || "");
    report.executor.pausedBefore = await executor.paused();
  }

  if (executor && report.blockingReasons.length === 0) {
    report.calldataBuild.attempted = true;
    let stage = "calldata_build";
    try {
      const [targets, calldatas, initialToken, finalToken] = await executor.buildV4PoolManagerExactInputRouteCalldata.staticCall(
        built.map((item) => item.swap),
        deadline,
      );
      report.calldataBuild.ok = true;
      report.calldataBuild.targetCount = targets.length;
      report.calldataBuild.calldataCount = calldatas.length;
      report.calldataBuild.initialToken = initialToken;
      report.calldataBuild.finalToken = finalToken;

      const targetAddresses = Array.from(targets).map((item) => normalizeAddress(item, { allowZero: true }));
      const swapValues = hops.map((hop, index) => {
        if (poolManagerAddress && targetAddresses[index] === poolManagerAddress) {
          return 0n;
        }
        return normalizeAddress(hop.tokenIn, { allowZero: true }) === hre.ethers.ZeroAddress ? toBigInt(hop.amountInRaw) : 0n;
      });
      const request = {
        token: normalizeAddress(repayment.borrowToken, { allowZero: true }),
        amount: toBigInt(repayment.borrowAmountRaw),
        swapTargets: Array.from(targets),
        swapCalldatas: Array.from(calldatas),
        swapValues,
        finalToken: normalizeAddress(repayment.finalToken, { allowZero: true }),
        minProfit: minProfitOverride,
        gasReserve: toBigInt(repayment.gasReserveRaw),
      };
      const requiresNativeValueForwarding = request.swapValues.some((value) => toBigInt(value) > 0n);
      const transactionValue = 0n;
      report.preflightSpec = report.preflightSpec || {};
      report.preflightSpec.executionRequest = report.preflightSpec.executionRequest || {};
      report.preflightSpec.executionRequest.transactionValueRaw = transactionValue.toString();
      report.nativeValueRoute.requiresForwarding = requiresNativeValueForwarding;
      report.nativeValueRoute.validationMode = requiresNativeValueForwarding ? "estimate_gas_first" : "static_call_first";
      const executionRequest = requiresNativeValueForwarding
        ? request
        : {
            token: request.token,
            amount: request.amount,
            swapTargets: request.swapTargets,
            swapCalldatas: request.swapCalldatas,
            finalToken: request.finalToken,
            minProfit: request.minProfit,
            gasReserve: request.gasReserve,
          };

      if (!requiresNativeValueForwarding) {
        for (let i = 0; i < targets.length; i++) {
          const target = targetAddresses[i] || "";
          const hopValue = toBigInt(request.swapValues[i] || 0n);
          if (poolManagerAddress && target === poolManagerAddress) {
            report.hopExecutionChecks.push({
              hopIndex: i + 1,
              target: targets[i],
              value: hopValue.toString(),
              ok: null,
              skipped: true,
              reason: "poolmanager_hop_requires_unlock_context",
            });
            continue;
          }
          try {
            const gas = await hre.ethers.provider.estimateGas({
              from: executorAddress,
              to: targets[i],
              data: calldatas[i],
              value: hopValue,
            });
            report.hopExecutionChecks.push({
              hopIndex: i + 1,
              target: targets[i],
              value: hopValue.toString(),
              ok: true,
              gas: gas.toString(),
              error: null,
            });
          } catch (error) {
            report.hopExecutionChecks.push({
              hopIndex: i + 1,
              target: targets[i],
              value: hopValue.toString(),
              ok: false,
              gas: null,
              error: decodeError(error),
            });
          }
        }
        if (report.hopExecutionChecks.some((check) => check.ok === false)) {
          report.blockingReasons.push("dynamic_v4_individual_hop_revert");
          report.routeVerification.individualHopChecksOk = false;
        } else {
          report.routeVerification.individualHopChecksOk = true;
        }
      } else {
        report.hopExecutionChecks = hops.map((hop, index) => ({
          hopIndex: index + 1,
          target: targets[index],
          value: nativeSwapValue(hop).toString(),
          ok: null,
          skipped: true,
          reason: "native_value_route_requires_full_execution_context",
        }));
      }
      if (referenceQuotedRoute) {
        report.blockingReasons.push("dynamic_route_reference_quote_unverified");
      }

      if (report.executor.pausedBefore && allowUnpause) {
        stage = "unpause";
        const tx = await executor.setPaused(false);
        await tx.wait();
      }

      const approvalTokens = Array.from(new Set(
        hops
          .map((hop) => normalizeAddress(hop.tokenIn, { allowZero: true }))
          .filter((token) => token && token !== hre.ethers.ZeroAddress),
      ));
      report.approvals.tokens = approvalTokens;
      if (approvalTokens.length > 0 && allowApprovals && allowUnpause && allowBroadcast) {
        report.approvals.attempted = true;
        const expiration = BigInt(Math.floor(Date.now() / 1000) + Number(process.env.ROBINHOOD_TESTNET_DYNAMIC_ROUTE_APPROVAL_SECONDS || "86400"));
        for (const token of approvalTokens) {
          try {
            const tx = await executor.approveTokenWithPermit2(token, (1n << 160n) - 1n, expiration);
            await tx.wait();
          } catch (error) {
            const decoded = decodeError(error);
            report.approvals.errors.push({ token, ...decoded });
            report.blockingReasons.push("dynamic_route_approval_failed");
          }
        }
        report.approvals.ok = report.approvals.errors.length === 0;
      }

      if (!requiresNativeValueForwarding) {
        report.staticCall.attempted = true;
        stage = "static_call";
        try {
          report.staticCall.result = await executor.requestV4FlashLoan.staticCall(executionRequest);
          report.staticCall.ok = true;
        } catch (error) {
          report.staticCall.error = decodeError(error);
          if (report.staticCall.error?.decoded?.name === "BadFlashCallback") {
            report.blockingReasons.push("dynamic_v4_poolmanager_callback_rejected");
          }
          if (report.staticCall.error?.decoded?.name === "CurrencyNotSettled") {
            report.blockingReasons.push("dynamic_v4_currency_not_settled");
          }
          if (report.staticCall.error?.decoded?.name === "BadFlashRepayment") {
            report.blockingReasons.push("dynamic_v4_bad_flash_repayment");
          }
          if (report.executor.pausedBefore && !allowUnpause) report.blockingReasons.push("paused_gate_blocks_execution");
          else report.blockingReasons.push("dynamic_route_static_call_failed");
        }
      }

      if (requiresNativeValueForwarding || report.staticCall.ok) {
        report.estimateGas.attempted = true;
        stage = "estimate_gas";
        try {
          report.estimateGas.gas = requiresNativeValueForwarding
            ? await executor.requestV4FlashLoanWithValues.estimateGas(request, { value: transactionValue })
            : await executor.requestV4FlashLoan.estimateGas(executionRequest);
          report.estimateGas.ok = true;
          report.routeVerification.amountChainVerified = true;
        } catch (error) {
          report.estimateGas.error = decodeError(error);
          if (report.routeVerification.individualHopChecksOk) {
            report.blockingReasons.push("dynamic_v4_route_amount_chain_unverified");
          }
          if (
            report.estimateGas.error?.decoded?.name === "RouterCallFailed"
            && report.estimateGas.error.decoded.args?.[0] === "0x00000000"
          ) {
            report.blockingReasons.push("dynamic_v4_live_swap_target_empty_revert");
          }
          if (report.estimateGas.error?.decoded?.name === "CurrencyNotSettled") {
            report.blockingReasons.push("dynamic_v4_currency_not_settled");
          }
          if (report.estimateGas.error?.decoded?.name === "BadFlashRepayment") {
            report.blockingReasons.push("dynamic_v4_bad_flash_repayment");
          }
          report.blockingReasons.push("dynamic_route_estimate_gas_failed");
        }
      }

      report.readyForDynamicBroadcast = Boolean((requiresNativeValueForwarding ? report.estimateGas.ok : report.staticCall.ok) && allowBroadcast);
      if (report.readyForDynamicBroadcast) {
        report.broadcast.attempted = true;
        stage = "broadcast";
        try {
          const tx = requiresNativeValueForwarding
            ? await executor.requestV4FlashLoanWithValues(request, { value: transactionValue })
            : await executor.requestV4FlashLoan(executionRequest);
          const receipt = await tx.wait();
          report.broadcast.ok = true;
          report.broadcast.hash = receipt.hash;
        } catch (error) {
          report.broadcast.error = error.shortMessage || error.message || String(error);
          report.blockingReasons.push("dynamic_route_broadcast_failed");
        }
      }
    } catch (error) {
      const decoded = decodeError(error);
      if (stage === "calldata_build") {
        report.calldataBuild.error = decoded;
        report.blockingReasons.push("dynamic_route_calldata_build_failed");
      } else {
        report.operationError = { stage, ...decoded };
        report.blockingReasons.push(`dynamic_route_${stage}_operation_failed`);
      }
    } finally {
      if (executor && allowUnpause && allowRePause) {
        try {
          const pausedNow = await executor.paused();
          if (!pausedNow) {
            const tx = await executor.setPaused(true);
            await tx.wait();
          }
        } catch (error) {
          report.blockingReasons.push(`repause_failed:${error.shortMessage || error.message || String(error)}`);
        }
      }
      if (executor) report.executor.pausedAfter = await executor.paused();
    }
  }

  report.blockingReasons = Array.from(new Set(report.blockingReasons));
  return stringify(report);
}

async function main() {
  const report = await runDynamicRoutePreflight();
  const reportPath = writeEvidence(report);
  console.log(JSON.stringify({ ...report, reportPath }, null, 2));
  const routeExecutable = report.nativeValueRoute?.requiresForwarding ? report.estimateGas.ok : report.staticCall.ok;
  if (!report.calldataBuild.ok || !routeExecutable || report.blockingReasons.length > 0) process.exitCode = 1;
}

if (require.main === module) {
  main().catch((error) => {
    console.error(error.message || error);
    process.exitCode = 1;
  });
}

module.exports = { runDynamicRoutePreflight, writeEvidence };
