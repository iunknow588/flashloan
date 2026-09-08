from __future__ import annotations

from flask import jsonify, request

from core.chain_selection import ChainSelectionError
from web.chain_selection import chain_selection_payload


def _database_url(panel) -> str | None:
    getter = getattr(panel, "database_url_or_none", None)
    if not callable(getter):
        return None
    try:
        return getter()
    except Exception:
        return None


def register_chain_selection_routes(app, panel) -> None:
    @app.get("/api/chain-selection")
    @app.get("/api/markets")
    def chain_selection():
        try:
            payload = chain_selection_payload(
                database_url=_database_url(panel),
                feature=request.args.get("feature", "").strip() or None,
                default_market_id=request.args.get("default_market_id", "").strip() or None,
            )
        except ChainSelectionError as exc:
            return jsonify({"ok": False, "error": exc.message, "code": exc.code}), 400
        return jsonify(payload)
