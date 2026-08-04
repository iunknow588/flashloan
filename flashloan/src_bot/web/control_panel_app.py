from flask import Flask

from web.control_panel_control_routes import register_control_routes
from web.control_panel_data_routes import register_data_routes
from web.control_panel_liquidation_context import install_liquidation_context
from web.control_panel_liquidation_routes import register_liquidation_routes
from web.control_panel_page_routes import register_page_routes


def register_control_panel_routes(app: Flask, panel) -> None:
    install_liquidation_context(panel)
    register_page_routes(app, panel)
    register_liquidation_routes(app, panel)
    register_data_routes(app, panel)
    register_control_routes(app, panel)


def create_control_panel_app(panel) -> Flask:
    app = Flask(getattr(panel, "__name__", __name__))
    register_control_panel_routes(app, panel)
    return app

