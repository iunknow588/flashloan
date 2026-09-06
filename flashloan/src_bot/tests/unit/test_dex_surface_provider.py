import pytest
from core.route_context import RouteContext
from core.dex_surface_provider import DexSurfaceProvider, bind_provider, provider_from_module


def test_provider_delegates_each_surface_builder():
    provider = DexSurfaceProvider(
        overview=lambda **kwargs: {"kind": "overview", **kwargs},
        observation=lambda **kwargs: {"kind": "observation", **kwargs},
        quote_routes=lambda **kwargs: {"kind": "quotes", **kwargs},
        pool_candidates=lambda **kwargs: {"kind": "pools", **kwargs},
        submission_review=lambda **kwargs: {"kind": "submissions", **kwargs},
    )

    assert provider.build_overview(chain_id=56)["kind"] == "overview"
    assert provider.build_observation(chain_id=56)["kind"] == "observation"
    assert provider.build_quote_routes(chain_id=56)["kind"] == "quotes"
    assert provider.build_pool_candidates(chain_id=56)["kind"] == "pools"
    assert provider.build_submission_review(chain_id=56)["kind"] == "submissions"


def test_provider_from_module_fails_early_for_missing_builder():
    class Incomplete:
        dex_overview_payload = staticmethod(lambda **kwargs: {})

    with pytest.raises(TypeError, match="dex_market_observation_payload"):
        provider_from_module(Incomplete)


def test_provider_from_module_accepts_explicit_service_module():
    class Complete:
        dex_overview_payload = staticmethod(lambda **kwargs: {})
        dex_market_observation_payload = staticmethod(lambda **kwargs: {})
        dex_quote_routes_payload = staticmethod(lambda **kwargs: {})
        dex_pool_candidates_payload = staticmethod(lambda **kwargs: {})
        dex_submission_review_payload = staticmethod(lambda **kwargs: {})

    provider = provider_from_module(Complete)
    assert isinstance(provider, DexSurfaceProvider)


def test_bind_provider_uses_explicit_context_without_flask_initialization():
    class Complete:
        dex_overview_payload = staticmethod(lambda **kwargs: {})
        dex_market_observation_payload = staticmethod(lambda **kwargs: {})
        dex_quote_routes_payload = staticmethod(lambda **kwargs: {})
        dex_pool_candidates_payload = staticmethod(lambda **kwargs: {})
        dex_submission_review_payload = staticmethod(lambda **kwargs: {})

    context = RouteContext()
    provider = bind_provider(context, Complete)

    assert context.provider("dex_surface") is provider


def test_provider_builder_for_resolves_service_and_unknown_name_fails():
    class Complete:
        dex_overview_payload = staticmethod(lambda **kwargs: {"kind": "overview"})
        dex_market_observation_payload = staticmethod(lambda **kwargs: {"kind": "observation"})
        dex_quote_routes_payload = staticmethod(lambda **kwargs: {"kind": "quotes"})
        dex_pool_candidates_payload = staticmethod(lambda **kwargs: {"kind": "pools"})
        dex_submission_review_payload = staticmethod(lambda **kwargs: {"kind": "submissions"})

    provider = provider_from_module(Complete)
    assert provider.builder_for("quote_routes")(chain_id=1)["kind"] == "quotes"
    with pytest.raises(KeyError):
        provider.builder_for("missing")
