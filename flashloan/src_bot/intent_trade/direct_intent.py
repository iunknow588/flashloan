"""Intent construction helpers for direct on-chain execution."""

from __future__ import annotations

from typing import Any

from intent_trade.builder import DEFAULT_INTENT_BORROW_SYMBOL, build_cow_intent_trade


def _impl():
    from intent_trade import direct_impl

    return direct_impl


def pair_index_from_tokenx_tokeny(
    token_x: Any,
    token_y: Any,
    pairs: list[dict[str, Any]] | tuple[dict[str, Any], ...] | None = None,
    *,
    router: Any = None,
) -> int | None:
    impl = _impl()
    normalized_x = impl._normalize_pair_address(token_x)
    normalized_y = impl._normalize_pair_address(token_y)
    normalized_router = impl._normalize_pair_address(router)
    if not normalized_x or not normalized_y:
        return None
    table = impl._normalize_usdc_pair_table(pairs) if pairs is not None else impl.local_usdc_pair_memory_table()
    for index, pair in enumerate(table):
        same_order = pair["tokenX"] == normalized_x and pair["tokenY"] == normalized_y
        reverse_order = pair["tokenX"] == normalized_y and pair["tokenY"] == normalized_x
        if not same_order and not reverse_order:
            continue
        if normalized_router and pair.get("router") and pair["router"] != normalized_router:
            continue
        return index
    return None


def pair_tokens_from_path(path: Any) -> tuple[Any, Any]:
    if isinstance(path, (list, tuple)) and len(path) >= 4:
        return path[1], path[2]
    return None, None


def resolve_pair_id(protocol: dict[str, Any], opportunity: dict[str, Any] | None = None) -> int | None:
    impl = _impl()
    explicit = impl._explicit_pair_id(protocol)
    if explicit is not None:
        return explicit

    token_x = impl._first_value(protocol, opportunity, names=("tokenX", "token_x", "tokex"))
    token_y = impl._first_value(protocol, opportunity, names=("tokenY", "token_y", "tokey"))
    router = impl._first_value(protocol, opportunity, names=("router", "dex_router", "router_address"))
    if token_x is None or token_y is None:
        path_x, path_y = pair_tokens_from_path(protocol.get("route_path"))
        token_x = token_x if token_x is not None else path_x
        token_y = token_y if token_y is not None else path_y
    if (token_x is None or token_y is None) and isinstance(opportunity, dict):
        path_x, path_y = pair_tokens_from_path(opportunity.get("route_path") or opportunity.get("path"))
        token_x = token_x if token_x is not None else path_x
        token_y = token_y if token_y is not None else path_y
    inferred = pair_index_from_tokenx_tokeny(
        token_x,
        token_y,
        impl.local_usdc_pair_memory_table(protocol),
        router=router,
    )
    return inferred if inferred is not None else impl._env_pair_id()


def build_triangular_onchain_intent_trade(
    link_name: Any,
    expected_profit: Any,
    rising_tokens: Any,
    falling_tokens: Any,
) -> dict[str, Any]:
    impl = _impl()
    intent = build_cow_intent_trade(link_name, expected_profit, rising_tokens, falling_tokens)
    route_path = list(intent.get("route_path") or [])
    signal_market_state = {
        "top": list(rising_tokens or []) if isinstance(rising_tokens, (list, tuple)) else [],
        "bottom": list(falling_tokens or []) if isinstance(falling_tokens, (list, tuple)) else [],
    }
    route_groups = impl._runtime_route_group_candidates({"market_state": signal_market_state}, None)
    selected_group, intent_route_match = impl._select_runtime_group_for_intent(route_groups, route_path)
    runtime_trades = list(selected_group["trades"]) if selected_group else []
    network, chain_id, testnet = impl._resolve_direct_onchain_network()
    unified_executor_address = impl._env(
        "UNIFIED_EXECUTOR_ADDRESS",
        "TRIANGULAR_UNIFIED_EXECUTOR_ADDRESS",
        "AAVE_TRIANGULAR_EXECUTOR_ADDRESS",
    )
    protocol = {
        "kind": "unified_flashloan_mev_executor_runtime_v1",
        "enabled": impl._unified_executor_supported_for_network(network),
        "network": network,
        "chain_id": chain_id,
        "testnet": testnet,
        "execution_support": (
            "supported"
            if impl._unified_executor_supported_for_network(network)
            else impl._executor_adapter_unverified_status(network)
        ),
        "owner_address": impl._env("LIQUIDATION_EXECUTOR_OWNER_ADDRESS", "TRIANGULAR_CONTROLLER_OWNER_ADDRESS"),
        "unified_executor_address": unified_executor_address,
        "executor_address": unified_executor_address,
        "runtime_trades": runtime_trades,
        "runtime_route_groups": route_groups,
        "runtime_trade_limit": impl.MAX_RUNTIME_TRADE_SCAN,
        "runtime_candidate_limit": impl.MAX_OFFCHAIN_RUNTIME_CANDIDATE_SCAN,
        "runtime_route_group_limit": impl._runtime_route_group_limit({}, None),
        "intent_route_match": intent_route_match,
        "execution_mode": "ordered_auto",
        "candidate_strategy": impl._runtime_strategy_from_env(),
        "selection_strategy": "all_usdc_route_groups_preview_then_best",
        "borrow_symbol": intent.get("initial_symbol") or DEFAULT_INTENT_BORROW_SYMBOL,
        "route_path": route_path,
        "route_direction": intent.get("route_direction"),
        "expected_profit_usdc": intent.get("expected_profit_amount"),
    }
    intent["direct_onchain_protocol"] = protocol
    intent["intent_protocol"] = "direct_onchain"
    intent["submission_protocol"] = "direct_onchain"
    intent["submission_mode"] = "direct_onchain"
    intent["direct_onchain_ready"] = bool(
        protocol["enabled"]
        and protocol["unified_executor_address"]
        and (protocol["runtime_trades"] or protocol["runtime_route_groups"])
    )
    return intent
