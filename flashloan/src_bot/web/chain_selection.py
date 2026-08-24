from __future__ import annotations

from typing import Any
from urllib.parse import urlencode
from urllib.parse import urlsplit

from flask import request

from core.contracts import MarketScope
from core.chain_selection import (
    ChainSelection,
    ChainSelectionError,
    available_chain_selections,
    resolve_chain_selection,
)
from core.market_config import market_id_for_scope
from web.chain_selector_component import (
    render_chain_selector_bootstrap,
    render_chain_selector_component,
)


CHAIN_SELECTOR_ORDER = {
    "bnb": 0,
    "avalanche": 1,
    "polygon": 2,
    "robinhood": 3,
    "ethereum": 4,
    "hyperliquid": 5,
    "cow": 6,
}
ROUTE_ONLY_PROTOCOLS = {"hyperliquid_native"}


def _selection_chain_key(selection: ChainSelection) -> str:
    if selection.chain_id is not None:
        return f"chain:{int(selection.chain_id)}"
    network = str(selection.network or "").strip().lower()
    if network:
        return f"network:{network}"
    return f"market:{selection.market_id}"


def _chain_order_key(selection: ChainSelection, fallback_index: int) -> tuple[int, int, str]:
    market_id = str(selection.market_id or "").strip().lower()
    network = str(selection.network or "").strip().lower()
    market_type = str(selection.market_type or "").strip().lower()
    if market_type == "execution_rail" or market_id == "cow-dex-arbitrage":
        network = "cow"
    elif market_id == "hyperliquid-native":
        network = "hyperliquid"
    order = CHAIN_SELECTOR_ORDER.get(network)
    if order is not None:
        return (0, order, network)
    return (1, fallback_index, network or market_id or _selection_chain_key(selection))


def _chain_display_name(selection: ChainSelection) -> str:
    network = str(selection.network or "").strip().replace("_", " ")
    if selection.market_type == "execution_rail":
        return "CoW Rail"
    if selection.market_type == "shadow_chain":
        return "Robinhood Chain"
    if network:
        return f"{network.upper()} Chain"
    return selection.display_name or selection.market_id or "Chain"


def _protocol_display_name(protocol: str) -> str:
    text = str(protocol or "").strip().replace("_", " ")
    return text.title() if text else "Protocol"


def _is_route_only_protocol(protocol: str) -> bool:
    return str(protocol or "").strip().lower() in ROUTE_ONLY_PROTOCOLS


def _feature_from_page_path(path: str | None) -> str | None:
    raw_path = str(path or "").strip()
    if not raw_path:
        return None
    pathname = urlsplit(raw_path).path.rstrip("/") or "/"
    if pathname == "/" or pathname.startswith("/bot") or pathname in {
        "/liquidation",
        "/account-scan",
        "/execution",
        "/audit",
    }:
        return "liquidation"
    if pathname.startswith("/dex") or pathname.startswith("/robinhood-dex") or pathname in {
        "/binance-market",
        "/dex-arbitrage",
        "/dex-pools",
        "/candidate-pools",
        "/market-observation",
    }:
        return "dex_arbitrage"
    return None


def _chain_scope_options(selections: list[ChainSelection], feature: str | None = None) -> list[dict[str, Any]]:
    groups: dict[str, list[ChainSelection]] = {}
    group_indexes: dict[str, int] = {}
    for selection in selections:
        key = _selection_chain_key(selection)
        group_indexes.setdefault(key, len(group_indexes))
        groups.setdefault(key, []).append(selection)

    options: list[dict[str, Any]] = []
    ordered_groups = sorted(
        groups.items(),
        key=lambda item: _chain_order_key(item[1][0], group_indexes[item[0]]),
    )
    for _key, group in ordered_groups:
        if not group:
            continue
        chain_id = group[0].chain_id
        preferred_market_id = market_id_for_scope(chain_id, feature) if chain_id is not None else None
        selected = None
        if preferred_market_id:
            selected = next((item for item in group if item.market_id == preferred_market_id), None)
        if selected is None:
            selected = next((item for item in group if item.is_default), group[0])
        chain_key = _selection_chain_key(selected)
        representative_market_id = selected.market_id
        protocol_groups: dict[str, list[ChainSelection]] = {}
        for item in group:
            if _is_route_only_protocol(item.protocol):
                continue
            protocol_groups.setdefault(item.protocol, []).append(item)
        protocols = sorted(protocol_groups.keys())
        protocol_options = []
        for protocol in protocols:
            protocol_group = protocol_groups[protocol]
            protocol_selected = next((item for item in protocol_group if item.is_default), protocol_group[0])
            protocol_options.append(
              {
                  "protocol": protocol,
                  "display_name": _protocol_display_name(protocol),
                  "market_ids": [item.market_id for item in protocol_group],
                  "market_count": len(protocol_group),
                  "is_default": bool(protocol_selected.is_default),
                  "market_id": protocol_selected.market_id,
              }
          )
        payload = dict(selected.as_dict())
        payload.update(
            {
                "chain_key": chain_key,
                "default_market_id": representative_market_id,
                "representative_market_id": representative_market_id,
                "display_name": _chain_display_name(selected),
                "protocol": selected.protocol,
                "protocol_summary": ", ".join(protocols) if protocols else "链路",
                "protocols": protocols,
                "protocol_options": protocol_options,
                "market_ids": [item.market_id for item in group],
                "market_count": len(group),
                "chain_scope": True,
            }
        )
        options.append(payload)
    return options


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
    requested_feature = (
        feature
        or request.args.get("feature", "").strip()
        or _feature_from_page_path(request.args.get("page_path", "").strip())
        or None
    )
    available = available_chain_selections(database_url=database_url, feature=requested_feature)
    selected = resolve_request_chain_selection(
        database_url=database_url,
        feature=requested_feature,
        default_market_id=default_market_id,
        selections=available,
    )
    chain_options = _chain_scope_options(available, requested_feature)
    selected_chain = next(
        (item for item in chain_options if item.get("chain_key") == _selection_chain_key(selected)),
        None,
    )
    protocol_options = list((selected_chain or {}).get("protocol_options") or [])
    requested_protocols = [
        item.strip().lower()
        for item in request.args.get("protocols", "").split(",")
        if item.strip()
    ]
    available_protocols = [
        str(item.get("protocol") or "").strip().lower()
        for item in protocol_options
        if str(item.get("protocol") or "").strip()
    ]
    selected_protocols = [protocol for protocol in requested_protocols if protocol in available_protocols]
    if not selected_protocols:
        selected_protocols = list(available_protocols)
    query_args = {"market_id": selected.market_id}
    if selected.chain_id is not None:
        query_args["chain_id"] = selected.chain_id
    if selected.feature:
        query_args["feature"] = selected.feature
    if selected_protocols:
        query_args["protocols"] = ",".join(selected_protocols)
    selector_query_args: dict[str, Any] = {}
    if selected.chain_id is not None:
        selector_query_args["chain_id"] = selected.chain_id
    elif selected.market_id:
        selector_query_args["market_id"] = selected.market_id
    if selected.feature:
        selector_query_args["feature"] = selected.feature
    if selected_protocols:
        selector_query_args["protocols"] = ",".join(selected_protocols)
    selected_chain_key = (selected_chain or {}).get("chain_key") or _selection_chain_key(selected)
    representative_market_id = str(
        (selected_chain or {}).get("representative_market_id")
        or (selected_chain or {}).get("default_market_id")
        or selected.market_id
    )
    return {
        "ok": True,
        "selected": {
            **selected.as_dict(),
            "chain_key": selected_chain_key,
            "default_market_id": representative_market_id,
            "representative_market_id": representative_market_id,
            "protocol_options": protocol_options,
            "selected_protocols": selected_protocols,
            "protocol_summary": (selected_chain or {}).get("protocol_summary")
            or ", ".join(available_protocols)
            or selected.protocol,
            "protocol_count": len(protocol_options),
        },
        "market_scope": MarketScope.from_selection(
            selected,
            workflow=request.args.get("workflow", "").strip() or "unknown",
            execution_mode=request.args.get("execution_mode", "").strip() or None,
            scope_type=request.args.get("scope", "").strip() or "single_chain",
        ).as_dict(),
        "available": [item.as_dict() for item in available],
        "chain_options": chain_options,
        "protocol_options": protocol_options,
        "selected_protocols": selected_protocols,
        "query": query_args,
        "query_string": urlencode(query_args),
        "selector_query": selector_query_args,
        "selector_query_string": urlencode(selector_query_args),
    }


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
    has_shared_shell = "data-chain-selector" in html
    if has_shared_shell:
        return html
    feature_value = str(feature or "").strip().lower()
    default_market_value = str(default_market_id or "").strip()
    shell = (
        '<div class="chain-selector-shell" data-chain-selector>'
        '<label>当前链路</label>'
        '<span data-chain-selection-meta>加载中</span>'
        '<div data-chain-protocols="true"></div>'
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
        f'defaultMarketId: "{default_market_value}"'
        '})'
        '.catch(function(error){console.error("chain selector:", error);});</script>'
    )
    if "</header>" in html:
        html = html.replace("</header>", shell + "</header>", 1)
    elif "</body>" in html:
        html = html.replace("</body>", shell + "</body>", 1)
    if "/static/chain-selector.js" not in html:
        if "</head>" in html:
            html = html.replace("</head>", script_src + "</head>", 1)
        elif "</body>" in html:
            html = html.replace("</body>", script_src + "</body>", 1)
    if "</body>" in html and "ChainSelector.init" not in html:
        html = html.replace("</body>", init_script + "</body>", 1)
    return html
