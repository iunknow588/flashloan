// Pure-intent quote and submission orchestration for the CoW flashloan probe CLI.

const { privateKeyToAccount } = require("viem/accounts");
const { OrderKind } = require("@cowprotocol/sdk-order-book");
const { AaveFlashLoanType } = require("@cowprotocol/sdk-flash-loans");
const { envFirst, normalizedPrivateKey } = require("./config");
const {
  formatUnits,
  percentageOfUnits,
  parseHumanUnits,
  bigintFrom,
  quoteAmountHuman,
  jsonFriendly,
} = require("./decimal-math");
const {
  orderToSignSummary,
  quoteResponseSummary,
  appDataSummary,
} = require("./report");
const { requireToken } = require("./token-registry");
const {
  pureIntentTokenScope,
  pureIntentAppData,
  mergeFlashloanAppData,
} = require("./pure-intent");

function resolveOrderSigner(owner) {
  const privateKey = normalizedPrivateKey(
    envFirst(
      "COW_ORDER_SIGNER_PRIVATE_KEY",
      "COW_FLASHLOAN_PROBE_PRIVATE_KEY",
      "LIQUIDATION_EXECUTION_PRIVATE_KEY",
      "LIQUIDATION_SELF_FUNDED_PRIVATE_KEY"
    )
  );
  if (!privateKey) {
    return { available: false, reason: "signer_private_key_missing", signerAddress: null };
  }
  const account = privateKeyToAccount(privateKey);
  const signerAddress = account.address;
  if (owner && signerAddress.toLowerCase() !== String(owner).toLowerCase()) {
    return { available: false, reason: "signer_owner_mismatch", signerAddress, owner };
  }
  return { available: true, reason: "signer_ready", signerAddress, privateKey };
}

async function submitPureIntentOrder({
  enabled,
  routeSpec,
  tradingSdk,
  flashSdk,
  chainId,
  owner,
  quoteParams,
  customAppData,
  principalUnits,
  minProfitUnits,
  intentFeeReserveUnits = 0n,
}) {
  const base = {
    enabled,
    attempted: false,
    submitted: false,
    orderId: null,
    signerAddress: null,
    blockedReason: null,
    error: null,
    quoteCall: null,
    postingCall: null,
    submitCall: null,
    analysis: null,
    startedAt: null,
    finishedAt: null,
  };
  if (!enabled) {
    return { ...base, blockedReason: "order_submission_disabled" };
  }
  const signer = resolveOrderSigner(owner);
  if (!signer.available) {
    return {
      ...base,
      blockedReason: signer.reason,
      signerAddress: signer.signerAddress,
    };
  }

  const startedAt = new Date().toISOString();
  const quoteDecimals = Number(quoteParams.buyTokenDecimals ?? quoteParams.sellTokenDecimals ?? 6);
  try {
    const quoteCall = {
      method: "tradingSdk.getQuote",
      startedAt,
      finishedAt: null,
      ok: false,
      error: null,
      input: {
        chainId,
        owner,
        kind: quoteParams.kind,
        sellToken: quoteParams.sellToken,
        buyToken: quoteParams.buyToken,
        amount: quoteParams.amount,
        slippageBps: quoteParams.slippageBps,
        validTo: quoteParams.validTo,
      },
      result: null,
    };
    const quoteAndPost = await tradingSdk.getQuote(
      {
        ...quoteParams,
        signer: signer.privateKey,
        owner,
      },
      {
        appData: customAppData,
        allowIntermediateEqSellToken: true,
      }
    );
    quoteCall.ok = true;
    quoteCall.finishedAt = new Date().toISOString();
    quoteCall.result = {
      quoteResults: jsonFriendly(quoteAndPost?.quoteResults || null),
      quoteResultKeys: Object.keys(quoteAndPost?.quoteResults || {}),
      hasPostSwapOrderFromQuote: typeof quoteAndPost?.postSwapOrderFromQuote === "function",
    };

    const quoteResults = quoteAndPost?.quoteResults || {};
    const orderToSign = quoteResults.orderToSign || null;
    const quoteAmounts = quoteResults.amountsAndCosts || {};
    const afterSlippageBuyUnits = bigintFrom(
      quoteAmounts.afterSlippage?.buyAmount ?? orderToSign?.buyAmount ?? 0n
    );
    const quotedNetworkFeeBuyUnits = bigintFrom(
      quoteAmounts.costs?.networkFee?.amountInBuyCurrency ?? 0n
    );
    const flashLoanFeeUnits = bigintFrom(quoteParams.flashLoanFeeAmount ?? 0n);
    const totalFeeReserveUnits = quotedNetworkFeeBuyUnits + flashLoanFeeUnits + bigintFrom(intentFeeReserveUnits);
    const netProfitUnits = afterSlippageBuyUnits - principalUnits - totalFeeReserveUnits;
    const requiredSellUnits = bigintFrom(orderToSign?.sellAmount ?? 0n);
    const maxSellUnits = principalUnits > totalFeeReserveUnits ? principalUnits - totalFeeReserveUnits : 0n;
    const sellBudgetPassed = requiredSellUnits <= maxSellUnits;
    const profitBudgetMet = netProfitUnits >= minProfitUnits;
    const submissionAnalysis = {
      principalUnits: String(principalUnits),
      afterSlippageBuyUnits: String(afterSlippageBuyUnits),
      quotedNetworkFeeBuyUnits: String(quotedNetworkFeeBuyUnits),
      flashLoanFeeUnits: String(flashLoanFeeUnits),
      intentFeeReserveUnits: String(intentFeeReserveUnits),
      totalFeeReserveUnits: String(totalFeeReserveUnits),
      requiredSellUnits: String(requiredSellUnits),
      maxSellUnits: String(maxSellUnits),
      netProfitUnits: String(netProfitUnits),
      principalHuman: quoteAmountHuman(principalUnits, quoteDecimals),
      afterSlippageBuyHuman: quoteAmountHuman(afterSlippageBuyUnits, quoteDecimals),
      quotedNetworkFeeBuyHuman: quoteAmountHuman(quotedNetworkFeeBuyUnits, quoteDecimals),
      flashLoanFeeHuman: quoteAmountHuman(flashLoanFeeUnits, quoteDecimals),
      intentFeeReserveHuman: quoteAmountHuman(intentFeeReserveUnits, quoteDecimals),
      totalFeeReserveHuman: quoteAmountHuman(totalFeeReserveUnits, quoteDecimals),
      requiredSellHuman: quoteAmountHuman(requiredSellUnits, quoteDecimals),
      maxSellHuman: quoteAmountHuman(maxSellUnits, quoteDecimals),
      netProfitHuman: quoteAmountHuman(netProfitUnits, quoteDecimals),
      minProfitHuman: quoteAmountHuman(minProfitUnits, quoteDecimals),
      profitBudgetMet,
      sellBudgetPassed,
      diagnosticReason: !profitBudgetMet
        ? "net_profit_below_percentage_floor"
        : !sellBudgetPassed
          ? "sell_budget_exceeds_principal_after_costs"
          : null,
    };
    if (!orderToSign) {
      return {
        ...base,
        attempted: false,
        signerAddress: signer.signerAddress,
        blockedReason: "submission_quote_missing_order",
        startedAt,
        finishedAt: new Date().toISOString(),
        quoteCall,
        analysis: submissionAnalysis,
      };
    }

    const posting = await flashSdk.getOrderPostingSettings(
      AaveFlashLoanType.CollateralSwap,
      quoteParams,
      {
        flashLoanAmount: principalUnits,
        orderToSign,
      }
    );
    const postingCall = {
      method: "flashSdk.getOrderPostingSettings",
      startedAt,
      finishedAt: new Date().toISOString(),
      ok: true,
      error: null,
      input: {
        flashLoanAmount: String(principalUnits),
        chainId,
        owner,
      },
      result: jsonFriendly(posting || null),
    };
    const swapSettings = {
      ...posting.swapSettings,
      appData: mergeFlashloanAppData(customAppData, posting.swapSettings?.appData),
    };
    const submitStartedAt = new Date().toISOString();
    let result = null;
    let submitError = null;
    try {
      result = await quoteAndPost.postSwapOrderFromQuote(swapSettings);
    } catch (error) {
      submitError = error;
    }
    const submitCall = {
      method: "quoteAndPost.postSwapOrderFromQuote",
      startedAt: submitStartedAt,
      finishedAt: new Date().toISOString(),
      ok: !submitError,
      error: submitError ? (submitError?.message || String(submitError)) : null,
      result: jsonFriendly(result || null),
    };
    return {
      ...base,
      attempted: true,
      submitted: Boolean(result?.orderId),
      orderId: result?.orderId || null,
      signerAddress: signer.signerAddress,
      blockedReason: submitError
        ? "order_submission_failed"
        : result?.orderId
          ? null
          : "order_submission_returned_no_order_id",
      startedAt,
      finishedAt: new Date().toISOString(),
      quoteCall,
      postingCall,
      submitCall,
      analysis: submissionAnalysis,
      route: routeSpec.route,
    };
  } catch (submitError) {
    return {
      ...base,
      attempted: true,
      signerAddress: signer.signerAddress,
      blockedReason: "order_submission_failed",
      error: submitError?.message || String(submitError),
      startedAt,
      finishedAt: new Date().toISOString(),
      quoteCall: {
        method: "tradingSdk.getQuote",
        startedAt,
        finishedAt: new Date().toISOString(),
        ok: false,
        error: submitError?.message || String(submitError),
        result: null,
      },
    };
  }
}

async function probePureIntent({
  routeSpec,
  registry,
  tradingSdk,
  flashSdk,
  chainId,
  owner,
  flashLoanFeePercent,
  slippageBps,
}) {
  const usdc = requireToken(registry, "USDC");
  const principalUnits = BigInt(routeSpec.amountUnits);
  const flashLoanFeeBps = Math.round(flashLoanFeePercent * 100);
  const { flashLoanFeeAmount } = flashSdk.calculateFlashLoanAmounts({
    sellAmount: principalUnits,
    flashLoanFeeBps,
  });
  const minProfitUnits = percentageOfUnits(
    principalUnits,
    routeSpec.pureIntent.minProfitPercentHuman
  );
  const gasReserveUnits = BigInt(parseHumanUnits(routeSpec.pureIntent.gasReserveHuman, usdc.decimals));
  const otherKnownCostsUnits = BigInt(
    parseHumanUnits(routeSpec.pureIntent.otherKnownCostsHuman, usdc.decimals)
  );
  const knownCostsUnits = flashLoanFeeAmount + gasReserveUnits + otherKnownCostsUnits;
  const targetBuyUnits = principalUnits + knownCostsUnits + minProfitUnits;
  const validTo = Math.ceil(Date.now() / 1000) + 300;
  const quoteParams = {
    chainId,
    owner,
    kind: OrderKind.BUY,
    sellToken: usdc.address,
    sellTokenDecimals: usdc.decimals,
    buyToken: usdc.address,
    buyTokenDecimals: usdc.decimals,
    amount: targetBuyUnits.toString(),
    validTo,
    slippageBps,
    flashLoanFeeAmount,
  };
  const customAppData = pureIntentAppData({
    slippageBps,
    tokenScope: routeSpec.pureIntent.tokenScope || pureIntentTokenScope(routeSpec),
    principalHuman: routeSpec.pureIntent.initialAmountHuman,
    minFinalAmountHuman: routeSpec.pureIntent.initialAmountHuman,
  });
  let quote = null;
  let appDataAttempt = "custom_intent";
  let customAppDataError = null;
  let error = null;
  try {
    quote = await tradingSdk.getQuoteOnly(quoteParams, {
      appData: customAppData,
      allowIntermediateEqSellToken: true,
    });
  } catch (firstError) {
    customAppDataError = firstError?.message || String(firstError);
    appDataAttempt = "standard_fallback";
    try {
      quote = await tradingSdk.getQuoteOnly(quoteParams, {
        allowIntermediateEqSellToken: true,
      });
    } catch (secondError) {
      error = `custom_app_data: ${customAppDataError}; standard_fallback: ${secondError?.message || String(secondError)}`;
    }
  }
  if (error || !quote?.orderToSign) {
    return {
      ok: false,
      network: routeSpec.network,
      pair: routeSpec.pair,
      pairRank: routeSpec.pairRank,
      routeDirection: routeSpec.routeDirection,
      priorityReason: routeSpec.priorityReason,
      observedAt: routeSpec.observedAt,
      route: routeSpec.route,
      amountHuman: routeSpec.amountHuman,
      classification: "pure_intent_quote_failed",
      error: error || "pure intent quote returned no order",
      intent: {
        ...routeSpec.pureIntent,
        targetBuyAmountHuman: formatUnits(targetBuyUnits, usdc.decimals),
        minPureProfitHuman: formatUnits(minProfitUnits, usdc.decimals),
        flashLoanFeeHuman: formatUnits(flashLoanFeeAmount, usdc.decimals),
        gasReserveHuman: formatUnits(gasReserveUnits, usdc.decimals),
        otherKnownCostsHuman: formatUnits(otherKnownCostsUnits, usdc.decimals),
        knownCostsHuman: formatUnits(knownCostsUnits, usdc.decimals),
        appDataAttempt,
        customAppDataAccepted: false,
        customAppDataError,
      },
      tokenScope: routeSpec.pureIntent.tokenScope,
      legs: [],
      profit: null,
      netProfit: null,
      costAnalysis: {
        pureIntent: true,
        minProfitPercent: routeSpec.pureIntent.minProfitPercentHuman,
        requiredMinPureProfitHuman: formatUnits(minProfitUnits, usdc.decimals),
        knownCostsHuman: formatUnits(knownCostsUnits, usdc.decimals),
        error,
      },
      singleSolverSettlement: {
        model: "pure_intent_single_solver_settlement",
        testedSemantics: "quote_only",
        proofStatus: "not_proven_quote_only",
      },
    };
  }
  let posting = null;
  try {
    posting = await flashSdk.getOrderPostingSettings(
      AaveFlashLoanType.CollateralSwap,
      quoteParams,
      {
        flashLoanAmount: principalUnits,
        orderToSign: quote.orderToSign,
      }
    );
  } catch (postingError) {
    error = postingError?.message || String(postingError);
  }
  const order = quote.orderToSign;
  const protectedBuyUnits = BigInt(order.buyAmount);
  const requiredSellUnits = BigInt(order.sellAmount);
  const maxSellUnits = principalUnits > knownCostsUnits ? principalUnits - knownCostsUnits : 0n;
  const grossDeltaUnits = protectedBuyUnits - principalUnits;
  const netDeltaUnits = grossDeltaUnits - knownCostsUnits;
  const sellBudgetPassed = requiredSellUnits <= maxSellUnits;
  const sellBudgetExcessUnits = requiredSellUnits > maxSellUnits ? requiredSellUnits - maxSellUnits : 0n;
  const profitBudgetMet = netDeltaUnits >= minProfitUnits;
  const feasible = !error && Boolean(quote?.orderToSign);
  const protectedBuyHuman = formatUnits(protectedBuyUnits, usdc.decimals);
  const netProfitHuman = formatUnits(netDeltaUnits, usdc.decimals);
  const postingMetadata = posting?.swapSettings?.appData?.metadata || {};
  const quoteAppData = appDataSummary(quote.appDataInfo, postingMetadata);
  const quoteReport = {
    tradeParameters: {
      kind: "buy",
      sellToken: usdc.symbol,
      buyToken: usdc.symbol,
      targetBuyAmountHuman: formatUnits(targetBuyUnits, usdc.decimals),
      requiredSellAmountHuman: formatUnits(requiredSellUnits, usdc.decimals),
      maxSellAmountHuman: formatUnits(maxSellUnits, usdc.decimals),
      sellBudgetExcessHuman: formatUnits(sellBudgetExcessUnits, usdc.decimals),
    },
    orderToSign: orderToSignSummary(order),
    quoteResponse: quoteResponseSummary(quote.quoteResponse),
    amountsAndCosts: jsonFriendly(quote.amountsAndCosts || null),
    appData: quoteAppData,
    postingAppDataMetadataKeys: Object.keys(postingMetadata),
    customAppDataAccepted: appDataAttempt === "custom_intent" && !customAppDataError,
  };
  return {
    ok: feasible,
    network: routeSpec.network,
    pair: routeSpec.pair,
    pairRank: routeSpec.pairRank,
    routeDirection: routeSpec.routeDirection,
    priorityReason: routeSpec.priorityReason,
    observedAt: routeSpec.observedAt,
    liveSignal: routeSpec.liveSignal || null,
    route: routeSpec.route,
    amountHuman: routeSpec.amountHuman,
    amountUnits: routeSpec.amountUnits,
    classification: feasible ? "pure_intent_quote_returned" : "pure_intent_quote_failed",
    error,
    tokenScope: routeSpec.pureIntent.tokenScope,
    intent: {
      ...routeSpec.pureIntent,
      targetBuyAmountHuman: formatUnits(targetBuyUnits, usdc.decimals),
      minPureProfitHuman: formatUnits(minProfitUnits, usdc.decimals),
      protectedBuyAmountHuman: protectedBuyHuman,
      requiredSellAmountHuman: formatUnits(requiredSellUnits, usdc.decimals),
      maxSellAmountHuman: formatUnits(maxSellUnits, usdc.decimals),
      flashLoanFeeHuman: formatUnits(flashLoanFeeAmount, usdc.decimals),
      gasReserveHuman: formatUnits(gasReserveUnits, usdc.decimals),
      otherKnownCostsHuman: formatUnits(otherKnownCostsUnits, usdc.decimals),
      knownCostsHuman: formatUnits(knownCostsUnits, usdc.decimals),
      sellBudgetCapHuman: formatUnits(maxSellUnits, usdc.decimals),
      sellBudgetExcessHuman: formatUnits(sellBudgetExcessUnits, usdc.decimals),
      sellBudgetPassed,
      profitBudgetMet,
      appDataAttempt,
      customAppDataAccepted: appDataAttempt === "custom_intent" && !customAppDataError,
      customAppDataError,
    },
    quote: quoteReport,
    profit: {
      inputAmount: routeSpec.amountHuman,
      finalAmount: protectedBuyHuman,
      deltaAmount: formatUnits(grossDeltaUnits, usdc.decimals),
      deltaPercent: String(Number(grossDeltaUnits) / Number(principalUnits) * 100),
      symbol: usdc.symbol,
    },
    netProfit: {
      inputAmount: routeSpec.amountHuman,
      finalAmount: netProfitHuman,
      deltaAmount: netProfitHuman,
      deltaPercent: String(Number(netDeltaUnits) / Number(principalUnits) * 100),
      symbol: usdc.symbol,
    },
    costAnalysis: {
      pureIntent: true,
      grossProfitBeforeFlashLoanAndGas: formatUnits(grossDeltaUnits, usdc.decimals),
      netProfitAfterAllCosts: {
        inputAmount: routeSpec.amountHuman,
        finalAmount: netProfitHuman,
        deltaAmount: netProfitHuman,
        deltaPercent: String(Number(netDeltaUnits) / Number(principalUnits) * 100),
        symbol: usdc.symbol,
      },
      minProfitPercent: routeSpec.pureIntent.minProfitPercentHuman,
      requiredMinPureProfitHuman: formatUnits(minProfitUnits, usdc.decimals),
      flashLoanFeeHuman: formatUnits(flashLoanFeeAmount, usdc.decimals),
      gasReserveHuman: formatUnits(gasReserveUnits, usdc.decimals),
      otherKnownCostsHuman: formatUnits(otherKnownCostsUnits, usdc.decimals),
      knownCostsHuman: formatUnits(knownCostsUnits, usdc.decimals),
      sellBudgetCapHuman: formatUnits(maxSellUnits, usdc.decimals),
      sellBudgetExcessHuman: formatUnits(sellBudgetExcessUnits, usdc.decimals),
      protectedBuyAmountHuman: protectedBuyHuman,
      requiredSellAmountHuman: formatUnits(requiredSellUnits, usdc.decimals),
      maxSellAmountHuman: formatUnits(maxSellUnits, usdc.decimals),
      sellBudgetPassed,
      profitBudgetMet,
      appData: quoteReport.appData,
    },
    legs: [],
    singleSolverSettlement: {
      model: "pure_intent_single_solver_settlement",
      requestedSemantics: "one_starting_asset_one_flashloan_one_cow_solver_settlement",
      testedSemantics: "single_intent_quote_only",
      borrowedAsset: usdc.symbol,
      repaidAsset: usdc.symbol,
      route: routeSpec.route,
      hopCount: 0,
      singleStartingAsset: true,
      borrowedAndRepaidSameAsset: true,
      flashLoanCount: 1,
      solverOrderCount: 1,
      settlementTransactionCount: 1,
      proofStatus: "not_proven_quote_only",
    },
  };
}


module.exports = {
  resolveOrderSigner,
  submitPureIntentOrder,
  probePureIntent,
};
