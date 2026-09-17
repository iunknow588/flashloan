const Q96 = (1n << 96n);
const MIN_TICK = -887272n;
const MAX_TICK = 887272n;

function sqrt(value) {
  if (value < 2n) return value;
  let x0 = 1n << BigInt(Math.ceil(value.toString(2).length / 2));
  let x1 = (x0 + value / x0) >> 1n;
  while (x1 < x0) {
    x0 = x1;
    x1 = (x0 + value / x0) >> 1n;
  }
  return x0;
}

function sqrtPriceX96ForRatio(amountA, amountB) {
  return (sqrt(amountB) * Q96) / sqrt(amountA);
}

function fullRangeTicks(tickSpacing) {
  const spacing = BigInt(tickSpacing);
  let lower = (MIN_TICK / spacing) * spacing;
  if (lower < MIN_TICK) lower += spacing;
  let upper = (MAX_TICK / spacing) * spacing;
  if (upper > MAX_TICK) upper -= spacing;
  return [lower, upper];
}

// Computes the V4 liquidity value for an effectively full-range position that
// deposits the desired amount0/amount1 at the current sqrtPriceX96.
function fullRangeLiquidityForAmounts(sqrtPriceX96, amount0, amount1) {
  const byAmount0 = (amount0 * sqrtPriceX96) / Q96;
  const byAmount1 = (amount1 * Q96) / sqrtPriceX96;
  return byAmount0 > byAmount1 ? byAmount0 : byAmount1;
}

module.exports = {
  Q96,
  fullRangeTicks,
  fullRangeLiquidityForAmounts,
  sqrtPriceX96ForRatio,
};
