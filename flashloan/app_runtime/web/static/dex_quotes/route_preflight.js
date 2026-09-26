// 链上复核状态：结果汇总、建议文案与失败落盘。

function preflightRouteTestSummary(result) {
  if (result?.txHash) return `链上复核完成 | 已返回链上复核结果：${result.txHash}`;
  if (result?.staticCallPassed) return "链上复核通过";
  const failureText = result?.detail || result?.errorDetail || result?.error;
  if (result?.ok === false) return failureText ? `链上复核失败：${failureText}` : "链上复核失败：后台未返回链上结果";
  if (result?.broadcastDisabled && result?.detail) return `链上复核通过 | 未提交链上交易：${result.detail}`;
  if (result?.detail && !result?.steps) return String(result.detail);
  if (result?.steps && result.steps.length) {
    const hopText = result.steps.map(step => `第 ${step.hop} 步：${step.fromSymbol} -> ${step.toSymbol} | 输入 ${numeric(step.inputAmount, 8)} -> 输出 ${numeric(step.outputAmount ?? step.referenceOutputAmount, 8)}`).join(" | ");
    const analysis = result.analysis || {};
    const profitText = analysis.referenceNetProfitAmount == null ? "" : `净收益 ${numeric(humanTokenValue(analysis.referenceNetProfitAmount), 8)} ${analysis.referenceInputSymbol || result.amountSymbol || ""}`;
    return `链上复核通过 | ${hopText}${profitText ? ` | ${profitText}` : ""}`;
  }
  return "链上复核完成";
}

function preflightRouteTestAdvice(result) {
  if (result?.txHash) return "建议：链上复核通过；当前金额已通过链上复核，请确认金额后点击链上执行。";
  if (result?.ok === false) return `建议：链上复核未通过；原因：${result?.detail || result?.error || "后台未返回结果"}，请稍后重试或降低金额/检查流动性。`;
  return "建议：链上复核通过；尚未执行链上交易。请确认金额后点击链上执行。";
}

function markPreflightFailed(route, detail, status = "rpc_verification_failed") {
  if (!route || typeof route !== "object") return route;
  route.__broadcastError = detail || "链上复核失败";
  setRouteUiState(route, ROUTE_UI_STATE.PREFLIGHT_FAILED, {quoteStatus: status});
  if (route.live_onchain_evidence && typeof route.live_onchain_evidence === "object") {
    const costs = route.live_onchain_evidence.costModel || {};
    route.live_onchain_evidence.status = status;
    route.live_onchain_evidence.hops = [];
    route.live_onchain_evidence.costModel = {
      ...costs,
      selectedBorrowInputAmount: costs.requestedBorrowInputAmount ?? null,
      borrowInputAmount: costs.requestedBorrowInputAmount ?? null,
      netProfitAmount: null,
      netProfitAmountBps: null,
      finalOutputAmount: null,
      finalOutputSymbol: costs.finalOutputSymbol ?? costs.borrowInputSymbol ?? null,
    };
  }
  if (route.quoteAnalysis && typeof route.quoteAnalysis === "object") {
    route.quoteAnalysis.steps = [];
    route.quoteAnalysis.referenceFinalOutputAmount = null;
    route.quoteAnalysis.referenceNetProfitAmount = null;
    route.quoteAnalysis.referenceNetReturnBps = null;
    route.quoteAnalysis.probeRecords = [];
    route.quoteAnalysis.probe_records = [];
    route.quoteAnalysis.bestAmountConfirmed = false;
    route.quoteAnalysis.amountOptimized = false;
  }
  return route;
}
