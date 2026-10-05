// 报价与路由页面：路线级状态机实现。
// 主页面只负责调用转换函数，不再散落维护 __reviewStage / reviewMode 的分支。

const routeEvidence = route => route?.live_onchain_evidence || route?.quoteAnalysis?.live_onchain_evidence || route?.quote?.live_onchain_evidence || {};

const unconfirmedFixedAmount = costs => {
  if (!costs || typeof costs !== "object") return false;
  if (costs.bestAmountMissing === true) return true;
  const reason = String(costs.selectedBorrowInputAmountReason || "").toLowerCase();
  return costs.selectedBorrowInputAmountConfirmedMax !== true
    && ["requested_amount_fallback", "onchain_execution_size_probe_confirmed_profitable_lower_bound"].includes(reason);
};

var activeExecution = { routeKey: "", state: "", txHash: "", startedAt: 0 };

const evidenceNeedsLiveReview = evidence => {
  if (!evidence || typeof evidence !== "object") return false;
  const lower = String(evidence?.status || evidence?.executionTrust || "").toLowerCase();
  return lower === "static_reference_pending_live_review" || lower === "static_reference_only" || lower === "static_live_quoter_estimate" || lower === "live_quote_reference_only";
};

const terminalRouteStatus = status => [
  "route_unprofitable",
  "slippage_limit_exceeded",
  "live_quote_failed",
  "quote_failed",
  "not_enough_liquidity",
  "pool_not_initialized",
  "pool_insufficient_for_profitable_execution",
  "missing_effective_pool",
  "missing_route_identity",
  "route_lacks_complete_token_address_identity",
  "rpc_verification_failed",
  "rpc_verification_timeout",
  "adapter_required",
  "missing_trigger_block",
].includes(String(status || "").toLowerCase());

const routeNeedsLiveReview = route => {
  if (!route || typeof route !== "object") return false;
  const evidence = routeEvidence(route);
  if (evidenceNeedsLiveReview(evidence)) return true;
  const lower = String(route?.quoteStatus || route?.onchainEvidenceStatus || route?.reviewMode || "").toLowerCase();
  return lower === "static_reference_pending_live_review" || lower === "static_reference_only" || lower === "static_live_quoter_estimate" || lower === "live_quote_reference_only";
};

const routeExecutionKey = route => {
  if (!route || typeof route !== "object") return "";
  const symbols = Array.isArray(route.routeSymbols)
    ? route.routeSymbols.map(item => String(item || "").trim().toUpperCase()).filter(Boolean)
    : [];
  const addresses = Array.isArray(route.routeAddresses)
    ? route.routeAddresses.map(item => String(item || "").trim().toLowerCase()).filter(Boolean)
    : Array.isArray(route?.quoteAnalysis?.routeAddresses)
      ? route.quoteAnalysis.routeAddresses.map(item => String(item || "").trim().toLowerCase()).filter(Boolean)
      : [];
  const poolIds = Array.isArray(route.poolIds)
    ? route.poolIds.map(item => String(item || "").trim().toLowerCase()).filter(Boolean)
    : [];
  const pools = Array.isArray(route.routePools)
    ? route.routePools.map(pool => String(pool?.pool || pool?.poolId || pool?.pairAddress || pool?.pairSymbol || "").trim().toLowerCase()).filter(Boolean)
    : [];
  const chain = String(route.chain_id ?? route.chainId ?? "");
  return ["exec", chain || "-", addresses.join("|"), poolIds.join("|"), pools.join("|"), symbols.join(" -> ")].join("|");
};

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
    ROUTE_UI_STATE.PREFLIGHT_DONE,
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
  if (stage === "preflight") return hasConfirmedBestAmount(route) ? ROUTE_UI_STATE.PREFLIGHT_DONE : ROUTE_UI_STATE.OFFCHAIN_ESTIMATE;
  if (stage === "execution_pending") return ROUTE_UI_STATE.EXECUTION_PENDING;
  if (stage === "execution_failed") return ROUTE_UI_STATE.EXECUTION_FAILED;
  if (stage === "execution") return ROUTE_UI_STATE.EXECUTION_SUBMITTED;
  if (route?.reviewMode === "pending_offchain_review") return ROUTE_UI_STATE.OFFCHAIN_PENDING;
  if (routeNeedsLiveReview(route)) return ROUTE_UI_STATE.OFFCHAIN_ESTIMATE;
  const evidence = routeEvidence(route);
  if (
    String(evidence?.status || route?.quoteStatus || "").toLowerCase() === "static_reference_pending_live_review"
  ) {
    return ROUTE_UI_STATE.OFFCHAIN_ESTIMATE;
  }
  if (terminalRouteStatus(evidence?.status || route?.quoteStatus)) {
    return ROUTE_UI_STATE.PREFLIGHT_FAILED;
  }
  if (
    evidence &&
    typeof evidence === "object" &&
    (
      String(evidence.status || "").trim() !== "" ||
      (evidence.costModel && typeof evidence.costModel === "object" && evidence.costModel.selectedBorrowInputAmountRaw != null)
    ) &&
    hasConfirmedBestAmount(route)
  ) {
    return ROUTE_UI_STATE.PREFLIGHT_DONE;
  }
  return ROUTE_UI_STATE.OFFCHAIN_ESTIMATE;
};

const uiStateOf = route => String(route?.__uiState || inferUiState(route) || ROUTE_UI_STATE.OFFCHAIN_ESTIMATE);

const hasConfirmedBestAmount = route => {
  if (routeNeedsLiveReview(route)) return false;
  const evidence = routeEvidence(route);
  const analysis = route?.quoteAnalysis && typeof route.quoteAnalysis === "object" ? route.quoteAnalysis : {};
  const costs = evidence && typeof evidence === "object" && evidence.costModel && typeof evidence.costModel === "object" ? evidence.costModel : {};
  const liveVerifiedStatus = String(evidence?.status || "").toLowerCase() === "live_verified";
  const bestAmountConfirmed = costs.selectedBorrowInputAmountConfirmedMax === true
    || costs.bestAmountConfirmed === true
    || evidence?.bestAmountConfirmed === true
    || evidence?.readyForTransactionPreflight === true
    || liveVerifiedStatus
    || analysis.bestAmountConfirmed === true
    || route?.bestAmountConfirmed === true;
  if (
    evidence &&
    typeof evidence === "object" &&
    evidence.costModel &&
    typeof evidence.costModel === "object" &&
    evidence.costModel.bestAmountMissing === true &&
    !bestAmountConfirmed
  ) {
    return false;
  }
  if ((analysis.bestAmountMissing === true || route?.bestAmountMissing === true) && !bestAmountConfirmed) return false;
  if (unconfirmedFixedAmount(evidence?.costModel) && !bestAmountConfirmed) return false;
  if (!bestAmountConfirmed) return false;
  if (
    evidence &&
    typeof evidence === "object" &&
    (costs.selectedBorrowInputAmount != null || String(evidence.status || "").toLowerCase() === "live_verified")
  ) {
    return true;
  }
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
  const liveVerifiedStatus = String(safeEvidence.status || "").toLowerCase() === "live_verified";
  const evidenceConfirmed = costs.selectedBorrowInputAmountConfirmedMax === true
    || costs.bestAmountConfirmed === true
    || safeEvidence.bestAmountConfirmed === true
    || safeEvidence.readyForTransactionPreflight === true
    || liveVerifiedStatus
    || safeAnalysis.bestAmountConfirmed === true;
  if (
    (costs.bestAmountMissing === true
      || safeAnalysis.bestAmountMissing === true
      || route?.bestAmountMissing === true
      || unconfirmedFixedAmount(costs)) && !evidenceConfirmed
  ) {
    return null;
  }
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
  const confirmedBest = costs.selectedBorrowInputAmount ?? costs.borrowInputAmount ?? analysisBest ?? calcBest;
  return confirmedBest ?? sizeBest ?? null;
};

const bestAmountMissingOf = (route, evidence, analysis) => {
  const safeEvidence = evidence && typeof evidence === "object" ? evidence : {};
  const safeAnalysis = analysis && typeof analysis === "object" ? analysis : {};
  const costs = safeEvidence.costModel && typeof safeEvidence.costModel === "object" ? safeEvidence.costModel : {};
  const liveVerifiedStatus = String(safeEvidence.status || "").toLowerCase() === "live_verified";
  const bestAmountConfirmed = costs.selectedBorrowInputAmountConfirmedMax === true
    || costs.bestAmountConfirmed === true
    || safeEvidence.bestAmountConfirmed === true
    || safeEvidence.readyForTransactionPreflight === true
    || liveVerifiedStatus
    || safeAnalysis.bestAmountConfirmed === true;
  if (
    (route?.bestAmountMissing === true
      || safeAnalysis.bestAmountMissing === true
      || costs.bestAmountMissing === true) && !bestAmountConfirmed
  ) return true;
  const picked = routeBestAmountPick(route, safeEvidence, safeAnalysis);
  return picked === null || picked === undefined || picked === "" || !bestAmountConfirmed;
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
  if ([
    ROUTE_UI_STATE.EXECUTION_PENDING,
    ROUTE_UI_STATE.EXECUTION_SUBMITTED,
    ROUTE_UI_STATE.EXECUTION_FAILED,
  ].includes(nextState)) {
    route.__executionRouteKey = routeExecutionKey(route);
  }
  const stage = uiStageFromUiState(nextState);
  route.__reviewStage = stage;
  route.reviewMode = nextState;
  route.onchainEvidenceStatus = meta.quoteStatus || route.onchainEvidenceStatus || route.quoteStatus || "";
  route.quoteStatus = meta.quoteStatus || route.quoteStatus || route.onchainEvidenceStatus || "";
  return route;
};

const hintAmountText = value => {
  const parsed = Number(value);
  if (value === null || value === undefined || value === "" || String(value).trim() === "-" || !Number.isFinite(parsed)) {
    return "未计算";
  }
  return parsed.toLocaleString("en-US", { maximumFractionDigits: 8 });
};

const hintAmountPart = (value, symbol) => {
  const text = hintAmountText(value);
  return text === "未计算" ? text : `${text} ${symbol || ""}`.trim();
};

const estimateBestAmountHint = ({amount, symbol, initialAmount, initialSymbol}) => {
  if (Number.isFinite(Number(initialAmount)) && Number(initialAmount) > 0
      && Math.abs(Number(amount) - Number(initialAmount)) / Number(initialAmount) > 0.01) {
    return `量级提示=链下估算初始量 ${hintAmountPart(amount, symbol)}；初始探测 ${hintAmountPart(initialAmount, initialSymbol)} 只是起点，最终最佳量由链上复核确认`;
  }
  return `量级提示=链下估算初始量 ${hintAmountPart(amount, symbol)}；仅作为链上合约初始值，最佳量由链上复核确认`;
};

const preflightBestAmountHint = ({amount, symbol}) => {
  return `量级提示=本次链上复核确认最佳交易量 ${hintAmountPart(amount, symbol)}；当前未执行链上交易。`;
};

const executionBestAmountHint = ({amount, symbol}) => {
  return `量级提示=本次链上执行选用金额为 ${hintAmountPart(amount, symbol)}`;
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

function normalizedReviewStage(route, latestBroadcast, currentExecution) {
  const active = currentExecution && typeof currentExecution === "object" ? currentExecution : activeExecution;
  const routeKey = routeExecutionKey(route);
  const selfExecutionKey = route?.__executionRouteKey || "";
  const selfState = String(route?.__uiState || "");
  const activeMatches = Boolean(active?.routeKey && active.routeKey === routeKey);
  const selfMatches = Boolean(selfExecutionKey && selfExecutionKey === routeKey);
  if (activeMatches && active.state) return uiStageFromUiState(active.state);
  if (activeMatches && active.txHash) return "execution";
  if (selfMatches && [ROUTE_UI_STATE.EXECUTION_PENDING, ROUTE_UI_STATE.EXECUTION_SUBMITTED, ROUTE_UI_STATE.EXECUTION_FAILED].includes(selfState)) {
    return uiStageFromUiState(selfState);
  }
  return uiStageFromUiState(effectiveUiState(route));
}

const estimateStageQuoteStatus = ({pending, terminalStatus, evidenceStatus}) => {
  if (String(evidenceStatus || "").toLowerCase() === "static_reference_pending_live_review") return evidenceStatusEx(evidenceStatus);
  if (pending) return "链下估算完成";
  if (terminalStatus) return evidenceStatusEx(evidenceStatus);
  return "链下估算完成";
};

const preflightStageQuoteStatus = ({pending, terminalStatus, evidenceStatus}) => {
  if (String(evidenceStatus || "").toLowerCase() === "static_reference_pending_live_review") return evidenceStatusEx(evidenceStatus);
  if (terminalStatus === "preflight_failed") {
    const evidenceValue = String(evidenceStatus || "").trim();
    if (evidenceValue && String(evidenceValue).toLowerCase() !== "preflight_failed") {
      const statusText = evidenceStatusEx(evidenceValue);
      if (statusText !== "链上复核状态未确认") return statusText;
    }
    return "链上复核失败";
  }
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
  if (String(evidenceStatus || "").toLowerCase() === "static_reference_pending_live_review") return evidenceStatusEx(evidenceStatus);
  if (pending) return "链下估算完成";
  if (terminalStatus) return evidenceStatusEx(evidenceStatus);
  return "链下估算完成";
};

const preflightStageResultStatus = ({pending, terminalStatus, evidenceStatus}) => {
  if (String(evidenceStatus || "").toLowerCase() === "static_reference_pending_live_review") return evidenceStatusEx(evidenceStatus);
  if (terminalStatus === "preflight_failed") {
    const evidenceValue = String(evidenceStatus || "").trim();
    if (evidenceValue && String(evidenceValue).toLowerCase() !== "preflight_failed") {
      const statusText = evidenceStatusEx(evidenceValue);
      if (statusText !== "链上复核状态未确认") return statusText;
    }
    return "链上复核失败";
  }
  if (pending) return "待链上复核";
  if (terminalStatus) return evidenceStatusEx(evidenceStatus);
  return "链上复核完成";
};

const executionStageResultStatus = ({failed, pending}) => {
  if (failed) return "链上执行失败/未提交";
  if (pending) return "链上执行提交中";
  return "链上执行已提交，等待确认";
};
