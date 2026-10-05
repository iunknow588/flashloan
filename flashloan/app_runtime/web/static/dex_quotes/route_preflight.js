// 链上复核状态：结果汇总、建议文案与失败落盘。

function routeTestStatusText(result) {
  const status = String(
    result?.quoteStatus || result?.evidenceStatus || result?.executionTrust || ""
  ).toLowerCase();
  const map = {
    "rpc_verification_timeout": "链上复核超时：RPC 未在期限内返回",
    "rpc_verification_failed": "链上复核失败：RPC 不可用",
    "live_quote_failed": "链上报价失败",
    "quote_failed": "链上报价失败",
    "not_enough_liquidity": "链上复核：池当前没有 LP 流动性",
    "pool_not_initialized": "V4 池未初始化",
    "missing_effective_pool": "链上复核：池未初始化或缺少有效池",
    "missing_route_pool_identity": "缺少中间桥池 PoolKey",
    "missing_route_identity": "缺少路线身份",
    "missing_configuration": "缺少链上配置",
    "missing_trigger_block": "链上复核未执行：缺少触发区块上下文",
    "adapter_required": "链上复核失败：缺少 V4 报价适配器",
    "slippage_limit_exceeded": "链上已验证，但至少一个单跳价格影响超过 10 bps 或无法确认",
    "route_unprofitable": "链上报价已验证，但净收益未转正",
    "pool_insufficient_for_profitable_execution": "链上已验证，池深度不足以盈利执行",
  };
  return map[status] || "";
}

function routeTestTerminalStatus(result) {
  return Boolean(routeTestStatusText(result));
}

function preflightResultPassed(result) {
  if (!result || typeof result !== "object") return false;
  const analysis = result.analysis && typeof result.analysis === "object" ? result.analysis : {};
  const calc = result.bestAmountCalculation && typeof result.bestAmountCalculation === "object" ? result.bestAmountCalculation : {};
  const bestAmountUnconfirmed = result.bestAmountMissing === true
    || analysis.bestAmountMissing === true
    || calc.bestAmountMissing === true;
  const bestAmountConfirmed = result.bestAmountConfirmed === true
    || analysis.bestAmountConfirmed === true
    || calc.bestAmountConfirmed === true;
  const ready = result.readyForTransactionPreflight === true
    || result.recommendation?.readyForTransactionPreflight === true;
  const readyBlockedExplicit = result.readyForTransactionPreflight === false
    || result.recommendation?.readyForTransactionPreflight === false;
  return Boolean(
    !bestAmountUnconfirmed
    && !readyBlockedExplicit
    && bestAmountConfirmed
    && (ready || result.staticCallPassed)
  );
}

function preflightRouteTestSummary(result) {
  const failureText = String(result?.detail || result?.errorDetail || result?.error || "").trim().replace(/^链上复核(?:失败|未通过)?[:：，]?\s*/, "");
  const terminalText = routeTestStatusText(result);
  if (result?.ok === false) return failureText
    ? `链上复核失败：${terminalText || failureText}`
    : (terminalText ? `链上复核失败：${terminalText}` : "链上复核失败：后台未返回链上结果");
  if (terminalText) return `链上复核失败：${terminalText}`;
  if (result?.txHash) return `链上复核完成 | 已返回链上复核结果：${result.txHash}`;
  if (!preflightResultPassed(result)) return "链上复核未通过：最佳量未计算成功";
  if (result?.staticCallPassed) return "链上复核通过";
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
  const terminalText = routeTestStatusText(result);
  const status = String(result?.quoteStatus || result?.evidenceStatus || result?.executionTrust || "").toLowerCase();
  const rpcFailure = !terminalText || status.includes("rpc_verification") || status.includes("rpc") || /timeout|timed out|unavailable/i.test(String(result?.detail || result?.error || ""));
  const rawDetail = String(result?.detail || result?.error || terminalText || "后台未返回结果").trim().replace(/[。！\s]+$/, "");
  const detail = ["missing_route_pool_identity", "missing_route_identity", "missing_configuration"].includes(status)
    ? rawDetail
    : terminalText || rawDetail;
  const detailAlreadyActionable = /请[^。]*后重试|请稍后|请检查|请先补全|请先补充/.test(detail);
  if (result?.ok === false) return detailAlreadyActionable
    ? `建议：链上复核未通过；原因：${detail}。`
    : `建议：链上复核未通过；原因：${detail}，${rpcFailure ? "请稍后重试或检查 RPC 配置" : "请根据原因调整路线/池配置后重试"}。`;
  if (terminalText) {
    if (status === "missing_route_pool_identity") return "建议：链上复核未通过；原因：缺少中间桥池 PoolKey；请先补全市场观察中的中间桥池身份后重试，不能作为已确认最佳量。";
    if (status === "missing_route_identity") return "建议：链上复核未通过；原因：缺少路线身份；请先补全路由地址/池身份后重试，不能作为已确认最佳量。";
    if (status === "missing_configuration") return "建议：链上复核未通过；原因：缺少链上配置；请检查官方 V4 仲裁合约和链级配置后重试。";
    return `建议：链上复核未通过；原因：${terminalText}，${rpcFailure ? "请稍后重试或检查 RPC 配置" : "请根据上述原因处理后重试，不能作为已确认最佳量"}。`;
  }
  if (!preflightResultPassed(result)) return "建议：链上复核未通过：最佳量未计算成功，不能进入链上执行；请重新链上复核并完成最佳量计算后再执行。";
  return "建议：链上复核通过；尚未执行链上交易。请确认金额后点击链上执行。";
}

function markPreflightFailed(route, detail, status = "rpc_verification_failed") {
  if (!route || typeof route !== "object") return route;
  route.__broadcastError = detail || "链上复核失败";
  route.bestAmountMissing = true;
  route.bestAmountConfirmed = false;
  setRouteUiState(route, ROUTE_UI_STATE.PREFLIGHT_FAILED, {quoteStatus: status});
  if (!route.live_onchain_evidence || typeof route.live_onchain_evidence !== "object") {
    route.live_onchain_evidence = {};
  }
  const evidence = route.live_onchain_evidence;
  const costs = evidence.costModel || {};
  evidence.status = status;
  evidence.executionTrust = status;
  evidence.offchainInitialProbeReason = evidence.offchainInitialProbeReason || "requested_borrow_amount_fallback";
  evidence.hops = [];
  evidence.costModel = {
    ...costs,
    bestAmountMissing: true,
    bestAmountConfirmed: false,
    selectedBorrowInputAmount: costs.requestedBorrowInputAmount ?? null,
    borrowInputAmount: costs.requestedBorrowInputAmount ?? null,
    netProfitAmount: null,
    netProfitAmountBps: null,
    finalOutputAmount: null,
    finalOutputSymbol: costs.finalOutputSymbol ?? costs.borrowInputSymbol ?? null,
  };
  if (route.quoteAnalysis && typeof route.quoteAnalysis === "object") {
    route.quoteAnalysis.steps = [];
    route.quoteAnalysis.referenceFinalOutputAmount = null;
    route.quoteAnalysis.referenceNetProfitAmount = null;
    route.quoteAnalysis.referenceNetReturnBps = null;
    route.quoteAnalysis.probeRecords = [];
    route.quoteAnalysis.probe_records = [];
    route.quoteAnalysis.bestAmountMissing = true;
    route.quoteAnalysis.bestAmountConfirmed = false;
    route.quoteAnalysis.amountOptimized = false;
  }
  return route;
}
