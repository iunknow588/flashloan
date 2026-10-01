from __future__ import annotations

import os
import re
from typing import Any


_TRUE_VALUES = {"1", "true", "yes", "on"}


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    return raw.lower() in _TRUE_VALUES


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return max(0, int(raw))
    except (TypeError, ValueError):
        return default


class QuoteLiveTestPolicy:
    """Resolves live quote-route review parameters for the quote-routes surface.

    Explicit call arguments win over environment variables, which win over
    built-in defaults. Passing ``anchor`` only constrains live/returned routes
    to that anchor-symbol closed loop; the underlying observer still discovers
    every candidate.
    """

    def __init__(
        self,
        *,
        anchor: str | None = None,
        live_route_limit: int | None = None,
        force_live_refresh: bool | None = None,
        probe_all_testnet_routes: bool | None = None,
    ) -> None:
        explicit_anchor = str(anchor or "").strip().upper() if anchor else ""
        env_anchor = str(os.getenv("DEX_QUOTE_ROUTE_ANCHOR_FILTER", "")).strip().upper()
        raw_anchor = explicit_anchor or env_anchor or ""
        self.anchors = {
            part.strip()
            for part in re.split(r"[,\s]+", raw_anchor)
            if part.strip()
        }
        self.anchor = ", ".join(sorted(self.anchors)) or None
        self.live_route_limit = (
            max(0, int(live_route_limit))
            if live_route_limit is not None
            else _env_int("DEX_QUOTE_ROUTE_LIVE_ROUTE_LIMIT", 0)
        )
        self.force_live_refresh = (
            bool(force_live_refresh)
            if force_live_refresh is not None
            else _env_bool("DEX_QUOTE_ROUTE_FORCE_REFRESH")
        )
        self.probe_all_testnet_routes = (
            bool(probe_all_testnet_routes)
            if probe_all_testnet_routes is not None
            else _env_bool("DEX_QUOTE_ROUTE_PROBE_ALL_TESTNET_ROUTES")
        )

    def matches_anchor(self, path: dict[str, Any]) -> bool:
        if not self.anchor:
            return True
        raw_route_symbols = path.get("routeSymbols") or []
        overview_route = path.get("overviewRoute") if isinstance(path.get("overviewRoute"), dict) else {}
        if not raw_route_symbols:
            raw_route_symbols = overview_route.get("pathSymbols") or []
        route_symbols = [
            str(symbol or "").strip().upper()
            for symbol in raw_route_symbols
            if str(symbol or "").strip()
        ]
        anchor_symbol = str(
            overview_route.get("anchorBorrowSymbol")
            or (route_symbols[0] if route_symbols else "")
        ).strip().upper()
        return bool(anchor_symbol and anchor_symbol in self.anchors)


__all__ = ["QuoteLiveTestPolicy"]
