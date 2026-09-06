"""Injectable service boundary for DEX surface route dependencies.

The legacy route module still exposes its names for compatibility and tests.
This facade makes the dependency boundary explicit so callers can migrate one
service at a time without changing Flask route semantics.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


PayloadBuilder = Callable[..., dict[str, Any]]


@dataclass(frozen=True)
class DexSurfaceProvider:
    overview: PayloadBuilder
    observation: PayloadBuilder
    quote_routes: PayloadBuilder
    pool_candidates: PayloadBuilder
    submission_review: PayloadBuilder

    def build_overview(self, **kwargs: Any) -> dict[str, Any]:
        return self.overview(**kwargs)

    def build_observation(self, **kwargs: Any) -> dict[str, Any]:
        return self.observation(**kwargs)

    def build_quote_routes(self, **kwargs: Any) -> dict[str, Any]:
        return self.quote_routes(**kwargs)

    def build_pool_candidates(self, **kwargs: Any) -> dict[str, Any]:
        return self.pool_candidates(**kwargs)

    def build_submission_review(self, **kwargs: Any) -> dict[str, Any]:
        return self.submission_review(**kwargs)

    def builder_for(self, name: str) -> PayloadBuilder:
        key = str(name or "").strip()
        if key == "overview":
            return self.overview
        if key == "observation":
            return self.observation
        if key == "quote_routes":
            return self.quote_routes
        if key == "pool_candidates":
            return self.pool_candidates
        if key == "submission_review":
            return self.submission_review
        raise KeyError(f"unknown dex surface builder: {key}")


def provider_from_module(module: object) -> DexSurfaceProvider:
    """Build a provider from an existing service module.

    Attribute lookup is intentionally explicit: missing service functions fail
    at provider construction instead of much later inside a Flask request.
    """
    names = {
        "overview": "dex_overview_payload",
        "observation": "dex_market_observation_payload",
        "quote_routes": "dex_quote_routes_payload",
        "pool_candidates": "dex_pool_candidates_payload",
        "submission_review": "dex_submission_review_payload",
    }
    resolved: dict[str, PayloadBuilder] = {}
    for field, name in names.items():
        candidate = getattr(module, name, None)
        if not callable(candidate):
            raise TypeError(f"DEX surface provider requires callable {name}")
        resolved[field] = candidate
    return DexSurfaceProvider(**resolved)


def bind_provider(context: object, module: object, *, name: str = "dex_surface") -> DexSurfaceProvider:
    """Construct and bind a provider to a RouteContext-like object.

    Keeping binding as an explicit operation lets application assembly decide
    when dependencies become available, while tests can inject a provider
    without importing or initializing the Flask application.
    """
    provider = provider_from_module(module)
    bind = getattr(context, "bind_provider", None)
    if not callable(bind):
        raise TypeError("route context must provide bind_provider")
    bind(name, provider)
    return provider


__all__ = ["DexSurfaceProvider", "bind_provider", "provider_from_module"]
