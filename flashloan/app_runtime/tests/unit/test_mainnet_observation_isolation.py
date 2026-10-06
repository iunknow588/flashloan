from __future__ import annotations

import json
from pathlib import Path

from dex.intent_trade.chains.robinhood.market_observation import fund_anchor_pair_observation


APP_RUNTIME_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = APP_RUNTIME_ROOT.parent / "src"


def dex_quotes_ui_text():
    template = APP_RUNTIME_ROOT / "web" / "templates" / "dex_quotes.html"
    static_root = APP_RUNTIME_ROOT / "web" / "static" / "dex_quotes"
    parts = [template.read_text(encoding="utf-8")]
    for name in (
        "route_state_machine.js",
        "route_offchain.js",
        "route_preflight.js",
        "route_execution.js",
        "quotes_render.js",
        "quotes_app.js",
    ):
        parts.append((static_root / name).read_text(encoding="utf-8"))
    return "\n".join(parts)


def test_mainnet_market_observation_does_not_import_or_reference_testnet():
    source = (SRC_ROOT / "dex" / "intent_trade" / "chains" / "robinhood" / "market_observation.py").read_text(encoding="utf-8")
    assert "robinhood_testnet" not in source
    assert "46630" not in source


def test_mainnet_v4_pool_liquidity_does_not_import_or_reference_testnet():
    source = (SRC_ROOT / "dex" / "intent_trade" / "chains" / "robinhood" / "v4_pool_liquidity.py").read_text(encoding="utf-8")
    assert "robinhood_testnet" not in source
    assert "46630" not in source


def test_mainnet_observation_hooks_refuse_other_chain_scopes():
    mainnet = fund_anchor_pair_observation(chain_id=4663, protocols=["uniswap_v4"])
    assert mainnet["enabled"] is True
    assert mainnet["status"] == "robinhood_mainnet_v4_eth_anchor_observation"
    assert "candidateEthPairTokenCount" in mainnet
    assert "candidateYzPairCount" in mainnet
    assert "verifiedYzPairCount" in mainnet
    assert "missingYzPairCount" in mainnet
    assert mainnet["candidateYzPairCount"] >= mainnet["verifiedYzPairCount"]
    assert mainnet["sourceTokenEvidence"] == "robinhood_eth_pair_tokens"

    invalid = fund_anchor_pair_observation(chain_id=46630, protocols=["uniswap_v4"])
    assert invalid["enabled"] is False
    assert invalid["status"] == "chain_scope_invalid"


def test_mainnet_observation_does_not_prefilter_yc_zc_by_symbol_or_stablecoin():
    source = (SRC_ROOT / "dex" / "intent_trade" / "chains" / "robinhood" / "market_observation.py").read_text(encoding="utf-8")
    assert "STABLE_SYMBOLS" not in source
    assert "_is_stable_symbol" not in source
    assert '"TA", "TB", "TZ"' not in source
    assert 'text not in {"TA", "TB", "TZ"}' not in source


def test_mainnet_chain_json_declares_shadow_and_pool_liquidity_evidence():
    path = SRC_ROOT / "chains" / "robinhood" / "chain.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    dex_arb = payload["features"]["dex_arbitrage"]
    assert dex_arb["protocol_supported"] is True
    assert dex_arb["observation_only"] is True
    assert "v4_pool_liquidity" in payload["data_files"]


def test_mainnet_flows_declare_v4_pool_liquidity_data_key():
    path = SRC_ROOT / "chains" / "robinhood" / "flows.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    for flow in payload["flows"].values():
        if flow["handler"].endswith("dex_surfaces:dex_quote_request_flow"):
            assert "v4_pool_liquidity" in flow["data_file_keys"]


def test_dex_quotes_template_no_longer_uses_repeat_small_arbitrage_warning():
    text = dex_quotes_ui_text()
    assert "不要重复执行相同最佳量" not in text
    assert "重复执行小量套利易误判" not in text
    assert "不要重复执行" not in text
    assert "广播测试" not in text
    assert "预检" not in text
    assert "链上复核/执行" not in text
    assert "禁止链上复核和执行" not in text
    assert "链下估算待开始" not in text
    assert "待链下估算" not in text
    assert "最佳交易量" in text
    assert "初始测试量" in text
    assert "链上复核金额" not in text
    assert "preflight_failed" in text
    assert "链上复核失败" in text
    assert "已执行链上交易" in text
    assert "已执行 2 笔链上交易" not in text
    assert "等 2 笔" not in text
    assert "先完成更大金额链上复核" not in text
    assert "扩大金额" not in text
    assert "其他金额" not in text
    assert "尝试其他金额" not in text
    assert "如需其他金额" not in text
    assert "如机会仍有效，可再次执行相同执行金额" not in text
    assert "不需要手动判断" in text
    assert "请先重新链上复核" not in text
    assert "链上执行" in text


def test_dex_quotes_copy_prefers_latest_execution_over_stale_dom_text():
    text = dex_quotes_ui_text()
    assert "const activeExecutionRoute = Boolean(activeExecution?.routeKey && routeExecutionKey(route) === activeExecution.routeKey);" in text
    assert "latestBroadcastAdvice(route)" in text
    assert "latestExecutionResultLine" in text
    assert "routeRecommendationText = latestExecution?.text" in text
    assert "if (broadcast && payload?.txHash)" in text
    assert "executionAnalysis.amountOptimizedFrom,\n            costs.requestedBorrowInputAmount,\n            costs.selectedBorrowInputAmount,\n            sourceAnalysis.requestedAmount,\n            sourceAnalysis.amountOptimizedFrom,\n            offchainProbe," in text
    assert "costs.requestedBorrowInputAmount ?? offchainProbe ?? costs.selectedBorrowInputAmount" in text


def test_dex_quotes_broadcast_always_reviews_before_execution():
    text = dex_quotes_ui_text()
    assert "const hasHistoricalReview" in text
    assert "const initialAmountLabel" in text
    assert "历史复核金额" in text
    assert "初始测试金额" in text
    assert "作为初始量，重新链上复核当前最佳量后再提交链上交易" in text
    assert "amountIsBest: false," in text
    assert "executionReuseAmount" not in text


def test_dex_quotes_contains_route_ui_state_machine_helpers():
    text = dex_quotes_ui_text()
    assert "const ROUTE_UI_STATE" in text
    assert "const ROUTE_UI_TRANSITIONS" in text
    assert "const setRouteUiState" in text
    assert "const canAutoEstimate" in text
    assert "const canAutoRefresh" in text
    assert "const shouldPreserveRouteState" in text
    assert "const uiStageFromUiState" in text
    assert "const uiStateFromStage" in text
    assert "__uiState" in text


def test_dex_quotes_auto_review_uses_can_auto_estimate_instead_of_stage_array():
    text = dex_quotes_ui_text()
    assert "if (!canAutoEstimate(initialRoute) || shouldPreserveRouteState(initialRoute)) continue;" in text
    assert "includes(String(initialRoute.__reviewStage || \"\"))" not in text
    assert "route.__uiState" in text
    assert "setRouteUiState(route, ROUTE_UI_STATE.OFFCHAIN_PENDING" in text
    assert "setRouteUiState(route, ROUTE_UI_STATE.OFFCHAIN_ESTIMATE" in text


def test_dex_quotes_route_state_machine_controls_scanner_refresh():
    text = dex_quotes_ui_text()
    assert "if (shouldPreserveRouteState(route))" in text
    assert "routeHasCompletedOffchainReview" in text
    assert "scheduleReviewActiveRoute()" in text
    assert "route.reviewMode = nextState" in text


def test_dex_quotes_chain_review_keeps_best_amount_visible():
    text = dex_quotes_ui_text()
    assert "evidence.costModel.selectedBorrowInputAmountRaw" in text
    assert "return ROUTE_UI_STATE.PREFLIGHT_DONE;" in text
    assert "const routeBestAmountPick = (route, evidence, analysis) =>" in text
    assert "const confirmedBest = costs.selectedBorrowInputAmount ?? costs.borrowInputAmount ?? analysisBest ?? calcBest;" in text
    assert "costs.bestAmountMissing === true" in text
    assert "return confirmedBest ?? sizeBest ?? null;" in text
    assert "const rawBestAmountValue = routeBestAmountPick(route, evidence, analysis);" in text
    assert "setRouteUiState(route, inferUiState(route)" in text


def test_dex_quotes_initial_probe_prefers_requested_execution_amount():
    text = dex_quotes_ui_text()
    assert "costs.requestedBorrowInputAmount ?? offchainProbe ?? costs.selectedBorrowInputAmount" in text
    assert "evidence.costModel?.requestedBorrowInputAmount ?? analysis.requestedAmount ?? analysis.amountOptimizedFrom ?? offchainProbe" in text
    assert "?? evidenceCosts.requestedBorrowInputAmount" in text
    assert "?? analysis.initialProbeAmount" in text
    assert "if (!liveHops.length) return staticSteps;" in text
    assert "var activeExecution = { routeKey: \"\", state: \"\", txHash: \"\", startedAt: 0 };" in text
    assert "const routeExecutionKey = route => {" in text
    assert "normalizedReviewStage(route, latestBroadcastForStage, activeExecution)" in text
    assert "const reviewStage = normalizedReviewStage(route, latestBroadcastForStage, activeExecution);" in text
    assert 'const priceReviewBadge = reviewStage === "execution" || execPending || execFailed' in text
    assert "const bestAmount = useBestForAction ? routeBestAmountPick(route, evidence, analysis) : null;" in text
    assert "const bestAmountMissing = useBestForAction && (bestAmount === null || bestAmount === undefined || bestAmount === \"\");" in text
    assert "const testAmount = bestAmount ?? probeAmount ?? autoRouteAmount(route);" in text
    assert "最佳量未计算" in text
    assert "bestAmountValue === null || bestAmountValue === undefined || bestAmountValue === \"\" ? \"未计算\"" in text
    assert "? \"未计算\"" in text
    assert "bestAmountCopy === null || bestAmountCopy === undefined || bestAmountCopy === \"\"" in text
    assert "步骤金额提示=复制步骤首跳金额" in text


def test_dex_quotes_preserves_pending_live_review_and_unique_execution():
    text = dex_quotes_ui_text()
    assert 'const quoteStatus = String(payload.quoteStatus || (payload.liveVerified ? "live_verified" : "static_reference_pending_live_review")).toLowerCase();' in text
    assert 'const pendingLiveReview = !payload.liveVerified && quoteStatus === "static_reference_pending_live_review";' in text
    assert "const staticEvidenceModes = [" in text
    assert "staticEvidenceModes.includes(String(payload.priceEvidenceMode || \"\").toLowerCase())" in text
    assert "priceEvidenceSource: payload.priceEvidenceSource ||" in text
    assert "const bestAmountMissing = payload.bestAmountMissing === true" in text
    assert "const confirmedBestAmount = bestAmountMissing ? null : amount;" in text
    assert text.index("const amount = payload.bestAmount") < text.index("const confirmedBestAmount = bestAmountMissing ? null : amount;")
    assert text.count("normalizedReviewStage(route, latestBroadcastForStage, activeExecution)") >= 2
    assert 'const priceReviewBadge = reviewStage === "execution" || execPending || execFailed' in text


def test_dex_quotes_offchain_preserves_payload_static_reference_evidence():
    text = dex_quotes_ui_text()
    assert "const payloadStaticReference = !payload.liveVerified && (" in text
    assert "payload.priceEvidenceMode || (payload.liveVerified ? \"live_verified\" : (payloadStaticReference ? \"static_reference_pending_live_review\" : \"live_required\"))" in text
    assert "payload.priceEvidenceSource || (payload.liveVerified ? \"chain_live_quote\" : (payloadStaticReference ? \"static_reference_pending_live_review\" : \"chain_live_quote_required\"))" in text


def test_dex_quotes_merge_action_does_not_default_static_reference_to_live():
    text = dex_quotes_ui_text()
    assert "const mergedQuoteStatus = payload.quoteStatus" in text
    assert "|| (route.quoteAnalysis?.livePriceVerified ? \"live_verified\" : \"static_reference_pending_live_review\")" in text
    assert "const mergedExecutionTrust = payload.executionTrust" in text
    assert "|| (route.quoteAnalysis?.livePriceVerified ? \"live_quoter_verified_readonly\" : \"static_reference_pending_live_review\")" in text
    assert "status: mergedQuoteStatus" in text
    assert "executionTrust: mergedExecutionTrust" in text


def test_dex_quotes_successful_preflight_detail_is_not_rendered_as_blocker():
    text = dex_quotes_ui_text()
    assert "const routeEvidenceBlockers = Array.isArray(payload.evidence?.blockers)" in text
    assert "const preflightPassed = payload.ok !== false" in text
    assert "const blockers = preflightPassed" in text
    assert "!terminalEvidenceStatus(payload.quoteStatus || payload.evidenceStatus || \"\")" in text
    assert "const informativeBlocker = blocker => {" in text
    assert "text.includes(\"broadcast disabled\") || text.includes(\"static preflight passed\")" in text


def test_dex_quotes_broadcast_disabled_is_a_not_submitted_state_not_failure():
    text = dex_quotes_ui_text()
    assert "const broadcastDisabledPayload = mode === \"broadcast\" && Boolean(" in text
    assert "payload?.broadcastDisabled" in text
    assert "official_arb_broadcast_disabled" in text
    assert "route.__broadcastDisabled = true" in text
    assert "activeExecution?.broadcastDisabled" in text
    assert "链上执行未提交：自动扫描复核并链上广播未开启，本次未发起链上交易。" in text


def test_dex_quotes_summary_shows_uncomputed_best_amount_instead_of_looking_confirmed():
    text = dex_quotes_ui_text()
    assert "const rawBestAmountMissing = bestAmountMissingOf(rawRoute, evidence, analysis);" in text
    assert "const bestAmountMissingOf = (route, evidence, analysis) =>" in text
    assert "const bestAmountMetric = reviewStage === \"estimate\"" in text
    assert "rawBestAmountMissing" in text
    assert "? \"未计算\"" in text
    assert "const netProfitUsd = evidenceFirst(analysis.netProfitUsd, costsForMetric.netProfitUsd)" in text
    assert "const referenceNetProfitAmount = humanTokenValue(referenceNetProfitRaw)" in text
    assert "参考净利=${referenceNetProfitMetric}" in text
    assert "静态参考提示=" in text
    assert "const bestAmountMissingCopy = pendingCopy" in text
    assert "const bestAmountCopyText = bestAmountMissingCopy" in text


def test_dex_quotes_estimate_copy_does_not_show_initial_probe_as_uncomputed():
    text = dex_quotes_ui_text()
    assert "reviewStage !== \"estimate\" && (costs.bestAmountMissing === true || sourceAnalysis.bestAmountMissing === true || route.bestAmountMissing === true)" in text
    assert "const explicitBestAmountMissing = payload.bestAmountMissing === true" in text
    assert "const mergedBestAmountMissing = explicitBestAmountMissing || (!mergedLiveVerifiedStatus && !payloadBestAmountConfirmed && !diagnosticBestComputed);" in text
    assert "route.bestAmountConfirmed = bestAmountConfirmed;" in text
    assert "route.bestAmountMissing = mergedAnalysis.bestAmountMissing === true;" in text
    assert "route.bestAmountConfirmed = mergedAnalysis.bestAmountConfirmed === true;" in text
    assert "const useActionRequest = Number.isFinite(actionRequested) && actionRequested > 0 && reviewStage !== \"estimate\";" in text
    assert "const hasActionRequest = Number.isFinite(actionRequested) && actionRequested > 0 && reviewStage !== \"estimate\";" in text


def test_dex_quotes_static_reference_never_masquerades_as_preflight_complete():
    text = dex_quotes_ui_text()
    assert "const evidenceNeedsLiveReview = evidence => {" in text
    assert "const routeNeedsLiveReview = route => {" in text
    assert "if (routeNeedsLiveReview(route)) return false;" in text
    assert "if (staticReview && reviewStage === \"estimate\") return \"当前为静态参考，尚未通过链上逐 hop 复核；不能作为已确认最佳量，不建议直接链上执行。\";" in text
    assert "const staticReview = routeNeedsLiveReview(route) || statusLower === \"static_reference_pending_live_review\"" in text
    assert "const staticReview = routeNeedsLiveReview(route) || evidenceStatusLower === \"static_reference_pending_live_review\"" in text
    assert "待链上复核后执行" in text
    assert "静态参考，待链上逐 hop 复核" in text


def test_dex_quotes_terminal_status_never_masquerades_as_preflight_complete():
    text = dex_quotes_ui_text()
    assert "const terminalRouteStatus = status => [" in text
    assert '"rpc_verification_timeout"' in text
    assert "if (terminalRouteStatus(evidence?.status || route?.quoteStatus))" in text
    assert "if (estimateFailed) return terminalFailureAdvice(evidence.status);" in text
    assert "statusLower === \"route_unprofitable\"" in text


def test_dex_quotes_structured_route_test_400_preserves_terminal_status():
    text = dex_quotes_ui_text()
    assert "async function postJson(path, payload, allowStructuredFailedResponse = false)" in text
    assert "const structuredFailedResponse = Boolean(" in text
    assert "data?.quoteStatus || data?.evidenceStatus || data?.executionTrust" in text
    assert 'postJson("/api/dex/quote-route/test", {' in text
    assert "}, true);" in text


def test_dex_quotes_fetch_failure_is_not_rpc_failure():
    text = dex_quotes_ui_text()
    assert "networkError.__dexErrorCode = \"backend_unreachable\"" in text
    assert "fetchFailure ? \"backend_unreachable\" : \"rpc_verification_failed\"" in text
    assert "请求服务不可达" in text
    assert "backend_unreachable" in text
    assert "fetch_network_error" in text


def test_dex_quotes_unprofitable_diagnostic_amount_is_not_missing():
    text = dex_quotes_ui_text()
    assert "terminalDiagnosticStatus" in text
    assert "const rawBestAmountMissing = bestAmountMissingOf(rawRoute, evidence, analysis);" in text
    assert "bestAmountComputed" in text
    assert "bestAmountMissingCopy = pendingCopy" in text


def test_dex_quotes_translates_evidence_timeout_blocker_to_chinese():
    text = dex_quotes_ui_text()
    assert "on-chain evidence timed out after ([0-9.]+)s" in text
    assert "链上复核超时：RPC 未在期限内返回（${evidenceTimeout[1]} 秒）" in text


def test_dex_quotes_missing_best_amount_never_masquerades_as_preflight_passed():
    text = dex_quotes_ui_text()
    assert "preflightResultPassed" in text
    assert "链上复核未通过：最佳量未计算成功" in text
    assert "最佳量未计算成功，不能进入链上执行" in text
    assert "const resolvedStage = stage === \"preflight\" && mergedBestAmountUnconfirmed" in text
    assert "hasConfirmedBestAmount(route)" in text
    assert "const bestAmountConfirmed = costs.selectedBorrowInputAmountConfirmedMax === true" in text
    assert "if (!bestAmountConfirmed) return false;" in text
    assert "on-chain best amount unconfirmed; cannot enter transaction preflight" in text


def test_dex_submissions_confirmed_execution_ignores_static_reference_evidence():
    submissions = (APP_RUNTIME_ROOT / "web" / "templates" / "dex_submissions.html").read_text(encoding="utf-8")
    assert "function rowIsConfirmedExecutionSuccess(row) {" in submissions
    assert "if (rowIsConfirmedExecutionSuccess(row)) return false;" in submissions
    assert "if (rowIsConfirmedExecutionSuccess(row)) return '<span class=\"badge live\">执行成功/已确认</span>';" in submissions
    assert "以链上确认后的实际成交为准" in submissions


def test_dex_quotes_execution_route_key_distinguishes_same_symbols_by_addresses():
    text = dex_quotes_ui_text()
    assert "const addresses = Array.isArray(route.routeAddresses)" in text
    assert "const poolIds = Array.isArray(route.poolIds)" in text
    assert 'return ["exec", chain || "-", addresses.join("|"), poolIds.join("|"), pools.join("|"), symbols.join(" -> ")].join("|");' in text
    assert "routeExecutionKey(route) === activeExecution.routeKey" in text
    assert text.count("normalizedReviewStage(route, latestBroadcastForStage, activeExecution)") >= 2


def test_dex_quotes_template_loads_split_route_state_files():
    template = (APP_RUNTIME_ROOT / "web" / "templates" / "dex_quotes.html").read_text(encoding="utf-8")
    for name in (
        "route_state_machine.js",
        "route_offchain.js",
        "route_preflight.js",
        "route_execution.js",
        "quotes_render.js",
        "quotes_app.js",
    ):
        assert f'<script src="/static/dex_quotes/{name}"></script>' in template
