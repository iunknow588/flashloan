from html import escape
from pathlib import Path

from market.observer import ASSETS


WEB_DIR = Path(__file__).resolve().parent
TEMPLATE_PATH = WEB_DIR / "templates" / "control_panel.html"


def render_control_panel() -> str:
    chart_options = "\n".join(f'<option value="{escape(symbol)}">{escape(symbol)}</option>' for symbol in ASSETS)
    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    return template.replace("__CHART_OPTIONS__", chart_options)
