// 链下估算状态：结果汇总、建议文案与估算结果合并。

function mergeRouteTestPayload(route, payload) {
  if (!route || typeof route !== "object" || !payload || typeof payload !== "object") return route;
  const analysis = payload.analysis && typeof payload.analysis === "object" ? payload.analysis : {};
  const steps = Array.isArray(payload.steps) ? payload.steps : Array.isArray(analysis.steps) ? analysis.steps : [];
  const quoteStatus = String(payload.quoteStatus || (payload.liveVerified ? "live_verified" : "live_quote_reference_only")).toLowerCase();
  const executionTrust = payload.executionTrust || (payload.liveVerified ? "live_quoter_verified_readonly" : "static_live_quoter_estimate");
  const amount = payload.bestAmount ?? payload.bestAmountCalculation?.bestAmount ?? payload.bestAmountCalculation?.recommendedBorrowInputAmount ?? analysis.bestAmount ?? analysis.best_amount ?? route.quoteAnalysis?.bestAmount ?? route.quoteAnalysis?.bestAmountCalculation?.bestAmount ?? route.quoteAnalysis?.bestAmountCalculation?.recommendedBorrowInputAmount ?? null;
  const symbol = payload.amountSymbol || payload.initialProbeSymbol || analysis.referenceInputSymbol || (Array.isArray(route.routeSymbols) ? route.routeSymbols[0] : "");
  const mergedAnalysis = {
    ...(route.quoteAnalysis && typeof route.quoteAnalysis === "object" ? route.quoteAnalysis : {}),
    ...analysis,
    steps,
    referenceInputAmount: amount,
    referenceInputSymbol: symbol,
    bestAmount: amount,
    bestAmountConfirmed: payload.bestAmountConfirmed === true
      || analysis.bestAmountConfirmed === true
      || payload.bestAmountCalculation?.bestAmountConfirmed === true,
    bestAmountSelectionBasis: payload.bestAmountSelectionBasis
      || analysis.bestAmountSelectionBasis
      || payload.bestAmountCalculation?.bestAmountSelectionBasis
      || "initial_probe_unconfirmed",
    bestAmountConfirmationReason: payload.bestAmountConfirmationReason
      || analysis.bestAmountConfirmationReason
      || payload.bestAmountCalculation?.bestAmountConfirmationReason
      || "no confirmed best amount",
    amountOptimizationMethod: payload.amountOptimizationMethod
      || analysis.amountOptimizationMethod
      || "live_route_amount_ladder",
    amountOptimized: payload.amountOptimized === true
      || analysis.amountOptimized === true
      || (Number.isFinite(Number(payload.bestAmount)) && Number.isFinite(Number(payload.requestedAmount)) && Math.abs(Number(payload.bestAmount) - Number(payload.requestedAmount)) > 1e-12),
    requestedAmount: payload.requestedAmount ?? analysis.requestedAmount ?? amount,
    initialProbeAmount: payload.initialProbeAmount ?? analysis.initialProbeAmount ?? payload.requestedAmount ?? amount,
    initialProbeSymbol: payload.initialProbeSymbol || analysis.initialProbeSymbol || symbol,
    livePriceVerified: Boolean(payload.liveVerified),
    priceEvidenceMode: payload.liveVerified ? "live_verified" : "live_required",
    priceEvidenceSource: payload.liveVerified ? "chain_live_quote" : "chain_live_quote_required",
    onchainEvidenceStatus: quoteStatus,
  };
  const evidence = {
    status: quoteStatus,
    executionTrust,
    blockNumber: payload.blockNumber || route.live_onchain_evidence?.blockNumber,
    hops: steps,
    routeSymbols: payload.routeSymbols || route.routeSymbols || [],
    costModel: {
      borrowInputSymbol: symbol,
      borrowInputAmount: amount,
      selectedBorrowInputAmount: amount,
      requestedBorrowInputAmount: payload.requestedAmount ?? analysis.requestedAmount ?? amount,
      bestAmountConfirmed: payload.bestAmountConfirmed === true
        || analysis.bestAmountConfirmed === true
        || payload.bestAmountCalculation?.bestAmountConfirmed === true,
      bestAmountSelectionBasis: payload.bestAmountSelectionBasis
        || analysis.bestAmountSelectionBasis
        || payload.bestAmountCalculation?.bestAmountSelectionBasis
        || "initial_probe_unconfirmed",
      bestAmountConfirmationReason: payload.bestAmountConfirmationReason
        || analysis.bestAmountConfirmationReason
        || payload.bestAmountCalculation?.bestAmountConfirmationReason
        || "no confirmed best amount",
      netProfitAmount: humanTokenValue(analysis.referenceNetProfitAmount ?? analysis.netProfitAmount),
      netProfitAmountBps: analysis.referenceNetReturnBps ?? analysis.netProfitBps,
      finalOutputAmount: analysis.referenceFinalOutputAmount ?? analysis.finalOutputAmount,
      finalOutputSymbol: analysis.referenceFinalOutputSymbol || analysis.finalOutputSymbol || symbol,
    },
    recommendation: analysis.recommendation || {},
    blockers: [],
  };
  route.quoteAnalysis = mergedAnalysis;
  route.quoteStatus = quoteStatus;
  route.onchainEvidenceStatus = quoteStatus;
  route.live_onchain_evidence = evidence;
  route.reviewMode = "estimate";
  route.__offchainReviewed = true;
  route.__reviewStage = "estimate";
  setRouteUiState(route, ROUTE_UI_STATE.OFFCHAIN_ESTIMATE, {quoteStatus});
  return route;
}

function estimateRouteTestSummary(result) {
  if (result?.txHash) return `链下估算完成 | 已返回链下估算结果：${result.txHash}`;
  const failureText = result?.detail || result?.errorDetail || result?.error;
  if (result?.ok === false) return failureText ? `链下估算失败：${failureText}` : "链下估算失败：后台未返回结果";
  if (result?.detail && !result?.steps) return String(result.detail);
  if (result?.steps && result.steps.length) {
    const hopText = result.steps.map(step => `第 ${step.hop} 步：${step.fromSymbol} -> ${step.toSymbol} | 输入 ${numeric(step.inputAmount, 8)} -> 输出 ${numeric(step.outputAmount ?? step.referenceOutputAmount, 8)}`).join(" | ");
    const analysis = result.analysis || {};
    const profitText = analysis.referenceNetProfitAmount == null ? "" : `净收益 ${numeric(humanTokenValue(analysis.referenceNetProfitAmount), 8)} ${analysis.referenceInputSymbol || result.amountSymbol || ""}`;
    return `链下估算完成 | ${hopText}${profitText ? ` | ${profitText}` : ""}`;
  }
  return "链下估算完成";
}

function estimateRouteTestAdvice(result) {
  if (result?.txHash) return "建议：链下估算完成；当前金额仅作为链上复核初始量，最佳量由链上复核确认。";
  if (result?.ok === false) return `建议：链下估算未完成；原因：${result?.detail || result?.error || "后台未返回结果"}，请稍后重试或检查路线/池状态。`;
  return "建议：链下估算完成；当前金额仅作为链上合约初始量，最佳量需由链上复核确认。";
}
