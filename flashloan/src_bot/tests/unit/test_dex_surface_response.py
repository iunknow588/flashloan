import pytest

from core.chain_flow_dispatcher import ChainFlowDispatchError
from web.data_routes import dex_surface_response as response


def test_chain_flow_protocol_only_selects_single_protocol():
    assert response.chain_flow_protocol(["uniswap_v3"]) == "uniswap_v3"
    assert response.chain_flow_protocol(["uniswap_v3", "curve"]) == ""


def test_dex_surface_payload_for_chain_uses_fallback_when_flow_is_missing(monkeypatch):
    monkeypatch.setattr(
        response,
        "dispatch_chain_flow",
        lambda **kwargs: (_ for _ in ()).throw(
            ChainFlowDispatchError("chain_flow_missing", "missing flow")
        ),
    )

    payload = response.dex_surface_payload_for_chain(
        flow_name="dex_overview",
        mode="dex_overview",
        chain_id=56,
        protocols=["uniswap_v3"],
        fallback=lambda: {"ok": True},
    )

    assert payload == {"ok": True, "mode": "dex_overview"}


def test_dex_surface_payload_for_chain_returns_structured_dispatch_error(monkeypatch):
    monkeypatch.setattr(
        response,
        "dispatch_chain_flow",
        lambda **kwargs: (_ for _ in ()).throw(
            ChainFlowDispatchError("chain_flow_invalid", "invalid flow")
        ),
    )

    payload = response.dex_surface_payload_for_chain(
        flow_name="dex_overview",
        mode="dex_overview",
        chain_id=56,
        protocols=["uniswap_v3"],
        fallback=lambda: {"ok": True},
    )

    assert payload == {
        "ok": False,
        "mode": "dex_overview",
        "chain_id": 56,
        "selected_protocols": ["uniswap_v3"],
        "code": "chain_flow_invalid",
        "error": "invalid flow",
    }


def test_dex_surface_payload_for_chain_keeps_unscoped_fallback_mode():
    payload = response.dex_surface_payload_for_chain(
        flow_name="dex_overview",
        mode="dex_overview",
        chain_id=None,
        protocols=[],
        fallback=lambda: {"items": []},
    )

    assert payload == {"items": [], "mode": "dex_overview"}


def test_attach_dex_strategy_adds_route_mode_inside_request_context():
    from flask import Flask

    app = Flask(__name__)
    with app.test_request_context("/api/dex/overview"):
        payload = response.attach_dex_strategy({"ok": True})

    assert payload["mode"] == "dex_overview"
    assert "dex_strategy" in payload
