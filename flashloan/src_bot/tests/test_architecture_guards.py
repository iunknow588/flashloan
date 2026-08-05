import re
from pathlib import Path

from web.control_panel import app as control_panel_app


SRC_ROOT = Path(__file__).resolve().parents[1]


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
