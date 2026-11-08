from __future__ import annotations

from pathlib import Path


APP_RUNTIME_ROOT = Path(__file__).resolve().parents[2]
QUOTES_ROOT = APP_RUNTIME_ROOT / "web" / "static" / "dex_quotes"


def _quotes_app() -> str:
    return (QUOTES_ROOT / "quotes_app.js").read_text(encoding="utf-8")


def _quotes_render() -> str:
    return (QUOTES_ROOT / "quotes_render.js").read_text(encoding="utf-8")


def test_route_action_buttons_require_managed_waiting_state() -> None:
    render = _quotes_render()

    broadcast_button_line = next(
        line
        for line in render.splitlines()
        if 'data-route-broadcast="${index}"' in line
    )
    review_button_line = next(
        line
        for line in render.splitlines()
        if 'data-route-static-call="${index}"' in line
    )
    assert 'canManualBroadcast ? "" : "disabled"' in broadcast_button_line
    assert 'canManualReview ? "" : "disabled"' in review_button_line
    assert "closedLoopControl?.broadcast" in render
    assert "closedLoopControl?.review" in render
    assert "manualWakeEligible === true" in render
    assert "不重新报价、不重算 PnL" in broadcast_button_line
    assert "route_unprofitable" not in broadcast_button_line


def test_live_onchain_review_button_requires_idle_waiting_worker() -> None:
    render = _quotes_render()

    static_call_line = next(
        line
        for line in render.splitlines()
        if 'data-route-static-call="${index}"' in line
    )
    assert 'canManualReview ? "" : "disabled"' in static_call_line
    assert "canManualReview = reviewControl.manualWakeEligible === true;" in render
    assert "仅空闲等待中的复核线程可手动唤醒" in static_call_line


def test_manual_commands_use_async_state_transition_contract() -> None:
    app = _quotes_app()

    assert '"X-Request-Id": requestToken("dex-ui-request")' in app
    assert '"Idempotency-Key": requestToken("dex-ui-idempotency")' in app
    assert 'postJson("/api/dex/closed-loop-groups/manual-review", {' in app
    assert 'postJson("/api/dex/closed-loop-groups/manual-broadcast", {' in app
    assert app.count("dryRun: false") >= 2
    assert "状态迁移已入队" in app
    assert "后续刷新读取状态" in app
    assert 'const button = event.target.closest("[data-route-static-call], [data-route-broadcast]");' in app
    assert 'if (button.hasAttribute("data-route-static-call")) {' in app
    assert "return requestManualNativeReview(latestRoutes[index], button, resultElement);" in app
    assert "return requestManualNativeBroadcast(latestRoutes[index], button, resultElement);" in app
    assert 'const mode = button.hasAttribute("data-route-broadcast")' not in app


def test_route_test_endpoint_is_diagnostic_only() -> None:
    app = _quotes_app()

    assert "Diagnostic fixture retained for legacy regression comparisons only." in app
    assert app.count('postJson("/api/dex/quote-route/test", {') == 1
    assert "return runRouteTest(" not in app
    assert "runRouteTest(latestRoutes" not in app
    assert "仅四段闭环支持手动复核" in app
    assert "route.__broadcastError" in app
    assert "bestAmountMissing" in app
