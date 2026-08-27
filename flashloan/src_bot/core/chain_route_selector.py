from __future__ import annotations

from typing import Any, Iterable
from urllib.parse import urlencode, urlsplit

from core.chain_data_registry import chain_config_summary, global_chain_id
from core.chain_selection import ChainSelection
from core.contracts import MarketScope
from core.market_config import market_id_for_scope


CHAIN_SELECTOR_ORDER = {
    "avalanche": 0,
    "bnb": 1,
    "polygon": 2,
    "ethereum": 3,
    "robinhood": 4,
    "robinhood-testnet": 5,
    "hyperliquid": 6,
    "cow": 7,
}
ROUTE_ONLY_PROTOCOLS = {"hyperliquid_native"}


def feature_from_page_path(path: str | None) -> str | None:
    raw_path = str(path or "").strip()
    if not raw_path:
        return None
    pathname = urlsplit(raw_path).path.rstrip("/") or "/"
    if pathname in {"/flash-loan", "/flash-loan/pools"}:
        return "dex_arbitrage"
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


def selection_chain_key(selection: ChainSelection) -> str:
    if selection.global_chain_id:
        return selection.global_chain_id
    if selection.chain_id is not None:
        return global_chain_id(chain_id=int(selection.chain_id), network=selection.network)
    network = str(selection.network or "").strip().lower()
    if network:
        return global_chain_id(network=network)
    return global_chain_id(market_id=selection.market_id)


def chain_display_name(selection: ChainSelection) -> str:
    network = str(selection.network or "").strip().replace("_", " ")
    if selection.market_type == "execution_rail":
        return "CoW Rail"
    if selection.network == "robinhood-testnet":
        return "Robinhood Testnet"
    if selection.market_type == "shadow_chain":
        return "Robinhood Chain"
    if network:
        return f"{network.upper()} Chain"
    return selection.display_name or selection.market_id or "Chain"


def protocol_display_name(protocol: str) -> str:
    text = str(protocol or "").strip().replace("_", " ")
    return text.title() if text else "Protocol"


def is_route_only_protocol(protocol: str) -> bool:
    return str(protocol or "").strip().lower() in ROUTE_ONLY_PROTOCOLS


def chain_config_for_selection(selection: ChainSelection) -> dict[str, Any]:
    try:
        if selection.chain_id is not None:
            return chain_config_summary(chain_id=int(selection.chain_id))
        return chain_config_summary(selection.network)
    except Exception:
        return {}


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
    return (1, fallback_index, network or market_id or selection_chain_key(selection))


def chain_scope_options(selections: Iterable[ChainSelection], feature: str | None = None) -> list[dict[str, Any]]:
    groups: dict[str, list[ChainSelection]] = {}
    group_indexes: dict[str, int] = {}
    for selection in selections:
        key = selection_chain_key(selection)
        group_indexes.setdefault(key, len(group_indexes))
        groups.setdefault(key, []).append(selection)

    options: list[dict[str, Any]] = []
    ordered_groups = sorted(
        groups.items(),
        key=lambda item: _chain_order_key(item[1][0], group_indexes[item[0]]),
    )
    for _key, group in ordered_groups:
        chain_id = group[0].chain_id
        preferred_market_id = market_id_for_scope(chain_id, feature) if chain_id is not None else None
        selected = next((item for item in group if item.market_id == preferred_market_id), None) if preferred_market_id else None
        if selected is None:
            selected = next((item for item in group if item.is_default), group[0])
        protocol_groups: dict[str, list[ChainSelection]] = {}
        for item in group:
            if not is_route_only_protocol(item.protocol):
                protocol_groups.setdefault(item.protocol, []).append(item)
        protocols = sorted(protocol_groups)
        protocol_options = []
        for protocol in protocols:
            protocol_group = protocol_groups[protocol]
            protocol_selected = next((item for item in protocol_group if item.is_default), protocol_group[0])
            protocol_options.append(
                {
                    "protocol": protocol,
                    "display_name": protocol_display_name(protocol),
                    "market_ids": [item.market_id for item in protocol_group],
                    "market_count": len(protocol_group),
                    "is_default": bool(protocol_selected.is_default),
                    "market_id": protocol_selected.market_id,
                }
            )
        chain_key = selection_chain_key(selected)
        chain_config = chain_config_for_selection(selected)
        payload = dict(selected.as_dict())
        payload.update(
            {
                "chain_key": chain_key,
                "global_chain_id": chain_key,
                "default_market_id": selected.market_id,
                "representative_market_id": selected.market_id,
                "display_name": chain_display_name(selected),
                "protocol": selected.protocol,
                "protocol_summary": ", ".join(protocols) if protocols else "链路",
                "protocols": protocols,
                "protocol_options": protocol_options,
                "chain_config": chain_config,
                "market_ids": [item.market_id for item in group],
                "market_count": len(group),
                "chain_scope": True,
            }
        )
        options.append(payload)
    return options


def build_chain_selection_payload(
    *,
    selected: ChainSelection,
    available: Iterable[ChainSelection],
    feature: str | None = None,
    requested_protocols: Iterable[str] | None = None,
    workflow: str = "unknown",
    execution_mode: str | None = None,
    scope_type: str = "single_chain",
) -> dict[str, Any]:
    available_rows = list(available)
    chain_options = chain_scope_options(available_rows, feature)
    selected_chain = next(
        (item for item in chain_options if item.get("chain_key") == selection_chain_key(selected)),
        None,
    )
    protocol_options = list((selected_chain or {}).get("protocol_options") or [])
    requested = [str(item).strip().lower() for item in (requested_protocols or []) if str(item).strip()]
    available_protocols = [
        str(item.get("protocol") or "").strip().lower()
        for item in protocol_options
        if str(item.get("protocol") or "").strip()
    ]
    selected_protocols = [protocol for protocol in requested if protocol in available_protocols] or list(available_protocols)
    query_args: dict[str, Any] = {"market_id": selected.market_id}
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
    selected_chain_key = (selected_chain or {}).get("chain_key") or selection_chain_key(selected)
    representative_market_id = str(
        (selected_chain or {}).get("representative_market_id")
        or (selected_chain or {}).get("default_market_id")
        or selected.market_id
    )
    selected_chain_config = (selected_chain or {}).get("chain_config") or chain_config_for_selection(selected)
    return {
        "ok": True,
        "selected": {
            **selected.as_dict(),
            "global_chain_id": selected_chain_key,
            "chain_key": selected_chain_key,
            "default_market_id": representative_market_id,
            "representative_market_id": representative_market_id,
            "protocol_options": protocol_options,
            "selected_protocols": selected_protocols,
            "chain_config": selected_chain_config,
            "protocol_summary": (selected_chain or {}).get("protocol_summary")
            or ", ".join(available_protocols)
            or selected.protocol,
            "protocol_count": len(protocol_options),
        },
        "market_scope": MarketScope.from_selection(
            selected,
            workflow=workflow,
            execution_mode=execution_mode,
            scope_type=scope_type,
        ).as_dict(),
        "available": [item.as_dict() for item in available_rows],
        "chain_options": chain_options,
        "protocol_options": protocol_options,
        "selected_protocols": selected_protocols,
        "query": query_args,
        "query_string": urlencode(query_args),
        "selector_query": selector_query_args,
        "selector_query_string": urlencode(selector_query_args),
        "selected_chain_config": selected_chain_config,
    }
