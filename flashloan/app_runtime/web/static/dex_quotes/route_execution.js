// 链上执行状态：结果汇总与建议文案。

function executionRouteTestSummary(result) {
  if (result?.txHash) return `链上执行已提交 | 交易哈希：${result.txHash}`;
  const failureText = result?.detail || result?.errorDetail || result?.error;
  if (result?.ok === false) return failureText ? `链上执行失败：${failureText}` : "链上执行失败：后台未返回交易哈希";
  if (result?.broadcast) return failureText ? `链上执行未提交：${failureText}` : "链上执行未提交：后台未返回交易哈希";
  if (result?.detail && !result?.steps) return String(result.detail);
  if (result?.steps && result.steps.length) {
    const hopText = result.steps.map(step => `第 ${step.hop} 步：${step.fromSymbol} -> ${step.toSymbol} | 输入 ${numeric(step.inputAmount, 8)} -> 输出 ${numeric(step.outputAmount ?? step.referenceOutputAmount, 8)}`).join(" | ");
    const analysis = result.analysis || {};
    const profitText = analysis.referenceNetProfitAmount == null ? "" : `净收益 ${numeric(humanTokenValue(analysis.referenceNetProfitAmount), 8)} ${analysis.referenceInputSymbol || result.amountSymbol || ""}`;
    return `链上执行结果 | ${hopText}${profitText ? ` | ${profitText}` : ""}`;
  }
  return "链上执行已提交，等待链上确认";
}

function executionRouteTestAdvice(result) {
  const error = String(result?.error || result?.detail || result?.errorDetail || "").toLowerCase();
  if (result?.txHash) return "建议：链上执行已提交；请到区块浏览器确认交易哈希与链上结果。";
  if (error.includes("broadcast disabled") || error.includes("static preflight passed")) {
    return "建议：链上执行未提交；当前环境未开启链上执行开关，因此未发起链上交易。";
  }
  if (result?.error === "official_arb_broadcast_route_unsupported" || error.includes("route_unsupported")) {
    return "建议：当前三币闭环无法解析完整代币地址，或不在当前链的链上执行范围内，暂不能执行链上交易；可先查看链下估算，再通过链上复核确认可执行性。";
  }
  if (result?.error === "official_arb_broadcast_disabled" || result?.broadcastDisabled || error.includes("broadcast_disabled")) {
    return "建议：链上执行未提交；链上执行开关未开启，请检查 ROBINHOOD_TESTNET_ALLOW_OFFICIAL_V4_ARB_BROADCAST 配置，并重启服务后重试。";
  }
  if (result?.error === "official_arb_broadcast_timeout" || error.includes("timeout") || error.includes("timed out") || error.includes("rpc_verification_timeout")) {
    return "建议：RPC 网络超时，请稍后重试链上执行；若持续失败，请检查 RPC/节点连接或重启服务。";
  }
  if (error.includes("connect") || error.includes("unavailable") || error.includes("winerror 10061") || error.includes("403 forbidden") || error.includes("rpc_verification_failed")) {
    return "建议：节点/RPC 连接失败，请检查网络或重启服务后重试链上执行。";
  }
  if (error.includes("reverted") || error.includes("not profitable") || error.includes("liquidity")) {
    return "建议：链上执行失败；链上校验未通过或流动性不足，请降低金额或等待流动性恢复后再试。";
  }
  if (result?.ok === false) return "建议：链上执行未成功，请确认链上状态、RPC 配置后重试；仍失败可重启服务。";
  if (result?.broadcast) return "建议：链上执行未返回交易哈希，请先完成链上复核后重试链上执行。";
  return "建议：本次未执行链上交易，请先完成链上复核后再决定是否执行。";
}
