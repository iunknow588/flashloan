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
    assert mainnet["status"] == "robinhood_mainnet_v4_shadow_observation"

    invalid = fund_anchor_pair_observation(chain_id=46630, protocols=["uniswap_v4"])
    assert invalid["enabled"] is False
    assert invalid["status"] == "chain_scope_invalid"


def test_mainnet_chain_json_declares_shadow_and_pool_liquidity_evidence():
    path = SRC_ROOT / "chains" / "robinhood" / "chain.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    dex_arb = payload["features"]["dex_arbitrage"]
    assert dex_arb["protocol_supported"] is False
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
    assert "const latestExecution = latestBroadcastAdvice(route);" in text
    assert "latestExecutionResultLine" in text
    assert "routeRecommendationText = latestExecution?.text" in text
    assert "if (broadcast && payload?.txHash)" in text


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
    assert "scheduleReviewAllRoutes()" in text
    assert "route.reviewMode = nextState" in text


def test_dex_quotes_chain_review_keeps_best_amount_visible():
    text = dex_quotes_ui_text()
    assert "evidence.costModel.selectedBorrowInputAmountRaw" in text
    assert "return ROUTE_UI_STATE.PREFLIGHT_DONE;" in text
    assert "const routeBestAmountPick = (route, evidence, analysis) =>" in text
    assert "return costs.selectedBorrowInputAmount ?? costs.borrowInputAmount ?? analysisBest ?? calcBest ?? sizeBest ?? null;" in text
    assert "const rawBestAmountValue = routeBestAmountPick(route, evidence, analysis);" in text
    assert "setRouteUiState(route, inferUiState(route)" in text


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
