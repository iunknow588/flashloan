import pytest

from core.route_context import RouteContext


def test_route_context_keeps_panel_binding_and_supports_named_provider():
    context = RouteContext()
    panel = type("Panel", (), {"value": 7})()
    provider = object()

    context.bind(panel)
    context.bind_provider("dex_surface", provider)

    assert context.get("value") == 7
    assert context.provider("dex_surface") is provider


def test_route_context_rejects_missing_provider_before_request_execution():
    context = RouteContext()

    with pytest.raises(RuntimeError, match="route provider is not bound"):
        context.provider("dex_surface")

    with pytest.raises(ValueError, match="provider name is required"):
        context.bind_provider("", object())

    with pytest.raises(ValueError, match="cannot be None"):
        context.bind_provider("dex_surface", None)


def test_route_context_can_clear_provider_without_affecting_panel():
    context = RouteContext()
    context.bind_provider("dex_surface", object())
    context.clear_provider("dex_surface")

    with pytest.raises(RuntimeError):
        context.provider("dex_surface")


def test_route_context_provider_snapshot_isolated_from_registry():
    context = RouteContext()
    provider = object()
    context.bind_provider("dex_surface", provider)

    snapshot = context.providers()
    snapshot.clear()

    assert context.provider("dex_surface") is provider


def test_route_context_panel_provider_can_replace_panel_dispatch():
    context = RouteContext()
    panel = type("Panel", (), {"value": lambda self, x: x + 1})()
    context.bind(panel)
    context.bind_panel_provider()

    assert context.panel_call("value", 4) == 5

    context.bind_provider("panel", lambda name, *args, **kwargs: (name, args, kwargs))
    assert context.panel_call("value", 4, mode="test") == ("value", (4,), {"mode": "test"})
