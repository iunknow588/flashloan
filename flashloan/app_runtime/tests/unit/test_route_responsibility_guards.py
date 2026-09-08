from pathlib import Path


APP_RUNTIME = Path(__file__).resolve().parents[2]


def test_data_route_helpers_do_not_register_flask_routes():
    helpers = [
        APP_RUNTIME / "web" / "data_routes" / "dex_surface_response.py",
        APP_RUNTIME / "web" / "data_routes" / "dex_surface_orchestration.py",
    ]
    for path in helpers:
        text = path.read_text(encoding="utf-8")
        assert "register_" not in text, path
        assert "@app." not in text, path


def test_provider_facade_does_not_import_flask_route_globals():
    text = (APP_RUNTIME.parent / "src" / "core" / "dex_surface_provider.py").read_text(encoding="utf-8")
    assert "from flask import" not in text
    assert "from web import" not in text


def test_market_service_is_not_a_web_route_registration_owner():
    market_service = APP_RUNTIME.parent / "src" / "market" / "binance_market" / "service.py"
    text = market_service.read_text(encoding="utf-8")
    assert "from flask import" not in text
    assert "jsonify(" not in text
    assert "@app." not in text
    assert "register_" not in text


def test_liquidation_response_helpers_are_route_registration_free():
    path = APP_RUNTIME.parent / "src" / "bot" / "liquidation" / "route_response.py"
    text = path.read_text(encoding="utf-8")
    assert "from flask import" not in text
    assert "@app." not in text
    assert "register_" not in text


def test_app_assembly_binds_explicit_providers_on_route_context():
    from web import control_panel_data_routes as shared
    from web.control_panel import app as control_panel_app

    assert control_panel_app is not None
    providers = shared.ROUTE_CONTEXT.providers()
    assert "dex_surface" in providers
    assert "panel" in providers
