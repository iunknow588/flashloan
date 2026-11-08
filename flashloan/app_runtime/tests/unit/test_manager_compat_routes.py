from __future__ import annotations

from flask import Flask

from core.manager_api_client import ManagerApiClientError
from web import manager_compat_routes


def _app() -> Flask:
    app = Flask(__name__)
    manager_compat_routes.register_manager_compat_routes(app)
    return app


def test_manager_compat_routes_are_disabled_by_default(monkeypatch):
    monkeypatch.delenv("MANAGER_UI_DATA_SOURCE", raising=False)

    response = _app().test_client().get("/api/manager/pipeline/status?chain_id=4663")

    assert response.status_code == 409
    payload = response.get_json()
    assert payload["ok"] is False
    assert payload["code"] == "MANAGER_UI_DATA_SOURCE_LEGACY"
    assert payload["dataSource"] == "legacy"


def test_pipeline_status_route_calls_manager_and_marks_manager_source(monkeypatch):
    calls = []

    class FakeClient:
        def get_json(self, path, *, query=None, request_id=None):
            calls.append(
                {
                    "path": path,
                    "query": dict(query or {}),
                    "request_id": request_id,
                }
            )
            return {"ok": True, "version": "M8", "data": {"chainId": 4663}}

    monkeypatch.setenv("MANAGER_UI_DATA_SOURCE", "manager")
    monkeypatch.setattr(
        manager_compat_routes.ManagerApiClient,
        "from_env",
        lambda: FakeClient(),
    )

    with _app().test_client() as client:
        response = client.get(
            "/api/manager/pipeline/status?chainId=4663",
            headers={"X-Request-Id": "web-pipeline-status-1"},
        )

    assert response.status_code == 200
    assert response.get_json()["dataSource"] == "manager"
    assert calls == [
        {
            "path": "/v1/pipeline/status",
            "query": {"chainId": 4663},
            "request_id": None,
        }
    ]


def test_pipeline_runs_route_validates_limit_and_preserves_manager_error(monkeypatch):
    class FailingClient:
        def get_json(self, path, *, query=None, request_id=None):
            raise ManagerApiClientError(
                "MANAGER_API_UNAVAILABLE",
                "connection refused",
                status=503,
            )

    monkeypatch.setenv("MANAGER_UI_DATA_SOURCE", "manager")
    monkeypatch.setattr(
        manager_compat_routes.ManagerApiClient,
        "from_env",
        lambda: FailingClient(),
    )
    app = _app()

    invalid = app.test_client().get("/api/manager/pipeline/runs?chain_id=4663&limit=0")
    assert invalid.status_code == 400
    assert invalid.get_json()["code"] == "INVALID_REQUEST"

    unavailable = app.test_client().get("/api/manager/pipeline/runs?chain_id=4663&limit=50")
    assert unavailable.status_code == 503
    payload = unavailable.get_json()
    assert payload["code"] == "MANAGER_API_UNAVAILABLE"
    assert payload["dataSource"] == "manager"
    assert payload["upstreamStatus"] == 503


def test_pipeline_run_detail_encodes_run_id_and_requires_chain(monkeypatch):
    calls = []

    class FakeClient:
        def get_json(self, path, *, query=None, request_id=None):
            calls.append((path, dict(query or {})))
            return {"ok": True, "data": {"runId": "run 1"}}

    monkeypatch.setenv("MANAGER_UI_DATA_SOURCE", "manager")
    monkeypatch.setattr(
        manager_compat_routes.ManagerApiClient,
        "from_env",
        lambda: FakeClient(),
    )
    app = _app()

    missing_chain = app.test_client().get("/api/manager/pipeline/runs/run%201")
    assert missing_chain.status_code == 400
    assert missing_chain.get_json()["code"] == "INVALID_REQUEST"

    response = app.test_client().get("/api/manager/pipeline/runs/run%201?chain_id=4663")
    assert response.status_code == 200
    assert calls == [("/v1/pipeline/runs/run%201", {"chainId": 4663})]
