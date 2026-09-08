// BigInt / decimal helpers used by the CoW flashloan probe CLI.

function pow10(decimals) {
  return TEN ** BigInt(Number(decimals));
}

function parseHumanUnits(amount, decimals) {
  const text = String(amount || "0").trim();
  if (!text || text.startsWith("-")) throw new Error(`invalid positive amount: ${amount}`);
  const [whole, fractional = ""] = text.split(".");
  const scale = Number(decimals);
  const padded = `${fractional}${"0".repeat(scale)}`.slice(0, scale);
  return (BigInt(whole || "0") * pow10(decimals) + BigInt(padded || "0")).toString();
}

function parseDecimalScaled(value, scale = 1000000n) {
  const text = String(value ?? "0").trim();
  if (!/^(?:\d+)(?:\.\d+)?$/.test(text)) {
    throw new Error(`invalid non-negative decimal: ${value}`);
  }
  const [whole, fractional = ""] = text.split(".");
  const scaleDigits = String(scale).length - 1;
  const fractionText = `${fractional}${"0".repeat(scaleDigits)}`.slice(0, scaleDigits);
  return BigInt(whole) * scale + BigInt(fractionText || "0");
}

function percentageOfUnits(amountUnits, percentHuman) {
  const scale = 1000000n;
  const percentScaled = parseDecimalScaled(percentHuman, scale);
  const denominator = 100n * scale;
  if (percentScaled === 0n || amountUnits === 0n) return 0n;
  // Round up so the configured pure-profit floor can never be underfunded.
  return (amountUnits * percentScaled + denominator - 1n) / denominator;
}

function formatUnits(amount, decimals) {
  if (amount == null || amount === "") return null;
  const value = BigInt(String(amount));
  const scale = pow10(decimals);
  const whole = value / scale;
  const fraction = value % scale;
  if (fraction === 0n) return whole.toString();
  return `${whole}.${fraction.toString().padStart(Number(decimals), "0").replace(/0+$/, "")}`;
}

function decimalNumber(value) {
  if (value == null || value === "") return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function numberOrNull(value) {
  const parsed = decimalNumber(value);
  return parsed == null ? null : parsed;
}

function compareHuman(actual, expected) {
  const a = decimalNumber(actual);
  const b = decimalNumber(expected);
  if (a == null || b == null) return null;
  if (a >= b) return "met";
  return "below";
}

function decimalDifference(a, b) {
  const left = decimalNumber(a);
  const right = decimalNumber(b);
  if (left == null || right == null) return null;
  return left - right;
}

function decimalPercent(numerator, denominator) {
  const top = decimalNumber(numerator);
  const bottom = decimalNumber(denominator);
  if (top == null || bottom == null || bottom === 0) return null;
  return (top / bottom) * 100;
}

function decimalPercentDelta(actual, expected) {
  const left = decimalNumber(actual);
  const right = decimalNumber(expected);
  if (left == null || right == null || right === 0) return null;
  return ((left - right) / right) * 100;
}

function bigintText(value) {
  return typeof value === "bigint" ? value.toString() : String(value ?? "");
}

function jsonFriendly(value) {
  if (value === undefined) return undefined;
  return JSON.parse(
    JSON.stringify(value, (_key, item) => {
      if (typeof item === "bigint") return item.toString();
      return item;
    })
  );
}

function bigintFrom(value, fallback = 0n) {
  try {
    if (value === null || value === undefined || value === "") return fallback;
    return BigInt(String(value));
  } catch (_) {
    return fallback;
  }
}

function quoteAmountHuman(amount, decimals) {
  return formatUnits(amount, decimals);
}


module.exports = {
  pow10,
  parseHumanUnits,
  parseDecimalScaled,
  percentageOfUnits,
  formatUnits,
  decimalNumber,
  numberOrNull,
  compareHuman,
  decimalDifference,
  decimalPercent,
  decimalPercentDelta,
  bigintText,
  jsonFriendly,
  bigintFrom,
  quoteAmountHuman,
};
