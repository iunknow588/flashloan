from __future__ import annotations

from typing import Any

from flask import Flask, g

from web.data_routes import dex_surface_routes_ops
from web.data_routes import dex_surfaces


def _install_test_app(
    monkeypatch,
    *,
    runtime: Any | None = None,
) -> Flask:
    app = Flask(__name__)
    dex_surface_routes_ops.register_dex_scanner_routes(app)
    monkeypatch.setattr(
        dex_surfaces,
        "selected_chain_id_from_request",
        lambda: ({"chainId": 46630, "network": "robinhood-testnet"}, 46630),
    )
    monkeypatch.setattr(
        dex_surfaces,
        "request_dex_protocols_arg",
        lambda: ["uniswap_v4"],
    )
    monkeypatch.setattr(
        dex_surfaces,
        "get_dex_opportunity_scanner",
        lambda chain_id: (_ for _ in ()).throw(
            AssertionError("manual transitions must not resolve the Python scanner")
        ),
    )
    if runtime is not None:

        @app.before_request
        def _bind_runtime():
            g.chain_runtime = runtime

    return app


class _FakeRuntime:
    def __init__(self, result: dict[str, Any] | None = None) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.native_pool_requested = False
        self._result = result or {
            "ok": True,
            "accepted": True,
            "transitionQueued": True,
            "groupKey": "aaa|bbb|ccc",
        }

    @property
    def native_pool(self):
        self.native_pool_requested = True
        return object()

    def execute_command(self, command: str, **kwargs: Any) -> dict[str, Any]:
        self.calls.append((command, kwargs))
        return dict(self._result)


def _review_headers() -> dict[str, str]:
    return {
        "X-Request-Id": "review-request",
        "Idempotency-Key": "review-idem",
    }


def _review_body(*, dry_run: bool = False) -> dict[str, Any]:
    return {
        "routeSymbols": ["AAA", "BBB", "CCC", "AAA"],
        "routeAddresses": ["0xa", "0xb", "0xc"],
        "dryRun": dry_run,
    }


def test_manual_transition_routes_require_write_contract(monkeypatch) -> None:
    app = _install_test_app(monkeypatch)
    client = app.test_client()

    missing_request = client.post(
        "/api/dex/closed-loop-groups/manual-review",
        json={"routeSymbols": ["AAA", "BBB", "CCC", "AAA"], "dryRun": False},
        headers={"Idempotency-Key": "idem-1"},
    )
    missing_idempotency = client.post(
        "/api/dex/closed-loop-groups/manual-broadcast",
        json={"routeSymbols": ["AAA", "BBB", "CCC", "AAA"], "dryRun": False},
        headers={"X-Request-Id": "request-1"},
    )
    missing_dry = client.post(
        "/api/dex/closed-loop-groups/manual-review",
        json={"routeSymbols": ["AAA", "BBB", "CCC", "AAA"]},
        headers={"X-Request-Id": "request-1", "Idempotency-Key": "idem-1"},
    )

    assert missing_request.status_code == 400
    assert missing_request.get_json()["error"] == "request_id_required"
    assert missing_idempotency.status_code == 400
    assert missing_idempotency.get_json()["error"] == "idempotency_key_required"
    assert missing_dry.status_code == 400
    assert missing_dry.get_json()["error"] == "dry_run_boolean_required"


def test_manual_review_delegates_only_to_runtime_core_command(monkeypatch) -> None:
    runtime = _FakeRuntime()
    app = _install_test_app(monkeypatch, runtime=runtime)

    response = app.test_client().post(
        "/api/dex/closed-loop-groups/manual-review",
        json=_review_body(),
        headers=_review_headers(),
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["ok"] is True
    assert payload["transitionQueued"] is True
    assert payload["group_key"] == "aaa|bbb|ccc"
    assert runtime.native_pool_requested is True
    assert runtime.calls == [
        (
            "manual_review",
            {
                "request_id": "review-request",
                "idempotency_key": "review-idem",
                "dry_run": False,
                "options": {
                    "groupKey": "aaa|bbb|ccc",
                    "routeSymbols": ["AAA", "BBB", "CCC", "AAA"],
                    "routeAddresses": ["0xa", "0xb", "0xc"],
                },
            },
        )
    ]


def test_manual_broadcast_delegates_only_to_runtime_core_command(monkeypatch) -> None:
    runtime = _FakeRuntime()
    app = _install_test_app(monkeypatch, runtime=runtime)

    response = app.test_client().post(
        "/api/dex/closed-loop-groups/manual-broadcast",
        json=_review_body(),
        headers=_review_headers(),
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["ok"] is True
    assert payload["mode"] == "native_closed_loop_group_manual_broadcast"
    assert payload["transitionQueued"] is True
    assert runtime.native_pool_requested is True
    assert runtime.calls == [
        (
            "manual_broadcast",
            {
                "request_id": "review-request",
                "idempotency_key": "review-idem",
                "dry_run": False,
                "options": {
                    "groupKey": "aaa|bbb|ccc",
                    "routeSymbols": ["AAA", "BBB", "CCC", "AAA"],
                    "routeAddresses": ["0xa", "0xb", "0xc"],
                },
            },
        )
    ]


def test_manual_review_dry_run_keeps_http_200(monkeypatch) -> None:
    runtime = _FakeRuntime(
        {
            "ok": True,
            "accepted": True,
            "groupKey": "aaa|bbb|ccc",
            "dryRun": True,
        }
    )
    app = _install_test_app(monkeypatch, runtime=runtime)

    response = app.test_client().post(
        "/api/dex/closed-loop-groups/manual-review",
        json=_review_body(dry_run=True),
        headers=_review_headers(),
    )

    assert response.status_code == 200
    assert response.get_json()["dryRun"] is True
    assert runtime.calls[0][1]["dry_run"] is True


def test_manual_broadcast_dry_run_keeps_http_200(monkeypatch) -> None:
    runtime = _FakeRuntime(
        {
            "ok": True,
            "accepted": True,
            "groupKey": "aaa|bbb|ccc",
            "dryRun": True,
        }
    )
    app = _install_test_app(monkeypatch, runtime=runtime)

    response = app.test_client().post(
        "/api/dex/closed-loop-groups/manual-broadcast",
        json=_review_body(dry_run=True),
        headers=_review_headers(),
    )

    assert response.status_code == 200
    assert response.get_json()["dryRun"] is True
    assert runtime.calls[0][0] == "manual_broadcast"
    assert runtime.calls[0][1]["dry_run"] is True


def test_manual_review_fails_closed_without_runtime_core(monkeypatch) -> None:
    app = _install_test_app(monkeypatch)

    response = app.test_client().post(
        "/api/dex/closed-loop-groups/manual-review",
        json=_review_body(),
        headers=_review_headers(),
    )

    assert response.status_code == 503
    payload = response.get_json()
    assert payload["error"] == "runtime_core_command_unavailable"
    assert payload["group_key"] == "aaa|bbb|ccc"


def test_manual_review_preserves_runtime_core_error_status(monkeypatch) -> None:
    runtime = _FakeRuntime(
        {
            "ok": False,
            "code": "CLOSED_LOOP_GROUP_BUSY",
            "detail": "native review worker is busy",
            "upstreamStatus": 409,
        }
    )
    app = _install_test_app(monkeypatch, runtime=runtime)

    response = app.test_client().post(
        "/api/dex/closed-loop-groups/manual-review",
        json=_review_body(),
        headers=_review_headers(),
    )

    assert response.status_code == 409
    payload = response.get_json()
    assert payload["code"] == "CLOSED_LOOP_GROUP_BUSY"
    assert payload["upstreamStatus"] == 409


def test_manual_review_rejects_non_closed_loop_route_before_runtime(monkeypatch) -> None:
    runtime = _FakeRuntime()
    app = _install_test_app(monkeypatch, runtime=runtime)

    response = app.test_client().post(
        "/api/dex/closed-loop-groups/manual-review",
        json={
            "routeSymbols": ["AAA", "BBB", "CCC"],
            "routeAddresses": [],
            "dryRun": False,
        },
        headers=_review_headers(),
    )

    assert response.status_code == 400
    assert response.get_json()["error"] == "closed_loop_route_unsupported"
    assert runtime.calls == []


def test_manual_broadcast_fails_closed_without_runtime_core(monkeypatch) -> None:
    app = _install_test_app(monkeypatch)

    response = app.test_client().post(
        "/api/dex/closed-loop-groups/manual-broadcast",
        json=_review_body(),
        headers=_review_headers(),
    )

    assert response.status_code == 503
    payload = response.get_json()
    assert payload["mode"] == "native_closed_loop_group_manual_broadcast"
    assert payload["error"] == "runtime_core_command_unavailable"
    assert payload["group_key"] == "aaa|bbb|ccc"


def test_manual_broadcast_preserves_runtime_core_error_status(monkeypatch) -> None:
    runtime = _FakeRuntime(
        {
            "ok": False,
            "code": "EXECUTION_TASK_NOT_WAITING",
            "detail": "execution task is already queued",
            "upstreamStatus": 409,
        }
    )
    app = _install_test_app(monkeypatch, runtime=runtime)

    response = app.test_client().post(
        "/api/dex/closed-loop-groups/manual-broadcast",
        json=_review_body(),
        headers=_review_headers(),
    )

    assert response.status_code == 409
    payload = response.get_json()
    assert payload["mode"] == "native_closed_loop_group_manual_broadcast"
    assert payload["code"] == "EXECUTION_TASK_NOT_WAITING"
    assert payload["upstreamStatus"] == 409
