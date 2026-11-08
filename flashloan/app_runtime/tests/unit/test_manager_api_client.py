from __future__ import annotations

import json

import pytest

from core import manager_api_client
from core.manager_api_client import ManagerApiClient, ManagerApiClientError


class FakeResponse:
    def __init__(self, body: bytes, *, status: int = 200) -> None:
        self._body = body
        self.status = status

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False


def test_manager_client_builds_read_request_with_query_and_request_id(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["timeout"] = timeout
        captured["headers"] = {key.lower(): value for key, value in request.header_items()}
        return FakeResponse(b'{"ok":true,"version":"M8","data":{"chainId":4663}}')

    monkeypatch.setattr(manager_api_client, "urlopen", fake_urlopen)

    payload = ManagerApiClient(
        base_url="http://127.0.0.1:8091/",
        timeout_seconds=2.5,
    ).get_json(
        "/v1/pipeline/runs",
        query={"chainId": 4663, "limit": 50, "empty": ""},
        request_id="web-request-9",
    )

    assert payload["version"] == "M8"
    assert captured["url"] == "http://127.0.0.1:8091/v1/pipeline/runs?chainId=4663&limit=50"
    assert captured["timeout"] == 2.5
    assert captured["headers"]["x-request-id"] == "web-request-9"
    assert captured["headers"]["accept"] == "application/json"


def test_manager_client_rejects_non_object_and_invalid_json(monkeypatch):
    monkeypatch.setattr(
        manager_api_client,
        "urlopen",
        lambda request, timeout: FakeResponse(b"[]"),
    )
    with pytest.raises(ManagerApiClientError) as non_object:
        ManagerApiClient().get_json("/v1/capabilities")
    assert non_object.value.code == "MANAGER_API_INVALID_RESPONSE"

    monkeypatch.setattr(
        manager_api_client,
        "urlopen",
        lambda request, timeout: FakeResponse(b"not-json"),
    )
    with pytest.raises(ManagerApiClientError) as invalid_json:
        ManagerApiClient().get_json("/v1/capabilities")
    assert invalid_json.value.code == "MANAGER_API_INVALID_RESPONSE"


def test_manager_client_builds_required_write_request(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["timeout"] = timeout
        captured["method"] = request.get_method()
        captured["headers"] = {key.lower(): value for key, value in request.header_items()}
        captured["body"] = json.loads(request.data.decode("utf-8"))
        return FakeResponse(
            b'{"ok":true,"version":"M8","data":{"clearedAttempts":2,"remainingAttempts":0}}'
        )

    monkeypatch.setattr(manager_api_client, "urlopen", fake_urlopen)

    payload = ManagerApiClient(
        base_url="http://127.0.0.1:8091",
        timeout_seconds=2.5,
    ).post_json(
        "/v1/broadcasts/clear",
        payload={"chainId": 4663, "dryRun": False},
        request_id="web-request-10",
        idempotency_key="ui-clear-10",
    )

    assert payload["data"]["clearedAttempts"] == 2
    assert captured["url"] == "http://127.0.0.1:8091/v1/broadcasts/clear"
    assert captured["timeout"] == 2.5
    assert captured["method"] == "POST"
    assert captured["headers"]["x-request-id"] == "web-request-10"
    assert captured["headers"]["idempotency-key"] == "ui-clear-10"
    assert captured["headers"]["content-type"] == "application/json"
    assert captured["body"] == {"chainId": 4663, "dryRun": False}


def test_manager_client_configuration_and_ui_source_are_fail_closed(monkeypatch):
    monkeypatch.setenv("MANAGER_API_TIMEOUT_SECONDS", "0")
    with pytest.raises(ManagerApiClientError) as timeout_error:
        ManagerApiClient.from_env()
    assert timeout_error.value.code == "MANAGER_API_CONFIG_INVALID"

    with pytest.raises(ManagerApiClientError) as base_url_error:
        ManagerApiClient(base_url="file:///tmp/manager.sqlite3").get_json("/v1/capabilities")
    assert base_url_error.value.code == "MANAGER_API_CONFIG_INVALID"

    monkeypatch.setenv("MANAGER_UI_DATA_SOURCE", "database")
    with pytest.raises(ManagerApiClientError) as source_error:
        manager_api_client.manager_ui_data_source()
    assert source_error.value.code == "MANAGER_UI_DATA_SOURCE_INVALID"
