from web.api_scope_policy import (
    api_requires_scope,
    api_scope_contract,
    validate_api_response_contract,
    validate_api_scope_policy,
    response_required_keys,
    validate_named_response_contract,
    validate_registered_response_contracts,
    validate_response_schema_registry,
    missing_response_schema_paths,
)
import pytest
from web.control_panel import app
from web.control_panel_app import install_chain_selection_context


def test_metadata_endpoints_are_scope_free():
    assert not api_requires_scope("/api/status")
    assert not api_requires_scope("/api/workflow-context")
    assert not api_requires_scope("/api/markets")


def test_business_api_endpoints_require_scope():
    assert api_requires_scope("/api/liquidation/accounts")
    assert api_requires_scope("/api/dex/overview")
    assert api_requires_scope("/api/binance-market/state")
    assert api_requires_scope("/api/flash-loan/pools")
    assert api_requires_scope("/api/opportunity-health")
    assert api_requires_scope("/api/db-summary")
    assert api_requires_scope("/api/liquidation-health")
    assert api_requires_scope("/api/account-pool/state")
    assert api_requires_scope("/api/start")
    assert api_requires_scope("/api/robinhood-dex/readiness")


def test_unrelated_api_paths_are_not_accidentally_scope_required():
    assert not api_requires_scope("/api/status/metrics")
    assert not api_requires_scope("/api/unknown")


def test_registered_business_api_routes_are_covered_by_scope_policy():
    paths = {
        rule.rule for rule in app.url_map.iter_rules()
        if rule.rule.startswith("/api/")
    }
    assert validate_api_scope_policy(paths) == []


def test_scope_required_request_without_scope_returns_validation_400():
    test_app = __import__("flask").Flask("scope-contract")

    @test_app.get("/api/dex/business")
    def business():
        return {"ok": True}

    install_chain_selection_context(test_app, object())
    response = test_app.test_client().get("/api/dex/business?market_id=only-market")
    assert response.status_code == 400
    assert response.get_json() == {
        "ok": False,
        "error": "scope missing: chain_id, network, protocol",
        "code": "validation",
    }


def test_successful_named_api_response_is_checked_by_flask_after_request():
    test_app = __import__("flask").Flask("response-contract")

    @test_app.get("/api/dex/overview")
    def overview():
        return {"wrong": {}}

    install_chain_selection_context(test_app, object())
    response = test_app.test_client().get(
        "/api/dex/overview?market_id=bnb-aave-v3&chain_id=56&network=bnb&protocol=aave_v3"
    )
    assert response.status_code == 500
    assert response.get_json()["code"] == "response_contract"


def test_every_registered_scope_required_route_rejects_partial_scope():
    client = app.test_client()
    routes = {
        rule.rule
        for rule in app.url_map.iter_rules()
        if rule.rule.startswith("/api/") and api_requires_scope(rule.rule)
    }
    assert routes
    for route in sorted(routes):
        request_path = route.replace("<account>", "0x0000000000000000000000000000000000000001")
        request_path = request_path.replace("<path:filename>", "placeholder")
        response = client.open(
            request_path + "?market_id=only-market", method="OPTIONS"
        )
        assert response.status_code == 400, (route, response.get_data(as_text=True))
        assert response.get_json().get("code") == "validation", route


def test_scope_contract_metadata_is_method_aware():
    assert api_scope_contract("/api/dex/overview", "post") == {
        "path": "/api/dex/overview",
        "method": "POST",
        "scope_required": True,
        "required_scope_fields": ["market_id", "chain_id", "network", "protocol"],
    }
    assert api_scope_contract("/api/status") == {
        "path": "/api/status",
        "method": "GET",
        "scope_required": False,
        "required_scope_fields": [],
    }


def test_api_response_contract_rejects_non_objects_and_missing_keys():
    assert validate_api_response_contract({"ok": True}, required_keys=("ok",))
    with pytest.raises(ValueError, match="must be a JSON object"):
        validate_api_response_contract([])
    with pytest.raises(ValueError, match="missing keys: scope"):
        validate_api_response_contract({"ok": True}, required_keys=("scope",))


def test_core_api_response_schemas_declare_required_keys():
    assert response_required_keys("/api/status") == ("running", "control_status")
    assert response_required_keys("/api/debt-pool/state") == ("status",)
    assert response_required_keys("/api/unknown") == ()


def test_domain_api_response_schemas_are_registered():
    assert response_required_keys("/api/dex/overview") == ("summary",)
    assert response_required_keys("/api/liquidation/market") == ("market_id", "chain_id")
    assert validate_named_response_contract("/api/dex/overview", {"summary": {}})
    with pytest.raises(ValueError, match="missing keys: routes"):
        validate_named_response_contract("/api/dex/quote-routes", {"summary": {}})


def test_execution_and_admin_response_schemas_are_registered():
    samples = {
        "/api/execution-plan/quote": {"quote": {}},
        "/api/execution-plan/payload": {"payload": {}},
        "/api/binance-market/cow-config": {"network": "bnb"},
        "/api/robinhood-dex/readiness": {"readiness": {}},
        "/api/liquidation/accounts": {"accounts": []},
    }
    assert validate_registered_response_contracts(samples) == sorted(samples)


def test_response_schema_registry_has_no_orphaned_routes():
    paths = {rule.rule for rule in app.url_map.iter_rules() if rule.rule.startswith("/api/")}
    assert validate_response_schema_registry(paths) == []


def test_response_schema_registry_reports_uncovered_business_routes():
    assert missing_response_schema_paths({"/api/dex/overview", "/api/dex/unregistered"}) == [
        "/api/dex/unregistered"
    ]


def test_market_analysis_response_schemas_are_registered():
    samples = {
        "/api/trade-stats": {"stats": {}},
        "/api/observations": {"symbol": "AVAXUSDT", "rows": []},
        "/api/aave-pair-prices": {"x_symbol": "A", "y_symbol": "B", "rows": []},
        "/api/binance-pair-prices": {"x_symbol": "A", "y_symbol": "B", "rows": []},
        "/api/pair-route-profits": {"x_symbol": "A", "y_symbol": "B", "routes": []},
        "/api/chart-symbols": {"symbols": []},
        "/api/binance-extremes/latest": {"extremes": {}},
        "/api/arbitrage/latest": {"simulation": {}},
        "/api/trigger/latest": {"trigger": {}},
        "/api/executable-signal/latest": {"executable_signal": {}},
    }
    assert validate_registered_response_contracts(samples) == sorted(samples)


def test_control_and_execution_response_schemas_are_registered():
    samples = {
        "/api/start": {"running": False, "starting": True},
        "/api/stop": {"running": False},
        "/api/clear": {"cleared": True},
        "/api/clear-files": {"deleted": [], "errors": []},
        "/api/dex/market-observation/refresh": {"refresh_state": {}},
        "/api/dex/quote-request": {"chain_selection": {}},
        "/api/dex-costs": {"symbol": "AVAXUSDT"},
        "/api/execution-plan/quote": {"quote": {}},
        "/api/execution-plan/payload": {"payload": {}},
        "/api/binance-market/cow-execution-attempts": {"attempts": []},
        "/api/binance-market/cow-candidate-queue": {"queue": []},
        "/api/binance-market/cow-submission-pause": {"paused": False},
        "/api/binance-market/cow-submission-pause/clear": {"paused": False},
        "/api/binance-market/cow-tokens": {"tokens": []},
        "/api/binance-market/cow-tokens/refresh": {"tokens": []},
        "/api/binance-market/cow-config": {"network": "bnb"},
        "/api/liquidation/control-summary": {"configured": True},
        "/api/liquidation/borrow-pool": {"rows": []},
        "/api/liquidation/borrow-pool/batches": {"configured": True, "batches": []},
        "/api/liquidation/borrow-pool/latest-batch": {"configured": True, "batch": {}},
        "/api/liquidation/high-frequency-pool": {"configured": True, "count": 0, "rows": []},
        "/api/liquidation/core-opportunities": {"configured": True, "count": 0, "rows": []},
        "/api/liquidation-settings": {
            "refresh_profile": {},
            "retention_days": 0,
            "scan_interval_seconds": 0,
            "discovery_interval_seconds": 0,
            "raw": {},
        },
        "/api/liquidation/pause-guard/clear": {"paused": False},
        "/api/liquidation/account/execute": {
            "account": "0x0000000000000000000000000000000000000001",
            "error": "blocked",
            "state": "submission_blocked",
            "execution_phase": "self_funded_disabled",
            "blocked_reasons": ["flashloan_executor_required"],
        },
    }
    assert validate_registered_response_contracts(samples) == sorted(samples)


def test_liquidation_response_schema_required_keys_match_route_payloads():
    assert response_required_keys("/api/liquidation/borrow-pool/batches") == ("configured", "batches")
    assert response_required_keys("/api/liquidation/borrow-pool/latest-batch") == ("configured", "batch")
    assert response_required_keys("/api/liquidation/high-frequency-pool") == ("configured", "count", "rows")
    assert response_required_keys("/api/liquidation/core-opportunities") == ("configured", "count", "rows")
    assert response_required_keys("/api/liquidation-settings") == (
        "refresh_profile",
        "retention_days",
        "scan_interval_seconds",
        "discovery_interval_seconds",
        "raw",
    )
    assert response_required_keys("/api/liquidation/pause-guard/clear") == ("paused",)
    assert response_required_keys("/api/liquidation/account/execute") == (
        "account",
        "error",
        "state",
        "execution_phase",
        "blocked_reasons",
    )

    with pytest.raises(ValueError, match="missing keys: configured"):
        validate_named_response_contract("/api/liquidation/borrow-pool/batches", {"batches": []})


def test_newly_registered_business_api_response_schemas_are_valid():
    samples = {
        "/api/binance-velocity/candidates": {
            "candidate_count": 0,
            "pairs": [],
            "side_limit": 20,
        },
        "/api/db-summary": {"rows": 0, "db_counts": {}, "liquidation": {}},
        "/api/debt-pool/decision": {
            "status": "idle_fresh",
            "result": "no_normal_pool_risk",
            "route_intent": None,
        },
        "/api/dex-pools/candidates/refresh": {"candidates": [], "refresh_state": {}},
        "/api/dex/submissions": {"mode": "dex_submission_review", "summary": {}, "attempts": []},
        "/api/dex/submissions/clear": {"ok": True, "mode": "dex_submission_clear", "cleared": 0, "results": []},
        "/api/flash-loan/pool-discovery/refresh": {"protocol_count": 0, "protocols": []},
        "/api/flash-loan/pools/refresh": {"providers": [], "refresh_state": {}},
        "/api/liquidation-discovery": {"saved": False, "count": 0},
        "/api/liquidation-health": {"rows": [], "summary": {}},
        "/api/liquidation/borrow-pool/scan": {"rows": [], "summary": {}},
        "/api/liquidation/account": {"account": "0x1", "summary": {}},
        "/api/liquidation/account/<account>/attempts": {"configured": False, "attempts": []},
        "/api/liquidation/account/<account>/samples": {"configured": False, "samples": []},
        "/api/liquidation/account/<account>/static-call-and-save": {"account": "0x1"},
        "/api/liquidation/account/cached": {"account": "0x1", "cached": True},
        "/api/liquidation/account/payload": {"request": {}},
        "/api/liquidation/account/preflight": {"account": "0x1"},
        "/api/liquidation/account/flashloan": {"mode": "flashloan"},
        "/api/liquidation/account-backfill": {"running": False, "stage": "idle", "stop_requested": False},
        "/api/liquidation/account-backfill/start": {"started": True, "running": True},
        "/api/liquidation/account-backfill/stop": {"running": False, "stop_requested": True},
        "/api/liquidation/daemon/status": {"state": "stopped", "running": False},
        "/api/liquidation/preflight/<account>": {"account": "0x1"},
        "/api/liquidation/reports/daily": {"configured": False, "report": None},
    }
    assert validate_registered_response_contracts(samples) == sorted(samples)
