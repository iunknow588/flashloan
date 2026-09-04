from __future__ import annotations

from typing import Any

from core.chain_data_registry import (
    chain_default_protocol_preferences,
    chain_supported_protocols,
)
from core.chain_flow_dispatcher import ChainFlowDispatchError, dispatch_chain_flow
from core.flash_loan_pools import pool_evidence_from_view, static_store_metadata, write_flash_loan_pool_static
from core.market_config import PROTOCOL_CAPABILITIES, supported_market_summaries
from intent_trade.flows.flash_loan_pools.presenter import (
    _empty_protocol_view,
    _is_route_only_protocol,
    _is_stable_symbol,
    _protocol_display_name,
    _route_only_protocol_view,
)


LENDING_PROTOCOLS = {"aave_v3", "hyper_evm_aave_v3", "venus_core", "morpho_blue"}
DEX_FLASH_PROTOCOLS = {
    "uniswap_v2",
    "uniswap_v3",
    "uniswap_v4",
    "pancakeswap_v2",
    "pancakeswap_v3",
    "biswap_v2",
    "quickswap_v2",
    "traderjoe_v2",
    "curve",
    "balancer_v2",
}
EXECUTION_RAIL_PROTOCOLS = {"cow_protocol", "hyperliquid_native"}
ROUTE_ONLY_PROTOCOLS = {"hyperliquid_native"}

def _chain_default_protocol_preferences() -> dict[int, list[str]]:
    try:
        return chain_default_protocol_preferences()
    except Exception:
        return {}


def _protocol_sort_key(protocol: str) -> tuple[int, str]:
    order = {
        "morpho_blue": 0,
        "venus_core": 1,
        "aave_v3": 2,
        "uniswap_v2": 3,
        "uniswap_v3": 4,
        "uniswap_v4": 5,
        "pancakeswap_v3": 6,
        "pancakeswap_v2": 7,
        "biswap_v2": 8,
        "quickswap_v2": 9,
        "traderjoe_v2": 10,
        "balancer_v2": 11,
        "curve": 12,
    }
    normalized = str(protocol or "").strip().lower()
    return (order.get(normalized, 1000), normalized)


def _chain_supported_protocol_set(chain_id: int | None) -> set[str]:
    if chain_id is None:
        return set()
    try:
        return set(chain_supported_protocols(chain_id=int(chain_id)))
    except Exception:
        return set()


def _provider_kind(protocol: str, market_type: str) -> str:
    normalized = str(protocol or "").strip().lower()
    if normalized in LENDING_PROTOCOLS:
        return "lending_pool"
    if normalized in DEX_FLASH_PROTOCOLS:
        return "dex_pool"
    if normalized in EXECUTION_RAIL_PROTOCOLS or str(market_type or "").strip().lower() == "execution_rail":
        return "execution_rail"
    return "unknown"


def _bot_consumable(row: dict[str, Any], provider_kind: str) -> bool:
    feature = str(row.get("feature") or "").strip().lower()
    return feature == "liquidation" and provider_kind in {"lending_pool", "execution_rail"}


def _dex_consumable(row: dict[str, Any], provider_kind: str) -> bool:
    feature = str(row.get("feature") or "").strip().lower()
    protocol = str(row.get("protocol") or "").strip().lower()
    if feature == "dex_arbitrage":
        return provider_kind in {"lending_pool", "dex_pool", "execution_rail"}
    return protocol in LENDING_PROTOCOLS


def _provider_from_market(row: dict[str, Any]) -> dict[str, Any]:
    protocol = str(row.get("protocol") or "").strip().lower()
    capabilities = PROTOCOL_CAPABILITIES.get(protocol, {})
    protocol_supported = bool(row.get("protocol_supported", capabilities.get("supported", False)))
    observation_only = bool(row.get("observation_only")) or not protocol_supported
    market_type = str(row.get("market_type") or "").strip().lower()
    provider_kind = _provider_kind(protocol, market_type)
    evidence = [
        "market_config",
        "protocol_capability",
    ]
    if row.get("migration_type"):
        evidence.append(str(row["migration_type"]))
    return {
        "market_id": row.get("market_id") or "",
        "feature": row.get("feature") or "",
        "display_name": row.get("display_name") or row.get("market_id") or "Unknown market",
        "network": row.get("network") or "",
        "chain_id": row.get("chain_id"),
        "native_symbol": row.get("native_symbol") or "",
        "market_type": market_type,
        "protocol": protocol,
        "protocol_display_name": _protocol_display_name(protocol),
        "provider_kind": provider_kind,
        "adapter": capabilities.get("adapter") or "ProtocolAdapter",
        "executor": capabilities.get("executor") or "Executor",
        "supported": protocol_supported,
        "observation_only": observation_only,
        "bot_consumable": _bot_consumable(row, provider_kind),
        "dex_consumable": _dex_consumable(row, provider_kind),
        "premium_bps": row.get("premium_bps"),
        "liquidity_status": row.get("liquidity_status") or "unknown",
        "pool_address": row.get("pool_address") or "",
        "provider_address": row.get("pool_address") or row.get("dex_router_address") or "",
        "notes": capabilities.get("notes") or "",
        "evidence": evidence,
    }


def _default_protocols_for_scope(
    *,
    chain_id: int | None,
    selected_market_row: dict[str, Any] | None,
    available_protocols: set[str],
) -> list[str]:
    effective_chain_id = chain_id
    if effective_chain_id is None and selected_market_row is not None:
        raw_chain_id = selected_market_row.get("chain_id")
        if raw_chain_id is not None:
            try:
                effective_chain_id = int(raw_chain_id)
            except (TypeError, ValueError):
                effective_chain_id = None
    preferred = _chain_default_protocol_preferences().get(
        effective_chain_id or -1,
        ["aave_v3", "venus_core", "uniswap_v3"],
    )
    for protocol in preferred:
        if protocol in available_protocols:
            ordered = [protocol]
            break
    else:
        ordered = []
    remaining = [
        protocol
        for protocol in sorted(available_protocols, key=_protocol_sort_key)
        if protocol not in ordered
    ]
    if ordered or remaining:
        return ordered + remaining
    return ["aave_v3"]


def _dispatch_flash_loan_pool_protocol_view(
    protocol: str,
    *,
    chain_id: int | None,
    **provider_options: Any,
) -> dict[str, Any]:
    try:
        return dispatch_chain_flow(
            chain_id=chain_id,
            flow_name="flash_loan_pools",
            protocol=protocol,
            **provider_options,
        )
    except ChainFlowDispatchError as exc:
        view = _empty_protocol_view(protocol, status="protocol_pool_cache_not_available")
        view["status_message"] = exc.message
        view["processing"] = {"flow_name": "flash_loan_pools", "chain_id": chain_id, "error_code": exc.code}
        return view


def flash_loan_pools_payload(
    *,
    database_url: str | None = None,
    market_id: str | None = None,
    chain_id: int | None = None,
    protocols: list[str] | None = None,
    discovered_protocols: list[str] | None = None,
    **provider_options: Any,
) -> dict[str, Any]:
    persist_static = bool(provider_options.pop("persist_static", False))
    refresh_mode = str(provider_options.pop("refresh_mode", "incremental") or "incremental").strip().lower()
    if refresh_mode not in {"incremental", "full"}:
        refresh_mode = "incremental"
    static_pool_store_root = provider_options.pop("static_pool_store_root", None)
    requested_protocols: list[str] = []
    for protocol in protocols or []:
        normalized = str(protocol or "").strip().lower()
        if normalized and normalized not in requested_protocols:
            requested_protocols.append(normalized)
    requested_protocol_set = set(requested_protocols)
    discovered_protocol_set = {
        str(protocol or "").strip().lower()
        for protocol in discovered_protocols or []
        if str(protocol or "").strip()
    }
    requested_market_id = str(market_id or "").strip()
    selected_market_row = None
    market_rows = supported_market_summaries(database_url)
    if requested_market_id:
        selected_market_row = next(
            (row for row in market_rows if str(row.get("market_id") or "") == requested_market_id),
            None,
        )
    scoped_providers = []
    for row in market_rows:
        if chain_id is not None:
            if row.get("chain_id") is None:
                continue
            if int(row["chain_id"]) != int(chain_id):
                continue
        elif selected_market_row is not None:
            if row.get("chain_id") != selected_market_row.get("chain_id"):
                continue
            if str(row.get("network") or "") != str(selected_market_row.get("network") or ""):
                continue
        provider = _provider_from_market(row)
        provider_chain_id = provider.get("chain_id")
        supported_protocols = _chain_supported_protocol_set(int(provider_chain_id)) if provider_chain_id is not None else set()
        if supported_protocols and provider["protocol"] not in supported_protocols:
            continue
        if discovered_protocols is not None and provider["protocol"] not in discovered_protocol_set:
            continue
        scoped_providers.append(provider)

    available_protocols = {item["protocol"] for item in scoped_providers if item.get("protocol")}
    providers = []
    for provider in scoped_providers:
        if requested_protocol_set and provider["protocol"] not in requested_protocol_set:
            continue
        providers.append(provider)
    providers.sort(
        key=lambda item: (
            str(item.get("network") or ""),
            str(item.get("provider_kind") or ""),
            str(item.get("protocol") or ""),
            str(item.get("market_id") or ""),
        )
    )
    summary = {
        "provider_count": len(providers),
        "execution_capable_count": sum(1 for item in providers if not item["observation_only"]),
        "observation_only_count": sum(1 for item in providers if item["observation_only"]),
        "bot_consumable_count": sum(1 for item in providers if item["bot_consumable"]),
        "dex_consumable_count": sum(1 for item in providers if item["dex_consumable"]),
        "chain_count": len({item.get("chain_id") for item in providers if item.get("chain_id") is not None}),
        "protocol_count": len({item.get("protocol") for item in providers if item.get("protocol")}),
    }
    selected_protocols = requested_protocols or _default_protocols_for_scope(
        chain_id=chain_id,
        selected_market_row=selected_market_row,
        available_protocols=available_protocols,
    )
    protocol_views = []
    has_protocol_scope = chain_id is not None or selected_market_row is not None
    for protocol in selected_protocols:
        view_chain_id = chain_id
        if view_chain_id is None and selected_market_row is not None and selected_market_row.get("chain_id") is not None:
            view_chain_id = int(selected_market_row["chain_id"])
        if view_chain_id is None:
            protocol_chain_ids = {
                int(provider["chain_id"])
                for provider in scoped_providers
                if provider.get("protocol") == protocol and provider.get("chain_id") is not None
            }
            if len(protocol_chain_ids) == 1:
                view_chain_id = next(iter(protocol_chain_ids))
        if _is_route_only_protocol(protocol):
            protocol_views.append(_route_only_protocol_view(protocol))
        elif has_protocol_scope and protocol not in available_protocols:
            protocol_views.append(_empty_protocol_view(protocol, status="chain_protocol_not_supported"))
        elif view_chain_id is not None:
            protocol_views.append(
                _dispatch_flash_loan_pool_protocol_view(
                    protocol,
                    chain_id=view_chain_id,
                    **provider_options,
                )
            )
        else:
            protocol_views.append(_empty_protocol_view(protocol))
    static_pool_store = []
    for view in protocol_views:
        protocol = str(view.get("protocol") or "").strip().lower()
        if not protocol:
            continue
        view_chain_id = chain_id
        cache = view.get("cache") if isinstance(view.get("cache"), dict) else {}
        if view_chain_id is None and cache.get("chain_id") is not None:
            try:
                view_chain_id = int(cache["chain_id"])
            except (TypeError, ValueError):
                view_chain_id = None
        network = str((cache.get("network") if isinstance(cache, dict) else "") or "").strip().lower()
        if persist_static and "status" not in view:
            metadata = write_flash_loan_pool_static(
                view,
                network=network or None,
                chain_id=view_chain_id,
                refresh_mode=refresh_mode,
                root=static_pool_store_root,
            )
            metadata = {
                "path": metadata.get("path"),
                "exists": bool(metadata.get("exists")),
                "network": metadata.get("network"),
                "chain_id": metadata.get("chain_id"),
                "protocol": metadata.get("protocol"),
                "generated_at": metadata.get("generated_at"),
                "refresh_mode": metadata.get("refresh_mode"),
                "section_counts": metadata.get("section_counts") or {},
                "pool_evidence_schema_version": metadata.get("pool_evidence_schema_version"),
                "pool_evidence": metadata.get("pool_evidence") or {},
            }
        else:
            metadata = static_store_metadata(
                network=network or None,
                chain_id=view_chain_id,
                protocol=protocol,
                root=static_pool_store_root,
            )
        view["static_pool_store"] = metadata
        view["pool_evidence"] = metadata.get("pool_evidence") or pool_evidence_from_view(view)
        static_pool_store.append(metadata)
    primary_view = protocol_views[0]
    scope = {
        "chain_id": chain_id,
        "protocols": requested_protocols,
    }
    if requested_market_id:
        scope["market_id"] = requested_market_id
    return {
        "ok": True,
        "scope": scope,
        "summary": summary,
        "providers": providers,
        "protocol_views": protocol_views,
        "reserve_cache": primary_view["cache"],
        "borrow_target_universe": primary_view["borrow_target_universe"],
        "static_pool_store": static_pool_store,
    }
