from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

from flask import request

from core.contracts import MarketScope
from core.chain_selection import (
    ChainSelection,
    ChainSelectionError,
    available_chain_selections,
    resolve_chain_selection,
)


def _request_chain_id() -> int | None:
    raw = request.args.get("chain_id", "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        raise ChainSelectionError("invalid_chain_id", f"invalid chain_id: {raw}") from None


def resolve_request_chain_selection(
    *,
    database_url: str | None = None,
    feature: str | None = None,
    default_market_id: str | None = None,
    selections: list[ChainSelection] | None = None,
) -> ChainSelection:
    return resolve_chain_selection(
        market_id=request.args.get("market_id", "").strip() or None,
        chain_id=_request_chain_id(),
        feature=feature or request.args.get("feature", "").strip() or None,
        database_url=database_url,
        default_market_id=default_market_id,
        selections=selections,
    )


def chain_selection_payload(
    *,
    database_url: str | None = None,
    feature: str | None = None,
    default_market_id: str | None = None,
) -> dict[str, Any]:
    available = available_chain_selections(database_url=database_url, feature=feature)
    selected = resolve_request_chain_selection(
        database_url=database_url,
        feature=feature,
        default_market_id=default_market_id,
        selections=available,
    )
    query_args = {"market_id": selected.market_id}
    if selected.chain_id is not None:
        query_args["chain_id"] = selected.chain_id
    if selected.feature:
        query_args["feature"] = selected.feature
    return {
        "ok": True,
        "selected": selected.as_dict(),
        "market_scope": MarketScope.from_selection(
            selected,
            workflow=request.args.get("workflow", "").strip() or "unknown",
            execution_mode=request.args.get("execution_mode", "").strip() or None,
            scope_type=request.args.get("scope", "").strip() or "single_chain",
        ).as_dict(),
        "available": [item.as_dict() for item in available],
        "query": query_args,
        "query_string": urlencode(query_args),
    }


def inject_chain_selector_markup(
    html: str,
    *,
    feature: str | None = None,
    default_market_id: str | None = None,
    default_chain_id: int | None = None,
) -> str:
    """Add the shared selector shell to legacy static pages."""
    has_shared_shell = "data-chain-selector" in html
    has_legacy_liquidation_selector = "liquidationMarketSelect" in html
    if has_shared_shell:
        return html
    feature_value = str(feature or "").strip().lower()
    default_market_value = str(default_market_id or "").strip()
    feature_attr = f' data-chain-feature="{feature_value}"' if feature_value else ""
    shell = (
        f'<div class="chain-selector-shell" data-chain-selector{feature_attr}>'
        '<label>Chain market</label>'
        '<span data-chain-selection-meta>Loading</span>'
        "</div>"
    )
    default_chain_value = "" if default_chain_id is None else str(int(default_chain_id))
    script_src = (
        "<script>"
        f'window.__CHAIN_SELECTOR_DEFAULT__={{market_id:"{default_market_value}",chain_id:"{default_chain_value}"}};'
        "</script><script src=\"/static/chain-selector.js\"></script>"
    )
    init_script = (
        f'<script>window.ChainSelector && window.ChainSelector.init({{feature: "{feature_value}", '
        f'defaultMarketId: "{default_market_value}"}})'
        '.catch(function(error){console.error("chain selector:", error);});</script>'
    )
    if not has_legacy_liquidation_selector and "</header>" in html:
        html = html.replace("</header>", shell + "</header>", 1)
    elif not has_legacy_liquidation_selector and "</body>" in html:
        html = html.replace("</body>", shell + "</body>", 1)
    if "/static/chain-selector.js" not in html:
        if "</head>" in html:
            html = html.replace("</head>", script_src + "</head>", 1)
        elif "</body>" in html:
            html = html.replace("</body>", script_src + "</body>", 1)
    if "</body>" in html and "ChainSelector.init" not in html:
        html = html.replace("</body>", init_script + "</body>", 1)
    return html
