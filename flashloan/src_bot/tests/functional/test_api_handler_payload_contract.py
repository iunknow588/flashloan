"""Smoke-contract checks for real, read-only Flask API handlers."""

from __future__ import annotations

from web.control_panel import app
from core.api_scope_policy import validate_named_response_contract


READ_ONLY_HANDLER_SAMPLES = (
    ("/api/system-info", None),
    ("/api/workflow-context?page_path=/dex/overview", None),
    ("/api/chain-selection", None),
    ("/api/markets", None),
    ("/api/flash-loan/pools?chain_id=56", None),
    ("/api/flash-loan/pools?chain_id=46630&protocols=uniswap_v4", None),
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
