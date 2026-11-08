"""Read-only contract checks for the Rust-owned BNB market store API."""

from __future__ import annotations

from web.control_panel import app


def test_bnb_market_rust_store_route_returns_sqlite_rust_contract():
    client = app.test_client()

    response = client.get("/api/binance-market/bnb-rust-store")

    assert response.status_code == 200, response.get_data(as_text=True)
    assert response.is_json
    payload = response.get_json()
    assert payload["ok"] is True
    assert payload["source"] == "sqlite_rust"
    assert payload["tables"] == ["bnb_market_observations", "bnb_market_snapshots"]
    assert isinstance(payload["snapshots"], list)
    assert isinstance(payload["observations"], list)
    assert isinstance(payload["observationSourceCounts"], dict)
    assert "collector" in payload
    assert "collectorError" in payload


def test_bnb_market_rust_store_route_respects_limits():
    client = app.test_client()

    response = client.get(
        "/api/binance-market/bnb-rust-store?snapshot_limit=3&observation_limit=4"
    )

    assert response.status_code == 200, response.get_data(as_text=True)
    payload = response.get_json()
    assert payload["snapshotLimit"] == 3
    assert payload["observationLimit"] == 4
    assert len(payload["snapshots"]) <= 3
    assert len(payload["observations"]) <= 4

