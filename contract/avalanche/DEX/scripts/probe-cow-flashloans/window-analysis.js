// Binance window price/rate analysis helpers for the CoW flashloan probe CLI.

const {
  tokenKey,
} = require("./token-registry");
const {
  decimalNumber,
  numberOrNull,
  decimalPercentDelta,
} = require("./decimal-math");
const {
  routeProfit,
} = require("./quote-analysis");

function stablePrice(symbol) {
  return ["USDC", "USDT", "DAI"].includes(tokenKey(symbol)) ? 1 : null;
}

function priceRowsFromRoute(routeSpec) {
  const rows = [];
  const addRow = (row, source) => {
    if (!row || typeof row !== "object") return;
    const symbol = tokenKey(row.base_symbol || row.symbol);
    if (!symbol) return;
    rows.push({
      symbol: symbol.replace(/USDT$/, ""),
      startPrice: numberOrNull(row.start_price),
      endPrice: numberOrNull(row.end_price ?? row.current_price),
      currentPrice: numberOrNull(row.current_price ?? row.end_price),
      changePercent: numberOrNull(row.change_percent),
      role: row.role || null,
      source,
    });
  };
  for (const row of routeSpec?.ownPlan?.market_prices || []) addRow(row, "ownPlan.market_prices");
  addRow(routeSpec?.liveSignal?.top, "liveSignal.top");
  addRow(routeSpec?.liveSignal?.bottom, "liveSignal.bottom");
  return rows;
}

function tokenWindowPrice(symbol, routeSpec) {
  const stable = stablePrice(symbol);
  if (stable != null) {
    return {
      symbol: tokenKey(symbol),
      startPrice: stable,
      endPrice: stable,
      currentPrice: stable,
      changePercent: 0,
      role: "stable_reference",
      source: "stable_reference",
    };
  }
  const wanted = tokenKey(symbol);
  const row = priceRowsFromRoute(routeSpec).find((item) => item.symbol === wanted);
  if (!row || row.startPrice == null || row.endPrice == null || row.startPrice <= 0 || row.endPrice <= 0) {
    return null;
  }
  return row;
}

function binanceRateForWindow(sellPrice, buyPrice) {
  if (sellPrice == null || buyPrice == null || buyPrice === 0) return null;
  return sellPrice / buyPrice;
}

function classifyRateTiming(actualRate, previousRate, nextRate) {
  if (actualRate == null || previousRate == null || nextRate == null) return null;
  const previousDistance = Math.abs(actualRate - previousRate);
  const nextDistance = Math.abs(actualRate - nextRate);
  const closerTo = previousDistance <= nextDistance ? "previous_window" : "next_window";
  const movement = nextRate - previousRate;
  let timing = "flat_window";
  if (Math.abs(movement) > Math.max(Math.abs(previousRate), Math.abs(nextRate), 1) * 1e-12) {
    if (movement > 0) {
      timing = actualRate < nextRate ? "lagging_vs_next_window" : "leading_vs_next_window";
    } else {
      timing = actualRate > nextRate ? "lagging_vs_next_window" : "leading_vs_next_window";
    }
  }
  return {
    closerTo,
    timing,
    previousDistancePercent: decimalPercentDelta(actualRate, previousRate),
    nextDistancePercent: decimalPercentDelta(actualRate, nextRate),
  };
}

function legWindowPriceAnalysis(routeSpec, leg, outputKey) {
  if (!leg?.ok) return null;
  const sell = tokenWindowPrice(leg.sellSymbol, routeSpec);
  const buy = tokenWindowPrice(leg.buySymbol, routeSpec);
  const sellAmount = decimalNumber(leg.signedSellAmountAfterFlashLoanFeeHuman || leg.inputAmountHuman);
  const buyAmount = decimalNumber(leg[outputKey]);
  if (!sell || !buy || sellAmount == null || buyAmount == null || sellAmount <= 0) {
    return {
      available: false,
      reason: "binance_window_price_missing_or_invalid",
      sellSymbol: leg.sellSymbol,
      buySymbol: leg.buySymbol,
      sellPrice: sell,
      buyPrice: buy,
    };
  }
  const actualRate = buyAmount / sellAmount;
  const previousRate = binanceRateForWindow(sell.startPrice, buy.startPrice);
  const nextRate = binanceRateForWindow(sell.endPrice, buy.endPrice);
  const timing = classifyRateTiming(actualRate, previousRate, nextRate);
  const impliedBuyPriceAtNextSell = actualRate > 0 ? sell.endPrice / actualRate : null;
  return {
    available: true,
    outputBasis: outputKey === "quoteBuyAmountHuman" ? "quote_before_slippage_floor" : "protected_min_buy_after_slippage_floor",
    sellSymbol: leg.sellSymbol,
    buySymbol: leg.buySymbol,
    sellAmountHuman: String(sellAmount),
    buyAmountHuman: String(buyAmount),
    actualBuyPerSell: String(actualRate),
    binancePreviousWindowBuyPerSell: previousRate == null ? null : String(previousRate),
    binanceNextWindowBuyPerSell: nextRate == null ? null : String(nextRate),
    actualOutputAtBinancePreviousWindow: previousRate == null ? null : String(sellAmount * previousRate),
    actualOutputAtBinanceNextWindow: nextRate == null ? null : String(sellAmount * nextRate),
    quoteVsPreviousWindowPercent: decimalPercentDelta(actualRate, previousRate),
    quoteVsNextWindowPercent: decimalPercentDelta(actualRate, nextRate),
    closerToWindow: timing?.closerTo || null,
    timingVsBinanceNextWindow: timing?.timing || null,
    impliedBuyTokenUsdtPriceUsingNextSellPrice:
      impliedBuyPriceAtNextSell == null ? null : String(impliedBuyPriceAtNextSell),
    sellPriceWindow: sell,
    buyPriceWindow: buy,
  };
}

function routeWindowAnalysis(routeSpec, tokens, legs) {
  if (!legs.length) return null;
  const previous = legs.map((leg) => legWindowPriceAnalysis(routeSpec, leg, "quoteBuyAmountHuman"));
  const protectedFloor = legs.map((leg) => legWindowPriceAnalysis(routeSpec, leg, "buyAmountHuman"));
  const routeQuote = routeProfit(routeSpec, tokens, legs, "quoteBuyAmountHuman");
  const routeProtected = routeProfit(routeSpec, tokens, legs, "buyAmountHuman");
  const input = decimalNumber(routeQuote?.inputAmount || routeProtected?.inputAmount);
  const spread = decimalNumber(routeSpec?.liveSignal?.spreadPercent);
  const quoteFinal = decimalNumber(routeQuote?.finalAmount);
  const protectedFinal = decimalNumber(routeProtected?.finalAmount);
  const binanceWindowEdgeFinal = input != null && spread != null ? input * (1 + spread / 100) : null;
  const routeRateQuote = input && quoteFinal != null ? quoteFinal / input : null;
  const routeRateProtected = input && protectedFinal != null ? protectedFinal / input : null;
  const edgeRate = spread != null ? 1 + spread / 100 : null;
  const quoteTiming = classifyRateTiming(routeRateQuote, 1, edgeRate);
  const protectedTiming = classifyRateTiming(routeRateProtected, 1, edgeRate);
  return {
    available: previous.every((item) => item?.available),
    binanceExpectedModel:
      "previous_window_roundtrip_rate_is_1; next_window_edge_rate_uses_top_change_minus_bottom_change_as the optimistic trigger edge",
    binanceWindowSpreadPercent: spread == null ? null : String(spread),
    binanceWindowEdgeExpectedFinalAmount: binanceWindowEdgeFinal == null ? null : String(binanceWindowEdgeFinal),
    quoteFinalAmountBeforeSlippageFloor: routeQuote?.finalAmount ?? null,
    protectedFinalAmountAfterSlippageFloor: routeProtected?.finalAmount ?? null,
    quoteGapVsBinanceWindowEdgePercent: decimalPercentDelta(quoteFinal, binanceWindowEdgeFinal),
    protectedGapVsBinanceWindowEdgePercent: decimalPercentDelta(protectedFinal, binanceWindowEdgeFinal),
    quoteCloserToWindow: quoteTiming?.closerTo || null,
    protectedCloserToWindow: protectedTiming?.closerTo || null,
    quoteTimingVsBinanceEdge: quoteTiming?.timing || null,
    protectedTimingVsBinanceEdge: protectedTiming?.timing || null,
    threeHopVerdict:
      protectedFinal != null && input != null && protectedFinal <= input
        ? "three_hop_cow_quote_lags_expected_binance_edge_and_is_unprofitable"
        : quoteFinal != null && binanceWindowEdgeFinal != null && quoteFinal < binanceWindowEdgeFinal
          ? "three_hop_cow_quote_lags_expected_binance_edge"
          : "three_hop_cow_quote_matches_or_leads_expected_binance_edge",
    legs: previous.map((item, index) => ({
      index: legs[index]?.index ?? index + 1,
      quoteBeforeSlippageFloor: item,
      protectedAfterSlippageFloor: protectedFloor[index],
    })),
  };
}

function legSlippageControl(leg) {
  const afterNetwork = decimalNumber(leg.costAnalysis?.afterNetworkCostsBuyHuman);
  const afterSlippage = decimalNumber(leg.costAnalysis?.afterSlippageBuyHuman);
  const configured = decimalNumber(leg.configuredSlippageBps);
  if (afterNetwork == null || afterSlippage == null || afterNetwork <= 0 || configured == null) {
    return { available: false, reason: "slippage_amounts_missing" };
  }
  const observedBps = ((afterNetwork - afterSlippage) / afterNetwork) * 10000;
  const expectedFloor = afterNetwork * (1 - configured / 10000);
  const floorDiffBps = observedBps - configured;
  return {
    available: true,
    configuredSlippageBps: configured,
    suggestedSlippageBps: leg.costAnalysis?.suggestedSlippageBps ?? null,
    observedSlippageBps: String(observedBps),
    expectedMinBuyFromConfiguredBps: String(expectedFloor),
    actualMinBuyAfterSlippage: String(afterSlippage),
    floorDifferenceBps: String(floorDiffBps),
    verdict:
      Math.abs(floorDiffBps) <= 0.05
        ? "matches_configured_slippage_bps"
        : floorDiffBps > 0
          ? "protected_floor_is_wider_than_configured_slippage"
          : "protected_floor_is_tighter_than_configured_slippage",
  };
}

module.exports = {
  stablePrice,
  priceRowsFromRoute,
  tokenWindowPrice,
  binanceRateForWindow,
  classifyRateTiming,
  legWindowPriceAnalysis,
  routeWindowAnalysis,
  legSlippageControl,
};
