const hre = require("hardhat");
const executorArtifact = require("../artifacts/src/UnifiedFlashLoanMevExecutor.sol/UnifiedFlashLoanMevExecutor.json");

const executorInterface = new hre.ethers.Interface(executorArtifact.abi);
const DECODED_SCHEMA_VERSION = 1;

const DETAIL_REASONS = new Map([
  [0, "ERR_NONE"],
  [1, "ERR_NOT_ENOUGH_POOLS"],
  [2, "ERR_NO_PRICE_SPREAD"],
  [3, "ERR_QUOTE_FAILED"],
  [4, "ERR_PROFIT_NOT_ENOUGH"],
  [5, "ERR_BORROW_ASSET_DISABLED"],
  [6, "ERR_ROUTE_LAYOUT_INVALID"],
  [55555, "ERR_NO_PROFITABLE_ROUTE"],
]);

function stringifyArg(value) {
  return typeof value === "bigint" ? value.toString() : String(value);
}

function detailReason(code) {
  return DETAIL_REASONS.get(Number(code)) || "ERR_UNKNOWN";
}

function decodeUnifiedExecutorError(data) {
  if (!data || typeof data !== "string" || !data.startsWith("0x") || data.length < 10) return null;
  try {
    const parsed = executorInterface.parseError(data);
    const decoded = {
      name: parsed.name,
      signature: parsed.signature,
    };
    if (parsed.name === "OrderedRuntimeExecutionFailed") {
      decoded.code = stringifyArg(parsed.args.code);
      decoded.codeReason = detailReason(parsed.args.code);
      decoded.failedStatus = stringifyArg(parsed.args.failedStatus);
      decoded.tradeArrayIndex = stringifyArg(parsed.args.tradeArrayIndex);
      decoded.detailCode = stringifyArg(parsed.args.detailCode);
      decoded.detailReason = detailReason(parsed.args.detailCode);
      decoded.expectedProfit = stringifyArg(parsed.args.expectedProfit);
      decoded.quotedFinal = stringifyArg(parsed.args.quotedFinal);
      decoded.requiredFinal = stringifyArg(parsed.args.requiredFinal);
      decoded.attemptedStatusMask = stringifyArg(parsed.args.attemptedStatusMask);
      decoded.remainingStatusMask = stringifyArg(parsed.args.remainingStatusMask);
      return decoded;
    }
    decoded.args = parsed.fragment.inputs.map((input, index) => ({
      name: input.name || `arg${index}`,
      value: stringifyArg(parsed.args[index]),
    }));
    return decoded;
  } catch (_error) {
    return null;
  }
}

function extractErrorData(error) {
  return (
    error?.data ||
    error?.info?.error?.data ||
    error?.error?.data ||
    error?.receipt?.revertReason ||
    null
  );
}

function resultError(error) {
  const data = extractErrorData(error);
  const decoded = decodeUnifiedExecutorError(data);
  return {
    message: error?.shortMessage || error?.message || String(error),
    data,
    decodedSchemaVersion: decoded ? DECODED_SCHEMA_VERSION : null,
    decoded,
  };
}

module.exports = {
  DECODED_SCHEMA_VERSION,
  decodeUnifiedExecutorError,
  detailReason,
  resultError,
};
