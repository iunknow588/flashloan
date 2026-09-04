from __future__ import annotations

from urllib.parse import urlsplit

from flask import request

from core.chain_data_registry import default_flash_loan_pool_scope, default_market_id_for_feature
from core.chain_route_selector import (
    build_chain_selection_payload,
    feature_from_page_path,
)
from core.chain_selection import (
    ChainSelection,
    ChainSelectionError,
    available_chain_selections,
    resolve_chain_selection,
)
from core.contracts import MarketScope
from web.chain_selector_component import (
    render_chain_selector_bootstrap,
    render_chain_selector_component,
)


def _request_chain_id() -> int | None:
    raw = request.args.get("chain_id", "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        raise ChainSelectionError("invalid_chain_id", f"invalid chain_id: {raw}") from None


def _request_bool(name: str, *, default: bool = True) -> bool:
    raw = request.args.get(name)
    if raw is None:
        return default
    return str(raw).strip().lower() not in {"0", "false", "no", "off"}


def _request_protocol_id() -> str | None:
    raw = request.args.get("protocol_id", "").strip()
    if raw:
        return raw
    protocols = request.args.get("protocols", "").replace("|", ",")
    for item in protocols.split(","):
        protocol = item.strip()
        if protocol:
            return protocol
    return None


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
        protocol_id=_request_protocol_id(),
        database_url=database_url,
        default_market_id=default_market_id,
        selections=selections,
    )


def chain_selection_payload(
    *,
    database_url: str | None = None,
    feature: str | None = None,
    default_market_id: str | None = None,
) -> dict:
    page_path = request.args.get("page_path", "").strip()
    requested_feature = (
        feature
        or request.args.get("feature", "").strip()
        or feature_from_page_path(page_path)
        or None
    )
    page_pathname = urlsplit(page_path).path.rstrip("/") or "/"
    if default_market_id is None and page_pathname in {"/flash-loan", "/flash-loan/pools"}:
        default_market_id = str(default_flash_loan_pool_scope()["market_id"])
    if default_market_id is None and page_pathname in {"/", "/bot"}:
        default_market_id = default_market_id_for_feature("liquidation")
    available = available_chain_selections(database_url=database_url, feature=requested_feature)
    selected = resolve_request_chain_selection(
        database_url=database_url,
        feature=requested_feature,
        default_market_id=default_market_id,
        selections=available,
    )
    requested_protocols = [
        item.strip().lower()
        for item in request.args.get("protocols", "").split(",")
        if item.strip()
    ]
    return build_chain_selection_payload(
        selected=selected,
        available=available,
        feature=requested_feature,
        requested_protocols=requested_protocols,
        workflow=request.args.get("workflow", "").strip() or "unknown",
        execution_mode=request.args.get("execution_mode", "").strip() or None,
        scope_type=request.args.get("scope", "").strip() or "single_chain",
        include_chain_config=_request_bool("include_chain_config", default=True),
    )


def request_market_scope(
    *,
    expected_feature: str | None = None,
    database_url: str | None = None,
    default_market_id: str | None = None,
    workflow: str = "unknown",
    execution_mode: str | None = None,
    scope_type: str | None = None,
) -> MarketScope:
    selection = resolve_request_chain_selection(
        database_url=database_url,
        feature=expected_feature,
        default_market_id=default_market_id,
    )
    return MarketScope.from_selection(
        selection,
        workflow=workflow,
        execution_mode=execution_mode or request.args.get("execution_mode", "").strip() or None,
        scope_type=scope_type or request.args.get("scope", "").strip() or "single_chain",
    )


def inject_chain_selector_markup(
    html: str,
    *,
    feature: str | None = None,
    default_market_id: str | None = None,
    default_chain_id: int | None = None,
) -> str:
    """Add the shared selector shell to legacy static pages."""
    if "data-chain-selector" in html:
        return html
    shell = render_chain_selector_component(variant="legacy")
    bootstrap = render_chain_selector_bootstrap(
        feature=feature,
        default_market_id=default_market_id,
        default_chain_id=default_chain_id,
    )
    if "</header>" in html:
        html = html.replace("</header>", shell + "</header>", 1)
    elif "</body>" in html:
        html = html.replace("</body>", shell + "</body>", 1)
    if "</body>" in html:
        html = html.replace("</body>", bootstrap + "</body>", 1)
    return html
