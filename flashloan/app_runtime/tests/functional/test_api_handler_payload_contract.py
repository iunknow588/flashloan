"""Smoke-contract checks for real, read-only Flask API handlers."""

from __future__ import annotations

import json

import pytest

from web.control_panel import app
from core.api_scope_policy import validate_named_response_contract


READ_ONLY_HANDLER_SAMPLES = (
    ("/api/status", None),
    ("/api/system-info", None),
    ("/api/workflow-context?page_path=/dex/overview", None),
    ("/api/chain-selection", None),
    ("/api/markets", None),
    ("/api/metrics/summary", None),
    ("/api/flash-loan/pools?chain_id=56", None),
    ("/api/flash-loan/pools?chain_id=46630&protocols=uniswap_v4", None),
    ("/api/binance-market/states?chain_id=56", None),
    (
        "/api/binance-market/cow-config?market_id=dex-arbitrage-56-pancakeswap-v3&chain_id=56&network=bnb&protocol=pancakeswap_v3",
        None,
    ),
    (
        "/api/binance-market/cow-tokens?market_id=dex-arbitrage-56-pancakeswap-v3&chain_id=56&network=bnb&protocol=pancakeswap_v3",
        None,
    ),
    (
        "/api/binance-market/cow-support?market_id=dex-arbitrage-56-pancakeswap-v3&chain_id=56&network=bnb&protocol=pancakeswap_v3&amount=1000",
        None,
    ),
    (
        "/api/dex-pools/candidates?market_id=dex-arbitrage-56-pancakeswap-v3&chain_id=56&network=bnb&protocol=pancakeswap_v3",
        None,
    ),
    (
        "/api/binance-velocity/candidates?market_id=dex-arbitrage-56-pancakeswap-v3&chain_id=56&network=bnb&protocol=pancakeswap_v3",
        None,
    ),
    (
        "/api/dex/overview?market_id=dex-arbitrage-56-pancakeswap-v3&chain_id=56&protocols=pancakeswap_v3",
        None,
    ),
    (
        "/api/dex/market-observation?market_id=dex-arbitrage-56-pancakeswap-v3&chain_id=56&protocols=pancakeswap_v3",
        None,
    ),
    (
        "/api/dex/quote-routes?market_id=dex-arbitrage-56-pancakeswap-v3&chain_id=56&protocols=pancakeswap_v3",
        None,
    ),
    (
        "/api/robinhood-dex/readiness?market_id=dex-arbitrage-4663-uniswap-v4&chain_id=4663&network=robinhood&protocol=uniswap_v4",
        None,
    ),
)


def test_read_only_handlers_return_contract_valid_json_payloads():
    client = app.test_client()

    for request_path, _ in READ_ONLY_HANDLER_SAMPLES:
        response = client.get(request_path)

        assert response.status_code == 200, (request_path, response.get_data(as_text=True))
        assert response.is_json, request_path
        payload = response.get_json()
        assert isinstance(payload, dict), request_path
        validate_named_response_contract(request_path.split("?", 1)[0], payload)


def test_read_only_handler_payloads_include_request_correlation_header():
    client = app.test_client()

    for request_path, _ in READ_ONLY_HANDLER_SAMPLES:
        response = client.get(request_path)

        assert response.status_code == 200, (request_path, response.get_data(as_text=True))
        assert response.headers.get("X-Request-Id"), request_path


def test_scope_required_handler_rejects_partial_scope_with_validation_code():
    client = app.test_client()

    response = client.get("/api/dex/overview?market_id=only-market")

    assert response.status_code == 400, response.get_data(as_text=True)
    payload = response.get_json()
    assert payload.get("code") == "validation", payload


def test_validation_error_shape_is_uniform_across_scope_required_api_families():
    client = app.test_client()
    paths = (
        "/api/binance-market/state?market_id=only-market",
        "/api/binance-market/states?market_id=only-market",
        "/api/dex-pools/candidates?market_id=only-market",
        "/api/binance-velocity/candidates?market_id=only-market",
        "/api/flash-loan/pools?market_id=only-market",
        "/api/dex/overview?market_id=only-market",
    )
    for request_path in paths:
        response = client.get(request_path)
        assert response.status_code == 400, request_path
        payload = response.get_json()
        assert payload.get("code") == "validation", request_path
        assert payload.get("ok") is False, request_path


def test_scope_required_handler_rejects_chain_mismatch_with_structured_error():
    client = app.test_client()

    response = client.get(
        "/api/dex/overview?market_id=dex-arbitrage-4663-uniswap-v3&chain_id=56&protocols=uniswap_v3"
    )

    assert response.status_code == 400, response.get_data(as_text=True)
    payload = response.get_json()
    assert payload == {
        "code": "chain_mismatch",
        "error": "market_id dex-arbitrage-4663-uniswap-v3 belongs to chain_id 4663, not 56",
        "ok": False,
    }


def test_scope_required_handler_rejects_protocol_mismatch_with_structured_error():
    client = app.test_client()

    response = client.get(
        "/api/dex/overview?market_id=dex-arbitrage-56-pancakeswap-v3&chain_id=56&protocols=not-a-protocol"
    )

    assert response.status_code == 400, response.get_data(as_text=True)
    payload = response.get_json()
    assert payload["ok"] is False
    assert payload["code"] == "scope_mismatch"
    assert "belongs to protocol_id pancakeswap_v3" in payload["error"]


def test_scope_required_handler_rejects_unknown_market_with_structured_error():
    client = app.test_client()

    response = client.get("/api/dex/overview?market_id=unknown-market&chain_id=56")

    assert response.status_code == 400, response.get_data(as_text=True)
    payload = response.get_json()
    assert payload["ok"] is False
    assert payload["code"] == "unknown_market"
    assert payload["error"] == "unknown market_id: unknown-market"


def test_chain_mismatch_error_shape_is_uniform_across_scope_required_api_families():
    client = app.test_client()
    paths = (
        "/api/binance-market/state?market_id=dex-arbitrage-4663-uniswap-v3&chain_id=56&protocol=uniswap_v3",
        "/api/flash-loan/pools?market_id=dex-arbitrage-4663-uniswap-v3&chain_id=56&protocol=uniswap_v3",
        "/api/dex-pools/candidates?market_id=dex-arbitrage-4663-uniswap-v3&chain_id=56&protocol=uniswap_v3",
        "/api/dex/overview?market_id=dex-arbitrage-4663-uniswap-v3&chain_id=56&protocols=uniswap_v3",
    )
    for request_path in paths:
        response = client.get(request_path)
        assert response.status_code == 400, request_path
        payload = response.get_json()
        assert payload == {
            "code": "chain_mismatch",
            "error": "market_id dex-arbitrage-4663-uniswap-v3 belongs to chain_id 4663, not 56",
            "ok": False,
        }


def test_unknown_market_error_shape_is_uniform_across_scope_required_api_families():
    client = app.test_client()
    paths = (
        "/api/binance-market/state?market_id=unknown-market&chain_id=56",
        "/api/binance-market/states?market_id=unknown-market&chain_id=56",
        "/api/dex-pools/candidates?market_id=unknown-market&chain_id=56&protocols=uniswap_v3",
        "/api/binance-velocity/candidates?market_id=unknown-market&chain_id=56&protocol=uniswap_v3",
        "/api/flash-loan/pools?market_id=unknown-market&chain_id=56&protocol=uniswap_v3",
        "/api/dex/overview?market_id=unknown-market&chain_id=56",
    )
    for request_path in paths:
        response = client.get(request_path)
        assert response.status_code == 400, request_path
        payload = response.get_json()
        assert payload == {
            "code": "unknown_market",
            "error": "unknown market_id: unknown-market",
            "ok": False,
        }


def test_scope_mismatch_error_shape_is_uniform_across_scope_required_api_families():
    client = app.test_client()
    paths = (
        "/api/binance-market/state?market_id=dex-arbitrage-56-pancakeswap-v3&chain_id=56&protocol=unicorn",
        "/api/dex-pools/candidates?market_id=dex-arbitrage-56-pancakeswap-v3&chain_id=56&protocols=unicorn",
        "/api/binance-velocity/candidates?market_id=dex-arbitrage-56-pancakeswap-v3&chain_id=56&protocol=unicorn",
        "/api/flash-loan/pools?market_id=dex-arbitrage-56-pancakeswap-v3&chain_id=56&protocol=unicorn",
        "/api/dex/overview?market_id=dex-arbitrage-56-pancakeswap-v3&chain_id=56&protocols=unicorn",
    )
    for request_path in paths:
        response = client.get(request_path)
        assert response.status_code == 400, request_path
        payload = response.get_json()
        assert payload["ok"] is False, request_path
        assert payload["code"] == "scope_mismatch", request_path
        assert "belongs to protocol_id pancakeswap_v3" in payload["error"], request_path


def test_liquidation_health_has_structured_unknown_market_error():
    client = app.test_client()
    response = client.get(
        "/api/liquidation/health?market_id=liquidation-4663-venus-core&chain_id=56&protocol=venus_core"
    )

    assert response.status_code == 400, response.get_data(as_text=True)
    payload = response.get_json()
    assert payload["ok"] is False
    assert payload["code"] == "unknown_market"
    assert payload["error"] == "unknown market_id: liquidation-4663-venus-core"


def test_metrics_summary_route_reads_configured_jsonl_sink(tmp_path, monkeypatch):
    sink_path = tmp_path / "execution.jsonl"
    sink_path.write_text(
        json.dumps(
            {
                "kind": "quote",
                "name": "quote_latency",
                "scope": {"chain_id": 56},
                "observed_at": "2026-09-07T00:00:00Z",
                "duration_seconds": 0.2,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("EXECUTION_METRICS_PATH", str(sink_path))
    client = app.test_client()

    response = client.get("/api/metrics/summary")

    assert response.status_code == 200, response.get_data(as_text=True)
    payload = response.get_json()
    assert payload["ok"] is True
    assert payload["event_count"] == 1
    assert payload["latency_percentiles"]["50"] == 0.2


def test_metrics_summary_route_reports_alert_threshold_breaches(tmp_path, monkeypatch):
    sink_path = tmp_path / "execution.jsonl"
    sink_path.write_text(
        json.dumps(
            {
                "kind": "rpc",
                "name": "rpc_latency",
                "scope": {"chain_id": 56},
                "observed_at": "2026-09-07T00:00:00Z",
                "duration_seconds": 0.8,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("EXECUTION_METRICS_PATH", str(sink_path))
    client = app.test_client()

    response = client.get("/api/metrics/summary?alert_p95_seconds=0.5")

    assert response.status_code == 200, response.get_data(as_text=True)
    payload = response.get_json()
    assert payload["alert"]["ok"] is False
    assert payload["alert"]["violations"][0]["metric"] == "p95"


def test_metrics_deliver_alert_route_writes_local_jsonl_delivery(tmp_path, monkeypatch):
    sink_path = tmp_path / "execution.jsonl"
    delivery_path = tmp_path / "alert_delivery.jsonl"
    sink_path.write_text(
        json.dumps(
            {
                "kind": "rpc",
                "name": "rpc_latency",
                "scope": {"chain_id": 56},
                "observed_at": "2026-09-07T00:00:00Z",
                "duration_seconds": 1.2,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("EXECUTION_METRICS_PATH", str(sink_path))
    client = app.test_client()

    response = client.post(
        "/api/metrics/deliver-alert",
        json={
            "p95_seconds": 0.5,
            "delivery_path": str(delivery_path),
        },
    )

    assert response.status_code == 200, response.get_data(as_text=True)
    payload = response.get_json()
    assert payload["alert"]["ok"] is False
    assert payload["alert"]["violations"][0]["metric"] == "p95"
    assert payload["delivery"]["ok"] is False
    assert payload["delivery"]["delivery"] == "local_jsonl"
    assert delivery_path.exists()
