from flask import Flask, g, jsonify, request
import json
from uuid import uuid4

from core.chain_selection import ChainSelectionError
from web.chain_selection_routes import register_chain_selection_routes
from web.chain_selection import resolve_request_chain_selection, validate_explicit_request_scope
from core.api_scope_policy import api_requires_scope
from core.api_scope_policy import validate_named_response_contract
from core.workflow import HARD_WORKFLOW_BLOCKS, WorkflowContextError, resolve_workflow_context
from web.control_panel_control_routes import register_control_routes
from web.control_panel_data_routes import register_data_routes
from web.control_panel_liquidation_context import install_liquidation_context
from web.control_panel_liquidation_routes import register_liquidation_routes
from web.control_panel_page_routes import register_page_routes
from web import control_panel_data_routes as shared_data_routes
from market.binance_market import dex_surfaces as market_dex_surfaces
from core.dex_surface_provider import bind_provider




def _panel_database_url(panel) -> str | None:
    getter = getattr(panel, "database_url_or_none", None)
    if not callable(getter):
        return None
    try:
        return getter()
    except Exception:
        return None


def install_chain_selection_context(app: Flask, panel) -> None:
    @app.before_request
    def resolve_chain_selection_context():
        request_id = request.headers.get("X-Request-Id", "").strip() or uuid4().hex
        g.request_id = request_id
        workflow_path = request.path
        if workflow_path in {"/api/workflow-context", "/api/chain-selection", "/api/markets"}:
            workflow_path = request.args.get("page_path", "").strip() or request.referrer or ""
        try:
            workflow_context = resolve_workflow_context(workflow_path, request.args)
        except WorkflowContextError as exc:
            return jsonify({"ok": False, "error": exc.message, "code": exc.code}), 400
        if workflow_context is not None:
            g.workflow_context = workflow_context
            if (
                workflow_context.workflow == "chain_neutral"
                and request.path.startswith("/api/")
                and request.method not in {"GET", "HEAD", "OPTIONS"}
            ):
                return jsonify(
                    {
                        "ok": False,
                        "error": "chain-neutral analytics endpoints are read-only",
                        "code": "workflow_execution_blocked",
                        "workflow_context": workflow_context.as_dict(),
                    }
                ), 400
            explicit_execution_mode = request.args.get("execution_mode", "").strip()
            if (
                request.path.startswith("/api/")
                and request.path != "/api/workflow-context"
                and explicit_execution_mode
                and HARD_WORKFLOW_BLOCKS.intersection(workflow_context.blocked_reasons)
            ):
                return jsonify(
                    {
                        "ok": False,
                        "error": "requested execution mode is blocked for this workflow",
                        "code": "workflow_execution_blocked",
                        "workflow_context": workflow_context.as_dict(),
                    }
                ), 400

        if not request.path.startswith("/api/") or not api_requires_scope(request.path):
            return None
        try:
            if request.path.startswith("/api/"):
                g.request_scope = validate_explicit_request_scope()
            g.chain_selection = resolve_request_chain_selection(
                database_url=_panel_database_url(panel),
                feature=None,
            )
        except ChainSelectionError as exc:
            return jsonify({"ok": False, "error": exc.message, "code": exc.code}), 400
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc), "code": "validation"}), 400
        return None

    @app.after_request
    def expose_chain_selection_context(response):
        if request.path.startswith("/api/") and 200 <= response.status_code < 300:
            if response.is_json and not getattr(g, "response_contract_validated", False):
                body = response.get_json(silent=True)
                mode_by_path = {
                    "/api/dex/overview": "dex_overview",
                    "/api/dex/market-observation": "dex_market_observation",
                    "/api/dex/quote-routes": "dex_quote_routes",
                    "/api/dex/submissions": "dex_submission_review",
                    "/api/dex-pools/candidates": "dex_pool_candidates",
                    "/api/dex-pools/candidates/refresh": "dex_pool_candidates",
                }
                mode = mode_by_path.get(request.path)
                if mode and isinstance(body, dict) and not body.get("mode"):
                    body["mode"] = mode
                    response.set_data(json.dumps(body, ensure_ascii=False))
                    response.content_type = "application/json"
                required_keys = __import__("core.api_scope_policy", fromlist=["response_required_keys"]).response_required_keys(request.path)
                if required_keys:
                    try:
                        validate_named_response_contract(request.path, body)
                    except ValueError as exc:
                        invalid = jsonify({"ok": False, "error": str(exc), "code": "response_contract"})
                        invalid.status_code = 500
                        return invalid
        request_id = getattr(g, "request_id", None)
        if request_id:
            response.headers["X-Request-Id"] = str(request_id)
        selection = getattr(g, "chain_selection", None)
        if selection is not None:
            response.headers["X-Chain-Selection-Market"] = selection.market_id
            response.headers["X-Chain-Selection-Global-Id"] = selection.global_chain_id
            if selection.chain_id is not None:
                response.headers["X-Chain-Selection-Chain-Id"] = str(selection.chain_id)
        workflow_context = getattr(g, "workflow_context", None)
        if workflow_context is not None:
            response.headers["X-Workflow"] = workflow_context.workflow
            if workflow_context.execution_mode:
                response.headers["X-Execution-Mode"] = workflow_context.execution_mode
            response.headers["X-Chain-Scope"] = workflow_context.scope_type
        content_type = str(response.headers.get("Content-Type", "")).lower()
        if content_type.startswith("text/html"):
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
        return response


def register_workflow_context_route(app: Flask) -> None:
    @app.get("/api/workflow-context")
    def workflow_context():
        page_path = request.args.get("page_path", "").strip() or request.referrer or "/"
        try:
            from core.workflow import workflow_context_payload

            payload = workflow_context_payload(page_path, request.args)
            if getattr(g, "request_id", None):
                payload["request_id"] = g.request_id
            return jsonify(payload)
        except WorkflowContextError as exc:
            return jsonify({"ok": False, "error": exc.message, "code": exc.code}), 400


def register_control_panel_routes(app: Flask, panel) -> None:
    install_chain_selection_context(app, panel)
    install_liquidation_context(panel)
    bind_provider(shared_data_routes.ROUTE_CONTEXT, market_dex_surfaces)
    register_page_routes(app, panel)
    register_workflow_context_route(app)
    register_chain_selection_routes(app, panel)
    register_liquidation_routes(app, panel)
    register_data_routes(app, panel)
    register_control_routes(app, panel)


def create_control_panel_app(panel) -> Flask:
    app = Flask(getattr(panel, "__name__", __name__))
    register_control_panel_routes(app, panel)
    return app


