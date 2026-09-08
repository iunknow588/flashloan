// Quote leg, cost, and route classification helpers for the CoW flashloan probe CLI.

const { OrderKind } = require("@cowprotocol/sdk-order-book");
const { AaveFlashLoanType } = require("@cowprotocol/sdk-flash-loans");

const {
  envBool,
  envFirst,
} = require("./config");
const {
  formatUnits,
  decimalNumber,
  decimalDifference,
  decimalPercent,
  bigintText,
  jsonFriendly,
  compareHuman,
} = require("./decimal-math");
const {
  orderToSignSummary,
  quoteResponseSummary,
  appDataSummary,
  hooksSummary,
} = require("./report");

function ownStepAnalysis(step, leg) {
  if (!step) return null;
  const actual = leg.buyAmountHuman;
  return {
    minOutputAmount: step.min_output_amount ?? step.cow_sdk_parameters?.min_buy_amount_after_fee ?? null,
    targetOutputAmount: step.target_output_amount ?? step.cow_sdk_parameters?.target_buy_amount_after_fee ?? null,
    selectedTargetSource: step.selected_target_source ?? null,
    selectedAcceptableSource: step.selected_acceptable_source ?? null,
    rule: step.selection_rule || step.rule || null,
    actualVsMin: leg.ok
      ? compareHuman(actual, step.min_output_amount ?? step.cow_sdk_parameters?.min_buy_amount_after_fee)
      : "not_quoted",
    actualVsTarget: leg.ok
      ? compareHuman(actual, step.target_output_amount ?? step.cow_sdk_parameters?.target_buy_amount_after_fee)
      : "not_quoted",
  };
}

function legCostAnalysis({ leg, sell, buy, quoteParams, quote }) {
  const costs = quote.amountsAndCosts || {};
  const beforeAll = costs.beforeAllFees || {};
  const afterNetwork = costs.afterNetworkCosts || {};
  const afterSlippage = costs.afterSlippage || {};
  const quoteBuyHuman = formatUnits(quote.quoteResponse?.quote?.buyAmount, buy.decimals);
  const beforeAllBuyHuman = formatUnits(beforeAll.buyAmount, buy.decimals);
  const afterNetworkBuyHuman = formatUnits(afterNetwork.buyAmount, buy.decimals);
  const afterSlippageBuyHuman = formatUnits(afterSlippage.buyAmount, buy.decimals);
  const networkFeeSellHuman = formatUnits(costs.costs?.networkFee?.amountInSellCurrency, sell.decimals);
  const protocolFeeBuyHuman = formatUnits(costs.costs?.protocolFee?.amount, buy.decimals);
  const slippageLossBuy = decimalDifference(afterNetworkBuyHuman, afterSlippageBuyHuman);
  return {
    configuredSlippageBps: leg.configuredSlippageBps,
    suggestedSlippageBps: quote.suggestedSlippageBps ?? null,
    signedSellAfterFlashLoanFeeHuman: formatUnits(quoteParams.amount, sell.decimals),
    flashLoanFeeHuman: formatUnits(quoteParams.flashLoanFeeAmount, sell.decimals),
    quoteBuyBeforeSlippageHuman: quoteBuyHuman,
    beforeAllFeesBuyHuman: beforeAllBuyHuman,
    afterNetworkCostsBuyHuman: afterNetworkBuyHuman,
    afterSlippageBuyHuman: afterSlippageBuyHuman,
    networkFeeSellHuman,
    protocolFeeBuyHuman,
    slippageLossBuyHuman: slippageLossBuy == null ? null : String(slippageLossBuy),
    slippageLossPercentOfAfterNetwork: decimalPercent(slippageLossBuy, afterNetworkBuyHuman),
    networkFeePercentOfSell: decimalPercent(networkFeeSellHuman, formatUnits(quoteParams.amount, sell.decimals)),
    protocolFeePercentOfBuy: decimalPercent(protocolFeeBuyHuman, beforeAllBuyHuman),
  };
}

async function quoteLeg({ tradingSdk, flashSdk, chainId, owner, sell, buy, amount, flashLoanFeePercent, slippageBps, ownStep }) {
  const tradeParameters = {
    kind: OrderKind.SELL,
    owner,
    sellToken: sell.address,
    sellTokenDecimals: sell.decimals,
    buyToken: buy.address,
    buyTokenDecimals: buy.decimals,
    amount: String(amount),
    validFor: 300,
    slippageBps,
  };
  const params = {
    chainId,
    tradeParameters,
    collateralToken: sell.address,
    flashLoanFeePercent,
    settings: {
      preventApproval: true,
    },
  };
  const quoteParams = await flashSdk.getSwapQuoteParams(params);
  const quote = await tradingSdk.getQuoteOnly(quoteParams);
  const posting = await flashSdk.getOrderPostingSettings(
    AaveFlashLoanType.CollateralSwap,
    quoteParams,
    {
      flashLoanAmount: BigInt(amount),
      orderToSign: quote.orderToSign,
    }
  );
  const metadata = posting.swapSettings?.appData?.metadata || {};
  const hooks = metadata.hooks || {};
  const flashloanMetadata = metadata.flashloan || metadata.flashLoan || null;
  const leg = {
    sellSymbol: sell.symbol,
    buySymbol: buy.symbol,
    sellToken: sell.address,
    buyToken: buy.address,
    sellDecimals: sell.decimals,
    buyDecimals: buy.decimals,
    inputAmount: String(amount),
    inputAmountHuman: formatUnits(amount, sell.decimals),
    configuredSlippageBps: slippageBps,
    signedSellAmountAfterFlashLoanFee: quoteParams.amount,
    signedSellAmountAfterFlashLoanFeeHuman: formatUnits(quoteParams.amount, sell.decimals),
    flashLoanFeeAmount: bigintText(quoteParams.flashLoanFeeAmount),
    flashLoanFeeAmountHuman: formatUnits(quoteParams.flashLoanFeeAmount, sell.decimals),
    buyAmount: quote.orderToSign.buyAmount,
    buyAmountHuman: formatUnits(quote.orderToSign.buyAmount, buy.decimals),
    quoteBuyAmount: quote.quoteResponse?.quote?.buyAmount,
    quoteBuyAmountHuman: formatUnits(quote.quoteResponse?.quote?.buyAmount, buy.decimals),
    sellAmount: quote.orderToSign.sellAmount,
    validTo: quote.orderToSign.validTo,
    orderToSign: orderToSignSummary(quote.orderToSign),
    quoteResponse: quoteResponseSummary(quote.quoteResponse),
    amountsAndCosts: jsonFriendly(quote.amountsAndCosts || null),
    appData: appDataSummary(quote.appDataInfo, metadata),
    instanceAddress: posting.instanceAddress,
    hasFlashloanMetadata: Boolean(flashloanMetadata),
    flashloanMetadata: jsonFriendly(flashloanMetadata),
    hasHooksMetadata: Boolean(metadata.hooks),
    hookKeys: Object.keys(hooks || {}),
    hooks: hooksSummary(hooks),
    aaveLiquidityHint: {
      sellTokenDepthScoreUsd: sell.aaveDepthScoreUsd ?? null,
      sellTokenAvailableLiquidity: sell.aaveAvailableLiquidity ?? null,
      sellTokenReserveLiquidity: sell.aaveReserveLiquidity ?? null,
    },
  };
  leg.costAnalysis = legCostAnalysis({ leg, sell, buy, quoteParams, quote });
  return {
    ...leg,
    ownGuard: ownStepAnalysis(ownStep, leg),
  };
}

function classifyRoute(routeSpec, tokens, legs, error) {
  if (error) return "quote_or_sdk_failed";
  if (!legs.length || legs.some((leg) => !leg.ok)) return "quote_or_sdk_failed";
  if (!legs.every((leg) => leg.hasFlashloanMetadata && leg.hasHooksMetadata)) return "hooks_missing";
  const ownGuardFailures = legs.filter((leg) => leg.ownGuard?.actualVsMin === "below").length;
  if (ownGuardFailures) return "query_below_own_guard";
  const first = tokens[0];
  const last = tokens[tokens.length - 1];
  if (first.address.toLowerCase() === last.address.toLowerCase()) {
    const input = decimalNumber(formatUnits(routeSpec.amountUnits, first.decimals));
    const output = decimalNumber(legs[legs.length - 1].buyAmountHuman);
    if (input != null && output != null && output <= input) return "not_profitable_after_sequential_quotes";
    if (input != null && output != null && output > input) return "profitable_after_sequential_quotes";
  }
  return "quote_hooks_ok_profit_unknown";
}

function enrichLiveSignalWithQuote(routeSpec, tokens, legs) {
  if (!routeSpec?.liveSignal || !legs.length) return routeSpec?.liveSignal || null;
  const expected = routeProfit(routeSpec, tokens, legs, "quoteBuyAmountHuman");
  const protectedFloor = routeProfit(routeSpec, tokens, legs, "buyAmountHuman");
  return {
    ...routeSpec.liveSignal,
    quoteComparison: {
      binanceWindowSpreadPercent: routeSpec.liveSignal.spreadPercent,
      expectedQuoteDeltaPercent: expected?.deltaPercent ?? null,
      protectedFloorDeltaPercent: protectedFloor?.deltaPercent ?? null,
      verdict:
        decimalNumber(expected?.deltaAmount) != null && decimalNumber(expected.deltaAmount) < 0
          ? "solver_price_not_matching_binance_window_edge"
          : "solver_quote_preserves_or_improves_binance_edge",
    },
  };
}

function routeProfit(routeSpec, tokens, legs, outputKey = "buyAmountHuman") {
  if (!legs.length) return null;
  const first = tokens[0];
  const last = tokens[tokens.length - 1];
  if (first.address.toLowerCase() !== last.address.toLowerCase()) return null;
  const input = decimalNumber(formatUnits(routeSpec.amountUnits, first.decimals));
  const output = decimalNumber(legs[legs.length - 1][outputKey]);
  if (input == null || output == null || input === 0) return null;
  return {
    inputAmount: String(input),
    finalAmount: String(output),
    deltaAmount: String(output - input),
    deltaPercent: String(((output - input) / input) * 100),
    symbol: first.symbol,
  };
}

function routeSelection(routes) {
  const pureIntentEnabled = envBool("COW_FLASHLOAN_PURE_INTENT_ENABLED", true);
  const pureIntentMinProfitPercent =
    decimalNumber(envFirst("COW_FLASHLOAN_PURE_INTENT_MIN_PROFIT_PERCENT")) ?? 0.618;
  const absoluteMinProfit =
    decimalNumber(envFirst("COW_FLASHLOAN_PROBE_MIN_PROFIT_USDC", "COW_AUTO_EXECUTE_MIN_PROFIT_USD")) ?? 0;
  const ranking = routes
    .map((route, index) => {
      const protectedProfit = route.profit || route.costAnalysis?.protectedProfitAfterSlippageFloor || null;
      const netProfit = route.netProfit || route.costAnalysis?.netProfitAfterAllCosts || null;
      const expectedProfit = route.costAnalysis?.expectedProfitBeforeSlippageFloor || null;
      const finalAmount = decimalNumber(protectedProfit?.finalAmount);
      const deltaAmount = decimalNumber(netProfit?.deltaAmount ?? protectedProfit?.deltaAmount);
      const expectedFinalAmount = decimalNumber(expectedProfit?.finalAmount);
      const expectedDeltaAmount = decimalNumber(expectedProfit?.deltaAmount);
      const inputAmount = decimalNumber(route.amountHuman ?? protectedProfit?.inputAmount);
      const minProfit =
        pureIntentEnabled
          ? decimalNumber(
              route.intent?.minPureProfitHuman ??
                route.costAnalysis?.requiredMinPureProfitHuman
            ) ??
            (inputAmount == null ? null : inputAmount * pureIntentMinProfitPercent / 100)
          : absoluteMinProfit;
      const quoteAvailable = Boolean(finalAmount != null && (route.ok || route.quote));
      const profitFloorMet = quoteAvailable && deltaAmount != null && minProfit != null && deltaAmount >= minProfit;
      const sellBudgetPassed =
        route.intent?.sellBudgetPassed ?? route.costAnalysis?.sellBudgetPassed ?? null;
      const executionBudgetMet = Boolean(route.ok && profitFloorMet);
      return {
        sourceIndex: index + 1,
        ok: Boolean(route.ok),
        quoteAvailable,
        pair: route.pair,
        pairRank: route.pairRank,
        routeDirection: route.routeDirection || null,
        priorityReason: route.priorityReason,
        route: route.route,
        classification: route.classification,
        finalAmount: protectedProfit?.finalAmount ?? null,
        deltaAmount: netProfit?.deltaAmount ?? protectedProfit?.deltaAmount ?? null,
        deltaPercent: netProfit?.deltaPercent ?? protectedProfit?.deltaPercent ?? null,
        grossDeltaAmount: protectedProfit?.deltaAmount ?? null,
        netProfitAfterAllCosts: netProfit?.deltaAmount ?? null,
        expectedFinalAmountBeforeSlippageFloor: expectedProfit?.finalAmount ?? null,
        expectedDeltaAmountBeforeSlippageFloor: expectedProfit?.deltaAmount ?? null,
        expectedDeltaPercentBeforeSlippageFloor: expectedProfit?.deltaPercent ?? null,
        profitFloorMet,
        sellBudgetPassed,
        profitBudgetMet: executionBudgetMet,
        blockingReason:
          !quoteAvailable
            ? "quote_unavailable"
            : !profitFloorMet
              ? "net_profit_below_percentage_floor"
              : !route.ok
                ? "route_not_feasible"
                : null,
        budgetWarning: sellBudgetPassed === false ? "required_sell_exceeds_available_budget" : null,
        minProfitHuman: minProfit == null ? null : String(minProfit),
        minProfitPercent: pureIntentEnabled ? String(pureIntentMinProfitPercent) : null,
        slippageRecommendation: route.costAnalysis?.slippageRecommendation || null,
        finalLossDominantCause: route.costAnalysis?.finalLossDominantCause || null,
        binanceWindowVerdict: route.binanceWindowAnalysis?.threeHopVerdict || null,
        quoteCloserToWindow: route.binanceWindowAnalysis?.quoteCloserToWindow || null,
        protectedCloserToWindow: route.binanceWindowAnalysis?.protectedCloserToWindow || null,
        threeHopRatios: (route.legs || []).map((leg) => ({
          index: leg.index,
          sellSymbol: leg.sellSymbol,
          buySymbol: leg.buySymbol,
          quoteActualBuyPerSell:
            leg.binanceWindowPriceAnalysis?.quoteBeforeSlippageFloor?.actualBuyPerSell ?? null,
          protectedActualBuyPerSell:
            leg.binanceWindowPriceAnalysis?.protectedAfterSlippageFloor?.actualBuyPerSell ?? null,
          binancePreviousWindowBuyPerSell:
            leg.binanceWindowPriceAnalysis?.quoteBeforeSlippageFloor?.binancePreviousWindowBuyPerSell ?? null,
          binanceNextWindowBuyPerSell:
            leg.binanceWindowPriceAnalysis?.quoteBeforeSlippageFloor?.binanceNextWindowBuyPerSell ?? null,
          quoteVsPreviousWindowPercent:
            leg.binanceWindowPriceAnalysis?.quoteBeforeSlippageFloor?.quoteVsPreviousWindowPercent ?? null,
          quoteVsNextWindowPercent:
            leg.binanceWindowPriceAnalysis?.quoteBeforeSlippageFloor?.quoteVsNextWindowPercent ?? null,
          closerToWindow:
            leg.binanceWindowPriceAnalysis?.quoteBeforeSlippageFloor?.closerToWindow ?? null,
          timingVsBinanceNextWindow:
            leg.binanceWindowPriceAnalysis?.quoteBeforeSlippageFloor?.timingVsBinanceNextWindow ?? null,
        })),
        _sortFinalAmount: quoteAvailable ? finalAmount : Number.NEGATIVE_INFINITY,
        _sortExpectedFinalAmount:
          route.ok && expectedFinalAmount != null ? expectedFinalAmount : Number.NEGATIVE_INFINITY,
        _sortDeltaAmount:
          route.ok && deltaAmount != null ? deltaAmount : Number.NEGATIVE_INFINITY,
        _sortExpectedDeltaAmount:
          route.ok && expectedDeltaAmount != null ? expectedDeltaAmount : Number.NEGATIVE_INFINITY,
      };
    })
    .sort((a, b) => {
      if (a.quoteAvailable !== b.quoteAvailable) return a.quoteAvailable ? -1 : 1;
      if (a.profitBudgetMet !== b.profitBudgetMet) return a.profitBudgetMet ? -1 : 1;
      if (a._sortFinalAmount !== b._sortFinalAmount) return b._sortFinalAmount - a._sortFinalAmount;
      if (a._sortExpectedFinalAmount !== b._sortExpectedFinalAmount) {
        return b._sortExpectedFinalAmount - a._sortExpectedFinalAmount;
      }
      return b._sortDeltaAmount - a._sortDeltaAmount;
    })
    .map(({ _sortFinalAmount, _sortExpectedFinalAmount, _sortDeltaAmount, _sortExpectedDeltaAmount, ...item }, rank) => ({
      rank: rank + 1,
      ...item,
    }));
  const best = ranking[0] || null;
  return {
    minProfitHuman: best?.minProfitHuman ?? null,
    minProfitPercent: pureIntentEnabled ? String(pureIntentMinProfitPercent) : null,
    minProfitRule: pureIntentEnabled ? "input_amount_times_percent" : "absolute_usdc",
    minProfitSymbol: "USDC",
    candidateCount: routes.length,
    quotedCandidateCount: routes.filter((route) => route.ok).length,
    selectedRoute: best && best.quoteAvailable
      ? {
          rank: best.rank,
          sourceIndex: best.sourceIndex,
          pair: best.pair,
          routeDirection: best.routeDirection,
          priorityReason: best.priorityReason,
          route: best.route,
          finalAmount: best.finalAmount,
          deltaAmount: best.deltaAmount,
          deltaPercent: best.deltaPercent,
        }
      : null,
    status: best ? (best.quoteAvailable ? "selected_diagnostic_best" : "best_route_unquoted") : "no_routes",
    bestRouteEvenIfBlocked: best,
    ranking,
  };
}

function routeCostAnalysis(routeSpec, tokens, legs) {
  const protectedProfit = routeProfit(routeSpec, tokens, legs, "buyAmountHuman");
  const expectedProfit = routeProfit(routeSpec, tokens, legs, "quoteBuyAmountHuman");
  const totalFlashFees = legs
    .map((leg) => decimalNumber(leg.flashLoanFeeAmountHuman))
    .filter((value) => value != null)
    .reduce((sum, value) => sum + value, 0);
  const totalNetworkFeeInLegSellAssets = legs
    .map((leg) => decimalNumber(leg.costAnalysis?.networkFeeSellHuman))
    .filter((value) => value != null)
    .reduce((sum, value) => sum + value, 0);
  const finalDelta = decimalNumber(protectedProfit?.deltaAmount);
  const expectedDelta = decimalNumber(expectedProfit?.deltaAmount);
  const expectedFinal = decimalNumber(expectedProfit?.finalAmount);
  const protectedFinal = decimalNumber(protectedProfit?.finalAmount);
  const slippageFloorLoss =
    expectedFinal != null && protectedFinal != null ? expectedFinal - protectedFinal : null;
  const slippageRecommendation =
    expectedDelta != null && expectedDelta < 0
      ? "do_not_widen_slippage; solver_price_edge_is_already_negative"
      : expectedDelta != null && expectedDelta > 0 && finalDelta != null && finalDelta <= 0
        ? "keep_or_reduce_slippage; protected_floor_removes_expected_profit"
        : "slippage_within_profitability_budget";
  return {
    finalProfit: protectedProfit,
    expectedProfitBeforeSlippageFloor: expectedProfit,
    protectedProfitAfterSlippageFloor: protectedProfit,
    slippageFloorLossInFinalAsset: slippageFloorLoss == null ? null : String(slippageFloorLoss),
    slippageFloorLossPercentOfExpected: decimalPercent(slippageFloorLoss, expectedFinal),
    slippageRecommendation,
    finalLossDominantCause:
      finalDelta != null && finalDelta < 0
        ? "solver_quote_prices_plus_network_protocol_fees_exceed_binance_window_edge"
        : expectedDelta != null && expectedDelta > 0 && finalDelta != null && finalDelta <= 0
          ? "slippage_floor_turns_expected_profit_into_unprofitable_protected_floor"
          : expectedDelta != null && expectedDelta > 0
            ? "profitable_before_slippage_floor"
            : "unknown",
    flashLoanFeePercentConfigured: null,
    totalFlashLoanFeesInNativeLegUnits: String(totalFlashFees),
    totalNetworkFeesInMixedSellAssetUnits: String(totalNetworkFeeInLegSellAssets),
    slippageBpsConsistent: legs.every((leg) => leg.configuredSlippageBps === legs[0]?.configuredSlippageBps),
    configuredSlippageBps: legs[0]?.configuredSlippageBps ?? null,
    legCount: legs.length,
  };
}

module.exports = {
  ownStepAnalysis,
  legCostAnalysis,
  quoteLeg,
  classifyRoute,
  enrichLiveSignalWithQuote,
  routeProfit,
  routeSelection,
  routeCostAnalysis,
};
