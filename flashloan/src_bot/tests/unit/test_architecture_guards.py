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
        "account_pool/state.py": 300,
        "debt_pool/workflow.py": 300,
        "liquidation/account_backfill.py": 300,
        "liquidation/scan_presenter.py": 300,
        "liquidation/execution_service.py": 100,
        "liquidation/submission_service.py": 150,
        "liquidation/scan_summary_service.py": 100,
        "execution/static_call.py": 300,
        "execution/receipt_formatter.py": 100,
        "intent_trade/builder.py": 300,
        "intent_trade/direct.py": 480,
        "config/intent_trade.py": 300,
        "page_state/models.py": 300,
        "page_state/store.py": 300,
        "observer_runtime/service.py": 250,
        "cow_flashloan/order_submission.py": 300,
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


def test_cow_intent_construction_stays_out_of_runtime_and_web_layers():
    runtime_source = (SRC_ROOT / "runtime/cow_arbitrage_daemon.py").read_text(encoding="utf-8")
    web_source = (SRC_ROOT / "web/control_panel_data_routes.py").read_text(encoding="utf-8")
    intent_source = (SRC_ROOT / "intent_trade/builder.py").read_text(encoding="utf-8")
    config_source = (SRC_ROOT / "config/intent_trade.py").read_text(encoding="utf-8")

    assert "build_triangular_onchain_intent_trade(" in runtime_source
    assert "_cow_pure_profit_intent(" not in runtime_source
    assert "route_trade_fee_amount" not in web_source
    assert "flashloan_fee_amount" not in web_source
    assert "def build_cow_intent_trade(" in intent_source
    assert "def intent_costs(" in config_source


def test_cross_page_functionality_uses_dedicated_packages():
    package_paths = {
        "page_state": ["models.py", "store.py", "service.py"],
        "market_events": ["volatility.py", "store.py"],
        "observer_runtime": ["service.py"],
        "market/binance_market": ["__init__.py", "service.py"],
    }
    retired_web_modules = (
        "page_state.py",
        "page_state_store.py",
        "page_state_service.py",
        "market_volatility_event_service.py",
        "market_volatility_event_store.py",
        "observer_runtime_service.py",
        "binance_market_service.py",
    )
    web_sources = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (SRC_ROOT / "web").glob("*.py")
    )

    for package, files in package_paths.items():
        for filename in files:
            assert (SRC_ROOT / package / filename).exists(), f"{package}/{filename} is missing"
    for filename in retired_web_modules:
        assert not (SRC_ROOT / "web" / filename).exists(), f"web/{filename} should be a dedicated package"
    assert "from web.page_state" not in web_sources
    assert "from web.market_volatility_event" not in web_sources
    assert "from web.observer_runtime_service" not in web_sources


def test_feature_packages_have_matching_test_directories():
    expected_test_directories = (
        "tests/unit/account_pool",
        "tests/unit/cow_flashloan",
        "tests/unit/debt_pool",
        "tests/unit/intent_trade",
        "tests/unit/config",
        "tests/unit/market_events",
        "tests/integration/cow_flashloan",
        "tests/integration/liquidation",
        "tests/functional/liquidation",
        "tests/functional/page_state",
        "tests/functional/observer_runtime",
        "tests/functional/binance_market",
    )

    for relative_path in expected_test_directories:
        assert (SRC_ROOT / relative_path).is_dir(), f"{relative_path} is missing"


def test_cow_flashloan_helpers_live_in_dedicated_package():
    expected_modules = (
        "cow_flashloan/routes.py",
        "cow_flashloan/order_submission.py",
        "cow_flashloan/capabilities.py",
    )
    retired_execution_modules = (
        "cow_routes.py",
        "cow_order_submission.py",
        "cow_flashloan_capabilities.py",
    )
    retired_imports = (
        "execution" + ".cow_routes",
        "execution" + ".cow_order_submission",
        "execution" + ".cow_flashloan_capabilities",
    )
    python_sources = "\n".join(path.read_text(encoding="utf-8") for path in _python_files())

    for relative_path in expected_modules:
        assert (SRC_ROOT / relative_path).exists(), f"{relative_path} is missing"
    for filename in retired_execution_modules:
        assert not (SRC_ROOT / "execution" / filename).exists(), f"execution/{filename} should be in cow_flashloan"
    for retired_import in retired_imports:
        assert retired_import not in python_sources


def test_service_functionality_stays_out_of_web_routes():
    retired_web_modules = (
        "account_pool_state_service.py",
        "debt_pool_workflow.py",
        "liquidation_account_backfill.py",
        "liquidation_discovery_service.py",
        "liquidation_discovery_workflow.py",
        "liquidation_execution_service.py",
        "liquidation_scan_presenter.py",
        "liquidation_scan_summary_service.py",
        "liquidation_submission_service.py",
        "binance_market_service.py",
    )
    web_sources = "\n".join(path.read_text(encoding="utf-8") for path in (SRC_ROOT / "web").glob("*.py"))

    assert (SRC_ROOT / "account_pool" / "state.py").exists()
    assert (SRC_ROOT / "debt_pool" / "workflow.py").exists()
    assert (SRC_ROOT / "liquidation" / "execution_service.py").exists()
    for filename in retired_web_modules:
        assert not (SRC_ROOT / "web" / filename).exists(), f"web/{filename} should be a dedicated package"
    assert "from web.account_pool_state_service" not in web_sources
    assert "from web.debt_pool_workflow" not in web_sources
    assert "from web.liquidation_" not in web_sources


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
    from page_state import AccountScanStatus, DebtPoolStatus, ExecutionStatus, MarketObservationStatus

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
    source = (
        SRC_ROOT / "cow_flashloan" / "node_adapter" / "scripts" / "submit-cow-flashloan-order.js"
    ).read_text(encoding="utf-8")

    assert "final_amount_below_minimum_bound" not in source
    assert "sell_budget_exceeds_principal" not in source
    assert "if (!profitBudgetMet || !sellBudgetPassed)" not in source
    assert "flashloanTokenScope" not in source
    assert "appDataIndicator" not in source
    assert "flashloanIntent" not in source
    assert "token_scope_only" not in source


def test_cow_intent_node_adapter_is_independent_from_contract_workspace():
    source = (
        SRC_ROOT / "cow_flashloan" / "node_adapter" / "scripts" / "submit-cow-flashloan-order.js"
    ).read_text(encoding="utf-8")
    package_json = (SRC_ROOT / "cow_flashloan" / "node_adapter" / "package.json").read_text(encoding="utf-8")

    assert "contracts-dex" not in source
    assert "hardhat" not in package_json.lower()
    assert "deploy" not in package_json.lower()


def test_unified_direct_submission_has_no_reachable_legacy_controller_path():
    source = (SRC_ROOT / "intent_trade" / "direct_impl.py").read_text(encoding="utf-8")
    submit_start = source.index("def submit_direct_onchain_trade(")
    submit_body = source[submit_start:]
    legacy_start = source.index("def _submit_legacy_direct_onchain_trade(")
    legacy_body = source[legacy_start:submit_start]

    assert "_submit_legacy_direct_onchain_trade(" not in submit_body
    assert "UnifiedFlashLoanMevExecutor" in legacy_body
    assert 'status": "legacy_direct_path_disabled"' in legacy_body
