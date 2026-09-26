// 报价与路由页面：路线级状态机实现。
// 主页面只负责调用转换函数，不再散落维护 __reviewStage / reviewMode 的分支。

const routeEvidence = route => route?.live_onchain_evidence || route?.quoteAnalysis?.live_onchain_evidence || route?.quote?.live_onchain_evidence || {};

const routeHasCompletedOffchainReview = route => {
  const evidence = routeEvidence(route);
  if (!evidence || typeof evidence !== "object" || !Object.keys(evidence).length) return false;
  if (Array.isArray(evidence.hops) && evidence.hops.length) return true;
  const quoteAnalysis = route?.quoteAnalysis && typeof route.quoteAnalysis === "object" ? route.quoteAnalysis : {};
  if (Array.isArray(quoteAnalysis.steps) && quoteAnalysis.steps.length) return true;
  const hasCost = evidence.costModel && typeof evidence.costModel === "object" && Object.keys(evidence.costModel).length;
  const hasStatus = String(evidence.status || "").trim() !== "";
  const hasBlock = String(evidence.blockNumber || evidence.triggerBlockNumber || "").trim() !== "";
  return Boolean(hasCost || hasStatus || hasBlock);
};

const ROUTE_UI_STATE = Object.freeze({
  OFFCHAIN_ESTIMATE: "offchain_estimate",
  OFFCHAIN_PENDING: "offchain_pending",
  PREFLIGHT_RUNNING: "preflight_running",
  PREFLIGHT_DONE: "preflight_done",
  PREFLIGHT_FAILED: "preflight_failed",
  EXECUTION_PENDING: "execution_pending",
  EXECUTION_SUBMITTED: "execution_submitted",
  EXECUTION_FAILED: "execution_failed",
});

const ROUTE_UI_TRANSITIONS = Object.freeze({
  [ROUTE_UI_STATE.OFFCHAIN_PENDING]: [
    ROUTE_UI_STATE.OFFCHAIN_ESTIMATE,
    ROUTE_UI_STATE.PREFLIGHT_RUNNING,
    ROUTE_UI_STATE.PREFLIGHT_DONE,
    ROUTE_UI_STATE.EXECUTION_PENDING,
  ],
  [ROUTE_UI_STATE.OFFCHAIN_ESTIMATE]: [
    ROUTE_UI_STATE.OFFCHAIN_ESTIMATE,
    ROUTE_UI_STATE.PREFLIGHT_RUNNING,
    ROUTE_UI_STATE.PREFLIGHT_DONE,
    ROUTE_UI_STATE.EXECUTION_PENDING,
  ],
  [ROUTE_UI_STATE.PREFLIGHT_RUNNING]: [
    ROUTE_UI_STATE.PREFLIGHT_DONE,
    ROUTE_UI_STATE.PREFLIGHT_FAILED,
  ],
  [ROUTE_UI_STATE.PREFLIGHT_DONE]: [
    ROUTE_UI_STATE.PREFLIGHT_RUNNING,
    ROUTE_UI_STATE.PREFLIGHT_FAILED,
    ROUTE_UI_STATE.EXECUTION_PENDING,
  ],
  [ROUTE_UI_STATE.PREFLIGHT_FAILED]: [
    ROUTE_UI_STATE.PREFLIGHT_RUNNING,
  ],
  [ROUTE_UI_STATE.EXECUTION_PENDING]: [
    ROUTE_UI_STATE.EXECUTION_SUBMITTED,
    ROUTE_UI_STATE.EXECUTION_FAILED,
  ],
  [ROUTE_UI_STATE.EXECUTION_SUBMITTED]: [
    ROUTE_UI_STATE.EXECUTION_FAILED,
    ROUTE_UI_STATE.EXECUTION_PENDING,
  ],
  [ROUTE_UI_STATE.EXECUTION_FAILED]: [
    ROUTE_UI_STATE.EXECUTION_PENDING,
  ],
});

const inferUiState = route => {
  const stage = String(route?.__reviewStage || "");
  if (["preflight_failed"].includes(stage)) return ROUTE_UI_STATE.PREFLIGHT_FAILED;
  if (stage === "preflight") return ROUTE_UI_STATE.PREFLIGHT_DONE;
  if (stage === "execution_pending") return ROUTE_UI_STATE.EXECUTION_PENDING;
  if (stage === "execution_failed") return ROUTE_UI_STATE.EXECUTION_FAILED;
  if (stage === "execution") return ROUTE_UI_STATE.EXECUTION_SUBMITTED;
  if (route?.reviewMode === "pending_offchain_review") return ROUTE_UI_STATE.OFFCHAIN_PENDING;
  const evidence = routeEvidence(route);
  if (
    evidence &&
    typeof evidence === "object" &&
    (
      String(evidence.status || "").trim() !== "" ||
      (evidence.costModel && typeof evidence.costModel === "object" && evidence.costModel.selectedBorrowInputAmountRaw != null)
    )
  ) {
    return ROUTE_UI_STATE.PREFLIGHT_DONE;
  }
  return ROUTE_UI_STATE.OFFCHAIN_ESTIMATE;
};

const uiStateOf = route => String(route?.__uiState || inferUiState(route) || ROUTE_UI_STATE.OFFCHAIN_ESTIMATE);

const hasConfirmedBestAmount = route => {
  const evidence = routeEvidence(route);
  const analysis = route?.quoteAnalysis && typeof route.quoteAnalysis === "object" ? route.quoteAnalysis : {};
  if (
    evidence &&
    typeof evidence === "object" &&
    (evidence.costModel && typeof evidence.costModel === "object" && evidence.costModel.selectedBorrowInputAmount != null)
  ) {
    return true;
  }
  if (String(evidence?.status || "").toLowerCase() === "live_verified") return true;
  return analysis.bestAmountConfirmed === true;
};

const effectiveUiState = route => {
  const state = uiStateOf(route);
  if (state === ROUTE_UI_STATE.OFFCHAIN_ESTIMATE && hasConfirmedBestAmount(route)) return ROUTE_UI_STATE.PREFLIGHT_DONE;
  return state;
};

const routeBestAmountPick = (route, evidence, analysis) => {
  const safeEvidence = evidence && typeof evidence === "object" ? evidence : {};
  const safeAnalysis = analysis && typeof analysis === "object" ? analysis : {};
  const costs = safeEvidence.costModel && typeof safeEvidence.costModel === "object" ? safeEvidence.costModel : {};
  const analysisBest = safeAnalysis.bestAmount ?? safeAnalysis.best_amount ?? null;
  const calcBest =
    safeAnalysis.bestAmountCalculation?.bestAmount ??
    route?.bestAmountCalculation?.bestAmount ??
    route?.quoteAnalysis?.bestAmountCalculation?.bestAmount ??
    null;
  const sizeBest =
    route?.sizeAnalysis?.recommendedBorrowInputAmount ??
    route?.sizeAnalysis?.tokenAmountSteps?.[0] ??
    null;
  return costs.selectedBorrowInputAmount ?? costs.borrowInputAmount ?? analysisBest ?? calcBest ?? sizeBest ?? null;
};

const canAutoEstimate = route => [ROUTE_UI_STATE.OFFCHAIN_ESTIMATE, ROUTE_UI_STATE.OFFCHAIN_PENDING].includes(effectiveUiState(route));
const canAutoRefresh = route => [ROUTE_UI_STATE.OFFCHAIN_ESTIMATE, ROUTE_UI_STATE.OFFCHAIN_PENDING].includes(effectiveUiState(route));

const shouldPreserveRouteState = route => [
  ROUTE_UI_STATE.PREFLIGHT_RUNNING,
  ROUTE_UI_STATE.PREFLIGHT_DONE,
  ROUTE_UI_STATE.PREFLIGHT_FAILED,
  ROUTE_UI_STATE.EXECUTION_PENDING,
  ROUTE_UI_STATE.EXECUTION_SUBMITTED,
  ROUTE_UI_STATE.EXECUTION_FAILED,
].includes(effectiveUiState(route));

const uiStageFromUiState = state => {
  if (state === ROUTE_UI_STATE.PREFLIGHT_DONE) return "preflight";
  if (state === ROUTE_UI_STATE.PREFLIGHT_FAILED) return "preflight_failed";
  if (state === ROUTE_UI_STATE.EXECUTION_SUBMITTED) return "execution";
  if (state === ROUTE_UI_STATE.EXECUTION_PENDING) return "execution_pending";
  if (state === ROUTE_UI_STATE.EXECUTION_FAILED) return "execution_failed";
  return "estimate";
};

const uiStateFromStage = stage => {
  if (stage === "preflight_failed") return ROUTE_UI_STATE.PREFLIGHT_FAILED;
  if (stage === "preflight") return ROUTE_UI_STATE.PREFLIGHT_DONE;
  if (stage === "execution_pending") return ROUTE_UI_STATE.EXECUTION_PENDING;
  if (stage === "execution_failed") return ROUTE_UI_STATE.EXECUTION_FAILED;
  if (stage === "execution") return ROUTE_UI_STATE.EXECUTION_SUBMITTED;
  return ROUTE_UI_STATE.OFFCHAIN_ESTIMATE;
};

const setRouteUiState = (route, nextState, meta = {}) => {
  if (!route || typeof route !== "object") return route;
  const currentState = uiStateOf(route);
  const allowed = ROUTE_UI_TRANSITIONS[currentState] || [];
  const tracked = Object.values(ROUTE_UI_STATE).includes(String(route.__uiState || ""));
  if (tracked && currentState !== nextState && !allowed.includes(nextState)) return route;
  route.__uiState = nextState;
  const stage = uiStageFromUiState(nextState);
  route.__reviewStage = stage;
  route.reviewMode = nextState;
  route.onchainEvidenceStatus = meta.quoteStatus || route.onchainEvidenceStatus || route.quoteStatus || "";
  route.quoteStatus = meta.quoteStatus || route.quoteStatus || route.onchainEvidenceStatus || "";
  return route;
};

const estimateBestAmountHint = ({amount, symbol, initialAmount, initialSymbol}) => {
  if (Number.isFinite(Number(initialAmount)) && Math.abs(Number(amount) - Number(initialAmount)) / Number(initialAmount) > 0.01) {
    return `量级提示=链下估算初始量 ${amount} ${symbol}；初始探测 ${initialAmount} ${initialSymbol} 只是起点，最终最佳量由链上复核确认`;
  }
  return `量级提示=链下估算初始量 ${amount} ${symbol}；仅作为链上合约初始值，最佳量由链上复核确认`;
};

const preflightBestAmountHint = ({amount, symbol}) => {
  return `量级提示=本次链上复核确认最佳交易量 ${amount} ${symbol}；当前未执行链上交易。`;
};

const executionBestAmountHint = ({amount, symbol}) => {
  return `量级提示=本次链上执行选用金额为 ${amount} ${symbol}`;
};

const bestAmountHint = ({evidence, analysis, amount, symbol, initialAmount, initialSymbol, executionCount = 0, stage = "estimate"}) => {
  if (["execution", "execution_pending", "execution_failed"].includes(stage)) {
    return executionBestAmountHint({amount, symbol});
  }
  if (stage === "preflight") {
    return preflightBestAmountHint({amount, symbol});
  }
  return estimateBestAmountHint({evidence, analysis, amount, symbol, initialAmount, initialSymbol});
};

const estimateBestAmountDisplayLabel = () => "链下估算初始量";
const preflightBestAmountDisplayLabel = () => "最佳交易量";
const executionBestAmountDisplayLabel = () => "链上执行金额";

const bestAmountDisplayLabel = ({evidence, analysis, stage = "estimate"}) => {
  if (["execution", "execution_pending", "execution_failed"].includes(stage)) return executionBestAmountDisplayLabel();
  if (stage === "preflight" || stage === "preflight_failed") return preflightBestAmountDisplayLabel();
  return estimateBestAmountDisplayLabel({evidence, analysis});
};

function normalizedReviewStage(route, latestBroadcast) {
  if (latestBroadcast?.txHashes?.length) return "execution";
  return uiStageFromUiState(effectiveUiState(route));
}

const estimateStageQuoteStatus = ({pending, terminalStatus, evidenceStatus}) => {
  if (pending) return "链下估算完成";
  if (terminalStatus) return evidenceStatusEx(evidenceStatus);
  return "链下估算完成";
};

const preflightStageQuoteStatus = ({pending, terminalStatus, evidenceStatus}) => {
  if (terminalStatus === "preflight_failed") return "链上复核失败";
  if (pending) return "链上复核待开始";
  if (terminalStatus) return evidenceStatusEx(evidenceStatus);
  return "链上复核完成";
};

const executionStageQuoteStatus = ({failed, pending}) => {
  if (failed) return "链上执行失败";
  if (pending) return "链上执行提交中";
  return "链上执行";
};

const estimateStageResultStatus = ({pending, terminalStatus, evidenceStatus}) => {
  if (pending) return "链下估算完成";
  if (terminalStatus) return evidenceStatusEx(evidenceStatus);
  return "链下估算完成";
};

const preflightStageResultStatus = ({pending, terminalStatus, evidenceStatus}) => {
  if (terminalStatus === "preflight_failed") return "链上复核失败";
  if (pending) return "待链上复核";
  if (terminalStatus) return evidenceStatusEx(evidenceStatus);
  return "链上复核完成";
};

const executionStageResultStatus = ({failed, pending}) => {
  if (failed) return "链上执行失败/未提交";
  if (pending) return "链上执行提交中";
  return "链上执行已提交，等待确认";
};
