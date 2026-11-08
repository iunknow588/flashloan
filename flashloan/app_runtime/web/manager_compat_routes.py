"""Read-only Flask compatibility routes for the Rust manager API."""

from __future__ import annotations

from typing import Any, Mapping
from urllib.parse import quote

from flask import Flask, g, jsonify, request

from core.api_response_contract import error_response
from core.manager_api_client import (
    ManagerApiClient,
    ManagerApiClientError,
    manager_ui_data_source,
)


def register_manager_compat_routes(app: Flask) -> None:
    @app.get("/api/manager/capabilities")
    def manager_capabilities():
        return _manager_get("/v1/capabilities")

    @app.get("/api/manager/pipeline/status")
    def manager_pipeline_status():
        chain_id, error = _required_chain_id()
        if error is not None:
            return error
        return _manager_get("/v1/pipeline/status", query={"chainId": chain_id})

    @app.get("/api/manager/pipeline/runs")
    def manager_pipeline_runs():
        chain_id, error = _required_chain_id()
        if error is not None:
            return error
        limit, limit_error = _optional_limit(default=50)
        if limit_error is not None:
            return limit_error
        return _manager_get(
            "/v1/pipeline/runs",
            query={"chainId": chain_id, "limit": limit},
        )

    @app.get("/api/manager/pipeline/runs/<run_id>")
    def manager_pipeline_run_detail(run_id: str):
        chain_id, error = _required_chain_id()
        if error is not None:
            return error
        normalized_run_id = str(run_id or "").strip()
        if not normalized_run_id or len(normalized_run_id) > 256:
            return _compat_error(
                "INVALID_REQUEST",
                "run_id must contain between 1 and 256 characters",
                status=400,
            )
        return _manager_get(
            f"/v1/pipeline/runs/{quote(normalized_run_id, safe='')}",
            query={"chainId": chain_id},
        )


def _manager_get(path: str, *, query: Mapping[str, object] | None = None):
    try:
        data_source = manager_ui_data_source()
    except ManagerApiClientError as exc:
        return _compat_error(exc.code, exc.message, status=500, data_source="legacy")
    if data_source != "manager":
        return _compat_error(
            "MANAGER_UI_DATA_SOURCE_LEGACY",
            "manager compatibility route is disabled while MANAGER_UI_DATA_SOURCE=legacy",
            status=409,
            data_source="legacy",
        )
    try:
        payload = ManagerApiClient.from_env().get_json(
            path,
            query=query,
            request_id=getattr(g, "request_id", None),
        )
    except ManagerApiClientError as exc:
        response = _compat_error(
            exc.code,
            exc.message,
            status=503,
            data_source="manager",
        )
        body = response[0].get_json(silent=True) or {}
        if exc.status is not None:
            body["upstreamStatus"] = exc.status
        if exc.response_payload:
            body["upstream"] = exc.response_payload
        return jsonify(body), 503
    payload["dataSource"] = "manager"
    return jsonify(payload)


def _required_chain_id():
    raw = request.args.get("chain_id", "").strip() or request.args.get("chainId", "").strip()
    if not raw:
        return None, _compat_error(
            "INVALID_REQUEST",
            "chain_id is required",
            status=400,
        )
    try:
        chain_id = int(raw)
    except ValueError:
        return None, _compat_error(
            "INVALID_REQUEST",
            "chain_id must be an integer",
            status=400,
        )
    if chain_id <= 0:
        return None, _compat_error(
            "INVALID_REQUEST",
            "chain_id must be greater than zero",
            status=400,
        )
    return chain_id, None


def _optional_limit(*, default: int):
    raw = request.args.get("limit", "").strip()
    if not raw:
        return default, None
    try:
        limit = int(raw)
    except ValueError:
        return None, _compat_error(
            "INVALID_REQUEST",
            "limit must be an integer",
            status=400,
        )
    if not 1 <= limit <= 200:
        return None, _compat_error(
            "INVALID_REQUEST",
            "limit must be between 1 and 200",
            status=400,
        )
    return limit, None


def _compat_error(
    code: str,
    message: str,
    *,
    status: int,
    data_source: str = "legacy",
):
    payload: dict[str, Any] = error_response(code, message, status=status)
    payload["code"] = code
    payload["detail"] = message
    payload["dataSource"] = data_source
    payload["mode"] = "manager_api_compat"
    return jsonify(payload), status


__all__ = ["register_manager_compat_routes"]
