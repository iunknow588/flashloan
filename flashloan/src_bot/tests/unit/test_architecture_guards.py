import re
from pathlib import Path

from web.control_panel import app as control_panel_app


SRC_ROOT = Path(__file__).resolve().parents[2]


def _python_files() -> list[Path]:
    return [
        path
        for path in SRC_ROOT.rglob("*.py")
        if "__pycache__" not in path.parts and ".pytest_cache" not in path.parts
    ]


def test_no_wildcard_imports_are_added():
    offenders = []
    for path in _python_files():
        for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if re.match(r"^\s*(from\s+\S+\s+import\s+\*|import\s+\*)", line):
                offenders.append(f"{path.relative_to(SRC_ROOT)}:{line_no}")

    assert offenders == []


def test_refactored_helper_modules_stay_under_line_limits():
    limits = {
        "web/liquidation_account_backfill.py": 300,
        "web/liquidation_scan_presenter.py": 300,
        "execution/static_call.py": 300,
        "execution/receipt_formatter.py": 100,
        "web/observer_runtime_service.py": 250,
        "db/storage_liquidation.py": 250,
        "db/storage_liquidation_accounts.py": 300,
        "db/storage_liquidation_attempts.py": 300,
    }

    for relative_path, limit in limits.items():
        line_count = len((SRC_ROOT / relative_path).read_text(encoding="utf-8").splitlines())
        assert line_count < limit, f"{relative_path} has {line_count} lines"


def test_key_modules_stop_using_the_storage_aggregator():
    forbidden_modules = {
        "web/control_panel.py": ["from db.storage import"],
        "web/control_panel_liquidation_scan.py": ["from db.storage import"],
        "market/observer_common.py": ["from db.storage import"],
        "market/observer_runtime.py": ["from db.storage import"],
    }

    for relative_path, forbidden_imports in forbidden_modules.items():
        source = (SRC_ROOT / relative_path).read_text(encoding="utf-8")
        for forbidden_import in forbidden_imports:
            assert forbidden_import not in source, f"{relative_path} still imports the storage aggregator"


def test_web_route_registration_smoke():
    paths = {rule.rule for rule in control_panel_app.url_map.iter_rules()}

    assert "/api/status" in paths
    assert "/api/debt-pool/state" in paths
    assert "/api/debt-pool/decision" in paths
    assert "/api/account-pool/state" in paths
    assert "/api/account-scan/state" in paths
    assert "/api/market-observation/state" in paths
    assert "/api/execution/state" in paths
    assert "/binance-market" in paths
    assert "/dex-arbitrage" in paths
    assert "/api/binance-market/state" in paths
    assert "/api/binance-market/states" in paths
    assert "/api/binance-market/cow-config" in paths
    assert "/api/binance-market/cow-tokens" in paths
    assert "/api/binance-market/cow-tokens/refresh" in paths
    assert "/api/binance-market/cow-support" in paths
    assert "/api/binance-market/cow-quotes" in paths
    assert "/api/binance-market/cow-execution-attempts" in paths
    assert "/api/binance-market/cow-candidate-queue" in paths
    assert "/api/binance-velocity/candidates" in paths
    assert "/api/liquidation-health" in paths
    assert "/api/liquidation/borrow-pool/scan" in paths
    assert "/api/liquidation/account/<account>/static-call-and-save" in paths


def test_page_status_enums_do_not_include_cross_page_route_nodes():
    from web.page_state import AccountScanStatus, DebtPoolStatus, ExecutionStatus, MarketObservationStatus

    route_nodes = {"ACCOUNT_SCAN_PAGE", "DEBT_POOL_PAGE", "EXECUTION_PAGE"}
    status_values = {
        *(status.value for status in DebtPoolStatus),
        *(status.value for status in AccountScanStatus),
        *(status.value for status in MarketObservationStatus),
        *(status.value for status in ExecutionStatus),
    }

    assert status_values.isdisjoint(route_nodes)


def test_cow_quotes_route_defines_quote_timeout_before_use():
    source = (SRC_ROOT / "web/control_panel_data_routes.py").read_text(encoding="utf-8")
    function_start = source.index("def binance_market_cow_quotes():")
    function_end = source.index("@app.get(\"/api/binance-market/cow-execution-attempts\")", function_start)
    body = source[function_start:function_end]

    assignment_at = body.index("quote_timeout_seconds = request_float_arg")
    use_at = body.index("quote_timeout_seconds=quote_timeout_seconds")

    assert assignment_at < use_at


def test_cow_quotes_route_applies_pause_guard_after_sdk_quote_verification():
    source = (SRC_ROOT / "web/control_panel_data_routes.py").read_text(encoding="utf-8")
    function_start = source.index("def binance_market_cow_quotes():")
    function_end = source.index("@app.get(\"/api/binance-market/cow-execution-attempts\")", function_start)
    body = source[function_start:function_end]

    verification_at = body.index("payload = build_cow_quote_verification(")
    pause_at = body.index("pause_guard = cow_submission_pause_guard_status()")
    recording_at = body.index("payload[\"history_recording\"] = record_cow_execution_attempts_safely(")

    assert verification_at < pause_at < recording_at


def test_intent_mode_submission_script_does_not_use_profit_gates():
    source = (SRC_ROOT.parent.parent / "contract" / "contracts-dex" / "scripts" / "submit-cow-flashloan-order.js").read_text(encoding="utf-8")

    assert "final_amount_below_minimum_bound" not in source
    assert "sell_budget_exceeds_principal" not in source
    assert "if (!profitBudgetMet || !sellBudgetPassed)" not in source


def test_probe_script_keeps_intent_submission_gate_free_of_profit_blocks():
    source = (SRC_ROOT.parent.parent / "contract" / "contracts-dex" / "scripts" / "probe-cow-flashloans.js").read_text(encoding="utf-8")

    assert "if (!profitBudgetMet || !sellBudgetPassed)" not in source
    assert "selected_profit_budget_passed" not in source
    assert "no_route_above_profit_budget" not in source
