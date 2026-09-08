from pathlib import Path


APP_RUNTIME = Path(__file__).resolve().parents[2]
HIGH_TOUCH_ROUTES = (
    APP_RUNTIME / "web" / "data_routes" / "dex_surfaces.py",
    APP_RUNTIME / "web" / "control_panel.py",
)
HELPER_FILES = (
    APP_RUNTIME.parent / "src" / "core" / "dex_surface_provider.py",
    APP_RUNTIME / "web" / "data_routes" / "dex_surface_response.py",
    APP_RUNTIME / "web" / "data_routes" / "dex_surface_orchestration.py",
    APP_RUNTIME.parent / "src" / "core" / "cow_evidence_helpers.py",
    APP_RUNTIME.parent / "src" / "bot" / "liquidation" / "route_response.py",
    APP_RUNTIME / "web" / "data_routes" / "liquidation_route_request.py",
    APP_RUNTIME / "web" / "data_routes" / "liquidation_control_payloads.py",
    APP_RUNTIME.parent / "src" / "market" / "binance_market" / "route_services" / "cow_support.py",
    APP_RUNTIME.parent / "src" / "market" / "binance_market" / "route_services" / "state.py",
    APP_RUNTIME.parent / "src" / "market" / "binance_market" / "route_services" / "quote.py",
    APP_RUNTIME / "web" / "data_routes" / "market_analysis_services.py",
)


def test_route_helper_directories_do_not_register_routes_or_import_web_globals():
    for path in HELPER_FILES:
        text = path.read_text(encoding="utf-8")
        assert "register_" not in text, path
        assert "@app." not in text, path


def test_high_touch_route_files_still_form_compatible_boundary(): 
    # These files are still intentionally large while legacy Web globals are
    # migrated. The guard proves we have not introduced new app-side imports.
    for path in HIGH_TOUCH_ROUTES:
        text = path.read_text(encoding="utf-8")
        assert "from app_runtime." not in text
        assert "from web.control_panel import" not in text


def test_market_service_and_dto_helpers_do_not_register_flask_routes():
    market_service = APP_RUNTIME.parent / "src" / "market" / "binance_market" / "service.py"
    assert market_service.exists()
    service_text = market_service.read_text(encoding="utf-8")
    assert "from flask import" not in service_text
    assert "@app." not in service_text
    assert "register_" not in service_text

    for path in HELPER_FILES:
        text = path.read_text(encoding="utf-8")
        assert "@app." not in text, path
        assert "register_" not in text, path
