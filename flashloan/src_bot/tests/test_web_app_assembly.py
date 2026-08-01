from web.control_panel import app


def test_control_panel_routes_are_assembled_by_app_factory():
    paths = {rule.rule for rule in app.url_map.iter_rules()}

    assert "/healthz" in paths
    assert "/api/status" in paths
    assert "/api/liquidation-health" in paths
    assert "/api/liquidation/account/<account>/static-call-and-save" in paths

